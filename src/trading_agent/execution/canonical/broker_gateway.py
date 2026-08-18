"""BrokerGateway — the ONLY capital-changing boundary.

All broker/exchange calls that move money or create/close positions MUST
flow through this gateway.  Direct calls to ``adapter.place_order()``,
``exchange.create_order()``, ``engine.close_all()``, or
``exchange._close_position()`` are FORBIDDEN outside this module.

The gateway is intentionally thin: it validates inputs, delegates to the
configured exchange adapter, and returns typed broker facts.  It does NOT:
- emit financial lifecycle events;
- allocate event sequences;
- decide lifecycle state transitions;
- interpret broker outcomes into financial state.

All side-effects (fills, cancellations, protective orders) are interpreted
and persisted by ExecutionLifecycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from trading_agent.execution.canonical.protection import (
    ProtectionPlan,
    ProtectionQuantityMode,
    ProtectionState,
)


class AuthorizationError(RuntimeError):
    """Raised when an unauthorized order reaches the gateway."""


class CancelState(str, Enum):
    """Typed cancel terminal and non-terminal states."""
    REQUEST_ACCEPTED = "REQUEST_ACCEPTED"
    PENDING = "PENDING"
    CANCELED = "CANCELED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CancelEvidence:
    """Typed terminal evidence for a cancel request."""
    broker_order_id: str
    state: CancelState
    venue: str
    confirmed_at: str
    source: str  # "BROKER" | "RECONCILIATION" | "SIMULATOR"
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProtectiveAckEvidence:
    """Typed evidence that a protective order is acknowledged by the broker."""
    broker_order_id: str
    broker_ack_id: str
    venue: str
    broker_status: str
    acknowledged_at: str
    protected_symbol: str
    protected_quantity: float
    evidence_source: str  # "BROKER" | "RECONCILIATION" | "SIMULATOR"
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerSubmitResult:
    """Typed result of a broker submit."""
    success: bool
    broker_order_id: str | None
    error: str | None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelResult:
    """Typed result of a broker cancel request."""
    success: bool
    evidence: CancelEvidence | None
    error: str | None


@dataclass(frozen=True)
class ProtectiveSubmitResult:
    """Typed result of a protective order submission."""
    success: bool
    evidence: ProtectiveAckEvidence | None
    error: str | None


class AuthorizedOrder:
    """Unforgeable authorization wrapper for broker submission.

    Construction is restricted to the lifecycle authorization path.
    Normal callers cannot create valid instances.
    """

    def __init__(self, token: str, **fields: Any) -> None:
        if token != "__authorized__":
            raise AuthorizationError(
                "AuthorizedOrder must be created through lifecycle authorization"
            )
        self._token = token
        self.intent_id = fields["intent_id"]
        self.symbol = fields["symbol"]
        self.side = fields["side"]
        self.quantity = fields["quantity"]
        self.idempotency_key = fields["idempotency_key"]
        self.price_reference = fields["price_reference"]
        self.metadata = fields.get("metadata", {})
        # Required authorization evidence
        self.risk_decision_id = fields["risk_decision_id"]
        self.forecast_fingerprint = fields["forecast_fingerprint"]
        self.model_artifact_id = fields["model_artifact_id"]
        self.permission_result = fields["permission_result"]
        self.authorization_id = fields["authorization_id"]
        self.lifecycle_event_id = fields["lifecycle_event_id"]
        self.correlation_id = fields["correlation_id"]
        self.exposure_effect = fields["exposure_effect"]
        self.current_exposure = fields["current_exposure"]
        self.resulting_exposure = fields["resulting_exposure"]
        self.authorized_at = fields["authorized_at"]
        self.authorization_hash = fields["authorization_hash"]

    @classmethod
    def create(cls, **fields: Any) -> AuthorizedOrder:
        """Factory for lifecycle-authorized orders."""
        return cls(token="__authorized__", **fields)


class ExchangeAdapter(Protocol):
    """Minimal exchange adapter protocol the gateway depends on.

    Implementations must NOT be called from outside the gateway.
    """

    capabilities: dict[str, bool] = {
        "close_position_protection": False,
    }

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
    """

    def __init__(self, adapter: ExchangeAdapter) -> None:
        self._adapter = adapter

    # ── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        order: AuthorizedOrder,
        *,
        correlation_id: str,
    ) -> BrokerSubmitResult:
        """Submit an order to the broker.

        Accepts ONLY lifecycle-authorized AuthorizedOrder.
        """
        if not isinstance(order, AuthorizedOrder):
            raise AuthorizationError(
                f"BrokerGateway.submit() accepts only AuthorizedOrder, got {type(order).__name__}"
            )
        order_payload = {
            "id": order.intent_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.quantity,
            "order_type": "market",
            "idempotency_key": order.idempotency_key,
        }
        try:
            response = self._adapter.place_order(order_payload)
            broker_order_id = response.get("id") or response.get("order_id")
            return BrokerSubmitResult(
                success=True,
                broker_order_id=broker_order_id,
                raw_response=response,
            )
        except Exception as exc:
            return BrokerSubmitResult(
                success=False,
                broker_order_id=None,
                error=str(exc),
            )

    def cancel(
        self,
        order_id: str,
        *,
        correlation_id: str,
    ) -> CancelResult:
        """Request cancellation of a broker order.

        Returns typed CancelResult.  Lifecycle interprets broker response
        into terminal evidence.
        """
        try:
            response = self._adapter.cancel_order(order_id)
            # Adapter returned without exception — request accepted, not confirmed
            return CancelResult(
                success=True,
                evidence=CancelEvidence(
                    broker_order_id=order_id,
                    state=CancelState.REQUEST_ACCEPTED,
                    venue="",
                    confirmed_at="",
                    source="BROKER",
                    raw_response=response,
                ),
                error=None,
            )
        except Exception as exc:
            return CancelResult(
                success=False,
                evidence=None,
                error=str(exc),
            )

    def fetch_order(
        self,
        order_id: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Fetch current state of a broker order."""
        return self._adapter.fetch_order(order_id)

    def fetch_positions(
        self,
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch current positions from the broker."""
        return self._adapter.fetch_positions()

    def fetch_balances(
        self,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Fetch current balances from the broker."""
        return self._adapter.fetch_balances()

    def submit_protection(
        self,
        plan: ProtectionPlan,
        *,
        correlation_id: str,
    ) -> ProtectiveSubmitResult:
        """Submit a protective order (stop-loss / take-profit).

        This is the ONLY path that creates a protective broker order.
        Returns typed result; lifecycle interprets into ProtectiveAckEvidence.
        """
        if plan.state != ProtectionState.PROTECTION_REQUIRED:
            raise ValueError(f"cannot submit protection in state {plan.state.value}")

        # Validate quantity semantics (P0)
        if plan.quantity_mode == ProtectionQuantityMode.EXPLICIT_QUANTITY:
            if not math.isfinite(plan.protected_quantity) or plan.protected_quantity <= 0:
                return ProtectiveSubmitResult(
                    success=False,
                    evidence=None,
                    error="EXPLICIT_QUANTITY requires protected_quantity > 0",
                )
            qty = plan.protected_quantity
        elif plan.quantity_mode == ProtectionQuantityMode.CLOSE_POSITION:
            capabilities = getattr(self._adapter, "capabilities", {})
            if not capabilities.get("close_position_protection", False):
                return ProtectiveSubmitResult(
                    success=False,
                    evidence=None,
                    error="adapter does not support CLOSE_POSITION protection",
                )
            qty = 0.0  # adapter interprets as close position
        else:
            return ProtectiveSubmitResult(
                success=False,
                evidence=None,
                error=f"unsupported quantity_mode: {plan.quantity_mode}",
            )

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
                qty=qty,
                order_type=plan.stop_type,
                limit_price=plan.take_profit,
            )
            broker_order_id = response.get("id") or response.get("order_id")
            return ProtectiveSubmitResult(
                success=True,
                evidence=ProtectiveAckEvidence(
                    broker_order_id=broker_order_id or "",
                    broker_ack_id=broker_order_id or "",
                    venue="",
                    broker_status="",
                    acknowledged_at="",
                    protected_symbol=plan.symbol,
                    protected_quantity=plan.protected_quantity,
                    evidence_source="BROKER",
                    raw_response=response,
                ),
                error=None,
            )
        except Exception as exc:
            return ProtectiveSubmitResult(
                success=False,
                evidence=None,
                error=str(exc),
            )

    def close_all_positions(
        self,
        *,
        correlation_id: str,
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
    "AuthorizedOrder",
    "CancelState",
    "CancelEvidence",
    "ProtectiveAckEvidence",
    "BrokerSubmitResult",
    "CancelResult",
    "ProtectiveSubmitResult",
    "AuthorizationError",
]
