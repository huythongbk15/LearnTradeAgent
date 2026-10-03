#!/usr/bin/env python3
"""Reproduce the simulator-vs-standard backtest discrepancy.

test_execution_simulator.py::test_simulator_vs_standard asserts the
simulator cannot beat the plain engine by more than 5%. It reports 420.86%
against 44.34% on the same 500 daily bars — a 10x gap. The test could not
run between 2026-08-24 and 2026-09-29 because backtest_sim/__init__.py
listed run_simulator_backtest in __all__ without importing it, so pytest
aborted collection for the whole shard.

This narrows where the two engines diverge: same strategy, same data, same
cost assumptions. Reported side by side.

Run: .venv/bin/python scripts/diagnose_simulator_discrepancy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.backtest_sim import (
    FillModel,
    ImpactModel,
    run_simulator_backtest,
)
from trading_agent.strategies.ma_crossover import MaCrossover


def main() -> None:
    df = load_ohlcv("binance", "BTC/USDT", "1d").head(500)
    strategy = MaCrossover(params={"fast_period": 50, "slow_period": 200})

    print(f"bars        : {df.height}")
    print(f"price       : {df['close'][0]:.1f} -> {df['close'][-1]:.1f}")

    # raw signal count, independent of either engine
    sig = strategy.generate_signals(strategy.compute_indicators(df)).to_numpy()
    n_long = int((sig > 0).sum())
    entries = int(((sig[1:] > 0) & (sig[:-1] <= 0)).sum())
    print(f"\nraw signals : long bars {n_long}/{len(sig)}  entries {entries}")
    print(f"buy & hold  : {(df['close'][-1] / df['close'][0] - 1) * 100:.2f}%")

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=100000,
        commission=0.0005,
        slippage=0.0002,
        long_only=True,
    )
    std = engine.run(df, symbol="BTC/USDT", timeframe="1d")

    sim = run_simulator_backtest(
        strategy=strategy,
        df=df,
        symbol="BTC/USDT",
        timeframe="1d",
        initial_capital=100000,
        simulator_config={
            "fill_model": FillModel.IMMEDIATE,
            "impact_model": ImpactModel.NONE,
            "maker_fee_bps": 5.0,
            "taker_fee_bps": 5.0,
            "base_slippage_bps": 2.0,
            "partial_fill_prob": 0.0,
        },
    )

    print("\n" + "=" * 66)
    print(f"{'metric':28} {'standard':>16} {'simulator':>16}")
    print("-" * 66)
    rows = [
        ("total_return_pct", std.total_return_pct, sim.total_return_pct),
        ("annualized_return_pct", std.annualized_return_pct, sim.annualized_return_pct),
        ("sharpe_ratio", std.sharpe_ratio, sim.sharpe_ratio),
        ("max_drawdown_pct", std.max_drawdown_pct, sim.max_drawdown_pct),
        ("total_trades", std.total_trades, sim.total_trades),
        ("win_rate", std.win_rate, getattr(sim, "win_rate", "n/a")),
    ]
    for name, a, b in rows:
        print(f"{name:28} {a:>16.4f} {b:>16.4f}")

    print("\nsimulator extras:")
    for name in ("total_fees", "total_slippage", "avg_latency_ms", "maker_ratio",
                 "n_orders", "n_fills"):
        print(f"  {name:20} {getattr(sim, name, 'n/a')}")
    print("=" * 66)

    gap = sim.total_return_pct - std.total_return_pct
    print(f"\ngap (simulator - standard): {gap:+.2f} pp")
    if sim.total_return_pct > std.total_return_pct + 5:
        print("\nASSERTION VIOLATED: the simulator is materially more optimistic.")
        print("The simulator charges fees and slippage yet returns 10x the plain")
        print("engine on identical bars, so the disagreement is in position")
        print("sizing or fill accounting, not in the cost model.")
    print(f"\nraw signal entries available: {entries}")
    print(f"standard engine trades     : {std.total_trades}")
    print(f"simulator trades           : {sim.total_trades}")


if __name__ == "__main__":
    main()