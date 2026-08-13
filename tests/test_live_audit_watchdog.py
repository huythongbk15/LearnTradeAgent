import os
import sys
from datetime import UTC, datetime, timedelta

import pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from check_live_audit import AuditHealthError, validate_audit_health


NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def event(name: str, seconds_ago: float) -> dict[str, object]:
    return {"event": name, "_timestamp": NOW - timedelta(seconds=seconds_ago)}


def check(events: list[dict[str, object]]) -> dict[str, object]:
    return validate_audit_health(
        events,
        now=NOW,
        max_age_seconds=4_500,
        lookback_seconds=4_500,
        max_run_seconds=900,
    )


def test_recent_completed_run_is_healthy():
    result = check([event("run_started", 120), event("run_completed", 60)])
    assert result["latest_event"] == "run_completed"
    assert result["age_seconds"] == pytest.approx(60)


@pytest.mark.parametrize(
    "name",
    [
        "order_submission_unknown",
        "order_non_terminal",
        "reconciliation_blocked",
        "run_failed",
    ],
)
def test_recent_critical_event_fails(name):
    with pytest.raises(AuditHealthError, match="critical live audit events"):
        check([event(name, 30)])


def test_stale_heartbeat_fails():
    with pytest.raises(AuditHealthError, match="stale"):
        check([event("run_completed", 5_000)])


def test_unfinished_run_fails_after_timeout():
    with pytest.raises(AuditHealthError, match="no terminal heartbeat"):
        check([event("run_started", 1_000), event("order_filled", 10)])
