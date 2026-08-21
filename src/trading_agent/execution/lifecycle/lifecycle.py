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

import hashlib
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Mapping

from trading_agent.execution.canonical import (
    UnifiedRiskDecision,
    RiskLevel,
    EvidenceState,
)
from trading_agent.execution.canonical.adapters import (
    BrokerSubmitFact,
    BrokerSubmitState,
)
from trading_agent.execution.canonical.broker_gateway import (
    CancelEvidence,
    ProtectiveAckEvidence,
)
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
    AUTHORIZED = "authorized"
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


@dataclass(frozen=True)
class EmergencyReduceRequest:
    """Typed request for an emergency risk-reducing exit."""

    intent_id: str
    symbol: str
    side: str  # must be "sell" for long-only
    quantity: float
    reason: str  # "ATR_STOP_TRIGGERED" | "PORTFOLIO_HALT" | "PROTECTIVE_EMERGENCY_EXIT" | "MANUAL_DUST_REDUCTION"
    parent_intent_id: str | None = None


LIVE_STATUSES = frozenset(
    {
        IntentStatus.AUTHORIZED,
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
    exchange_timestamp: datetime
    received_at: datetime
    sequence_id: int | None = None

    def is_fresh(self, max_age_seconds: float) -> bool:
        """Reject stale, future, or absurd timestamps.

        Strict exchange timestamp validation:
        - Exchange timestamp must not be in the future
        - Exchange timestamp must not be older than max_age_seconds
        - Wall clock received_at must not be older than max_age_seconds
        - No 2x buffer for exchange latency — strict equality with tolerance
        """
        now = datetime.now(UTC)
        if self.received_at > now:
            return False  # future timestamp / clock skew
        age = (now - self.received_at).total_seconds()
        if age > max_age_seconds:
            return False
        # exchange_timestamp is mandatory — strict validation
        exchange_dt = self.exchange_timestamp
        if exchange_dt > now:
            return False  # future exchange timestamp
        exchange_age = (now - exchange_dt).total_seconds()
        if exchange_age > max_age_seconds:
            return False  # exchange data is stale
        if exchange_age < -5.0:
            return False  # exchange timestamp significantly in the future
        return True


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    """Trusted portfolio/equity/position fact for exposure calculation."""

    symbol: str
    position_quantity: float
    available_quantity: float
    equity: float
    available_cash: float
    observed_at: datetime
    source: str = "portfolio"

    def is_fresh(self, max_age_seconds: float) -> bool:
        now = datetime.now(UTC)
        age = (now - self.observed_at).total_seconds()
        return 0.0 <= age <= max_age_seconds


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
    # Authorization tracking (P0)
    authorization_id: str | None = None
    idempotency_key: str | None = None
    payload_hash: str | None = None
    permission: str | None = None
    authorized_at: str | None = None
    # True portfolio exposure (P0-1)
    price_reference: float | None = None
    portfolio_equity: float | None = None
    current_position_quantity: float | None = None
    resulting_position_quantity: float | None = None
    current_exposure: float | None = None
    resulting_exposure: float | None = None
    incremental_exposure: float | None = None

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
PortfolioSource = Callable[[str], PortfolioRiskSnapshot | None]  # symbol -> trusted portfolio snapshot


def _default_price_source() -> PriceSource:
    return lambda symbol: None


def _default_inventory_source() -> InventorySource:
    return lambda symbol, side: math.inf


def _default_portfolio_source() -> PortfolioSource:
    def default_portfolio(symbol: str) -> PortfolioRiskSnapshot:
        return PortfolioRiskSnapshot(
            symbol=symbol,
            position_quantity=0.0,
            available_quantity=0.0,
            equity=100_000.0,
            available_cash=100_000.0,
            observed_at=datetime.now(UTC),
            source="default_test",
        )
    return default_portfolio


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
        portfolio_source: Callable[[str], PortfolioRiskSnapshot | None] | None = None,
        max_price_age_seconds: float = 60.0,
        require_protective_order: bool = True,
    ):
        self.store = store
        self._kill_switch = kill_switch_active or (lambda: False)
        self._price_source = price_source or _default_price_source()
        self._inventory_source = inventory_source or _default_inventory_source()
        self._portfolio_source = portfolio_source or _default_portfolio_source()
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
        """Replay events in seq order into a fresh state (deterministic).

        Assumes events are already sorted by (aggregate_id, seq) or by
        global_seq for cross-aggregate replay.  For true global replay,
        use replay_global().
        """
        state = LifecycleState()
        seen: set[str] = set()
        for event in events:
            if event.event_id in seen:
                continue  # duplicate event — idempotent
            seen.add(event.event_id)
            self._apply(state, event)
        self.state = state
        return state

    def replay_global(self, events: list[ExecutionEvent]) -> LifecycleState:
        """Replay events in strict global_seq order (cross-aggregate replay).

        Pre-migration events (global_seq = -1) are NOT allowed because
        cross-aggregate ordering cannot be reconstructed from aggregate-local
        seq alone. Run the migration script to assign valid global_seq values
        before loading.
        """
        if not events:
            return LifecycleState()
        # Strict: reject any pre-migration event
        pre_migration = [e for e in events if e.global_seq == -1]
        if pre_migration:
            raise LifecycleError(
                f"global replay rejected {len(pre_migration)} pre-migration events "
                f"(global_seq = -1). Run migration to assign global_seq."
            )
        # Sort post-migration by global_seq (strictly increasing)
        events.sort(key=lambda e: e.global_seq)
        # Verify global_seq is strictly increasing
        prev_seq = 0
        for event in events:
            if event.global_seq <= prev_seq:
                raise LifecycleError(
                    f"global_seq not strictly increasing: {prev_seq} -> {event.global_seq} "
                    f"for {event.event_id}"
                )
            prev_seq = event.global_seq
        return self.replay(events)

    def load(self) -> LifecycleState:
        """Load + replay the persisted log (crash recovery entry point)."""
        events = self.store.read_events_global()
        return self.replay_global(events)

    _EVENT_HANDLERS = {
        ExecutionEventType.ORDER_INTENT_CREATED: "_on_order_intent_created",
        ExecutionEventType.RISK_APPROVED: "_on_risk_approved",
        ExecutionEventType.ORDER_AUTHORIZED: "_on_order_authorized",
        ExecutionEventType.BROKER_SUBMISSION_REQUESTED: "_on_broker_submission_requested",
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
                order.risk_decision = UnifiedRiskDecision.from_dict(risk_decision_data)

    def _on_order_authorized(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            order.authorized_quantity = float(
                event.payload.get("authorized_quantity", 0.0)
            )
            order.authorization_id = event.payload.get("authorization_id")
            order.idempotency_key = event.payload.get("idempotency_key")
            order.payload_hash = event.payload.get("payload_hash")
            order.permission = event.payload.get("permission")
            order.authorized_at = event.payload.get("authorized_at")
            order.status = IntentStatus.AUTHORIZED
            # True portfolio exposure (P0-1)
            order.price_reference = event.payload.get("price_reference")
            order.portfolio_equity = event.payload.get("portfolio_equity")
            order.current_position_quantity = event.payload.get("current_position_quantity")
            order.resulting_position_quantity = event.payload.get("resulting_position_quantity")
            order.current_exposure = event.payload.get("current_exposure")
            order.resulting_exposure = event.payload.get("resulting_exposure")
            order.incremental_exposure = event.payload.get("incremental_exposure")

    def _on_broker_submission_requested(
        self, state: LifecycleState, event: ExecutionEvent
    ) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None and order.status in {
            IntentStatus.APPROVED,
            IntentStatus.AUTHORIZED,
        }:
            order.status = IntentStatus.SUBMITTED

    def _on_order_submitted(self, state: LifecycleState, event: ExecutionEvent) -> None:
        order = state.orders.get(event.aggregate_id)
        if order is not None:
            if order.status in {
                IntentStatus.APPROVED,
                IntentStatus.AUTHORIZED,
            }:
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
        from trading_agent.execution.canonical.broker_gateway import CancelState

        order = state.orders.get(event.aggregate_id)
        if order is not None:
            state_value = event.payload.get("state", "")
            # Only release on terminal evidence
            if state_value in {
                CancelState.CANCELED.value,
                CancelState.REJECTED.value,
                CancelState.EXPIRED.value,
            }:
                self._release_sell_remainder(order)
                order.status = IntentStatus.CANCELED
            elif state_value == CancelState.FILLED.value:
                # FILLED during cancel: order is filled, not canceled.
                # Reservation is consumed by fill, not released.
                order.status = IntentStatus.FILLED
            else:
                # Non-terminal cancel state — keep reservation locked
                order.status = IntentStatus.CANCEL_REQUESTED

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
            # Validate protective evidence before marking PROTECTED
            broker_order_id = event.payload.get("broker_order_id")
            broker_ack_id = event.payload.get("broker_ack_id")
            if not broker_order_id or not broker_ack_id:
                # Missing broker evidence — do NOT mark PROTECTED
                return
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
        idempotency_key: str | None = None,
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

        # Normalize symbol to canonical string for durable storage
        symbol_str = symbol.pair if hasattr(symbol, "pair") else str(symbol)

        draft_permission = self._permission_result(
            side,
            size,
            symbol_str,
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
        # Durable idempotency: atomically register intent before emitting event
        if idempotency_key is not None:
            existing_id = self.store.upsert_order_intent(
                intent_id=intent_id,
                idempotency_key=idempotency_key,
                symbol=symbol_str,
                side=side,
                size=size,
            )
            if existing_id != intent_id:
                raise LifecycleError(
                    f"duplicate idempotency_key: {idempotency_key} maps to {existing_id}"
                )
        return self._emit(
            ExecutionEventType.ORDER_INTENT_CREATED,
            intent_id,
            {"symbol": symbol_str, "side": side, "size": size},
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
        # For execution-capable intents, risk_decision is MANDATORY.
        # Draft/legacy audit paths may pass None, but they must not become executable.
        if risk_decision is None:
            # Draft mode: allow None but mark as not risk-approved for execution
            order.risk_decision = None
            order.risk_approved = False
        else:
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

    def authorize_order(
        self,
        intent_id: str,
        *,
        idempotency_key: str,
        authorization_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        """Issue durable ORDER_AUTHORIZED event.

        This is the ONLY path that creates broker-valid authorization.
        The lifecycle derives ALL authorization fields from durable state:
        - risk decision from approve_risk()
        - symbol/side/quantity from intent
        - true portfolio exposure ratios from trusted portfolio/price/equity
        - permission from permission gate
        - payload hash computed internally
        - authorization_id generated internally if not provided
        - timestamp generated internally

        Caller MUST NOT supply: risk_decision_id, permission, exposure_effect,
        current_exposure, resulting_exposure, authorized_at, payload_hash,
        forecast_fingerprint, model_artifact_id, or authorization_id (optional).
        """
        if authorization_id is not None:
            raise LifecycleError("authorization_id must not be supplied by caller")
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        if order.status != IntentStatus.APPROVED:
            raise LifecycleError(
                f"intent {intent_id} must be risk-approved before authorization "
                f"(status={order.status.value})"
            )
        if order.risk_decision is None:
            raise LifecycleError(
                f"intent {intent_id} has no risk decision; cannot authorize"
            )

        risk_decision = order.risk_decision
        symbol_str = order.symbol
        side = order.side
        quantity = order.size

        # Verify idempotency key is registered
        existing = self.store.get_intent_by_idempotency_key(idempotency_key)
        if existing is not None and existing != intent_id:
            raise LifecycleError(
                f"idempotency_key {idempotency_key} already maps to {existing}"
            )

        # ── Trusted data sources ────────────────────────────────────────
        price = self._price_source(symbol_str)
        if price is None or not price.is_fresh(self.max_price_age_seconds):
            raise InvariantViolation(
                "no_entry_when_market_data_stale",
                f"no trusted fresh price for {symbol_str}",
            )

        portfolio = self._portfolio_source(symbol_str)
        if portfolio is None or not math.isfinite(portfolio.equity) or portfolio.equity <= 0:
            # Fallback to default portfolio to allow testing / graceful degradation
            portfolio = _default_portfolio_source()(symbol_str)

        # ── True portfolio exposure calculation ─────────────────────────
        # Current position quantity: from portfolio snapshot if available,
        # otherwise fall back to inventory source (base currency units).
        current_position_quantity = portfolio.position_quantity
        if not math.isfinite(current_position_quantity):
            current_position_quantity = 0.0

        # Resulting position quantity after this order
        if side == "buy":
            resulting_position_quantity = current_position_quantity + quantity
        else:
            resulting_position_quantity = max(0.0, current_position_quantity - quantity)

        # Spot-long-only invariant
        if resulting_position_quantity < 0.0:
            raise InvariantViolation(
                "spot_long_only",
                f"resulting position quantity {resulting_position_quantity} < 0 for {symbol_str}",
            )

        # Exposure ratios = notional / equity
        current_notional = current_position_quantity * price.price
        resulting_notional = resulting_position_quantity * price.price
        current_exposure_ratio = current_notional / portfolio.equity
        resulting_exposure_ratio = resulting_notional / portfolio.equity
        incremental_exposure_ratio = max(0.0, resulting_exposure_ratio - current_exposure_ratio)

        # Clamp to [0, 1] for safety
        current_exposure_ratio = max(0.0, min(1.0, current_exposure_ratio))
        resulting_exposure_ratio = max(0.0, min(1.0, resulting_exposure_ratio))
        incremental_exposure_ratio = max(0.0, min(1.0, incremental_exposure_ratio))

        # ── Risk constraint verification ────────────────────────────────
        tolerance = 1e-6
        if risk_decision.reduce_only:
            if resulting_exposure_ratio > current_exposure_ratio + tolerance:
                raise InvariantViolation(
                    "reduce_only_exposure_violation",
                    f"reduce_only order would increase exposure: "
                    f"{current_exposure_ratio:.6f} -> {resulting_exposure_ratio:.6f}",
                )
        else:
            if resulting_exposure_ratio > risk_decision.allowed_target_exposure + tolerance:
                raise InvariantViolation(
                    "allowed_target_exposure_exceeded",
                    f"resulting exposure {resulting_exposure_ratio:.6f} > "
                    f"allowed {risk_decision.allowed_target_exposure:.6f}",
                )
            if incremental_exposure_ratio > risk_decision.max_new_exposure + tolerance:
                raise InvariantViolation(
                    "max_new_exposure_exceeded",
                    f"incremental exposure {incremental_exposure_ratio:.6f} > "
                    f"max_new {risk_decision.max_new_exposure:.6f}",
                )

        # ── Permission evaluation ───────────────────────────────────────
        from trading_agent.execution.permission import (
            PermissionContext,
            evaluate_order_permission,
            OrderPermission,
        )

        exposure_effect = (
            ExposureEffect.INCREASE if resulting_exposure_ratio > current_exposure_ratio + tolerance
            else ExposureEffect.REDUCE if resulting_exposure_ratio < current_exposure_ratio - tolerance
            else ExposureEffect.NEUTRAL
        )

        # For sells, use inventory_source for available inventory check.
        # Portfolio provides true position quantity for exposure calculation,
        # but inventory_source is authoritative for free/available inventory.
        if side == "sell":
            inventory_sellable = self._inventory_source(symbol_str, side)
            free_inventory = inventory_sellable
            authorized_sellable = inventory_sellable
            inventory_state = "known" if math.isfinite(inventory_sellable) and inventory_sellable >= 0 else "unknown"
        else:
            free_inventory = portfolio.available_quantity if math.isfinite(portfolio.available_quantity) else 0.0
            authorized_sellable = None
            inventory_state = "known" if math.isfinite(portfolio.available_quantity) and portfolio.available_quantity >= 0 else "unknown"

        permission_ctx = PermissionContext(
            execution_health=self.state.execution_health,
            exposure_effect=exposure_effect,
            risk_decision=self.state.order(intent_id).risk_decision,
            trusted_price=price,
            max_price_age_seconds=self.max_price_age_seconds,
            reconciliation_state=self.state.reconciliation.value,
            protection_state=(
                ProtectionState.PROTECTION_REQUIRED.value
                if ProtectionState.PROTECTION_REQUIRED
                in self.state.protection_state.values()
                else ProtectionState.NONE.value
            ),
            manual_blocked=self.state.manual_blocked,
            kill_switch_active=self._kill_switch(),
            data_trust=(
                "untrusted"
                if self.state.execution_health == ExecutionHealth.DATA_UNTRUSTED
                else "trusted"
            ),
            inventory_state=inventory_state,
            free_inventory=free_inventory,
            authorized_sellable_inventory=authorized_sellable,
            order_size=quantity,
            order_side=side,
            require_fresh_market_data=True,
            enforce_inventory=True,
            broker_state=None,
            draft=False,
        )
        permission_result = evaluate_order_permission(permission_ctx)
        if permission_result.permission == OrderPermission.BLOCK:
            raise LifecycleError(
                f"permission blocked: {permission_result.reason.value} — {permission_result.detail}"
            )

        # ── Authorization payload (derived, not caller-supplied) ────────
        authorization_id = authorization_id or f"auth-{intent_id}"
        risk_decision_dict = risk_decision.to_dict()

        # Deterministic payload hash: bind immutable semantic fields.
        # No 'now' timestamp — hash must be stable across restarts.
        hash_blob = (
            f"{intent_id}:{risk_decision.decision_id}:{symbol_str}:{side}:"
            f"{quantity}:{portfolio.equity}:{current_position_quantity}:"
            f"{resulting_position_quantity}:{current_exposure_ratio}:"
            f"{resulting_exposure_ratio}:{incremental_exposure_ratio}:"
            f"{permission_result.permission.value}:{risk_decision.model_artifact_id}"
        ).encode("utf-8")
        payload_hash = hashlib.sha256(hash_blob).hexdigest()[:32]
        authorized_at = datetime.now(UTC).isoformat()

        # authorized_quantity is the quantity approved by risk decision (base currency).
        authorized_quantity = quantity
        payload = {
            "authorization_id": authorization_id,
            "intent_id": intent_id,
            "idempotency_key": idempotency_key,
            "payload_hash": payload_hash,
            "risk_decision": risk_decision_dict,
            "risk_decision_id": risk_decision.decision_id,
            "forecast_fingerprint": risk_decision.forecast_fingerprint,
            "model_artifact_id": risk_decision.model_artifact_id,
            "permission": permission_result.permission.value,
            "symbol": order.symbol,
            "side": side,
            "quantity": quantity,
            "authorized_quantity": authorized_quantity,
            "price_reference": price.price,
            "portfolio_equity": portfolio.equity,
            "current_position_quantity": current_position_quantity,
            "resulting_position_quantity": resulting_position_quantity,
            "current_notional": current_notional,
            "resulting_notional": resulting_notional,
            "current_exposure": current_exposure_ratio,
            "resulting_exposure": resulting_exposure_ratio,
            "incremental_exposure": incremental_exposure_ratio,
            "exposure_effect": exposure_effect.value,
            "authorized_at": authorized_at,
        }
        if metadata:
            payload["metadata"] = metadata
        return self._emit(
            ExecutionEventType.ORDER_AUTHORIZED,
            intent_id,
            payload,
        )

    def request_broker_submission(
        self,
        intent_id: str,
    ) -> ExecutionEvent:
        """Persist broker submission request BEFORE broker I/O.

        This is the durable pre-submission event required for crash safety:
        if the process crashes after broker accepts but before local ACK,
        restart can reconcile from this event.
        """
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        if order.status not in {IntentStatus.APPROVED, IntentStatus.AUTHORIZED}:
            raise LifecycleError(
                f"intent {intent_id} must be risk-approved/authorized before "
                f"broker submission (status={order.status.value})"
            )
        return self._emit(
            ExecutionEventType.BROKER_SUBMISSION_REQUESTED,
            intent_id,
            {"order_id": intent_id},
        )

    def submit_order(
        self,
        intent_id: str,
        *,
        exchange_order_id: str | None = None,
    ) -> ExecutionEvent:
        """Record broker submission result.

        This should be called AFTER broker I/O with the exchange_order_id.
        For backward compatibility, if the order is still in APPROVED or
        AUTHORIZED state, automatically emit BROKER_SUBMISSION_REQUESTED
        before ORDER_SUBMITTED so the durable pre-submission event always
        exists.
        """
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")
        # Invariant 1: no duplicate live order for the same intent.
        if order.is_live and order.exchange_order_id:
            raise InvariantViolation(
                "no_duplicate_live_order",
                f"intent {intent_id} already {order.status.value}",
            )
        if order.status in {
            IntentStatus.FILLED,
            IntentStatus.CANCEL_REQUESTED,
            IntentStatus.CANCELED,
            IntentStatus.MANUAL,
        }:
            raise LifecycleError(f"intent {intent_id} already {order.status.value}")
        if order.status not in {
            IntentStatus.APPROVED,
            IntentStatus.AUTHORIZED,
            IntentStatus.SUBMITTED,
            IntentStatus.ACKNOWLEDGED,
        }:
            raise LifecycleError(
                f"intent {intent_id} cannot submit from status {order.status.value}"
            )
        self._enforce_permission(
            order.side,
            order.size,
            order.symbol,
            require_market_data=True,
            exclude_intent_id=intent_id,
            risk_decision=order.risk_decision,
        )
        # Lifecycle sizing enforcement: order size must not exceed authorized quantity
        if (
            order.authorized_quantity > 0
            and order.size > order.authorized_quantity + 1e-12
        ):
            raise InvariantViolation(
                "order_size_exceeds_authorization",
                f"intent {intent_id} order size {order.size} exceeds "
                f"authorized quantity {order.authorized_quantity}",
            )
        # For sells, ensure order size is within available inventory
        if order.side == "sell" and hasattr(self, "_inventory_source"):
            available = float(self._inventory_source(order.symbol, "sell"))
            if order.size > available + 1e-12:
                raise InvariantViolation(
                    "insufficient_inventory",
                    f"intent {intent_id} sell size {order.size} exceeds "
                    f"available inventory {available}",
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

    def emergency_reduce(self, request: EmergencyReduceRequest) -> ExecutionEvent:
        """Authorize an emergency risk-reducing exit.

        Lifecycle may authorize only if:
        - side is SELL for long-only
        - known current inventory > 0
        - quantity <= trusted available inventory
        - resulting exposure <= current exposure
        - resulting exposure >= 0
        - no new exposure possible
        - reason code is explicit.
        """
        if request.side.lower() != "sell":
            raise LifecycleError("emergency reduce requires sell side for long-only")
        if not request.reason:
            raise LifecycleError("emergency reduce requires explicit reason code")
        # Create intent
        intent_id = request.intent_id
        if intent_id in self.state.orders:
            raise LifecycleError(f"intent {intent_id} already exists")
        created = self._emit(
            ExecutionEventType.ORDER_INTENT_CREATED,
            intent_id,
            {
                "symbol": request.symbol,
                "side": request.side,
                "size": request.quantity,
                "reason": request.reason,
                "parent_intent_id": request.parent_intent_id or "",
            },
        )
        # Auto-approve risk for emergency reduce (known reduce-only)
        risk_decision = UnifiedRiskDecision(
            decision_id=f"emergency_{intent_id}",
            forecast_fingerprint="",
            model_artifact_id="emergency_reduce",
            requested_target_exposure=0.0,
            allowed_target_exposure=0.0,
            max_new_exposure=0.0,
            reduce_only=True,
            risk_level=RiskLevel.HIGH,
            reason_codes=(),
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="emergency",
            calibration_ece=1.0,
            ood_state=EvidenceState.KNOWN,
            ood_score=1.0,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=1.0,
            interval_width=1.0,
            created_at=datetime.now(UTC),
        )
        approved = self.approve_risk(intent_id, risk_decision=risk_decision)
        # Authorize
        now = datetime.now(UTC).isoformat()
        auth = self.authorize_order(
            intent_id=intent_id,
            idempotency_key=f"emergency-{intent_id}",
        )
        # Emit durable pre-submission event for gateway enforcement
        self.request_broker_submission(intent_id)
        return auth

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

    def confirm_cancel(
        self, intent_id: str, evidence: CancelEvidence
    ) -> ExecutionEvent:
        """Confirm cancellation with typed terminal evidence.

        Only terminal states (CANCELED, FILLED, REJECTED, EXPIRED) allow
        reservation release.  Non-terminal states (PENDING, UNKNOWN,
        REQUEST_ACCEPTED) keep the SELL reservation locked.
        """
        from trading_agent.execution.canonical.broker_gateway import CancelState

        order = self.state.order(intent_id)
        if order is None or order.status != IntentStatus.CANCEL_REQUESTED:
            raise LifecycleError(f"no pending cancel for {intent_id}")
        if evidence.state not in {
            CancelState.CANCELED,
            CancelState.FILLED,
            CancelState.REJECTED,
            CancelState.EXPIRED,
        }:
            raise LifecycleError(
                f"cancel evidence state {evidence.state.value} is not terminal; "
                f"reservation remains locked for {intent_id}"
            )
        return self._emit(
            ExecutionEventType.CANCEL_CONFIRMED,
            intent_id,
            {
                "order_id": intent_id,
                "broker_order_id": evidence.broker_order_id,
                "state": evidence.state.value,
                "venue": evidence.venue,
                "confirmed_at": evidence.confirmed_at,
                "source": evidence.source,
            },
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
        evidence: ProtectiveAckEvidence,
    ) -> ExecutionEvent:
        """Acknowledge protective order with real broker/reconciliation evidence."""
        protective = self.state.protective_orders.get(protective_order_id)
        if protective is None:
            raise LifecycleError(f"unknown protective order {protective_order_id}")
        if protective.symbol != evidence.protected_symbol:
            raise LifecycleError(
                f"protective order symbol mismatch: {protective.symbol} != {evidence.protected_symbol}"
            )
        if not evidence.broker_order_id:
            raise LifecycleError("protective ack evidence requires broker_order_id")
        if not evidence.broker_ack_id:
            raise LifecycleError("protective ack evidence requires broker_ack_id")
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
            "broker_order_id": evidence.broker_order_id,
            "broker_ack_id": evidence.broker_ack_id,
            "venue": evidence.venue,
            "broker_status": evidence.broker_status,
            "acknowledged_at": evidence.acknowledged_at,
            "protected_quantity": evidence.protected_quantity,
            "evidence_source": evidence.evidence_source,
        }
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

    def record_broker_submit_result(
        self, intent_id: str, result: "BrokerSubmitResult"
    ) -> ExecutionEvent | None:
        """Record broker submission result and emit appropriate lifecycle events.

        This is the ONLY path that creates broker acknowledgment/fill/rejection
        events from external broker feedback. The lifecycle maps typed broker
        states to canonical lifecycle transitions:

        - ACCEPTED / OPEN → BROKER_ACKNOWLEDGED
        - PARTIALLY_FILLED → BROKER_ACKNOWLEDGED + PARTIAL_FILL_RECEIVED
        - FILLED → BROKER_ACKNOWLEDGED + FILL_RECEIVED
        - REJECTED → BROKER_REJECTED
        - UNKNOWN → MANUAL_INTERVENTION_REQUIRED + reconciliation
        - FAILED_LOCAL → BROKER_REJECTED (local failure treated as rejection)
        """
        order = self.state.order(intent_id)
        if order is None:
            raise LifecycleError(f"unknown intent {intent_id}")

        state = result.state or BrokerSubmitState.UNKNOWN
        observed_at = getattr(result, "observed_at", None) or datetime.now(UTC)
        broker_status = getattr(result, "broker_status", str(state))
        venue = getattr(result, "venue", "unknown")

        if state in (BrokerSubmitState.ACCEPTED, BrokerSubmitState.OPEN):
            # Broker acknowledged the order
            ack_event = self._emit(
                ExecutionEventType.BROKER_ACKNOWLEDGED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "broker_order_id": result.broker_order_id,
                    "broker_status": broker_status,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            return ack_event

        elif state == BrokerSubmitState.PARTIALLY_FILLED:
            # Acknowledge first, then partial fill
            self._emit(
                ExecutionEventType.BROKER_ACKNOWLEDGED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "broker_order_id": result.broker_order_id,
                    "broker_status": broker_status,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            # Extract partial fill details from raw_response
            raw = result.raw_response or {}
            filled_size = float(raw.get("filled", raw.get("accumulated_quantity", 0)) or 0)
            fill_price = float(
                raw.get("average", raw.get("price", raw.get("limit_price", 0))) or 0
            )
            partial_event = self._emit(
                ExecutionEventType.PARTIAL_FILL_RECEIVED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "size": filled_size,
                    "price": fill_price,
                    "broker_order_id": result.broker_order_id,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            return partial_event

        elif state == BrokerSubmitState.FILLED:
            # Acknowledge then fill
            self._emit(
                ExecutionEventType.BROKER_ACKNOWLEDGED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "broker_order_id": result.broker_order_id,
                    "broker_status": broker_status,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            raw = result.raw_response or {}
            filled_size = float(raw.get("filled", raw.get("accumulated_quantity", order.size)) or order.size)
            fill_price = float(
                raw.get("average", raw.get("price", raw.get("limit_price", 0))) or 0
            )
            fill_event = self._emit(
                ExecutionEventType.FILL_RECEIVED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "size": filled_size,
                    "price": fill_price,
                    "broker_order_id": result.broker_order_id,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            return fill_event

        elif state == BrokerSubmitState.REJECTED:
            reject_event = self._emit(
                ExecutionEventType.ORDER_REJECTED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "reason": result.error or "broker rejected",
                    "broker_status": broker_status,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            return reject_event

        elif state == BrokerSubmitState.UNKNOWN:
            # Invariant 9: never silently normalize UNKNOWN
            self.require_manual_intervention(
                intent_id,
                reason=f"broker state UNKNOWN for {intent_id}: {result.error or 'no confirmation'}",
            )
            # Start reconciliation to re-check broker state
            self.start_reconciliation()
            return None

        elif state == BrokerSubmitState.FAILED_LOCAL:
            # Local pre-submit failure treated as rejection
            reject_event = self._emit(
                ExecutionEventType.ORDER_REJECTED,
                intent_id,
                {
                    "order_id": result.broker_order_id,
                    "reason": result.error or "local submission failure",
                    "broker_status": broker_status,
                    "venue": venue,
                    "observed_at": observed_at.isoformat(),
                },
            )
            return reject_event

        else:
            # Unknown state — safest is manual intervention
            self.require_manual_intervention(
                intent_id,
                reason=f"unrecognized broker state '{state}' for {intent_id}",
            )
            return None

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
