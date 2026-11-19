"""Composition root for the laptop profile: Query API, MCP server, verifier and gateway, in one
process, wired the way the compose stack wires them over HTTP.

Used by the end-to-end tests and the eval harness. The seams are real: the gateway talks MCP to
the server, the server exchanges tokens and calls the Query API, which runs as the user.
"""

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import httpx2
from copilot_auth import JwksKeyProvider, TokenExchangeClient, TokenValidator
from copilot_catalogue import Catalogue
from copilot_drafts import DraftsRepo, PostgresEntitlements
from copilot_memory import InMemoryResultCache
from copilot_sql_verifier import Verifier
from copilot_tracing import Tracing
from mcp.server.transport_security import TransportSecuritySettings
from mcp_analytics.query_client import HttpQueryApi
from mcp_analytics.server import Deps, build_server
from mcp_analytics.verifier import CatalogueVerifier
from mcp_drafts.server import Deps as DraftsDeps
from mcp_drafts.server import build_server as build_drafts_server
from policy_gateway import Gateway, Ledger, McpUpstream, Policy
from query_api.app import create_app
from query_api.config import Settings

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_POLICY = ROOT / "services" / "policy-gateway" / "policy" / "policy.yaml"
DEFAULT_CATALOGUE = ROOT / "data" / "catalogue"


@dataclass(frozen=True)
class LocalStack:
    gateway: Gateway
    catalogue: Catalogue
    policy: Policy


def _result_cache(env: Mapping[str, str]) -> InMemoryResultCache | None:
    """A per-user result cache, five minutes by default; RESULT_CACHE_TTL_SECONDS=0 turns it off."""
    try:
        ttl = float(env.get("RESULT_CACHE_TTL_SECONDS", "300"))
    except ValueError:
        ttl = 300.0
    return InMemoryResultCache(ttl_seconds=ttl) if ttl > 0 else None


@asynccontextmanager
async def local_stack(
    ledger: Ledger,
    *,
    tracing: Tracing | None = None,
    env: Mapping[str, str] = os.environ,
    policy_file: Path = DEFAULT_POLICY,
    catalogue_dir: Path = DEFAULT_CATALOGUE,
) -> AsyncIterator[LocalStack]:
    keycloak = env.get("KEYCLOAK_URL", "http://localhost:8081")
    issuer = f"{keycloak}/realms/halcyon"
    back = (
        f"{env.get('KEYCLOAK_INTERNAL_URL', keycloak)}/realms/halcyon"  # server-to-server address
    )
    jwks = f"{back}/protocol/openid-connect/certs"
    host = env.get("POSTGRES_HOST", "localhost:5433")
    settings = Settings(
        issuer=issuer,
        jwks_url=jwks,
        dsn=f"postgresql://copilot_query:{env['QUERY_DB_PASSWORD']}@{host}/analytics",
    )
    catalogue = Catalogue.load(catalogue_dir)
    policy = Policy.from_file(policy_file)
    query_app = create_app(settings, tracing=tracing)
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
                    issuer=issuer, audience="mcp-analytics", keys=JwksKeyProvider(jwks)
                ),
                catalogue=catalogue,
                query_api=HttpQueryApi(query_http, inject=tracing.inject if tracing else None),
                verifier=CatalogueVerifier(Verifier(catalogue)),
                exchanger=TokenExchangeClient(
                    token_url=f"{back}/protocol/openid-connect/token",
                    client_id="mcp-analytics",
                    client_secret=env["MCP_ANALYTICS_CLIENT_SECRET"],
                    timeout_seconds=5.0,
                ),
                issuer_url=issuer,
                resource_url="http://mcp/mcp",
                tracing=tracing,
                cache=_result_cache(env),
            )
        )
        mcp_app = server.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=["mcp"], allowed_origins=[]
            )
        )
        await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))

        drafts_dsn = f"postgresql://copilot_drafts:{env['DRAFTS_DB_PASSWORD']}@{host}/analytics"
        drafts_server = build_drafts_server(
            DraftsDeps(
                validator=TokenValidator(
                    issuer=issuer, audience="mcp-drafts", keys=JwksKeyProvider(jwks)
                ),
                store=DraftsRepo(drafts_dsn),
                entitlements=PostgresEntitlements(drafts_dsn),
                issuer_url=issuer,
                resource_url="http://mcp-drafts/mcp",
                tracing=tracing,
            )
        )
        drafts_app = drafts_server.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["mcp-drafts"],
                allowed_origins=[],
            )
        )
        await stack.enter_async_context(drafts_app.router.lifespan_context(drafts_app))
        apps = {"analytics": (mcp_app, "http://mcp"), "drafts": (drafts_app, "http://mcp-drafts")}

        def http_for(server_name: str, token: str) -> httpx2.AsyncClient:
            app, base = apps[server_name]
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url=base,
                headers={"Authorization": f"Bearer {token}"},
            )

        upstream = McpUpstream(
            {"analytics": "http://mcp/mcp", "drafts": "http://mcp-drafts/mcp"},
            http_factory=http_for,
            timeout_seconds=10.0,
            trace_context=tracing.inject if tracing else None,
        )
        tool_tracer = tracing.tool_tracer() if tracing else None
        yield LocalStack(Gateway(policy, upstream, ledger, tool_tracer), catalogue, policy)
