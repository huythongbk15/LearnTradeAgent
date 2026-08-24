#!/usr/bin/env python3
"""
Test fast MA params on holdout period (last 3 months)
"""

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.ma_crossover import MaCrossover

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
TIMEFRAME = "1d"
EXCHANGE = "binance"

# Fast params from WFO results
BEST_FAST_PARAMS = {
    "BTC/USDT": {"fast_period": 5, "slow_period": 20},
    "ETH/USDT": {"fast_period": 12, "slow_period": 20},
    "SOL/USDT": {"fast_period": 5, "slow_period": 20},
    "XRP/USDT": {"fast_period": 12, "slow_period": 20},
    "BNB/USDT": {"fast_period": 5, "slow_period": 20},
    "ZEC/USDT": {"fast_period": 12, "slow_period": 20},
    "DOGE/USDT": {"fast_period": 5, "slow_period": 20},
    "TRX/USDT": {"fast_period": 12, "slow_period": 20},
    "ADA/USDT": {"fast_period": 5, "slow_period": 20},
    "NEAR/USDT": {"fast_period": 10, "slow_period": 20},
}

INITIAL_CAPITAL = 100000
HOLDOUT_MONTHS = 3


def run_backtest(df: pl.DataFrame, params: dict) -> dict:
    strategy = MaCrossover(params=params)
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=INITIAL_CAPITAL,
        commission=0.0005,
        slippage=0.0002,
        long_only=True,
    )
    result = engine.run(df, symbol="TEMP", timeframe=TIMEFRAME)
    equity_vals = (
        result.equity_curve["equity"].to_numpy()
        if "equity" in result.equity_curve.columns
        else np.array([])
    )
    return {
        "sharpe": float(result.sharpe_ratio),
        "return": float(result.total_return_pct),
        "max_dd": float(result.max_drawdown_pct),
        "trades": int(result.total_trades),
        "win_rate": float(result.win_rate),
        "profit_factor": float(result.profit_factor),
        "equity_curve": equity_vals,
    }


def load_data(symbol: str, timeframe: str):
    return load_ohlcv(EXCHANGE, symbol.replace("/", "_"), timeframe).sort("timestamp")


def split_holdout(df: pl.DataFrame, holdout_months: int):
    max_date = df["timestamp"].max()
    holdout_start = max_date - timedelta(days=holdout_months * 30)
    holdout_df = df.filter(pl.col("timestamp") >= holdout_start)
    return holdout_df


def main():
    print("=== Holdout Test: Fast MA Params (Last 3 months) ===\n")

    for sym in SYMBOLS:
        try:
            df = load_data(sym, TIMEFRAME)
            holdout_df = split_holdout(df, HOLDOUT_MONTHS)
            params = BEST_FAST_PARAMS.get(sym, {"fast_period": 5, "slow_period": 20})
            metrics = run_backtest(holdout_df, params)
            print(
                f"  {sym} {params}: Sharpe={metrics['sharpe']:.2f}, Return={metrics['return']:.1f}%, DD={metrics['max_dd']:.1f}%, Trades={metrics['trades']}"
            )
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    # Also test full period with fast params
    print("\n=== Full Period: Fast MA Params ===")
    for sym in SYMBOLS:
        try:
            df = load_data(sym, TIMEFRAME)
            params = BEST_FAST_PARAMS.get(sym, {"fast_period": 5, "slow_period": 20})
            metrics = run_backtest(df, params)
            print(
                f"  {sym} {params}: Sharpe={metrics['sharpe']:.2f}, Return={metrics['return']:.1f}%, DD={metrics['max_dd']:.1f}%, Trades={metrics['trades']}"
            )
        except Exception as e:
            print(f"  {sym}: ERROR {e}")


if __name__ == "__main__":
    main()
