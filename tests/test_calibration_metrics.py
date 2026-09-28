import math

from payerbench.calibration.metrics import (
    CaseResult,
    agreement,
    auroc,
    brier,
    expected_calibration_error,
    repeatability,
    summarize,
)


def _cr(cid, gold, probs):
    return CaseResult(cid, gold, probs, [0.1] * len(probs), 0.0)


def test_perfect_judge():
    rs = [_cr("a", True, [0.9, 0.95]), _cr("b", False, [0.1, 0.05]), _cr("c", False, [0.2, 0.2])]
    a = agreement(rs)
    assert a["accuracy"] == 1.0 and a["fail_recall"] == 1.0 and a["kappa"] == 1.0
    r = repeatability(rs)
    assert r["unanimous"] == 1.0
    assert auroc(rs) == 1.0
    assert brier(rs) < 0.05


def test_random_judge_has_low_kappa_and_auroc_half():
    rs = [
        _cr("a", True, [0.5]),
        _cr("b", False, [0.5]),
        _cr("c", True, [0.5]),
        _cr("d", False, [0.5]),
    ]
    assert auroc(rs) == 0.5
    assert abs(agreement(rs)["kappa"]) < 1e-9


def test_repeatability_counts_flips():
    rs = [_cr("a", True, [0.6, 0.4]), _cr("b", True, [0.9, 0.9])]
    assert repeatability(rs)["unanimous"] == 0.5


def test_ece_zero_when_calibrated():
    # 10 cases at p=0.7 with 7 passes -> bucket confidence equals bucket accuracy
    rs = [_cr(str(i), i < 7, [0.7]) for i in range(10)]
    assert expected_calibration_error(rs) < 1e-9


def test_summarize_handles_single_rep():
    s = summarize([_cr("a", True, [0.8]), _cr("b", False, [0.3])])
    assert s["accuracy"] == 1.0
    assert math.isnan(s["unanimous"])
    assert s["signal_value"] == 1.0
