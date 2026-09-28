#!/usr/bin/env python3
"""Diagnose why both AC14 strategies lost money on the real-data window.

Separates four candidate causes:
  1. market direction  — was the window simply bad for long-only?
  2. cost drag        — how much did commission+slippage+spread consume?
  3. signal quality    — do the entries have any predictive edge at all?
  4. implementation    — timing, sizing, or accounting bugs
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.range_mean_reversion import RangeMeanReversionStrategy

COMMISSION = 0.001
SLIPPAGE = 0.0005
SPREAD_BPS = 2.0
WARMUP = 200
START = 19_800

df = (
    pl.read_parquet(ROOT / "data/raw/binance/BTC_USDT/1h_full.parquet")
    .sort("timestamp")
    .slice(START, 8_400)
)
if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
    df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))

opens = df["open"].to_numpy()
closes = df["close"].to_numpy()
n = len(opens) - WARMUP
o = opens[WARMUP:]
c = closes[WARMUP:]

print("=" * 70)
print("1. MARKET DIRECTION — is the window itself hostile to long-only?")
print("=" * 70)
bh_ret = (c[-1] / o[0] - 1) * 100
r1h = np.diff(c) / c[:-1]
r24 = (c[24:] - c[:-24]) / c[:-24]
sharpe_bh = float(np.mean(r1h) / np.std(r1h) * math.sqrt(8760)) if np.std(r1h) > 0 else 0
peak = np.maximum.accumulate(c)
mdd = abs(float(np.min((c - peak) / peak))) * 100
print(f"  window        : {df['timestamp'][WARMUP]} -> {df['timestamp'][-1]}")
print(f"  price         : {o[0]:.2f} -> {c[-1]:.2f}")
print(f"  buy&hold ret  : {bh_ret:+.2f}%")
print(f"  buy&hold DD   : {mdd:.2f}%")
print(f"  buy&hold Sharpe (1h ann.): {sharpe_bh:.3f}")
print(f"  24h return std: {float(np.std(r24)) * 100:.3f}%  "
      f"(annualised {float(np.std(r24)) * math.sqrt(365) * 100:.1f}%)")
up_bars = float((c[1:] > c[:-1]).mean())
print(f"  % up bars     : {up_bars * 100:.1f}%")

print()
print("=" * 70)
print("2. STRATEGY SIGNALS — do the entries carry any edge?")
print("=" * 70)

trend = MaCrossover(params={"fast_period": 10, "slow_period": 20})
mr = RangeMeanReversionStrategy(
    params={"vwap_window": 10, "zscore_entry": 1.5, "zscore_exit": 0.5,
            "bb_lookback": 10, "bb_std": 1.5,
            "rsi_oversold": 30, "rsi_overbought": 70}
)
sig_trend = trend.generate_signals(trend.compute_indicators(df)).to_numpy()[WARMUP:]
sig_mr = mr.generate_signals(mr.compute_indicators(df)).to_numpy()[WARMUP:]

for name, sig in (("ma_crossover", sig_trend), ("range_mr", sig_mr)):
    uniq = {int(v): int((sig == v).sum()) for v in np.unique(sig)}
    entries = int((sig[1:] > 0).sum() - (sig[:-1] > 0).sum())
    print(f"  {name:14s} value counts {uniq}  entries={entries}")


def simulate(signals, opens_arr, cost_bps=None):
    """Same accounting as the AC14 oracle, with cost scaling for attribution."""
    if cost_bps is None:
        cost_bps = (COMMISSION + SLIPPAGE) * 2 * 10_000 + SPREAD_BPS
    half = cost_bps / 2 / 10_000
    buy_f = 1.0 + half
    sell_f = 1.0 - half
    cash, qty, entry = 100_000.0, 0.0, 0.0
    pnls, n_sw = [], 0
    for i in range(len(opens_arr) - 1):
        s = int(signals[i])
        nxt = opens_arr[i + 1]
        if s > 0 and qty == 0.0:
            fp = nxt * buy_f
            qty = cash / fp
            cash -= qty * fp
            entry = fp
            n_sw += 1
        elif s <= 0 and qty > 0.0:
            fp = nxt * sell_f
            cash += qty * fp
            pnls.append(qty * (fp - entry))
            qty, entry = 0.0, 0.0
            n_sw += 1
    if qty > 0.0:
        fp = opens_arr[-1] * sell_f
        cash += qty * fp
        pnls.append(qty * (fp - entry))
        n_sw += 1
    eq = cash if qty == 0 else cash + qty * opens_arr[-1]
    return (eq - 100_000) / 100_000 * 100, pnls, n_sw


print()
print("=" * 70)
print("3. COST DRAG — return with real costs vs frictionless")
print("=" * 70)
for name, sig in (("ma_crossover", sig_trend), ("range_mr", sig_mr)):
    real, pnls, sw = simulate(sig, o, None)
    free, _, _ = simulate(sig, o, 0.0)
    print(f"  {name:14s} with costs {real:+7.2f}%   frictionless {free:+7.2f}%   "
          f"drag {real - free:+6.2f}pp   switches={sw}")
    if pnls:
        a = np.array(pnls)
        print(f"                 trades={len(a)} win_rate={float((a > 0).mean()) * 100:.1f}% "
              f"avg={a.mean():+,.0f} best={a.max():+,.0f} worst={a.min():+,.0f}")

print()
print("=" * 70)
print("4. IMPLEMENTATION — hold-period sanity + cost per round trip")
print("=" * 70)
round_trip_bps = (COMMISSION * 2 + SLIPPAGE * 2 + SPREAD_BPS / 10_000) * 10_000
print(f"  round-trip cost  : {round_trip_bps:.1f} bps = {round_trip_bps / 100:.3f}%")
print(f"  24h move needed  : > {round_trip_bps / 100:.3f}% just to break even")
for name, sig in (("ma_crossover", sig_trend), ("range_mr", sig_mr)):
    holds = []
    cur = 0
    for i, s in enumerate(sig):
        if s > 0:
            cur += 1
        elif cur:
            holds.append(cur)
            cur = 0
    if cur:
        holds.append(cur)
    if holds:
        h = np.array(holds)
        print(f"  {name:14s} holds={len(h)} median={np.median(h):.0f}h "
              f"mean={h.mean():.1f}h min={h.min()} max={h.max()}")
