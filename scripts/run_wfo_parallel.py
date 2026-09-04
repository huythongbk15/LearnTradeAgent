#!/usr/bin/env python3
"""Parallel WFO runner: spawns multiple cells concurrently via ProcessPoolExecutor.

Same scope as run_wfo_minimal.py but cells run in parallel across CPU cores.
Each cell = 1 backtest run (params × cost × fold), independent.

Expected speedup: 3-4x on 4-core machine, 5-7x on 8-core.

Usage:
    python scripts/run_wfo_parallel.py --workers 4
    python scripts/run_wfo_parallel.py --strategy rsi --workers 6
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.tournament import (
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    CostScenario,
    run_cell,
)
from trading_agent.backtest.nested_wfo import (
    WFOSpec,
    _get_fold_indices,
    _resolve_frozen_holdout_window,
    _default_purge_embargo,
)
from trading_agent.strategies.canonical.candidates import build_default_registry
from trading_agent.data.storage import load_ohlcv
from trading_agent.backtest.tournament import EvaluationCellSpec


# Param grids (same as run_wfo_minimal.py)
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


def get_cost_scenarios(cost_arg: str) -> tuple[CostScenario, ...]:
    if cost_arg == "1x":
        return (SCENARIO_BASE,)
    if cost_arg == "2x":
        return (SCENARIO_DOUBLE,)
    if cost_arg == "slip_stress":
        return (SCENARIO_SLIPPAGE_STRESS,)
    return (SCENARIO_BASE, SCENARIO_DOUBLE, SCENARIO_SLIPPAGE_STRESS)


def build_cells(
    spec: WFOSpec,
    cost_scenarios: tuple[CostScenario, ...],
) -> list[tuple[int, dict, CostScenario, Any]]:
    """Build (fold_id, params, cost_scenario, fold_window) tuples for all cells."""
    df = load_ohlcv("binance", spec.symbol, spec.timeframe)
    n_bars = df.height
    registry_canonical = build_default_registry()
    descriptor = registry_canonical.describe(spec.strategy_id)
    purge, embargo = _default_purge_embargo(descriptor)
    folds = _get_fold_indices(
        n_bars=n_bars,
        timeframe=spec.timeframe,
        train_months=spec.train_months,
        val_months=spec.val_months,
        test_months=spec.test_months,
        step_months=spec.step_months,
        purge=purge,
        embargo=embargo,
    )
    # Drop folds overlapping holdout
    holdout_bars = _resolve_frozen_holdout_window(df, spec)
    if holdout_bars is not None:
        h_start, h_end = holdout_bars
        folds = [f for f in folds if f.outer_test_end <= h_start]
    param_grid = spec.param_grid
    param_combos: list[dict] = []
    keys = list(param_grid.keys())
    for values in product(*[param_grid[k] for k in keys]):
        param_combos.append(dict(zip(keys, values)))
    cells = []
    for fold in folds:
        for params in param_combos:
            for cost in cost_scenarios:
                cells.append((len(cells), params, cost, fold))
    return cells


def _run_cell_inline(
    spec_primitives: dict,
    out_root: Path,
    start: int,
    end: int,
) -> dict:
    """Run one cell inline (no subprocess overhead for fast path).

    Takes primitives dict to avoid MappingProxyType pickle issues when
    used with ProcessPoolExecutor.
    """

    spec = EvaluationCellSpec(
        strategy_id=spec_primitives["strategy_id"],
        symbol=spec_primitives["symbol"],
        timeframe=spec_primitives["timeframe"],
        params=spec_primitives["params"],
        cost_scenario=spec_primitives["cost_scenario"],
    )
    artifact = run_cell(
        spec,
        out_root=out_root,
        start=start,
        end=end,
        fresh=True,
        _use_multiprocessing=False,  # avoid nested process overhead
        timeout_seconds=600,
        max_retries=1,
    )
    return {
        "cell_id": spec_primitives.get("cell_id", 0),
        "status": artifact.status,
        "metrics": dict(artifact.metrics) if artifact.metrics else {},
        "failure_reasons": list(artifact.failure_reasons),
    }


def main():
    parser = argparse.ArgumentParser(description="Parallel WFO scope")
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--cost", default="1x", choices=["1x", "2x", "slip_stress", "all"])
    parser.add_argument("--out", default="data/backtests/wfo_parallel")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--val-months", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--step-months", type=int, default=3)
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    cost_scenarios = get_cost_scenarios(args.cost)

    spec = WFOSpec(
        strategy_id=args.strategy,
        symbol=args.symbol,
        timeframe=args.timeframe,
        param_grid=MINIMAL_PARAM_GRIDS.get(args.strategy, {}),
        cost_scenarios=cost_scenarios,
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        step_months=args.step_months,
        registry_path="data/wfo/experiments_parallel.sqlite3",
        search_family="s3_wfo_parallel",
        evaluator_version="v1",
        seed=42,
        min_oos_trades=30,
        evidence_class="REAL_MARKET",
    )

    print(f"Building cells for {args.strategy} {args.symbol} {args.timeframe}...", flush=True)
    cells = build_cells(spec, cost_scenarios)
    n_cells = len(cells)
    print(f"  Params: {MINIMAL_PARAM_GRIDS.get(args.strategy, {})}", flush=True)
    print(f"  Cost: {[c.name for c in cost_scenarios]}", flush=True)
    print(f"  Cells: {n_cells}", flush=True)
    print(f"  Workers: {args.workers}", flush=True)
    print(f"  Out: {out_root}", flush=True)

    # Run cells in parallel
    completed = 0
    failed = 0
    start_time = time.time()
    futures_map: dict[concurrent.futures.Future, int] = {}

    print(f"Submitting {n_cells} cells to {args.workers} workers...", flush=True)
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as executor:
        for cell_id, params, cost, fold in cells:
            spec_primitives = {
                "cell_id": cell_id,
                "strategy_id": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "params": dict(params),  # ensure plain dict, not MappingProxyType
                "cost_scenario": cost,
            }
            future = executor.submit(
                _run_cell_inline,
                spec_primitives,
                out_root,
                fold.outer_test_start,
                fold.outer_test_end,
            )
            futures_map[future] = cell_id

        for future in concurrent.futures.as_completed(futures_map):
            cell_id = futures_map[future]
            try:
                result = future.result()
                if result["status"] == "COMPLETED":
                    completed += 1
                else:
                    failed += 1
                elapsed = time.time() - start_time
                rate = (completed + failed) / max(elapsed, 0.1)
                print(
                    f"  [{completed+failed}/{n_cells}] cell={cell_id} "
                    f"status={result['status']} "
                    f"elapsed={elapsed:.0f}s rate={rate:.2f}cells/s"
                )
            except Exception as exc:
                failed += 1
                print(f"  [{completed+failed}/{n_cells}] cell={cell_id} EXCEPTION: {exc}")

    elapsed = time.time() - start_time
    print("\n=== Done ===")
    print(f"  Completed: {completed}/{n_cells}")
    print(f"  Failed: {failed}/{n_cells}")
    print(f"  Elapsed: {elapsed:.0f}s")
    if n_cells > 0:
        print(f"  Avg per cell: {elapsed/n_cells:.1f}s")
    # Save summary
    summary = {
        "strategy": args.strategy,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "cost_scenarios": [c.name for c in cost_scenarios],
        "n_cells": n_cells,
        "completed": completed,
        "failed": failed,
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (out_root / "parallel_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
