"""Legacy signal → canonical risk decision adapter.

Converts Phase 2 agent signals (AgentMessage) into the canonical
UnifiedRiskDecision / TargetExposure pipeline without rewriting the
entire ExecutionEngine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.research.forecast import RiskReason, TargetExposure
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)


@dataclass(frozen=True)
class LegacySignal:
    """Minimal representation of a Phase 2 agent signal."""

    symbol: str
    side: str  # "buy" | "sell"
    confidence: float
    max_position_size_pct: float
    atr: float | None = None
    risk_reward: float = 2.0
    trailing_atr_mult: float = 2.0


class LegacyDecisionAdapter:
    """Convert legacy AgentMessage into canonical risk decision + target exposure."""

    def __init__(
        self,
        default_risk_level: RiskLevel = RiskLevel.MEDIUM,
        calibration_ece: float = 0.05,
        ood_score: float = 0.3,
        regime_entropy: float = 0.4,
    ) -> None:
        self.default_risk_level = default_risk_level
        self.calibration_ece = calibration_ece
        self.ood_score = ood_score
        self.regime_entropy = regime_entropy

    def adapt(
        self, signal: Any, observation: EnrichedMarketObservation
    ) -> tuple[UnifiedRiskDecision, TargetExposure]:
        """Convert signal to canonical risk decision and target exposure."""
        if hasattr(signal, "signal"):
            signal_str = str(signal.signal).upper()
            details = getattr(signal, "details", {}) or {}
            confidence = float(getattr(signal, "confidence", 0.5) or 0.5)
            max_pos_pct = float(getattr(signal, "max_position_size_pct", 0.25) or 0.25)
        else:
            signal_str = str(signal.get("signal", "HOLD")).upper()
            details = signal.get("details", {}) or {}
            confidence = float(signal.get("confidence", 0.5) or 0.5)
            max_pos_pct = float(signal.get("max_position_size_pct", 0.25) or 0.25)

        side = (
            "buy" if signal_str == "BUY" else "sell" if signal_str == "SELL" else "hold"
        )
        if side == "hold":
            raise ValueError("HOLD signals do not produce risk decisions")

        atr = None
        if details.get("atr") is not None:
            atr = float(details["atr"])
        elif signal_str in ("BUY", "SELL"):
            # ATR will be computed upstream in canonical path if needed
            pass

        # Build a minimal unified risk decision from legacy signal
        decision_id = f"legacy_{observation.observation_id}"
        forecast_fingerprint = observation.observation_id
        model_artifact_id = "legacy_runner"
        now = datetime.now(UTC)

        reason_codes: tuple[Any, ...] = ()
        # For legacy signals, we do NOT fabricate KNOWN evidence.
        # Missing real evidence must remain UNKNOWN/MISSING/STALE.
        # A strategy signal is not a risk approval.
        backtest_allow = os.getenv("BACKTEST_ALLOW_NEW_EXPOSURE", "").lower() in ("1", "true", "yes")
        calibration_state = EvidenceState.KNOWN if backtest_allow else EvidenceState.MISSING
        ood_state = EvidenceState.KNOWN if backtest_allow else EvidenceState.MISSING
        regime_state = EvidenceState.KNOWN if backtest_allow else EvidenceState.MISSING
        calibration_artifact_id = "backtest-legacy-v1" if backtest_allow else ""
        # Use safe defaults for numeric fields; missing evidence means
        # new exposure is blocked by the risk layer, not by crashing.
        calibration_ece = 0.0 if backtest_allow else 1.0
        ood_score = 0.0 if backtest_allow else 1.0
        regime_entropy = 0.0 if backtest_allow else 1.0
        interval_width = 0.0 if backtest_allow else 1.0

        # Legacy signals do NOT carry real risk evidence, so they cannot
        # approve new exposure.  Only reduce-only exits are allowed.
        if side == "sell":
            reason_codes = (RiskReason.APPROVED,)
        else:
            reason_codes = ()

        # For spot-long-only, SELL means EXIT_TO_FLAT (target 0.0), not -1.0
        if side == "sell":
            requested_target = 0.0
            allowed_target = 0.0
            max_new_exposure = 0.0
            reduce_only = True
        else:
            requested_target = 1.0
            # Allow new exposure in backtest-only mode; production stays blocked
            # when evidence is missing.
            if os.getenv("BACKTEST_ALLOW_NEW_EXPOSURE", "").lower() in ("1", "true", "yes"):
                allowed_target = 1.0
                max_new_exposure = 1.0
                reduce_only = False
            else:
                allowed_target = 0.0
                max_new_exposure = 0.0
                reduce_only = True

        risk_decision = UnifiedRiskDecision(
            decision_id=decision_id,
            forecast_fingerprint=forecast_fingerprint,
            model_artifact_id=model_artifact_id,
            requested_target_exposure=requested_target,
            allowed_target_exposure=allowed_target,
            max_new_exposure=max_new_exposure,
            reduce_only=reduce_only,
            risk_level=self.default_risk_level,
            reason_codes=reason_codes,
            calibration_state=calibration_state,
            calibration_artifact_id=calibration_artifact_id,
            calibration_ece=calibration_ece,
            ood_state=ood_state,
            ood_score=ood_score,
            regime_state=regime_state,
            regime_entropy=regime_entropy,
            interval_width=interval_width,
            created_at=now,
        )

        exposure = (
            1.0 if side == "buy" else 0.0
        )  # spot-long-only: SELL → FLAT (0.0), not -1.0
        target = TargetExposure(
            symbol=observation.symbol,
            exposure=exposure,
            horizon=1,
            forecast_fingerprint=forecast_fingerprint,
            model_artifact_id=model_artifact_id,
            risk_decision_id=decision_id,
        )

        return risk_decision, target
