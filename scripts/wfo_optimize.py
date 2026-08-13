#!/usr/bin/env python3
"""
Walk-Forward Optimization (WFO) for finding optimal strategy + parameters.
Tests multiple strategies on 3+ years BTC 1h data with rolling windows.
"""

import polars as pl
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.backtest.engine import BacktestEngine


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
    """Generate all combinations from param grid."""
    import itertools

    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def run_backtest(
    df: pl.DataFrame, strategy_name: str, params: dict, capital: float = 10000
) -> dict:
    """Run single backtest and return metrics."""
    strategy_cls = STRATEGY_CLASSES[strategy_name]
    strategy = strategy_cls(params=params)

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=capital,
        commission=0.0005,  # 0.05% per side
        slippage=0.0002,
        long_only=True,
    )
    result = engine.run(df, symbol="BTC/USDT", timeframe="1h")

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
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
) -> list[dict]:
    """
    Walk-forward optimization:
    - Train on train_months, pick best params
    - Test on test_months (OOS)
    - Roll forward by step_months
    """
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
        best_sharpe = -np.inf

        for params in param_combos:
            try:
                metrics = run_backtest(train_df, strategy_name, params)
                if metrics["sharpe"] > best_sharpe and metrics["num_trades"] > 10:
                    best_sharpe = metrics["sharpe"]
                    best_params = params
            except Exception:
                continue

        if best_params is None:
            print(f"    Fold {fold}: No valid params found")
            current_start += timedelta(days=step_months * 30)
            fold += 1
            continue

        # Out-of-sample test
        oos_metrics = run_backtest(test_df, strategy_name, best_params)

        results.append(
            {
                "fold": fold,
                "train_start": str(current_start),
                "train_end": str(train_end),
                "test_start": str(train_end),
                "test_end": str(test_end),
                "best_params": best_params,
                "is_sharpe": best_sharpe,
                "oos_metrics": oos_metrics,
            }
        )

        print(
            f"    Fold {fold}: IS Sharpe={best_sharpe:.3f} | OOS Sharpe={oos_metrics['sharpe']:.3f} | Return={oos_metrics['total_return_pct']:.2f}% | DD={oos_metrics['max_drawdown_pct']:.2f}% | Trades={oos_metrics['num_trades']}"
        )

        current_start += timedelta(days=step_months * 30)
        fold += 1

    return results


def main():
    print("=" * 70)
    print("WALK-FORWARD OPTIMIZATION — BTC/USDT 1h (3.6 years)")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    df = load_ohlcv("binance", "BTC/USDT", "1h")
    df = df.sort("timestamp")
    print(f"  Total candles: {len(df)}")
    print(f"  Range: {df['timestamp'].min()} → {df['timestamp'].max()}")

    all_results = {}

    for strategy_name in ["ma_crossover", "rsi", "bbands"]:
        print(f"\n{'=' * 70}")
        print(f"Strategy: {strategy_name.upper()}")
        print(f"{'=' * 70}")

        results = walk_forward_optimize(df, strategy_name)
        all_results[strategy_name] = results

        # Aggregate OOS performance
        if results:
            oos_returns = [r["oos_metrics"]["total_return_pct"] for r in results]
            oos_sharpes = [r["oos_metrics"]["sharpe"] for r in results]
            oos_dds = [r["oos_metrics"]["max_drawdown_pct"] for r in results]
            oos_trades = [r["oos_metrics"]["num_trades"] for r in results]

            print(f"\n  AGGREGATE OOS ({len(results)} folds):")
            print(
                f"    Avg Return: {np.mean(oos_returns):.2f}% (median: {np.median(oos_returns):.2f}%)"
            )
            print(
                f"    Avg Sharpe: {np.mean(oos_sharpes):.3f} (median: {np.median(oos_sharpes):.3f})"
            )
            print(
                f"    Avg MaxDD:  {np.mean(oos_dds):.2f}% (worst: {np.max(oos_dds):.2f}%)"
            )
            print(f"    Avg Trades: {np.mean(oos_trades):.0f}/fold")

            # Best fold
            best_fold = max(results, key=lambda r: r["oos_metrics"]["sharpe"])
            print(f"\n  BEST FOLD (Fold {best_fold['fold']}):")
            print(f"    Params: {best_fold['best_params']}")
            print(
                f"    OOS: Return={best_fold['oos_metrics']['total_return_pct']:.2f}% Sharpe={best_fold['oos_metrics']['sharpe']:.3f} DD={best_fold['oos_metrics']['max_drawdown_pct']:.2f}%"
            )

    # Save results
    output = {
        "timestamp": datetime.now().isoformat(),
        "data_range": {
            "start": str(df["timestamp"].min()),
            "end": str(df["timestamp"].max()),
        },
        "results": all_results,
    }

    out_path = Path("data/wfo_results_3y.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n\nResults saved to {out_path}")

    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY — Best OOS Sharpe per Strategy")
    print("=" * 70)
    for strat, results in all_results.items():
        if results:
            best = max(results, key=lambda r: r["oos_metrics"]["sharpe"])
            m = best["oos_metrics"]
            print(
                f"  {strat:15s} | Sharpe={m['sharpe']:6.3f} | Return={m['total_return_pct']:7.2f}% | DD={m['max_drawdown_pct']:6.2f}% | Trades={m['num_trades']:3.0f} | Params={best['best_params']}"
            )


if __name__ == "__main__":
    main()
