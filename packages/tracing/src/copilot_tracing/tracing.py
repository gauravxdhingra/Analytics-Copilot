"""OpenTelemetry tracing with the decision-trace span model (Analytics Copilot.md §5.3).

Spans: agent.run -> agent.node -> gen_ai.chat / tool.call / verifier.check. Attributes carry
identity, versions, token counts and hashes. They never carry prompt or completion text: attribute
keys are fixed by the code, and any string longer than 128 characters (anything prompt-sized) is
replaced by a length-and-hash marker, so a mistake cannot leak content. Short values are kept.
Redacted content goes to the decision store, keyed by span id.

Tracing never blocks or breaks a run: exporters are asynchronous and bounded, and the span API does
not raise into application code.
"""

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, Status, StatusCode

_MAX_ATTRIBUTE_CHARS = 128


def _marker(text: str) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"redacted:{len(text)}:{digest}"


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list | tuple | set):
        cleaned = [c for v in value if (c := _clean(v)) is not None]
        return tuple(str(c) if not isinstance(c, str | bool | int | float) else c for c in cleaned)
    text = str(value)
    return _marker(text) if len(text) > _MAX_ATTRIBUTE_CHARS else text


def attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise span attributes: drop None, stringify unknown types, never keep long text."""
    cleaned = {k: _clean(v) for k, v in values.items()}
    return {k: v for k, v in cleaned.items() if v is not None}


def template_hash(template: str) -> str:
    return "sha256:" + hashlib.sha256(template.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class SpanInfo:
    trace_id: str
    span_id: str


def _ids(span: Span) -> SpanInfo:
    ctx = span.get_span_context()
    return SpanInfo(format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"))


class ModelSpan:
    """The open span of one model call. Close it with finish() or fail()."""

    def __init__(self, span: Span) -> None:
        self._span = span
        self.info = _ids(span)

    def finish(
        self, *, model: str, input_tokens: int, output_tokens: int, cost_usd: Decimal | None
    ) -> None:
        self._span.set_attributes(
            attributes(
                {
                    "gen_ai.response.model": model,
                    "gen_ai.usage.input_tokens": input_tokens,
                    "gen_ai.usage.output_tokens": output_tokens,
                    "copilot.cost_usd": cost_usd,  # omitted when unknown, never guessed
                }
            )
        )

    def fail(self, code: str) -> None:
        self._span.set_attribute("error.code", code)
        self._span.set_status(Status(StatusCode.ERROR, code))


class _SpanHandle:
    def __init__(self, span: Span) -> None:
        self._span = span

    def set(self, values: Mapping[str, Any]) -> None:
        self._span.set_attributes(attributes(values))


class ToolTracer:
    """Gives the gateway an open span around each tool call, so the spans the call produces
    downstream (MCP server, verifier, Query API) nest inside it."""

    def __init__(self, tracing: "Tracing") -> None:
        self._tracing = tracing

    @contextmanager
    def span(self, name: str) -> Iterator[_SpanHandle]:
        with self._tracing.span(name) as span:
            yield _SpanHandle(span)


class Tracing:
    def __init__(self, provider: TracerProvider | None = None) -> None:
        source = provider if provider is not None else trace.get_tracer_provider()
        self.tracer = source.get_tracer("copilot")

    # -- spans ------------------------------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        with self.tracer.start_as_current_span(name, attributes=attributes(attrs)) as span:
            yield span

    @contextmanager
    def run(
        self,
        *,
        run_id: str,
        capability: str,
        user_id: str,
        tenant_id: str,
        versions: Mapping[str, str],
    ) -> Iterator[SpanInfo]:
        attrs = {
            "copilot.run_id": run_id,
            "copilot.capability": capability,
            "copilot.user_id": user_id,
            "copilot.tenant_id": tenant_id,
            **{f"copilot.version.{k}": v for k, v in versions.items()},
        }
        with self.span("agent.run", **attrs) as span:
            yield _ids(span)

    @contextmanager
    def node(self, name: str, iteration: int | None = None) -> Iterator[None]:
        with self.span("agent.node", **{"copilot.node": name, "copilot.iteration": iteration}):
            yield

    @contextmanager
    def model_call(
        self, *, role: str, prompt_version: str, template: str, temperature: float
    ) -> Iterator[ModelSpan]:
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.temperature": temperature,
            "copilot.model.role": role,
            "copilot.prompt.template_id": prompt_version,
            "copilot.prompt.template_hash": template_hash(template),
        }
        with self.span("gen_ai.chat", **attrs) as span:
            yield ModelSpan(span)

    def annotate(self, **attrs: Any) -> None:
        """Add attributes to the current span (for outcomes known only at the end)."""
        trace.get_current_span().set_attributes(attributes(attrs))

    def tool_tracer(self) -> ToolTracer:
        return ToolTracer(self)

    # -- context -----------------------------------------------------------------------------------

    def current(self) -> SpanInfo | None:
        span = trace.get_current_span()
        return _ids(span) if span.get_span_context().is_valid else None

    def inject(self) -> dict[str, str]:
        """The W3C trace context for the current span, to send across a process boundary."""
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        return carrier

    @contextmanager
    def attach(self, carrier: Mapping[str, str]) -> Iterator[None]:
        """Continue a trace started elsewhere. A missing or invalid carrier is ignored."""
        token = otel_context.attach(propagate.extract(dict(carrier)))
        try:
            yield
        finally:
            otel_context.detach(token)
