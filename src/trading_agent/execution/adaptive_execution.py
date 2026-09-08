"""
R06: Adaptive Execution End-to-End Bridge

Connects AdaptiveForecastResult → OrderIntent → ExecutionValidationInput →
ExecutionAuthority → BrokerGateway (Simulator/Paper) → Ledger → Positions/Cash/Equity

Key requirements:
- No flat/exposure=0/equity fixed assumptions
- Handle position switching (close old strategy, open new strategy)
- Track ledger/positions/cash/equity from execution results
- Compare adaptive vs incumbent on same OOS data, capital, cost
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import polars as pl

from trading_agent.authority.adaptive_router import (
    AdaptiveForecastResult,
    AdaptiveForecastRuntime,
    AdaptiveStrategyRouter,
    HandoverState,
    RoutingDecision,
)
from trading_agent.authority.config import Environment
from trading_agent.execution.canonical import (
    BrokerGateway,
    EnrichedMarketObservation,
    EvidenceState,
    InstrumentRules,
    MarketPrice,
    OrderPlanner,
    PaperExecutionAdapter,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    ExposureEffect,
    OrderIntent,
    OrderPlanningResult,
    OrderPlanningStatus,
    TargetExposure,
)
from trading_agent.execution.canonical.broker_gateway import (
    BrokerGateway,
    BrokerSubmitResult,
    BrokerSubmitState,
)
from trading_agent.execution.canonical.market_observation import (
    BarState,
    EnrichedMarketObservation,
)
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionLifecycle,
    LifecycleState,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.simulator.engine import (
    MarketReplayEngine,
    OrderIntent as SimOrderIntent,
    run_strategy_through_simulator,
    SimulatedExecutionResult,
    SimulationConfig,
    SimOrderType,
    SimSide,
)
from trading_agent.execution.simulator.ledger import ExecutionLedger
from trading_agent.execution.simulator.metrics import compute_execution_metrics
from trading_agent.research.forecast import Forecast, MarketObservation
from trading_agent.research.selection_policy import (
    SelectionPolicyRegistry,
    SelectionPolicyArtifact,
    ParamArtifact,
    PolicyStatus,
    PolicyActivationService,
)


@dataclass(frozen=True)
class AdaptiveExecutionConfig:
    """Configuration for adaptive execution bridge."""

    # Position management
    max_position_pct: float = 1.0  # Max position as fraction of equity
    position_sizing_fraction: float = 0.1  # Risk budget fraction
    close_on_switch: bool = True  # Close incumbent position before opening challenger

    # Execution
    commission_bps: float = 5.0  # 5 bps = 0.05%
    slippage_bps: float = 2.0  # 2 bps = 0.02%
    spread_bps: float = 1.0  # 1 bps base spread

    # Simulation
    random_seed: int = 42
    min_qty: float = 0.0
    min_notional: float = 10.0

    # Reconciliation
    max_price_age_seconds: int = 300
    require_fresh_market_data: bool = True


@dataclass
class AdaptiveExecutionState:
    """Runtime state for adaptive execution (restart-safe)."""

    symbol: str
    timeframe: str
    current_strategy_id: str | None = None
    current_policy_id: str | None = None
    position_quantity: float = 0.0
    position_entry_price: float | None = None
    cash_quote: float = 10_000.0
    equity: float = 10_000.0
    last_observed_at: str | None = None
    last_decision_id: str | None = None
    cooldown_remaining: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_strategy_id": self.current_strategy_id,
            "current_policy_id": self.current_policy_id,
            "position_quantity": self.position_quantity,
            "position_entry_price": self.position_entry_price,
            "cash_quote": self.cash_quote,
            "equity": self.equity,
            "last_observed_at": self.last_observed_at,
            "last_decision_id": self.last_decision_id,
            "cooldown_remaining": self.cooldown_remaining,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptiveExecutionState:
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        checksum = hashlib.sha256(encoded).hexdigest()
        stored = {"state": self.to_dict(), "checksum": checksum}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stored, sort_keys=True, separators=(",", ":")))
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> AdaptiveExecutionState:
        if not path.exists():
            raise FileNotFoundError(f"State file not found: {path}")
        stored = json.loads(path.read_text())
        payload = stored.get("state")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expected = hashlib.sha256(encoded).hexdigest()
        import hmac
        if not hmac.compare_digest(expected, str(stored.get("checksum", ""))):
            raise ValueError("Execution state checksum mismatch")
        return cls.from_dict(payload)


@dataclass
class AdaptiveExecutionResult:
    """Result of one adaptive execution step."""

    decision: RoutingDecision
    forecast_result: AdaptiveForecastResult
    order_intents: list[OrderIntent]
    execution_output: Any  # ExecutionValidationOutput or SimulatedExecutionResult
    position_before: float
    position_after: float
    cash_before: float
    cash_after: float
    equity_before: float
    equity_after: float
    pnl_delta: float
    handover_executed: bool = False
    handover_reason: str = ""


def _forecast_to_order_intent(
    forecast: Forecast,
    decision: RoutingDecision,
    observation: MarketObservation,
    state: AdaptiveExecutionState,
    config: AdaptiveExecutionConfig,
) -> list[OrderIntent]:
    """Convert a routed forecast into executable order intents."""
    from trading_agent.execution.canonical.order_planner import ExposureEffect

    intents: list[OrderIntent] = []

    if forecast.expected_excess_return <= 0:
        return intents  # No positive alpha → no new exposure

    # Calculate position size using risk budget (mirrors simulator logic)
    equity = state.equity
    price = observation.close
    atr = forecast.metadata.get("atr") if forecast.metadata else None

    risk_amount = equity * config.position_sizing_fraction
    if atr and atr > 0:
        position_size = risk_amount / (atr * 2.0)
    else:
        position_size = risk_amount / price

    position_size = min(position_size, equity * config.max_position_pct / price)
    position_size = min(position_size, equity * 0.95 / price)
    position_size = max(position_size, 0.0)

    current_qty = state.position_quantity

    # Handle position switching
    if decision.handover_state in (HandoverState.ACTIVATE, HandoverState.WAIT_FLAT):
        if decision.chosen_strategy_id != state.current_strategy_id:
            # Close existing position first
            if current_qty != 0:
                intents.append(
                    OrderIntent(
                        intent_id=f"close_{decision.decision_id[:8]}",
                        decision_id=decision.decision_id,
                        forecast_fingerprint=decision.posterior_fingerprint,
                        model_artifact_id=f"adaptive.{decision.chosen_strategy_id}",
                        symbol=observation.symbol,
                        asset_class="crypto_spot",
                        side="sell" if current_qty > 0 else "buy",
                        quantity=abs(current_qty),
                        current_exposure=state.position_quantity / (equity / price) if equity > 0 else 0.0,
                        target_exposure=0.0,
                        resulting_exposure=0.0,
                        exposure_effect=ExposureEffect.REDUCE,
                        price_reference=price,
                        idempotency_key=f"close_{decision.decision_id}",
                        created_at=observation.observed_at,
                        metadata={
                            "action": "close_for_switch",
                            "old_strategy": state.current_strategy_id,
                            "new_strategy": decision.chosen_strategy_id,
                        },
                    )
                )
                current_qty = 0

    # Open new position if allowed and flat
    if decision.allow_new_exposure and position_size > 0:
        target_qty = position_size * decision.exposure_multiplier
        if target_qty > current_qty:
            intents.append(
                OrderIntent(
                    intent_id=f"open_{decision.decision_id[:8]}",
                    decision_id=decision.decision_id,
                    forecast_fingerprint=decision.posterior_fingerprint,
                    model_artifact_id=f"adaptive.{decision.chosen_strategy_id}",
                    symbol=observation.symbol,
                    asset_class="crypto_spot",
                    side="buy",
                    quantity=target_qty - current_qty,
                    current_exposure=state.position_quantity / (equity / price) if equity > 0 else 0.0,
                    target_exposure=decision.exposure_multiplier,
                    resulting_exposure=decision.exposure_multiplier,
                    exposure_effect=ExposureEffect.INCREASE,
                    price_reference=price,
                    idempotency_key=f"open_{decision.decision_id}",
                    created_at=observation.observed_at,
                    metadata={
                        "action": "open_new",
                        "strategy": decision.chosen_strategy_id,
                        "policy_id": decision.chosen_policy_id,
                        "exposure_multiplier": decision.exposure_multiplier,
                        "expected_return": forecast.expected_excess_return,
                    },
                )
            )

    # Reduce position if exposure multiplier decreased
    elif current_qty > 0 and decision.exposure_multiplier < 1.0:
        target_qty = current_qty * decision.exposure_multiplier
        if target_qty < current_qty:
            intents.append(
                OrderIntent(
                    intent_id=f"reduce_{decision.decision_id[:8]}",
                    decision_id=decision.decision_id,
                    forecast_fingerprint=decision.posterior_fingerprint,
                    model_artifact_id=f"adaptive.{decision.chosen_strategy_id}",
                    symbol=observation.symbol,
                    asset_class="crypto_spot",
                    side="sell",
                    quantity=current_qty - target_qty,
                    current_exposure=state.position_quantity / (equity / price) if equity > 0 else 0.0,
                    target_exposure=decision.exposure_multiplier,
                    resulting_exposure=decision.exposure_multiplier,
                    exposure_effect=ExposureEffect.REDUCE,
                    price_reference=price,
                    idempotency_key=f"reduce_{decision.decision_id}",
                    created_at=observation.observed_at,
                    metadata={
                        "action": "reduce_exposure",
                        "strategy": decision.chosen_strategy_id,
                        "multiplier": decision.exposure_multiplier,
                    },
                )
            )

    return intents


class AdaptiveExecutionBridge:
    """
    Bridges AdaptiveForecastResult to canonical execution pipeline.

    Flow:
    1. AdaptiveStrategyRouter.route() → RoutingDecision
    2. AdaptiveForecastRuntime.forecast() → AdaptiveForecastResult (with Forecast)
    3. AdaptiveExecutionBridge.execute_step() → OrderIntent(s) → ExecutionAuthority → BrokerGateway
    4. Update state from execution result
    """

    def __init__(
        self,
        router: AdaptiveStrategyRouter,
        runtime: AdaptiveForecastRuntime,
        planner: OrderPlanner,
        lifecycle: ExecutionLifecycle,
        gateway: BrokerGateway,
        config: AdaptiveExecutionConfig,
        state: AdaptiveExecutionState | None = None,
    ):
        self.router = router
        self.runtime = runtime
        self.planner = planner
        self.lifecycle = lifecycle
        self.gateway = gateway
        self.config = config
        self.state = state or AdaptiveExecutionState(
            symbol="",
            timeframe="",
            cash_quote=config.position_sizing_fraction * 10_000,  # Will be set properly
            equity=10_000.0,
        )

    def execute_step(
        self,
        observation: MarketObservation,
        posterior: Any,  # RegimePosterior
        observed_at: datetime,
        position_is_flat: bool | None = None,
        position_owner_strategy_id: str | None = None,
    ) -> AdaptiveExecutionResult:
        """
        Execute one adaptive step:
        1. Route → decision
        2. Forecast → forecast_result
        3. Convert to order intents
        4. Plan and execute orders
        5. Update state
        """
        from trading_agent.authority.execution import (
            ExecutionAuthority,
            ExecutionValidationInput,
        )

        # 1. Route
        if position_is_flat is None:
            position_is_flat = self.state.position_quantity == 0
        if position_owner_strategy_id is None:
            position_owner_strategy_id = (
                self.state.current_strategy_id if not position_is_flat else None
            )

        decision = self.router.route(
            symbol=observation.symbol,
            timeframe=self.state.timeframe,
            posterior=posterior,
            observed_at=observed_at,
            position_is_flat=position_is_flat,
            position_owner_strategy_id=position_owner_strategy_id,
        )

        # 2. Forecast
        forecast_result = self.runtime.forecast(decision, observation)

        # 3. Convert to order intents
        intents = []
        if forecast_result.executable and forecast_result.forecast:
            intents = _forecast_to_order_intent(
                forecast_result.forecast,
                decision,
                observation,
                self.state,
                self.config,
            )

        # 4. Plan and execute (simplified - in real use, batch all intents)
        position_before = self.state.position_quantity
        cash_before = self.state.cash_quote
        equity_before = self.state.equity

        execution_output = None
        for intent in intents:
            # Build execution input
            exec_input = self._build_execution_input(intent, observation, decision)
            # Execute through authority
            authority = ExecutionAuthority(self.lifecycle, self.gateway, self.planner)
            execution_output = authority.execute(exec_input)

        # 5. Update state from execution result
        position_after = self.state.position_quantity
        cash_after = self.state.cash_quote
        equity_after = self.state.equity

        # Simplified state update (in real, this comes from lifecycle/ledger)
        for intent in intents:
            if intent.metadata.get("action") == "close_for_switch":
                position_after = 0
            elif intent.metadata.get("action") == "open_new":
                position_after = intent.quantity
            elif intent.metadata.get("action") == "reduce_exposure":
                position_after = intent.quantity

        pnl_delta = equity_after - equity_before
        handover_executed = decision.handover_state in (
            HandoverState.ACTIVATE,
            HandoverState.WAIT_FLAT,
        )
        handover_reason = decision.reason if handover_executed else ""

        # Update state
        self.state.current_strategy_id = decision.chosen_strategy_id
        self.state.current_policy_id = decision.chosen_policy_id
        self.state.position_quantity = position_after
        self.state.last_observed_at = observed_at.isoformat()
        self.state.last_decision_id = decision.decision_id
        if self.state.cooldown_remaining > 0:
            self.state.cooldown_remaining -= 1

        return AdaptiveExecutionResult(
            decision=decision,
            forecast_result=forecast_result,
            order_intents=intents,
            execution_output=execution_output,
            position_before=position_before,
            position_after=position_after,
            cash_before=cash_before,
            cash_after=cash_after,
            equity_before=equity_before,
            equity_after=equity_after,
            pnl_delta=pnl_delta,
            handover_executed=handover_executed,
            handover_reason=handover_reason,
        )

    def _build_execution_input(
        self, intent: OrderIntent, observation: MarketObservation, decision: RoutingDecision
    ) -> Any:
        """Build ExecutionValidationInput for the authority."""
        from trading_agent.authority.execution import ExecutionValidationInput

        # Build trusted price
        trusted_price = TrustedPrice(
            price=observation.close,
            exchange_timestamp=observation.observed_at,
            received_at=datetime.now(UTC),
        )

        # Build market price
        market_price = MarketPrice(
            mid=observation.close,
            bid=observation.close * (1 - self.config.spread_bps / 10000),
            ask=observation.close * (1 + self.config.spread_bps / 10000),
            timestamp=observation.observed_at,
        )

        # Build portfolio state (simplified)
        portfolio_state = CurrentPortfolioState(
            symbol=observation.symbol,
            existing_quantity=self.state.position_quantity,
            existing_reservations=0.0,
            available_cash=self.state.cash_quote,
            equity=self.state.equity,
            timestamp=observation.observed_at,
        )

        # Build instrument rules
        instrument_rules = InstrumentRules(
            symbol=observation.symbol,
            min_order_qty=0.001,
            max_order_qty=1000.0,
            qty_step=0.001,
            price_precision=2,
            min_notional=self.config.min_notional,
            max_notional=None,
            spot_long_only=True,
        )

        # Build risk decision from routing decision
        risk_decision = UnifiedRiskDecision(
            decision_id=decision.decision_id,
            target_exposure=decision.exposure_multiplier,
            max_new_exposure=decision.exposure_multiplier,
            risk_level=RiskLevel.NORMAL,
            evidence_state=EvidenceState.SUFFICIENT,
            reasoning="Adaptive routing decision",
        )

        # Build enriched observation
        enriched_obs = EnrichedMarketObservation(
            observation_id=f"obs_{observation.observed_at.isoformat()}",
            symbol=observation.symbol,
            timestamp=observation.observed_at,
            price=market_price,
            bar_state=BarState.CLOSED,
            features=observation.features,
        )

        return ExecutionValidationInput(
            intent=intent,
            observation=enriched_obs,
            portfolio_state=portfolio_state,
            price=market_price,
            instrument_rules=instrument_rules,
            existing_reservations=0.0,
            causation_chain=None,
            risk_decision=risk_decision,
        )


class AdaptiveSimulatorBridge:
    """
    Runs adaptive strategies through the MarketReplayEngine for backtesting.

    This is the primary R06 backtesting path - it replays bars and drives
    adaptive routing + execution through the deterministic simulator.
    """

    def __init__(
        self,
        router: AdaptiveStrategyRouter,
        runtime: AdaptiveForecastRuntime,
        regime_detector: Callable[[pl.DataFrame], Any],
        config: AdaptiveExecutionConfig,
        initial_cash: float = 10_000.0,
    ):
        self.router = router
        self.runtime = runtime
        self.regime_detector = regime_detector
        self.config = config
        self.initial_cash = initial_cash

    def run(
        self,
        df: pl.DataFrame,
        *,
        symbol: str = "",
        timeframe: str = "1h",
        warmup_bars: int = 200,
        bars_per_year: float = 365.25 * 24,
    ) -> SimulatedExecutionResult:
        """
        Run adaptive strategy through the execution simulator.

        Returns a SimulatedExecutionResult with full ledger, metrics, equity curve.
        """
        # Prepare data
        df = df.sort("timestamp")

        # Build regime detector state over warmup
        # We'll compute posteriors on-the-fly in the provider

        # Create simulator config
        sim_config = SimulationConfig(
            random_seed=self.config.random_seed,
            taker_fee=self.config.commission_bps / 10000,
            maker_fee=self.config.commission_bps / 10000 * 0.5,
            spread_bps=self.config.spread_bps,
            min_qty=self.config.min_qty,
            min_notional=self.config.min_notional,
            step_size=0.001,
        )

        # Create engine
        engine = MarketReplayEngine(
            df,
            config=sim_config,
            symbol=symbol or "ADAPTIVE",
            initial_cash=self.initial_cash,
        )

        # Track state across bars
        exec_state = AdaptiveExecutionState(
            symbol=symbol,
            timeframe=timeframe,
            cash_quote=self.initial_cash,
            equity=self.initial_cash,
        )

        # Pre-compute regime posteriors for all bars (or compute on-the-fly)
        # For efficiency, compute on-the-fly in provider

        def provider(bar_index: int, eng: MarketReplayEngine) -> list[SimOrderIntent]:
            if bar_index < warmup_bars:
                return []

            # Get observation at this bar
            row = df.row(bar_index, named=True)
            observed_at = row["timestamp"]
            if isinstance(observed_at, str):
                observed_at = datetime.fromisoformat(observed_at).replace(tzinfo=UTC)

            # Get recent data for regime detection
            recent_df = df.slice(max(0, bar_index - 200), min(200, bar_index + 1))

            # Detect regime
            signal = self.regime_detector(recent_df)
            posterior = self._signal_to_posterior(signal, observed_at)

            # Build market observation
            observation = MarketObservation(
                symbol=symbol,
                observed_at=observed_at,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )

            # Route
            position_is_flat = exec_state.position_quantity == 0
            decision = self.router.route(
                symbol=symbol,
                timeframe=timeframe,
                posterior=posterior,
                observed_at=observed_at,
                position_is_flat=position_is_flat,
                position_owner_strategy_id=(
                    exec_state.current_strategy_id if not position_is_flat else None
                ),
            )

            # Forecast
            forecast_result = self.runtime.forecast(decision, observation)

            # Convert to simulator intents
            sim_intents: list[SimOrderIntent] = []
            if forecast_result.executable and forecast_result.forecast:
                intents = _forecast_to_order_intent(
                    forecast_result.forecast,
                    decision,
                    observation,
                    exec_state,
                    self.config,
                )
                for intent in intents:
                    sim_intent = SimOrderIntent(
                        order_id=intent.order_id,
                        side=SimSide.BUY if intent.side == "buy" else SimSide.SELL,
                        order_type=SimOrderType.MARKET
                        if intent.order_type == "market"
                        else SimOrderType.LIMIT,
                        quantity=intent.quantity,
                        metadata=intent.metadata,
                    )
                    sim_intents.append(sim_intent)

                    # Update execution state
                    if intent.metadata.get("action") == "close_for_switch":
                        exec_state.position_quantity = 0
                    elif intent.metadata.get("action") == "open_new":
                        exec_state.position_quantity = intent.quantity
                        exec_state.position_entry_price = row["open"]
                    elif intent.metadata.get("action") == "reduce_exposure":
                        exec_state.position_quantity -= intent.quantity

                    exec_state.current_strategy_id = decision.chosen_strategy_id
                    exec_state.current_policy_id = decision.chosen_policy_id

            return sim_intents

        # Run simulation
        result = engine.run(provider, bars_per_year=bars_per_year)

        # Update final equity from ledger
        final_equity = engine.ledger.equity_at_mid(float(df["close"][-1]))
        exec_state.equity = final_equity
        exec_state.cash_quote = engine.ledger.cash_quote

        return result

    def _signal_to_posterior(
        self, signal: Any, observed_at: datetime
    ) -> Any:
        """Convert regime signal to RegimePosterior."""
        from trading_agent.ml.regime_detection import RegimePosterior
        from trading_agent.online_learning.regime_detector import MarketRegime

        REGIME_TO_POLICY_REGIME = {
            MarketRegime.TRENDING_UP: "trend",
            MarketRegime.TRENDING_DOWN: "trend",
            MarketRegime.SIDEWAYS: "mean_reversion",
            MarketRegime.VOLATILE: "high_vol",
            MarketRegime.UNKNOWN: "other",
        }

        probs = self.regime_detector.get_regime_probabilities(
            signal.recent_df if hasattr(signal, "recent_df") else signal
        )

        buckets = {
            "trend": 0.0,
            "mean_reversion": 0.0,
            "high_vol": 0.0,
            "crisis": 0.0,
            "other": 0.0,
        }
        for regime, prob in probs.items():
            mapped = REGIME_TO_POLICY_REGIME.get(regime, "other")
            buckets[mapped] += prob

        total = sum(buckets.values())
        if total <= 0:
            buckets = {k: 0.2 for k in buckets}
            total = 1.0
        normalized = {k: v / total for k, v in buckets.items()}

        return RegimePosterior(
            p_trend=normalized["trend"],
            p_mean_reversion=normalized["mean_reversion"],
            p_high_vol=normalized["high_vol"],
            p_crisis=normalized["crisis"],
            p_other=normalized["other"],
            model_id="online-regime-detector-v1",
            fitted_start=observed_at,
            fitted_end=observed_at,
            generated_at=observed_at,
            ood_score=0.1,
        )


__all__ = [
    "AdaptiveExecutionBridge",
    "AdaptiveSimulatorBridge",
    "AdaptiveExecutionConfig",
    "AdaptiveExecutionState",
    "AdaptiveExecutionResult",
    "_forecast_to_order_intent",
]