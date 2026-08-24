"""
ExposureAuthority — Validates exposure changes against caps.

This is the SECOND authority in the chain. It is the SINGLE SOURCE OF TRUTH
for exposure limits. No exposure-increasing order can bypass this authority.

Responsibilities:
1. Validate target_exposure against portfolio/symbol/strategy caps
2. Compute allowed exposure delta (increase/reduce/neutral)
3. Enforce correlation limits (future: multi-pair)
4. Emit causation link for audit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from trading_agent.authority.causation import CausationChain
from trading_agent.authority.config import AuthorityConfig, ExposureConfig, get_authority_config
from trading_agent.authority.decision import TargetExposure
from trading_agent.execution.canonical.order_planner import ExposureEffect

logger = logging.getLogger(__name__)


# ── Input / Output ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExposureValidationInput:
    """Input to ExposureAuthority."""

    target_exposure: TargetExposure
    symbol: str
    strategy_id: str  # Strategy identifier for per-strategy caps
    current_exposure: float  # Current exposure for this symbol (0.0 to 1.0)
    portfolio_exposure: float  # Total portfolio exposure (sum of all positions)
    strategy_exposure: float  # Current exposure for this strategy
    equity: float
    available_cash: float
    correlation_exposure: float = 0.0  # Exposure to correlated symbols
    causation_chain: CausationChain | None = None


@dataclass(frozen=True, slots=True)
class ExposureValidationOutput:
    """Output from ExposureAuthority."""

    allowed: bool
    exposure_effect: ExposureEffect
    allowed_target_exposure: float  # Capped target exposure
    allowed_max_new_exposure: float  # Capped max new exposure
    allowed_delta: float  # Exposure change allowed (positive = increase)
    reason: str
    causation_chain: CausationChain
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ── ExposureAuthority ──────────────────────────────────────────────────


class ExposureAuthority:
    """
    Authority 2: Validates TargetExposure against all caps.

    This is the EXCLUSIVE gate for exposure increases. No order that increases
    exposure can be placed without passing through this authority.

    Fail-closed: any validation error → DENY (exposure_effect=NEUTRAL, delta=0)
    """

    def __init__(self, config: AuthorityConfig | None = None):
        self.config = config or get_authority_config()
        self.exposure_config: ExposureConfig = self.config.exposure

    def validate(self, input_: ExposureValidationInput) -> ExposureValidationOutput:
        """Main validation entry point — fail-closed."""

        # Start or continue causation chain
        chain = input_.causation_chain
        if chain is None:
            from trading_agent.authority.causation import new_chain

            chain = new_chain(
                {
                    "authority": "ExposureAuthority",
                    "symbol": input_.symbol,
                    "strategy_id": input_.strategy_id,
                }
            )

        try:
            # Compute exposure delta
            delta = input_.target_exposure.target_exposure_pct - input_.current_exposure
            exposure_effect = self._classify_effect(delta)

            # Run all validation checks
            checks = [
                self._check_portfolio_cap(input_, delta, exposure_effect),
                self._check_strategy_cap(input_, delta, exposure_effect),
                self._check_symbol_cap(input_, delta, exposure_effect),
                self._check_correlation_cap(input_, delta, exposure_effect),
                self._check_cash_availability(input_, delta, exposure_effect),
                self._check_notional_limits(input_, delta, exposure_effect),
                self._check_reduce_only(input_, exposure_effect),
            ]

            # Aggregate results
            all_passed = all(c[0] for c in checks)
            reasons = [c[1] for c in checks if not c[0]]
            warnings = tuple(c[2] for c in checks if c[2])

            if not all_passed:
                return self._deny(input_, chain, reasons, warnings)

            # Compute allowed values (capped)
            allowed_target = self._cap_target(input_)
            allowed_max_new = self._cap_max_new(input_, allowed_target)
            allowed_delta = allowed_target - input_.current_exposure

            # Final chain append
            chain = chain.append(
                authority="ExposureAuthority",
                inputs={
                    "requested_target": input_.target_exposure.target_exposure_pct,
                    "requested_max_new": input_.target_exposure.max_new_exposure_pct,
                    "current_exposure": input_.current_exposure,
                    "portfolio_exposure": input_.portfolio_exposure,
                    "strategy_exposure": input_.strategy_exposure,
                    "correlation_exposure": input_.correlation_exposure,
                },
                outputs={
                    "allowed": True,
                    "exposure_effect": exposure_effect.value,
                    "allowed_target": allowed_target,
                    "allowed_max_new": allowed_max_new,
                    "allowed_delta": allowed_delta,
                },
            )

            return ExposureValidationOutput(
                allowed=True,
                exposure_effect=exposure_effect,
                allowed_target_exposure=allowed_target,
                allowed_max_new_exposure=allowed_max_new,
                allowed_delta=allowed_delta,
                reason="All exposure checks passed",
                causation_chain=chain,
                warnings=warnings,
            )

        except Exception as e:
            logger.error(f"ExposureAuthority failed: {e}", exc_info=True)
            return self._deny(input_, chain, [f"Internal error: {e}"], ("internal_error",))

    def _classify_effect(self, delta: float) -> ExposureEffect:
        """Classify exposure change direction."""
        if delta > 1e-9:
            return ExposureEffect.INCREASE
        if delta < -1e-9:
            return ExposureEffect.REDUCE
        return ExposureEffect.NEUTRAL

    # ── Individual validation checks ────────────────────────────────────

    def _check_portfolio_cap(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Portfolio-level exposure cap."""
        if effect != ExposureEffect.INCREASE:
            return True, "", None

        projected = input_.portfolio_exposure + delta
        if projected > self.exposure_config.max_portfolio_exposure + 1e-9:
            return (
                False,
                f"portfolio_exposure {projected:.4f} > max {self.exposure_config.max_portfolio_exposure}",
                f"portfolio_cap_breach: {projected:.4f} > {self.exposure_config.max_portfolio_exposure}",
            )
        return True, "", None

    def _check_strategy_cap(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Per-strategy exposure cap."""
        if effect != ExposureEffect.INCREASE:
            return True, "", None

        projected = input_.strategy_exposure + delta
        if projected > self.exposure_config.max_single_strategy_exposure + 1e-9:
            return (
                False,
                f"strategy_exposure {projected:.4f} > max {self.exposure_config.max_single_strategy_exposure}",
                f"strategy_cap_breach: {projected:.4f} > {self.exposure_config.max_single_strategy_exposure}",
            )
        return True, "", None

    def _check_symbol_cap(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Per-symbol exposure cap."""
        if effect != ExposureEffect.INCREASE:
            return True, "", None

        projected = input_.current_exposure + delta
        if projected > self.exposure_config.max_single_symbol_exposure + 1e-9:
            return (
                False,
                f"symbol_exposure {projected:.4f} > max {self.exposure_config.max_single_symbol_exposure}",
                f"symbol_cap_breach: {projected:.4f} > {self.exposure_config.max_single_symbol_exposure}",
            )
        return True, "", None

    def _check_correlation_cap(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Correlated symbols exposure cap."""
        if effect != ExposureEffect.INCREASE:
            return True, "", None

        # Correlation exposure includes this symbol's projected exposure
        projected = input_.correlation_exposure + delta
        if projected > self.exposure_config.max_correlated_exposure + 1e-9:
            return (
                False,
                f"correlated_exposure {projected:.4f} > max {self.exposure_config.max_correlated_exposure}",
                f"correlation_cap_breach: {projected:.4f} > {self.exposure_config.max_correlated_exposure}",
            )
        return True, "", None

    def _check_cash_availability(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Sufficient cash for exposure increase (spot long-only)."""
        if effect != ExposureEffect.INCREASE:
            return True, "", None

        required_notional = delta * input_.equity
        if required_notional > input_.available_cash + 1e-9:
            return (
                False,
                f"required_notional {required_notional:.2f} > available_cash {input_.available_cash:.2f}",
                f"insufficient_cash: need {required_notional:.2f}, have {input_.available_cash:.2f}",
            )
        return True, "", None

    def _check_notional_limits(
        self, input_: ExposureValidationInput, delta: float, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Min/max notional per trade."""
        if effect == ExposureEffect.NEUTRAL:
            return True, "", None

        trade_notional = abs(delta) * input_.equity

        if trade_notional > self.exposure_config.max_trade_notional + 1e-9:
            return (
                False,
                f"trade_notional {trade_notional:.2f} > max {self.exposure_config.max_trade_notional}",
                f"max_notional_breach: {trade_notional:.2f} > {self.exposure_config.max_trade_notional}",
            )

        if effect == ExposureEffect.INCREASE and trade_notional < self.exposure_config.min_trade_notional:
            return (
                False,
                f"trade_notional {trade_notional:.2f} < min {self.exposure_config.min_trade_notional}",
                f"min_notional_breach: {trade_notional:.2f} < {self.exposure_config.min_trade_notional}",
            )

        return True, "", None

    def _check_reduce_only(
        self, input_: ExposureValidationInput, effect: ExposureEffect
    ) -> tuple[bool, str, str | None]:
        """Respect reduce_only flag from risk decision."""
        if input_.target_exposure.reduce_only and effect == ExposureEffect.INCREASE:
            return (
                False,
                "reduce_only=True but exposure_effect=INCREASE",
                "reduce_only_violation",
            )
        return True, "", None

    # ── Capping logic ──────────────────────────────────────────────────

    def _cap_target(self, input_: ExposureValidationInput) -> float:
        """Compute capped target exposure."""
        target = input_.target_exposure.target_exposure_pct

        # Portfolio cap
        max_by_portfolio = self.exposure_config.max_portfolio_exposure - input_.portfolio_exposure + input_.current_exposure
        target = min(target, max_by_portfolio)

        # Strategy cap
        max_by_strategy = self.exposure_config.max_single_strategy_exposure - input_.strategy_exposure + input_.current_exposure
        target = min(target, max_by_strategy)

        # Symbol cap
        target = min(target, self.exposure_config.max_single_symbol_exposure)

        # Correlation cap
        max_by_corr = self.exposure_config.max_correlated_exposure - input_.correlation_exposure + input_.current_exposure
        target = min(target, max_by_corr)

        # Cash cap
        max_by_cash = input_.available_cash / input_.equity if input_.equity > 0 else 0.0
        target = min(target, max_by_cash)

        # Notional caps
        max_notional = self.exposure_config.max_trade_notional / input_.equity if input_.equity > 0 else 0.0
        target = min(target, max_notional)

        min_notional = self.exposure_config.min_trade_notional / input_.equity if input_.equity > 0 else 0.0
        if target > 0 and target < min_notional:
            target = 0.0

        return max(0.0, target)

    def _cap_max_new(self, input_: ExposureValidationInput, capped_target: float) -> float:
        """Compute capped max_new_exposure."""
        max_new = input_.target_exposure.max_new_exposure_pct
        max_new = min(max_new, capped_target)  # Can't exceed target
        max_new = min(max_new, self.exposure_config.max_single_symbol_exposure)
        max_new = min(max_new, self.exposure_config.max_single_strategy_exposure)
        max_new = min(max_new, input_.available_cash / input_.equity if input_.equity > 0 else 0.0)
        return max(0.0, max_new)

    # ── Deny helper ────────────────────────────────────────────────────

    def _deny(
        self,
        input_: ExposureValidationInput,
        chain: CausationChain,
        reasons: list[str],
        warnings: tuple[str, ...],
    ) -> ExposureValidationOutput:
        """Construct deny output with causation chain."""

        chain = chain.append(
            authority="ExposureAuthority",
            inputs={
                "requested_target": input_.target_exposure.target_exposure_pct,
                "current_exposure": input_.current_exposure,
            },
            outputs={
                "allowed": False,
                "exposure_effect": ExposureEffect.NEUTRAL.value,
                "allowed_target": input_.current_exposure,
                "allowed_delta": 0.0,
            },
            metadata={"denied": True, "reasons": reasons},
        )

        return ExposureValidationOutput(
            allowed=False,
            exposure_effect=ExposureEffect.NEUTRAL,
            allowed_target_exposure=input_.current_exposure,
            allowed_max_new_exposure=0.0,
            allowed_delta=0.0,
            reason="; ".join(reasons) if reasons else "Exposure validation failed",
            causation_chain=chain,
            warnings=warnings,
        )


__all__ = [
    "ExposureAuthority",
    "ExposureValidationInput",
    "ExposureValidationOutput",
]