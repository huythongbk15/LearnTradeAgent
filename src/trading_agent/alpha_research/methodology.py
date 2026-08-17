"""Causal alpha evaluation and nested walk-forward model selection.

The module deliberately separates three concerns that were historically mixed:
fitting a factor transform, constructing a tradable return series, and measuring
out-of-sample performance.  Every transform and direction is fitted on training
data only; outer test folds are used once for reporting.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats as sp_stats

from .stats import combinatorially_symmetric_cross_validation


GRADE_SCORE = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def periods_per_year_for_timeframe(
    timeframe: str,
    asset_class: str = "crypto",
) -> float:
    """Return an explicit annualization factor for common bar timeframes."""

    value = timeframe.strip().lower()
    if not value:
        raise ValueError("timeframe must not be empty")
    unit = value[-1]
    try:
        amount = float(value[:-1])
    except ValueError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe!r}") from exc
    if amount <= 0:
        raise ValueError("timeframe amount must be positive")

    crypto = asset_class.strip().lower() in {"crypto", "digital_asset"}
    days = 365.0 if crypto else 252.0
    if unit == "d":
        return days / amount
    if unit == "h":
        hours_per_day = 24.0 if crypto else 6.5
        return days * hours_per_day / amount
    if unit == "m":
        hours_per_day = 24.0 if crypto else 6.5
        return days * hours_per_day * 60.0 / amount
    if unit == "w":
        return days / (7.0 * amount)
    raise ValueError(f"unsupported timeframe unit: {unit!r}")


@dataclass(frozen=True)
class ReturnSeries:
    """Auditable position, turnover, cost, and strategy-return vectors."""

    target_positions: np.ndarray
    turnover: np.ndarray
    gross_returns: np.ndarray
    costs: np.ndarray
    net_returns: np.ndarray


@dataclass
class AlphaEvaluation:
    """Performance report for one alpha on one evaluation sample."""

    name: str
    category: str
    gross_sharpe: float = 0.0
    net_sharpe: float = 0.0
    mean_return: float = 0.0
    volatility: float = 0.0
    max_drawdown: float = 0.0
    turnover: float = 0.0
    cost_drag: float = 0.0
    ic_mean: float = 0.0
    ic_ir: float = 0.0
    decay_halflife: int = 0
    monotonicity: float = 0.0
    n_samples: int = 0
    correlation_with_others: dict[str, float] = field(default_factory=dict)
    grade: str = "F"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def sharpe(self) -> float:
        """Compatibility alias: selection always uses after-cost Sharpe."""

        return self.net_sharpe


# Compatibility for callers that imported the previous name.
AlphaReport = AlphaEvaluation


@dataclass(frozen=True)
class FactorTransform:
    """Winsorization, robust scaling, and sign learned on one training set."""

    lower: float
    upper: float
    median: float
    scale: float
    direction: float
    n_fitted: int


@dataclass(frozen=True)
class ChronologicalFold:
    """Half-open train and test ranges with an explicit purge/embargo gap."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int
    purge: int
    embargo: int


def fit_factor_transform(
    values: np.ndarray,
    forward_returns: np.ndarray,
    *,
    winsor_quantile: float = 0.01,
) -> FactorTransform:
    """Fit all preprocessing and the predictive direction on training data."""

    x = np.asarray(values, dtype=float)
    y = np.asarray(forward_returns, dtype=float)
    if x.shape != y.shape:
        raise ValueError("values and forward_returns must have identical shapes")
    valid = np.isfinite(x) & np.isfinite(y)
    sample = x[valid]
    if sample.size < 10:
        return FactorTransform(-1.0, 1.0, 0.0, 1.0, 1.0, int(sample.size))

    q = min(max(float(winsor_quantile), 0.0), 0.20)
    lower, upper = np.quantile(sample, [q, 1.0 - q])
    clipped = np.clip(sample, lower, upper)
    median = float(np.median(clipped))
    q25, q75 = np.quantile(clipped, [0.25, 0.75])
    scale = float(q75 - q25)
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = float(np.std(clipped))
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = 1.0

    correlation = sp_stats.spearmanr(sample, y[valid]).statistic
    direction = -1.0 if math.isfinite(correlation) and correlation < 0.0 else 1.0
    return FactorTransform(
        lower=float(lower),
        upper=float(upper),
        median=median,
        scale=scale,
        direction=direction,
        n_fitted=int(sample.size),
    )


def apply_factor_transform(values: np.ndarray, transform: FactorTransform) -> np.ndarray:
    """Apply a frozen train-only transform without learning from its input."""

    x = np.asarray(values, dtype=float)
    finite = np.isfinite(x)
    result = np.zeros_like(x, dtype=float)
    result[finite] = (
        (np.clip(x[finite], transform.lower, transform.upper) - transform.median)
        / transform.scale
        * transform.direction
    )
    return result


def make_chronological_folds(
    n_observations: int,
    *,
    n_splits: int,
    min_train_size: int,
    purge: int,
    embargo: int,
) -> list[ChronologicalFold]:
    """Build expanding outer/inner folds; ranges are chronological and disjoint."""

    n_observations = int(n_observations)
    gap = max(0, int(purge)) + max(0, int(embargo))
    min_train_size = max(20, int(min_train_size))
    if n_observations <= min_train_size + gap + 10:
        return []
    test_indices = np.arange(min_train_size + gap, n_observations)
    blocks = [block for block in np.array_split(test_indices, max(1, int(n_splits))) if len(block)]
    folds: list[ChronologicalFold] = []
    for block in blocks:
        test_start = int(block[0])
        test_end = int(block[-1]) + 1
        train_end = test_start - gap
        if train_end < min_train_size or test_end - test_start < 5:
            continue
        folds.append(
            ChronologicalFold(
                train_start=0,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                purge=max(0, int(purge)),
                embargo=max(0, int(embargo)),
            )
        )
    return folds


class AlphaEvaluator:
    """Evaluate realized position returns, after turnover-dependent costs."""

    def __init__(
        self,
        forward_periods: int = 5,
        *,
        transaction_cost_bps: float = 1.0,
        timeframe: str = "1d",
        asset_class: str = "crypto",
        periods_per_year: float | None = None,
        rank_window: int = 252,
    ):
        if forward_periods < 1:
            raise ValueError("forward_periods must be >= 1")
        if transaction_cost_bps < 0:
            raise ValueError("transaction_cost_bps must be non-negative")
        self.forward_periods = int(forward_periods)
        self.transaction_cost_bps = float(transaction_cost_bps)
        self.timeframe = timeframe
        self.asset_class = asset_class
        self.periods_per_year = float(
            periods_per_year
            if periods_per_year is not None
            else periods_per_year_for_timeframe(timeframe, asset_class)
        )
        self.rank_window = max(10, int(rank_window))

    @property
    def annualization(self) -> float:
        return math.sqrt(self.periods_per_year / self.forward_periods)

    def _causal_rank_positions(self, alpha_values: np.ndarray) -> np.ndarray:
        values = np.asarray(alpha_values, dtype=float)
        positions = np.zeros_like(values)
        for index, value in enumerate(values):
            if not math.isfinite(value):
                continue
            start = max(0, index - self.rank_window + 1)
            history = values[start : index + 1]
            history = history[np.isfinite(history)]
            if history.size < 5:
                continue
            less = float(np.sum(history < value))
            equal = float(np.sum(history == value))
            percentile = (less + 0.5 * equal) / history.size
            positions[index] = 2.0 * percentile - 1.0
        return positions

    def build_return_series(
        self,
        alpha_values: np.ndarray,
        forward_returns: np.ndarray,
        *,
        direction: float = 1.0,
        target_positions: np.ndarray | None = None,
        transaction_cost_bps: float | None = None,
    ) -> ReturnSeries:
        """Construct gross/net returns from actual target-position changes."""

        alpha = np.asarray(alpha_values, dtype=float)
        returns = np.asarray(forward_returns, dtype=float)
        if alpha.shape != returns.shape:
            raise ValueError("alpha_values and forward_returns must have identical shapes")
        if target_positions is None:
            positions = self._causal_rank_positions(alpha) * float(direction)
        else:
            positions = np.asarray(target_positions, dtype=float)
            if positions.shape != returns.shape:
                raise ValueError("target_positions and forward_returns must have identical shapes")
            positions = positions.copy()
        positions = np.where(np.isfinite(positions), np.clip(positions, -1.0, 1.0), 0.0)
        turnover = np.abs(np.diff(positions, prepend=0.0))
        cost_bps = self.transaction_cost_bps if transaction_cost_bps is None else float(transaction_cost_bps)
        if cost_bps < 0:
            raise ValueError("transaction_cost_bps must be non-negative")
        costs = turnover * cost_bps / 10_000.0
        gross = positions * returns
        net = gross - costs
        invalid = ~np.isfinite(returns)
        gross[invalid] = np.nan
        net[invalid] = np.nan
        costs[invalid] = np.nan
        turnover[invalid] = np.nan
        return ReturnSeries(positions, turnover, gross, costs, net)

    def _sharpe(self, returns: np.ndarray) -> float:
        sample = np.asarray(returns, dtype=float)
        sample = sample[np.isfinite(sample)]
        if sample.size < 3:
            return 0.0
        volatility = float(np.std(sample, ddof=1))
        if volatility <= 1e-12:
            return 0.0
        return float(np.mean(sample) / volatility * self.annualization)

    @staticmethod
    def _max_drawdown(returns: np.ndarray) -> float:
        sample = np.asarray(returns, dtype=float)
        sample = sample[np.isfinite(sample)]
        if sample.size == 0:
            return 0.0
        equity = np.cumprod(1.0 + np.clip(sample, -0.999999, None))
        peaks = np.maximum.accumulate(equity)
        drawdowns = equity / np.maximum(peaks, 1e-12) - 1.0
        return float(np.min(drawdowns))

    @staticmethod
    def objective(report: AlphaEvaluation) -> float:
        """OOS objective: net Sharpe minus drawdown, turnover, and cost penalties."""

        return float(
            report.net_sharpe
            - 0.50 * abs(report.max_drawdown)
            - 0.25 * report.turnover
            - 0.25 * abs(report.cost_drag)
        )

    def evaluate(
        self,
        alpha_values: np.ndarray,
        forward_returns: np.ndarray,
        name: str = "",
        category: str = "",
        *,
        direction: float = 1.0,
        target_positions: np.ndarray | None = None,
        transaction_cost_bps: float | None = None,
    ) -> AlphaEvaluation:
        alpha = np.asarray(alpha_values, dtype=float)
        returns = np.asarray(forward_returns, dtype=float)
        if alpha.shape != returns.shape:
            raise ValueError("alpha_values and forward_returns must have identical shapes")
        valid = np.isfinite(alpha) & np.isfinite(returns)
        report = AlphaEvaluation(name=name, category=category, n_samples=int(valid.sum()))
        if valid.sum() < 30:
            report.details = {"n_valid": int(valid.sum()), "reason": "insufficient_sample"}
            return report

        signal = alpha[valid]
        future = returns[valid]
        series = self.build_return_series(
            signal,
            future,
            direction=direction,
            target_positions=None if target_positions is None else np.asarray(target_positions)[valid],
            transaction_cost_bps=transaction_cost_bps,
        )
        ic = sp_stats.spearmanr(signal, future).statistic
        report.ic_mean = float(ic) if math.isfinite(ic) else 0.0

        window = max(10, min(50, len(signal) // 4))
        rolling_ic: list[float] = []
        for start in range(0, len(signal) - window + 1, window):
            value = sp_stats.spearmanr(
                signal[start : start + window], future[start : start + window]
            ).statistic
            if math.isfinite(value):
                rolling_ic.append(float(value))
        if len(rolling_ic) > 1:
            dispersion = float(np.std(rolling_ic, ddof=1))
            if dispersion > 1e-12:
                report.ic_ir = float(np.mean(rolling_ic) / dispersion)

        report.gross_sharpe = self._sharpe(series.gross_returns)
        report.net_sharpe = self._sharpe(series.net_returns)
        report.mean_return = float(np.nanmean(series.net_returns))
        report.volatility = float(np.nanstd(series.net_returns, ddof=1))
        report.max_drawdown = self._max_drawdown(series.net_returns)
        report.turnover = float(np.nanmean(series.turnover))
        report.cost_drag = float(np.nanmean(series.costs) * self.periods_per_year)
        report.decay_halflife = self._estimate_decay(signal, future)
        report.monotonicity = self._monotonicity(signal, future)

        if report.net_sharpe >= 1.5 and abs(report.ic_mean) >= 0.05:
            report.grade = "A"
        elif report.net_sharpe >= 0.5 and abs(report.ic_mean) >= 0.02:
            report.grade = "B"
        elif report.net_sharpe > 0.0 and abs(report.ic_mean) >= 0.01:
            report.grade = "C"
        elif report.net_sharpe > -0.5:
            report.grade = "D"
        else:
            report.grade = "F"

        report.details = {
            "n_valid": report.n_samples,
            "ic_mean": round(report.ic_mean, 6),
            "ic_ir": round(report.ic_ir, 6),
            "gross_sharpe": round(report.gross_sharpe, 6),
            "net_sharpe": round(report.net_sharpe, 6),
            "sharpe": round(report.net_sharpe, 6),
            "mean_return": round(report.mean_return, 10),
            "volatility": round(report.volatility, 10),
            "max_drawdown": round(report.max_drawdown, 6),
            "turnover": round(report.turnover, 6),
            "cost_drag": round(report.cost_drag, 6),
            "decay_halflife": report.decay_halflife,
            "monotonicity": round(report.monotonicity, 6),
            "periods_per_year": self.periods_per_year,
            "forward_periods": self.forward_periods,
            "transaction_cost_bps": (
                self.transaction_cost_bps
                if transaction_cost_bps is None
                else float(transaction_cost_bps)
            ),
        }
        return report

    @staticmethod
    def _monotonicity(alpha: np.ndarray, returns: np.ndarray) -> float:
        if len(alpha) < 50:
            return 0.0
        ordered = np.argsort(alpha)
        buckets = [part for part in np.array_split(ordered, 5) if len(part)]
        means = [float(np.mean(returns[part])) for part in buckets]
        increasing = sum(right > left for left, right in zip(means, means[1:]))
        decreasing = sum(right < left for left, right in zip(means, means[1:]))
        return float(max(increasing, decreasing) / max(1, len(means) - 1))

    @staticmethod
    def _estimate_decay(alpha: np.ndarray, forward_returns: np.ndarray) -> int:
        max_lag = min(20, len(alpha) // 5)
        base_ic = sp_stats.spearmanr(alpha, forward_returns).statistic
        if not math.isfinite(base_ic) or abs(base_ic) < 0.01:
            return 0
        half_ic = abs(base_ic) / 2.0
        for lag in range(1, max_lag):
            ic = sp_stats.spearmanr(alpha[:-lag], forward_returns[lag:]).statistic
            if math.isfinite(ic) and abs(ic) < half_ic:
                return lag
        return max_lag

    def correlation_matrix(
        self,
        alpha_values: dict[str, np.ndarray],
    ) -> dict[str, dict[str, float]]:
        names = list(alpha_values)
        matrix: dict[str, dict[str, float]] = {}
        for left in names:
            matrix[left] = {}
            for right in names:
                a = np.asarray(alpha_values[left], dtype=float)
                b = np.asarray(alpha_values[right], dtype=float)
                valid = np.isfinite(a) & np.isfinite(b)
                if valid.sum() <= 10:
                    matrix[left][right] = 0.0
                    continue
                correlation = sp_stats.spearmanr(a[valid], b[valid]).statistic
                matrix[left][right] = (
                    round(float(correlation), 6) if math.isfinite(correlation) else 0.0
                )
        return matrix


class AutoMLPipeline:
    """Nested expanding walk-forward selection with untouched outer folds."""

    def __init__(
        self,
        alpha_lib: Any,
        evaluator: AlphaEvaluator,
        *,
        outer_splits: int = 4,
        inner_splits: int = 3,
        embargo: int = 1,
        max_selected: int = 5,
        redundancy_threshold: float = 0.85,
    ):
        self.lib = alpha_lib
        self.eval = evaluator
        self.outer_splits = max(2, int(outer_splits))
        self.inner_splits = max(2, int(inner_splits))
        self.embargo = max(0, int(embargo))
        self.max_selected = max(1, int(max_selected))
        self.redundancy_threshold = float(redundancy_threshold)

    def _inner_score(
        self,
        values: np.ndarray,
        returns: np.ndarray,
        outer_train_end: int,
    ) -> float:
        folds = make_chronological_folds(
            outer_train_end,
            n_splits=self.inner_splits,
            min_train_size=max(40, outer_train_end // 2),
            purge=self.eval.forward_periods,
            embargo=self.embargo,
        )
        scores: list[float] = []
        for fold in folds:
            transform = fit_factor_transform(
                values[fold.train_start : fold.train_end],
                returns[fold.train_start : fold.train_end],
            )
            transformed = apply_factor_transform(
                values[fold.test_start : fold.test_end], transform
            )
            positions = np.tanh(transformed)
            report = self.eval.evaluate(
                transformed,
                returns[fold.test_start : fold.test_end],
                target_positions=positions,
            )
            if report.n_samples >= 30:
                scores.append(self.eval.objective(report))
        if not scores:
            return -math.inf
        return float(np.mean(scores) - 0.25 * np.std(scores))

    def _select_uncorrelated(
        self,
        ranked_names: list[str],
        inner_scores: dict[str, float],
        transformed_train: dict[str, np.ndarray],
    ) -> list[str]:
        selected: list[str] = []
        for name in ranked_names:
            if not math.isfinite(inner_scores.get(name, -math.inf)):
                continue
            candidate = transformed_train[name]
            redundant = False
            for existing in selected:
                peer = transformed_train[existing]
                valid = np.isfinite(candidate) & np.isfinite(peer)
                if valid.sum() > 10:
                    corr = np.corrcoef(candidate[valid], peer[valid])[0, 1]
                    if math.isfinite(corr) and abs(corr) >= self.redundancy_threshold:
                        redundant = True
                        break
            if not redundant:
                selected.append(name)
            if len(selected) >= self.max_selected:
                break
        return selected

    @staticmethod
    def _selection_instability(selections: list[list[str]]) -> float:
        if len(selections) < 2:
            return 0.0
        distances: list[float] = []
        for left_index in range(len(selections)):
            for right_index in range(left_index + 1, len(selections)):
                left = set(selections[left_index])
                right = set(selections[right_index])
                union = left | right
                distances.append(0.0 if not union else 1.0 - len(left & right) / len(union))
        return float(np.mean(distances)) if distances else 0.0

    def scan(
        self,
        df,
        target_col: str = "close",
        forward_periods: int | None = None,
        max_alphas: int = 40,
        report_path: str = "alpha_reports",
        *,
        persist_report: bool = True,
    ) -> dict[str, Any]:
        """Fit/select on inner folds and evaluate once on each outer test fold."""

        horizon = self.eval.forward_periods if forward_periods is None else int(forward_periods)
        if horizon != self.eval.forward_periods:
            raise ValueError("forward_periods must match the evaluator horizon")
        forward_returns = (
            df[target_col].pct_change(horizon).shift(-horizon).to_numpy(dtype=float)
        )
        n_usable = len(forward_returns) - horizon
        if n_usable < 120:
            raise ValueError("at least 120 usable observations are required")

        categories: dict[str, str] = {}
        alpha_values: dict[str, np.ndarray] = {}
        failures: dict[str, str] = {}
        for alpha_info in self.lib.list_alphas()[:max_alphas]:
            name = alpha_info["name"]
            categories[name] = alpha_info.get("category", "")
            try:
                values = self.lib.compute(name, df)
                values = values.to_numpy(dtype=float) if hasattr(values, "to_numpy") else np.asarray(values, dtype=float)
                if values.shape != forward_returns.shape:
                    raise ValueError("factor length does not match price history")
                alpha_values[name] = values
            except Exception as exc:  # failure is retained in the audit report
                failures[name] = f"{type(exc).__name__}: {exc}"

        if not alpha_values:
            raise ValueError("no alpha factors could be computed")
        outer_folds = make_chronological_folds(
            n_usable,
            n_splits=self.outer_splits,
            min_train_size=max(80, n_usable // 3),
            purge=horizon,
            embargo=self.embargo,
        )
        if len(outer_folds) < 2:
            raise ValueError("insufficient data for nested outer folds")

        candidate_positions = {
            name: np.full(n_usable, np.nan, dtype=float) for name in alpha_values
        }
        candidate_signals = {
            name: np.full(n_usable, np.nan, dtype=float) for name in alpha_values
        }
        candidate_net_returns = {
            name: np.full(n_usable, np.nan, dtype=float) for name in alpha_values
        }
        composite_positions = np.full(n_usable, np.nan, dtype=float)
        composite_signals = np.full(n_usable, np.nan, dtype=float)
        fold_records: list[dict[str, Any]] = []
        selections: list[list[str]] = []

        for fold_number, fold in enumerate(outer_folds):
            inner_scores = {
                name: self._inner_score(values, forward_returns, fold.train_end)
                for name, values in alpha_values.items()
            }
            ranked = sorted(
                alpha_values,
                key=lambda name: (inner_scores[name], name),
                reverse=True,
            )
            transforms: dict[str, FactorTransform] = {}
            transformed_train: dict[str, np.ndarray] = {}
            for name, values in alpha_values.items():
                transform = fit_factor_transform(
                    values[fold.train_start : fold.train_end],
                    forward_returns[fold.train_start : fold.train_end],
                )
                transforms[name] = transform
                transformed_train[name] = apply_factor_transform(
                    values[fold.train_start : fold.train_end], transform
                )
            selected = self._select_uncorrelated(ranked, inner_scores, transformed_train)
            if not selected:
                selected = ranked[:1]
            selections.append(selected)

            selected_test_positions: list[np.ndarray] = []
            for name, values in alpha_values.items():
                transformed_test = apply_factor_transform(
                    values[fold.test_start : fold.test_end], transforms[name]
                )
                positions = np.tanh(transformed_test)
                series = self.eval.build_return_series(
                    transformed_test,
                    forward_returns[fold.test_start : fold.test_end],
                    target_positions=positions,
                )
                candidate_signals[name][fold.test_start : fold.test_end] = transformed_test
                candidate_positions[name][fold.test_start : fold.test_end] = positions
                candidate_net_returns[name][fold.test_start : fold.test_end] = series.net_returns
                if name in selected:
                    selected_test_positions.append(positions)

            combo_positions = np.mean(np.vstack(selected_test_positions), axis=0)
            combo_signal = combo_positions.copy()
            composite_positions[fold.test_start : fold.test_end] = combo_positions
            composite_signals[fold.test_start : fold.test_end] = combo_signal
            combo_report = self.eval.evaluate(
                combo_signal,
                forward_returns[fold.test_start : fold.test_end],
                name=f"outer_fold_{fold_number}",
                category="composite",
                target_positions=combo_positions,
            )
            fold_records.append(
                {
                    **asdict(fold),
                    "fold": fold_number,
                    "fit_end": fold.train_end - 1,
                    "selected_factors": selected,
                    "inner_scores": {
                        name: (round(score, 8) if math.isfinite(score) else None)
                        for name, score in inner_scores.items()
                    },
                    "transforms": {
                        name: asdict(transforms[name]) for name in selected
                    },
                    "oos_net_sharpe": combo_report.net_sharpe,
                    "oos_gross_sharpe": combo_report.gross_sharpe,
                    "oos_objective": self.eval.objective(combo_report),
                }
            )

        reports: list[AlphaEvaluation] = []
        oos_mask = np.isfinite(composite_positions)
        for name in alpha_values:
            mask = np.isfinite(candidate_positions[name]) & np.isfinite(forward_returns[:n_usable])
            report = self.eval.evaluate(
                candidate_signals[name][mask],
                forward_returns[:n_usable][mask],
                name=name,
                category=categories[name],
                target_positions=candidate_positions[name][mask],
            )
            report.details["oos_objective"] = round(self.eval.objective(report), 8)
            reports.append(report)
        reports.sort(
            key=lambda report: (
                float(report.details["oos_objective"]),
                report.net_sharpe,
                abs(report.ic_mean),
            ),
            reverse=True,
        )

        composite_report = self.eval.evaluate(
            composite_signals[oos_mask],
            forward_returns[:n_usable][oos_mask],
            name="nested_walk_forward_composite",
            category="composite",
            target_positions=composite_positions[oos_mask],
        )
        selection_counts = {
            name: sum(name in selected for selected in selections) for name in alpha_values
        }
        final_names = sorted(
            (name for name, count in selection_counts.items() if count),
            key=lambda name: (selection_counts[name], -reports.index(next(r for r in reports if r.name == name))),
            reverse=True,
        )[: self.max_selected]

        # CSCV must see the searched trial space, not a subset chosen with OOS data.
        pbo_names = list(alpha_values)
        pbo_matrix = np.column_stack([candidate_net_returns[name] for name in pbo_names])
        pbo = combinatorially_symmetric_cross_validation(pbo_matrix, n_slices=8)
        top_10 = [
            {
                "name": report.name,
                "category": report.category,
                "grade": report.grade,
                **report.details,
            }
            for report in reports[:10]
        ]
        report_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "methodology": {
                "selection": "nested expanding walk-forward; inner folds only",
                "evaluation": "outer test folds used once",
                "normalization": "train-only winsorization, robust scaling, and sign",
                "objective": "net OOS Sharpe - 0.50*|MDD| - 0.25*turnover - 0.25*|annual cost drag|",
                "purge": horizon,
                "embargo": self.embargo,
                "timeframe": self.eval.timeframe,
                "periods_per_year": self.eval.periods_per_year,
                "transaction_cost_bps": self.eval.transaction_cost_bps,
            },
            "total_alphas": len(reports),
            "top_10": top_10,
            "alphas": [
                {"name": report.name, "category": report.category, "grade": report.grade, **report.details}
                for report in reports
            ],
            "best_combo": {
                "names": final_names,
                "gross_sharpe": composite_report.gross_sharpe,
                "net_sharpe": composite_report.net_sharpe,
                "objective": self.eval.objective(composite_report),
                "selection_frequency": selection_counts,
                "n_outer_folds": len(outer_folds),
            },
            "outer_folds": fold_records,
            "selection_instability": self._selection_instability(selections),
            "pbo": pbo["pbo"],
            "pbo_n_splits": pbo["n_splits"],
            "pbo_logit_ranks": pbo["logit_ranks"],
            "pbo_oos_degradation": pbo["oos_degradation"],
            "pbo_candidates": pbo_names,
            "grade_distribution": {
                grade: sum(report.grade == grade for report in reports) for grade in GRADE_SCORE
            },
            "factor_failures": failures,
        }
        if persist_report:
            destination = Path(report_path)
            destination.mkdir(parents=True, exist_ok=True)
            filename = destination / (
                f"alpha_scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
            )
            filename.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")
            report_data["report_file"] = str(filename)
        return report_data
