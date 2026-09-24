from opentelemetry.sdk.trace import TracerProvider

from payerbench import telemetry


def test_configure_is_idempotent(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    first = telemetry.configure("test")
    second = telemetry.configure("test")
    assert isinstance(first, TracerProvider)
    assert first is second
