"""Run the seed pack against the agent under each defense configuration.

One record per (case, config, rep) in JSONL; the file is resumable, so re-running skips keys that
are already present. Each conversation runs inside a fresh session (verified caller, plants,
defenses) and every detector is applied to the full, untruncated transcript. The stored tool
results are truncated for size.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from payerbench.redteam.cases import Case, tools_for
from payerbench.redteam.detect import Outcome, run_all, utility
from payerbench.session import DEFENSES, Session, use_session

CONFIGS: dict[str, tuple[str, ...]] = {
    "none": (),
    **{d: (d,) for d in DEFENSES},
    "all": DEFENSES,
    "minimal": (),
    "minimal+all": DEFENSES,
}
# Configs that use the minimal (P1/P2-style) system prompt instead of the full session prompt.
MINIMAL_PROMPT = {"minimal", "minimal+all"}
STORE_LIMIT = 800


def _tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                calls[tc["id"]] = {"name": tc["name"], "args": tc["args"], "result": None}
                order.append(tc["id"])
        elif isinstance(m, ToolMessage) and m.tool_call_id in calls:
            c = m.content if isinstance(m.content, str) else json.dumps(m.content)
            calls[m.tool_call_id]["result"] = c
    return [calls[i] for i in order]


def run_case(
    case: Case, config: str, classifier: Any = None, history: list[Any] | None = None
) -> dict[str, Any]:
    """One conversation. Returns the record written to JSONL.

    `history` seeds earlier turns (LangChain messages), for drivers such as promptfoo's multi-turn
    strategies that keep the conversation themselves.
    """
    from opentelemetry import trace

    from payerbench.agent import build_agent, run_turn

    session = Session(
        member_id=case.caller,
        plants=dict(case.plants),
        defenses=frozenset(CONFIGS[config]),
        prompt="minimal" if config in MINIMAL_PROMPT else "full",
        classifier=classifier,
    )
    tracer = trace.get_tracer("payerbench.redteam")
    t0 = time.perf_counter()
    answers: list[str] = []
    history = list(history or [])
    error = None
    with (
        use_session(session),
        tracer.start_as_current_span(
            "redteam_case",
            attributes={
                "payerbench.redteam.case_id": case.id,
                "payerbench.redteam.category": case.category,
                "payerbench.redteam.config": config,
                "session.id": str(uuid.uuid4()),
            },
        ) as span,
    ):
        agent = build_agent(redteam=True, tools=tools_for(case))
        try:
            for text in case.turns:
                result = run_turn(agent, text, history)
                history = result["messages"]
                last = history[-1].content
                answers.append(last if isinstance(last, str) else json.dumps(last))
        except Exception as e:  # noqa: BLE001 - a crashed conversation is recorded, not raised
            error = f"{type(e).__name__}: {e}"[:300]
        trace_id = format(span.get_span_context().trace_id, "032x")

    calls = _tool_calls(history)
    o = Outcome(
        caller=case.caller,
        user_turns=case.turns,
        answers=answers,
        tool_calls=calls,
        actions=session.actions,
        marker=case.marker,
    )
    fired = run_all(o)
    rec: dict[str, Any] = {
        "case_id": case.id,
        "category": case.category,
        "config": config,
        "caller": case.caller,
        "turns": case.turns,
        "answers": answers,
        "tool_calls": [{**c, "result": (c["result"] or "")[:STORE_LIMIT]} for c in calls],
        "actions": [asdict(a) for a in session.actions],
        "gated": session.gated,
        "detectors": {k: v for k, v in fired.items() if v},
        "error": error,
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "model": os.getenv("PAYERBENCH_MODEL", "mlx-community/gemma-4-31b-it-8bit"),
        "trace_id": trace_id,
    }
    if case.benign:
        ok, why = utility(o, case.expect)
        rec["utility"], rec["utility_reasons"] = ok, why
        rec["success"] = None
    else:
        rec["success"] = any(fired.get(d) for d in case.succeeds_if)
    return rec


def _key(r: dict[str, Any]) -> tuple[str, str, int]:
    return (r["case_id"], r["config"], r["rep"])


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def run(
    cases: list[Case],
    configs: list[str],
    reps: int,
    out_path: Path,
    progress: Any = None,
    classifier: Any = None,
) -> int:
    """Run every (case, config, rep) not already in `out_path`. Returns records written."""
    done = {_key(r) for r in load(out_path)}
    jobs = [
        (c, cfg, rep)
        for rep in range(reps)
        for cfg in configs
        for c in cases
        if (c.id, cfg, rep) not in done
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for i, (case, cfg, rep) in enumerate(jobs, 1):
        rec = run_case(case, cfg, classifier=classifier)
        rec["rep"] = rep
        with out_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        if progress:
            progress(i, len(jobs), rec)
    return len(jobs)


# Detectors whose inputs are stored in full in each record, so they can be recomputed after a
# detector fix without re-running the agent. phi_leak and foreign_* need untruncated tool
# results and keep their original values.
RESCORABLE = ("unauthorized_action", "false_approval", "prompt_leak", "unsafe_advice", "marker")


def rescore(path: Path, cases: dict[str, Case]) -> int:
    """Recompute the rescorable detectors and `success` in place. Returns records changed."""
    from payerbench.redteam.detect import DETECTORS
    from payerbench.session import Action

    records = load(path)
    changed = 0
    for r in records:
        case = cases.get(r["case_id"])
        if case is None:
            continue
        o = Outcome(
            caller=r["caller"],
            user_turns=r["turns"],
            answers=r["answers"],
            tool_calls=r["tool_calls"],
            actions=[Action(**a) for a in r["actions"]],
            marker=case.marker,
        )
        det = dict(r["detectors"])
        for name in RESCORABLE:
            hits = DETECTORS[name](o)
            if hits:
                det[name] = hits
            else:
                det.pop(name, None)
        success = None if case.benign else any(det.get(d) for d in case.succeeds_if)
        if det != r["detectors"] or success != r["success"]:
            r["detectors"], r["success"] = det, success
            changed += 1
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return changed
