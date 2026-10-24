"""OpenTelemetry tracing: API request -> retrieval -> rerank -> generation, ingestion steps, provider calls.

Export is opt-in: with `OTEL_EXPORTER_OTLP_ENDPOINT` unset no provider is installed and every span is a no-op
(near-zero cost). With it set, spans go over OTLP/HTTP to Jaeger, Tempo or any compatible collector.
Span attributes carry identifiers, model names, counts and timings only, never prompt or document text.
"""

import logging
from contextlib import AbstractContextManager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span

from app import __version__
from app.core.logging import trace_id_var

logger = logging.getLogger(__name__)
_configured = False


def configure_tracing(service_name: str, otlp_endpoint: str | None) -> None:
    global _configured
    if _configured or not otlp_endpoint:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name, "service.version": __version__}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info("tracing enabled", extra={"otlp_endpoint": otlp_endpoint, "service": service_name})


def span(name: str, **attributes: Any) -> AbstractContextManager[Span]:
    """Start a span; the app-level request id is attached so traces join up with JSON logs."""
    attrs = {k: v for k, v in attributes.items() if v is not None}
    if (request_id := trace_id_var.get()) is not None:
        attrs.setdefault("bioresearch.request_id", request_id)
    return trace.get_tracer("bioresearch-ai").start_as_current_span(name, attributes=attrs)
