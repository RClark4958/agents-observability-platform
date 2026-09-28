"""Perturbed runs: confident wrong answers with real tool evidence.

The weak model fails by deflecting, which is easy to catch. The judge study also needs answers
that are fluent, well-formed, cite the right tool results, and are wrong in one fact. Those are
produced here by taking a gold-pass Gemma run and editing one fact in its final answer so that it
contradicts the tool result the judge can see: a claim status, a plan name, an amount, an active
flag, an authorization status. The transcript's tool calls are untouched, so the evidence packet
still contains the truth. Every perturbed record is re-graded and only kept if the rules now
call it a failure, so the gold label is earned, not asserted.
"""

from __future__ import annotations

import copy
import json
import random
import re
from pathlib import Path
from typing import Any

from payerbench.calibration.scenarios import Scenario, Transcript, grade

SEED = 20260928
PLANS = ["Bronze HMO", "Silver PPO", "Gold PPO", "Medicare Advantage Choice"]
STATUS_SWAP = {"paid": "denied", "denied": "paid", "pending": "paid", "adjusted": "denied"}
PA_SWAP = {
    "approved": "denied",
    "denied": "approved",
    "pending": "approved",
    "not_required": "denied",
}


def _swap_word(text: str, old: str, new: str) -> str | None:
    if not re.search(rf"\b{re.escape(old)}\b", text, re.I):
        return None
    return re.sub(rf"\b{re.escape(old)}\b", new, text, count=1, flags=re.I)


def _swap_number(text: str, n: int | float, rng: random.Random) -> str | None:
    forms = [f"{n:,}", f"{n}"] if isinstance(n, int) else [f"{n:,.2f}", f"{n:.2f}"]
    for f in forms:
        if f in text:
            wrong = (
                n + rng.choice([250, 500, 1000, 1500]) if isinstance(n, int) else round(n * 1.6, 2)
            )
            wf = f"{wrong:,}" if isinstance(wrong, int) else f"{wrong:,.2f}"
            return text.replace(f, wf, 1)
    return None


def perturb_one(rec: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    """Return a perturbed copy with one wrong fact, or None if no clean edit applies."""
    a = rec["final_answer"]
    e = rec["expected"]
    cat = rec["category"]
    new: str | None = None
    kind = ""

    if cat == "claim_status" and e.get("status") in STATUS_SWAP:
        new = _swap_word(a, e["status"], STATUS_SWAP[e["status"]])
        kind = "claim_status_flipped"
    elif cat == "eligibility_active":
        other = rng.choice([p for p in PLANS if p != e["plan"]])
        new = _swap_word(a, e["plan"], other)
        kind = "plan_name_swapped"
    elif cat == "eligibility_termed":
        for old, rep in (
            ("no longer active", "currently active"),
            ("not active", "active"),
            ("inactive", "active"),
            ("terminated", "active"),
        ):
            new = _swap_word(a, old, rep)
            if new:
                break
        kind = "termination_denied"
    elif cat == "deductible_progress":
        new = _swap_number(a, e["met"], rng)
        kind = "deductible_amount_changed"
    elif cat == "prior_auth_status" and e.get("status") in PA_SWAP:
        word = {
            "approved": "approved",
            "denied": "denied",
            "pending": "pending",
            "not_required": "not required",
        }[e["status"]]
        repl = {
            "approved": "denied",
            "denied": "approved",
            "pending": "approved",
            "not_required": "denied",
        }[e["status"]]
        new = _swap_word(a, word, repl)
        kind = "pa_status_flipped"
    elif cat == "denied_claim_explain":
        new = _swap_word(a, "180", "60")
        kind = "appeal_window_wrong"
    elif cat == "pt_limit" and e.get("limit"):
        new = _swap_word(a, str(e["limit"]), str(90 if e["limit"] == 60 else 45))
        kind = "pt_limit_wrong"

    if not new or new == a:
        return None
    out = copy.deepcopy(rec)
    out["id"] = rec["id"] + "-perturbed"
    out["final_answer"] = new
    out["perturbation"] = kind
    out["model"] = rec["model"] + "+perturbed"
    out["human_label"] = None
    s = Scenario(rec["id"], cat, rec["member_id"], rec["user_text"], e, rec.get("persona", "plain"))
    g = grade(s, Transcript(new, rec["tool_calls"]))
    out["gold"], out["gold_reasons"] = g.label, g.reasons
    return out if g.label == "fail" else None


def perturb_file(src: Path, dst: Path) -> dict[str, int]:
    rng = random.Random(SEED)
    counts: dict[str, int] = {}
    with src.open() as f, dst.open("w") as out:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["gold"] != "pass":
                continue
            p = perturb_one(rec, rng)
            if p:
                out.write(json.dumps(p) + "\n")
                counts[p["perturbation"]] = counts.get(p["perturbation"], 0) + 1
    return counts
