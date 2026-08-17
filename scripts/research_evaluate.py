#!/usr/bin/env python3
"""
Evaluate baselines with walk-forward + cost stress + statistical validation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(".")
RUN_DIR = ROOT / "data" / "research_runs" / "latest"
RUN_DIR.mkdir(parents=True, exist_ok=True)

# Reuse constants
SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ZEC/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "ADA/USDT",
    "NEAR/USDT",
]
TIMEFRAMES = ["1h", "4h", "1d"]

COST = {
    "maker_fee": 0.0006,
    "taker_fee": 0.001,
    "spread_bps": 5,
    "slippage_bps": 5,
}
COST_STRESS = [0.5, 1.0, 1.5, 2.0, 3.0]


def _load_folds(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    path = RUN_DIR / "folds" / "folds.json"
    if not path.exists():
        return []
    folds = json.loads(path.read_text())
    return [f for f in folds if f["symbol"] == symbol and f["timeframe"] == timeframe]


def _run_backtest(df: pl.DataFrame, strategy, cost_mult: float = 1.0) -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.backtest.engine import BacktestEngine

    fee = COST["taker_fee"] * cost_mult
    engine = BacktestEngine(
        strategy,
        initial_capital=10_000.0,
        commission=fee + COST["spread_bps"] / 10000,
        slippage=COST["slippage_bps"] / 10000,
        spread_bps=COST["spread_bps"],
        atr_sl_mult=2.0,
        atr_tp_mult=3.0,
        trailing_atr_mult=1.5,
    )
    result = engine.run(df)
    return {
        "return": result.total_return_pct,
        "sharpe": result.sharpe_ratio,
        "sortino": result.sortino_ratio,
        "max_dd": result.max_drawdown_pct,
        "calmar": result.calmar_ratio,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "avg_hold_bars": result.avg_hold_bars,
    }


def _defensive_sharpe(sharpe: float, n: int) -> float:
    """DSR-style adjustment: shrink Sharpe by sqrt(n) factor."""
    if n <= 1 or sharpe == 0:
        return 0.0
    return sharpe / np.sqrt(n)


def evaluate() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.strategies import get_strategy
    from trading_agent.data.storage import load_ohlcv

    strategies = {
        "ma_crossover": get_strategy("ma_crossover")(),
        "rsi": get_strategy("rsi")(),
        "bbands": get_strategy("bbands")(),
        "enhanced_ma": get_strategy("enhanced_ma")(),
    }

    results = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            folds = _load_folds(sym, tf)
            if not folds:
                continue
            df_full = load_ohlcv("binance", sym, tf).sort("timestamp")
            for name, strategy in strategies.items():
                fold_returns = []
                fold_sharpes = []
                for fold in folds:
                    train_start = pl.lit(fold["train_start_ts"]).str.to_datetime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    train_end = pl.lit(fold["train_end_ts"]).str.to_datetime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    test_start = pl.lit(fold["test_start_ts"]).str.to_datetime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    test_end = pl.lit(fold["test_end_ts"]).str.to_datetime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    train = df_full.filter(
                        pl.col("timestamp") >= train_start,
                        pl.col("timestamp") < train_end,
                    )
                    test = df_full.filter(
                        pl.col("timestamp") >= test_start,
                        pl.col("timestamp") < test_end,
                    )
                    if len(test) < 10:
                        continue
                    try:
                        res = _run_backtest(test, strategy, cost_mult=1.0)
                        fold_returns.append(res["return"])
                        fold_sharpes.append(res["sharpe"])
                    except Exception:
                        continue

                if not fold_returns:
                    continue

                oos_returns = np.array(fold_returns)
                oos_sharpe = float(np.mean(fold_sharpes)) if fold_sharpes else 0.0
                net_return = float(np.mean(oos_returns))
                max_dd = float(np.min(oos_returns)) if len(oos_returns) else 0.0
                trades = int(np.sum([r.get("trades", 0) for r in []]))

                # Cost stress
                cost_2x = 0.0
                cost_3x = 0.0
                if folds:
                    last = folds[-1]
                    try:
                        ts = pl.lit(last["test_start_ts"]).str.to_datetime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        test_slice = df_full.filter(pl.col("timestamp") >= ts).head(
                            2000
                        )
                        res2 = _run_backtest(test_slice, strategy, cost_mult=2.0)
                        cost_2x = res2["sharpe"]
                        res3 = _run_backtest(test_slice, strategy, cost_mult=3.0)
                        cost_3x = res3["sharpe"]
                    except Exception:
                        pass

                # DSR
                dsr = _defensive_sharpe(oos_sharpe, len(fold_sharpes))

                results.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "strategy": name,
                        "folds": len(fold_returns),
                        "oos_sharpe": round(oos_sharpe, 4),
                        "net_return": round(net_return, 4),
                        "max_dd": round(max_dd, 4),
                        "cost_2x_sharpe": round(cost_2x, 4),
                        "cost_3x_sharpe": round(cost_3x, 4),
                        "dsr": round(dsr, 4),
                        "status": "RESEARCH_ONLY",
                    }
                )

    with open(RUN_DIR / "walk_forward_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(
        f"Saved {len(results)} walk-forward results to {RUN_DIR / 'walk_forward_results.json'}"
    )


if __name__ == "__main__":
    evaluate()
