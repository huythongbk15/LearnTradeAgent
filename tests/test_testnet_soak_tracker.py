"""Tests for the testnet soak tracker (P3 release gates)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.testnet_soak_tracker import (
    build_report,
    count_critical,
    count_lifecycles,
    evaluate_gates,
    load_events,
    tracking_days,
)

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


def _event(name: str, when: datetime, **details) -> dict:
    return {"timestamp": when.isoformat(), "event": name, "details": details}


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
    events = [
        _event("order_filled", NOW, symbol="BTC/USDT", side="BUY"),
        _event("protective_stop_placed", NOW, symbol="BTC/USDT"),
        _event("order_filled", NOW, symbol="SOL/USDT", side="SELL"),
        _event("order_filled", NOW, symbol="SOL/USDT", side="BUY"),
        _event("protective_stop_placed", NOW, symbol="SOL/USDT"),
        _event("protective_stop_placed", NOW, symbol="SOL/USDT"),
    ]
    complete, parts = count_lifecycles(events)
    assert parts["fills"] == 3
    assert parts["stops"] == 3
    assert complete == 3  # min(fills, stops) per symbol: BTC 1 + SOL 2


def test_count_critical_with_lookback():
    events = [
        _event("order_submission_unknown", NOW - timedelta(days=40)),
        _event("reconciliation_blocked", NOW - timedelta(days=1)),
        _event("order_non_terminal", NOW),
    ]
    assert count_critical(events)["order_submission_unknown"] == 1
    within = count_critical(events, lookback_days=7)
    assert within == {"reconciliation_blocked": 1, "order_non_terminal": 1}


def test_gates_evaluate():
    events = []
    for day in range(30):
        events.append(_event("run_completed", NOW - timedelta(days=29 - day, hours=0)))
    for symbol in ["BTC/USDT", "SOL/USDT"]:
        for _ in range(60):
            events.append(_event("order_filled", NOW, symbol=symbol))
            events.append(_event("protective_stop_placed", NOW, symbol=symbol))
    report = build_report(events, max_gap_hours=36)
    gates = evaluate_gates(report, min_days=30, min_lifecycles=100)
    assert gates["continuous_30_days"] is True
    assert gates["100_complete_lifecycles"] is True
    assert gates["zero_unexplained_events"] is True
    assert gates["stop_coverage_100pct"] is True


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