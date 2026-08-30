#!/usr/bin/env python3
"""CLI for Nested Walk-Forward Optimization (Phase S3).

Usage:
    python scripts/run_nested_wfo.py --strategy ma_adx --symbol SOL/USDT --timeframe 1h
    python scripts/run_nested_wfo.py --portfolio --out data/backtests/wfo
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (
    WFOSpec,
    _default_purge_embargo,
    _get_fold_indices,
    run_nested_wfo_portfolio,
)
from trading_agent.backtest.tournament import (
    CostScenario,
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
)


# Default param grids for strategies
DEFAULT_PARAM_GRIDS: dict[str, dict[str, list[Any]]] = {
    "ma_adx": {
        "fast_ma": [10, 20, 30],
        "slow_ma": [40, 60, 80],
        "adx_period": [14, 20],
        "adx_threshold": [25, 30, 40],
    },
    "enhanced_ma": {
        "fast": [10, 20],
        "slow": [40, 60, 80],
        "signal_ma": [20, 40],
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
    "ma_vol_target": {
        "fast_ma": [20, 30],
        "slow_ma": [60, 80],
        "vol_target": [0.15, 0.20, 0.25],
    },
}


PAPER_ELIGIBLE = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "NEAR/USDT",
    "ZEC/USDT",
]


def build_wfo_spec(
    strategy_id: str,
    symbol: str,
    timeframe: str = "1h",
    train_months: int = 12,
    val_months: int = 3,
    test_months: int = 3,
    step_months: int = 3,
    cost_scenarios: tuple[CostScenario, ...] = (
        SCENARIO_BASE,
        SCENARIO_DOUBLE,
        SCENARIO_SLIPPAGE_STRESS,
    ),
    registry_path: str = "data/wfo/experiments.sqlite3",
    search_family: str = "s3_wfo",
    evaluator_version: str = "v1",
    seed: int = 42,
) -> WFOSpec:
    param_grid = DEFAULT_PARAM_GRIDS.get(strategy_id, {})
    return WFOSpec(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        param_grid=param_grid,
        cost_scenarios=cost_scenarios,
        train_months=train_months,
        val_months=val_months,
        test_months=test_months,
        step_months=step_months,
        registry_path=registry_path,
        search_family=search_family,
        evaluator_version=evaluator_version,
        seed=seed,
        min_oos_trades=30,
        evidence_class="REAL_MARKET",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nested Walk-Forward Optimization (S3)"
    )
    parser.add_argument("--strategy", required=False, help="Strategy ID (e.g., ma_adx)")
    parser.add_argument("--symbol", required=False, help="Symbol (e.g., SOL/USDT)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe (1h, 4h, 1d)")
    parser.add_argument(
        "--portfolio", action="store_true", help="Run for all paper-eligible symbols"
    )
    parser.add_argument(
        "--train-months", type=int, default=12, help="Train window months"
    )
    parser.add_argument(
        "--val-months", type=int, default=3, help="Validation window months"
    )
    parser.add_argument("--test-months", type=int, default=3, help="Test window months")
    parser.add_argument("--step-months", type=int, default=3, help="Step months")
    parser.add_argument(
        "--cost", choices=["1x", "2x", "slip_stress", "all"], default="all"
    )
    parser.add_argument(
        "--out", default="data/backtests/wfo", help="Output root directory"
    )
    parser.add_argument(
        "--registry", default="data/wfo/experiments.sqlite3", help="Registry path"
    )
    parser.add_argument("--search-family", default="s3_wfo", help="Search family name")
    parser.add_argument("--evaluator-version", default="v1", help="Evaluator version")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would run without executing"
    )
    parser.add_argument(
        "--run-holdout",
        action="store_true",
        help="After gates pass, run the independent frozen holdout one-shot (STR-0309)",
    )
    parser.add_argument(
        "--no-real-sensitivity",
        action="store_true",
        help="Skip real cost-2x/slippage/drop-best re-evaluations (STR-0308 framework only)",
    )

    args = parser.parse_args()

    cost_scenarios: tuple[CostScenario, ...]
    if args.cost == "1x":
        cost_scenarios = (SCENARIO_BASE,)
    elif args.cost == "2x":
        cost_scenarios = (SCENARIO_DOUBLE,)
    elif args.cost == "slip_stress":
        cost_scenarios = (SCENARIO_SLIPPAGE_STRESS,)
    else:
        cost_scenarios = (SCENARIO_BASE, SCENARIO_DOUBLE, SCENARIO_SLIPPAGE_STRESS)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    if args.portfolio:
        if not args.strategy:
            parser.error("--strategy required when using --portfolio")
        symbols = PAPER_ELIGIBLE
        specs = [
            build_wfo_spec(
                strategy_id=args.strategy,
                symbol=symbol,
                timeframe=args.timeframe,
                train_months=args.train_months,
                val_months=args.val_months,
                test_months=args.test_months,
                step_months=args.step_months,
                cost_scenarios=cost_scenarios,
                registry_path=args.registry,
                search_family=args.search_family,
                evaluator_version=args.evaluator_version,
                seed=args.seed,
            )
            for symbol in symbols
        ]
    else:
        if not args.strategy or not args.symbol:
            parser.error("--strategy and --symbol required (or use --portfolio)")
        specs = [
            build_wfo_spec(
                strategy_id=args.strategy,
                symbol=args.symbol,
                timeframe=args.timeframe,
                train_months=args.train_months,
                val_months=args.val_months,
                test_months=args.test_months,
                step_months=args.step_months,
                cost_scenarios=cost_scenarios,
                registry_path=args.registry,
                search_family=args.search_family,
                evaluator_version=args.evaluator_version,
                seed=args.seed,
            )
        ]

    if args.dry_run:
        print("DRY RUN - would execute:")
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.strategies.canonical.candidates import build_default_registry

        canonical_registry = build_default_registry()
        for spec in specs:
            param_combos = 1
            for v in spec.param_grid.values():
                param_combos *= len(v)
            descriptor = canonical_registry.describe(spec.strategy_id)
            purge, embargo = _default_purge_embargo(descriptor)
            df = load_ohlcv("binance", spec.symbol, spec.timeframe)
            folds = _get_fold_indices(
                n_bars=df.height,
                timeframe=spec.timeframe,
                train_months=spec.train_months,
                val_months=spec.val_months,
                test_months=spec.test_months,
                step_months=spec.step_months,
                purge=spec.purge_bars or purge,
                embargo=spec.embargo_bars or embargo,
            )
            n_folds = len(folds)
            print(f"  {spec.strategy_id} × {spec.symbol} × {spec.timeframe}")
            print(f"    Data bars: {df.height}; pre-holdout outer folds: {n_folds}")
            print(
                f"    Param combos: {param_combos} × {len(cost_scenarios)} cost scenarios = {param_combos * len(cost_scenarios)} trials/fold"
            )
            print(
                f"    Inner cells: {param_combos * len(cost_scenarios) * n_folds}; "
                f"outer one-shot cells: {n_folds}"
            )
        return

    print(f"Running nested WFO for {len(specs)} spec(s)...")
    results = run_nested_wfo_portfolio(
        specs,
        out_root=out_root,
        run_holdout=args.run_holdout,
        real_sensitivity=not args.no_real_sensitivity,
    )

    # Save summary
    summary_path = out_root / "wfo_summary.json"
    summary = {
        "timestamp": datetime.now().isoformat(),
        "specs": [vars(s) for s in specs],
        "portfolio_verdict": results.verdict,
        "portfolio_passes": results.passes_hard_gates,
        "portfolio_aggregate": results.aggregate_metrics,
        "portfolio_gates": [vars(gate) for gate in results.gate_results],
        "results": [
            {
                "strategy": r.spec.strategy_id,
                "symbol": r.spec.symbol,
                "passes": r.passes_hard_gates,
                "gate_failures": r.gate_failures,
                "aggregate": r.aggregate_metrics,
                "statistical": r.statistical_hardening,
                "n_folds": len(r.outer_results),
                "final_holdout": r.final_holdout,
                "sensitivity_real_computed": r.aggregate_metrics.get(
                    "sensitivity", {}
                ).get("real_computed"),
            }
            for r in results
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSummary saved to {summary_path}")

    # Print final results
    print("\n=== RESULTS ===")
    print(f"Portfolio decision: {results.verdict}")
    for r in results:
        status = "✅ PASS" if r.passes_hard_gates else "❌ FAIL"
        print(
            f"{status} {r.spec.strategy_id} {r.spec.symbol}: "
            f"Sharpe={r.aggregate_metrics.get('median_test_sharpe', 0):.3f} "
            f"Return={r.aggregate_metrics.get('median_test_return_pct', 0):.2f}% "
            f"Trades={r.aggregate_metrics.get('total_test_trades', 0)} "
            f"Folds={r.aggregate_metrics.get('n_outer_folds', 0)}"
        )
        if r.gate_failures:
            for gf in r.gate_failures:
                print(f"    Gate fail: {gf}")

    # Exit code
    sys.exit(0 if results.passes_hard_gates else 1)


if __name__ == "__main__":
    main()
