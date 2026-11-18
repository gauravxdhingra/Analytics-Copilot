"""A scripted browser against the real Keycloak: login, session, an answer, and logout."""

import html
import json
import os
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from bff.app import BffDeps, create_app
from bff.broker import ExchangeBroker
from bff.config import BffConfig
from bff.copilot import OrchestratorCopilot
from bff.oidc import OidcClient
from bff.sessions import InMemorySessionStore
from copilot_auth import JwksKeyProvider, TokenValidator
from copilot_models import FakeClient
from halcyon_world import generate_world
from halcyon_world.load import load_world
from orchestrator import InsightsOrchestrator
from orchestrator.stack import local_stack
from orchestrator.testing import COUNT_PLAN, narrate, plan, route
from policy_gateway.postgres_ledger import PostgresLedger, ensure_schema
from query_api.provision import load_identity, provision

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[4]
PUBLIC = "http://localhost:3000"
KEYCLOAK = os.environ.get("KEYCLOAK_URL", "http://localhost:8081")
ISSUER = f"{KEYCLOAK}/realms/halcyon"
WORLD = generate_world(seed=7)


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set; load deploy/compose/.env")
    return value


@pytest.fixture(scope="module", autouse=True)
def database() -> None:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    owner = f"postgresql://copilot_owner:{_env('POSTGRES_OWNER_PASSWORD')}@{host}/analytics"
    load_world(WORLD, owner)
    provision(
        owner,
        load_identity(ROOT / "deploy" / "compose" / "identity" / "users.json"),
        login_password=_env("QUERY_DB_PASSWORD"),
    )
    ensure_schema(owner, app_password=_env("GATEWAY_DB_PASSWORD"), reset=True)


@pytest.fixture
def ledger() -> Iterator[PostgresLedger]:
    host = os.environ.get("POSTGRES_HOST", "localhost:5433")
    yield PostgresLedger(
        f"postgresql://copilot_gateway:{_env('GATEWAY_DB_PASSWORD')}@{host}/analytics"
    )


@asynccontextmanager
async def _bff(ledger: PostgresLedger) -> AsyncIterator[httpx.AsyncClient]:
    config = BffConfig(issuer=ISSUER, client_secret=_env("BFF_CLIENT_SECRET"), public_url=PUBLIC)
    jwks = JwksKeyProvider(f"{ISSUER}/protocol/openid-connect/certs")
    broker = ExchangeBroker(
        token_url=f"{ISSUER}/protocol/openid-connect/token",
        bff_secret=_env("BFF_CLIENT_SECRET"),
        orchestrator_secret=_env("ORCHESTRATOR_CLIENT_SECRET"),
    )
    async with local_stack(ledger, env=os.environ) as stack, httpx.AsyncClient() as idp_http:
        orchestrator = InsightsOrchestrator(
            FakeClient(
                [route(), plan(COUNT_PLAN), narrate("There are some booked transactions.")] * 3
            ),
            stack.gateway,
            stack.catalogue,
        )
        deps = BffDeps(
            config=config,
            oidc=OidcClient(config, idp_http, jwks),
            sessions=InMemorySessionStore(
                idle_seconds=1800, absolute_seconds=28800, login_ttl_seconds=600
            ),
            access_validator=TokenValidator(issuer=ISSUER, audience="copilot-bff", keys=jwks),
            broker=broker,
            copilot=OrchestratorCopilot(
                orchestrator,
                stack.gateway,
                broker,
                TokenValidator(issuer=ISSUER, audience="mcp-analytics", keys=jwks),
            ),
            approvals=httpx.AsyncClient(base_url="http://approval.invalid"),
        )
        app = create_app(deps)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=PUBLIC
        ) as http:
            yield http


def _cookie_header(response: httpx.Response) -> str:
    """The cookies Keycloak set, as a Cookie header.

    Keycloak marks its login cookies Secure. Browsers still send them to http://localhost
    (a secure context); the stdlib cookie jar will not, so carry them by hand.
    """
    return "; ".join(
        f"{c.split('=', 1)[0]}={c.split('=', 1)[1].split(';')[0]}"
        for c in response.headers.get_list("set-cookie")
    )


async def _login(http: httpx.AsyncClient, username: str, password: str | None = None) -> str | None:
    """The browser's side of the flow. Returns the callback URL, or None if Keycloak refused."""
    start = await http.get("/auth/login")
    assert start.status_code == 302
    async with httpx.AsyncClient(follow_redirects=False) as idp:
        page = await idp.get(start.headers["location"])
        match = re.search(r'action="([^"]+)"', page.text)
        assert match, "no login form on the page"
        done = await idp.post(
            html.unescape(match.group(1)),
            headers={"Cookie": _cookie_header(page)},
            data={
                "username": username,
                "password": password or _env("DEMO_USER_PASSWORD"),
                "credentialId": "",
            },
        )
    location: str = str(done.headers.get("location", ""))
    if not location.startswith(f"{PUBLIC}/auth/callback"):
        return None
    parsed = urlparse(location)
    callback = await http.get(f"{parsed.path}?{parsed.query}")
    assert callback.status_code == 302, callback.text
    return location


def _booked(*entities: str) -> int:
    return sum(
        1
        for t in WORLD.transactions
        if t.is_current and t.status == "booked" and t.entity_id in entities
    )


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def _ask(http: httpx.AsyncClient, question: str) -> list[tuple[str, dict[str, Any]]]:
    headers = {"X-CSRF-Token": http.cookies.get("copilot_csrf") or "", "Origin": PUBLIC}
    async with http.stream("POST", "/api/ask", json={"question": question}, headers=headers) as r:
        assert r.status_code == 200
        return _events((await r.aread()).decode())


async def test_a_real_login_creates_a_session_with_the_users_identity(
    ledger: PostgresLedger,
) -> None:
    async with _bff(ledger) as http:
        assert await _login(http, "ana.analyst")
        me = (await http.get("/api/me")).json()
        assert me["username"] == "ana.analyst" and me["roles"] == ["analyst"]
        assert me["entities"] == ["HAL-DE"] and me["tenant_id"] == "halcyon"
        assert "eyJ" not in json.dumps(me) and "eyJ" not in str(dict(http.cookies))


async def test_a_wrong_password_never_reaches_a_session(ledger: PostgresLedger) -> None:
    async with _bff(ledger) as http:
        assert await _login(http, "ana.analyst", password="not-the-password") is None
        assert (await http.get("/api/me")).status_code == 401


async def test_an_answer_through_the_browser_path_follows_the_users_scope(
    ledger: PostgresLedger,
) -> None:
    expected = {"ana.analyst": _booked("HAL-DE"), "maria.manager": _booked("HAL-DE", "HAL-UK")}
    for user, count in expected.items():
        async with _bff(ledger) as http:
            assert await _login(http, user)
            events = await _ask(http, "How many booked transactions are there?")
        answer = next(data for name, data in events if name == "answer")
        assert answer["status"] == "answered", answer
        assert answer["table"]["rows"] == [[count]], user
        assert [name for name, _ in events][-1] == "done"
        assert "eyJ" not in json.dumps(events)  # no token in the stream either
    assert len(set(expected.values())) == 2


async def test_logout_ends_the_session_and_the_idp_login(ledger: PostgresLedger) -> None:
    async with _bff(ledger) as http:
        assert await _login(http, "ana.analyst")
        headers = {"X-CSRF-Token": http.cookies.get("copilot_csrf") or "", "Origin": PUBLIC}
        response = await http.post("/auth/logout", headers=headers)
        assert response.status_code == 200
        redirect = response.json()["redirect"]
        async with httpx.AsyncClient(follow_redirects=False) as idp:
            ended = await idp.get(redirect)
        assert ended.status_code in (200, 302)
        if ended.status_code == 302:
            assert ended.headers["location"].startswith(PUBLIC)
        assert (await http.get("/api/me")).status_code == 401
