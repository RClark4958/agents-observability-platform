"""Run judges over collected runs, repeatedly, and persist every verdict.

Verdicts go to JSONL keyed by (judge, variant, case_id, rep) and the runner is resumable: existing
keys are skipped. API judges run in a small thread pool; local judges run sequentially so the
Mac's GPU is not oversubscribed.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any

from payerbench.calibration.judges import Judge, Verdict, evidence_packet

VARIANTS = {
    "baseline": {"include_tool_output": True, "inject": False},
    "no_tool_output": {"include_tool_output": False, "inject": False},
    "injected": {"include_tool_output": True, "inject": True},
}


def _key(v: dict[str, Any]) -> tuple[str, str, str, int]:
    return (v["judge"], v["variant"], v["case_id"], v["rep"])


def load_verdicts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def run(
    runs: list[dict[str, Any]],
    judge: Judge,
    reps: int,
    out_path: Path,
    variant: str = "baseline",
    workers: int = 1,
    progress: Any = None,
) -> int:
    """Judge every run `reps` times under `variant`. Returns the number of new verdicts written."""
    opts = VARIANTS[variant]
    done = {_key(v) for v in load_verdicts(out_path)}
    jobs = [
        (rec, rep)
        for rec in runs
        for rep in range(reps)
        if (judge.name, variant, rec["id"], rep) not in done
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0

    def one(rec: dict[str, Any], rep: int) -> Verdict:
        packet = evidence_packet(rec, **opts)
        return judge.judge(rec["id"], packet, rep)

    with out_path.open("a") as f:
        if workers <= 1:
            for i, (rec, rep) in enumerate(jobs, 1):
                v = one(rec, rep)
                _write(f, v, variant, rec)
                written += 1
                if progress:
                    progress(i, len(jobs), v)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(one, rec, rep): rec for rec, rep in jobs}
                for i, fut in enumerate(as_completed(futures), 1):
                    v = fut.result()
                    _write(f, v, variant, futures[fut])
                    written += 1
                    if progress:
                        progress(i, len(jobs), v)
    return written


def _write(f: Any, v: Verdict, variant: str, rec: dict[str, Any]) -> None:
    row = asdict(v)
    row["variant"] = variant
    row["gold"] = rec["gold"]
    row["human_label"] = rec.get("human_label")
    row["category"] = rec["category"]
    row["source"] = rec.get("model")
    # NaN is not valid JSON; store null for a failed verdict.
    if row["pass_prob"] != row["pass_prob"]:
        row["pass_prob"] = None
    f.write(json.dumps(row) + "\n")
    f.flush()
