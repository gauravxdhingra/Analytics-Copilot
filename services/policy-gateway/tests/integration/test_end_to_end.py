"""Gateway -> MCP server -> Query API -> Postgres, with real Keycloak tokens at every hop."""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from copilot_auth import JwksKeyProvider, Principal, TokenExchangeClient, TokenValidator
from copilot_auth.testing import keycloak_token_for
from copilot_catalogue import Catalogue
from copilot_sql_verifier import Verifier
from halcyon_world import generate_world
from halcyon_world.load import load_world
from mcp.server.transport_security import TransportSecuritySettings
from mcp_analytics.query_client import HttpQueryApi
from mcp_analytics.server import Deps, build_server
from mcp_analytics.verifier import CatalogueVerifier
from policy_gateway import Allowed, Denied, Gateway, McpUpstream, Policy, RunContext
from policy_gateway.postgres_ledger import PostgresLedger
from policy_gateway.testing import InMemoryTracer
from query_api.app import create_app
from query_api.config import Settings
from query_api.provision import load_identity, provision

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
POLICY_FILE = ROOT / "services" / "policy-gateway" / "policy" / "policy.yaml"
IDENTITY_FILE = ROOT / "deploy" / "compose" / "identity" / "users.json"
CATALOGUE = Catalogue.load(ROOT / "data" / "catalogue")
KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8081")
ISSUER = f"{KEYCLOAK}/realms/halcyon"
JWKS = f"{ISSUER}/protocol/openid-connect/certs"
PLANNER = "insights.query_planner"
SELECT = "analytics.run_validated_select"
COUNT_SQL = "SELECT count(*) FROM treasury.transactions WHERE is_current AND status = $1"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="module", autouse=True)
def database(owner_dsn: str) -> None:
    load_world(generate_world(seed=7), owner_dsn)
    provision(owner_dsn, load_identity(IDENTITY_FILE), login_password=_env("QUERY_DB_PASSWORD"))


class Stack:
    def __init__(self, gateway: Gateway, tracer: InMemoryTracer) -> None:
        self.gateway = gateway
        self.tracer = tracer

    async def ctx(self, username: str, *, audience: str = "mcp-analytics") -> RunContext:
        token = await keycloak_token_for(username, audience=audience)
        if audience == "mcp-analytics":
            principal = TokenValidator(
                issuer=ISSUER, audience="mcp-analytics", keys=JwksKeyProvider(JWKS)
            ).validate(token)
        else:  # a deliberately wrong token: the caller identity is not the point of the test
            principal = Principal(
                subject="x",
                tenant_id="halcyon",
                entities=(),
                roles=("analyst",),
                authorized_party="test",
            )
        return RunContext(
            run_id=f"run-{username}",
            trace_id="trace-e2e",
            principal=principal,
            upstream_tokens={"analytics": token},
        )


@asynccontextmanager
async def _stack(pg_ledger: PostgresLedger) -> AsyncIterator[Stack]:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    settings = Settings(
        issuer=ISSUER,
        jwks_url=JWKS,
        dsn=f"postgresql://copilot_query:{_env('QUERY_DB_PASSWORD')}@{host}/analytics",
    )
    query_app = create_app(settings)
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(query_app.router.lifespan_context(query_app))
        query_http = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=query_app), base_url="http://query-api"
            )
        )
        server = build_server(
            Deps(
                validator=TokenValidator(
                    issuer=ISSUER, audience="mcp-analytics", keys=JwksKeyProvider(JWKS)
                ),
                catalogue=CATALOGUE,
                query_api=HttpQueryApi(query_http),
                verifier=CatalogueVerifier(Verifier(CATALOGUE)),
                exchanger=TokenExchangeClient(
                    token_url=f"{ISSUER}/protocol/openid-connect/token",
                    client_id="mcp-analytics",
                    client_secret=_env("MCP_ANALYTICS_CLIENT_SECRET"),
                    timeout_seconds=5.0,
                ),
                issuer_url=ISSUER,
                resource_url="http://mcp/mcp",
            )
        )
        mcp_app = server.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=["mcp"], allowed_origins=[]
            )
        )
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))

        def factory(_server: str, token: str) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=mcp_app),
                base_url="http://mcp",
                headers={"Authorization": f"Bearer {token}"},
            )

        tracer = InMemoryTracer()
        gateway = Gateway(
            Policy.from_file(POLICY_FILE),
            McpUpstream(
                {"analytics": "http://mcp/mcp"}, http_factory=factory, timeout_seconds=10.0
            ),
            pg_ledger,
            tracer,
        )
        yield Stack(gateway, tracer)


def _args(sql: str = COUNT_SQL, **extra: Any) -> dict[str, Any]:
    params = extra.pop("params", ["booked"] if sql == COUNT_SQL else [])
    return {"sql": sql, "params": params, "purpose": "insights:e2e", **extra}


async def test_each_users_answer_follows_their_scope_through_every_layer(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        counts = {}
        for user in ("ana.analyst", "ben.analyst", "maria.manager"):
            out = await stack.gateway.call(await stack.ctx(user), PLANNER, SELECT, _args())
            assert isinstance(out, Allowed), out
            counts[user] = out.output["rows"][0][0]
        world = generate_world(seed=7)

        def booked(*entities: str) -> int:
            return sum(
                1
                for t in world.transactions
                if t.is_current and t.status == "booked" and t.entity_id in entities
            )

        assert counts == {
            "ana.analyst": booked("HAL-DE"),
            "ben.analyst": booked("HAL-UK"),
            "maria.manager": booked("HAL-DE", "HAL-UK"),
        }
        assert len(set(counts.values())) == 3


async def test_untrusted_remittance_text_arrives_wrapped_and_taints_the_run(
    pg_ledger: PostgresLedger,
) -> None:
    sql = (
        "SELECT transaction_id, remittance_text FROM treasury.transactions "
        "WHERE account_id = $1 AND booking_date = $2 AND is_current AND status = $3"
    )
    async with _stack(pg_ledger) as stack:
        ctx = await stack.ctx("ben.analyst")
        args = _args(sql, params=["A-UK-GBP-1", "2026-05-20", "booked"])
        out = await stack.gateway.call(ctx, PLANNER, SELECT, args)
        assert isinstance(out, Allowed) and out.tainted
        texts = [row[1] for row in out.output["rows"]]
        assert all(t.startswith("<untrusted source=") and t.endswith("</untrusted>") for t in texts)
        assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in t for t in texts)  # shown as data
        assert ctx.state.tainted_pending
        # The text asked for a payment approval. No tool could approve anything, and nothing
        # else runs until a clean re-plan.
        held = await stack.gateway.call(ctx, PLANNER, "analytics.get_freshness", {})
        assert isinstance(held, Denied) and held.reason == "TAINT_REPLAN"


async def test_a_role_without_access_is_stopped_before_any_server_is_reached(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        out = await stack.gateway.call(await stack.ctx("amit.approver"), PLANNER, SELECT, _args())
        assert isinstance(out, Denied) and out.reason == "ROLE_REQUIRED"


async def test_a_token_for_the_wrong_audience_is_refused_by_the_server(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        ctx = await stack.ctx("ana.analyst", audience="query-api")
        out = await stack.gateway.call(ctx, PLANNER, SELECT, _args())
        assert isinstance(out, Denied) and out.reason.startswith("UPSTREAM_")


async def test_a_write_is_stopped_by_the_query_api_guard_with_a_mapped_code(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        ctx = await stack.ctx("maria.manager")
        out = await stack.gateway.call(ctx, PLANNER, SELECT, _args("DELETE FROM treasury.accounts"))
        assert isinstance(out, Denied) and out.reason == "UPSTREAM_ERROR"
        assert out.detail is not None and "NOT_SELECT" in out.detail


async def test_an_unreachable_tool_server_fails_closed(pg_ledger: PostgresLedger) -> None:
    async with _stack(pg_ledger) as stack:
        broken = McpUpstream({"analytics": "http://127.0.0.1:9/mcp"}, timeout_seconds=2.0)
        gateway = Gateway(Policy.from_file(POLICY_FILE), broken, pg_ledger)
        out = await gateway.call(await stack.ctx("ana.analyst"), PLANNER, SELECT, _args())
        assert isinstance(out, Denied) and out.reason.startswith("UPSTREAM_")


async def test_every_call_is_in_the_durable_chain_and_spans_carry_no_sql(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        await stack.gateway.call(await stack.ctx("ana.analyst"), PLANNER, SELECT, _args())
        await stack.gateway.call(await stack.ctx("amit.approver"), PLANNER, SELECT, _args())
        assert pg_ledger.verify_chain()
        entries = pg_ledger.read_all()
        assert [e.decision for e in entries] == ["ALLOW", "DENY"]
        assert entries[1].reason_code == "ROLE_REQUIRED"
        assert entries[0].policy_version == 1 and entries[0].tool == SELECT
        assert all("SELECT" not in str(span.attributes) for span in stack.tracer.spans)


def test_the_production_policy_has_no_tool_that_can_act() -> None:
    policy = Policy.from_file(POLICY_FILE)
    assert policy.allowed_tools("insights.narrator") == set()
    assert policy.allowed_tools("recon.remittance_extractor") == set()
    assert all(t.startswith("analytics.") for t in policy.allowed_tools(PLANNER))


async def test_the_verifier_catches_a_currency_mix_before_the_query_api_is_asked(
    pg_ledger: PostgresLedger,
) -> None:
    sql = "SELECT sum(amount) FROM treasury.transactions WHERE is_current AND status = $1"
    async with _stack(pg_ledger) as stack:
        out = await stack.gateway.call(
            await stack.ctx("maria.manager"), PLANNER, SELECT, _args(sql, params=["booked"])
        )
        assert isinstance(out, Denied) and out.reason == "UPSTREAM_ERROR"
        assert out.detail is not None
        assert "VERIFIER_REJECTED" in out.detail and "CURRENCY_MIX" in out.detail


async def test_business_terms_and_store_descriptions_reach_the_planner(
    pg_ledger: PostgresLedger,
) -> None:
    async with _stack(pg_ledger) as stack:
        ctx = await stack.ctx("ana.analyst")
        term = await stack.gateway.call(
            ctx, PLANNER, "analytics.resolve_business_term", {"term": "outflow"}
        )
        assert isinstance(term, Allowed) and "negative" in term.output["definition"]
        store = await stack.gateway.call(
            ctx, PLANNER, "analytics.describe_store", {"store_id": "treasury.transactions"}
        )
        assert isinstance(store, Allowed)
        assert any("is_current" in f for f in store.output["required_filters"])
