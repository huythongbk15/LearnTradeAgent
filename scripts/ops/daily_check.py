#!/usr/bin/env python3
"""Daily read-only check of the scheduled Binance testnet runner.

Reports expected vs actual cycles, each execution run's outcome and duration,
the health check, ledger and lifecycle consistency, M1 progress and, while the
operational track is authorized, M3 progress
(docs/operations/OPERABLE_SYSTEM_PLAN.md). Exit 0 when nothing needs attention,
1 otherwise. Reads files only: no exchange call and no write.

    .venv/bin/python scripts/ops/daily_check.py [--hours 24] [--m1-start ISO]
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from check_live_audit import (  # noqa: E402
    AuditHealthError,
    _runs,
    load_events,
    validate_audit_health,
)

CADENCE = timedelta(hours=2)
GAP_LIMIT = timedelta(hours=2, minutes=15)
MAX_CYCLE_SECONDS = 60.0
M1_RUNS, M1_HOURS = 24, 48
M3_LIFECYCLES, M3_EACH_EXIT = 10, 3
OPERATIONAL_REASON = "OPERATIONAL_EXERCISER"
# First clean headless cycle (task run 2026-10-29 17:41:59Z, rc=0, 18 s).
DEFAULT_M1_START = "2026-10-29T17:41:00+00:00"
TERMINAL_LIFECYCLE = {"exec.fill_received", "exec.order_rejected", "exec.cancel_confirmed"}
CYCLE_LINE = re.compile(r"^=== (\S+Z) cycle (start|end|killed by) ?(\S*)")
PID_FIELD = re.compile(r"pid=(\d+)")


def wrapper_cycles(log: Path, since: datetime) -> list[dict]:
    """Cycles as seen by the wrapper, including ones killed before any audit line."""
    cycles: list[dict] = []
    if not log.is_file():
        return cycles
    for line in log.read_text(errors="replace").splitlines():
        match = CYCLE_LINE.match(line)
        if not match:
            continue
        at = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
        if at < since:
            continue
        if match[2] == "start":
            pid = PID_FIELD.search(line)
            alive = bool(pid) and Path(f"/proc/{pid[1]}").exists()
            cycles.append({"start": at, "end": None,
                           "result": "running" if alive else "died, no end line"})
        elif cycles and cycles[-1]["end"] is None:
            cycles[-1]["end"] = at
            cycles[-1]["result"] = match[3] if match[2] == "end" else f"killed {match[3]}"
    return cycles


def open_lifecycle_intents(accounts: Path) -> list[str]:
    stores = sorted(accounts.glob("*/events.db"))
    if len(stores) != 1:
        return [f"expected one account event store, found {len(stores)}"]
    # Live store with a WAL: read-only, fetch everything, close right away.
    con = sqlite3.connect(f"file:{stores[0]}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT aggregate_id, event_type FROM execution_events e WHERE seq = "
            "(SELECT MAX(seq) FROM execution_events WHERE aggregate_id = e.aggregate_id)"
        ).fetchall()
    finally:
        con.close()
    return [f"{aid} ends at {event}" for aid, event in rows if event not in TERMINAL_LIFECYCLE]


def m3_progress(events: list[dict], runs: list[dict], *, now: datetime) -> list[str]:
    """Lifecycles of the operational track since the authorization in force."""
    from trading_agent.execution.live_safety import account_fingerprint
    from trading_agent.execution.operational_authorization import (
        DEFAULT_PATH,
        EXCHANGE,
        OperationalAuthorizationError,
        load_authorization,
    )
    from trading_agent.execution.operational_driver import driver_code_sha

    path = Path(os.getenv("LIVE_OPERATIONAL_AUTHORIZATION", str(DEFAULT_PATH)))
    account_id = os.getenv("LIVE_ACCOUNT_ID", "").strip()
    try:
        authorization = load_authorization(
            path,
            key=os.getenv("LIVE_SAFETY_HMAC_KEY", ""),
            account_fingerprint=account_fingerprint(exchange=EXCHANGE, api_key=account_id)
            if account_id
            else "",
            driver_code_sha=driver_code_sha(),
            now=now,
        )
    except OperationalAuthorizationError as exc:
        print(f"M3       operational track inactive: {exc}")
        return []
    if authorization is None:
        print("M3       operational track not authorized (entries locked)")
        return []
    since = authorization.issued_at
    recent = [event for event in events if event["_timestamp"] >= since]

    def fills(side: str, operational: bool) -> int:
        return sum(
            1
            for event in recent
            if event["event"] == "order_filled"
            and event.get("details", {}).get("side") == side
            and (event.get("details", {}).get("reason") == OPERATIONAL_REASON) == operational
        )

    opened, sold = fills("BUY", True), fills("SELL", True)
    stopped = sum(1 for event in recent if event["event"] == "protective_stop_filled")
    other = fills("SELL", False)
    closed = sold + stopped + other
    failed = [
        run for run in runs
        if run["started"] >= since and run["terminal"]
        and run["terminal"]["event"] != "run_completed"
    ]
    passed = (
        closed >= M3_LIFECYCLES and sold >= M3_EACH_EXIT and stopped >= M3_EACH_EXIT
        and not failed
    )
    print(
        f"M3       {closed}/{M3_LIFECYCLES} lifecycles closed since {since:%m-%d %H:%MZ} "
        f"(sell {sold}, venue stop {stopped}, other {other}; opened {opened}); "
        f"authorization {authorization.authorization_id} until "
        f"{authorization.expires_at:%m-%d %H:%MZ}; "
        + ("PASSED" if passed else "RESET NEEDED (see issues)" if failed else "in progress")
    )
    return [
        f"M3: execution run at {run['started']:%m-%d %H:%M}Z ended {run['terminal']['event']}"
        for run in failed
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--m1-start", default=DEFAULT_M1_START)
    parser.add_argument("--audit-log", default="data/execution/binance_live_audit.jsonl")
    parser.add_argument("--cycle-log", default="logs/binance_testnet.log")
    parser.add_argument("--state-file", default="data/binance_testnet_risk_state_v2.json")
    args = parser.parse_args()
    os.chdir(ROOT)
    now = datetime.now(UTC)
    since = now - timedelta(hours=args.hours)
    m1_start = datetime.fromisoformat(args.m1_start)
    issues: list[str] = []
    print(f"DAILY CHECK {now:%Y-%m-%d %H:%MZ}  window {args.hours:g} h")

    # Schedule adherence, from the wrapper log.
    cycles = wrapper_cycles(Path(args.cycle_log), since)
    expected = int((now - max(since, m1_start)).total_seconds() // CADENCE.total_seconds()) if now > m1_start else 0
    print(f"Cycles   {len(cycles)} started by the schedule (≈{expected} expected since "
          f"{max(since, m1_start):%m-%d %H:%MZ})")
    for cycle in cycles:
        took = (cycle["end"] - cycle["start"]).total_seconds() if cycle["end"] else None
        print(f"  {cycle['start']:%m-%d %H:%M}Z  {cycle['result']:<14} "
              f"{'' if took is None else f'{took:.0f} s'}")
        if cycle["result"] not in ("rc=0", "running"):
            issues.append(f"cycle {cycle['start']:%H:%M}Z: {cycle['result']}")
    starts = [c["start"] for c in cycles] + [now]
    for earlier, later in zip(starts, starts[1:]):
        if later - earlier > GAP_LIMIT and later > m1_start:
            issues.append(f"no cycle between {earlier:%m-%d %H:%M}Z and {later:%m-%d %H:%M}Z")
    if not cycles and now - max(since, m1_start) > GAP_LIMIT:
        issues.append("no scheduled cycle in the window")

    # Execution runs and health, from the audit log.
    events = load_events(args.audit_log)
    runs = [r for r in _runs(sorted(events, key=lambda e: e["_timestamp"])) if r["execute"]]
    recent = [r for r in runs if r["started"] >= since]
    for run in recent:
        end = run["terminal"]
        took = (end["_timestamp"] - run["started"]).total_seconds() if end else None
        outcome = end["event"] if end else "no terminal event"
        if outcome != "run_completed":
            issues.append(f"execution run pid {run['pid']} at {run['started']:%H:%M}Z: {outcome}")
        elif took is not None and took > MAX_CYCLE_SECONDS:
            issues.append(f"execution run at {run['started']:%H:%M}Z took {took:.0f} s")
    print(f"Runs     {len(recent)} execution runs in the window, "
          f"{sum(1 for r in recent if r['terminal'] and r['terminal']['event'] == 'run_completed')} completed")
    try:
        validate_audit_health(events, now=now, max_age_seconds=9_900,
                              lookback_seconds=9_900, max_run_seconds=2_700)
        print("Health   OK")
    except AuditHealthError as exc:
        print(f"Health   CRITICAL: {exc}")
        issues.append(f"health: {exc}")

    # Books: ledger (HMAC-verified) and lifecycle.
    from dotenv import load_dotenv
    from trading_agent.execution.live_safety import LiveRiskStateStore

    load_dotenv(ROOT / ".env")
    store = LiveRiskStateStore(args.state_file, integrity_key=os.environ["LIVE_SAFETY_HMAC_KEY"])
    unfinished = list(store.unfinished_orders())
    print(f"Ledger   unfinished {len(unfinished)}, locked {store.state.locked_reason}")
    if unfinished or store.state.locked_reason:
        issues.append(f"ledger: unfinished {unfinished}, locked {store.state.locked_reason}")
    open_intents = open_lifecycle_intents(ROOT / "data" / "execution" / "accounts")
    print(f"Lifecycle open intents {len(open_intents)}")
    issues.extend(f"lifecycle: {item}" for item in open_intents)

    # M1 progress since its start, over all scheduled execution runs.
    m1_runs = [r for r in runs if r["started"] >= m1_start]
    clean = [
        r for r in m1_runs
        if r["terminal"] and r["terminal"]["event"] == "run_completed"
        and (r["terminal"]["_timestamp"] - r["started"]).total_seconds() <= MAX_CYCLE_SECONDS
    ]
    hours = max(0.0, (now - m1_start).total_seconds() / 3600)
    m1_issues = [c for c in cycles if c["start"] >= m1_start and c["result"] not in ("rc=0", "running")]
    m1_ok = len(clean) == len(m1_runs) and not m1_issues
    print(f"M1       {len(clean)}/{M1_RUNS} clean cycles, {hours:.1f}/{M1_HOURS} h since "
          f"{m1_start:%m-%d %H:%MZ}; "
          + ("not started" if now < m1_start
             else "PASSED" if m1_ok and len(clean) >= M1_RUNS and hours >= M1_HOURS
             else "on track" if m1_ok else "RESET NEEDED (see issues)"))

    issues.extend(m3_progress(events, runs, now=now))

    if issues:
        print("ISSUES")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("No issues.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
