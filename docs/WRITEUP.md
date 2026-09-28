# One agent, one OpenTelemetry pipeline, four observability backends

*What Langfuse, Arize Phoenix, Grafana Tempo and LangSmith each keep, change, or lose when they
receive the identical span. September 2026.*

Repo: [RClark4958/agents-observability-platform](https://github.com/RClark4958/agents-observability-platform)

## TL;DR

- Instrument once with OpenTelemetry, route through a collector, fan out to every backend. No
  vendor SDK in the agent. Adding a backend is a YAML change.
- The same LLM span reports **810 input tokens in three backends and 1 in the fourth**. Both are
  "correct". Langfuse subtracts cached tokens; the others report the raw prompt count.
- Attribute fidelity for that span: **48 → 44 → 35 → 23** attributes across Tempo, Phoenix,
  Langfuse and LangSmith. Each backend reshapes the span into its own model; what looks like loss
  is mostly promotion into typed columns, except in LangSmith, which dropped every `gen_ai.*`
  attribute the collector had added.
- Two attribute vocabularies compete: OpenInference (what LangChain's instrumentor emits) and the
  OpenTelemetry GenAI semantic conventions (the standard, still marked Development). A collector
  transform can carry both; message content is the part that does not translate cleanly.
- PHI redaction needed two layers, and the cloud backend is what exposed the gap in the first
  one. Three separate bugs came from redacting a serialization instead of the data.
- Footprint: the full Langfuse stack is six containers and 6.3 GiB resident; Phoenix is one
  container and 0.9 GiB; Grafana's all-in-one image is 2.0 GiB.

## Setup

A synthetic health-plan member-services agent (LangGraph, six tools, seeded fake members with
PHI-shaped fields) runs on a local Gemma 4 31B via `mlx_lm.server` on an M5 Ultra. The
OpenInference LangChain instrumentor emits spans to an OpenTelemetry Collector, which:

1. adds OTel GenAI attribute names next to the OpenInference ones and renames spans to the
   convention's `{operation} {target}` form,
2. redacts PHI with pattern rules,
3. fans out over OTLP to Langfuse v4 (self-hosted), Arize Phoenix (self-hosted), Grafana Tempo
   (self-hosted, via the `otel-lgtm` image) and LangSmith (cloud).

A second redaction layer, Microsoft Presidio, runs inside the agent process as a wrapper around the
OTLP exporter and handles names in free text. Everything is verified by a script that asks each
backend what it holds and probes for seven known synthetic identifiers.

## The same span in four places

Final `chat` span of one conversation, span id `77ed839a01cc4b9c`, Gemma 4 31B, 1,277 ms.

| | Tempo | Phoenix | Langfuse v4 | LangSmith |
|---|---|---|---|---|
| Span classified as | `SPAN_KIND_CLIENT` (raw OTel kind) | `llm` | `GENERATION` | run type `llm` |
| Input tokens | 810 | 810 | **1** | 810 |
| Output tokens | 29 | 29 | 29 | 29 |
| Cached input tokens | 809 | 809 | 809 | not surfaced |
| Total | n/a (attributes only) | 839 | 839 | 839 |
| Attributes retained | 48 | 44 | 35 | 23 |
| `gen_ai.*` attributes | 9 | 9 | 9 (in metadata) | **0** |
| Message content | 12 flattened `llm.input_messages.N.*` keys | reconstructed list of messages | `input` 4,933 chars, `output` 1,874 chars | `inputs.messages` list |
| Model name | attribute | attribute | typed column `provided_model_name` | from LangChain's `ls_model_name` |
| Latency | 1,277 ms | 1,276.8 ms | 1,277 ms | run duration |
| Ingest lag observed | search index ~30–45 s; fetch by id immediate | immediate | 1.8–3.1 s | seconds |

Ten spans in the trace; all four backends stored all ten.

### 810 or 1?

The local server reports `prompt_tokens: 810` with `cached_tokens: 809`. Phoenix, Tempo and
LangSmith show 810 as input. Langfuse shows `input: 1, input_cached_tokens: 809, total: 839`. It has
subtracted the cached portion so that its cost math charges cached tokens at the cached rate. Both
numbers are defensible. A dashboard that compares "input tokens" across the two vendors is off by
two to three orders of magnitude on a warm cache, and nothing in either UI says so.

### 48, 44, 35, 23

- **Tempo** stores exactly what arrived: 48 attributes, including message content as 12 numbered
  keys, because OpenInference flattens lists on the wire. It has no idea what an LLM is, and that is
  its strength: it also holds the agent's traces next to any other service's, and TraceQL can query
  any attribute (`{ span.gen_ai.tool.name = "get_claim" }`).
- **Phoenix** reconstructs the flattened messages into a list (12 keys become 1), so 44. It reads
  OpenInference natively and also maps the *other* way: a span that arrives with only `gen_ai.*`
  attributes is shown with `llm.*` equivalents. It was the only backend that understood both
  vocabularies without help.
- **Langfuse** promotes model, usage, parameters, input, output and tool calls into typed columns
  and keeps the remaining 35 attribute names in metadata. Both vocabularies survive. Its v4
  release stores spans in a denormalized events table; the legacy traces API is disabled in that
  mode and the list endpoint omits the very fields (model, usage, content) that the UI and
  ClickHouse hold, which cost an hour of confusion.
- **LangSmith** maps spans to its run model and reads model name and tokens from LangChain's own
  `ls_*` metadata. It rolls token counts up the tree (the root run shows 1,449/57, the sum of its
  LLM children), stamps `OTEL_TRACE_ID`/`OTEL_SPAN_ID` into metadata so a run can be
  cross-referenced to the local backends, and drops every `gen_ai.*` attribute. It does not need
  them, since it has its own fields, but a team relying on the standard names for portability
  should know they do not survive the trip.

## Two vocabularies

LangChain's instrumentor speaks OpenInference: `openinference.span.kind`, `llm.model_name`,
`llm.token_count.prompt`, `input.value`. The OpenTelemetry GenAI semantic conventions say
`gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
`gen_ai.tool.call.arguments`, and name spans `chat {model}` / `execute_tool {tool}` /
`invoke_agent {agent}`. The conventions are still marked Development and renamed attributes as
recently as v1.37.

A collector transform (about 60 lines of OTTL, the collector's expression language) adds the
standard names next to the originals and renames the spans. Scalars translate cleanly. Message
content does not: the standard wants one structured JSON attribute with `role` and typed `parts`,
OpenInference sends dozens of flat keys, and rebuilding the former from the latter in OTTL is not
practical. The mapping therefore covers operation, model, provider, tokens, finish reasons,
sampling parameters, tool name/arguments/result and agent identity, and leaves content under the
OpenInference keys. A backend that only spoke the standard would see token counts and tool calls
but no message text.

The LangGraph graph itself has no equivalent in the standard. Node spans (`model`, `tools`,
`route`) stay as they are, with the node name and step exposed as attributes.

## Redaction: what it took to get to zero

Synthetic members have real-looking names, dates of birth, addresses and phones, so redaction can
be tested against known values.

**Layer 1, collector, pattern rules.** Member IDs become a SHA-256 pseudonym (stable per member,
so traces stay correlatable). Name/address/phone fields in JSON tool results become markers.
Every date, ISO or prose or US numeric, keeps only the year, per HIPAA Safe Harbor. SSN, email,
phone and street-address shapes in free text are masked. Runs before any exporter, so the cloud
backend never sees raw values.

**Layer 2, agent process, Presidio.** A wrapper around the OTLP exporter copies each finished span
with redacted attributes. Presidio's spaCy model finds `PERSON`, `US_SSN`, `EMAIL_ADDRESS`,
`PHONE_NUMBER`. Measured: 0.8 s engine load, then 4–25 ms per attribute on the export thread; agent
latency unchanged. The 600 MB model is an optional extra.

**Three bugs, one lesson.**
1. The first field rules matched `"name": "x"`. The probe passed. The member name reached LangSmith
   six times, because LangChain wraps tool results as a JSON string inside another JSON value, so
   the field arrives as `\"name\": \"x\"`. Found only by reading the cloud backend's payload.
2. The fix used `$1name` as a replacement. Go's regex expansion reads that as a group *named*
   "1name", which does not exist. The key vanished, leaving invalid JSON. The leak probe cannot see
   damage, so a second check now parses a redacted result back into JSON.
3. Presidio, run over the same escaped text, put an entity boundary on `Ayers\"` and swallowed the
   backslash. Same symptom. The layer now parses JSON values, redacts only string leaves, recursing
   into leaves that are themselves JSON, and re-serializes.

Redact the data, not its serialization. Test against the wire form, not the tool's output. Probe
every backend.

**Known misses.** A bare first name opening a sentence ("Alexis, yes: you are covered") is not
recognized as a person. Provider names are over-redacted because doctors are people too. Dates
reduced to year everywhere, including claim service dates, hurts debugging; a production
deployment would classify dates per field.

## Operating cost of "free"

Resident memory after a day of light use, Docker Desktop on the Mac:

| Backend | Containers | Resident memory | Images on disk |
|---|---|---|---|
| Langfuse v4 (web, worker, ClickHouse, Postgres, Redis, MinIO) | 6 | 6.3 GiB | 5.7 GB |
| Grafana otel-lgtm (Grafana, Tempo, Loki, Prometheus, collector) | 1 | 2.0 GiB | 3.4 GB |
| Arize Phoenix | 1 | 0.9 GiB | 1.6 GB |
| OTel Collector (contrib) | 1 | 59 MiB | 0.5 GB |
| LangSmith | 0 (cloud) | 0 | 0 |

ClickHouse alone is 4.1 GiB idle. Langfuse's architecture (object storage → queue → ClickHouse)
is what makes it scale; it is also why it needs six containers and lags two to three seconds.

## What I would pick

- **LangGraph team, wants the least friction:** LangSmith. It reads LangChain's metadata directly,
  auto-creates the project, and rolls up costs. Accept that standard attribute names do not survive,
  and that self-hosting is an enterprise feature.
- **Wants to own the data, has a platform team:** Langfuse. Richest typed model of the four, MIT
  core, but budget the six containers and read the v4 API notes before writing integrations.
- **Wants the least infrastructure and speaks two vocabularies:** Phoenix. One container,
  OTel-native, bidirectional convention mapping, immediate ingest.
- **Already runs Grafana:** Tempo for agent traces beside everything else, plus one LLM-aware tool
  for prompt and eval work. Tempo will not render a conversation, but it will show you where the
  9 seconds went.
- **Regulated data:** whichever you pick, redact in the collector before the exporter, and add an
  entity recognizer in-process for free text. Then probe the storage, not the pipeline.

## Gotchas worth knowing

- OTTL: indexing a missing key in parsed JSON (`ParseJSON(x)["k"]`) fails the whole condition; test
  presence with `IsMatch` on the string. Brace group references when followed by text:
  `$${1}name`. A `": "` inside an unquoted statement is parsed by YAML as a mapping. Collector 0.161
  wants `span.` prefixes on paths and `otlp_http`/`otlp_grpc` exporter names.
- Tempo search needs an explicit `start`/`end`; fetch-by-trace-id does not. Its JSON uses base64
  span ids, not hex.
- Langfuse v4 events-only mode: use `/api/public/v2/observations`; the list view omits model,
  usage and content; the data is in ClickHouse `events_full`.
- Gemma 4 thinks by default and returns reasoning in a separate field, starving tool calls of
  budget. `chat_template_kwargs: {enable_thinking: false}`. `mlx_lm.server` ships a Gemma 4
  tool-call parser.

## Caveats

One agent, one local model, one operator, a few dozen traces. Pricing is deliberately out of scope;
vendor pages change monthly. The conventions are in Development and attribute names may move.

## Next

The same traces become the dataset for a judge calibration study: hand-label a few hundred
conversations, then compare LLM judges against typed-decision models (Jev and local equivalents)
on agreement, repeatability, and calibration.
