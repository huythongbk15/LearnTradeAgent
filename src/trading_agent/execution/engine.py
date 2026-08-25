"""
Execution Engine — canonical interface to trade.

Kết nối Phase 2 (signals) với Phase 3 (execution) qua canonical pipeline:
AgentMessage → DecisionAuthority → ExposureAuthority → ExecutionAuthority
→ OrderPlanner → OrderPermission → ExecutionLifecycle → BrokerGateway.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.config.loader import config
from trading_agent.execution.paper_exchange import STATE_DIR, PaperExchange
from trading_agent.execution.canonical import (
    BrokerGateway,
    OrderPlanner,
    EnrichedMarketObservation,
    CurrentPortfolioState,
    MarketPrice,
    InstrumentRules,
    ProtectionPlan,
    ProtectionState,
    ProtectionQuantityMode,
)
from trading_agent.execution.lifecycle.lifecycle import IntentStatus
from trading_agent.execution.canonical.broker_gateway import (
    BrokerSubmitState,
    CancelEvidence,
    CancelState,
)
from trading_agent.execution.canonical.adapters import PaperExecutionAdapter
from trading_agent.execution.application import (
    CanonicalExecutionService,
)
from trading_agent.execution.canonical.order_planner import (
    OrderPlanningStatus,
)
from trading_agent.execution.lifecycle import (
    ExecutionLifecycle,
    ExecutionEventStore,
    ExecutionHealth,
    TrustedPrice,
    PortfolioRiskSnapshot,
)
from trading_agent.execution.lifecycle.lifecycle import EmergencyReduceRequest
from trading_agent.execution.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

# ── Authority Chain (Milestone B) ────────────────────────────────────
from trading_agent.authority import (
    DecisionAuthority,
    DecisionInput,
    ExposureAuthority,
    ExposureValidationInput,
    ExecutionAuthority,
    ExecutionValidationInput,
    TargetExposure,
    get_authority_config,
    AuthorityConfig,
    PortfolioAllocator,
    AllocationRequest,
    RuntimeStrategyResolver,
    StrategyRuntime,
    PromotionStateStore,
)
from trading_agent.authority.config import Environment
from trading_agent.authority.causation import CausationChain
from trading_agent.authority.portfolio import PortfolioSnapshot
from trading_agent.config.loader import config as legacy_config
from trading_agent.execution.batch_models import (
    FinalizedPairDecision,
    is_execution_barrier,
    MarketDataInput,
    PairOrderPlan,
    PairPreparedDecision,
    PlannedAction,
    PlannedSubmissionOutcome,
)
from trading_agent.execution.canonical.risk_decision import (
    UnifiedRiskDecision,
)

logger = logging.getLogger(__name__)

# ── Graceful shutdown handling ────────────────────────────────────────

_shutdown_handlers: list[Callable[[], None]] = []
_shutdown_lock = threading.Lock()
_shutdown_initiated = False


def register_shutdown_handler(handler: Callable[[], None]) -> None:
    """Register a function to be called on graceful shutdown (SIGTERM/SIGINT)."""
    with _shutdown_lock:
        _shutdown_handlers.append(handler)


def _run_shutdown_handlers() -> None:
    """Execute all registered shutdown handlers."""
    global _shutdown_initiated
    with _shutdown_lock:
        if _shutdown_initiated:
            return
        _shutdown_initiated = True
        handlers = list(_shutdown_handlers)
        _shutdown_handlers.clear()

    for handler in handlers:
        try:
            handler()
        except Exception as e:
            logger.error(f"Shutdown handler error: {e}", exc_info=True)


def _signal_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    _run_shutdown_handlers()
    sys.exit(0)


def setup_graceful_shutdown() -> None:
    """Install signal handlers for SIGTERM and SIGINT."""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    logger.debug("Graceful shutdown handlers installed (SIGTERM, SIGINT)")


def _timeframe_duration_seconds(timeframe: str) -> int:
    """Parse a timeframe string ('15m', '1h', '4h', '1d') to seconds."""
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    tf = str(timeframe).lower().strip()
    if len(tf) < 2 or tf[-1] not in units:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    try:
        amount = int(tf[:-1])
    except ValueError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}") from exc
    if amount <= 0:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    return amount * units[tf[-1]]


class ExecutionEngine:
    """Canonical execution engine.

    Currently supports paper trading only (safe, no real money).
    All capital-changing orders flow through the canonical pipeline:
    AgentMessage → DecisionAuthority → ExposureAuthority → ExecutionAuthority
    → OrderPlanner → OrderPermission → ExecutionLifecycle → BrokerGateway.
    """

    def __init__(
        self,
        exchange_name: str | None = None,
        initial_capital: float | None = None,
        commission: float | None = None,
        slippage: float | None = None,
        *,
        exchange: PaperExchange | None = None,
        store: Any | None = None,
        instrument_rules: InstrumentRules | None = None,
        state_dir: str | Path | None = None,
        event_store_path: str | Path | None = None,
        allow_backtest_new_exposure: bool | None = None,
        paper_price_persist_interval: int = 1,
        disable_paper_telemetry: bool = False,
        authority_config: AuthorityConfig | None = None,
        promotion_store: PromotionStateStore | None = None,
        artifact_store: Any | None = None,
    ):
        # ── Constructor strictness: validate inputs early ─────────────
        if exchange is not None:
            # When an exchange is injected, we still need a name for telemetry.
            resolved_exchange_name = exchange_name or getattr(
                exchange, "exchange_name", "injected"
            )
        else:
            resolved_exchange_name = exchange_name or legacy_config.default_exchange
        if not isinstance(resolved_exchange_name, str) or not resolved_exchange_name:
            raise ValueError(
                f"exchange_name must be a non-empty string, got {exchange_name!r}"
            )
        self.exchange_name: str = resolved_exchange_name

        if initial_capital is not None:
            if not math.isfinite(initial_capital) or initial_capital <= 0:
                raise ValueError(
                    f"initial_capital must be finite and positive, got {initial_capital}"
                )
        if commission is not None:
            if not math.isfinite(commission) or commission < 0:
                raise ValueError(
                    f"commission must be finite and non-negative, got {commission}"
                )
        if slippage is not None:
            if not math.isfinite(slippage) or slippage < 0:
                raise ValueError(
                    f"slippage must be finite and non-negative, got {slippage}"
                )

        # ── Paper exchange (broker adapter) ───────────────────────────
        paper_exchange_kwargs: dict[str, Any] = {}
        if disable_paper_telemetry:
            paper_exchange_kwargs["telemetry"] = None
        self.exchange = exchange or PaperExchange(
            exchange_name=self.exchange_name,
            initial_balance=(
                config.initial_capital if initial_capital is None else initial_capital
            ),
            commission=legacy_config.commission if commission is None else commission,
            slippage=legacy_config.slippage if slippage is None else slippage,
            state_dir=state_dir or STATE_DIR,
            price_persist_interval=paper_price_persist_interval,
            **paper_exchange_kwargs,
        )

        # ── Canonical execution stack ─────────────────────────────────
        self.store = store or ExecutionEventStore(
            event_store_path or "data/execution/events.db"
        )
        if store is None:
            self.store.connect()
        # Use PaperExecutionAdapter wrapping PaperExchange
        paper_adapter = PaperExecutionAdapter(self.exchange)
        self.lifecycle = ExecutionLifecycle(
            self.store,
            price_source=lambda symbol: (
                TrustedPrice(
                    price=float(self.exchange._last_price_cache[symbol]),
                    exchange_timestamp=datetime.fromtimestamp(
                        self.exchange._last_price_timestamps[symbol], UTC
                    ),
                    received_at=datetime.now(UTC),
                )
                if symbol in self.exchange._last_price_cache
                else None
            ),
            inventory_source=self._inventory_source,
            portfolio_source=lambda symbol: self._build_portfolio_snapshot(symbol),
        )
        self.gateway = BrokerGateway(
            adapter=paper_adapter, store=self.store, lifecycle=self.lifecycle
        )
        self.planner: OrderPlanner | None
        self.execution_service: CanonicalExecutionService | None
        if instrument_rules is not None:
            self.planner = OrderPlanner(
                instrument_rules=instrument_rules,
                strategy_version="authority-chain-v1",
            )
            self.execution_service = CanonicalExecutionService(
                lifecycle=self.lifecycle,
                gateway=self.gateway,
                planner=self.planner,
            )
        else:
            self.planner = None
            self.execution_service = None

        # ── Authority Chain (Milestone B) ─────────────────────────────
        self.authority_config = authority_config or get_authority_config()
        self.decision_authority = DecisionAuthority(config=self.authority_config)
        self.exposure_authority = ExposureAuthority(config=self.authority_config)
        self.portfolio_allocator = PortfolioAllocator(config=self.authority_config)
        self.execution_authority = (
            ExecutionAuthority(
                lifecycle=self.lifecycle,
                gateway=self.gateway,
                planner=self.planner,
                config=self.authority_config,
            )
            if self.planner is not None
            else None
        )

        # ── Artifact-driven resolver (Milestone B) ────────────────────
        self.promotion_store = promotion_store
        self.artifact_store = artifact_store
        self.resolver = (
            RuntimeStrategyResolver(
                config=self.authority_config,
                promotion_store=promotion_store,
                artifact_store=artifact_store,
            )
            if promotion_store is not None
            else None
        )

        # Register graceful shutdown handler
        register_shutdown_handler(self._graceful_shutdown)

    def _build_portfolio_snapshot(self, symbol: str) -> PortfolioRiskSnapshot | None:
        """Build a trusted portfolio snapshot from exchange state."""
        try:
            with self.exchange._state_lock:
                position = self.exchange.get_position(symbol)
                position_quantity = position.quantity if position else 0.0
                available_quantity = position_quantity  # spot long-only: all available
                equity = self.exchange.get_total_equity()
                available_cash = self.exchange.get_balance("USDT")
                observed_at = datetime.now(UTC)
                source = "paper_exchange"
        except Exception as e:
            logger.warning(f"Failed to build portfolio snapshot for {symbol}: {e}")
            return None
        return PortfolioRiskSnapshot(
            symbol=symbol,
            position_quantity=position_quantity,
            available_quantity=available_quantity,
            equity=equity,
            available_cash=available_cash,
            observed_at=observed_at,
            source=source,
        )

    def _inventory_source(self, symbol: str, side: str) -> float:
        """Return broker-backed free spot inventory for lifecycle authorization."""
        if side.lower() != "sell":
            return 0.0
        snapshot = self._build_portfolio_snapshot(symbol)
        return snapshot.available_quantity if snapshot is not None else float("nan")

    # ── Milestone C: staged preparation/planning/submission ────────────
    #
    # execute_strategy() is now a THIN COMPOSITION of the same no-I/O stages
    # that MultiPairRuntime uses for batches. Single-pair == batch with N=1.

    def _prepare_from_runtime(
        self,
        strategy_runtime: StrategyRuntime,
        market_data: Any,
        observation: EnrichedMarketObservation | None,
    ) -> PairPreparedDecision:
        """Resolve → StrategyOutput → DecisionAuthority. NO allocation,
        NO exposure validation, NO broker I/O after this point in prepare."""
        symbol = strategy_runtime.symbol
        timeframe = strategy_runtime.timeframe

        def _no_action(status: str, signal: str = "HOLD") -> PairPreparedDecision:
            return PairPreparedDecision(
                symbol=symbol,
                timeframe=timeframe,
                artifact_id=strategy_runtime.artifact_id,
                strategy_name=strategy_runtime.strategy_name,
                observation=observation,
                strategy_output=None,
                risk_decision=None,
                signal=signal,
                prepare_status=status,
            )

        # Execute strategy to get signal
        strategy_output = strategy_runtime.execute(
            market_data=market_data,
            portfolio_state=None,  # Portfolio state injected by authority chain
            observation_id=observation.observation_id if observation else None,
            data_manifest_id=getattr(observation, "data_manifest_id", None),
            feature_artifact_id=getattr(observation, "feature_artifact_id", None),
            research_run_id=getattr(observation, "research_run_id", None),
        )

        signal_value = str(strategy_output.signal).upper()
        if signal_value == "HOLD":
            logger.info(f"Strategy {strategy_runtime.strategy_name}: HOLD — no action")
            return _no_action("hold")

        # Sync protective orders with actual positions
        self._sync_protective_orders()

        # Get current price
        price_info = self._get_current_price(symbol)
        if price_info is None:
            logger.warning(f"Cannot execute strategy: no price data for {symbol}")
            return _no_action("no_price")
        current_price, exchange_timestamp = price_info
        if current_price <= 0:
            logger.warning(f"Cannot execute strategy: no price data for {symbol}")
            return _no_action("no_price")

        # Observation: must come from market data layer
        if observation is None:
            logger.warning(
                "execute_strategy requires a market observation from the data layer"
            )
            return _no_action("bad_observation")
        if not observation.is_closed:
            logger.warning(
                f"Refusing to execute from unclosed observation "
                f"{observation.observation_id}"
            )
            return _no_action("bad_observation")

        # ── Build DecisionInput from StrategyOutput ──────────────────
        existing_pos = self.exchange.get_position(symbol)
        current_qty = existing_pos.quantity if existing_pos else 0.0
        current_notional = current_qty * current_price
        equity = self.exchange.get_total_equity()
        current_exposure = current_notional / equity if equity > 0 else 0.0
        available_cash = self.exchange.get_balance("USDT")

        decision_input = DecisionInput(
            strategy_output=strategy_output,  # Direct StrategyOutput path
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            current_exposure=current_exposure,
            equity=equity,
            available_cash=available_cash,
            portfolio_value=equity,
            observation_id=observation.observation_id if observation else None,
            regime=getattr(observation, "regime", None),
            volatility_pct=getattr(observation, "volatility_pct", None),
        )

        decision_output = self.decision_authority.decide(decision_input)
        risk_decision = UnifiedRiskDecision(
            decision_id=decision_output.risk_decision.decision_id,
            forecast_fingerprint=decision_output.risk_decision.forecast_fingerprint,
            model_artifact_id=(
                decision_output.risk_decision.model_artifact_id
                or strategy_runtime.artifact_id
            ),
            requested_target_exposure=(
                decision_output.risk_decision.requested_target_exposure
            ),
            allowed_target_exposure=decision_output.risk_decision.allowed_target_exposure,
            max_new_exposure=decision_output.risk_decision.max_new_exposure,
            reduce_only=decision_output.risk_decision.reduce_only,
            risk_level=decision_output.risk_decision.risk_level,
            reason_codes=decision_output.risk_decision.reason_codes,
            calibration_state=decision_output.risk_decision.calibration_state,
            calibration_artifact_id=(
                decision_output.risk_decision.calibration_artifact_id
            ),
            calibration_ece=decision_output.risk_decision.calibration_ece,
            ood_state=decision_output.risk_decision.ood_state,
            ood_score=decision_output.risk_decision.ood_score,
            regime_state=decision_output.risk_decision.regime_state,
            regime_entropy=decision_output.risk_decision.regime_entropy,
            interval_width=decision_output.risk_decision.interval_width,
            created_at=decision_output.risk_decision.created_at,
            metadata=decision_output.risk_decision.metadata,
            warnings=decision_output.risk_decision.warnings,
            authority_chain=decision_output.causation_chain.links,
        )
        target = TargetExposure(
            target_exposure_pct=decision_output.target_exposure.target_exposure_pct,
            max_new_exposure_pct=(decision_output.target_exposure.max_new_exposure_pct),
            reduce_only=decision_output.target_exposure.reduce_only,
            confidence=decision_output.target_exposure.confidence,
            authority_chain=decision_output.causation_chain.links,
        )

        total_portfolio_exposure = (
            sum(
                (pos.quantity * self.exchange._last_price_cache.get(pos.symbol, 0.0))
                / equity
                for pos in self.exchange.get_all_positions()
                if pos.is_active
            )
            if equity > 0
            else 0.0
        )

        return PairPreparedDecision(
            symbol=symbol,
            timeframe=timeframe,
            artifact_id=strategy_runtime.artifact_id,
            strategy_name=strategy_runtime.strategy_name,
            observation=observation,
            strategy_output=strategy_output,
            risk_decision=risk_decision,
            requested_target_exposure=target.target_exposure_pct,
            current_exposure=current_exposure,
            signal=signal_value,
            causation_chain=decision_output.causation_chain,
            current_price=current_price,
            equity=equity,
            available_cash=available_cash,
            current_quantity=current_qty,
            total_portfolio_exposure=total_portfolio_exposure,
            prepare_status="ok",
        )

    def prepare_promoted_strategy(
        self,
        symbol: str,
        timeframe: str,
        environment: str | Environment,
        market_data_input: MarketDataInput,
    ) -> PairPreparedDecision | None:
        """No-I/O preparation of ONE promoted binding (batch stage).

        Resolves the promoted artifact, runs the strategy, seeds the price,
        and runs DecisionAuthority. Performs NO allocation, NO exposure
        validation, NO order planning, NO broker I/O.

        Returns None when no eligible promotion record exists (fail-closed
        upstream: required-universe policy treats None as a failed binding).
        """
        if self.resolver is None:
            raise RuntimeError(
                "prepare_promoted_strategy requires RuntimeStrategyResolver "
                "(promotion_store + artifact_store at engine construction)"
            )
        env = (
            Environment(environment.lower())
            if isinstance(environment, str)
            else environment
        )
        strategy_runtime = self.resolver.resolve_for(symbol, timeframe, env)
        if strategy_runtime is None:
            logger.warning(
                f"No promoted strategy resolved for {symbol} {timeframe} {env.value}"
            )
            return None

        observation = self.build_observation(market_data_input)
        # Seed fresh price so DecisionAuthority sees a tradable quote
        try:
            close_price = float(market_data_input.data.tail(1).to_dicts()[0]["close"])
            self.update_prices({symbol: close_price})
        except Exception as e:
            logger.warning(
                "prepare_promoted_strategy[%s]: cannot seed price: %s", symbol, e
            )

        return self._prepare_from_runtime(
            strategy_runtime, market_data_input.data, observation
        )

    def build_observation(self, market_data_input: MarketDataInput) -> Any:
        """EnrichedMarketObservation from the LAST CLOSED bar, carrying REAL
        provenance from the MarketDataInput (never fabricated IDs)."""
        tail = market_data_input.data.tail(1).to_dicts()[0]
        observed_at = datetime.now(UTC)

        bar_ts = tail.get("timestamp")
        if isinstance(bar_ts, datetime):
            bar_close_at = bar_ts if bar_ts.tzinfo else bar_ts.replace(tzinfo=UTC)
        else:
            bar_close_at = observed_at

        return EnrichedMarketObservation(
            symbol=market_data_input.symbol,
            observed_at=observed_at,
            open=float(tail["open"]),
            high=float(tail["high"]),
            low=float(tail["low"]),
            close=float(tail["close"]),
            volume=float(tail.get("volume", 0.0)),
            features={},
            timeframe=market_data_input.timeframe,
            bar_close_at=bar_close_at,
            is_closed=True,
            data_manifest_id=market_data_input.data_manifest_id,
            feature_artifact_id=market_data_input.feature_artifact_id,
        )

    def build_allocation_request(
        self,
        prepared: PairPreparedDecision,
        snapshot: PortfolioSnapshot | None = None,
    ) -> AllocationRequest:
        """AllocationRequest for one prepared pair against SHARED truth.

        When ``snapshot`` is given (batch mode), equity/cash/exposures come
        from the authoritative shared PortfolioSnapshot; otherwise from the
        values captured at prepare time (single-pair parity path).
        """
        if snapshot is not None:
            equity = float(snapshot.equity)
            available_cash = float(snapshot.available_cash)
            total_portfolio_exposure = float(snapshot.gross_exposure)
            symbol_total: float | None = float(
                snapshot.symbol_exposures.get(prepared.symbol, 0.0)
            )
        else:
            equity = prepared.equity
            available_cash = prepared.available_cash
            total_portfolio_exposure = prepared.total_portfolio_exposure
            symbol_total = None  # falls back to current_exposure (legacy parity)

        return AllocationRequest(
            strategy_id=prepared.artifact_id,
            symbol=prepared.symbol,
            risk_decision=prepared.risk_decision,
            current_exposure=prepared.current_exposure,
            equity=equity,
            available_cash=available_cash,
            portfolio_exposure=total_portfolio_exposure,
            correlation_cluster=None,
            symbol_total_exposure=symbol_total,
            causation_chain=prepared.causation_chain,
        )

    def finalize_prepared_decision(
        self,
        prepared: PairPreparedDecision,
        *,
        approved_target: TargetExposure,
        combined_chain: CausationChain,
    ) -> FinalizedPairDecision | None:
        """ExposureAuthority validation of an ALLOCATED target (no broker I/O).

        ``approved_target`` carries the allocation result (single-pair:
        PortfolioAllocator.allocate; batch: allocate_batch via the target
        vector). Returns None when ExposureAuthority blocks the pair.
        """
        exposure_input = ExposureValidationInput(
            target_exposure=approved_target,
            symbol=prepared.symbol,
            strategy_id=prepared.artifact_id,
            current_exposure=prepared.current_exposure,
            portfolio_exposure=prepared.total_portfolio_exposure,
            strategy_exposure=prepared.current_exposure,
            equity=prepared.equity,
            available_cash=prepared.available_cash,
            correlation_exposure=0.0,
            causation_chain=combined_chain,
        )

        exposure_output = self.exposure_authority.validate(exposure_input)
        if not exposure_output.allowed:
            logger.warning(
                "Order blocked by ExposureAuthority [%s %s]: %s",
                prepared.symbol,
                prepared.timeframe,
                exposure_output.reason,
            )
            return None

        base_rd = prepared.risk_decision
        assert base_rd is not None
        risk_decision = UnifiedRiskDecision(
            decision_id=base_rd.decision_id,
            forecast_fingerprint=base_rd.forecast_fingerprint,
            model_artifact_id=base_rd.model_artifact_id,
            requested_target_exposure=base_rd.requested_target_exposure,
            allowed_target_exposure=exposure_output.allowed_target_exposure,
            max_new_exposure=exposure_output.allowed_max_new_exposure,
            reduce_only=base_rd.reduce_only,
            risk_level=base_rd.risk_level,
            reason_codes=base_rd.reason_codes,
            calibration_state=base_rd.calibration_state,
            calibration_artifact_id=base_rd.calibration_artifact_id,
            calibration_ece=base_rd.calibration_ece,
            ood_state=base_rd.ood_state,
            ood_score=base_rd.ood_score,
            regime_state=base_rd.regime_state,
            regime_entropy=base_rd.regime_entropy,
            interval_width=base_rd.interval_width,
            created_at=base_rd.created_at,
            metadata=base_rd.metadata,
            warnings=base_rd.warnings + exposure_output.warnings,
            authority_chain=exposure_output.causation_chain.links,
        )
        target = TargetExposure(
            target_exposure_pct=exposure_output.allowed_target_exposure,
            max_new_exposure_pct=exposure_output.allowed_max_new_exposure,
            reduce_only=risk_decision.reduce_only,
            confidence=approved_target.confidence,
            authority_chain=exposure_output.causation_chain.links,
        )

        no_change = abs(target.target_exposure_pct - prepared.current_exposure) < 1e-9
        if no_change:
            logger.info(
                "[%s %s] Target exposure equals current (%.4f) — no action",
                prepared.symbol,
                prepared.timeframe,
                prepared.current_exposure,
            )
        return FinalizedPairDecision(
            prepared=prepared,
            approved_target_exposure=target.target_exposure_pct,
            risk_decision=risk_decision,
            target=target,
            causation_chain=exposure_output.causation_chain,
            no_change=no_change,
        )

    def plan_pair_order(self, finalized: FinalizedPairDecision) -> PairOrderPlan:
        """Plan the order for one finalized pair. NO broker I/O."""
        prepared = finalized.prepared
        symbol = prepared.symbol
        timeframe = prepared.timeframe

        if finalized.no_change:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=None,
                idempotency_key=None,
                detail="target equals current exposure",
            )

        assert self.execution_service is not None
        assert self.planner is not None

        pair_rules = self.planner.rules_for(symbol)
        rule_id = getattr(pair_rules, "rule_id", None) or (
            getattr(pair_rules, "name", None)
        )
        if pair_rules is None:
            logger.error("No instrument rules registered for %s — cannot plan", symbol)
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.BLOCKED,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=None,
                idempotency_key=None,
                detail="missing instrument rules",
            )

        portfolio = CurrentPortfolioState(
            symbol=symbol,
            current_exposure=prepared.current_exposure,
            equity=prepared.equity,
            existing_quantity=prepared.current_quantity,
            available_cash=prepared.available_cash,
        )
        price = MarketPrice(
            symbol=symbol,
            mid=prepared.current_price,
            bid=prepared.current_price,
            ask=prepared.current_price,
            last=prepared.current_price,
        )

        # ── Convert authority TargetExposure to canonical TargetExposure ──
        from trading_agent.research.forecast import (
            TargetExposure as CanonicalTargetExposure,
        )

        canonical_target = CanonicalTargetExposure(
            symbol=symbol,
            exposure=finalized.target.target_exposure_pct,
            horizon=1,
            forecast_fingerprint=finalized.risk_decision.forecast_fingerprint,
            model_artifact_id=finalized.risk_decision.model_artifact_id,
            risk_decision_id=finalized.risk_decision.decision_id,
        )

        reservations_for_side = (
            0.0
            if prepared.signal == "SELL"
            else self.lifecycle.active_sell_reservations(symbol)
        )
        plan_result = self.execution_service.plan(
            target=canonical_target,
            risk_decision=finalized.risk_decision,
            observation=prepared.observation,
            portfolio=portfolio,
            price=price,
            existing_reservations=reservations_for_side,
        )
        if plan_result.status != OrderPlanningStatus.ORDER_REQUIRED:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=rule_id,
                idempotency_key=None,
                detail=f"planner status {plan_result.status.value}",
            )
        if plan_result.intent is None:
            return PairOrderPlan(
                symbol=symbol,
                timeframe=timeframe,
                action=PlannedAction.NO_ORDER,
                finalized=finalized,
                intent=None,
                intent_id=None,
                side=None,
                quantity=0.0,
                limit_price=None,
                instrument_rule_id=rule_id,
                idempotency_key=None,
                detail="planner produced no intent",
            )

        intent = plan_result.intent
        side_lower = intent.side.lower()
        action = (
            PlannedAction.REDUCTION if side_lower == "sell" else PlannedAction.INCREASE
        )
        return PairOrderPlan(
            symbol=symbol,
            timeframe=timeframe,
            action=action,
            finalized=finalized,
            intent=intent,
            intent_id=intent.intent_id,
            side=intent.side,
            quantity=float(intent.quantity),
            limit_price=getattr(intent, "limit_price", None),
            instrument_rule_id=rule_id,
            idempotency_key=getattr(intent, "idempotency_key", None),
        )

    def replan_pair_with_live_truth(self, plan: PairOrderPlan) -> PairOrderPlan:
        """Re-quantize a planned INCREASE against LIVE broker truth.

        Batch plans are built on ONE shared snapshot; sibling fills shift
        equity/cash (fees, slippage) before later BUYs reach the broker.
        The canonical invariant at authorize time checks against LIVE truth,
        so quantities must be re-floored on fresh numbers. Allocation caps,
        authority approvals and causation chains are NOT changed here — only
        the executable quantity.
        """
        finalized = plan.finalized
        if finalized is None or plan.action is not PlannedAction.INCREASE:
            return plan
        prepared = finalized.prepared

        live_equity = float(self.exchange.get_total_equity())
        live_cash = float(self.exchange.get_balance("USDT"))
        pos = self.exchange.get_position(prepared.symbol)
        live_qty = float(pos.quantity) if pos else 0.0
        price_info = self._get_current_price(prepared.symbol)
        if price_info is None:
            return plan
        live_price, _ts = price_info

        drift_eps = max(1e-9, abs(prepared.equity) * 1e-12)
        unchanged = (
            abs(live_equity - prepared.equity) <= drift_eps
            and abs(live_cash - prepared.available_cash) <= drift_eps
            and abs(live_qty - prepared.current_quantity) <= drift_eps
            and abs(live_price - prepared.current_price) <= drift_eps
        )
        if unchanged:
            return plan

        live_exposure = live_qty * live_price / live_equity if live_equity > 0 else 0.0
        total_exposure = self._portfolio_gross_exposure_ratio(live_equity)
        prepared2 = dataclasses.replace(
            prepared,
            current_price=live_price,
            equity=live_equity,
            available_cash=live_cash,
            current_quantity=live_qty,
            current_exposure=live_exposure,
            total_portfolio_exposure=total_exposure,
        )
        finalized2 = FinalizedPairDecision(
            prepared=prepared2,
            approved_target_exposure=finalized.approved_target_exposure,
            risk_decision=finalized.risk_decision,
            target=finalized.target,
            causation_chain=finalized.causation_chain,
            no_change=False,
        )
        logger.info(
            "[%s] replan against live truth: equity %.4f→%.4f",
            prepared.symbol,
            prepared.equity,
            live_equity,
        )
        return self.plan_pair_order(finalized2)

    def _portfolio_gross_exposure_ratio(self, equity: float) -> float:
        """Gross open exposure / equity from exchange positions."""
        if equity <= 0:
            return 0.0
        gross = 0.0
        for p in self.exchange.get_all_positions():
            if not p.is_active or p.quantity <= 0:
                continue
            px = float(self.exchange._last_price_cache.get(p.symbol, 0.0))
            gross += p.quantity * px / equity
        return gross

    def submit_planned_order(self, plan: PairOrderPlan) -> PlannedSubmissionOutcome:
        """THE ONLY stage that performs broker I/O for one planned order.

        Mirrors the legacy single-pair submission sequence exactly:
        ExecutionAuthority validate → sell-protection cancel → gateway result
        → fill handling → BUY protective stop.
        """
        finalized = plan.finalized
        assert finalized is not None
        prepared = finalized.prepared
        intent = plan.intent
        assert intent is not None
        symbol = prepared.symbol

        assert self.planner is not None
        assert self.execution_authority is not None
        if self.execution_service is None:
            raise RuntimeError(
                "submit_planned_order requires execution_service "
                "(engine must be constructed with instrument_rules)"
            )

        pair_rules = self.planner.rules_for(symbol)

        def _outcome(**kwargs: Any) -> PlannedSubmissionOutcome:
            return PlannedSubmissionOutcome(plan=plan, **kwargs)

        exec_input = ExecutionValidationInput(
            intent=intent,
            observation=prepared.observation,
            portfolio_state=CurrentPortfolioState(
                symbol=symbol,
                current_exposure=prepared.current_exposure,
                equity=prepared.equity,
                existing_quantity=prepared.current_quantity,
                available_cash=prepared.available_cash,
            ),
            price=MarketPrice(
                symbol=symbol,
                mid=prepared.current_price,
                bid=prepared.current_price,
                ask=prepared.current_price,
                last=prepared.current_price,
            ),
            instrument_rules=pair_rules,
            existing_reservations=(
                0.0
                if prepared.signal == "SELL"
                else self.lifecycle.active_sell_reservations(symbol)
            ),
            causation_chain=finalized.causation_chain,
            risk_decision=finalized.risk_decision,
        )

        exec_output = self.execution_authority.execute(exec_input)
        if not exec_output.allowed:
            logger.warning(
                "Order blocked by ExecutionAuthority [%s]: %s",
                symbol,
                exec_output.reason,
            )
            return _outcome(
                order=None,
                submit_state="BLOCKED_AUTHORITY",
                barrier=False,
                submitted=False,
            )

        # ── Protective order handling (sell signals) ────────────────
        canceled_protection_intents: list[str] = []
        if intent.side.lower() == "sell":
            cancel_ok, canceled_protection_intents = self._cancel_resting_protection(
                symbol
            )
            if not cancel_ok:
                # Legacy parity: submission already happened inside
                # ExecutionAuthority; report it without an Order wrapper so
                # lifecycle reconciliation owns the follow-up.
                broker_result_pre = exec_output.broker_result
                return _outcome(
                    order=self._result_to_order(
                        broker_result_pre, symbol, intent.side, intent.quantity
                    ),
                    submit_state=str(
                        getattr(
                            broker_result_pre.state, "value", broker_result_pre.state
                        )
                    ),
                    barrier=is_execution_barrier(broker_result_pre),
                    submitted=True,
                )

        broker_result = exec_output.broker_result

        order = self._result_to_order(
            broker_result, symbol, intent.side, intent.quantity
        )

        # Check if fill was received
        order_state = self.lifecycle.state.orders.get(intent.intent_id)
        fill_received = order_state is not None and order_state.filled_size > 1e-12

        protection_submitted = False
        if (
            intent.side.lower() == "sell"
            and canceled_protection_intents
            and not fill_received
        ):
            self._mark_protection_gap(
                symbol,
                canceled_protection_intents,
                "exit was not filled after protective cancellation",
            )
        if fill_received and intent.side.lower() == "buy":
            position = self.exchange.get_position(symbol)
            protected_quantity = (
                position.quantity if position and position.is_active else 0.0
            )
            if protected_quantity <= 0:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker fill did not produce a positive protected quantity",
                )
                return _outcome(
                    order=order,
                    submit_state=str(
                        getattr(broker_result.state, "value", broker_result.state)
                    ),
                    barrier=is_execution_barrier(broker_result),
                    submitted=True,
                    protection_submitted=False,
                )
            current_price = prepared.current_price
            plan_protection = ProtectionPlan(
                plan_id=f"prot_{intent.intent_id}",
                model_risk_decision_id=finalized.risk_decision.decision_id,
                symbol=intent.symbol,
                stop_type="stop_loss",
                stop_trigger=current_price * 0.95,
                take_profit=current_price * 1.10,
                state=ProtectionState.PROTECTION_REQUIRED,
                quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
                protected_quantity=protected_quantity,
            )
            protective_event = self.lifecycle.create_protective_order(
                symbol=plan_protection.symbol,
                kind=plan_protection.stop_type,
                trigger_price=plan_protection.stop_trigger,
                parent_intent_id=intent.intent_id,
            )
            protection_intent_id = f"{protective_event.aggregate_id}_submit"
            protection_result = self.execution_service.emergency_protection(
                EmergencyReduceRequest(
                    intent_id=protection_intent_id,
                    symbol=plan_protection.symbol,
                    side="sell",
                    quantity=plan_protection.protected_quantity,
                    reason="PROTECTIVE_STOP",
                    parent_intent_id=intent.intent_id,
                    idempotency_key=protection_intent_id,
                    metadata={
                        "order_type": "stop",
                        "stop_price": plan_protection.stop_trigger,
                        "time_in_force": "gtc",
                    },
                ),
                correlation_id=protection_intent_id,
            )
            if protection_result.success and protection_result.evidence:
                self.lifecycle.acknowledge_protective_order(
                    protective_order_id=protective_event.aggregate_id,
                    evidence=protection_result.evidence,
                )
                protection_submitted = True
            else:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker did not acknowledge the required protective order",
                )

        return _outcome(
            order=order,
            submit_state=str(
                getattr(broker_result.state, "value", broker_result.state)
            ),
            barrier=is_execution_barrier(broker_result),
            submitted=True,
            protection_submitted=protection_submitted,
        )

    # ── Execute signals from Phase 2 agents ────────────────────────────

    def execute_strategy(
        self,
        strategy_runtime: StrategyRuntime,
        market_data: Any,
        observation: EnrichedMarketObservation | None = None,
    ) -> list[Order]:
        """Execute a promoted strategy through the full authority chain.

        N=1 composition over the SAME staged APIs MultiPairRuntime uses:
        _prepare_from_runtime → build_allocation_request → allocate
        → finalize_prepared_decision → plan_pair_order → submit_planned_order
        """
        if self.execution_service is None:
            raise RuntimeError(
                "execute_strategy requires instrument_rules to be provided at engine construction"
            )
        if self.resolver is None:
            raise RuntimeError(
                "execute_strategy requires RuntimeStrategyResolver (promotion_store + artifact_store)"
            )

        # Stage 1: resolve + StrategyOutput + DecisionAuthority (no I/O)
        prepared = self._prepare_from_runtime(
            strategy_runtime, market_data, observation
        )
        if prepared.prepare_status != "ok":
            return []

        # Stage 2: single-pair allocation (batch runtime uses allocate_batch)
        allocation_request = self.build_allocation_request(prepared)
        allocation_result = self.portfolio_allocator.allocate(allocation_request)
        if allocation_result.allocation_pct <= 0:
            logger.info(
                f"PortfolioAllocator returned zero allocation for "
                f"{prepared.symbol}: {allocation_result.reason}"
            )
            return []

        approved_target = allocation_result.target_exposure
        combined_chain = CausationChain(
            links=prepared.causation_chain.links
            + allocation_result.causation_chain.links
        )

        # Stage 3: ExposureAuthority validation of the allocated target
        finalized = self.finalize_prepared_decision(
            prepared,
            approved_target=approved_target,
            combined_chain=combined_chain,
        )
        if finalized is None:
            return []

        # Stage 4: planning (no broker I/O)
        plan = self.plan_pair_order(finalized)
        if plan.action not in (PlannedAction.REDUCTION, PlannedAction.INCREASE):
            return []

        # Stage 5: THE broker I/O step (+ inline post-fill protection)
        outcome = self.submit_planned_order(plan)
        return [outcome.order] if outcome.order is not None else []

    # ── Legacy adapter: AgentMessage → StrategyRuntime ──────────────

    def execute_signal(
        self, signal: AgentMessage, observation: EnrichedMarketObservation | None = None
    ) -> list[Order]:
        """Legacy adapter: Execute a trading signal from the multi-agent system.

        This method is DEPRECATED. Use execute_strategy() with a resolved
        StrategyRuntime for artifact-driven execution.

        Takes the final ``Trader`` agent signal and converts it to orders
        through the authority chain pipeline.
        """
        if self.execution_service is None:
            raise RuntimeError(
                "execute_signal requires instrument_rules to be provided at engine construction"
            )
        if self.resolver is None:
            raise RuntimeError(
                "execute_signal requires RuntimeStrategyResolver (promotion_store + artifact_store)"
            )

        signal_str = signal.signal.upper()
        orders: list[Order] = []

        if signal_str == "HOLD":
            logger.info("Signal: HOLD — no action")
            return orders

        # Sync protective orders
        self._sync_protective_orders()

        symbol = signal.details.get("symbol") if signal.details else None
        if not isinstance(symbol, str) or not symbol:
            logger.warning("Cannot execute: signal is missing an explicit symbol")
            return orders

        # Resolve strategy for this symbol/timeframe/environment
        env = self.authority_config.environment
        timeframe = signal.details.get("timeframe", "1h") if signal.details else "1h"

        strategy_runtime = self.resolver.resolve_for(symbol, timeframe, env)
        if strategy_runtime is None:
            logger.warning(
                f"No promoted strategy resolved for {symbol} {timeframe} {env.value}"
            )
            return orders

        # Execute via new authority-driven pipeline
        market_data = signal.details.get("market_data") if signal.details else None
        if market_data is None:
            logger.warning("execute_signal: signal.details missing market_data")
            return orders

        return self.execute_strategy(strategy_runtime, market_data, observation)

    @staticmethod
    def _is_protective_intent(intent_id: str) -> bool:
        return intent_id.startswith("prot_") and intent_id.endswith("_submit")

    def _protective_intents(self, symbol: str | None = None):
        return [
            (intent_id, order_state)
            for intent_id, order_state in list(self.lifecycle.state.orders.items())
            if self._is_protective_intent(intent_id)
            and (symbol is None or order_state.symbol == symbol)
            and order_state.remaining_reserved_quantity > 1e-12
        ]

    def _record_protective_fill(self, intent_id: str, broker_order_id: str) -> bool:
        """Record an asynchronous paper stop fill in the canonical lifecycle."""
        order_state = self.lifecycle.state.orders.get(intent_id)
        if order_state is None:
            return False
        try:
            fact = self.gateway.fetch_order(
                broker_order_id,
                correlation_id=f"reconcile-{intent_id}",
            )
        except Exception as exc:
            logger.error("Failed to fetch protective order %s: %s", intent_id, exc)
            return False
        status = str(fact.get("status", "")).lower()
        filled_total = float(fact.get("filled_quantity", 0.0) or 0.0)
        fill_delta = min(
            order_state.remaining,
            max(0.0, filled_total - order_state.filled_size),
        )
        if fill_delta <= 1e-12:
            return status != "filled" or order_state.remaining <= 1e-12
        raw = fact.get("raw_response") or {}
        fill_price = float(
            raw.get("avg_fill_price")
            or fact.get("price")
            or self.exchange._last_price_cache.get(order_state.symbol, 0.0)
            or 0.0
        )
        if not math.isfinite(fill_price) or fill_price <= 0:
            logger.error("Protective fill %s has no valid fill price", intent_id)
            return False
        try:
            self.lifecycle.receive_fill(
                intent_id,
                size=fill_delta,
                price=fill_price,
            )
        except Exception as exc:
            logger.error("Failed to record protective fill %s: %s", intent_id, exc)
            return False
        updated = self.lifecycle.state.orders[intent_id]
        return status != "filled" or updated.remaining <= 1e-12

    def _mark_protection_gap(
        self,
        symbol: str,
        intent_ids: list[str],
        reason: str,
    ) -> None:
        position = self.exchange.get_position(symbol)
        if not position or not position.is_active or position.quantity <= 0:
            return
        self.lifecycle.state.execution_health = ExecutionHealth.PROTECTION_GAP
        for intent_id in intent_ids:
            self.lifecycle.require_manual_intervention(intent_id, reason=reason)

    def _cancel_resting_protection(self, symbol: str) -> tuple[bool, list[str]]:
        """Cancel protective orders using typed broker evidence."""
        self._sync_protective_orders(symbol)
        canceled_intents: list[str] = []
        for intent_id, order_state in self._protective_intents(symbol):
            broker_order_id = (
                order_state.exchange_order_id or order_state.broker_order_id
            )
            if not broker_order_id:
                self.lifecycle.require_manual_intervention(
                    intent_id,
                    reason="protective order has no broker identity during cancel",
                )
                self._mark_protection_gap(
                    symbol,
                    canceled_intents,
                    "protective cancellation lacked broker identity",
                )
                return False, canceled_intents
            try:
                if order_state.status != IntentStatus.CANCEL_REQUESTED:
                    self.lifecycle.request_cancel(
                        intent_id,
                        reason="explicit_exit_signal",
                    )
                cancel_result = self.gateway.cancel(
                    broker_order_id,
                    correlation_id=f"cancel-{intent_id}",
                    symbol=symbol,
                )
            except Exception as exc:
                self.lifecycle.require_manual_intervention(
                    intent_id,
                    reason=f"protective cancellation failed: {exc}",
                )
                self._mark_protection_gap(
                    symbol,
                    canceled_intents,
                    "protective cancellation raised",
                )
                return False, canceled_intents
            evidence = cancel_result.evidence
            if evidence is None:
                self.lifecycle.require_manual_intervention(
                    intent_id,
                    reason=cancel_result.error
                    or "protective cancel returned no broker evidence",
                )
                self._mark_protection_gap(
                    symbol,
                    canceled_intents,
                    "protective cancellation was unknown",
                )
                return False, canceled_intents
            if evidence.state == CancelState.FILLED:
                if not self._record_protective_fill(intent_id, broker_order_id):
                    self.lifecycle.require_manual_intervention(
                        intent_id,
                        reason="filled-during-cancel lacked complete fill evidence",
                    )
                    return False, canceled_intents
                continue
            if evidence.state in {
                CancelState.CANCELED,
                CancelState.REJECTED,
                CancelState.EXPIRED,
            }:
                self.lifecycle.confirm_cancel(intent_id, evidence)
                canceled_intents.append(intent_id)
                continue
            self.lifecycle.require_manual_intervention(
                intent_id,
                reason=f"protective cancel is non-terminal: {evidence.state.value}",
            )
            self._mark_protection_gap(
                symbol,
                canceled_intents,
                "protective cancel remained non-terminal",
            )
            return False, canceled_intents
        return True, canceled_intents

    def _sync_protective_orders(self, symbol: str | None = None) -> None:
        """Reconcile asynchronous protective broker facts into lifecycle state."""
        terminal_cancel_states = {
            "canceled": CancelState.CANCELED,
            "cancelled": CancelState.CANCELED,
            "rejected": CancelState.REJECTED,
            "expired": CancelState.EXPIRED,
        }
        for intent_id, order_state in self._protective_intents(symbol):
            broker_order_id = (
                order_state.exchange_order_id or order_state.broker_order_id
            )
            if not broker_order_id:
                if order_state.status != IntentStatus.MANUAL:
                    self.lifecycle.require_manual_intervention(
                        intent_id,
                        reason="protective order cannot be reconciled without broker identity",
                    )
                continue
            try:
                fact = self.gateway.fetch_order(
                    broker_order_id,
                    correlation_id=f"reconcile-{intent_id}",
                )
            except Exception as exc:
                if order_state.status != IntentStatus.MANUAL:
                    self.lifecycle.require_manual_intervention(
                        intent_id,
                        reason=f"protective broker lookup failed: {exc}",
                    )
                continue
            status = str(fact.get("status", "")).lower()
            if status in {"partially_filled", "filled"}:
                if not self._record_protective_fill(intent_id, broker_order_id):
                    self.lifecycle.require_manual_intervention(
                        intent_id,
                        reason="protective fill reconciliation lacked valid evidence",
                    )
                continue
            cancel_state = terminal_cancel_states.get(status)
            if cancel_state is None:
                if (
                    status not in {"pending", "open"}
                    and order_state.status != IntentStatus.MANUAL
                ):
                    self.lifecycle.require_manual_intervention(
                        intent_id,
                        reason=f"unrecognized protective broker status: {status or 'missing'}",
                    )
                continue
            if order_state.status == IntentStatus.MANUAL:
                continue
            if order_state.status != IntentStatus.CANCEL_REQUESTED:
                self.lifecycle.request_cancel(
                    intent_id,
                    reason="broker_terminal_reconciliation",
                )
            self.lifecycle.confirm_cancel(
                intent_id,
                CancelEvidence(
                    broker_order_id=broker_order_id,
                    state=cancel_state,
                    venue=str(fact.get("venue") or "paper"),
                    confirmed_at=datetime.now(UTC).isoformat(),
                    source="RECONCILIATION",
                    raw_response=dict(fact.get("raw_response") or {}),
                ),
            )

    def _graceful_shutdown(self) -> None:
        """Called on SIGTERM/SIGINT to close positions and persist state."""
        logger.info("Graceful shutdown: closing all positions...")
        try:
            self.close_all(reason="graceful_shutdown")
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")

    # ── Price feed ─────────────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]):
        """Update price data for internal tracking."""
        self.exchange.update_prices(prices)
        self._sync_protective_orders()

    def update_market_price(
        self,
        symbol: str,
        price: float,
        data_timestamp: datetime,
        timeframe: str,
    ) -> None:
        """Seed the exchange price cache from an OHLCV bar timestamp.

        Timestamp-guarded (fail-closed):
        - ``data_timestamp`` is the OPEN time of the last closed candle.
          If close (= open + duration) is in the future → "incomplete" candle.
        - If the candle closed too long ago (> 2× its duration) → "stale".
        Only then is the price pushed into the exchange cache.

        This restores the guard that was lost when the canonical engine
        replaced the legacy feed API; callers (CLI live paths) pass the
        orchestrator's data timestamp so stale/incomplete bars can never
        silently become fill prices.
        """
        if not isinstance(price, (int, float)) or not math.isfinite(float(price)):
            raise ValueError(f"invalid price for {symbol}: {price!r}")
        duration_s = _timeframe_duration_seconds(timeframe)
        if not isinstance(data_timestamp, datetime):
            raise ValueError(
                f"data_timestamp for {symbol} must be a datetime, "
                f"got {type(data_timestamp).__name__}"
            )
        ts = (
            data_timestamp
            if data_timestamp.tzinfo is not None
            else data_timestamp.replace(tzinfo=UTC)
        )
        bar_close_at = ts + timedelta(seconds=duration_s)
        now = datetime.now(UTC)

        if bar_close_at > now:
            raise ValueError(
                f"incomplete candle for {symbol} {timeframe}: "
                f"closes at {bar_close_at.isoformat()} in the future"
            )
        staleness_limit = timedelta(seconds=2 * duration_s)
        if now - bar_close_at > staleness_limit:
            raise ValueError(
                f"stale candle for {symbol} {timeframe}: closed at "
                f"{bar_close_at.isoformat()}, older than {staleness_limit}"
            )

        self.exchange.update_prices({symbol: float(price)})
        self._sync_protective_orders()

    # ── Position management ────────────────────────────────────────────

    def close_all(self, reason: str = "manual") -> list[Order]:
        """Close all open positions via canonical lifecycle emergency reduce."""
        if self.execution_service is None:
            raise RuntimeError(
                "close_all requires instrument_rules to be provided at engine construction"
            )
        orders: list[Order] = []
        for pos in self.exchange.get_all_positions():
            if pos.quantity <= 0:
                continue
            symbol = pos.symbol
            price_info = self._get_current_price(symbol)
            if price_info is None:
                continue
            current_price, _ = price_info
            # Use canonical emergency reduce through lifecycle
            emergency = EmergencyReduceRequest(
                intent_id=f"emergency-close-{symbol}-{uuid.uuid4().hex}",
                symbol=symbol,
                side="sell",
                quantity=pos.quantity,
                reason=reason,
                metadata={"order_type": "market", "time_in_force": "gtc"},
            )
            try:
                result = self.execution_service.emergency_close(emergency).result
                if result.state == BrokerSubmitState.FILLED and result.broker_order_id:
                    raw = result.raw_response or {}
                    orders.append(
                        Order(
                            id=result.broker_order_id,
                            symbol=symbol,
                            side=OrderSide.SELL,
                            type=OrderType.MARKET,
                            amount=pos.quantity,
                            status=OrderStatus.FILLED,
                            filled_amount=float(
                                raw.get("filled_qty", raw.get("filled_amount", 0)) or 0
                            ),
                            avg_fill_price=float(
                                raw.get("avg_fill_price", raw.get("price", 0)) or 0
                            ),
                        )
                    )
            except Exception as e:
                logger.error(f"Emergency reduce failed for {symbol}: {e}")
        return orders

    def close_position(self, symbol: str, reason: str = "manual") -> Order | None:
        """Close a single position via canonical lifecycle emergency reduce."""
        if self.execution_service is None:
            raise RuntimeError(
                "close_position requires instrument_rules to be provided at engine construction"
            )
        pos = self.exchange.get_position(symbol)
        if not pos or not pos.is_active or pos.quantity <= 0:
            return None
        price_info = self._get_current_price(symbol)
        if price_info is None:
            return None
        current_price, _ = price_info
        emergency = EmergencyReduceRequest(
            intent_id=f"emergency-close-{symbol}-{uuid.uuid4().hex}",
            symbol=symbol,
            side="sell",
            quantity=pos.quantity,
            reason=reason,
            metadata={"order_type": "market", "time_in_force": "gtc"},
        )
        try:
            result = self.execution_service.emergency_close(emergency).result
            if result.state == BrokerSubmitState.FILLED and result.broker_order_id:
                raw = result.raw_response or {}
                return Order(
                    id=result.broker_order_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    amount=pos.quantity,
                    status=OrderStatus.FILLED,
                    filled_amount=float(
                        raw.get("filled_qty", raw.get("filled_amount", 0)) or 0
                    ),
                    avg_fill_price=float(
                        raw.get("avg_fill_price", raw.get("price", 0)) or 0
                    ),
                )
        except Exception as e:
            logger.error(f"Emergency reduce failed for {symbol}: {e}")
        return None

    def get_summary(self) -> dict[str, Any]:
        """Get current portfolio summary."""
        positions = self.exchange.get_all_positions()
        return {
            "total_equity": self.exchange.get_total_equity(),
            "cash": self.exchange.get_balance("USDT"),
            "open_positions": len([p for p in positions if p.is_active]),
            "open_orders": len(self.exchange.get_open_orders()),
            "total_trades": len(self.exchange.trades),
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_current_price(self, symbol: str) -> tuple[float, datetime] | None:
        """Return (price, exchange_timestamp) from live ticker or price cache.

        Strict: only returns a price when the exchange-provided timestamp is
        available. Fabricating ``datetime.now(UTC)`` would bypass the freshness
        invariant and is not allowed.
        """
        # Prefer live ticker from adapter; fall back to simulator price cache.
        try:
            get_ticker = getattr(self.exchange, "get_ticker", None)
            if not callable(get_ticker):
                raise AttributeError("exchange has no live ticker API")
            ticker = get_ticker(symbol)
            price = ticker.get("last") or ticker.get("price")
            if price is not None:
                ts = ticker.get("timestamp")
                if ts is not None:
                    if isinstance(ts, datetime):
                        exchange_ts = ts
                    else:
                        exchange_ts = datetime.fromtimestamp(float(ts), UTC)
                    return float(price), exchange_ts
                # No timestamp from live adapter — reject to avoid stale data
                logger.debug(
                    "Ticker for %s missing timestamp; rejecting as stale", symbol
                )
                return None
        except Exception:
            pass
        try:
            price = float(self.exchange._last_price_cache[symbol])
            ts = self.exchange._last_price_timestamps.get(symbol)
            if ts is not None:
                exchange_ts = datetime.fromtimestamp(float(ts), UTC)
                return price, exchange_ts
            # No cached timestamp — reject to avoid stale data
            logger.debug(
                "Cached price for %s missing timestamp; rejecting as stale", symbol
            )
            return None
        except Exception:
            return None

    # ── Canonical One-Call API ────────────────────────────────────────

    def execute_promoted_strategy(
        self,
        symbol: str,
        timeframe: str,
        environment: str | Environment,
        observation: EnrichedMarketObservation | None = None,
        market_data: Any | None = None,
    ) -> list[Order]:
        """
        Canonical one-call API: Execute a promoted strategy end-to-end.

        This is the SINGLE ENTRY POINT for artifact-driven execution.
        Internally calls:
        1. resolver.resolve_for(symbol, timeframe, environment) → StrategyRuntime
        2. execute_strategy(runtime, market_data, observation) → orders

        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")
            timeframe: Timeframe (e.g., "1h")
            environment: Runtime environment (testnet/paper/production or Environment enum)
            observation: Market observation (required for execution)
            market_data: OHLCV data with indicators (required for strategy execution)

        Returns:
            List of submitted orders (may be empty if no action)
        """
        if self.resolver is None:
            raise RuntimeError(
                "execute_promoted_strategy requires RuntimeStrategyResolver "
                "(promotion_store + artifact_store at engine construction)"
            )
        if self.execution_service is None:
            raise RuntimeError(
                "execute_promoted_strategy requires instrument_rules "
                "for canonical execution pipeline"
            )

        # Normalize environment
        if isinstance(environment, str):
            env = Environment(environment.lower())
        else:
            env = environment

        # Resolve promoted strategy for this symbol/timeframe/environment
        strategy_runtime = self.resolver.resolve_for(symbol, timeframe, env)
        if strategy_runtime is None:
            logger.warning(
                f"No promoted strategy resolved for {symbol} {timeframe} {env.value}"
            )
            return []

        # Execute via authority-driven pipeline
        if market_data is None:
            logger.warning(
                "execute_promoted_strategy: market_data is required for strategy execution"
            )
            return []

        return self.execute_strategy(strategy_runtime, market_data, observation)

    @staticmethod
    def _result_to_order(
        result: Any,
        symbol: str,
        side: str,
        quantity: float,
    ) -> Order:
        """Convert a BrokerSubmitResult to an Order for backward compatibility."""
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        state = getattr(result, "state", None)
        if state == BrokerSubmitState.UNKNOWN:
            # UNKNOWN is not a rejection; it means the broker did not confirm
            # the final state. Treat as OPEN for reconciliation downstream.
            status = OrderStatus.OPEN
        elif result.success:
            status = OrderStatus.FILLED
        else:
            status = OrderStatus.REJECTED
        raw = result.raw_response or {}
        success = result.state in {
            BrokerSubmitState.ACCEPTED,
            BrokerSubmitState.OPEN,
            BrokerSubmitState.PARTIALLY_FILLED,
            BrokerSubmitState.FILLED,
        }
        filled_amount = float(
            raw.get(
                "filled",
                raw.get("accumulated_quantity", quantity if success else 0),
            )
            or 0
        )
        avg_fill_price = float((raw.get("average") or raw.get("price") or 0))
        return Order(
            id=result.broker_order_id or "",
            symbol=symbol,
            side=order_side,
            type=OrderType.MARKET,
            amount=float(quantity),
            status=status,
            filled_amount=filled_amount,
            avg_fill_price=avg_fill_price,
            client_order_id=result.broker_order_id,
            metadata={"error": result.error} if result.error else {},
        )


def _make_authorization_hash(
    intent_id: str,
    risk_decision_id: str,
    permission: str,
    authorized_at: str,
    symbol: str = "",
    side: str = "",
    quantity: float = 0.0,
    current_exposure: float = 0.0,
    resulting_exposure: float = 0.0,
    exposure_effect: str = "",
) -> str:
    """Stable authorization hash for audit."""
    import hashlib

    blob = (
        f"{intent_id}|{risk_decision_id}|{permission}|{authorized_at}|"
        f"{symbol}|{side}|{quantity}|{current_exposure}|{resulting_exposure}|{exposure_effect}"
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class _TrustedPrice:
    """Minimal trusted price wrapper for permission checks."""

    def __init__(self, price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            raise ValueError(
                f"_TrustedPrice requires a finite positive price, got {price}"
            )
        self.price = price
        self.updated_at = datetime.now(UTC)
        self.age_seconds = 0.0
