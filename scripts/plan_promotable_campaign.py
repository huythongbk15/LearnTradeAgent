#!/usr/bin/env python3
"""Plan a campaign that the spread gate can actually resolve.

The gate needs enough folds to distinguish an edge from luck. At p <= 0.20:

  n=10  needs 7/10 (70.0%)   n=20  needs 13/20 (65.0%)
  n=14  needs 10/14 (71.4%)  n=22  needs 14/22 (63.6%)
  n=15  needs 10/15 (66.7%)  n=33  needs 20/33 (60.6%)

The first attempt at this used 18/3/3/6 and produced 4 folds on 1h, which
cannot pass at any clearing rate. This picks the fold structure from the
data actually on disk instead of guessing, and reports the bar cost of each
option before anything is run.

Run: .venv/bin/python scripts/plan_promotable_campaign.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.backtest.nested_wfo import _binomial_upper_tail, _get_fold_indices

BARS_PER_YEAR = {"1h": 8760, "4h": 2190, "1d": 365}
P_MAX = 0.20
MIN_FOLDS = 14

# Candidate fold structures, most conservative first.
CONFIGS = [
    ("12/3/3/3", 12, 3, 3, 3),
    ("12/2/2/2", 12, 2, 2, 2),
    ("9/3/3/3", 9, 3, 3, 3),
    ("18/3/3/6", 18, 3, 3, 6),
]

# Purge and embargo equal the slowest feature lookback the registry uses.
# ma_crossover defaults to slow_period=80; enhanced_ma to 80 plus an ATR
# stop. On 1h that is ~80 bars, so purge=embargo=100 avoids overlapping
# feature windows between train and test.
PURGE = 100


def required_k(n: int) -> int | None:
    for k in range(n + 1):
        if _binomial_upper_tail(k, n) <= P_MAX:
            return k
    return None


def main() -> None:
    print("=" * 78)
    print("CAMPAIGN PLAN — fold structures the spread gate can resolve")
    print(f"gate: cost-clearing folds above chance at p <= {P_MAX}, n >= {MIN_FOLDS}")
    print("=" * 78)

    viable: list[dict] = []

    for symbol in ("BTC_USDT", "ETH_USDT", "SOL_USDT"):
        for tf in ("1h", "4h", "1d"):
            path = ROOT / "data" / "raw" / "binance" / symbol / f"{tf}.parquet"
            if not path.exists():
                continue
            n_bars = pl.read_parquet(path, columns=["close"]).height
            years = n_bars / BARS_PER_YEAR[tf]

            print(f"\n{symbol} {tf}  ({n_bars:,} bars, {years:.1f} years)")
            for label, tr, va, te, st in CONFIGS:
                purge = min(PURGE, int(BARS_PER_YEAR[tf] * 0.5 / 24 * 24))
                purge = PURGE if tf == "1h" else max(PURGE // 4, 1)
                folds = _get_fold_indices(n_bars, tf, tr, va, te, st, purge, purge)
                k = required_k(len(folds)) if folds else None
                ok = len(folds) >= MIN_FOLDS
                mark = "VIABLE" if ok else "too few"
                rate = f"{k}/{len(folds)}" if k else "-"
                need = f"{k / len(folds) * 100:.0f}%" if k and folds else "-"
                print(f"    {label:12s} purge={purge:<4} folds={len(folds):>3}  "
                      f"need {rate:>7} ({need:>5})  {mark}")
                if ok and k:
                    viable.append({
                        "symbol": symbol, "timeframe": tf, "config": label,
                        "train": tr, "val": va, "test": te, "step": st,
                        "purge": purge, "folds": len(folds), "need_k": k,
                        "need_rate": k / len(folds),
                    })

    print("\n" + "=" * 78)
    print("VIABLE COMBINATIONS")
    print("=" * 78)
    if not viable:
        print("none — the data on disk cannot resolve the spread gate")
        return
    for v in sorted(viable, key=lambda x: (-x["folds"], x["need_rate"])):
        print(f"  {v['symbol']:10s} {v['timeframe']:4s} {v['config']:12s} "
              f"{v['folds']:>3} folds  need {v['need_k']}/{v['folds']} "
              f"({v['need_rate'] * 100:.0f}% clearing)")

    best = max(viable, key=lambda x: x["folds"])
    print(f"\nRecommended: {best['symbol']} {best['timeframe']} "
          f"{best['config']} — {best['folds']} folds")
    print(f"  A strategy must clear costs in {best['need_k']} of "
          f"{best['folds']} folds ({best['need_rate'] * 100:.0f}%) to pass.")

    print("\nCost estimate")
    print("  Each fold runs the inner grid then one outer evaluation. The")
    print("  104-cell run earlier took ~10 hours; per-cell cost scales with")
    print("  trades, so the cheapest useful plan is one strategy, one")
    print("  parameter set, sensitivity off. Estimate 2-4 hours for "
          f"{best['folds']} folds.")

    print("\nBefore running")
    print("  1. Gate semantics: p<=0.20 and n>=14 are decisions, not")
    print("     measurements. Confirm they are the bar you want held.")
    print("  2. The strategy choice matters more than the symbol: the")
    print("     sign-off ranked 16 and rejected most. Start with the")
    print("     highest-ranked one that is not on the quarantine list.")


if __name__ == "__main__":
    main()