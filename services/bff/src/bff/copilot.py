"""The BFF's view of the copilot: ask, and manage the user's own drafts."""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from copilot_auth import TokenValidator
from orchestrator import InsightsOrchestrator
from policy_gateway import Allowed, Denied, Gateway, RunContext

from bff.broker import BrokerError, TokenBroker
from bff.sessions import Session

Progress = Callable[[str], Awaitable[None]]


class CopilotError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CopilotApi(Protocol):
    async def ask(self, session: Session, question: str, progress: Progress) -> dict[str, Any]: ...

    async def list_drafts(self, session: Session) -> list[dict[str, Any]]: ...

    async def submit_draft(self, session: Session, draft_id: str) -> dict[str, Any]: ...


class OrchestratorCopilot:
    """Runs the orchestrator as the signed-in user, with tokens exchanged for each hop."""

    def __init__(
        self,
        orchestrator: InsightsOrchestrator,
        gateway: Gateway,
        broker: TokenBroker,
        mcp_validator: TokenValidator,
    ) -> None:
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._broker = broker
        self._validator = mcp_validator

    async def _context(self, session: Session) -> RunContext:
        try:
            tokens = await self._broker.upstream_tokens(session.access_token)
            principal = self._validator.validate(tokens["analytics"])
        except BrokerError as exc:
            raise CopilotError(exc.code) from None
        except Exception:  # an unverifiable token means we cannot act as the user
            raise CopilotError("unavailable") from None
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        return RunContext(
            run_id=run_id, trace_id=run_id, principal=principal, upstream_tokens=tokens
        )

    async def ask(self, session: Session, question: str, progress: Progress) -> dict[str, Any]:
        answer = await self._orchestrator.run(question, await self._context(session), progress)
        return answer.model_dump(mode="json")

    async def list_drafts(self, session: Session) -> list[dict[str, Any]]:
        outcome = await self._gateway.call(
            await self._context(session), "user.drafts", "drafts.list_drafts", {}
        )
        if isinstance(outcome, Allowed):
            return list(outcome.output.get("drafts", []))
        raise CopilotError(_code(outcome))

    async def submit_draft(self, session: Session, draft_id: str) -> dict[str, Any]:
        outcome = await self._gateway.call(
            await self._context(session),
            "user.drafts",
            "drafts.submit_for_review",
            {"draft_id": draft_id},
        )
        if isinstance(outcome, Allowed):
            return dict(outcome.output)
        raise CopilotError(_code(outcome))


def _code(denial: Denied) -> str:
    if denial.reason in ("ROLE_REQUIRED", "TOOL_NOT_ALLOWED"):
        return "forbidden"
    if denial.reason == "UPSTREAM_ERROR" and (denial.detail or "").startswith("NOT_FOUND"):
        return "not_found"
    return "unavailable"
