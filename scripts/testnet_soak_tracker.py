#!/usr/bin/env python3
"""Binance Spot Testnet soak tracker (P3 release gates).

Tracks the P3 soak evidence from the local audit trail so the operator can
see progress toward the release gates without manual counting:

  * continuous testnet days        — calendar days with at least one
    terminal run, with no gap longer than `--max-gap-hours` between runs
  * complete order lifecycles      — an entry/exit fill that also produced a
    protective stop placement for the same symbol (entry → fill → protection)
  * duplicate / unresolved orders  — order_submission_unknown, order_non_terminal,
    reconciliation_blocked, position_protection_failed events
  * protective-stop coverage       — stops placed vs eligible fills
  * ledger drift evidence          — order_balance_reconciled (ok) vs
    reconciliation_blocked (fail)

Usage:
  python scripts/testnet_soak_tracker.py --audit-log data/execution/binance_live_audit.jsonl
  python scripts/testnet_soak_tracker.py --check --min-days 30 --min-lifecycles 100

Exit codes: 0 = report written (and gates pass with --check); 1 = a --check
gate is not yet met (still soaking is expected, not an error state);
2 = audit log missing/unreadable.
"""

from __future__ import annotations

import argparse
import json
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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
                continue
            events.append(payload)
    if not events:
        raise SystemExit(f"audit log is empty: {path}")
    return events


def _terminals(events: list[dict]) -> list[datetime]:
    out: list[datetime] = []
    for event in events:
        if event.get("event") in TERMINAL_RUNS:
            ts = _ts(event.get("timestamp"))
            if ts is not None:
                out.append(ts)
    return sorted(out)


def _calendar_days(terminals: list[datetime]) -> int:
    if not terminals:
        return 0
    return (terminals[-1].date() - terminals[0].date()).days + 1


def tracking_days(events: list[dict], *, max_gap_hours: float) -> int:
    """Consecutive covered days; a gap longer than max_gap_hours breaks the run."""
    terminals = _terminals(events)
    if not terminals:
        return 0
    days = 1
    prev = terminals[0]
    for ts in terminals[1:]:
        if (ts - prev) <= timedelta(hours=max_gap_hours):
            days += 1 if ts.date() != prev.date() else 0
        else:
            days = 1
        prev = ts
    return days


def count_lifecycles(events: list[dict]) -> tuple[int, dict[str, int]]:
    """Entry/exit fills with a protective stop placed for the same symbol."""
    fills_by_symbol: dict[str, int] = {}
    stops_by_symbol: dict[str, int] = {}
    for event in events:
        details = event.get("details") or {}
        symbol = details.get("symbol")
        if not symbol:
            continue
        if event.get("event") == FILL_EVENT:
            fills_by_symbol[symbol] = fills_by_symbol.get(symbol, 0) + 1
        elif event.get("event") == STOP_EVENT:
            stops_by_symbol[symbol] = stops_by_symbol.get(symbol, 0) + 1
    complete = 0
    for symbol in set(fills_by_symbol) | set(stops_by_symbol):
        complete += min(fills_by_symbol.get(symbol, 0), stops_by_symbol.get(symbol, 0))
    return complete, {
        "fills": sum(fills_by_symbol.values()),
        "stops": sum(stops_by_symbol.values()),
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


def build_report(events: list[dict], *, max_gap_hours: float) -> dict:
    terminals = _terminals(events)
    lifecycles, lifecycle_parts = count_lifecycles(events)
    critical = count_critical(events)
    reconcil = count_reconciliations(events)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tracking": {
            "days_cumulative": _calendar_days(terminals),
            "days_continuous": tracking_days(events, max_gap_hours=max_gap_hours),
            "max_gap_hours": max_gap_hours,
            "terminal_runs": len(terminals),
            "first_run": terminals[0].isoformat() if terminals else None,
            "last_run": terminals[-1].isoformat() if terminals else None,
        },
        "order_lifecycles": {
            "complete": lifecycles,
            "fills": lifecycle_parts["fills"],
            "protective_stops_placed": lifecycle_parts["stops"],
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
    gates = {
        "continuous_30_days": tracking["days_continuous"] >= min_days,
        "100_complete_lifecycles": lifecycles["complete"] >= min_lifecycles,
        "zero_unexplained_events": sum(critical.values()) == 0,
        "stop_coverage_100pct": (
            lifecycles["protective_stops_placed"] >= lifecycles["fills"]
            and lifecycles["fills"] > 0
        )
        or lifecycles["fills"] == 0,
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        events = load_events(Path(args.audit_log))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = build_report(events, max_gap_hours=args.max_gap_hours)
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
