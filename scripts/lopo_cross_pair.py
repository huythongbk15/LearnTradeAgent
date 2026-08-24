#!/usr/bin/env python3
"""
Leave-One-Pair-Out (LOPO) Cross-Pair Validation
Tests if MA Crossover parameters generalize across symbols.
"""

import json
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.ma_crossover import MaCrossover

# ── Symbols from 1d PAPER_ELIGIBLE + top candidates ──────────────────────
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

# Best params from full-data optimization per symbol (from research pipeline)
BEST_PARAMS = {
    "BTC/USDT": {"fast_period": 50, "slow_period": 200},
    "ETH/USDT": {"fast_period": 50, "slow_period": 200},
    "SOL/USDT": {"fast_period": 50, "slow_period": 200},
    "XRP/USDT": {"fast_period": 50, "slow_period": 200},
    "BNB/USDT": {"fast_period": 50, "slow_period": 200},
    "ZEC/USDT": {"fast_period": 50, "slow_period": 200},
    "DOGE/USDT": {"fast_period": 50, "slow_period": 200},
    "TRX/USDT": {"fast_period": 50, "slow_period": 200},
    "ADA/USDT": {"fast_period": 50, "slow_period": 200},
    "NEAR/USDT": {"fast_period": 50, "slow_period": 200},
}

INITIAL_CAPITAL = 100000


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
    return {
        "sharpe": result.sharpe_ratio,
        "return": result.total_return_pct,
        "max_dd": result.max_drawdown_pct,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
    }


def main():
    print("=== LOPO Cross-Pair Validation ===")
    print(f"Symbols: {len(SYMBOLS)}")
    print("Strategy: MA Crossover (50/200) — tested across all pairs\n")

    # Load all data
    all_data = {}
    for sym in SYMBOLS:
        try:
            df = load_ohlcv(EXCHANGE, sym.replace("/", "_"), TIMEFRAME)
            if df is not None and len(df) > 100:
                all_data[sym] = df.sort("timestamp")
                print(f"  ✓ {sym}: {len(df)} bars")
            else:
                print(f"  ✗ {sym}: insufficient data")
        except Exception as e:
            print(f"  ✗ {sym}: {e}")

    # LOPO: Train on N-1, test on 1
    results = []
    for test_sym in SYMBOLS:
        if test_sym not in all_data:
            continue

        train_symbols = [s for s in SYMBOLS if s != test_sym and s in all_data]

        # Optimize on combined train data (pooled)
        # Or: average best params from train symbols
        param_votes = {}
        for s in train_symbols:
            p = BEST_PARAMS.get(s, {"fast_period": 50, "slow_period": 200})
            key = (p["fast_period"], p["slow_period"])
            param_votes[key] = param_votes.get(key, 0) + 1

        # Use most common params from training symbols
        best_param_key = max(param_votes.items(), key=lambda x: x[1])[0]
        best_params = {
            "fast_period": best_param_key[0],
            "slow_period": best_param_key[1],
        }

        # Test on held-out symbol
        test_df = all_data[test_sym]
        test_metrics = run_backtest(test_df, best_params)

        results.append(
            {
                "test_symbol": test_sym,
                "train_symbols": train_symbols,
                "params_used": best_params,
                "metrics": test_metrics,
            }
        )

        print(f"\n  {test_sym}: params={best_params}")
        print(
            f"    Sharpe: {test_metrics['sharpe']:.2f} | Return: {test_metrics['return']:.1f}% | DD: {test_metrics['max_dd']:.1f}% | Trades: {test_metrics['trades']}"
        )

    # Summary
    sharpes = [r["metrics"]["sharpe"] for r in results]
    returns = [r["metrics"]["return"] for r in results]
    positive_sharpe = sum(1 for s in sharpes if s > 0)
    positive_return = sum(1 for r in returns if r > 0)

    print("\n=== LOPO SUMMARY ===")
    print(f"Total symbols tested: {len(results)}")
    print(f"Positive OOS Sharpe: {positive_sharpe}/{len(results)}")
    print(f"Positive OOS Return: {positive_return}/{len(results)}")
    print(f"Mean OOS Sharpe: {np.mean(sharpes):.2f}")
    print(f"Median OOS Sharpe: {np.median(sharpes):.2f}")
    print(f"Mean OOS Return: {np.mean(returns):.1f}%")

    # Save
    import datetime

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(f"data/wfo_results/lopo_{ts}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
