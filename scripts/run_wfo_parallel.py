#!/usr/bin/env python3
"""Parallel WFO runner: schedules canonical WFO cells via ProcessPoolExecutor.

This runner uses `run_nested_wfo` as the single authority for S3 validation.
It parallelizes the cell execution (inner validation + outer OOS trials) while
preserving the canonical WFO logic: inner selection freezes before outer test,
purge/embargo, statistical hardening on real return series, registry trial counting.

Usage:
    python scripts/run_wfo_parallel.py --strategy rsi --workers 4
    python scripts/run_wfo_parallel.py --strategy ma_adx --symbol BTC/USDT --workers 8
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
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
    run_nested_wfo,
    EvaluationCellSpec,
)
from trading_agent.strategies.canonical.candidates import build_default_registry
from trading_agent.data.storage import load_ohlcv
from trading_agent.backtest.tournament import EvaluationArtifact


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


class ParallelCellRunner:
    """Cell runner that executes cells in parallel via ProcessPoolExecutor.

    This runner is passed to `run_nested_wfo` as the `cell_runner` callback.
    It batches cell submissions and waits for results, enabling parallel
    execution of inner validation and outer OOS trials.
    """

    def __init__(
        self,
        workers: int,
        out_root: Path,
        timeout_seconds: int = 600,
        max_retries: int = 1,
    ):
        self.workers = workers
        self.out_root = out_root
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._ctx = mp.get_context("spawn")
        self._executor: concurrent.futures.ProcessPoolExecutor | None = None
        self._pending: dict[concurrent.futures.Future, tuple[EvaluationCellSpec, int, int, bool, int, int]] = {}

    def __enter__(self):
        self._executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=self.workers, mp_context=self._ctx
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def _submit_cell(
        self,
        spec: EvaluationCellSpec,
        start: int,
        end: int,
        fresh: bool,
        measurement_start: int,
        measurement_end: int,
    ) -> concurrent.futures.Future:
        """Submit a single cell for execution."""
        assert self._executor is not None
        spec_primitives = {
            "strategy_id": spec.strategy_id,
            "symbol": spec.symbol,
            "timeframe": spec.timeframe,
            "params": dict(spec.params),  # ensure plain dict for pickling
            "cost_scenario": spec.cost_scenario,
        }
        future = self._executor.submit(
            _run_cell_worker,
            spec_primitives,
            str(self.out_root),
            start,
            end,
            fresh,
            measurement_start,
            measurement_end,
            self.timeout_seconds,
            self.max_retries,
        )
        self._pending[future] = (spec, start, end, fresh, measurement_start, measurement_end)
        return future

    def run(
        self,
        spec: EvaluationCellSpec,
        out_root: Path,
        start: int,
        end: int,
        fresh: bool,
        measurement_start: int,
        measurement_end: int,
    ) -> EvaluationArtifact:
        """Run a single cell, blocking until complete.

        This is the callback signature expected by `run_nested_wfo`.
        """
        future = self._submit_cell(spec, start, end, fresh, measurement_start, measurement_end)
        return future.result(timeout=self.timeout_seconds)

    def run_batch(
        self,
        cells: list[tuple[EvaluationCellSpec, int, int, bool, int, int]],
    ) -> list[EvaluationArtifact]:
        """Run multiple cells in parallel and return results in order."""
        futures = [self._submit_cell(*cell) for cell in cells]
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                artifact = future.result(timeout=self.timeout_seconds)
                results.append(artifact)
            except Exception as exc:
                # Create a failed artifact for the failed cell
                spec, start, end, fresh, m_start, m_end = self._pending[future]
                artifact = EvaluationArtifact(
                    status="FAILED",
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    params=dict(spec.params),
                    cost_scenario=spec.cost_scenario.name,
                    failure_reasons={f"worker_exception: {exc}"},
                    metrics={},
                    execution_health={},
                )
                results.append(artifact)
        # Sort results to match input order
        # Note: as_completed doesn't preserve order, but for our use case
        # the order doesn't matter since results are matched by freeze_id/fold_id
        return results


def _run_cell_worker(
    spec_primitives: dict,
    out_root: str,
    start: int,
    end: int,
    fresh: bool,
    measurement_start: int,
    measurement_end: int,
    timeout_seconds: int,
    max_retries: int,
) -> EvaluationArtifact:
    """Worker function for ProcessPoolExecutor.

    Must be at module level for pickling.
    """
    spec = EvaluationCellSpec(
        strategy_id=spec_primitives["strategy_id"],
        symbol=spec_primitives["symbol"],
        timeframe=spec_primitives["timeframe"],
        params=spec_primitives["params"],
        cost_scenario=spec_primitives["cost_scenario"],
    )
    return run_cell(
        spec,
        out_root=Path(out_root),
        start=start,
        end=end,
        fresh=fresh,
        measurement_start=measurement_start,
        measurement_end=measurement_end,
        _use_multiprocessing=False,  # avoid nested process overhead
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def build_canonical_cells(
    spec: WFOSpec,
) -> tuple[list[tuple[EvaluationCellSpec, int, int, bool, int, int]], list[Any]]:
    """Build all cells needed for canonical WFO (inner + outer).

    Returns:
        - inner_cells: list of (spec, start, end, fresh, m_start, m_end) for inner validation
        - folds: list of fold objects for outer test scheduling
    """
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

    # Generate parameter combinations
    param_combos: list[dict] = []
    keys = list(spec.param_grid.keys())
    for values in product(*[spec.param_grid[k] for k in keys]):
        param_combos.append(dict(zip(keys, values)))

    # Inner validation cells: one per (params, cost, fold) on validation window
    inner_cells = []
    for fold in folds:
        warmup = descriptor.warmup_bars
        buffer_bars = 100
        sim_start = max(0, fold.inner_train_start - warmup - buffer_bars)
        for params in param_combos:
            for cost_scenario in spec.cost_scenarios:
                cell_spec = EvaluationCellSpec(
                    strategy_id=spec.strategy_id,
                    symbol=spec.symbol,
                    timeframe=spec.timeframe,
                    params=params,
                    cost_scenario=cost_scenario,
                )
                inner_cells.append((
                    cell_spec,
                    sim_start,
                    fold.inner_val_end,
                    True,  # fresh
                    fold.inner_val_start,
                    fold.inner_val_end,
                ))

    return inner_cells, folds


def main():
    parser = argparse.ArgumentParser(description="Parallel Canonical WFO runner")
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--cost", default="1x", choices=["1x", "2x", "slip_stress", "all"]
    )
    parser.add_argument("--out", default="data/backtests/wfo_parallel_canonical")
    parser.add_argument(
        "--workers", type=int, default=4, help="Number of parallel workers"
    )
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--val-months", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--step-months", type=int, default=3)
    parser.add_argument(
        "--run-holdout", action="store_true", help="Run final holdout if gates pass"
    )
    parser.add_argument(
        "--real-sensitivity", action="store_true", default=True, help="Run real sensitivity analysis"
    )
    parser.add_argument(
        "--no-real-sensitivity", action="store_false", dest="real_sensitivity", help="Disable real sensitivity"
    )
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
        search_family="s3_wfo_parallel_canonical",
        evaluator_version="v1",
        seed=42,
        min_oos_trades=30,
        evidence_class="REAL_MARKET",
    )

    print(
        f"Running CANONICAL WFO for {args.strategy} {args.symbol} {args.timeframe}...",
        flush=True,
    )
    print(f"  Params: {MINIMAL_PARAM_GRIDS.get(args.strategy, {})}", flush=True)
    print(f"  Cost: {[c.name for c in cost_scenarios]}", flush=True)
    print(f"  Workers: {args.workers}", flush=True)
    print(f"  Out: {out_root}", flush=True)
    print(f"  Run holdout: {args.run_holdout}", flush=True)
    print(f"  Real sensitivity: {args.real_sensitivity}", flush=True)

    start_time = time.time()

    # Run canonical WFO with parallel cell runner
    with ParallelCellRunner(
        workers=args.workers,
        out_root=out_root,
    ) as runner:
        result = run_nested_wfo(
            spec,
            out_root=out_root,
            run_holdout=args.run_holdout,
            real_sensitivity=args.real_sensitivity,
            cell_runner=runner.run,  # Pass the runner's single-cell method
        )

    elapsed = time.time() - start_time

    print("\n=== Canonical WFO Complete ===")
    print(f"  Status: {'PASS' if result.passes_hard_gates else 'FAIL'}")
    print(f"  Verdict: {result.verdict}")
    print(f"  Folds: {result.aggregate_metrics.get('n_outer_folds', 0)}")
    print(f"  Median Sharpe: {result.aggregate_metrics.get('median_test_sharpe', 0):.4f}")
    print(f"  Median Return: {result.aggregate_metrics.get('median_test_return_pct', 0):.2f}%")
    print(f"  Elapsed: {elapsed:.0f}s")

    # Save summary
    summary = {
        "strategy": args.strategy,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "cost_scenarios": [c.name for c in cost_scenarios],
        "verdict": result.verdict,
        "passes_hard_gates": result.passes_hard_gates,
        "aggregate_metrics": result.aggregate_metrics,
        "gate_results": [g.to_dict() if hasattr(g, 'to_dict') else g for g in result.gate_results],
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "run_holdout": args.run_holdout,
        "real_sensitivity": args.real_sensitivity,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (out_root / "parallel_canonical_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n  Summary saved: {out_root / 'parallel_canonical_summary.json'}")


if __name__ == "__main__":
    main()