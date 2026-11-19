"""The BFF's view of the copilot: ask, and manage the user's own drafts."""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from copilot_auth import TokenValidator
from copilot_memory import MemoryStore, Owner, Preference, Turn
from orchestrator import Answer, AnswerStatus, InsightsOrchestrator
from policy_gateway import Allowed, Denied, Gateway, RunContext

from bff.broker import BrokerError, TokenBroker
from bff.sessions import Session

Progress = Callable[[str], Awaitable[None]]

_log = logging.getLogger("bff")
_CONVERSATION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
HISTORY_TURNS = 5


class CopilotError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CopilotApi(Protocol):
    async def ask(
        self,
        session: Session,
        question: str,
        progress: Progress,
        conversation_id: str | None = None,
    ) -> dict[str, Any]: ...

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
        memory: MemoryStore | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._broker = broker
        self._validator = mcp_validator
        self._memory = memory

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

    async def ask(
        self,
        session: Session,
        question: str,
        progress: Progress,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        # Both parts of the owner come from the validated session, never from the request.
        owner = Owner(tenant_id=session.tenant_id, user_id=session.subject)
        conversation = conversation_id if _valid_conversation(conversation_id) else None
        history, preferences = await self._recall(owner, conversation)
        answer = await self._orchestrator.run(
            question,
            await self._context(session),
            progress,
            history=history,
            preferences=preferences,
        )
        await self._remember(owner, conversation, question, answer)
        return answer.model_dump(mode="json")

    async def _recall(
        self, owner: Owner, conversation: str | None
    ) -> tuple[list[Turn], list[Preference]]:
        """Context for this question. If memory is down the question is answered without it:
        memory is context, not a guardrail, and nothing about authorization depends on it."""
        if self._memory is None:
            return [], []
        memory = self._memory
        try:
            history = (
                await asyncio.to_thread(memory.recent_turns, owner, conversation, HISTORY_TURNS)
                if conversation
                else []
            )
            preferences = await asyncio.to_thread(memory.list_preferences, owner)
        except Exception as exc:
            _log.warning("memory unavailable, answering without it: %s", type(exc).__name__)
            return [], []
        return history, preferences

    async def _remember(
        self, owner: Owner, conversation: str | None, question: str, answer: Answer
    ) -> None:
        if self._memory is None or conversation is None:
            return
        turn = Turn(
            conversation_id=conversation,
            question=question,
            status=answer.status.value,
            plan=answer.plan if answer.status is AnswerStatus.ANSWERED else None,
            summary=_summary(answer),
            trace_id=answer.trace_id,
        )
        try:
            await asyncio.to_thread(self._memory.save_turn, owner, turn)
        except Exception as exc:
            _log.warning("could not remember a turn: %s", type(exc).__name__)

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


def _valid_conversation(value: str | None) -> bool:
    return value is not None and _CONVERSATION.fullmatch(value) is not None


def _summary(answer: Answer) -> str:
    """What happened, written by code. Never the model's words: those may carry outside text."""
    if answer.status is AnswerStatus.ANSWERED and answer.table is not None:
        when = f", data as of {answer.table.as_of[:10]}" if answer.table.as_of else ""
        return f"answered: {answer.table.row_count} rows{when}"
    reason = f": {answer.refusal_reason}" if answer.refusal_reason else ""
    return f"{answer.status.value}{reason}"


def _code(denial: Denied) -> str:
    if denial.reason in ("ROLE_REQUIRED", "TOOL_NOT_ALLOWED"):
        return "forbidden"
    if denial.reason == "UPSTREAM_ERROR" and (denial.detail or "").startswith("NOT_FOUND"):
        return "not_found"
    return "unavailable"
