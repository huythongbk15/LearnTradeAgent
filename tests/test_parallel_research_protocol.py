"""Synthetic engineering checks, never market qualification evidence."""

import copy
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from trading_agent.backtest import campaign_integrity, tournament
from trading_agent.backtest.nested_wfo import NestedFold, WFOSpec
from trading_agent.backtest.selection_eligibility import freeze_selection_contract
from trading_agent.research.param_grids import get_param_grid


def load_script(relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location("protocol_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parallel_grid_uses_catalog_authority():
    module = load_script("scripts/run_wfo_parallel.py")
    assert module.get_param_grid_for_strategy("ma_vol_target") == get_param_grid("vol_target")
    assert module.get_param_grid_for_strategy("regime_switching") == get_param_grid("regime_ensemble")


@pytest.fixture
def planned(tmp_path, monkeypatch):
    module = load_script("scripts/run_wfo_parallel.py")
    spec = WFOSpec("ma_vol_target", "BTC/USDT", param_grid={"fast_period": [20], "slow_period": [60]},
                   registry_path=str(tmp_path / "trials.sqlite3"), evidence_class="SYNTHETIC_TEST_ONLY")
    fold = NestedFold("fold_000", 0, 150, 200, 240, 250, 300, 50, 10)
    frame = pl.DataFrame({"timestamp": [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(300)],
                          "open": [100.0] * 300, "high": [101.0] * 300,
                          "low": [99.0] * 300, "close": [100.0] * 300, "volume": [1.0] * 300})
    monkeypatch.setattr(module, "load_ohlcv", lambda *args: frame)
    seen = {}

    def build(**kwargs):
        seen.update(kwargs)
        return [fold]

    monkeypatch.setattr(module, "_get_fold_indices", build)
    # The canonical resolver returns THREE values; planner must not unpack two.
    monkeypatch.setattr(module, "_resolve_frozen_holdout_window", lambda *args: (300, 400, {}))
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source", lambda *args: {
        "code_commit": "b" * 40, "tree_fingerprint": "a" * 64, "worktree_dirty": True,
    })
    path = tmp_path / "input.parquet"
    frame.write_parquet(path)
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps({
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exchange": "binance", "symbol": spec.symbol, "timeframe": spec.timeframe,
    }))
    return module, spec, fold, seen


def test_protocol_is_frozen_before_work_and_rejects_packet_reuse(planned, tmp_path):
    module, spec, fold, seen = planned
    protocol, folds = module.prepare_parallel_protocol(spec, tmp_path, workers=1, cell_timeout=60)
    assert protocol["selection_contract"] == freeze_selection_contract(min_validation_trades=30, min_validation_sharpe=0)
    assert folds == [fold]
    assert protocol["folds"] == [asdict(fold)]
    assert seen["purge"] >= protocol["history"]["warmup_bars"]
    assert len(protocol["expected_inner_cells"]) == 1
    assert protocol["live_authorized"] is False
    assert not (tmp_path / "parallel_canonical_summary.json").exists()
    with pytest.raises(ValueError, match="new output"):
        module.prepare_parallel_protocol(spec, tmp_path, workers=1, cell_timeout=60)


def test_unbound_input_rejected(planned, tmp_path, monkeypatch):
    module, spec, *_ = planned
    monkeypatch.delenv("TRADING_FROZEN_RESEARCH_DATA")
    with pytest.raises(ValueError, match="frozen input"):
        module.prepare_parallel_protocol(spec, tmp_path, workers=1, cell_timeout=60)


def coverage_fixture(planned, tmp_path):
    module, spec, fold, _ = planned
    protocol, folds = module.prepare_parallel_protocol(spec, tmp_path, workers=1, cell_timeout=60)
    params = {"fast_period": 20, "slow_period": 60}
    cell = tournament.EvaluationCellSpec(spec.strategy_id, spec.symbol, spec.timeframe, params)
    descriptor = module.build_default_registry().describe(spec.strategy_id)

    def artifact_for(name, window, simulation, cost=cell.cost_scenario, delay=0):
        directory = tmp_path / name
        directory.mkdir()
        metrics = {key: 1.0 for key in tournament._REQUIRED_METRICS}
        metrics["total_trades"] = 30
        report = {"metrics": metrics, "commit_sha": "b" * 40,
                  "measurement_window": list(window),
                  "execution_health": {"unknown_orders": 0, "manual_interventions": 0, "unprotected_positions": 0}}
        path = directory / "report.json"
        path.write_text(json.dumps(report))
        request_cell = tournament.EvaluationCellSpec(spec.strategy_id, spec.symbol, spec.timeframe, params, cost)
        return tournament._artifact_from_report(request_cell, descriptor, directory, report, "d" * 64,
                                               measurement_window=window, simulation_window=simulation, signal_delay_bars=delay)

    planned_cell = protocol["expected_inner_cells"][0]
    val = artifact_for("validation", (fold.inner_val_start, fold.inner_val_end), tuple(planned_cell["simulation_window"]))
    outer_artifact = artifact_for("outer", (fold.outer_test_start, fold.outer_test_end), (fold.outer_test_start, fold.outer_test_end))
    val_path = Path(val.report_path)
    candidate = {"params": params, "cost_scenario": "1x", "val_metrics": dict(val.metrics),
                 "validation_source": {"report_path": str(val_path.relative_to(tmp_path)),
                                       "report_sha256": hashlib.sha256(val_path.read_bytes()).hexdigest(),
                                       "artifact": val.to_dict()}}
    best = {**params, "cost_scenario": "1x"}
    inner = SimpleNamespace(fold_id=fold.fold_id, val_start=fold.inner_val_start, val_end=fold.inner_val_end,
                            candidate_metrics=[candidate], best_params=best)
    outer = SimpleNamespace(fold_id=fold.fold_id, test_start=fold.outer_test_start, test_end=fold.outer_test_end,
                            artifact=outer_artifact, params=best, test_metrics=dict(outer_artifact.metrics))
    from trading_agent.backtest.nested_wfo import _sensitivity_requests, _sensitivity_receipt
    expected = _sensitivity_requests(spec, [outer], folds)
    sensitivity_root = tmp_path / "sensitivity"
    sensitivity_root.mkdir()
    (sensitivity_root / "sensitivity_plan.json").write_text(json.dumps({"version": 1, "requests": expected, "startup": "fresh_at_outer_test_start"}))
    receipts = []
    for request in expected:
        art = artifact_for("sensitivity/" + request["kind"], tuple(request["measurement_window"]),
                           tuple(request["simulation_window"]), cost=tournament.CostScenario(**request["cost"]),
                           delay=request["signal_delay_bars"])
        receipts.append(_sensitivity_receipt(request, art, sensitivity_root))
    result = SimpleNamespace(inner_results=[inner], outer_results=[outer], aggregate_metrics={
        "sensitivity": {"coverage": {"status": "PASS", "expected": expected, "observed": receipts,
                                     "source_root": "sensitivity", "live_authorized": False}}})
    return module, spec, protocol, folds, result


def test_exact_inventory_positive_with_raw_reconstruction(planned, tmp_path):
    module, spec, protocol, folds, result = coverage_fixture(planned, tmp_path)
    coverage = module.verify_parallel_result_coverage(spec, result, protocol, folds, tmp_path)
    assert coverage["status"] == "PASS"
    assert coverage["inner_cells"] == coverage["outer_folds"] == 1
    assert len(coverage["reports"]) == 5
    assert coverage["live_authorized"] is False


@pytest.mark.parametrize("mutation", ["missing_fold", "duplicate_fold", "missing_candidate", "duplicate_candidate",
                                    "wrong_window", "wrong_subject", "wrong_cost", "report_tamper", "plan_tamper",
                                    "missing_sensitivity", "changed_sensitivity_report", "unfrozen_sensitivity"])
def test_exact_inventory_negative_controls(planned, tmp_path, mutation):
    module, spec, protocol, folds, result = coverage_fixture(planned, tmp_path)
    inner, outer = result.inner_results[0], result.outer_results[0]
    if mutation == "missing_fold":
        result.outer_results.clear()
    elif mutation == "duplicate_fold":
        result.outer_results.append(outer)
    elif mutation == "missing_candidate":
        inner.candidate_metrics.clear()
    elif mutation == "duplicate_candidate":
        inner.candidate_metrics.append(copy.deepcopy(inner.candidate_metrics[0]))
    elif mutation == "wrong_window":
        outer.artifact = replace(outer.artifact, measurement_window=(251, 300))
    elif mutation == "wrong_subject":
        outer.artifact = replace(outer.artifact, symbol="ETH/USDT")
    elif mutation == "wrong_cost":
        outer.artifact = replace(outer.artifact, commission=0)
    elif mutation == "report_tamper":
        Path(inner.candidate_metrics[0]["validation_source"]["artifact"]["report_path"]).write_text("{}")
    elif mutation == "missing_sensitivity":
        result.aggregate_metrics["sensitivity"]["coverage"]["observed"].pop()
    elif mutation == "changed_sensitivity_report":
        receipt = result.aggregate_metrics["sensitivity"]["coverage"]["observed"][0]
        Path(receipt["artifact"]["report_path"]).write_text("{}")
    elif mutation == "unfrozen_sensitivity":
        (tmp_path / "sensitivity/sensitivity_plan.json").write_text("{}")
    else:
        protocol["expected_inner_cells"].clear()
    with pytest.raises(ValueError):
        module.verify_parallel_result_coverage(spec, result, protocol, folds, tmp_path)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong_pair", "wrong_tf", "timeout", "extra", "empty"])
def test_subject_coverage_negative_controls(mutation):
    wanted = [("ma_vol_target", "BTC/USDT", "1h"), ("ma_vol_target", "ETH/USDT", "4h")]
    results = [dict(strategy_id=sid, symbol=symbol, timeframe=tf, status="FAIL") for sid, symbol, tf in wanted]
    if mutation == "missing":
        results.pop()
    elif mutation == "duplicate":
        results.append(dict(results[0]))
    elif mutation == "wrong_pair":
        results[1]["symbol"] = "SOL/USDT"
    elif mutation == "wrong_tf":
        results[1]["timeframe"] = "1h"
    elif mutation == "timeout":
        results[1]["status"] = "TIMEOUT"
    elif mutation == "extra":
        results.append(dict(strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", status="PASS"))
    else:
        wanted, results = [], []
    assert campaign_integrity.verify_subject_coverage(requested_subjects=wanted, results=results)["status"] == "INCOMPLETE"


def test_subject_coverage_strategy_fail_is_not_missing_evidence():
    wanted = [("ma_vol_target", "BTC/USDT", "1h")]
    rows = [dict(strategy_id="ma_vol_target", symbol="BTC/USDT", timeframe="1h", status="FAIL")]
    assert campaign_integrity.verify_subject_coverage(requested_subjects=wanted, results=rows)["status"] == "PASS"


def test_cli_forwards_contract_and_abstains_without_summary(planned, tmp_path, monkeypatch):
    module, *_ = planned
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--strategy", "ma_vol_target", "--symbol", "BTC/USDT", "--out", str(tmp_path), "--workers", "1"])

    def wfo(spec, **kwargs):
        from trading_agent.backtest.selection_eligibility import assess_validation_candidates
        decision = assess_validation_candidates(kwargs["selection_contract"], [
            {"params": {"fast_period": 20}, "cost_scenario": "1x", "val_metrics": {"total_trades": 5, "sharpe": 100}},
        ])
        assert decision["decision"] == "ABSTAIN"
        assert kwargs["native_gate_contract"]["run_holdout"] is False
        raise ValueError("NOT_QUALIFIED: fixture ABSTAIN")

    monkeypatch.setattr(module, "run_nested_wfo", wfo)
    with pytest.raises(ValueError, match="ABSTAIN"):
        module.main()
    assert (tmp_path / "research_protocol.json").exists()
    assert not (tmp_path / "parallel_canonical_summary.json").exists()


def test_orchestrator_rejects_legacy_summary(tmp_path):
    module = load_script("scripts/strategy_research/run_campaign.py")
    (tmp_path / "parallel_canonical_summary.json").write_text(json.dumps({"passes_hard_gates": True}))
    with pytest.raises(FileNotFoundError):
        module._validate_worker_summary(tmp_path, code_name="ma_vol_target", symbol="BTC/USDT", timeframe="1h", param_grid={})


@pytest.mark.parametrize("mutation", [None, "wrong_subject", "coverage_count", "changed_report", "duplicate_report", "lower_selection", "summary_pass_forgery", "legacy_trial_contract"])
def test_orchestrator_checks_bound_worker_packet(planned, tmp_path, mutation):
    module, spec, protocol, folds, result = coverage_fixture(planned, tmp_path)
    coverage = module.verify_parallel_result_coverage(spec, result, protocol, folds, tmp_path)
    summary = {"strategy": spec.strategy_id, "symbol": spec.symbol, "timeframe": spec.timeframe,
               "protocol_fingerprint": protocol["fingerprint"], "exact_coverage": coverage,
               "run_holdout": False, "passes_hard_gates": False, "aggregate_metrics": {}}
    decision_bytes = json.dumps({"spec": spec.to_dict(), "passes_hard_gates": False, "aggregate_metrics": {}}).encode()
    (tmp_path / "wfo_decision.json").write_bytes(decision_bytes)
    summary["decision_sha256"] = hashlib.sha256(decision_bytes).hexdigest()
    if mutation == "wrong_subject":
        summary["symbol"] = "ETH/USDT"
    elif mutation == "coverage_count":
        coverage["inner_cells"] = 0
    elif mutation == "changed_report":
        (tmp_path / coverage["reports"][0]["path"]).write_text("{}")
    elif mutation == "duplicate_report":
        coverage["reports"][1] = copy.deepcopy(coverage["reports"][0])
    elif mutation == "lower_selection":
        from trading_agent.backtest.campaign_evidence import content_hash
        protocol["selection_contract"] = freeze_selection_contract(min_validation_trades=5, min_validation_sharpe=0)
        protocol["fingerprint"] = content_hash({key: value for key, value in protocol.items() if key != "fingerprint"})
        summary["protocol_fingerprint"] = coverage["protocol_fingerprint"] = protocol["fingerprint"]
    elif mutation == "summary_pass_forgery":
        summary["passes_hard_gates"] = True
    elif mutation == "legacy_trial_contract":
        from trading_agent.backtest.campaign_evidence import content_hash
        protocol.pop("trial_identity_contract")
        protocol["fingerprint"] = content_hash({key: value for key, value in protocol.items() if key != "fingerprint"})
        summary["protocol_fingerprint"] = coverage["protocol_fingerprint"] = protocol["fingerprint"]
    (tmp_path / "research_protocol.json").write_text(json.dumps(protocol))
    (tmp_path / "exact_coverage.json").write_text(json.dumps(coverage))
    (tmp_path / "parallel_canonical_summary.json").write_text(json.dumps(summary))
    orchestrator = load_script("scripts/strategy_research/run_campaign.py")

    def validate():
        return orchestrator._validate_worker_summary(tmp_path, code_name=spec.strategy_id,
                                                     symbol=spec.symbol, timeframe=spec.timeframe,
                                                     param_grid=spec.param_grid)
    if mutation is None:
        assert validate()["passes_hard_gates"] is False
    else:
        with pytest.raises(ValueError):
            validate()


@pytest.mark.parametrize("mutation", [None, "duplicate", "changed"])
def test_input_manifest_bytes_and_unique_subjects(tmp_path, mutation):
    module = load_script("scripts/strategy_research/run_campaign.py")
    source = tmp_path / "bars.parquet"
    source.write_bytes(b"fixture")
    row = {"symbol": "BTC/USDT", "timeframe": "1h", "status": "FROZEN",
           "path": str(source), "byte_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    records = [row, dict(row)] if mutation == "duplicate" else [row]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"inputs": records}))
    if mutation == "changed":
        source.write_bytes(b"changed")
    if mutation:
        with pytest.raises(ValueError):
            module._load_frozen_inputs(manifest)
    else:
        assert module._load_frozen_inputs(manifest)[("BTC/USDT", "1h")]["path"] == str(source)
