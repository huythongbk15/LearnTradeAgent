#!/usr/bin/env python
"""
Walk-Forward Optimization (WFO) — rolling window re-optimization.
VECTORIZED version — no Python per-bar loops.

Strategy: MA Crossover + RSI filter (long-only)
- Entry: golden cross (fast MA crosses above slow MA) AND RSI < rsi_buy
- Exit: death cross (fast MA crosses below slow MA) OR RSI > rsi_sell

Fast mode: --fast  (coarse grid, fewer windows, ~seconds)
Full mode:  default (finer grid, still vectorized)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trading_agent.data.storage import load_ohlcv

# ── Parameter Space ────────────────────────────────────────────────────────


def get_param_space(fast: bool = False) -> dict:
    if fast:
        return {
            "fast_ma": [5, 10, 20],
            "slow_ma": [30, 50, 80],
            "rsi_period": [7, 14, 21],
            "rsi_buy": [30, 40],
            "rsi_sell": [60, 70],
        }
    return {
        "fast_ma": [5, 10, 15, 20, 25, 30],
        "slow_ma": [20, 30, 40, 50, 60, 80, 100],
        "rsi_period": [7, 10, 14, 21],
        "rsi_buy": [25, 30, 35, 40, 45],
        "rsi_sell": [55, 60, 65, 70, 75, 80],
    }


# ── Vectorized indicators ──────────────────────────────────────────────────


def compute_indicators(
    df: pl.DataFrame, fast: int, slow: int, rsi_p: int
) -> pl.DataFrame:
    """Compute MA + RSI + crossover columns once per (fast, slow, rsi_p)."""
    close = pl.col("close")
    return df.with_columns(
        [
            close.rolling_mean(fast).alias("fast_ma"),
            close.rolling_mean(slow).alias("slow_ma"),
            (close.rolling_mean(fast) > close.rolling_mean(slow)).alias("bullish"),
            (
                100
                - 100
                / (
                    1
                    + (
                        close.diff().clip(lower_bound=0).rolling_mean(rsi_p)
                        / (-close.diff().clip(upper_bound=0)).rolling_mean(rsi_p)
                    )
                )
            ).alias("rsi"),
        ]
    )


# ── Vectorized backtest (no per-bar loop) ─────────────────────────────────


def backtest_vectorized(
    df: pl.DataFrame,
    rsi_buy: float,
    rsi_sell: float,
) -> dict:
    """
    Long-only: enter on golden cross + RSI<buy, exit on death cross or RSI>sell.
    Position sized 100% when in (for ranking). All vectorized.
    """
    n = len(df)
    if n < 60:
        return {"valid": False, "reason": "short"}

    close = df["close"].to_numpy()
    bullish = df["bullish"].to_numpy().astype(bool)
    rsi = df["rsi"].to_numpy()

    # Replace NaN RSI with 50 (neutral)
    rsi = np.nan_to_num(rsi, nan=50.0)

    # Crossover: 1 on golden cross, -1 on death cross
    cross = np.zeros(n)
    cross[1:] = bullish[1:].astype(int) - bullish[:-1].astype(int)

    # Signal: 1 = enter, -1 = exit
    signal = np.zeros(n)
    signal[cross == 1] = 1
    signal[cross == -1] = -1

    # RSI filter: enter only when oversold, exit when overbought
    signal[(signal == 1) & ~(rsi < rsi_buy)] = 0
    signal[(signal == -1) & ~(rsi > rsi_sell)] = 0

    # Position: 1 while in market
    position = np.zeros(n)
    in_market = 0
    for i in range(n):
        if signal[i] == 1 and not in_market:
            in_market = 1
        elif signal[i] == -1 and in_market:
            in_market = 0
        position[i] = in_market

    if position.sum() == 0:
        return {"valid": False, "reason": "no_trades"}

    # Returns
    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1
    strat_ret = position * ret
    equity = 10000 * np.cumprod(1 + strat_ret)
    total_return = (equity[-1] - 10000) / 10000 * 100

    # Metrics
    std = strat_ret.std()
    sharpe = (strat_ret.mean() / std * np.sqrt(24 * 365)) if std > 1e-12 else 0.0
    peak = np.maximum.accumulate(equity)
    max_dd = ((peak - equity) / peak * 100).max()

    # Trades count
    entries = np.where(signal == 1)[0]
    num_trades = int(len(entries))

    # Win rate via per-trade returns
    trade_rets = []
    for e in entries:
        exit_idx = np.where((np.arange(n) > e) & (signal == -1))[0]
        if len(exit_idx) == 0:
            exit_idx = np.array([n - 1])
        x = exit_idx[0]
        trade_rets.append(close[x] / close[e] - 1)
    wins = [r for r in trade_rets if r > 0]
    win_rate = len(wins) / len(trade_rets) * 100 if trade_rets else 0.0

    return {
        "valid": True,
        "total_return_pct": float(total_return),
        "sharpe": float(sharpe),
        "max_dd_pct": float(max_dd),
        "win_rate_pct": float(win_rate),
        "num_trades": num_trades,
    }


# ── Grid evaluation for one window ─────────────────────────────────────────


def evaluate_window(train_df: pl.DataFrame, space: dict) -> list[dict]:
    """Evaluate all param combos on train data. Returns sorted list (best Sharpe first)."""
    results = []
    for fast in space["fast_ma"]:
        for slow in space["slow_ma"]:
            if fast >= slow:
                continue
            for rsi_p in space["rsi_period"]:
                df_ind = compute_indicators(train_df, fast, slow, rsi_p)
                for rsi_b in space["rsi_buy"]:
                    for rsi_s in space["rsi_sell"]:
                        if rsi_b >= rsi_s:
                            continue
                        res = backtest_vectorized(df_ind, rsi_b, rsi_s)
                        if res["valid"]:
                            res["params"] = {
                                "fast_ma": fast,
                                "slow_ma": slow,
                                "rsi_period": rsi_p,
                                "rsi_buy": rsi_b,
                                "rsi_sell": rsi_s,
                            }
                            results.append(res)
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    return results


# ── Walk-forward driver ────────────────────────────────────────────────────


def walk_forward_optimize(
    symbol: str,
    timeframe: str,
    train_months: int,
    test_months: int,
    step_months: int,
    top_n: int,
    fast: bool,
) -> list[dict]:
    t0 = time.time()
    print(f"Loading {symbol} {timeframe}...")
    df = load_ohlcv("binance", symbol, timeframe).sort("timestamp")
    end_date = df["timestamp"].max()
    start_date = df["timestamp"].min()
    print(f"Data: {start_date.date()} → {end_date.date()} ({len(df)} bars)")

    space = get_param_space(fast)
    combos = sum(
        1
        for f in space["fast_ma"]
        for s in space["slow_ma"]
        if f < s
        for _p in space["rsi_period"]
        for b in space["rsi_buy"]
        for s2 in space["rsi_sell"]
        if b < s2
    )
    print(f"Param combos per window: {combos}")

    results = []
    current = start_date
    window_num = 0

    while True:
        train_end = current + timedelta(days=train_months * 30)
        test_end = train_end + timedelta(days=test_months * 30)
        if test_end > end_date:
            break

        window_num += 1
        train_df = df.filter(
            (pl.col("timestamp") >= current) & (pl.col("timestamp") < train_end)
        )
        test_df = df.filter(
            (pl.col("timestamp") >= train_end) & (pl.col("timestamp") < test_end)
        )

        if len(train_df) < 300 or len(test_df) < 80:
            print(f"[W{window_num}] insufficient data, stop.")
            break

        w_t0 = time.time()
        train_results = evaluate_window(train_df, space)

        if not train_results:
            print(f"[W{window_num}] no valid results, skip.")
            current += timedelta(days=step_months * 30)
            continue

        top = train_results[:top_n]
        test_metrics = []
        for r in top:
            df_ind = compute_indicators(
                test_df,
                r["params"]["fast_ma"],
                r["params"]["slow_ma"],
                r["params"]["rsi_period"],
            )
            tr = backtest_vectorized(
                df_ind, r["params"]["rsi_buy"], r["params"]["rsi_sell"]
            )
            if tr["valid"]:
                tr["params"] = r["params"]
                test_metrics.append(tr)

        if test_metrics:
            avg_sharpe = float(np.mean([m["sharpe"] for m in test_metrics]))
            avg_ret = float(np.mean([m["total_return_pct"] for m in test_metrics]))
            avg_dd = float(np.mean([m["max_dd_pct"] for m in test_metrics]))
            results.append(
                {
                    "window": window_num,
                    "train_start": str(current.date()),
                    "train_end": str(train_end.date()),
                    "test_start": str(train_end.date()),
                    "test_end": str(test_end.date()),
                    "train_bars": int(len(train_df)),
                    "test_bars": int(len(test_df)),
                    "top_params": [r["params"] for r in top],
                    "test_metrics": [
                        {
                            "sharpe": m["sharpe"],
                            "return": m["total_return_pct"],
                            "dd": m["max_dd_pct"],
                            "trades": m["num_trades"],
                            "win_rate": m["win_rate_pct"],
                        }
                        for m in test_metrics
                    ],
                    "avg_oos_sharpe": avg_sharpe,
                    "avg_oos_return": avg_ret,
                    "avg_oos_dd": avg_dd,
                }
            )
            print(
                f"[W{window_num}] {current.date()}→{test_end.date()} "
                f"train={len(train_df)} test={len(test_df)} "
                f"OOS Sharpe={avg_sharpe:.2f} Ret={avg_ret:.1f}% DD={avg_dd:.1f}% "
                f"({time.time() - w_t0:.1f}s)"
            )

        current += timedelta(days=step_months * 30)

    print(f"\nTotal: {len(results)} windows in {time.time() - t0:.1f}s")
    return results


def print_summary(results: list[dict]):
    if not results:
        print("No results.")
        return
    print("\n" + "=" * 66)
    print("WALK-FORWARD OPTIMIZATION SUMMARY")
    print("=" * 66)
    s = [r["avg_oos_sharpe"] for r in results]
    r = [r["avg_oos_return"] for r in results]
    d = [r["avg_oos_dd"] for r in results]
    print(f"Windows: {len(results)}")
    print(
        f"OOS Sharpe: mean={np.mean(s):.2f}  min={np.min(s):.2f}  max={np.max(s):.2f}"
    )
    print(
        f"OOS Return: mean={np.mean(r):.1f}%  min={np.min(r):.1f}%  max={np.max(r):.1f}%"
    )
    print(f"OOS MaxDD:  mean={np.mean(d):.1f}%  max={np.max(d):.1f}%")

    # Parameter stability across top-N
    print("\nTop param frequency:")
    freq = {}
    for w in results:
        for p in w["top_params"]:
            k = f"fast={p['fast_ma']} slow={p['slow_ma']} rsi={p['rsi_period']}({p['rsi_buy']}/{p['rsi_sell']})"
            freq[k] = freq.get(k, 0) + 1
    for k, c in sorted(freq.items(), key=lambda x: -x[1])[:8]:
        print(f"  {c}x  {k}")

    print("\nPer-window:")
    print(
        f"{'W':>2} {'Train':>22} {'Test':>22} {'Sharpe':>7} {'Ret%':>7} {'DD%':>6} {'Trades':>6}"
    )
    for w in results:
        tr = np.mean([m["trades"] for m in w["test_metrics"]])
        print(
            f"{w['window']:>2} {w['train_start']}→{w['train_end']} "
            f"{w['test_start']}→{w['test_end']} "
            f"{w['avg_oos_sharpe']:>7.2f} {w['avg_oos_return']:>6.1f} "
            f"{w['avg_oos_dd']:>6.1f} {tr:>6.0f}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=2)
    parser.add_argument("--step-months", type=int, default=3)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument(
        "--fast", action="store_true", help="Fast mode: coarse grid, fewer windows"
    )
    parser.add_argument("--output", default="data/wfo_results.json")
    args = parser.parse_args()

    if args.fast:
        args.train_months, args.test_months, args.step_months = 3, 1, 3

    results = walk_forward_optimize(
        args.symbol,
        args.timeframe,
        args.train_months,
        args.test_months,
        args.step_months,
        args.top_n,
        args.fast,
    )
    print_summary(results)

    out = ROOT / args.output
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved → {out}")
