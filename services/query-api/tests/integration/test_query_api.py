"""The Query API against real Postgres and Keycloak: scope, bypass attempts, caps, fail-closed."""

import logging
import re
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from copilot_auth.testing import keycloak_token_for as token_for
from query_api.app import create_app
from query_api.config import Settings
from query_api.provision import IdentityUser

pytestmark = pytest.mark.integration


async def _post(
    client: httpx.AsyncClient,
    user: str,
    sql: str,
    params: list[object] | None = None,
    **extra: object,
) -> httpx.Response:
    body = {
        "database": "treasury_analytics",
        "sql": sql,
        "params": params or [],
        "purpose": "test",
        **extra,
    }
    headers = {"Authorization": f"Bearer {await token_for(user)}"}
    return await client.post("/v1/query", json=body, headers=headers)


def _expected(owner: psycopg.Connection, where: str) -> int:
    row = owner.execute(f"SELECT count(*) FROM treasury.transactions WHERE {where}").fetchone()
    assert row is not None
    return int(row[0])


COUNT = "SELECT count(*) AS n FROM treasury.transactions"

# --- row scope: the underlying system owns data authorization (D6) ---------------------------


async def test_each_user_sees_only_their_entities(
    client: httpx.AsyncClient, owner: psycopg.Connection
) -> None:
    de = _expected(owner, "entity_id = 'HAL-DE'")
    uk = _expected(owner, "entity_id = 'HAL-UK'")
    assert de > 0 and uk > 0
    for user, expected in [
        ("ana.analyst", de),
        ("ben.analyst", uk),
        ("maria.manager", de + uk),
    ]:
        response = await _post(client, user, COUNT)
        assert response.status_code == 200, response.text
        assert response.json()["rows"] == [[expected]], user


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM treasury.transactions WHERE entity_id = 'HAL-UK'",
        "SELECT count(*) FROM treasury.accounts WHERE entity_id = 'HAL-UK'",
        "SELECT count(*) FROM treasury.payments WHERE entity_id = 'HAL-UK'",
        "SELECT count(*) FROM treasury.entities WHERE entity_id = 'HAL-UK'",
        "SELECT count(*) FROM treasury.daily_balances WHERE account_id LIKE 'A-UK-%'",
        "SELECT count(*) FROM treasury.transactions t JOIN treasury.accounts a "
        "USING (account_id) WHERE a.entity_id = 'HAL-UK'",
        "SELECT count(*) FROM (SELECT * FROM treasury.transactions UNION ALL "
        "SELECT * FROM treasury.transactions) u WHERE entity_id = 'HAL-UK'",
    ],
)
async def test_a_hand_written_query_cannot_reach_another_entitys_rows(
    client: httpx.AsyncClient, sql: str
) -> None:
    response = await _post(client, "ana.analyst", sql)
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == [[0]]


async def test_shared_reference_data_is_visible_to_everyone(client: httpx.AsyncClient) -> None:
    response = await _post(client, "ana.analyst", "SELECT count(*) FROM treasury.counterparties")
    assert response.json()["rows"] == [[40]]


# --- bypass attempts: rejected by the guard, and independently by the database ---------------


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DELETE FROM treasury.transactions", "NOT_SELECT"),
        ("SELECT 1; DROP TABLE treasury.transactions", "NOT_SELECT"),
        ("SELECT set_config('role', 'copilot_owner', true)", "PERMISSION"),
        ("SELECT * FROM eval.labels", "PERMISSION"),
        ("SELECT * FROM pg_catalog.pg_authid", "PERMISSION"),
        ("SELECT pg_sleep(30)", "PERMISSION"),
        ("SELECT * FROM ((", "SYNTAX"),
    ],
)
async def test_dangerous_statements_get_a_mapped_code_and_no_detail(
    client: httpx.AsyncClient, sql: str, code: str
) -> None:
    response = await _post(client, "ana.analyst", sql)
    assert response.status_code in (400, 403)
    body = response.json()
    assert body["error_code"] == code
    assert set(body) == {"error_code", "message"}


def _as_user(query_dsn: str, user: IdentityUser) -> psycopg.Connection:
    conn = psycopg.connect(query_dsn, autocommit=True)
    conn.execute(f"SET ROLE {user.db_role}")
    return conn


def _user(identity: list[IdentityUser], username: str) -> IdentityUser:
    return next(u for u in identity if u.username == username)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO treasury.entities VALUES ('X','x','DE','EUR','UTC',1)",
        "DELETE FROM treasury.transactions",
        "UPDATE treasury.accounts SET iban = 'x'",
        "DROP TABLE treasury.transactions",
        "CREATE TABLE treasury.x (a int)",
        "CREATE TABLE public.x (a int)",
        "SELECT * FROM eval.labels",
        "SELECT * FROM docs.documents",
        "SELECT set_config('role', 'copilot_owner', true)",
        "SELECT pg_sleep(0)",
    ],
)
def test_the_database_refuses_even_if_the_guard_were_bypassed(
    query_dsn: str, identity: list[IdentityUser], statement: str
) -> None:
    with (
        _as_user(query_dsn, _user(identity, "ana.analyst")) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(statement)


def test_a_select_cannot_switch_to_another_users_role(
    query_dsn: str, identity: list[IdentityUser]
) -> None:
    """The SELECT-only route to a role switch (set_config) is closed at the database.

    Residual risk, recorded in the threat model: the login role may SET ROLE to any user role,
    so a statement like `SET ROLE <other user>` would work if it ever reached the database. The
    guard rejects every non-SELECT statement, which is what keeps that path shut.
    """
    ben = _user(identity, "ben.analyst")
    with (
        _as_user(query_dsn, _user(identity, "ana.analyst")) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(f"SELECT set_config('role', '{ben.db_role}', false)")


def test_the_login_role_itself_can_read_nothing(query_dsn: str) -> None:
    with (
        psycopg.connect(query_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute("SELECT 1 FROM treasury.transactions")


def test_row_level_security_is_forced_on_every_scoped_table(owner: psycopg.Connection) -> None:
    rows = owner.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'treasury' AND relkind = 'r' AND relname IN "
        "('entities','accounts','transactions','payments','daily_balances')"
    ).fetchall()
    assert len(rows) == 5
    assert all(r[1] and r[2] for r in rows)


# --- authentication fails closed -------------------------------------------------------------


async def test_missing_malformed_and_foreign_tokens_are_refused(
    client: httpx.AsyncClient,
) -> None:
    body = {"database": "treasury_analytics", "sql": "SELECT 1", "purpose": "t"}
    for headers in [{}, {"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}]:
        response = await client.post("/v1/query", json=body, headers=headers)
        assert response.status_code == 401
        assert response.json()["error_code"] == "UNAUTHENTICATED"


async def test_a_token_for_another_audience_is_refused(client: httpx.AsyncClient) -> None:
    import os

    async with httpx.AsyncClient(timeout=5.0) as http:
        token = (
            await http.post(
                "http://localhost:8081/realms/halcyon/protocol/openid-connect/token",
                data={
                    "grant_type": "password",
                    "client_id": "test-harness",
                    "username": "ana.analyst",
                    "password": os.environ["DEMO_USER_PASSWORD"],
                },
            )
        ).json()["access_token"]  # aud=copilot-bff, not query-api
    response = await client.post(
        "/v1/query",
        json={"database": "treasury_analytics", "sql": "SELECT 1", "purpose": "t"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


async def test_an_unprovisioned_user_gets_nothing(
    settings: Settings, identity: list[IdentityUser]
) -> None:
    """A valid token for a user with no database role is refused, not given a default scope."""
    from query_api.db import Executor

    executor = Executor(settings.dsn)
    await executor.open()
    try:
        with pytest.raises(Exception) as info:  # mapped by the executor
            await executor.run(
                "u_00000000000000000000000000000000",
                "SELECT count(*) FROM treasury.transactions",
                {},
                max_rows=10,
                timeout_ms=1000,
            )
        assert getattr(info.value, "code", None) == "PERMISSION"
    finally:
        await executor.close()


async def test_idp_outage_with_no_cached_keys_fails_closed(settings: Settings) -> None:
    broken = Settings(
        issuer=settings.issuer,
        jwks_url="http://127.0.0.1:9/certs",
        dsn=settings.dsn,
    )
    app = create_app(broken)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://q") as http:
            response = await http.post(
                "/v1/query",
                json={"database": "treasury_analytics", "sql": "SELECT 1", "purpose": "t"},
                headers={"Authorization": f"Bearer {await token_for('ana.analyst')}"},
            )
    assert response.status_code == 503
    assert response.json()["error_code"] == "UNAVAILABLE"


# --- resource protection and result handling ---------------------------------------------------


async def test_row_cap_truncates_and_says_so(client: httpx.AsyncClient) -> None:
    response = await _post(
        client, "maria.manager", "SELECT transaction_id FROM treasury.transactions", max_rows=5
    )
    body = response.json()
    assert body["row_count"] == 5 and body["truncated"] is True and len(body["rows"]) == 5


async def test_statement_timeout_is_enforced(client: httpx.AsyncClient) -> None:
    sql = (
        "SELECT count(*) FROM treasury.transactions a, treasury.transactions b, "
        "treasury.transactions c, treasury.transactions d"
    )
    response = await _post(client, "maria.manager", sql, timeout_ms=100)
    assert response.status_code == 504
    assert response.json()["error_code"] == "TIMEOUT"


async def test_limits_above_the_server_maximum_are_refused(client: httpx.AsyncClient) -> None:
    response = await _post(client, "ana.analyst", "SELECT 1", max_rows=10_000_000)
    assert response.status_code == 422


async def test_money_comes_back_as_exact_strings_never_floats(client: httpx.AsyncClient) -> None:
    response = await _post(
        client, "ana.analyst", "SELECT amount, currency FROM treasury.transactions LIMIT 3"
    )
    body = response.json()
    assert [c["name"] for c in body["columns"]] == ["amount", "currency"]
    for amount, _currency in body["rows"]:
        assert isinstance(amount, str)
        assert Decimal(amount) == Decimal(amount).quantize(Decimal("0.01"))


async def test_parameters_are_bound_not_interpolated(client: httpx.AsyncClient) -> None:
    sql = (
        "SELECT count(*) FROM treasury.transactions "
        "WHERE booking_date >= $1 AND booking_date < $2 AND reference LIKE $3"
    )
    ok = await _post(client, "maria.manager", sql, ["2026-03-01", "2026-04-01", "REF0%"])
    assert ok.status_code == 200 and ok.json()["rows"][0][0] >= 0
    hostile = await _post(
        client, "maria.manager", sql, ["2026-03-01'; DROP TABLE treasury.transactions;--", "x", "y"]
    )
    assert hostile.status_code == 400
    assert hostile.json()["error_code"] == "BAD_PARAMS"
    again = await _post(client, "maria.manager", COUNT)
    assert again.status_code == 200 and again.json()["rows"][0][0] > 0


async def test_wrong_number_of_parameters_is_refused(client: httpx.AsyncClient) -> None:
    response = await _post(client, "ana.analyst", "SELECT $1::int", [])
    assert response.status_code == 400
    assert response.json()["error_code"] == "BAD_PARAMS"


async def test_explain_returns_a_plan_without_running_the_query(
    client: httpx.AsyncClient,
) -> None:
    body = {
        "database": "treasury_analytics",
        "sql": "SELECT count(*) FROM treasury.transactions",
        "purpose": "t",
    }
    response = await client.post(
        "/v1/explain",
        json=body,
        headers={"Authorization": f"Bearer {await token_for('ana.analyst')}"},
    )
    assert response.status_code == 200
    assert response.json()["total_cost"] > 0


async def test_schema_lists_only_the_treasury_schema(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/v1/schema", headers={"Authorization": f"Bearer {await token_for('ana.analyst')}"}
    )
    tables = {t["table"] for t in response.json()["tables"]}
    assert {"transactions", "accounts", "payments"} <= tables
    assert not any(t["schema"] != "treasury" for t in response.json()["tables"])


async def test_freshness_reports_the_last_load(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/v1/freshness", headers={"Authorization": f"Bearer {await token_for('ana.analyst')}"}
    )
    assert response.status_code == 200
    assert re.match(r"\d{4}-\d\d-\d\dT", response.json()["as_of"])


# --- audit ----------------------------------------------------------------------------------


async def test_every_call_is_audited_without_sql_text_params_or_tokens(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="query_api.audit")
    token = await token_for("ana.analyst")
    marker = "SECRET-LITERAL-123"
    await _post(client, "ana.analyst", "SELECT $1::text", [marker], purpose="insights:test")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "insights:test" in text
    assert "query_hash" in text and "sha256:" in text
    assert marker not in text
    assert token not in text
    assert "SELECT" not in text


# --- single source of truth for scope ----------------------------------------------------------


async def test_database_scope_matches_the_identity_provider_for_every_user(
    owner: psycopg.Connection, identity: list[IdentityUser]
) -> None:
    """Scope lives in two places (IdP claim, DB roles). They must never drift apart."""
    for user in identity:
        rows = owner.execute(
            "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = %s AND r.rolname LIKE 'ent\\_%%'",
            (user.db_role,),
        ).fetchall()
        in_db = {row[0] for row in rows}
        from_idp = {f"ent_{e.lower().replace('-', '_')}" for e in user.entities}
        assert in_db == from_idp, user.username
        claim = (await _claims(user.username))["entities"]
        assert set(claim) == set(user.entities), user.username


async def _claims(username: str) -> dict[str, Any]:
    import base64
    import json

    token = await token_for(username)
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(payload)))


async def test_the_catalogue_matches_the_live_warehouse_schema(client: httpx.AsyncClient) -> None:
    """Schema drift gate: undocumented, removed or retyped columns fail here (ARCHITECTURE.md §8.1)."""
    from pathlib import Path

    from copilot_catalogue import Catalogue, check_drift

    catalogue = Catalogue.load(Path(__file__).resolve().parents[4] / "data" / "catalogue")
    response = await client.get(
        "/v1/schema", headers={"Authorization": f"Bearer {await token_for('maria.manager')}"}
    )
    assert response.status_code == 200
    assert check_drift(catalogue, response.json()) == []
