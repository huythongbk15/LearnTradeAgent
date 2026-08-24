#!/usr/bin/env python3
"""
Backtest Adaptive MA Crossover vs Fixed 50/200 on 1d timeframe.
"""

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.online_learning import create_adaptive_ma_crossover
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.base import Strategy


class PrecomputedSignalStrategy(Strategy):
    """Strategy that uses pre-computed signals."""

    name = "precomputed"

    def __init__(self, signals: pl.Series):
        super().__init__({})
        self._signals = signals

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return self._signals


def run_backtest_with_signals(
    df: pl.DataFrame, signals: pl.Series, symbol: str
) -> dict:
    """Run backtest using pre-computed signals."""
    strategy = PrecomputedSignalStrategy(signals)
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=100000,
        commission=0.0005,
        slippage=0.0002,
        long_only=True,
    )
    result = engine.run(df, symbol=symbol, timeframe="1d")
    return {
        "sharpe": result.sharpe_ratio,
        "return": result.total_return_pct,
        "max_dd": result.max_drawdown_pct,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
    }


def main():
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
    EXCHANGE = "binance"

    print("=== Backtest: Adaptive vs Fixed 50/200 MA Crossover ===\n")

    for sym in SYMBOLS:
        print(f"\n--- {sym} ---")
        try:
            df = load_ohlcv(EXCHANGE, sym.replace("/", "_"), "1d")
            if df is None or len(df) < 200:
                print("  ✗ Insufficient data")
                continue

            # 1. Fixed 50/200
            fixed_strategy = MaCrossover(params={"fast_period": 50, "slow_period": 200})
            fixed_engine = BacktestEngine(
                strategy=fixed_strategy,
                initial_capital=100000,
                commission=0.0005,
                slippage=0.0002,
                long_only=True,
            )
            fixed_result = fixed_engine.run(df, symbol=sym, timeframe="1d")

            # 2. Adaptive
            adaptive = create_adaptive_ma_crossover(
                regime_lookback=100, regime_update_bars=20
            )
            adaptive_signals = adaptive.generate_signals(df)
            adaptive_result = run_backtest_with_signals(df, adaptive_signals, sym)

            print(
                f"  Fixed 50/200:  Sharpe={fixed_result.sharpe_ratio:.2f}, Return={fixed_result.total_return_pct:.1f}%, DD={fixed_result.max_drawdown_pct:.1f}%, Trades={fixed_result.total_trades}"
            )
            print(
                f"  Adaptive:      Sharpe={adaptive_result['sharpe']:.2f}, Return={adaptive_result['return']:.1f}%, DD={adaptive_result['max_dd']:.1f}%, Trades={adaptive_result['trades']}"
            )

        except Exception as e:
            print(f"  ✗ Error: {e}")


if __name__ == "__main__":
    main()
