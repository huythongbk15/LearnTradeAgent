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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from trading_agent.execution.canonical.adapters import (
    BrokerCancelFact,
    BrokerCancelRequest,
    BrokerClosePositionFact,
    BrokerClosePositionRequest,
    BrokerOrderFact,
    BrokerOrderRequest,
    BrokerPositionFact,
    BrokerSubmitFact,
    BrokerSubmitState,
    CanonicalExecutionAdapter,
)
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    OrderSide,
    OrderType,
    Symbol,
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
    state: "BrokerSubmitState | None" = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    venue: str = "unknown"
    broker_status: str = "unknown"
    observed_at: datetime | None = None


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
    submission: BrokerSubmitResult | None = None


class BrokerGateway:
    """The ONLY capital-changing boundary.

    Parameters
    ----------
    adapter:
        The exchange/broker adapter implementing CanonicalExecutionAdapter.
        The gateway owns the reference; no other module may call the adapter directly.
    store:
        The execution event store for durable authorization verification.
        REQUIRED - no broker I/O is permitted without durable authorization.
    """

    def __init__(
        self,
        adapter: CanonicalExecutionAdapter,
        store: Any,
        lifecycle: Any,
    ) -> None:
        if store is None:
            raise ValueError("BrokerGateway requires a durable execution event store")
        if lifecycle is None:
            raise ValueError("BrokerGateway requires a lifecycle for durable BROKER_IO_STARTED")
        self._adapter = adapter
        self._store = store
        self._lifecycle = lifecycle

    # ── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        authorization_id: str,
        *,
        correlation_id: str,
    ) -> BrokerSubmitResult:
        """Submit an order reconstructed exclusively from durable authorization.

        The caller supplies only an opaque authorization id.  Risk, permission,
        sizing, and venue fields are loaded from ``ORDER_AUTHORIZED`` and cannot
        be overridden after lifecycle authorization.

        The gateway verifies BOTH durable facts before broker I/O:
        1. ORDER_AUTHORIZED exists with matching authorization_id
        2. BROKER_SUBMISSION_REQUESTED exists for the same intent
        """
        if not isinstance(authorization_id, str) or not authorization_id:
            raise AuthorizationError("authorization_id must be a non-empty string")
        else:
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

            # Verify atomic submission ownership: lifecycle MUST have claimed
            # this submission before broker I/O (P0-3).  Unclaimed submissions
            # are rejected to prevent double-execution or unowned orders.
            claim = self._store.submission_claim(intent_id)
            if claim is None:
                raise AuthorizationError(
                    f"submission not claimed for intent {intent_id}: "
                    f"caller must call lifecycle.request_broker_submission() "
                    f"with claimed_by before gateway.submit()"
                )
            if claim["claimed_by"] != correlation_id:
                # Another connection owns this submission.
                existing = self._store.get_latest_broker_event(intent_id)
                if existing is not None:
                    etype, payload = existing
                    return self._reconstruct_broker_result(etype, payload)
                return BrokerSubmitResult(
                    success=False,
                    broker_order_id=None,
                    error="submission already claimed by another connection",
                    state=BrokerSubmitState.UNKNOWN,
                    raw_response={},
                    venue="unknown",
                    broker_status="unknown",
                    observed_at=datetime.now(UTC),
                )

            # Build canonical broker request from DURABLE authorization payload (not from caller object)
            # Convert legacy string types to canonical types
            symbol_str = str(auth["symbol"])
            side_str = str(auth["side"]).lower()
            order_type_str = str(
                auth.get("metadata", {}).get("order_type", "market")
            ).lower()

            # Parse symbol string to Symbol object
            if "/" in symbol_str:
                base, quote = symbol_str.split("/", maxsplit=1)
                symbol_obj = Symbol(
                    base, quote, AssetClass.CRYPTO, MarketType.SPOT, "paper"
                )
            else:
                symbol_obj = Symbol(
                    symbol_str, "USD", AssetClass.STOCK, MarketType.SPOT, "paper"
                )

            if side_str not in {"buy", "sell"}:
                raise AuthorizationError(f"unsupported authorized side {side_str!r}")
            side_enum = OrderSide.BUY if side_str == "buy" else OrderSide.SELL

            # Convert order_type string to OrderType enum
            order_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "stop": OrderType.STOP,
                "stop_limit": OrderType.STOP_LIMIT,
                "trailing_stop": OrderType.TRAILING_STOP,
            }
            if order_type_str not in order_type_map:
                raise AuthorizationError(
                    f"unsupported authorized order type {order_type_str!r}"
                )
            order_type_enum = order_type_map[order_type_str]

            # Convert numeric values to Decimal
            quantity_decimal = Decimal(str(auth["quantity"]))
            price_val = auth.get("metadata", {}).get("price")
            stop_price_val = auth.get("metadata", {}).get("stop_price")
            price_decimal = Decimal(str(price_val)) if price_val is not None else None
            stop_price_decimal = (
                Decimal(str(stop_price_val)) if stop_price_val is not None else None
            )

            request = BrokerOrderRequest(
                intent_id=auth["intent_id"],
                symbol=symbol_obj,
                side=side_enum,
                quantity=quantity_decimal,
                order_type=order_type_enum,
                price=price_decimal,
                stop_price=stop_price_decimal,
                time_in_force=str(
                    auth.get("metadata", {}).get("time_in_force", "day")
                ).upper(),
                idempotency_key=auth["idempotency_key"],
            )
        try:
            # Durable transition CLAIMED → IO_STARTED before broker I/O (P0-3A)
            self._lifecycle.record_broker_io_started(auth["intent_id"])
            # Use canonical adapter.submit_order() returning BrokerSubmitFact
            submit_fact: BrokerSubmitFact = self._adapter.submit_order(request)
            success = submit_fact.state in (
                BrokerSubmitState.ACCEPTED,
                BrokerSubmitState.OPEN,
                BrokerSubmitState.PARTIALLY_FILLED,
                BrokerSubmitState.FILLED,
            )
            return BrokerSubmitResult(
                success=success,
                broker_order_id=submit_fact.broker_order_id,
                error=submit_fact.error,
                state=submit_fact.state,
                raw_response=submit_fact.raw_response,
                venue=submit_fact.venue,
                broker_status=submit_fact.broker_status,
                observed_at=submit_fact.observed_at,
            )
        except Exception as exc:
            return BrokerSubmitResult(
                success=False,
                broker_order_id=None,
                error=str(exc),
                state=BrokerSubmitState.UNKNOWN,
                raw_response={},
                venue="unknown",
                broker_status="unknown",
                observed_at=datetime.now(UTC),
            )

    def _reconstruct_broker_result(
        self, event_type: str, payload: dict[str, Any]
    ) -> BrokerSubmitResult:
        """Reconstruct a BrokerSubmitResult from a durable broker event payload."""
        state_map = {
            "exec.broker_acknowledged": BrokerSubmitState.ACKNOWLEDGED,
            "exec.order_rejected": BrokerSubmitState.REJECTED,
            "exec.broker_state_unknown": BrokerSubmitState.UNKNOWN,
            "exec.local_submission_failed": BrokerSubmitState.FAILED_LOCAL,
            "exec.partial_fill_received": BrokerSubmitState.PARTIALLY_FILLED,
            "exec.fill_received": BrokerSubmitState.FILLED,
        }
        state = state_map.get(event_type, BrokerSubmitState.UNKNOWN)
        success = state in {
            BrokerSubmitState.ACCEPTED,
            BrokerSubmitState.OPEN,
            BrokerSubmitState.PARTIALLY_FILLED,
            BrokerSubmitState.FILLED,
        }
        observed_at = payload.get("observed_at")
        if observed_at and isinstance(observed_at, str):
            try:
                observed_at = datetime.fromisoformat(observed_at)
            except ValueError:
                observed_at = None
        return BrokerSubmitResult(
            success=success,
            broker_order_id=payload.get("broker_order_id") or payload.get("order_id"),
            error=payload.get("reason") or payload.get("error"),
            state=state,
            raw_response=payload.get("raw_response", {}),
            venue=payload.get("venue", "unknown"),
            broker_status=payload.get("broker_status", str(state)),
            observed_at=observed_at,
        )

    def cancel(
        self,
        order_id: str,
        *,
        correlation_id: str,
        symbol: str | None = None,
    ) -> CancelResult:
        """Request cancellation of a broker order.

        Returns typed CancelResult.  Lifecycle interprets broker response
        into terminal evidence.
        """
        try:
            # Use canonical adapter.request_cancel() returning BrokerCancelFact
            from trading_agent.execution.canonical.adapters import BrokerCancelRequest

            symbol_obj = None
            if symbol:
                if "/" in symbol:
                    base, quote = symbol.split("/", maxsplit=1)
                    symbol_obj = Symbol(
                        base, quote, AssetClass.CRYPTO, MarketType.SPOT, "live"
                    )
                else:
                    symbol_obj = Symbol(
                        symbol, "USD", AssetClass.STOCK, MarketType.SPOT, "live"
                    )
            cancel_request = BrokerCancelRequest(
                broker_order_id=order_id,
                symbol=symbol_obj,
                client_order_id=None,
                idempotency_key=None,
            )
            cancel_fact = self._adapter.request_cancel(cancel_request)
            # Map canonical cancel state to internal CancelState
            state_map = {
                "REQUEST_ACCEPTED": CancelState.REQUEST_ACCEPTED,
                "PENDING": CancelState.PENDING,
                "CANCELED": CancelState.CANCELED,
                "REJECTED": CancelState.REJECTED,
                "EXPIRED": CancelState.EXPIRED,
                "UNKNOWN": CancelState.UNKNOWN,
                "FAILED": CancelState.FAILED,
            }
            mapped_state = state_map.get(cancel_fact.state, CancelState.UNKNOWN)
            return CancelResult(
                success=mapped_state
                in {CancelState.REQUEST_ACCEPTED, CancelState.CANCELED},
                evidence=CancelEvidence(
                    broker_order_id=order_id,
                    state=mapped_state,
                    venue=cancel_fact.venue,
                    confirmed_at=cancel_fact.confirmed_at.isoformat()
                    if isinstance(cancel_fact.confirmed_at, datetime)
                    else str(cancel_fact.confirmed_at),
                    source=cancel_fact.source,
                    raw_response=cancel_fact.raw_response,
                ),
                error=cancel_fact.error,
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
        # Use canonical adapter.fetch_order() returning BrokerOrderFact
        order_fact = self._adapter.fetch_order(order_id)
        return {
            "broker_order_id": order_fact.broker_order_id,
            "client_order_id": order_fact.client_order_id,
            "symbol": str(order_fact.symbol),
            "side": order_fact.side.value,
            "order_type": order_fact.order_type.value,
            "quantity": float(order_fact.quantity),
            "filled_quantity": float(order_fact.filled_quantity),
            "price": float(order_fact.price) if order_fact.price is not None else None,
            "stop_price": float(order_fact.stop_price)
            if order_fact.stop_price is not None
            else None,
            "status": order_fact.status,
            "venue": order_fact.venue,
            "created_at": order_fact.created_at.isoformat()
            if isinstance(order_fact.created_at, datetime)
            else str(order_fact.created_at),
            "updated_at": order_fact.updated_at.isoformat()
            if isinstance(order_fact.updated_at, datetime)
            else str(order_fact.updated_at),
            "raw_response": order_fact.raw_response,
        }

    def fetch_positions(
        self,
        *,
        correlation_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch current positions from the broker."""
        # Use canonical adapter.fetch_positions() returning list[BrokerPositionFact]
        position_facts = self._adapter.fetch_positions()
        return [
            {
                "symbol": str(p.symbol),
                "quantity": float(p.quantity),
                "side": p.side.value,
                "entry_price": float(p.entry_price)
                if p.entry_price is not None
                else None,
                "current_price": float(p.current_price)
                if p.current_price is not None
                else None,
                "unrealized_pnl": float(p.unrealized_pnl)
                if p.unrealized_pnl is not None
                else None,
                "realized_pnl": float(p.realized_pnl)
                if p.realized_pnl is not None
                else None,
                "venue": p.venue,
            }
            for p in position_facts
        ]

    def fetch_balances(
        self,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Fetch current balances from the broker."""
        # Use canonical adapter.fetch_balances() returning dict[str, Decimal]
        balances = self._adapter.fetch_balances()
        return {k: float(v) for k, v in balances.items()}

    def submit_protection(
        self,
        authorization_id: str,
        *,
        correlation_id: str,
    ) -> ProtectiveSubmitResult:
        """Submit a reduce-only protective order from durable authorization."""
        if not isinstance(authorization_id, str) or not authorization_id:
            raise AuthorizationError("authorization_id must be a non-empty string")
        auth = self._store.get_latest_authorization_by_auth_id(authorization_id)
        if auth is None:
            raise AuthorizationError(
                f"no durable ORDER_AUTHORIZED found for authorization_id {authorization_id}"
            )
        metadata = auth.get("metadata", {})
        order_type = str(metadata.get("order_type", "")).lower()
        if str(auth.get("side", "")).lower() != "sell" or order_type not in {
            "stop",
            "stop_limit",
            "trailing_stop",
        }:
            raise AuthorizationError(
                "protective submission requires an authorized SELL stop order"
            )

        submission = self.submit(
            authorization_id,
            correlation_id=correlation_id,
        )
        if (
            not submission.success
            or not submission.broker_order_id
            or submission.state
            not in {BrokerSubmitState.ACCEPTED, BrokerSubmitState.OPEN}
        ):
            return ProtectiveSubmitResult(
                success=False,
                evidence=None,
                error=submission.error
                or "protective order did not become a resting acknowledged order",
                submission=submission,
            )
        broker_status = submission.broker_status.lower()
        observed_at = submission.observed_at or datetime.now(UTC)
        return ProtectiveSubmitResult(
            success=True,
            evidence=ProtectiveAckEvidence(
                broker_order_id=submission.broker_order_id,
                broker_ack_id=submission.broker_order_id,
                venue=submission.venue,
                broker_status=broker_status,
                acknowledged_at=observed_at.isoformat(),
                protected_symbol=str(auth["symbol"]),
                protected_quantity=float(auth["quantity"]),
                evidence_source="BROKER",
                raw_response=submission.raw_response,
            ),
            error=None,
            submission=submission,
        )


__all__ = [
    "CanonicalExecutionAdapter",
    "BrokerOrderRequest",
    "BrokerSubmitFact",
    "BrokerSubmitState",
    "BrokerCancelRequest",
    "BrokerCancelFact",
    "BrokerOrderFact",
    "BrokerPositionFact",
    "BrokerClosePositionRequest",
    "BrokerClosePositionFact",
    "BrokerGateway",
    "CancelState",
    "CancelEvidence",
    "ProtectiveAckEvidence",
    "BrokerSubmitResult",
    "CancelResult",
    "ProtectiveSubmitResult",
    "AuthorizationError",
]
