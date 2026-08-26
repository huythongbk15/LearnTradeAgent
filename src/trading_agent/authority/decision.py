"""
DecisionAuthority — Converts StrategyOutput (from promoted strategy) into UnifiedRiskDecision + TargetExposure.

This is the FIRST authority in the chain. It is fail-closed:
- No signal → HOLD (no exposure)
- Invalid signal → HOLD
- Missing required fields → HOLD
- Any error → HOLD with error metadata

The authority_chain field on UnifiedRiskDecision is populated here and propagated downstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from trading_agent.authority.causation import CausationChain, new_chain
from trading_agent.authority.config import AuthorityConfig, get_authority_config
from trading_agent.authority.resolver import StrategyOutput
from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
# Define TargetExposure locally for the authority chain


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


logger = logging.getLogger(__name__)


# ── Input types ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Input to DecisionAuthority — StrategyOutput from promoted strategy runtime."""

    # StrategyOutput from RuntimeStrategyResolver (promoted strategy path)
    strategy_output: StrategyOutput | None = None

    # Legacy: AgentMessage (will be converted to StrategyOutput)
    agent_message: Any | None = None

    # Option: Direct risk decision (bypass, for testing)
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
            for f in (self.strategy_output, self.agent_message, self.risk_decision)
            if f is not None
        )
        if provided != 1:
            raise ValueError(
                "Exactly one of strategy_output, agent_message, risk_decision must be provided"
            )
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
    Authority 1: StrategyOutput → UnifiedRiskDecision + TargetExposure.

    Responsibilities:
    1. Validate StrategyOutput from promoted strategy runtime
    2. Apply risk profile scaling (from AuthorityConfig)
    3. Enforce exposure caps (from AuthorityConfig)
    4. Produce UnifiedRiskDecision with authority_chain
    5. Produce TargetExposure with authority_chain reference
    6. Emit causation chain for audit
    """

    def __init__(self, config: AuthorityConfig | None = None):
        self.config = config or get_authority_config()

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
            if input_.strategy_output is not None:
                risk_decision, target, chain = self._from_strategy_output(input_, chain)
            elif input_.agent_message is not None:
                risk_decision, target, chain = self._from_agent_message(input_, chain)
            else:  # input_.risk_decision is not None
                risk_decision, target, chain = self._from_direct_decision(input_, chain)

            # Final validation & enforcement
            risk_decision, target = self._enforce_limits(
                risk_decision, target, input_, chain
            )

            return DecisionOutput(
                risk_decision=risk_decision,
                target_exposure=target,
                causation_chain=chain,
            )

        except Exception as e:
            logger.error(f"DecisionAuthority failed: {e}", exc_info=True)
            return self._fail_closed(input_, chain, str(e))

    def _input_type(self, input_: DecisionInput) -> str:
        if input_.strategy_output:
            return "strategy_output"
        if input_.agent_message:
            return "agent_message"
        return "risk_decision"

    # ── Handler: StrategyOutput path (promoted strategy runtime) ────────

    def _from_strategy_output(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure, CausationChain]:
        output = input_.strategy_output

        # Extract signal and confidence from StrategyOutput
        signal = output.signal
        metadata = output.metadata

        # Handle polars Series (from generate_signals)
        if hasattr(signal, "to_numpy"):
            values = signal.to_numpy()
            last_val = float(values[-1]) if len(values) > 0 else 0.0
            signal_str = "BUY" if last_val > 0 else ("SELL" if last_val < 0 else "HOLD")
            reduce_only = last_val < 0
        elif hasattr(signal, "upper"):
            signal_str = signal.upper()
            reduce_only = signal_str == "SELL" or bool(
                metadata.get("reduce_only", False)
            )
        else:
            signal_str = str(signal).upper() if signal else "HOLD"
            reduce_only = False

        confidence = output.confidence
        target_exposure_pct = output.target_exposure_pct

        # Evidence states from artifact metadata (set during promotion)
        calibration_state = metadata.get("calibration_state", "UNKNOWN")
        calibration_ece = metadata.get("calibration_ece", 1.0)
        ood_state = metadata.get("ood_state", "UNKNOWN")
        ood_score = metadata.get("ood_score", 1.0)
        regime_state = metadata.get("regime_state", "UNKNOWN")
        regime_entropy = metadata.get("regime_entropy", 1.0)

        # Apply risk profile scaling
        scaled_target = self._apply_risk_scaling(target_exposure_pct, input_)
        scaled_max_new = self._apply_risk_scaling(
            target_exposure_pct, input_
        )  # Same base

        # Build UnifiedRiskDecision from StrategyOutput
        #
        # decision_id MUST vary per observation: it feeds intent_id and the
        # idempotency key downstream. A constant id would make every new
        # bar collide with the first claimed intent ("submission already
        # claimed by another connection"). observation_id is deterministic
        # (venue+symbol+timeframe+bar_close+data_manifest) so backtest
        # replays stay reproducible.
        if input_.observation_id:
            decision_id = (
                f"strategy_{output.artifact_id[:16]}_{input_.observation_id[:24]}"
            )
        else:
            decision_id = f"strategy_{output.artifact_id[:16]}"
        risk_decision = UnifiedRiskDecision(
            decision_id=decision_id,
            forecast_fingerprint=metadata.get("forecast_fingerprint", ""),
            model_artifact_id=output.artifact_id,
            requested_target_exposure=target_exposure_pct,
            allowed_target_exposure=min(
                scaled_target, self.config.exposure.max_single_strategy_exposure
            ),
            max_new_exposure=min(
                scaled_max_new, self.config.exposure.max_single_strategy_exposure
            ),
            reduce_only=reduce_only,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=(),
            calibration_state=EvidenceState(calibration_state),
            calibration_artifact_id=metadata.get("calibration_artifact_id"),
            calibration_ece=calibration_ece,
            ood_state=EvidenceState(ood_state),
            ood_score=ood_score,
            regime_state=EvidenceState(regime_state),
            regime_entropy=regime_entropy,
            interval_width=metadata.get("interval_width", 1.0),
            created_at=datetime.now(UTC),
            metadata={
                "artifact_id": output.artifact_id,
                "strategy_name": output.strategy_name,
                "symbol": output.symbol,
                "timeframe": output.timeframe,
            },
            warnings=(),
        )

        # Compute target exposure
        target = self._compute_target_exposure(
            signal=signal_str,
            risk_decision=risk_decision,
            current_exposure=input_.current_exposure,
            equity=input_.equity,
            available_cash=input_.available_cash,
        )

        # Append to causation chain
        chain = chain.append(
            authority="DecisionAuthority.promoted_strategy",
            inputs={
                "artifact_id": output.artifact_id,
                "strategy_name": output.strategy_name,
                "signal": signal_str,
                "confidence": confidence,
                "target_exposure_pct": target_exposure_pct,
                "reduce_only": reduce_only,
            },
            outputs={
                "allowed_target_exposure": risk_decision.allowed_target_exposure,
                "max_new_exposure": risk_decision.max_new_exposure,
                "reduce_only": risk_decision.reduce_only,
            },
        )

        return risk_decision, target, chain

    # ── Handler: AgentMessage (legacy path) ────────────────────────────

    def _from_agent_message(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure, CausationChain]:
        """Convert legacy AgentMessage to StrategyOutput and process."""
        msg = input_.agent_message

        # Extract signal details from AgentMessage
        signal_str = getattr(msg, "signal", "HOLD").upper()
        confidence = getattr(msg, "confidence", 0.5)
        details = getattr(msg, "details", {}) or {}
        target_exposure_pct = details.get("target_exposure_pct", 0.0)
        reduce_only = details.get("reduce_only", False)

        # Build StrategyOutput from AgentMessage
        strategy_output = StrategyOutput(
            artifact_id="legacy_agent_message",
            strategy_name="legacy_ensemble",
            symbol=input_.symbol,
            timeframe=input_.timeframe,
            signal=msg,
            confidence=confidence,
            target_exposure_pct=target_exposure_pct,
            metadata={
                "source": "legacy_agent_message",
                "signal": signal_str,
                "details": details,
            },
            generated_at=datetime.now(UTC),
        )

        # Process as StrategyOutput
        output = strategy_output
        metadata = output.metadata
        calibration_state = metadata.get("calibration_state", "UNKNOWN")
        calibration_ece = metadata.get("calibration_ece", 1.0)
        ood_state = metadata.get("ood_state", "UNKNOWN")
        ood_score = metadata.get("ood_score", 1.0)
        regime_state = metadata.get("regime_state", "UNKNOWN")
        regime_entropy = metadata.get("regime_entropy", 1.0)

        # Apply risk profile scaling
        scaled_target = self._apply_risk_scaling(target_exposure_pct, input_)
        scaled_max_new = self._apply_risk_scaling(target_exposure_pct, input_)

        # Build UnifiedRiskDecision from StrategyOutput
        risk_decision = UnifiedRiskDecision(
            decision_id=f"legacy_{input_.symbol.replace('/', '_')}",
            forecast_fingerprint="",
            model_artifact_id="legacy_agent_message",
            requested_target_exposure=target_exposure_pct,
            allowed_target_exposure=min(
                scaled_target, self.config.exposure.max_single_strategy_exposure
            ),
            max_new_exposure=min(
                scaled_max_new, self.config.exposure.max_single_strategy_exposure
            ),
            reduce_only=reduce_only,
            risk_level=RiskLevel.MEDIUM,
            reason_codes=(),
            calibration_state=EvidenceState.KNOWN,  # Legacy path: no research evidence yet
            calibration_artifact_id=None,
            calibration_ece=0.0,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.0,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.0,
            interval_width=1.0,
            created_at=datetime.now(UTC),
            metadata={
                "artifact_id": "legacy_agent_message",
                "strategy_name": "legacy_ensemble",
                "symbol": input_.symbol,
                "timeframe": input_.timeframe,
            },
            warnings=(),
        )

        target = self._compute_target_exposure(
            signal=signal_str,
            risk_decision=risk_decision,
            current_exposure=input_.current_exposure,
            equity=input_.equity,
            available_cash=input_.available_cash,
        )

        chain = chain.append(
            authority="DecisionAuthority.legacy_agent_message",
            inputs={
                "signal": signal_str,
                "confidence": confidence,
                "target_exposure_pct": target_exposure_pct,
                "reduce_only": reduce_only,
            },
            outputs={
                "allowed_target_exposure": risk_decision.allowed_target_exposure,
                "max_new_exposure": risk_decision.max_new_exposure,
                "reduce_only": risk_decision.reduce_only,
            },
        )

        return risk_decision, target, chain

    # ── Handler: Direct UnifiedRiskDecision (testing/bypass) ────────────

    def _from_direct_decision(
        self, input_: DecisionInput, chain: CausationChain
    ) -> tuple[UnifiedRiskDecision, TargetExposure, CausationChain]:
        risk_decision = input_.risk_decision

        # Apply config caps
        capped_target = min(
            risk_decision.allowed_target_exposure,
            self.config.exposure.max_single_strategy_exposure,
        )
        capped_max_new = min(
            risk_decision.max_new_exposure,
            self.config.exposure.max_single_strategy_exposure,
        )

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

        signal_str = (
            "BUY"
            if capped_target > input_.current_exposure
            else "SELL"
            if capped_target < input_.current_exposure
            else "HOLD"
        )

        target = self._compute_target_exposure(
            signal=signal_str,
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

        return risk_decision, target, chain

    # ── Core logic ──────────────────────────────────────────────────────

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
            confidence=0.5,
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

        # Symbol-level cap
        target_exposure = min(
            target.target_exposure_pct, self.config.exposure.max_single_symbol_exposure
        )
        max_new_exposure = min(
            target.max_new_exposure_pct, self.config.exposure.max_single_symbol_exposure
        )

        # Notional caps
        target_notional = target_exposure * input_.equity
        if target_notional > self.config.exposure.max_trade_notional:
            target_exposure = self.config.exposure.max_trade_notional / input_.equity
            warnings.append("target_notional capped at max_trade_notional")
        if (
            target_notional < self.config.exposure.min_trade_notional
            and target_exposure > 0
        ):
            target_exposure = 0.0
            max_new_exposure = 0.0
            warnings.append("target_notional below min_trade_notional → HOLD")

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
