"""Provider setup: export spans over OTLP/HTTP without ever blocking the run."""

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter


def configure_tracing(
    service_name: str,
    *,
    otlp_endpoint: str | None = None,
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Build a provider for one service. Export is asynchronous with a bounded queue, so a slow or
    absent collector drops spans rather than slowing or failing a request.

    `otlp_endpoint` is the collector's base URL, for example http://localhost:4318.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is None and otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces", timeout=3)
    if exporter is not None:
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                max_queue_size=2048,  # bounded: overflow is dropped, never blocks
                schedule_delay_millis=1000,
                export_timeout_millis=3000,
            )
        )
    return provider
