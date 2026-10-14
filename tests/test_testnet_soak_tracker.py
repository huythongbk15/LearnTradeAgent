"""Tests for the testnet soak tracker (P3 release gates)."""

from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
import pytest

from scripts.testnet_soak_tracker import (
    build_report,
    count_critical,
    count_lifecycles,
    evaluate_gates,
    load_events,
    tracking_days,
)

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)
SUBJECT = {
    "environment": "testnet",
    "execute": True,
    "account_fingerprint": hashlib.sha256(
        b"local-fixture-account-not-live"
    ).hexdigest(),
    "build_sha": hashlib.sha256(b"local-fixture-source-not-live").hexdigest(),
    "policy_sha": hashlib.sha256(b"local-fixture-policy-not-live").hexdigest(),
}


def _event(name: str, when: datetime, **details) -> dict:
    defaults = {}
    if name == "run_started":
        defaults = {"execute": True, "testnet": True}
    elif name == "run_completed":
        defaults = {
            "exit_code": 0,
            "health": {
                "inventory_protected": True,
                "unresolved_orders": 0,
                "ledger_drift": 0,
                "reconciliation": "resolved",
            },
        }
    return {
        "timestamp": when.isoformat(),
        "event": name,
        "details": defaults | details,
        "correlation_id": "run-" + when.date().isoformat(),
        "subject": dict(SUBJECT),
    }


def _valid_events(days=30, entries=120):
    """Schema fixtures only; never represent an actual testnet soak."""
    events = []
    for day in range(days):
        end = NOW - timedelta(days=days - 1 - day)
        events.extend(
            [
                _event("run_started", end - timedelta(hours=1)),
                _event("run_completed", end),
            ]
        )
    for index in range(entries):
        key = f"entry-{index}"
        events.extend(
            [
                _event(
                    "order_filled",
                    NOW - timedelta(minutes=2),
                    symbol="BTC/USDT",
                    side="BUY",
                    order_key=key,
                    filled_qty=1.0,
                    exchange_order_id=f"fill-{index}",
                ),
                _event(
                    "protective_stop_placed",
                    NOW - timedelta(minutes=1),
                    symbol="BTC/USDT",
                    protected_order_key=key,
                    quantity=1.0,
                    status="open",
                    stop_price=90.0,
                    exchange_order_id=f"stop-{index}",
                ),
            ]
        )
    return events


def _write(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def test_tracking_days_continuous_and_gap():
    events = [
        _event("run_completed", NOW - timedelta(days=10)),
        _event("run_completed", NOW - timedelta(days=9)),
        _event("run_completed", NOW - timedelta(days=1)),  # big gap
        _event("run_completed", NOW),
    ]
    # gap of 8 days resets the streak in both cases; the -1d -> now pair counts 2
    assert tracking_days(events, max_gap_hours=36) == 2
    assert tracking_days(events, max_gap_hours=72) == 2


def test_count_lifecycles_pairs_fill_and_stop():
    events = _valid_events(days=1, entries=2)
    events.append(_event("order_filled", NOW, symbol="SOL/USDT", side="SELL"))
    complete, parts = count_lifecycles(events)
    assert parts["fills"] == parts["stops"] == complete == 2


def test_count_critical_with_lookback():
    events = [
        _event("order_submission_unknown", NOW - timedelta(days=40)),
        _event("reconciliation_blocked", NOW - timedelta(days=1)),
        _event("order_non_terminal", NOW),
    ]
    assert count_critical(events)["order_submission_unknown"] == 1
    within = count_critical(events, lookback_days=7, now=NOW)
    assert within == {"reconciliation_blocked": 1, "order_non_terminal": 1}


def test_gates_evaluate():
    report = build_report(
        _valid_events(), max_gap_hours=36, expected_subject=SUBJECT, now=NOW
    )
    gates = evaluate_gates(report, min_days=30, min_lifecycles=100)
    assert gates["continuous_30_days"] is True
    assert gates["100_complete_lifecycles"] is True
    assert gates["zero_unexplained_events"] is True
    assert gates["stop_coverage_100pct"] is True
    assert gates["evidence_eligible"] is True


def test_gates_fail_when_evidence_missing():
    events = [_event("run_completed", NOW)]
    report = build_report(events, max_gap_hours=36)
    gates = evaluate_gates(report, min_days=30, min_lifecycles=100)
    assert gates["continuous_30_days"] is False
    assert gates["100_complete_lifecycles"] is False


def test_load_events_missing_file():
    import pytest

    with pytest.raises(SystemExit):
        load_events(Path("/nonexistent/audit.jsonl"))


def test_load_events_roundtrip(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write(path, [_event("run_started", NOW), _event("run_completed", NOW)])
    events = load_events(path)
    assert len(events) == 2
    assert events[1]["event"] == "run_completed"


def test_failed_only_runs_can_never_close_soak_gates():
    events = _valid_events()
    for event in events:
        if event["event"] == "run_completed":
            event["event"] = "run_failed"
            event["details"] = {"error_type": "LocalFixtureFailure"}
    report = build_report(events, max_gap_hours=36, expected_subject=SUBJECT, now=NOW)
    assert report["run_outcomes"]["failed"] == 30
    assert report["tracking"]["days_continuous"] == 0
    assert not any(evaluate_gates(report, min_days=30, min_lifecycles=100).values())


def test_failed_and_critical_events_reset_the_streak():
    events = [
        _event("run_completed", NOW - timedelta(days=2)),
        _event("run_failed", NOW - timedelta(days=1)),
        _event("run_completed", NOW),
    ]
    assert tracking_days(events, max_gap_hours=36) == 1
    events[1]["event"] = "position_protection_failed"
    assert tracking_days(events, max_gap_hours=36) == 1


def test_exact_duplicate_audit_events_do_not_inflate_lifecycles_or_runs():
    events = _valid_events(entries=1)
    report = build_report(
        events * 120, max_gap_hours=36, expected_subject=SUBJECT, now=NOW
    )
    assert report["run_outcomes"]["completed"] == 30
    assert report["order_lifecycles"]["complete"] == 1
    assert report["evidence"]["duplicate_events_ignored"] > 0
    assert not evaluate_gates(report, min_days=30, min_lifecycles=100)[
        "100_complete_lifecycles"
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "unrelated_stop",
        "too_small",
        "stop_before_fill",
        "unconfirmed_stop",
        "reused_stop_id",
        "conflicting_fill",
        "reused_fill_id",
        "other_run",
    ],
)
def test_symbol_totals_cannot_prove_inventory_coverage(fault):
    events = _valid_events(days=1, entries=2)
    stops = [event for event in events if event["event"] == "protective_stop_placed"]
    if fault == "unrelated_stop":
        stops[0]["details"]["protected_order_key"] = "another-entry"
    elif fault == "too_small":
        stops[0]["details"]["quantity"] = 0.5
    elif fault == "stop_before_fill":
        stops[0]["timestamp"] = (NOW - timedelta(minutes=3)).isoformat()
    elif fault == "unconfirmed_stop":
        stops[0]["details"]["status"] = "unknown"
    elif fault == "reused_stop_id":
        stops[1]["details"]["exchange_order_id"] = stops[0]["details"][
            "exchange_order_id"
        ]
    elif fault == "reused_fill_id":
        fills = [event for event in events if event["event"] == "order_filled"]
        fills[1]["details"]["exchange_order_id"] = fills[0]["details"][
            "exchange_order_id"
        ]
    elif fault == "other_run":
        stops[0]["correlation_id"] = "another-run"
    else:
        extra = deepcopy(
            next(event for event in events if event["event"] == "order_filled")
        )
        extra["details"]["filled_qty"] = 2.0
        events.append(extra)
    report = build_report(events, max_gap_hours=36, expected_subject=SUBJECT, now=NOW)
    assert report["order_lifecycles"]["complete"] < 2
    assert not evaluate_gates(report, min_days=1, min_lifecycles=1)[
        "stop_coverage_100pct"
    ]


@pytest.mark.parametrize(
    "fault",
    [
        "dry_run",
        "mainnet",
        "wrong_account",
        "wrong_build",
        "wrong_policy",
        "missing_subject",
        "missing_start",
        "missing_health",
        "unresolved",
        "unprotected",
        "drift",
        "future",
        "naive",
        "stale",
        "conflicting_terminal",
    ],
)
def test_invalid_or_unattributed_evidence_blocks_every_gate(fault):
    events = _valid_events()
    terminal = next(event for event in events if event["event"] == "run_completed")
    if fault in {"mainnet", "wrong_account", "wrong_build", "wrong_policy"}:
        key = {
            "mainnet": "environment",
            "wrong_account": "account_fingerprint",
            "wrong_build": "build_sha",
            "wrong_policy": "policy_sha",
        }[fault]
        terminal["subject"][key] = "wrong"
    elif fault == "dry_run":
        next(event for event in events if event["event"] == "run_started")["details"][
            "execute"
        ] = False
    elif fault == "missing_subject":
        terminal.pop("subject")
    elif fault == "missing_start":
        events = [event for event in events if event["event"] != "run_started"]
    elif fault == "missing_health":
        terminal["details"].pop("health")
    elif fault in {"unresolved", "unprotected", "drift"}:
        key, value = {
            "unresolved": ("unresolved_orders", 1),
            "unprotected": ("inventory_protected", False),
            "drift": ("ledger_drift", 0.1),
        }[fault]
        terminal["details"]["health"][key] = value
    elif fault == "future":
        terminal["timestamp"] = (NOW + timedelta(hours=1)).isoformat()
    elif fault == "naive":
        terminal["timestamp"] = NOW.replace(tzinfo=None).isoformat()
    elif fault == "stale":
        for event in events:
            event["timestamp"] = (
                datetime.fromisoformat(event["timestamp"]) - timedelta(days=3)
            ).isoformat()
    else:
        conflict = deepcopy(terminal)
        conflict["timestamp"] = (
            datetime.fromisoformat(conflict["timestamp"]) + timedelta(seconds=1)
        ).isoformat()
        events.append(conflict)
    report = build_report(events, max_gap_hours=36, expected_subject=SUBJECT, now=NOW)
    assert not report["evidence"]["eligible"]
    assert not any(evaluate_gates(report, min_days=30, min_lifecycles=100).values())


def test_legacy_report_and_missing_expected_subject_are_not_certificates():
    report = build_report(_valid_events(), max_gap_hours=36, now=NOW)
    assert not any(evaluate_gates(report, min_days=30, min_lifecycles=100).values())
    report["evidence"]["eligible"] = True
    report.pop("schema_version")
    assert not any(evaluate_gates(report, min_days=30, min_lifecycles=100).values())


def test_load_events_rejects_non_object_lines(tmp_path):
    path = tmp_path / "invalid.jsonl"
    path.write_text("[1,2]\n")
    with pytest.raises(SystemExit, match="non-object"):
        load_events(path)


@pytest.mark.parametrize("gap", [0, -1, float("inf"), float("nan")])
def test_invalid_gap_cannot_create_a_green_report(gap):
    with pytest.raises(ValueError):
        build_report([], max_gap_hours=gap)


def test_gap_contract_cannot_be_relaxed_into_a_green_report():
    report = build_report(
        _valid_events(), max_gap_hours=72, expected_subject=SUBJECT, now=NOW
    )
    assert not any(evaluate_gates(report, min_days=30, min_lifecycles=100).values())


@pytest.mark.parametrize(
    "extra", [["--min-days", "1"], ["--min-lifecycles", "1"], ["--max-gap-hours", "72"]]
)
def test_cli_check_cannot_lower_release_contract(tmp_path, monkeypatch, extra):
    import scripts.testnet_soak_tracker as tracker

    path = tmp_path / "audit.jsonl"
    _write(path, _valid_events())
    monkeypatch.setattr(
        "sys.argv", ["tracker", "--audit-log", str(path), "--check"] + extra
    )
    assert tracker.main() == 2


def test_cli_legacy_failed_only_log_is_diagnostic_not_a_green_check(
    tmp_path, monkeypatch
):
    import scripts.testnet_soak_tracker as tracker

    path, out = tmp_path / "audit.jsonl", tmp_path / "report.json"
    events = [
        {
            "timestamp": NOW.isoformat(),
            "event": "run_failed",
            "details": {"error": "fixture"},
        }
    ]
    _write(path, events)
    argv = ["tracker", "--audit-log", str(path), "--report", str(out)]
    monkeypatch.setattr("sys.argv", argv + ["--check"])
    assert tracker.main() == 1
    report = json.loads(out.read_text())
    assert not report["evidence"]["eligible"]
    assert not any(report["gates"].values())
    monkeypatch.setattr("sys.argv", argv)
    assert tracker.main() == 0  # Writing a diagnostic report is not gate PASS.
