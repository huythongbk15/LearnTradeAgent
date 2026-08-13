"""Drift / change detection and strategy health (Section 11).

``StrategyHealthState`` is derived from drift detectors:

    HEALTHY   — all detectors green
    DEGRADED  — at least one detector yellow (actionable, reduces conviction)
    OOD       — data drift or regime change detected (inputs outside training)
    SUSPENDED — any detector red (halt trading, fail closed)

Drift metrics:

* feature drift (PSI — population stability index)
* return drift, volatility drift (rolling vs reference)
* correlation drift (asset/feature correlation vs reference)
* spread drift (market spread vs reference)
* fill-rate drift (execution fill ratio vs reference)
* calibration drift (model calibration vs reference)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np


class DriftLevel(Enum):
    OK = "ok"
    YELLOW = "yellow"
    RED = "red"


class StrategyHealthState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OOD = "ood"
    SUSPENDED = "suspended"


@dataclass
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


def psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    """Population stability index between two distributions."""
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.size == 0 or cur.size == 0:
        return 1.0  # missing data = drift (fail closed)
    lo = min(float(ref.min()), float(cur.min()))
    hi = max(float(ref.max()), float(cur.max()))
    if hi == lo:
        return 0.0
    edges = np.linspace(lo, hi, bins + 1)
    ref_hist, _ = np.histogram(ref, bins=edges)
    cur_hist, _ = np.histogram(cur, bins=edges)
    ref_pct = ref_hist / max(ref_hist.sum(), 1)
    cur_pct = cur_hist / max(cur_hist.sum(), 1)
    out = 0.0
    for r, c in zip(ref_pct, cur_pct):
        if r == 0 and c == 0:
            continue
        r = max(r, 1e-6)
        c = max(c, 1e-6)
        out += (c - r) * math.log(c / r)
    return out


def _level(
    value: float, threshold: float, yellow: float = 0.1, red: float = 0.25
) -> DriftLevel:
    """Map a deviation to OK/YELLOW/RED via absolute thresholds."""
    if value > red:
        return DriftLevel.RED
    if value > yellow:
        return DriftLevel.YELLOW
    return DriftLevel.OK


class DriftMonitor:
    """Runs all configured drift detectors deterministically."""

    def __init__(
        self,
        *,
        psi_yellow: float = 0.10,
        psi_red: float = 0.25,
        rel_yellow: float = 0.50,
        rel_red: float = 1.00,
    ) -> None:
        self.psi_yellow = psi_yellow
        self.psi_red = psi_red
        self.rel_yellow = rel_yellow
        self.rel_red = rel_red

    def _rel(self, value: float, ref: float) -> float:
        if ref == 0:
            return 1.0 if value != 0 else 0.0
        return abs(value - ref) / abs(ref)

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
        calibration_ref: float | None = None,
        calibration_current: float | None = None,
    ) -> list[DriftResult]:
        """Run every detector that has both ref and current values."""
        results: list[DriftResult] = []

        if features_ref is not None and features_current is not None:
            p = psi(features_ref, features_current)
            results.append(
                DriftResult(
                    "feature_psi",
                    p,
                    0.0,
                    _level(p, 0, self.psi_yellow, self.psi_red),
                    self.psi_yellow,
                    self.psi_red,
                )
            )

        if returns_ref is not None and returns_current is not None:
            p = psi(returns_ref, returns_current)
            results.append(
                DriftResult(
                    "return_psi",
                    p,
                    0.0,
                    _level(p, 0, self.psi_yellow, self.psi_red),
                    self.psi_yellow,
                    self.psi_red,
                )
            )

        if vol_ref is not None and vol_current is not None:
            d = self._rel(vol_current, vol_ref)
            results.append(
                DriftResult(
                    "volatility",
                    vol_current,
                    vol_ref,
                    _level(d, 0, self.rel_yellow, self.rel_red),
                    self.rel_yellow,
                    self.rel_red,
                )
            )

        if corr_ref is not None and corr_current is not None:
            d = self._rel(corr_current, corr_ref)
            results.append(
                DriftResult(
                    "correlation",
                    corr_current,
                    corr_ref,
                    _level(d, 0, self.rel_yellow, self.rel_red),
                    self.rel_yellow,
                    self.rel_red,
                )
            )

        if spread_ref is not None and spread_current is not None:
            d = self._rel(spread_current, spread_ref)
            results.append(
                DriftResult(
                    "spread",
                    spread_current,
                    spread_ref,
                    _level(d, 0, self.rel_yellow, self.rel_red),
                    self.rel_yellow,
                    self.rel_red,
                )
            )

        if fill_rate_ref is not None and fill_rate_current is not None:
            d = self._rel(fill_rate_current, fill_rate_ref)
            results.append(
                DriftResult(
                    "fill_rate",
                    fill_rate_current,
                    fill_rate_ref,
                    _level(d, 0, self.rel_yellow, self.rel_red),
                    self.rel_yellow,
                    self.rel_red,
                )
            )

        if calibration_ref is not None and calibration_current is not None:
            d = self._rel(calibration_current, calibration_ref)
            results.append(
                DriftResult(
                    "calibration",
                    calibration_current,
                    calibration_ref,
                    _level(d, 0, self.rel_yellow, self.rel_red),
                    self.rel_yellow,
                    self.rel_red,
                )
            )

        return results

    def health_state(self, results: list[DriftResult]) -> StrategyHealthState:
        """Aggregate detector levels into a fail-closed health state."""
        levels = {r.level for r in results}
        if DriftLevel.RED in levels:
            return StrategyHealthState.SUSPENDED
        if any(
            r.detector.startswith(("feature_", "return_"))
            and r.level == DriftLevel.YELLOW
            for r in results
        ):
            return (
                StrategyHealthState.OOD
                if DriftLevel.YELLOW in levels
                else StrategyHealthState.HEALTHY
            )
        if DriftLevel.YELLOW in levels:
            return StrategyHealthState.DEGRADED
        return StrategyHealthState.HEALTHY
