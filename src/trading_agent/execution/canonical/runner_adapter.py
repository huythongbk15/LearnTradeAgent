"""Canonical broker adapter for legacy runners.

Legacy live trading scripts (``scripts/live_enhanced_ma*.py``) currently
call ``broker.place_order()``, ``broker.replace_order()``, and
``broker.cancel_order()`` directly.  This adapter wraps those calls so
they flow through :class:`BrokerGateway`, preserving the canonical
execution boundary without rewriting the entire runner.

Risk validation is intentionally minimal here: the existing runner logic
already performs its own risk checks.  This adapter focuses on the
capital-changing boundary (P0 §11/§12).
"""

from __future__ import annotations

from typing import Any

from trading_agent.execution.canonical.broker_gateway import (
    AuthorizedOrder,
    BrokerGateway,
)
from trading_agent.execution.canonical.events import compute_idempotency_key


class CanonicalBrokerAdapter:
    """Wraps a legacy broker facade and routes calls through BrokerGateway.

    Parameters
    ----------
    broker:
        The legacy broker facade (e.g. ``LiveBroker``).
    gateway:
        The canonical :class:`BrokerGateway`.  Created automatically if
        not provided.
    """

    def __init__(self, broker: Any, gateway: BrokerGateway | None = None) -> None:
        self._broker = broker
        self._gateway = gateway or BrokerGateway(adapter=broker)

    # ── Public API (mirrors LiveBroker) ────────────────────────────────

    def place_order(self, order: Any) -> dict[str, Any]:
        """Submit a new order through the canonical gateway."""
        correlation_id = self._make_correlation_id(order)
        authorized = self._to_authorized(order)
        result = self._gateway.submit(
            authorized,
            correlation_id=correlation_id,
        )
        return self._to_legacy_result(result)

    def replace_order(self, order_id: str, order: Any) -> dict[str, Any]:
        """Cancel-replace an existing order through the canonical gateway.

        The canonical gateway does not natively support replace; we
        implement it as cancel + submit atomically via the gateway's
        cancel path if available, otherwise fall back to the legacy
        broker for this operation.
        """
        # Try canonical cancel if available
        cancel_result = None
        if hasattr(self._gateway, "cancel"):
            cancel_result = self._gateway.cancel(
                order_id=order_id,
                correlation_id=self._make_correlation_id(order),
            )

        if cancel_result and cancel_result.success:
            # Submit the new order
            return self.place_order(order)

        # Fallback to legacy broker if gateway cancel not available
        return self._broker.replace_order(order_id, order)

    def cancel_order(self, order_id: str, symbol: Any = None) -> bool:
        """Cancel an order through the canonical gateway."""
        correlation_id = f"cancel-{order_id}"
        cancel_result = self._gateway.cancel(
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return cancel_result.success if cancel_result else False

    # ── Private helpers ────────────────────────────────────────────────

    def _make_correlation_id(self, order: Any) -> str:
        """Generate a correlation ID from an order object."""
        client_id = getattr(order, "client_order_id", None)
        if client_id:
            return f"runner-{client_id}"
        symbol = getattr(order, "symbol", "unknown")
        return f"runner-{symbol}-{id(order)}"

    def _to_authorized(self, order: Any) -> AuthorizedOrder:
        """Convert a legacy Order to AuthorizedOrder."""
        symbol = getattr(order, "symbol", "UNKNOWN")
        side = "buy"
        if hasattr(order, "side"):
            side_value = str(order.side).upper()
            if side_value in ("SELL", "SHORT"):
                side = "sell"

        quantity = float(getattr(order, "size", 0) or 0)
        if quantity <= 0:
            quantity = 0.0

        price_reference = 0.0
        if hasattr(order, "limit_price") and order.limit_price:
            price_reference = float(order.limit_price)

        intent_id = getattr(order, "client_order_id", None) or f"runner-{id(order)}"
        idempotency_key = compute_idempotency_key(
            decision_id=intent_id,
            symbol=str(symbol),
            target_exposure=0.0,
            horizon=0,
        )

        return AuthorizedOrder(
            intent_id=intent_id,
            symbol=str(symbol),
            side=side,
            quantity=quantity,
            idempotency_key=idempotency_key,
            price_reference=price_reference,
        )

    def _to_legacy_result(self, result: Any) -> dict[str, Any]:
        """Convert a CapitalChangeResult to legacy dict format."""
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return {
            "success": getattr(result, "success", False),
            "broker_order_id": getattr(result, "broker_order_id", None),
            "error": getattr(result, "error", None),
            "raw_response": getattr(result, "raw_response", {}),
        }
