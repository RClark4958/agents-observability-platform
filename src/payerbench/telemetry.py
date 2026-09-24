"""OpenTelemetry setup. Vendor-neutral by design: OTLP to the local collector, nothing else.

Configuration is read from the standard OTEL_* environment variables (see .env.example), so the
same code ships to any backend the collector is pointed at.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def configure(service_name: str | None = None) -> TracerProvider:
    """Install a global TracerProvider that exports OTLP/HTTP to the collector. Idempotent."""
    global _configured
    if _configured:
        return trace.get_tracer_provider()  # type: ignore[return-value]

    resource = Resource.create(
        {
            "service.name": service_name or os.getenv("OTEL_SERVICE_NAME", "payerbench"),
            "service.version": os.getenv("PAYERBENCH_VERSION", "0.1.0"),
            "deployment.environment.name": os.getenv("PAYERBENCH_ENV", "local"),
        }
    )
    provider = TracerProvider(resource=resource)
    # Endpoint and protocol come from OTEL_EXPORTER_OTLP_* env vars.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def instrument_langchain() -> None:
    """Attach OpenInference's LangChain/LangGraph instrumentor to the global provider."""
    from openinference.instrumentation.langchain import LangChainInstrumentor

    LangChainInstrumentor().instrument(tracer_provider=trace.get_tracer_provider())
