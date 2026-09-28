"""Agreement, repeatability and calibration metrics for judge verdicts.

Inputs are plain lists so the module has no dependency on the judge implementations. Every judge
produces, per case and per repetition, a probability that the answer passes. The gold label is
binary. Metrics:

- accuracy, precision/recall/F1 on the *fail* class (a judge exists to catch failures), and
  Cohen's kappa against gold, using the mean probability over repetitions thresholded at 0.5
- repeatability: share of cases where every repetition gave the same verdict, plus the mean
  per-case standard deviation of the probability
- signal value (LangChain's term): agreement x repeatability
- Brier score, expected calibration error (10 equal-width bins) and AUROC on the probabilities
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass
class CaseResult:
    case_id: str
    gold_pass: bool
    probs: list[float]  # one entry per repetition
    latencies_s: list[float]
    cost_usd: float


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def verdict(p: float, threshold: float = 0.5) -> bool:
    return p >= threshold


def agreement(results: list[CaseResult], threshold: float = 0.5) -> dict[str, float]:
    tp = fp = tn = fn = 0  # positive class = FAIL (what a judge is for)
    for r in results:
        pred_fail = not verdict(_mean(r.probs), threshold)
        gold_fail = not r.gold_pass
        if pred_fail and gold_fail:
            tp += 1
        elif pred_fail and not gold_fail:
            fp += 1
        elif not pred_fail and not gold_fail:
            tn += 1
        else:
            fn += 1
    n = len(results) or 1
    acc = (tp + tn) / n
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    # Cohen's kappa
    p_yes = ((tp + fp) / n) * ((tp + fn) / n)
    p_no = ((fn + tn) / n) * ((fp + tn) / n)
    pe = p_yes + p_no
    kappa = (acc - pe) / (1 - pe) if pe < 1 else 0.0
    return {
        "n": n,
        "accuracy": acc,
        "fail_precision": precision,
        "fail_recall": recall,
        "fail_f1": f1,
        "kappa": kappa,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }


def repeatability(results: list[CaseResult], threshold: float = 0.5) -> dict[str, float]:
    multi = [r for r in results if len(r.probs) > 1]
    if not multi:
        return {"unanimous": float("nan"), "mean_prob_std": float("nan"), "reps": 1}
    unanimous = sum(len({verdict(p, threshold) for p in r.probs}) == 1 for r in multi) / len(multi)
    stds = [statistics.pstdev(r.probs) for r in multi]
    return {
        "unanimous": unanimous,
        "mean_prob_std": _mean(stds),
        "reps": _mean([len(r.probs) for r in multi]),
    }


def brier(results: list[CaseResult]) -> float:
    return _mean([(_mean(r.probs) - (1.0 if r.gold_pass else 0.0)) ** 2 for r in results])


def expected_calibration_error(results: list[CaseResult], bins: int = 10) -> float:
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for r in results:
        p = _mean(r.probs)
        i = min(int(p * bins), bins - 1)
        buckets[i].append((p, 1.0 if r.gold_pass else 0.0))
    n = len(results) or 1
    ece = 0.0
    for b in buckets:
        if not b:
            continue
        conf = _mean([p for p, _ in b])
        acc = _mean([y for _, y in b])
        ece += len(b) / n * abs(conf - acc)
    return ece


def auroc(results: list[CaseResult]) -> float:
    """Rank-based AUROC via the Mann-Whitney statistic; ties count half."""
    pos = [_mean(r.probs) for r in results if r.gold_pass]
    neg = [_mean(r.probs) for r in results if not r.gold_pass]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for q in neg:
            wins += 1.0 if p > q else 0.5 if p == q else 0.0
    return wins / (len(pos) * len(neg))


def summarize(results: list[CaseResult], threshold: float = 0.5) -> dict[str, float]:
    a = agreement(results, threshold)
    rep = repeatability(results, threshold)
    lat = [x for r in results for x in r.latencies_s]
    lat.sort()
    p = lambda q: lat[min(int(q * len(lat)), len(lat) - 1)] if lat else float("nan")  # noqa: E731
    signal = a["accuracy"] * (rep["unanimous"] if not math.isnan(rep["unanimous"]) else 1.0)
    return {
        **a,
        **rep,
        "signal_value": signal,
        "brier": brier(results),
        "ece": expected_calibration_error(results),
        "auroc": auroc(results),
        "cost_usd_total": sum(r.cost_usd for r in results),
        "cost_usd_per_verdict": sum(r.cost_usd for r in results)
        / max(1, sum(len(r.probs) for r in results)),
        "latency_p50_s": p(0.5),
        "latency_p95_s": p(0.95),
    }
