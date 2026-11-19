from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from bff.app import BffDeps, create_app
from bff.config import BffConfig
from bff.oidc import OidcClient
from bff.sessions import InMemorySessionStore
from bff.testing import (
    CLIENT_SECRET,
    PUBLIC,
    TRACE,
    Clock,
    FakeBroker,
    FakeCopilot,
    FakeIdp,
    approval_transport,
    jaeger_transport,
)
from copilot_auth import TokenValidator
from copilot_memory import InMemoryMemoryStore
from fastapi import FastAPI


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def idp(clock: Clock) -> FakeIdp:
    return FakeIdp(clock)


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker()


@pytest.fixture
def copilot() -> FakeCopilot:
    return FakeCopilot()


@pytest.fixture
def memory() -> InMemoryMemoryStore:
    return InMemoryMemoryStore()


@pytest.fixture
def approval_calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def config() -> BffConfig:
    return BffConfig(issuer=FakeIdp.issuer, client_secret=CLIENT_SECRET, public_url=PUBLIC)


@pytest.fixture
def make_app(
    clock: Clock,
    idp: FakeIdp,
    broker: FakeBroker,
    copilot: FakeCopilot,
    approval_calls: list[httpx.Request],
    memory: InMemoryMemoryStore,
) -> Callable[..., FastAPI]:
    def build(config: BffConfig | None = None, **overrides: Any) -> FastAPI:
        cfg = config or BffConfig(
            issuer=FakeIdp.issuer, client_secret=CLIENT_SECRET, public_url=PUBLIC
        )
        sessions = InMemorySessionStore(
            idle_seconds=cfg.session_idle_seconds,
            absolute_seconds=cfg.session_absolute_seconds,
            login_ttl_seconds=cfg.login_ttl_seconds,
            clock=clock.now,
        )
        deps = BffDeps(
            config=cfg,
            oidc=OidcClient(
                cfg, httpx.AsyncClient(transport=idp.transport()), idp.keys(), clock.now
            ),
            sessions=sessions,
            access_validator=TokenValidator(
                issuer=FakeIdp.issuer, audience="copilot-bff", keys=idp.keys(), clock=clock.now
            ),
            broker=broker,
            copilot=copilot,
            approvals=httpx.AsyncClient(
                transport=approval_transport(approval_calls, {"drafts": []}),
                base_url="http://approval",
            ),
            jaeger=httpx.AsyncClient(
                transport=jaeger_transport(TRACE, "sub-ana"), base_url="http://jaeger"
            ),
            memory=memory,
            clock=clock.now,
        )
        for key, value in overrides.items():
            setattr(deps, key, value)
        return create_app(deps)

    return build


@pytest.fixture
def app(make_app: Callable[..., FastAPI]) -> FastAPI:
    return make_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=PUBLIC, follow_redirects=False
    ) as http:
        yield http
