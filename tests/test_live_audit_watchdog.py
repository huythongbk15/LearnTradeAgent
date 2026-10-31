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
        "position_protection_failed",
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


def run(name, seconds_ago, *, pid=100, execute=None):
    item = event(name, seconds_ago)
    item["pid"] = pid
    if execute is not None:
        item["details"] = {"execute": execute}
    return item


def check_live(events, *, alive=lambda pid: True):
    return validate_audit_health(
        events, now=NOW, max_age_seconds=9_900, lookback_seconds=9_900,
        max_run_seconds=2_700, process_alive=alive,
    )


def test_dry_runs_do_not_make_a_dead_scheduler_look_fresh():
    events = [
        run("run_started", 20_000, pid=1, execute=True),
        run("run_completed", 19_000, pid=1),
        run("run_started", 60, pid=2, execute=False),
        run("run_completed", 30, pid=2),
    ]
    with pytest.raises(AuditHealthError, match="latest execution run is stale"):
        check_live(events)


def test_dry_run_failure_is_not_critical_but_execution_failure_is():
    healthy = [
        run("run_started", 600, pid=1, execute=True),
        run("run_completed", 100, pid=1),
        run("run_started", 60, pid=2, execute=False),
        run("run_failed", 50, pid=2),
    ]
    assert check_live(healthy)["latest_execution_run_pid"] == 1
    failing = healthy + [run("run_started", 40, pid=3, execute=True), run("run_failed", 30, pid=3)]
    with pytest.raises(AuditHealthError, match="critical live audit events: run_failed"):
        check_live(failing)


def test_terminal_events_pair_by_pid_when_runs_interleave():
    events = [
        run("run_started", 900, pid=1, execute=True),
        run("run_started", 800, pid=2, execute=False),
        run("run_completed", 700, pid=2),  # the dry run ends; execution 1 is still open
    ]
    with pytest.raises(AuditHealthError, match="pid 1 ended without a terminal event"):
        check_live(events, alive=lambda pid: False)
    assert check_live(events, alive=lambda pid: True)["latest_execution_run_pid"] == 1


def test_killed_run_is_reported_before_the_run_timeout():
    events = [run("run_started", 120, pid=7, execute=True), run("order_filled", 60, pid=7)]
    with pytest.raises(AuditHealthError, match="ended without a terminal event"):
        check_live(events, alive=lambda pid: False)


def test_a_long_execution_run_within_the_default_budget_is_healthy():
    # 2026-10-29: two orders took 845 s, which tripped the old 900 s default for three.
    events = [run("run_started", 1_500, pid=5, execute=True), run("order_filled", 30, pid=5)]
    assert check_live(events)["latest_execution_run_pid"] == 5


def test_unknowable_liveness_falls_back_to_the_run_timeout():
    events = [run("run_started", 3_000, pid=5, execute=True)]
    with pytest.raises(AuditHealthError, match="no terminal heartbeat"):
        check_live(events, alive=lambda pid: None)


def test_interrupted_execution_run_is_terminal_and_critical():
    events = [run("run_started", 300, pid=8, execute=True), run("run_interrupted", 200, pid=8)]
    with pytest.raises(AuditHealthError, match="critical live audit events: run_interrupted"):
        check_live(events, alive=lambda pid: False)


def test_interrupted_dry_run_is_not_critical():
    events = [
        run("run_started", 900, pid=1, execute=True), run("run_completed", 800, pid=1),
        run("run_started", 300, pid=9, execute=False), run("run_interrupted", 200, pid=9),
    ]
    assert check_live(events)["latest_execution_run_pid"] == 1
