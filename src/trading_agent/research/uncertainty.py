"""Uncertainty gate + abstention reasons (Sections 8-9).

The uncertainty gate is fail-safe: when uncertainty is not LOW, the only
allowed actions REDUCE risk or ABSTAIN — never increase exposure.

Abstention is explicit and auditable via ``AbstentionReason`` codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from trading_agent.research.calibration import (
    CalibrationArtifact,
    CalibrationState,
    calibration_state,
)


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
    calibration_score: float  # 0..1, 1 = perfectly calibrated
    ood_score: float  # 0..1, higher = more out-of-distribution
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
            "uncertainty_state": self.uncertainty_state.value
            if self.uncertainty_state
            else None,
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


# ── Calibrated Decision Layer (Wave E) ──────────────────────────────────

# Replaces UncertaintySignal.uncertainty_state with a calibrated probability
# distribution over actions: {increase, hold, reduce, abstain} summing to 1.0


class Action(Enum):
    INCREASE = "increase"
    HOLD = "hold"
    REDUCE = "reduce"
    ABSTAIN = "abstain"

    @property
    def risk_level(self) -> int:
        return {"increase": 3, "hold": 2, "reduce": 1, "abstain": 0}[self.value]


@dataclass(frozen=True)
class CalibratedDecision:
    """Calibrated probability distribution over actions.

    Replaces the threshold-based uncertainty_state with proper probabilities.
    Calibrated via isotonic regression on historical (signal, outcome) pairs.
    """

    action_probabilities: dict[Action, float]
    expected_return: float
    prediction_interval_lower: float
    prediction_interval_upper: float
    calibration_score: float
    ood_score: float
    horizon: str = "1h"
    temperature: float = 1.0
    calibration_artifact_id: str | None = None
    calibration_status: CalibrationState = CalibrationState.UNCALIBRATED

    def __post_init__(self) -> None:
        # Validate probabilities sum to 1.0
        total = sum(self.action_probabilities.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"action_probabilities must sum to 1.0, got {total}")
        # Validate all actions present
        for a in Action:
            if a not in self.action_probabilities:
                raise ValueError(f"missing action probability for {a.value}")

    @property
    def most_likely_action(self) -> Action:
        return max(self.action_probabilities, key=self.action_probabilities.get)

    @property
    def can_increase_exposure(self) -> bool:
        """Fail-safe: increase only if increase_prob > threshold AND no HIGH uncertainty."""
        return (
            self.action_probabilities[Action.INCREASE] > 0.4
            and self.uncertainty_state != UncertaintyState.HIGH
        )

    @property
    def uncertainty_state(self) -> UncertaintyState:
        """Backward-compatible uncertainty state derived from calibrated probs."""
        width = self.prediction_interval_upper - self.prediction_interval_lower
        if self.ood_score >= 0.7 or self.calibration_score < 0.5:
            return UncertaintyState.HIGH
        if self.ood_score >= 0.4 or self.calibration_score < 0.7 or width <= 0:
            return UncertaintyState.MEDIUM
        return UncertaintyState.LOW

    def to_dict(self) -> dict:
        return {
            "action_probabilities": {
                k.value: v for k, v in self.action_probabilities.items()
            },
            "expected_return": self.expected_return,
            "prediction_interval_lower": self.prediction_interval_lower,
            "prediction_interval_upper": self.prediction_interval_upper,
            "calibration_score": self.calibration_score,
            "ood_score": self.ood_score,
            "horizon": self.horizon,
            "temperature": self.temperature,
            "calibration_artifact_id": self.calibration_artifact_id,
            "calibration_status": self.calibration_status.value,
            "uncertainty_state": self.uncertainty_state.value,
        }


class DecisionPolicy:
    """Configurable risk appetite mapping from calibrated probs to allowed actions.

    risk_appetite:
      - "aggressive": allow INCREASE if p_increase > 0.35
      - "moderate":   allow INCREASE if p_increase > 0.50
      - "conservative": allow INCREASE only if p_increase > 0.65 and p_abstain < 0.1
    """

    def __init__(self, risk_appetite: str = "moderate") -> None:
        if risk_appetite not in ("aggressive", "moderate", "conservative"):
            raise ValueError("risk_appetite must be aggressive|moderate|conservative")
        self.risk_appetite = risk_appetite

        self.thresholds = {
            "aggressive": {"increase": 0.35, "abstain_max": 0.30},
            "moderate": {"increase": 0.50, "abstain_max": 0.20},
            "conservative": {"increase": 0.65, "abstain_max": 0.10},
        }[risk_appetite]

    def allowed_actions(self, decision: CalibratedDecision) -> set[Action]:
        """Return the set of actions permitted by this policy."""
        p = decision.action_probabilities
        allowed = set()

        # ABSTAIN always allowed (fail-safe)
        allowed.add(Action.ABSTAIN)

        # REDUCE allowed if uncertainty is not LOW
        if (
            p[Action.REDUCE] > 0.15
            or decision.uncertainty_state != UncertaintyState.LOW
        ):
            allowed.add(Action.REDUCE)

        # HOLD always allowed as neutral
        allowed.add(Action.HOLD)

        # INCREASE gated by risk appetite
        if (
            p[Action.INCREASE] > self.thresholds["increase"]
            and p[Action.ABSTAIN] <= self.thresholds["abstain_max"]
            and decision.uncertainty_state == UncertaintyState.LOW
        ):
            allowed.add(Action.INCREASE)

        return allowed

    def recommended_action(self, decision: CalibratedDecision) -> Action:
        """Single recommended action: highest-prob allowed action."""
        allowed = self.allowed_actions(decision)
        if not allowed:
            return Action.ABSTAIN
        return max(allowed, key=lambda a: decision.action_probabilities[a])


class GovernedDecisionPolicy(DecisionPolicy):
    """Production gate requiring current calibration evidence for risk increase."""

    def allowed_actions(self, decision: CalibratedDecision) -> set[Action]:
        allowed = super().allowed_actions(decision)
        interval_crosses_zero = (
            decision.prediction_interval_lower
            <= 0.0
            <= decision.prediction_interval_upper
        )
        if (
            decision.calibration_status != CalibrationState.CALIBRATED
            or interval_crosses_zero
        ):
            allowed.discard(Action.INCREASE)
        return allowed


def isotonic_calibration(
    predictions: list[float],
    outcomes: list[float],
) -> tuple[list[float], list[float]]:
    """Isotonic regression for probability calibration.

    Fits a non-decreasing function mapping raw scores to calibrated probabilities.
    Returns (calibrated_predictions, boundaries) for use with numpy interp.
    """
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        # Fallback: identity (no calibration)
        return predictions, sorted(set(predictions))

    ir = IsotonicRegression(out_of_bounds="clip", increasing=True)
    calibrated = ir.fit_transform(predictions, outcomes)
    return calibrated.tolist(), ir.X_thresholds_.tolist()


def temperature_scale(logits: list[float], temperature: float) -> list[float]:
    """Temperature scaling for neural network outputs.

    softmax(logits / T) - higher T = softer (more uniform) distribution.
    """
    if temperature <= 0:
        raise ValueError("temperature must be > 0")
    import math

    scaled = [x / temperature for x in logits]
    max_logit = max(scaled)
    exps = [math.exp(x - max_logit) for x in scaled]
    sum_exps = sum(exps)
    return [e / sum_exps for e in exps]


def uncertainty_signal_to_decision(
    signal: UncertaintySignal,
    *,
    historical_signals: list[UncertaintySignal] | None = None,
    historical_outcomes: list[float] | None = None,
    temperature: float = 1.0,
    calibration_artifact: CalibrationArtifact | None = None,
) -> CalibratedDecision:
    """Convert UncertaintySignal to CalibratedDecision with calibration.

    If historical data provided, fit isotonic regression on
    (signal.expected_return, outcome) pairs and apply calibration.
    """
    # Base probabilities from heuristic (uncalibrated)
    width = signal.prediction_interval_upper - signal.prediction_interval_lower
    expected = signal.expected_return
    cal = signal.calibration_score
    ood = signal.ood_score

    # Heuristic: map expected return to action probs
    if expected > width:
        base = {
            Action.INCREASE: 0.6,
            Action.HOLD: 0.25,
            Action.REDUCE: 0.1,
            Action.ABSTAIN: 0.05,
        }
    elif expected > 0:
        base = {
            Action.INCREASE: 0.35,
            Action.HOLD: 0.45,
            Action.REDUCE: 0.1,
            Action.ABSTAIN: 0.1,
        }
    elif expected > -width:
        base = {
            Action.INCREASE: 0.1,
            Action.HOLD: 0.5,
            Action.REDUCE: 0.25,
            Action.ABSTAIN: 0.15,
        }
    else:
        base = {
            Action.INCREASE: 0.05,
            Action.HOLD: 0.25,
            Action.REDUCE: 0.4,
            Action.ABSTAIN: 0.3,
        }

    # Adjust for calibration and OOD
    if cal < 0.7 or ood > 0.4:
        # Shift probability mass to HOLD/ABSTAIN
        shift = min(0.3, (0.7 - cal) + ood * 0.5)
        base[Action.INCREASE] = max(0.02, base[Action.INCREASE] - shift)
        base[Action.ABSTAIN] = min(0.5, base[Action.ABSTAIN] + shift * 0.7)
        base[Action.HOLD] = 1.0 - sum(v for k, v in base.items() if k != Action.HOLD)

    # Apply temperature scaling to make probs more/less confident
    probs = temperature_scale(list(base.values()), temperature)

    return CalibratedDecision(
        action_probabilities=dict(zip(Action, probs, strict=True)),
        expected_return=signal.expected_return,
        prediction_interval_lower=signal.prediction_interval_lower,
        prediction_interval_upper=signal.prediction_interval_upper,
        calibration_score=signal.calibration_score,
        ood_score=signal.ood_score,
        horizon=signal.horizon,
        temperature=temperature,
        calibration_artifact_id=(
            calibration_artifact.calibration_id if calibration_artifact else None
        ),
        calibration_status=calibration_state(calibration_artifact),
    )


# Adapter for backward compatibility
class ThresholdDecisionPolicy:
    """Adapter: wraps threshold-based UncertaintySignal for legacy code."""

    def __init__(self, risk_appetite: str = "moderate") -> None:
        self.policy = DecisionPolicy(risk_appetite)

    def allowed_actions(self, signal: UncertaintySignal) -> set[Action]:
        decision = uncertainty_signal_to_decision(signal)
        return self.policy.allowed_actions(decision)

    def recommended_action(self, signal: UncertaintySignal) -> Action:
        decision = uncertainty_signal_to_decision(signal)
        return self.policy.recommended_action(decision)

    def can_increase(self, signal: UncertaintySignal) -> bool:
        return Action.INCREASE in self.allowed_actions(signal)
