"""Layer-2 redaction tests. The exporter wrapper is tested with a stub redactor (no model needed);
the Presidio-backed redactor is tested only when the `redact` extra is installed."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from payerbench import redaction


class CapturingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def _stub_redact(text: str) -> tuple[str, int]:
    n = text.count("Alexis Ayers")
    return text.replace("Alexis Ayers", "[REDACTED_PERSON]"), n


def _provider_with(exporter: SpanExporter) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def test_wrapper_redacts_content_attributes_and_leaves_model_fields_alone():
    sink = CapturingExporter()
    provider = _provider_with(redaction.RedactingSpanExporter(sink, _stub_redact))
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span(
        "chat local",
        attributes={
            "input.value": "Hi, I'm Alexis Ayers, member M-SYNTH-000001",
            "output.value": "Alexis Ayers, you are covered.",
            "gen_ai.request.model": "Alexis Ayers",  # skipped by key prefix
            "gen_ai.usage.input_tokens": 12,
        },
    ):
        pass
    (span,) = sink.spans
    a = dict(span.attributes)
    assert a["input.value"] == "Hi, I'm [REDACTED_PERSON], member M-SYNTH-000001"
    assert a["output.value"] == "[REDACTED_PERSON], you are covered."
    assert a["gen_ai.request.model"] == "Alexis Ayers"
    assert a["gen_ai.usage.input_tokens"] == 12
    assert a["payerbench.redaction.presidio"] == "v1"
    assert a["payerbench.redaction.presidio.entities"] == 2


def test_wrapper_preserves_span_identity_and_timing():
    sink = CapturingExporter()
    provider = _provider_with(redaction.RedactingSpanExporter(sink, _stub_redact))
    with provider.get_tracer("t").start_as_current_span("parent") as parent:
        with provider.get_tracer("t").start_as_current_span(
            "child", attributes={"x": "Alexis Ayers"}
        ):
            pass
    child = next(s for s in sink.spans if s.name == "child")
    assert child.parent.span_id == parent.get_span_context().span_id
    assert child.context.trace_id == parent.get_span_context().trace_id
    assert child.end_time >= child.start_time


def test_wrapper_fails_closed_when_redactor_raises():
    def boom(_: str) -> tuple[str, int]:
        raise RuntimeError("model exploded")

    sink = CapturingExporter()
    provider = _provider_with(redaction.RedactingSpanExporter(sink, boom))
    with provider.get_tracer("t").start_as_current_span("s", attributes={"input.value": "secret"}):
        pass
    assert dict(sink.spans[0].attributes)["input.value"] == "[REDACTION_ERROR]"


@pytest.mark.skipif(
    not redaction.presidio_enabled(), reason="presidio extra / en_core_web_lg not installed"
)
def test_presidio_finds_a_name_in_prose():
    redact = redaction.make_presidio_redactor()
    text, n = redact("Hi, this is Alexis Ayers calling about my Bronze HMO plan.")
    assert "Alexis Ayers" not in text
    assert "[REDACTED_PERSON]" in text
    assert n >= 1
    # Product and plan names are not people.
    unchanged, m = redact("Prior authorization is required for MRI and CT imaging.")
    assert m == 0 and "MRI" in unchanged


def test_json_values_are_walked_not_regexed():
    """Nested, escaped JSON (how LangChain wraps tool results) must stay valid after redaction."""
    import json

    inner = json.dumps({"member_id": "M-SYNTH-000001", "name": "Alexis Ayers", "dob": "2018-02-17"})
    outer = json.dumps({"type": "tool", "data": {"content": inner, "note": "Alexis Ayers called"}})
    out, n = redaction.redact_value(outer, _stub_redact)
    parsed = json.loads(out)  # would raise if a quote had been swallowed
    assert json.loads(parsed["data"]["content"])["name"] == "[REDACTED_PERSON]"
    assert parsed["data"]["note"] == "[REDACTED_PERSON] called"
    assert n == 2


def test_prose_values_go_straight_to_the_redactor():
    out, n = redaction.redact_value("Alexis Ayers is on the line", _stub_redact)
    assert out == "[REDACTED_PERSON] is on the line" and n == 1
