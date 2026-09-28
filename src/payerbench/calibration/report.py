# ruff: noqa: E501  (rubric text and table rows read better unwrapped)
"""Turn verdicts into the comparison table."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from payerbench.calibration.collect import effective_label
from payerbench.calibration.metrics import CaseResult, agreement, summarize
from payerbench.calibration.runner import load_verdicts


def build_case_results(
    verdicts: list[dict[str, Any]], runs_by_id: dict[str, dict[str, Any]], variant: str = "baseline"
) -> dict[str, list[CaseResult]]:
    """Group verdicts per judge into CaseResult lists. Verdicts with errors are dropped."""
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for v in verdicts:
        if v["variant"] != variant or v.get("error") or v["pass_prob"] is None:
            continue
        rec = runs_by_id.get(v["case_id"])
        if rec is None:
            continue
        slot = grouped[v["judge"]].setdefault(
            v["case_id"],
            {"gold_pass": effective_label(rec) == "pass", "probs": [], "lat": [], "cost": 0.0},
        )
        slot["probs"].append(v["pass_prob"])
        slot["lat"].append(v["latency_s"])
        slot["cost"] += v["cost_usd"] or 0.0
    out: dict[str, list[CaseResult]] = {}
    for judge, cases in grouped.items():
        out[judge] = [
            CaseResult(cid, c["gold_pass"], c["probs"], c["lat"], c["cost"])
            for cid, c in cases.items()
        ]
    return out


COLUMNS = [
    ("n", "n", "{:d}"),
    ("accuracy", "acc", "{:.3f}"),
    ("fail_recall", "fail recall", "{:.3f}"),
    ("fail_precision", "fail prec", "{:.3f}"),
    ("kappa", "kappa", "{:.3f}"),
    ("unanimous", "unanimous", "{:.3f}"),
    ("mean_prob_std", "prob std", "{:.3f}"),
    ("signal_value", "signal", "{:.3f}"),
    ("brier", "Brier", "{:.3f}"),
    ("ece", "ECE", "{:.3f}"),
    ("auroc", "AUROC", "{:.3f}"),
    ("cost_usd_per_verdict", "$/verdict", "{:.5f}"),
    ("latency_p50_s", "p50 s", "{:.2f}"),
    ("latency_p95_s", "p95 s", "{:.2f}"),
]


def _fmt(fmt: str, x: Any) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return fmt.format(x)


def markdown_table(summaries: dict[str, dict[str, float]]) -> str:
    head = "| judge | " + " | ".join(label for _, label, _ in COLUMNS) + " |"
    sep = "|---|" + "|".join("---:" for _ in COLUMNS) + "|"
    rows = [head, sep]
    for judge, s in sorted(summaries.items(), key=lambda kv: -kv[1].get("signal_value", 0)):
        rows.append(
            f"| {judge} | " + " | ".join(_fmt(fmt, s.get(k)) for k, _, fmt in COLUMNS) + " |"
        )
    return "\n".join(rows)


def injection_table(verdicts: list[dict[str, Any]], runs_by_id: dict[str, dict[str, Any]]) -> str:
    """For gold-FAIL cases judged under all three variants: mean pass probability per variant and
    the share of verdicts flipped to PASS by the injected note."""
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for v in verdicts:
        if v.get("error") or v["pass_prob"] is None:
            continue
        rec = runs_by_id.get(v["case_id"])
        if rec is None or effective_label(rec) != "fail":
            continue
        by[v["judge"]][v["variant"]].append(v["pass_prob"])
    rows = [
        "| judge | baseline P(pass) | no tool output | injected | flipped to PASS by injection |",
        "|---|---:|---:|---:|---:|",
    ]
    for judge, variants in sorted(by.items()):
        b, n, i = (
            variants.get("baseline", []),
            variants.get("no_tool_output", []),
            variants.get("injected", []),
        )
        mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")  # noqa: E731
        flipped = sum(1 for p in i if p >= 0.5) / len(i) if i else float("nan")
        rows.append(
            f"| {judge} | {_fmt('{:.3f}', mean(b))} | {_fmt('{:.3f}', mean(n))} | {_fmt('{:.3f}', mean(i))} | {_fmt('{:.1%}', flipped)} |"
        )
    return "\n".join(rows)


def breakdown_table(
    verdicts: list[dict[str, Any]], runs_by_id: dict[str, dict[str, Any]], key: str
) -> str:
    """Accuracy per judge, split by a run attribute (`source` model or `category`)."""
    groups: dict[str, dict[str, list[CaseResult]]] = defaultdict(dict)
    values = sorted({(rec.get(key) or "?").split("/")[-1] for rec in runs_by_id.values()})
    for value in values:
        subset = {
            cid: r for cid, r in runs_by_id.items() if (r.get(key) or "?").split("/")[-1] == value
        }
        for judge, cases in build_case_results(verdicts, subset).items():
            groups[judge][value] = cases
    head = "| judge | " + " | ".join(f"{v} (n)" for v in values) + " |"
    rows = [head, "|---|" + "|".join("---:" for _ in values) + "|"]
    for judge in sorted(groups):
        cells = []
        for v in values:
            cases = groups[judge].get(v)
            if not cases:
                cells.append("-")
            else:
                a = agreement(cases)
                cells.append(f"{a['accuracy']:.2f} ({a['n']})")
        rows.append(f"| {judge} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def report(verdict_paths: list[Path], run_paths: list[Path], out_json: Path | None = None) -> str:
    runs_by_id: dict[str, dict[str, Any]] = {}
    for p in run_paths:
        with p.open() as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    # Two collections share scenario ids; namespace by source model.
                    rec_id = f"{rec['id']}@{rec.get('model', '?').split('/')[-1]}"
                    rec["id"] = rec_id
                    runs_by_id[rec_id] = rec
    verdicts = [v for p in verdict_paths for v in load_verdicts(p)]
    grouped = build_case_results(verdicts, runs_by_id)
    summaries = {judge: summarize(cases) for judge, cases in grouped.items()}
    gold_counts = defaultdict(int)
    for rec in runs_by_id.values():
        gold_counts[effective_label(rec)] += 1
    parts = [
        f"Gold labels across {len(runs_by_id)} runs: "
        + ", ".join(f"{k}={v}" for k, v in sorted(gold_counts.items())),
        "",
        markdown_table(summaries),
        "",
        "Injection experiment (gold-FAIL cases only):",
        injection_table(verdicts, runs_by_id),
        "",
        "Accuracy by source of the run (baseline variant):",
        breakdown_table([v for v in verdicts if v["variant"] == "baseline"], runs_by_id, "model"),
        "",
        "Accuracy by scenario category (baseline variant):",
        breakdown_table(
            [v for v in verdicts if v["variant"] == "baseline"], runs_by_id, "category"
        ),
    ]
    if out_json:
        out_json.write_text(
            json.dumps({"summaries": summaries, "gold_counts": gold_counts}, indent=1)
        )
    return "\n".join(parts)
