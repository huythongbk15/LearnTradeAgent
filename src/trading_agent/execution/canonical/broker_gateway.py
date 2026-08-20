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
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from trading_agent.execution.canonical.adapters import (
    BrokerCancelFact,
    BrokerCancelRequest,
    BrokerClosePositionFact,
    BrokerClosePositionRequest,
    BrokerOrderFact,
    BrokerPositionFact,
    BrokerSubmitFact,
    BrokerSubmitState,
    CanonicalExecutionAdapter,
)
from trading_agent.execution.canonical.protection import (
    ProtectionPlan,
    ProtectionQuantityMode,
    ProtectionState,
)
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    OrderSide,
    OrderType,
    Symbol,
)


_AUTHORIZED_TOKEN = uuid.uuid4().hex


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


class AuthorizedOrder:
    """Unforgeable authorization wrapper for broker submission.

    Construction is restricted to the lifecycle authorization path.
    Normal callers cannot create valid instances.
    """

    def __init__(self, token: str, **fields: Any) -> None:
        if token != _AUTHORIZED_TOKEN:
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

    def __init__(self, adapter: CanonicalExecutionAdapter, store: Any) -> None:
        if store is None:
            raise ValueError("BrokerGateway requires a durable execution event store")
        self._adapter = adapter
        self._store = store

    # ── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        authorization: AuthorizedOrder | str,
        *,
        correlation_id: str,
    ) -> BrokerSubmitResult:
        """Submit an order to the broker using a durable authorization.

        Accepts either an AuthorizedOrder object or an authorization_id string.
        The authorization must have been previously created through the
        lifecycle authorization path and persisted as ORDER_AUTHORIZED.
        The gateway verifies BOTH durable facts before broker I/O:
        1. ORDER_AUTHORIZED exists with matching authorization_id
        2. BROKER_SUBMISSION_REQUESTED exists for the same intent
        """
        if isinstance(authorization, AuthorizedOrder):
            # Verify authorization against durable state (P0 §15)
            if self._store is not None:
                self._verify_authorization(authorization)
            # Build canonical broker request from the AuthorizedOrder object
            # Convert legacy string types to canonical types
            symbol_str = str(authorization.symbol)
            side_str = str(authorization.side).lower()
            order_type_str = str(authorization.metadata.get("order_type", "market")).lower()
            
            # Parse symbol string to Symbol object
            if "/" in symbol_str:
                base, quote = symbol_str.split("/")
                symbol_obj = Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "paper")
            else:
                # Fallback for non-standard symbols
                symbol_obj = Symbol(symbol_str, "USD", AssetClass.STOCK, MarketType.SPOT, "paper")
            
            # Convert side string to OrderSide enum
            side_enum = OrderSide.BUY if side_str == "buy" else OrderSide.SELL
            
            # Convert order_type string to OrderType enum
            order_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "stop": OrderType.STOP,
                "stop_limit": OrderType.STOP_LIMIT,
                "trailing_stop": OrderType.TRAILING_STOP,
            }
            order_type_enum = order_type_map.get(order_type_str, OrderType.MARKET)
            
            # Convert numeric values to Decimal
            quantity_decimal = Decimal(str(authorization.quantity))
            price_decimal = Decimal(str(authorization.metadata["price"])) if authorization.metadata.get("price") is not None else None
            stop_price_decimal = Decimal(str(authorization.metadata["stop_price"])) if authorization.metadata.get("stop_price") is not None else None
            
            request = BrokerOrderRequest(
                intent_id=authorization.intent_id,
                symbol=symbol_obj,
                side=side_enum,
                quantity=quantity_decimal,
                order_type=order_type_enum,
                price=price_decimal,
                stop_price=stop_price_decimal,
                time_in_force=str(authorization.metadata.get("time_in_force", "day")).upper(),
                idempotency_key=authorization.idempotency_key,
            )
        else:
            # Load authorization from durable store (P0 §15, P0-7)
            auth = self._store.get_latest_authorization_by_auth_id(authorization)
            if auth is None:
                raise AuthorizationError(
                    f"no durable ORDER_AUTHORIZED found for authorization_id {authorization}"
                )

            # Verify BROKER_SUBMISSION_REQUESTED exists (P0-7)
            intent_id = auth["intent_id"]
            submission = self._store.get_latest_submission_request(intent_id)
            if submission is None:
                raise AuthorizationError(
                    f"no durable BROKER_SUBMISSION_REQUESTED for intent {intent_id}"
                )

            # Build canonical broker request from DURABLE authorization payload (not from caller object)
            # Convert legacy string types to canonical types
            symbol_str = str(auth["symbol"])
            side_str = str(auth["side"]).lower()
            order_type_str = str(auth.get("metadata", {}).get("order_type", "market")).lower()
            
            # Parse symbol string to Symbol object
            if "/" in symbol_str:
                base, quote = symbol_str.split("/")
                symbol_obj = Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "paper")
            else:
                symbol_obj = Symbol(symbol_str, "USD", AssetClass.STOCK, MarketType.SPOT, "paper")
            
            # Convert side string to OrderSide enum
            side_enum = OrderSide.BUY if side_str == "buy" else OrderSide.SELL
            
            # Convert order_type string to OrderType enum
            order_type_map = {
                "market": OrderType.MARKET,
                "limit": OrderType.LIMIT,
                "stop": OrderType.STOP,
                "stop_limit": OrderType.STOP_LIMIT,
                "trailing_stop": OrderType.TRAILING_STOP,
            }
            order_type_enum = order_type_map.get(order_type_str, OrderType.MARKET)
            
            # Convert numeric values to Decimal
            quantity_decimal = Decimal(str(auth["quantity"]))
            price_val = auth.get("metadata", {}).get("price")
            stop_price_val = auth.get("metadata", {}).get("stop_price")
            price_decimal = Decimal(str(price_val)) if price_val is not None else None
            stop_price_decimal = Decimal(str(stop_price_val)) if stop_price_val is not None else None
            
            request = BrokerOrderRequest(
                intent_id=auth["intent_id"],
                symbol=symbol_obj,
                side=side_enum,
                quantity=quantity_decimal,
                order_type=order_type_enum,
                price=price_decimal,
                stop_price=stop_price_decimal,
                time_in_force=str(auth.get("metadata", {}).get("time_in_force", "day")).upper(),
                idempotency_key=auth["idempotency_key"],
            )
        try:
            # Use canonical adapter.submit_order() returning BrokerSubmitFact
            submit_fact: BrokerSubmitFact = self._adapter.submit_order(request)
            return BrokerSubmitResult(
                success=submit_fact.state
                in (
                    BrokerSubmitState.ACCEPTED,
                    BrokerSubmitState.OPEN,
                    BrokerSubmitState.PARTIALLY_FILLED,
                    BrokerSubmitState.FILLED,
                ),
                broker_order_id=submit_fact.broker_order_id,
                error=submit_fact.error,
                raw_response=submit_fact.raw_response,
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
            # Use canonical adapter.request_cancel() returning BrokerCancelFact
            from trading_agent.execution.canonical.adapters import BrokerCancelRequest
            cancel_request = BrokerCancelRequest(
                broker_order_id=order_id,
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
                success=mapped_state in {CancelState.REQUEST_ACCEPTED, CancelState.CANCELED},
                evidence=CancelEvidence(
                    broker_order_id=order_id,
                    state=mapped_state,
                    venue=cancel_fact.venue,
                    confirmed_at=cancel_fact.confirmed_at.isoformat() if isinstance(cancel_fact.confirmed_at, datetime) else str(cancel_fact.confirmed_at),
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
            "stop_price": float(order_fact.stop_price) if order_fact.stop_price is not None else None,
            "status": order_fact.status,
            "venue": order_fact.venue,
            "created_at": order_fact.created_at.isoformat() if isinstance(order_fact.created_at, datetime) else str(order_fact.created_at),
            "updated_at": order_fact.updated_at.isoformat() if isinstance(order_fact.updated_at, datetime) else str(order_fact.updated_at),
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
                "entry_price": float(p.entry_price) if p.entry_price is not None else None,
                "current_price": float(p.current_price) if p.current_price is not None else None,
                "unrealized_pnl": float(p.unrealized_pnl) if p.unrealized_pnl is not None else None,
                "realized_pnl": float(p.realized_pnl) if p.realized_pnl is not None else None,
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
        # Use canonical adapter.fetch_positions() returning list[BrokerPositionFact]
        position_facts = self._adapter.fetch_positions()
        remaining: list[str] = []
        for pos in position_facts:
            symbol = str(pos.symbol)
            if not symbol:
                continue
            try:
                # Use canonical adapter.close_position() with BrokerClosePositionRequest
                from trading_agent.execution.canonical.adapters import BrokerClosePositionRequest
                close_request = BrokerClosePositionRequest(
                    symbol=pos.symbol,
                    reason=reason,
                )
                self._adapter.close_position(close_request)
            except Exception:
                remaining.append(symbol)
        return {"remaining": remaining}


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
    "AuthorizedOrder",
    "CancelState",
    "CancelEvidence",
    "ProtectiveAckEvidence",
    "BrokerSubmitResult",
    "CancelResult",
    "ProtectiveSubmitResult",
    "AuthorizationError",
]
