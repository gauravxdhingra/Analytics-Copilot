"""A failing model provider must fail fast, with a mapped code, and not wait out every timeout."""

import asyncio

import pytest
from copilot_models import (
    FakeClient,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
    Usage,
)
from copilot_models.resilient import ResilientModel, is_outage
from copilot_resilience import Bulkhead, CircuitBreaker


def canned(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        usage=Usage(input_tokens=1, output_tokens=1, cost_usd=None),
        model="stub-model",
        latency_ms=0,
        finish_reason="stop",
    )


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def _wrapped(inner: FakeClient, clock: Clock, limit: int = 4) -> ResilientModel:
    return ResilientModel(
        inner,
        CircuitBreaker(
            "model",
            failure_threshold=3,
            reset_seconds=30,
            counts_as_failure=is_outage,
            clock=clock,
        ),
        Bulkhead("model", limit=limit),
    )


def _down() -> ModelError:
    return ModelError(ModelErrorCode.UNAVAILABLE)


async def test_a_healthy_provider_passes_through(request_: ModelRequest) -> None:

    inner = FakeClient([canned("hi")])
    reply = await _wrapped(inner, Clock()).generate(request_)
    assert reply.text == "hi"


async def test_after_repeated_outages_calls_are_refused_without_reaching_the_provider(
    request_: ModelRequest,
) -> None:
    inner = FakeClient([_down(), _down(), _down()])
    model = _wrapped(inner, Clock())
    for _ in range(3):
        with pytest.raises(ModelError):
            await model.generate(request_)
    with pytest.raises(ModelError) as info:
        await model.generate(request_)
    assert info.value.code is ModelErrorCode.UNAVAILABLE
    assert len(inner.requests) == 3  # the fourth never left the process


async def test_timeouts_count_as_outages(request_: ModelRequest) -> None:
    inner = FakeClient([ModelError(ModelErrorCode.TIMEOUT)] * 3)
    model = _wrapped(inner, Clock())
    for _ in range(3):
        with pytest.raises(ModelError):
            await model.generate(request_)
    with pytest.raises(ModelError):
        await model.generate(request_)
    assert len(inner.requests) == 3


@pytest.mark.parametrize(
    "code",
    [
        ModelErrorCode.BAD_REQUEST,
        ModelErrorCode.INVALID_RESPONSE,
        ModelErrorCode.RATE_LIMITED,
        ModelErrorCode.AUTH,
    ],
)
async def test_errors_that_are_not_an_outage_never_open_it(
    request_: ModelRequest, code: ModelErrorCode
) -> None:
    inner = FakeClient([ModelError(code)] * 10)
    model = _wrapped(inner, Clock())
    for _ in range(10):
        with pytest.raises(ModelError) as info:
            await model.generate(request_)
        assert info.value.code is code  # the caller still sees the real mapped code
    assert len(inner.requests) == 10


async def test_it_recovers_after_the_reset_time(request_: ModelRequest) -> None:

    clock = Clock()
    inner = FakeClient([_down(), _down(), _down(), canned("back")])
    model = _wrapped(inner, clock)
    for _ in range(3):
        with pytest.raises(ModelError):
            await model.generate(request_)
    clock.now += 31
    assert (await model.generate(request_)).text == "back"


async def test_too_many_at_once_is_refused_as_unavailable(request_: ModelRequest) -> None:

    release = asyncio.Event()

    class Slow:
        async def generate(self, request: ModelRequest) -> ModelResponse:
            await release.wait()
            return canned("x")

    model = ResilientModel(Slow(), CircuitBreaker("m"), Bulkhead("m", limit=1))
    first = asyncio.create_task(model.generate(request_))
    await asyncio.sleep(0)
    with pytest.raises(ModelError) as info:
        await model.generate(request_)
    assert info.value.code is ModelErrorCode.UNAVAILABLE
    release.set()
    await first
