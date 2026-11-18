"""Approval service (PL3): the human decision on a draft.

It is separate from the copilot on purpose. Its own audience means the copilot's tokens cannot
reach it; a person must hold the approver role, be entitled to the draft's entity (read from the
underlying system), differ from the draft's maker and submitter, and have authenticated strongly
and recently. Every attempt is ledgered, and a failed ledger write blocks the decision.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from copilot_auth import AuthError, AuthErrorCode, Principal, TokenValidator
from copilot_drafts import Draft, DraftError, DraftStatus
from copilot_tracing import Tracing
from fastapi import Depends, FastAPI, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from policy_gateway.ledger import Ledger, LedgerDraft, LedgerError
from pydantic import BaseModel, ConfigDict, Field

_log = logging.getLogger("approval.audit")

_STATUS = {
    "UNAUTHENTICATED": 401,
    "ROLE_REQUIRED": 403,
    "NOT_ENTITLED": 403,
    "SOD_VIOLATION": 403,
    "STEP_UP_REQUIRED": 403,
    "NOT_FOUND": 404,
    "NOT_SUBMITTED": 409,
    "BAD_REQUEST": 422,
    "UNAVAILABLE": 503,
}
_MESSAGE = {
    "UNAUTHENTICATED": "Authentication is required.",
    "ROLE_REQUIRED": "Only approvers may decide drafts.",
    "NOT_ENTITLED": "You are not entitled to decide drafts for this entity.",
    "SOD_VIOLATION": "You cannot decide a draft you made or submitted.",
    "STEP_UP_REQUIRED": "Stronger, more recent authentication is required to decide a draft.",
    "NOT_FOUND": "No such draft.",
    "NOT_SUBMITTED": "Only a submitted draft can be decided, and only once.",
    "BAD_REQUEST": "The request is malformed.",
    "UNAVAILABLE": "The approval service is temporarily unavailable.",
}


@dataclass(frozen=True)
class ApprovalConfig:
    required_role: str = "approver"
    step_up_acr: frozenset[str] = frozenset({"2", "mfa"})
    max_auth_age_seconds: int = 300
    policy_version: int = 1


class Repo(Protocol):
    def get(self, draft_id: str) -> Draft | None: ...

    def queue(self, entities: list[str]) -> list[Draft]: ...

    def decide(
        self, draft_id: str, decision: Literal["approved", "rejected"], by: str, comment: str | None
    ) -> Draft: ...


class Entitlements(Protocol):
    def can_access(self, subject: str, entity_id: str) -> bool: ...

    def entities_for(self, subject: str) -> list[str]: ...


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)


class _Refused(Exception):  # internal control flow
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _error(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=_STATUS[code], content={"error_code": code, "message": _MESSAGE[code]}
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def create_app(
    config: ApprovalConfig,
    *,
    validator: TokenValidator,
    repo: Repo,
    entitlements: Entitlements,
    ledger: Ledger,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    tracing: Tracing | None = None,
) -> FastAPI:
    app = FastAPI(title="Approval service", docs_url=None, redoc_url=None)

    @app.exception_handler(_Refused)
    async def _refused(_request: Request, exc: _Refused) -> JSONResponse:
        return _error(exc.code)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error("BAD_REQUEST")

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise _Refused("UNAUTHENTICATED")
        try:
            return await run_in_threadpool(validator.validate, token)
        except AuthError as exc:
            if exc.code is AuthErrorCode.IDP_UNAVAILABLE:
                raise _Refused("UNAVAILABLE") from None
            raise _Refused("UNAUTHENTICATED") from None

    Caller = Annotated[Principal, Depends(authenticate)]

    def record(
        who: Principal, draft_id: str, decision: str, allowed: bool, reason: str | None
    ) -> None:
        """Ledger an attempt. A failure here blocks the decision (fail closed)."""
        args = {"draft_id": draft_id, "decision": decision}
        current = tracing.current() if tracing else None
        try:
            ledger.append(
                LedgerDraft(
                    run_id=draft_id,
                    trace_id=current.trace_id if current else "",
                    tenant_id=who.tenant_id,
                    agent_id="approval-service",
                    user_id=who.subject,
                    tool="drafts.decision",
                    args_hash=_digest(args),
                    args_redacted=args,
                    decision="ALLOW" if allowed else "DENY",
                    reason_code=reason,
                    policy_version=config.policy_version,
                    result_hash=None,
                )
            )
        except LedgerError:
            raise _Refused("UNAVAILABLE") from None

    def require_role(who: Principal) -> None:
        if config.required_role not in who.roles:
            raise _Refused("ROLE_REQUIRED")

    def require_step_up(who: Principal) -> None:
        recent = (
            who.auth_time is not None
            and clock().timestamp() - who.auth_time <= config.max_auth_age_seconds
        )
        if who.acr not in config.step_up_acr or not recent:
            raise _Refused("STEP_UP_REQUIRED")

    def load(draft_id: str) -> Draft:
        try:
            draft = repo.get(draft_id)
        except DraftError:
            raise _Refused("UNAVAILABLE") from None
        if draft is None:
            raise _Refused("NOT_FOUND")
        return draft

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}  # liveness only: no dependency calls, nothing to leak

    @app.get("/v1/queue")
    async def queue(who: Caller) -> JSONResponse:
        require_role(who)
        entities = entitlements.entities_for(who.subject)
        try:
            items = repo.queue(entities)
        except DraftError:
            raise _Refused("UNAVAILABLE") from None
        # Show only what this person could actually decide.
        mine = [d for d in items if who.subject not in (d.created_by, d.submitted_by)]
        return JSONResponse({"drafts": [d.model_dump(mode="json") for d in mine]})

    @app.get("/v1/drafts/{draft_id}")
    async def detail(draft_id: str, who: Caller) -> JSONResponse:
        require_role(who)
        draft = load(draft_id)
        if not entitlements.can_access(who.subject, draft.entity_id):
            raise _Refused("NOT_ENTITLED")
        return JSONResponse(draft.model_dump(mode="json"))

    @app.post("/v1/drafts/{draft_id}/decision")
    async def decide(draft_id: str, body: DecisionRequest, who: Caller) -> JSONResponse:
        outcome: Literal["approved", "rejected"] = (
            "approved" if body.decision == "approve" else "rejected"
        )
        with ExitStack() as stack:
            if tracing:
                stack.enter_context(
                    tracing.span(
                        "human.decision",
                        **{
                            "copilot.actor": who.subject,
                            "copilot.draft_id": draft_id,
                            "copilot.decision": outcome,
                            "copilot.edit_size": 0,  # a decision never edits the draft
                        },
                    )
                )
            try:
                require_role(who)
                draft = load(draft_id)
                if draft.status is not DraftStatus.SUBMITTED:
                    raise _Refused("NOT_SUBMITTED")
                if not entitlements.can_access(who.subject, draft.entity_id):
                    raise _Refused("NOT_ENTITLED")
                if who.subject in (draft.created_by, draft.submitted_by):
                    raise _Refused("SOD_VIOLATION")
                require_step_up(who)
            except _Refused as refusal:
                record(who, draft_id, body.decision, False, refusal.code)
                raise
            record(who, draft_id, body.decision, True, None)  # authorised: record before acting
            try:
                decided = repo.decide(draft_id, outcome, who.subject, body.comment)
            except DraftError as exc:
                code = {
                    "SOD_VIOLATION": "SOD_VIOLATION",
                    "ILLEGAL_TRANSITION": "NOT_SUBMITTED",
                    "NOT_FOUND": "NOT_FOUND",
                }.get(exc.code, "UNAVAILABLE")
                record(who, draft_id, body.decision, False, f"DB_{code}")
                raise _Refused(code) from None
        _log.info("draft %s %s by %s", draft_id, outcome, who.subject)
        return JSONResponse(decided.model_dump(mode="json"))

    return app
