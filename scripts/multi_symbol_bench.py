#!/usr/bin/env python
"""
Multi-symbol robustness benchmark — vectorized.
Tests a fixed MA+RSI strategy across all available symbols against buy-and-hold.
Answers: is edge symbol-specific, or is holding better?

Usage: python3 scripts/multi_symbol_bench.py [--timeframe 1h]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trading_agent.data.storage import load_ohlcv


# Strategy params (a mid-grid default — not optimized per symbol)
PARAMS = {"fast_ma": 15, "slow_ma": 50, "rsi_period": 14, "rsi_buy": 35, "rsi_sell": 65}
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]


def backtest_strategy(df: pl.DataFrame, p: dict) -> dict:
    close = pl.col("close")
    df = df.with_columns(
        [
            close.rolling_mean(p["fast_ma"]).alias("fast_ma"),
            close.rolling_mean(p["slow_ma"]).alias("slow_ma"),
            (close.rolling_mean(p["fast_ma"]) > close.rolling_mean(p["slow_ma"])).alias(
                "bullish"
            ),
            (
                100
                - 100
                / (
                    1
                    + (
                        close.diff().clip(lower_bound=0).rolling_mean(p["rsi_period"])
                        / -close.diff()
                        .clip(upper_bound=0)
                        .rolling_mean(p["rsi_period"])
                    )
                )
            ).alias("rsi"),
        ]
    )
    close = df["close"].to_numpy()
    bullish = df["bullish"].to_numpy().astype(bool)
    rsi = np.nan_to_num(df["rsi"].to_numpy(), nan=50.0)

    n = len(close)
    cross = np.zeros(n)
    cross[1:] = bullish[1:].astype(int) - bullish[:-1].astype(int)
    signal = np.zeros(n)
    signal[cross == 1] = 1
    signal[cross == -1] = -1
    signal[(signal == 1) & ~(rsi < p["rsi_buy"])] = 0
    signal[(signal == -1) & ~(rsi > p["rsi_sell"])] = 0

    position = np.zeros(n)
    in_mkt = 0
    for i in range(n):
        if signal[i] == 1 and not in_mkt:
            in_mkt = 1
        elif signal[i] == -1 and in_mkt:
            in_mkt = 0
        position[i] = in_mkt

    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1

    strat = 10000 * np.cumprod(1 + position * ret)
    bh = 10000 * np.cumprod(1 + ret)
    strat_ret = position * ret

    std = strat_ret.std()
    sharpe = (strat_ret.mean() / std * np.sqrt(24 * 365)) if std > 1e-12 else 0
    peak = np.maximum.accumulate(strat)
    dd = ((peak - strat) / peak * 100).max()
    bh_peak = np.maximum.accumulate(bh)
    bh_dd = ((bh_peak - bh) / bh_peak * 100).max()
    trades = int((signal == 1).sum())

    return {
        "strat_ret": (strat[-1] - 10000) / 10000 * 100,
        "bh_ret": (bh[-1] - 10000) / 10000 * 100,
        "sharpe": float(sharpe),
        "dd": float(dd),
        "bh_dd": float(bh_dd),
        "trades": trades,
        "beat_bh": strat[-1] > bh[-1],
        "n": n,
    }


def main(timeframe: str):
    t0 = time.time()
    print(
        f"{'Symbol':>10} {'Bars':>7} {'Strat%':>8} {'H&L%':>8} {'Sharpe':>7} {'DD%':>6} {'H&L_DD%':>7} {'Trades':>6} {'BeatH&L'}"
    )
    print("-" * 78)
    rows = []
    for sym in SYMBOLS:
        try:
            df = load_ohlcv("binance", sym, timeframe)
        except Exception as e:
            print(f"{sym:>10}  ERROR: {e}")
            continue
        if df.is_empty():
            print(f"{sym:>10}  no data")
            continue
        r = backtest_strategy(df, PARAMS)
        rows.append((sym.split("/")[0], r))
        print(
            f"{sym.split('/')[0]:>10} {r['n']:>7} {r['strat_ret']:>7.1f} {r['bh_ret']:>7.1f} "
            f"{r['sharpe']:>7.2f} {r['dd']:>5.1f} {r['bh_dd']:>7.1f} {r['trades']:>6} "
            f"{'YES' if r['beat_bh'] else 'no':>6}"
        )

    print("-" * 78)
    if rows:
        n_beat = sum(1 for _, r in rows if r["beat_bh"])
        avg_sharpe = np.mean([r["sharpe"] for _, r in rows])
        avg_strat = np.mean([r["strat_ret"] for _, r in rows])
        avg_bh = np.mean([r["bh_ret"] for _, r in rows])
        print(f"Beat buy-and-hold: {n_beat}/{len(rows)} symbols")
        print(f"Avg strategy return: {avg_strat:.1f}%  vs  buy-and-hold: {avg_bh:.1f}%")
        print(f"Avg strategy Sharpe: {avg_sharpe:.2f}")
    print(f"\nDone in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    main(ap.parse_args().timeframe)
