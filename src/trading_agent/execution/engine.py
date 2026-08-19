"""
Execution Engine — canonical interface to trade.

Kết nối Phase 2 (signals) với Phase 3 (execution) qua canonical pipeline:
AgentMessage → LegacyDecisionAdapter → UnifiedRiskDecision → TargetExposure
→ OrderPlanner → OrderPermission → ExecutionLifecycle → BrokerGateway.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.config.loader import config
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.canonical import (
    BrokerGateway,
    LegacyDecisionAdapter,
    OrderPlanner,
    EnrichedMarketObservation,
    CurrentPortfolioState,
    MarketPrice,
    InstrumentRules,
    ProtectionPlan,
    ProtectionQuantityMode,
    ProtectiveAckEvidence,
)
from trading_agent.execution.canonical.order_planner import (
    OrderPlanningStatus,
    ExposureEffect,
)
from trading_agent.execution.permission import (
    PermissionContext,
    evaluate_order_permission,
)
from trading_agent.execution.lifecycle import (
    ExecutionLifecycle,
    ExecutionEventStore,
    ExecutionHealth,
    TrustedPrice,
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
    ):
        self.exchange_name = exchange_name or config.default_exchange

        # ── Paper exchange (broker adapter) ───────────────────────────
        self.exchange = exchange or PaperExchange(
            exchange_name=self.exchange_name,
            initial_balance=(
                config.initial_capital if initial_capital is None else initial_capital
            ),
            commission=config.commission if commission is None else commission,
            slippage=config.slippage if slippage is None else slippage,
        )

        # ── Canonical execution stack ─────────────────────────────────
        self.store = ExecutionEventStore("data/execution/events.db")
        self.store.connect()
        self.gateway = BrokerGateway(adapter=self.exchange, store=self.store)
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
        )
        self.planner = OrderPlanner(
            instrument_rules=InstrumentRules(symbol="BTC/USDT", spot_long_only=True),
            strategy_version="legacy-engine-v1",
        )
        self.legacy_adapter = LegacyDecisionAdapter()

        # Register graceful shutdown handler
        register_shutdown_handler(self._graceful_shutdown)

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
        signal_str = signal.signal.upper()
        orders: list[Order] = []

        if signal_str == "HOLD":
            logger.info("Signal: HOLD — no action")
            return orders

        symbol = (
            signal.details.get("symbol", "BTC/USDT") if signal.details else "BTC/USDT"
        )
        current_price = self._get_current_price(symbol)
        if current_price is None or current_price <= 0:
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
        plan_result = self.planner.plan(
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
                exchange_timestamp=None,
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
        permission = evaluate_order_permission(permission_ctx)
        if not permission.allowed():
            logger.warning(f"Order blocked by permission: {permission.reason.value}")
            return orders

        # ── Lifecycle: create intent + approve risk + authorize ────────
        # Lifecycle owns event append; callers must NOT double-append.
        created_event = self.lifecycle.create_order_intent(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            size=intent.quantity,
            idempotency_key=intent.idempotency_key,
        )

        approved_event = self.lifecycle.approve_risk(
            intent_id=intent.intent_id,
            risk_decision=risk_decision,
        )

        # Build durable authorization through lifecycle
        now = datetime.now(UTC).isoformat()
        authorization_hash = _make_authorization_hash(
            intent.intent_id,
            risk_decision.decision_id,
            permission.permission.value,
            now,
            intent.symbol,
            intent.side,
            intent.quantity,
            portfolio.current_exposure,
            plan_result.executable_delta + portfolio.current_exposure,
            exposure_effect.value,
        )
        authorized_event = self.lifecycle.authorize_order(
            intent_id=intent.intent_id,
            authorization_id=f"auth-{intent.intent_id}",
            idempotency_key=intent.idempotency_key,
            payload_hash=authorization_hash,
            risk_decision_id=risk_decision.decision_id,
            forecast_fingerprint=risk_decision.forecast_fingerprint,
            model_artifact_id=risk_decision.model_artifact_id,
            permission=permission.permission.value,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            exposure_effect=exposure_effect.value,
            current_exposure=portfolio.current_exposure,
            resulting_exposure=plan_result.executable_delta
            + portfolio.current_exposure,
            authorized_at=now,
        )

        # ──── Durable broker submission request BEFORE broker I/O ────────
        request_event = self.lifecycle.request_broker_submission(
            intent_id=intent.intent_id,
        )

        # ── Build AuthorizedOrder from durable authorization ────────────
        authorized = AuthorizedOrder(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            idempotency_key=intent.idempotency_key,
            price_reference=current_price,
            risk_decision_id=risk_decision.decision_id,
            forecast_fingerprint=risk_decision.forecast_fingerprint,
            model_artifact_id=risk_decision.model_artifact_id,
            permission_result=permission.permission.value,
            authorization_id=authorized_event.payload["authorization_id"],
            lifecycle_event_id=authorized_event.event_id,
            correlation_id=intent.intent_id,
            exposure_effect=exposure_effect.value,
            current_exposure=portfolio.current_exposure,
            resulting_exposure=plan_result.executable_delta
            + portfolio.current_exposure,
            authorized_at=now,
            authorization_hash=authorization_hash,
        )

        # ── Submit via gateway (verifies auth against durable state) ────
        result = self.gateway.submit(authorized, correlation_id=intent.intent_id)
        submit_event = self.lifecycle.submit_order(
            intent_id=intent.intent_id,
            exchange_order_id=result.broker_order_id,
        )

        if result.success and result.broker_order_id:
            # Simulate immediate fill for paper trading
            fill_event = self.lifecycle.receive_fill(
                intent_id=intent.intent_id,
                size=intent.quantity,
                price=current_price,
            )

            # Protection plan (explicit quantity, no magic zero)
            plan = ProtectionPlan(
                plan_id=f"prot_{intent.intent_id}",
                model_risk_decision_id=risk_decision.decision_id,
                symbol=intent.symbol,
                stop_type="stop_loss",
                stop_trigger=current_price * 0.95,
                take_profit=current_price * 1.10,
                quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
                protected_quantity=intent.quantity,
            )
            protective_event = self.lifecycle.create_protective_order(
                symbol=plan.symbol,
                kind=plan.stop_type,
                trigger_price=plan.stop_trigger,
                parent_intent_id=intent.intent_id,
            )
            protection_result = self.gateway.submit_protection(
                plan,
                correlation_id=intent.intent_id,
            )
            if protection_result.success and protection_result.evidence:
                ack_evidence = ProtectiveAckEvidence(
                    broker_order_id=protection_result.evidence.broker_order_id,
                    broker_ack_id=protection_result.evidence.broker_ack_id,
                    venue=protection_result.evidence.venue,
                    broker_status="open",
                    acknowledged_at=datetime.now(UTC).isoformat(),
                    protected_symbol=plan.symbol,
                    protected_quantity=plan.protected_quantity,
                    evidence_source="BROKER",
                    raw_response=protection_result.evidence.raw_response,
                )
                ack_event = self.lifecycle.acknowledge_protective_order(
                    protective_order_id=protective_event.aggregate_id,
                    evidence=ack_evidence,
                )

            orders.append(
                self._result_to_order(result, symbol, intent.side, intent.quantity)
            )
        else:
            reject_event = self.lifecycle.reject_order(
                intent_id=intent.intent_id,
                reason=result.error or "gateway submit failed",
            )

        return orders

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
        orders: list[Order] = []
        for pos in self.exchange.get_all_positions():
            if pos.quantity <= 0:
                continue
            symbol = pos.symbol
            current_price = self._get_current_price(symbol)
            if current_price is None:
                continue
            # Use canonical emergency reduce through lifecycle
            emergency = EmergencyReduceRequest(
                intent_id=f"emergency-close-{symbol}-{uuid.uuid4().hex}",
                symbol=symbol,
                side="sell",
                quantity=pos.quantity,
                reason=reason,
            )
            try:
                auth_event = self.lifecycle.emergency_reduce(emergency)
                # Submit via gateway
                from trading_agent.execution.canonical.broker_gateway import (
                    _AUTHORIZED_TOKEN,
                )

                authorized = AuthorizedOrder(
                    token=_AUTHORIZED_TOKEN,
                    intent_id=emergency.intent_id,
                    symbol=symbol,
                    side="sell",
                    quantity=pos.quantity,
                    idempotency_key=f"emergency-{symbol}",
                    price_reference=current_price,
                    risk_decision_id=auth_event.payload.get("risk_decision_id", ""),
                    forecast_fingerprint="",
                    model_artifact_id="emergency_reduce",
                    permission_result="REDUCE_ONLY",
                    authorization_id=auth_event.payload.get("authorization_id", ""),
                    lifecycle_event_id=auth_event.event_id,
                    correlation_id=emergency.intent_id,
                    exposure_effect="reduce",
                    current_exposure=0.0,
                    resulting_exposure=0.0,
                    authorized_at=auth_event.payload.get("authorized_at", ""),
                    authorization_hash="",
                )
                result = self.gateway.submit(
                    auth_id, correlation_id=emergency.intent_id
                )
                if result.success and result.broker_order_id:
                    self.lifecycle.submit_order(
                        intent_id=emergency.intent_id,
                        exchange_order_id=result.broker_order_id,
                    )
                    self.lifecycle.receive_fill(
                        intent_id=emergency.intent_id,
                        size=pos.quantity,
                        price=current_price,
                    )
                    orders.append(
                        Order(
                            id=result.broker_order_id,
                            symbol=symbol,
                            side=OrderSide.SELL,
                            type=OrderType.MARKET,
                            amount=pos.quantity,
                            status=OrderStatus.FILLED,
                            filled_amount=pos.quantity,
                            avg_fill_price=current_price,
                        )
                    )
            except Exception as e:
                logger.error(f"Emergency reduce failed for {symbol}: {e}")
        return orders

    def close_position(self, symbol: str, reason: str = "manual") -> Order | None:
        """Close a single position via canonical lifecycle emergency reduce."""
        pos = self.exchange.get_position(symbol)
        if not pos or not pos.is_active or pos.quantity <= 0:
            return None
        current_price = self._get_current_price(symbol)
        if current_price is None:
            return None
        emergency = EmergencyReduceRequest(
            intent_id=f"emergency-close-{symbol}-{uuid.uuid4().hex}",
            symbol=symbol,
            side="sell",
            quantity=pos.quantity,
            reason=reason,
        )
        try:
            auth_event = self.lifecycle.emergency_reduce(emergency)
            from trading_agent.execution.canonical.broker_gateway import (
                _AUTHORIZED_TOKEN,
            )

            authorized = AuthorizedOrder(
                token=_AUTHORIZED_TOKEN,
                intent_id=emergency.intent_id,
                symbol=symbol,
                side="sell",
                quantity=pos.quantity,
                idempotency_key=f"emergency-{symbol}",
                price_reference=current_price,
                risk_decision_id=auth_event.payload.get("risk_decision_id", ""),
                forecast_fingerprint="",
                model_artifact_id="emergency_reduce",
                permission_result="REDUCE_ONLY",
                authorization_id=auth_event.payload.get("authorization_id", ""),
                lifecycle_event_id=auth_event.event_id,
                correlation_id=emergency.intent_id,
                exposure_effect="reduce",
                current_exposure=0.0,
                resulting_exposure=0.0,
                authorized_at=auth_event.payload.get("authorized_at", ""),
                authorization_hash="",
            )
            result = self.gateway.submit(authorized, correlation_id=emergency.intent_id)
            if result.success and result.broker_order_id:
                self.lifecycle.submit_order(
                    intent_id=emergency.intent_id,
                    exchange_order_id=result.broker_order_id,
                )
                self.lifecycle.receive_fill(
                    intent_id=emergency.intent_id,
                    size=pos.quantity,
                    price=current_price,
                )
                return Order(
                    id=result.broker_order_id,
                    symbol=symbol,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    amount=pos.quantity,
                    status=OrderStatus.FILLED,
                    filled_amount=pos.quantity,
                    avg_fill_price=current_price,
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

    def _get_current_price(self, symbol: str) -> float | None:
        # Prefer live ticker from adapter; fall back to simulator price cache.
        try:
            ticker = self.exchange.get_ticker(symbol)
            price = ticker.get("last") or ticker.get("price")
            if price is not None:
                return float(price)
        except Exception:
            pass
        try:
            return float(self.exchange._last_price_cache[symbol])
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
        status = OrderStatus.FILLED if result.success else OrderStatus.REJECTED
        raw = result.raw_response or {}
        filled_amount = float(
            raw.get(
                "filled",
                raw.get("accumulated_quantity", quantity if result.success else 0),
            )
        )
        avg_fill_price = float(raw.get("average", raw.get("price", 0)))
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
        self.price = price
        self.updated_at = datetime.now(UTC)
        self.age_seconds = 0.0
