#!/usr/bin/env python3
"""
Portfolio Construction + Final Holdout Test
- Equal-risk allocation across PAPER_ELIGIBLE candidates
- Final holdout on untouched recent data
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy

# ── PAPER_ELIGIBLE from research pipeline ────────────────────────────────
PAPER_ELIGIBLE = [
    {
        "symbol": "BNB/USDT",
        "timeframe": "1d",
        "strategy": "ma_crossover",
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "symbol": "ZEC/USDT",
        "timeframe": "1d",
        "strategy": "ma_crossover",
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "symbol": "DOGE/USDT",
        "timeframe": "1d",
        "strategy": "ma_crossover",
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "symbol": "TRX/USDT",
        "timeframe": "1d",
        "strategy": "ma_crossover",
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "symbol": "ZEC/USDT",
        "timeframe": "4h",
        "strategy": "rsi",
        "params": {"period": 14, "oversold": 30, "overbought": 70},
    },
    {
        "symbol": "DOGE/USDT",
        "timeframe": "4h",
        "strategy": "ma_crossover",
        "params": {"fast_period": 50, "slow_period": 200},
    },
    {
        "symbol": "NEAR/USDT",
        "timeframe": "1d",
        "strategy": "bbands",
        "params": {"period": 20, "std_dev": 2.0},
    },
]

EXCHANGE = "binance"
INITIAL_CAPITAL = 100000
HOLDOUT_MONTHS = 3  # Final 3 months as holdout


STRATEGY_CLASSES = {
    "ma_crossover": MaCrossover,
    "rsi": RsiStrategy,
    "bbands": BBandsStrategy,
}


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
    result = engine.run(df, symbol="TEMP", timeframe="1d")
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
    train_df = df.filter(pl.col("timestamp") < holdout_start)
    holdout_df = df.filter(pl.col("timestamp") >= holdout_start)
    return train_df, holdout_df


def main():
    print("=== Portfolio Construction + Final Holdout ===\n")

    # Load all data and split holdout
    all_data = {}
    for cand in PAPER_ELIGIBLE:
        sym, tf = cand["symbol"], cand["timeframe"]
        try:
            df = load_data(sym, tf)
            if df is not None and len(df) > 100:
                train_df, holdout_df = split_holdout(df, HOLDOUT_MONTHS)
                all_data[(sym, tf)] = {
                    "train": train_df,
                    "holdout": holdout_df,
                    "candidate": cand,
                }
                print(
                    f"  ✓ {sym} {tf}: train={len(train_df)} bars, holdout={len(holdout_df)} bars"
                )
            else:
                print(f"  ✗ {sym} {tf}: insufficient data")
        except Exception as e:
            print(f"  ✗ {sym} {tf}: {e}")

    # Train-phase: compute volatility for equal-risk allocation
    print("\n--- Train Phase (Volatility Estimation) ---")
    train_vols = {}
    train_metrics = {}
    for key, data in all_data.items():
        cand = data["candidate"]
        metrics = run_backtest(data["train"], cand["strategy"], cand["params"])
        eq = metrics.get("equity_curve", np.array([]))
        if len(eq) > 2:
            rets = np.diff(eq) / eq[:-1]
            vol = np.std(rets) * np.sqrt(252)  # annualize
        else:
            vol = 0.15  # fallback
        train_vols[key] = float(vol)
        train_metrics[key] = metrics
        print(
            f"  {cand['symbol']} {cand['timeframe']} {cand['strategy']}: vol={vol:.2%}, Sharpe={metrics['sharpe']:.2f}"
        )

    # Equal-risk weights (inverse vol)
    inv_vols = {k: 1.0 / v if v > 0 else 0.0 for k, v in train_vols.items()}
    total_inv = sum(inv_vols.values())
    weights = {k: v / total_inv for k, v in inv_vols.items()}

    print("\n--- Equal-Risk Weights ---")
    for k, w in weights.items():
        cand = all_data[k]["candidate"]
        print(f"  {cand['symbol']} {cand['timeframe']} {cand['strategy']}: {w:.1%}")

    # Holdout phase: test each and aggregate
    print(f"\n--- Holdout Test (Last {HOLDOUT_MONTHS} months) ---")
    holdout_results = {}
    for key, data in all_data.items():
        cand = data["candidate"]
        metrics = run_backtest(data["holdout"], cand["strategy"], cand["params"])
        holdout_results[key] = metrics
        print(
            f"  {cand['symbol']} {cand['timeframe']} {cand['strategy']}: Sharpe={metrics['sharpe']:.2f}, Return={metrics['return']:.1f}%, DD={metrics['max_dd']:.1f}%, Trades={metrics['trades']}"
        )

    # Portfolio aggregation
    print("\n--- Portfolio Results (Equal-Risk) ---")
    port_return = sum(
        weights[k] * holdout_results[k]["return"]
        for k in weights
        if k in holdout_results
    )

    # Aggregate equity curves (weighted)
    equity_curves = {}
    for k in weights:
        if k in holdout_results:
            eq = holdout_results[k].get("equity_curve", np.array([]))
            if len(eq) > 0:
                equity_curves[k] = np.array(eq)

    port_sharpe = 0.0
    port_max_dd = 0.0
    if equity_curves:
        min_len = min(len(eq) for eq in equity_curves.values())
        if min_len > 2:
            port_eq = np.zeros(min_len)
            for k, w in weights.items():
                if k in equity_curves:
                    eq = equity_curves[k]
                    if len(eq) >= min_len:
                        port_eq += w * eq[:min_len]

            port_rets = np.diff(port_eq) / port_eq[:-1]
            port_sharpe = float(
                np.mean(port_rets) / np.std(port_rets) * np.sqrt(252)
                if np.std(port_rets) > 0
                else 0.0
            )
            port_dd = 0.0
            peak = port_eq[0]
            for v in port_eq:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak
                if dd > port_dd:
                    port_dd = dd
            port_max_dd = float(port_dd * 100)

    print(f"  Portfolio Return: {port_return:.1f}%")
    print(f"  Portfolio Sharpe: {port_sharpe:.2f}")
    print(f"  Portfolio Max DD: {port_max_dd:.1f}%")

    # Cost stress test on holdout
    print("\n--- Cost Stress Test (2x, 3x fees) ---")
    for key, data in all_data.items():
        cand = data["candidate"]
        for mult in [1.0, 2.0, 3.0]:
            try:
                strategy_cls = STRATEGY_CLASSES[cand["strategy"]]
                strategy = strategy_cls(params=cand["params"])
                engine = BacktestEngine(
                    strategy=strategy,
                    initial_capital=INITIAL_CAPITAL,
                    commission=0.0005 * mult,
                    slippage=0.0002 * mult,
                    long_only=True,
                )
                result = engine.run(
                    data["holdout"], symbol="TEMP", timeframe=cand["timeframe"]
                )
                print(
                    f"  {cand['symbol']} {cand['timeframe']} {cand['strategy']} @ {mult}x: Sharpe={result.sharpe_ratio:.2f}, Return={result.total_return_pct:.1f}%"
                )
            except Exception as e:
                print(
                    f"  {cand['symbol']} {cand['timeframe']} {cand['strategy']} @ {mult}x: ERROR {e}"
                )

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(f"data/wfo_results/portfolio_holdout_{ts}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Convert numpy arrays to lists for JSON
    holdout_serializable = {}
    for k, v in holdout_results.items():
        holdout_serializable[str(k)] = {
            kk: (vv.tolist() if isinstance(vv, np.ndarray) else vv)
            for kk, vv in v.items()
        }
    with open(out, "w") as f:
        json.dump(
            {
                "weights": {str(k): float(v) for k, v in weights.items()},
                "holdout_results": holdout_serializable,
                "portfolio": {
                    "return": float(port_return),
                    "sharpe": float(port_sharpe),
                    "max_dd": float(port_max_dd),
                },
                "holdout_months": HOLDOUT_MONTHS,
            },
            f,
            indent=2,
        )
    print(f"\nResults saved to {out}")


if __name__ == "__main__":
    main()
