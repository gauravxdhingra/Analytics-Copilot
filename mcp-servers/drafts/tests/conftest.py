from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx2
import pytest
from copilot_auth import StaticKeyProvider, TokenValidator
from copilot_auth.testing import ISSUER, KID, NOW, mint_token
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp_drafts.server import Deps, build_server
from mcp_drafts.testing import FakeEntitlements, FakeStore

AUDIENCE = "mcp-drafts"


@pytest.fixture(scope="session")
def key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def entitlements() -> FakeEntitlements:
    return FakeEntitlements({"maker-1": {"HAL-DE"}, "maker-2": {"HAL-UK"}})


@pytest.fixture
def token_for(key: RSAPrivateKey) -> Callable[..., str]:
    def make(subject: str = "maker-1", **claims: Any) -> str:
        defaults: dict[str, Any] = {"aud": AUDIENCE, "sub": subject, "roles": ["analyst"]}
        return mint_token(key, **(defaults | claims))

    return make


@pytest.fixture
def connect(
    key: RSAPrivateKey, store: FakeStore, entitlements: FakeEntitlements
) -> Callable[[str | None], AbstractAsyncContextManager[Client]]:
    def build_app() -> Any:
        validator = TokenValidator(
            issuer=ISSUER,
            audience=AUDIENCE,
            keys=StaticKeyProvider({KID: key.public_key()}),
            clock=lambda: NOW,
        )
        server = build_server(
            Deps(
                validator=validator,
                store=store,
                entitlements=entitlements,
                issuer_url=ISSUER,
                resource_url="http://mcp/mcp",
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
