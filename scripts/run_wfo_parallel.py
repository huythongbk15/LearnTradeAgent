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
import hashlib
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, replace
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
    FaultProfile,
    run_cell,
)
from trading_agent.backtest.nested_wfo import (
    WFOSpec,
    _get_fold_indices,
    _resolve_frozen_holdout_window,
    _default_purge_embargo,
    run_nested_wfo,
    EvaluationCellSpec,
    PortfolioGatePolicy,
    _build_portfolio_selection_result,
)
from trading_agent.strategies.canonical.candidates import build_default_registry
from trading_agent.data.storage import load_research_ohlcv as load_ohlcv
from trading_agent.backtest.tournament import EvaluationArtifact
from trading_agent.backtest.campaign_execution import (
    CampaignCellRunner, CACHE_VERSION, _durable_json, execution_environment,
    simulator_runtime_config, _window_identity,
)


# Param grids (aligned with R01 parameter schema in candidates.py)
# ── Param grids ─────────────────────────────────────────────────────────────
# For single-asset strategies: look up from param_grids.py (canonical source)
# For cross-sectional strategies: use cs_variant suffix
from trading_agent.research.param_grids import SINGLE_ASSET_GRIDS, CROSS_SECTIONAL_GRIDS

# Map CLI strategy names to catalog param grid keys
_STRATEGY_GRID_MAP: dict[str, str] = {
    "ma_adx": "ma_adx",
    "rsi": "rsi",
    "bbands": "bbands",
    "enhanced_ma": "enhanced_ma",
    "ma_vol_target": "vol_target",
    "regime_switching": "regime_ensemble",
    "trend_pullback": "trend_pullback",
    "range_mean_reversion": "range_mean_reversion",
    "volatility_breakout": "volatility_breakout",
    "funding_carry": "funding_carry",
    # Cross-sectional variants
    "cross_sectional_momentum_lo": ("cross_sectional_momentum", "long_only"),
    "cross_sectional_momentum_ls": ("cross_sectional_momentum", "long_short"),
    "stat_arbitrage_lo": ("stat_arbitrage", "long_only"),
    "stat_arbitrage_ls": ("stat_arbitrage", "long_short"),
}

# Default param grids matching existing behavior (used for strategies not in param_grids)
MINIMAL_PARAM_GRIDS: dict[str, dict[str, list]] = {
    "ma_adx": {
        "fast_period": [10, 20, 30],
        "slow_period": [40, 60, 80],
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
        "fast_period": [10, 20],
        "slow_period": [40, 60, 80],
        "adx_period": [14],
        "adx_threshold": [30],
        "require_close_above_slow": [False],
        "momentum_period": [0],
        "atr_period": [14],
        "atr_sl_mult": [2.0],
        "atr_tp_mult": [3.0],
        "max_dd_pct": [0.15],
        "dd_cooldown_bars": [0],
        "dd_recovery_pct": [0.03],
        "trailing_atr_mult": [0.0],
        "risk_per_trade": [0.02],
    },
    "ma_vol_target": {
        "fast_period": [20, 30],
        "slow_period": [60, 80],
    },
    "regime_switching": {
        "regime_method": ["rule_based", "hybrid"],
        "min_confidence": [0.4, 0.65],
        "regime_smoothing": [2, 3],
        "base_position_pct": [0.1, 0.2],
    },
    # S1: Trend Pullback
    "trend_pullback": {
        "ma_fast": [10, 20, 30],
        "ma_slow": [80, 120],
        "adx_threshold": [25, 30],
        "adx_period": [14],
    },
    # S2: Range Mean Reversion
    "range_mean_reversion": {
        "vwap_window": [14, 20, 30],
        "zscore_entry": [1.5, 2.0, 2.5],
        "zscore_exit": [0.5],
        "bb_lookback": [20],
        "bb_std": [2.0],
    },
    # S3: Volatility Expansion Breakout
    "volatility_breakout": {
        "bb_period": [14, 20, 21],
        "bb_std": [2.0],
        "compression_percentile": [0.03, 0.05],
        "atr_spike_mult": [1.5, 2.0],
        "max_hold_bars": [10, 20],
    },
    # S5: Funding Carry
    # Aligned with SINGLE_ASSET_GRIDS in param_grids.py (canonical source)
    "funding_carry": {
        "funding_entry_threshold": [-0.0001, -0.00008, -0.00005, -0.00003],
        "funding_exit_threshold": [0.0, 0.00005],
        "max_hold_periods": [0],
        "vol_window": [20],
        "fr_lookback_bars": [22],
    },
    # S4: Cross-sectional Momentum (long-only)
    "cross_sectional_momentum_lo": {
        "lookback_days": [60, 90],
        "fast_period": [10, 20, 30],
        "slow_period": [40, 60],
        "rsi_period": [14],
    },
    # S4: Cross-sectional Momentum (long-short)
    "cross_sectional_momentum_ls": {
        "lookback_days": [60, 90],
        "fast_period": [10, 20, 30],
        "slow_period": [40, 60],
    },
    # S8: Stat Arbitrage (long-only)
    "stat_arbitrage_lo": {
        "zscore_entry": [1.5, 2.0],
        "zscore_exit": [0.5],
        "lookback_days": [20],
    },
    # S8: Stat Arbitrage (long-short)
    "stat_arbitrage_ls": {
        "zscore_entry": [2.0, 2.5],
        "zscore_exit": [0.5],
        "lookback_days": [20],
    },
}


def get_param_grid_for_strategy(strategy_id: str) -> dict[str, list]:
    """Get param grid for a strategy, falling back to MINIMAL_PARAM_GRIDS."""
    mapping = _STRATEGY_GRID_MAP.get(strategy_id, strategy_id)
    if isinstance(mapping, tuple):
        grid_key, cs_variant = mapping
        grid = CROSS_SECTIONAL_GRIDS.get((grid_key, cs_variant))
        if grid:
            return grid
        # Fallback
        return MINIMAL_PARAM_GRIDS.get(strategy_id, {})
    # Single-asset: check SINGLE_ASSET_GRIDS first
    grid = SINGLE_ASSET_GRIDS.get(mapping)
    if grid:
        return grid
    return MINIMAL_PARAM_GRIDS.get(strategy_id, {})


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
        execution_context: dict | None = None,
    ):
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError("workers must be 1..4")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        self.workers = workers
        self.out_root = Path(out_root).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.execution_context = json.loads(json.dumps(execution_context or {}, allow_nan=False))
        self._ctx = mp.get_context("spawn")
        self._executor: concurrent.futures.ProcessPoolExecutor | None = None
        self._pending: dict[
            concurrent.futures.Future,
            tuple[EvaluationCellSpec, int, int, bool, int, int],
        ] = {}

    def __enter__(self):
        if self.workers <= 1:
            # Sequential mode — avoids spawn-context issues in restricted envs
            self._executor = None
        else:
            self._executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=self.workers, mp_context=self._ctx
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._executor:
            # Each cell's isolated attempt enforces its own timeout/retry.
            # A Future timeout alone cannot stop a running process-pool task.
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

    @staticmethod
    def _primitives(spec):
        return {
            "strategy_id": spec.strategy_id, "symbol": spec.symbol,
            "timeframe": spec.timeframe, "params": dict(spec.params),
            "cost_scenario": spec.cost_scenario, "fault": asdict(spec.fault),
        }

    def _inline(self, cell):
        spec, start, end, fresh, m_start, m_end = cell
        return _run_cell_worker(
            self._primitives(spec), str(self.out_root), start, end, fresh,
            m_start, m_end, self.timeout_seconds, self.max_retries,
            self.execution_context,
        )

    def _transport_failure(self, cell, exc):
        from trading_agent.backtest.tournament import _failed_artifact
        spec, start, end, _fresh, m_start, m_end = cell
        artifact = _failed_artifact(
            spec, None, f"worker_transport_exception:{type(exc).__name__}:{exc}",
            measurement_window=(m_start, m_end) if m_start is not None and m_end is not None else None,
            simulation_window=(start, end),
        )
        directory = self.out_root / "cell_execution" / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        _write_execution_record(directory / "transport_failure.json", {
            "execution_context": self.execution_context, "artifact": artifact.to_dict(),
            "status": "FAILED", "live_authorized": False,
        })
        return artifact

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
        spec_primitives = self._primitives(spec)
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
            self.execution_context,
        )
        self._pending[future] = (
            spec,
            start,
            end,
            fresh,
            measurement_start,
            measurement_end,
        )
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
        When ``workers <= 1`` we execute inline (no process pool).
        """
        if Path(out_root).resolve() != self.out_root:
            raise ValueError("cell output root differs from runner root")
        cell = (spec, start, end, fresh, measurement_start, measurement_end)
        if self._executor is None:
            return self._inline(cell)
        future = self._submit_cell(
            spec, start, end, fresh, measurement_start, measurement_end
        )
        try:
            return future.result()  # Attempt timeouts are enforced inside run_cell.
        except Exception as exc:
            return self._transport_failure(cell, exc)
        finally:
            self._pending.pop(future, None)

    # Canonical WFO dispatches outer OOS through a callable, not run_batch.
    # Without this binding the outer path silently falls back to run_cell.
    __call__ = run

    def run_batch(
        self,
        cells: list[tuple[EvaluationCellSpec, int, int, bool, int, int]],
    ) -> list[EvaluationArtifact]:
        """Run multiple cells and return results in order.

        In sequential mode (``workers <= 1``) cells run one-by-one inline.
        """
        if self._executor is None:
            return [self._inline(cell) for cell in cells]
        futures = {self._submit_cell(*cell): index for index, cell in enumerate(cells)}
        results = [None] * len(cells)
        # Consume promptly in completion order; return in deterministic plan order.
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = self._transport_failure(cells[index], exc)
            finally:
                self._pending.pop(future, None)
        return results


def _write_execution_record(path, payload):
    """Exclusive, fsynced record; a partial file is never a resume certificate."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


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
    execution_context: dict | None = None,
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
        fault=FaultProfile(**spec_primitives.get("fault", {})),
    )
    directory = Path(out_root) / "cell_execution" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    request = {
        "cell_id": spec.cell_id, "strategy_id": spec.strategy_id,
        "symbol": spec.symbol, "timeframe": spec.timeframe,
        "params": dict(spec.params), "cost": spec.cost_scenario.to_dict(),
        "fault": asdict(spec.fault), "simulation_window": [start, end],
        "measurement_window": [measurement_start, measurement_end], "fresh": fresh,
        "timeout_seconds": timeout_seconds, "max_retries": max_retries,
        "execution_context": execution_context or {},
        "started_at": datetime.now(UTC).isoformat(), "worker_pid": os.getpid(),
        "live_authorized": False,
    }
    _write_execution_record(directory / "started.json", request)
    started = time.perf_counter()
    try:
        artifact = run_cell(
            spec, out_root=Path(out_root), start=start, end=end, fresh=fresh,
            measurement_start=measurement_start, measurement_end=measurement_end,
            _use_multiprocessing=True,  # Enforce timeout, stop timed-out child BEFORE retry.
            timeout_seconds=timeout_seconds, max_retries=max_retries,
        )
    except Exception as exc:
        from trading_agent.backtest.tournament import _failed_artifact
        artifact = _failed_artifact(
            spec, None, f"worker_exception:{type(exc).__name__}:{exc}",
            measurement_window=(measurement_start, measurement_end)
            if measurement_start is not None and measurement_end is not None else None,
            simulation_window=(start, end),
        )
    _write_execution_record(directory / "result.json", {
        "request": request, "artifact": artifact.to_dict(), "status": artifact.status,
        "elapsed_sec": time.perf_counter() - started,
        "finished_at": datetime.now(UTC).isoformat(), "live_authorized": False,
        "scope": "execution_receipt_not_qualification_or_resume_certificate",
    })
    return artifact


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
    from trading_agent.strategies.canonical.candidates import campaign_history_contract
    history = campaign_history_contract(spec.strategy_id, spec.param_grid)
    descriptor = replace(descriptor, warmup_bars=history["warmup_bars"])
    purge, embargo = _default_purge_embargo(descriptor)
    purge = spec.purge_bars or purge
    embargo = spec.embargo_bars or embargo
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
        h_start, _h_end, _manifest = holdout_bars
        folds = [f for f in folds if f.outer_test_end <= h_start]
    if not folds:
        raise ValueError("No eligible pre-holdout folds")

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
                inner_cells.append(
                    (
                        cell_spec,
                        sim_start,
                        fold.inner_val_end,
                        True,  # fresh
                        fold.inner_val_start,
                        fold.inner_val_end,
                    )
                )

    return inner_cells, folds


TRIAL_IDENTITY_CONTRACT = {
    "version": 2, "candidate_identity": "unchanged_canonical_ExperimentSpec",
    "evaluation_identity": "candidate_fold_phase_full_protocol_execution_context",
    "legacy_action": "retain_without_reuse_fresh_evaluation_only",
    "burden": "candidate_x_context_plus_legacy_bucket_no_reset",
}


def _trial_execution_context(protocol):
    if protocol.get("trial_identity_contract") != TRIAL_IDENTITY_CONTRACT:
        raise ValueError("qualification requires trial identity contract v2; do not upgrade legacy protocols")
    exec_config = protocol["execution"]
    rt = dict(exec_config.get("runtime_config", {}))
    rt["cell_timeout_seconds"] = exec_config.get("cell_timeout", rt.get("cell_timeout_seconds", 300))
    rt["max_retries"] = exec_config.get("max_retries", rt.get("max_retries", 1))
    return {"trial_identity_version": 2, "protocol_fingerprint": protocol["fingerprint"],
            "source": protocol["source"], "environment": exec_config["environment"],
            "runtime_config": rt}


def audit_parallel_trial_history(spec, protocol, folds):
    """Read-only, scoped early conflict check, NOT metric/replay acceptance.

    Candidate IDs use canonical WFO's hashes, never display-name matching.
    OUTER_OOS checks cover potential selections conservatively. The atomic
    append remains authoritative for metrics, full metadata and concurrent writes.
    """
    import sqlite3
    from trading_agent.backtest import nested_wfo as wfo
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.provenance import evaluation_identity
    from trading_agent.research.trials import (
        ExperimentSpec, param_hash, search_space_hash, execution_scope_identity,
        evaluation_scope_from_metadata,
    )

    protocol_body = {k: v for k, v in protocol.items() if k != "fingerprint"}
    if (protocol["spec"] != spec.to_dict()
            or protocol["folds"] != [asdict(fold) for fold in folds]
            or protocol["fingerprint"] != content_hash(protocol_body)):
        raise ValueError("trial preflight protocol/spec/fold mismatch")
    context = _trial_execution_context(protocol)
    current_scope = execution_scope_identity(context)["context_sha256"]
    strategy_sha = wfo._compute_strategy_code_sha(spec.strategy_id)
    data_sha = wfo._compute_data_manifest_sha(spec.symbol, spec.timeframe)
    feature_sha = wfo._compute_feature_schema_hash(build_default_registry().describe(spec.strategy_id))
    space_sha = search_space_hash(spec.param_grid)
    candidates = {}
    for params in wfo._generate_param_combinations(spec.param_grid):
        for cost in spec.cost_scenarios:
            candidate = ExperimentSpec.build(
                strategy_name=spec.strategy_id, strategy_code_sha=strategy_sha,
                data_manifest_sha=data_sha, feature_schema_hash=feature_sha,
                params_hash=param_hash({**params, "cost_scenario": cost.name}),
                search_family=spec.search_family, search_space_hash=space_sha,
                target_horizon=f"{spec.timeframe}_wfo", evaluator_version=spec.evaluator_version, seed=spec.seed,
            )
            candidates[candidate.experiment_id] = (params, cost)
    path = Path(spec.registry_path).resolve()
    report = {"version": 2, "scope": "planned_candidate_fold_context_only",
              "protocol_fingerprint": protocol["fingerprint"], "registry_path": str(path),
              "candidate_ids": sorted(candidates), "total_registry_rows": 0,
              "matched_rows": [], "conflicts": [], "current_context_rows": 0,
              "retained_history_rows": 0, "metrics_verified": False,
              "outer_scope": "all_potential_selected_candidates", "live_authorized": False}
    if not path.exists():
        report["status"] = "NO_MATCHING_HISTORY"
        return report
    # URI mode=ro never initializes/migrates or changes append-only rows. A
    # transaction gives a single consistent snapshot, including committed WAL.
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        report["total_registry_rows"] = connection.execute("SELECT COUNT(*) FROM evaluation_records").fetchone()[0]
        rows = []
        for candidate_id in sorted(candidates):
            rows.extend(dict(row) for row in connection.execute(
                "SELECT * FROM evaluation_records WHERE experiment_id=? ORDER BY evaluation_id", (candidate_id,)))
    fold_map = {fold.fold_id: fold for fold in folds}
    groups = {}
    for row in rows:
        if row["fold_id"] not in fold_map or row["trial_phase"] not in ("INNER_VALIDATION", "OUTER_OOS"):
            continue
        key = (row["experiment_id"], row["fold_id"], row["trial_phase"])
        groups.setdefault(key, []).append(row)
    for key, records in sorted(groups.items()):
        candidate_id, fold_id, phase = key
        params, cost = candidates[candidate_id]
        expected = evaluation_identity(
            strategy_id=spec.strategy_id, symbol=spec.symbol, timeframe=spec.timeframe,
            strategy_code_sha=strategy_sha, data_manifest_sha=data_sha, feature_schema_hash=feature_sha,
            search_space_hash=space_sha, evaluator_version=spec.evaluator_version, policy_version="v1",
            cost_scenario=cost.name, window_kind=phase, seed=spec.seed,
            commit_sha=protocol["source"]["code_commit"], extra={"execution_context": context},
        )
        scoped_entries = []
        for row in records:
            reasons = []
            try:
                metadata = json.loads(row["metadata_json"])
                scope = evaluation_scope_from_metadata(metadata)
                identity = metadata.get("evaluation_identity")
            except (ValueError, TypeError):
                identity = None
                scope = "INVALID"
                reasons.append("invalid_execution_scope_or_metadata")
            current = scope == current_scope
            if (scope not in (None, "INVALID", current_scope)
                    and metadata["evaluation_execution_identity"]["protocol_fingerprint"] == context["protocol_fingerprint"]):
                reasons.append("same_protocol_different_execution_context")
            if current:
                report["current_context_rows"] += 1
                if not isinstance(identity, dict):
                    reasons.append("missing_or_invalid_evaluation_identity")
                else:
                    reasons.extend("identity_mismatch:" + field for field, value in expected.items()
                                   if identity.get(field) != value)
                if row["environment_hash"] != wfo._compute_environment_hash(spec, fold_map[fold_id]):
                    reasons.append("fold_environment_mismatch")
            elif scope != "INVALID":
                report["retained_history_rows"] += 1
            entry = {"evaluation_id": row["evaluation_id"], "experiment_id": candidate_id,
                     "fold_id": fold_id, "trial_phase": phase, "reasons": reasons,
                     "execution_scope": scope,
                     "history_class": "CURRENT_CONTEXT" if current else "LEGACY_UNSCOPED" if scope is None else
                     "INVALID" if scope == "INVALID" else "HISTORICAL_CONTEXT"}
            report["matched_rows"].append(entry)
            scoped_entries.append(entry)
        # Multiple contexts are legitimate NEW evaluations. Duplicates within
        # the same versioned scope remain corruption. Legacy repetitions retain
        # their burden but cannot be reused or relabeled as current evidence.
        counts = Counter(entry["execution_scope"] for entry in scoped_entries)
        for entry in scoped_entries:
            if entry["execution_scope"] not in (None, "INVALID") and counts[entry["execution_scope"]] > 1:
                entry["reasons"].append("duplicate_execution_scope_tuple")
            if entry["reasons"]:
                report["conflicts"].append(entry)
    report["snapshot_digest"] = content_hash(rows)
    report["status"] = ("BLOCKED_HISTORY_CONFLICT" if report["conflicts"] else
                        "CONTEXT_COMPATIBLE_PENDING_EVALUATION" if report["current_context_rows"] else
                        "HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED" if report["retained_history_rows"] else "NO_MATCHING_HISTORY")
    return report


def prepare_parallel_protocol(spec, out_root, *, workers, cell_timeout, reuse_from=None, validation_budget=None):
    """Freeze effective inputs before evaluation; never upgrade a legacy packet."""
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    from trading_agent.backtest.native_gate_contract import freeze_native_gate_contract
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract
    from trading_agent.backtest.reporting import assess_ohlcv, timeframe_delta
    from trading_agent.strategies.canonical.candidates import campaign_history_contract

    if any(out_root.rglob("report.json")) or any(
        (out_root / name).exists()
        for name in ("research_protocol.json", "trial_history_preflight.json", "trial_history_startup.json",
                     "wfo_decision.json", "selection_contract.json",
                     "execution_started.json", "execution_finished.json", "exact_coverage.json",
                     "parallel_canonical_summary.json", "worker_stdout.log", "worker_stderr.log",
                     "cell_execution", "cell_attempts", "cell_resume_cache", "cell_locks",
                     "reused_reports", "cell_failures", "sensitivity", "inner_selection_freezes", "outer_one_shot")
    ):
        raise ValueError("Use a new output directory; existing research packet is immutable")
    binding = json.loads(os.environ.get("TRADING_FROZEN_RESEARCH_DATA", "null"))
    if not isinstance(binding, dict):
        raise ValueError("Parallel research requires an exact frozen input binding")
    # The loader verifies bytes and subject, not merely a caller's declarations.
    frame = load_ohlcv("binance", spec.symbol, spec.timeframe)
    quality = assess_ohlcv(frame, expected_interval=timeframe_delta(spec.timeframe), gap_policy="reject")
    if not quality.accepted:
        raise ValueError(f"Preflight data quality rejected: {quality.status}")
    cells, folds = build_canonical_cells(spec)
    selection = freeze_selection_contract(
        min_validation_trades=30, min_validation_sharpe=0.0,
    )
    body = {
        "version": 1,
        "authority": "run_nested_wfo",
        "trial_identity_contract": dict(TRIAL_IDENTITY_CONTRACT),
        "spec": spec.to_dict(),
        "input_binding": binding,
        "input_bars": frame.height,
        "input_quality": quality.to_dict(),
        "sensitivity_rule": {
            "version": 1, "fold_scope": "all_selected_outer_folds",
            "scenarios": ["cost_2x", "slippage_stress", "delay_1_bar"],
            "neighbors": "adjacent_grid_values_each_axis_with_at_least_two_values",
            "startup": "fresh_at_outer_test_start", "missing_action": "INCOMPLETE",
        },
        "input_first_timestamp": str(frame["timestamp"][0]),
        "input_last_timestamp": str(frame["timestamp"][-1]),
        "source": capture_campaign_source(ROOT),
        "history": campaign_history_contract(spec.strategy_id, spec.param_grid),
        "native_gate_contract": freeze_native_gate_contract(spec, run_holdout=False),
        "selection_contract": selection,
        "folds": [asdict(fold) for fold in folds],
        "expected_inner_cells": [
            {"cell_id": cell.cell_id, "params": dict(cell.params),
             "cost": cell.cost_scenario.to_dict(), "simulation_window": [start, end],
             "measurement_window": [m_start, m_end]}
            for cell, start, end, _fresh, m_start, m_end in cells
        ],
        "execution": {
            "workers": workers, "cell_timeout": cell_timeout, "max_retries": 1,
            "timeout_scope": "each_isolated_attempt_terminated_before_retry",
            "receipt_scope": "inner_selected_outer_cache_v2_fresh_packet_only",
            "cache_version": CACHE_VERSION,
            "environment": execution_environment(),
            "runtime_config": simulator_runtime_config(),
            "reuse_scope": "inner_validation_and_selected_outer_only",
            "sensitivity_reuse": False,
        },
        "live_authorized": False,
    }
    if validation_budget is not None:
        if (workers != 1 or not 1 <= cell_timeout <= 180 or reuse_from is not None
                or set(validation_budget) != {"total_timeout_seconds", "max_rss_mb"}
                or type(validation_budget["total_timeout_seconds"]) is not int
                or not 1 <= validation_budget["total_timeout_seconds"] <= 1800
                or type(validation_budget["max_rss_mb"]) is not int
                or not 128 <= validation_budget["max_rss_mb"] <= 4096):
            raise ValueError("invalid validation budget or unsupported reuse")
        body["execution"]["max_retries"] = 0
        body["validation_budget"] = dict(validation_budget)
        body["scope"] = "inner_validation_only_not_qualification"
    protocol = {**body, "fingerprint": content_hash(body)}
    # Research protocol is already deterministic and binds the full execution
    # context. Do not add an unhashed derived field or relax source/dirty binding.
    if reuse_from is not None:
        source = Path(reuse_from).resolve()
        destination = Path(out_root).resolve()
        if source.is_relative_to(destination) or destination.is_relative_to(source):
            raise ValueError("reuse requires disjoint source and destination packets")
        path = (source / "research_protocol.json").resolve()
        if not path.is_relative_to(source):
            raise ValueError("reuse protocol escapes source packet")
        previous = json.loads(path.read_bytes())
        if previous != protocol:
            raise ValueError("reuse protocol mismatch; legacy/source/config/window changes require a fresh campaign")
    audit = audit_parallel_trial_history(spec, protocol, folds)
    _durable_json(out_root / "trial_history_preflight.json", audit)
    if audit["conflicts"]:
        raise ValueError("trial history conflict before evaluation; inspect trial_history_preflight.json; do not reset registry")
    _durable_json(out_root / "research_protocol.json", protocol)
    return protocol, folds


class QualificationCellRunner(CampaignCellRunner):
    """Cold recovery of exact inner/selected-outer cells into a new packet.

    Sensitivity remains canonical measured execution, never skipped on cache hit.
    No same-packet qualification replay or copied executable state is supported.
    """

    def __init__(self, out_root, protocol, folds, *, reuse_from=None):
        self.protocol = protocol
        self.folds = folds
        execution = protocol["execution"]
        self.trial_context = _trial_execution_context(protocol)
        super().__init__(out_root, {
            "source": protocol["source"], "data": protocol["input_binding"],
            "plan": protocol["fingerprint"], "history": protocol["history"],
            "runtime_config": execution["runtime_config"],
            "require_frozen_input": True,
        }, workers=execution["workers"], reuse_from=reuse_from, runner=self._run_measured)
        if self.environment != execution["environment"]:
            raise ValueError("qualification runtime environment differs from frozen protocol")
        declared = dict(protocol["spec"])
        declared["cost_scenarios"] = tuple(CostScenario(**cost) for cost in declared["cost_scenarios"])
        audit = audit_parallel_trial_history(WFOSpec(**declared), protocol, folds)
        _durable_json(self.root / "trial_history_startup.json", audit)
        if audit["conflicts"]:
            raise ValueError("trial history conflict at runner startup; inspect trial_history_startup.json; do not reset registry")

    def _run_measured(self, spec, **kwargs):
        return _run_cell_worker(
            ParallelCellRunner._primitives(spec), str(kwargs["out_root"]),
            kwargs["start"], kwargs["end"], kwargs["fresh"],
            kwargs["measurement_start"], kwargs["measurement_end"],
            self.protocol["execution"]["cell_timeout"], self.protocol["execution"]["max_retries"],
            {"protocol_fingerprint": self.protocol["fingerprint"],
             "source": self.protocol["source"], "input_binding": self.protocol["input_binding"]},
        )

    def __call__(self, spec, **kwargs):
        from trading_agent.backtest.campaign_evidence import content_hash
        stage = kwargs.pop("_cache_stage", "OUTER_OOS")
        if any(getattr(spec, key) != self.protocol["spec"][key] for key in ("strategy_id", "symbol", "timeframe")):
            raise ValueError("recovery cell subject differs from protocol")
        row = {"cell_id": spec.cell_id, "params": dict(spec.params),
               "cost": spec.cost_scenario.to_dict(),
               "simulation_window": [kwargs.get("start"), kwargs.get("end")],
               "measurement_window": [kwargs.get("measurement_start"), kwargs.get("measurement_end")]}
        if kwargs.get("fresh") is not True or spec.fault.active:
            raise ValueError("qualification recovery requires fresh planned cells without faults")
        if stage == "INNER_VALIDATION":
            if content_hash(row) not in {content_hash(item) for item in self.protocol["expected_inner_cells"]}:
                raise ValueError("unplanned validation recovery cell")
        elif stage == "OUTER_OOS":
            fold = next((fold for fold in self.folds
                         if row["simulation_window"] == row["measurement_window"]
                         == [fold.outer_test_start, fold.outer_test_end]), None)
            if fold is None:
                raise ValueError("unplanned outer recovery window")
            decision_path = self.root / f"selection_decision_{fold.fold_id}.json"
            decision = json.loads(decision_path.read_bytes())
            selected = decision.get("selected") or {}
            if (decision.get("decision") != "SELECT" or selected.get("params") != dict(spec.params)
                    or selected.get("cost_scenario") != spec.cost_scenario.name):
                raise ValueError("outer recovery differs from frozen validation selection")
            if self.reuse_root is not None:
                controls = self.protocol["execution"]
                window = _window_identity({**kwargs, "timeout_seconds": controls["cell_timeout"],
                                           "max_retries": controls["max_retries"]})
                cache_key = content_hash(self._identity(spec, window, stage))
                source_cache = (self.reuse_root / "cell_resume_cache" / f"{cache_key}.json").resolve()
                if not source_cache.is_relative_to(self.reuse_root):
                    raise ValueError("outer source cache escapes reuse packet")
                has_outer_cache = source_cache.exists()
                previous = (self.reuse_root / decision_path.name).resolve()
                if not previous.is_relative_to(self.reuse_root):
                    raise ValueError("source selection escapes reuse packet")
                if previous.exists() and json.loads(previous.read_bytes()) != decision:
                    raise ValueError("source selection changed on cold recovery")
                if has_outer_cache and not previous.exists():
                    raise ValueError("outer cache missing source selection evidence")
                from trading_agent.backtest.nested_wfo import InnerSelectionFreeze
                freezes = []
                for path in self.root.glob("inner_selection_freezes/*.json"):
                    payload = json.loads(path.read_bytes())
                    freeze = InnerSelectionFreeze.from_dict(payload)
                    if freeze.fold_id == fold.fold_id:
                        if payload.get("freeze_id") != freeze.freeze_id:
                            raise ValueError("destination selection freeze hash mismatch")
                        freezes.append(freeze)
                if len(freezes) != 1:
                    raise ValueError("outer recovery requires exactly one frozen selection")
                freeze = freezes[0]
                old_path = (self.reuse_root / "inner_selection_freezes" / f"{freeze.freeze_id.removeprefix('sha256:')}.json").resolve()
                if not old_path.is_relative_to(self.reuse_root):
                    raise ValueError("source freeze escapes reuse packet")
                if has_outer_cache and not old_path.exists():
                    raise ValueError("outer cache missing source selection freeze")
                if old_path.exists():
                    previous_freeze = json.loads(old_path.read_bytes())
                    old_identity = {key: value for key, value in previous_freeze.items() if key != "created_at"}
                    new_identity = {key: value for key, value in freeze.to_dict().items() if key != "created_at"}
                    if old_identity != new_identity:
                        raise ValueError("source selection freeze changed on cold recovery")
        else:
            raise ValueError("unregistered qualification recovery stage")
        return super().__call__(spec, **kwargs, _cache_stage=stage,
                                timeout_seconds=self.protocol["execution"]["cell_timeout"],
                                max_retries=self.protocol["execution"]["max_retries"])


def verify_parallel_result_coverage(spec, result, protocol, folds, out_root):
    """Compare exact planned validation tuples and selected OOS folds to raw sources.

    This is coverage/report attribution, not independent metric or alpha acceptance.
    """
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.selection_eligibility import verify_validation_reports
    from trading_agent.backtest.tournament import _artifact_from_report
    from trading_agent.backtest.nested_wfo import _sensitivity_requests, _sensitivity_receipt

    body = {key: value for key, value in protocol.items() if key != "fingerprint"}
    if (content_hash(body) != protocol["fingerprint"]
            or protocol["spec"] != spec.to_dict()
            or protocol["folds"] != [asdict(fold) for fold in folds]):
        raise ValueError("Protocol identity mismatch")
    expected_folds = [fold.fold_id for fold in folds]
    if (
        [inner.fold_id for inner in result.inner_results] != expected_folds
        or [outer.fold_id for outer in result.outer_results] != expected_folds
    ):
        raise ValueError("Missing, duplicate or unexpected fold")
    observed = []
    reports = []
    root = out_root.resolve()
    for fold, inner, outer in zip(folds, result.inner_results, result.outer_results):
        if (inner.val_start, inner.val_end) != (fold.inner_val_start, fold.inner_val_end):
            raise ValueError("Validation fold window mismatch")
        decision = verify_validation_reports(
            protocol["selection_contract"], inner.candidate_metrics,
            root=root, validation_window=(fold.inner_val_start, fold.inner_val_end),
            expected_spec=spec, expected_commit=protocol["source"]["code_commit"],
        )
        if decision["decision"] != "SELECT":
            raise ValueError("NOT_QUALIFIED: validation ABSTAIN")
        selected = decision["selected"]
        if inner.best_params != {**selected["params"], "cost_scenario": selected["cost_scenario"]}:
            raise ValueError("Selection differs from validation contract")
        for candidate in inner.candidate_metrics:
            source = candidate["validation_source"]
            artifact = EvaluationArtifact.from_dict(source["artifact"])
            observed.append({
                "cell_id": artifact.cell_id, "params": candidate["params"],
                "cost": next(cost.to_dict() for cost in spec.cost_scenarios
                             if cost.name == candidate["cost_scenario"]),
                "simulation_window": list(artifact.simulation_window),
                "measurement_window": list(artifact.measurement_window),
            })
            reports.append(source["report_path"])
        artifact = outer.artifact
        cost = next(cost for cost in spec.cost_scenarios if cost.name == selected["cost_scenario"])
        cell = EvaluationCellSpec(spec.strategy_id, spec.symbol, spec.timeframe,
                                  selected["params"], cost)
        if (
            artifact is None or artifact.status != "COMPLETED"
            or artifact.cell_id != cell.cell_id
            or artifact.params_hash != cell.params_hash
            or artifact.cost_scenario != cost.name
            or artifact.fault_profile != cell.fault.name
            or type(artifact.signal_delay_bars) is not int or artifact.signal_delay_bars != 0
            or artifact.commit_sha != protocol["source"]["code_commit"]
            or artifact.measurement_window != (fold.outer_test_start, fold.outer_test_end)
            or artifact.simulation_window != artifact.measurement_window
            or (outer.test_start, outer.test_end) != artifact.measurement_window
            or outer.params != inner.best_params
            or any(getattr(artifact, key) != getattr(spec, key)
                   for key in ("strategy_id", "symbol", "timeframe"))
            or artifact.commission != cost.commission or artifact.slippage != cost.slippage
        ):
            raise ValueError("Outer artifact subject/params/cost/window mismatch")
        path = Path(artifact.report_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Outer source escapes packet")
        report = json.loads(path.read_bytes())
        rebuilt = _artifact_from_report(
            cell, build_default_registry().describe(spec.strategy_id), path.parent,
            report, artifact.data_manifest_sha,
            measurement_window=artifact.measurement_window,
            simulation_window=artifact.simulation_window,
            signal_delay_bars=artifact.signal_delay_bars,
        )
        if (report.get("measurement_window") != list(artifact.measurement_window)
                or rebuilt.status != "COMPLETED"
                or rebuilt.metrics != artifact.metrics
                or rebuilt.execution_health != artifact.execution_health
                or dict(outer.test_metrics) != dict(artifact.metrics)):
            raise ValueError("Outer raw report differs from result")
        reports.append(str(path.relative_to(root)))
    expected_keys = [content_hash(row) for row in protocol["expected_inner_cells"]]
    observed_keys = [content_hash(row) for row in observed]
    if (len(set(expected_keys)) != len(expected_keys)
            or len(set(observed_keys)) != len(observed_keys)
            or sorted(expected_keys) != sorted(observed_keys)
            or len(set(reports)) != len(reports)):
        raise ValueError("Exact validation cell coverage mismatch or duplicate report")
    sensitivity = result.aggregate_metrics.get("sensitivity", {}).get("coverage", {})
    expected_sensitivity = _sensitivity_requests(spec, result.outer_results, folds)
    observed_sensitivity = sensitivity.get("observed", [])
    if (sensitivity.get("status") != "PASS" or sensitivity.get("source_root") != "sensitivity"
            or sensitivity.get("expected") != expected_sensitivity
            or sorted(content_hash(row["request"]) for row in observed_sensitivity)
            != sorted(content_hash(row) for row in expected_sensitivity)):
        raise ValueError("Sensitivity coverage incomplete or differs from selected-fold schedule")
    sensitivity_root = root / "sensitivity"
    frozen_sensitivity = json.loads((sensitivity_root / "sensitivity_plan.json").read_text())
    if frozen_sensitivity != {"version": 1, "requests": expected_sensitivity, "startup": "fresh_at_outer_test_start"}:
        raise ValueError("Frozen sensitivity schedule mismatch")
    for receipt in observed_sensitivity:
        artifact = EvaluationArtifact.from_dict(receipt["artifact"])
        if (artifact.commit_sha != protocol["source"]["code_commit"]
                or _sensitivity_receipt(receipt["request"], artifact, sensitivity_root) != receipt):
            raise ValueError("Sensitivity source changed or lacks attributable identity")
        path = Path(artifact.report_path).resolve()
        reports.append(str(path.relative_to(root)))
    if len(set(reports)) != len(reports):
        raise ValueError("Duplicate sensitivity source reports")
    return {
        "status": "PASS", "scope": "planned_inner_selected_outer_and_sensitivity",
        "protocol_fingerprint": protocol["fingerprint"],
        "inner_cells": len(observed), "outer_folds": len(folds),
        "sensitivity_cells": len(observed_sensitivity),
        "reports": [{"path": report, "sha256": hashlib.sha256((root / report).read_bytes()).hexdigest()}
                    for report in reports],
        "live_authorized": False,
    }


def run_validation_only(spec: WFOSpec, out_root: Path, protocol: dict, folds, cell_timeout: int) -> None:
    """Supervise a canonical inner-only worker; never append to an old packet."""
    if cell_timeout != protocol["execution"]["cell_timeout"]:
        raise ValueError("validation timeout differs from frozen protocol")
    _verify_validation_protocol(spec, protocol, folds)
    budget = protocol["validation_budget"]
    _durable_json(out_root / "execution_started.json", {
        "protocol_fingerprint": protocol["fingerprint"], "source": protocol["source"],
        "budget": budget, "status": "STARTED", "live_authorized": False,
    })
    outcome = {"status": "INCOMPLETE"}
    previous_signal = signal.getsignal(signal.SIGTERM)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("validation supervisor terminated")
    try:
        signal.signal(signal.SIGTERM, interrupted)
        outcome = _supervise_validation(
            [sys.executable, str(Path(__file__).resolve()), "--validation-worker", str(out_root.resolve())],
            out_root, **budget,
        )
        if outcome["status"] != "COMPLETED":
            raise RuntimeError(f"validation worker {outcome['status']}; inspect worker logs")
        summary = json.loads((out_root / "parallel_canonical_summary.json").read_bytes())
        coverage = json.loads((out_root / "exact_coverage.json").read_bytes())
        if (summary["verdict"] != "VALIDATION_COMPLETE" or coverage["status"] != "PASS"
                or summary["protocol_fingerprint"] != protocol["fingerprint"]
                or coverage["protocol_fingerprint"] != protocol["fingerprint"]):
            raise ValueError("missing or invalid worker completion evidence")
        _verify_validation_protocol(spec, protocol, folds)
    except BaseException as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        if outcome["status"] == "COMPLETED":
            outcome["status"] = "INCOMPLETE"
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_signal)
        _durable_json(out_root / "execution_finished.json", {
            **outcome, "protocol_fingerprint": protocol["fingerprint"],
            "finished_at": datetime.now(UTC).isoformat(), "live_authorized": False,
        })


def _verify_validation_protocol(spec, protocol, folds):
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    controls = protocol["execution"]
    budget = protocol.get("validation_budget", {})
    if (content_hash({k: v for k, v in protocol.items() if k != "fingerprint"}) != protocol["fingerprint"]
            or protocol.get("scope") != "inner_validation_only_not_qualification"
            or protocol["spec"] != spec.to_dict() or protocol["folds"] != [asdict(f) for f in folds]
            or controls["workers"] != 1 or controls["max_retries"] != 0
            or not 1 <= controls["cell_timeout"] <= 180
            or type(budget.get("total_timeout_seconds")) is not int
            or not 1 <= budget["total_timeout_seconds"] <= 1800
            or type(budget.get("max_rss_mb")) is not int or not 128 <= budget["max_rss_mb"] <= 4096
            or capture_campaign_source(ROOT) != protocol["source"]
            or execution_environment() != controls["environment"]
            or simulator_runtime_config() != controls["runtime_config"]
            or hashlib.sha256(Path(protocol["input_binding"]["path"]).read_bytes()).hexdigest()
            != protocol["input_binding"]["sha256"]):
        raise ValueError("validation protocol/source/input/environment/budget mismatch")


def _validation_group_rss_mb(group):
    """Linux RSS sum of the owned process session, including spawned cells."""
    rss_kb = 0
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == group:  # state, ppid, pgrp
                status = (path / "status").read_text().splitlines()
                rss_kb += next((int(line.split()[1]) for line in status if line.startswith("VmRSS:")), 0)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return rss_kb / 1024


def _validation_group_pids(group):
    """Return all PIDs in the process group, including zombies."""
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            stat = (path / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == group:
                pids.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return pids


def _wait_for_all_reaped(group, deadline):
    """Wait until all processes in group are reaped (no PIDs exist)."""
    while _validation_group_pids(group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _supervise_validation(command, out_root, *, total_timeout_seconds, max_rss_mb):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("validation budget supervision requires Linux")
    started = time.monotonic()
    peak = 0.0
    status = None
    with (out_root / "worker_stdout.log").open("x") as stdout, (out_root / "worker_stderr.log").open("x") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while process.poll() is None:
                peak = max(peak, _validation_group_rss_mb(process.pid))
                if time.monotonic() - started >= total_timeout_seconds:
                    status = "TIMEOUT"
                    break
                if peak > max_rss_mb:
                    status = "MEMORY_LIMIT"
                    break
                time.sleep(0.05)
        finally:
            # Always clean our session, even if the leader exited first.
            cleanup_deadline = time.monotonic() + 5
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.2)
            process.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
            # Wait for ALL processes in group to be reaped (including zombies)
            if not _wait_for_all_reaped(process.pid, cleanup_deadline):
                remaining = _validation_group_pids(process.pid)
                raise RuntimeError(f"owned validation session did not stop within cleanup grace; remaining PIDs: {remaining}")
    return {"status": status or ("COMPLETED" if process.returncode == 0 else "WORKER_ERROR"),
            "worker_exit_code": process.returncode, "elapsed_seconds": time.monotonic() - started,
            "sampled_group_peak_rss_mb": peak, "rss_sample_interval_seconds": 0.05,
            "budget_enforcement": "owned_session_wall_deadline_and_sampled_RSS_not_hard_allocator_cap"}


def verify_validation_only_coverage(spec, result, protocol, folds, out_root):
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.selection_eligibility import verify_validation_reports
    from trading_agent.backtest.nested_wfo import InnerSelectionFreeze
    _verify_validation_protocol(spec, protocol, folds)
    if (result.outer_results or result.final_holdout or result.passes_hard_gates
            or [r.fold_id for r in result.inner_results] != [f.fold_id for f in folds]):
        raise ValueError("validation fold coverage or prohibited OOS/holdout mismatch")
    observed, reports, decisions = [], [], []
    for fold, inner in zip(folds, result.inner_results):
        if (inner.val_start, inner.val_end) != (fold.inner_val_start, fold.inner_val_end):
            raise ValueError("validation fold window mismatch")
        # Verify report attribution (this is the actual purpose of verify_validation_reports)
        verify_validation_reports(
            protocol["selection_contract"], inner.candidate_metrics, root=out_root,
            validation_window=(fold.inner_val_start, fold.inner_val_end), expected_spec=spec,
            expected_commit=protocol["source"]["code_commit"],
        )
        # Re-compute expected decision from candidates (tamper-proof)
        from trading_agent.backtest.selection_eligibility import assess_validation_candidates
        expected_decision = assess_validation_candidates(protocol["selection_contract"], inner.candidate_metrics)
        expected_decision_with_fold = {**expected_decision, "fold_id": fold.fold_id}
        # Read the decision from the file (written by nested_wfo via assess_validation_candidates)
        saved = json.loads((out_root / f"selection_decision_{fold.fold_id}.json").read_bytes())
        # Compare ignoring fields added by nested_wfo after assess_validation_candidates
        def strip_augmented_fields(d):
            if not isinstance(d, dict):
                return d
            result = {}
            for k, v in d.items():
                if k in ("report_sha256", "report_path"):
                    continue
                if isinstance(v, list):
                    result[k] = [strip_augmented_fields(item) for item in v]
                elif isinstance(v, dict):
                    result[k] = strip_augmented_fields(v)
                else:
                    result[k] = v
            return result
        if strip_augmented_fields(saved) != strip_augmented_fields(expected_decision_with_fold):
            raise ValueError("validation selection decision differs from raw evidence")
        decision = expected_decision_with_fold
        selected = decision.get("selected")
        expected_best = {**selected["params"], "cost_scenario": selected["cost_scenario"]} if selected else {}
        if inner.best_params != expected_best:
            raise ValueError("validation best params differ from eligibility contract")
        freezes = [f for f in result.inner_selection_freezes if f.fold_id == fold.fold_id]
        if len(freezes) != int(selected is not None):
            raise ValueError("missing, duplicate or unexpected validation freeze")
        for freeze in freezes:
            path = out_root / "inner_selection_freezes" / f"{freeze.freeze_id.removeprefix('sha256:')}.json"
            raw = json.loads(path.read_bytes())
            parsed = InnerSelectionFreeze.from_dict(raw)
            if (raw["freeze_id"] != parsed.freeze_id or parsed.to_dict() != freeze.to_dict()
                    or parsed.best_params != expected_best or parsed.inner_val_start != fold.inner_val_start
                    or parsed.inner_val_end != fold.inner_val_end):
                raise ValueError("invalid validation freeze")
        decisions.append({"fold_id": fold.fold_id, "decision": decision["decision"]})
        for candidate in inner.candidate_metrics:
            source = candidate["validation_source"]
            artifact = EvaluationArtifact.from_dict(source["artifact"])
            control = artifact.execution_control
            if (control.get("timeout_seconds") != protocol["execution"]["cell_timeout"]
                    or control.get("max_retries") != 0 or control.get("attempts_made") != 1
                    or control.get("isolation") != "spawned_process" or control.get("outcome") != "completed"):
                raise ValueError("validation execution controls differ from protocol")
            observed.append({"cell_id": artifact.cell_id, "params": candidate["params"],
                "cost": next(c.to_dict() for c in spec.cost_scenarios if c.name == candidate["cost_scenario"]),
                "simulation_window": list(artifact.simulation_window),
                "measurement_window": list(artifact.measurement_window)})
            reports.append({"path": source["report_path"], "sha256": source["report_sha256"]})
    if Counter(map(content_hash, observed)) != Counter(map(content_hash, protocol["expected_inner_cells"])):
        raise ValueError("missing, duplicate or unexpected validation cells")
    # Verify raw report SHA256 matches actual file content
    import hashlib
    for report_info in reports:
        report_path = Path(report_info["path"])
        if report_path.exists():
            actual_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
            expected_sha = report_info["sha256"]
            if actual_sha != expected_sha:
                raise ValueError(f"Report SHA256 mismatch: {report_path} actual={actual_sha} expected={expected_sha}")
    audit = audit_parallel_trial_history(spec, protocol, folds)
    if audit["conflicts"] or audit["current_context_rows"] != len(observed):
        raise ValueError("canonical validation trial accounting incomplete or conflicting")
    return {"status": "PASS", "scope": "inner_validation_only_not_qualification",
            "protocol_fingerprint": protocol["fingerprint"], "inner_cells": len(observed),
            "folds": decisions, "reports": reports, "outer_folds": 0, "sensitivity_cells": 0,
            "trial_counts": result.trial_counts, "live_authorized": False}


def _run_validation_canonical(spec, out_root, protocol, folds, cell_runner=None):
    _verify_validation_protocol(spec, protocol, folds)
    runner = QualificationCellRunner(out_root, protocol, folds)
    # Allow test override of cell runner
    if cell_runner is not None:
        runner = cell_runner
    exec_context = _trial_execution_context(protocol)
    result = run_nested_wfo(
        spec, out_root=out_root, real_sensitivity=False, run_holdout=False,
        cell_runner=runner, native_gate_contract=protocol["native_gate_contract"],
        selection_contract=protocol["selection_contract"], validation_only=True,
        execution_context=exec_context,
    )

    # Write cell_execution receipts for each validation cell (per-cell terminal receipts)
    cell_exec_dir = out_root / "cell_execution"
    cell_exec_dir.mkdir(parents=True, exist_ok=True)

    # Verify raw report SHA256 and write receipts
    import hashlib
    for fold, inner in zip(folds, result.inner_results):
        for candidate in inner.candidate_metrics:
            source = candidate["validation_source"]
            report_path = Path(source["report_path"])
            if report_path.exists():
                raw_bytes = report_path.read_bytes()
                report_sha256 = hashlib.sha256(raw_bytes).hexdigest()

                # Verify SHA256 matches cached value if available
                if "report_sha256" in source and source["report_sha256"] != report_sha256:
                    raise ValueError(f"Report SHA256 mismatch for {report_path}: cached={source['report_sha256']} actual={report_sha256}")

                # Write cell_execution receipt
                exec_dir = cell_exec_dir / uuid.uuid4().hex
                exec_dir.mkdir(parents=True, exist_ok=False)
                _durable_json(exec_dir / "started.json", {
                    "cell_id": candidate.get("cell_id", source.get("cell_id", "unknown")),
                    "strategy_id": spec.strategy_id,
                    "symbol": spec.symbol,
                    "timeframe": spec.timeframe,
                    "params": candidate["params"],
                    "cost": next((c.to_dict() for c in spec.cost_scenarios if c.name == candidate["cost_scenario"]), {}),
                    "validation_window": {"start": inner.val_start, "end": inner.val_end},
                    "report_sha256": report_sha256,
                    "report_path": str(report_path),
                    "status": "COMPLETED",
                    "live_authorized": False,
                    "scope": "validation_receipt_not_qualification",
                })
                _durable_json(exec_dir / "result.json", {
                    "report_sha256": report_sha256,
                    "report_path": str(report_path),
                    "status": "COMPLETED",
                    "metrics": candidate.get("val_metrics", {}),
                    "live_authorized": False,
                })

    coverage = verify_validation_only_coverage(spec, result, protocol, folds, out_root)

    # Write inner_selection_freezes (already written by nested_wfo, but verify here)
    freeze_dir = out_root / "inner_selection_freezes"
    # Directory may not exist if all decisions are ABSTAIN (no freezes written)
    # Create it if missing for consistent structure
    if not freeze_dir.exists():
        freeze_dir.mkdir(parents=True, exist_ok=True)

    # Remove existing files first (tests reuse temp directories)
    for path in [out_root / "exact_coverage.json", out_root / "parallel_canonical_summary.json"]:
        if path.exists():
            path.unlink()
    _durable_json(out_root / "exact_coverage.json", coverage)
    _durable_json(out_root / "parallel_canonical_summary.json", {
        "strategy": spec.strategy_id, "symbol": spec.symbol, "timeframe": spec.timeframe,
        "verdict": "VALIDATION_COMPLETE", "validation_only": True, "passes_hard_gates": False,
        "protocol_fingerprint": protocol["fingerprint"], "aggregate_metrics": result.aggregate_metrics,
        "trial_counts": result.trial_counts, "fold_decisions": coverage["folds"], "live_authorized": False,
    })
    return result


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
    parser.add_argument(
        "--cell-timeout", type=int, default=1800,
        help="Per-cell timeout in seconds (inner validation & outer folds)",
    )
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--val-months", type=int, default=3)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--step-months", type=int, default=3)
    parser.add_argument(
        "--run-holdout", action="store_true", help="Run final holdout if gates pass"
    )
    parser.add_argument("--preflight-only", action="store_true",
                        help="Freeze protocol and exact validation inventory; do not evaluate")
    parser.add_argument("--validation-only", action="store_true",
                        help="Run inner validation ONLY (no outer OOS, no sensitivity, no holdout). Produces selection freeze and coverage.")
    parser.add_argument("--total-timeout", type=int, default=1800,
                        help="Validation-only worker session wall budget (1..1800s)")
    parser.add_argument("--max-rss-mb", type=int, default=2048,
                        help="Validation-only sampled process-session RSS ceiling (128..4096 MiB)")
    parser.add_argument("--validation-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--reuse-from", default=None,
                        help="Cold recover exact cache v2 inner/selected outer cells into a NEW output packet; sensitivity reruns")
    parser.add_argument(
        "--real-sensitivity",
        action="store_true",
        default=True,
        help="Run real sensitivity analysis",
    )
    parser.add_argument(
        "--no-real-sensitivity",
        action="store_false",
        dest="real_sensitivity",
        help="Disable real sensitivity",
    )
    args = parser.parse_args()
    if args.validation_worker is not None:
        from trading_agent.backtest.nested_wfo import NestedFold
        root = args.validation_worker.resolve()
        protocol = json.loads((root / "research_protocol.json").read_bytes())
        declared = dict(protocol["spec"])
        declared["cost_scenarios"] = tuple(CostScenario(**c) for c in declared["cost_scenarios"])
        spec = WFOSpec(**declared)
        folds = [NestedFold(**f) for f in protocol["folds"]]
        _run_validation_canonical(spec, root, protocol, folds)
        return
    if args.run_holdout:
        parser.error("This research entry point does not authorize holdout execution")
    if not 1 <= args.workers <= 4 or args.cell_timeout < 1:
        parser.error("workers must be 1..4 and cell timeout must be positive")
    if args.validation_only:
        if args.run_holdout:
            parser.error("--validation-only cannot be used with --run-holdout")
        if args.real_sensitivity:
            parser.error("--validation-only cannot be used with --real-sensitivity")
        if args.workers != 1:
            parser.error("--validation-only requires --workers 1")
        if args.cell_timeout > 180:
            parser.error("--validation-only requires --cell-timeout <= 180")
        if args.reuse_from is not None:
            parser.error("--validation-only requires a fresh packet without reuse")
        if not 1 <= args.total_timeout <= 1800 or not 128 <= args.max_rss_mb <= 4096:
            parser.error("validation budget requires total-timeout 1..1800 and max-rss-mb 128..4096")
    elif not args.real_sensitivity:
        parser.error("This qualification entry point requires measured sensitivity")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    cost_scenarios = get_cost_scenarios(args.cost)

    # Normalize symbol: CLI args may use BTCUSDT, BTC_USDT, or BTC/USDT.
    # Strategy descriptors expect BTC/USDT (slash format).
    raw_symbol = args.symbol
    if "/" in raw_symbol:
        symbol = raw_symbol
    elif "_" in raw_symbol:
        symbol = raw_symbol.replace("_", "/")
    else:
        # Heuristic: try common crypto quote currencies
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH", "USD", "ADA"):
            if raw_symbol.endswith(quote) and len(raw_symbol) > len(quote):
                symbol = raw_symbol[:-len(quote)] + "/" + quote
                break
        else:
            symbol = raw_symbol  # fallback: use as-is

    spec = WFOSpec(
        strategy_id=args.strategy,
        symbol=symbol,
        timeframe=args.timeframe,
        param_grid=get_param_grid_for_strategy(args.strategy),
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
    protocol, folds = prepare_parallel_protocol(
        spec, out_root, workers=args.workers, cell_timeout=args.cell_timeout,
        reuse_from=args.reuse_from,
        **({"validation_budget": {"total_timeout_seconds": args.total_timeout,
                                  "max_rss_mb": args.max_rss_mb}} if args.validation_only else {}),
    )
    print(f"  Protocol: {protocol['fingerprint']}", flush=True)
    if args.preflight_only:
        print(f"PREFLIGHT_ONLY: {len(folds)} folds / {len(protocol['expected_inner_cells'])} inner cells; no evaluation")
        return
    if args.validation_only:
        run_validation_only(spec, out_root, protocol, folds, args.cell_timeout)
        return

    print(
        f"Running CANONICAL WFO for {args.strategy} {args.symbol} {args.timeframe}...",
        flush=True,
    )
    print(f"  Params: {get_param_grid_for_strategy(args.strategy)}", flush=True)
    print(f"  Cost: {[c.name for c in cost_scenarios]}", flush=True)
    print(f"  Workers: {args.workers}", flush=True)
    print(f"  Out: {out_root}", flush=True)
    print(f"  Run holdout: {args.run_holdout}", flush=True)
    print(f"  Real sensitivity: {args.real_sensitivity}", flush=True)

    print(f"  Cell timeout: {args.cell_timeout}s", flush=True)

    start_time = time.time()

    # Run canonical WFO with parallel cell runner
    runner = QualificationCellRunner(out_root, protocol, folds, reuse_from=args.reuse_from)
    result = run_nested_wfo(
        spec,
        out_root=out_root,
        run_holdout=args.run_holdout,
        real_sensitivity=args.real_sensitivity,
        cell_runner=runner,  # Pass the runner object (has run_batch)
        native_gate_contract=protocol["native_gate_contract"],
        selection_contract=protocol["selection_contract"],
    )

    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    if capture_campaign_source(ROOT) != protocol["source"]:
        raise ValueError("NOT_QUALIFIED: source changed during campaign")
    if hashlib.sha256(Path(protocol["input_binding"]["path"]).read_bytes()).hexdigest() != protocol["input_binding"]["sha256"]:
        raise ValueError("NOT_QUALIFIED: input changed during campaign")
    coverage = verify_parallel_result_coverage(spec, result, protocol, folds, out_root)
    with (out_root / "exact_coverage.json").open("x", encoding="utf-8") as stream:
        json.dump(coverage, stream, indent=2, allow_nan=False)

    elapsed = time.time() - start_time

    print("\n=== Canonical WFO Complete ===")
    print(f"  Status: {'PASS' if result.passes_hard_gates else 'FAIL'}")
    verdict = getattr(result, "verdict", "N/A")
    print(f"  Verdict: {verdict}")
    print(f"  Folds: {result.aggregate_metrics.get('n_outer_folds', 0)}")
    print(
        f"  Median Sharpe: {result.aggregate_metrics.get('median_test_sharpe', 0):.4f}"
    )
    print(
        f"  Median Return: {result.aggregate_metrics.get('median_test_return_pct', 0):.2f}%"
    )
    print(f"  Elapsed: {elapsed:.0f}s")

    # Compute per-strategy output directory for summary persistence
    symbol_safe = spec.symbol.replace("/", "_").replace(" ", "_")
    spec_dir = out_root / f"{spec.strategy_id}__{symbol_safe}__{spec.timeframe}"

    # Save summary
    summary = {
        "strategy": args.strategy,
        "symbol": symbol,
        "timeframe": args.timeframe,
        "cost_scenarios": [c.name for c in cost_scenarios],
        "verdict": verdict,
        "passes_hard_gates": result.passes_hard_gates,
        "aggregate_metrics": result.aggregate_metrics,
        "gate_results": [
            g.to_dict() if hasattr(g, "to_dict") else g for g in result.gate_results
        ],
        "elapsed_seconds": elapsed,
        "workers": args.workers,
        "run_holdout": args.run_holdout,
        "real_sensitivity": args.real_sensitivity,
        "timestamp": datetime.now(UTC).isoformat(),
        "protocol_fingerprint": protocol["fingerprint"],
        "exact_coverage": coverage,
        "live_authorized": False,
        "decision_sha256": hashlib.sha256((out_root / "wfo_decision.json").read_bytes()).hexdigest(),
    }
    (out_root / "parallel_canonical_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n  Summary saved: {out_root / 'parallel_canonical_summary.json'}")
    print(f"  Per-strategy:  {spec_dir / 'parallel_canonical_summary.json'}")

    # R07: Evaluate PortfolioGatePolicy (fail-closed promotion path)
    policy = PortfolioGatePolicy()
    portfolio_result = _build_portfolio_selection_result(
        [result],
        run_holdout=args.run_holdout,
        out_root=out_root,
        policy=policy,
    )
    summary["portfolio_verdict"] = portfolio_result.verdict
    summary["portfolio_passes_hard_gates"] = portfolio_result.passes_hard_gates
    summary["portfolio_gate_results"] = [
        g.to_dict() for g in portfolio_result.gate_results
    ]
    summary["portfolio_artifact_id"] = portfolio_result.artifact_id

    # Save summary to per-strategy directory to avoid overwrites in campaign runs
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "parallel_canonical_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    print("\n=== Portfolio Gate Evaluation (R07) ===")
    print(f"  Policy ID: {policy.policy_id[:16]}...")
    print(f"  Verdict: {portfolio_result.verdict}")
    print(f"  Passes hard gates: {portfolio_result.passes_hard_gates}")
    for g in portfolio_result.gate_results:
        status = "PASS" if g.is_pass() else ("FAIL" if g.verdict == "FAIL" else "INVALID")
        val = f"{g.observed_value:.2f}" if g.observed_value is not None else "N/A"
        print(f"  [{status}] {g.gate_id}: {val} (threshold={g.threshold})")

    if portfolio_result.passes_hard_gates:
        print("\n  --> Strategy passes portfolio gates. Eligible for promotion.")
    else:
        failed_gates = [g.gate_id for g in portfolio_result.gate_results if not g.is_pass()]
        print(f"\n  --> Strategy FAILS portfolio gates: {failed_gates}")
        if portfolio_result.no_trade_artifact:
            print("  --> FormalNoTradeArtifact generated.")


if __name__ == "__main__":
    main()
