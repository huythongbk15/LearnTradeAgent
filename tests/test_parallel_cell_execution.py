"""Synthetic timeout/durability checks, not strategy qualification."""

import concurrent.futures
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts import run_wfo_parallel as module
from trading_agent.backtest import tournament
from trading_agent.backtest.tournament import EvaluationCellSpec, FaultProfile, _failed_artifact


def hung_entry(connection, primitives, out_root, *args):
    # Disposable test child only; no market source or simulator accessed.
    Path(out_root, "hung_pid.txt").write_text(str(os.getpid()))
    time.sleep(20)
    Path(out_root, "late_mutation.txt").touch()
    connection.close()


def cell(fast=10):
    spec = EvaluationCellSpec("enhanced_ma", "BTC/USDT", "4h",
                              {"fast_period": fast, "slow_period": 60},
                              fault=FaultProfile(reject_first_n_orders=1))
    return spec, 100, 200, True, 120, 200


def receipts(root):
    return [json.loads(path.read_text()) for path in root.glob("cell_execution/*/result.json")]


def test_worker_enforces_isolation_preserves_fault_and_context(tmp_path, monkeypatch):
    spec, start, end, fresh, m_start, m_end = cell()
    seen = {}

    def fake(specification, **kwargs):
        seen.update(kwargs)
        assert specification.fault == spec.fault
        assert len(list(tmp_path.glob("cell_execution/*/started.json"))) == 1
        return _failed_artifact(specification, None, "synthetic_failure")

    monkeypatch.setattr(module, "run_cell", fake)
    result = module._run_cell_worker(module.ParallelCellRunner._primitives(spec), str(tmp_path),
                                    start, end, fresh, m_start, m_end, 2, 1,
                                    {"protocol_fingerprint": "synthetic"})
    assert seen["_use_multiprocessing"] is True
    assert seen["timeout_seconds"] == 2 and seen["max_retries"] == 1
    record = receipts(tmp_path)[0]
    assert record["artifact"] == result.to_dict()
    assert record["request"]["fault"] == asdict(spec.fault)
    assert record["request"]["execution_context"]["protocol_fingerprint"] == "synthetic"
    assert record["elapsed_sec"] >= 0 and record["live_authorized"] is False


def test_parallel_worker_preserves_expected_execution_contract(tmp_path, monkeypatch):
    from dataclasses import replace

    original, start, end, fresh, m_start, m_end = cell()
    spec = replace(original, expected_execution_contract_id="a"*64)
    seen = []
    def fake(specification, **kwargs):
        seen.append(specification.expected_execution_contract_id)
        return _failed_artifact(specification, None, "synthetic_failure")
    monkeypatch.setattr(module, "run_cell", fake)
    artifact = module._run_cell_worker(module.ParallelCellRunner._primitives(spec), str(tmp_path),
        start, end, fresh, m_start, m_end, 2, 0)
    assert seen == ["a"*64]
    record = receipts(tmp_path)[0]
    assert record["request"]["expected_execution_contract_id"] == "a"*64
    assert artifact.cell_id == spec.cell_id


def test_worker_exception_is_durable_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "run_cell", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")))
    with module.ParallelCellRunner(1, tmp_path) as runner:
        results = runner.run_batch([cell()])
    assert results[0].status == "FAILED"
    assert "injected" in results[0].failure_reasons[0]
    assert receipts(tmp_path)[0]["status"] == "FAILED"
    assert not list(tmp_path.glob("cell_resume_cache/*"))


def test_hung_child_terminated_before_worker_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(tournament, "_cell_process_entry", hung_entry)
    spec, start, end, fresh, m_start, m_end = cell()
    started = time.perf_counter()
    # The spawned child re-imports the package before it can record its pid;
    # on a loaded CI runner that alone can exceed a 3s deadline. The child
    # still sleeps 20s, so 8s keeps the hang well past the deadline.
    result = module._run_cell_worker(module.ParallelCellRunner._primitives(spec), str(tmp_path),
                                    start, end, fresh, m_start, m_end, 8, 0)
    elapsed = time.perf_counter() - started
    assert result.status == "FAILED" and "timeout" in result.failure_reasons[0]
    assert result.execution_control["attempts_made"] == 1
    assert elapsed < 15
    pid = int((tmp_path / "hung_pid.txt").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (tmp_path / "late_mutation.txt").exists()
    assert receipts(tmp_path)[0]["artifact"] == result.to_dict()


def test_batch_drains_completion_but_returns_plan_order(tmp_path, monkeypatch):
    completed = []

    def fake(primitives, *args):
        fast = primitives["params"]["fast_period"]
        time.sleep(0.05 if fast == 10 else 0.001)
        completed.append(fast)
        spec = EvaluationCellSpec(primitives["strategy_id"], primitives["symbol"],
                                  primitives["timeframe"], primitives["params"],
                                  fault=FaultProfile(**primitives["fault"]))
        return _failed_artifact(spec, None, "synthetic")

    monkeypatch.setattr(module, "_run_cell_worker", fake)
    runner = module.ParallelCellRunner(2, tmp_path)
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        runner._executor = executor
        result = runner.run_batch([cell(10), cell(20)])
        assert not runner._pending
    assert completed == [20, 10]
    assert [artifact.cell_id for artifact in result] == [cell(10)[0].cell_id, cell(20)[0].cell_id]


def test_spawn_pool_has_bounded_attempts_and_durable_results(tmp_path):
    # Timeout before strategy/data work: genuine spawn pool + isolated child.
    with module.ParallelCellRunner(2, tmp_path, timeout_seconds=0.01, max_retries=0) as runner:
        result = runner.run_batch([cell(10), cell(20)])
    assert all(artifact.status == "FAILED" for artifact in result)
    assert all(artifact.execution_control["attempts_made"] == 1 for artifact in result)
    assert len(receipts(tmp_path)) == 2


@pytest.mark.parametrize("workers,timeout,retries", [(0, 1, 0), (5, 1, 0), (True, 1, 0),
                                                       (1, 0, 0), (1, True, 0), (1, 1, -1), (1, 1, True)])
def test_invalid_execution_budget_rejected(tmp_path, workers, timeout, retries):
    with pytest.raises(ValueError):
        module.ParallelCellRunner(workers, tmp_path, timeout_seconds=timeout, max_retries=retries)


def test_wrong_root_rejected(tmp_path):
    spec, start, end, fresh, m_start, m_end = cell()
    with module.ParallelCellRunner(1, tmp_path) as runner:
        with pytest.raises(ValueError, match="root differs"):
            runner.run(spec, tmp_path / "other", start, end, fresh, m_start, m_end)


def test_transport_failure_retains_every_cell(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("injected transport failure")

    monkeypatch.setattr(module, "_run_cell_worker", fail)
    context = {"source": {"fingerprint": "before"}}
    runner = module.ParallelCellRunner(2, tmp_path, execution_context=context)
    context["source"]["fingerprint"] = "after"
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        runner._executor = executor
        results = runner.run_batch([cell(10), cell(20)])
    assert [artifact.cell_id for artifact in results] == [cell(10)[0].cell_id, cell(20)[0].cell_id]
    assert all(artifact.status == "FAILED" for artifact in results)
    assert not runner._pending
    records = [json.loads(path.read_text()) for path in tmp_path.glob("cell_execution/*/transport_failure.json")]
    assert len(records) == 2
    assert all(record["execution_context"]["source"]["fingerprint"] == "before" for record in records)


def test_completed_cell_receipt_written_before_return(tmp_path, monkeypatch):
    from dataclasses import replace
    spec, start, end, fresh, m_start, m_end = cell()
    # Typed synthetic completed artifact; no qualification or raw-source claim.
    artifact = replace(_failed_artifact(spec, None, "synthetic"), status="COMPLETED", failure_reasons=())
    monkeypatch.setattr(module, "run_cell", lambda *args, **kwargs: artifact)
    result = module._run_cell_worker(module.ParallelCellRunner._primitives(spec), str(tmp_path),
                                    start, end, fresh, m_start, m_end, 2, 0)
    assert result == artifact
    assert receipts(tmp_path)[0]["status"] == "COMPLETED"
    assert receipts(tmp_path)[0]["scope"] == "execution_receipt_not_qualification_or_resume_certificate"


def test_outer_callable_uses_same_bounded_worker(tmp_path, monkeypatch):
    seen = {}

    def fake(specification, **kwargs):
        seen.update(kwargs)
        return _failed_artifact(specification, None, "synthetic_outer")

    monkeypatch.setattr(module, "run_cell", fake)
    spec, start, end, fresh, m_start, m_end = cell()
    with module.ParallelCellRunner(1, tmp_path, timeout_seconds=7, max_retries=0) as runner:
        assert callable(runner)
        artifact = runner(spec, out_root=tmp_path, start=start, end=end, fresh=fresh,
                          measurement_start=m_start, measurement_end=m_end)
    assert artifact.status == "FAILED"
    assert seen["timeout_seconds"] == 7 and seen["_use_multiprocessing"] is True
    assert receipts(tmp_path)[0]["request"]["measurement_window"] == [m_start, m_end]
