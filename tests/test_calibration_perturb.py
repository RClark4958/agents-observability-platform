import random

from payerbench.calibration.perturb import perturb_one


def _rec(category, expected, answer, tool_calls):
    return {
        "id": "x",
        "category": category,
        "member_id": "M-SYNTH-000001",
        "user_text": "q",
        "expected": expected,
        "final_answer": answer,
        "tool_calls": tool_calls,
        "model": "m",
        "gold": "pass",
        "persona": "plain",
    }


def test_claim_status_flip_yields_a_gold_fail():
    calls = [{"name": "get_claim", "args": {"claim_id": "C-1"}, "result": "{}"}]
    rec = _rec(
        "claim_status",
        {"claim_id": "C-1", "status": "paid", "denial_code": None},
        "Claim C-1 was paid on 2026-XX-XX.",
        calls,
    )
    p = perturb_one(rec, random.Random(1))
    assert p and p["gold"] == "fail" and "denied" in p["final_answer"]
    assert p["tool_calls"] == calls  # evidence untouched


def test_no_edit_possible_returns_none():
    rec = _rec("out_of_scope", {}, "I cannot help with that.", [])
    assert perturb_one(rec, random.Random(1)) is None
