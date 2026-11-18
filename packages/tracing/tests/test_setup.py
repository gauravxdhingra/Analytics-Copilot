import time
from typing import Any

from copilot_tracing import Tracing, configure_tracing
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class _Slow(SpanExporter):
    def __init__(self) -> None:
        self.exported = 0

    def export(self, spans: Any) -> SpanExportResult:
        time.sleep(0.3)
        self.exported += len(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def test_a_slow_collector_does_not_slow_the_traced_code() -> None:
    exporter = _Slow()
    provider = configure_tracing("svc", exporter=exporter)
    tracing = Tracing(provider)
    started = time.monotonic()
    for _ in range(20):
        with tracing.span("work"):
            pass
    assert time.monotonic() - started < 0.2  # export happens in the background
    provider.shutdown()


def test_with_no_collector_configured_tracing_is_a_harmless_no_op() -> None:
    provider = configure_tracing("svc")
    with Tracing(provider).span("work"):
        pass
    provider.shutdown()


def test_the_service_name_is_set_on_the_resource() -> None:
    provider = configure_tracing("copilot-orchestrator")
    assert provider.resource.attributes["service.name"] == "copilot-orchestrator"
