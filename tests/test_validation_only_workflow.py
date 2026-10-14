"""Synthetic canonical validation-only safety and accounting; not alpha proof."""
import copy
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import run_wfo_parallel as worker
from trading_agent.backtest import nested_wfo, tournament
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.research.trials import ExperimentRegistry

fixture_spec = importlib.util.spec_from_file_location(
    "qualification_test_fixture", Path(__file__).with_name("test_qualification_cold_resume.py"))
fixtures = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixtures)
research = fixtures.research


@pytest.fixture
def validation(tmp_path, research, monkeypatch):
    spec, calls = research
    first = nested_wfo.NestedFold("fold_000", 0, 150, 200, 240, 250, 300, 50, 10)
    second = nested_wfo.NestedFold("fold_001", 30, 210, 250, 270, 280, 300, 40, 10)
    folds = [first, second]
    monkeypatch.setattr(worker, "_get_fold_indices", lambda **kw: folds)
    monkeypatch.setattr(nested_wfo, "_get_fold_indices", lambda **kw: folds)
    original = worker.run_cell
    modifications = {"trades": 30, "tie": False, "fail": False}

    def measured(cell, **kw):
        if modifications["fail"] and cell.params["period"] == 21:
            return tournament._failed_artifact(cell, None, "synthetic_failure",
                measurement_window=(kw["measurement_start"], kw["measurement_end"]),
                simulation_window=(kw["start"], kw["end"]))
        artifact = original(cell, **kw)
        path = Path(artifact.report_path)
        report = json.loads(path.read_bytes())
        report["metrics"]["total_trades"] = modifications["trades"]
        if modifications["tie"]:
            report["metrics"]["sharpe"] = 1.0
        elif kw["measurement_start"] == 250 and cell.params["period"] == 14:
            report["metrics"]["sharpe"] = 5.0
        path.write_text(json.dumps(report))
        artifact = tournament._artifact_from_report(cell, worker.build_default_registry().describe(cell.strategy_id),
            path.parent, report, "d" * 64, measurement_window=artifact.measurement_window,
            simulation_window=artifact.simulation_window)
        return replace(artifact, execution_control={"isolation": "spawned_process",
            "timeout_seconds": kw["timeout_seconds"], "max_retries": kw["max_retries"],
            "attempts_made": 1, "resource_budget": {}, "outcome": "completed"})

    monkeypatch.setattr(worker, "run_cell", measured)
    root = tmp_path / "packet"
    root.mkdir()
    protocol, planned = worker.prepare_parallel_protocol(spec, root, workers=1, cell_timeout=60,
        validation_budget={"total_timeout_seconds": 180, "max_rss_mb": 1024})
    return spec, root, protocol, planned, calls, modifications


def execute(validation):
    spec, root, protocol, folds, *_ = validation
    # Create a cell runner that uses the mocked run_cell
    class MockCellRunner:
        def __init__(self, out_root, protocol, folds):
            pass
        def __call__(self, spec, **kwargs):
            from scripts import run_wfo_parallel as worker
            # Add timeout_seconds and max_retries from protocol (like QualificationCellRunner does)
            kwargs.setdefault("timeout_seconds", protocol["execution"]["cell_timeout"])
            kwargs.setdefault("max_retries", protocol["execution"]["max_retries"])
            return worker.run_cell(spec, **kwargs)
    return worker._run_validation_canonical(spec, root, protocol, folds, cell_runner=MockCellRunner(root, protocol, folds))


def test_all_windows_independent_and_only_inner_registry_rows(validation):
    spec, root, protocol, folds, calls, _ = validation
    result = execute(validation)
    assert len(calls) == len(protocol["expected_inner_cells"]) == 4
    assert [r.best_params["period"] for r in result.inner_results] == [21, 14]
    assert [r.val_metrics["total_trades"] for r in result.inner_results] == [30, 30]
    assert not result.outer_results and not result.passes_hard_gates
    rows = ExperimentRegistry(spec.registry_path).evaluations()
    assert len(rows) == 4 and {row.trial_phase for row in rows} == {"INNER_VALIDATION"}
    assert {row.fold_id for row in rows} == {fold.fold_id for fold in folds}
    for row in rows:
        assert row.metadata["evaluation_identity"]["execution_context"]["protocol_fingerprint"] == protocol["fingerprint"]
    coverage = json.loads((root / "exact_coverage.json").read_bytes())
    assert coverage["status"] == "PASS" and coverage["inner_cells"] == 4
    assert len(coverage["reports"]) == 4
    for forbidden in ("outer_one_shot", "sensitivity", "wfo_decision.json", "wfo_result.json"):
        assert not (root / forbidden).exists()
    timings = [json.loads(line) for line in (root / "cell_timings.jsonl").read_text().splitlines()]
    assert all(r["window"]["timeout_seconds"] == 60 and r["window"]["max_retries"] == 0 for r in timings)


@pytest.mark.parametrize("trades", [0, 4, 29])
def test_low_trade_high_sharpe_abstains_without_dropping_folds(validation, trades):
    spec, root, _, _, _, mods = validation
    mods["trades"] = trades
    result = execute(validation)
    assert len(result.inner_results) == 2 and not result.inner_selection_freezes
    assert not result.passes_hard_gates
    summary = json.loads((root / "parallel_canonical_summary.json").read_bytes())
    assert [d["decision"] for d in summary["fold_decisions"]] == ["ABSTAIN", "ABSTAIN"]
    assert len(ExperimentRegistry(spec.registry_path).evaluations()) == 4


def test_tie_break_is_canonical(validation):
    validation[-1]["tie"] = True
    assert [r.best_params["period"] for r in execute(validation).inner_results] == [14, 14]


def test_failed_trial_retained_but_never_complete_coverage(validation):
    spec, root, _, _, _, mods = validation
    mods["fail"] = True
    with pytest.raises(ValueError):
        execute(validation)
    rows = ExperimentRegistry(spec.registry_path).evaluations()
    assert len(rows) == 4
    assert sum(row.metadata["status"] == "FAILED" for row in rows) == 2
    assert not (root / "exact_coverage.json").exists()
    assert not (root / "parallel_canonical_summary.json").exists()


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "window", "metrics", "decision", "report", "control", "accounting", "freeze"])
def test_exact_coverage_rejects_missing_tampered_or_misattributed(validation, mutation):
    spec, root, protocol, folds, *_ = validation
    result = execute(validation)
    candidate = result.inner_results[0].candidate_metrics[0]
    if mutation == "missing":
        result.inner_results[0].candidate_metrics.pop()
    elif mutation == "duplicate":
        result.inner_results[0].candidate_metrics.append(copy.deepcopy(candidate))
    elif mutation == "window":
        result.inner_results[0] = replace(result.inner_results[0], val_start=result.inner_results[0].val_start + 1)
    elif mutation == "metrics":
        candidate["val_metrics"].pop("total_trades")
    elif mutation == "decision":
        path = root / "selection_decision_fold_000.json"
        raw = json.loads(path.read_bytes())
        raw["decision"] = "ABSTAIN"
        path.write_text(json.dumps(raw))
    elif mutation == "report":
        (root / candidate["validation_source"]["report_path"]).write_text("{}")
    elif mutation == "control":
        artifact = tournament.EvaluationArtifact.from_dict(candidate["validation_source"]["artifact"])
        artifact = replace(artifact, execution_control={**artifact.execution_control, "timeout_seconds": 300})
        candidate["validation_source"]["artifact"] = artifact.to_dict()
    elif mutation == "accounting":
        row = ExperimentRegistry(spec.registry_path).evaluations()[0]
        fixtures.duplicate_fixture_row(spec.registry_path, row.evaluation_id)
    elif mutation == "freeze":
        path = next((root / "inner_selection_freezes").glob("*.json"))
        raw = json.loads(path.read_bytes())
        raw["best_params"]["period"] = 1000
        path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        worker.verify_validation_only_coverage(spec, result, protocol, folds, root)


def test_budget_contract_mismatch_rejected_before_cells(validation):
    spec, root, protocol, folds, calls, _ = validation
    protocol["execution"]["max_retries"] = 1
    protocol["fingerprint"] = content_hash({k: v for k, v in protocol.items() if k != "fingerprint"})
    with pytest.raises(ValueError, match="budget mismatch"):
        worker._run_validation_canonical(spec, root, protocol, folds)
    assert not calls


def test_timeout_terminal_and_no_summary(validation, monkeypatch):
    spec, root, protocol, folds, calls, _ = validation
    monkeypatch.setattr(worker, "_supervise_validation", lambda *a, **kw: {"status": "TIMEOUT", "worker_exit_code": -15})
    with pytest.raises(RuntimeError, match="TIMEOUT"):
        worker.run_validation_only(spec, root, protocol, folds, 60)
    final = json.loads((root / "execution_finished.json").read_bytes())
    assert final["status"] == "TIMEOUT" and final["live_authorized"] is False
    assert not calls and not (root / "parallel_canonical_summary.json").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux session budget")
def test_real_supervisor_deadline_and_rss(tmp_path, monkeypatch):
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    result = worker._supervise_validation(command, tmp_path, total_timeout_seconds=0.2, max_rss_mb=512)
    assert result["status"] == "TIMEOUT" and result["elapsed_seconds"] < 3
    other = tmp_path / "memory"
    other.mkdir()
    monkeypatch.setattr(worker, "_validation_group_rss_mb", lambda pid: 600)
    result = worker._supervise_validation(command, other, total_timeout_seconds=2, max_rss_mb=512)
    assert result["status"] == "MEMORY_LIMIT"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux session budget")
def test_supervisor_stops_child_session_not_only_leader(tmp_path):
    code = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']);"
            "print(p.pid,flush=True); time.sleep(30)")
    result = worker._supervise_validation([sys.executable, "-c", code], tmp_path,
                                          total_timeout_seconds=0.3, max_rss_mb=512)
    assert result["status"] == "TIMEOUT"
    pid = int((tmp_path / "worker_stdout.log").read_text().strip())
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        assert stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    assert result["elapsed_seconds"] < 3


@pytest.mark.parametrize("flags", [
    ["--workers", "2", "--cell-timeout", "60"],
    ["--workers", "1", "--cell-timeout", "181"],
    ["--workers", "1", "--cell-timeout", "60", "--total-timeout", "1801"],
    ["--workers", "1", "--cell-timeout", "60", "--max-rss-mb", "100"],
    ["--workers", "1", "--cell-timeout", "60", "--reuse-from", "old"],
])
def test_cli_rejects_invalid_validation_budget_before_preflight(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--validation-only", "--no-real-sensitivity", *flags])
    monkeypatch.setattr(worker, "prepare_parallel_protocol", lambda *a, **k: pytest.fail("unexpected preflight"))
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2
