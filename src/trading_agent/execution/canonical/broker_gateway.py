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

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id,
            "state": self.state.value,
            "venue": self.venue,
            "confirmed_at": self.confirmed_at,
            "source": self.source,
            "raw_response": self.raw_response,
        }


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id,
            "broker_ack_id": self.broker_ack_id,
            "venue": self.venue,
            "broker_status": self.broker_status,
            "acknowledged_at": self.acknowledged_at,
            "protected_symbol": self.protected_symbol,
            "protected_quantity": self.protected_quantity,
            "evidence_source": self.evidence_source,
            "raw_response": self.raw_response,
        }


@dataclass(frozen=True)
class BrokerSubmitResult:
    """Typed result of a broker submit."""

    success: bool
    broker_order_id: str | None
    error: str | None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrokerOrderRequest:
    """Typed request for a broker order submission.

    Constructed by BrokerGateway from an AuthorizedOrder and passed to the
    adapter's place_order() as a dict payload.
    """

    intent_id: str
    symbol: Any  # Symbol
    side: str
    quantity: float
    order_type: str = "market"
    price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    idempotency_key: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.intent_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.quantity,
            "order_type": self.order_type,
            "price": self.price,
            "stop_price": self.stop_price,
            "time_in_force": self.time_in_force,
            "idempotency_key": self.idempotency_key,
        }


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


@dataclass(frozen=True)
class AuthorizedOrder:
    """Authorization reference for broker submission.

    Instances are constructed ONLY by ExecutionLifecycle during authorization.
    The gateway verifies against durable store and derives broker request
    from the durable ORDER_AUTHORIZED record, NOT from this object.
    """

    intent_id: str
    symbol: Any  # Symbol
    side: str
    quantity: float
    idempotency_key: str
    price_reference: float
    metadata: dict[str, Any] = field(default_factory=dict)
    # Required authorization evidence (verified against durable store)
    risk_decision_id: str = ""
    forecast_fingerprint: str = ""
    model_artifact_id: str = ""
    permission_result: str = ""
    authorization_id: str = ""
    lifecycle_event_id: str = ""
    correlation_id: str = ""
    exposure_effect: str = ""
    current_exposure: float = 0.0
    resulting_exposure: float = 0.0
    authorized_at: str = ""
    authorization_hash: str = ""

    @classmethod
    def _from_authorization_payload(cls, payload: dict[str, Any]) -> "AuthorizedOrder":
        """Reconstruct from durable ORDER_AUTHORIZED payload.

        This is the ONLY way to create a valid AuthorizedOrder.
        """
        return cls(
            intent_id=payload["intent_id"],
            symbol=payload["symbol"],
            side=payload["side"],
            quantity=float(payload["quantity"]),
            idempotency_key=payload["idempotency_key"],
            price_reference=float(payload.get("price_reference", 0.0)),
            metadata=payload.get("metadata", {}),
            risk_decision_id=payload["risk_decision_id"],
            forecast_fingerprint=payload["forecast_fingerprint"],
            model_artifact_id=payload["model_artifact_id"],
            permission_result=payload["permission"],
            authorization_id=payload["authorization_id"],
            lifecycle_event_id=payload.get("lifecycle_event_id", ""),
            correlation_id=payload.get("correlation_id", ""),
            exposure_effect=payload["exposure_effect"],
            current_exposure=float(payload["current_exposure"]),
            resulting_exposure=float(payload["resulting_exposure"]),
            authorized_at=payload["authorized_at"],
            authorization_hash=payload["payload_hash"],
        )


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
    store:
        The execution event store for durable authorization verification.
        REQUIRED - no broker I/O is permitted without durable authorization.
    """

    def __init__(self, adapter: ExchangeAdapter, store: Any) -> None:
        if store is None:
            raise AuthorizationError(
                "BrokerGateway requires a durable execution event store"
            )
        self._adapter = adapter
        self._store = store

    # ── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        authorization_id: str,
        *,
        correlation_id: str,
    ) -> BrokerSubmitResult:
        """Submit an order to the broker using a durable authorization.

        The authorization must have been previously created through the
        lifecycle authorization path and persisted as ORDER_AUTHORIZED.
        The gateway verifies BOTH durable facts before broker I/O:
        1. ORDER_AUTHORIZED exists with matching authorization_id
        2. BROKER_SUBMISSION_REQUESTED exists for the same intent
        """
        # Load authorization from durable store (P0 §15, P0-7)
        auth = self._store.get_latest_authorization_by_auth_id(authorization_id)
        if auth is None:
            raise AuthorizationError(
                f"no durable ORDER_AUTHORIZED found for authorization_id {authorization_id}"
            )

        # Verify BROKER_SUBMISSION_REQUESTED exists (P0-7)
        intent_id = auth["intent_id"]
        submission = self._store.get_latest_submission_request(intent_id)
        if submission is None:
            raise AuthorizationError(
                f"no durable BROKER_SUBMISSION_REQUESTED for intent {intent_id}"
            )

        # Build broker request from DURABLE authorization payload (not from caller object)
        request = BrokerOrderRequest(
            intent_id=auth["intent_id"],
            symbol=auth["symbol"],
            side=auth["side"],
            quantity=float(auth["quantity"]),
            order_type=auth.get("metadata", {}).get("order_type", "market"),
            price=auth.get("metadata", {}).get("price"),
            stop_price=auth.get("metadata", {}).get("stop_price"),
            time_in_force=auth.get("metadata", {}).get("time_in_force", "day"),
            idempotency_key=auth["idempotency_key"],
        )
        try:
            response = self._adapter.place_order(request.to_payload())
            broker_order_id = response.get("id") or response.get("order_id")
            return BrokerSubmitResult(
                success=True,
                broker_order_id=broker_order_id,
                error=None,
                raw_response=response,
            )
        except Exception as exc:
            return BrokerSubmitResult(
                success=False,
                broker_order_id=None,
                error=str(exc),
            )

    def _verify_authorization(self, order: AuthorizedOrder) -> None:
        """Verify authorization against durable lifecycle state."""
        auth = self._store.get_latest_authorization(order.intent_id)
        if auth is None:
            raise AuthorizationError(
                f"no durable ORDER_AUTHORIZED found for intent {order.intent_id}"
            )
        # Verify binding
        if auth.get("authorization_id") != order.authorization_id:
            raise AuthorizationError("authorization_id mismatch")
        if auth.get("idempotency_key") != order.idempotency_key:
            raise AuthorizationError("idempotency_key mismatch")
        # Normalize symbol for comparison (Symbol object vs persisted string)
        auth_symbol = auth.get("symbol")
        order_symbol = order.symbol
        if hasattr(order_symbol, "pair"):
            order_symbol = order_symbol.pair
        if auth_symbol != order_symbol:
            raise AuthorizationError("symbol mismatch")
        if auth.get("side") != order.side:
            raise AuthorizationError("side mismatch")
        if abs(float(auth.get("quantity", 0)) - float(order.quantity)) > 1e-12:
            raise AuthorizationError("quantity mismatch")
        if auth.get("risk_decision_id") != order.risk_decision_id:
            raise AuthorizationError("risk_decision_id mismatch")
        if auth.get("payload_hash") != order.authorization_hash:
            raise AuthorizationError("payload_hash mismatch")

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
            if (
                not math.isfinite(plan.protected_quantity)
                or plan.protected_quantity <= 0
            ):
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
