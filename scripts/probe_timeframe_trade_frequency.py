#!/usr/bin/env python3
"""How many trades per window does enhanced_ma produce at each timeframe?

The hypothesis under test is that a higher timeframe fixes the binding
constraint. The constraint is `median_trades_per_trading_fold_ge_20`, and
a fold is a 3-month window. MA 20/80 is expressed in bars, so:

  1h: 80 bars = 3.3 days, 3 months = ~2,160 bars
  4h: 80 bars = 13.3 days, 3 months = ~540 bars
  1d: 80 bars = 80 days, 3 months = ~90 bars

If crossovers scale with bar count, 4h produces a quarter of the trades of
1h in the same window and the hypothesis is backwards. This measures the
actual signal frequency before spending ten hours on a campaign to find
out.

Counting signals, not P&L: the question is purely how often the strategy
enters.
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

SYMBOLS = ("BTC_USDT", "ETH_USDT", "SOL_USDT")
TIMEFRAMES = ("1h", "4h", "1d")
FOLD_MONTHS = 3


def entry_bars_per_window(path: Path, timeframe: str) -> tuple[int, int, float]:
    """Return (entries, windows, entries_per_window) at one timeframe."""
    df = (
        pl.read_parquet(path)
        .sort("timestamp")
        .filter(pl.col("close").is_not_null())
    )
    if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))

    strat = EnhancedMaCrossover()
    sig = strat.generate_signals(strat.compute_indicators(df)).to_numpy()
    is_long = sig > 0
    # An entry is a 0 -> 1 transition, matching how the oracle counts trades.
    entries = int(((is_long[1:]) & (~is_long[:-1])).sum())

    bars_per_month = {"1h": 730.0, "4h": 182.5, "1d": 30.4}[timeframe]
    window_bars = int(bars_per_month * FOLD_MONTHS)
    n_windows = max(df.height // window_bars, 1)
    return entries, n_windows, entries / n_windows


def main() -> None:
    print(f"enhanced_ma default params (MA 20/80) — entry frequency per "
          f"{FOLD_MONTHS}-month window")
    print("=" * 72)
    print(f"{'symbol':10s} {'TF':4s} {'bars':>7} {'window bars':>12} "
          f"{'entries':>8} {'windows':>8} {'per window':>11}")
    print("-" * 72)

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            path = ROOT / "data" / "raw" / "binance" / sym / f"{tf}.parquet"
            if not path.exists():
                print(f"{sym:10s} {tf:4s}  (no data)")
                continue
            entries, windows, per = entry_bars_per_window(path, tf)
            df_rows = pl.read_parquet(path, columns=["close"]).height
            print(f"{sym:10s} {tf:4s} {df_rows:>7} "
                  f"{int(df_rows / windows):>12} {entries:>8} {windows:>8} "
                  f"{per:>11.2f}")
        print()

    print("=" * 72)
    print("The promotion gate requires median_trades_per_trading_fold >= 20.")
    print("Measured 1h medians were 9-10 trades per window (SPREAD_GATES_")
    print("BY_SYMBOL.md), so the strategy is about half the floor there.")
    print("Read the per-window column: if 4h is below 1h rather than above,")
    print("the timeframe hypothesis is backwards and the constraint is not")
    print("where it was assumed to be.")
    print("=" * 72)


if __name__ == "__main__":
    main()
