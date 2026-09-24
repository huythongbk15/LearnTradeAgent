#!/usr/bin/env python3
"""Batch WFO runner for 7 Tier C strategies on BTC/USDT 1h.

Uses fast_wfo.py's run_fast_wfo() for each strategy and writes
consolidated evidence to docs/STRATEGY_SIGNOFF_P2PHASE4.md.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("POLARS_MAX_THREADS", "1")

from scripts.fast_wfo import run_fast_wfo

# Use catalog strategy IDs from trading_agent.research.strategy_catalog
# (S1 trend_pullback, S2 range_mean_reversion, S3 volatility_breakout,
#  S6 vol_target→ma_vol_target, S7 regime_ensemble→regime_switching)
TIER_C_STRATEGIES = [
    "trend_pullback",
    "range_mean_reversion",
    "volatility_breakout",
    "vol_target",
    "regime_ensemble",
]

# All assets from ResearchProtocol.ASSETS
TIER_C_ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME = "1h"
WFO_WORKERS = 4  # per ResearchProtocol
TIMEFRAME = "1h"
WFO_WORKERS = 1  # cell-level parallelism within each strategy


def main():
    results = {}
    start = time.time()

    for strat in TIER_C_STRATEGIES:
        # Resolve catalog code name
        from trading_agent.research.param_grids import get_strategy_code_name
        code_name = get_strategy_code_name(strat)

        for asset in TIER_C_ASSETS:
            print(f"\n{'='*60}")
            print(f"Running WFO: {strat} | {asset} | {TIMEFRAME}")
            print(f"{'='*60}")
            s = time.time()
            r = run_fast_wfo(code_name, asset, workers=WFO_WORKERS, timeframe=TIMEFRAME, limit_bars=0)
            elapsed = time.time() - s
            m = r["aggregate_metrics"]
            print(f"  {elapsed:.1f}s | Sharpe={m['median_test_sharpe']:.4f} | "
                  f"Trades={m['median_oos_trades']:.0f} | "
                  f"Ret={m['median_test_return_pct']:.2f}% | "
                  f"MaxDD={m['median_max_dd_pct']:.2f}% | {r['verdict']}")
            key = f"{strat}__{asset.replace('/', '_')}"
            results[key] = r
            results[key]["elapsed_seconds"] = round(elapsed, 1)

            # Save individual result
            out_dir = Path("data/backtests/fast_wfo") / "tier_c"
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_dir / f"{key}.json", "w") as f:
                json.dump(r, f, indent=2)

    elapsed_total = time.time() - start
    print(f"\n{'='*60}")
    print(f"All Tier C WFO complete in {elapsed_total:.1f}s")
    print(f"{'='*60}")
    print(f"\n{'Strategy':24} {'Asset':>12} {'Sharpe':>8} {'Trades':>8} {'MaxDD%':>8} "
          f"{'Return%':>8} {'Verdict':>8}")
    print("-" * 80)
    for key in sorted(results):
        strat_name, asset_name = key.rsplit("__", 1)
        r = results[key]
        m = r["aggregate_metrics"]
        print(f"{r['strategy'][:24]:24} {asset_name[:12]:>12} {m['median_test_sharpe']:>8.4f} "
              f"{m['median_oos_trades']:>8.0f} {m['median_max_dd_pct']:>8.2f} "
              f"{m['median_test_return_pct']:>8.2f} {r['verdict']:>8}")

    # Save summary
    summary = {
        "tier": "C",
        "assets": TIER_C_ASSETS,
        "timeframe": TIMEFRAME,
        "total_elapsed_seconds": round(elapsed_total, 1),
        "results": results,
    }
    out_dir = Path("data/backtests/fast_wfo") / "tier_c"
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return results


if __name__ == "__main__":
    main()
