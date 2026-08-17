#!/usr/bin/env python3
"""Deterministic methodology benchmarks against simple baselines.

These are synthetic diagnostics, not evidence of a tradable edge.  A method is
reported as empirically validated only when exchange observations are supplied;
this script deliberately has no such input and labels every result accordingly.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from trading_agent.alpha_research.methodology import (
    apply_factor_transform,
    fit_factor_transform,
)
from trading_agent.ml.online.adaptive import (
    FastExpert,
    MediumExpert,
    OnlineWeightAllocator,
    SlowExpert,
)
from trading_agent.ml.regime_detection import RegimePosterior, mix_regime_forecasts
from trading_agent.research.calibration import (
    CalibrationMethod,
    DataWindow,
    apply_calibrator,
    fit_calibration_artifact,
    reliability_diagram,
)


SEED = 20260817


def _net_metrics(position: np.ndarray, returns: np.ndarray, cost_bps: float = 2.0) -> dict[str, float]:
    position = np.asarray(position, dtype=float)
    returns = np.asarray(returns, dtype=float)
    turnover = np.abs(np.diff(position, prepend=0.0))
    net = position * returns - turnover * cost_bps * 1e-4
    volatility = float(np.std(net, ddof=1))
    sharpe = 0.0 if volatility <= 1e-15 else float(np.mean(net) / volatility * math.sqrt(252.0))
    equity = np.cumprod(1.0 + net)
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    return {
        "net_sharpe": round(sharpe, 6),
        "net_return": round(float(equity[-1] - 1.0), 6),
        "mean_turnover": round(float(np.mean(turnover)), 6),
        "max_drawdown": round(float(np.min(drawdown)), 6),
    }


def benchmark_alpha_ensemble(seed: int = SEED) -> dict[str, Any]:
    """Inner-train factor selection versus all-factor equal standardized baseline."""

    rng = np.random.default_rng(seed)
    observations, train_size, factors = 2_400, 1_000, 8
    latent = rng.normal(size=observations)
    values = np.empty((observations, factors))
    values[:, 0] = latent + rng.normal(scale=0.7, size=observations)
    values[:, 1] = 0.8 * latent + rng.normal(scale=0.9, size=observations)
    values[:, 2] = -0.6 * latent + rng.normal(scale=0.9, size=observations)
    values[:, 3:] = rng.normal(size=(observations, factors - 3))
    forward = 0.0015 * latent + rng.normal(scale=0.01, size=observations)

    transformed_train = []
    transformed_test = []
    train_scores = []
    for index in range(factors):
        transform = fit_factor_transform(values[:train_size, index], forward[:train_size])
        train = apply_factor_transform(values[:train_size, index], transform)
        test = apply_factor_transform(values[train_size:, index], transform)
        transformed_train.append(train)
        transformed_test.append(test)
        score = stats.spearmanr(train, forward[:train_size]).statistic
        train_scores.append(0.0 if not math.isfinite(float(score)) else abs(float(score)))
    train_matrix = np.column_stack(transformed_train)
    test_matrix = np.column_stack(transformed_test)
    selected = np.argsort(train_scores)[-3:]
    selected_position = np.tanh(np.mean(test_matrix[:, selected], axis=1))
    baseline_position = np.tanh(np.mean(test_matrix, axis=1))
    candidate = _net_metrics(selected_position, forward[train_size:])
    baseline = _net_metrics(baseline_position, forward[train_size:])
    winner = "selected_equal_standardized" if candidate["net_sharpe"] > baseline["net_sharpe"] else "all_factor_equal_standardized"
    return {
        "status": "SYNTHETIC_ONLY",
        "candidate": candidate,
        "baseline": baseline,
        "selected_factor_indices": [int(item) for item in sorted(selected)],
        "winner_on_this_fixture": winner,
        "production_claim": False,
    }


def _regime_series(seed: int, observations: int = 3_000) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    transition = np.array(
        [
            [0.94, 0.02, 0.02, 0.01, 0.01],
            [0.03, 0.91, 0.03, 0.01, 0.02],
            [0.04, 0.03, 0.88, 0.03, 0.02],
            [0.03, 0.02, 0.04, 0.88, 0.03],
            [0.04, 0.04, 0.03, 0.02, 0.87],
        ]
    )
    states = np.zeros(observations, dtype=int)
    returns = np.zeros(observations, dtype=float)
    for index in range(1, observations):
        states[index] = rng.choice(5, p=transition[states[index - 1]])
        state = states[index]
        previous = returns[index - 1]
        if state == 0:
            returns[index] = 0.0007 + 0.35 * previous + rng.normal(scale=0.006)
        elif state == 1:
            returns[index] = -0.50 * previous + rng.normal(scale=0.006)
        elif state == 2:
            returns[index] = rng.normal(scale=0.020)
        elif state == 3:
            returns[index] = -0.0020 + rng.normal(scale=0.014)
        else:
            returns[index] = rng.normal(scale=0.008)
    return states, returns


def benchmark_regime_mixture(seed: int = SEED + 1) -> dict[str, Any]:
    states, returns = _regime_series(seed)
    soft_positions = np.zeros_like(returns)
    baseline_positions = np.zeros_like(returns)
    for index in range(40, len(returns) - 1):
        true_state = states[index]
        probabilities = np.full(5, 0.05)
        probabilities[true_state] = 0.80
        posterior = RegimePosterior(*probabilities)
        rolling_mean = float(np.mean(returns[index - 20 : index]))
        rolling_vol = float(np.std(returns[index - 20 : index], ddof=1))
        expert_forecasts = {
            "trend": float(np.clip(rolling_mean / max(rolling_vol, 1e-12), -1.0, 1.0)),
            "mean_reversion": float(np.clip(-returns[index] / max(rolling_vol, 1e-12), -1.0, 1.0)),
            "high_vol": 0.0,
            "crisis": -0.5,
            "other": 0.0,
        }
        soft_positions[index] = mix_regime_forecasts(
            posterior, expert_forecasts, abstain_entropy=0.99
        ).forecast
        baseline_positions[index] = float(
            np.clip(rolling_mean / max(rolling_vol, 1e-12), -1.0, 1.0)
            * np.clip(0.01 / max(rolling_vol, 1e-12), 0.0, 1.0)
        )
    candidate = _net_metrics(soft_positions[:-1], returns[1:])
    baseline = _net_metrics(baseline_positions[:-1], returns[1:])
    return {
        "status": "ORACLE_ASSISTED_SYNTHETIC_ONLY",
        "candidate": candidate,
        "baseline": baseline,
        "winner_on_this_fixture": "soft_regime_mixture" if candidate["net_sharpe"] > baseline["net_sharpe"] else "trend_x_vol",
        "production_claim": False,
        "caveat": "posterior is generated from the hidden synthetic state and is not deployable evidence",
    }


def benchmark_adaptive_experts(seed: int = SEED + 2) -> dict[str, Any]:
    _, returns = _regime_series(seed, observations=2_000)
    prices = 100.0 * np.cumprod(1.0 + returns)
    allocator = OnlineWeightAllocator(
        [FastExpert(3), MediumExpert(12), SlowExpert(40)],
        learning_rate=250.0,
        max_weight=0.60,
        turnover_penalty=5.0,
        min_observations=30,
        uncertainty_shrinkage=1.0,
    )
    adaptive: list[float] = []
    fixed_equal: list[float] = []
    individual: list[list[float]] = [[], [], []]
    outcomes: list[float] = []
    for index in range(len(prices) - 1):
        result = allocator.observe_market(float(prices[index]))
        expert_values = list(result.expert_forecasts.values())
        adaptive.append(result.forecast)
        fixed_equal.append(float(np.mean(expert_values)))
        for expert_index, value in enumerate(expert_values):
            individual[expert_index].append(value)
        realized = float(prices[index + 1] / prices[index] - 1.0)
        outcomes.append(realized)
        allocator.observe_outcome(realized, observation_id=result.observation_id)
    candidate = _net_metrics(np.asarray(adaptive), np.asarray(outcomes))
    equal = _net_metrics(np.asarray(fixed_equal), np.asarray(outcomes))
    fixed = [_net_metrics(np.asarray(item), np.asarray(outcomes)) for item in individual]
    best_fixed = max(fixed, key=lambda item: item["net_sharpe"])
    winner = max(
        (
            (candidate["net_sharpe"], "adaptive"),
            (equal["net_sharpe"], "fixed_equal"),
            (best_fixed["net_sharpe"], "best_fixed_ex_post"),
        )
    )
    return {
        "status": "SYNTHETIC_ONLY",
        "candidate": candidate,
        "fixed_equal_baseline": equal,
        "best_fixed_ex_post_baseline": best_fixed,
        "winner_on_this_fixture": winner[1],
        "final_weights": {expert.name: round(float(weight), 6) for expert, weight in zip(allocator.experts, allocator.weights)},
        "production_claim": False,
    }


def benchmark_calibration(seed: int = SEED + 3) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    observations = 4_500
    true_probability = rng.beta(2.0, 2.0, size=observations)
    outcomes = rng.binomial(1, true_probability).astype(float)
    logits = np.log(np.clip(true_probability, 1e-8, 1.0) / np.clip(1.0 - true_probability, 1e-8, 1.0))
    raw = 1.0 / (1.0 + np.exp(-(1.8 * logits + 0.45)))
    train, validation = slice(0, 1_500), slice(1_500, 3_000)
    test = slice(3_000, observations)
    start = datetime(2020, 1, 1, tzinfo=UTC)
    artifact = fit_calibration_artifact(
        method=CalibrationMethod.PLATT,
        model_artifact_id="synthetic_calibration_benchmark",
        train_predictions=raw[train],
        train_outcomes=outcomes[train],
        validation_predictions=raw[validation],
        validation_outcomes=outcomes[validation],
        train_window=DataWindow(start, start + timedelta(days=1)),
        validation_window=DataWindow(start + timedelta(days=2), start + timedelta(days=3)),
        created_at=start + timedelta(days=4),
    )
    calibrated = apply_calibrator(raw[test], artifact)
    _, raw_ece = reliability_diagram(raw[test], outcomes[test])
    _, calibrated_ece = reliability_diagram(calibrated, outcomes[test])
    raw_brier = float(np.mean((raw[test] - outcomes[test]) ** 2))
    calibrated_brier = float(np.mean((calibrated - outcomes[test]) ** 2))
    return {
        "status": "SYNTHETIC_INDEPENDENT_TEST",
        "uncalibrated": {"brier": round(raw_brier, 6), "ece": round(raw_ece, 6)},
        "platt_calibrated": {"brier": round(calibrated_brier, 6), "ece": round(calibrated_ece, 6)},
        "brier_improved": calibrated_brier < raw_brier,
        "ece_improved": calibrated_ece < raw_ece,
        "production_claim": False,
    }


def run_benchmarks(seed: int = SEED) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "seed": seed,
        "evidence_class": "SYNTHETIC_DIAGNOSTIC",
        "alpha_ensemble_vs_equal_standardized": benchmark_alpha_ensemble(seed),
        "soft_regime_vs_trend_vol": benchmark_regime_mixture(seed + 1),
        "adaptive_experts_vs_fixed": benchmark_adaptive_experts(seed + 2),
        "mpc_vs_twap_pov": {
            "status": "NOT_EMPIRICALLY_BENCHMARKABLE",
            "reason": "MPC is a deterministic feasibility layer without a calibrated exchange impact model or held-out order-level dataset",
            "production_claim": False,
        },
        "calibrated_vs_uncalibrated": benchmark_calibration(seed + 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_benchmarks(args.seed)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
