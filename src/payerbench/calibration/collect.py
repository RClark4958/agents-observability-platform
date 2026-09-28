"""Run the agent over every scenario and record a transcript per scenario.

Output is JSONL, one line per scenario, resumable: scenarios whose id is already present in the
output file are skipped, so an interrupted collection can be re-run. Each record keeps the final
answer, the tool calls with arguments and (truncated) results, timing, the model name, the
OpenTelemetry trace id, and the deterministic gold grade with its reasons.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from payerbench.calibration.scenarios import Scenario, Transcript, grade

RESULT_LIMIT = 1500  # chars of tool result to keep; enough for judges, small enough for JSONL


def _transcript(messages: list[Any]) -> Transcript:
    """Pair each tool call with its result message by call id."""
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                calls[tc["id"]] = {"name": tc["name"], "args": tc["args"], "result": None}
                order.append(tc["id"])
        elif isinstance(m, ToolMessage) and m.tool_call_id in calls:
            content = m.content if isinstance(m.content, str) else json.dumps(m.content)
            calls[m.tool_call_id]["result"] = content[:RESULT_LIMIT]
    final = messages[-1].content if messages else ""
    if not isinstance(final, str):
        final = json.dumps(final)
    return Transcript(final_answer=final, tool_calls=[calls[i] for i in order])


def collect(
    scenarios: list[Scenario],
    out_path: Path,
    limit: int | None = None,
    progress: Any = None,
) -> dict[str, int]:
    """Run each scenario once through the agent. Returns counts by gold label."""
    from opentelemetry import trace

    from payerbench.agent import build_agent, run_turn

    out_path.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if out_path.exists():
        with out_path.open() as f:
            for line in f:
                if line.strip():
                    done.add(json.loads(line)["id"])

    agent = build_agent()
    tracer = trace.get_tracer("payerbench.calibration")
    model = os.getenv("PAYERBENCH_MODEL", "unknown")
    counts: dict[str, int] = {"pass": 0, "fail": 0, "review": 0, "error": 0, "skipped": len(done)}
    todo = [s for s in scenarios if s.id not in done]
    if limit is not None:
        todo = todo[:limit]

    with out_path.open("a") as f:
        for i, s in enumerate(todo, 1):
            conversation_id = str(uuid.uuid4())
            with tracer.start_as_current_span(
                "payerbench.chat",
                attributes={
                    "gen_ai.conversation.id": conversation_id,
                    "payerbench.scenario": s.category,
                    "payerbench.calibration.id": s.id,
                },
            ) as span:
                t0 = time.perf_counter()
                try:
                    result = run_turn(agent, s.user_text)
                    t = _transcript(result["messages"])
                    g = grade(s, t)
                    error = None
                except Exception as exc:  # noqa: BLE001 - record and continue
                    t = Transcript(final_answer="", tool_calls=[])
                    g = grade(s, t)
                    g.label = "error"
                    error = f"{type(exc).__name__}: {exc}"[:300]
                elapsed = time.perf_counter() - t0
                trace_id = format(span.get_span_context().trace_id, "032x")

            rec = {
                "id": s.id,
                "category": s.category,
                "persona": s.persona,
                "member_id": s.member_id,
                "user_text": s.user_text,
                "expected": s.expected,
                "final_answer": t.final_answer,
                "tool_calls": t.tool_calls,
                "latency_s": round(elapsed, 2),
                "model": model,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
                "gold": g.label,
                "gold_reasons": g.reasons,
                "human_label": None,  # filled in during review; overrides gold when set
                "human_note": None,
                "error": error,
            }
            f.write(json.dumps(rec) + "\n")
            f.flush()
            counts[g.label] = counts.get(g.label, 0) + 1
            if progress:
                progress(i, len(todo), s, g, elapsed)

    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
    return counts


def load_runs(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def effective_label(rec: dict[str, Any]) -> str:
    """Human label wins over the deterministic grade when present. A run that errored produced no
    usable answer, which is a failure from the member's point of view."""
    label = rec.get("human_label") or rec["gold"]
    return "fail" if label == "error" else label
