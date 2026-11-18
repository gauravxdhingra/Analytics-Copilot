from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from copilot_auth import StaticKeyProvider, TokenValidator
from copilot_auth.testing import AUDIENCE, ISSUER, KID, NOW, mint_token
from copilot_catalogue import Catalogue
from copilot_sql_verifier import Verifier
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp_analytics.server import Deps, build_server
from mcp_analytics.testing import FakeExchanger, FakeQueryApi
from mcp_analytics.verifier import CatalogueVerifier, SqlVerifier

MCP_AUDIENCE = "mcp-analytics"
CATALOGUE = Catalogue.load(Path(__file__).resolve().parents[3] / "data" / "catalogue")


@pytest.fixture(scope="session")
def key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def query_api() -> FakeQueryApi:
    return FakeQueryApi()


@pytest.fixture
def exchanger() -> FakeExchanger:
    return FakeExchanger()


@pytest.fixture
def token(key: RSAPrivateKey) -> str:
    return mint_token(key, aud=MCP_AUDIENCE)


@pytest.fixture
def verifier(request: pytest.FixtureRequest) -> SqlVerifier:
    return getattr(request, "param", None) or CatalogueVerifier(Verifier(CATALOGUE))


@pytest.fixture
def connect(
    key: RSAPrivateKey, query_api: FakeQueryApi, exchanger: FakeExchanger, verifier: SqlVerifier
) -> Callable[[str | None], AbstractAsyncContextManager[Client]]:
    """Returns `connect(token)`, an async context manager yielding a connected MCP client.

    The server and client are opened inside the test's own task, because the SDK's task group
    cannot be entered in a fixture and exited in another task. Each call builds a fresh server.
    """

    def build_app() -> Any:
        validator = TokenValidator(
            issuer=ISSUER,
            audience=MCP_AUDIENCE,
            keys=StaticKeyProvider({KID: key.public_key()}),
            clock=lambda: NOW,
        )
        server = build_server(
            Deps(
                validator=validator,
                catalogue=CATALOGUE,
                query_api=query_api,
                exchanger=exchanger,
                issuer_url=ISSUER,
                resource_url="http://mcp/mcp",
                verifier=verifier,
            )
        )
        return server.streamable_http_app(
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=["mcp"], allowed_origins=[]
            )
        )

    @asynccontextmanager
    async def client_for(token: str | None) -> AsyncIterator[Client]:
        app = build_app()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://mcp", headers=headers
        )
        async with (
            app.router.lifespan_context(app),
            Client(streamable_http_client("http://mcp/mcp", http_client=http)) as client,
        ):
            yield client

    return client_for


__all__ = ["AUDIENCE"]
