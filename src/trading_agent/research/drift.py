"""Reference-frozen, metric-specific drift and sequential change detection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy import stats as sp_stats


class DriftLevel(Enum):
    OK = "ok"
    YELLOW = "yellow"
    RED = "red"


class StrategyHealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OOD = "ood"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class DriftResult:
    detector: str
    value: float
    reference: float
    level: DriftLevel
    threshold_yellow: float
    threshold_red: float

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "value": round(self.value, 6),
            "reference": round(self.reference, 6),
            "level": self.level.value,
            "threshold_yellow": self.threshold_yellow,
            "threshold_red": self.threshold_red,
        }


@dataclass(frozen=True)
class ReferenceHistogram:
    """PSI bins and probabilities fitted once on the reference sample."""

    edges: tuple[float, ...]
    probabilities: tuple[float, ...]
    sample_count: int

    @classmethod
    def fit(cls, reference: np.ndarray, bins: int = 10) -> "ReferenceHistogram":
        values = np.asarray(reference, dtype=float)
        values = values[np.isfinite(values)]
        if values.size == 0:
            raise ValueError("reference sample must contain finite observations")
        if bins < 2:
            raise ValueError("bins must be >= 2")
        quantiles = np.quantile(values, np.linspace(0.0, 1.0, bins + 1))
        interior = np.unique(quantiles[1:-1])
        if interior.size == 0:
            center = float(values[0])
            epsilon = max(abs(center), 1.0) * 1e-9
            interior = np.asarray([center - epsilon, center + epsilon])
        edges = np.concatenate(([-np.inf], interior, [np.inf]))
        counts, _ = np.histogram(values, bins=edges)
        probabilities = counts / counts.sum()
        return cls(
            edges=tuple(float(value) for value in edges),
            probabilities=tuple(float(value) for value in probabilities),
            sample_count=int(values.size),
        )

    def score(self, current: np.ndarray, epsilon: float = 1e-6) -> float:
        values = np.asarray(current, dtype=float)
        values = values[np.isfinite(values)]
        if values.size == 0:
            return math.inf
        counts, _ = np.histogram(values, bins=np.asarray(self.edges))
        current_probabilities = counts / counts.sum()
        reference_probabilities = np.asarray(self.probabilities)
        reference_probabilities = np.maximum(reference_probabilities, epsilon)
        current_probabilities = np.maximum(current_probabilities, epsilon)
        return float(
            np.sum(
                (current_probabilities - reference_probabilities)
                * np.log(current_probabilities / reference_probabilities)
            )
        )


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """PSI using quantile edges derived exclusively from the reference sample."""

    try:
        histogram = ReferenceHistogram.fit(reference, bins=bins)
    except ValueError:
        return math.inf
    return histogram.score(current)


def _level(value: float, yellow: float, red: float) -> DriftLevel:
    if not math.isfinite(value) or value > red:
        return DriftLevel.RED
    if value > yellow:
        return DriftLevel.YELLOW
    return DriftLevel.OK


def volatility_log_ratio(reference: float, current: float) -> float:
    if reference <= 0.0 or current <= 0.0:
        return math.inf if reference != current else 0.0
    return abs(math.log(current / reference))


def fisher_z_distance(reference: float, current: float) -> float:
    bound = 1.0 - 1e-12
    reference = float(np.clip(reference, -bound, bound))
    current = float(np.clip(current, -bound, bound))
    return abs(math.atanh(current) - math.atanh(reference))


@dataclass
class PageHinkley:
    """Small deterministic detector for persistent mean changes."""

    delta: float = 0.005
    threshold: float = 5.0
    forgetting: float = 0.999
    count: int = 0
    mean: float = 0.0
    cumulative: float = 0.0
    minimum: float = 0.0

    def update(self, value: float) -> bool:
        if not math.isfinite(float(value)):
            return True
        self.count += 1
        self.mean += (float(value) - self.mean) / self.count
        self.cumulative = (
            self.forgetting * self.cumulative + float(value) - self.mean - self.delta
        )
        self.minimum = min(self.minimum, self.cumulative)
        return self.cumulative - self.minimum > self.threshold

    def reset(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.cumulative = 0.0
        self.minimum = 0.0


class DriftMonitor:
    """Runs distribution and domain-specific drift metrics deterministically."""

    def __init__(
        self,
        *,
        psi_yellow: float = 0.10,
        psi_red: float = 0.25,
        rel_yellow: float = 0.50,
        rel_red: float = 1.00,
        wasserstein_yellow: float = 0.25,
        wasserstein_red: float = 0.75,
        ks_yellow: float = 0.10,
        ks_red: float = 0.20,
        correlation_yellow: float = 0.25,
        correlation_red: float = 0.50,
        calibration_yellow: float = 0.02,
        calibration_red: float = 0.05,
    ) -> None:
        self.psi_yellow = float(psi_yellow)
        self.psi_red = float(psi_red)
        self.log_ratio_yellow = math.log1p(float(rel_yellow))
        self.log_ratio_red = math.log1p(float(rel_red))
        self.wasserstein_yellow = float(wasserstein_yellow)
        self.wasserstein_red = float(wasserstein_red)
        self.ks_yellow = float(ks_yellow)
        self.ks_red = float(ks_red)
        self.correlation_yellow = float(correlation_yellow)
        self.correlation_red = float(correlation_red)
        self.calibration_yellow = float(calibration_yellow)
        self.calibration_red = float(calibration_red)

    def _distribution_results(
        self,
        prefix: str,
        reference: np.ndarray,
        current: np.ndarray,
    ) -> list[DriftResult]:
        ref = np.asarray(reference, dtype=float)
        cur = np.asarray(current, dtype=float)
        ref = ref[np.isfinite(ref)]
        cur = cur[np.isfinite(cur)]
        population_stability = psi(ref, cur)
        if ref.size == 0 or cur.size == 0:
            wasserstein = math.inf
            ks = math.inf
        else:
            scale = float(np.subtract(*np.quantile(ref, [0.75, 0.25])))
            if scale <= 1e-12:
                scale = float(np.std(ref))
            if scale <= 1e-12:
                scale = max(abs(float(np.mean(ref))), 1.0) * 1e-9
            wasserstein = float(sp_stats.wasserstein_distance(ref, cur) / scale)
            ks = float(sp_stats.ks_2samp(ref, cur).statistic)
        return [
            DriftResult(
                f"{prefix}_psi",
                population_stability,
                0.0,
                _level(population_stability, self.psi_yellow, self.psi_red),
                self.psi_yellow,
                self.psi_red,
            ),
            DriftResult(
                f"{prefix}_wasserstein",
                wasserstein,
                0.0,
                _level(wasserstein, self.wasserstein_yellow, self.wasserstein_red),
                self.wasserstein_yellow,
                self.wasserstein_red,
            ),
            DriftResult(
                f"{prefix}_ks",
                ks,
                0.0,
                _level(ks, self.ks_yellow, self.ks_red),
                self.ks_yellow,
                self.ks_red,
            ),
        ]

    @staticmethod
    def _degradation(reference: float, current: float) -> float:
        return max(0.0, float(current) - float(reference))

    def check_all(
        self,
        *,
        features_ref: np.ndarray | None = None,
        features_current: np.ndarray | None = None,
        returns_ref: np.ndarray | None = None,
        returns_current: np.ndarray | None = None,
        vol_ref: float | None = None,
        vol_current: float | None = None,
        corr_ref: float | None = None,
        corr_current: float | None = None,
        spread_ref: float | None = None,
        spread_current: float | None = None,
        fill_rate_ref: float | None = None,
        fill_rate_current: float | None = None,
        latency_ref: float | None = None,
        latency_current: float | None = None,
        adverse_selection_ref: float | None = None,
        adverse_selection_current: float | None = None,
        ece_ref: float | None = None,
        ece_current: float | None = None,
        brier_ref: float | None = None,
        brier_current: float | None = None,
        calibration_ref: float | None = None,
        calibration_current: float | None = None,
    ) -> list[DriftResult]:
        results: list[DriftResult] = []
        if features_ref is not None and features_current is not None:
            results.extend(
                self._distribution_results("feature", features_ref, features_current)
            )
        if returns_ref is not None and returns_current is not None:
            results.extend(
                self._distribution_results("return", returns_ref, returns_current)
            )
        if vol_ref is not None and vol_current is not None:
            distance = volatility_log_ratio(vol_ref, vol_current)
            results.append(
                DriftResult(
                    "volatility_log_ratio",
                    distance,
                    0.0,
                    _level(distance, self.log_ratio_yellow, self.log_ratio_red),
                    self.log_ratio_yellow,
                    self.log_ratio_red,
                )
            )
        if corr_ref is not None and corr_current is not None:
            distance = fisher_z_distance(corr_ref, corr_current)
            results.append(
                DriftResult(
                    "correlation_fisher_z",
                    distance,
                    0.0,
                    _level(distance, self.correlation_yellow, self.correlation_red),
                    self.correlation_yellow,
                    self.correlation_red,
                )
            )
        for name, reference, current in (
            ("spread_log_ratio", spread_ref, spread_current),
            ("latency_log_ratio", latency_ref, latency_current),
        ):
            if reference is not None and current is not None:
                distance = volatility_log_ratio(reference, current)
                results.append(
                    DriftResult(
                        name,
                        distance,
                        0.0,
                        _level(distance, self.log_ratio_yellow, self.log_ratio_red),
                        self.log_ratio_yellow,
                        self.log_ratio_red,
                    )
                )
        if fill_rate_ref is not None and fill_rate_current is not None:
            distance = max(0.0, float(fill_rate_ref) - float(fill_rate_current))
            results.append(
                DriftResult(
                    "fill_rate_drop",
                    distance,
                    0.0,
                    _level(distance, 0.05, 0.15),
                    0.05,
                    0.15,
                )
            )
        if adverse_selection_ref is not None and adverse_selection_current is not None:
            distance = self._degradation(
                adverse_selection_ref, adverse_selection_current
            )
            results.append(
                DriftResult(
                    "adverse_selection_drift",
                    distance,
                    0.0,
                    _level(distance, 0.0005, 0.002),
                    0.0005,
                    0.002,
                )
            )
        for name, reference, current in (
            ("ece_drift", ece_ref, ece_current),
            ("brier_drift", brier_ref, brier_current),
            ("calibration_legacy_absolute", calibration_ref, calibration_current),
        ):
            if reference is not None and current is not None:
                distance = abs(float(current) - float(reference))
                results.append(
                    DriftResult(
                        name,
                        distance,
                        0.0,
                        _level(
                            distance,
                            self.calibration_yellow,
                            self.calibration_red,
                        ),
                        self.calibration_yellow,
                        self.calibration_red,
                    )
                )
        return results

    def health_state(self, results: list[DriftResult]) -> StrategyHealthState:
        levels = {result.level for result in results}
        if DriftLevel.RED in levels:
            return StrategyHealthState.SUSPENDED
        if any(
            result.detector.startswith(("feature_", "return_"))
            and result.level == DriftLevel.YELLOW
            for result in results
        ):
            return StrategyHealthState.OOD
        if DriftLevel.YELLOW in levels:
            return StrategyHealthState.DEGRADED
        return StrategyHealthState.HEALTHY
