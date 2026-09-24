set dotenv-load := true

default:
    @just --list

# Fill every CHANGEME in .env with a random value (idempotent; only touches CHANGEME lines)
gen-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    [ -f .env ] || cp .env.example .env
    rnd() { openssl rand -hex "$1"; }
    sed -i '' \
      -e "s|^POSTGRES_PASSWORD=CHANGEME|POSTGRES_PASSWORD=$(rnd 16)|" \
      -e "s|^CLICKHOUSE_PASSWORD=CHANGEME|CLICKHOUSE_PASSWORD=$(rnd 16)|" \
      -e "s|^MINIO_ROOT_PASSWORD=CHANGEME|MINIO_ROOT_PASSWORD=$(rnd 16)|" \
      -e "s|^REDIS_AUTH=CHANGEME|REDIS_AUTH=$(rnd 16)|" \
      -e "s|^LANGFUSE_SALT=CHANGEME|LANGFUSE_SALT=$(rnd 16)|" \
      -e "s|^LANGFUSE_ENCRYPTION_KEY=CHANGEME.*|LANGFUSE_ENCRYPTION_KEY=$(rnd 32)|" \
      -e "s|^LANGFUSE_NEXTAUTH_SECRET=CHANGEME|LANGFUSE_NEXTAUTH_SECRET=$(rnd 16)|" \
      -e "s|^LANGFUSE_INIT_USER_PASSWORD=CHANGEME|LANGFUSE_INIT_USER_PASSWORD=$(rnd 8)|" \
      -e "s|^LANGFUSE_LOCAL_PUBLIC_KEY=pk-lf-CHANGEME|LANGFUSE_LOCAL_PUBLIC_KEY=pk-lf-$(rnd 16)|" \
      -e "s|^LANGFUSE_LOCAL_SECRET_KEY=sk-lf-CHANGEME|LANGFUSE_LOCAL_SECRET_KEY=sk-lf-$(rnd 16)|" \
      .env
    pk=$(grep '^LANGFUSE_LOCAL_PUBLIC_KEY=' .env | cut -d= -f2)
    sk=$(grep '^LANGFUSE_LOCAL_SECRET_KEY=' .env | cut -d= -f2)
    auth=$(printf '%s:%s' "$pk" "$sk" | base64)
    sed -i '' -e "s|^LANGFUSE_OTEL_AUTH=.*|LANGFUSE_OTEL_AUTH=$auth|" .env
    echo "secrets written to .env (set LANGFUSE_INIT_USER_EMAIL yourself)"

# Start the local stack
up:
    docker compose up -d
    @echo "Langfuse  http://localhost:3000   Phoenix http://localhost:6006   Grafana http://localhost:3001"

# Start the stack with the LangSmith exporter enabled
up-cloud:
    OTEL_EXTRA_CONFIG=langsmith.yaml docker compose up -d --force-recreate otel-collector
    docker compose up -d

down:
    docker compose down

# Stop and delete all data volumes
nuke:
    docker compose down -v

logs service="otel-collector":
    docker compose logs -f {{service}}

ps:
    docker compose ps

# Validate compose and collector config without starting anything
check:
    docker compose config --quiet
    docker run --rm -v "$PWD/otel:/etc/otelcol:ro" otel/opentelemetry-collector-contrib:latest validate --config=/etc/otelcol/base.yaml

# Serve the local model with mlx_lm (OpenAI-compatible on :8080, Gemma 4 tool-call parser built in)
serve model=env("PAYERBENCH_MODEL", "mlx-community/gemma-4-31b-it-8bit"):
    HF_HOME=$HOME/models/hf mlx_lm.server --model {{model}} --host 127.0.0.1 --port 8080 \
      --chat-template-args '{"enable_thinking": false}'

# One question to the agent (traced)
chat message:
    uv run payerbench chat "{{message}}"

# Run every canned scenario (one trace each)
demo *args:
    uv run payerbench demo {{args}}

# Send one hand-built trace through the collector
smoke:
    uv run python scripts/smoke_trace.py

# Ask each local backend how many payerbench traces it holds (exit 1 if any is empty)
verify:
    @docker compose logs otel-collector --no-log-prefix 2>&1 | grep -oE '"spans": [0-9]+' | awk -F': ' '{s+=$2} END {print "      collector " s+0 " spans received"}'
    uv run python scripts/verify_backends.py

test:
    uv run pytest -q

lint:
    uv run ruff check . && uv run ruff format --check .

fmt:
    uv run ruff format . && uv run ruff check --fix .
