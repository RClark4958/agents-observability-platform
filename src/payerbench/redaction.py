"""PHI redaction, layer 2: entity recognition inside the agent process.

The collector's regex layer catches identifiers that have a shape or a known field label. This
layer catches what regex cannot: a person's name in free text ("I'm Alexis Ayers", or the model
answering "Alexis, your plan is active"). It uses Microsoft Presidio, an open-source PII/PHI
recogniser built on a spaCy named-entity model plus pattern recognisers.

Mechanics: a SpanExporter wrapper. The OpenTelemetry SDK hands finished spans to the exporter;
attributes on a finished span are frozen, so each span is copied with redacted attributes and the
copy is passed to the real OTLP exporter. Spans therefore leave the process already redacted, and
the collector layer runs second.

`redact_fn` is injectable so the exporter can be unit-tested without the 600 MB language model.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

log = logging.getLogger(__name__)

# (new_text, number_of_entities_replaced)
RedactFn = Callable[[str], tuple[str, int]]

# Presidio entity types worth the model's time on this traffic. Dates are left to the collector's
# year-only rule; LOCATION is skipped because Safe Harbor permits state names and the recogniser
# flags them constantly.
DEFAULT_ENTITIES: tuple[str, ...] = ("PERSON", "US_SSN", "EMAIL_ADDRESS", "PHONE_NUMBER")

# Attributes that never carry member text: model identifiers, span typing, tool schemas, graph
# metadata, and this project's own markers. Skipping them avoids false positives on tool
# descriptions and saves the NER call.
SKIP_KEY_PREFIXES: tuple[str, ...] = (
    "gen_ai.request.",
    "gen_ai.response.",
    "gen_ai.provider.",
    "gen_ai.operation.",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.tool.description",
    "gen_ai.agent.",
    "gen_ai.conversation.",
    "llm.model_name",
    "llm.provider",
    "llm.system",
    "llm.tools.",
    "llm.invocation_parameters",
    "llm.token_count.",
    "openinference.",
    "tool.name",
    "tool.description",
    "metadata",
    "session.id",
    "payerbench.",
    "input.mime_type",
    "output.mime_type",
)


def _looks_like_json(text: str) -> bool:
    t = text.lstrip()
    return t[:1] in ("{", "[")


def redact_value(text: str, redact_fn: RedactFn) -> tuple[str, int]:
    """Redact one attribute value.

    Attribute values are often JSON, and JSON is often nested as an escaped string inside more
    JSON (LangChain wraps tool results that way). Running an entity recogniser over the raw
    serialisation lets an entity boundary swallow a quote or a backslash and corrupt the document.
    So: if the value parses as JSON, walk it and redact only string leaves, recursing into leaves
    that are themselves JSON, then re-serialise. Otherwise treat the value as prose.
    """
    if _looks_like_json(text):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict | list):
            total = 0

            def walk(node: Any) -> Any:
                nonlocal total
                if isinstance(node, dict):
                    return {k: walk(v) for k, v in node.items()}
                if isinstance(node, list):
                    return [walk(v) for v in node]
                if isinstance(node, str):
                    new, n = redact_value(node, redact_fn)
                    total += n
                    return new
                return node

            redacted = walk(parsed)
            return json.dumps(redacted), total
    return redact_fn(text)


def _should_scan(key: str, value: Any, min_len: int) -> bool:
    if not isinstance(value, str) or len(value) < min_len:
        return False
    return not key.startswith(SKIP_KEY_PREFIXES)


class RedactingSpanExporter(SpanExporter):
    """Wraps another exporter; every string attribute is passed through `redact_fn` first."""

    def __init__(self, downstream: SpanExporter, redact_fn: RedactFn, min_len: int = 4) -> None:
        self._downstream = downstream
        self._redact = redact_fn
        self._min_len = min_len

    def _copy_with_redacted_attributes(self, span: ReadableSpan) -> ReadableSpan:
        attrs: dict[str, Any] = dict(span.attributes or {})
        replaced = 0
        for key, value in list(attrs.items()):
            if not _should_scan(key, value, self._min_len):
                continue
            try:
                new_value, n = redact_value(value, self._redact)
            except Exception:  # noqa: BLE001 - never let the redactor break export
                log.exception("presidio redaction failed on attribute %s", key)
                # Fail closed: drop the value rather than export it unredacted.
                attrs[key] = "[REDACTION_ERROR]"
                replaced += 1
                continue
            if n:
                attrs[key] = new_value
                replaced += n
        attrs["payerbench.redaction.presidio"] = "v1"
        attrs["payerbench.redaction.presidio.entities"] = replaced
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=attrs,
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._downstream.export([self._copy_with_redacted_attributes(s) for s in spans])

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._downstream.force_flush(timeout_millis)

    def shutdown(self) -> None:
        self._downstream.shutdown()


def make_presidio_redactor(
    entities: Sequence[str] = DEFAULT_ENTITIES,
    score_threshold: float = 0.5,
    model_name: str = "en_core_web_lg",
) -> RedactFn:
    """Build a redact function backed by Presidio.

    Imports lazily because the `redact` extra is optional.
    """
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_anonymizer import AnonymizerEngine
    from presidio_anonymizer.entities import OperatorConfig

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": model_name}],
        }
    ).create_engine()
    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])
    anonymizer = AnonymizerEngine()
    operators = {e: OperatorConfig("replace", {"new_value": f"[REDACTED_{e}]"}) for e in entities}
    entity_list = list(entities)

    def redact(text: str) -> tuple[str, int]:
        results = analyzer.analyze(
            text=text, entities=entity_list, language="en", score_threshold=score_threshold
        )
        if not results:
            return text, 0
        out = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
        return out.text, len(results)

    return redact


def presidio_enabled() -> bool:
    """Read PAYERBENCH_PRESIDIO: true, false, or auto (default: on when the extra is installed)."""
    setting = os.getenv("PAYERBENCH_PRESIDIO", "auto").lower()
    if setting in ("1", "true", "yes", "on"):
        return True
    if setting in ("0", "false", "no", "off"):
        return False
    try:
        import presidio_analyzer  # noqa: F401
        import spacy

        return spacy.util.is_package("en_core_web_lg")
    except ImportError:
        return False
