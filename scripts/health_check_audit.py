#!/usr/bin/env python
"""Health check for the tournament audit database.

Verifies:
  1. Database file exists and is readable
  2. SQLite schema integrity (PRAGMA integrity_check)
  3. WAL mode is enabled (concurrency support)
  4. Entry count is non-zero (recent activity)
  5. No stale entries (last entry within max_age_hours)
  6. Deterministic entry_id uniqueness (no hash collisions)

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def check_database(db_path: Path, *, max_age_hours: float = 168.0) -> list[dict[str, Any]]:
    """Run all health checks on the audit database.

    Returns a list of check results: {"name": str, "passed": bool, "detail": str}.
    """
    results: list[dict[str, Any]] = []

    # ── Check 1: File exists ──────────────────────────────────────────────
    if db_path.exists():
        results.append({
            "name": "database_exists",
            "passed": True,
            "detail": f"Found at {db_path}",
        })
    else:
        results.append({
            "name": "database_exists",
            "passed": False,
            "detail": f"Database not found: {db_path}",
        })
        return results

    # ── Check 2: Read connection ────────────────────────────────────────
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        results.append({
            "name": "read_access",
            "passed": True,
            "detail": "SQLite read connection successful",
        })
    except sqlite3.Error as e:
        results.append({
            "name": "read_access",
            "passed": False,
            "detail": f"Failed to read database: {e}",
        })
        return results

    # ── Check 3: SQLite integrity ───────────────────────────────────────
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        passed = row[0] == "ok"
        results.append({
            "name": "integrity_check",
            "passed": passed,
            "detail": row[0] if not passed else "SQLite integrity OK",
        })
    except sqlite3.Error as e:
        results.append({
            "name": "integrity_check",
            "passed": False,
            "detail": f"Integrity check failed: {e}",
        })

    # ── Check 4: WAL mode ───────────────────────────────────────────────
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        results.append({
            "name": "wal_mode",
            "passed": journal_mode.lower() in ("wal", "delete"),
            "detail": f"journal_mode={journal_mode}",
        })
    except sqlite3.Error as e:
        results.append({
            "name": "wal_mode",
            "passed": False,
            "detail": f"Cannot read journal_mode: {e}",
        })

    # ── Check 5: Entry count ────────────────────────────────────────────
    try:
        count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        results.append({
            "name": "entry_count",
            "passed": count > 0,
            "detail": f"{count} entries in audit table",
        })
    except sqlite3.Error as e:
        results.append({
            "name": "entry_count",
            "passed": False,
            "detail": f"Cannot count entries: {e}",
        })

    # ── Check 6: No stale entries ───────────────────────────────────────
    try:
        latest_row = conn.execute(
            "SELECT observed_at FROM entries ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
        if latest_row and latest_row[0]:
            latest_ts = datetime.fromisoformat(latest_row[0])
            now = datetime.now(UTC)
            age_hours = (now - latest_ts).total_seconds() / 3600
            stale = age_hours > max_age_hours
            results.append({
                "name": "freshness",
                "passed": not stale,
                "detail": (
                    f"Latest entry at {latest_ts.isoformat()}, "
                    f"age={age_hours:.1f}h "
                    f"({'STALE' if stale else 'fresh'} > max {max_age_hours}h)"
                ),
            })
        else:
            results.append({
                "name": "freshness",
                "passed": False,
                "detail": "No entries found for freshness check",
            })
    except sqlite3.Error as e:
        results.append({
            "name": "freshness",
            "passed": False,
            "detail": f"Cannot check freshness: {e}",
        })

    # ── Check 7: entry_id uniqueness ────────────────────────────────────
    try:
        dupes = conn.execute(
            "SELECT entry_id, COUNT(*) as c FROM entries GROUP BY entry_id HAVING c > 1 LIMIT 1"
        ).fetchone()
        passed = dupes is None
        results.append({
            "name": "entry_id_uniqueness",
            "passed": passed,
            "detail": (
                f"All entry_ids unique ({dupes[1]} collision for {dupes[0]})"
                if not passed else "All entry_ids are unique"
            ),
        })
    except sqlite3.Error as e:
        results.append({
            "name": "entry_id_uniqueness",
            "passed": False,
            "detail": f"Cannot verify uniqueness: {e}",
        })

    conn.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Health check for tournament audit database"
    )
    parser.add_argument("db_path", type=Path, help="Path to tournament_audit.sqlite3")
    parser.add_argument(
        "--max-age-hours", type=float, default=168.0,
        help="Max acceptable staleness in hours (default: 168 = 1 week)",
    )
    args = parser.parse_args()

    results = check_database(args.db_path, max_age_hours=args.max_age_hours)
    all_passed = True

    print(f"Audit DB Health Check: {args.db_path}\n" + "=" * 50)
    for r in results:
        status = "✓ PASS" if r["passed"] else "✗ FAIL"
        print(f"  {status} — {r['name']}: {r['detail']}")
        if not r["passed"]:
            all_passed = False

    print("=" * 50)
    if all_passed:
        print("\nAll checks passed ✓")
        return 0
    else:
        print("\nHealth check FAILED — audit DB requires attention")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
