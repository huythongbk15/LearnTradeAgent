#!/usr/bin/env python3
"""Minimal WFO scope: 1 strategy, 1 symbol, 1 cost scenario, 3 params.

Usage:
    python scripts/run_wfo_minimal.py [--symbol SOL/USDT] [--strategy ma_adx]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (
    WFOSpec,
    run_nested_wfo_portfolio,
)
from trading_agent.backtest.tournament import (
    CostScenario,
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
)


MINIMAL_PARAM_GRIDS: dict[str, dict[str, list]] = {
    "ma_adx": {
        "fast_ma": [10, 20, 30],
        "slow_ma": [40, 60, 80],
        "adx_period": [14],
        "adx_threshold": [30],
    },
    "rsi": {
        "period": [14, 21],
        "oversold": [30, 25],
        "overbought": [70, 75],
    },
    "bbands": {
        "period": [20, 21],
        "std_dev": [2.0, 2.5],
    },
    "enhanced_ma": {
        "fast": [10, 20],
        "slow": [40, 60, 80],
        "signal_ma": [20, 40],
    },
    "ma_vol_target": {
        "fast_ma": [20, 30],
        "slow_ma": [60, 80],
        "vol_target": [0.15, 0.20, 0.25],
    },
}


def build_minimal_spec(strategy_id: str, symbol: str, timeframe: str = "1h") -> WFOSpec:
    param_grid = MINIMAL_PARAM_GRIDS.get(strategy_id, {})
    return WFOSpec(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        param_grid=param_grid,
        cost_scenarios=(SCENARIO_BASE, SCENARIO_DOUBLE),
        train_months=12,
        val_months=3,
        test_months=3,
        step_months=3,
        registry_path="data/wfo/experiments_medium.sqlite3",
        search_family="s3_wfo_medium",
        evaluator_version="v1",
        seed=42,
        min_oos_trades=30,
        evidence_class="REAL_MARKET",
    )


def main():
    parser = argparse.ArgumentParser(description="Minimal WFO scope")
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--out", default="data/backtests/wfo_medium")
    args = parser.parse_args()

    spec = build_minimal_spec(args.strategy, args.symbol, args.timeframe)
    print(f"Running minimal WFO: {spec.strategy_id} {spec.symbol} {spec.timeframe}")
    print(f"  Params: {spec.param_grid}")
    print(f"  Cost: {[c.name for c in spec.cost_scenarios]}")
    print(f"  Out: {args.out}")

    result = run_nested_wfo_portfolio([spec], out_root=Path(args.out))
    print(f"\nDone. Verdict: {result.verdict}")
    print(f"  Passes gates: {result.passes_hard_gates}")
    print(f"  Median Sharpe: {result.aggregate_metrics.get('median_test_sharpe', 'N/A')}")
    print(f"  Median Return: {result.aggregate_metrics.get('median_test_return_pct', 'N/A')}%")
    print(f"  Artifact ID: {result.artifact_id}")


if __name__ == "__main__":
    main()