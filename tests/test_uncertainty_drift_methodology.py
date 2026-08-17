from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from trading_agent.research.calibration import (
    CalibrationArtifactStore,
    CalibrationMethod,
    CalibrationState,
    DataWindow,
    ExposureUncertainty,
    MonotonicExposurePolicy,
    PredictionInterval,
    apply_calibrator,
    calibration_state,
    conformal_interval,
    fit_calibration_artifact,
    fit_split_conformal,
)
from trading_agent.research.drift import (
    DriftLevel,
    DriftMonitor,
    PageHinkley,
    ReferenceHistogram,
    fisher_z_distance,
    psi,
    volatility_log_ratio,
)
from trading_agent.research.uncertainty import (
    Action,
    GovernedDecisionPolicy,
    UncertaintySignal,
    uncertainty_signal_to_decision,
)


def _calibration_samples(seed: int = 101):
    rng = np.random.default_rng(seed)
    train_latent = rng.normal(size=300)
    validation_latent = rng.normal(size=180)
    train_truth = 1.0 / (1.0 + np.exp(-train_latent))
    validation_truth = 1.0 / (1.0 + np.exp(-validation_latent))
    train_outcomes = rng.binomial(1, train_truth)
    validation_outcomes = rng.binomial(1, validation_truth)
    # Deliberately overconfident raw probabilities.
    train_predictions = 1.0 / (1.0 + np.exp(-2.0 * train_latent))
    validation_predictions = 1.0 / (1.0 + np.exp(-2.0 * validation_latent))
    train_window = DataWindow(
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 6, 30, tzinfo=UTC)
    )
    validation_window = DataWindow(
        datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 9, 30, tzinfo=UTC)
    )
    return (
        train_predictions,
        train_outcomes,
        validation_predictions,
        validation_outcomes,
        train_window,
        validation_window,
    )


@pytest.mark.parametrize("method", list(CalibrationMethod))
def test_pluggable_calibrators_fit_train_and_score_validation(method) -> None:
    train_p, train_y, validation_p, validation_y, train_window, validation_window = (
        _calibration_samples()
    )
    artifact = fit_calibration_artifact(
        method=method,
        model_artifact_id="model-a",
        train_predictions=train_p,
        train_outcomes=train_y,
        validation_predictions=validation_p,
        validation_outcomes=validation_y,
        train_window=train_window,
        validation_window=validation_window,
    )
    calibrated = apply_calibrator(validation_p, artifact)
    assert artifact.sample_count == len(validation_p)
    assert 0.0 <= artifact.brier <= 1.0
    assert 0.0 <= artifact.ece <= 1.0
    assert artifact.reliability_data
    assert np.all((calibrated >= 0.0) & (calibrated <= 1.0))


def test_validation_outcomes_cannot_change_fitted_parameters() -> None:
    train_p, train_y, validation_p, validation_y, train_window, validation_window = (
        _calibration_samples(202)
    )
    baseline = fit_calibration_artifact(
        method=CalibrationMethod.PLATT,
        model_artifact_id="model-a",
        train_predictions=train_p,
        train_outcomes=train_y,
        validation_predictions=validation_p,
        validation_outcomes=validation_y,
        train_window=train_window,
        validation_window=validation_window,
    )
    perturbed = fit_calibration_artifact(
        method=CalibrationMethod.PLATT,
        model_artifact_id="model-a",
        train_predictions=train_p,
        train_outcomes=train_y,
        validation_predictions=validation_p,
        validation_outcomes=1 - validation_y,
        train_window=train_window,
        validation_window=validation_window,
    )
    assert baseline.parameters == perturbed.parameters
    assert baseline.input_hash != perturbed.input_hash
    assert (baseline.brier, baseline.ece) != (perturbed.brier, perturbed.ece)


def test_overlapping_calibration_windows_are_rejected() -> None:
    train_p, train_y, validation_p, validation_y, train_window, _ = _calibration_samples()
    overlapping = DataWindow(
        datetime(2024, 6, 1, tzinfo=UTC), datetime(2024, 8, 1, tzinfo=UTC)
    )
    with pytest.raises(ValueError, match="disjoint"):
        fit_calibration_artifact(
            method="isotonic",
            model_artifact_id="model-a",
            train_predictions=train_p,
            train_outcomes=train_y,
            validation_predictions=validation_p,
            validation_outcomes=validation_y,
            train_window=train_window,
            validation_window=overlapping,
        )


def test_calibration_artifact_store_and_states(tmp_path) -> None:
    train_p, train_y, validation_p, validation_y, train_window, validation_window = (
        _calibration_samples()
    )
    artifact = fit_calibration_artifact(
        method="isotonic",
        model_artifact_id="model-a",
        train_predictions=train_p,
        train_outcomes=train_y,
        validation_predictions=validation_p,
        validation_outcomes=validation_y,
        train_window=train_window,
        validation_window=validation_window,
    )
    store = CalibrationArtifactStore(tmp_path / "calibration")
    store.put(artifact)
    assert store.get(artifact.calibration_id) == artifact
    assert calibration_state(None) == CalibrationState.UNCALIBRATED
    good = replace(artifact, brier=0.10, ece=0.02, sample_count=100)
    assert good.state(now=good.created_at + timedelta(days=1)) == CalibrationState.CALIBRATED
    degraded = replace(good, ece=0.20)
    assert degraded.state(now=degraded.created_at) == CalibrationState.DEGRADED
    assert good.state(now=good.created_at + timedelta(days=31)) == CalibrationState.STALE

    signal = UncertaintySignal(
        expected_return=0.05,
        prediction_interval_lower=0.01,
        prediction_interval_upper=0.04,
        calibration_score=0.95,
        ood_score=0.01,
    )
    policy = GovernedDecisionPolicy("aggressive")
    uncalibrated = uncertainty_signal_to_decision(signal)
    assert Action.INCREASE not in policy.allowed_actions(uncalibrated)
    calibrated = uncertainty_signal_to_decision(
        signal, calibration_artifact=good, temperature=0.5
    )
    assert Action.INCREASE in policy.allowed_actions(calibrated)


def test_uncertainty_worsening_never_increases_allowed_exposure() -> None:
    policy = MonotonicExposurePolicy(interval_width_scale=0.02)
    baseline = ExposureUncertainty(
        calibration_state=CalibrationState.CALIBRATED,
        ece=0.01,
        ood_score=0.05,
        interval=PredictionInterval(0.01, 0.02, 0.9),
        regime_entropy=0.05,
    )
    base_exposure = policy.allowed_directional_exposure(1.0, baseline)
    worse_cases = [
        replace(baseline, calibration_state=CalibrationState.DEGRADED),
        replace(baseline, ece=0.20),
        replace(baseline, ood_score=0.60),
        replace(baseline, interval=PredictionInterval(0.001, 0.04, 0.9)),
        replace(baseline, regime_entropy=0.80),
    ]
    assert all(
        policy.allowed_directional_exposure(1.0, case) <= base_exposure
        for case in worse_cases
    )
    crossing = replace(baseline, interval=PredictionInterval(-0.01, 0.02, 0.9))
    assert policy.allowed_directional_exposure(1.0, crossing) == 0.0


def test_split_conformal_quantifies_interval_without_changing_point_forecast() -> None:
    rng = np.random.default_rng(303)
    calibration_predictions = rng.normal(0.002, 0.01, 500)
    calibration_outcomes = calibration_predictions + rng.normal(0.0, 0.005, 500)
    window = DataWindow(
        datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)
    )
    artifact = fit_split_conformal(
        model_artifact_id="model-a",
        calibration_predictions=calibration_predictions,
        calibration_outcomes=calibration_outcomes,
        calibration_window=window,
        alpha=0.10,
    )
    interval = conformal_interval(0.003, artifact)
    assert (interval.lower + interval.upper) / 2.0 == pytest.approx(0.003)
    test_predictions = rng.normal(0.002, 0.01, 1_000)
    test_outcomes = test_predictions + rng.normal(0.0, 0.005, 1_000)
    covered = np.mean(
        (test_outcomes >= test_predictions - artifact.residual_quantile)
        & (test_outcomes <= test_predictions + artifact.residual_quantile)
    )
    assert covered >= 0.84


def test_psi_edges_are_frozen_from_reference_only() -> None:
    rng = np.random.default_rng(404)
    reference = rng.normal(0.0, 1.0, 2_000)
    histogram = ReferenceHistogram.fit(reference, bins=10)
    edges_before = histogram.edges
    shifted = rng.normal(5.0, 1.0, 2_000)
    assert histogram.score(shifted) > 0.25
    assert histogram.edges == edges_before
    assert psi(reference, reference) == pytest.approx(0.0, abs=1e-12)


def test_metric_specific_drift_and_sequential_change_detection() -> None:
    assert volatility_log_ratio(0.01, 0.02) == pytest.approx(np.log(2.0))
    assert fisher_z_distance(0.2, 0.8) > fisher_z_distance(0.2, 0.3)
    monitor = DriftMonitor()
    results = monitor.check_all(
        vol_ref=0.01,
        vol_current=0.016,
        corr_ref=0.1,
        corr_current=0.8,
        ece_ref=0.03,
        ece_current=0.12,
        spread_ref=0.0001,
        spread_current=0.0005,
        fill_rate_ref=0.98,
        fill_rate_current=0.70,
        latency_ref=0.05,
        latency_current=0.20,
        adverse_selection_ref=0.0001,
        adverse_selection_current=0.004,
    )
    names = {result.detector for result in results}
    assert {
        "volatility_log_ratio",
        "correlation_fisher_z",
        "ece_drift",
        "spread_log_ratio",
        "fill_rate_drop",
        "latency_log_ratio",
        "adverse_selection_drift",
    } <= names
    assert any(result.level == DriftLevel.RED for result in results)

    detector = PageHinkley(delta=0.01, threshold=5.0)
    assert not any(detector.update(0.0) for _ in range(100))
    assert any(detector.update(1.0) for _ in range(100))
