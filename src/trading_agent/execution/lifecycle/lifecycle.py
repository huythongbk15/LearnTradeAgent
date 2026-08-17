"""ExecutionLifecycle — event-sourced order lifecycle aggregate.

Wave C — deterministic recovery.

State is a pure function of the event log:

    state = replay(events)          # deterministic
    apply(state, event)             # idempotent per event_id

Invariant guards (see also ``chaos_invariants``) are enforced *inside* the
command surface, so a fault-injected event stream can never leave the
aggregate in a financially unsafe state:

1. No duplicate live order.
2. No entry when market data is stale.
3. No entry while reconciliation is unresolved.
4. No position without required protective order.
5. No increased exposure while kill switch blocks entry.
6. No replay creating synthetic extra fill.
7. No sell quantity above available free inventory.
8. No mainnet enablement caused by deploy.
9. No unknown broker state silently normalized.

Unknown broker states are *never* silently normalized: they emit
``MANUAL_INTERVENTION_REQUIRED`` and the affected order enters
``MANUAL`` so a human must decide.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Mapping

from trading_agent.execution.canonical import UnifiedRiskDecision
from trading_agent.execution.lifecycle.events import (
    ExecutionEvent,
    ExecutionEventType,
    make_event,
    validate_event,
)
from trading_agent.execution.lifecycle.store import (
    ExecutionEventStore,
    ReservationConflictError,
    SequenceGapError,
)

# ── Status vocabulary ──────────────────────────────────────────────────


class IntentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    REJECTED = "rejected"
    MANUAL = "manual"


class ReconciliationState(str, Enum):
    NONE = "none"
    STARTED = "started"
    RESOLVED = "resolved"


class ExposureEffect(str, Enum):
    """Effect of an order on total exposure."""

    INCREASE = "increase"
    REDUCE = "reduce"
    NEUTRAL = "neutral"


class ExecutionHealth(str, Enum):
    """Global execution health state."""

    NORMAL = "normal"
    RECONCILING = "reconciling"
    MANUAL_BLOCKED = "manual_blocked"
    DATA_UNTRUSTED = "data_untrusted"
    PROTECTION_GAP = "protection_gap"
    REDUCE_ONLY = "reduce_only"


class ProtectionState(str, Enum):
    """Protection status for a position."""

    NONE = "none"
    PROTECTION_REQUIRED = "protection_required"
    PROTECTIVE_SUBMITTING = "protective_submitting"
    PROTECTED = "protected"


LIVE_STATUSES = frozenset(
    {
        IntentStatus.SUBMITTED,
        IntentStatus.ACKNOWLEDGED,
        IntentStatus.PARTIALLY_FILLED,
    }
)

# ── Trusted market data ────────────────────────────────────────────────


@dataclass(frozen=True)
class TrustedPrice:
    """Typed market-data object with freshness and integrity guarantees."""

    price: float
    exchange_timestamp: datetime | None
    received_at: datetime
    sequence_id: int | None = None

    def is_fresh(self, max_age_seconds: float) -> bool:
        """Reject stale, future, or absurd timestamps."""
        now = datetime.now(UTC)
        if self.received_at > now:
            return False  # future timestamp / clock skew
        age = (now - self.received_at).total_seconds()
        if age > max_age_seconds:
            return False
        # Validate exchange timestamp if present
        if self.exchange_timestamp is not None:
            exchange_dt = self.exchange_timestamp
            if exchange_dt > now:
                return False  # future exchange timestamp
            exchange_age = (now - exchange_dt).total_seconds()
            if (
                exchange_age > max_age_seconds * 2
            ):  # Allow 2x buffer for exchange latency
                return False
        return True


# ── Violations ─────────────────────────────────────────────────────────


class InvariantViolation(RuntimeError):
    """A financial safety invariant was violated."""

    def __init__(self, invariant: str, detail: str = ""):
        self.invariant = invariant
        self.detail = detail
        message = f"invariant '{invariant}' violated"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class LifecycleError(RuntimeError):
    """Invalid lifecycle transition (rejected, no event emitted)."""


# ── Projected state ────────────────────────────────────────────────────


@dataclass
class OrderState:
    intent_id: str
    symbol: str
    side: str
    size: float
    status: IntentStatus = IntentStatus.PENDING
    risk_approved: bool = False
    risk_decision: UnifiedRiskDecision | None = None
    broker_order_id: str | None = None
    exchange_order_id: str | None = None
    filled_size: float = 0.0
    authorized_quantity: float = 0.0
    reserved_quantity: float = 0.0
    released_quantity: float = 0.0
    avg_fill_price: float | None = None
    fees: float = 0.0
    protective_order_ids: list[str] = field(default_factory=list)
    manual_reasons: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_STATUSES

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled_size)

    @property
    def remaining_reserved_quantity(self) -> float:
        """Unfilled SELL quantity still locked by event-sourced evidence."""
        return max(
            0.0,
            self.reserved_quantity - self.filled_size - self.released_quantity,
        )


@dataclass
class ProtectiveOrderState:
    order_id: str
    symbol: str
    kind: str  # "stop_loss" | "take_profit"
    trigger_price: float
    status: str = "active"


@dataclass
class LifecycleState:
    """Fully replayed aggregate state (a pure function of the log)."""

    orders: dict[str, OrderState] = field(default_factory=dict)
    protective_orders: dict[str, ProtectiveOrderState] = field(default_factory=dict)
    reconciliation: ReconciliationState = ReconciliationState.NONE
    last_event_ids: dict[str, str] = field(default_factory=dict)
    state_version: int = 0
    execution_health: ExecutionHealth = ExecutionHealth.NORMAL
    protection_state: dict[str, ProtectionState] = field(default_factory=dict)
    manual_blocked: bool = False
    unresolved_manual_intents: set[str] = field(default_factory=set)

    def order(self, intent_id: str) -> OrderState | None:
        return self.orders.get(intent_id)

    def clone(self) -> "LifecycleState":
        return LifecycleState(
            orders={k: v for k, v in self.orders.items()},
            protective_orders={k: v for k, v in self.protective_orders.items()},
            reconciliation=self.reconciliation,
            last_event_ids=dict(self.last_event_ids),
            state_version=self.state_version,
            execution_health=self.execution_health,
            protection_state=dict(self.protection_state),
            manual_blocked=self.manual_blocked,
            unresolved_manual_intents=set(self.unresolved_manual_intents),
        )


# ── Guards plumbing ────────────────────────────────────────────────────

PriceSource = Callable[[str], TrustedPrice | None]  # symbol -> trusted price
InventorySource = Callable[[str, str], float]  # symbol, side -> free inventory


def _default_price_source() -> PriceSource:
    return lambda symbol: None


def _default_inventory_source() -> InventorySource:
    return lambda symbol, side: math.inf


# ── Aggregate ──────────────────────────────────────────────────────────


class ExecutionLifecycle:
    """Event-sourced execution lifecycle aggregate.

    Parameters
    ----------
    store:
        Connected append-only event store (the single source of truth).
    kill_switch_active:
        Callable returning True when the kill switch blocks new entries.
        Mirrors ``TRADING_ENTRY_KILL_SWITCH`` semantics.
    price_source:
        Callable ``symbol -> fresh price or None``.  ``None`` or an age
        beyond ``max_price_age_seconds`` means stale market data.
    inventory_source:
        Callable ``(symbol, side) -> free inventory`` for sell-size checks.
    max_price_age_seconds:
        Freshness bound for the ``stale market data`` invariant.
    require_protective_order:
        When True, a fill that creates a position must carry a protective
        order (created in the same fill flow).
    """

    def __init__(
        self,
        store: ExecutionEventStore,
        *,
        kill_switch_active: Callable[[], bool] | None = None,
        price_source: PriceSource | None = None,
        inventory_source: InventorySource | None = None,
        max_price_age_seconds: float = 60.0,
        require_protective_order: bool = True,
    ):
        self.store = store
        self._kill_switch = kill_switch_active or (lambda: False)
        self._price_source = price_source or _default_price_source()
        self._inventory_source = inventory_source or _default_inventory_source()
        self.broker_confirm_cancel: Callable[[str], bool] | None = None
        self.max_price_age_seconds = max_price_age_seconds
        self.require_protective_order = require_protective_order
        self.state = LifecycleState()

    # ── Helpers ──────────────────────────────────────────────────────────

    def active_sell_reservations(
        self,
        symbol: str | None = None,
        *,
        exclude_intent_id: str | None = None,
    ) -> float:
        """Return active SELL locks reconstructed from lifecycle events."""
        return sum(
            order.remaining_reserved_quantity
            for intent_id, order in self.state.orders.items()
            if order.side == "sell"
            and (symbol is None or order.symbol == symbol)
            and intent_id != exclude_intent_id
        )

    def _available_sell_inventory(
        self,
        symbol: str,
        *,
        exclude_intent_id: str | None = None,
    ) -> float:
        authorized = float(self._inventory_source(symbol, "sell"))
        if not math.isfinite(authorized) or authorized < 0:
            return math.nan
        return max(
            0.0,
            authorized
            - self.active_sell_reservations(
                symbol,
                exclude_intent_id=exclude_intent_id,
            ),
        )

    def _determine_exposure_effect(
        self,
        side: str,
        size: float,
        symbol: str,
        *,
        exclude_intent_id: str | None = None,
    ) -> ExposureEffect:
        """Determine resulting spot-long exposure after local reservations."""
        if side == "buy":
            return ExposureEffect.INCREASE
        available = self._available_sell_inventory(
            symbol,
            exclude_intent_id=exclude_intent_id,
        )
        if math.isfinite(available) and size <= available + 1e-9:
            return ExposureEffect.REDUCE
        return ExposureEffect.INCREASE

    def _permission_result(
        self,
        side: str,
        size: float,
        symbol: str,
        *,
        require_market_data: bool,
        exclude_intent_id: str | None = None,
        broker_state: str | None = None,
        draft: bool = False,
        risk_decision: UnifiedRiskDecision | None = None,
    ):
        # Local import avoids a module cycle: permission types intentionally
        # reuse lifecycle's canonical health/exposure enums.
        from trading_agent.execution.permission import (
            PermissionContext,
            evaluate_order_permission,
        )

        available = (
            self._available_sell_inventory(
                symbol,
                exclude_intent_id=exclude_intent_id,
            )
            if side == "sell"
            else 0.0
        )
        protection_state = (
            ProtectionState.PROTECTION_REQUIRED.value
            if ProtectionState.PROTECTION_REQUIRED
            in self.state.protection_state.values()
            else ProtectionState.NONE.value
        )
        return evaluate_order_permission(
            PermissionContext(
                execution_health=self.state.execution_health,
                exposure_effect=self._determine_exposure_effect(
                    side,
                    size,
                    symbol,
                    exclude_intent_id=exclude_intent_id,
                ),
                risk_decision=risk_decision,
                trusted_price=self._price_source(symbol),
                max_price_age_seconds=self.max_price_age_seconds,
                reconciliation_state=self.state.reconciliation.value,
                protection_state=protection_state,
                manual_blocked=self.state.manual_blocked,
                kill_switch_active=self._kill_switch(),
                data_trust=(
                    "untrusted"
                    if self.state.execution_health == ExecutionHealth.DATA_UNTRUSTED
                    else "trusted"
                ),
                inventory_state=("known" if math.isfinite(available) else "unknown"),
                free_inventory=available,
                authorized_sellable_inventory=available,
                order_size=size,
                order_side=side,
                require_fresh_market_data=require_market_data,
                enforce_inventory=require_market_data,
                broker_state=broker_state,
                draft=draft,
            )
        )

    def _enforce_permission(
        self,
        side: str,
        size: float,
        symbol: str,
        *,
        require_market_data: bool,
        exclude_intent_id: str | None = None,
        broker_state: str | None = None,
        risk_decision: UnifiedRiskDecision | None = None,
    ):
        from trading_agent.execution.permission import OrderPermission

        result = self._permission_result(
            side,
            size,
            symbol,
            require_market_data=require_market_data,
            exclude_intent_id=exclude_intent_id,
            broker_state=broker_state,
            risk_decision=risk_decision,
        )
        if result.permission == OrderPermission.BLOCK:
            raise InvariantViolation(result.reason.value, result.detail)
        return result

    def _check_price(self, symbol: str) -> TrustedPrice:
        """Validate and return trusted price. Raises if stale/invalid."""
        price = self._price_source(symbol)
        if price is None:
            raise InvariantViolation(
                "no_entry_when_market_data_stale",
                f"no trusted price for {symbol}",
            )
        if not math.isfinite(price.price) or price.price <= 0:
            raise InvariantViolation(
                "no_entry_when_market_data_stale",
                f"invalid price {price.price} for {symbol}",
            )
        if not price.is_fresh(self.max_price_age_seconds):
            raise InvariantViolation(
                "no_entry_when_market_data_stale",
                f"stale price for {symbol}: age > {self.max_price_age_seconds}s",
            )
        return price

    # ── Deterministic replay ────────────────────────────────────────────

    def replay(self, events: list[ExecutionEvent]) -> LifecycleState:
        """Replay events in seq order into a fresh state (deterministic)."""
        state = LifecycleState()
        seen: set[str] = set()
        for event in sorted(events, key=lambda e: (e.aggregate_id, e.seq)):
            if event.event_id in seen:
                continue  # duplicate event — idempotent
            seen.add(event.event_id)
            self._apply(state, event)
        self.state = state
        return state

    def load(self) -> LifecycleState:
        """Load + replay the persisted log (crash recovery entry point)."""
        events = self.store.read_events()
        return self.replay(events)

    _EVENT_HANDLERS = {
        ExecutionEventType.ORDER_INTENT_CREATED: "_on_order_intent_created",
        ExecutionEventType.RISK_APPROVED: "_on_risk_approved",
        ExecutionEventType.ORDER_SUBMITTED: "_on_order_submitted",
        ExecutionEventType.ORDER_REJECTED: "_on_order_rejected",
        ExecutionEventType.BROKER_ACKNOWLEDGED: "_on_broker_acknowledged",
        ExecutionEventType.PARTIAL_FILL_RECEIVED: "_on_partial_fill_received",
        ExecutionEventType.FILL_RECEIVED: "_on_fill_received",
        ExecutionEventType.FEE_BOOKED: "_on_fee_booked",
        ExecutionEventType.CANCEL_REQUESTED: "_on_cancel_requested",
        ExecutionEventType.CANCEL_CONFIRMED: "_on_cancel_confirmed",
        ExecutionEventType.PROTECTIVE_ORDER_CREATED: "_on_protective_order_created",
        ExecutionEventType.PROTECTIVE_ORDER_ACKNOWLEDGED: "_on_protective_order_acknowledged",
        ExecutionEventType.PROTECTIVE_ORDER_REPLACED: "_on_protective_order_replaced",
        ExecutionEventType.RECONCILIATION_STARTED: "_on_reconciliation_started",
        ExecutionEventType.RECONCILIATION_RESOLVED: "_on_reconciliation_resolved",
        ExecutionEventType.MANUAL_INTERVENTION_REQUIRED: "_on_manual_intervention_required",
    }

    def _apply(self, state: LifecycleState, event: ExecutionEvent) -> None:
        """Apply one event through exactly one deterministic semantic handler."""
        handler_name = self._EVENT_HANDLERS.get(event.event_type)
        if handler_name is None:
            raise LifecycleError(f"no semantic handler for {event.event_type.value}")
        getattr(self, handler_name)(state, event)
        state.last_event_ids[event.aggregate_id] = event.event_id
        state.state_version += 1

    def _on_order_intent_created(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        payload = event.payload
        state.orders[event.aggregate_id] = OrderState(
            intent_id=event.aggregate_id,
            symbol=payload["symbol"],
            side=payload["side"],
            size=float(payload["size"]),
            created_at=event.occurred_at,
        )

    def _on_risk_approved(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            order.risk_approved = True
            order.status = IntentStatus.APPROVED
            # Reconstruct risk decision from persisted event payload
            risk_decision_data = event.payload.get("risk_decision")
            if risk_decision_data is not None:
                order.risk_decision = UnifiedRiskDecision(**risk_decision_data)

    def _on_order_submitted(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None and order.status == IntentStatus.APPROVED:
            order.status = IntentStatus.SUBMITTED
            order.exchange_order_id = event.payload.get("exchange_order_id")
            if order.side == "sell":
                order.authorized_quantity = float(event.payload["authorized_quantity"])
                order.reserved_quantity = float(event.payload["reserved_quantity"])

    @staticmethod
    def _release_sell_remainder(order: OrderState) -> None:
        if order.side == "sell":
            order.released_quantity += order.remaining_reserved_quantity

    def _on_order_rejected(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            self._release_sell_remainder(order)
            order.status = IntentStatus.REJECTED

    def _on_broker_acknowledged(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            order.broker_order_id = (
                event.payload.get("broker_order_id") or order.broker_order_id
            )
            order.status = IntentStatus.ACKNOWLEDGED

    def _apply_fill(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is None:
            return
        size = float(event.payload["size"])
        price = float(event.payload["price"])
        previous_filled = order.filled_size
        order.filled_size = min(order.size, previous_filled + size)
        if order.avg_fill_price is None:
            order.avg_fill_price = price
        elif previous_filled > 0:
            order.avg_fill_price = (
                previous_filled * order.avg_fill_price + size * price
            ) / order.filled_size
        order.status = (
            IntentStatus.FILLED
            if order.filled_size >= order.size - 1e-9
            else IntentStatus.PARTIALLY_FILLED
        )
        if self.require_protective_order and order.side == "buy":
            state.protection_state[event.aggregate_id] = (
                ProtectionState.PROTECTION_REQUIRED
            )

    def _on_partial_fill_received(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        self._apply_fill(state, event)

    def _on_fill_received(self, state: LifecycleState, event: ExecutionEvent) -> None:
        self._apply_fill(state, event)

    def _on_fee_booked(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            order.fees += float(event.payload.get("fee", 0.0))

    def _on_cancel_requested(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None and order.status != IntentStatus.FILLED:
            order.status = IntentStatus.CANCEL_REQUESTED

    def _on_cancel_confirmed(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            self._release_sell_remainder(order)
            order.status = IntentStatus.CANCELED

    def _on_protective_order_created(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        payload = event.payload
        state.protective_orders[event.aggregate_id] = ProtectiveOrderState(
            order_id=event.aggregate_id,
            symbol=payload["symbol"],
            kind=payload.get("kind", "stop_loss"),
            trigger_price=float(payload["trigger_price"]),
        )
        parent = payload.get("parent_intent_id")
        if parent and parent in state.orders:
            protective_id = event.aggregate_id
            if protective_id not in state.orders[parent].protective_order_ids:
                state.orders[parent].protective_order_ids.append(protective_id)

    def _on_protective_order_acknowledged(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        protective = state.protective_orders.get(event.aggregate_id)
        parent = event.payload.get("parent_intent_id")
        if protective is not None and parent and parent in state.orders:
            state.protection_state[parent] = ProtectionState.PROTECTED

    def _on_protective_order_replaced(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        protective = state.protective_orders.get(event.aggregate_id)
        if protective is not None:
            protective.trigger_price = float(event.payload["trigger_price"])

    def _on_reconciliation_started(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        state.reconciliation = ReconciliationState.STARTED
        state.execution_health = ExecutionHealth.RECONCILING

    def _on_reconciliation_resolved(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        state.reconciliation = ReconciliationState.RESOLVED
        if not state.unresolved_manual_intents:
            state.manual_blocked = False
            if state.execution_health in {
                ExecutionHealth.MANUAL_BLOCKED,
                ExecutionHealth.RECONCILING,
            }:
                state.execution_health = ExecutionHealth.NORMAL

    def _on_manual_intervention_required(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            order.status = IntentStatus.MANUAL
            reason = event.payload.get("reason", "manual intervention required")
            if reason not in order.manual_reasons:
                order.manual_reasons.append(reason)
        state.manual_blocked = True
        state.unresolved_manual_intents.add(event.aggregate_id)
        if state.execution_health != ExecutionHealth.PROTECTION_GAP:
            state.execution_health = ExecutionHealth.MANUAL_BLOCKED

    # ── Command surface (each command validates + appends) ──────────────

    def _emit(
        self, etype: ExecutionEventType, aggregate_id: str, payload: dict
    ) -> ExecutionEvent:
        event = make_event(
            etype,
            aggregate_id,
            seq=self.store.max_seq(aggregate_id) + 1,
            payload=payload,
        )
        validate_event(event)
        try:
            inserted = self.store.append(event)
        except ReservationConflictError as exc:
            raise InvariantViolation(
                "active_sell_reservations_never_exceed_authorized_inventory",
                str(exc),
            ) from exc
        except SequenceGapError:
            raise
        if inserted:
            self._apply(self.state, event)
        return event

    def create_order_intent(
        self,
        intent_id: str,
        symbol: str,
        side: str,
        size: float,
    ) -> ExecutionEvent:
        """Invariant 5: no increased exposure while kill switch blocks entry."""
        if side not in ("buy", "sell"):
            raise LifecycleError(f"side must be buy|sell, got {side!r}")
        if size <= 0:
            raise LifecycleError("size must be positive")
        # A reconciliation window may accept a draft intent for audit/replay,
        # but submission remains fail-closed below.  Other controls (manual
        # block, protection gap, kill switch) still reject risk-increasing
        # intent creation immediately.
        from trading_agent.execution.permission import OrderPermission, PermissionReason

        draft_permission = self._permission_result(
            side,
            size,
            symbol,
            require_market_data=False,
            draft=True,
        )
        if (
            draft_permission.permission == OrderPermission.BLOCK
            and draft_permission.reason != PermissionReason.RECONCILIATION_UNRESOLVED
        ):
            raise InvariantViolation(
                draft_permission.reason.value, draft_permission.detail
            )
        if intent_id in self.state.orders:
            raise LifecycleError(f"intent {intent_id} already exists")
        return self._emit(
            ExecutionEventType.ORDER_INTENT_CREATED,
            intent_id,
            {"symbol": symbol, "side": side, "size": size},
        )

    def approve_risk(
        self,
        intent_id: str,
        *,
        rationale: str = "",
        risk_decision: UnifiedRiskDecision | None = None,
    ) -> ExecutionEvent:
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        if order.status not in (IntentStatus.PENDING, IntentStatus.APPROVED):
            raise LifecycleError(
                f"intent {intent_id} not approvable in {order.status.value}"
            )
        # Store risk decision for permission checks on submit
        order.risk_decision = risk_decision
        order.risk_approved = True
        payload = {"rationale": rationale}
        if risk_decision is not None:
            # Persist full risk decision evidence for audit/replay
            payload["risk_decision"] = asdict(risk_decision)
        return self._emit(
            ExecutionEventType.RISK_APPROVED,
            intent_id,
            payload,
        )

    def submit_order(
        self,
        intent_id: str,
        *,
        exchange_order_id: str | None = None,
    ) -> ExecutionEvent:
        """Submit an order to the broker.

        Guards (invariants):
        * 1 no duplicate live order — an intent cannot have two live orders;
        * 2 no entry when market data is stale;
        * 3 no entry while reconciliation is unresolved;
        * 5 no increased exposure while kill switch blocks entry.
        """
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        # Invariant 1: no duplicate live order for the same intent.
        if order.is_live:
            raise InvariantViolation(
                "no_duplicate_live_order",
                f"intent {intent_id} already {order.status.value}",
            )
        if order.status in (
            IntentStatus.FILLED,
            IntentStatus.CANCEL_REQUESTED,
            IntentStatus.CANCELED,
            IntentStatus.MANUAL,
        ):
            raise LifecycleError(f"intent {intent_id} already {order.status.value}")
        if order.status != IntentStatus.APPROVED:
            raise LifecycleError(
                f"intent {intent_id} must be risk-approved before submit "
                f"(status={order.status.value})"
            )
        self._enforce_permission(
            order.side,
            order.size,
            order.symbol,
            require_market_data=True,
            exclude_intent_id=intent_id,
            risk_decision=order.risk_decision,
        )
        payload = {
            "order_id": intent_id,
            "exchange_order_id": exchange_order_id or "",
        }
        if order.side == "sell":
            authorized = float(self._inventory_source(order.symbol, "sell"))
            payload.update(
                symbol=order.symbol,
                side=order.side,
                authorized_quantity=authorized,
                reserved_quantity=order.size,
            )
        return self._emit(
            ExecutionEventType.ORDER_SUBMITTED,
            intent_id,
            payload,
        )

    def acknowledge_broker(
        self, intent_id: str, broker_order_id: str
    ) -> ExecutionEvent:
        order = self.state.order(intent_id)
        if order is None or order.status != IntentStatus.SUBMITTED:
            raise LifecycleError(
                f"cannot ack {intent_id}: status={order.status.value if order else 'unknown'}"
            )
        return self._emit(
            ExecutionEventType.BROKER_ACKNOWLEDGED,
            intent_id,
            {"order_id": intent_id, "broker_order_id": broker_order_id},
        )

    def receive_fill(
        self,
        intent_id: str,
        size: float,
        price: float,
        *,
        protective_trigger: float | None = None,
    ) -> ExecutionEvent:
        """Record a fill (partial or full).

        Invariants:
        * 6 no replay creating synthetic extra fill — fill is rejected if the
          order does not exist or the size exceeds the remaining quantity;
        * 7 no sell above available free inventory;
        * 4 no position without required protective order — a buy fill that
          would leave a residual position must have a protective trigger.
        """
        order = self.state.order(intent_id)
        if order is None:
            raise InvariantViolation(
                "no_replay_creating_synthetic_extra_fill",
                f"fill for unknown intent {intent_id}",
            )
        if order.status not in LIVE_STATUSES | {IntentStatus.APPROVED}:
            raise InvariantViolation(
                "no_replay_creating_synthetic_extra_fill",
                f"fill for intent {intent_id} in {order.status.value}",
            )
        if size <= 0 or price <= 0:
            raise InvariantViolation(
                "no_replay_creating_synthetic_extra_fill",
                "fill size/price must be positive",
            )
        remaining = order.remaining
        if size > remaining + 1e-9:
            raise InvariantViolation(
                "no_replay_creating_synthetic_extra_fill",
                f"fill {size} exceeds remaining {remaining}",
            )
        # Fills consume the immutable reservation, not a moving free-balance snapshot.
        if order.side == "sell" and size > order.remaining_reserved_quantity + 1e-9:
            raise InvariantViolation(
                "active_sell_reservations_never_exceed_authorized_inventory",
                f"fill {size} exceeds remaining reservation "
                f"{order.remaining_reserved_quantity} for {order.symbol}",
            )
        is_full = size >= remaining - 1e-9
        etype = (
            ExecutionEventType.FILL_RECEIVED
            if is_full
            else ExecutionEventType.PARTIAL_FILL_RECEIVED
        )
        payload_with_trigger = {
            "order_id": intent_id,
            "size": size,
            "price": price,
            "protective_trigger": protective_trigger,
        }
        event = self._emit(
            etype,
            intent_id,
            payload_with_trigger,
        )
        # Invariant 4: buy fill must carry a protective order.
        if self.require_protective_order and order.side == "buy":
            self.state.protection_state[intent_id] = ProtectionState.PROTECTION_REQUIRED
            if protective_trigger is None:
                # Residual long position without any protective order — never
                # silently accepted: flag for manual review.
                self.state.execution_health = ExecutionHealth.PROTECTION_GAP
                self.require_manual_intervention(
                    intent_id,
                    reason="position created without protective order",
                )
        return event

    def book_fee(self, intent_id: str, fee: float) -> ExecutionEvent:
        if fee < 0:
            raise LifecycleError("fee must be non-negative")
        return self._emit(
            ExecutionEventType.FEE_BOOKED,
            intent_id,
            {"order_id": intent_id, "fee": fee},
        )

    def request_cancel(self, intent_id: str, reason: str = "") -> ExecutionEvent:
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        if order.status not in LIVE_STATUSES | {IntentStatus.APPROVED}:
            raise LifecycleError(f"cannot cancel {intent_id} in {order.status.value}")
        return self._emit(
            ExecutionEventType.CANCEL_REQUESTED,
            intent_id,
            {"order_id": intent_id, "reason": reason},
        )

    def confirm_cancel(self, intent_id: str) -> ExecutionEvent:
        order = self.state.order(intent_id)
        if order is None or order.status != IntentStatus.CANCEL_REQUESTED:
            raise LifecycleError(f"no pending cancel for {intent_id}")
        if self.broker_confirm_cancel is not None and not self.broker_confirm_cancel(
            intent_id
        ):
            raise LifecycleError(
                f"broker unreachable: cancel for {intent_id} not confirmed"
            )
        return self._emit(
            ExecutionEventType.CANCEL_CONFIRMED,
            intent_id,
            {"order_id": intent_id},
        )

    def reject_order(self, intent_id: str, reason: str = "") -> ExecutionEvent:
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        if order.status in {
            IntentStatus.FILLED,
            IntentStatus.CANCELED,
            IntentStatus.REJECTED,
        }:
            raise LifecycleError(f"cannot reject {intent_id} in {order.status.value}")
        return self._emit(
            ExecutionEventType.ORDER_REJECTED,
            intent_id,
            {"order_id": intent_id, "reason": reason},
        )

    def create_protective_order(
        self,
        symbol: str,
        kind: str,
        trigger_price: float,
        *,
        parent_intent_id: str | None = None,
    ) -> ExecutionEvent:
        if trigger_price <= 0:
            raise LifecycleError("trigger_price must be positive")
        order_id = (
            f"prot_{parent_intent_id or symbol}_{len(self.state.protective_orders)}"
        )
        return self._emit(
            ExecutionEventType.PROTECTIVE_ORDER_CREATED,
            order_id,
            {
                "symbol": symbol,
                "kind": kind,
                "trigger_price": trigger_price,
                "parent_intent_id": parent_intent_id or "",
            },
        )

    def acknowledge_protective_order(
        self,
        protective_order_id: str,
        *,
        broker_ack_id: str | None = None,
    ) -> ExecutionEvent:
        protective = self.state.protective_orders.get(protective_order_id)
        if protective is None:
            raise LifecycleError(f"unknown protective order {protective_order_id}")
        parent_intent_id = None
        for intent_id, order in self.state.orders.items():
            if protective_order_id in order.protective_order_ids:
                parent_intent_id = intent_id
                break
        if parent_intent_id is None:
            raise LifecycleError(
                f"protective order {protective_order_id} has no parent intent"
            )
        payload = {
            "protective_order_id": protective_order_id,
            "parent_intent_id": parent_intent_id,
        }
        if broker_ack_id is not None:
            payload["broker_ack_id"] = broker_ack_id
        return self._emit(
            ExecutionEventType.PROTECTIVE_ORDER_ACKNOWLEDGED,
            protective_order_id,
            payload,
        )

    def replace_protective_order(
        self, protective_order_id: str, trigger_price: float
    ) -> ExecutionEvent:
        if protective_order_id not in self.state.protective_orders:
            raise LifecycleError(f"unknown protective order {protective_order_id}")
        if trigger_price <= 0:
            raise LifecycleError("trigger_price must be positive")
        return self._emit(
            ExecutionEventType.PROTECTIVE_ORDER_REPLACED,
            protective_order_id,
            {"trigger_price": trigger_price},
        )

    def start_reconciliation(self) -> ExecutionEvent:
        return self._emit(
            ExecutionEventType.RECONCILIATION_STARTED,
            "reconciliation",
            {},
        )

    def resolve_reconciliation(self, outcome: str = "resolved") -> ExecutionEvent:
        if self.state.reconciliation != ReconciliationState.STARTED:
            raise LifecycleError("no reconciliation in progress")
        if self.state.unresolved_manual_intents:
            raise LifecycleError(
                f"cannot resolve reconciliation with unresolved manual issues: "
                f"{sorted(self.state.unresolved_manual_intents)}"
            )
        return self._emit(
            ExecutionEventType.RECONCILIATION_RESOLVED,
            "reconciliation",
            {"outcome": outcome},
        )

    def require_manual_intervention(
        self, intent_id: str, reason: str
    ) -> ExecutionEvent:
        """Invariant 9: unknown broker state → MANUAL, never silent normalize."""
        self.state.manual_blocked = True
        self.state.unresolved_manual_intents.add(intent_id)
        # Preserve PROTECTION_GAP if already set; otherwise use MANUAL_BLOCKED.
        if self.state.execution_health != ExecutionHealth.PROTECTION_GAP:
            self.state.execution_health = ExecutionHealth.MANUAL_BLOCKED
        return self._emit(
            ExecutionEventType.MANUAL_INTERVENTION_REQUIRED,
            intent_id,
            {"reason": reason},
        )

    # ── Reconciliation with broker state ────────────────────────────────

    def reconcile_broker_state(
        self,
        broker_states: Mapping[str, str],
        *,
        known_states: frozenset[str] = frozenset(
            {"open", "closed", "canceled", "rejected", "partial"}
        ),
    ) -> dict[str, Any]:
        """Reconcile open orders against broker state (invariant 9).

        Every open order must be present with a *known* broker status.
        Unknown status → MANUAL_INTERVENTION_REQUIRED (never normalized).
        """
        self.start_reconciliation()
        report: dict[str, Any] = {"unknown": [], "synced": [], "manual": []}
        for intent_id, order in self.state.orders.items():
            if not order.is_live:
                continue
            broker_status = broker_states.get(intent_id) or broker_states.get(
                order.exchange_order_id or ""
            )
            if broker_status is None:
                self.require_manual_intervention(
                    intent_id,
                    reason=f"broker has no record of live order {intent_id}",
                )
                report["manual"].append(intent_id)
                report["unknown"].append(intent_id)
                continue
            if broker_status not in known_states:
                self.require_manual_intervention(
                    intent_id,
                    reason=f"unknown broker status '{broker_status}' for {intent_id}",
                )
                report["manual"].append(intent_id)
                report["unknown"].append(intent_id)
                continue
            report["synced"].append(intent_id)
        if report["manual"]:
            self.state.execution_health = ExecutionHealth.MANUAL_BLOCKED
            # Do NOT auto-resolve when manual issues remain — operator must act.
            return report
        self.resolve_reconciliation(outcome="audited")
        return report

    # ── Queries ─────────────────────────────────────────────────────────

    def order(self, intent_id: str) -> OrderState | None:
        return self.state.order(intent_id)

    def snapshot_state(self) -> dict[str, Any]:
        """Serializable state for the durable snapshot (store.save_snapshot)."""
        return {
            "orders": {
                k: {
                    "intent_id": v.intent_id,
                    "symbol": v.symbol,
                    "side": v.side,
                    "size": v.size,
                    "status": v.status.value,
                    "risk_approved": v.risk_approved,
                    "broker_order_id": v.broker_order_id,
                    "exchange_order_id": v.exchange_order_id,
                    "filled_size": v.filled_size,
                    "authorized_quantity": v.authorized_quantity,
                    "reserved_quantity": v.reserved_quantity,
                    "released_quantity": v.released_quantity,
                    "remaining_reserved_quantity": v.remaining_reserved_quantity,
                    "avg_fill_price": v.avg_fill_price,
                    "fees": v.fees,
                    "protective_order_ids": v.protective_order_ids,
                    "manual_reasons": v.manual_reasons,
                }
                for k, v in self.state.orders.items()
            },
            "protective_orders": {
                k: {
                    "order_id": v.order_id,
                    "symbol": v.symbol,
                    "kind": v.kind,
                    "trigger_price": v.trigger_price,
                    "status": v.status,
                }
                for k, v in self.state.protective_orders.items()
            },
            "reconciliation": self.state.reconciliation.value,
            "execution_health": self.state.execution_health.value,
            "protection_state": {
                k: v.value for k, v in self.state.protection_state.items()
            },
            "manual_blocked": self.state.manual_blocked,
            "unresolved_manual_intents": sorted(self.state.unresolved_manual_intents),
        }

    def last_seq(self) -> int:
        seqs = [self.store.max_seq(agg) for agg in self.store.aggregates()]
        return max(seqs) if seqs else 0


__all__ = [
    "ExecutionLifecycle",
    "IntentStatus",
    "ReconciliationState",
    "ExposureEffect",
    "ExecutionHealth",
    "ProtectionState",
    "TrustedPrice",
    "OrderState",
    "ProtectiveOrderState",
    "LifecycleState",
    "InvariantViolation",
    "LifecycleError",
    "LIVE_STATUSES",
    "PriceSource",
    "InventorySource",
]
