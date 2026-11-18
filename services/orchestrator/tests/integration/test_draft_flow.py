"""From the copilot's draft to a human decision, against real Postgres and Keycloak.

The copilot can create and (on a person's behalf) submit a draft. It cannot approve: the approval
service has its own audience, its own database role and its own rules.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from approval.app import ApprovalConfig, create_app
from copilot_auth import JwksKeyProvider, StaticKeyProvider, TokenValidator
from copilot_auth.testing import ISSUER as TEST_ISSUER
from copilot_auth.testing import KID, NOW, keycloak_token_for, mint_token
from copilot_drafts import ApprovalRepo, PostgresEntitlements, ensure_schema
from copilot_models import FakeClient
from cryptography.hazmat.primitives.asymmetric import rsa
from halcyon_world import generate_world
from halcyon_world.load import load_world
from orchestrator import AnswerStatus, InsightsOrchestrator
from orchestrator.stack import local_stack
from orchestrator.testing import narrate, plan, real_user_context, route
from policy_gateway import Allowed, Denied
from policy_gateway.postgres_ledger import PostgresLedger
from query_api.provision import load_identity, provision

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
IDENTITY = {
    u.username: u for u in load_identity(ROOT / "deploy" / "compose" / "identity" / "users.json")
}
KEYCLOAK_ISSUER = f"{os.environ.get('KEYCLOAK_URL', 'http://localhost:8081')}/realms/halcyon"
TEST_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
HOST = os.environ.get("POSTGRES_HOST", "localhost:5433")

# Payments near the limit for the maker's own entity (a plan the scripted planner "chose").
NEAR_LIMIT_PLAN: dict[str, Any] = {
    "store": "treasury.transactions",
    "select": [{"column": "transactions.transaction_id", "agg": "count", "alias": "n"}],
    "limit": 10,
}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="module", autouse=True)
def database(owner_dsn: str) -> None:
    load_world(generate_world(seed=7), owner_dsn)
    provision(owner_dsn, list(IDENTITY.values()), login_password=_env("QUERY_DB_PASSWORD"))


@pytest.fixture(autouse=True)
def drafts_schema(owner_dsn: str) -> None:
    ensure_schema(
        owner_dsn,
        drafts_password=_env("DRAFTS_DB_PASSWORD"),
        approval_password=_env("APPROVAL_DB_PASSWORD"),
        reset=True,
    )


def _approval_dsn() -> str:
    return f"postgresql://copilot_approval:{_env('APPROVAL_DB_PASSWORD')}@{HOST}/analytics"


def _approval_app(ledger: PostgresLedger, *, real_idp: bool = False) -> Any:
    keys = (
        JwksKeyProvider(f"{KEYCLOAK_ISSUER}/protocol/openid-connect/certs")
        if real_idp
        else StaticKeyProvider({KID: TEST_KEY.public_key()})
    )
    validator = TokenValidator(
        issuer=KEYCLOAK_ISSUER if real_idp else TEST_ISSUER,
        audience="approval-service",
        keys=keys,
        **({} if real_idp else {"clock": lambda: NOW}),
    )
    dsn = _approval_dsn()
    return create_app(
        ApprovalConfig(),
        validator=validator,
        repo=ApprovalRepo(dsn),
        entitlements=PostgresEntitlements(dsn),
        ledger=ledger,
        clock=(lambda: datetime.now(UTC)) if real_idp else (lambda: NOW),
    )


def _step_up_token(username: str, **claims: Any) -> str:
    """A token the IdP would issue after MFA, signed with the test key."""
    base: dict[str, Any] = {
        "aud": "approval-service",
        "sub": IDENTITY[username].id,
        "roles": list(IDENTITY[username].roles),
        "acr": "2",
        "auth_time": int(NOW.timestamp()) - 60,
        "azp": "copilot-bff",
    }
    return mint_token(TEST_KEY, **(base | claims))


async def _http(
    app: Any, method: str, path: str, token: str, body: dict[str, Any] | None = None
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://approval"
    ) as c:
        return await c.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )


@asynccontextmanager
async def _copilot(
    pg_ledger: PostgresLedger, script: list[Any]
) -> AsyncIterator[tuple[InsightsOrchestrator, Any]]:
    async with local_stack(pg_ledger, env=os.environ) as stack:
        agent = InsightsOrchestrator(FakeClient(script), stack.gateway, stack.catalogue)
        yield agent, stack


async def _make_and_submit(pg_ledger: PostgresLedger, maker: str = "ana.analyst") -> str:
    async with _copilot(
        pg_ledger,
        [route("draft"), plan(NEAR_LIMIT_PLAN), narrate("There are some payments to review.")],
    ) as (agent, stack):
        ctx = await real_user_context(maker)
        answer = await agent.run("Draft a review of the payments near the approval limit.", ctx)
        assert answer.status is AnswerStatus.ANSWERED and answer.draft_id, answer.draft_error
        submitted = await stack.gateway.call(
            ctx, "user.drafts", "drafts.submit_for_review", {"draft_id": answer.draft_id}
        )
        assert isinstance(submitted, Allowed) and submitted.output["status"] == "submitted"
    return answer.draft_id


async def test_the_copilot_creates_a_draft_attributed_to_the_user_and_nothing_more(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    async with _copilot(
        pg_ledger, [route("draft"), plan(NEAR_LIMIT_PLAN), narrate("There are some payments.")]
    ) as (agent, _):
        answer = await agent.run(
            "Draft a review of the payments near the limit.", await real_user_context("ana.analyst")
        )
    assert answer.draft_id
    row = owner.execute(
        "SELECT status, created_by, entity_id, payload->'evidence'->0->>'source_ref' "
        "FROM ops.drafts WHERE draft_id = %s::uuid",
        (answer.draft_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "draft" and row[1] == IDENTITY["ana.analyst"].id and row[2] == "HAL-DE"
    assert str(row[3]).startswith("sha256:")  # evidence points at the verified query


async def test_a_maker_without_the_entity_cannot_create_a_draft_for_it(
    pg_ledger: PostgresLedger,
) -> None:
    async with local_stack(pg_ledger, env=os.environ) as stack:
        ctx = await real_user_context("ben.analyst")  # HAL-UK only
        outcome = await stack.gateway.call(
            ctx,
            "insights.drafter",
            "drafts.create_draft",
            {"title": "t", "summary": "s", "entity_id": "HAL-DE"},
        )
    assert isinstance(outcome, Denied) and "NOT_ENTITLED" in (outcome.detail or "")


async def test_an_approver_cannot_create_drafts(pg_ledger: PostgresLedger) -> None:
    async with local_stack(pg_ledger, env=os.environ) as stack:
        outcome = await stack.gateway.call(
            await real_user_context("amit.approver"),
            "insights.drafter",
            "drafts.create_draft",
            {"title": "t", "summary": "s", "entity_id": "HAL-DE"},
        )
    assert isinstance(outcome, Denied) and outcome.reason == "ROLE_REQUIRED"


async def test_a_full_cycle_ends_in_a_recorded_human_approval(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    draft_id = await _make_and_submit(pg_ledger)
    app = _approval_app(pg_ledger)
    queue = await _http(app, "GET", "/v1/queue", _step_up_token("amit.approver"))
    assert queue.status_code == 200 and [d["draft_id"] for d in queue.json()["drafts"]] == [
        draft_id
    ]

    decision = {"decision": "approve", "comment": "Checked the payments."}
    response = await _http(
        app, "POST", f"/v1/drafts/{draft_id}/decision", _step_up_token("amit.approver"), decision
    )
    assert response.status_code == 200, response.text
    row = owner.execute(
        "SELECT status, decided_by, created_by FROM ops.drafts WHERE draft_id = %s::uuid",
        (draft_id,),
    ).fetchone()
    assert row == ("approved", IDENTITY["amit.approver"].id, IDENTITY["ana.analyst"].id)
    entry = pg_ledger.read_all()[-1]
    assert entry.tool == "drafts.decision" and entry.decision == "ALLOW"
    assert entry.user_id == IDENTITY["amit.approver"].id and pg_ledger.verify_chain()

    again = await _http(
        app, "POST", f"/v1/drafts/{draft_id}/decision", _step_up_token("amit.approver"), decision
    )
    assert again.status_code == 409  # a decision is final


async def test_the_maker_cannot_approve_even_with_a_forged_approver_role(
    pg_ledger: PostgresLedger, owner: psycopg.Connection
) -> None:
    draft_id = await _make_and_submit(pg_ledger)
    forged = _step_up_token("ana.analyst", roles=["approver"])
    response = await _http(
        _approval_app(pg_ledger),
        "POST",
        f"/v1/drafts/{draft_id}/decision",
        forged,
        {"decision": "approve"},
    )
    assert response.status_code == 403 and response.json()["error_code"] == "SOD_VIOLATION"
    status = owner.execute(
        "SELECT status FROM ops.drafts WHERE draft_id = %s::uuid", (draft_id,)
    ).fetchone()
    assert status == ("submitted",)


async def test_an_approver_of_another_entity_is_not_entitled(pg_ledger: PostgresLedger) -> None:
    draft_id = await _make_and_submit(pg_ledger)  # a HAL-DE draft
    uk_approver = _step_up_token("ben.analyst", roles=["approver"])  # Ben only holds HAL-UK
    response = await _http(
        _approval_app(pg_ledger),
        "POST",
        f"/v1/drafts/{draft_id}/decision",
        uk_approver,
        {"decision": "approve"},
    )
    assert response.status_code == 403 and response.json()["error_code"] == "NOT_ENTITLED"


async def test_a_real_password_only_session_is_refused_for_lack_of_step_up(
    pg_ledger: PostgresLedger,
) -> None:
    draft_id = await _make_and_submit(pg_ledger)
    real_token = await keycloak_token_for("amit.approver", audience="approval-service")
    response = await _http(
        _approval_app(pg_ledger, real_idp=True),
        "POST",
        f"/v1/drafts/{draft_id}/decision",
        real_token,
        {"decision": "approve"},
    )
    assert response.status_code == 403 and response.json()["error_code"] == "STEP_UP_REQUIRED"


async def test_the_copilots_own_tokens_cannot_reach_the_approval_service(
    pg_ledger: PostgresLedger,
) -> None:
    draft_id = await _make_and_submit(pg_ledger)
    drafts_token = await keycloak_token_for("amit.approver", audience="mcp-drafts")
    response = await _http(
        _approval_app(pg_ledger, real_idp=True),
        "POST",
        f"/v1/drafts/{draft_id}/decision",
        drafts_token,
        {"decision": "approve"},
    )
    assert response.status_code == 401  # wrong audience: never accepted here
