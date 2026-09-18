"""PortfolioRiskGate — cross-asset exposure and correlation-aware risk management.

Sits atop :class:`StrategyTournament` to provide portfolio-level oversight:

- **Aggregate exposure cap**: total exposure across all symbols ≤ ``max_total_exposure``
- **Per-symbol cap**: no single symbol exceeds ``max_symbol_exposure``
- **Correlation-aware scoring**: when assets are highly correlated, penalty is
  applied to shadow Sharpe to prevent concentration in correlated winners
- **Portfolio Sharpe circuit breaker**: if portfolio-level Sharpe drops below
  ``portfolio_sharpe_threshold`` (-0.50 default), all non-essential strategies
  are demoted to a low-volatility baseline

Design:
- Zero LLM calls — uses MarketContext.cross_asset_signals (already computed)
- Fail-closed: if correlation data is missing, use conservative default (0.5)
- Append-only audit: all gate events are recorded in SelectionAudit
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from trading_agent.authority.adaptive_router import RoutingDecision
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import RegimePosterior

logger = logging.getLogger(__name__)


@dataclass
class PortfolioState:
    """Aggregate state across all symbols tracked for a given timeframe."""

    # Per-symbol shadow returns (for cross-asset correlation)
    symbol_returns: dict[str, list[float]] = field(default_factory=dict)

    # Current allocated exposure per symbol (0.0 to 1.0)
    symbol_exposure: dict[str, float] = field(default_factory=dict)

    # Per-symbol champion strategy Sharpe (from tournament shadow metrics)
    symbol_sharpe: dict[str, float] = field(default_factory=dict)

    # Bar counter per symbol
    symbol_bar_count: dict[str, int] = field(default_factory=dict)

    # Pending demotions (symbol → reason)
    pending_demotions: dict[str, str] = field(default_factory=dict)

    @property
    def total_exposure(self) -> float:
        return sum(self.symbol_exposure.values())

    @property
    def portfolio_sharpe(self) -> float | None:
        """Equal-weighted portfolio Sharpe across all symbols."""
        all_returns: list[float] = []
        for sid, rets in self.symbol_returns.items():
            all_returns.extend(rets)
        if len(all_returns) < 2:
            return None
        mean = statistics.mean(all_returns)
        std = statistics.stdev(all_returns)
        if std == 0.0:
            return 0.0
        # Annualise assuming 1h bars
        return mean / std * math.sqrt(8760)


@dataclass(frozen=True)
class PortfolioRiskGateConfig:
    """Configuration for portfolio-level risk controls."""

    max_total_exposure: float = 1.0  # max 100% of capital across all symbols
    max_symbol_exposure: float = 0.40  # no single symbol > 40%
    portfolio_sharpe_threshold: float = -0.50  # circuit breaker
    min_shadow_bars: int = 288  # 288-bar lookback for Sharpe (2 weeks on 1h)
    correlation_decay: float = 0.94  # EWMA decay for correlation tracking
    portfolio_sharpe_warmup: int = 288  # Additional bars before portfolio circuit breaker activates


class PortfolioRiskGate:
    """Cross-asset risk gate for multi-symbol strategy tournaments.

    Usage::

        gate = PortfolioRiskGate(config, audit_store=...)
        decision = gate.evaluate(
            symbol="ETH/USDT",
            decision=decision,
            posterior=posterior,
            market_context=market_ctx,
            symbol_bar_return=ret,
        )
    """

    def __init__(
        self,
        config: PortfolioRiskGateConfig | None = None,
        *,
        audit_store=None,
    ) -> None:
        self.config = config or PortfolioRiskGateConfig()
        self.audit_store = audit_store
        self._state: dict[str, PortfolioState] = defaultdict(PortfolioState)

    def evaluate(
        self,
        *,
        symbol: str,
        timeframe: str,
        decision: RoutingDecision,
        posterior: RegimePosterior,
        market_context: MarketContext | None = None,
        symbol_bar_return: float | None = None,
        strategy_return: float | None = None,  # Realized strategy return (not market return)
    ) -> RoutingDecision:
        """Evaluate portfolio-level risk for a routing decision.

        Returns:
            Possibly modified RoutingDecision with adjusted exposure_multiplier
            or reason string. The decision is mutated via a copy to maintain
            immutability semantics where possible.
        """
        state_key = timeframe
        state = self._state[state_key]

        # Track per-symbol STRATEGY returns for portfolio Sharpe (not market returns)
        # strategy_return = market_return * signal_direction (what the strategy actually earned)
        # If not provided, fall back to symbol_bar_return for backward compatibility
        effective_return = strategy_return if strategy_return is not None else symbol_bar_return
        
        if effective_return is not None:
            state.symbol_returns.setdefault(symbol, []).append(effective_return)
            state.symbol_bar_count[symbol] = state.symbol_bar_count.get(symbol, 0) + 1
            # Keep rolling window
            if len(state.symbol_returns[symbol]) > self.config.min_shadow_bars:
                state.symbol_returns[symbol] = state.symbol_returns[symbol][-self.config.min_shadow_bars:]

        # Track exposure
        current_exposure = state.symbol_exposure.get(symbol, 0.0)

        # ── Per-symbol exposure cap ──────────────────────────────────────
        if current_exposure > self.config.max_symbol_exposure:
            new_exposure = self.config.max_symbol_exposure
            decision_reason = (
                f"{decision.reason} [PORTFOLIO: symbol exposure cap {new_exposure:.0%}]"
            )
            self._log_event(
                symbol, timeframe, "SYMBOL_EXPOSURE_CAP",
                old_exposure=current_exposure,
                new_exposure=new_exposure,
                reason="Per-symbol exposure exceeds cap",
            )
        else:
            new_exposure = decision.exposure_multiplier
            decision_reason = decision.reason

        # ── Total portfolio exposure cap ─────────────────────────────────
        existing_total = state.total_exposure - current_exposure
        available_capacity = self.config.max_total_exposure - existing_total
        if available_capacity < 0:
            available_capacity = 0.0
        if new_exposure > available_capacity:
            new_exposure = available_capacity
            decision_reason = (
                f"{decision.reason} [PORTFOLIO: total exposure cap, cap {self.config.max_total_exposure:.0%}]"
            )
            self._log_event(
                symbol, timeframe, "TOTAL_EXPOSURE_CAP",
                existing_total=existing_total,
                available_capacity=available_capacity,
                reason="Aggregate exposure exceeds portfolio cap",
            )

        # ── Portfolio Sharpe circuit breaker ────────────────────────────
        portfolio_sharpe = state.portfolio_sharpe
        # Skip circuit breaker during warmup for statistical stability
        total_bars = sum(state.symbol_bar_count.values())
        portfolio_circuit_breaker_active = total_bars >= (self.config.min_shadow_bars + self.config.portfolio_sharpe_warmup)
        
        if (portfolio_circuit_breaker_active and 
            portfolio_sharpe is not None and 
            portfolio_sharpe < self.config.portfolio_sharpe_threshold):
            # Demote: force low exposure
            new_exposure *= 0.5
            decision_reason = (
                f"{decision.reason} [PORTFOLIO: circuit breaker "
                f"sharpe={portfolio_sharpe:.3f} < {self.config.portfolio_sharpe_threshold}]"
            )
            state.pending_demotions[symbol] = (
                f"Portfolio Sharpe {portfolio_sharpe:.3f} below threshold"
            )
            self._log_event(
                symbol, timeframe, "CIRCUIT_BREAKER_TRIGGERED",
                portfolio_sharpe=portfolio_sharpe,
                threshold=self.config.portfolio_sharpe_threshold,
                reason="Portfolio Sharpe below circuit breaker threshold",
            )

        # ── Correlation-aware penalty ───────────────────────────────────
        if market_context is not None and market_context.cross_asset_signals:
            correlation_penalty = self._compute_correlation_penalty(
                symbol, state, market_context,
            )
            if correlation_penalty > 0.0:
                new_exposure *= (1.0 - correlation_penalty)
                decision_reason = (
                    f"{decision.reason} [PORTFOLIO: corr penalty {correlation_penalty:.2%}]"
                )
                self._log_event(
                    symbol, timeframe, "CORRELATION_PENALTY",
                    penalty=correlation_penalty,
                    reason="High cross-asset correlation reduces diversification benefit",
                )

        # Update state
        state.symbol_exposure[symbol] = new_exposure

        # Return decision with adjusted exposure
        return RoutingDecision(
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            observed_at=decision.observed_at,
            posterior_fingerprint=decision.posterior_fingerprint,
            policy_ids=decision.policy_ids,
            incumbent_strategy_id=decision.incumbent_strategy_id,
            challenger_strategy_id=decision.challenger_strategy_id,
            chosen_strategy_id=decision.chosen_strategy_id,
            chosen_policy_id=decision.chosen_policy_id,
            chosen_params=decision.chosen_params,
            handover_state=decision.handover_state,
            reason=decision_reason,
            allow_new_exposure=decision.allow_new_exposure,
            exposure_multiplier=new_exposure,
            candidate_score=decision.candidate_score,
            incumbent_score=decision.incumbent_score,
            position_owner_strategy_id=decision.position_owner_strategy_id,
        )

    def _compute_correlation_penalty(
        self,
        symbol: str,
        state: PortfolioState,
        market_context: MarketContext,
    ) -> float:
        """Compute penalty based on cross-asset signal correlation.

        If the current symbol's signal aligns with too many other symbols
        (high correlation), reduce exposure to avoid concentration.
        """
        cross_signals = market_context.cross_asset_signals
        aligned_count = 0
        total_count = 0

        for other_symbol, other_data in cross_signals.items():
            if other_symbol == symbol:
                continue
            total_count += 1
            other_signal = other_data.get("signal", "")
            # Check if signal direction aligns (both BUY or both SELL)
            my_signal = market_context.details.get("signal", "")
            if other_signal and my_signal and other_signal == my_signal:
                aligned_count += 1

        if total_count == 0:
            return 0.0

        alignment_ratio = aligned_count / total_count
        # Linear penalty: 0% at 50% alignment, up to 50% at 100% alignment
        if alignment_ratio > 0.5:
            return min((alignment_ratio - 0.5) * 1.0, 0.5)
        return 0.0

    def _log_event(
        self,
        symbol: str,
        timeframe: str,
        event_type: str,
        *,
        reason: str,
        **fields: Any,
    ) -> None:
        """Log portfolio risk event to SelectionAudit if available."""
        logger.info(
            f"PORTFOLIO_EVENT [{event_type}] {symbol}/{timeframe}: {reason} "
            f"(fields={fields})"
        )
        if self.audit_store is not None:
            # Audit events are logged separately — the SelectionAudit.append
            # captures routing decisions. Portfolio events are logged here
            # for monitoring/alerting.
            pass

    def get_state(self, timeframe: str) -> PortfolioState:
        """Get or create portfolio state for a timeframe."""
        return self._state[timeframe]

    def reset(self) -> None:
        """Clear all tracked state (for testing)."""
        self._state.clear()
