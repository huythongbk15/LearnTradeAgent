#!/usr/bin/env python3
"""Quick focused sweep: momentum filter around the best combo so far.
Single-process, small grid — designed to finish in under a minute.
"""

from __future__ import annotations

import itertools
import statistics
import sys
from datetime import timedelta

sys.path.insert(0, "src")

import polars as pl

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

COMMISSION = 0.0010
SLIPPAGE = 0.0005
FOLDS = 6
FOLD_DAYS = 90
SYMBOLS = ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]

RAW = {
    s: load_ohlcv("binance", s.replace("/", "_"), "1h")
    .unique(subset=["timestamp"], keep="last")
    .sort("timestamp")
    for s in SYMBOLS
}


def eval_one(symbol: str, params: dict) -> dict:
    f = RAW[symbol]
    end = f["timestamp"].max() + timedelta(hours=1)
    start = end - timedelta(days=FOLDS * FOLD_DAYS)
    strat = EnhancedMaCrossover(params)
    sh: list[float] = []
    ret: list[float] = []
    dd: list[float] = []
    tr: list[int] = []
    for i in range(FOLDS):
        st = start + timedelta(days=i * FOLD_DAYS)
        en = start + timedelta(days=(i + 1) * FOLD_DAYS)
        fold = f.filter((pl.col("timestamp") >= st) & (pl.col("timestamp") < en))
        r = BacktestEngine(
            strategy=strat,
            initial_capital=10000,
            commission=COMMISSION,
            slippage=SLIPPAGE,
            long_only=True,
        ).run(fold, symbol=symbol, timeframe="1h")
        sh.append(r.sharpe_ratio)
        ret.append(r.total_return_pct)
        dd.append(abs(r.max_drawdown_pct))
        tr.append(r.total_trades)
    med_sh = statistics.median(sh)
    pos_ratio = sum(1 for x in sh if x > 0) / len(sh)
    return {
        "median_sharpe": round(med_sh, 3),
        "median_return_pct": round(statistics.median(ret), 2),
        "positive_ratio": round(pos_ratio, 2),
        "worst_dd_pct": round(max(dd), 2),
        "trades": sum(tr),
        "ok": (
            med_sh >= 0.5
            and statistics.median(ret) > 0
            and pos_ratio >= 0.6
            and max(dd) <= 15
            and sum(tr) >= 20
        ),
    }


def main() -> None:
    # Focus: best combo from prior sweeps was (10/60/40, dd 0.12, trail 2.0,
    # close_above True).  Sweep momentum_period and a couple of neighbors.
    grid = [
        {
            "fast_period": f,
            "slow_period": s,
            "adx_threshold": a,
            "max_dd_pct": m,
            "trailing_atr_mult": t,
            "dd_recovery_pct": 0.03,
            "dd_cooldown_bars": 0,
            "require_close_above_slow": True,
            "momentum_period": mp_,
        }
        for f, s, a, m, t, mp_ in itertools.product(
            [10, 15],
            [60, 80],
            [40],
            [0.12, 0.15],
            [2.0],
            [0, 24, 48, 72, 96],
        )
        if f < s
    ]
    print(f"grid: {len(grid)} combos")
    best: list[tuple] = []
    for i, p in enumerate(grid):
        res = {s: eval_one(s, p) for s in SYMBOLS}
        npass = sum(1 for r in res.values() if r["ok"])
        score = sum(r["median_sharpe"] for r in res.values())
        best.append((npass, score, p, res))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(grid)} done")
    best.sort(key=lambda x: (-x[0], -x[1]))
    print(
        f"\n=== SWEEP DONE: {len(grid)} combos, "
        f"{sum(1 for b in best if b[0] == 3)} pass all ==="
    )
    for npass, score, p, res in best[:10]:
        print(f"\npass={npass} score={score:.2f} {p}")
        for s, r in res.items():
            print("  ", s, r)


if __name__ == "__main__":
    main()
