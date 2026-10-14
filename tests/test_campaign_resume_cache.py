"""Synthetic execution cache checks; never alpha or qualification evidence."""

import hashlib
import json
import multiprocessing
import time
from dataclasses import replace
from pathlib import Path

import pytest

from trading_agent.backtest import campaign_execution as module, campaign_integrity, tournament
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.reporting import fingerprint_payload
from trading_agent.strategies.canonical.candidates import build_default_registry


def specimen():
    return tournament.EvaluationCellSpec("rsi", "BTC/USDT", "1h",
                                         {"period": 14, "oversold": 30, "overbought": 70})


def produce(spec, commit, **kwargs):
    directory = Path(kwargs["out_root"]) / "synthetic_cell"
    directory.mkdir(parents=True)
    measurement = None
    if kwargs.get("measurement_start") is not None:
        measurement = (kwargs["measurement_start"], kwargs["measurement_end"])
    config = {"strategy": {"strategy_id": spec.strategy_id, "parameters": dict(spec.params)},
              "cost_model": {"commission_rate": spec.cost_scenario.commission,
                             "slippage_rate": spec.cost_scenario.slippage},
              "execution_timing": "decision_on_closed_bar_execute_next_bar_open",
              "data_policy": {"gap_policy": kwargs.get("gap_policy", "record")}}
    config["config_id"] = fingerprint_payload(config)
    report = {"metrics": {key: 1.0 for key in tournament._REQUIRED_METRICS}, "commit_sha": commit,
              "symbol": spec.symbol, "timeframe": spec.timeframe, "data_manifest_id": "d" * 64,
              "measurement_window": list(measurement) if measurement else None,
              "active_config": config, "trades": [{"synthetic_test_only": True}],
              "equity_curve": [["synthetic_open", 100], ["synthetic_close", 101]],
              "execution_health": {"unknown_orders": 0, "manual_interventions": 0, "unprotected_positions": 0}}
    (directory / "report.json").write_text(json.dumps(report))
    return tournament._artifact_from_report(spec, build_default_registry().describe(spec.strategy_id),
                                           directory, report, "d" * 64, measurement_window=measurement,
                                           simulation_window=(kwargs.get("simulation_start") or kwargs.get("start", 0), kwargs.get("end")),
                                           signal_delay_bars=kwargs.get("signal_delay_bars", 0))


@pytest.fixture
def context(tmp_path, monkeypatch):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-input-only")
    source = {"code_commit": "b" * 40, "tree_fingerprint": "c" * 64,
              "worktree_dirty": True, "dirty_diff_sha256": "e" * 64}
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source", lambda *args: dict(source))
    return {"source": source, "data": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                        "symbol": "BTC/USDT", "timeframe": "1h"}, "plan": "a" * 64}


def runner(packet, context, **kwargs):
    return module.CampaignCellRunner(packet, context,
                                     runner=lambda spec, **kw: produce(spec, context["source"]["code_commit"], **kw), **kwargs)


def test_same_packet_restart_exact_hit_and_defaults(tmp_path, context):
    packet = tmp_path / "packet"
    baseline = runner(packet, context)(specimen(), out_root=packet, end=10)
    restarted = module.CampaignCellRunner(packet, context, resume=True,
                                          runner=lambda *a, **kw: pytest.fail("exact hit must not rerun"))
    actual = restarted(specimen(), out_root=packet, start=0, end=10, measurement_start=None, fresh=True)
    assert actual == baseline and restarted.cache_hits == 1
    record = json.loads(next((packet / "cell_resume_cache").glob("*.json")).read_text())
    assert record["version"] == 2 and record["live_authorized"] is False


def test_cold_reuse_confined_and_source_packet_unchanged(tmp_path, context):
    old, new = tmp_path / "old", tmp_path / "new"
    baseline = runner(old, context)(specimen(), out_root=old, end=10)
    before = {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    imported = module.CampaignCellRunner(new, context, reuse_from=old,
                                         runner=lambda *a, **kw: pytest.fail("cold hit must not rerun"))
    actual = imported(specimen(), out_root=new, end=10)
    assert imported.cache_hits == 1 and Path(actual.report_path).is_relative_to(new)
    assert actual.metrics == baseline.metrics and actual.execution_health == baseline.execution_health
    assert Path(actual.report_path).read_bytes() == Path(baseline.report_path).read_bytes()
    assert before == {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    # A second process/runner can read the imported certificate as its own confined cache.
    restarted = module.CampaignCellRunner(new, context, resume=True, runner=lambda *a, **kw: pytest.fail("no rerun"))
    assert restarted(specimen(), out_root=new, end=10).report_path == actual.report_path


@pytest.mark.parametrize("mutation", ["partial", "legacy", "wrong_window", "wrong_commit", "report_hash",
                                      "raw_window", "raw_metrics", "config", "escape"])
def test_corrupt_cache_fails_without_rerun_or_overwrite(tmp_path, context, mutation):
    packet = tmp_path / "packet"
    artifact = runner(packet, context)(specimen(), out_root=packet, end=10)
    cache_path = next((packet / "cell_resume_cache").glob("*.json"))
    record = json.loads(cache_path.read_bytes())
    report_path = Path(artifact.report_path)
    if mutation == "partial":
        cache_path.write_bytes(b'{"version":')
    else:
        if mutation == "legacy":
            record["version"] = 1
        elif mutation in {"wrong_window", "wrong_commit", "escape"}:
            change = ({"simulation_window": (0, 11)} if mutation == "wrong_window" else
                      {"commit_sha": "f" * 40} if mutation == "wrong_commit" else
                      {"report_path": str(tmp_path / "outside_report.json")})
            forged = replace(artifact, **change)
            record["artifact"] = forged.to_dict()
            record["artifact_hash"] = content_hash(forged.to_dict())
        else:
            report = json.loads(report_path.read_bytes())
            if mutation == "raw_window":
                report["measurement_window"] = [1, 10]
            elif mutation == "raw_metrics":
                report["metrics"]["sharpe"] += 100
            elif mutation == "config":
                report["active_config"]["cost_model"]["commission_rate"] = 0
                report["active_config"]["config_id"] = fingerprint_payload({key: value for key, value in report["active_config"].items() if key != "config_id"})
            else:
                report["trades"].append({"tampered": True})
            report_path.write_text(json.dumps(report))
            if mutation != "report_hash":
                record["report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        cache_path.write_text(json.dumps(record))
    before = cache_path.read_bytes(), report_path.read_bytes()
    restarted = module.CampaignCellRunner(packet, context, resume=True,
                                          runner=lambda *a, **kw: pytest.fail("invalid cache must not rerun"))
    with pytest.raises((ValueError, KeyError)):
        restarted(specimen(), out_root=packet, end=10)
    assert (cache_path.read_bytes(), report_path.read_bytes()) == before
    assert restarted.cache_hits == 0


@pytest.mark.parametrize("field", ["source", "data", "environment"])
def test_live_bindings_rechecked_before_hit(tmp_path, context, monkeypatch, field):
    packet = tmp_path / "packet"
    runner(packet, context)(specimen(), out_root=packet, end=10)
    restarted = module.CampaignCellRunner(packet, context, resume=True, runner=lambda *a, **kw: pytest.fail("no rerun"))
    if field == "source":
        monkeypatch.setattr(campaign_integrity, "capture_campaign_source", lambda *a: {})
    elif field == "data":
        Path(context["data"]["path"]).write_bytes(b"changed")
    else:
        monkeypatch.setattr(module, "execution_environment", lambda: {"changed": True})
    with pytest.raises(ValueError, match="changed"):
        restarted(specimen(), out_root=packet, end=10)


@pytest.mark.parametrize("field", ["window", "cost", "params", "delay", "stage", "plan"])
def test_different_identity_runs_new_cell_never_reuses(tmp_path, context, field):
    old, new = tmp_path / "old", tmp_path / "new"
    runner(old, context)(specimen(), out_root=old, end=10)
    spec, kwargs = specimen(), {"out_root": new, "end": 10}
    if field == "window":
        kwargs["end"] = 11
    elif field == "cost":
        spec = replace(spec, cost_scenario=tournament.SCENARIO_DOUBLE)
    elif field == "params":
        spec = replace(spec, params={**dict(spec.params), "period": 21})
    elif field == "delay":
        kwargs["signal_delay_bars"] = 1
    elif field == "stage":
        kwargs["_cache_stage"] = "OUTER_OOS"
    else:
        context = {**context, "plan": "different-preregistered-plan"}
    actual = runner(new, context, reuse_from=old)
    assert actual(spec, **kwargs).status == "COMPLETED" and actual.cache_hits == 0


def test_failed_attempts_are_immutable_and_not_cacheable(tmp_path, context):
    def fail(spec, **kwargs):
        return tournament._failed_artifact(spec, None, "synthetic_failure")
    actual = module.CampaignCellRunner(tmp_path / "packet", context, resume=True, runner=fail)
    actual(specimen(), end=10)
    actual(specimen(), end=10)
    assert len(list((actual.root / "cell_failures").glob("*.json"))) == 2
    assert len(list((actual.root / "cell_attempts").glob("*/execution_started.json"))) == 2
    assert not list((actual.root / "cell_resume_cache").glob("*.json"))


def interrupted_writer(packet, context, ready):
    original = module._durable_json

    def interrupt(path, payload):
        if path.parent.name == "cell_resume_cache":
            (path.parent / ".incomplete.pending").write_bytes(b'{"version":')
            Path(ready).touch()
            time.sleep(30)
        else:
            original(path, payload)

    module._durable_json = interrupt
    runner(Path(packet), context)(specimen(), out_root=Path(packet), end=10)


def test_kill_before_publication_restart_preserves_previous_attempt(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-only")
    context = {"source": campaign_integrity.capture_campaign_source(module.ROOT),
               "data": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
               "plan": "synthetic-kill-restart-only"}
    packet, ready = tmp_path / "packet", tmp_path / "ready"
    process = multiprocessing.get_context("spawn").Process(target=interrupted_writer,
                                                          args=(str(packet), context, str(ready)))
    process.start()
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), f"writer not reached, exit={process.exitcode}"
        old_reports = {str(file): file.read_bytes() for file in packet.rglob("report.json")}
    finally:
        if process.is_alive():
            process.terminate()  # Only this test's owned disposable child.
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    assert not process.is_alive()
    restarted = runner(packet, context, resume=True)
    result = restarted(specimen(), out_root=packet, end=10)
    assert result.status == "COMPLETED" and restarted.cache_hits == 0
    assert all(Path(file).read_bytes() == raw for file, raw in old_reports.items())
    assert len(list((packet / "cell_attempts").glob("*/execution_started.json"))) == 2
    assert len(list((packet / "cell_resume_cache").glob("*.json"))) == 1


def test_packet_overlap_rejected(tmp_path, context):
    with pytest.raises(ValueError, match="disjoint"):
        runner(tmp_path / "source" / "nested", context, reuse_from=tmp_path / "source")


def completed_writer(packet, context, ready):
    runner(Path(packet), context)(specimen(), out_root=Path(packet), end=10)
    Path(ready).touch()
    time.sleep(30)


def test_kill_after_publication_restarts_without_rerunning_cell(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-only")
    context = {"source": campaign_integrity.capture_campaign_source(module.ROOT),
               "data": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
               "plan": "synthetic-after-publication-only"}
    packet, ready = tmp_path / "packet", tmp_path / "ready"
    process = multiprocessing.get_context("spawn").Process(target=completed_writer,
                                                          args=(str(packet), context, str(ready)))
    process.start()
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), f"publication not reached, exit={process.exitcode}"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    assert not process.is_alive()
    resumed = module.CampaignCellRunner(packet, context, resume=True,
                                        runner=lambda *a, **kw: pytest.fail("published cell must not rerun"))
    assert resumed(specimen(), out_root=packet, end=10).status == "COMPLETED"
    assert resumed.cache_hits == 1
    assert len(list((packet / "cell_attempts").glob("*/execution_started.json"))) == 1


def competing_writer(packet, context, start, calls):
    def measured(spec, **kwargs):
        with Path(calls).open("ab") as stream:
            stream.write(b"one\n")
        time.sleep(0.5)
        return produce(spec, context["source"]["code_commit"], **kwargs)
    start.wait(10)
    module.CampaignCellRunner(packet, context, resume=True, runner=measured)(specimen(), end=10)


def test_two_processes_serialize_exact_cell_without_duplicate_execution(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-only")
    context = {"source": campaign_integrity.capture_campaign_source(module.ROOT),
               "data": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
               "plan": "synthetic-concurrent-only"}
    packet, calls = tmp_path / "packet", tmp_path / "calls"
    spawn = multiprocessing.get_context("spawn")
    start = spawn.Event()
    processes = [spawn.Process(target=competing_writer, args=(str(packet), context, start, str(calls))) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    try:
        for process in processes:
            process.join(20)
        assert all(p.exitcode == 0 for p in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
    assert calls.read_bytes() == b"one\n"
    assert len(list(packet.glob("cell_attempts/*/execution_started.json"))) == 1
    assert len(list(packet.glob("cell_resume_cache/*.json"))) == 1
    records = [json.loads(line) for line in (packet / "cell_timings.jsonl").read_text().splitlines()]
    assert len(records) == 2 and sum(row["cache_hit"] for row in records) == 1


def interrupted_import(origin, destination, context, ready):
    original = module.CampaignCellRunner._publish_cache
    def pause(self, path, identity, artifact, raw, lineage=None):
        if lineage:
            Path(ready).touch()
            time.sleep(30)
        original(self, path, identity, artifact, raw, lineage)
    module.CampaignCellRunner._publish_cache = pause
    module.CampaignCellRunner(destination, context, reuse_from=origin)(specimen(), end=10)


def test_kill_during_cold_copy_restart_keeps_orphan_and_imports_cleanly(tmp_path):
    path = tmp_path / "input"
    path.write_bytes(b"synthetic-only")
    context = {"source": campaign_integrity.capture_campaign_source(module.ROOT),
               "data": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
               "plan": "synthetic-import-kill-only"}
    old, new, ready = tmp_path / "old", tmp_path / "new", tmp_path / "ready"
    runner(old, context)(specimen(), end=10)
    original = {str(p): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    process = multiprocessing.get_context("spawn").Process(target=interrupted_import,
                    args=(str(old), str(new), context, str(ready)))
    process.start()
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    orphan = {str(p): p.read_bytes() for p in new.rglob("report.json")}
    assert len(orphan) == 1 and not list(new.glob("cell_resume_cache/*.json"))
    recovered = module.CampaignCellRunner(new, context, reuse_from=old,
                                         runner=lambda *a, **kw: pytest.fail("do not rerun imported cell"))
    assert recovered(specimen(), end=10).status == "COMPLETED" and recovered.cache_hits == 1
    assert all(Path(p).read_bytes() == raw for p, raw in orphan.items())
    assert len(list(new.rglob("report.json"))) == 2
    assert original == {str(p): p.read_bytes() for p in old.rglob("*") if p.is_file()}


def test_non_secret_simulator_environment_is_part_of_cache_identity(tmp_path, context, monkeypatch):
    packet = tmp_path / "packet"
    runner(packet, context)(specimen(), end=10)
    monkeypatch.setenv("STOP_LOSS_PCT", "0.09")
    changed = runner(packet, context, resume=True)
    changed(specimen(), end=10)
    assert changed.cache_hits == 0 and len(list(packet.glob("cell_resume_cache/*.json"))) == 2


def test_output_symlink_escape_is_rejected_before_writes(tmp_path, context):
    outside, packet = tmp_path / "outside", tmp_path / "packet"
    outside.mkdir()
    packet.mkdir()
    (packet / "cell_locks").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="namespace escapes"):
        runner(packet, context)(specimen(), end=10)
    assert list(outside.iterdir()) == []
