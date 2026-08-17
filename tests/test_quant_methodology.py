from __future__ import annotations

import numpy as np
import pandas as pd

from trading_agent.alpha_research.methodology import (
    AlphaEvaluator,
    AutoMLPipeline,
    apply_factor_transform,
    fit_factor_transform,
    make_chronological_folds,
)
from trading_agent.alpha_research.stats import (
    combinatorially_symmetric_cross_validation,
)


class SyntheticAlphaLibrary:
    def __init__(self, names: list[str]):
        self.names = names

    def list_alphas(self) -> list[dict[str, str]]:
        return [{"name": name, "category": "synthetic"} for name in self.names]

    def compute(self, name: str, frame: pd.DataFrame):
        return frame[name]


def _synthetic_frame(seed: int = 7, n: int = 480) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    true_signal = np.zeros(n)
    innovations = rng.normal(size=n)
    for index in range(1, n):
        true_signal[index] = 0.85 * true_signal[index - 1] + innovations[index]
    realized = 0.0015 * np.tanh(true_signal) + rng.normal(0.0, 0.001, n)
    close = np.empty(n)
    close[0] = 100.0
    for index in range(n - 1):
        close[index + 1] = close[index] * (1.0 + realized[index])
    return pd.DataFrame(
        {
            "close": close,
            "true_alpha": true_signal,
            "noise_a": rng.normal(size=n),
            "noise_b": rng.normal(size=n),
            "noise_c": rng.normal(size=n),
            "noise_d": rng.normal(size=n),
            "inverted_alpha": -true_signal,
        }
    )


def test_turnover_is_actual_target_position_change() -> None:
    evaluator = AlphaEvaluator(forward_periods=1, transaction_cost_bps=10.0)
    positions = np.array([0.0, 0.5, -0.5, -0.5, 1.0])
    series = evaluator.build_return_series(
        np.arange(5.0),
        np.full(5, 0.01),
        target_positions=positions,
    )
    np.testing.assert_allclose(series.turnover, [0.0, 0.5, 1.0, 0.0, 1.5])
    np.testing.assert_allclose(series.costs, series.turnover * 10.0 / 10_000.0)


def test_higher_cost_cannot_improve_any_net_return() -> None:
    evaluator = AlphaEvaluator(forward_periods=1)
    positions = np.array([0.0, 0.8, -0.8, 0.4, -0.4, 0.9])
    returns = np.array([0.0, 0.01, -0.02, 0.005, -0.004, 0.003])
    low = evaluator.build_return_series(
        positions, returns, target_positions=positions, transaction_cost_bps=1.0
    )
    high = evaluator.build_return_series(
        positions, returns, target_positions=positions, transaction_cost_bps=20.0
    )
    assert np.all(high.net_returns <= low.net_returns + 1e-15)
    assert np.nansum(high.net_returns) <= np.nansum(low.net_returns)


def test_higher_turnover_has_higher_cost_drag() -> None:
    n = 100
    evaluator = AlphaEvaluator(forward_periods=1, transaction_cost_bps=10.0)
    returns = np.linspace(-0.01, 0.01, n)
    low_positions = np.full(n, 0.5)
    high_positions = np.where(np.arange(n) % 2, 1.0, -1.0)
    low = evaluator.evaluate(
        low_positions, returns, target_positions=low_positions, name="low"
    )
    high = evaluator.evaluate(
        high_positions, returns, target_positions=high_positions, name="high"
    )
    assert high.turnover > low.turnover
    assert high.cost_drag > low.cost_drag


def test_timeframe_controls_annualization() -> None:
    rng = np.random.default_rng(9)
    returns = rng.normal(0.001, 0.01, 200)
    positions = np.ones(200)
    daily = AlphaEvaluator(
        forward_periods=1, transaction_cost_bps=0.0, timeframe="1d"
    ).evaluate(positions, returns, target_positions=positions)
    hourly = AlphaEvaluator(
        forward_periods=1, transaction_cost_bps=0.0, timeframe="1h"
    ).evaluate(positions, returns, target_positions=positions)
    assert hourly.net_sharpe > daily.net_sharpe


def test_purge_and_embargo_are_between_train_and_test() -> None:
    folds = make_chronological_folds(
        500, n_splits=4, min_train_size=150, purge=5, embargo=2
    )
    assert len(folds) == 4
    for fold in folds:
        assert fold.train_end + fold.purge + fold.embargo == fold.test_start
        assert fold.train_end <= fold.test_start


def test_inverted_factor_direction_is_learned_on_training_only() -> None:
    rng = np.random.default_rng(12)
    returns = rng.normal(size=200)
    inverted = -returns + rng.normal(0.0, 0.05, 200)
    transform = fit_factor_transform(inverted[:150], returns[:150])
    assert transform.direction == -1.0
    transformed = apply_factor_transform(inverted[150:], transform)
    assert np.corrcoef(transformed, returns[150:])[0, 1] > 0.8


def test_nested_walk_forward_ranks_true_alpha_and_does_not_grade_sort(tmp_path) -> None:
    frame = _synthetic_frame()
    names = [
        "true_alpha",
        "noise_a",
        "noise_b",
        "noise_c",
        "noise_d",
        "inverted_alpha",
    ]
    evaluator = AlphaEvaluator(
        forward_periods=1,
        transaction_cost_bps=1.0,
        timeframe="1h",
    )
    report = AutoMLPipeline(
        SyntheticAlphaLibrary(names), evaluator, outer_splits=3, inner_splits=2
    ).scan(frame, max_alphas=len(names), persist_report=False)
    top_names = [entry["name"] for entry in report["top_10"][:2]]
    assert "true_alpha" in top_names or "inverted_alpha" in top_names
    objectives = [entry["oos_objective"] for entry in report["top_10"]]
    assert objectives == sorted(objectives, reverse=True)
    for fold in report["outer_folds"]:
        assert fold["fit_end"] < fold["test_start"]


def test_final_outer_test_perturbation_cannot_change_final_fit(tmp_path) -> None:
    frame = _synthetic_frame(seed=13)
    names = ["true_alpha", "noise_a", "noise_b", "noise_c", "inverted_alpha"]
    pipeline = AutoMLPipeline(
        SyntheticAlphaLibrary(names),
        AlphaEvaluator(forward_periods=1, transaction_cost_bps=0.0),
        outer_splits=3,
        inner_splits=2,
    )
    baseline = pipeline.scan(frame, max_alphas=len(names), persist_report=False)
    last = baseline["outer_folds"][-1]
    perturbed = frame.copy()
    rng = np.random.default_rng(99)
    perturbed.loc[last["test_start"] : last["test_end"] - 1, names] = rng.normal(
        0.0, 10_000.0, (last["test_end"] - last["test_start"], len(names))
    )
    changed = pipeline.scan(perturbed, max_alphas=len(names), persist_report=False)
    changed_last = changed["outer_folds"][-1]
    assert changed_last["selected_factors"] == last["selected_factors"]
    assert changed_last["transforms"] == last["transforms"]


def test_positive_gross_but_negative_net_is_rejected() -> None:
    n = 200
    positions = np.where(np.arange(n) % 2, 1.0, -1.0)
    gross_target = 0.0002 + 0.00005 * np.sin(np.arange(n))
    future_returns = positions * gross_target
    report = AlphaEvaluator(forward_periods=1, transaction_cost_bps=100.0).evaluate(
        positions, future_returns, target_positions=positions
    )
    assert report.gross_sharpe > 0.0
    assert report.mean_return < 0.0
    assert report.net_sharpe < 0.0
    assert report.grade not in {"A", "B"}


def test_cscv_flags_noise_selection_more_than_stable_edge() -> None:
    rng = np.random.default_rng(123)
    n, trials = 320, 12
    stable = rng.normal(0.0, 0.01, (n, trials))
    stable[:, 0] += 0.01
    noise = rng.normal(0.0, 0.01, (n, trials))
    stable_pbo = combinatorially_symmetric_cross_validation(stable)["pbo"]
    noise_pbo = combinatorially_symmetric_cross_validation(noise)["pbo"]
    assert noise_pbo > stable_pbo
