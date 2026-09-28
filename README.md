# agents-observability-platform

One agent, one OpenTelemetry pipeline, four observability backends.

A vendor-neutral tracing lab for LLM agents. A synthetic health-plan member-services agent
("PayerBench", built on LangGraph) is instrumented with the OpenTelemetry GenAI semantic
conventions and sends every span to a local OTel Collector. The collector redacts PHI, then fans
the same trace out to:

| Backend | Where | Why it is here |
|---|---|---|
| Langfuse | self-hosted, `http://localhost:3000` | The open-source default; MIT core |
| Arize Phoenix | self-hosted, `http://localhost:6006` | OTel/OpenInference-native |
| Grafana Tempo + Grafana | self-hosted, `http://localhost:3001` | Agent traces next to ordinary infra traces |
| LangSmith | cloud, optional | LangGraph-native; the one hosted tool worth knowing well |

The point is to learn what each backend keeps, what it drops, and what it costs, from one
identical stream of data, and to keep the agent code free of any vendor SDK.

## Status

Project 1 of [ROADMAP.md](ROADMAP.md) is complete; findings are in [docs/WRITEUP.md](docs/WRITEUP.md).

- [x] Compose stack boots (Langfuse, Phoenix, otel-lgtm, collector)
- [x] Smoke span arrives in all local backends (`just verify`)
- [x] PayerBench agent v0 with tools and synthetic data (`just demo`, local Gemma 4 31B via mlx_lm)
- [x] LangGraph instrumented via OpenInference (AGENT / CHAIN / GENERATION / TOOL spans)
- [x] Collector adds GenAI semconv attributes and span names next to OpenInference (`just verify` reports the counts)
- [x] PHI redaction in the collector, layer 1: pattern rules (`just verify --phi-since <epoch>` probes for leaks)
- [x] PHI redaction, layer 2: Presidio exporter wrapper in the agent for free-text names (`uv sync --extra redact`)
- [x] LangSmith receives the same redacted stream over OTLP (`OTEL_EXTRA_CONFIG=langsmith.yaml`)
- [~] Claude Code telemetry routed into the same collector (deferred; the collector accepts it as-is)
- [x] Write-up: [docs/WRITEUP.md](docs/WRITEUP.md), same span in four backends with measured numbers

## Layout

```
compose.yaml            Langfuse (+ Postgres, ClickHouse, Redis, MinIO), Phoenix, otel-lgtm, collector
otel/                   Collector configs: base.yaml, langsmith.yaml (cloud add-on), noop.yaml
src/payerbench/         The agent, its tools, synthetic data, and telemetry setup
scripts/                smoke_trace.py and other one-off helpers
tests/                  pytest
docs/                   Architecture notes and the eventual write-up
.env.example            Every variable the stack reads; copy to .env
justfile                up / down / logs / smoke / agent
```

## Quick start

Prerequisites: Docker Desktop with at least 24 GB of memory allotted, `uv`, `just`, `direnv`.

```bash
cp .env.example .env            # then fill in the CHANGEME values (just gen-secrets does it)
just gen-secrets                # writes random secrets into .env
direnv allow                    # sources ~/.config/secrets/ai.env and .env
just up                         # docker compose up -d
just smoke                      # sends one test trace through the collector
```

Then `just verify` asks each backend what it received, or open Langfuse (login with the init user
from `.env`), Phoenix and Grafana (admin/admin) and look for the `invoke_agent payerbench` trace.

Note: Langfuse v4 runs in events-only mode. The legacy `/api/public/traces` endpoint is disabled;
use `/api/public/v2/observations`. Spans land in the `events_core` / `events_full` ClickHouse tables.

To also ship to LangSmith, set `LANGSMITH_API_KEY` in `~/.config/secrets/ai.env` and run
`just up-cloud`.

## Conventions

- No vendor SDK in agent code. Only `opentelemetry-*` and `openinference-instrumentation-*`.
- Secrets never enter git. Real keys live in `~/.config/secrets/ai.env`; per-stack values in `.env`.
- Everything stateful runs in containers. The host only runs Python and the local model server.
- Synthetic data only. Member records are generated and are shaped like PHI so redaction is testable.

## License

MIT
