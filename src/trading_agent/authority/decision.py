"""
DecisionAuthority — Converts agent signals / research artifacts into UnifiedRiskDecision + TargetExposure.

This is the FIRST authority in the chain. It is fail-closed:
- No signal → HOLD (no exposure)
- Invalid signal → HOLD
- Missing required fields → HOLD
- Any error → HOLD with error metadata

The authority_chain field on UnifiedRiskDecision is populated here and propagated downstream.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.authority.causation import CausationChain, generate_causation_id, new_chain
from trading_agent.authority.config import AuthorityConfig, get_authority_config
from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
# Define TargetExposure locally for the authority chain
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TargetExposure:
    """Target exposure for the authority chain."""

    target_exposure_pct: float  # Target exposure as fraction of equity [0, 1]
    max_new_exposure_pct: float  # Maximum new exposure this step [0, 1]
    reduce_only: bool = False  # True if only reducing exposure allowed
    confidence: float = 0.5  # Confidence in this target [0, 1]
    authority_chain: tuple[Any, ...] = field(default_factory=tuple)  # Causation links

    def __post_init__(self) -> None:
        for name in ("target_exposure_pct", "max_new_exposure_pct", "confidence"):
            val = float(getattr(self, name))
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {val}")
from trading_agent.research.artifact import StrategyArtifact

logger = logging.getLogger(__name__)


# ── Input types ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Input to DecisionAuthority — one of these must be provided."""

    # Option 1: Agent ensemble signal (legacy path)
    agent_message: AgentMessage | None = None

    # Option 2: Promoted strategy artifact (research path)
    strategy_artifact: StrategyArtifact | None = None

    # Option 3: Direct risk decision (bypass, for testing)
    risk_decision: UnifiedRiskDecision | None = None

    # Context (required for all paths)
    symbol: str = ""
    timeframe: str = "1h"
    current_price: float = 0.0
    current_exposure: float = 0.0
    equity: float = 0.0
    available_cash: float = 0.0
    portfolio_value: float = 0.0

    # Market observation (for regime, volatility context)
    observation_id: str | None = None
    regime: str | None = None
    volatility_pct: float | None = None

    def __post_init__(self) -> None:
        provided = sum(
            1
            for f in (self.agent_message, self.strategy_artifact, self.risk_decision)
            if f is not None
        )
        if provided != 1:
            raise ValueError("Exactly one of agent_message, strategy_artifact, risk_decision must be provided")
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.current_price <= 0:
            raise ValueError("current_price must be positive")
        if self.equity <= 0:
            raise ValueError("equity must be positive")


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    """Output from DecisionAuthority."""

    risk_decision: UnifiedRiskDecision
    target_exposure: TargetExposure
    causation_chain: CausationChain
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ── DecisionAuthority ───────────────────────────────────────────────────


class DecisionAuthority:
    """
    Authority 1: Signal → UnifiedRiskDecision + TargetExposure.

    Responsibilities:
    1. Validate input (agent message, artifact, or direct decision)
    2. Apply risk profile scaling (from AuthorityConfig)
    3. Enforce exposure caps (from AuthorityConfig)
    4. Produce UnifiedRiskDecision with authority_chain
    5. Produce TargetExposure with authority_chain reference
    6. Emit causation chain for audit
    """

    def __init__(self, config: AuthorityConfig | None = None):
        self.config = config or get_authority_config()
        self._chain: CausationChain | None = None

    def decide(self, input_: DecisionInput) -> DecisionOutput:
        """
        Main entry point — fail-closed, always returns a valid output.

        On any error, returns HOLD with zero exposure and error metadata.
        """
        # Start causation chain with root inputs
        root_inputs = {
            "authority": "DecisionAuthority",
            "input_type": self._input_type(input_),
            "symbol": input_.symbol,
            "timeframe": input_.timeframe,
            "current_price": input_.current_price,
            "current_exposure": input_.current_exposure,
            "equity": input_.equity,
        }
        chain = new_chain(root_inputs)

        try:
            # Route to appropriate handler
            if input_.agent_message is not None:
                risk_decision, target = self._from_agent_message(input_, chain)
            elif input_.strategy_artifact is not None:
                risk_decision, target = self._from_strategy_artifact(input_, chain)
            else:  # input_.risk_decision is not None
                risk_decision, target = self._from_direct_decision(input_, chain)

            # Final validation & enforcement
            risk_decision, target = self._enforce_limits(risk_decision, target, input_, chain)

            return DecisionOutput(
                risk_decision=risk_decision,
                target_exposure=target,
                causation_chain=chain,
            )

        except Exception as e:
            logger.error(f"DecisionAuthority failed: {e}", exc_info=True)
            return self._fail_closed(input_, chain, str(e))

    def _input_type(self, input_: DecisionInput) -> str:
        if input_.agent_message:
            return "agent_message"
        if input_.strategy_artifact:
            return "strategy_artifact"
        return "risk_decision"

    # ── Handler: AgentMessage path (legacy multi-agent) ────────────────

    def _from_agent_message(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure]:
        msg = input_.agent_message

        # Extract risk decision from agent message details
        details = msg.details or {}
        risk_level = details.get("risk_level", "MEDIUM")
        target_exposure_pct = details.get("target_exposure_pct", 0.0)
        max_new_exposure_pct = details.get("max_new_exposure_pct", 0.0)
        reduce_only = details.get("reduce_only", False)

        # Build UnifiedRiskDecision from agent ensemble output
        risk_decision = UnifiedRiskDecision(
            decision_id=f"agent_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}",
            forecast_fingerprint="",
            model_artifact_id="",
            requested_target_exposure=target_exposure_pct,
            allowed_target_exposure=min(target_exposure_pct, self.config.exposure.max_single_strategy_exposure),
            max_new_exposure=min(max_new_exposure_pct, self.config.exposure.max_single_strategy_exposure),
            reduce_only=reduce_only,
            risk_level=RiskLevel(risk_level.upper()),
            reason_codes=(),
            calibration_state=EvidenceState.MISSING,
            calibration_artifact_id=None,
            calibration_ece=1.0,
            ood_state=EvidenceState.UNKNOWN,
            ood_score=1.0,
            regime_state=EvidenceState.UNKNOWN,
            regime_entropy=1.0,
            interval_width=1.0,
            created_at=datetime.now(UTC),
            metadata={},
            warnings=(),
        )

        # Determine target exposure
        target = self._compute_target_exposure(
            signal=msg.signal,
            risk_decision=risk_decision,
            current_exposure=input_.current_exposure,
            equity=input_.equity,
            available_cash=input_.available_cash,
        )

        # Append to causation chain
        chain = chain.append(
            authority="DecisionAuthority.agent_ensemble",
            inputs={
                "signal": msg.signal,
                "confidence": msg.confidence,
                "risk_level": risk_level,
                "details": details,
            },
            outputs={
                "allowed_target_exposure": risk_decision.allowed_target_exposure,
                "max_new_exposure": risk_decision.max_new_exposure,
                "reduce_only": risk_decision.reduce_only,
            },
        )

        return risk_decision, target

    # ── Handler: StrategyArtifact path (research-promoted) ──────────────

    def _from_strategy_artifact(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure]:
        artifact = input_.strategy_artifact

        # Load strategy parameters from artifact metadata
        params = artifact.metadata.get("parameters", {})
        target_exposure_pct = params.get("target_exposure_pct", 0.10)
        max_new_exposure_pct = params.get("max_new_exposure_pct", 0.10)
        reduce_only = params.get("reduce_only", False)
        confidence = params.get("confidence", 0.5)

        # Apply risk profile scaling
        scaled_target = self._apply_risk_scaling(target_exposure_pct, input_)
        scaled_max_new = self._apply_risk_scaling(max_new_exposure_pct, input_)

        risk_decision = UnifiedRiskDecision(
            decision_id=f"artifact_{artifact.artifact_id[:16]}",
            forecast_fingerprint=artifact.code_sha[:32],
            model_artifact_id=artifact.artifact_id,
            requested_target_exposure=target_exposure_pct,
            allowed_target_exposure=min(scaled_target, self.config.exposure.max_single_strategy_exposure),
            max_new_exposure=min(scaled_max_new, self.config.exposure.max_single_strategy_exposure),
            reduce_only=reduce_only,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=(),
            calibration_state=EvidenceState(params.get("calibration_state", "UNKNOWN")),
            calibration_artifact_id=params.get("calibration_artifact_id"),
            calibration_ece=params.get("calibration_ece", 1.0),
            ood_state=EvidenceState(params.get("ood_state", "UNKNOWN")),
            ood_score=params.get("ood_score", 1.0),
            regime_state=EvidenceState(params.get("regime_state", "UNKNOWN")),
            regime_entropy=params.get("regime_entropy", 1.0),
            interval_width=params.get("interval_width", 1.0),
            created_at=datetime.now(UTC),
            metadata={"artifact_id": artifact.artifact_id, "strategy_name": artifact.strategy_name},
            warnings=(),
        )

        target = self._compute_target_exposure(
            signal="BUY" if scaled_target > 0 else "HOLD",
            risk_decision=risk_decision,
            current_exposure=input_.current_exposure,
            equity=input_.equity,
            available_cash=input_.available_cash,
        )

        chain = chain.append(
            authority="DecisionAuthority.promoted_strategy",
            inputs={
                "artifact_id": artifact.artifact_id,
                "strategy_name": artifact.strategy_name,
                "code_sha": artifact.code_sha[:16],
                "parameter_hash": artifact.parameter_hash[:16],
                "params": params,
            },
            outputs={
                "allowed_target_exposure": risk_decision.allowed_target_exposure,
                "max_new_exposure": risk_decision.max_new_exposure,
                "reduce_only": risk_decision.reduce_only,
            },
        )

        return risk_decision, target

    # ── Handler: Direct UnifiedRiskDecision (testing/bypass) ────────────

    def _from_direct_decision(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure]:
        risk_decision = input_.risk_decision

        # Apply config caps
        capped_target = min(risk_decision.allowed_target_exposure, self.config.exposure.max_single_strategy_exposure)
        capped_max_new = min(risk_decision.max_new_exposure, self.config.exposure.max_single_strategy_exposure)

        risk_decision = UnifiedRiskDecision(
            decision_id=risk_decision.decision_id,
            forecast_fingerprint=risk_decision.forecast_fingerprint,
            model_artifact_id=risk_decision.model_artifact_id,
            requested_target_exposure=risk_decision.requested_target_exposure,
            allowed_target_exposure=capped_target,
            max_new_exposure=capped_max_new,
            reduce_only=risk_decision.reduce_only,
            risk_level=risk_decision.risk_level,
            reason_codes=risk_decision.reason_codes,
            calibration_state=risk_decision.calibration_state,
            calibration_artifact_id=risk_decision.calibration_artifact_id,
            calibration_ece=risk_decision.calibration_ece,
            ood_state=risk_decision.ood_state,
            ood_score=risk_decision.ood_score,
            regime_state=risk_decision.regime_state,
            regime_entropy=risk_decision.regime_entropy,
            interval_width=risk_decision.interval_width,
            created_at=risk_decision.created_at,
            metadata=risk_decision.metadata,
            warnings=risk_decision.warnings,
        )

        target = self._compute_target_exposure(
            signal="BUY" if capped_target > input_.current_exposure else "SELL" if capped_target < input_.current_exposure else "HOLD",
            risk_decision=risk_decision,
            current_exposure=input_.current_exposure,
            equity=input_.equity,
            available_cash=input_.available_cash,
        )

        chain = chain.append(
            authority="DecisionAuthority.direct",
            inputs={
                "allowed_target_exposure": risk_decision.allowed_target_exposure,
                "max_new_exposure": risk_decision.max_new_exposure,
                "reduce_only": risk_decision.reduce_only,
            },
            outputs={
                "target_exposure": target.target_exposure_pct,
                "max_new_exposure": target.max_new_exposure_pct,
            },
        )

        return risk_decision, target

    # ── Core logic ──────────────────────────────────────────────────────

    def _signal_to_score(self, signal: str, confidence: float) -> float:
        """Convert agent signal to calibrated score [-1, 1]."""
        signal_map = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}
        base = signal_map.get(signal.upper(), 0.0)
        return base * confidence

    def _apply_risk_scaling(self, base_exposure: float, input_: DecisionInput) -> float:
        """Apply risk profile and volatility scaling."""
        # Risk profile multiplier
        profile_multipliers = {
            "conservative": 0.5,
            "moderate": 0.75,
            "aggressive": 1.0,
        }
        multiplier = profile_multipliers.get(self.config.risk_profile.value, 0.75)

        # Volatility scaling (if available)
        vol_scale = 1.0
        if input_.volatility_pct is not None and input_.volatility_pct > 0:
            # Higher vol → smaller position
            vol_scale = min(1.0, 2.0 / (input_.volatility_pct / 100.0))

        return base_exposure * multiplier * vol_scale

    def _compute_target_exposure(
        self,
        signal: str,
        risk_decision: UnifiedRiskDecision,
        current_exposure: float,
        equity: float,
        available_cash: float,
    ) -> TargetExposure:
        """Compute TargetExposure from risk decision and current state."""

        # If reduce_only, target is 0 (exit only)
        if risk_decision.reduce_only:
            target_pct = 0.0
            max_new_pct = 0.0
        else:
            target_pct = risk_decision.allowed_target_exposure
            max_new_pct = risk_decision.max_new_exposure

        # Ensure we don't exceed available cash (for long-only spot)
        max_affordable = available_cash / equity if equity > 0 else 0.0
        target_pct = min(target_pct, max_affordable)
        max_new_pct = min(max_new_pct, max_affordable)

        return TargetExposure(
            target_exposure_pct=target_pct,
            max_new_exposure_pct=max_new_pct,
            reduce_only=risk_decision.reduce_only,
            confidence=0.5,  # Not directly available in UnifiedRiskDecision
            authority_chain=(),  # Filled by caller
        )

    def _enforce_limits(
        self,
        risk_decision: UnifiedRiskDecision,
        target: TargetExposure,
        input_: DecisionInput,
        chain: CausationChain,
    ) -> tuple[UnifiedRiskDecision, TargetExposure]:
        """Final enforcement of all limits."""

        warnings = []

        # Portfolio-level exposure cap
        max_portfolio = self.config.exposure.max_portfolio_exposure
        # Note: In single-pair mode, this equals single-strategy cap
        # Multi-pair enforcement happens in PortfolioAllocator (Milestone C)

        # Symbol-level cap
        target_exposure = min(target.target_exposure_pct, self.config.exposure.max_single_symbol_exposure)
        max_new_exposure = min(target.max_new_exposure_pct, self.config.exposure.max_single_symbol_exposure)

        # Notional caps
        target_notional = target_exposure * input_.equity
        if target_notional > self.config.exposure.max_trade_notional:
            target_exposure = self.config.exposure.max_trade_notional / input_.equity
            warnings.append(f"target_notional capped at max_trade_notional")
        if target_notional < self.config.exposure.min_trade_notional and target_exposure > 0:
            target_exposure = 0.0
            max_new_exposure = 0.0
            warnings.append(f"target_notional below min_trade_notional → HOLD")

        # Rebuild with capped values
        risk_decision = UnifiedRiskDecision(
            decision_id=risk_decision.decision_id,
            forecast_fingerprint=risk_decision.forecast_fingerprint,
            model_artifact_id=risk_decision.model_artifact_id,
            requested_target_exposure=risk_decision.requested_target_exposure,
            allowed_target_exposure=target_exposure,
            max_new_exposure=max_new_exposure,
            reduce_only=target.reduce_only,
            risk_level=risk_decision.risk_level,
            reason_codes=risk_decision.reason_codes,
            calibration_state=risk_decision.calibration_state,
            calibration_artifact_id=risk_decision.calibration_artifact_id,
            calibration_ece=risk_decision.calibration_ece,
            ood_state=risk_decision.ood_state,
            ood_score=risk_decision.ood_score,
            regime_state=risk_decision.regime_state,
            regime_entropy=risk_decision.regime_entropy,
            interval_width=risk_decision.interval_width,
            created_at=risk_decision.created_at,
            metadata=risk_decision.metadata,
            warnings=risk_decision.warnings + tuple(warnings),
        )

        target = TargetExposure(
            target_exposure_pct=target_exposure,
            max_new_exposure_pct=max_new_exposure,
            reduce_only=target.reduce_only,
            confidence=target.confidence,
            authority_chain=chain.links,
        )

        return risk_decision, target

    # ── Fail-closed fallback ────────────────────────────────────────────

    def _fail_closed(
        self, input_: DecisionInput, chain: CausationChain, error: str
    ) -> DecisionOutput:
        """Return HOLD with zero exposure on any failure."""

        risk_decision = UnifiedRiskDecision(
            decision_id=f"fail_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            forecast_fingerprint="",
            model_artifact_id="",
            requested_target_exposure=0.0,
            allowed_target_exposure=0.0,
            max_new_exposure=0.0,
            reduce_only=True,
            risk_level=RiskLevel.EXTREME,
            reason_codes=(),
            calibration_state=EvidenceState.MISSING,
            calibration_artifact_id=None,
            calibration_ece=1.0,
            ood_state=EvidenceState.UNKNOWN,
            ood_score=1.0,
            regime_state=EvidenceState.STALE,
            regime_entropy=1.0,
            interval_width=1.0,
            created_at=datetime.now(UTC),
            metadata={"error": error},
            warnings=(error,),
        )

        target = TargetExposure(
            target_exposure_pct=0.0,
            max_new_exposure_pct=0.0,
            reduce_only=True,
            confidence=0.0,
            authority_chain=chain.links,
        )

        chain = chain.append(
            authority="DecisionAuthority.fail_closed",
            inputs={"error": error, "input_type": self._input_type(input_)},
            outputs={"target_exposure": 0.0, "action": "HOLD"},
            metadata={"error": True},
        )

        return DecisionOutput(
            risk_decision=risk_decision,
            target_exposure=target,
            causation_chain=chain,
            warnings=(error,),
        )


__all__ = [
    "DecisionAuthority",
    "DecisionInput",
    "DecisionOutput",
]