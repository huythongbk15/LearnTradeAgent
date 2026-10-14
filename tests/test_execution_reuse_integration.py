"""Synthetic CLI wiring checks. No market, promotion or live authority."""

import copy
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_real_wfo_campaign as cli
from scripts.strategy_research import run_campaign as orchestrator
from trading_agent.backtest import campaign_execution, campaign_integrity, nested_wfo, tournament
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_plan import freeze_campaign_plan, semantic_fingerprint, verify_frozen_plan


def plan(at, **changes):
    value = freeze_campaign_plan(
        subject={"strategy_id": "rsi", "symbol": "BTC/USDT", "market_type": "spot", "timeframe": "1h"},
        folds=[{"fold_id": "fold_000", "train_span": ["2024-01-01", "2024-02-01"],
                "val_span": ["2024-02-02", "2024-03-01"], "test_span": ["2024-03-02", "2024-04-01"],
                "purge_bars": 1, "embargo_bars": 1}],
        data_manifest={"file_sha256": "a" * 64}, param_grid={"period": [14]},
        cost_schedule={"commission_bps": 10, "slippage_bps": 5, "spread_bps": 0, "round_trip_bps": 30},
        gate_set=cli.DEFAULT_GATE_SET, frozen_by="synthetic-test", frozen_at=at,
        execution_source={"code_commit": "b" * 40, "tree_fingerprint": "c" * 64, "worktree_dirty": True},
    )
    value.update(changes)
    value["semantic_fingerprint"] = semantic_fingerprint(value)
    value["plan_fingerprint"] = content_hash({k: v for k, v in value.items() if k != "plan_fingerprint"})
    return value


def test_semantic_identity_is_repeatable_and_excludes_only_packet_metadata():
    first = plan(datetime(2026, 1, 1, tzinfo=UTC))
    second = plan(datetime(2026, 1, 2, tzinfo=UTC), frozen_by="another-operator")
    assert first["plan_fingerprint"] != second["plan_fingerprint"]
    assert first["semantic_fingerprint"] == second["semantic_fingerprint"]
    assert semantic_fingerprint(first) == semantic_fingerprint(first)
    verify_frozen_plan(first)
    forged = {**first, "semantic_fingerprint": "f" * 64}
    forged["plan_fingerprint"] = content_hash({k: v for k, v in forged.items() if k != "plan_fingerprint"})
    with pytest.raises(ValueError, match="semantic fingerprint"):
        verify_frozen_plan(forged)


@pytest.mark.parametrize("field", ["subject", "execution_source", "execution_context", "data_manifest", "cost_schedule", "gate_set", "param_grid", "folds"])
def test_semantic_identity_retains_all_execution_constraints(tmp_path, field):
    first = plan(datetime(2026, 1, 1, tzinfo=UTC))
    source = tmp_path / "source"
    (source / "rsi").mkdir(parents=True)
    (source / "cell_resume_cache").mkdir()
    (source / "rsi/campaign_plan.json").write_text(json.dumps(first))
    second = copy.deepcopy(first)
    # Deliberately change the field while recomputing hashes, not just corrupt bytes.
    second[field] = {"synthetic_different": True}
    second["semantic_fingerprint"] = semantic_fingerprint(second)
    second["plan_fingerprint"] = content_hash({k: v for k, v in second.items() if k != "plan_fingerprint"})
    with pytest.raises(ValueError, match="reuse plan mismatch"):
        cli._execution_plan_binding(second, evidence=tmp_path / "new", strategy="rsi", reuse_from=source)


def test_real_entrypoint_cold_import_across_timestamp_changes(tmp_path, monkeypatch):
    # Drive main()/argparse/actual plan freeze/actual cache importer. Only the
    # expensive nested evaluator is synthetic; stop before any bundle/publish.
    def test_helper(filename):
        module_spec = importlib.util.spec_from_file_location(filename, Path(__file__).with_name(filename + ".py"))
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module
    produce = test_helper("test_campaign_resume_cache").produce
    write_bars = test_helper("test_run_real_wfo_campaign_script").write_bars

    data = write_bars(tmp_path / "synthetic.parquet")
    calls = []
    captured = []

    def evaluate(cell, **kwargs):
        calls.append(cell.cell_id)
        commit = campaign_integrity.capture_campaign_source(campaign_execution.ROOT)["code_commit"]
        return produce(cell, commit, **kwargs)

    monkeypatch.setattr(campaign_execution, "run_cell", evaluate)

    def nested(spec, *, cell_runner, **kwargs):
        cell = tournament.EvaluationCellSpec(spec.strategy_id, spec.symbol, spec.timeframe,
                                             {"fast_period": 10, "slow_period": 20})
        artifact = cell_runner(cell, out_root=cell_runner.root, end=10)
        captured.append((cell_runner.cache_hits, Path(artifact.report_path).read_bytes()))
        raise RuntimeError("SYNTHETIC_ENGINEERING_STOP_BEFORE_PUBLICATION")

    monkeypatch.setattr(nested_wfo, "run_nested_wfo", nested)
    original = tmp_path / "original"
    destination = tmp_path / "destination"
    for out, extra in ((original, []), (destination, ["--reuse-from", str(original)])):
        monkeypatch.setattr(sys, "argv", [str(cli.__file__), "--strategy", "ma_crossover", "--timeframe", "1h",
            "--data-file", str(data), "--out-root", str(out), "--no-publish", "--n-outer", "1",
            "--train-months", "1", "--val-months", "1", "--test-months", "1",
            "--param-grid", '{"fast_period":[10],"slow_period":[20]}', *extra])
        with pytest.raises(RuntimeError, match="SYNTHETIC_ENGINEERING_STOP"):
            cli.main()
    assert len(calls) == 1 and [entry[0] for entry in captured] == [0, 1]
    assert captured[0][1] == captured[1][1]
    old_plan = json.loads((original / "ma_crossover/campaign_plan.json").read_text())
    new_plan = json.loads((destination / "ma_crossover/campaign_plan.json").read_text())
    assert old_plan["frozen_at"] != new_plan["frozen_at"]
    assert old_plan["semantic_fingerprint"] == new_plan["semantic_fingerprint"]
    imported = json.loads(next((destination / "cell_resume_cache").glob("*.json")).read_text())
    assert imported["lineage"]["packet"] == str(original)
    assert json.loads((destination / "cell_timings.jsonl").read_text())["cache_reason"] == "VERIFIED_COLD_IMPORT"
    for packet in (original, destination):
        finished = json.loads(next((packet / "ma_crossover/execution_runs").glob("*/finished.json")).read_text())
        assert finished["status"] == "INCOMPLETE" and finished["live_authorized"] is False
        assert "SYNTHETIC_ENGINEERING_STOP" in finished["error"]


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    from scripts.run_wfo_parallel import TRIAL_IDENTITY_CONTRACT
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract

    data = tmp_path / "synthetic.parquet"
    data.write_bytes(b"dispatch-test-only")
    binding = {"path": str(data), "sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
               "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "4h"}
    monkeypatch.setattr(orchestrator, "FROZEN_INPUTS", {("BTC/USDT", "4h"): {
        "path": str(data), "byte_sha256": binding["sha256"]}})
    packet = tmp_path / "source/trend_pullback__BTCUSDT__4h"
    (packet / "cell_resume_cache").mkdir(parents=True)
    body = {"spec": {"strategy_id": orchestrator.get_strategy_code_name("trend_pullback"),
            "symbol": "BTC/USDT", "timeframe": "4h", "param_grid": orchestrator.get_param_grid("trend_pullback")},
            "input_binding": binding, "source": campaign_integrity.capture_campaign_source(orchestrator.ROOT),
            "trial_identity_contract": TRIAL_IDENTITY_CONTRACT,
            "selection_contract": freeze_selection_contract(min_validation_trades=30, min_validation_sharpe=0),
            "execution": {"workers": 2, "environment": campaign_execution.execution_environment(),
                          "runtime_config": campaign_execution.simulator_runtime_config()}, "live_authorized": False}
    path = packet / "research_protocol.json"
    path.write_text(json.dumps({**body, "fingerprint": content_hash(body)}))
    frozen_source = copy.deepcopy(body["source"])
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source", lambda *a: copy.deepcopy(frozen_source))
    return packet, body


def test_orchestrator_forwards_exact_subject_and_records_terminal_error(tmp_path, monkeypatch, dispatch):
    packet, _ = dispatch
    seen = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: (seen.append(cmd) or SimpleNamespace(returncode=9)))
    result = orchestrator.run_single_strategy_cell("trend_pullback", "BTC/USDT", "4h", tmp_path / "new", 2, packet.parent)
    assert result["status"] == "ERROR"
    assert seen[0][seen[0].index("--reuse-from") + 1] == str(packet)
    terminal = json.loads((tmp_path / "new/trend_pullback__BTCUSDT__4h/execution_finished.json").read_text())
    assert terminal["worker_exit_code"] == 9 and terminal["live_authorized"] is False


@pytest.mark.parametrize("field", ["subject", "data", "source", "environment", "runtime", "workers", "hash", "missing"])
def test_orchestrator_rejects_bad_reuse_before_launch(tmp_path, monkeypatch, dispatch, field):
    packet, body = dispatch
    if field == "subject":
        body["spec"]["symbol"] = "ETH/USDT"
    elif field == "data":
        body["input_binding"]["sha256"] = "f" * 64
    elif field == "source":
        body["source"]["tree_fingerprint"] = "f" * 64
    elif field in ("environment", "runtime"):
        body["execution"]["environment" if field == "environment" else "runtime_config"] = {}
    elif field == "workers":
        body["execution"]["workers"] = 4
    source = packet.parent if field != "missing" else tmp_path / "absent"
    (packet / "research_protocol.json").write_text(json.dumps({**body, "fingerprint": "f" * 64 if field == "hash" else content_hash(body)}))
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(ValueError, match="reuse"):
        orchestrator.run_single_strategy_cell("trend_pullback", "BTC/USDT", "4h", tmp_path / "new", 2, source)


def test_phase_preflights_all_subjects_before_dispatch(tmp_path, monkeypatch, dispatch):
    packet, _ = dispatch
    monkeypatch.setattr(orchestrator, "run_single_strategy_cell", lambda *a, **kw: pytest.fail("no job may start"))
    with pytest.raises(ValueError, match="missing reuse"):
        orchestrator.run_single_asset_phase(tmp_path / "new", ["BTC/USDT", "ETH/USDT"], ["4h"],
                                            strategies=["trend_pullback"], reuse_from=packet.parent)


def test_global_worker_cap_is_respected(tmp_path, monkeypatch):
    observed = []
    from concurrent.futures import ThreadPoolExecutor
    def pool(*, max_workers):
        observed.append(max_workers)
        return ThreadPoolExecutor(max_workers=max_workers)
    monkeypatch.setattr("concurrent.futures.ThreadPoolExecutor", pool)
    monkeypatch.setattr(orchestrator, "run_single_strategy_cell", lambda *a: {"status": "FAIL"})
    monkeypatch.setattr(orchestrator, "_assert_coverage", lambda *a: None)
    orchestrator.run_single_asset_phase(tmp_path, ["BTC/USDT"], ["4h"], workers=8,
        strategies=["trend_pullback"], cell_workers=2, max_cell_workers=4)
    assert observed == [2]


def test_orchestrator_timeout_and_missing_summary_have_terminal_receipts(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(orchestrator, "FROZEN_INPUTS", {})
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired("synthetic", 1)
    monkeypatch.setattr("subprocess.run", timeout)
    out = tmp_path / "timeout"
    assert orchestrator.run_single_strategy_cell("trend_pullback", "BTC/USDT", "4h", out)["status"] == "TIMEOUT"
    record = json.loads((out / "trend_pullback__BTCUSDT__4h/execution_finished.json").read_text())
    assert record["status"] == "TIMEOUT" and record["worker_exit_code"] is None
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0))
    out = tmp_path / "missing"
    assert orchestrator.run_single_strategy_cell("trend_pullback", "BTC/USDT", "4h", out)["status"] == "ERROR"
    record = json.loads((out / "trend_pullback__BTCUSDT__4h/execution_finished.json").read_text())
    assert record["worker_exit_code"] == 0 and "verified summary" in record["error"]
