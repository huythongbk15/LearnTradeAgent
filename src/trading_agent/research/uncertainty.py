"""Uncertainty gate + abstention reasons (Sections 8-9).

The uncertainty gate is fail-safe: when uncertainty is not LOW, the only
allowed actions REDUCE risk or ABSTAIN — never increase exposure.

Abstention is explicit and auditable via ``AbstentionReason`` codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class UncertaintyState(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AbstentionReason(Enum):
    """The 9 canonical abstention codes (Section 9)."""

    INSUFFICIENT_DATA = "insufficient_data"
    OOD_INPUT = "ood_input"
    HIGH_UNCERTAINTY = "high_uncertainty"
    CALIBRATION_DEGRADED = "calibration_degraded"
    REGIME_UNKNOWN = "regime_unknown"
    LATENCY_VIOLATION = "latency_violation"
    DATA_STALE = "data_stale"
    RISK_LIMIT = "risk_limit"
    MANUAL_OVERRIDE = "manual_override"


ABSTENTION_CODES: set[str] = {r.value for r in AbstentionReason}


@dataclass(frozen=True)
class UncertaintySignal:
    """Model's own confidence statement (must be produced by the model)."""

    expected_return: float
    prediction_interval_lower: float
    prediction_interval_upper: float
    calibration_score: float      # 0..1, 1 = perfectly calibrated
    ood_score: float              # 0..1, higher = more out-of-distribution
    horizon: str = "1h"

    @property
    def uncertainty_state(self) -> UncertaintyState:
        """Derive LOW/MEDIUM/HIGH from the calibrated components."""
        width = self.prediction_interval_upper - self.prediction_interval_lower
        if self.ood_score >= 0.7 or self.calibration_score < 0.5:
            return UncertaintyState.HIGH
        if self.ood_score >= 0.4 or self.calibration_score < 0.7 or width <= 0:
            return UncertaintyState.MEDIUM
        return UncertaintyState.LOW

    @property
    def can_increase_exposure(self) -> bool:
        return self.uncertainty_state == UncertaintyState.LOW

    def to_dict(self) -> dict:
        return {
            "expected_return": self.expected_return,
            "prediction_interval_lower": self.prediction_interval_lower,
            "prediction_interval_upper": self.prediction_interval_upper,
            "calibration_score": self.calibration_score,
            "ood_score": self.ood_score,
            "horizon": self.horizon,
            "uncertainty_state": self.uncertainty_state.value,
        }


def uncertainty_gate(signal: UncertaintySignal) -> UncertaintyState:
    """Fail-safe gate: returns the state that restricts actions."""
    return signal.uncertainty_state


@dataclass(frozen=True)
class Abstention:
    """An explicit, auditable abstention decision."""

    reason: AbstentionReason
    symbol: str
    strategy: str
    uncertainty_state: UncertaintyState | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "reason": self.reason.value,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "uncertainty_state": self.uncertainty_state.value if self.uncertainty_state else None,
            "detail": self.detail,
        }


def should_abstain(
    *,
    symbol: str,
    strategy: str,
    reason: AbstentionReason,
    uncertainty: UncertaintySignal | None = None,
    detail: str = "",
) -> Abstention:
    """Factory for an abstention record (always returned, never None)."""
    if reason not in AbstentionReason:
        raise ValueError(f"unknown abstention reason: {reason!r}")
    return Abstention(
        reason=reason,
        symbol=symbol,
        strategy=strategy,
        uncertainty_state=uncertainty.uncertainty_state if uncertainty else None,
        detail=detail,
    )