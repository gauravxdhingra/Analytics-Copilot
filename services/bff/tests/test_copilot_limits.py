"""Too many questions at once are refused with a mapped code, not queued without bound."""

import asyncio
from typing import Any

import pytest
from bff.copilot import CopilotError, OrchestratorCopilot
from bff.sessions import Session
from bff.testing import FakeBroker
from copilot_auth import Principal
from orchestrator import Answer, AnswerStatus


class SlowOrchestrator:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def run(self, question: str, ctx: Any, progress: Any = None, **kwargs: Any) -> Answer:
        await self.release.wait()
        return Answer(status=AnswerStatus.REFUSED, text="No.", refusal_reason="unsupported")


class Validator:
    def validate(self, token: str) -> Principal:
        return Principal(
            subject="u",
            tenant_id="t",
            entities=("HAL-DE",),
            roles=("analyst",),
            authorized_party="copilot-bff",
        )


def _session() -> Session:
    return Session(
        session_id="s",
        subject="u",
        username="u",
        roles=("analyst",),
        entities=("HAL-DE",),
        tenant_id="t",
        csrf_token="c",
        access_token="a",
        refresh_token=None,
        id_token=None,
    )


async def _noop(step: str) -> None:
    return None


async def test_a_burst_beyond_the_cap_is_refused_and_the_rest_still_finish() -> None:
    slow = SlowOrchestrator()
    copilot = OrchestratorCopilot(
        slow,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        FakeBroker(),
        Validator(),  # type: ignore[arg-type]
        max_concurrent_asks=2,
    )
    first = asyncio.create_task(copilot.ask(_session(), "q1", _noop))
    second = asyncio.create_task(copilot.ask(_session(), "q2", _noop))
    await asyncio.sleep(0.05)
    with pytest.raises(CopilotError) as info:
        await copilot.ask(_session(), "q3", _noop)
    assert info.value.code == "unavailable"
    slow.release.set()
    assert (await first)["status"] == "refused" and (await second)["status"] == "refused"
    slow.release.clear()


async def test_a_slot_is_free_again_afterwards() -> None:
    slow = SlowOrchestrator()
    slow.release.set()
    copilot = OrchestratorCopilot(
        slow,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        FakeBroker(),
        Validator(),  # type: ignore[arg-type]
        max_concurrent_asks=1,
    )
    for _ in range(3):
        assert (await copilot.ask(_session(), "q", _noop))["status"] == "refused"
