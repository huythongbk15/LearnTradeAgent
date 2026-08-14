#!/usr/bin/env python3
"""Run a strategy through the Execution Simulator V2 and emit evidence.

Produces ``data/simulated_execution_report.json`` with:

* versioned simulation config + model versions + data manifest
* execution metrics + P&L attribution (theoretical alpha vs execution cost)
* a RealityGapReport comparing the idealized backtest against the simulator

Usage:
    python scripts/simulate_execution.py [--symbol BTC/USDT] [--timeframe 1h]
        [--strategy ma_crossover] [--seed 42] [--out data/simulated_execution_report.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Import strategies to register them.
import trading_agent.strategies  # noqa: F401
from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.base import get_strategy


def main() -> None:
    ap = argparse.ArgumentParser(description="Execution Simulator V2 evidence")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--strategy", default="ma_crossover")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--initial-cash", type=float, default=10_000.0)
    ap.add_argument("--spread-bps", type=float, default=5.0)
    ap.add_argument("--taker-fee", type=float, default=0.0005)
    ap.add_argument("--maker-fee", type=float, default=0.0002)
    ap.add_argument("--out", default="data/simulated_execution_report.json")
    args = ap.parse_args()

    df = load_ohlcv(config.default_exchange, args.symbol, args.timeframe)
    if df is None or len(df) < 100:
        raise SystemExit(f"Not enough data for {args.symbol} {args.timeframe}")

    from trading_agent.execution.simulator import (
        SimulationConfig,
        compute_reality_gap,
        run_strategy_through_simulator,
    )

    cfg = SimulationConfig(
        random_seed=args.seed,
        spread_bps=args.spread_bps,
        taker_fee=args.taker_fee,
        maker_fee=args.maker_fee,
        min_notional=10.0,
        min_qty=0.00001,
    )
    strategy = get_strategy(args.strategy)({})

    result = run_strategy_through_simulator(
        strategy,
        df,
        symbol=args.symbol,
        timeframe=args.timeframe,
        initial_cash=args.initial_cash,
        config=cfg,
    )

    # Idealized backtest metrics (vectorized engine, costs disabled) as the
    # reference for the reality gap.
    from trading_agent.backtest.engine import BacktestEngine

    ideal = BacktestEngine(
        strategy,
        initial_capital=args.initial_cash,
        commission=0.0,
        slippage=0.0,
        spread_bps=0.0,
        timeframe=args.timeframe,
    ).run(df, symbol=args.symbol, timeframe=args.timeframe)

    ref_metrics = {
        "fill_ratio": 1.0,
        "slippage_bps": 0.0,
        "implementation_shortfall_bps": 0.0,
        "trade_count": float(ideal.total_trades),
        "turnover": 0.0,
        "avg_latency_ms": 0.0,
        "spread_cost_quote": 0.0,
        "fees_quote": 0.0,
        "sharpe": float(ideal.sharpe_ratio),
        "total_return_pct": float(ideal.total_return_pct),
        "max_drawdown_pct": float(ideal.max_drawdown_pct),
        "rejected_order_rate": 0.0,
        "partial_fill_rate": 0.0,
    }
    gap = compute_reality_gap(
        environment="execution_simulator_v2",
        reference_environment="idealized_backtest",
        observed=result.metrics.to_dict(),
        reference=ref_metrics,
    )

    report = {
        "head": "Execution Simulator V2 — Wave A evidence",
        "strategy": args.strategy,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "config": cfg.to_dict(),
        "model_versions": result.model_versions,
        "data_manifest": result.data_manifest,
        "data_bars": len(df),
        "execution_metrics": result.metrics.to_dict(),
        "theoretical_alpha_pnl": result.theoretical_alpha_pnl,
        "attribution": result.metrics.attribution.to_dict(),
        "reality_gap": gap.to_dict(),
        "idealized_backtest": {
            "total_return_pct": ideal.total_return_pct,
            "sharpe": ideal.sharpe_ratio,
            "max_drawdown_pct": ideal.max_drawdown_pct,
            "total_trades": ideal.total_trades,
        },
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nReport written to {out}")


if __name__ == "__main__":
    main()
