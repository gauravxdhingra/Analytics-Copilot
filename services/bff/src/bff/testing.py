"""Test doubles for the BFF: a fake IdP that enforces PKCE, a controllable clock, and fakes."""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from copilot_auth import StaticKeyProvider
from cryptography.hazmat.primitives.asymmetric import rsa

from bff import pkce
from bff.broker import BrokerError
from bff.sessions import Session

START = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
DRAFT = "0123abcd-0000-4000-8000-000000000001"
PUBLIC = "http://app.test"
TRACE = "a" * 32
KID = "idp-key"
CLIENT_ID = "copilot-bff"
CLIENT_SECRET = "bff-secret"  # noqa: S105  # test value


class Clock:
    def __init__(self) -> None:
        self._now = START

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@dataclass
class _Code:
    user: str
    nonce: str
    challenge: str
    acr: str


USERS: dict[str, dict[str, Any]] = {
    "ana": {"sub": "sub-ana", "roles": ["analyst"], "entities": ["HAL-DE"], "name": "ana.analyst"},
    "ben": {"sub": "sub-ben", "roles": ["analyst"], "entities": ["HAL-UK"], "name": "ben.analyst"},
    "amit": {
        "sub": "sub-amit",
        "roles": ["approver"],
        "entities": ["HAL-DE", "HAL-UK"],
        "name": "amit.approver",
    },
}


class FakeIdp:
    issuer = "http://idp.test/realms/halcyon"

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._codes: dict[str, _Code] = {}
        self._refresh: dict[str, tuple[str, str]] = {}
        self.refreshes = 0
        self.down = False
        self.reject_refresh = False
        self.token_lifetime = 300

    def keys(self) -> StaticKeyProvider:
        return StaticKeyProvider({KID: self._key.public_key()})

    def _sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": KID})

    def _base(self, user: str, acr: str) -> dict[str, Any]:
        now = int(self._clock.now().timestamp())
        profile = USERS[user]
        return {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "sub": profile["sub"],
            "azp": CLIENT_ID,
            "iat": now,
            "exp": now + self.token_lifetime,
            "acr": acr,
            "auth_time": now,
            "preferred_username": profile["name"],
        }

    def access_token(self, user: str, acr: str = "1") -> str:
        profile = USERS[user]
        return self._sign(
            {
                **self._base(user, acr),
                "tenant_id": "halcyon",
                "roles": profile["roles"],
                "entities": profile["entities"],
            }
        )

    def issue_code(self, user: str, *, nonce: str, challenge: str, acr: str = "1") -> str:
        code = pkce.random_token(16)
        self._codes[code] = _Code(user, nonce, challenge, acr)
        return code

    def _tokens(self, user: str, nonce: str | None, acr: str) -> dict[str, Any]:
        refresh = pkce.random_token(16)
        self._refresh[refresh] = (user, acr)
        id_claims = {**self._base(user, acr), **({"nonce": nonce} if nonce else {})}
        return {
            "access_token": self.access_token(user, acr),
            "refresh_token": refresh,
            "id_token": self._sign(id_claims),
            "expires_in": self.token_lifetime,
        }

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("idp down")
            if not request.url.path.endswith("/token"):
                return httpx.Response(404)
            authorization = request.headers.get("authorization", "")
            if authorization != _basic():
                return httpx.Response(401, json={"error": "invalid_client"})
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("grant_type") == "authorization_code":
                code = self._codes.pop(form.get("code", ""), None)
                verifier = form.get("code_verifier", "")
                if code is None or pkce.challenge(verifier) != code.challenge:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._tokens(code.user, code.nonce, code.acr))
            if form.get("grant_type") == "refresh_token":
                known = self._refresh.pop(form.get("refresh_token", ""), None)
                if known is None or self.reject_refresh:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.refreshes += 1
                return httpx.Response(200, json=self._tokens(known[0], None, known[1]))
            return httpx.Response(400, json={"error": "unsupported_grant_type"})

        return httpx.MockTransport(handle)


def _basic() -> str:
    import base64

    return "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


class FakeBroker:
    def __init__(self) -> None:
        self.exchanged: list[tuple[str, str]] = []
        self.fail = False

    async def upstream_tokens(self, access_token: str) -> dict[str, str]:
        if self.fail:
            raise BrokerError("idp_unavailable")
        self.exchanged.append((access_token, "tools"))
        return {"analytics": "analytics-token", "drafts": "drafts-token"}

    async def approval_token(self, access_token: str) -> str:
        if self.fail:
            raise BrokerError("idp_unavailable")
        self.exchanged.append((access_token, "approval-service"))
        return "approval-token"


class FakeCopilot:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []
        self.drafts: list[dict[str, Any]] = [{"draft_id": DRAFT, "status": "draft"}]
        self.fail_with: Exception | None = None

    async def ask(self, session: Session, question: str, progress: Any) -> dict[str, Any]:
        self.asked.append((session.subject, question))
        if self.fail_with:
            raise self.fail_with
        for step in ("route", "plan", "execute", "narrate"):
            await progress(step)
        return {"status": "answered", "text": "There are 776.", "trace_id": "a" * 32}

    async def list_drafts(self, session: Session) -> list[dict[str, Any]]:
        return self.drafts

    async def submit_draft(self, session: Session, draft_id: str) -> dict[str, Any]:
        return {"draft_id": draft_id, "status": "submitted"}


def jaeger_transport(trace_id: str, owner: str) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != f"/api/traces/{trace_id}":
            return httpx.Response(404, json={"data": []})

        def tag(key: str, value: Any) -> dict[str, Any]:
            return {"key": key, "type": "string", "value": value}

        spans = [
            {
                "spanID": "root",
                "operationName": "agent.run",
                "startTime": int(time.time() * 1e6),
                "duration": 5000,
                "references": [],
                "tags": [
                    tag("copilot.user_id", owner),
                    tag("copilot.outcome", "answered"),
                    tag("copilot.secret_internal", "do-not-show"),
                ],
            },
            {
                "spanID": "child",
                "operationName": "tool.call",
                "startTime": 1,
                "duration": 900,
                "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                "tags": [tag("tool", "analytics.run_validated_select"), tag("decision", "ALLOW")],
            },
        ]
        return httpx.Response(200, json={"data": [{"traceID": trace_id, "spans": spans}]})

    return httpx.MockTransport(handle)


def approval_transport(
    record: list[httpx.Request], reply: dict[str, Any], status: int = 200
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        record.append(request)
        return httpx.Response(
            status, content=json.dumps(reply), headers={"content-type": "application/json"}
        )

    return httpx.MockTransport(handle)


async def sign_in(
    http: httpx.AsyncClient,
    idp: FakeIdp,
    user: str = "ana",
    *,
    acr: str = "1",
    path: str = "/auth/login",
) -> httpx.Response:
    """Drive the login the way a browser would, with the IdP step played by the fake."""
    start = await http.get(path)
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code(
        user, nonce=query["nonce"][0], challenge=query["code_challenge"][0], acr=acr
    )
    return await http.get("/auth/callback", params={"code": code, "state": query["state"][0]})


def csrf_headers(http: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": http.cookies.get("copilot_csrf") or "", "Origin": PUBLIC}
