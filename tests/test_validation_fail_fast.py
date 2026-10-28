"""Fail-fast characterization: unattempted plans never become fake attempts."""

import hashlib
import json
import math
import sys
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts import run_wfo_parallel as worker
from trading_agent.backtest.tournament import EvaluationCellSpec, _failed_artifact


def _cells():
    return [(EvaluationCellSpec(strategy_id="rsi_adx_regime_filter", symbol="BTC/USDT",
                               timeframe="1d", params={}, cost_scenario=c), 0, 260, True, 230, 260)
            for c in worker.get_cost_scenarios("all")]


def test_failure_stops_dispatch_without_fake_attempts(tmp_path, monkeypatch):
    cells = _cells()
    rows = [{"cell_id": s.cell_id, "params": {}, "cost": s.cost_scenario.to_dict(),
             "simulation_window": [start, end], "measurement_window": [ms, me]}
            for s, start, end, _, ms, me in cells]
    runner = object.__new__(worker.QualificationCellRunner)
    runner.root = tmp_path
    runner.workers = 1
    runner.protocol = {"execution": {"stop_on_failure": True},
                       "scope": "inner_validation_only_not_qualification",
                       "fingerprint": "f" * 64, "expected_inner_cells": rows}
    called = []
    def fail(self, spec, **kwargs):
        called.append(spec.cell_id)
        return _failed_artifact(spec, None, "injected_timeout",
                                simulation_window=(0, 260), measurement_window=(230, 260))
    monkeypatch.setattr(worker.QualificationCellRunner, "__call__", fail)
    with pytest.raises(worker.ValidationStoppedAfterFailure) as exc:
        runner.run_batch(cells)
    assert called == [cells[0][0].cell_id]
    assert len(exc.value.receipt["attempted"]) == 1
    assert len(exc.value.receipt["not_attempted"]) == 2
    assert not list(tmp_path.glob("cell_execution/*"))
    saved = json.loads((tmp_path / "stop_on_failure.json").read_text())
    assert saved == exc.value.receipt
    study = worker.WFOSpec(strategy_id="rsi_adx_regime_filter", symbol="BTC/USDT",
                           timeframe="1d", param_grid={"adx_threshold": [20]},
                           evidence_class="SYNTHETIC_TEST_ONLY")
    worker._publish_stopped_validation(study, runner.protocol, tmp_path, saved)
    coverage = json.loads((tmp_path / "exact_coverage.json").read_text())
    assert coverage["status"] == "INCOMPLETE"
    assert coverage["attempted_cells"] == 1
    assert coverage["completed_cells"] == 0
    assert len(coverage["not_attempted_cells"]) == 2
    assert not list(tmp_path.glob("selection_decision*"))


def test_default_uses_unchanged_batch_implementation(tmp_path, monkeypatch):
    runner = object.__new__(worker.QualificationCellRunner)
    runner.protocol = {"execution": {}}
    sentinel = object()
    monkeypatch.setattr(worker.CampaignCellRunner, "run_batch", lambda self, cells: sentinel)
    assert runner.run_batch([]) is sentinel


@pytest.mark.parametrize("extra", [[], ["--preflight-only"], ["--validation-worker", "never_read"]])
def test_cli_rejects_fail_fast_outside_validation_before_io(tmp_path, monkeypatch, extra):
    out = tmp_path / "never"
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--stop-on-failure", "--out", str(out), *extra])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2
    assert not out.exists()


def _prepare(tmp_path, monkeypatch, *, preflight):
    frame = pl.DataFrame({
        "timestamp": [datetime(2020, 1, 1) + timedelta(days=i) for i in range(500)],
        "close": [100.0 + math.sin(i / 4) for i in range(500)],
    }).with_columns(open=pl.col("close"), high=pl.col("close") + .2,
                    low=pl.col("close") - .2, volume=pl.lit(1000.0))
    source = tmp_path / "synthetic.parquet"
    frame.write_parquet(source)
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps({
        "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "symbol": "BTC/USDT", "timeframe": "1d", "exchange": "binance"}))
    original = worker.WFOSpec
    def isolated(*args, **kwargs):
        kwargs["registry_path"] = str(tmp_path / "registry.sqlite3")
        return original(*args, **kwargs)
    monkeypatch.setattr(worker, "WFOSpec", isolated)
    out = tmp_path / "packet"
    argv = ["run_wfo_parallel.py", "--strategy", "rsi_adx_regime_filter", "--symbol", "BTC/USDT",
            "--timeframe", "1d", "--fixed-params", "{}", "--workers", "1", "--cost", "all",
            "--train-months", "1", "--val-months", "1", "--test-months", "1", "--step-months", "1",
            "--cell-timeout", "90", "--total-timeout", "180", "--max-retries", "0",
            "--validation-only", "--no-real-sensitivity", "--stop-on-failure",
            "--synthetic-test-only", "--out", str(out)]
    if preflight:
        argv.append("--preflight-only")
    monkeypatch.setattr(sys, "argv", argv)
    worker.main()
    return out, json.loads((out / "research_protocol.json").read_text())


def test_worker_receipt_and_ledger_only_record_real_failed_attempt(tmp_path, monkeypatch):
    from trading_agent.backtest.nested_wfo import NestedFold, WFOSpec
    out, protocol = _prepare(tmp_path, monkeypatch, preflight=True)
    assert protocol["execution"]["stop_on_failure"] is True
    called = []
    def fault(spec, **kwargs):
        called.append(spec.cell_id)
        return _failed_artifact(spec, None, "synthetic_timeout",
            simulation_window=(kwargs["start"], kwargs["end"]),
            measurement_window=(kwargs["measurement_start"], kwargs["measurement_end"]))
    monkeypatch.setattr(worker, "run_cell", fault)
    declared = dict(protocol["spec"])
    declared["cost_scenarios"] = tuple(worker.CostScenario(**c) for c in declared["cost_scenarios"])
    spec = WFOSpec(**declared)
    folds = [NestedFold(**f) for f in protocol["folds"]]
    # Real QualificationCellRunner/_run_cell_worker/attempt ledger, injected cell result.
    def supervise(*args, **kwargs):
        worker._run_validation_canonical(spec, out, protocol, folds)
        return {"status": "COMPLETED", "worker_exit_code": 0}
    monkeypatch.setattr(worker, "_supervise_validation", supervise)
    worker.run_validation_only(spec, out, protocol, folds, 90)
    assert len(called) == 1
    actual = [p for p in out.rglob("cell_execution/*/started.json")
              if "execution_context" in json.loads(p.read_text())]
    assert len(actual) == 1
    assert len(list(out.rglob("cell_execution/*/result.json"))) == 1
    assert len(list(out.glob("cell_ledger/*.json"))) == 1
    assert not list(out.rglob("report.json"))
    assert json.loads((out / "execution_finished.json").read_text())["status"] == "INCOMPLETE"
    coverage = json.loads((out / "exact_coverage.json").read_text())
    assert coverage["attempted_cells"] == 1 and len(coverage["not_attempted_cells"]) == 2
    assert coverage["trial_accounting_status"] == "INCOMPLETE_BATCH_ABORTED_BEFORE_REGISTRATION"


@pytest.mark.slow
def test_all_complete_native_worker_still_completes(tmp_path, monkeypatch):
    out, protocol = _prepare(tmp_path, monkeypatch, preflight=False)
    assert protocol["execution"]["stop_on_failure"] is True
    assert json.loads((out / "execution_finished.json").read_text())["status"] == "COMPLETED"
    assert json.loads((out / "exact_coverage.json").read_text())["status"] == "PASS"
    assert len(list(out.glob("cell_ledger/*.json"))) == 3
    assert not (out / "stop_on_failure.json").exists()
    assert not (out / "terminal_INCOMPLETE.json").exists()
