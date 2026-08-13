#!/usr/bin/env python3
"""
QwenPaw Agent: Cleanup cron - kill stale processes, archive old results.
Run via crontab every 5-10 minutes.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from process_registry import cleanup_stale, list_active
from pathlib import Path
import time
import shutil


def main():
    print(f"=== QWENPAW CLEANUP {time.ctime()} ===", flush=True)

    # 1. Kill stale processes (>4h no heartbeat)
    killed = cleanup_stale(max_age_sec=14400, kill=True)
    print(f"Stale processes killed: {killed}", flush=True)

    # 2. Archive old result files (>7 days)
    archive_old_results()

    # 3. Report active processes
    active = list_active()
    print(f"Active processes: {len(active)}", flush=True)
    for p in active:
        age = int(time.time() - p.started_at)
        hb_age = int(time.time() - p.heartbeat_at)
        print(f"  PID={p.pid} age={age}s hb_age={hb_age}s {p.cmd[:80]}", flush=True)

    # 4. Clean old registry entries (>30 days completed)
    clean_old_registry()

    print("=== CLEANUP DONE ===", flush=True)


def archive_old_results(max_age_days: int = 7):
    """Move old result files to archive."""
    data_dir = Path(__file__).parent.parent.parent / "data"
    archive_dir = data_dir / "archive" / time.strftime("%Y-%m")
    archive_dir.mkdir(parents=True, exist_ok=True)

    cutoff = time.time() - max_age_days * 86400
    moved = 0

    for pattern in [
        "backtest_*.json",
        "subagent_*.json",
        "full_system_backtest.json",
        "paper_*.json",
    ]:
        for f in data_dir.glob(pattern):
            if f.stat().st_mtime < cutoff:
                dest = archive_dir / f.name
                shutil.move(str(f), str(dest))
                moved += 1
                print(f"Archived: {f.name} -> {dest}", flush=True)

    if moved:
        print(f"Archived {moved} files", flush=True)


def clean_old_registry(max_age_days: int = 30):
    """Delete completed/failed registry entries older than max_age_days."""
    import sqlite3

    db_path = (
        Path(__file__).parent.parent.parent / "data" / "qwenpaw_process_registry.db"
    )
    if not db_path.exists():
        return

    cutoff = time.time() - max_age_days * 86400
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            """DELETE FROM qwenpaw_processes 
            WHERE status IN ('completed','failed','timeout','killed','stale_killed') 
            AND started_at < ?""",
            (cutoff,),
        )
        deleted = cur.rowcount
        con.commit()
        if deleted:
            print(f"Cleaned {deleted} old registry entries", flush=True)


if __name__ == "__main__":
    main()
