"""The BFF's browser-facing contract. Every protection has a test that tries to defeat it."""

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from bff.config import BffConfig
from bff.sessions import InMemorySessionStore, PendingLogin
from bff.testing import (
    CLIENT_SECRET,
    DRAFT,
    PUBLIC,
    TRACE,
    Clock,
    FakeBroker,
    FakeCopilot,
    FakeIdp,
    csrf_headers,
    sign_in,
)
from fastapi import FastAPI

Http = httpx.AsyncClient


def _cookie_headers(response: httpx.Response) -> list[str]:
    return response.headers.get_list("set-cookie")


# --- login: authorization code flow with PKCE -----------------------------------------------------


async def test_login_redirects_to_the_idp_with_pkce_state_and_nonce(client: Http) -> None:
    response = await client.get("/auth/login")
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    query = parse_qs(url.query)
    assert url.path.endswith("/protocol/openid-connect/auth")
    assert query["response_type"] == ["code"] and query["client_id"] == ["copilot-bff"]
    assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) >= 43
    assert query["redirect_uri"] == [f"{PUBLIC}/auth/callback"]
    assert query["state"][0] and query["nonce"][0] and query["scope"] == ["openid"]
    assert CLIENT_SECRET not in response.headers["location"]  # the secret never rides a URL


async def test_the_login_is_bound_to_the_browser_with_a_cookie(client: Http) -> None:
    response = await client.get("/auth/login")
    cookie = next(c for c in _cookie_headers(response) if c.startswith("copilot_login="))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


async def test_a_successful_callback_creates_an_httponly_session(
    client: Http, idp: FakeIdp
) -> None:
    response = await sign_in(client, idp)
    assert response.status_code == 302 and response.headers["location"] == "/"
    cookies = _cookie_headers(response)
    session = next(c for c in cookies if c.startswith("copilot_session="))
    csrf = next(c for c in cookies if c.startswith("copilot_csrf="))
    assert "HttpOnly" in session and "SameSite=lax" in session and "Path=/" in session
    assert "HttpOnly" not in csrf  # the page must read it to echo it


async def test_session_cookies_are_secure_behind_tls(
    make_app: Callable[..., FastAPI], idp: FakeIdp
) -> None:
    secure = BffConfig(
        issuer=FakeIdp.issuer,
        client_secret=CLIENT_SECRET,
        public_url="https://app.test",
        cookie_secure=True,
    )
    app = make_app(secure)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://app.test"
    ) as http:
        response = await sign_in(http, idp)
    assert all("Secure" in c for c in _cookie_headers(response) if "copilot_" in c)


async def test_no_token_ever_reaches_the_browser(client: Http, idp: FakeIdp) -> None:
    response = await sign_in(client, idp)
    token = idp.access_token("ana")
    leaked = response.text + str(response.headers) + str(dict(client.cookies))
    assert "eyJ" not in leaked  # no JWT anywhere: body, headers or cookies
    me = await client.get("/api/me")
    assert "eyJ" not in me.text and "token" not in me.text.lower() and token not in me.text


@pytest.mark.parametrize("breakage", ["no_state", "bad_state", "no_cookie", "no_code"])
async def test_a_bad_callback_is_refused(client: Http, idp: FakeIdp, breakage: str) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code("ana", nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    params = {"code": code, "state": query["state"][0]}
    if breakage == "no_state":
        del params["state"]
    elif breakage == "bad_state":
        params["state"] = "forged"
    elif breakage == "no_cookie":
        client.cookies.clear()
    else:
        del params["code"]
    response = await client.get("/auth/callback", params=params)
    assert response.status_code == 400 and response.json()["error_code"] == "LOGIN_FAILED"
    assert (await client.get("/api/me")).status_code == 401


async def test_a_callback_cannot_be_replayed(client: Http, idp: FakeIdp) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code("ana", nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    params = {"code": code, "state": query["state"][0]}
    assert (await client.get("/auth/callback", params=params)).status_code == 302
    client.cookies.set("copilot_login", query["state"][0], domain="app.test", path="/")
    assert (await client.get("/auth/callback", params=params)).status_code == 400


async def test_a_code_issued_for_another_pkce_challenge_is_rejected(
    client: Http, idp: FakeIdp
) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    stolen = idp.issue_code("ana", nonce=query["nonce"][0], challenge="someone-elses-challenge")
    response = await client.get(
        "/auth/callback", params={"code": stolen, "state": query["state"][0]}
    )
    assert response.status_code == 400  # the verifier does not match: PKCE holds


async def test_an_id_token_with_the_wrong_nonce_is_rejected(client: Http, idp: FakeIdp) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code("ana", nonce="not-the-nonce", challenge=query["code_challenge"][0])
    response = await client.get("/auth/callback", params={"code": code, "state": query["state"][0]})
    assert response.status_code == 400


async def test_an_unreachable_idp_fails_closed_with_a_503(client: Http, idp: FakeIdp) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code("ana", nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    idp.down = True
    response = await client.get("/auth/callback", params={"code": code, "state": query["state"][0]})
    assert response.status_code == 503 and response.json()["error_code"] == "UNAVAILABLE"


async def test_login_always_issues_a_fresh_session_id(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp)
    first = client.cookies.get("copilot_session")
    await sign_in(client, idp)
    assert client.cookies.get("copilot_session") != first


@pytest.mark.parametrize(
    "target", ["//evil.example", "https://evil.example", "/\\evil", "javascript:x"]
)
async def test_the_return_target_cannot_be_an_open_redirect(
    client: Http, idp: FakeIdp, target: str
) -> None:
    response = await sign_in(client, idp, path=f"/auth/login?return={target}")
    assert response.headers["location"] == "/"


async def test_a_local_return_target_is_honoured(client: Http, idp: FakeIdp) -> None:
    response = await sign_in(client, idp, path="/auth/login?return=/approvals")
    assert response.headers["location"] == "/approvals"


# --- the session ------------------------------------------------------------------------------------------


async def test_me_describes_the_user_without_secrets(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp)
    body = (await client.get("/api/me")).json()
    assert body["username"] == "ana.analyst" and body["roles"] == ["analyst"]
    assert body["entities"] == ["HAL-DE"] and body["tenant_id"] == "halcyon"
    assert body["strong_authentication"] is False


async def test_everything_behind_the_login_needs_a_session(client: Http) -> None:
    for path in ("/api/me", "/api/drafts", "/api/approvals/queue", f"/api/traces/{TRACE}"):
        response = await client.get(path)
        assert response.status_code == 401 and response.json()["error_code"] == "UNAUTHENTICATED"


async def test_a_session_ends_after_it_has_been_idle(
    client: Http, idp: FakeIdp, clock: Clock
) -> None:
    await sign_in(client, idp)
    clock.advance(31 * 60)
    assert (await client.get("/api/me")).status_code == 401


async def test_a_session_ends_at_its_absolute_limit_even_when_active(
    client: Http, idp: FakeIdp, clock: Clock
) -> None:
    idp.token_lifetime = 10 * 3600
    await sign_in(client, idp)
    for _ in range(9):
        clock.advance(55 * 60)  # always inside the idle window
        status = (await client.get("/api/me")).status_code
    assert status == 401  # past 8 hours


async def test_an_expiring_access_token_is_refreshed_transparently(
    client: Http, idp: FakeIdp, clock: Clock
) -> None:
    await sign_in(client, idp)
    clock.advance(280)  # inside the refresh margin of a 300 s token
    assert (await client.get("/api/me")).status_code == 200
    assert idp.refreshes == 1


async def test_a_session_that_cannot_refresh_is_ended(
    client: Http, idp: FakeIdp, clock: Clock
) -> None:
    await sign_in(client, idp)
    idp.reject_refresh = True
    clock.advance(280)
    assert (await client.get("/api/me")).status_code == 401
    idp.reject_refresh = False
    assert (await client.get("/api/me")).status_code == 401  # gone for good


async def test_logout_needs_csrf_destroys_the_session_and_returns_the_idp_logout(
    client: Http, idp: FakeIdp
) -> None:
    await sign_in(client, idp)
    assert (await client.post("/auth/logout")).status_code == 403
    response = await client.post("/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 200
    target = urlparse(response.json()["redirect"])
    assert target.path.endswith("/logout") and "id_token_hint" in target.query
    assert "post_logout_redirect_uri" in target.query
    assert (await client.get("/api/me")).status_code == 401


# --- CSRF and origin ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: {k: v for k, v in h.items() if k != "X-CSRF-Token"},
        lambda h: {**h, "X-CSRF-Token": "forged"},
        lambda h: {**h, "Origin": "https://evil.example"},
        lambda h: {k: v for k, v in h.items() if k != "Origin"},
    ],
)
async def test_state_changing_requests_need_a_matching_csrf_token_and_our_origin(
    client: Http,
    idp: FakeIdp,
    copilot: FakeCopilot,
    mutate: Callable[[dict[str, str]], dict[str, str]],
) -> None:
    await sign_in(client, idp)
    headers = mutate(csrf_headers(client))
    for method, path, body in [
        ("POST", "/api/ask", {"question": "How many?"}),
        ("POST", f"/api/drafts/{DRAFT}/submit", None),
        ("POST", f"/api/approvals/{DRAFT}/decision", {"decision": "approve"}),
    ]:
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403 and response.json()["error_code"] == "CSRF", path
    assert copilot.asked == []


async def test_a_referer_from_our_origin_is_accepted_when_there_is_no_origin_header(
    client: Http, idp: FakeIdp
) -> None:
    await sign_in(client, idp)
    headers = {
        "X-CSRF-Token": client.cookies.get("copilot_csrf") or "",
        "Referer": f"{PUBLIC}/chat",
    }
    response = await client.post(f"/api/drafts/{DRAFT}/submit", headers=headers)
    assert response.status_code == 200


# --- asking, streamed over SSE ---------------------------------------------------------------------------------


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_an_answer_streams_progress_then_the_answer_then_done(
    client: Http, idp: FakeIdp, copilot: FakeCopilot
) -> None:
    await sign_in(client, idp)
    async with client.stream(
        "POST", "/api/ask", json={"question": "How many?"}, headers=csrf_headers(client)
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = (await response.aread()).decode()
    events = _events(body)
    assert [e for e, _ in events] == ["step"] * 4 + ["answer", "done"]
    assert [d["step"] for e, d in events if e == "step"] == ["route", "plan", "execute", "narrate"]
    assert events[4][1]["text"] == "There are 776."
    assert copilot.asked == [("sub-ana", "How many?")]
    assert "eyJ" not in body


@pytest.mark.parametrize("question", ["", "x" * 2001])
async def test_questions_must_be_a_sensible_size(client: Http, idp: FakeIdp, question: str) -> None:
    await sign_in(client, idp)
    response = await client.post(
        "/api/ask", json={"question": question}, headers=csrf_headers(client)
    )
    assert response.status_code == 422 and response.json() == {"error_code": "BAD_REQUEST"}


async def test_unknown_fields_are_rejected_and_not_echoed(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp)
    response = await client.post(
        "/api/ask", json={"question": "q", "role": "admin"}, headers=csrf_headers(client)
    )
    assert response.status_code == 422 and "admin" not in response.text


async def test_asking_is_rate_limited_per_session(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp)
    statuses = []
    for _ in range(32):
        r = await client.post("/api/ask", json={"question": "q"}, headers=csrf_headers(client))
        statuses.append(r.status_code)
    assert statuses.count(200) == 30 and statuses[-1] == 429
    assert r.headers["retry-after"] == "60"


async def test_a_broken_token_exchange_becomes_a_mapped_error_event_not_a_500(
    client: Http, idp: FakeIdp, copilot: FakeCopilot
) -> None:
    from bff.copilot import CopilotError

    copilot.fail_with = CopilotError("idp_unavailable")
    await sign_in(client, idp)
    async with client.stream(
        "POST", "/api/ask", json={"question": "q"}, headers=csrf_headers(client)
    ) as response:
        events = _events((await response.aread()).decode())
    assert events[-2][0] == "error" and events[-2][1]["error_code"] == "UNAVAILABLE"
    assert "Traceback" not in json.dumps(events)


async def test_an_unexpected_failure_leaks_nothing(
    client: Http, idp: FakeIdp, copilot: FakeCopilot
) -> None:
    copilot.fail_with = RuntimeError("secret internal detail: password=hunter2")
    await sign_in(client, idp)
    async with client.stream(
        "POST", "/api/ask", json={"question": "q"}, headers=csrf_headers(client)
    ) as response:
        body = (await response.aread()).decode()
    assert "hunter2" not in body and '"error_code": "INTERNAL"' in body


# --- drafts -------------------------------------------------------------------------------------------------------


async def test_a_user_lists_and_submits_their_own_drafts(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp)
    assert (await client.get("/api/drafts")).json()["drafts"][0]["draft_id"] == DRAFT
    submitted = await client.post(f"/api/drafts/{DRAFT}/submit", headers=csrf_headers(client))
    assert submitted.json()["status"] == "submitted"
    bad = await client.post("/api/drafts/not valid!/submit", headers=csrf_headers(client))
    assert bad.status_code in (404, 422)


# --- approvals: a pass-through with the approval service's own token ------------------------------------------------


async def test_the_approval_queue_is_fetched_with_an_exchanged_token_never_the_browsers(
    client: Http, idp: FakeIdp, broker: FakeBroker, approval_calls: list[httpx.Request]
) -> None:
    await sign_in(client, idp, "amit")
    response = await client.get("/api/approvals/queue")
    assert response.status_code == 200
    call = approval_calls[-1]
    assert call.headers["authorization"] == "Bearer approval-token"
    assert "cookie" not in call.headers  # the browser's session never travels downstream
    assert broker.exchanged[-1][1] == "approval-service"


async def test_a_decision_needs_csrf_and_is_forwarded_as_is(
    client: Http, idp: FakeIdp, approval_calls: list[httpx.Request]
) -> None:
    await sign_in(client, idp, "amit")
    body = {"decision": "approve", "comment": "ok"}
    assert (await client.post(f"/api/approvals/{DRAFT}/decision", json=body)).status_code == 403
    response = await client.post(
        f"/api/approvals/{DRAFT}/decision", json=body, headers=csrf_headers(client)
    )
    assert response.status_code == 200
    assert json.loads(approval_calls[-1].content) == body
    assert approval_calls[-1].url.path == f"/v1/drafts/{DRAFT}/decision"


async def test_a_step_up_refusal_tells_the_ui_where_to_go(
    make_app: Callable[..., FastAPI], idp: FakeIdp, approval_calls: list[httpx.Request]
) -> None:
    from bff.testing import approval_transport

    reply = {"error_code": "STEP_UP_REQUIRED", "message": "Stronger authentication is required."}
    app = make_app(
        approvals=httpx.AsyncClient(
            transport=approval_transport(approval_calls, reply, 403), base_url="http://approval"
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as http:
        await sign_in(http, idp, "amit")
        response = await http.post(
            f"/api/approvals/{DRAFT}/decision",
            json={"decision": "approve"},
            headers=csrf_headers(http),
        )
    assert response.status_code == 403
    assert response.json()["error_code"] == "STEP_UP_REQUIRED"
    assert response.json()["step_up_url"].startswith("/auth/step-up")


async def test_a_broken_exchange_for_approvals_fails_closed(
    client: Http, idp: FakeIdp, broker: FakeBroker
) -> None:
    await sign_in(client, idp, "amit")
    broker.fail = True
    response = await client.get("/api/approvals/queue")
    assert response.status_code == 503 and response.json()["error_code"] == "UNAVAILABLE"


async def test_step_up_asks_the_idp_for_a_stronger_fresh_login_and_upgrades_the_session(
    client: Http, idp: FakeIdp
) -> None:
    await sign_in(client, idp, "amit")
    assert (await client.get("/api/me")).json()["acr"] == "1"
    start = await client.get("/auth/step-up?return=/approvals")
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert (
        query["acr_values"] == ["2"] and query["prompt"] == ["login"] and query["max_age"] == ["0"]
    )
    code = idp.issue_code(
        "amit", nonce=query["nonce"][0], challenge=query["code_challenge"][0], acr="2"
    )
    done = await client.get("/auth/callback", params={"code": code, "state": query["state"][0]})
    assert done.headers["location"] == "/approvals"
    me = (await client.get("/api/me")).json()
    assert me["acr"] == "2" and me["strong_authentication"] is True


async def test_step_up_needs_a_session(client: Http) -> None:
    assert (await client.get("/auth/step-up")).status_code == 401


# --- "how I got this": your own trace only ------------------------------------------------------------------------------


async def test_a_user_can_read_the_span_tree_of_their_own_run(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp, "ana")
    response = await client.get(f"/api/traces/{TRACE}")
    assert response.status_code == 200
    spans = {s["name"]: s for s in response.json()["spans"]}
    assert spans["tool.call"]["parent_id"] == spans["agent.run"]["span_id"]
    assert spans["tool.call"]["attributes"]["decision"] == "ALLOW"
    assert "copilot.secret_internal" not in spans["agent.run"]["attributes"]  # only an allowlist


async def test_someone_elses_trace_looks_like_no_trace(client: Http, idp: FakeIdp) -> None:
    await sign_in(client, idp, "ben")
    assert (await client.get(f"/api/traces/{TRACE}")).status_code == 404


@pytest.mark.parametrize("trace_id", ["short", "Z" * 32, "../../etc/passwd", "a" * 31])
async def test_malformed_trace_ids_never_reach_the_trace_backend(
    client: Http, idp: FakeIdp, trace_id: str
) -> None:
    await sign_in(client, idp, "ana")
    assert (await client.get(f"/api/traces/{trace_id}")).status_code in (404, 422)


# --- headers --------------------------------------------------------------------------------------------------------------


async def test_security_headers_are_set_and_api_responses_are_never_cached(
    client: Http, idp: FakeIdp
) -> None:
    await sign_in(client, idp)
    response = await client.get("/api/me")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


async def test_the_health_check_needs_no_login(client: Http) -> None:
    assert (await client.get("/healthz")).json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("breakage", "reason"),
    [
        ("no_cookie", "login_binding"),
        ("no_code", "missing_parameter"),
        ("bad_state", "login_binding"),
    ],
)
async def test_a_refused_login_logs_why_without_logging_secrets(
    client: Http, idp: FakeIdp, caplog: pytest.LogCaptureFixture, breakage: str, reason: str
) -> None:
    start = await client.get("/auth/login")
    query = parse_qs(urlparse(start.headers["location"]).query)
    code = idp.issue_code("ana", nonce=query["nonce"][0], challenge=query["code_challenge"][0])
    params = {"code": code, "state": query["state"][0]}
    if breakage == "no_cookie":
        client.cookies.clear()
    elif breakage == "no_code":
        del params["code"]
    else:
        params["state"] = "forged"
    with caplog.at_level("WARNING", logger="bff"):
        await client.get("/auth/callback", params=params)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert reason in text
    assert code not in text and query["state"][0] not in text and "forged" not in text


def _store(clock: Clock, max_pending: int) -> InMemorySessionStore:
    return InMemorySessionStore(
        idle_seconds=1800,
        absolute_seconds=28_800,
        login_ttl_seconds=600,
        clock=clock.now,
        max_pending=max_pending,
    )


def _pending(state: str, clock: Clock) -> PendingLogin:
    return PendingLogin(
        state=state, nonce="n", verifier="v", return_to="/", created_at=clock.now().timestamp()
    )


def test_unfinished_logins_cannot_grow_memory_without_bound(clock: Clock) -> None:
    # /auth/login is open to anyone, so an attacker can start logins forever.
    store = _store(clock, max_pending=3)
    for i in range(10):
        store.add_pending(_pending(f"s{i}", clock))
    assert store.pending_count == 3
    assert store.take_pending("s0") is None  # the oldest were dropped
    assert store.take_pending("s9") is not None  # a real user's login still works


def test_expired_logins_are_swept_when_new_ones_arrive(clock: Clock) -> None:
    store = _store(clock, max_pending=1000)
    for i in range(5):
        store.add_pending(_pending(f"old{i}", clock))
    clock.advance(601)
    store.add_pending(_pending("new", clock))
    assert store.pending_count == 1
