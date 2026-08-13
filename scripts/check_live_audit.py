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


def validate_audit_health(
    events: Iterable[dict[str, object]],
    *,
    now: datetime,
    max_age_seconds: float,
    lookback_seconds: float,
    max_run_seconds: float,
) -> dict[str, object]:
    if now.tzinfo is None:
        raise AuditHealthError("health-check time must include a timezone")
    if min(max_age_seconds, lookback_seconds, max_run_seconds) <= 0:
        raise AuditHealthError("health-check intervals must be positive")
    current = now.astimezone(UTC)
    ordered = sorted(events, key=lambda item: item["_timestamp"])
    if not ordered:
        raise AuditHealthError("audit log contains no events")
    latest = ordered[-1]
    latest_time = latest["_timestamp"]
    if not isinstance(latest_time, datetime):
        raise AuditHealthError("audit event timestamp was not normalized")
    age = (current - latest_time).total_seconds()
    if age < -60:
        raise AuditHealthError(f"latest audit event is {-age:.0f}s in the future")
    if age > max_age_seconds:
        raise AuditHealthError(
            f"latest audit event is stale: {age:.0f}s > {max_age_seconds:.0f}s"
        )

    cutoff = current - timedelta(seconds=lookback_seconds)
    critical = [
        str(item["event"])
        for item in ordered
        if cutoff <= item["_timestamp"] <= current and item["event"] in CRITICAL_EVENTS
    ]
    if critical:
        raise AuditHealthError("critical live audit events: " + ", ".join(critical))

    open_run: datetime | None = None
    for item in ordered:
        event = item["event"]
        if event == "run_started":
            open_run = item["_timestamp"]
        elif event in {"run_completed", "run_failed"}:
            open_run = None
    if open_run is not None:
        runtime = (current - open_run).total_seconds()
        if runtime > max_run_seconds:
            raise AuditHealthError(
                f"runner has no terminal heartbeat after {runtime:.0f}s"
            )

    return {
        "latest_event": latest["event"],
        "latest_timestamp": latest_time.isoformat(),
        "age_seconds": max(0.0, age),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-log",
        default="data/execution/binance_live_audit.jsonl",
    )
    parser.add_argument("--max-age-seconds", type=float, default=4_500)
    parser.add_argument("--lookback-seconds", type=float, default=4_500)
    parser.add_argument("--max-run-seconds", type=float, default=900)
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
