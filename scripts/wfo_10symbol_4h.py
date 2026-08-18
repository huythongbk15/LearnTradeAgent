#!/usr/bin/env python3
"""
Multi-symbol Walk-Forward Optimization (WFO) for 10 symbols on 4h timeframe.
Runs optimization/training workflow and saves results.
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy

# ── Symbols and timeframe ────────────────────────────────────────────────
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
TIMEFRAME = "4h"
TRAIN_MONTHS = 12
TEST_MONTHS = 3
STEP_MONTHS = 3
INITIAL_CAPITAL = 100000
OUTPUT_DIR = Path("./data/wfo_results")

# ── Strategy parameter grids ──────────────────────────────────────────────
PARAM_GRIDS = {
    "ma_crossover": {
        "fast_period": [5, 8, 10, 12, 15, 20],
        "slow_period": [20, 30, 40, 50, 60, 80],
    },
    "rsi": {
        "period": [7, 10, 14, 21],
        "oversold": [20, 25, 30, 35],
        "overbought": [65, 70, 75, 80],
    },
    "bbands": {
        "period": [10, 14, 20, 25, 30],
        "std_dev": [1.5, 2.0, 2.5, 3.0],
    },
}

STRATEGY_CLASSES = {
    "ma_crossover": MaCrossover,
    "rsi": RsiStrategy,
    "bbands": BBandsStrategy,
}


def generate_param_combinations(grid: dict) -> list[dict]:
    import itertools

    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def run_backtest(df: pl.DataFrame, strategy_name: str, params: dict) -> dict:
    strategy_cls = STRATEGY_CLASSES[strategy_name]
    strategy = strategy_cls(params=params)

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=INITIAL_CAPITAL,
        commission=0.0005,
        slippage=0.0002,
        long_only=True,
    )
    result = engine.run(df, symbol=SYMBOLS[0], timeframe=TIMEFRAME)

    return {
        "total_return_pct": result.total_return_pct,
        "annual_return_pct": result.annualized_return_pct,
        "sharpe": result.sharpe_ratio,
        "sortino": result.sortino_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "num_trades": result.total_trades,
        "avg_hold_bars": result.avg_hold_bars,
        "calmar": result.calmar_ratio,
    }


def walk_forward_optimize(
    df: pl.DataFrame,
    strategy_name: str,
    train_months: int = TRAIN_MONTHS,
    test_months: int = TEST_MONTHS,
    step_months: int = STEP_MONTHS,
) -> list[dict]:
    results = []
    min_date = df["timestamp"].min()
    max_date = df["timestamp"].max()

    param_combos = generate_param_combinations(PARAM_GRIDS[strategy_name])
    print(f"  {strategy_name}: {len(param_combos)} param combinations")

    current_start = min_date
    fold = 0

    while True:
        train_end = current_start + timedelta(days=train_months * 30)
        test_end = train_end + timedelta(days=test_months * 30)

        if test_end > max_date:
            break

        train_df = df.filter(
            (pl.col("timestamp") >= current_start) & (pl.col("timestamp") < train_end)
        )
        test_df = df.filter(
            (pl.col("timestamp") >= train_end) & (pl.col("timestamp") < test_end)
        )

        if len(train_df) < 500 or len(test_df) < 100:
            break

        # In-sample optimization
        best_params = None
        best_sharpe = -float("inf")

        for params in param_combos:
            try:
                metrics = run_backtest(train_df, strategy_name, params)
                sharpe = metrics.get("sharpe", -999)
                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = params
            except Exception:
                continue

        if best_params is None:
            current_start += timedelta(days=step_months * 30)
            fold += 1
            continue

        # Out-of-sample test
        try:
            oos_metrics = run_backtest(test_df, strategy_name, best_params)
            results.append(
                {
                    "fold": fold,
                    "strategy": strategy_name,
                    "best_params": best_params,
                    "is_sharpe": best_sharpe,
                    "oos_metrics": oos_metrics,
                }
            )
            print(
                f"    Fold {fold}: IS sharpe={best_sharpe:.2f}, "
                f"OOS return={oos_metrics['total_return_pct']:.1f}%"
            )
        except Exception:
            pass

        current_start += timedelta(days=step_months * 30)
        fold += 1

    return results


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / f"wfo_10symbol_4h_{timestamp}.json"

    print(f"\n=== Multi-Symbol WFO 4h {timestamp} ===")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Strategies: {', '.join(PARAM_GRIDS.keys())}")
    print(f"Train: {TRAIN_MONTHS}m | Test: {TEST_MONTHS}m | Step: {STEP_MONTHS}m")
    print(f"Output: {output_file}\n")

    all_results = {}

    for symbol in SYMBOLS:
        print(f"\n--- {symbol} ---")
        symbol_results = {}

        # Load data
        symbol_path = f"data/raw/binance/{symbol.replace('/', '_')}/{TIMEFRAME}.parquet"
        if not Path(symbol_path).exists():
            print(f"  ✗ Data not found: {symbol_path}")
            continue

        try:
            exchange = "binance"
            symbol_name = symbol.replace("/", "_")
            df = load_ohlcv(exchange, symbol_name, TIMEFRAME)
            if df is None or len(df) < 1000:
                print(f"  ✗ Insufficient data: {len(df) if df is not None else 0} bars")
                continue
            print(f"  Loaded {len(df)} bars")
        except Exception as e:
            print(f"  ✗ Failed to load data: {e}")
            continue

        # Run WFO for each strategy
        for strategy_name in PARAM_GRIDS.keys():
            print(f"  Running {strategy_name}...")
            try:
                results = walk_forward_optimize(df, strategy_name)
                symbol_results[strategy_name] = results
            except Exception as e:
                print(f"    ✗ Failed: {e}")

        all_results[symbol] = symbol_results

    # Save results
    with open(output_file, "w") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "symbols": SYMBOLS,
                "timeframe": TIMEFRAME,
                "train_months": TRAIN_MONTHS,
                "test_months": TEST_MONTHS,
                "step_months": STEP_MONTHS,
                "results": all_results,
            },
            f,
            indent=2,
        )

    print(f"\n✅ Done — results: {output_file}")


if __name__ == "__main__":
    main()
