from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.migrate_global_seq import analyze, migrate, verify
from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle.events import (
    EVENT_SCHEMA_VERSION,
    ExecutionEventType,
    make_event,
)
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionHealth,
    ExecutionLifecycle,
    IntentStatus,
    LifecycleState,
    OrderState,
    ProtectionState,
    ProtectiveOrderState,
    ReconciliationState,
)
from trading_agent.execution.lifecycle.store import (
    ExecutionEventStore,
    LegacyCutoverStateRequired,
    SnapshotIntegrityError,
    snapshot_checksum,
)


def _create_legacy_financial_db(path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE execution_events (
            event_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL,
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            payload TEXT NOT NULL,
            correlation_id TEXT,
            causation_id TEXT,
            occurred_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            global_seq INTEGER NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        """
    )
    event_types = (
        "exec.order_intent_created",
        "exec.risk_approved",
        "exec.order_authorized",
        "exec.broker_submission_requested",
        "exec.partial_fill_received",
    )
    rows: list[tuple[object, ...]] = []
    for seq, event_type in enumerate(event_types, start=1):
        rows.append(
            (
                f"legacy-{seq}",
                seq,
                "legacy-intent",
                event_type,
                1,
                json.dumps({"legacy": True, "event_type": event_type}),
                "legacy-correlation",
                None if seq == 1 else f"legacy-{seq - 1}",
                f"2024-01-01T00:0{seq}:00+00:00",
                f"2024-01-01T00:0{seq}:00+00:00",
                0,
            )
        )
    conn.executemany(
        "INSERT INTO execution_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return rows


def _trusted_state() -> LifecycleState:
    created_at = datetime(2024, 1, 1, tzinfo=UTC)
    risk = UnifiedRiskDecision(
        decision_id="verified-risk",
        forecast_fingerprint="verified-forecast",
        model_artifact_id="verified-model",
        requested_target_exposure=0.20,
        allowed_target_exposure=0.16,
        max_new_exposure=0.10,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=(),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="verified-calibration",
        calibration_ece=0.01,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.02,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.03,
        interval_width=0.04,
        created_at=created_at,
    )
    order = OrderState(
        intent_id="legacy-intent",
        symbol="BTC/USDT",
        side="sell",
        size=1.0,
        status=IntentStatus.PARTIALLY_FILLED,
        risk_approved=True,
        risk_decision=risk,
        broker_order_id="broker-verified",
        exchange_order_id="exchange-verified",
        filled_size=0.4,
        authorized_quantity=1.0,
        reserved_quantity=1.0,
        released_quantity=0.1,
        avg_fill_price=50_000.0,
        fees=1.25,
        protective_order_ids=["protective-verified"],
        manual_reasons=["verified-review"],
        created_at=created_at,
        authorization_id="authorization-verified",
        idempotency_key="idempotency-verified",
        payload_hash="payload-verified",
        permission="ALLOW",
        authorized_at=created_at.isoformat(),
        submission_requested=True,
        io_started=True,
        price_reference=50_000.0,
        portfolio_equity=100_000.0,
        current_position_quantity=2.0,
        resulting_position_quantity=1.0,
        current_exposure=0.20,
        resulting_exposure=0.10,
        incremental_exposure=0.0,
    )
    return LifecycleState(
        orders={order.intent_id: order},
        protective_orders={
            "protective-verified": ProtectiveOrderState(
                order_id="protective-verified",
                symbol="BTC/USDT",
                kind="stop_loss",
                trigger_price=45_000.0,
                status="active",
            )
        },
        reconciliation=ReconciliationState.STARTED,
        last_event_ids={order.intent_id: "legacy-5"},
        state_version=17,
        execution_health=ExecutionHealth.RECONCILING,
        protection_state={order.intent_id: ProtectionState.PROTECTED},
        manual_blocked=True,
        unresolved_manual_intents={order.intent_id},
    )


def _write_snapshot(
    path: Path,
    state: LifecycleState | None = None,
    *,
    verified_empty: bool = False,
) -> Path:
    state = state or LifecycleState()
    envelope = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "state_version": state.state_version,
        "last_global_seq": 0,
        "provenance": "pytest independently verified pre-cutover state",
        "verified_empty": verified_empty,
        "state": state.to_dict(),
    }
    envelope["checksum"] = snapshot_checksum(envelope["state"])
    path.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    return path


def _migrated_db(tmp_path: Path) -> tuple[Path, LifecycleState]:
    db_path = tmp_path / "migrated.db"
    state = _trusted_state()
    _create_legacy_financial_db(db_path)
    snapshot_path = _write_snapshot(tmp_path / "verified-snapshot.json", state)
    assert migrate(db_path, snapshot_path=snapshot_path) == 5
    return db_path, state


def test_legacy_financial_history_without_verified_snapshot_fails_without_mutation(
    tmp_path: Path,
) -> None:
    """A non-empty unordered financial log must never become an empty authority."""
    db_path = tmp_path / "legacy.db"
    original_rows = _create_legacy_financial_db(db_path)

    with pytest.raises(RuntimeError, match="trustworthy.*snapshot|verified.*snapshot"):
        migrate(str(db_path))

    conn = sqlite3.connect(db_path)
    try:
        rows_after = conn.execute(
            "SELECT * FROM execution_events ORDER BY aggregate_id, seq"
        ).fetchall()
        tables_after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert rows_after == original_rows
    assert "execution_snapshots" not in tables_after
    assert "execution_migration_state" not in tables_after


def test_unknown_legacy_event_aborts_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "unknown.db"
    _create_legacy_financial_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE execution_events SET event_type = 'exec.future_unknown' "
        "WHERE event_id = 'legacy-3'"
    )
    conn.commit()
    conn.close()
    before = db_path.read_bytes()
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _trusted_state())

    with pytest.raises(RuntimeError, match="unknown legacy"):
        migrate(db_path, snapshot_path=snapshot_path)

    assert db_path.read_bytes() == before


def test_empty_snapshot_rejected_for_financial_history(tmp_path: Path) -> None:
    db_path = tmp_path / "empty-authority.db"
    _create_legacy_financial_db(db_path)
    snapshot_path = _write_snapshot(tmp_path / "empty.json")

    with pytest.raises(LegacyCutoverStateRequired, match="empty authoritative"):
        migrate(db_path, snapshot_path=snapshot_path)


def test_operator_snapshot_checksum_mismatch_aborts_without_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bad-checksum.db"
    _create_legacy_financial_db(db_path)
    before = db_path.read_bytes()
    snapshot_path = _write_snapshot(tmp_path / "bad-checksum.json", _trusted_state())
    envelope = json.loads(snapshot_path.read_text(encoding="utf-8"))
    envelope["checksum"] = "0" * 64
    snapshot_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        migrate(db_path, snapshot_path=snapshot_path)

    assert db_path.read_bytes() == before


def test_cli_without_verified_snapshot_exits_nonzero(tmp_path: Path) -> None:
    db_path = tmp_path / "cli-blocked.db"
    _create_legacy_financial_db(db_path)

    result = subprocess.run(
        [sys.executable, "scripts/migrate_global_seq.py", str(db_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "MIGRATION BLOCKED" in result.stderr
    assert "verified pre-cutover snapshot" in result.stderr


def test_recovery_rejects_pre_cutover_rows_without_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "orphaned-legacy.db"
    _create_legacy_financial_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE execution_events SET global_seq = -1")
    conn.commit()
    conn.close()

    store = ExecutionEventStore(db_path).connect()
    try:
        with pytest.raises(LegacyCutoverStateRequired, match="without a verified"):
            ExecutionLifecycle(store).load()
    finally:
        store.close()


def test_valid_snapshot_cutover_metadata_and_first_sequence(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        assert {
            row["global_seq"]
            for row in conn.execute("SELECT global_seq FROM execution_events")
        } == {-1}
        metadata = conn.execute("SELECT * FROM execution_migration_state").fetchone()
        assert metadata is not None
        assert metadata["source_event_count"] == 5
        assert metadata["legacy_event_count"] == 5
        assert metadata["first_post_cutover_global_seq"] == 1
        assert metadata["snapshot_checksum"]
        assert metadata["source_event_checksum"]
        assert metadata["source_provenance"]
    finally:
        conn.close()

    store = ExecutionEventStore(db_path).connect()
    try:
        event = make_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            "post-cutover",
            1,
            payload={"symbol": "ETH/USDT", "side": "buy", "size": 1.0},
        )
        assert store.append(event)
        persisted = store.read_events_global()
        assert [item.global_seq for item in persisted] == [1]
    finally:
        store.close()


def test_existing_valid_global_snapshot_is_accepted_as_authority(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "existing-snapshot.db"
    _create_legacy_financial_db(db_path)
    trusted = _trusted_state()
    store = ExecutionEventStore(db_path).connect()
    try:
        store.save_snapshot(
            "global",
            trusted.to_dict(),
            state_version=trusted.state_version,
            last_global_seq=0,
        )
    finally:
        store.close()

    assert migrate(db_path) == 5
    assert verify(db_path)


def test_restart_equals_snapshot_plus_post_cutover_delta(tmp_path: Path) -> None:
    db_path, trusted = _migrated_db(tmp_path)
    store = ExecutionEventStore(db_path).connect()
    try:
        event = make_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            "post-cutover",
            1,
            payload={"symbol": "ETH/USDT", "side": "buy", "size": 2.0},
        )
        store.append(event)
        persisted = store.read_events_global()
        expected = ExecutionLifecycle(store).replay(
            persisted, initial_state=LifecycleState.from_dict(trusted.to_dict())
        )
    finally:
        store.close()

    restarted_store = ExecutionEventStore(db_path).connect()
    try:
        restarted = ExecutionLifecycle(restarted_store).load()
        assert restarted.to_dict() == expected.to_dict()
    finally:
        restarted_store.close()


def test_full_snapshot_semantic_round_trip_is_lossless() -> None:
    original = _trusted_state().to_dict()
    restored = LifecycleState.from_dict(original).to_dict()

    assert restored == original
    order = restored["orders"]["legacy-intent"]
    assert order["risk_decision"]["decision_id"] == "verified-risk"
    assert order["authorization_id"] == "authorization-verified"
    assert order["payload_hash"] == "payload-verified"
    assert order["submission_requested"] is True
    assert order["io_started"] is True
    assert order["reserved_quantity"] == 1.0
    assert order["current_exposure"] == 0.20
    assert restored["protection_state"]["legacy-intent"] == "protected"
    assert restored["reconciliation"] == "started"
    assert restored["manual_blocked"] is True


def test_duplicate_positive_global_sequence_rejected_by_sqlite(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    store = ExecutionEventStore(db_path).connect()
    try:
        first = make_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            "positive-one",
            1,
            payload={"symbol": "ETH/USDT", "side": "buy", "size": 1.0},
        )
        store.append(first)
    finally:
        store.close()

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO execution_events
                (event_id, seq, aggregate_id, event_type, schema_version, payload,
                 correlation_id, causation_id, occurred_at, ingested_at, global_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-positive",
                    1,
                    "positive-two",
                    ExecutionEventType.ORDER_INTENT_CREATED.value,
                    EVENT_SCHEMA_VERSION,
                    "{}",
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                    1,
                ),
            )
    finally:
        conn.close()


def test_gap_in_positive_global_sequence_blocks_recovery(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO execution_events
        (event_id, seq, aggregate_id, event_type, schema_version, payload,
         correlation_id, causation_id, occurred_at, ingested_at, global_seq)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gap-at-two",
            1,
            "gap-intent",
            ExecutionEventType.ORDER_INTENT_CREATED.value,
            EVENT_SCHEMA_VERSION,
            json.dumps({"symbol": "ETH/USDT", "side": "buy", "size": 1.0}),
            None,
            None,
            now,
            now,
            2,
        ),
    )
    conn.commit()
    conn.close()

    store = ExecutionEventStore(db_path).connect()
    try:
        with pytest.raises(SnapshotIntegrityError, match="contiguous from 1"):
            ExecutionLifecycle(store).load()
    finally:
        store.close()


def test_corrupted_cutover_snapshot_blocks_recovery(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE execution_cutover_snapshots SET state_json = ?",
        (json.dumps({"orders": {}}),),
    )
    conn.commit()
    conn.close()

    store = ExecutionEventStore(db_path).connect()
    try:
        with pytest.raises(SnapshotIntegrityError, match="checksum"):
            ExecutionLifecycle(store).load()
    finally:
        store.close()


def test_unknown_pre_cutover_event_blocks_recovery(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE execution_events SET event_type = 'exec.unknown_after_cutover' "
        "WHERE event_id = 'legacy-1'"
    )
    conn.commit()
    conn.close()

    store = ExecutionEventStore(db_path).connect()
    try:
        with pytest.raises(SnapshotIntegrityError, match="unknown or malformed"):
            ExecutionLifecycle(store).load()
    finally:
        store.close()


def test_interruption_before_commit_rolls_back_entire_cutover(tmp_path: Path) -> None:
    db_path = tmp_path / "interrupted.db"
    original_rows = _create_legacy_financial_db(db_path)
    snapshot_path = _write_snapshot(tmp_path / "snapshot.json", _trusted_state())

    def interrupt() -> None:
        raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        migrate(db_path, snapshot_path=snapshot_path, _before_commit=interrupt)

    conn = sqlite3.connect(db_path)
    try:
        assert (
            conn.execute(
                "SELECT * FROM execution_events ORDER BY aggregate_id, seq"
            ).fetchall()
            == original_rows
        )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "execution_cutover_snapshots" not in tables
    assert "execution_migration_state" not in tables


def test_rerun_is_idempotent_and_verifies_existing_cutover(tmp_path: Path) -> None:
    db_path, _ = _migrated_db(tmp_path)
    assert migrate(db_path) == 0
    assert verify(db_path)


def test_dry_run_reports_blocker_without_any_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "dry-run.db"
    _create_legacy_financial_db(db_path)
    before = db_path.read_bytes()

    assert migrate(db_path, dry_run=True) == 5
    output = capsys.readouterr().out

    assert db_path.read_bytes() == before
    assert '"legacy_event_count": 5' in output
    assert '"known_event_count": 5' in output
    assert '"unknown_event_count": 0' in output
    assert '"migration_possible": false' in output
    assert analyze(db_path).migration_possible is False


def test_append_and_batch_allocate_unique_sequences_under_concurrency(
    tmp_path: Path,
) -> None:
    db_path, _ = _migrated_db(tmp_path)
    bootstrap = ExecutionEventStore(db_path).connect()
    bootstrap.close()
    barrier = threading.Barrier(2)

    def append_one() -> None:
        store = ExecutionEventStore(db_path).connect()
        try:
            event = make_event(
                ExecutionEventType.ORDER_INTENT_CREATED,
                "concurrent-single",
                1,
                payload={"symbol": "ETH/USDT", "side": "buy", "size": 1.0},
            )
            barrier.wait(timeout=10)
            store.append(event)
        finally:
            store.close()

    def append_batch() -> None:
        store = ExecutionEventStore(db_path).connect()
        try:
            events = [
                make_event(
                    ExecutionEventType.ORDER_INTENT_CREATED,
                    "concurrent-batch",
                    1,
                    payload={"symbol": "SOL/USDT", "side": "buy", "size": 1.0},
                ),
                make_event(
                    ExecutionEventType.RISK_APPROVED,
                    "concurrent-batch",
                    2,
                    payload={},
                ),
            ]
            barrier.wait(timeout=10)
            store.append_batch(events)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(append_one), pool.submit(append_batch)]
        for future in futures:
            future.result(timeout=30)

    store = ExecutionEventStore(db_path).connect()
    try:
        assert [event.global_seq for event in store.read_events_global()] == [1, 2, 3]
    finally:
        store.close()


def test_idempotent_batch_skip_does_not_create_global_sequence_gap(
    tmp_path: Path,
) -> None:
    db_path, _ = _migrated_db(tmp_path)
    store = ExecutionEventStore(db_path).connect()
    try:
        duplicate = make_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            "already-persisted",
            1,
            payload={"symbol": "ETH/USDT", "side": "buy", "size": 1.0},
        )
        assert store.append(duplicate)
        next_event = make_event(
            ExecutionEventType.ORDER_INTENT_CREATED,
            "batch-next",
            1,
            payload={"symbol": "SOL/USDT", "side": "buy", "size": 1.0},
        )

        assert store.append_batch([duplicate, next_event], expect_seq=False) == [
            False,
            True,
        ]
        assert [event.global_seq for event in store.read_events_global()] == [1, 2]
    finally:
        store.close()
