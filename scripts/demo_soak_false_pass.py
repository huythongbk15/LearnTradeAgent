#!/usr/bin/env python3
"""Demonstrate the soak tracker's false-PASS path before it is fixed.

Builds a synthetic audit log of 31 consecutive failing runs over 30
calendar days — the exact shape a real outage would produce — and asks
the tracker whether it passes. If it does, the tracker cannot distinguish
a system that ran every eight hours for a month from a healthy one.

Run: .venv/bin/python scripts/demo_soak_false_pass.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from testnet_soak_tracker import build_report, evaluate_gates  # noqa: E402

SUBJECT = {
    "environment": "testnet",
    "execute": True,
    "account_fingerprint": "a" * 64,
    "build_sha": "b" * 40,
    "policy_sha": "c" * 64,
}


def build_log(*, runs: int, fail: bool, start: datetime) -> list[dict]:
    """One run every eight hours, each either succeeding or failing."""
    events: list[dict] = []
    for i in range(runs):
        run_id = f"run-{i:03d}"
        t0 = start + timedelta(hours=8 * i)
        events.append({
            "event": "run_started", "correlation_id": run_id,
            "timestamp": t0.isoformat(), "subject": SUBJECT,
            "details": {"execute": True, "testnet": True},
        })
        t1 = t0 + timedelta(minutes=5)
        if fail:
            events.append({
                "event": "run_failed", "correlation_id": run_id,
                "timestamp": t1.isoformat(), "subject": SUBJECT,
                "details": {"reason": "signal producer abstained"},
            })
            continue
        events.append({
            "event": "order_filled", "correlation_id": run_id,
            "timestamp": (t1 + timedelta(seconds=1)).isoformat(),
            "subject": SUBJECT,
            "details": {"symbol": "BTC/USDT", "side": "BUY",
                        "quantity": 0.001, "price": 100.0},
        })
        events.append({
            "event": "protective_stop_placed", "correlation_id": run_id,
            "timestamp": (t1 + timedelta(seconds=2)).isoformat(),
            "subject": SUBJECT,
            "details": {"symbol": "BTC/USDT", "quantity": 0.001},
        })
        events.append({
            "event": "run_completed", "correlation_id": run_id,
            "timestamp": (t1 + timedelta(minutes=1)).isoformat(),
            "subject": SUBJECT,
            "details": {"exit_code": 0,
                        "health": {"inventory_protected": True,
                                   "unresolved_orders": 0}},
        })
    return events


def main() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    runs = 91  # 91 x 8h = 30 days

    print("=" * 72)
    print("SOAK TRACKER — false-PASS demonstration")
    print("=" * 72)

    for label, fail in (("all runs FAIL", True), ("all runs OK", False)):
        log = build_log(runs=runs, fail=fail, start=start)
        now = start + timedelta(hours=8 * runs + 1)
        report = build_report(log, max_gap_hours=36.0,
                              expected_subject=SUBJECT, now=now)
        gates = evaluate_gates(report, min_days=30, min_lifecycles=100)
        out = report["run_outcomes"]
        print(f"\n{label}")
        print(f"  run outcomes        : {out}")
        print(f"  days_continuous     : {report['tracking']['days_continuous']}")
        print(f"  complete lifecycles : {report['order_lifecycles']['complete']}")
        print(f"  evidence eligible   : {report['evidence']['eligible']}")
        for name, ok in gates.items():
            print(f"  gate {name:28s} {'PASS' if ok else 'FAIL'}")

    print()
    print("=" * 72)
    print("READING")
    print("=" * 72)
    print("""
A month of consecutive failures advances days_continuous exactly as a
healthy run does, because tracking_days counts run_failed as a terminal
event and never inspects which kind it was. evaluate_gates does not read
run_outcomes at all.

So the question this raises is not whether a failed run was noticed — the
report does surface outcomes["failed"] — but whether anything refuses to
admit the soak on the strength of that count.
""")


if __name__ == "__main__":
    main()