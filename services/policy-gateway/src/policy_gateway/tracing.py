from collections.abc import Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol


class SpanHandle(Protocol):
    def set(self, values: Mapping[str, Any]) -> None: ...


class Tracer(Protocol):
    """Where `tool.call` spans go. The span is open while the call runs, so downstream spans
    nest inside it. The OpenTelemetry binding is `copilot_tracing.ToolTracer`."""

    def span(self, name: str) -> AbstractContextManager[SpanHandle]: ...


class _NullHandle:
    def set(self, values: Mapping[str, Any]) -> None:
        return None


class NullTracer:
    @contextmanager
    def span(self, name: str) -> Any:
        yield _NullHandle()
