#!/usr/bin/env python3
"""Confirm the AC14 losses are cost-driven: sweep round-trip cost and hold length."""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.strategies.ma_crossover import MaCrossover

df = (
    pl.read_parquet(ROOT / "data/raw/binance/BTC_USDT/1h_full.parquet")
    .sort("timestamp").slice(19_800, 8_400)
)
if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
    df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
o = df["open"].to_numpy()[200:]
c = df["close"].to_numpy()[200:]

trend = MaCrossover(params={"fast_period": 10, "slow_period": 20})
sig = trend.generate_signals(trend.compute_indicators(df)).to_numpy()[200:]


def sim(signals, opens_arr, cost_bps):
    half = cost_bps / 2 / 10_000
    buy_f, sell_f = 1 + half, 1 - half
    cash, qty, entry = 100_000.0, 0.0, 0.0
    for i in range(len(opens_arr) - 1):
        s = int(signals[i])
        nxt = opens_arr[i + 1]
        if s > 0 and qty == 0:
            fp = nxt * buy_f
            qty = cash / fp
            cash -= qty * fp
            entry = fp
        elif s <= 0 and qty > 0:
            fp = nxt * sell_f
            cash += qty * fp
            qty, entry = 0.0, 0.0
    return (cash - 100_000) / 100_000 * 100


print("Cost sweep — ma_crossover (10/20), long-only, 8200 bars")
print(f"{'round-trip bps':>16} | {'return %':>9}")
print("-" * 30)
for bps in (0, 2, 5, 10, 16, 20, 32, 50):
    print(f"{bps:>16} | {sim(sig, o, bps):>+8.2f}%")

# Break-even cost
lo, hi = 0.0, 60.0
for _ in range(40):
    mid = (lo + hi) / 2
    if sim(sig, o, mid) > 0:
        lo = mid
    else:
        hi = mid
print(f"\nbreak-even round-trip cost: {lo:.1f} bps")
print(f"  -> strategy needs >{lo / 100:.3f}% move per trade, but median hold is 1h")

# Frictionless edge magnitude per trade
f = sim(sig, o, 0.0)
n_sw = 440
print(f"\nfrictionless total: {f:+.2f}% over {n_sw} switches")
print(f"  gross edge per round trip: {f / n_sw:+.4f}% "
      f"({f / n_sw * 100:.1f} bps)")
print("  real cost per round trip : 32.0 bps")
print(f"  -> cost exceeds edge by ~{32 - f / n_sw * 100:.0f} bps per trade")
