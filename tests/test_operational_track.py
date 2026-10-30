"""Testnet operational (E2E) track: authorization, waiver, driver and full runs.

Local fixtures only: the real runner, admission pipeline, planner, lifecycle and
ledger against the Decimal accounting broker. No network IO.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import daily_check
import live_enhanced_ma_binance as runner

from tests.test_order_permission import _fresh_price, _sample_risk_decision
from tests.test_promoted_live_admission import (
    AccountingBroker,
    _testnet_args,
    install_exchange_boundary,
    market_frame,
    produce,
)
from trading_agent.authority.config import Environment
from trading_agent.execution.canonical import (
    EvidenceState,
    OperationalEvidenceWaiver,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle.lifecycle import ExecutionHealth, ExposureEffect
from trading_agent.execution.live_safety import (
    LiveRiskStateStore,
    LiveSafetyError,
    account_fingerprint,
)
from trading_agent.execution.operational_authorization import (
    OperationalAuthorization,
    OperationalAuthorizationError,
    load_authorization,
    write_authorization,
)
from trading_agent.execution.operational_driver import (
    LifecycleExerciser,
    OperationalLiveAdmissionProducer,
    OperationalRuntimeResolver,
    TrackLedgerView,
    driver_code_sha,
    entry_block_reason,
    plan_entries,
    tight_stop_symbols,
    track_ledger_view,
)
from trading_agent.execution.permission import (
    OrderPermission,
    PermissionContext,
    PermissionReason,
    evaluate_order_permission,
)

KEY = "local-fixture-integrity-key-not-for-live"
ACCOUNT = account_fingerprint(exchange="binance-testnet", api_key="local-fixture-venue-uid")


def authorization(**overrides) -> OperationalAuthorization:
    issued = datetime.now(UTC) - timedelta(minutes=5)
    values = dict(
        authorization_id="ops-test-0001",
        account_fingerprint=ACCOUNT,
        symbols=("BTC/USDT",),
        driver_code_sha=driver_code_sha(),
        hold_minutes=230.0,
        tight_stop_every=3,
        tight_stop_pct=0.003,
        max_order_usd=20.0,
        max_open_positions=1,
        max_lifecycles=120,
        max_daily_loss_usd=10.0,
        issued_at=issued,
        expires_at=issued + timedelta(days=14),
        approver="operator",
        reason="local fixture",
    )
    values.update(overrides)
    return OperationalAuthorization(**values)


def load(path, **overrides):
    kwargs = dict(key=KEY, account_fingerprint=ACCOUNT, driver_code_sha=driver_code_sha())
    kwargs.update(overrides)
    return load_authorization(path, **kwargs)


# ── Authorization file ──────────────────────────────────────────────────────


def test_signed_authorization_round_trips_owner_only(tmp_path):
    path = tmp_path / "auth.json"
    expected = authorization()
    write_authorization(path, expected, key=KEY)
    assert load(path) == expected
    assert path.stat().st_mode & 0o777 == 0o600
    assert load(tmp_path / "absent.json") is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda doc: doc.update(max_order_usd=24.0), "signature"),
        (lambda doc: doc.update(environment="mainnet"), "signature"),
        (lambda doc: doc.pop("signature"), "signature"),
    ],
)
def test_edited_authorization_is_refused(tmp_path, change, message):
    path = tmp_path / "auth.json"
    write_authorization(path, authorization(), key=KEY)
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))
    with pytest.raises(OperationalAuthorizationError, match=message):
        load(path)


@pytest.mark.parametrize(
    ("overrides", "loader", "message"),
    [
        ({}, {"account_fingerprint": "another-account"}, "another account"),
        ({}, {"driver_code_sha": "0" * 64}, "driver source changed"),
        ({}, {"key": "another-key"}, "signature"),
        (
            {
                "issued_at": datetime.now(UTC) - timedelta(days=15),
                "expires_at": datetime.now(UTC) - timedelta(days=1),
            },
            {},
            "expired",
        ),
        (
            {
                "issued_at": datetime.now(UTC) + timedelta(hours=1),
                "expires_at": datetime.now(UTC) + timedelta(days=2),
            },
            {},
            "not valid yet",
        ),
    ],
)
def test_authorization_for_another_context_is_refused(tmp_path, overrides, loader, message):
    path = tmp_path / "auth.json"
    write_authorization(path, authorization(**overrides), key=KEY)
    with pytest.raises(OperationalAuthorizationError, match=message):
        load(path, **loader)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_order_usd": 26.0},
        {"max_open_positions": 2},
        {"tight_stop_pct": 0.05},
        {"expires_at": datetime.now(UTC) + timedelta(days=40)},
        {"approver": " "},
        {"max_lifecycles": 301},
    ],
)
def test_authorization_beyond_hard_limits_cannot_be_written(tmp_path, overrides):
    with pytest.raises(OperationalAuthorizationError):
        write_authorization(tmp_path / "auth.json", authorization(**overrides), key=KEY)


# ── Evidence waiver at the permission gate ─────────────────────────────────


def _buy_permission(risk):
    return evaluate_order_permission(
        PermissionContext(
            execution_health=ExecutionHealth.NORMAL,
            exposure_effect=ExposureEffect.INCREASE,
            risk_decision=risk,
            trusted_price=_fresh_price(),
            order_side="buy",
            order_size=1.0,
        )
    )


def _waiver(expires_in=timedelta(days=1)):
    return OperationalEvidenceWaiver(
        authorization_id="ops-test-0001",
        environment="testnet",
        expires_at=datetime.now(UTC) + expires_in,
    )


def _unknown_risk(state=EvidenceState.UNKNOWN, waiver=None):
    risk = _sample_risk_decision(
        calibration_state=state, ood_state=state, regime_state=state
    )
    return replace(risk, evidence_waiver=waiver)


def test_unknown_evidence_buy_needs_an_active_testnet_waiver():
    assert _buy_permission(_unknown_risk()).reason is PermissionReason.MISSING_CALIBRATION_EVIDENCE
    assert _buy_permission(_unknown_risk(waiver=_waiver())).permission is OrderPermission.ALLOW
    expired = _unknown_risk(waiver=_waiver(expires_in=timedelta(seconds=-1)))
    assert _buy_permission(expired).permission is OrderPermission.BLOCK


@pytest.mark.parametrize("state", [EvidenceState.MISSING, EvidenceState.STALE])
def test_waiver_never_admits_missing_or_stale_evidence(state):
    result = _buy_permission(_unknown_risk(state=state, waiver=_waiver()))
    assert result.permission is OrderPermission.BLOCK


def test_waiver_is_testnet_only_and_replays_with_the_decision():
    with pytest.raises(ValueError, match="testnet only"):
        OperationalEvidenceWaiver(
            authorization_id="x", environment="production", expires_at=datetime.now(UTC)
        )
    risk = _unknown_risk(waiver=_waiver())
    serialized = risk.to_dict()
    assert serialized["evidence_waiver"]["authorization_id"] == "ops-test-0001"
    # The lifecycle is event-sourced: the waiver must survive a replay.
    assert UnifiedRiskDecision.from_dict(serialized).evidence_waiver == risk.evidence_waiver
    serialized["evidence_waiver"]["environment"] = "production"
    with pytest.raises(ValueError, match="testnet only"):
        UnifiedRiskDecision.from_dict(serialized)
    assert "evidence_waiver" not in _sample_risk_decision().to_dict()


# ── Driver rules ────────────────────────────────────────────────────────────


def _ledger_record(symbol, side, status, created, filled=0.0):
    return {
        "symbol": symbol,
        "side": side,
        "status": status,
        "filled_quantity": filled,
        "created_at": created.isoformat(),
    }


def test_ledger_view_counts_track_buys_since_the_authorization():
    issued = datetime(2026, 11, 1, tzinfo=UTC)
    ledger = {
        "old": _ledger_record("BTC/USDT", "BUY", "filled", issued - timedelta(hours=1), 1),
        "b1": _ledger_record("BTC/USDT", "BUY", "filled", issued + timedelta(hours=1), 1),
        "s1": _ledger_record("BTC/USDT", "SELL", "filled", issued + timedelta(hours=5), 1),
        "rej": _ledger_record("SOL/USDT", "BUY", "rejected", issued + timedelta(hours=6)),
        "other": _ledger_record("ETH/USDT", "BUY", "filled", issued + timedelta(hours=6), 1),
        "b2": _ledger_record("SOL/USDT", "BUY", "filled", issued + timedelta(hours=7), 1),
        "b3": _ledger_record("BTC/USDT", "BUY", "submitted", issued + timedelta(hours=9)),
    }
    view = track_ledger_view(ledger, symbols=("BTC/USDT", "SOL/USDT"), issued_at=issued)
    assert view.lifecycles_opened == 3
    assert view.latest_entries["BTC/USDT"].index == 2
    assert view.latest_entries["SOL/USDT"].index == 1


def test_entry_caps_blocks_and_tight_stop_rotation():
    auth = authorization(symbols=("A", "B", "C"), max_open_positions=2, max_lifecycles=5)
    now = datetime.now(UTC)
    view = TrackLedgerView(lifecycles_opened=4, latest_entries={})
    assert plan_entries(auth, view, held_symbols={"A"}, block_reason=None) == {"B"}
    assert plan_entries(auth, view, held_symbols=set(), block_reason="x") == frozenset()
    fresh = TrackLedgerView(lifecycles_opened=0, latest_entries={})
    assert entry_block_reason(auth, fresh, daily_loss_usd=0, run_failure=None) is None
    assert "run_failed" in entry_block_reason(
        auth, fresh, daily_loss_usd=0, run_failure="run at 19:01Z ended with run_failed"
    )
    assert "daily loss" in entry_block_reason(auth, fresh, daily_loss_usd=11, run_failure=None)
    capped = TrackLedgerView(lifecycles_opened=5, latest_entries={})
    assert "cap" in entry_block_reason(auth, capped, daily_loss_usd=0, run_failure=None)
    from trading_agent.execution.operational_driver import TrackEntry

    rotation = TrackLedgerView(
        lifecycles_opened=3,
        latest_entries={
            "A": TrackEntry(now, 0),
            "B": TrackEntry(now, 1),
            "C": TrackEntry(now, 2),
        },
    )
    assert tight_stop_symbols(auth, rotation, held_symbols={"A", "B", "C"}) == {"C"}
    assert tight_stop_symbols(
        replace(auth, tight_stop_every=0), rotation, held_symbols={"C"}
    ) == set()


class _Portfolio:
    def __init__(self, quantity):
        self.positions = {"BTC/USDT": quantity}
        self.equity = 1000.0


@pytest.mark.parametrize(
    ("quantity", "entry_age", "may_enter", "signal"),
    [
        (0.0, None, True, "BUY"),
        (0.0, None, False, "HOLD"),
        (0.2, timedelta(minutes=30), True, "HOLD"),
        (0.2, timedelta(minutes=240), True, "SELL"),
        (0.2, None, True, "SELL"),  # a position the track did not open
    ],
)
def test_exerciser_buys_when_flat_and_sells_after_the_hold(quantity, entry_age, may_enter, signal):
    now = datetime.now(UTC)
    exerciser = LifecycleExerciser(
        symbol="BTC/USDT",
        max_order_usd=20.0,
        hold=timedelta(minutes=230),
        entry_at=None if entry_age is None else now - entry_age,
        may_enter=may_enter,
        now=now,
        flat_below_usd=1.0,
    )
    output = exerciser.generate_signal_with_position(market_frame(), _Portfolio(quantity))
    assert output.signal == signal


def test_operational_producer_runs_the_real_pipeline_on_testnet_only(tmp_path):
    auth = authorization()
    resolver = OperationalRuntimeResolver(
        auth,
        view=TrackLedgerView(0, {}),
        entry_symbols=frozenset({"BTC/USDT"}),
        now=datetime.now(UTC),
    )
    assert resolver.resolve_for("BTC/USDT", "1h", Environment.PRODUCTION) is None
    producer = OperationalLiveAdmissionProducer(resolver)
    decisions = produce(tmp_path, producer)
    assert [d["action"] for d in decisions] == ["BUY"]
    risk = decisions[0]["risk_decision"]
    assert decisions[0]["reason"] == "OPERATIONAL_EXERCISER"
    assert risk.calibration_state is EvidenceState.UNKNOWN  # honest label
    assert risk.evidence_waiver.authorization_id == auth.authorization_id
    assert risk.model_artifact_id == resolver.artifact_id
    assert decisions[0]["permission_context"].risk_decision is risk
    with pytest.raises(LiveSafetyError, match="environment does not match"):
        producer.produce(
            inputs={},
            quotes={},
            snapshot=None,
            allocations={},
            lifecycle=None,
            environment=Environment.CANARY,
            limits=None,
            states={},
        )


@pytest.mark.parametrize("quantity", [0.19801, 0.29999, 0.10007, 0.2])
def test_sell_to_flat_plans_the_whole_inventory(tmp_path, quantity):
    """Regression: n * step in binary used to exceed the inventory and block it."""
    from trading_agent.authority.portfolio import PortfolioSnapshot, ReconciliationState
    from trading_agent.execution.batch_models import wrap_market_data
    from trading_agent.execution.live_safety import LiveRiskLimits
    from trading_agent.execution.operational_driver import TrackEntry

    now = datetime.now(UTC)
    resolver = OperationalRuntimeResolver(
        authorization(),
        view=TrackLedgerView(1, {"BTC/USDT": TrackEntry(now - timedelta(hours=5), 0)}),
        entry_symbols=frozenset(),
        now=now,
    )
    broker = AccountingBroker()
    lifecycle, _ = runner.build_canonical_execution_stack(
        broker, event_store_path=str(tmp_path / "events.db")
    )
    equity = 980.0 + quantity * 100
    decisions = OperationalLiveAdmissionProducer(resolver).produce(
        inputs={"BTC/USDT": wrap_market_data("BTC/USDT", "1h", market_frame(), "local-fixture")},
        quotes={"BTC/USDT": runner.trusted_broker_quote(broker, "BTC/USDT", buy=True)},
        snapshot=PortfolioSnapshot(
            equity=equity,
            available_cash=980.0,
            positions={"BTC/USDT": quantity},
            symbol_exposures={"BTC/USDT": quantity * 100 / equity},
            gross_exposure=quantity * 100 / equity,
            reconciliation_state=ReconciliationState.RECONCILED,
        ),
        allocations={"BTC/USDT": 0.2},
        lifecycle=lifecycle,
        environment=Environment.TESTNET,
        limits=LiveRiskLimits.for_profile("testnet"),
        states={"BTC/USDT": {"atr": 5.0, "recent_high": 100.0}},
    )
    assert [(d["action"], d["qty"]) for d in decisions] == [("SELL", quantity)]


# ── Full runs through the real runner ──────────────────────────────────────


def _authorize(tmp_path, **overrides):
    write_authorization(
        tmp_path / "operational_authorization.json", authorization(**overrides), key=KEY
    )


def _events(tmp_path, name):
    return [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip() and json.loads(line)["event"] == name
    ]


def _store(tmp_path):
    return LiveRiskStateStore(tmp_path / "risk.json", integrity_key=KEY)


def _fire_stop(broker):
    """The venue triggers the open protective stop and closes the position."""
    stop = next(o for o in broker.orders.values() if o["type"] == "stop" and o["status"] == "open")
    quantity = Decimal(str(stop["qty"]))
    price = Decimal(str(stop["stop_price"]))
    broker.quantity -= quantity
    broker.cash += quantity * price
    broker.price = price
    stop.update(status="filled", filled_qty=float(quantity), avg_fill_price=float(price))
    return stop


def test_unauthorized_testnet_keeps_entries_locked_and_says_why(tmp_path, monkeypatch):
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    assert runner.run(_testnet_args(tmp_path)) == 0
    assert broker.transactions == []
    (locked,) = _events(tmp_path, "entries_locked_no_promoted_runtime")
    assert "operational track not authorized" in locked["details"]["reason"]


def test_lifecycle_buy_protect_then_sell_after_the_hold(tmp_path, monkeypatch, capsys):
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    _authorize(tmp_path, tight_stop_every=0)

    assert runner.run(_testnet_args(tmp_path)) == 0
    (side, qty, price, _fee) = broker.transactions[0]
    assert side == "buy" and 5 <= qty * price <= 20  # capped by the authorization
    (filled,) = _events(tmp_path, "order_filled")
    assert filled["details"]["reason"] == "OPERATIONAL_EXERCISER"
    assert _store(tmp_path).protective_order_state("BTC/USDT")["active"]["status"] == "open"
    assert _events(tmp_path, "operational_track_active")[0]["details"]["may_open"] == ["BTC/USDT"]

    # Next cycle, before the hold has elapsed: hold, keep the stop.
    assert runner.run(_testnet_args(tmp_path)) == 0
    assert len(broker.transactions) == 1

    # After the hold: the track sells, cancelling its stop first.
    later = datetime.now(UTC) + timedelta(minutes=231)
    monkeypatch.setattr(
        runner,
        "load_operational_track",
        _shifted(runner.load_operational_track, later),
    )
    assert runner.run(_testnet_args(tmp_path)) == 0
    assert [t[0] for t in broker.transactions] == ["buy", "sell"]
    assert broker.quantity == 0
    store = _store(tmp_path)
    assert store.unfinished_orders() == {}
    assert not store.protective_order_state("BTC/USDT").get("active")
    assert daily_check.open_lifecycle_intents(tmp_path / "accounts") == []
    assert _events(tmp_path, "run_failed") == []
    assert "1/10 lifecycles closed" in _m3_line(tmp_path, capsys)
    assert "sell 1, venue stop 0" in _m3_line(tmp_path, capsys)


def _m3_line(tmp_path, capsys):
    from check_live_audit import _runs, load_events

    events = load_events(tmp_path / "audit.jsonl")
    runs = [r for r in _runs(sorted(events, key=lambda e: e["_timestamp"])) if r["execute"]]
    capsys.readouterr()
    assert daily_check.m3_progress(events, runs, now=datetime.now(UTC)) == []
    return capsys.readouterr().out


def _shifted(original, now):
    def at_later_time(**kwargs):
        return original(**{**kwargs, "now": now})

    return at_later_time


def test_tight_stop_lifecycle_is_closed_by_the_venue_and_booked(tmp_path, monkeypatch, capsys):
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    _authorize(tmp_path, tight_stop_every=1, max_lifecycles=1)

    assert runner.run(_testnet_args(tmp_path)) == 0  # buy + ATR stop
    first_stop = _store(tmp_path).protective_order_state("BTC/USDT")["active"]["stop_price"]

    assert runner.run(_testnet_args(tmp_path)) == 0  # hold: stop tightened near price
    tight = _store(tmp_path).protective_order_state("BTC/USDT")["active"]["stop_price"]
    assert tight > first_stop
    assert tight == pytest.approx(100 * (1 - 0.003))

    stop = _fire_stop(broker)
    assert runner.run(_testnet_args(tmp_path)) == 0  # flat; cap reached, no new buy
    assert len(broker.transactions) == 1
    (booked,) = _events(tmp_path, "protective_stop_filled")
    assert booked["details"]["client_order_id"] == stop["client_order_id"]
    assert daily_check.open_lifecycle_intents(tmp_path / "accounts") == []
    blocked = _events(tmp_path, "operational_track_active")[-1]["details"]["entries_blocked"]
    assert "cap reached" in blocked
    assert "sell 0, venue stop 1, other 0; opened 1" in _m3_line(tmp_path, capsys)


def _write_runs(tmp_path, *outcomes, torn=False):
    now = datetime.now(UTC)
    with (tmp_path / "audit.jsonl").open("w") as handle:
        for pid, outcome in enumerate(outcomes, start=10):
            at = (now - timedelta(minutes=len(outcomes) - pid + 10)).isoformat()
            handle.write(json.dumps(
                {"event": "run_started", "pid": pid, "timestamp": at, "details": {"execute": True}}
            ) + "\n")
            handle.write(json.dumps(
                {"event": outcome, "pid": pid, "timestamp": at, "details": {}}
            ) + "\n")
        if torn:
            handle.write('{"event": "run_sta')


@pytest.mark.parametrize(
    ("outcomes", "torn", "expected"),
    [
        (("run_failed",), False, "run_failed"),
        # A later clean, entry-blocked run does not clear the block.
        (("run_failed", "run_completed"), False, "run_failed"),
        (("run_interrupted", "run_completed", "run_completed"), False, "run_interrupted"),
        # Without a readable audit log nothing proves earlier runs were clean.
        (("run_completed",), True, "audit log unreadable"),
    ],
)
def test_a_failed_execution_run_blocks_new_entries_until_reauthorized(
    tmp_path, monkeypatch, outcomes, torn, expected
):
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    _authorize(tmp_path)
    _write_runs(tmp_path, *outcomes, torn=torn)
    blocked = runner._execution_failure_since(
        str(tmp_path / "audit.jsonl"), datetime.now(UTC) - timedelta(hours=1)
    )
    assert expected in blocked
    if torn:
        return
    assert runner.run(_testnet_args(tmp_path)) == 0
    assert broker.transactions == []
    details = _events(tmp_path, "operational_track_active")[0]["details"]
    assert expected in details["entries_blocked"]


def test_clean_earlier_runs_do_not_block(tmp_path):
    _write_runs(tmp_path, "run_completed", "run_completed")
    since = datetime.now(UTC) - timedelta(hours=1)
    assert runner._execution_failure_since(str(tmp_path / "audit.jsonl"), since) is None
    assert "no audit log" in runner._execution_failure_since(None, since)


def test_every_default_runner_symbol_has_instrument_rules():
    from trading_agent.execution.canonical.instrument_registry import get_instrument_rules

    from trading_agent.execution.canonical.instrument_registry import INSTRUMENT_RULES_1H

    defaults = runner.build_parser().parse_args([]).symbols.split(",")
    assert defaults == ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]
    for symbol in defaults:
        assert get_instrument_rules(symbol).qty_step > 0
    # The live-only extra stays out of the reviewed research universe.
    assert "AVAX/USDT" not in INSTRUMENT_RULES_1H
    assert get_instrument_rules("AVAX/USDT").qty_step == 0.01


# ── Operator CLI ────────────────────────────────────────────────────────────


def test_cli_signs_shows_and_revokes(tmp_path, monkeypatch, capsys):
    import authorize_operational_track as cli

    path = tmp_path / "operational_authorization.json"
    monkeypatch.setenv("LIVE_OPERATIONAL_AUTHORIZATION", str(path))
    monkeypatch.setenv("LIVE_SAFETY_HMAC_KEY", KEY)
    monkeypatch.setenv("LIVE_ACCOUNT_ID", "local-fixture-venue-uid")
    monkeypatch.chdir(tmp_path)  # the CLI changes directory; restore it afterwards
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    assert cli.main(["--show"]) == 1
    assert cli.main(["--approver", "op", "--reason", "M3"]) == 1  # not confirmed
    assert not path.exists()
    assert cli.main(["--approver", "op", "--reason", "M3", "--max-order-usd", "30", "--yes"]) == 1
    assert cli.main(["--approver", "op", "--reason", "M3", "--yes"]) == 0
    signed = load(path)
    assert signed.symbols == ("BTC/USDT", "SOL/USDT", "AVAX/USDT")
    assert signed.max_order_usd == 20.0 and signed.max_open_positions == 3
    assert cli.main(["--show"]) == 0
    assert cli.main(["--revoke"]) == 0
    assert not path.exists() and list(tmp_path.glob("operational_authorization.json.revoked-*"))
    assert "fixture-venue-uid" not in capsys.readouterr().out
