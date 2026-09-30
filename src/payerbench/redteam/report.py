"""Summarize red-team runs: attack success rate (ASR) by category and defense config.

ASR is successes / attacks, with a 95% Wilson interval, because per-category counts are small
(5-10 seeds times a few reps) and a bare percentage would overstate precision. Benign seeds are
reported separately as the utility rate: the share of legitimate requests still served, which is
what a defense costs.
"""

from __future__ import annotations

import collections
import json
import math
from pathlib import Path
from typing import Any

from payerbench.redteam.run import CONFIGS, load


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _cell(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k / n:.2f} [{lo:.2f}-{hi:.2f}] ({k}/{n})"


def _order(configs: set[str]) -> list[str]:
    return [c for c in CONFIGS if c in configs] + sorted(configs - set(CONFIGS))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts behind every table, also written as JSON for the site and CI gate."""
    attack = [r for r in records if r["success"] is not None and not r.get("error")]
    benign = [r for r in records if r["success"] is None and not r.get("error")]
    by: dict[tuple[str, str], list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in attack:
        for key in ((r["category"], r["config"]), ("ALL", r["config"])):
            by[key][0] += int(r["success"])
            by[key][1] += 1
    util: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in benign:
        util[r["config"]][0] += int(r["utility"])
        util[r["config"]][1] += 1
    exposure: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in attack:
        exposure[r["config"]][0] += int(bool(r["detectors"].get("foreign_read")))
        exposure[r["config"]][1] += 1
    return {
        "asr": {f"{c}|{cfg}": v for (c, cfg), v in by.items()},
        "utility": dict(util),
        "exposure": dict(exposure),
        "errors": sum(1 for r in records if r.get("error")),
        "n_records": len(records),
    }


def report(paths: list[Path], out_json: Path | None = None) -> str:
    records = [r for p in paths for r in load(p)]
    if not records:
        return "no records"
    s = summarize(records)
    configs = _order({r["config"] for r in records})
    cats = sorted({r["category"] for r in records if r["success"] is not None}) + ["ALL"]

    lines = ["## Attack success rate by category (lower is better)", ""]
    lines.append("| category | " + " | ".join(configs) + " |")
    lines.append("|---|" + "---|" * len(configs))
    for c in cats:
        row = [_cell(*s["asr"].get(f"{c}|{cfg}", [0, 0])) for cfg in configs]
        name = f"**{c}**" if c == "ALL" else c
        lines.append(f"| {name} | " + " | ".join(row) + " |")

    lines += ["", "## Utility on benign requests (higher is better)", ""]
    lines.append("| | " + " | ".join(configs) + " |")
    lines.append("|---|" + "---|" * len(configs))
    lines.append(
        "| served | " + " | ".join(_cell(*s["utility"].get(c, [0, 0])) for c in configs) + " |"
    )
    lines.append(
        "| other member's records reached the model | "
        + " | ".join(_cell(*s["exposure"].get(c, [0, 0])) for c in configs)
        + " |"
    )

    # Per-seed view: which attacks land, under which configs.
    per: dict[str, dict[str, list[int]]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: [0, 0])
    )
    cat_of: dict[str, str] = {}
    for r in records:
        if r["success"] is None or r.get("error"):
            continue
        per[r["case_id"]][r["config"]][0] += int(r["success"])
        per[r["case_id"]][r["config"]][1] += 1
        cat_of[r["case_id"]] = r["category"]
    landed = [cid for cid, v in per.items() if any(k for k, _ in v.values())]
    if landed:
        lines += ["", "## Seeds that succeeded at least once", ""]
        lines.append("| seed | category | " + " | ".join(configs) + " |")
        lines.append("|---|---|" + "---|" * len(configs))
        for cid in sorted(landed, key=lambda x: (cat_of[x], x)):
            row = [f"{per[cid][c][0]}/{per[cid][c][1]}" if per[cid][c][1] else "-" for c in configs]
            lines.append(f"| {cid} | {cat_of[cid]} | " + " | ".join(row) + " |")

    unserved = collections.Counter(
        (r["case_id"], r["config"]) for r in records if r["success"] is None and not r["utility"]
    )
    if unserved:
        lines += ["", "## Benign requests not served", ""]
        for (cid, cfg), k in sorted(unserved.items()):
            lines.append(f"- {cid} under `{cfg}`: {k}x")

    lines += ["", f"{s['n_records']} conversations, {s['errors']} errors."]
    if out_json:
        out_json.write_text(json.dumps(s, indent=1))
    return "\n".join(lines) + "\n"
