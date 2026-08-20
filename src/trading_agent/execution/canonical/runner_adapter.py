"""Canonical broker adapter for legacy runners.

Legacy live trading scripts (``scripts/live_enhanced_ma*.py``) currently
call ``broker.place_order()``, ``broker.replace_order()``, and
``broker.cancel_order()`` directly.  This adapter wraps those calls so
they flow through :class:`BrokerGateway`, preserving the canonical
execution boundary.

The adapter accepts ONLY lifecycle-authorized :class:`AuthorizedOrder`
instances.  Runners must obtain authorization through the canonical
path before calling this adapter.
"""

from __future__ import annotations

from typing import Any

from trading_agent.execution.canonical.broker_gateway import (
    AuthorizedOrder,
    BrokerGateway,
    BrokerSubmitResult,
    AuthorizationError,
)


class CanonicalBrokerAdapter:
    """Thin wrapper that routes already-authorized commands through BrokerGateway.

    Parameters
    ----------
    broker:
        The legacy broker facade (e.g. ``LiveBroker``).
    store:
        The execution event store for durable authorization verification.
        REQUIRED - no broker I/O is permitted without durable authorization.
    """

    def __init__(self, broker: Any, store: Any) -> None:
        self._broker = broker
        # Create canonical adapter from broker and inject with store into BrokerGateway
        from trading_agent.execution.canonical.adapters import (
            AlpacaExecutionAdapter,
            BinanceExecutionAdapter,
            LiveBrokerExecutionAdapter,
        )

        # Auto-detect adapter type based on broker class name
        broker_cls_name = type(broker).__name__.lower()
        if "alpaca" in broker_cls_name:
            canonical_adapter = AlpacaExecutionAdapter(broker)
        elif "binance" in broker_cls_name:
            canonical_adapter = BinanceExecutionAdapter(broker)
        else:
            canonical_adapter = LiveBrokerExecutionAdapter(broker)
        self._gateway = BrokerGateway(adapter=canonical_adapter, store=store)

    # ── Public API (mirrors LiveBroker) ────────────────────────────────

    def place_order(
        self,
        order: Any,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Submit an order through the canonical gateway.

        Accepts ONLY lifecycle-authorized ``AuthorizedOrder``.
        """
        if not isinstance(order, AuthorizedOrder):
            raise AuthorizationError(
                f"CanonicalBrokerAdapter.place_order() accepts only AuthorizedOrder, "
                f"got {type(order).__name__}"
            )
        result = self._gateway.submit(order, correlation_id=correlation_id)
        return self._to_legacy_result(result)

    def replace_order(
        self,
        order_id: str,
        order: Any,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Cancel-replace via canonical lifecycle path.

        Canonical replace: cancel existing → obtain terminal evidence →
        new authorization → submit replacement.
        If canonical cancel cannot be performed safely, fail closed.
        """
        if not isinstance(order, AuthorizedOrder):
            raise AuthorizationError(
                f"CanonicalBrokerAdapter.replace_order() accepts only AuthorizedOrder, "
                f"got {type(order).__name__}"
            )
        # Canonical replace: cancel then submit new
        cancel_result = self._gateway.cancel(
            order_id=order_id,
            correlation_id=correlation_id,
        )
        if not cancel_result.success or cancel_result.evidence is None:
            return {
                "success": False,
                "error": cancel_result.error or "cancel failed",
            }
        # Only proceed if cancel is terminal
        from trading_agent.execution.canonical.broker_gateway import CancelState

        if cancel_result.evidence.state not in {
            CancelState.CANCELED,
            CancelState.REJECTED,
            CancelState.EXPIRED,
        }:
            return {
                "success": False,
                "error": f"cancel not terminal: {cancel_result.evidence.state.value}",
            }
        # Submit replacement
        return self.place_order(order, correlation_id=correlation_id)

    def cancel_order(self, order_id: str, *, correlation_id: str) -> dict[str, Any]:
        """Cancel an order through the canonical gateway."""
        result = self._gateway.cancel(
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return {
            "success": result.success,
            "error": result.error,
            "evidence": result.evidence.to_dict() if result.evidence else None,
        }

    # ── Proxy methods for read-only broker operations ──────────────────

    def get_account(self) -> dict[str, Any]:
        """Proxy to underlying broker."""
        return self._broker.get_account()

    def get_positions(self) -> list[dict[str, Any]]:
        """Proxy to underlying broker."""
        return self._broker.get_positions()

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Proxy to underlying broker."""
        return self._broker.get_ticker(symbol)

    def get_order_book(self, symbol: str, limit: int = 50) -> dict[str, Any]:
        """Proxy to underlying broker."""
        return self._broker.get_order_book(symbol, limit=limit)

    def get_order_by_client_id(
        self, client_order_id: str, symbol: str
    ) -> dict[str, Any] | None:
        """Proxy to underlying broker."""
        return self._broker.get_order_by_client_id(client_order_id, symbol)

    def normalize_order_amount(self, symbol: str, amount: float) -> float:
        """Proxy to underlying broker."""
        return self._broker.normalize_order_amount(symbol, amount)

    # ── Private helpers ────────────────────────────────────────────────

    def _make_correlation_id(self, order: Any) -> str:
        """Generate a deterministic correlation ID from an order object."""
        client_id = getattr(order, "client_order_id", None)
        if client_id:
            return f"runner-{client_id}"
        symbol = getattr(order, "symbol", "unknown")
        pair = symbol.pair if hasattr(symbol, "pair") else str(symbol)
        side = getattr(order, "side", "unknown")
        return (
            f"runner-{pair}-{side.value.lower()}-{int(getattr(order, 'size', 0) * 1e8)}"
        )

    def _to_legacy_result(self, result: BrokerSubmitResult) -> dict[str, Any]:
        """Convert a BrokerSubmitResult to legacy dict format."""
        return {
            "success": result.success,
            "broker_order_id": result.broker_order_id,
            "error": result.error,
            "raw_response": result.raw_response,
        }


__all__ = ["CanonicalBrokerAdapter"]
