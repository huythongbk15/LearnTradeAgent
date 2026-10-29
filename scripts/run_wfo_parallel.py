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
import math
import multiprocessing as mp
import os
import signal
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
#: Where the APPROVED preregistration packet lives. It defaults to the repository
#: root. A different root is only used by the synthetic integration, which must
#: bind to a synthetic packet instead of the registered one; the resolved root
#: is recorded in the frozen protocol, so a run against a non-default root is
#: visible in the protocol and in the receipts.
def preregistration_root() -> Path:
    """Where the APPROVED preregistration packet lives.

    Resolved at CALL time (not import time) so that a worker subprocess
    inherits the same root through the environment. It defaults to the
    repository root; the resolved value is recorded in the frozen protocol, so a
    run against a non-default root is visible in the protocol and the receipts.
    """
    return Path(os.environ.get("TRADING_PREREGISTRATION_ROOT") or ROOT).resolve()

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
    NestedFold,
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
    # Regime-switched: ADX-filtered RSI + MA crossover (calibrated best config)
    "rsi_adx_regime_filter": {
        "rsi_period": [14],
        "rsi_oversold": [35],
        "rsi_overbought": [65],
        "ma_fast": [20],
        "ma_slow": [50],
        "adx_period": [14],
        "adx_threshold": [15],
        "ma_type": ["sma"],
    },
    "own24_context": {"arm": ["A0", "A1", "A2"]},
}


#: Fixed parameter grid of a SCOPED candidate. Reachable only when the caller
#: presents the scope token that names it, so a scoped candidate never appears
#: in the broad grid maps.
_SCOPED_CANDIDATE_PARAM_GRIDS: dict[str, dict[str, list]] = {
    "di_adx_state_edge": {"adx_period": [14], "adx_threshold": [25.0]},
}


def get_param_grid_for_strategy(
    strategy_id: str, scope_token: str | None = None
) -> dict[str, list]:
    """Get param grid for a strategy, falling back to MINIMAL_PARAM_GRIDS.

    A scoped candidate's grid is returned only when the scope token names it;
    without the token the scoped id has no grid and the cell plan is empty,
    which fails closed rather than silently running something else.
    """
    from trading_agent.strategies.canonical.candidates import resolve_scoped_candidate_id

    scoped_id = resolve_scoped_candidate_id(scope_token)
    if scoped_id == strategy_id and scoped_id in _SCOPED_CANDIDATE_PARAM_GRIDS:
        return {k: list(v) for k, v in _SCOPED_CANDIDATE_PARAM_GRIDS[scoped_id].items()}
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


def fixed_parameter_grid(strategy_id: str, payload: str, scope_token=None) -> dict[str, list]:
    """Strict scalar JSON -> normalized singleton grid; no I/O or search."""
    from trading_agent.strategies.canonical.candidates import validate_params

    def reject_constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate fixed parameter: {key}")
            result[key] = value
        return result

    raw = json.loads(payload, parse_constant=reject_constant, object_pairs_hook=unique_object)
    if not isinstance(raw, dict):
        raise ValueError("--fixed-params requires a JSON object of scalar values")
    descriptor = build_default_registry(scope_token=scope_token).describe(strategy_id)
    properties = descriptor.parameters_schema["properties"]
    for key, value in raw.items():
        if key not in properties:
            raise ValueError(f"unknown fixed parameter: {key}")
        schema = properties[key]
        kind = schema.get("type")
        valid_type = {
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "string": type(value) is str,
            "boolean": type(value) is bool,
        }.get(kind, False)
        if not valid_type:
            raise ValueError(f"{key}: expected scalar {kind}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{key}: must be finite")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{key}: not in allowed enum")
    normalized = validate_params(strategy_id, raw)
    # These shared names are not cross-checked by the legacy validator.
    for left, right in (("ma_fast", "ma_slow"), ("rsi_oversold", "rsi_overbought")):
        if left in normalized and right in normalized and normalized[left] >= normalized[right]:
            raise ValueError(f"{left} must be < {right}")
    return {key: [value] for key, value in normalized.items()}


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
            "expected_execution_contract_id": spec.expected_execution_contract_id,
        }

    def _inline(self, cell):
        spec, start, end, fresh, m_start, m_end = cell
        return _run_cell_worker(
            self._primitives(spec), str(self.out_root), start, end, fresh,
            m_start, m_end, self.timeout_seconds, self.max_retries,
            self.execution_context,
            scope_token=(self.execution_context or {}).get("scope_token"),
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
    scope_token: str | None = None,
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
        expected_execution_contract_id=spec_primitives.get("expected_execution_contract_id"),
    )
    frozen_contract_id = None
    if execution_context and "native_execution_contract" in execution_context:
        from trading_agent.backtest.execution_contract import require_execution_contract

        contract = execution_context["native_execution_contract"]
        if not isinstance(contract, dict):
            raise ValueError("missing frozen native execution contract")
        require_execution_contract(contract.get("contract_id"), contract)
        frozen_contract_id = contract["contract_id"]
    directory = Path(out_root) / "cell_execution" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    request = {
        "cell_id": spec.cell_id, "strategy_id": spec.strategy_id,
        "symbol": spec.symbol, "timeframe": spec.timeframe,
        "params": dict(spec.params), "cost": spec.cost_scenario.to_dict(),
        "fault": asdict(spec.fault), "simulation_window": [start, end],
        "expected_execution_contract_id": spec.expected_execution_contract_id,
        "frozen_execution_contract_id": frozen_contract_id,
        "measurement_window": [measurement_start, measurement_end], "fresh": fresh,
        "timeout_seconds": timeout_seconds, "max_retries": max_retries,
        "execution_context": execution_context or {},
        "scope_token": scope_token,
        "started_at": datetime.now(UTC).isoformat(),
        "started_at_epoch": time.time(),
        "worker_pid": os.getpid(),
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
            frozen_execution_contract_id=frozen_contract_id,
            scope_token=scope_token,
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


def _explicit_measurement_fold(n_bars: int, measurement: dict) -> NestedFold:
    """One fixed validation span. Parameters are already frozen, so train does not select them."""
    required = ("measurement_start", "measurement_end", "purge_bars", "embargo_bars")
    if set(measurement) != set(required):
        raise ValueError("explicit measurement must contain exactly the locked window fields")
    start = measurement["measurement_start"]
    end = measurement["measurement_end"]
    purge = measurement["purge_bars"]
    embargo = measurement["embargo_bars"]
    if any(type(value) is not int for value in (start, end, purge, embargo)):
        raise ValueError("explicit measurement fields must be integers")
    if not (0 <= start < end <= n_bars) or purge < 1 or embargo < 1:
        raise ValueError("explicit measurement is outside the loaded frame")
    return NestedFold(
        fold_id="fold_000",
        inner_train_start=0,
        inner_train_end=start,
        inner_val_start=start,
        inner_val_end=end,
        outer_test_start=end,
        outer_test_end=end,
        purge=purge,
        embargo=embargo,
    )


def build_canonical_cells(
    spec: WFOSpec,
    scope_token: str | None = None,
    *,
    frame=None,
    explicit_measurement: dict | None = None,
) -> tuple[list[tuple[EvaluationCellSpec, int, int, bool, int, int]], list[Any]]:
    """Build all cells needed for canonical WFO (inner + outer).

    ``explicit_measurement`` locks one validation window. It is only the
    indexed span already named by a preregistration. Month geometry remains
    the path for every call that does not pass it.
    """
    df = frame if frame is not None else load_ohlcv("binance", spec.symbol, spec.timeframe)
    n_bars = df.height
    registry_canonical = build_default_registry(scope_token=scope_token)
    descriptor = registry_canonical.describe(spec.strategy_id)
    from trading_agent.strategies.canonical.candidates import campaign_history_contract
    history = campaign_history_contract(spec.strategy_id, spec.param_grid)
    descriptor = replace(descriptor, warmup_bars=history["warmup_bars"])
    purge, embargo = _default_purge_embargo(descriptor)
    purge = spec.purge_bars or purge
    embargo = spec.embargo_bars or embargo
    if explicit_measurement is None:
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
    else:
        folds = [_explicit_measurement_fold(n_bars, explicit_measurement)]
    # Drop folds overlapping holdout
    holdout_bars = _resolve_frozen_holdout_window(df, spec)
    if holdout_bars is not None:
        h_start, _h_end, _manifest = holdout_bars
        if explicit_measurement is not None and folds[0].inner_val_end > h_start:
            raise ValueError("explicit measurement reaches the frozen holdout")
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


# ── Validation-only execution envelope ──────────────────────────────────────
# These are CEILINGS for the inner-validation scope, not defaults. The cap on
# the per-cell timeout is what lets a cell that legitimately needs more than
# the historical 180 s finish instead of being killed mid-write; the total
# budget and the RSS ceiling bound the session.
#
# Raising or lowering any of these is a BUDGET-POLICY change: it must be
# reviewed together with the preregistration that relies on it, and the
# regression test tests/test_wfo_validation_envelope.py must keep passing.
# The guards below are fail-closed and are never removed — only their bound
# is expressed here.
VALIDATION_CELL_TIMEOUT_MAX_SECONDS = 600
VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS = 7200
VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS = 1
VALIDATION_MAX_RSS_MB_MIN = 128
VALIDATION_MAX_RSS_MB_MAX = 4096

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
    context = {"trial_identity_version": 2, "protocol_fingerprint": protocol["fingerprint"],
            "source": protocol["source"], "environment": exec_config["environment"],
            "runtime_config": rt}
    if "native_execution_contracts" in protocol:
        context["native_execution_policy_version"] = 1
    # The scope token is part of the frozen context, so it is covered by the
    # execution scope identity and by the cache key. A study without a scoped
    # candidate carries no token and its context is unchanged.
    scope_token = exec_config.get("scope_token")
    if scope_token:
        context["scope_token"] = scope_token
    return context


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
    scope_token = (protocol.get("execution") or {}).get("scope_token")
    strategy_sha = wfo._compute_strategy_code_sha(spec.strategy_id, scope_token)
    data_sha = wfo._compute_data_manifest_sha(spec.symbol, spec.timeframe)
    feature_sha = wfo._compute_feature_schema_hash(
        build_default_registry(scope_token=scope_token).describe(spec.strategy_id))
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


def prepare_parallel_protocol(spec, out_root, *, workers, cell_timeout, reuse_from=None,
                              validation_budget=None, scope_token=None, max_retries=1,
                              stop_on_failure=False, explicit_measurement=None):
    """Freeze effective inputs before evaluation; never upgrade a legacy packet."""
    from trading_agent.backtest.campaign_evidence import content_hash
    if type(stop_on_failure) is not bool or (stop_on_failure and (
            validation_budget is None or workers != 1)):
        raise ValueError("stop-on-failure requires serial validation-only budget")
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
    if explicit_measurement is not None and validation_budget is None:
        raise ValueError("explicit measurement requires validation-only")
    cells, folds = build_canonical_cells(
        spec, scope_token=scope_token, frame=frame, explicit_measurement=explicit_measurement,
    )
    selection = freeze_selection_contract(
        min_validation_trades=30, min_validation_sharpe=0.0,
    )
    from scripts.full_system_backtest import describe_execution_contract

    native_contracts = {cell.cell_id: describe_execution_contract(
        symbol=cell.symbol, timeframe=cell.timeframe, strategy_name=cell.strategy_id,
        parameters=dict(cell.params), commission=cell.cost_scenario.commission,
        slippage=cell.cost_scenario.slippage,
    ) for cell, *_ in cells}
    body = {
        "version": 1,
        "authority": "run_nested_wfo",
        "trial_identity_contract": dict(TRIAL_IDENTITY_CONTRACT),
        "native_execution_contracts": native_contracts,
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
        **({"explicit_measurement": dict(explicit_measurement)} if explicit_measurement is not None else {}),
        "expected_inner_cells": [
            {"cell_id": cell.cell_id, "params": dict(cell.params),
             "cost": cell.cost_scenario.to_dict(), "simulation_window": [start, end],
             "measurement_window": [m_start, m_end]}
            for cell, start, end, _fresh, m_start, m_end in cells
        ],
        "execution": {
            "workers": workers, "cell_timeout": cell_timeout, "max_retries": max_retries,
            "timeout_scope": "each_isolated_attempt_terminated_before_retry",
            "receipt_scope": "inner_selected_outer_cache_v2_fresh_packet_only",
            "cache_version": CACHE_VERSION,
            "environment": execution_environment(),
            "runtime_config": simulator_runtime_config(),
            "reuse_scope": "inner_validation_and_selected_outer_only",
            "sensitivity_reuse": False,
            # Scope token for a scoped candidate: it travels inside the frozen
            # protocol, so it is part of the protocol fingerprint, the trial
            # execution context and the cache key. Absent for every study that
            # does not use a scoped candidate, so those protocols are unchanged.
            **({"scope_token": scope_token} if scope_token else {}),
            # where the approved preregistration packet was read from, and
            # whether that is the canonical (repository) root. A non-canonical
            # root is a DECLARED deviation: it is inside the frozen protocol,
            # so it is in the protocol fingerprint, the trial context, the
            # cache key and the receipts.
            "preregistration_root": str(preregistration_root()),
            "preregistration_root_canonical": preregistration_root() == ROOT,
        },
        "live_authorized": False,
    }
    if validation_budget is not None:
        # The same named caps the CLI guard uses, so the producer and the entry
        # point cannot disagree about the envelope. The guard stays fail-closed
        # and keeps every other condition.
        if (workers != 1
                or not 1 <= cell_timeout <= VALIDATION_CELL_TIMEOUT_MAX_SECONDS
                or reuse_from is not None
                or set(validation_budget) != {"total_timeout_seconds", "max_rss_mb"}
                or type(validation_budget["total_timeout_seconds"]) is not int
                or not (VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS
                        <= validation_budget["total_timeout_seconds"]
                        <= VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS)
                or type(validation_budget["max_rss_mb"]) is not int
                or not (VALIDATION_MAX_RSS_MB_MIN
                        <= validation_budget["max_rss_mb"]
                        <= VALIDATION_MAX_RSS_MB_MAX)):
            raise ValueError(
                "invalid validation budget or unsupported reuse: cell_timeout "
                f"1..{VALIDATION_CELL_TIMEOUT_MAX_SECONDS}, total_timeout "
                f"{VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS}.."
                f"{VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS}, max_rss_mb "
                f"{VALIDATION_MAX_RSS_MB_MIN}..{VALIDATION_MAX_RSS_MB_MAX}")
        body["execution"]["max_retries"] = 0
        body["validation_budget"] = dict(validation_budget)
        body["scope"] = "inner_validation_only_not_qualification"
    if stop_on_failure:
        body["execution"]["stop_on_failure"] = True
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


class ValidationStoppedAfterFailure(RuntimeError):
    """A real failed attempt stopped dispatch; remaining cells are not attempts."""

    def __init__(self, receipt):
        super().__init__("validation stopped after first failed cell")
        self.receipt = receipt


class QualificationCellRunner(CampaignCellRunner):
    """Cold recovery of exact inner/selected-outer cells into a new packet.

    Sensitivity remains canonical measured execution, never skipped on cache hit.
    No same-packet qualification replay or copied executable state is supported.
    """

    def __init__(self, out_root, protocol, folds, *, reuse_from=None):
        if not isinstance(protocol.get("native_execution_contracts"), dict) or not protocol["native_execution_contracts"]:
            raise ValueError("new qualification requires frozen native execution contracts; no legacy upgrade")
        self.protocol = protocol
        self.folds = folds
        execution = protocol["execution"]
        self.trial_context = _trial_execution_context(protocol)
        super().__init__(out_root, {
            "source": protocol["source"], "data": protocol["input_binding"],
            "plan": protocol["fingerprint"], "history": protocol["history"],
            "runtime_config": execution["runtime_config"],
            "native_execution_contracts": protocol["native_execution_contracts"],
            "require_frozen_input": True,
            # The scope token is part of the frozen trial context, so it is
            # covered by the execution scope identity and by the cache key.
            **({"scope_token": self.trial_context["scope_token"]}
               if "scope_token" in self.trial_context else {}),
        }, workers=execution["workers"], reuse_from=reuse_from, runner=self._run_measured)
        if self.environment != execution["environment"]:
            raise ValueError("qualification runtime environment differs from frozen protocol")
        declared = dict(protocol["spec"])
        declared["cost_scenarios"] = tuple(CostScenario(**cost) for cost in declared["cost_scenarios"])
        audit = audit_parallel_trial_history(WFOSpec(**declared), protocol, folds)
        _durable_json(self.root / "trial_history_startup.json", audit)
        if audit["conflicts"]:
            raise ValueError("trial history conflict at runner startup; inspect trial_history_startup.json; do not reset registry")

    def _verify_scoped_admission(self, spec, scope_token):
        """Verify the actual frozen protocol, not a fabricated PLUS envelope."""
        declared = self.protocol["spec"]
        if (scope_token != self.protocol["execution"].get("scope_token")
                or spec.strategy_id != declared["strategy_id"]
                or spec.symbol != declared["symbol"] or spec.timeframe != declared["timeframe"]):
            raise ValueError("preregistration scoped subject/token differs from protocol")
        key = replace(spec, expected_execution_contract_id=None).cell_id
        if key not in self.protocol["native_execution_contracts"]:
            raise ValueError("preregistration cell is not in the frozen protocol")
        _verify_approved_payload_binding(self.protocol, scope_token)

    def _run_measured(self, spec, **kwargs):
        key = replace(spec, expected_execution_contract_id=None).cell_id
        if key not in self.protocol["native_execution_contracts"]:
            raise ValueError("unregistered native execution contract for qualification cell")
        return _run_cell_worker(
            ParallelCellRunner._primitives(spec), str(kwargs["out_root"]),
            kwargs["start"], kwargs["end"], kwargs["fresh"],
            kwargs["measurement_start"], kwargs["measurement_end"],
            self.protocol["execution"]["cell_timeout"], self.protocol["execution"]["max_retries"],
            {"protocol_fingerprint": self.protocol["fingerprint"],
             "source": self.protocol["source"], "input_binding": self.protocol["input_binding"],
             "native_execution_contract": self.protocol["native_execution_contracts"][key]},
            scope_token=self.trial_context.get("scope_token"),
        )

    def run_batch(self, cells):
        if not self.protocol["execution"].get("stop_on_failure", False):
            return super().run_batch(cells)
        if self.workers != 1 or self.protocol.get("scope") != "inner_validation_only_not_qualification":
            raise ValueError("stop-on-failure is serial validation-only")
        from trading_agent.backtest.campaign_evidence import content_hash
        observations = getattr(self, "_serial_observations", [])
        self._serial_observations = observations
        artifacts = []
        for spec, start, end, fresh, measurement_start, measurement_end in cells:
            artifact = self(spec, out_root=self.root, start=start, end=end, fresh=fresh,
                            measurement_start=measurement_start, measurement_end=measurement_end,
                            _cache_stage="INNER_VALIDATION")
            row = {"cell_id": spec.cell_id, "params": dict(spec.params),
                   "cost": spec.cost_scenario.to_dict(), "simulation_window": [start, end],
                   "measurement_window": [measurement_start, measurement_end]}
            observations.append({"planned_cell": row, "status": artifact.status,
                                 "artifact": artifact.to_dict()})
            artifacts.append(artifact)
            if artifact.status != "COMPLETED":
                attempted = {content_hash(o["planned_cell"]) for o in observations}
                receipt = {
                    "protocol_fingerprint": self.protocol["fingerprint"],
                    "reason": "stop_on_first_failed_cell", "attempted": list(observations),
                    "not_attempted": [r for r in self.protocol["expected_inner_cells"]
                                      if content_hash(r) not in attempted],
                    "live_authorized": False,
                }
                _durable_json(self.root / "stop_on_failure.json", receipt)
                raise ValidationStoppedAfterFailure(receipt)
        return artifacts

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


def _verify_native_report(protocol, artifact, root):
    """Frozen-vs-raw-vs-artifact verification shared by both coverage scopes."""
    from trading_agent.backtest.execution_contract import require_frozen_native_evidence

    path = Path(artifact.report_path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("native source escapes packet")
    require_frozen_native_evidence(protocol.get("native_execution_contracts"),
        artifact.cell_id, json.loads(path.read_bytes()), artifact)


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
            _verify_native_report(protocol, artifact, root)
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
        _verify_native_report(protocol, artifact, root)
        rebuilt = _artifact_from_report(
            cell,
            build_default_registry(
                scope_token=(protocol.get("execution") or {}).get("scope_token")
            ).describe(spec.strategy_id),
            path.parent,
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
        if (summary.get("protocol_fingerprint") != protocol["fingerprint"]
                or coverage.get("protocol_fingerprint") != protocol["fingerprint"]):
            raise ValueError("worker evidence protocol mismatch")
        _verify_validation_protocol(spec, protocol, folds)
        if coverage.get("status") == "INCOMPLETE":
            terminal = json.loads((out_root / "terminal_INCOMPLETE.json").read_bytes())
            if (summary.get("verdict") != "VALIDATION_INCOMPLETE"
                    or terminal != coverage or not coverage.get("failed_cells")
                    or summary.get("live_authorized") is not False
                    or coverage.get("live_authorized") is not False):
                raise ValueError("missing or inconsistent worker INCOMPLETE evidence")
            outcome.update(status="INCOMPLETE", reason=coverage["reason"],
                           failed_cells=coverage["failed_cells"],
                           completed_cells=coverage["completed_cells"])
            return
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
            or ("stop_on_failure" in controls and controls["stop_on_failure"] is not True)
            or protocol.get("scope") != "inner_validation_only_not_qualification"
            or protocol["spec"] != spec.to_dict() or protocol["folds"] != [asdict(f) for f in folds]
            or controls["workers"] != 1 or controls["max_retries"] != 0
            or not 1 <= controls["cell_timeout"] <= VALIDATION_CELL_TIMEOUT_MAX_SECONDS
            or type(budget.get("total_timeout_seconds")) is not int
            or not (VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS
                    <= budget["total_timeout_seconds"]
                    <= VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS)
            or type(budget.get("max_rss_mb")) is not int
            or not (VALIDATION_MAX_RSS_MB_MIN
                    <= budget["max_rss_mb"]
                    <= VALIDATION_MAX_RSS_MB_MAX)
            or capture_campaign_source(ROOT) != protocol["source"]
            or execution_environment() != controls["environment"]
            or simulator_runtime_config() != controls["runtime_config"]
            or hashlib.sha256(Path(protocol["input_binding"]["path"]).read_bytes()).hexdigest()
            != protocol["input_binding"]["sha256"]):
        raise ValueError("validation protocol/source/input/environment/budget mismatch")


def _validation_group_rss_mb(group):
    from trading_agent.backtest.validation_supervisor import _validation_group_rss_mb as read_rss

    return read_rss(group)


def _validation_group_pids(group):
    from trading_agent.backtest.validation_supervisor import _validation_group_pids as read_pids

    return read_pids(group)


def _wait_for_all_reaped(group, deadline):
    from trading_agent.backtest.validation_supervisor import _wait_for_all_reaped as wait_reaped

    return wait_reaped(group, deadline, group_pids=_validation_group_pids)


def _supervise_validation(command, out_root, *, total_timeout_seconds, max_rss_mb):
    from trading_agent.backtest.validation_supervisor import _supervise_validation as supervise

    return supervise(
        command, out_root, total_timeout_seconds=total_timeout_seconds,
        max_rss_mb=max_rss_mb, cwd=ROOT, group_rss_mb=_validation_group_rss_mb,
        wait_for_all_reaped=_wait_for_all_reaped, group_pids=_validation_group_pids,
    )


def verify_validation_only_coverage(spec, result, protocol, folds, out_root):
    """Compatibility adapter with launcher collaborators resolved at call time."""
    from trading_agent.backtest.validation_reporting import (
        verify_validation_only_coverage as verify_coverage,
    )

    return verify_coverage(
        spec, result, protocol, folds, out_root,
        verify_protocol=_verify_validation_protocol,
        verify_native_report=_verify_native_report,
        audit_trial_history=audit_parallel_trial_history,
    )


def _verify_approved_payload_binding(protocol, scope_token):
    """Fail closed before the first cell if the run is not the approved payload."""
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    from trading_agent.backtest.preregistration_binding import (
        verify_registered_payload_binding,
    )

    resolved_root = preregistration_root()
    # the frozen protocol records where the approved packet was read from; the
    # run must agree with it, so a packet location cannot be swapped after the
    # protocol was frozen
    recorded_root = (protocol.get("execution") or {}).get("preregistration_root")
    if recorded_root and Path(recorded_root).resolve() != resolved_root:
        raise ValueError(
            "the preregistration root does not match the frozen protocol: "
            f"protocol {recorded_root}, resolved {resolved_root}"
        )
    declared_canonical = (protocol.get("execution") or {}).get(
        "preregistration_root_canonical"
    )
    if declared_canonical is not None and bool(declared_canonical) != (
        resolved_root == ROOT
    ):
        raise ValueError(
            "the preregistration root was redirected without the frozen "
            "protocol declaring it: "
            f"declared canonical={declared_canonical}, resolved {resolved_root}"
        )
    failed = verify_registered_payload_binding(
        protocol, scope_token, root=resolved_root,
        source_fingerprint=capture_campaign_source(ROOT)["tree_fingerprint"],
    )
    if failed:
        raise ValueError(
            "run does not match the approved preregistration; refusing to start: "
            + ", ".join(failed)
        )


def _run_validation_canonical(spec, out_root, protocol, folds, cell_runner=None):
    _verify_validation_protocol(spec, protocol, folds)
    # the worker re-verifies the approved payload independently of the launcher
    _verify_approved_payload_binding(protocol, (protocol.get("execution") or {}).get("scope_token"))
    scope_token = (protocol.get("execution") or {}).get("scope_token")
    explicit_measurement = protocol.get("explicit_measurement")
    cells, actual_folds = build_canonical_cells(
        spec, scope_token=scope_token, explicit_measurement=explicit_measurement,
    )
    actual_rows = [{"cell_id": cell.cell_id, "params": dict(cell.params),
        "cost": cell.cost_scenario.to_dict(), "simulation_window": [start, end],
        "measurement_window": [m_start, m_end]}
        for cell, start, end, _fresh, m_start, m_end in cells]
    if actual_folds != folds or actual_rows != protocol["expected_inner_cells"]:
        raise ValueError("frozen validation schedule mismatch before execution")
    runner = QualificationCellRunner(out_root, protocol, folds)
    # Allow test override of cell runner
    if cell_runner is not None:
        runner = cell_runner
    exec_context = _trial_execution_context(protocol)
    try:
        result = run_nested_wfo(
            spec, out_root=out_root, real_sensitivity=False, run_holdout=False,
            cell_runner=runner, native_gate_contract=protocol["native_gate_contract"],
            selection_contract=protocol["selection_contract"], validation_only=True,
            execution_context=exec_context, folds=folds,
        )
    except ValidationStoppedAfterFailure as exc:
        _publish_stopped_validation(spec, protocol, out_root, exc.receipt)
        return None

    from trading_agent.backtest.validation_reporting import publish_validation_result

    return publish_validation_result(
        spec, result, protocol, folds, out_root,
        verify_coverage=verify_validation_only_coverage, write_json=_durable_json,
    )


def _publish_stopped_validation(spec, protocol, out_root, receipt):
    """Abort receipts only: never fake artifacts/attempts or grant coverage PASS."""
    attempted = receipt["attempted"]
    coverage = {
        "status": "INCOMPLETE", "scope": "inner_validation_only_not_qualification",
        "evidence_class": spec.evidence_class, "reason": receipt["reason"],
        "protocol_fingerprint": protocol["fingerprint"],
        "failed_cells": [o for o in attempted if o["status"] != "COMPLETED"],
        "completed_cells": sum(o["status"] == "COMPLETED" for o in attempted),
        "attempted_cells": len(attempted), "not_attempted_cells": receipt["not_attempted"],
        "trial_accounting_status": "INCOMPLETE_BATCH_ABORTED_BEFORE_REGISTRATION",
        "live_authorized": False,
    }
    _durable_json(out_root / "terminal_INCOMPLETE.json", coverage)
    _durable_json(out_root / "exact_coverage.json", coverage)
    _durable_json(out_root / "parallel_canonical_summary.json", {
        "strategy": spec.strategy_id, "symbol": spec.symbol, "timeframe": spec.timeframe,
        "evidence_class": spec.evidence_class, "verdict": "VALIDATION_INCOMPLETE",
        "qualification_status": "NOT_QUALIFIED_VALIDATION_ONLY", "validation_only": True,
        "passes_hard_gates": False, "all_folds_selected": False, "sample_complete": False,
        "protocol_fingerprint": protocol["fingerprint"], "stop_on_failure": receipt,
        "live_authorized": False,
    })


def validate_validation_only_budget(args) -> None:
    """Fail-closed check of the validation-only execution envelope.

    Pure function: it reads the parsed arguments and raises ValueError with
    the offending limit named. It performs no I/O, creates no directory and
    starts no cell, so the budget policy can be regression-tested without
    touching the market.

    The caps live in the named constants above; this function is the single
    place that enforces them.
    """
    if args.cell_timeout > VALIDATION_CELL_TIMEOUT_MAX_SECONDS:
        raise ValueError(
            "--validation-only requires --cell-timeout <= "
            f"{VALIDATION_CELL_TIMEOUT_MAX_SECONDS}")
    if args.reuse_from is not None:
        raise ValueError("--validation-only requires a fresh packet without reuse")
    if not (VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS <= args.total_timeout
            <= VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS) or not (
                VALIDATION_MAX_RSS_MB_MIN <= args.max_rss_mb
                <= VALIDATION_MAX_RSS_MB_MAX):
        raise ValueError(
            "validation budget requires total-timeout "
            f"{VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS}..{VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS}"
            f" and max-rss-mb {VALIDATION_MAX_RSS_MB_MIN}..{VALIDATION_MAX_RSS_MB_MAX}")


def main():
    parser = argparse.ArgumentParser(description="Parallel Canonical WFO runner")
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--fixed-params", default=None,
                        help="Scalar JSON object for one fixed candidate; defaults are resolved and frozen. Validation/preflight only, not a search grid.")
    parser.add_argument("--explicit-measurement", default=None,
                        help="Validation-only locked window start:end:purge:embargo")
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
    parser.add_argument("--stop-on-failure", action="store_true",
                        help="Serial validation-only: stop dispatch after first failed attempt; preserve unattempted cells.")
    parser.add_argument("--synthetic-test-only", action="store_true",
                        help="Label validation/preflight fixtures SYNTHETIC_TEST_ONLY; never market evidence")
    parser.add_argument("--fill-cycle-development", type=Path,
                        help="Run one frozen, exposed-development fill-cycle contrast; never OOS/live.")
    parser.add_argument("--fill-cycle-arm", choices=("A0", "PLUS"), default="A0",
                        help="Arm for the isolated synthetic fill-cycle proof only.")
    parser.add_argument("--fill-cycle-btc-context", choices=("up", "down", "missing"),
                        default="missing", help="Synthetic BTC direction; never reads market prices.")
    parser.add_argument("--fill-cycle-synthetic", action="store_true",
                        help="Run the synthetic fill-cycle timer proof. Refuses frozen market input and does not qualify.")
    parser.add_argument("--total-timeout", type=int, default=1800,
                        help="Validation-only worker session wall budget "
                             f"({VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS}.."
                             f"{VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS}s)")
    parser.add_argument("--max-rss-mb", type=int, default=2048,
                        help="Validation-only sampled process-session RSS ceiling "
                             f"({VALIDATION_MAX_RSS_MB_MIN}..{VALIDATION_MAX_RSS_MB_MAX} MiB)")
    parser.add_argument("--validation-worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--max-retries", type=int, default=1,
        help="Automatic cell retries. The preregistered packet pins this to 0: "
             "a failed cell stays failed and is never retried.",
    )
    parser.add_argument(
        "--scope-token", default=None,
        help="Scope token unlocking a scoped candidate. It is frozen into the "
             "protocol, so it is part of the protocol fingerprint, the trial "
             "execution context and the cache key. Without it a scoped "
             "candidate is invisible and its cells fail at resolution.",
    )
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
    if args.fill_cycle_development is not None:
        if sys.argv[1:] != ["--fill-cycle-development", str(args.fill_cycle_development.resolve()),
                             "--no-real-sensitivity"]:
            parser.error("development command must match the frozen command exactly")
        if any((args.fill_cycle_synthetic, args.run_holdout, args.validation_only,
                args.preflight_only, args.explicit_measurement is not None,
                args.validation_worker is not None, args.real_sensitivity,
                args.fill_cycle_arm != "A0", args.fill_cycle_btc_context != "missing")):
            parser.error("development fill-cycle requires its frozen protocol and --no-real-sensitivity")
        from trading_agent.backtest.fill_cycle_development import run_packet

        raise SystemExit(run_packet(args.fill_cycle_development))
    if args.fill_cycle_synthetic:
        if any((
            args.run_holdout, args.validation_only, args.preflight_only,
            args.explicit_measurement is not None, args.validation_worker is not None,
        )):
            parser.error("--fill-cycle-synthetic does not run market validation")
        if os.environ.get("TRADING_FROZEN_RESEARCH_DATA"):
            parser.error("fill-cycle synthetic refuses a frozen market input")
        from trading_agent.backtest.fill_cycle_synthetic import run_fill_cycle_synthetic
        raise SystemExit(run_fill_cycle_synthetic(
            Path(args.out), arm=args.fill_cycle_arm, btc_context=args.fill_cycle_btc_context,
        ))
    if args.fill_cycle_arm != "A0" or args.fill_cycle_btc_context != "missing":
        parser.error("fill-cycle arm/context require --fill-cycle-synthetic")
    if args.stop_on_failure and (not args.validation_only or args.validation_worker is not None):
        parser.error("--stop-on-failure requires validation-only and cannot override a worker protocol")
    if args.fixed_params is not None and args.validation_worker is not None:
        parser.error("--fixed-params cannot override a frozen validation worker protocol")
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
    if args.synthetic_test_only and not (args.validation_only or args.preflight_only):
        parser.error("--synthetic-test-only requires validation-only or preflight-only")
    if (not args.synthetic_test_only and preregistration_root() != ROOT
            and args.scope_token):
        parser.error("REAL_MARKET requires the canonical preregistration root")
    if not 1 <= args.workers <= 4 or args.cell_timeout < 1:
        parser.error("workers must be 1..4 and cell timeout must be positive")
    if args.explicit_measurement is not None and not args.validation_only:
        parser.error("--explicit-measurement requires validation-only")
    explicit_measurement = None
    if args.explicit_measurement is not None:
        parts = args.explicit_measurement.split(":")
        if len(parts) != 4 or not all(part.isdigit() for part in parts):
            parser.error("--explicit-measurement must be start:end:purge:embargo")
        start, end, purge, embargo = (int(part) for part in parts)
        explicit_measurement = {
            "measurement_start": start,
            "measurement_end": end,
            "purge_bars": purge,
            "embargo_bars": embargo,
        }
    if args.validation_only:
        if args.run_holdout:
            parser.error("--validation-only cannot be used with --run-holdout")
        if args.real_sensitivity:
            parser.error("--validation-only cannot be used with --real-sensitivity")
        if args.workers != 1:
            parser.error("--validation-only requires --workers 1")
        try:
            validate_validation_only_budget(args)
        except ValueError as exc:
            parser.error(str(exc))
    elif not args.real_sensitivity:
        parser.error("This qualification entry point requires measured sensitivity")

    if args.fixed_params is not None:
        if not (args.validation_only or args.preflight_only):
            parser.error("--fixed-params requires validation-only or preflight-only")
        try:
            param_grid = fixed_parameter_grid(args.strategy, args.fixed_params, args.scope_token)
        except (ValueError, KeyError) as exc:
            parser.error(str(exc))
    else:
        param_grid = get_param_grid_for_strategy(args.strategy, args.scope_token)

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
        param_grid=param_grid,
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
        evidence_class="SYNTHETIC_TEST_ONLY" if args.synthetic_test_only else "REAL_MARKET",
    )
    if not spec.param_grid:
        # Fail clearly: an unsupported strategy must not fall back to another
        # one and must not silently plan an empty cell set.
        raise ValueError(
            f"no parameter grid is registered for strategy {spec.strategy_id!r}; "
            "refusing to plan an empty cell set"
        )
    protocol, folds = prepare_parallel_protocol(
        spec, out_root, workers=args.workers, cell_timeout=args.cell_timeout,
        reuse_from=args.reuse_from, scope_token=args.scope_token,
        max_retries=args.max_retries,
        **({"stop_on_failure": True} if args.stop_on_failure else {}),
        **({"validation_budget": {"total_timeout_seconds": args.total_timeout,
                                  "max_rss_mb": args.max_rss_mb}} if args.validation_only else {}),
        **({"explicit_measurement": explicit_measurement} if explicit_measurement is not None else {}),
    )
    print(f"  Protocol: {protocol['fingerprint']}", flush=True)
    if args.preflight_only:
        print(f"PREFLIGHT_ONLY: {len(folds)} folds / {len(protocol['expected_inner_cells'])} inner cells; no evaluation")
        return
    # Binding gate: the run must execute the APPROVED preregistration, not
    # merely run. It is checked on BOTH paths — the qualification path included —
    # and it is a no-op for a study that is not a scoped candidate.
    _verify_approved_payload_binding(protocol, args.scope_token)
    if args.validation_only:
        run_validation_only(spec, out_root, protocol, folds, args.cell_timeout)
        return

    print(
        f"Running CANONICAL WFO for {args.strategy} {args.symbol} {args.timeframe}...",
        flush=True,
    )
    print(f"  Params: {spec.param_grid}", flush=True)
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
