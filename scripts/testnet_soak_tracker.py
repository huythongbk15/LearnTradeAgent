#!/usr/bin/env python3
"""Binance Spot Testnet soak tracker (P3 release gates).

Tracks the P3 soak evidence from the local audit trail so the operator can
see progress toward the release gates without manual counting:

  * continuous testnet days        — successful, attributed, healthy runs;
    failures/critical events reset the streak and stale evidence is blocked
  * protected entry lifecycles     — unique BUY fill explicitly linked to a
    confirmed, quantity-covering stop; NOT completed round trips
  * duplicate / unresolved orders  — order_submission_unknown, order_non_terminal,
    reconciliation_blocked, position_protection_failed events
  * protective-stop coverage       — stops placed vs eligible fills
  * ledger drift evidence          — order_balance_reconciled (ok) vs
    reconciliation_blocked (fail)

Schema v2 requires the same frozen ``subject`` on every event (environment,
execute, account_fingerprint, build_sha, policy_sha), correlated start/terminal
runs, and terminal portfolio health. Legacy runner logs lack this instrumentation
and are diagnostic-only: this tool must not invent missing evidence for them.
This report is not a mainnet admission certificate or a signature verifier.

Usage (explicit identity is required for gate checks):
  python scripts/testnet_soak_tracker.py --audit-log data/execution/binance_live_audit.jsonl
  python scripts/testnet_soak_tracker.py --check --account-fingerprint HASH --build-sha SHA --policy-sha HASH

Exit codes: 0 = report written (and gates pass with --check); 1 = a --check
gate is not yet met (still soaking is expected, not an error state);
2 = audit log missing/unreadable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

TERMINAL_RUNS = frozenset({"run_completed", "run_failed"})
CRITICAL_EVENTS = frozenset(
    {
        "order_submission_unknown",
        "order_non_terminal",
        "reconciliation_blocked",
        "position_protection_failed",
    }
)
FILL_EVENT = "order_filled"
STOP_EVENT = "protective_stop_placed"


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"audit log does not exist: {path}")
    events: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"corrupt audit line in {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise SystemExit(f"non-object audit line in {path}")
            events.append(payload)
    if not events:
        raise SystemExit(f"audit log is empty: {path}")
    return events


def _terminals(events: list[dict]) -> list[datetime]:
    out: list[datetime] = []
    for event in events:
        if event.get("event") == "run_completed":
            ts = _ts(event.get("timestamp"))
            if ts is not None:
                out.append(ts)
    return sorted(out)


def _calendar_days(terminals: list[datetime]) -> int:
    if not terminals:
        return 0
    return (terminals[-1].date() - terminals[0].date()).days + 1


def tracking_days(events: list[dict], *, max_gap_hours: float) -> int:
    """Successful streak only; failed/critical events break continuous coverage."""
    if not math.isfinite(max_gap_hours) or max_gap_hours <= 0:
        raise ValueError("max_gap_hours must be finite and positive")
    days, prev = 0, None
    relevant = [
        event
        for event in _unique_events(events)
        if event.get("event") in TERMINAL_RUNS | CRITICAL_EVENTS
    ]
    if any(_ts(event.get("timestamp")) is None for event in relevant):
        return 0
    for event in sorted(relevant, key=lambda item: _ts(item["timestamp"])):
        ts = _ts(event["timestamp"])
        if event["event"] != "run_completed":
            days, prev = 0, None
            continue
        if prev is None or ts - prev > timedelta(hours=max_gap_hours):
            days = 1
        elif ts.date() != prev.date():
            days += 1
        prev = ts
    return days


def _unique_events(events: list[dict]) -> list[dict]:
    unique = {}
    for event in events:
        if isinstance(event, dict):
            unique.setdefault(json.dumps(event, sort_keys=True), event)
    return list(unique.values())


def _positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def count_lifecycles(events: list[dict]) -> tuple[int, dict[str, int]]:
    """Unique BUY entries linked to confirmed stops, never symbol-level totals.

    This unit is a protected entry, NOT a completed round trip or alpha evidence.
    Unlinked historical audit records cannot prove protection coverage.
    """
    fills, stops, conflicts, fill_ids = {}, {}, set(), {}
    invalid, stop_ids = 0, {}
    for event in _unique_events(events):
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        symbol = details.get("symbol")
        name = event.get("event")
        if name not in {FILL_EVENT, STOP_EVENT}:
            continue
        if name == FILL_EVENT and details.get("side") == "SELL":
            continue  # An exit does not require another entry protective stop.
        key = details.get("order_key" if name == FILL_EVENT else "protected_order_key")
        if (
            not symbol
            or not isinstance(key, str)
            or not key
            or _ts(event.get("timestamp")) is None
        ):
            invalid += 1
            continue
        subject = (symbol, key)
        if name == FILL_EVENT:
            if (
                details.get("side") != "BUY"
                or not _positive(details.get("filled_qty"))
                or not details.get("exchange_order_id")
            ):
                invalid += 1
                continue
            facts = (float(details["filled_qty"]), details["exchange_order_id"])
            fill_id = (symbol, details["exchange_order_id"])
            if fill_id in fill_ids and fill_ids[fill_id] != subject:
                conflicts.update((subject, fill_ids[fill_id]))
            fill_ids[fill_id] = subject
            if subject in fills and fills[subject][0] != facts:
                conflicts.add(subject)
            fills.setdefault(
                subject, (facts, _ts(event["timestamp"]), event.get("correlation_id"))
            )
        else:
            if (
                details.get("status") != "open"
                or not details.get("exchange_order_id")
                or not _positive(details.get("quantity"))
                or not _positive(details.get("stop_price"))
            ):
                invalid += 1
                continue
            stop_id = (symbol, details["exchange_order_id"])
            if stop_id in stop_ids:
                if stop_ids[stop_id] != subject:
                    conflicts.add(subject)
                    conflicts.add(stop_ids[stop_id])
                continue
            stop_ids[stop_id] = subject
            stops.setdefault(subject, []).append(
                (details, _ts(event["timestamp"]), event.get("correlation_id"))
            )
    covered = sum(
        subject not in conflicts
        and any(
            timestamp >= fill_time
            and details["quantity"] >= facts[0] - 1e-9
            and run_id
            and stop_run_id == run_id
            for details, timestamp, stop_run_id in stops.get(subject, [])
        )
        for subject, (facts, fill_time, run_id) in fills.items()
    )
    return covered, {
        "fills": len(fills),
        "stops": len(stop_ids),
        "invalid": invalid + len(conflicts),
        "unprotected": len(fills) - covered,
    }


def count_critical(
    events: list[dict], *, lookback_days: int | None = None, now: datetime | None = None
) -> dict[str, int]:
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=lookback_days) if lookback_days else None
    counts: dict[str, int] = {}
    for event in events:
        if event.get("event") not in CRITICAL_EVENTS:
            continue
        if cutoff is not None:
            ts = _ts(event.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
        counts[event["event"]] = counts.get(event["event"], 0) + 1
    return counts


def count_reconciliations(events: list[dict]) -> dict[str, int]:
    counts = {"order_balance_reconciled": 0, "reconciliation_blocked": 0}
    for event in events:
        name = event.get("event")
        if name in counts:
            counts[name] += 1
    return counts


def _hash_id(value: object, lengths: set[int]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(c in "0123456789abcdef" for c in value)
    )


def _validate_runs(
    events: list[dict],
    expected_subject: dict | None,
    now: datetime,
    max_gap_hours: float,
) -> tuple[dict, list[str]]:
    errors, runs = [], {}
    if max_gap_hours > 36:
        errors.append("coverage gap exceeds the 36-hour soak contract")
    required = {
        "environment",
        "execute",
        "account_fingerprint",
        "build_sha",
        "policy_sha",
    }
    if (
        not isinstance(expected_subject, dict)
        or set(expected_subject) != required
        or expected_subject.get("environment") != "testnet"
        or expected_subject.get("execute") is not True
        or not _hash_id(expected_subject.get("account_fingerprint"), {64})
        or not _hash_id(expected_subject.get("build_sha"), {40, 64})
        or not _hash_id(expected_subject.get("policy_sha"), {64})
    ):
        errors.append("missing/invalid explicitly frozen testnet execution subject")
    for event in events:
        ts = _ts(event.get("timestamp"))
        if ts is None or ts > now:
            errors.append("missing/naive/future event timestamp")
        if not isinstance(event.get("details"), dict):
            errors.append("invalid event details")
            continue
        if expected_subject is None or event.get("subject") != expected_subject:
            errors.append("missing/mismatched environment/account/build/policy subject")
        run_id = event.get("correlation_id")
        if not isinstance(run_id, str) or not run_id.strip():
            errors.append("missing run correlation identity")
            continue
        runs.setdefault(run_id, []).append(event)
    outcomes = {"completed": 0, "failed": 0, "incomplete": 0, "invalid": 0}
    for run_id, run_events in runs.items():
        starts = [e for e in run_events if e.get("event") == "run_started"]
        terminals = [e for e in run_events if e.get("event") in TERMINAL_RUNS]
        if len(starts) != 1 or len(terminals) != 1:
            outcomes["incomplete"] += 1
            errors.append(f"run {run_id}: missing/conflicting start or terminal")
            continue
        started, ended = (
            _ts(starts[0].get("timestamp")),
            _ts(terminals[0].get("timestamp")),
        )
        if (
            started is None
            or ended is None
            or ended < started
            or ended - started > timedelta(hours=max_gap_hours)
            or any(
                _ts(e.get("timestamp")) is None
                or not started <= _ts(e["timestamp"]) <= ended
                for e in run_events
            )
        ):
            outcomes["invalid"] += 1
            errors.append(f"run {run_id}: invalid interval/event ordering")
            continue
        if (
            starts[0].get("details", {}).get("execute") is not True
            or starts[0].get("details", {}).get("testnet") is not True
        ):
            errors.append(f"run {run_id}: not an explicit executed testnet run")
        terminal = terminals[0]
        if terminal["event"] == "run_failed":
            outcomes["failed"] += 1
            continue
        outcomes["completed"] += 1
        details = terminal.get("details") or {}
        health = details.get("health")
        if (
            type(details.get("exit_code")) is not int
            or details["exit_code"] != 0
            or not isinstance(health, dict)
            or health.get("inventory_protected") is not True
            or type(health.get("unresolved_orders")) is not int
            or health["unresolved_orders"] != 0
            or type(health.get("ledger_drift")) not in {int, float}
            or health["ledger_drift"] != 0
            or health.get("reconciliation") != "resolved"
        ):
            errors.append(
                f"run {run_id}: missing/nonhealthy terminal portfolio evidence"
            )
    if not runs:
        errors.append("no attributed runs")
    return outcomes, errors


def build_report(
    events: list[dict],
    *,
    max_gap_hours: float,
    expected_subject: dict | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    if not math.isfinite(max_gap_hours) or not 0 < max_gap_hours <= 8760:
        raise ValueError("max_gap_hours must be finite and positive")
    unique = _unique_events(events)
    terminals = _terminals(unique)
    lifecycles, lifecycle_parts = count_lifecycles(unique)
    critical = count_critical(unique)
    reconcil = count_reconciliations(unique)
    outcomes, errors = _validate_runs(unique, expected_subject, now, max_gap_hours)
    fresh = bool(terminals) and timedelta(0) <= now - terminals[-1] <= timedelta(
        hours=max_gap_hours
    )
    report = {
        "schema_version": 2,
        "generated_at": now.isoformat(),
        "subject": expected_subject,
        "evidence": {
            "eligible": not errors and fresh,
            "errors": sorted(set(errors))[:50],
            "error_count": len(set(errors)),
            "fresh": fresh,
            "duplicate_events_ignored": len(events) - len(unique),
        },
        "run_outcomes": outcomes,
        "tracking": {
            "days_cumulative": _calendar_days(terminals),
            "days_continuous": tracking_days(unique, max_gap_hours=max_gap_hours),
            "max_gap_hours": max_gap_hours,
            "terminal_runs": len(terminals),
            "first_run": terminals[0].isoformat() if terminals else None,
            "last_run": terminals[-1].isoformat() if terminals else None,
        },
        "order_lifecycles": {
            "definition": "unique BUY fill linked to confirmed protective stop; not round trips",
            "complete": lifecycles,
            "fills": lifecycle_parts["fills"],
            "protective_stops_placed": lifecycle_parts["stops"],
            "invalid_events": lifecycle_parts["invalid"],
            "unprotected_entries": lifecycle_parts["unprotected"],
        },
        "unexplained_events": critical,
        "reconciliation": reconcil,
        "gates": {},
    }
    return report


def evaluate_gates(
    report: dict, *, min_days: int, min_lifecycles: int
) -> dict[str, bool]:
    tracking = report["tracking"]
    lifecycles = report["order_lifecycles"]
    critical = report["unexplained_events"]
    if min_days <= 0 or min_lifecycles <= 0:
        raise ValueError("gate thresholds must be positive")
    eligible = (
        report.get("schema_version") == 2
        and report.get("evidence", {}).get("eligible") is True
    )
    gates = {
        "evidence_eligible": eligible,
        "continuous_30_days": eligible and tracking["days_continuous"] >= min_days,
        "100_complete_lifecycles": eligible
        and lifecycles["complete"] >= min_lifecycles,
        "zero_unexplained_events": eligible
        and sum(critical.values()) == 0
        and sum(
            report["run_outcomes"][key] for key in ("failed", "incomplete", "invalid")
        )
        == 0,
        "stop_coverage_100pct": eligible
        and lifecycles["fills"] > 0
        and lifecycles["complete"] == lifecycles["fills"]
        and lifecycles["invalid_events"] == 0,
    }
    report["gates"] = gates
    return gates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-log", default="data/execution/binance_live_audit.jsonl"
    )
    parser.add_argument("--report", default="data/testnet_soak_report.json")
    parser.add_argument("--max-gap-hours", type=float, default=36.0)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--min-days", type=int, default=30)
    parser.add_argument("--min-lifecycles", type=int, default=100)
    parser.add_argument("--account-fingerprint")
    parser.add_argument("--build-sha")
    parser.add_argument("--policy-sha")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        events = load_events(Path(args.audit_log))
    except (SystemExit, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if (
        not math.isfinite(args.max_gap_hours)
        or not 0 < args.max_gap_hours <= 8760
        or args.min_days <= 0
        or args.min_lifecycles <= 0
    ):
        print("gap and gate thresholds must be finite and positive", file=sys.stderr)
        return 2
    if args.check and (
        args.min_days < 30 or args.min_lifecycles < 100 or args.max_gap_hours > 36
    ):
        print(
            "--check cannot weaken the 30-day/100-entry/36-hour soak contract",
            file=sys.stderr,
        )
        return 2
    subject = {
        "environment": "testnet",
        "execute": True,
        "account_fingerprint": args.account_fingerprint,
        "build_sha": args.build_sha,
        "policy_sha": args.policy_sha,
    }
    report = build_report(
        events, max_gap_hours=args.max_gap_hours, expected_subject=subject
    )
    gates = evaluate_gates(
        report,
        min_days=args.min_days,
        min_lifecycles=args.min_lifecycles,
    )
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    tracking = report["tracking"]
    lifecycles = report["order_lifecycles"]
    print(
        f"soak: {tracking['days_continuous']} continuous days "
        f"({tracking['days_cumulative']} cumulative), {lifecycles['complete']} complete "
        f"lifecycles ({lifecycles['fills']} fills / {lifecycles['protective_stops_placed']} stops)"
    )
    print(f"unexplained events: {report['unexplained_events'] or 'none'}")
    print(
        f"run outcomes: {report['run_outcomes']}; evidence eligible: {report['evidence']['eligible']}"
    )
    for error in report["evidence"]["errors"]:
        print(f"evidence blocked: {error}")
    if args.check:
        failed = [name for name, ok in gates.items() if not ok]
        print(f"gates: {gates}")
        if failed:
            print(
                f"GATES NOT MET: {', '.join(failed)} (still soaking — expected until thresholds pass)"
            )
            return 1
        print("ALL GATES MET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
