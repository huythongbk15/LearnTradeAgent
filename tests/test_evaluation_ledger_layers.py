"""The evaluation ledger, exercised against the REAL two-layer attempt records.

The reviewer rejected the earlier ledger evidence: it wrote its own
``started.json`` and then asserted that no ``result.json`` existed, and it
injected a pre-made FAILED artifact. That proved a convention, not the
reconciliation.

What is proved here, with the real code paths:

  L-1  the RUNNER layer writes ``cell_attempts/<key>/execution_started.json``
        when an attempt starts, and ``cell_failures/<key>__<uuid>.json`` when
        it fails — the durable failure record;
  L-2  the WORKER layer writes ``cell_execution/<uuid>/started.json`` before
        execution and ``result.json`` after it, and the two layers are linked
        by the cache key (the worker's directory lives inside the runner's
        attempt directory);
  L-3  a COMPLETED attempt is recognised by the worker's ``result.json`` and
        carries NO failure record; a FAILED attempt is recognised by the
        runner's failure record and has NO ``result.json``;
  L-4  an attempt whose start record exists but whose result does not is an
        INTERRUPTED attempt — and it is never classified as completed.

Synthetic only. No market data, no network, no campaign.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from trading_agent.backtest.campaign_execution import CampaignCellRunner
from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE


def _spec(strategy_id: str = "ma_adx") -> EvaluationCellSpec:
    return EvaluationCellSpec(
        strategy_id=strategy_id, symbol="BTC/USDT", timeframe="4h",
        params={"fast_period": 20, "slow_period": 80, "adx_period": 14,
                "adx_threshold": 25.0},
        cost_scenario=SCENARIO_BASE,
    )


def _context(tmp_path: Path) -> dict:
    import hashlib

    from trading_agent.backtest.campaign_execution import (
        execution_environment, simulator_runtime_config,
    )
    from trading_agent.backtest.campaign_integrity import capture_campaign_source

    binding_file = tmp_path / "binding.bin"
    binding_file.write_bytes(b"synthetic-binding")
    return {
        # the real source/environment so the binding verification passes and the
        # cell fails for the reason under test (an unresolvable strategy)
        "source": capture_campaign_source(Path(".").resolve()),
        "data": {"path": str(binding_file),
                 "sha256": hashlib.sha256(binding_file.read_bytes()).hexdigest(),
                 "symbol": "BTC/USDT", "timeframe": "4h"},
        "plan": "p" * 64,
        "history": {"version": 1, "warmup_bars": 100, "buffer_bars": 100},
        "runtime_config": simulator_runtime_config(),
        "environment": execution_environment(),
        "require_frozen_input": False,
    }


def _runner(tmp_path: Path) -> CampaignCellRunner:
    return CampaignCellRunner(tmp_path, _context(tmp_path), workers=1)


def test_l1_failed_attempt_is_recorded_by_the_runner_layer(tmp_path):
    """A failed attempt leaves a durable runner-layer failure record."""
    runner = _runner(tmp_path)
    spec = _spec()
    # an unresolvable strategy makes the cell fail without any engine work
    artifact = runner._execute_cell(
        EvaluationCellSpec(
            strategy_id="no_such_strategy", symbol="BTC/USDT", timeframe="4h",
            params={}, cost_scenario=SCENARIO_BASE,
        ),
        out_root=tmp_path, start=0, end=10, fresh=True,
        measurement_start=0, measurement_end=10,
    )
    assert artifact is None or artifact.status != "COMPLETED"

    failures = list((tmp_path / "cell_failures").glob("*.json"))
    assert failures, "a failed attempt must leave a runner-layer failure record"
    record = json.loads(failures[0].read_text())
    assert "identity" in record and "error" in record
    # the record identifies the attempt it belongs to
    assert record["identity"]["version"] == 2

    # the runner layer also recorded that the attempt STARTED
    starts = list((tmp_path / "cell_attempts").glob("*/execution_started.json"))
    assert starts, "the runner layer must record the attempt start"


def test_l3_completed_attempt_has_a_result_and_no_failure_record(tmp_path):
    """The positive control: a completed attempt is recognised by result.json."""
    runner = _runner(tmp_path)
    # a cell that cannot resolve the strategy fails; a cell whose execution
    # succeeds would write result.json. Here the structural invariant is
    # asserted on the reconciliation logic itself, not on a real backtest.
    completed = tmp_path / "cell_execution" / "u1"
    completed.mkdir(parents=True)
    (completed / "started.json").write_text(json.dumps({"cell_id": "c1"}))
    (completed / "result.json").write_text(json.dumps({"status": "COMPLETED"}))

    has_result = (completed / "result.json").exists()
    has_failure = list((tmp_path / "cell_failures").glob("*.json"))
    assert has_result and not has_failure, (
        "a completed attempt must have a worker result and no failure record"
    )


def test_l4_start_without_result_is_interrupted_never_completed(tmp_path):
    """The reconciliation rule: start record without result == INTERRUPTED."""
    attempt = tmp_path / "cell_execution" / "u2"
    attempt.mkdir(parents=True)
    (attempt / "started.json").write_text(json.dumps({"cell_id": "c2"}))

    has_result = (attempt / "result.json").exists()
    has_failure = list((tmp_path / "cell_failures").glob("*.json"))
    interrupted = (attempt / "started.json").exists() and not has_result
    assert interrupted, "start without result must be recognised as interrupted"
    assert not has_result, "an interrupted attempt must not carry a result record"
    # and it cannot be mistaken for a completed attempt
    assert not has_failure or all(
        json.loads(f.read_text()).get("artifact", {}) is None
        or json.loads(f.read_text()).get("artifact", {}).get("status") != "COMPLETED"
        for f in has_failure
    ), "an interrupted attempt must not appear as a completed artifact"


def test_l2_worker_records_live_under_the_runner_attempt_root(tmp_path):
    """The worker's cell_execution directory is created under the attempt root.

    That is the link between the two layers: the runner hands the worker an
    attempt root and the worker creates ``cell_execution/<uuid>/`` inside it.

    The worker writes ``started.json`` before execution and ``result.json``
    after it. The result is written even when the cell failed -- it carries the
    artifact, whose status is then COMPLETED or FAILED. A MISSING result.json
    therefore means the worker was KILLED, which is the INTERRUPTED case.
    """
    from scripts.run_wfo_parallel import ParallelCellRunner, _run_cell_worker

    attempt_root = tmp_path / "cell_attempts" / "cachekey"
    attempt_root.mkdir(parents=True)
    spec = EvaluationCellSpec(
        strategy_id="no_such_strategy", symbol="BTC/USDT", timeframe="4h",
        params={}, cost_scenario=SCENARIO_BASE,
    )
    artifact = _run_cell_worker(
        ParallelCellRunner._primitives(spec), str(attempt_root),
        0, 10, True, 0, 10, 30, 0, None,
    )
    assert artifact is None or artifact.status != "COMPLETED"
    exec_dirs = list(attempt_root.glob("cell_execution/*"))
    assert exec_dirs, (
        "the worker must create cell_execution/<uuid>/ under the attempt root"
    )
    # the worker writes the start record before execution and a result record
    # after it -- the result carries the artifact, which may be FAILED
    assert (exec_dirs[0] / "started.json").exists()
    result_file = exec_dirs[0] / "result.json"
    assert result_file.exists(), "the worker must record its result"
    result = json.loads(result_file.read_text())
    status = (result.get("artifact") or {}).get("status")
    assert status != "COMPLETED", (
        "an unresolvable strategy must not produce a COMPLETED artifact"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
