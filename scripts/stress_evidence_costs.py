#!/usr/bin/env python3
"""Stress-test evidence against 1x/2x/3x execution costs (P2).

Runs the exact live strategy evidence pipeline at increasing commission /
slippage / spread multipliers and reports how much edge survives.  A strategy
that only works at 1x costs is fragile: it must stay profitable (or at least
bounded) at 2x-3x before it can be trusted.

Usage:
  python scripts/stress_evidence_costs.py
  python scripts/stress_evidence_costs.py --symbols BTC/USDT,SOL/USDT --folds 4
  python scripts/stress_evidence_costs.py --multipliers 1,2,3,4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import polars as pl

from generate_live_strategy_evidence import (
    COMMISSION,
    SLIPPAGE,
    SPREAD_BPS,
    build_portfolio_folds,
    evaluate_symbol,
    fold_ranges,
)
from live_config import TIMEFRAME
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.live_safety import LiveSafetyError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTC/USDT,SOL/USDT,AVAX/USDT")
    parser.add_argument("--weights", default="4,3,3")
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--fold-days", type=int, default=90)
    parser.add_argument("--multipliers", default="1,2,3")
    parser.add_argument("--json", default="data/cost_stress_report.json")
    return parser


def _as_utc(value) :
    from datetime import UTC

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def run(args: argparse.Namespace) -> int:
    symbols = [v.strip().upper() for v in args.symbols.split(",") if v.strip()]
    weights = [float(v) / 100.0 for v in args.weights.split(",")]
    if len(symbols) != len(weights):
        raise LiveSafetyError("--weights must match --symbols count")
    allocations = dict(zip(symbols, weights, strict=True))
    multipliers = [float(v) for v in args.multipliers.split(",") if v.strip()]
    if not multipliers or any(m <= 0 for m in multipliers):
        raise LiveSafetyError("--multipliers must be positive numbers")

    frames: dict[str, pl.DataFrame] = {}
    data_ends = []
    for symbol in symbols:
        frame = load_ohlcv("binance", symbol, TIMEFRAME)
        if frame.is_empty():
            raise LiveSafetyError(f"{symbol} data is empty")
        frames[symbol] = frame
        data_ends.append(_as_utc(frame["timestamp"].max()))
    common_end = min(data_ends)
    ranges = fold_ranges(common_end, args.folds, args.fold_days)

    report: dict[str, object] = {
        "strategy": "enhanced_ma",
        "base_costs": {
            "commission_bps": COMMISSION * 10_000,
            "slippage_bps": SLIPPAGE * 10_000,
            "spread_bps": SPREAD_BPS,
        },
        "folds": args.folds,
        "fold_days": args.fold_days,
        "symbols": symbols,
        "allocations": allocations,
        "runs": {},
    }
    for mult in multipliers:
        commission = COMMISSION * mult
        slippage = SLIPPAGE * mult
        spread = SPREAD_BPS * mult
        symbol_results: dict[str, dict] = {}
        curves: dict[str, list[pl.DataFrame]] = {}
        for symbol in symbols:
            folds, curves[symbol] = evaluate_symbol(
                frames[symbol],
                symbol,
                ranges,
                allocations[symbol],
                commission=commission,
                slippage=slippage,
                spread_bps=spread,
            )
            symbol_results[symbol] = {"allocation": allocations[symbol], "folds": folds}
        portfolio_folds, _ = build_portfolio_folds(symbol_results, curves)
        med_sharpe = sorted(f["sharpe"] for f in portfolio_folds)[len(portfolio_folds) // 2]
        worst_dd = max(abs(f["max_drawdown_pct"]) for f in portfolio_folds)
        total_return = sum(
            (1 + f["return_pct"] / 100.0 for f in portfolio_folds),
            0.0,
        )
        report["runs"][f"{mult}x"] = {
            "commission_bps": round(commission * 10_000, 2),
            "slippage_bps": round(slippage * 10_000, 2),
            "spread_bps": round(spread, 2),
            "median_fold_sharpe": round(med_sharpe, 3),
            "worst_fold_drawdown_pct": round(worst_dd, 2),
            "sum_return_pct": round((total_return - 1.0) * 100, 2),
            "trades_per_fold": [f["trades"] for f in portfolio_folds],
        }
        label = f"{mult}x"
        print(
            f"{label:>4}: med Sharpe {med_sharpe:+.2f} | "
            f"worst DD {worst_dd:.1f}% | "
            f"sum return {((total_return - 1.0) * 100):+.1f}% | "
            f"trades/fold {report['runs'][label]['trades_per_fold']}"
        )

    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    print(f"report: {args.json}")
    return 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
