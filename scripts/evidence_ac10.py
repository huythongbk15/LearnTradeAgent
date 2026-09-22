"""
AC10 Evidence Script — Cancel / Timeout / Restart (Crash Safety).

Independent oracle: hand-traces state transitions for cancel_requested,
cancel_confirmed, timeout, and duplicate-event idempotency. Then replays
through public store + ExecutionLifecycle.replay() to verify crash-recovery.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trading_agent.execution.lifecycle.events import (
    ExecutionEvent, ExecutionEventType,
)
from trading_agent.execution.lifecycle.store import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle, IntentStatus,
)

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC10 evidence FAIL: {name}")

ORDER_ID = "order_cancel_001"
SYMBOL = "ETH_USDT"
ORDER_SIZE = 50.0
NOW = datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc)

events = [
    ExecutionEvent(
        event_id="c1_evt_create", event_type=ExecutionEventType.ORDER_INTENT_CREATED,
        aggregate_id=ORDER_ID, seq=1, occurred_at=NOW,
        payload={"symbol": SYMBOL, "side": "buy", "size": ORDER_SIZE},
    ),
    ExecutionEvent(
        event_id="c1_evt_risk", event_type=ExecutionEventType.RISK_APPROVED,
        aggregate_id=ORDER_ID, seq=2, occurred_at=NOW, payload={},
    ),
    ExecutionEvent(
        event_id="c1_evt_auth", event_type=ExecutionEventType.ORDER_AUTHORIZED,
        aggregate_id=ORDER_ID, seq=3, occurred_at=NOW,
        payload={"authorized_quantity": ORDER_SIZE, "authorization_id": "auth_c1",
                 "idempotency_key": "idem_c1", "payload_hash": "h_c1",
                 "permission": "trade", "authorized_at": NOW.isoformat(),
                 "price_reference": 2000.0, "portfolio_equity": 50000.0,
                 "current_position_quantity": 50.0, "resulting_position_quantity": 50.0,
                 "current_exposure": 100000.0, "resulting_exposure": 100000.0,
                 "incremental_exposure": 100000.0},
    ),
    ExecutionEvent(
        event_id="c1_evt_submit", event_type=ExecutionEventType.ORDER_SUBMITTED,
        aggregate_id=ORDER_ID, seq=4, occurred_at=NOW,
        payload={"broker_order_id": "broker_001", "side": "buy", "quantity": ORDER_SIZE,
                 "price": 2000.0, "symbol": SYMBOL},
    ),
    ExecutionEvent(
        event_id="c1_evt_cancel_req", event_type=ExecutionEventType.CANCEL_REQUESTED,
        aggregate_id=ORDER_ID, seq=5, occurred_at=NOW,
        payload={"reason": "risk_limit_breached"},
    ),
]

tmpdir = Path(tempfile.mkdtemp())
store = ExecutionEventStore(tmpdir / "cancel_events.db").connect()
for e in events:
    store.append(e, expect_seq=True)

store_events = store.read_events(aggregate_id=ORDER_ID)
lc = ExecutionLifecycle(store, require_protective_order=False)
state = lc.replay(store_events)
order = state.orders[ORDER_ID]

check("C1_cancel_requested", order.status == IntentStatus.CANCEL_REQUESTED,
      f"system={order.status}, oracle=CANCEL_REQUESTED")

cancel_confirmed = ExecutionEvent(
    event_id="c1_evt_cancel_confirmed", event_type=ExecutionEventType.CANCEL_CONFIRMED,
    aggregate_id=ORDER_ID, seq=6, occurred_at=NOW,
    payload={"state": "CANCELED", "broker_order_id": "broker_001"},
)
store.append(cancel_confirmed, expect_seq=True)

all_events = store.read_events(aggregate_id=ORDER_ID)
state2 = lc.replay(all_events)
order2 = state2.orders[ORDER_ID]
check("C2_cancel_confirmed", order2.status == IntentStatus.CANCELED,
      f"system={order2.status}, oracle=CANCELED")

# C3: Idempotency
events_with_dup = all_events + [
    ExecutionEvent(event_id="c1_evt_cancel_confirmed",
        event_type=ExecutionEventType.CANCEL_CONFIRMED,
        aggregate_id=ORDER_ID, seq=6, occurred_at=NOW,
        payload={"state": "CANCELED", "broker_order_id": "broker_001"}),
]
state_dup = lc.replay(events_with_dup)
order_dup = state_dup.orders[ORDER_ID]
check("C3_idempotent_replay", order_dup.status == IntentStatus.CANCELED and
      abs(order_dup.filled_size - order2.filled_size) < 1e-9,
      f"status={order_dup.status}, filled={order_dup.filled_size} unchanged")

# C4: Sequence gap rejection
gap_event = ExecutionEvent(
    event_id="c1_evt_gap", event_type=ExecutionEventType.FILL_RECEIVED,
    aggregate_id=ORDER_ID, seq=100, occurred_at=NOW,
    payload={"size": 10.0, "price": 1999.0},
)
try:
    store.append(gap_event, expect_seq=True)
    check("C4_seq_gap_rejected", False, "should have raised")
except Exception:
    check("C4_seq_gap_rejected", True, "sequence gap correctly raises")

# C5: Crash recovery — fresh lifecycle, replay from store
events_final = store.read_events(aggregate_id=ORDER_ID)
lc_recovered = ExecutionLifecycle(store, require_protective_order=False)
state_recovered = lc_recovered.replay(events_final)
order_recovered = state_recovered.orders[ORDER_ID]
check("C5_crash_recovery", order_recovered.status == IntentStatus.CANCELED and
      abs(order_recovered.filled_size - order2.filled_size) < 1e-9,
      f"recovered_status={order_recovered.status}, filled={order_recovered.filled_size}")

# C6: Unconfirmed cancel (cancel_requested but no confirm) -> still CANCEL_REQUESTED
# Simulate timeout: order submitted, cancel requested, but no cancel_confirmed
timeout_order = "order_timeout_001"
timeout_events = [
    ExecutionEvent(event_id="t1", event_type=ExecutionEventType.ORDER_INTENT_CREATED,
        aggregate_id=timeout_order, seq=1, occurred_at=NOW,
        payload={"symbol": SYMBOL, "side": "buy", "size": 100.0}),
    ExecutionEvent(event_id="t2", event_type=ExecutionEventType.ORDER_AUTHORIZED,
        aggregate_id=timeout_order, seq=2, occurred_at=NOW,
        payload={"authorized_quantity": 100.0, "authorization_id": "a1",
                 "idempotency_key": "i1", "payload_hash": "h1",
                 "permission": "trade", "authorized_at": NOW.isoformat(),
                 "price_reference": 0.45, "portfolio_equity": 50000.0,
                 "current_position_quantity": 0.0, "resulting_position_quantity": 100.0,
                 "current_exposure": 0.0, "resulting_exposure": 45.0,
                 "incremental_exposure": 45.0}),
    ExecutionEvent(event_id="t3", event_type=ExecutionEventType.ORDER_SUBMITTED,
        aggregate_id=timeout_order, seq=3, occurred_at=NOW,
        payload={"broker_order_id": "broker_t1", "side": "buy", "quantity": 100.0,
                 "price": 0.45, "symbol": SYMBOL}),
    ExecutionEvent(event_id="t4", event_type=ExecutionEventType.CANCEL_REQUESTED,
        aggregate_id=timeout_order, seq=4, occurred_at=NOW,
        payload={"reason": "stale_no_ack"}),
]
state_timeout = lc.replay(timeout_events)
order_timeout = state_timeout.orders[timeout_order]
check("C6_timeout_status", order_timeout.status == IntentStatus.CANCEL_REQUESTED,
      f"system={order_timeout.status}, oracle=CANCEL_REQUESTED (no confirm)")

store.close()

all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC10 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")

evidence = {
    "ac_id": "AC10",
    "oracle": "independent hand-trace of cancel/timeout/idempotency/seq-gaps/crash-recovery",
    "cases": results, "all_pass": all_pass,
    "total_checks": len(results), "passed": sum(1 for r in results if r["status"] == "PASS"),
    "order_id": ORDER_ID,
}
out = Path("/tmp/ac10_evidence.json")
out.write_text(json.dumps(evidence, indent=2, default=str))
print(f"Evidence written to {out}")
sys.exit(0 if all_pass else 1)
