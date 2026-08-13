#!/usr/bin/env python3
"""Measure paper/testnet execution tracking error (P2).

Computes realized slippage (tracking error) between the signal price at
decision time and the actual average fill price, per symbol and overall,
from `order_filled` audit events:

    slippage_bps = (average_fill_price - signal_price) / signal_price * 10_000

A BUY filling above its signal is positive (slippage); a SELL filling below
its signal is also slippage, so sign-normalized by side
(SELL slippage = -slippage_bps). The report is written as JSON for the P3
tracking-error gate:

    python scripts/measure_tracking_error.py --audit-log data/execution/binance_live_audit.jsonl
    python scripts/measure_tracking_error.py --max-mean-slippage-bps 5 --check

Exit codes: 0 = measured within limits (or nothing to measure); 1 = measured
tracking error exceeds the approved limit; 2 = cannot measure (no fill data).
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

_REFERENCE_KEYS = ("signal_price", "reference_price")


def load_fills(audit_path: Path) -> list[dict]:
    """Collect order_filled events that carry both average_fill_price and signal_price."""
    fills: list[dict] = []
    if not audit_path.exists():
        return fills
    with audit_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("event") != "order_filled":
                continue
            details = payload.get("details") or {}
            fill_price = details.get("average_fill_price")
            signal_price = next(
                (
                    details.get(key)
                    for key in _REFERENCE_KEYS
                    if details.get(key) is not None
                ),
                None,
            )
            if fill_price is None or signal_price is None:
                continue
            try:
                fill_price_f = float(fill_price)
                signal_price_f = float(signal_price)
            except (TypeError, ValueError):
                continue
            if fill_price_f <= 0 or signal_price_f <= 0:
                continue
            fills.append(
                {
                    "timestamp": payload.get("timestamp", ""),
                    "symbol": details.get("symbol", "?"),
                    "side": details.get("side", "?"),
                    "filled_qty": details.get("filled_qty", 0.0),
                    "average_fill_price": fill_price_f,
                    "signal_price": signal_price_f,
                    "slippage_bps": _slippage_bps(
                        fill_price_f, signal_price_f, details.get("side")
                    ),
                }
            )
    return fills


def _slippage_bps(fill_price: float, signal_price: float, side: object) -> float:
    raw = (fill_price - signal_price) / signal_price * 10_000.0
    if str(side).upper() == "SELL":
        return -raw
    return raw


def summarize(fills: list[dict]) -> dict:
    if not fills:
        return {"fills": 0, "mean_slippage_bps": None, "median_slippage_bps": None}
    values = [fill["slippage_bps"] for fill in fills]
    by_symbol: dict[str, list[float]] = {}
    for fill in fills:
        by_symbol.setdefault(fill["symbol"], []).append(fill["slippage_bps"])
    return {
        "fills": len(fills),
        "mean_slippage_bps": statistics.fmean(values),
        "median_slippage_bps": statistics.median(values),
        "p95_slippage_bps": _percentile(values, 95),
        "per_symbol": {
            symbol: {
                "fills": len(v),
                "mean_slippage_bps": statistics.fmean(v),
                "median_slippage_bps": statistics.median(v),
            }
            for symbol, v in sorted(by_symbol.items())
        },
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, round(len(ordered) * percentile / 100.0 - 0.5)))
    return ordered[idx]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit-log", default="data/execution/binance_live_audit.jsonl"
    )
    parser.add_argument("--report", default="data/tracking_error_report.json")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when mean exceeds limit"
    )
    parser.add_argument("--max-mean-slippage-bps", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    fills = load_fills(Path(args.audit_log))
    report = summarize(fills)
    report["max_mean_slippage_bps"] = args.max_mean_slippage_bps
    report["gate"] = (
        "PASS"
        if report["fills"] == 0
        or report["mean_slippage_bps"] is None
        or report["mean_slippage_bps"] <= args.max_mean_slippage_bps
        else "FAIL"
    )
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if report["fills"] == 0:
        print(
            f"no measurable fills in {args.audit_log} (need signal_price + avg_fill_price)"
        )
        return 0 if not args.check else 2
    print(
        f"tracking error: {report['fills']} fills, mean {report['mean_slippage_bps']:.2f} bps, "
        f"median {report['median_slippage_bps']:.2f} bps, p95 {report['p95_slippage_bps']:.2f} bps"
    )
    for symbol, stat in report["per_symbol"].items():
        print(
            f"  {symbol}: {stat['fills']} fills, mean {stat['mean_slippage_bps']:.2f} bps, "
            f"median {stat['median_slippage_bps']:.2f} bps"
        )
    if args.check and report["gate"] == "FAIL":
        print(
            f"GATE FAIL: mean slippage {report['mean_slippage_bps']:.2f} bps > {args.max_mean_slippage_bps} bps"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
