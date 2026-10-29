"""Operator recovery for an intent reserved but never sent to the venue."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts.resolve_unsubmitted_order import resolve
from tests.test_execution_lifecycle import sample_unified_decision
from trading_agent.execution.canonical import EvidenceState, RiskLevel
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionLifecycle,
    IntentStatus,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.live_safety import LiveRiskStateStore, LiveSafetyError

KEY = "unsubmitted-recovery-integrity-key-with-32-plus-characters"
ORDER_KEY = "lta-test-sol-unsubmitted"
RESERVED_AT = datetime(2026, 10, 29, 14, 11, tzinfo=UTC)
NOW = RESERVED_AT + timedelta(hours=1)


class FakeVenue:
    def __init__(self, *, exists=False, open_with_id=0, trades=0):
        self.exists, self.open_with_id, self.trades = exists, open_with_id, trades
        self.calls: list[tuple] = []

    def client_order_exists(self, symbol, client_order_id):
        self.calls.append(("lookup", symbol, client_order_id))
        return self.exists

    def open_orders_with_client_id(self, symbol, client_order_id):
        return self.open_with_id

    def trades_since(self, symbol, since):
        self.calls.append(("trades", symbol, since))
        return self.trades


def _lifecycle(db):
    return ExecutionLifecycle(
        ExecutionEventStore(str(db)).connect(),
        price_source=lambda s: TrustedPrice(
            price=100.0, exchange_timestamp=datetime.now(UTC), received_at=datetime.now(UTC)
        ),
        inventory_source=lambda s, side: 10.0 if side == "sell" else 0.0,
        portfolio_source=lambda s: PortfolioRiskSnapshot(
            symbol=s, position_quantity=4.0, available_quantity=10.0, equity=100_000.0,
            available_cash=100_000.0, observed_at=datetime.now(UTC), source="test",
        ),
    )


def _authorized_sell(db, *, request_submission=False, io_started=False):
    lc = _lifecycle(db)
    lc.create_order_intent(ORDER_KEY, "SOL/USDT", "sell", 4.0)
    lc.approve_risk(
        ORDER_KEY,
        risk_decision=sample_unified_decision(
            decision_id="d-sell", forecast_fingerprint="fp", model_artifact_id="m",
            requested_target_exposure=0.0, allowed_target_exposure=0.0,
            max_new_exposure=0.0, reduce_only=True, risk_level=RiskLevel.LOW,
            reason_codes=("SELL",), calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal", calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN, ood_score=0.1,
            regime_state=EvidenceState.KNOWN, regime_entropy=0.2, interval_width=0.05,
        ),
    )
    lc.authorize_order(ORDER_KEY, idempotency_key=ORDER_KEY)
    if request_submission or io_started:
        lc.request_broker_submission(ORDER_KEY, claimed_by=ORDER_KEY)
    if io_started:
        lc.record_broker_io_started(ORDER_KEY)
    return lc


def _stuck_ledger(tmp_path, status="manual_intervention"):
    store = LiveRiskStateStore(tmp_path / "risk.json", integrity_key=KEY)
    store.reserve_order(ORDER_KEY, symbol="SOL/USDT", side="sell", quantity=4.0, now=RESERVED_AT)
    if status != "reserved":
        store.update_order(ORDER_KEY, status="reconciling", now=RESERVED_AT)
    if status == "manual_intervention":
        store.update_order(
            ORDER_KEY, status="manual_intervention",
            error="client order ID not found before reconciliation deadline", now=RESERVED_AT,
        )
    return store


def _resolve(store, db, venue, *, apply, audit=None, min_age=timedelta(minutes=30)):
    return resolve(
        store=store, event_store_path=db, order_key=ORDER_KEY, venue=venue,
        min_age=min_age, apply=apply, audit_log=audit, now=NOW,
    )


def test_report_only_changes_nothing(tmp_path):
    db = tmp_path / "events.db"
    _authorized_sell(db)
    store = _stuck_ledger(tmp_path)
    before = (tmp_path / "risk.json").read_bytes()

    report = _resolve(store, db, FakeVenue(), apply=False)

    assert report.eligible and not report.applied and not report.reasons
    assert (tmp_path / "risk.json").read_bytes() == before
    lc = _lifecycle(db)
    lc.load()
    assert lc.order(ORDER_KEY).status == IntentStatus.AUTHORIZED


@pytest.mark.parametrize("request_submission", [False, True])
def test_apply_closes_ledger_and_lifecycle_and_releases_inventory(tmp_path, request_submission):
    db = tmp_path / "events.db"
    lc = _authorized_sell(db, request_submission=request_submission)
    if not request_submission:
        # As in the live incident: claimed, but no submission event yet.
        assert lc.store.claim_submission(
            intent_id=ORDER_KEY, claimed_by=ORDER_KEY, idempotency_key=ORDER_KEY,
            payload_hash="h",
        )
    assert lc.store.submission_claim(ORDER_KEY) is not None
    store = _stuck_ledger(tmp_path)
    audit = tmp_path / "audit.jsonl"

    report = _resolve(store, db, FakeVenue(), apply=True, audit=str(audit))

    assert report.applied
    reloaded = LiveRiskStateStore(tmp_path / "risk.json", integrity_key=KEY)  # HMAC verifies
    record = reloaded.state.order_ledger[ORDER_KEY]
    assert record["status"] == "rejected" and record["resolution"] == "operator_unsubmitted"
    assert record["status_history"][-1]["evidence"]["venue_lookup"] == "not_found"
    assert ORDER_KEY not in reloaded.unfinished_orders()
    after = _lifecycle(db)
    after.load()
    assert after.order(ORDER_KEY).status == IntentStatus.REJECTED
    assert after.store.active_sell_reservations("SOL/USDT") == pytest.approx(0.0)
    assert after.store.submission_claim(ORDER_KEY) is None
    events = [json.loads(line)["event"] for line in audit.read_text().splitlines()]
    assert events == ["unsubmitted_intent_resolved"]

    again = _resolve(reloaded, db, FakeVenue(), apply=True, audit=str(audit))
    assert again.already_resolved and not again.applied
    assert len(audit.read_text().splitlines()) == 1


def test_trade_window_starts_before_the_reservation(tmp_path):
    db = tmp_path / "events.db"
    _authorized_sell(db)
    venue = FakeVenue()
    _resolve(_stuck_ledger(tmp_path), db, venue, apply=False)
    (since,) = [call[2] for call in venue.calls if call[0] == "trades"]
    assert since < RESERVED_AT


@pytest.mark.parametrize(
    ("venue", "reason"),
    [
        (FakeVenue(exists=True), "venue knows this client order ID"),
        (FakeVenue(open_with_id=1), "open venue order"),
        (FakeVenue(trades=1), "trade(s) on SOL/USDT since the reservation"),
    ],
)
def test_venue_evidence_refuses(tmp_path, venue, reason):
    db = tmp_path / "events.db"
    _authorized_sell(db)
    store = _stuck_ledger(tmp_path)
    before = (tmp_path / "risk.json").read_bytes()

    report = _resolve(store, db, venue, apply=True)

    assert not report.applied and any(reason in r for r in report.reasons)
    assert (tmp_path / "risk.json").read_bytes() == before


def test_refuses_when_lifecycle_shows_broker_io(tmp_path):
    db = tmp_path / "events.db"
    _authorized_sell(db, io_started=True)
    report = _resolve(_stuck_ledger(tmp_path), db, FakeVenue(), apply=True)
    assert not report.applied
    assert any("exec.broker_io_started" in r for r in report.reasons)


def test_refuses_a_young_reservation(tmp_path):
    db = tmp_path / "events.db"
    _authorized_sell(db)
    report = _resolve(
        _stuck_ledger(tmp_path), db, FakeVenue(), apply=True, min_age=timedelta(hours=2)
    )
    assert not report.applied and any("minimum age" in r for r in report.reasons)


def test_refuses_an_entry_with_venue_evidence(tmp_path):
    store = _stuck_ledger(tmp_path, status="reserved")
    store.update_order(ORDER_KEY, status="submitted", exchange_order_id="123", now=RESERVED_AT)
    report = _resolve(store, None, FakeVenue(), apply=True)
    assert not report.applied
    assert any("submitted" in r for r in report.reasons)
    assert any("exchange order ID" in r for r in report.reasons)


@pytest.mark.parametrize(
    "evidence",
    [
        {"venue_lookup": "found", "open_orders_with_client_id": 0, "trades_since_reservation": 0, "checked_at": "t"},
        {"venue_lookup": "not_found", "open_orders_with_client_id": 0, "trades_since_reservation": 2, "checked_at": "t"},
        {"venue_lookup": "not_found", "open_orders_with_client_id": 0, "trades_since_reservation": 0},
    ],
)
def test_store_method_requires_complete_evidence(tmp_path, evidence):
    store = _stuck_ledger(tmp_path)
    with pytest.raises(LiveSafetyError):
        store.resolve_unsubmitted_order(ORDER_KEY, evidence=evidence, now=NOW)
    assert store.state.order_ledger[ORDER_KEY]["status"] == "manual_intervention"


def test_store_method_refuses_filled_entries(tmp_path):
    store = _stuck_ledger(tmp_path, status="reserved")
    store.update_order(ORDER_KEY, status="submitted", now=RESERVED_AT)
    store.update_order(ORDER_KEY, status="acknowledged", exchange_order_id="9", now=RESERVED_AT)
    store.update_order(ORDER_KEY, status="filled", filled_quantity=4.0, now=RESERVED_AT)
    good = {"venue_lookup": "not_found", "open_orders_with_client_id": 0,
            "trades_since_reservation": 0, "checked_at": "t"}
    with pytest.raises(LiveSafetyError, match="filled"):
        store.resolve_unsubmitted_order(ORDER_KEY, evidence=good, now=NOW)
