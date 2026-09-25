"""Ask every local backend what it holds for the payerbench service. Exit 1 if any backend is empty.

Reads LANGFUSE_LOCAL_PUBLIC_KEY / LANGFUSE_LOCAL_SECRET_KEY from the environment (.env via direnv).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import urllib.request

LANGFUSE = os.getenv("LANGFUSE_URL", "http://localhost:3000")
PHOENIX = os.getenv("PHOENIX_URL", "http://localhost:6006")
GRAFANA = os.getenv("GRAFANA_URL", "http://localhost:3001")
SERVICE = os.getenv("OTEL_SERVICE_NAME", "payerbench")


def _get(url: str, headers: dict[str, str] | None = None, body: bytes | None = None) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read())


def langfuse() -> tuple[int, str]:
    pk, sk = os.environ["LANGFUSE_LOCAL_PUBLIC_KEY"], os.environ["LANGFUSE_LOCAL_SECRET_KEY"]
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    data = _get(
        f"{LANGFUSE}/api/public/v2/observations?limit=100",
        headers={"Authorization": f"Basic {auth}"},
    ).get("data", [])
    traces = {o["traceId"] for o in data}
    return len(traces), f"{len(data)} observations across {len(traces)} traces (v2 API)"


def phoenix() -> tuple[int, str]:
    body = json.dumps({"query": "{ projects { edges { node { name traceCount } } } }"}).encode()
    edges = _get(f"{PHOENIX}/graphql", headers={"content-type": "application/json"}, body=body)[
        "data"
    ]["projects"]["edges"]
    total = sum(e["node"]["traceCount"] for e in edges)
    detail = ", ".join(f"{e['node']['name']}={e['node']['traceCount']}" for e in edges)
    return total, f"{total} traces ({detail})"


def tempo() -> tuple[int, str]:
    # Without an explicit window Tempo searches only its most recent block; pass the last 24h.
    import time

    now = int(time.time())
    q = urllib.parse.quote(f'{{ resource.service.name = "{SERVICE}" }}')
    url = (
        f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search"
        f"?q={q}&limit=100&start={now - 86400}&end={now}"
    )
    auth = base64.b64encode(b"admin:admin").decode()
    traces = _get(url, headers={"Authorization": f"Basic {auth}"}).get("traces", [])
    return len(traces), f"{len(traces)} traces (TraceQL via Grafana proxy)"


def semconv() -> tuple[int, str]:
    """Count spans that carry the OTel GenAI attributes the collector adds from OpenInference."""
    import time

    now = int(time.time())
    auth = base64.b64encode(b"admin:admin").decode()
    counts = {}
    for op in ("chat", "execute_tool", "invoke_agent"):
        q = urllib.parse.quote(
            f'{{ resource.service.name = "{SERVICE}" && span.gen_ai.operation.name = "{op}" }}'
        )
        url = (
            f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search"
            f"?q={q}&limit=200&spss=50&start={now - 86400}&end={now}"
        )
        traces = _get(url, headers={"Authorization": f"Basic {auth}"}).get("traces", [])
        counts[op] = sum(len(t.get("spanSet", {}).get("spans", [])) for t in traces)
    detail = ", ".join(f"{k}={v}" for k, v in counts.items())
    return sum(counts.values()), f"spans with gen_ai.operation.name: {detail} (Tempo)"


def main() -> int:
    failures = 0
    for name, fn in (
        ("langfuse", langfuse),
        ("phoenix", phoenix),
        ("tempo", tempo),
        ("semconv", semconv),
    ):
        try:
            count, detail = fn()
            status = "ok " if count else "EMPTY"
            failures += count == 0
        except Exception as exc:  # noqa: BLE001
            status, detail = "ERR ", f"{type(exc).__name__}: {exc}"
            failures += 1
        print(f"{status}  {name:<9} {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
