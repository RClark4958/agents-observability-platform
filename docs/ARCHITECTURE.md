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
  Claude Code OTLP ─────▶│  OTel Collector (contrib)     │
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
- Tempo makes the added attributes queryable with TraceQL, e.g.
  `{ span.gen_ai.operation.name = "execute_tool" && span.gen_ai.tool.name = "get_claim" }`.
- OTTL gotcha: indexing a missing key in a parsed JSON map (`ParseJSON(x)["k"] == nil`) raises
  "key not found in map" and the whole condition fails. Test key presence with `IsMatch` on the JSON
  string instead.

## Open questions to answer with data

1. Which backends preserve `gen_ai.input.messages` / `gen_ai.output.messages` as structured
   content vs. flattening to strings?
2. Does each backend recognize `gen_ai.operation.name=execute_tool` as a tool span in its UI?
3. What does each one do with a nested `invoke_agent` (sub-agent) span?
4. Cost per 1k traces once content capture is on.
5. Where redaction should live: collector processor vs. semconv "external storage + reference".
