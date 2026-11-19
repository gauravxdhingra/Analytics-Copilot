"""A breaker turns a dependency that keeps failing into an immediate, mapped refusal."""

import asyncio

import pytest
from copilot_resilience import Bulkhead, CircuitBreaker, CircuitOpen, Overloaded


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Boom(Exception):
    pass


async def _fail() -> None:
    raise Boom


async def _ok() -> str:
    return "ok"


def _breaker(clock: Clock, **kw: object) -> CircuitBreaker:
    return CircuitBreaker(
        "model",
        failure_threshold=3,
        reset_seconds=30,
        clock=clock,
        **kw,  # type: ignore[arg-type]
    )


async def _trip(breaker: CircuitBreaker) -> None:
    for _ in range(3):
        with pytest.raises(Boom):
            await breaker.call(_fail)


async def test_a_healthy_dependency_is_untouched() -> None:
    breaker = _breaker(Clock())
    for _ in range(10):
        assert await breaker.call(_ok) == "ok"
    assert breaker.state == "closed"


async def test_it_opens_after_consecutive_failures_and_then_refuses_without_calling() -> None:
    breaker, calls = _breaker(Clock()), 0
    await _trip(breaker)

    async def counted() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    with pytest.raises(CircuitOpen) as info:
        await breaker.call(counted)
    assert calls == 0 and info.value.name == "model" and breaker.state == "open"


async def test_a_success_in_between_resets_the_count() -> None:
    breaker = _breaker(Clock())
    for _ in range(2):
        with pytest.raises(Boom):
            await breaker.call(_fail)
    await breaker.call(_ok)
    for _ in range(2):
        with pytest.raises(Boom):
            await breaker.call(_fail)
    assert breaker.state == "closed"  # never three in a row


async def test_after_the_reset_time_one_probe_is_allowed_and_success_closes_it() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    await _trip(breaker)
    clock.now += 31
    assert await breaker.call(_ok) == "ok"
    assert breaker.state == "closed"


async def test_a_failed_probe_reopens_for_another_full_period() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    await _trip(breaker)
    clock.now += 31
    with pytest.raises(Boom):
        await breaker.call(_fail)
    assert breaker.state == "open"
    clock.now += 29
    with pytest.raises(CircuitOpen):
        await breaker.call(_ok)  # the clock restarted at the failed probe


async def test_only_one_probe_runs_at_a_time_while_half_open() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    await _trip(breaker)
    clock.now += 31
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "ok"

    probe = asyncio.create_task(breaker.call(slow))
    await asyncio.sleep(0)
    with pytest.raises(CircuitOpen):
        await breaker.call(_ok)  # a second caller does not get through during the probe
    release.set()
    assert await probe == "ok"


async def test_errors_that_are_the_callers_fault_do_not_count() -> None:
    breaker = _breaker(Clock(), counts_as_failure=lambda exc: not isinstance(exc, ValueError))

    async def bad_input() -> None:
        raise ValueError

    for _ in range(10):
        with pytest.raises(ValueError):
            await breaker.call(bad_input)
    assert breaker.state == "closed"


async def test_cancellation_is_not_a_failure() -> None:
    breaker = _breaker(Clock())

    async def cancelled() -> None:
        raise asyncio.CancelledError

    for _ in range(5):
        with pytest.raises(asyncio.CancelledError):
            await breaker.call(cancelled)
    assert breaker.state == "closed"


async def test_a_bulkhead_refuses_when_full_instead_of_queueing_forever() -> None:
    bulkhead = Bulkhead("insights", limit=2)
    release = asyncio.Event()

    async def hold() -> str:
        await release.wait()
        return "done"

    first = asyncio.create_task(bulkhead.call(hold))
    second = asyncio.create_task(bulkhead.call(hold))
    await asyncio.sleep(0)
    with pytest.raises(Overloaded) as info:
        await bulkhead.call(_ok)
    assert info.value.name == "insights"
    release.set()
    assert await first == await second == "done"
    assert await bulkhead.call(_ok) == "ok"  # a slot is free again


async def test_a_bulkhead_frees_its_slot_when_the_work_fails() -> None:
    bulkhead = Bulkhead("insights", limit=1)
    with pytest.raises(Boom):
        await bulkhead.call(_fail)
    assert await bulkhead.call(_ok) == "ok"
