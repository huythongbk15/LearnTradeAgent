#!/usr/bin/env python3
"""Measure one WFO fold before committing to a two-run comparison.

Three attempts at the determinism check this session were each abandoned
after burning hours, for reasons only a cheap measurement would have shown:

  1. 4 minutes estimated, 4 hours observed — sensitivity analysis runs on
     every fold.
  2. A "minimal" 3m/1m/1m spec generated 40 folds, because shorter folds
     produce *more* folds.
  3. Swapping rsi for enhanced_ma so the spec would actually trade made it
     far slower, and the first pass closed 1 of 4 folds in 2.7 hours.

This runs a single fold, counts the trades it produces, and reports
wall-clock. That answers the only question that gates the comparison — does
this spec trade, and what does one fold cost — for the price of one fold
instead of eight.

Run: .venv/bin/python scripts/probe_wfo_fold_cost.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.backtest.nested_wfo import WFOSpec, _get_fold_indices, run_nested_wfo

CANDIDATES = [
    # (strategy, grid, label)
    ("enhanced_ma", {"fast_period": [20], "slow_period": [80]}, "enhanced_ma 1 combo"),
    ("ma_vol_target", {"fast_period": [20], "slow_period": [80]}, "ma_vol_target 1 combo"),
    ("rsi", {"period": [10]}, "rsi 1 combo"),
]


def main() -> None:
    n_bars = pl.read_parquet(
        ROOT / "data/raw/binance/BTC_USDT/1h.parquet", columns=["close"]
    ).height
    folds = _get_fold_indices(n_bars, "1h", 18, 3, 3, 6, 0, 0)
    print("=" * 74)
    print(f"WFO FOLD COST PROBE — {n_bars} bars, {len(folds)} folds at 18/3/3/6m")
    print("=" * 74)
    print(f"outer test span of fold 0: bars "
          f"{folds[0].outer_test_start}..{folds[0].outer_test_end}")
    print()

    out = []
    import tempfile

    for strategy, grid, label in CANDIDATES:
        with tempfile.TemporaryDirectory(prefix="fold_probe_") as td:
            td = Path(td)
            spec = WFOSpec(
                strategy_id=strategy,
                symbol="BTC/USDT",
                timeframe="1h",
                param_grid=grid,
                train_months=18,
                val_months=3,
                test_months=3,
                step_months=6,
                min_trades_per_fold=1,
                registry_path=str(td / "registry.sqlite3"),
            )
            print(f"--- {label} ---", flush=True)
            t0 = time.time()
            try:
                result = run_nested_wfo(
                    spec, out_root=td, real_sensitivity=False
                )
            except Exception as exc:
                elapsed = time.time() - t0
                print(f"  FAILED after {elapsed / 60:.1f} min: "
                      f"{type(exc).__name__}: {exc}\n")
                out.append({"label": label, "error": str(exc),
                            "minutes": round(elapsed / 60, 1)})
                continue
            elapsed = time.time() - t0

        agg = result.aggregate_metrics
        trades = int(agg.get("total_test_trades", 0) or 0)
        fold_dir = td / "outer_one_shot" / "BTC_USDT" / strategy
        n_folds_done = len(list(fold_dir.glob("fold_*"))) if fold_dir.exists() else 0
        per_fold = elapsed / max(n_folds_done, 1)

        print(f"  folds completed : {n_folds_done} of {len(folds)}")
        print(f"  total trades    : {trades}")
        print(f"  median sharpe   : {agg.get('median_test_sharpe')}")
        print(f"  median return % : {agg.get('median_test_return_pct')}")
        print(f"  wall clock      : {elapsed / 60:.1f} min "
              f"({per_fold / 60:.1f} min/fold)")
        verdict = "USABLE" if trades > 0 else "no trades — useless for determinism"
        print(f"  verdict         : {verdict}\n")
        out.append({
            "label": label, "strategy": strategy,
            "folds_done": n_folds_done, "folds_expected": len(folds),
            "total_trades": trades,
            "median_sharpe": agg.get("median_test_sharpe"),
            "median_return_pct": agg.get("median_test_return_pct"),
            "minutes": round(elapsed / 60, 1),
            "min_per_fold": round(per_fold / 60, 1),
            "usable": trades > 0,
        })

    usable = [r for r in out if r.get("usable")]
    print("=" * 74)
    print("DECISION INPUT")
    print("=" * 74)
    for r in out:
        if "error" in r:
            print(f"  {r['label']:26s} ERROR after {r['minutes']} min")
        else:
            print(f"  {r['label']:26s} trades={r['total_trades']:>4} "
                  f"{r['minutes']:>6} min ({r['min_per_fold']} min/fold) "
                  f"{'USABLE' if r['usable'] else 'no trades'}")
    if usable:
        best = min(usable, key=lambda r: r["minutes"])
        n = best["folds_expected"]
        print()
        print(f"  Cheapest spec that trades: {best['label']}")
        print(f"  A full two-run comparison costs roughly "
              f"{best['minutes'] * 2:.0f} min (~{best['minutes'] * 2 / 60:.1f} h).")
    else:
        print("  No candidate traded. A determinism comparison on these windows")
        print("  cannot be made with the strategies available, which is itself")
        print("  the finding: the registry does not trade on BTC/USDT 1h folds.")
    print("=" * 74)

    json.dump(out, open("/tmp/wfo_fold_cost.json", "w"), indent=2, default=str)
    print("evidence: /tmp/wfo_fold_cost.json")


if __name__ == "__main__":
    main()
