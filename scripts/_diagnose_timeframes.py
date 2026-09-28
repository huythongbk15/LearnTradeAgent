#!/usr/bin/env python3
"""Compare ma_crossover cost viability across timeframes (1h/4h/1d).

Hypothesis from AC14_LOSS_DIAGNOSIS: the 1h run lost because 440 round trips
over 8200 bars (median 1h hold) put 32bps of cost against a ~2.5bps edge.
Higher timeframes cut the trade count, so the same edge should survive costs.
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

COST_BPS = 32.0
WARMUP = 200
WINDOW_BARS_1H = 8_400  # same calendar span the AC14 real run used


def load_1h() -> pl.DataFrame:
    df = pl.read_parquet(ROOT / "data/raw/binance/BTC_USDT/1h_full.parquet").sort("timestamp")
    if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    return df


def resample(df: pl.DataFrame, rule: str) -> pl.DataFrame:
    return (
        df.sort("timestamp")
        .group_by_dynamic("timestamp", every=rule, closed="left", label="left")
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
        )
        .sort("timestamp")
    )


def sim(signals, opens_arr, cost_bps):
    half = cost_bps / 2 / 10_000
    buy_f, sell_f = 1 + half, 1 - half
    cash, qty, entry, n_sw, pnls = 100_000.0, 0.0, 0.0, 0, []
    eq_curve = np.empty(len(opens_arr))
    eq_curve[0] = 100_000.0
    for i in range(len(opens_arr) - 1):
        s = int(signals[i])
        nxt = opens_arr[i + 1]
        if s > 0 and qty == 0:
            fp = nxt * buy_f
            qty = cash / fp
            cash -= qty * fp
            entry = fp
            n_sw += 1
        elif s <= 0 and qty > 0:
            fp = nxt * sell_f
            cash += qty * fp
            pnls.append(qty * (fp - entry))
            qty, entry = 0.0, 0.0
            n_sw += 1
        eq_curve[i + 1] = cash + (qty * opens_arr[i + 1] if qty > 0 else 0.0)
    if qty > 0:
        fp = opens_arr[-1] * sell_f
        cash += qty * fp
        pnls.append(qty * (fp - entry))
        n_sw += 1
    eq_curve[-1] = cash
    rets = np.diff(eq_curve) / np.maximum(eq_curve[:-1], 1e-12)
    rets = np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)
    sharpe = float(np.mean(rets) / np.std(rets) * math.sqrt(8760)) if np.std(rets) > 1e-12 else 0.0
    peak = np.maximum.accumulate(eq_curve)
    mdd = abs(float(np.min((eq_curve - peak) / np.maximum(peak, 1e-12)))) * 100
    return {
        "ret": (cash - 100_000) / 100_000 * 100,
        "sharpe": sharpe,
        "mdd": mdd,
        "switches": n_sw,
        "trades": len(pnls),
    }


def period_hours(rule: str) -> int:
    return {"1h": 1, "4h": 4, "1d": 24}[rule]


print("=" * 78)
print("ma_crossover(10/20) across timeframes — same calendar window (2022-04..2023-03)")
print("=" * 78)
print(f"{'TF':<5} {'bars':>6} {'ret@32bps':>11} {'ret@0':>9} {'sharpe':>8} {'DD':>7} {'sw':>5} {'trades':>7} {'medHold':>8}")

base = load_1h()
for rule in ("1h", "4h", "1d"):
    df = base if rule == "1h" else resample(base, rule)
    # Same calendar span as the AC14 real run: 8400 1h bars.
    span = WINDOW_BARS_1H if rule == "1h" else WINDOW_BARS_1H * 1 / period_hours(rule)
    span = int(span)
    if df.height < span + WARMUP:
        print(f"{rule:<5} SKIP (only {df.height} bars available)")
        continue
    start = 19_800 // period_hours(rule) - WARMUP
    w = df.slice(start, span + WARMUP)
    o = w["open"].to_numpy()[WARMUP:]
    s = MaCrossover(params={"fast_period": 10, "slow_period": 20})
    sig = s.generate_signals(s.compute_indicators(w)).to_numpy()[WARMUP:]

    real = sim(sig, o, COST_BPS)
    free = sim(sig, o, 0.0)

    holds, cur = [], 0
    for v in sig:
        if v > 0:
            cur += 1
        elif cur:
            holds.append(cur)
            cur = 0
    if cur:
        holds.append(cur)
    med = np.median(holds) if holds else 0

    print(f"{rule:<5} {len(o):>6} {real['ret']:>+10.2f}% {free['ret']:>+8.2f}% "
          f"{real['sharpe']:>8.3f} {real['mdd']:>6.1f}% {real['switches']:>5} {real['trades']:>7} {med:>7.0f}")

# Buy & hold reference for the same span
o1h = base.slice(19_800, WINDOW_BARS_1H)["open"].to_numpy()[WARMUP:]
c1h = base.slice(19_800, WINDOW_BARS_1H)["close"].to_numpy()[WARMUP:]
print(f"\nBuy & hold (same window): {(c1h[-1] / o1h[0] - 1) * 100:+.2f}%")
