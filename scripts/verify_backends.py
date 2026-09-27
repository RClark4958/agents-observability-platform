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


# Values that exist in the synthetic world and must never reach a backend once redaction is on.
# Members 000001 (eligibility scenario) and 000003 (denied-claim scenario),
# with a date of birth and a claim service date.
PHI_PROBES = (
    "M-SYNTH-000001",
    "Alexis Ayers",
    "2018-02-17",
    "M-SYNTH-000003",
    "Daniel Ramos",
    "2026-01-31",
    "January 31, 2026",
)


def phi_leaks(since_epoch: int) -> tuple[int, str]:
    """Search Tempo for spans newer than `since_epoch` whose attributes contain a known
    synthetic identifier. Returns the number of leaking spans; 0 means redaction held."""
    import time

    now = int(time.time())
    auth = base64.b64encode(b"admin:admin").decode()
    leaks: dict[str, int] = {}
    for probe in PHI_PROBES:
        # TraceQL regex match across the attributes that carry content.
        conds = " || ".join(
            f'span.{k} =~ ".*{probe}.*"'
            for k in (
                "input.value",
                "output.value",
                "gen_ai.tool.call.result",
                "gen_ai.tool.call.arguments",
                "llm.input_messages.0.message.content",
            )
        )
        q = urllib.parse.quote(f'{{ resource.service.name = "{SERVICE}" && ({conds}) }}')
        url = (
            f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search"
            f"?q={q}&limit=200&spss=50&start={since_epoch}&end={now}"
        )
        traces = _get(url, headers={"Authorization": f"Basic {auth}"}).get("traces", [])
        leaks[probe] = sum(len(t.get("spanSet", {}).get("spans", [])) for t in traces)
    # A window with no spans at all would make "0 leaks" meaningless; report that as a failure.
    q = urllib.parse.quote(f'{{ resource.service.name = "{SERVICE}" }}')
    url = (
        f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/search"
        f"?q={q}&limit=200&spss=50&start={since_epoch}&end={now}"
    )
    examined = sum(
        len(t.get("spanSet", {}).get("spans", []))
        for t in _get(url, headers={"Authorization": f"Basic {auth}"}).get("traces", [])
    )
    detail = ", ".join(f'"{k}"={v}' for k, v in leaks.items())
    if examined == 0:
        return -1, f"no payerbench spans in Tempo since {since_epoch}; nothing examined"
    return sum(leaks.values()), f"{examined} spans examined, probe hits: {detail}"


def langsmith() -> tuple[int, str]:
    """Count root runs in the LangSmith project over the last 24h. Skipped without an API key."""
    import datetime as dt

    key = os.getenv("LANGSMITH_API_KEY")
    if not key:
        return -1, "LANGSMITH_API_KEY not set; skipped"
    project = os.getenv("LANGSMITH_PROJECT", "payerbench")
    base = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    headers = {"x-api-key": key, "content-type": "application/json"}
    sessions = _get(f"{base}/api/v1/sessions?name={urllib.parse.quote(project)}", headers)
    if not sessions:
        return 0, f"project {project!r} does not exist yet"
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=1)).isoformat()
    body = json.dumps(
        {"session": [sessions[0]["id"]], "is_root": True, "start_time": since, "limit": 100}
    ).encode()
    runs = _get(f"{base}/api/v1/runs/query", headers, body).get("runs", [])
    return len(runs), f"{len(runs)} root runs in project {project!r} (last 24h)"


def redaction_shape() -> tuple[int, str]:
    """Fetch the newest check_eligibility tool span from Phoenix and prove two things: the
    redacted result still parses as JSON (keys survived) and the member fields carry redaction
    markers. Returns the number of problems found; 0 is a pass."""
    body = json.dumps(
        {
            "query": "{ projects { edges { node { spans(first: 60, "
            "sort: {col: startTime, dir: desc}) { edges { node { name attributes } } } } } } }"
        }
    ).encode()
    edges = _get(f"{PHOENIX}/graphql", {"content-type": "application/json"}, body)["data"][
        "projects"
    ]["edges"][0]["node"]["spans"]["edges"]
    for e in edges:
        node = e["node"]
        if node["name"] != "execute_tool check_eligibility":
            continue
        attrs = json.loads(node["attributes"])
        raw = attrs.get("gen_ai", {}).get("tool", {}).get("call", {}).get("result")
        if not raw:
            return 1, "tool span found but gen_ai.tool.call.result is missing"
        outer = json.loads(raw)  # raises if the redaction broke the JSON
        inner = json.loads(outer["data"]["content"]) if "data" in outer else outer
        problems = []
        if inner.get("name") != "[REDACTED_NAME]":
            problems.append(f"name={inner.get('name')!r}")
        if not str(inner.get("member_id", "")).startswith("MEMBER-"):
            problems.append(f"member_id={inner.get('member_id')!r}")
        if not str(inner.get("dob", "")).endswith("-XX-XX"):
            problems.append(f"dob={inner.get('dob')!r}")
        detail = "redacted tool result parses; " + (
            "fields ok" if not problems else "problems: " + ", ".join(problems)
        )
        return len(problems), detail
    return 1, "no execute_tool check_eligibility span among the newest 60 in Phoenix"


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--phi-since",
        type=int,
        default=None,
        help="epoch seconds; also run the PHI leak check on spans newer than this",
    )
    args = ap.parse_args()

    failures = 0
    for name, fn in (
        ("langfuse", langfuse),
        ("phoenix", phoenix),
        ("tempo", tempo),
        ("langsmith", langsmith),
        ("semconv", semconv),
    ):
        try:
            count, detail = fn()
            # -1 means the check was skipped on purpose (no credentials); not a failure.
            status = "skip" if count < 0 else ("ok " if count else "EMPTY")
            failures += count == 0
        except Exception as exc:  # noqa: BLE001
            status, detail = "ERR ", f"{type(exc).__name__}: {exc}"
            failures += 1
        print(f"{status}  {name:<9} {detail}")

    if args.phi_since is not None:
        try:
            count, detail = phi_leaks(args.phi_since)
            status = "ok " if count == 0 else ("EMPTY" if count < 0 else "LEAK")
            failures += count != 0
        except Exception as exc:  # noqa: BLE001
            status, detail = "ERR ", f"{type(exc).__name__}: {exc}"
            failures += 1
        print(f"{status}  {'phi':<9} {detail}")
        try:
            count, detail = redaction_shape()
            status = "ok " if count == 0 else "FAIL"
            failures += count != 0
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAIL", f"redacted JSON no longer parses: {type(exc).__name__}: {exc}"
            failures += 1
        print(f"{status}  {'shape':<9} {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
