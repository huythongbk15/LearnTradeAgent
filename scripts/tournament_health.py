#!/usr/bin/env python3
"""Health monitor for the tournament baseline run.

Detects every silent-failure mode we have hit so far:

1. Dead process            → PID not running
2. Crash-loop              → EXCEPTION lines appearing in the run log
3. Stuck cell (stall)      → newest report.json older than --stall-minutes
4. No progress             → COMPLETED count unchanged since last check
5. Silent FAILED artifacts → cells recorded FAILED in the index

Exit code 0 = healthy, 1 = something is wrong (cron-friendly).
State for the no-progress check lives in <out>/logs/.health_state.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pid_alive(pid: int) -> bool:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "--no-headers"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument(
        "--out", type=Path, default=ROOT / "data" / "backtests" / "tournament"
    )
    parser.add_argument("--total-cells", type=int, default=150)
    parser.add_argument(
        "--stall-minutes",
        type=float,
        default=30.0,
        help="alert when the newest report.json is older than this",
    )
    args = parser.parse_args()

    problems: list[str] = []
    notes: list[str] = []

    # ── 1. Process ──────────────────────────────────────────────────────
    if args.pid:
        if pid_alive(args.pid):
            notes.append(f"process {args.pid}: ALIVE")
        else:
            problems.append(f"process {args.pid}: DEAD")

    # ── 2. Progress from the content-addressed index ────────────────────
    index_path = args.out / "tournament_index.json"
    completed = failed_cells = 0
    if index_path.exists():
        cells = json.loads(index_path.read_text()).get("cells", {})
        statuses = [c.get("status") for c in cells.values()]
        completed = statuses.count("COMPLETED")
        failed_cells = statuses.count("FAILED")
        notes.append(f"progress: {completed}/{args.total_cells} COMPLETED")
        if failed_cells:
            problems.append(f"{failed_cells} cells FAILED in index")
    elif any(args.out.glob("*/report.json")):
        problems.append(
            f"reports exist but no index at {index_path} — save_index broken?"
        )
    else:
        notes.append("index not created yet (first cell still running)")

    # No-progress detection vs previous run of this script.
    logs_dir = args.out / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    state_path = logs_dir / ".health_state.json"
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text())
    prev_completed = state.get("completed")
    prev_ts = state.get("ts", 0)
    hours_since_check = (time.time() - prev_ts) / 3600 if prev_ts else 0
    if (
        prev_completed is not None
        and completed == prev_completed
        and hours_since_check >= 0.5
        and completed < args.total_cells
    ):
        problems.append(
            f"NO PROGRESS: still {completed} COMPLETED after "
            f"{hours_since_check:.1f}h since previous check"
        )
    state_path.write_text(json.dumps({"completed": completed, "ts": time.time()}))

    # ── 3. Stall: newest report.json on disk ────────────────────────────
    reports = sorted(
        args.out.glob("*/report.json"), key=lambda p: p.stat().st_mtime
    )
    if reports:
        newest = reports[0]
        age_min = (time.time() - newest.stat().st_mtime) / 60
        notes.append(f"newest report: {newest.parent.name[:48]} ({age_min:.0f}m old)")
        if age_min > args.stall_minutes and completed < args.total_cells:
            problems.append(
                f"STALL: no new report for {age_min:.0f}m (> {args.stall_minutes:.0f}m)"
            )
    elif (
        completed < args.total_cells
        and index_path.exists()
        and len(reports) == 0
        and completed == 0
    ):
        # Index empty AND no reports at all — only alarming once warm-up passed.
        first_dir = min(args.out.glob("*/"), key=lambda p: p.mtime(), default=None)
        if first_dir:
            age_min = (time.time() - first_dir.stat().st_mtime) / 60
            if age_min > args.stall_minutes:
                problems.append(
                    f"STALL: running {age_min:.0f}m without a single finished cell"
                )

    # ── 4. Log scan (works because runs use python -u) ──────────────────
    log_path = logs_dir / "baseline_run2.log"
    if log_path.exists():
        text = log_path.read_text(errors="replace")
        n_exc = text.count("EXCEPTION")
        n_failed = text.count("❌ FAILED")
        if n_exc:
            problems.append(f"log shows {n_exc} EXCEPTION lines")
        if n_failed:
            notes.append(f"log shows {n_failed} explicit FAILED cells")
        last_line = [ln for ln in text.strip().splitlines() if ln.strip()]
        if last_line:
            notes.append(f"last log: {last_line[-1][:80]}")

    # ── Verdict ─────────────────────────────────────────────────────────
    print("=== TOURNAMENT HEALTH ===")
    for note in notes:
        print(f"  · {note}")
    if problems:
        print("  ⚠ PROBLEMS:")
        for problem in problems:
            print(f"    ✗ {problem}")
        sys.exit(1)
    print("  ✅ HEALTHY")


if __name__ == "__main__":
    main()
