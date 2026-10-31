"""The scheduled daily check: full report to a file, one line for Windows."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import daily_check  # noqa: E402
from check_live_audit import critical_events, load_events  # noqa: E402

M1 = {"clean": 8, "state": "on track"}
M1_START = datetime(2026, 10, 30, 15, 1, tzinfo=UTC)


def test_brief_keeps_m1_and_m3_inside_the_notification():
    issues = ["ledger: unfinished " + "x" * 300, "cycle 17:01Z: died, no end line"]
    line = daily_check.brief(issues, M1, {"state": "not authorized"})
    assert line.startswith("2 issue(s): ledger: unfinished x")
    assert line.endswith("| M1 8/24 on track | M3 not authorized")
    assert len(line) < 200 and line.isascii()
    signed = {"state": "in progress", "closed": 3, "required": 10}
    assert daily_check.brief([], M1, signed) == "No issues | M1 8/24 on track | M3 3/10 in progress"


def test_report_goes_to_the_file_and_one_line_to_stdout(tmp_path, monkeypatch, capsys):
    started = datetime.now(UTC) - timedelta(minutes=30)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        "\n".join(
            json.dumps({"event": event, "correlation_id": "c1", "pid": 1,
                        "timestamp": (started + timedelta(seconds=offset)).isoformat(),
                        "details": details})
            for event, offset, details in (
                ("run_started", 0, {"execute": True, "testnet": True}),
                ("run_completed", 20, {"exit_code": 0}),
            )
        )
        + "\n"
    )
    cycles = tmp_path / "cycles.log"
    cycles.write_text(
        f"=== {started:%Y-%m-%dT%H:%M:%SZ} cycle start pid=1\n"
        f"=== {started + timedelta(seconds=21):%Y-%m-%dT%H:%M:%SZ} cycle end rc=0\n"
    )
    monkeypatch.chdir(tmp_path)  # main() moves to the repo root; restored after
    monkeypatch.setenv("LIVE_SAFETY_HMAC_KEY", "k" * 32)
    monkeypatch.setenv("LIVE_OPERATIONAL_AUTHORIZATION", str(tmp_path / "none.json"))
    report = tmp_path / "reports" / "today.txt"
    monkeypatch.setattr(sys, "argv", [
        "daily_check.py", "--m1-start", started.isoformat(),
        "--audit-log", str(audit), "--cycle-log", str(cycles),
        "--state-file", str(tmp_path / "state.json"), "--report", str(report),
    ])
    code = daily_check.main()
    out = capsys.readouterr().out
    text = report.read_text()
    assert text.startswith("DAILY CHECK") and "\nM1       1/24 clean cycles" in text
    assert out.count("\n") == 1 and out.rstrip().endswith(f"| {report}")
    assert ("ISSUES" in text) == (code == 1) == (not out.startswith("No issues"))
    assert "M3 not authorized" in out


def scheduled(count: int) -> list[datetime]:
    return [M1_START + daily_check.CADENCE * index for index in range(count)]


def cycle_at(start: datetime) -> dict:
    return {"start": start, "end": start + timedelta(seconds=20), "result": "rc=0"}


def run_at(start: datetime, *, delay: float = 5.0) -> dict:
    begun = start + timedelta(seconds=delay)
    return {"pid": 1, "execute": True, "started": begun,
            "terminal": {"event": "run_completed", "_timestamp": begun + timedelta(seconds=20)}}


def m1_of(starts: list[datetime], now: datetime, *, critical: tuple = ()) -> dict:
    return daily_check.m1_status(
        [run_at(start) for start in starts], [cycle_at(start) for start in starts],
        m1_start=M1_START, now=now, critical=critical,
    )


def test_m1_passes_on_a_clean_unbroken_schedule():
    starts = scheduled(25)
    m1 = m1_of(starts, starts[-1] + timedelta(minutes=5))
    assert (m1["state"], m1["clean"], m1["violations"]) == ("PASSED", 25, [])


def test_a_gap_between_execution_starts_fails_m1():
    starts = scheduled(26)
    del starts[5]
    m1 = m1_of(starts, starts[-1] + timedelta(minutes=5))
    assert m1["clean"] == 25 and m1["state"].startswith("RESET NEEDED")
    assert m1["earliest_pass"] is None
    assert m1["violations"] == ["no execution start between 10-30 23:01Z and 10-31 03:01Z"]


def test_a_gap_at_the_limit_is_allowed():
    starts = scheduled(2)
    now = starts[-1] + daily_check.GAP_LIMIT
    assert m1_of(starts, now)["violations"] == []


def test_silence_fails_m1_before_the_next_run_starts():
    starts = scheduled(25)
    m1 = m1_of(starts, starts[-1] + daily_check.GAP_LIMIT + timedelta(minutes=1))
    assert m1["clean"] == 25 and m1["state"].startswith("RESET NEEDED")


def test_a_critical_event_since_the_start_fails_m1():
    starts = scheduled(25)
    m1 = m1_of(starts, starts[-1], critical=("position_protection_failed",))
    assert m1["state"].startswith("RESET NEEDED")
    assert m1["violations"] == ["critical event: position_protection_failed"]


def test_an_execution_run_no_cycle_started_fails_m1():
    starts = scheduled(25)
    manual = run_at(starts[3] + timedelta(minutes=30))
    m1 = daily_check.m1_status(
        sorted([run_at(start) for start in starts] + [manual], key=lambda r: r["started"]),
        [cycle_at(start) for start in starts], m1_start=M1_START, now=starts[-1],
    )
    assert m1["state"].startswith("RESET NEEDED")
    assert m1["violations"] == [f"execution run at {manual['started']:%m-%d %H:%M}Z has no scheduled cycle"]


def test_a_slow_start_still_belongs_to_its_cycle():
    starts = scheduled(2)
    limit = daily_check.RUN_START_LIMIT.total_seconds()
    runs = [run_at(starts[0]), run_at(starts[1], delay=limit)]
    m1 = daily_check.m1_status(runs, [cycle_at(start) for start in starts],
                               m1_start=M1_START, now=starts[1])
    assert m1["violations"] == []


def write_audit(path, rows) -> None:
    path.write_text("".join(
        json.dumps({"event": event, "correlation_id": str(pid), "pid": pid,
                    "timestamp": moment.isoformat(), "details": details}) + "\n"
        for event, pid, moment, details in rows
    ))


def test_only_critical_events_from_the_m1_start_count(tmp_path):
    now = M1_START + timedelta(hours=1)
    rows = [("run_failed", 1, M1_START - timedelta(minutes=1), {}),
            ("position_protection_failed", 2, M1_START + timedelta(minutes=1), {})]
    audit = tmp_path / "audit.jsonl"
    write_audit(audit, rows)
    ordered = sorted(load_events(audit), key=lambda e: e["_timestamp"])
    assert critical_events(ordered, since=M1_START, until=now) == ["position_protection_failed"]


def test_the_m1_check_reads_cycles_older_than_the_report_window(tmp_path, monkeypatch, capsys):
    first = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=30)
    starts = [first + daily_check.CADENCE * index for index in range(15)]
    audit, cycles = tmp_path / "audit.jsonl", tmp_path / "cycles.log"
    write_audit(audit, [
        row for pid, start in enumerate(starts, 1) for row in (
            ("run_started", pid, start + timedelta(seconds=2), {"execute": True, "testnet": True}),
            ("run_completed", pid, start + timedelta(seconds=20), {"exit_code": 0}),
        )
    ])
    cycles.write_text("".join(
        f"=== {start:%Y-%m-%dT%H:%M:%SZ} cycle start pid={pid}\n"
        f"=== {start + timedelta(seconds=21):%Y-%m-%dT%H:%M:%SZ} cycle end rc=0\n"
        for pid, start in enumerate(starts, 1)
    ))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIVE_SAFETY_HMAC_KEY", "k" * 32)
    monkeypatch.setenv("LIVE_OPERATIONAL_AUTHORIZATION", str(tmp_path / "none.json"))
    report = tmp_path / "today.txt"
    monkeypatch.setattr(sys, "argv", [
        "daily_check.py", "--m1-start", first.isoformat(), "--audit-log", str(audit),
        "--cycle-log", str(cycles), "--state-file", str(tmp_path / "state.json"),
        "--report", str(report),
    ])
    daily_check.main()
    text = report.read_text()
    assert "\nM1       15/24 clean cycles" in text
    assert "M1:" not in text and "on track" in text
