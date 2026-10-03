#!/usr/bin/env python3
"""Can any strategy in this registry fill a walk-forward test window?

The 32-fold campaign on daily bars failed with only 5 of 23 folds
containing a trade. The cause is structural: a 2-month test window is 61
daily bars, and enhanced_ma produces 15 entries across the entire 2,456-bar
file, so 99 of 160 sampled windows contain none.

That decides whether the registry can satisfy the spread gate at all. The
gate needs strategy clears in 19 of 32 folds. If a strategy emits no entry
in most windows, it cannot clear a window, so it cannot clear the gate —
regardless of how good the strategy is.

This measures entry density per window for every strategy at both the daily
window size and the hourly one, and states which, if any, could pass.

Run: .venv/bin/python scripts/probe_registry_window_density.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl


STRATEGIES = [
    ("ma_crossover", "ma_crossover", "MaCrossover"),
    ("enhanced_ma", "enhanced_ma", "EnhancedMaCrossover"),
    ("ma_adx", "enhanced_ma", "MaAdxCrossover"),
    ("ma_vol_target", "enhanced_ma", "MaVolTargetCrossover"),
    ("ensemble_ma_adx", "enhanced_ma", "EnsembleMaAdx"),
    ("rsi", "rsi", "RsiStrategy"),
    ("bbands", "bbands", "BBandsStrategy"),
    ("volatility_breakout", "volatility_breakout", "VolatilityBreakoutStrategy"),
    ("trend_pullback", "trend_pullback", "TrendPullbackStrategy"),
    ("range_mean_reversion", "range_mean_reversion", "RangeMeanReversionStrategy"),
    ("regime_switching", "regime_switching", "RegimeSwitchingStrategy"),
    ("ma_adx_regime", "enhanced_ma", "MaAdxRegimeAware"),
]

P_MAX = 0.20


def density(symbol: str, timeframe: str, window: int, stride: int) -> dict:
    """Entries per test window for each strategy."""
    path = ROOT / "data" / "raw" / "binance" / symbol / f"{timeframe}.parquet"
    df = pl.read_parquet(path)
    n = df.height
    starts = list(range(0, max(n - window, 1), stride))
    out = {}
    for sid, module, cls_name in STRATEGIES:
        try:
            cls = getattr(
                importlib.import_module(f"trading_agent.strategies.{module}"),
                cls_name,
            )
            sig = cls().generate_signals(cls().compute_indicators(df)).to_numpy()
        except Exception:
            continue
        entries = {i for i in range(1, len(sig)) if sig[i] > 0 and sig[i - 1] <= 0}
        counts = [sum(1 for e in entries if s <= e < s + window) for s in starts]
        empty = sum(1 for c in counts if c == 0)
        out[sid] = {
            "total_entries": len(entries),
            "windows": len(starts),
            "empty_pct": empty / len(starts) * 100 if starts else 100.0,
            "median_per_window": sorted(counts)[len(counts) // 2] if counts else 0,
        }
    return out


def main() -> None:
    print("=" * 76)
    print("REGISTRY WINDOW DENSITY — can anything fill a walk-forward window?")
    print("=" * 76)

    configs = [
        ("BTC_USDT", "1d", 61, 15, "daily, 2-month window"),
        ("BTC_USDT", "1h", 61, 15, "hourly, ~2.5-day window"),
        ("BTC_USDT", "4h", 61, 15, "4h, ~10-day window"),
    ]

    viable = []
    for symbol, timeframe, window, stride, label in configs:
        data = density(symbol, timeframe, window, stride)
        if not data:
            continue
        print(f"\n{symbol} {timeframe} — {label} "
              f"({window} bars, {data[next(iter(data))]['windows']} windows)")
        print(f"  {'strategy':24s} {'entries':>8} {'empty%':>8} {'med/win':>8}")
        print("  " + "-" * 50)
        for sid, d in sorted(data.items(), key=lambda kv: kv[1]["empty_pct"]):
            print(f"  {sid:24s} {d['total_entries']:>8} "
                  f"{d['empty_pct']:>7.1f}% {d['median_per_window']:>8}")
            if d["empty_pct"] <= 50:
                viable.append((symbol, timeframe, sid, d))

    print()
    print("=" * 76)
    print("VERDICT")
    print("=" * 76)
    if not viable:
        print("No strategy fills half the test windows on any configuration.")
        print("\nThe spread gate requires clearing 19 of 32 folds. A strategy")
        print("that emits no entry in most windows cannot clear a window at")
        print("all, so no amount of parameter tuning changes the outcome.")
        print("\nWhat this means for going live:")
        print("  - The registry is not usable with this test-window design.")
        print("  - Either the window must grow until low-frequency strategies")
        print("    can fill it, which cuts fold count below the gate minimum,")
        print("  - or the registry needs strategies whose signal frequency")
        print("    matches a 61-bar window.")
        print("\nThe arithmetic is the binding constraint: 61-bar windows,")
        print("14 folds minimum, and this registry's entry density.")
    else:
        print("Strategies dense enough to fill a window:")
        for symbol, tf, sid, d in viable:
            print(f"  {sid:24s} {symbol} {tf}  "
                  f"empty {d['empty_pct']:.0f}%  median {d['median_per_window']}/window")


if __name__ == "__main__":
    main()