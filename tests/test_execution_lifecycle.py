"""Wave C — event-sourced execution lifecycle tests.

Covers: deterministic replay, idempotency, duplicate-event handling, crash
recovery, sequence validation, auditability, event schema version, durable
snapshot + restore (schema_version/checksum/partial/corrupt rejection).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle import (
    EVENT_SCHEMA_VERSION,
    EventValidationError,
    ExecutionEventStore,
    ExecutionEventType,
    ExecutionHealth,
    ExecutionLifecycle,
    IntentStatus,
    InvariantViolation,
    LifecycleError,
    ProtectionState,
    ReconciliationState,
    SequenceGapError,
    SnapshotIntegrityError,
    TrustedPrice,
    make_event,
)


def sample_unified_decision(
    *,
    decision_id: str = "decision-1",
    forecast_fingerprint: str = "fp-1",
    model_artifact_id: str = "model-v1",
    requested_target_exposure: float = 0.5,
    allowed_target_exposure: float = 0.4,
    max_new_exposure: float = 0.4,
    reduce_only: bool = False,
    risk_level: RiskLevel = RiskLevel.LOW,
    reason_codes: tuple[Any, ...] = ("APPROVED",),
    calibration_state: EvidenceState = EvidenceState.KNOWN,
    calibration_artifact_id: str | None = "cal-1",
    calibration_ece: float = 0.02,
    ood_state: EvidenceState = EvidenceState.KNOWN,
    ood_score: float = 0.1,
    regime_state: EvidenceState = EvidenceState.KNOWN,
    regime_entropy: float = 0.2,
    interval_width: float = 0.05,
    created_at: datetime | None = None,
) -> UnifiedRiskDecision:
    max_new = min(max_new_exposure, allowed_target_exposure)
    return UnifiedRiskDecision(
        decision_id=decision_id,
        forecast_fingerprint=forecast_fingerprint,
        model_artifact_id=model_artifact_id,
        requested_target_exposure=requested_target_exposure,
        allowed_target_exposure=allowed_target_exposure,
        max_new_exposure=max_new,
        reduce_only=reduce_only,
        risk_level=risk_level,
        reason_codes=reason_codes,
        calibration_state=calibration_state,
        calibration_artifact_id=calibration_artifact_id,
        calibration_ece=calibration_ece,
        ood_state=ood_state,
        ood_score=ood_score,
        regime_state=regime_state,
        regime_entropy=regime_entropy,
        interval_width=interval_width,
        created_at=created_at or datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path):
    return ExecutionEventStore(tmp_path / "exec.db").connect()


@pytest.fixture
def lifecycle(store):
    return ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )


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
        lc = ExecutionLifecycle(
            store,
            price_source=lambda s: TrustedPrice(
                price=100.0,
                exchange_timestamp=datetime.now(UTC),
                received_at=datetime.now(UTC),
            ),
        )
        lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        risk_decision = sample_unified_decision(
            decision_id="decision-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_target_exposure=0.5,
            allowed_target_exposure=0.5,
            max_new_exposure=0.5,
            reduce_only=False,
            risk_level=RiskLevel.LOW,
            reason_codes=("APPROVED",),
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        lc.approve_risk("i1", risk_decision=risk_decision)
        lc.submit_order("i1", exchange_order_id="ex_1")
        lc.acknowledge_broker("i1", broker_order_id="br_1")
        lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
        order = lc.order("i1")
        lc.create_protective_order(
            symbol="BTC/USDT",
            kind="stop_loss",
            trigger_price=90.0,
            parent_intent_id="i1",
        )
        protective_id = order.protective_order_ids[0]
        lc.acknowledge_protective_order(protective_id, broker_ack_id="ack_1")
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
    assert lc2.state.protection_state["i1"] == ProtectionState.PROTECTED
    assert lc2.state.reconciliation == ReconciliationState.NONE


def test_crash_between_submit_and_persist_leaves_no_phantom(tmp_path):
    path = tmp_path / "crash.db"
    with ExecutionEventStore(path).connect() as store:
        lc = ExecutionLifecycle(
            store,
            price_source=lambda s: TrustedPrice(
                price=100.0,
                exchange_timestamp=datetime.now(UTC),
                received_at=datetime.now(UTC),
            ),
        )
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.approve_risk("i1", risk_decision=risk_decision)
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.approve_risk("i1", risk_decision=risk_decision)
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
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
    )
    lc.create_order_intent("i1", "BTC/USDT", "sell", 1.0)
    lc.approve_risk("i1")
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")
    assert lc.order("i1").status == IntentStatus.APPROVED
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.approve_risk("i1", risk_decision=risk_decision)
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
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
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")  # duplicate live order
    assert lc.order("i1").status == IntentStatus.SUBMITTED


# ── P0 regression tests ────────────────────────────────────────────────


def test_kill_switch_blocks_buy_but_allows_reduce_only_sell(store):
    inventory = {"BTC/USDT": 1.0}
    kill = {"active": False}
    lc = ExecutionLifecycle(
        store,
        kill_switch_active=lambda: kill["active"],
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    # Existing position: buy 1.0 first
    lc.create_order_intent("i_buy", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i_buy", risk_decision=risk_decision)
    lc.submit_order("i_buy", exchange_order_id="ex_1")
    lc.acknowledge_broker("i_buy", broker_order_id="br_1")
    lc.receive_fill("i_buy", 1.0, 99.5, protective_trigger=90.0)
    assert lc.order("i_buy").status == IntentStatus.FILLED
    # Kill switch ON
    kill["active"] = True
    # New BUY blocked
    with pytest.raises(InvariantViolation):
        lc.create_order_intent("i_new_buy", "BTC/USDT", "buy", 1.0)
    # Reduce-only SELL allowed
    lc.create_order_intent("i_sell", "BTC/USDT", "sell", 1.0)
    risk_decision_sell = sample_unified_decision(
        decision_id="decision-2",
        forecast_fingerprint="fp-2",
        model_artifact_id="model-v1",
        requested_target_exposure=0.0,
        allowed_target_exposure=0.0,
        max_new_exposure=0.0,
        reduce_only=True,
        risk_level=RiskLevel.LOW,
        reason_codes=("REDUCE_ONLY",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.approve_risk("i_sell", risk_decision=risk_decision_sell)
    lc.submit_order("i_sell", exchange_order_id="ex_2")
    lc.acknowledge_broker("i_sell", broker_order_id="br_2")
    lc.receive_fill("i_sell", 1.0, 101.0)
    assert lc.order("i_sell").status == IntentStatus.FILLED


def test_kill_switch_blocks_sell_exceeding_inventory(store):
    inventory = {"BTC/USDT": 0.5}
    kill = {"active": True}
    lc = ExecutionLifecycle(
        store,
        kill_switch_active=lambda: kill["active"],
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
    )
    # SELL larger than inventory → INCREASE (would create short) → blocked
    with pytest.raises(InvariantViolation):
        lc.create_order_intent("i_sell", "BTC/USDT", "sell", 1.0)


def test_trusted_price_stale_and_invalid_blocked(store):
    now = datetime.now(UTC)
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=now,
            received_at=now - timedelta(seconds=120),  # stale
        ),
        max_price_age_seconds=60,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1")
    with pytest.raises(InvariantViolation):
        lc.submit_order("i1", exchange_order_id="ex_1")

    # Future timestamp blocked
    lc2 = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=now,
            received_at=now + timedelta(seconds=10),  # future
        ),
        max_price_age_seconds=60,
    )
    lc2.create_order_intent("i2", "BTC/USDT", "buy", 1.0)
    lc2.approve_risk("i2")
    with pytest.raises(InvariantViolation):
        lc2.submit_order("i2", exchange_order_id="ex_2")

    # NaN price blocked
    lc3 = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=float("nan"),
            exchange_timestamp=now,
            received_at=now,
        ),
        max_price_age_seconds=60,
    )
    lc3.create_order_intent("i3", "BTC/USDT", "buy", 1.0)
    lc3.approve_risk("i3")
    with pytest.raises(InvariantViolation):
        lc3.submit_order("i3", exchange_order_id="ex_3")


def test_cumulative_sell_inventory_guard(store):
    inventory = {"BTC/USDT": 1.0}
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
    )
    lc.create_order_intent("i1", "BTC/USDT", "sell", 1.0)
    lc.approve_risk("i1")
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    # Partial fill #1: 0.6 OK (inventory 1.0)
    lc.receive_fill("i1", 0.6, 100.0)
    assert lc.order("i1").filled_size == 0.6
    # Partial fill #2: 0.5 would make cumulative 1.1 > inventory 1.0 → blocked
    with pytest.raises(InvariantViolation):
        lc.receive_fill("i1", 0.5, 100.0)
    assert lc.order("i1").filled_size == 0.6  # unchanged


def test_manual_intervention_blocks_new_exposure(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    # Unknown broker state → manual
    report = lc.reconcile_broker_state({"ex_1": "weird_state"})
    assert "i1" in report["manual"]
    assert lc.order("i1").status == IntentStatus.MANUAL
    assert lc.state.manual_blocked is True
    # New intent blocked
    with pytest.raises(InvariantViolation):
        lc.create_order_intent("i2", "BTC/USDT", "buy", 1.0)
    # Existing live orders untouched


def test_resolve_reconciliation_requires_no_manual_issues(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.reconcile_broker_state({"ex_1": "weird_state"})
    with pytest.raises(LifecycleError):
        lc.resolve_reconciliation()


def test_append_batch_sequence_per_aggregate(tmp_path):
    path = tmp_path / "batch_seq.db"
    with ExecutionEventStore(path).connect() as store:
        # Batch with interleaved aggregates
        events = [
            make_event(
                ExecutionEventType.ORDER_INTENT_CREATED,
                "i1",
                1,
                payload={"symbol": "BTC/USDT", "side": "buy", "size": 1.0},
            ),
            make_event(
                ExecutionEventType.ORDER_INTENT_CREATED,
                "i2",
                1,
                payload={"symbol": "ETH/USDT", "side": "buy", "size": 1.0},
            ),
            make_event(
                ExecutionEventType.RISK_APPROVED, "i1", 2, payload={"rationale": "ok"}
            ),
            make_event(
                ExecutionEventType.RISK_APPROVED, "i2", 2, payload={"rationale": "ok"}
            ),
        ]
        results = store.append_batch(events)
        assert all(results)
        assert store.max_seq("i1") == 2
        assert store.max_seq("i2") == 2

        # Gap in batch for same aggregate → entire batch rejected
        bad_events = [
            make_event(
                ExecutionEventType.ORDER_SUBMITTED,
                "i1",
                5,
                payload={"order_id": "i1", "exchange_order_id": "ex_1"},
            ),
        ]
        with pytest.raises(SequenceGapError):
            store.append_batch(bad_events)
        # Nothing written
        assert store.max_seq("i1") == 2


def test_protection_gap_blocks_new_exposure(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        require_protective_order=True,
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    # Fill without protective trigger → protection gap
    lc.receive_fill("i1", 1.0, 99.5)
    assert lc.state.execution_health == ExecutionHealth.PROTECTION_GAP
    assert lc.state.protection_state["i1"] == ProtectionState.PROTECTION_REQUIRED
    # New exposure blocked
    with pytest.raises(InvariantViolation):
        lc.create_order_intent("i2", "BTC/USDT", "buy", 1.0)


def test_fill_with_trigger_requires_ack_for_protected(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        require_protective_order=True,
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
    order = lc.order("i1")
    lc.create_protective_order(
        symbol="BTC/USDT",
        kind="stop_loss",
        trigger_price=90.0,
        parent_intent_id="i1",
    )
    assert order.protective_order_ids
    assert lc.state.protection_state["i1"] == ProtectionState.PROTECTION_REQUIRED
    # New BUY blocked while protection required
    with pytest.raises(InvariantViolation):
        lc.create_order_intent("i2", "BTC/USDT", "buy", 1.0)
    # Reduce-only SELL allowed (inventory guard only)
    inventory = {"BTC/USDT": 1.0}
    lc2 = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: inventory.get(sym, 0.0),
        require_protective_order=True,
    )
    lc2.create_order_intent("i3", "BTC/USDT", "sell", 1.0)
    risk_decision_sell = sample_unified_decision(
        decision_id="decision-3",
        forecast_fingerprint="fp-3",
        model_artifact_id="model-v1",
        requested_target_exposure=0.0,
        allowed_target_exposure=0.0,
        max_new_exposure=0.0,
        reduce_only=True,
        risk_level=RiskLevel.LOW,
        reason_codes=("REDUCE_ONLY",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc2.approve_risk("i3", risk_decision=risk_decision_sell)
    lc2.submit_order("i3", exchange_order_id="ex_3")
    lc2.acknowledge_broker("i3", broker_order_id="br_3")
    # Should not raise
    lc2.receive_fill("i3", 1.0, 100.0)


def test_acknowledge_protective_order_sets_protected(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        require_protective_order=True,
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
    order = lc.order("i1")
    lc.create_protective_order(
        symbol="BTC/USDT",
        kind="stop_loss",
        trigger_price=90.0,
        parent_intent_id="i1",
    )
    protective_id = order.protective_order_ids[0]
    assert lc.state.protection_state["i1"] == ProtectionState.PROTECTION_REQUIRED
    lc.acknowledge_protective_order(protective_id, broker_ack_id="ack_1")
    assert lc.state.protection_state["i1"] == ProtectionState.PROTECTED


def test_crash_between_fill_and_protective_replay_requires_protection(tmp_path):
    path = tmp_path / "crash_protection.db"
    with ExecutionEventStore(path).connect() as store:
        lc = ExecutionLifecycle(
            store,
            price_source=lambda s: TrustedPrice(
                price=100.0,
                exchange_timestamp=datetime.now(UTC),
                received_at=datetime.now(UTC),
            ),
            require_protective_order=True,
        )
        risk_decision = sample_unified_decision(
            decision_id="decision-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_target_exposure=0.5,
            allowed_target_exposure=0.5,
            max_new_exposure=0.5,
            reduce_only=False,
            risk_level=RiskLevel.LOW,
            reason_codes=("APPROVED",),
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lc.approve_risk("i1", risk_decision=risk_decision)
        lc.submit_order("i1", exchange_order_id="ex_1")
        lc.acknowledge_broker("i1", broker_order_id="br_1")
        lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
        # Crash before protective order created/persisted.
        # Protective order is created by an external recovery step, not by fill.
    with ExecutionEventStore(path).connect() as store2:
        lc2 = ExecutionLifecycle(store2)
        lc2.load()
        assert lc2.state.protection_state["i1"] == ProtectionState.PROTECTION_REQUIRED
        assert lc2.order("i1").protective_order_ids == []


def test_repeated_recovery_does_not_duplicate_protection(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        require_protective_order=True,
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    lc.receive_fill("i1", 1.0, 99.5, protective_trigger=90.0)
    order = lc.order("i1")
    lc.create_protective_order(
        symbol="BTC/USDT",
        kind="stop_loss",
        trigger_price=90.0,
        parent_intent_id="i1",
    )
    protective_id = order.protective_order_ids[0]
    lc.acknowledge_protective_order(protective_id, broker_ack_id="ack_1")
    # Re-acknowledge same protective order idempotently
    lc.acknowledge_protective_order(protective_id, broker_ack_id="ack_1")
    assert lc.state.protection_state["i1"] == ProtectionState.PROTECTED
    assert len(order.protective_order_ids) == 1


def test_unknown_broker_state_fail_closed(store):
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        require_protective_order=True,
    )
    risk_decision = sample_unified_decision(
        decision_id="decision-1",
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.5,
        max_new_exposure=0.5,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
    )
    lc.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("i1", risk_decision=risk_decision)
    lc.submit_order("i1", exchange_order_id="ex_1")
    lc.acknowledge_broker("i1", broker_order_id="br_1")
    report = lc.reconcile_broker_state({"ex_1": "weird_state"})
    assert "i1" in report["unknown"]
    assert lc.state.execution_health == ExecutionHealth.MANUAL_BLOCKED
