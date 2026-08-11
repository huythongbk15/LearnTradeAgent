"""Tests for LLM confidence calibration (audit Phase 5: reliability
diagrams)."""

from __future__ import annotations

from trading_agent.agents.calibration import ConfidenceCalibrator


def test_perfectly_calibrated_has_zero_ece():
    cal = ConfidenceCalibrator(bins=10)
    # accuracy == average confidence in every populated bin -> ECE = 0
    for _ in range(5):
        cal.add_observation(0.5, correct=True)
        cal.add_observation(0.5, correct=False)  # accuracy 0.5 == conf 0.5
    assert cal.expected_calibration_error() == 0.0


def test_overconfident_model_has_positive_ece():
    cal = ConfidenceCalibrator(bins=10)
    # Says 0.9, right only 50% of the time -> large ECE.
    for _ in range(10):
        cal.add_observation(0.9, correct=True)
        cal.add_observation(0.9, correct=False)
    ece = cal.expected_calibration_error()
    assert 0.3 < ece < 0.5  # |0.5 - 0.9| = 0.4


def test_calibrate_rescales_to_empirical_accuracy():
    cal = ConfidenceCalibrator(bins=10)
    for _ in range(10):
        cal.add_observation(0.8, correct=True)
        cal.add_observation(0.8, correct=False)
    # Raw 0.8 maps to empirical accuracy 0.5.
    assert cal.calibrate(0.8) == 0.5


def test_calibrate_unseen_bin_returns_raw():
    cal = ConfidenceCalibrator(bins=10)
    cal.add_observation(0.9, correct=True)
    # No observations in the 0.2 bin -> raw confidence returned.
    assert cal.calibrate(0.2) == 0.2


def test_reliability_curve_bins():
    cal = ConfidenceCalibrator(bins=10)
    cal.add_observation(0.95, correct=True)  # bin 9
    cal.add_observation(0.85, correct=False)  # bin 8
    curve = cal.reliability_curve()
    assert len(curve) == 2
    bin8 = curve[0]  # 0.8-0.9
    bin9 = curve[1]  # 0.9-1.0
    assert bin8.count == 1 and bin8.accuracy == 0.0
    assert bin8.avg_confidence == 0.85
    assert bin9.count == 1 and bin9.accuracy == 1.0
    assert bin9.avg_confidence == 0.95


def test_report_summary():
    cal = ConfidenceCalibrator(bins=5)
    cal.add_many([(0.1, False), (0.9, True)])
    report = cal.report()
    assert report["n"] == 2
    assert "ece" in report
    assert isinstance(report["bins"], list)
    assert len(report["bins"]) == 2


def test_json_roundtrip_preserves_state():
    cal = ConfidenceCalibrator(bins=10)
    cal.add_many([(0.7, True), (0.7, False), (0.3, True)])
    restored = ConfidenceCalibrator.from_json(cal.to_json())
    assert restored.report() == cal.report()
    assert restored.calibrate(0.7) == cal.calibrate(0.7)


def test_empty_calibrator_safe():
    cal = ConfidenceCalibrator(bins=10)
    assert cal.expected_calibration_error() == 0.0
    assert cal.calibrate(0.5) == 0.5
    assert cal.report()["n"] == 0
