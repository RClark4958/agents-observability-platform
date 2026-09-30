"""promptfoo Python provider for the PayerBench agent in session mode.

promptfoo calls `call_api(prompt, options, context)` once per test. The agent runs inside a fresh
red-team session (verified caller, chosen defenses), exactly as the seed-pack runner does, and the
deterministic detectors' findings are returned as metadata so assert_detectors.py can score the
same response a second way.

Multi-turn strategies (crescendo, hydra) send the whole conversation as a JSON list of
{role, content}; earlier turns become history and only the last user message is run.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from payerbench import telemetry
from payerbench.redteam.cases import CALLERS, Case
from payerbench.redteam.run import run_case

_ready = False


def _setup() -> None:
    global _ready
    if not _ready:
        telemetry.configure(service_name="payerbench-promptfoo")
        telemetry.instrument_langchain()
        _ready = True


def _split(prompt: str) -> tuple[list[Any], str]:
    try:
        msgs = json.loads(prompt)
    except (json.JSONDecodeError, TypeError):
        return [], prompt
    if not (isinstance(msgs, list) and msgs and isinstance(msgs[-1], dict)):
        return [], prompt
    history = [
        HumanMessage(m["content"]) if m.get("role") == "user" else AIMessage(m["content"])
        for m in msgs[:-1]
        if m.get("role") in ("user", "assistant")
    ]
    return history, str(msgs[-1].get("content", ""))


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    _setup()
    cfg = options.get("config", {})
    test_meta = (context.get("test") or {}).get("metadata") or {}
    note = (context.get("vars") or {}).get("record_note")
    history, text = _split(prompt)
    case = Case(
        id=f"promptfoo:{test_meta.get('pluginId', 'unknown')}",
        category=test_meta.get("pluginId", "promptfoo"),
        caller=CALLERS[cfg.get("caller", "A")],
        turns=[text],
        plants={"claim_note": note} if note else {},
        succeeds_if=[],
    )
    rec = run_case(case, cfg.get("defenses", "none"), history=history)
    if rec["error"]:
        return {"error": rec["error"]}
    return {
        "output": rec["answers"][-1] if rec["answers"] else "",
        "metadata": {
            "detectors": rec["detectors"],
            "tool_calls": [{"name": c["name"], "args": c["args"]} for c in rec["tool_calls"]],
            "actions": rec["actions"],
            "trace_id": rec["trace_id"],
        },
    }
