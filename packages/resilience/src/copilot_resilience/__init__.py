"""Circuit breakers and bulkheads.

A dependency that keeps failing should fail *fast*: after a few consecutive failures the breaker
opens and refuses calls at once, instead of letting every request wait out a timeout. A bulkhead
caps concurrent work so one slow capability cannot take every worker. Both refuse with an error
the caller maps to a code; neither ever lets a call through that policy has refused.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

T = TypeVar("T")
State = Literal["closed", "open", "half_open"]


class CircuitOpen(Exception):
    """The dependency is failing and is not being called. Carries only its name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"circuit open: {name}")
        self.name = name


class Overloaded(Exception):
    """Too much work is already running in this compartment."""

    def __init__(self, name: str) -> None:
        super().__init__(f"overloaded: {name}")
        self.name = name


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        counts_as_failure: Callable[[Exception], bool] = lambda exc: True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._reset = reset_seconds
        self._counts = counts_as_failure
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> State:
        if self._opened_at is None:
            return "closed"
        if self._probing or self._clock() - self._opened_at >= self._reset:
            return "half_open"
        return "open"

    async def call(self, work: Callable[[], Awaitable[T]]) -> T:
        if self._opened_at is not None:
            if self._probing or self._clock() - self._opened_at < self._reset:
                raise CircuitOpen(self.name)
            self._probing = True  # the reset time has passed: exactly one caller probes
            probe = True
        else:
            probe = False
        try:
            result = await work()
        except asyncio.CancelledError:
            if probe:
                self._probing = False  # a cancelled probe says nothing about the dependency
            raise
        except Exception as exc:
            if self._counts(exc):
                self._record_failure(probe)
            elif probe:
                self._probing = False
            raise
        self._failures = 0
        self._opened_at = None
        self._probing = False
        return result

    def _record_failure(self, probe: bool) -> None:
        self._failures += 1
        self._probing = False
        if probe or self._failures >= self._threshold:
            self._opened_at = self._clock()  # open, or reopen for another full period


class Bulkhead:
    def __init__(self, name: str, *, limit: int) -> None:
        self.name = name
        self._limit = limit
        self._running = 0

    async def call(self, work: Callable[[], Awaitable[T]]) -> T:
        if self._running >= self._limit:
            raise Overloaded(self.name)  # refuse now rather than queue without bound
        self._running += 1
        try:
            return await work()
        finally:
            self._running -= 1


__all__ = ["Bulkhead", "CircuitBreaker", "CircuitOpen", "Overloaded"]
