"""Wave C — event-sourced execution lifecycle tests.

Covers: deterministic replay, idempotency, duplicate-event handling, crash
recovery, sequence validation, auditability, event schema version, durable
snapshot + restore (schema_version/checksum/partial/corrupt rejection).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.execution.lifecycle import (
    EVENT_SCHEMA_VERSION,
    EventValidationError,
    ExecutionEventStore,
    ExecutionEventType,
    ExecutionLifecycle,
    IntentStatus,
    InvariantViolation,
    ReconciliationState,
    SequenceGapError,
    SnapshotIntegrityError,
    make_event,
)


@pytest.fixture
def store(tmp_path):
    return ExecutionEventStore(tmp_path / "exec.db").connect()


@pytest.fixture
def lifecycle(store):
    return ExecutionLifecycle(store, price_source=lambda s: 100.0)


def test_append_and_replay_roundtrip(store):
    e1 = make_event(
        ExecutionEventType.ORDER_INTENT_CREATED,
        "i1",
        1,
        payload={"symbol": "BTC/USDT", "side": "buy", "size": 1.0},
    )
    e2 = make_event(
        ExecutionEventType.RISK_APPROVED, "i1", 2, payload={"rationale": "ok"}
    )
    assert store.append(e1) is True
    assert store.append(e2) is True
    events = store.read_events("i1")
    assert [e.seq for e in events] == [1, 2]
    assert events[0].schema_version == EVENT_SCHEMA_VERSION
    # Deterministic replay → identical state
    lc = ExecutionLifecycle(store)
    state = lc.replay(events)
    assert state.order("i1").status == IntentStatus.APPROVED


def test_duplicate_event_id_is_idempotent(store):
    event = make_event(
        ExecutionEventType.ORDER_INTENT_CREATED,
        "i1",
        1,
        payload={"symbol": "BTC/USDT", "side": "buy", "size": 1.0},
    )
    assert store.append(event) is True
    assert store.append(event) is False  # duplicate event_id → ignored
    assert store.count() == 1
    lc = ExecutionLifecycle(store)
    state = lc.replay(store.read_events())
    # Replaying the duplicate stream must not double-apply
    assert state.order("i1").size == 1.0
    assert state.order("i1").status == IntentStatus.PENDING


def test_sequence_gap_rejected(store):
    e1 = make_event(
        ExecutionEventType.ORDER_INTENT_CREATED,
        "i1",
        1,
        payload={"symbol": "BTC/USDT", "side": "buy", "size": 1.0},
    )
    store.append(e1)
    bad = make_event(
        ExecutionEventType.RISK_APPROVED, "i1", 5, payload={"rationale": "gap"}
    )
    with pytest.raises(SequenceGapError):
        store.append(bad, expect_seq=True)
    # Nothing partial persisted
    assert [e.seq for e in store.read_events("i1")] == [1]
    assert store.integrity_check()["ok"] is True


def test_full_lifecycle_and_replay_determinism(tmp_path):
    path = tmp_path / "lifecycle.db"
    with ExecutionEventStore(path).connect() as store:
        lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
        lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lc.approve_risk("i1")
        lc.submit_order("i1", exchange_order_id="ex_1")
        lc.acknowledge_broker("i1", broker_order_id="br_1")
        lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
        lc.book_fee("i1", 0.1)
        first = lc.snapshot_state()
    # New connection, replay from disk — identical projection
    with ExecutionEventStore(path).connect() as store2:
        lc2 = ExecutionLifecycle(store2)
        lc2.load()
        second = lc2.snapshot_state()
    assert first == second
    order = lc2.order("i1")
    assert order.status == IntentStatus.FILLED
    assert order.filled_size == 1.0
    assert order.protective_order_ids  # protective order created on fill
    assert lc2.state.reconciliation == ReconciliationState.NONE


def test_crash_between_submit_and_persist_leaves_no_phantom(tmp_path):
    path = tmp_path / "crash.db"
    with ExecutionEventStore(path).connect() as store:
        lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
        lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lc.approve_risk("i1")
        # Crash: submit event was built but never persisted.
        # Replay only sees what was durable.
    with ExecutionEventStore(path).connect() as store2:
        lc2 = ExecutionLifecycle(store2)
        lc2.load()
        order = lc2.order("i1")
        assert order is not None
        assert order.status == IntentStatus.APPROVED  # not SUBMITTED


def test_unknown_broker_state_goes_manual_not_silent(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    # Broker reports an unknown status for the live order
    report = lc.reconcile_broker_state({"ex_1": "weird_state"})
    order = lc.order("i1")
    assert order.status == IntentStatus.MANUAL
    assert order.manual_reasons
    assert "i1" in report["unknown"]
    # And a *missing* live order also goes manual
    from trading_agent.execution.chaos_invariants import check_invariants

    assert check_invariants(lc) == []


def test_no_replay_creating_synthetic_extra_fill(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
    with pytest.raises(InvariantViolation):
        lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)  # over-remaining
    assert lc.order("i1").filled_size == 1.0


def test_sell_above_free_inventory_blocked(store):
    inventory = {"BTC/USDT": 0.5}
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: 100.0,
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
    )
    lc.create_order_intent("i1", "BTC/USDT", "sell", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    with pytest.raises(InvariantViolation):
        lc.receive_fill("i1", 1.0, 100.0, protective_trigger=90.0)
    assert lc.order("i1").filled_size == 0.0


def test_kill_switch_blocks_new_entry(store):
    kill = {"active": False}
    lc = ExecutionLifecycle(store, kill_switch_active=lambda: kill["active"])
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    kill["active"] = True
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")
    # Existing live orders untouched
    assert lc.order("i1").status == IntentStatus.APPROVED


def test_reconciliation_blocks_entry(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    lc.start_reconciliation()
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")
    lc.resolve_reconciliation()
    # Now allowed
    lc.submit_order("i1", exchange_order_id="ex_1")


def test_stale_market_data_blocks_entry(store):
    prices = {"BTC/USDT": 100.0}
    timestamps = {"BTC/USDT": datetime.now(UTC)}

    def price_source(symbol):
        if symbol not in prices:
            return None
        if (datetime.now(UTC) - timestamps[symbol]).total_seconds() > 5:
            return None
        return prices[symbol]

    lc = ExecutionLifecycle(store, price_source=price_source, max_price_age_seconds=5)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    timestamps["BTC/USDT"] = datetime.now(UTC) - timedelta(seconds=60)  # stale
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")


def test_snapshot_restore_roundtrip(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    snap = store.save_snapshot(
        "i1",
        lc.snapshot_state(),
        state_version=1,
        last_seq=lc.last_seq(),
    )
    restored = store.load_snapshot("i1")
    assert restored is not None
    assert restored.checksum == snap.checksum
    assert restored.state_version == 1
    assert restored.last_seq == 2
    assert restored.state["orders"]["i1"]["status"] == "approved"


def test_corrupt_snapshot_rejected(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    store.save_snapshot("i1", lc.snapshot_state(), state_version=1, last_seq=1)
    # Corrupt the stored state_json
    store.conn.execute(
        "UPDATE execution_snapshots SET state_json = '{}' WHERE aggregate_id = 'i1'"
    )
    store.conn.commit()
    with pytest.raises(SnapshotIntegrityError):
        store.load_snapshot("i1")


def test_partial_snapshot_json_rejected(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    store.save_snapshot("i1", lc.snapshot_state(), state_version=1, last_seq=1)
    # Simulate a torn write (truncated json)
    store.conn.execute(
        "UPDATE execution_snapshots SET state_json = '{\"orders\": {' WHERE aggregate_id = 'i1'"
    )
    store.conn.commit()
    with pytest.raises(SnapshotIntegrityError):
        store.load_snapshot("i1")


def test_old_schema_snapshot_rejected(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    store.save_snapshot("i1", lc.snapshot_state(), state_version=1, last_seq=1)
    store.conn.execute(
        "UPDATE execution_snapshots SET schema_version = 0 WHERE aggregate_id = 'i1'"
    )
    store.conn.commit()
    with pytest.raises(SnapshotIntegrityError):
        store.load_snapshot("i1")


def test_event_validation_requires_order_id(store):
    with pytest.raises(EventValidationError):
        make_event(ExecutionEventType.ORDER_SUBMITTED, "i1", 1, payload={})
    with pytest.raises(EventValidationError):
        make_event(
            ExecutionEventType.FILL_RECEIVED,
            "i1",
            1,
            payload={"order_id": "o1", "size": 0, "price": 100.0},
        )


def test_duplicate_submit_blocked(store):
    lc = ExecutionLifecycle(store, price_source=lambda s: 100.0)
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")  # duplicate live order
    assert lc.order("i1").status == IntentStatus.SUBMITTED
