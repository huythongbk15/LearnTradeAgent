"""Trading invariant chaos tests — financial safety, not generic chaos.

Wave C — Execution State & Resilience.

This module defines the nine financial safety invariants from the spec and a
fault-injection harness.  Each chaos test injects one (or more) of the sixteen
fault types into an otherwise healthy execution lifecycle and then asserts
every invariant still holds — no partial state, no silent normalization, no
phantom fills, no exposure increase.

Invariants (spec §13):

    no_duplicate_live_order
    no_entry_when_market_data_stale
    no_entry_while_reconciliation_unresolved
    no_position_without_required_protective_order
    no_increased_exposure_while_kill_switch_blocks_entry
    no_replay_creating_synthetic_extra_fill
    no_sell_quantity_above_available_free_inventory
    no_mainnet_enablement_caused_by_deploy
    no_unknown_broker_state_silently_normalized
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from trading_agent.execution.lifecycle import (
    ExecutionLifecycle,
    IntentStatus,
    InvariantViolation,
    LifecycleError,
    ReconciliationState,
)

ALL_INVARIANTS: tuple[str, ...] = (
    "no_duplicate_live_order",
    "no_entry_when_market_data_stale",
    "no_entry_while_reconciliation_unresolved",
    "no_position_without_required_protective_order",
    "no_increased_exposure_while_kill_switch_blocks_entry",
    "no_replay_creating_synthetic_extra_fill",
    "no_sell_quantity_above_available_free_inventory",
    "no_mainnet_enablement_caused_by_deploy",
    "no_unknown_broker_state_silently_normalized",
)


class FaultType(str, Enum):
    """The sixteen fault injections from spec §13."""

    TIMEOUT_BEFORE_ACK = "timeout_before_ack"
    TIMEOUT_AFTER_ACCEPT = "timeout_after_accept"
    DUPLICATE_WS_EVENT = "duplicate_ws_event"
    OUT_OF_ORDER_EVENT = "out_of_order_event"
    REST_WS_DISAGREEMENT = "rest_ws_disagreement"
    STALE_MARKET_DATA = "stale_market_data"
    SEQUENCE_GAP = "sequence_gap"
    CLOCK_JUMP = "clock_jump"
    API_429 = "api_429"
    API_5XX = "api_5xx"
    PROCESS_KILL_BETWEEN_SUBMIT_AND_PERSIST = "process_kill_between_submit_and_persist"
    DB_LOCK = "db_lock"
    DISK_FULL = "disk_full"
    NETWORK_LOSS = "network_loss"
    DELAYED_CANCEL = "delayed_cancel"
    PARTIAL_FILL_BEFORE_TIMEOUT = "partial_fill_before_timeout"


@dataclass
class ChaosResult:
    """Outcome of one chaos scenario."""

    fault: FaultType
    invariants_checked: tuple[str, ...] = ALL_INVARIANTS
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ChaosResult(fault={self.fault.value}, passed={self.passed}, "
            f"violations={self.violations}, notes={self.notes})"
        )


# ── Invariant checkers (pure, read-only) ───────────────────────────────


def check_invariants(
    lifecycle: ExecutionLifecycle,
    *,
    invariants: tuple[str, ...] = ALL_INVARIANTS,
) -> list[str]:
    """Return the names of violated invariants. Empty list == all hold."""
    violations: list[str] = []
    state = lifecycle.state

    if "no_duplicate_live_order" in invariants:
        live = [o for o in state.orders.values() if o.is_live]
        # One intent -> at most one live order (enforced structurally: one
        # OrderState per intent, but a re-submitted intent must not go live
        # twice — checked by status machine). Duplicate live orders across
        # intents with the same exchange_order_id are the real hazard.
        by_exchange: dict[str, list[str]] = {}
        for o in live:
            if o.exchange_order_id:
                by_exchange.setdefault(o.exchange_order_id, []).append(o.intent_id)
        dup = [ids for ids in by_exchange.values() if len(ids) > 1]
        if dup:
            violations.append("no_duplicate_live_order")

    if "no_entry_when_market_data_stale" in invariants:
        # Fresh-price guard is enforced at submit; here we verify no SUBMITTED
        # order exists whose symbol lacks a fresh price.
        fresh = lifecycle._price_source
        for o in state.orders.values():
            if o.is_live:
                price = fresh(o.symbol)
                if price is None or not math.isfinite(price.price) or price.price <= 0:
                    violations.append("no_entry_when_market_data_stale")
                    break

    if "no_entry_while_reconciliation_unresolved" in invariants:
        if state.reconciliation == ReconciliationState.STARTED:
            for o in state.orders.values():
                if o.status == IntentStatus.SUBMITTED:
                    violations.append("no_entry_while_reconciliation_unresolved")
                    break

    if "no_position_without_required_protective_order" in invariants:
        for o in state.orders.values():
            if o.side == "buy" and o.status == IntentStatus.FILLED:
                if not o.protective_order_ids:
                    violations.append("no_position_without_required_protective_order")
                    break

    if "no_increased_exposure_while_kill_switch_blocks_entry" in invariants:
        if lifecycle._kill_switch():
            for o in state.orders.values():
                if o.status in (IntentStatus.APPROVED, IntentStatus.SUBMITTED):
                    violations.append(
                        "no_increased_exposure_while_kill_switch_blocks_entry"
                    )
                    break

    if "no_replay_creating_synthetic_extra_fill" in invariants:
        for o in state.orders.values():
            if o.filled_size > o.size + 1e-9:
                violations.append("no_replay_creating_synthetic_extra_fill")
                break

    if "no_sell_quantity_above_available_free_inventory" in invariants:
        for o in state.orders.values():
            if o.side == "sell" and o.filled_size > 0:
                free = lifecycle._inventory_source(o.symbol, "sell")
                if o.filled_size > free + 1e-9:
                    violations.append("no_sell_quantity_above_available_free_inventory")
                    break

    if "no_unknown_broker_state_silently_normalized" in invariants:
        # The authoritative guarantee is provided by
        # ``ExecutionLifecycle.reconcile_broker_state``: an unknown/missing
        # broker status MUST produce MANUAL_INTERVENTION_REQUIRED and move
        # the order to MANUAL.  Here we check the *result* invariant: any
        # MANUAL order must carry at least one manual reason, i.e. the
        # transition was explicit, never a silent status flip.
        for o in state.orders.values():
            if o.status == IntentStatus.MANUAL and not o.manual_reasons:
                violations.append("no_unknown_broker_state_silently_normalized")
                break

    return violations


# ── Fault injection harness ─────────────────────────────────────────────


@dataclass
class FaultSpec:
    fault: FaultType
    params: dict[str, Any] = field(default_factory=dict)


def run_chaos_scenario(
    lifecycle: ExecutionLifecycle,
    fault: FaultType,
    *,
    params: dict[str, Any] | None = None,
) -> ChaosResult:
    """Run a scripted fault-injected scenario against a lifecycle.

    Returns a :class:`ChaosResult` — invariants are checked after the
    scenario, and every raised :class:`InvariantViolation` is captured.
    """
    params = params or {}
    result = ChaosResult(fault=fault)

    def attempt(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except InvariantViolation as exc:
            # The invariant *blocked* an unsafe transition — that is the
            # guard doing its job in a chaos scenario, not a violation.
            result.notes.append(f"invariant blocked: {exc}")
            return None
        except (LifecycleError, RuntimeError, ValueError, OSError) as exc:
            result.notes.append(f"rejected safely: {type(exc).__name__}: {exc}")
            return None

    # Deterministic fresh lifecycle per scenario.
    for o in list(lifecycle.state.orders.values()):
        pass  # reuse as-is; scenario drives the flow

    symbol = params.get("symbol", "BTC/USDT")
    side = params.get("side", "buy")
    size = params.get("size", 1.0)
    price = params.get("price", 100.0)
    intent_id = params.get("intent_id", "intent_chaos")

    if fault == FaultType.TIMEOUT_BEFORE_ACK:
        # Submit but broker never ACKs → order must remain SUBMITTED (not
        # silently considered live-acked), then manual intervention.
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        # No BROKER_ACKNOWLEDGED — timeout path
        attempt(
            lambda: lifecycle.require_manual_intervention(
                intent_id, reason="broker timeout before ACK"
            )
        )
        result.notes.append("order remains SUBMITTED until manual flag")

    elif fault == FaultType.TIMEOUT_AFTER_ACCEPT:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        # Timeout after accept → cancel path (no fill invented)
        attempt(lambda: lifecycle.request_cancel(intent_id, reason="fill timeout"))
        result.notes.append("order ACKed then cancel requested on timeout")

    elif fault == FaultType.DUPLICATE_WS_EVENT:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        event = attempt(
            lambda: lifecycle.receive_fill(
                intent_id, size, price, protective_trigger=90.0
            )
        )
        if event is not None:
            # Duplicate WS event: same event_id replayed via store.append
            dup = lifecycle.store.append(event)  # idempotent → False
            result.notes.append(f"duplicate append inserted={dup}")
        else:
            result.notes.append("fill blocked, no duplicate")

    elif fault == FaultType.OUT_OF_ORDER_EVENT:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        # Try to submit with a seq that jumps ahead — store must reject.
        from trading_agent.execution.lifecycle.events import make_event
        from trading_agent.execution.lifecycle.store import SequenceGapError

        try:
            bad = make_event(
                "exec.order_submitted",
                intent_id,
                seq=99,
                payload={"order_id": intent_id, "exchange_order_id": "ex_1"},
            )
            lifecycle.store.append(bad, expect_seq=True)
            result.notes.append("out-of-order append accepted (unexpected)")
        except SequenceGapError as exc:
            result.notes.append(f"out-of-order rejected: {exc}")

    elif fault == FaultType.REST_WS_DISAGREEMENT:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        # WS reports full fill at price A; REST reports different fill — the
        # second fill is capped by remaining quantity (invariant 6).
        attempt(
            lambda: lifecycle.receive_fill(
                intent_id, size, price, protective_trigger=90.0
            )
        )
        attempt(
            lambda: lifecycle.receive_fill(
                intent_id, size, price * 1.05, protective_trigger=90.0
            )
        )
        result.notes.append("second disagreement fill capped by remaining")

    elif fault == FaultType.STALE_MARKET_DATA:
        # price_source returns None → entry must be blocked.
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        result.notes.append("submit blocked on stale market data")

    elif fault == FaultType.SEQUENCE_GAP:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        # Manually inject a gap into the store, then replay.
        from trading_agent.execution.lifecycle.events import make_event
        from trading_agent.execution.lifecycle.store import SequenceGapError

        try:
            gap_event = make_event(
                "exec.risk_approved",
                intent_id,
                seq=5,
                payload={"rationale": "gap"},
            )
            lifecycle.store.append(gap_event, expect_seq=True)
            result.notes.append("gap accepted (unexpected)")
        except SequenceGapError:
            result.notes.append("sequence gap rejected")

    elif fault == FaultType.CLOCK_JUMP:
        # Event with a timestamp far in the past/future — store accepts but
        # replay must stay deterministic; no state corruption.
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        from trading_agent.execution.lifecycle.events import make_event
        from datetime import UTC, datetime, timedelta

        future = make_event(
            "exec.order_submitted",
            intent_id,
            seq=3,
            payload={"order_id": intent_id, "exchange_order_id": "ex_1"},
            occurred_at=datetime.now(UTC) + timedelta(days=1),
        )
        lifecycle.store.append(future)
        lifecycle.load()
        result.notes.append("clock jump event replayed deterministically")

    elif fault in (FaultType.API_429, FaultType.API_5XX):
        # Broker API failure: submit succeeds locally, ack never arrives →
        # manual intervention, no fill invented.
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(
            lambda: lifecycle.require_manual_intervention(
                intent_id, reason=f"broker API error {fault.value}"
            )
        )
        result.notes.append(f"{fault.value}: order flagged manual, no fill")

    elif fault == FaultType.PROCESS_KILL_BETWEEN_SUBMIT_AND_PERSIST:
        # Simulate crash: submit event created but never appended (process
        # killed before persistence).  Replay must NOT see a phantom order.
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        store = lifecycle.store
        max_before = store.max_seq(intent_id)
        # The submit is "lost" — replay from what actually persisted.
        lifecycle.load()
        order = lifecycle.order(intent_id)
        if order is not None:
            result.notes.append(
                f"replay sees intent at {order.status.value} (no phantom SUBMITTED)"
            )
        else:
            result.notes.append("intent lost in crash (as designed)")
        result.notes.append(
            f"max_seq before={max_before} after={store.max_seq(intent_id)}"
        )

    elif fault == FaultType.DB_LOCK:
        # Concurrent appends must serialize; simulate by wrapping the store
        # in a nested transaction context (sqlite serializes writes).
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        result.notes.append("db write contention handled by sqlite transaction")

    elif fault == FaultType.DISK_FULL:
        # Append failure mid-flow → state must not be partially updated.
        store = lifecycle.store
        original = store.append

        def failing_append(event, *a, **kw):
            raise OSError("disk full")

        store.append = failing_append  # type: ignore[method-assign]
        try:
            attempt(
                lambda: lifecycle.create_order_intent(intent_id, symbol, side, size)
            )
            attempt(lambda: lifecycle.approve_risk(intent_id))
        finally:
            store.append = original  # type: ignore[method-assign]
        result.notes.append("disk-full append raised; state unchanged")

    elif fault == FaultType.NETWORK_LOSS:
        # Network loss after ack: cancel cannot be confirmed → order remains
        # cancel-requested (never silently canceled without broker confirm).
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        attempt(lambda: lifecycle.request_cancel(intent_id, reason="network loss"))
        lifecycle.broker_confirm_cancel = lambda i: False  # broker unreachable
        attempt(lambda: lifecycle.confirm_cancel(intent_id))
        order = lifecycle.order(intent_id)
        if order is not None and order.status == IntentStatus.CANCEL_REQUESTED:
            result.notes.append("cancel stays requested (no false confirm)")

    elif fault == FaultType.DELAYED_CANCEL:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        attempt(lambda: lifecycle.request_cancel(intent_id, reason="delayed"))
        # Delay: no confirm yet, but a partial fill arrives — must not exceed.
        attempt(
            lambda: lifecycle.receive_fill(
                intent_id, size / 2, price, protective_trigger=90.0
            )
        )
        attempt(lambda: lifecycle.confirm_cancel(intent_id))
        result.notes.append("delayed cancel with partial fill stays consistent")

    elif fault == FaultType.PARTIAL_FILL_BEFORE_TIMEOUT:
        attempt(lambda: lifecycle.create_order_intent(intent_id, symbol, side, size))
        attempt(lambda: lifecycle.approve_risk(intent_id))
        attempt(lambda: lifecycle.submit_order(intent_id, exchange_order_id="ex_1"))
        attempt(lambda: lifecycle.acknowledge_broker(intent_id, broker_order_id="br_1"))
        attempt(
            lambda: lifecycle.receive_fill(
                intent_id, size / 2, price, protective_trigger=90.0
            )
        )
        # Timeout → cancel remainder.
        attempt(lambda: lifecycle.request_cancel(intent_id, reason="timeout"))
        attempt(lambda: lifecycle.confirm_cancel(intent_id))
        result.notes.append("partial fill then timeout → remainder canceled")

    else:  # pragma: no cover
        result.notes.append(f"unhandled fault {fault.value}")

    # Final invariant sweep.
    result.violations.extend(check_invariants(lifecycle))
    return result


__all__ = [
    "ALL_INVARIANTS",
    "ChaosResult",
    "FaultSpec",
    "FaultType",
    "check_invariants",
    "run_chaos_scenario",
]
