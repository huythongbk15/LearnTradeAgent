#!/usr/bin/env python3
"""Independent pager for the Binance live-trading audit.

Runs *outside* the trading runner (cron/systemd supervisor) and pages an
operator device when the audit log proves the live path is unhealthy:

  - audit log missing / stale (no terminal event within the lookback window)
  - critical events in the window (order_submission_unknown,
    order_non_terminal, reconciliation_blocked, run_failed,
    position_protection_failed, risk_limits_change_blocked)
  - runner heartbeat missing (run_started without run_completed/run_failed
    beyond the max-run deadline)

Fail-closed: when the audit cannot be validated the pager exits non-zero
and emits an alert. When Telegram credentials are absent it falls back to
console (stderr) so the failure is still observable by the supervisor.

Usage:
    python scripts/alert_pager.py --check                 # validate only
    python scripts/alert_pager.py --page                  # validate + page
    python scripts/alert_pager.py --dry-run               # page to console only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from trading_agent.monitoring.alerter import init_alerts, send_risk_alert

CRITICAL_EVENTS = frozenset(
    {
        "order_submission_unknown",
        "order_non_terminal",
        "reconciliation_blocked",
        "run_failed",
        "position_protection_failed",
        "risk_limits_change_blocked",
        "orphan_protective_stop_cleared",
    }
)
TERMINAL_EVENTS = frozenset({"run_completed", "run_failed"})


class PagerError(RuntimeError):
    """Raised when the audit cannot prove healthy operation."""


def _parse_time(value: object, line_number: int) -> datetime:
    if not isinstance(value, str):
        raise PagerError(f"audit line {line_number} has no timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PagerError(f"audit line {line_number} has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise PagerError(f"audit line {line_number} timestamp has no timezone")
    return parsed.astimezone(UTC)


def load_events(path: str | Path) -> list[dict[str, object]]:
    audit_path = Path(path)
    if not audit_path.is_file():
        raise PagerError(f"audit log does not exist: {audit_path}")
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
                    raise PagerError(f"audit line {line_number} is not an event object")
                payload["_timestamp"] = _parse_time(
                    payload.get("timestamp"), line_number=line_number
                )
                events.append(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise PagerError(f"cannot read audit log {audit_path}: {exc}") from exc
    if not events:
        raise PagerError(f"audit log is empty: {audit_path}")
    return events


def validate(
    events: Iterable[dict[str, object]],
    *,
    now: datetime,
    max_age_seconds: float,
    lookback_seconds: float,
    max_run_seconds: float,
) -> dict[str, object]:
    """Return a health summary, raising PagerError on any unhealthy condition."""
    if now.tzinfo is None:
        raise PagerError("pager time must include a timezone")
    if min(max_age_seconds, lookback_seconds, max_run_seconds) <= 0:
        raise PagerError("pager intervals must be positive")

    current = now.astimezone(UTC)
    ordered = sorted(events, key=lambda item: item["_timestamp"])
    latest = ordered[-1]
    latest_time = latest["_timestamp"]
    if not isinstance(latest_time, datetime):
        raise PagerError("audit event timestamp was not normalized")

    age = (current - latest_time).total_seconds()
    if age < -60:
        raise PagerError(f"latest audit event is {-age:.0f}s in the future")
    if age > max_age_seconds:
        raise PagerError(
            f"latest audit event is stale: {age:.0f}s > {max_age_seconds:.0f}s"
        )

    cutoff = current - timedelta(seconds=lookback_seconds)
    critical = [
        str(item["event"])
        for item in ordered
        if cutoff <= item["_timestamp"] <= current and item["event"] in CRITICAL_EVENTS
    ]
    if critical:
        raise PagerError(
            "critical live audit events: " + ", ".join(sorted(set(critical)))
        )

    open_run: datetime | None = None
    for item in ordered:
        event = item["event"]
        if event == "run_started":
            open_run = item["_timestamp"]
        elif event in TERMINAL_EVENTS:
            open_run = None
    if open_run is not None:
        runtime = (current - open_run).total_seconds()
        if runtime > max_run_seconds:
            raise PagerError(f"runner has no terminal heartbeat after {runtime:.0f}s")

    return {
        "status": "ok",
        "latest_event": latest["event"],
        "latest_timestamp": latest_time.isoformat(),
        "age_seconds": max(0.0, age),
        "checked_at": current.isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-log", default="data/execution/binance_live_audit.jsonl"
    )
    parser.add_argument("--max-age-seconds", type=float, default=4_500)
    parser.add_argument("--lookback-seconds", type=float, default=4_500)
    parser.add_argument("--max-run-seconds", type=float, default=900)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--page", action="store_true", help="send operator page on failure"
    )
    mode.add_argument("--dry-run", action="store_true", help="page to console only")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    init_alerts({"console": {"enabled": True}, "telegram": {"enabled": True}})
    try:
        result = validate(
            load_events(args.audit_log),
            now=datetime.now(UTC),
            max_age_seconds=args.max_age_seconds,
            lookback_seconds=args.lookback_seconds,
            max_run_seconds=args.max_run_seconds,
        )
    except PagerError as exc:
        message = f"LIVE_PAGER_CRITICAL: {exc}"
        print(message, file=sys.stderr)
        if args.page:
            send_risk_alert("live_pager", str(exc), value=1.0, limit=0.0)
        elif args.dry_run:
            print(f"[dry-run would page] {message}")
        return 2
    print(
        f"LIVE_PAGER_OK: {result['latest_event']} at {result['latest_timestamp']} "
        f"({result['age_seconds']:.0f}s ago)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
