"""The scheduled daily check: full report to a file, one line for Windows."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "ops"))
import daily_check  # noqa: E402

M1 = {"clean": 8, "state": "on track"}


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
