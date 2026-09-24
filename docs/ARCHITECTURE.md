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

## Open questions to answer with data

1. Which backends preserve `gen_ai.input.messages` / `gen_ai.output.messages` as structured
   content vs. flattening to strings?
2. Does each backend recognize `gen_ai.operation.name=execute_tool` as a tool span in its UI?
3. What does each one do with a nested `invoke_agent` (sub-agent) span?
4. Cost per 1k traces once content capture is on.
5. Where redaction should live: collector processor vs. semconv "external storage + reference".
