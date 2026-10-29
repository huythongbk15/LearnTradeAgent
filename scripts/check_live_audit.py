#!/usr/bin/env python3
"""Fail a supervisor health check when the Binance live audit is stale or unsafe."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

CRITICAL_EVENTS = frozenset(
    {
        "order_submission_unknown",
        "order_non_terminal",
        "reconciliation_blocked",
        "run_failed",
    }
)


class AuditHealthError(RuntimeError):
    """Raised when the local live-trading audit cannot prove healthy operation."""


def _timestamp(value: object, *, line_number: int) -> datetime:
    if not isinstance(value, str):
        raise AuditHealthError(f"audit line {line_number} has no timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditHealthError(
            f"audit line {line_number} has an invalid timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise AuditHealthError(f"audit line {line_number} timestamp has no timezone")
    return parsed.astimezone(UTC)


def load_events(path: str | Path) -> list[dict[str, object]]:
    audit_path = Path(path)
    if not audit_path.is_file():
        raise AuditHealthError(f"audit log does not exist: {audit_path}")
    events: list[dict[str, object]] = []
    try:
        with audit_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("event"), str
                ):
                    raise AuditHealthError(
                        f"audit line {line_number} is not an event object"
                    )
                payload["_timestamp"] = _timestamp(
                    payload.get("timestamp"), line_number=line_number
                )
                events.append(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditHealthError(f"cannot read audit log {audit_path}: {exc}") from exc
    if not events:
        raise AuditHealthError(f"audit log is empty: {audit_path}")
    return events


RUNNER_MARKER = "live_enhanced_ma_binance.py"
TERMINAL_EVENTS = frozenset({"run_completed", "run_failed"})


def _process_alive(pid: object) -> bool | None:
    """True/False when /proc can tell whether ``pid`` is still the runner; None if unknowable."""

    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    # A reused PID belongs to another program: treat the runner as gone.
    return RUNNER_MARKER.encode() in cmdline


def _runs(ordered: list[dict[str, object]]) -> list[dict[str, object]]:
    """Pair run_started with its terminal event by pid (dry runs share this log)."""

    runs: list[dict[str, object]] = []
    open_by_pid: dict[object, dict[str, object]] = {}
    for item in ordered:
        event = item["event"]
        pid = item.get("pid")
        if event == "run_started":
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            run = {
                "pid": pid,
                "started": item["_timestamp"],
                # Older lines carry no mode: count them as execution runs.
                "execute": bool(details.get("execute", True)),
                "terminal": None,
            }
            runs.append(run)
            open_by_pid[pid] = run
        elif event in TERMINAL_EVENTS:
            run = open_by_pid.pop(pid, None)
            if run is None and pid is None and open_by_pid:
                run = open_by_pid.pop(next(reversed(open_by_pid)))
            if run is not None:
                run["terminal"] = item
    return runs


def validate_audit_health(
    events: Iterable[dict[str, object]],
    *,
    now: datetime,
    max_age_seconds: float,
    lookback_seconds: float,
    max_run_seconds: float,
    process_alive=_process_alive,
) -> dict[str, object]:
    """Fail unless execution runs keep arriving, finish, and report no critical event.

    Freshness is measured on execution runs only: dry runs and operator tools
    write to the same log and must not make a dead scheduler look healthy.
    """
    if now.tzinfo is None:
        raise AuditHealthError("health-check time must include a timezone")
    if min(max_age_seconds, lookback_seconds, max_run_seconds) <= 0:
        raise AuditHealthError("health-check intervals must be positive")
    current = now.astimezone(UTC)
    ordered = sorted(events, key=lambda item: item["_timestamp"])
    if not ordered:
        raise AuditHealthError("audit log contains no events")
    for item in ordered:
        if not isinstance(item.get("_timestamp"), datetime):
            raise AuditHealthError("audit event timestamp was not normalized")
    latest = ordered[-1]
    if (latest["_timestamp"] - current).total_seconds() > 60:
        raise AuditHealthError(
            f"latest audit event is {(latest['_timestamp'] - current).total_seconds():.0f}s in the future"
        )

    runs = _runs(ordered)
    executions = [run for run in runs if run["execute"]]
    dry_pids = {run["pid"] for run in runs if not run["execute"]}
    cutoff = current - timedelta(seconds=lookback_seconds)
    critical = [
        str(item["event"])
        for item in ordered
        if cutoff <= item["_timestamp"] <= current
        and item["event"] in CRITICAL_EVENTS
        and not (item.get("pid") is not None and item.get("pid") in dry_pids)
    ]
    if critical:
        raise AuditHealthError("critical live audit events: " + ", ".join(critical))

    if not executions:
        raise AuditHealthError("audit log is stale: it has no execution run")
    last = executions[-1]
    last_activity = (
        last["terminal"]["_timestamp"] if last["terminal"] is not None else last["started"]
    )
    age = (current - last_activity).total_seconds()
    if age > max_age_seconds:
        raise AuditHealthError(
            f"latest execution run is stale: {age:.0f}s > {max_age_seconds:.0f}s"
        )

    if last["terminal"] is None:
        runtime = (current - last["started"]).total_seconds()
        if process_alive(last["pid"]) is False:
            raise AuditHealthError(
                f"execution run pid {last['pid']} ended without a terminal event"
            )
        if runtime > max_run_seconds:
            raise AuditHealthError(
                f"runner has no terminal heartbeat after {runtime:.0f}s"
            )

    return {
        "latest_event": latest["event"],
        "latest_timestamp": latest["_timestamp"].isoformat(),
        "age_seconds": max(0.0, age),
        "latest_execution_run_pid": last["pid"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-log",
        default="data/execution/binance_live_audit.jsonl",
    )
    # 2h schedule + a run of up to ~25 min + grace.
    parser.add_argument("--max-age-seconds", type=float, default=9_900)
    parser.add_argument("--lookback-seconds", type=float, default=9_900)
    # Observed 2026-10-29: ~5-6 min per order; 3 orders + setup ~ 20 min.
    parser.add_argument("--max-run-seconds", type=float, default=2_700)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = validate_audit_health(
            load_events(args.audit_log),
            now=datetime.now(UTC),
            max_age_seconds=args.max_age_seconds,
            lookback_seconds=args.lookback_seconds,
            max_run_seconds=args.max_run_seconds,
        )
    except AuditHealthError as exc:
        print(f"LIVE_AUDIT_CRITICAL: {exc}", file=sys.stderr)
        return 2
    print(
        f"LIVE_AUDIT_OK: {result['latest_event']} at "
        f"{result['latest_timestamp']} ({result['age_seconds']:.0f}s ago)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
