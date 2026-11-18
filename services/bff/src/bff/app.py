"""The BFF: the only thing the browser talks to.

It owns the login (OIDC code flow with PKCE), holds tokens server-side behind an opaque httpOnly
cookie, checks CSRF and origin on every state change, and exchanges tokens for each hop so the
browser's credential is never passed through. It decides nothing about data or approvals: the
copilot and the approval service enforce their own rules.
"""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from copilot_auth import AuthError, TokenValidator
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from bff import pkce
from bff.broker import BrokerError, TokenBroker
from bff.config import BffConfig
from bff.copilot import CopilotApi, CopilotError
from bff.oidc import OidcClient, OidcError
from bff.security import SECURITY_HEADERS, RateLimiter, csrf_valid, origin_valid
from bff.sessions import PendingLogin, Session, SessionStore, new_session_id

_log = logging.getLogger("bff")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_DRAFT_ID = re.compile(r"^[0-9a-fA-F-]{8,40}$")

_STATUS = {
    "UNAUTHENTICATED": 401,
    "CSRF": 403,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "RATE_LIMITED": 429,
    "BAD_REQUEST": 422,
    "LOGIN_FAILED": 400,
    "UNAVAILABLE": 503,
}
_SAFE_SPAN_ATTRIBUTES = {
    "copilot.node",
    "copilot.iteration",
    "copilot.outcome",
    "copilot.reason",
    "copilot.model_calls",
    "copilot.tool_calls",
    "copilot.model.role",
    "copilot.prompt.template_id",
    "copilot.row_count",
    "copilot.truncated",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.response.model",
    "tool",
    "server",
    "agent",
    "decision",
    "reason",
    "rows",
    "passed",
    "codes",
    "error.code",
    "copilot.decision",
    "duration_ms",
    "policy_version",
}


@dataclass
class BffDeps:
    config: BffConfig
    oidc: OidcClient
    sessions: SessionStore
    access_validator: TokenValidator  # validates the user's access token (audience: this client)
    broker: TokenBroker
    copilot: CopilotApi
    approvals: httpx.AsyncClient
    jaeger: httpx.AsyncClient | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    comment: str | None = Field(default=None, max_length=1000)


def _error(code: str, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=_STATUS[code], content={"error_code": code, **extra})


def _safe_return(target: str | None) -> str:
    """Only same-site relative paths: never an open redirect."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


class _Refused(Exception):  # control flow
    def __init__(self, response: JSONResponse) -> None:
        super().__init__("refused")
        self.response = response


def create_app(deps: BffDeps, lifespan: Any = None) -> FastAPI:
    cfg = deps.config
    limiter = RateLimiter(cfg.asks_per_minute, deps.clock)
    app = FastAPI(title="BFF", docs_url=None, redoc_url=None, lifespan=lifespan)

    def now() -> float:
        return deps.clock().timestamp()

    @app.middleware("http")
    async def headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith(("/api", "/auth")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(_Refused)
    async def _refused(_request: Request, exc: _Refused) -> JSONResponse:
        return exc.response

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error("BAD_REQUEST")  # the default body would echo the question

    # -- cookies --------------------------------------------------------------------------------

    def set_session_cookies(response: Response, session: Session) -> None:
        response.set_cookie(
            cfg.session_cookie,
            session.session_id,
            httponly=True,
            samesite="lax",
            secure=cfg.cookie_secure,
            path="/",
            max_age=cfg.session_absolute_seconds,
        )
        # Readable by the page's script on purpose: it echoes the value in a header.
        response.set_cookie(
            cfg.csrf_cookie,
            session.csrf_token,
            httponly=False,
            samesite="lax",
            secure=cfg.cookie_secure,
            path="/",
            max_age=cfg.session_absolute_seconds,
        )

    def delete_cookie(response: Response, name: str) -> None:
        # Same attributes as when it was set, so a Secure cookie is never "deleted" insecurely.
        response.delete_cookie(
            name,
            path="/",
            secure=cfg.cookie_secure,
            httponly=name != cfg.csrf_cookie,
            samesite="lax",
        )

    def clear_cookies(response: Response) -> None:
        for name in (cfg.session_cookie, cfg.csrf_cookie, cfg.login_cookie):
            delete_cookie(response, name)

    # -- login ----------------------------------------------------------------------------------

    def begin_login(return_to: str, *, step_up: bool) -> RedirectResponse:
        state, nonce, verifier = pkce.random_token(), pkce.random_token(), pkce.new_verifier()
        deps.sessions.add_pending(
            PendingLogin(state, nonce, verifier, return_to, now(), step_up=step_up)
        )
        url = deps.oidc.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=pkce.challenge(verifier),
            acr_values=cfg.step_up_acr if step_up else None,
            force_login=step_up,
        )
        response = RedirectResponse(url, status_code=302)
        # Binds the login to this browser, so a callback delivered to another browser fails.
        response.set_cookie(
            cfg.login_cookie,
            state,
            httponly=True,
            samesite="lax",
            secure=cfg.cookie_secure,
            path="/",
            max_age=cfg.login_ttl_seconds,
        )
        return response

    @app.get("/auth/login")
    async def login(request: Request) -> RedirectResponse:
        return begin_login(_safe_return(request.query_params.get("return")), step_up=False)

    @app.get("/auth/step-up")
    async def step_up(request: Request) -> RedirectResponse:
        await current(request)
        return begin_login(_safe_return(request.query_params.get("return")), step_up=True)

    @app.get("/auth/callback")
    async def callback(request: Request) -> Response:
        params = request.query_params
        state, code = params.get("state"), params.get("code")
        bound = request.cookies.get(cfg.login_cookie)
        if "error" in params or not state or not code or bound != state:
            return _error("LOGIN_FAILED")
        pending = deps.sessions.take_pending(state)
        if pending is None:
            return _error("LOGIN_FAILED")  # unknown, expired or replayed
        try:
            tokens = await deps.oidc.exchange_code(code, pending.verifier)
            if tokens.id_token is None:
                return _error("LOGIN_FAILED")
            deps.oidc.verify_id_token(tokens.id_token, pending.nonce)
            principal = await asyncio.to_thread(deps.access_validator.validate, tokens.access_token)
        except OidcError as exc:
            return _error("UNAVAILABLE" if exc.code.startswith("idp_") else "LOGIN_FAILED")
        except AuthError:
            return _error("LOGIN_FAILED")
        old = request.cookies.get(cfg.session_cookie)
        if old:
            deps.sessions.delete(old)  # a new session id on every login: no fixation
        session = Session(
            session_id=new_session_id(),
            subject=principal.subject,
            username=_username(tokens.access_token, principal.subject),
            roles=principal.roles,
            entities=principal.entities,
            tenant_id=principal.tenant_id,
            csrf_token=pkce.random_token(),
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            id_token=tokens.id_token,
            access_expires_at=now() + tokens.expires_in,
            acr=principal.acr,
            auth_time=principal.auth_time,
        )
        deps.sessions.create(session)
        response = RedirectResponse(pending.return_to, status_code=302)
        set_session_cookies(response, session)
        delete_cookie(response, cfg.login_cookie)
        return response

    def _username(access_token: str, fallback: str) -> str:
        import jwt

        claims = jwt.decode(access_token, options={"verify_signature": False})
        return str(claims.get("preferred_username") or claims.get("name") or fallback)

    # -- the session ----------------------------------------------------------------------------

    async def current(request: Request) -> Session:
        session_id = request.cookies.get(cfg.session_cookie)
        session = deps.sessions.get(session_id) if session_id else None
        if session is None:
            raise _Refused(_error("UNAUTHENTICATED"))
        if session.refresh_token and session.access_expires_at - now() < cfg.refresh_margin_seconds:
            try:
                fresh = await deps.oidc.refresh(session.refresh_token)
                principal = await asyncio.to_thread(
                    deps.access_validator.validate, fresh.access_token
                )
            except (OidcError, AuthError):
                deps.sessions.delete(session.session_id)  # cannot refresh: the session ends
                raise _Refused(_error("UNAUTHENTICATED")) from None
            session.access_token = fresh.access_token
            session.refresh_token = fresh.refresh_token or session.refresh_token
            session.access_expires_at = now() + fresh.expires_in
            session.acr, session.auth_time = principal.acr, principal.auth_time
        return session

    Authed = Annotated[Session, Depends(current)]

    def require_csrf(request: Request, session: Session) -> None:
        if not (
            csrf_valid(request, session.csrf_token, cfg.csrf_cookie)
            and origin_valid(request, cfg.public_url)
        ):
            raise _Refused(_error("CSRF"))

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/me")
    async def me(session: Authed) -> dict[str, Any]:
        recent = session.auth_time is not None and now() - session.auth_time <= 300
        return {
            "username": session.username,
            "roles": list(session.roles),
            "entities": list(session.entities),
            "tenant_id": session.tenant_id,
            "acr": session.acr,
            "strong_authentication": session.acr == cfg.step_up_acr and recent,
        }

    @app.post("/auth/logout")
    async def logout(request: Request, session: Authed) -> JSONResponse:
        require_csrf(request, session)
        deps.sessions.delete(session.session_id)
        response = JSONResponse({"redirect": deps.oidc.end_session_url(session.id_token)})
        clear_cookies(response)
        return response

    # -- asking ----------------------------------------------------------------------------------

    @app.post("/api/ask")
    async def ask(body: AskRequest, request: Request, session: Authed) -> StreamingResponse:
        require_csrf(request, session)
        if len(body.question) > cfg.max_question_chars:
            raise _Refused(_error("BAD_REQUEST"))
        if not limiter.allow(session.session_id):
            raise _Refused(
                JSONResponse(
                    status_code=429,
                    content={"error_code": "RATE_LIMITED"},
                    headers={"Retry-After": "60"},
                )
            )
        return StreamingResponse(
            _stream(session, body.question),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def _stream(session: Session, question: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def progress(step: str) -> None:
            await queue.put(("step", {"step": step}))

        async def work() -> None:
            try:
                answer = await deps.copilot.ask(session, question, progress)
                await queue.put(("answer", answer))
            except CopilotError as exc:
                await queue.put(("error", {"error_code": "UNAVAILABLE", "reason": exc.code}))
            except Exception as exc:
                _log.error("ask failed: %s", type(exc).__name__)  # class only, never the content
                await queue.put(("error", {"error_code": "INTERNAL"}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(work())
        try:
            while (item := await queue.get()) is not None:
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
            yield "event: done\ndata: {}\n\n"
        finally:
            task.cancel()  # the client went away: stop spending on its behalf

    # -- drafts ----------------------------------------------------------------------------------

    def _copilot_failure(exc: CopilotError) -> JSONResponse:
        mapped = {"forbidden": "FORBIDDEN", "not_found": "NOT_FOUND"}.get(exc.code, "UNAVAILABLE")
        return _error(mapped)

    @app.get("/api/drafts")
    async def drafts(session: Authed) -> JSONResponse:
        try:
            return JSONResponse({"drafts": await deps.copilot.list_drafts(session)})
        except CopilotError as exc:
            return _copilot_failure(exc)

    @app.post("/api/drafts/{draft_id}/submit")
    async def submit(draft_id: str, request: Request, session: Authed) -> JSONResponse:
        require_csrf(request, session)
        if not _DRAFT_ID.match(draft_id):
            return _error("NOT_FOUND")
        try:
            return JSONResponse(await deps.copilot.submit_draft(session, draft_id))
        except CopilotError as exc:
            return _copilot_failure(exc)

    # -- approvals: a pass-through to the approval service, with its own token -------------------

    async def approval_call(
        session: Session, method: str, path: str, body: dict[str, Any] | None = None
    ) -> JSONResponse:
        try:
            token = await deps.broker.approval_token(session.access_token)
            response = await deps.approvals.request(
                method, path, json=body, headers={"Authorization": f"Bearer {token}"}
            )
        except (BrokerError, httpx.TransportError):
            return _error("UNAVAILABLE")
        if response.status_code >= 500:
            return _error("UNAVAILABLE")
        payload = response.json()
        if payload.get("error_code") == "STEP_UP_REQUIRED":
            payload["step_up_url"] = "/auth/step-up?" + urlencode({"return": "/"})
        return JSONResponse(status_code=response.status_code, content=payload)

    @app.get("/api/approvals/queue")
    async def approvals_queue(session: Authed) -> JSONResponse:
        return await approval_call(session, "GET", "/v1/queue")

    @app.get("/api/approvals/{draft_id}")
    async def approvals_detail(draft_id: str, session: Authed) -> JSONResponse:
        if not _DRAFT_ID.match(draft_id):
            return _error("NOT_FOUND")
        return await approval_call(session, "GET", f"/v1/drafts/{draft_id}")

    @app.post("/api/approvals/{draft_id}/decision")
    async def approvals_decide(
        draft_id: str, body: DecisionBody, request: Request, session: Authed
    ) -> JSONResponse:
        require_csrf(request, session)
        if not _DRAFT_ID.match(draft_id):
            return _error("NOT_FOUND")
        return await approval_call(
            session, "POST", f"/v1/drafts/{draft_id}/decision", body.model_dump()
        )

    # -- "how I got this": the user's own trace -------------------------------------------------

    @app.get("/api/traces/{trace_id}")
    async def trace(trace_id: str, session: Authed) -> JSONResponse:
        if deps.jaeger is None or not _TRACE_ID.match(trace_id):
            return _error("NOT_FOUND")
        try:
            response = await deps.jaeger.get(f"/api/traces/{trace_id}")
        except httpx.TransportError:
            return _error("UNAVAILABLE")
        if response.status_code != httpx.codes.OK:
            return _error("NOT_FOUND")
        spans = (response.json().get("data") or [{}])[0].get("spans", [])
        owners = {
            t["value"] for s in spans for t in s.get("tags", []) if t["key"] == "copilot.user_id"
        }
        if session.subject not in owners:
            return _error("NOT_FOUND")  # someone else's run is indistinguishable from none
        return JSONResponse({"trace_id": trace_id, "spans": [_span(s) for s in spans]})

    def _span(span: dict[str, Any]) -> dict[str, Any]:
        parents = [r["spanID"] for r in span.get("references", []) if r["refType"] == "CHILD_OF"]
        return {
            "span_id": span["spanID"],
            "parent_id": parents[0] if parents else None,
            "name": span["operationName"],
            "start_us": span["startTime"],
            "duration_us": span["duration"],
            # Only a fixed allowlist of attributes ever reaches the browser.
            "attributes": {
                t["key"]: t["value"]
                for t in span.get("tags", [])
                if t["key"] in _SAFE_SPAN_ATTRIBUTES
            },
        }

    return app
