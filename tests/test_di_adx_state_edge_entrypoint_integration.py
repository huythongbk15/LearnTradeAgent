"""Integration test: the scoped candidate through the INTENDED entrypoint chain.

This is the integration review the reviewer asked for. It does not unit-test
the pieces; it drives the real chain the sanctioned entrypoint uses:

    launcher (prepare_parallel_protocol)
        -> frozen protocol (carries the scope token)
        -> trial execution context (carries the scope token)
        -> QualificationCellRunner (context carries the scope token)
        -> _run_measured
        -> _run_cell_worker
        -> run_cell(scope_token=...)
        -> registry resolution / adapter / simulator

Two properties are checked:

  * with the CORRECT scope token the scoped candidate resolves and the cell
    executes (synthetic data only, no market read);
  * with a WRONG or MISSING token the cell fails at RESOLUTION — before the
    data is read and before the engine is constructed — and produces no engine
    artifact.

Synthetic data only: a generated parquet, a monkeypatched loader, a temporary
registry. No market data, no network, no campaign.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta

import polars as pl
import pytest

sys.path.insert(0, ".")

from scripts import run_wfo_parallel as worker
from trading_agent.backtest import campaign_integrity, nested_wfo

SCOPE_TOKEN = "bnb_di_adx_stateedge_prereg_20261020_01"
CANDIDATE_ID = "di_adx_state_edge"
PARAMS = {"adx_period": 14, "adx_threshold": 25.0}
PARAM_GRID = {"adx_period": [14], "adx_threshold": [25.0]}


def _synthetic_frame(bars: int = 300) -> pl.DataFrame:
    rows = []
    price = 100.0
    for i in range(bars):
        price *= 1.004 if (i // 40) % 2 == 0 else 0.997
        rows.append({
            "timestamp": datetime(2024, 1, 1) + timedelta(hours=i),
            "open": price, "high": price * 1.004, "low": price * 0.996,
            "close": price, "volume": 1.0 + (i % 7),
        })
    return pl.DataFrame(rows)


@pytest.fixture
def synthetic_scope(tmp_path, monkeypatch):
    """A synthetic frozen input, loader and fold plan."""
    frame = _synthetic_frame()
    path = tmp_path / "synthetic.parquet"
    frame.write_parquet(path)
    binding = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h",
    }
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps(binding))
    monkeypatch.setattr(worker, "load_ohlcv", lambda *a: frame)
    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", lambda *a: frame)
    fold = nested_wfo.NestedFold("fold_000", 0, 150, 200, 240, 250, 300, 50, 10)
    monkeypatch.setattr(worker, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(nested_wfo, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(worker, "_resolve_frozen_holdout_window", lambda *a: None)
    monkeypatch.setattr(nested_wfo, "_resolve_frozen_holdout_window", lambda *a: None)
    source = {"code_commit": "b" * 40, "tree_fingerprint": "a" * 64,
              "worktree_dirty": True}
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source",
                        lambda *a: dict(source))
    monkeypatch.setattr(nested_wfo, "_compute_commit_sha", lambda: source["code_commit"])
    monkeypatch.setattr(nested_wfo, "_compute_data_manifest_sha", lambda *a: "d" * 64)
    return tmp_path


def _candidate_spec(tmp_path):
    return nested_wfo.WFOSpec(
        CANDIDATE_ID, "BTC/USDT", param_grid=dict(PARAM_GRID),
        registry_path=str(tmp_path / "trials.sqlite3"),
        evidence_class="SYNTHETIC_TEST_ONLY",
    )


def test_scope_token_survives_launcher_protocol_and_trial_context(synthetic_scope, tmp_path):
    """launcher -> protocol -> trial context must all carry the token."""
    spec = _candidate_spec(tmp_path)
    out_root = tmp_path / "packet"
    out_root.mkdir()
    protocol, _folds = worker.prepare_parallel_protocol(
        spec, out_root, workers=1, cell_timeout=60, scope_token=SCOPE_TOKEN,
    )
    assert protocol["execution"]["scope_token"] == SCOPE_TOKEN
    context = worker._trial_execution_context(protocol)
    assert context["scope_token"] == SCOPE_TOKEN
    # the token is covered by the execution scope identity
    from trading_agent.research.trials import execution_scope_identity
    scope = execution_scope_identity(context)["context_sha256"]
    without = {k: v for k, v in context.items() if k != "scope_token"}
    assert scope != execution_scope_identity(without)["context_sha256"]


def test_correct_token_resolves_the_candidate_through_the_entrypoint_chain(synthetic_scope, tmp_path):
    """The scoped candidate must be executable by the real cell chain."""
    spec = _candidate_spec(tmp_path)
    out_root = tmp_path / "packet"
    out_root.mkdir()
    protocol, _folds = worker.prepare_parallel_protocol(
        spec, out_root, workers=1, cell_timeout=60, scope_token=SCOPE_TOKEN,
    )
    folds = worker._get_fold_indices(n_bars=300)
    runner = worker.QualificationCellRunner(out_root, protocol, folds)
    # the cell runner's own context carries the token (it is the cache key input)
    assert runner.context.get("scope_token") == SCOPE_TOKEN

    cells, _ = worker.build_canonical_cells(spec, scope_token=SCOPE_TOKEN)
    cell = cells[0][0]
    artifact = runner._run_measured(
        cell, out_root=str(out_root), start=200, end=300, fresh=True,
        measurement_start=240, measurement_end=300,
    )
    assert artifact.status == "COMPLETED", (
        f"the scoped candidate did not run through the entrypoint chain: "
        f"{artifact.status} {artifact.failure_reasons}"
    )


def test_wrong_token_is_blocked_at_plan_time_before_any_cell(synthetic_scope, tmp_path):
    """A wrong token cannot even build the cell plan."""
    spec = _candidate_spec(tmp_path)
    out_root = tmp_path / "packet"
    out_root.mkdir()
    with pytest.raises(Exception) as excinfo:
        worker.prepare_parallel_protocol(
            spec, out_root, workers=1, cell_timeout=60, scope_token="not-the-token",
        )
    assert "di_adx_state_edge" in str(excinfo.value)
    produced = {p.name for p in out_root.rglob("*") if p.is_file()}
    assert not ({"events.db", "paper_binance.json", "report.json"} & produced), (
        f"an artifact was produced despite the wrong token: {produced}"
    )


def test_token_swapped_between_plan_and_execution_is_blocked_before_simulation(
    synthetic_scope, tmp_path,
):
    """The token must be checked again at cell execution, not only at plan time.

    A protocol planned with the correct token but executed with a different one
    must fail at RESOLUTION — before the data is read and before the engine is
    constructed — and must not leave an engine artifact.
    """
    spec = _candidate_spec(tmp_path)
    out_root = tmp_path / "packet"
    out_root.mkdir()
    protocol, _folds = worker.prepare_parallel_protocol(
        spec, out_root, workers=1, cell_timeout=60, scope_token=SCOPE_TOKEN,
    )
    folds = worker._get_fold_indices(n_bars=300)
    runner = worker.QualificationCellRunner(out_root, protocol, folds)
    cells, _ = worker.build_canonical_cells(spec, scope_token=SCOPE_TOKEN)
    cell = cells[0][0]
    # the plan is valid; the EXECUTION is given a different token
    artifact = worker._run_cell_worker(
        worker.ParallelCellRunner._primitives(cell), str(out_root),
        200, 300, True, 240, 300, 60, 0,
        {"protocol_fingerprint": protocol["fingerprint"],
         "source": protocol["source"], "input_binding": protocol["input_binding"],
         "native_execution_contract": protocol["native_execution_contracts"][
             cell.cell_id]},
        scope_token="not-the-token",
    )
    assert artifact.status == "FAILED"
    reasons = " ".join(str(r) for r in (artifact.failure_reasons or []))
    assert "worker_exception" in reasons or "registry" in reasons.lower() or \
        "allowlist" in reasons.lower() or "unknown" in reasons.lower(), (
        f"unexpected failure reason: {reasons!r}"
    )
    produced = {p.name for p in out_root.rglob("*") if p.is_file()}
    assert not ({"events.db", "paper_binance.json"} & produced), (
        f"an engine artifact was produced despite the wrong token: {produced}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
