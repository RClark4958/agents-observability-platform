# Architecture

```
                         ┌──────────────────────────────┐
  mlx_lm.server /        │  PayerBench agent (LangGraph) │
  llama-server  ◀────────┤  OTel SDK + OpenInference     │
  :8080/v1               │  GenAI semconv v1.41          │
                         └──────────────┬───────────────┘
                                        │ OTLP/HTTP :4318
                                        ▼
                         ┌──────────────────────────────┐
  Dev-tool OTLP    ─────▶│  OTel Collector (contrib)     │
  LiteLLM OTLP     ─────▶│  memory_limiter → redact →   │
                         │  batch                        │
                         └──┬──────────┬────────┬───────┘
                            │          │        │        (cloud profile)
                            ▼          ▼        ▼              ▼
                        Langfuse    Phoenix   Tempo        LangSmith
                        :3000       :6006     (Grafana     api.smith.langchain.com
                                               :3001)
```

## Decisions

- **Collector in the middle.** The agent never imports a vendor SDK. Adding or removing a backend
  is a change to `otel/*.yaml`, not to code. This is also the only place PHI redaction has to be
  correct.
- **Langfuse headless init.** Project and API keys are declared in `.env` and created on first
  boot, so the collector's basic-auth header is known before Langfuse is up.
- **Two collector config files.** `base.yaml` is the local pipeline; `langsmith.yaml` is merged
  on top for the cloud profile. Collector config merge replaces lists, so the overlay restates the
  full exporter list.
- **Grafana on 3001.** Langfuse owns 3000 and its `NEXTAUTH_URL` is baked into links.
- **Phoenix over OTLP/HTTP.** Phoenix accepts OTLP on its UI port; gRPC is exposed to the host
  only for debugging.
- **Semconv opt-in.** `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` selects the
  v1.37+ attribute names (`gen_ai.provider.name`, `gen_ai.input.messages`, ...). The conventions
  are still Development status; pin instrumentation versions and expect renames.

## OpenInference to GenAI semantic conventions (collector transform)

The LangChain instrumentor speaks OpenInference. The collector's `transform/openinference-to-genai`
processor adds the OpenTelemetry GenAI attribute names next to the originals and renames spans to
the convention's `{operation} {target}` form, so the agent code stays free of any convention-specific
work and both vocabularies reach every backend.

| OpenInference (as emitted)                       | GenAI semconv (added)                    | Note |
|--------------------------------------------------|------------------------------------------|------|
| `openinference.span.kind = LLM`                  | `gen_ai.operation.name = chat`           | span renamed `chat {model}`, kind CLIENT |
| `llm.model_name`                                 | `gen_ai.request.model`                   | |
| `llm.provider` (fallback `llm.system`)           | `gen_ai.provider.name`                   | |
| `llm.token_count.prompt` / `.completion`         | `gen_ai.usage.input_tokens` / `.output_tokens` | |
| `llm.token_count.prompt_details.cache_read`      | `gen_ai.usage.cache_read.input_tokens`   | |
| `llm.finish_reason`                              | `gen_ai.response.finish_reasons` (array) | |
| `metadata` JSON: `ls_temperature`, `ls_max_tokens` | `gen_ai.request.temperature`, `.max_tokens` | LangChain packs these in one JSON string |
| `openinference.span.kind = TOOL`                 | `gen_ai.operation.name = execute_tool`   | span renamed `execute_tool {tool}` |
| `tool.name`, `tool.description`                  | `gen_ai.tool.name`, `gen_ai.tool.description` | `gen_ai.tool.type = function` |
| `input.value` / `output.value` on TOOL spans     | `gen_ai.tool.call.arguments` / `.result` | opt-in content in the convention |
| `openinference.span.kind = AGENT`                | `gen_ai.operation.name = invoke_agent`   | |
| LangGraph root CHAIN (`ls_integration=langgraph`, no node) | `gen_ai.operation.name = invoke_agent`, `gen_ai.agent.name` | the compiled graph is the agent |
| LangGraph node CHAIN (`langgraph_node` present)  | none; `payerbench.langgraph.node` / `.step` added | the convention has no "graph step" operation |

Deliberately not mapped: `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.tool.definitions`.
OpenInference flattens messages into `llm.input_messages.N.message.*` keys on the wire; rebuilding the
convention's structured JSON from flat keys is impractical in OTTL. Langfuse and Phoenix read the
OpenInference keys directly, so nothing is lost for those two. A backend that only understands the
GenAI convention would see token counts and tool calls but not message text. The original span name is
kept in `payerbench.original_name`.

Observed after mapping:
- Phoenix classifies the renamed spans exactly as before (llm / tool / chain) and shows the added
  `gen_ai.*` attributes alongside the OpenInference ones. Phoenix also maps the other direction: a span
  that arrives with only `gen_ai.*` attributes (the smoke script) is shown with `llm.*` equivalents.
- Langfuse keeps GENERATION / TOOL / CHAIN typing, model name and usage. Langfuse reports
  `input` as prompt tokens minus cached tokens (e.g. 1473 prompt, 1273 cached -> input 200), while
  Phoenix reports the raw prompt count. Same span, two token accountings.
- LangSmith (cloud, via the `langsmith.yaml` overlay) auto-creates the project on first receipt and
  maps spans to run types chain / llm / tool. It reads the model name from LangChain's own
  `ls_model_name` metadata and rolls token counts up the tree (the root chain run shows the sum of
  its LLM children). It keeps LangGraph metadata and stamps `OTEL_TRACE_ID` / `OTEL_SPAN_ID` into run
  metadata, so a run can be cross-referenced to the same trace in the local backends.
- Tempo makes the added attributes queryable with TraceQL, e.g.
  `{ span.gen_ai.operation.name = "execute_tool" && span.gen_ai.tool.name = "get_claim" }`.
- OTTL gotcha: indexing a missing key in a parsed JSON map (`ParseJSON(x)["k"] == nil`) raises
  "key not found in map" and the whole condition fails. Test key presence with `IsMatch` on the JSON
  string instead.

## PHI redaction

Two layers, by design. Neither is a redaction engine we wrote.

**Layer 1, in the collector (`transform/phi-redaction`, config only).** Regex rewrites applied to every
string attribute of every span, after the semconv transform and before any exporter. It covers the
HIPAA Safe Harbor identifiers that have a recognisable shape in this agent's traffic:

| Identifier | Rule | Result |
|---|---|---|
| Member ID `M-SYNTH-nnnnnn` | SHA-256 pseudonym | `MEMBER-<hash>`; the same member hashes the same everywhere, so traces stay correlatable |
| `"name"`, `"first_name"`, `"last_name"` JSON fields | field-aware replace | `"name": "[REDACTED_NAME]"` |
| `"address"`, `"phone"` JSON fields | field-aware replace | `[REDACTED_ADDRESS]`, `[REDACTED_PHONE]` |
| Any ISO date | keep year only | `2026-01-31` -> `2026-XX-XX` (Safe Harbor keeps only the year of dates tied to a person) |
| SSN, email, phone, US street address in free text | shape regex | `[REDACTED_*]` |

Every span gets `payerbench.redaction = regex-v1` so a reader can tell which rule set was in force.

Trade-off accepted: dates are reduced to year everywhere, including claim service dates and
prior-auth decision dates, which hurts debugging. That is the Safe Harbor rule; a production
deployment might instead classify dates per field. Claim IDs, CPT codes, provider names and plan
names are not patient identifiers and pass through.

**Layer 2, in the agent process (`src/payerbench/redaction.py`).** Names spoken in free text ("I'm
Alexis Ayers") have no shape a regex can catch. Microsoft Presidio (open-source PII/PHI recogniser:
a spaCy named-entity model plus pattern recognisers) runs inside the agent's OpenTelemetry SDK
pipeline as a wrapper around the OTLP exporter. Finished spans have frozen attributes, so the
wrapper builds a copy of each span with redacted attributes and hands the copy to the real exporter.
Spans therefore leave the process already redacted; the collector layer runs second and sees, for
example, `"name": "[REDACTED_PERSON]"` rather than the name.

- Entities: `PERSON`, `US_SSN`, `EMAIL_ADDRESS`, `PHONE_NUMBER`. Dates are left to the collector's
  year-only rule. `LOCATION` is skipped because Safe Harbor permits state names and the recogniser
  fires on them constantly.
- Scanned: every string attribute except model identifiers, span typing, tool schemas, LangGraph
  metadata and this project's own markers. Those never carry member text, and skipping them avoids
  false positives on tool descriptions.
- Fail closed: if the recogniser throws, the attribute is exported as `[REDACTION_ERROR]`, never raw.
- Markers: `payerbench.redaction.presidio = v1` and `payerbench.redaction.presidio.entities = N`.
- Cost: spaCy's large English model on CPU, roughly tens of milliseconds per attribute, on the
  export thread, so the agent's latency is unaffected. The model is ~600 MB and optional
  (`uv sync --extra redact`); `PAYERBENCH_PRESIDIO=auto` turns the layer on when it is installed.
- Known over-redaction: provider names ("Dr. John Hill") are people too, so they become
  `[REDACTED_PERSON]` even though provider identity is not patient PHI.
- Known misses (measured 2026-09-28, spaCy en_core_web_lg): a bare first name opening a
  sentence ("Alexis, yes: you are covered") is not recognised as a person; Faker's
  extension-style phone numbers ("001-555-123-4567x123") are not recognised as phone numbers in
  prose, though the collector's `"phone"` field rule catches them in tool results. Full names in
  prose and in JSON fields are caught. Measured cost: engine load 0.8 s once, then 4–25 ms per
  attribute on the export thread.

This is the one place a few lines of Python are unavoidable; it is still standard OpenTelemetry,
not a vendor SDK, and the redactor itself is Presidio, not ours. The reason it is not the only
layer: the collector layer also protects telemetry from sources we do not control (IDE agents, the
model gateway) and is enforced by platform config rather than by each application remembering to
install a processor.

**Escaping lesson.** The first version of the field rules matched `"name": "x"` and passed the
probe, yet the member name reached LangSmith six times. LangChain wraps tool results as a JSON
string inside the span's `output.value` JSON, so the field arrives as `\"name\": \"x\"` and a rule
written for bare quotes never sees it. The rules now treat every quote as an optional backslash plus
a quote and re-emit whatever they matched, so the enclosing document stays valid. Lesson for the
write-up: test redaction against the wire form of the data, not against the tool's own output, and
probe every backend, since it was the cloud one that exposed the gap.

**Entity-boundary lesson (layer 2).** Running the recogniser over a raw serialisation broke JSON
the same way the regex layer had: the entity span for `Alexis Ayers\"` swallowed the backslash, the
quote became bare, and the downstream field rule faithfully re-emitted the bare quote. The shape
check caught it. The layer now parses any value that is JSON, recurses into string leaves that are
themselves JSON, redacts only leaf strings, and re-serialises. Same lesson as before from a
different direction: redact the data, not its serialisation.

**Group-reference lesson.** OTTL replacements use Go's `regexp.Expand` syntax, where `$1name` is
read as a group *named* "1name" (which does not exist and expands to nothing). A replacement that
concatenates a group with literal text must brace the group: `${1}name`, written `$${1}name` inside
collector config because `$` is also the config's environment-variable marker. The first version
silently deleted the `name` key while redacting its value, leaving invalid JSON in every backend;
the probe did not catch it because the probe looks for leaks, not for damage. Verification now also
parses a redacted tool result back into JSON.

**Verification.** `just verify --phi-since <epoch>` searches Tempo for spans newer than the given time
whose content attributes contain known synthetic identifiers (a member ID, a member name, a service
date). Zero hits means layer 1 held for everything Tempo received; since all exporters receive the
same processed span, the same holds for Langfuse, Phoenix and LangSmith.

## Open questions to answer with data

1. Which backends preserve `gen_ai.input.messages` / `gen_ai.output.messages` as structured
   content vs. flattening to strings?
2. Does each backend recognize `gen_ai.operation.name=execute_tool` as a tool span in its UI?
3. What does each one do with a nested `invoke_agent` (sub-agent) span?
4. Cost per 1k traces once content capture is on.
5. Where redaction should live: collector processor vs. semconv "external storage + reference".
