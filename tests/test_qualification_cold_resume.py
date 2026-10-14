"""Synthetic qualification recovery wiring; not trading/alpha evidence."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from scripts import run_wfo_parallel as module
from trading_agent.backtest import campaign_integrity, nested_wfo, tournament
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.reporting import fingerprint_payload
from trading_agent.research.trials import ExperimentRegistry, ExperimentSpec


@pytest.fixture
def research(tmp_path, monkeypatch):
    frame = pl.DataFrame({"timestamp": [datetime(2024, 1, 1) + timedelta(hours=i) for i in range(300)],
                          "open": [100.0] * 300, "high": [101.0] * 300,
                          "low": [99.0] * 300, "close": [100.0] * 300, "volume": [1.0] * 300})
    fold = nested_wfo.NestedFold("fold_000", 0, 150, 200, 240, 250, 300, 50, 10)
    path = tmp_path / "synthetic.parquet"
    frame.write_parquet(path)
    binding = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1h"}
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps(binding))
    monkeypatch.setattr(module, "load_ohlcv", lambda *args: frame)
    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", lambda *args: frame)
    monkeypatch.setattr(module, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(nested_wfo, "_get_fold_indices", lambda **kw: [fold])
    monkeypatch.setattr(module, "_resolve_frozen_holdout_window", lambda *args: None)
    monkeypatch.setattr(nested_wfo, "_resolve_frozen_holdout_window", lambda *args: None)
    source = {"code_commit": "b" * 40, "tree_fingerprint": "a" * 64, "worktree_dirty": True}
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source", lambda *args: dict(source))
    monkeypatch.setattr(nested_wfo, "_compute_commit_sha", lambda: source["code_commit"])
    monkeypatch.setattr(nested_wfo, "_compute_data_manifest_sha", lambda *args: "d" * 64)
    spec = nested_wfo.WFOSpec("rsi", "BTC/USDT", param_grid={"period": [14, 21]},
                              registry_path=str(tmp_path / "trials.sqlite3"), evidence_class="SYNTHETIC_TEST_ONLY")
    calls = []

    def fake(spec, **kw):
        calls.append((spec.params, kw["measurement_start"]))
        directory = Path(kw["out_root"]) / f"{spec.cell_id}__w{kw['start']}_{kw['end']}__d{kw.get('signal_delay_bars', 0)}"
        directory.mkdir(parents=True)
        runtime = module.simulator_runtime_config()
        config = {key: value for key, value in runtime.items() if key != "initial_capital"}
        config.update(strategy={"strategy_id": spec.strategy_id, "parameters": dict(spec.params)},
                      cost_model={"commission_rate": spec.cost_scenario.commission,
                                  "slippage_rate": spec.cost_scenario.slippage},
                      execution_timing="decision_on_closed_bar_execute_next_bar_open",
                      data_policy={"gap_policy": "record"})
        config["config_id"] = fingerprint_payload(config)
        metrics = {key: 1.0 for key in tournament._REQUIRED_METRICS}
        metrics.update(total_trades=30, sharpe=float(spec.params["period"]) / 10)
        report = {"metrics": metrics, "commit_sha": source["code_commit"],
                  "symbol": spec.symbol, "timeframe": spec.timeframe, "data_manifest_id": "d" * 64,
                  "measurement_window": [kw["measurement_start"], kw["measurement_end"]],
                  "active_config": config, "initial_capital": runtime["initial_capital"],
                  "measurement_evidence": {"opening_equity": runtime["initial_capital"]},
                  "trades": [{"synthetic_test_only": True}], "equity_curve": [["open", 100], ["close", 101]],
                  "execution_health": {"unknown_orders": 0, "manual_interventions": 0, "unprotected_positions": 0}}
        (directory / "report.json").write_text(json.dumps(report))
        return tournament._artifact_from_report(spec, module.build_default_registry().describe(spec.strategy_id),
                                               directory, report, "d" * 64,
                                               measurement_window=tuple(report["measurement_window"]),
                                               simulation_window=(kw["start"], kw["end"]),
                                               signal_delay_bars=kw.get("signal_delay_bars", 0))

    monkeypatch.setattr(module, "run_cell", fake)
    return spec, calls


def prepare(root, spec, **kwargs):
    root.mkdir()
    return module.prepare_parallel_protocol(spec, root, workers=kwargs.pop("workers", 1), cell_timeout=60, **kwargs)


def run(root, spec, protocol, folds, reuse_from=None, real_sensitivity=False):
    runner = module.QualificationCellRunner(root, protocol, folds, reuse_from=reuse_from)
    result = nested_wfo.run_nested_wfo(spec, out_root=root, run_holdout=False, real_sensitivity=real_sensitivity,
                                     cell_runner=runner, native_gate_contract=protocol["native_gate_contract"],
                                     selection_contract=protocol["selection_contract"])
    # Disabling measured sensitivity here is a TEST restriction, not permitted by CLI.
    assert result.passes_hard_gates is False
    return result, runner


def duplicate_fixture_row(path, evaluation_id):
    """Deliberate corruption of a synthetic test DB only, bypassing safe API."""
    import sqlite3
    import uuid
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO evaluation_records SELECT ?, experiment_id, fold_id, metric_name, "
                   "metric_value, created_at, environment_hash, trial_phase, metadata_json "
                   "FROM evaluation_records WHERE evaluation_id=?", ("corrupt_" + uuid.uuid4().hex, evaluation_id))


def test_history_preflight_is_readonly_and_uses_canonical_candidate_ids(tmp_path, research):
    spec, calls = research
    root = tmp_path / "original"
    protocol, folds = prepare(root, spec)
    assert not Path(spec.registry_path).exists()  # Preflight must not initialize SQLite.
    run(root, spec, protocol, folds)
    registry = ExperimentRegistry(spec.registry_path)
    before = registry.evaluations()
    audit = module.audit_parallel_trial_history(spec, protocol, folds)
    assert audit["status"] == "CONTEXT_COMPATIBLE_PENDING_EVALUATION"
    assert audit["metrics_verified"] is False
    assert audit["total_registry_rows"] == len(audit["matched_rows"]) == 3
    assert set(audit["candidate_ids"]) == {row.experiment_id for row in before}
    assert registry.evaluations() == before and len(calls) == 3


@pytest.mark.parametrize("mutation", ["legacy_context", "fold_environment", "duplicate",
                                      "subject", "window_kind", "malformed_metadata", "outer_context", "missing_marker"])
def test_history_conflicts_stop_before_any_replay(tmp_path, research, mutation):
    import sqlite3

    spec, calls = research
    old = tmp_path / "old"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    registry = ExperimentRegistry(spec.registry_path)
    before = registry.evaluations()
    target = next(row for row in before if row.trial_phase ==
                  ("OUTER_OOS" if mutation == "outer_context" else "INNER_VALIDATION"))
    metadata = copy.deepcopy(target.metadata)
    environment = target.environment_hash
    if mutation in ("legacy_context", "outer_context"):
        del metadata["evaluation_identity"]["execution_context"]
    elif mutation == "fold_environment":
        environment = "wrong"
    elif mutation == "subject":
        metadata["evaluation_identity"]["symbol"] = "ETH/USDT"
    elif mutation == "window_kind":
        metadata["evaluation_identity"]["window_kind"] = "OUTER_OOS"
    elif mutation == "missing_marker":
        del metadata["evaluation_execution_identity"]
    if mutation == "duplicate":
        duplicate_fixture_row(spec.registry_path, target.evaluation_id)
    else:
        # Corrupt only a synthetic fixture's SQLite, never operational history.
        with sqlite3.connect(spec.registry_path) as db:
            db.execute("DROP TRIGGER evaluation_records_no_update")
            db.execute("UPDATE evaluation_records SET metadata_json=?, environment_hash=? WHERE evaluation_id=?",
                       ("[]" if mutation == "malformed_metadata" else json.dumps(metadata), environment, target.evaluation_id))
    preserved = registry.evaluations()
    new = tmp_path / "new"
    with pytest.raises(ValueError, match="history conflict before evaluation"):
        prepare(new, spec)
    report = json.loads((new / "trial_history_preflight.json").read_bytes())
    assert report["status"] == "BLOCKED_HISTORY_CONFLICT" and report["conflicts"]
    assert not (new / "research_protocol.json").exists()
    assert not list(new.rglob("report.json")) and len(calls) == 3
    assert registry.evaluations() == preserved


def test_startup_rechecks_history_added_after_preflight(tmp_path, research):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    recovered, recovered_folds = prepare(new, spec, reuse_from=old)
    registry = ExperimentRegistry(spec.registry_path)
    row = registry.evaluations()[0]
    duplicate_fixture_row(spec.registry_path, row.evaluation_id)
    with pytest.raises(ValueError, match="history conflict at runner startup"):
        module.QualificationCellRunner(new, recovered, recovered_folds, reuse_from=old)
    assert len(calls) == 3 and not list(new.rglob("report.json"))


def test_unrelated_history_does_not_block_target_subject(tmp_path, research):
    spec, _ = research
    registry = ExperimentRegistry(spec.registry_path)
    unrelated = ExperimentSpec.build(strategy_name="elsewhere", strategy_code_sha="other",
                                     data_manifest_sha="other", feature_schema_hash="other", params_hash="other",
                                     search_family="other", search_space_hash="other", target_horizon="other",
                                     evaluator_version="v1", seed=42)
    registry.register_experiment(unrelated)
    registry.append_evaluation(experiment_id=unrelated.experiment_id, fold_id="fold_000",
                               metric_name="inner_val_sharpe", metric_value=1,
                               environment_hash="legacy", trial_phase="INNER_VALIDATION", metadata={})
    protocol, folds = prepare(tmp_path / "packet", spec)
    audit = module.audit_parallel_trial_history(spec, protocol, folds)
    assert audit["total_registry_rows"] == 1 and audit["matched_rows"] == []
    assert audit["status"] == "NO_MATCHING_HISTORY" and len(registry.evaluations()) == 1


def test_new_protocol_executes_fresh_preserving_previous_scope_and_burden(tmp_path, research):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec, workers=1)
    baseline, _ = run(old, spec, protocol, folds)
    registry = ExperimentRegistry(spec.registry_path)
    previous = registry.evaluations()
    packet_before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    # Execution-control change creates a different immutable protocol context;
    # it is deliberately NOT eligible for old-cell reuse.
    fresh, folds = prepare(new, spec, workers=2)
    audit = json.loads((new / "trial_history_preflight.json").read_bytes())
    assert audit["status"] == "HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED"
    assert audit["retained_history_rows"] == 3 and audit["current_context_rows"] == 0
    current, runner = run(new, spec, fresh, folds)
    assert len(calls) == 6 and runner.cache_hits == 0
    assert all(row in registry.evaluations() for row in previous)
    assert len(registry.evaluations()) == 6
    assert baseline.trial_counts["unique_experiments"] == current.trial_counts["unique_experiments"] == 2
    assert baseline.trial_counts["raw_trial_count"] == 2
    assert current.trial_counts["raw_trial_count"] == current.trial_counts["effective_trial_count"] == 4
    assert current.statistical_hardening["dsr_trials"] == 4
    assert current.trial_counts["total_trial_runs"] == 6
    replay = tmp_path / "replay"
    replay_protocol, folds = prepare(replay, spec, workers=2, reuse_from=new)
    recovered, recovered_runner = run(replay, spec, replay_protocol, folds, reuse_from=new)
    assert len(calls) == 6 and recovered_runner.cache_hits == 3
    assert current.trial_counts == recovered.trial_counts
    assert packet_before == {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}


def test_legacy_history_is_retained_but_never_used_as_replay(tmp_path, research):
    spec, calls = research
    old = tmp_path / "legacy_fixture"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    # Model an old unscoped packet by removing v2 fields in the TEST DB only.
    import sqlite3
    registry = ExperimentRegistry(spec.registry_path)
    with sqlite3.connect(spec.registry_path) as db:
        db.execute("DROP TRIGGER evaluation_records_no_update")
        for row in registry.evaluations():
            metadata = copy.deepcopy(row.metadata)
            del metadata["evaluation_execution_identity"]
            del metadata["evaluation_identity"]["execution_context"]["trial_identity_version"]
            metadata["evaluation_identity"]["commit_sha"] = "old_revision"
            db.execute("UPDATE evaluation_records SET metadata_json=? WHERE evaluation_id=?",
                       (json.dumps(metadata), row.evaluation_id))
    legacy = registry.evaluations()
    new = tmp_path / "new"
    fresh, folds = prepare(new, spec)
    audit = module.audit_parallel_trial_history(spec, fresh, folds)
    assert audit["status"] == "HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED"
    assert audit["retained_history_rows"] == 3
    current, runner = run(new, spec, fresh, folds)
    assert len(calls) == 6 and runner.cache_hits == 0
    assert all(row in registry.evaluations() for row in legacy)
    assert current.trial_counts["raw_trial_count"] == 4
    assert current.trial_counts["unique_experiments"] == 2


@pytest.mark.parametrize("workers", [1, 2])
def test_canonical_cold_replay_preserves_selection_and_trial_burden(tmp_path, research, workers):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec, workers=workers)
    baseline, original = run(old, spec, protocol, folds)
    assert len(calls) == 3 and original.cache_hits == 0
    before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    new_protocol, new_folds = prepare(new, spec, reuse_from=old, workers=workers)
    recovered, runner = run(new, spec, new_protocol, new_folds, reuse_from=old)
    assert len(calls) == 3 and runner.cache_hits == 3
    assert baseline.trial_counts == recovered.trial_counts
    assert len(ExperimentRegistry(spec.registry_path).evaluations()) == 3
    assert [f.freeze_id for f in baseline.inner_selection_freezes] == [f.freeze_id for f in recovered.inner_selection_freezes]
    assert baseline.outer_results[0].artifact.metrics == recovered.outer_results[0].artifact.metrics
    assert (old / "selection_decision_fold_000.json").read_bytes() == (new / "selection_decision_fold_000.json").read_bytes()
    assert before == {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    assert all(Path(row.artifact.report_path).is_relative_to(new) for row in recovered.outer_results)
    assert protocol["execution"]["sensitivity_reuse"] is False


def test_cold_recovery_still_runs_sensitivity_and_exact_coverage(tmp_path, research, monkeypatch):
    spec, calls = research
    monkeypatch.setattr(nested_wfo, "run_cell", module.run_cell)
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec)
    baseline, _ = run(old, spec, protocol, folds, real_sensitivity=True)
    coverage = module.verify_parallel_result_coverage(spec, baseline, protocol, folds, old)
    assert coverage["status"] == "PASS" and coverage["sensitivity_cells"] == 4
    assert len(calls) == 7
    before = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    new_protocol, folds = prepare(new, spec, reuse_from=old)
    recovered, runner = run(new, spec, new_protocol, folds, reuse_from=old, real_sensitivity=True)
    new_coverage = module.verify_parallel_result_coverage(spec, recovered, new_protocol, folds, new)
    assert new_coverage["status"] == "PASS" and new_coverage["sensitivity_cells"] == 4
    assert runner.cache_hits == 3 and len(calls) == 11  # Only sensitivity re-executes.
    assert recovered.trial_counts == baseline.trial_counts
    assert before == {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}


@pytest.mark.parametrize("evidence", ["selection", "freeze"])
def test_outer_cache_requires_origin_selection_and_freeze(tmp_path, research, evidence):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    path = (old / "selection_decision_fold_000.json" if evidence == "selection"
            else next(old.glob("inner_selection_freezes/*.json")))
    path.rename(path.with_suffix(".missing_for_negative_test"))
    fresh, folds = prepare(new, spec, reuse_from=old)
    with pytest.raises(ValueError, match="outer cache missing source selection"):
        run(new, spec, fresh, folds, reuse_from=old)
    assert len(calls) == 3


@pytest.mark.parametrize("mutation", ["source", "environment", "history", "threshold", "window", "legacy", "hash"])
def test_protocol_mismatch_refuses_import_before_evaluation(tmp_path, research, mutation):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, _ = prepare(old, spec)
    corrupted = copy.deepcopy(protocol)
    if mutation == "source":
        corrupted["source"]["tree_fingerprint"] = "f" * 64
    elif mutation == "environment":
        corrupted["execution"]["environment"]["python"] = "different"
    elif mutation == "history":
        corrupted["history"]["warmup_bars"] += 1
    elif mutation == "threshold":
        corrupted["selection_contract"]["min_validation_trades"] = 1
    elif mutation == "window":
        corrupted["folds"][0]["inner_val_end"] += 1
    elif mutation == "legacy":
        corrupted["execution"].pop("cache_version")
    else:
        corrupted["fingerprint"] = "wrong"
    if mutation != "hash":
        corrupted["fingerprint"] = content_hash({k: v for k, v in corrupted.items() if k != "fingerprint"})
    (old / "research_protocol.json").write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match="protocol mismatch"):
        prepare(new, spec, reuse_from=old)
    assert calls == [] and not (new / "research_protocol.json").exists()


def test_changed_origin_selection_refuses_outer_reuse(tmp_path, research):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    decision_path = old / "selection_decision_fold_000.json"
    decision = json.loads(decision_path.read_bytes())
    decision["selected"]["params"]["period"] = 14
    decision_path.write_text(json.dumps(decision))
    fresh, folds = prepare(new, spec, reuse_from=old)
    with pytest.raises(ValueError, match="source selection changed"):
        run(new, spec, fresh, folds, reuse_from=old)
    assert len(calls) == 3


def test_unplanned_or_wrong_subject_cannot_enter_cache(tmp_path, research):
    spec, calls = research
    root = tmp_path / "packet"
    protocol, folds = prepare(root, spec)
    runner = module.QualificationCellRunner(root, protocol, folds)
    cell = tournament.EvaluationCellSpec("rsi", "BTC/USDT", "1h", {"period": 14})
    with pytest.raises(ValueError, match="unplanned validation"):
        runner(cell, start=0, end=200, fresh=True, measurement_start=180, measurement_end=200,
               _cache_stage="INNER_VALIDATION")
    with pytest.raises(ValueError, match="subject"):
        runner(replace(cell, strategy_id="enhanced_ma"), fresh=True)
    assert calls == []


def test_registry_replay_is_atomic_and_rejects_identity_mismatch(tmp_path):
    registry = ExperimentRegistry(tmp_path / "registry.sqlite3")
    experiment = ExperimentSpec.build(strategy_name="synthetic", strategy_code_sha="a", data_manifest_sha="b",
                                      feature_schema_hash="c", params_hash="d", search_family="test",
                                      search_space_hash="e", target_horizon="1h", evaluator_version="v1", seed=7)
    registry.register_experiment(experiment)
    kwargs = dict(experiment_id=experiment.experiment_id, fold_id="fold_000", metric_name="sharpe",
                  metric_value=1.0, environment_hash="exact", metadata={"identity": "source-bound"},
                  trial_phase="INNER_VALIDATION", idempotent=True)
    original = registry.append_evaluation(**kwargs)
    assert registry.append_evaluation(**kwargs) == original
    for change in ({"metric_value": 2.0}, {"environment_hash": "changed"}, {"metadata": {"identity": "forged"}}):
        with pytest.raises(ValueError, match="trial replay"):
            registry.append_evaluation(**{**kwargs, **change})
    assert len(registry.evaluations()) == 1
    # Separate registry instances model independent callers, not just one lock.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ExperimentRegistry(registry.path).append_evaluation(**kwargs), range(2)))
    assert results == [original, original] and len(registry.evaluations()) == 1


def test_active_loader_binding_mutation_is_rejected_before_cell(tmp_path, research, monkeypatch):
    spec, calls = research
    root = tmp_path / "packet"
    protocol, folds = prepare(root, spec)
    runner = module.QualificationCellRunner(root, protocol, folds)
    active = dict(protocol["input_binding"], path="/not/the/frozen/input")
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps(active))
    with pytest.raises(ValueError, match="active frozen loader"):
        runner.run_batch(module.build_canonical_cells(spec)[0])
    assert calls == []


def test_tampered_source_freeze_refuses_outer_cache(tmp_path, research):
    spec, calls = research
    old, new = tmp_path / "old", tmp_path / "new"
    protocol, folds = prepare(old, spec)
    run(old, spec, protocol, folds)
    freeze = next(old.glob("inner_selection_freezes/*.json"))
    payload = json.loads(freeze.read_bytes())
    payload["candidate_count"] += 1
    freeze.write_text(json.dumps(payload))
    fresh, folds = prepare(new, spec, reuse_from=old)
    with pytest.raises(ValueError, match="source selection freeze changed"):
        run(new, spec, fresh, folds, reuse_from=old)
    assert len(calls) == 3


def test_forged_risk_config_is_not_a_reusable_report(tmp_path, research):
    spec, calls = research
    root = tmp_path / "packet"
    protocol, folds = prepare(root, spec)
    runner = module.QualificationCellRunner(root, protocol, folds)
    runner.run_batch(module.build_canonical_cells(spec)[0])
    path = next(root.glob("cell_resume_cache/*.json"))
    record = json.loads(path.read_bytes())
    report_path = Path(record["artifact"]["report_path"])
    report = json.loads(report_path.read_bytes())
    report["active_config"]["risk"]["cooldown_hours"] = 0
    config = report["active_config"]
    config["config_id"] = fingerprint_payload({key: value for key, value in config.items() if key != "config_id"})
    report_path.write_text(json.dumps(report))
    record["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(record))
    fresh, folds = prepare(tmp_path / "new", spec, reuse_from=root)
    recovered = module.QualificationCellRunner(tmp_path / "new", fresh, folds, reuse_from=root)
    with pytest.raises(ValueError, match="risk/sizing/protection"):
        recovered.run_batch(module.build_canonical_cells(spec)[0])
    assert len(calls) == 2
