"""Tests for the independent audit pager (P1.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts.alert_pager import (
    CRITICAL_EVENTS,
    PagerError,
    load_events,
    validate,
)

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


def _event(name: str, when: datetime, **details) -> dict[str, object]:
    return {
        "timestamp": when.isoformat(),
        "event": name,
        "pid": 123,
        "details": details,
    }


def _write_events(path, events) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def test_healthy_audit_passes(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_events(
        path,
        [
            _event("run_started", NOW - timedelta(minutes=50)),
            _event("order_acknowledged", NOW - timedelta(minutes=49)),
            _event("run_completed", NOW - timedelta(minutes=45)),
            _event("run_started", NOW - timedelta(minutes=5)),
            _event("run_completed", NOW - timedelta(minutes=1)),
        ],
    )
    events = load_events(path)
    result = validate(
        events,
        now=NOW,
        max_age_seconds=4_500,
        lookback_seconds=4_500,
        max_run_seconds=900,
    )
    assert result["status"] == "ok"
    assert result["latest_event"] == "run_completed"


def test_stale_audit_raises(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_events(
        path,
        [_event("run_completed", NOW - timedelta(hours=2))],
    )
    with pytest.raises(PagerError, match="stale"):
        validate(
            load_events(path),
            now=NOW,
            max_age_seconds=4_500,
            lookback_seconds=4_500,
            max_run_seconds=900,
        )


def test_critical_event_raises(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_events(
        path,
        [
            _event("run_started", NOW - timedelta(minutes=10)),
            _event("run_completed", NOW - timedelta(minutes=9)),
            _event("run_started", NOW - timedelta(minutes=5)),
            _event("order_submission_unknown", NOW - timedelta(minutes=2)),
            _event("run_completed", NOW - timedelta(minutes=1)),
        ],
    )
    with pytest.raises(PagerError, match="order_submission_unknown"):
        validate(
            load_events(path),
            now=NOW,
            max_age_seconds=4_500,
            lookback_seconds=4_500,
            max_run_seconds=900,
        )


def test_critical_outside_lookback_ignored(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_events(
        path,
        [
            _event("run_started", NOW - timedelta(hours=3)),
            _event("reconciliation_blocked", NOW - timedelta(hours=2, minutes=59)),
            _event("run_completed", NOW - timedelta(hours=2, minutes=58)),
            _event("run_started", NOW - timedelta(minutes=5)),
            _event("run_completed", NOW - timedelta(minutes=1)),
        ],
    )
    result = validate(
        load_events(path),
        now=NOW,
        max_age_seconds=4_500,
        lookback_seconds=3_600,
        max_run_seconds=900,
    )
    assert result["status"] == "ok"


def test_open_run_heartbeat_timeout(tmp_path):
    path = tmp_path / "audit.jsonl"
    _write_events(
        path,
        [_event("run_started", NOW - timedelta(minutes=30))],
    )
    with pytest.raises(PagerError, match="no terminal heartbeat"):
        validate(
            load_events(path),
            now=NOW,
            max_age_seconds=4_500,
            lookback_seconds=4_500,
            max_run_seconds=900,
        )


def test_missing_audit_log_raises(tmp_path):
    with pytest.raises(PagerError, match="does not exist"):
        load_events(tmp_path / "nope.jsonl")


def test_corrupt_line_raises(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(PagerError, match="cannot read"):
        load_events(path)


def test_critical_events_cover_key_operational_risks():
    for event in (
        "order_submission_unknown",
        "order_non_terminal",
        "reconciliation_blocked",
        "run_failed",
        "position_protection_failed",
        "risk_limits_change_blocked",
    ):
        assert event in CRITICAL_EVENTS