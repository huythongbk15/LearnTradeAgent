"""
Execution Engine — canonical interface to trade.

Kết nối Phase 2 (signals) với Phase 3 (execution) qua canonical pipeline:
AgentMessage → LegacyDecisionAdapter → UnifiedRiskDecision → TargetExposure
→ OrderPlanner → OrderPermission → ExecutionLifecycle → BrokerGateway.
"""

from __future__ import annotations

import logging
import math
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.config.loader import config
from trading_agent.execution.paper_exchange import STATE_DIR, PaperExchange
from trading_agent.execution.canonical import (
    BrokerGateway,
    LegacyDecisionAdapter,
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
from trading_agent.execution.canonical.broker_gateway import BrokerSubmitState
from trading_agent.execution.canonical.adapters import PaperExecutionAdapter
from trading_agent.execution.application import (
    CanonicalExecutionService,
    ExecutionBlockedError,
)
from trading_agent.execution.canonical.order_planner import (
    OrderPlanningStatus,
)
from trading_agent.execution.permission import (
    PermissionContext,
)
from trading_agent.execution.lifecycle import (
    ExecutionLifecycle,
    ExecutionEventStore,
    ExecutionEventType,
    ExecutionHealth,
    ExposureEffect,
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


class ExecutionEngine:
    """Canonical execution engine.

    Currently supports paper trading only (safe, no real money).
    All capital-changing orders flow through the canonical pipeline:
    AgentMessage → LegacyDecisionAdapter → UnifiedRiskDecision → TargetExposure
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
    ):
        # ── Constructor strictness: validate inputs early ─────────────
        if exchange is not None:
            # When an exchange is injected, we still need a name for telemetry.
            resolved_exchange_name = exchange_name or getattr(
                exchange, "exchange_name", "injected"
            )
        else:
            resolved_exchange_name = exchange_name or config.default_exchange
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
        self.exchange = exchange or PaperExchange(
            exchange_name=self.exchange_name,
            initial_balance=(
                config.initial_capital if initial_capital is None else initial_capital
            ),
            commission=config.commission if commission is None else commission,
            slippage=config.slippage if slippage is None else slippage,
            state_dir=state_dir or STATE_DIR,
            price_persist_interval=paper_price_persist_interval,
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
        if instrument_rules is not None:
            self.planner = OrderPlanner(
                instrument_rules=instrument_rules,
                strategy_version="legacy-engine-v1",
            )
            self.execution_service = CanonicalExecutionService(
                lifecycle=self.lifecycle,
                gateway=self.gateway,
                planner=self.planner,
            )
        else:
            self.planner = None
            self.execution_service = None
        self.legacy_adapter = LegacyDecisionAdapter(
            allow_new_exposure=allow_backtest_new_exposure
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

    # ── Execute signals from Phase 2 agents ────────────────────────────

    def execute_signal(
        self, signal: AgentMessage, observation: EnrichedMarketObservation | None = None
    ) -> list[Order]:
        """Execute a trading signal from the multi-agent system.

        Takes the final ``Trader`` agent signal and converts it to orders
        through the canonical pipeline:
        AgentMessage → LegacyDecisionAdapter → UnifiedRiskDecision → TargetExposure
        → OrderPlanner → PermissionContext → ExecutionLifecycle → BrokerGateway.
        """
        if self.execution_service is None:
            raise RuntimeError(
                "execute_signal requires instrument_rules to be provided at engine construction"
            )
        signal_str = signal.signal.upper()
        orders: list[Order] = []

        if signal_str == "HOLD":
            logger.info("Signal: HOLD — no action")
            return orders

        # Sync protective orders with actual positions (handles internal closes)
        self._sync_protective_orders()

        symbol = (
            signal.details.get("symbol", "BTC/USDT") if signal.details else "BTC/USDT"
        )
        # For SELL signals (exits), cancel any resting protective order first
        # to release its inventory reservation before authorizing the exit.
        if signal_str == "SELL":
            self._cancel_resting_protection(symbol)

        symbol = (
            signal.details.get("symbol", "BTC/USDT") if signal.details else "BTC/USDT"
        )
        price_info = self._get_current_price(symbol)
        if price_info is None:
            logger.warning(f"Cannot execute: no price data for {symbol}")
            return orders
        current_price, exchange_timestamp = price_info
        if current_price <= 0:
            logger.warning(f"Cannot execute: no price data for {symbol}")
            return orders

        # ── Observation: must come from market data layer ───────────────
        if observation is None:
            logger.warning(
                "execute_signal requires a market observation from the data layer"
            )
            return orders
        if not observation.is_closed:
            logger.warning(
                f"Refusing to execute from unclosed observation {observation.observation_id}"
            )
            return orders

        # ── Canonical legacy adapter: AgentMessage → risk + target ─────
        try:
            risk_decision, target = self.legacy_adapter.adapt(signal, observation)
        except ValueError as exc:
            logger.warning(f"Legacy adapter rejected signal: {exc}")
            return orders

        # ── Portfolio state (canonical fields) ──────────────────────────
        existing_pos = self.exchange.get_position(symbol)
        current_qty = existing_pos.quantity if existing_pos else 0.0
        current_notional = current_qty * current_price
        equity = self.exchange.get_total_equity()
        current_exposure = current_notional / equity if equity > 0 else 0.0
        portfolio = CurrentPortfolioState(
            symbol=symbol,
            current_exposure=current_exposure,
            equity=equity,
            existing_quantity=current_qty,
            available_cash=self.exchange.get_balance("USDT"),
        )
        price = MarketPrice(
            symbol=symbol,
            mid=current_price,
            bid=current_price,
            ask=current_price,
            last=current_price,
        )

        # ── Order planning (canonical sizing) ──────────────────────────
        plan_result = self.execution_service.plan(
            target=target,
            risk_decision=risk_decision,
            observation=observation,
            portfolio=portfolio,
            price=price,
            existing_reservations=self.lifecycle.active_sell_reservations(symbol),
        )
        if plan_result.status != OrderPlanningStatus.ORDER_REQUIRED:
            logger.info(f"Planner returned {plan_result.status.value} — no order")
            return orders
        if plan_result.intent is None:
            return orders

        intent = plan_result.intent

        # ── Permission check (canonical PermissionContext with actual intent) ──
        exposure_effect = (
            ExposureEffect.INCREASE if intent.side == "buy" else ExposureEffect.REDUCE
        )
        permission_ctx = PermissionContext(
            execution_health=ExecutionHealth.NORMAL,
            exposure_effect=exposure_effect,
            risk_decision=risk_decision,
            trusted_price=TrustedPrice(
                price=current_price,
                exchange_timestamp=exchange_timestamp,
                received_at=datetime.now(UTC),
            ),
            max_price_age_seconds=60.0,
            reconciliation_state="none",
            protection_state="none",
            manual_blocked=False,
            kill_switch_active=False,
            data_trust="trusted",
            inventory_state="known",
            free_inventory=portfolio.available_cash
            if intent.side == "buy"
            else current_qty,
            authorized_sellable_inventory=current_qty,
            order_size=intent.quantity,
            order_side=intent.side,
            require_fresh_market_data=True,
            enforce_inventory=True,
            broker_state=None,
            draft=False,
        )
        try:
            submission = self.execution_service.submit_planned(
                planning=plan_result,
                risk_decision=risk_decision,
                permission_context=permission_ctx,
                correlation_id=intent.intent_id,
            )
        except ExecutionBlockedError as exc:
            logger.warning(f"Order blocked by canonical execution service: {exc}")
            return orders
        result = submission.result
        broker_event = submission.broker_event

        orders.append(
            self._result_to_order(result, symbol, intent.side, intent.quantity)
        )
        fill_received = (
            broker_event is not None
            and broker_event.event_type
            in {
                ExecutionEventType.FILL_RECEIVED,
                ExecutionEventType.PARTIAL_FILL_RECEIVED,
            }
        )
        if fill_received and intent.side.lower() == "sell":
            self._cancel_resting_protection(symbol)
        if fill_received and intent.side.lower() == "buy":
            # Use actual paper exchange position quantity for protective order
            # (accounts for fees, slippage, partial fills)
            position = self.exchange.get_position(symbol)
            protected_quantity = position.quantity if position and position.is_active else 0.0
            if protected_quantity <= 0:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker fill did not produce a positive protected quantity",
                )
                return orders
            plan = ProtectionPlan(
                plan_id=f"prot_{intent.intent_id}",
                model_risk_decision_id=risk_decision.decision_id,
                symbol=intent.symbol,
                stop_type="stop_loss",
                stop_trigger=current_price * 0.95,
                take_profit=current_price * 1.10,
                state=ProtectionState.PROTECTION_REQUIRED,
                quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
                protected_quantity=protected_quantity,
            )
            protective_event = self.lifecycle.create_protective_order(
                symbol=plan.symbol,
                kind=plan.stop_type,
                trigger_price=plan.stop_trigger,
                parent_intent_id=intent.intent_id,
            )
            protection_intent_id = f"{protective_event.aggregate_id}_submit"
            protection_result = self.execution_service.emergency_protection(
                EmergencyReduceRequest(
                    intent_id=protection_intent_id,
                    symbol=plan.symbol,
                    side="sell",
                    quantity=plan.protected_quantity,
                    reason="PROTECTIVE_STOP",
                    parent_intent_id=intent.intent_id,
                    idempotency_key=protection_intent_id,
                    metadata={
                        "order_type": "stop",
                        "stop_price": plan.stop_trigger,
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
            else:
                self.lifecycle.require_manual_intervention(
                    intent.intent_id,
                    reason="broker did not acknowledge the required protective order",
                )

        return orders

    def _cancel_resting_protection(self, symbol: str) -> None:
        """Cancel paper stop orders and their lifecycle intents after an explicit exit."""
        # Cancel paper exchange order
        for order in list(self.exchange.get_open_orders(symbol)):
            if order.side == OrderSide.SELL and order.type in {
                OrderType.STOP_LOSS,
                OrderType.STOP_LOSS_LIMIT,
            }:
                self.exchange.cancel_order(order.id)
        # Cancel associated lifecycle protective order intent to release reservation
        for intent_id, order_state in self.lifecycle.state.orders.items():
            if not intent_id.startswith("prot_") or not intent_id.endswith("_submit"):
                continue
            if order_state.symbol != symbol:
                continue
            if order_state.status not in {IntentStatus.AUTHORIZED, IntentStatus.ACKNOWLEDGED}:
                continue
            # Cancel the protective order intent
            self._cancel_protective_intent(intent_id, "explicit_exit_signal")

    def _sync_protective_orders(self) -> None:
        """Sync protective orders with actual paper exchange positions.
        
        The paper exchange may close positions internally (stop_loss/take_profit)
        without notifying the lifecycle. This method detects such closures and
        cancels the associated protective order intents to release reservations.
        """
        # Get all positions from paper exchange
        current_positions = {}
        for pos in self.exchange.get_all_positions():
            if pos.is_active and pos.quantity > 0:
                current_positions[pos.symbol] = pos.quantity
        
        # Check lifecycle orders for protective orders
        for intent_id, order_state in self.lifecycle.state.orders.items():
            if not intent_id.startswith("prot_") or not intent_id.endswith("_submit"):
                continue
            if order_state.status not in {IntentStatus.AUTHORIZED, IntentStatus.ACKNOWLEDGED}:
                continue
            symbol = order_state.symbol
            protected_qty = order_state.size  # the quantity the protective order tries to sell
            
            # If position is closed or reduced, cancel the protective order
            current_qty = current_positions.get(symbol, 0.0)
            if current_qty <= 0:
                # Position fully closed - cancel protective order
                logger.info(f"Position {symbol} closed, cancelling protective order {intent_id}")
                self._cancel_protective_intent(intent_id, "position_closed_externally")
            elif current_qty < protected_qty:
                # Position reduced - adjust protective order quantity
                # For now, cancel and let new one be created on next fill
                logger.warning(
                    f"Position {symbol} reduced from {protected_qty} to {current_qty}, "
                    f"cancelling protective order {intent_id}"
                )
                self._cancel_protective_intent(intent_id, "position_reduced_externally")

    def _cancel_protective_intent(self, intent_id: str, reason: str) -> None:
        """Cancel a protective order intent and release its reservation."""
        order_state = self.lifecycle.state.orders.get(intent_id)
        if order_state is None:
            return
        # Emit CANCEL_CONFIRMED event to release store reservation
        # CANCEL_CONFIRMED requires payload['order_id'] (broker/exchange order ID)
        # and payload['state'] = 'CANCELED' (uppercase, per CancelState enum) to trigger reservation release
        order_id = order_state.exchange_order_id or order_state.broker_order_id
        payload = {"reason": reason, "state": "CANCELED"}
        if order_id:
            payload["order_id"] = order_id
        self.lifecycle._emit(
            ExecutionEventType.CANCEL_CONFIRMED,
            intent_id,
            payload,
        )
        # Cancel paper exchange order
        self._cancel_resting_protection(order_state.symbol)

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
