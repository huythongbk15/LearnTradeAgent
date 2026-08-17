"""BrokerGateway — the ONLY capital-changing boundary.

All broker/exchange calls that move money or create/close positions MUST
flow through this gateway.  Direct calls to ``adapter.place_order()``,
``exchange.create_order()``, ``engine.close_all()``, or
``exchange._close_position()`` are FORBIDDEN outside this module.

The gateway is intentionally thin: it validates inputs, records the intent,
and delegates to the configured exchange adapter.  All side-effects (fills,
cancellations, protective orders) are emitted as execution events so the
lifecycle store can replay them.
"""

from __future__ import annotations

from typing import Any, Protocol

from trading_agent.execution.canonical.order_planner import OrderIntent
from trading_agent.execution.canonical.protection import ProtectionPlan, ProtectionState
from trading_agent.execution.lifecycle.events import (
    ExecutionEventType,
    make_event,
)


class CapitalChangeResult:
    """Result of a capital-changing gateway call."""

    def __init__(
        self,
        success: bool,
        broker_order_id: str | None = None,
        error: str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.broker_order_id = broker_order_id
        self.error = error
        self.raw_response = raw_response or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "broker_order_id": self.broker_order_id,
            "error": self.error,
            "raw_response": self.raw_response,
        }


class ExchangeAdapter(Protocol):
    """Minimal exchange adapter protocol the gateway depends on.

    Implementations must NOT be called from outside the gateway.
    """

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]: ...
    def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        limit_price: float | None = None,
    ) -> dict[str, Any]: ...
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...
    def fetch_order(self, order_id: str) -> dict[str, Any]: ...
    def fetch_positions(self) -> list[dict[str, Any]]: ...
    def fetch_balances(self) -> dict[str, Any]: ...
    def close_position(
        self, symbol: str, price: float, reason: str
    ) -> dict[str, Any]: ...


class BrokerGateway:
    """The ONLY capital-changing boundary.

    Parameters
    ----------
    adapter:
        The exchange/broker adapter.  The gateway owns the reference; no
        other module may call the adapter directly.
    event_sink:
        Callable that receives ExecutionEvent instances for append to the
        lifecycle store.
    """

    def __init__(
        self,
        adapter: ExchangeAdapter,
        event_sink: Any | None = None,
    ) -> None:
        self._adapter = adapter
        self._event_sink = event_sink

    # ── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        intent: OrderIntent,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> CapitalChangeResult:
        """Submit an order intent to the broker.

        This is the ONLY path that creates a new broker order.
        """
        if self._event_sink is not None:
            event = make_event(
                event_type=ExecutionEventType.ORDER_SUBMITTED,
                aggregate_id=intent.intent_id,
                seq=1,
                payload={
                    "order_id": intent.intent_id,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "qty": intent.quantity,
                    "order_type": "market",
                    "idempotency_key": intent.idempotency_key,
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            self._event_sink(event)

        order_payload = {
            "id": intent.intent_id,
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": intent.quantity,
            "order_type": "market",
            "idempotency_key": intent.idempotency_key,
        }
        try:
            response = self._adapter.place_order(order_payload)
            broker_order_id = response.get("id") or response.get("order_id")
            return CapitalChangeResult(
                success=True,
                broker_order_id=broker_order_id,
                raw_response=response,
            )
        except Exception as exc:
            if self._event_sink is not None:
                reject = make_event(
                    event_type=ExecutionEventType.ORDER_REJECTED,
                    aggregate_id=intent.intent_id,
                    seq=1,
                    payload={
                        "order_id": intent.intent_id,
                        "error": str(exc),
                    },
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                self._event_sink(reject)
            return CapitalChangeResult(success=False, error=str(exc))

    def cancel(
        self,
        order_id: str,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> CapitalChangeResult:
        """Request cancellation of a broker order."""
        if self._event_sink is not None:
            event = make_event(
                event_type=ExecutionEventType.CANCEL_REQUESTED,
                aggregate_id=order_id,
                seq=1,
                payload={"order_id": order_id},
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            self._event_sink(event)

        try:
            response = self._adapter.cancel_order(order_id)
            if self._event_sink is not None:
                confirm = make_event(
                    event_type=ExecutionEventType.CANCEL_CONFIRMED,
                    aggregate_id=order_id,
                    seq=2,
                    payload={"order_id": order_id},
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                self._event_sink(confirm)
            return CapitalChangeResult(
                success=True,
                broker_order_id=order_id,
                raw_response=response,
            )
        except Exception as exc:
            return CapitalChangeResult(success=False, error=str(exc))

    def fetch_order(
        self,
        order_id: str,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch current state of a broker order."""
        return self._adapter.fetch_order(order_id)

    def fetch_positions(
        self,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch current positions from the broker."""
        return self._adapter.fetch_positions()

    def fetch_balances(
        self,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch current balances from the broker."""
        return self._adapter.fetch_balances()

    def submit_protection(
        self,
        plan: ProtectionPlan,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> CapitalChangeResult:
        """Submit a protective order (stop-loss / take-profit).

        This is the ONLY path that creates a protective broker order.
        """
        if plan.state != ProtectionState.PROTECTION_REQUIRED:
            raise ValueError(f"cannot submit protection in state {plan.state.value}")

        if self._event_sink is not None:
            event = make_event(
                event_type=ExecutionEventType.PROTECTIVE_ORDER_CREATED,
                aggregate_id=plan.plan_id,
                seq=1,
                payload={
                    "plan_id": plan.plan_id,
                    "symbol": plan.symbol,
                    "stop_type": plan.stop_type,
                    "stop_trigger": plan.stop_trigger,
                    "take_profit": plan.take_profit,
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            self._event_sink(event)

        # Build broker order payload from plan
        order_payload: dict[str, Any] = {
            "id": plan.plan_id,
            "symbol": plan.symbol,
            "order_type": plan.stop_type,
            "stop_price": plan.stop_trigger,
            "limit_price": plan.take_profit,
        }
        try:
            response = self._adapter.create_order(
                symbol=plan.symbol,
                side="sell",
                qty=0.0,  # protective orders may use different qty semantics
                order_type=plan.stop_type,
                limit_price=plan.take_profit,
            )
            broker_order_id = response.get("id") or response.get("order_id")
            if self._event_sink is not None:
                ack = make_event(
                    event_type=ExecutionEventType.PROTECTIVE_ORDER_ACKNOWLEDGED,
                    aggregate_id=plan.plan_id,
                    seq=2,
                    payload={
                        "plan_id": plan.plan_id,
                        "broker_order_id": broker_order_id,
                    },
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                self._event_sink(ack)
            return CapitalChangeResult(
                success=True,
                broker_order_id=broker_order_id,
                raw_response=response,
            )
        except Exception as exc:
            return CapitalChangeResult(success=False, error=str(exc))

    def close_all_positions(
        self,
        *,
        correlation_id: str,
        causation_id: str | None = None,
        reason: str = "manual_kill",
    ) -> dict[str, list[str]]:
        """Emergency close all positions.

        This is the ONLY authorized path for close-all operations.
        """
        positions = self._adapter.fetch_positions()
        remaining: list[str] = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            if not symbol:
                continue
            try:
                current_price = float(pos.get("current_price", 0.0))
                self._adapter.close_position(symbol, current_price, reason=reason)
            except Exception:
                remaining.append(symbol)
        return {"remaining": remaining}


__all__ = [
    "ExchangeAdapter",
    "BrokerGateway",
    "CapitalChangeResult",
]
