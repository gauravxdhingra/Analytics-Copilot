"""A model client wrapped in a circuit breaker and a bulkhead.

An outage (the provider unreachable, or timing out) opens the breaker, so the next requests are
refused at once with a mapped code instead of each waiting out a timeout. Errors that are not an
outage (a bad request, an invalid answer, a rate limit, a rejected key) never open it, and the
caller still sees their real code. Too many calls at once are refused rather than queued.
"""

from copilot_resilience import Bulkhead, CircuitBreaker, CircuitOpen, Overloaded

from copilot_models.errors import ModelError, ModelErrorCode
from copilot_models.port import ModelClient, ModelRequest, ModelResponse

_OUTAGES = {ModelErrorCode.UNAVAILABLE, ModelErrorCode.TIMEOUT}


def is_outage(exc: Exception) -> bool:
    return isinstance(exc, ModelError) and exc.code in _OUTAGES


class ResilientModel:
    def __init__(self, inner: ModelClient, breaker: CircuitBreaker, bulkhead: Bulkhead) -> None:
        self._inner = inner
        self._breaker = breaker
        self._bulkhead = bulkhead

    @property
    def inner(self) -> ModelClient:
        return self._inner

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            return await self._bulkhead.call(
                lambda: self._breaker.call(lambda: self._inner.generate(request))
            )
        except CircuitOpen:
            raise ModelError(ModelErrorCode.UNAVAILABLE, "circuit_open") from None
        except Overloaded:
            raise ModelError(ModelErrorCode.UNAVAILABLE, "overloaded") from None


def resilient(
    inner: ModelClient,
    *,
    name: str = "model",
    failure_threshold: int = 5,
    reset_seconds: float = 30.0,
    max_concurrent: int = 8,
) -> ResilientModel:
    return ResilientModel(
        inner,
        CircuitBreaker(
            name,
            failure_threshold=failure_threshold,
            reset_seconds=reset_seconds,
            counts_as_failure=is_outage,
        ),
        Bulkhead(name, limit=max_concurrent),
    )
