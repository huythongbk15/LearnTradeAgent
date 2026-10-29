"""Synthetic input fixtures for the explicitly separate development binding lane."""
from dataclasses import replace
import hashlib
import json

import polars as pl
import pytest

from trading_agent.backtest import fill_cycle_development as development
from trading_agent.backtest.fill_cycle_synthetic import synthetic_frame
from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy, fill_policy_fingerprint_plus


@pytest.fixture
def packet(tmp_path, monkeypatch):
    frame = synthetic_frame().with_columns(pl.lit(True).alias("is_closed"))
    inputs = {}
    for symbol, name in [("ETH/USDT", "eth.parquet"), ("BTC/USDT", "btc.parquet")]:
        path = tmp_path / name
        frame.write_parquet(path)
        inputs[symbol] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setattr(development, "ROOT", tmp_path)
    monkeypatch.setattr(development, "INPUTS", inputs)
    monkeypatch.setattr(development, "WINDOW", {"start": 240, "end": 280})
    monkeypatch.setattr(development, "capture_campaign_source", lambda root: {
        "tree_fingerprint": "synthetic-source", "code_commit": "synthetic-fixture",
    })
    monkeypatch.delenv("TRADING_FROZEN_RESEARCH_DATA", raising=False)
    out = tmp_path / "packet"
    plan = development.prepare_packet(out, {"user_instruction": "synthetic fixture, not market approval"})
    return out / "protocol.json", plan


def test_registered_development_input_and_plan_match(packet):
    path, plan = packet
    assert development.verify_plan(path) == plan
    frame, request = development.load_input(path, "PLUS")
    assert "btc_close" in frame.columns
    assert request.protocol_fingerprint == plan["fingerprint"]
    assert request.frame_sha256 == development.frame_digest(frame)


@pytest.mark.parametrize("field,value", [
    ("budget", {"cell_timeout_seconds": 601}),
    ("window", {"start": 0, "end": 280}),
    ("arms", ["A0"]), ("cost", "2x"), ("live_authorized", True),
])
def test_changed_scope_is_rejected_even_after_rehash(packet, field, value):
    path, plan = packet
    plan[field] = value
    plan["fingerprint"] = development.digest({k: v for k, v in plan.items() if k != "fingerprint"})
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match=field):
        development.verify_plan(path)


def test_source_drift_is_refused(packet, monkeypatch):
    path, _ = packet
    monkeypatch.setattr(development, "capture_campaign_source", lambda root: {"tree_fingerprint": "changed"})
    with pytest.raises(ValueError, match="source mismatch"):
        development.verify_plan(path)


def test_input_tamper_is_refused_before_decode(packet, monkeypatch):
    path, _ = packet
    (development.ROOT / "eth.parquet").write_bytes(b"changed")
    monkeypatch.setattr(pl, "read_parquet", lambda *a, **k: pytest.fail("must not decode tampered data"))
    with pytest.raises(ValueError, match="input hash mismatch"):
        development.load_input(path, "PLUS")


def arguments(path, plan, arm="PLUS"):
    cell = path.parent / "cells" / arm
    return {"symbol": "ETH/USDT", "timeframe": "1h", "strategy": Own24FillContextStrategy({"arm": arm}),
        "manifest": "development:" + plan["fingerprint"],
        "contract_id": plan["native_contracts"][arm]["contract_id"],
        "state_dir": cell / "execution", "report_path": cell / "report.json", "fresh": True}


def test_simulator_input_rejects_changed_or_forged_frame(packet):
    path, plan = packet
    frame, request = development.load_input(path, "PLUS")
    changed = frame.with_columns((pl.col("close") + 1).alias("close"))
    with pytest.raises(ValueError, match="binding mismatch"):
        development.verify_simulator_input(request, changed, **arguments(path, plan))
    forged = replace(request, frame_sha256=development.frame_digest(changed))
    with pytest.raises(ValueError, match="registered input"):
        development.verify_simulator_input(forged, changed, **arguments(path, plan))


def test_simulator_cannot_write_outside_registered_cell(packet):
    path, plan = packet
    frame, request = development.load_input(path, "PLUS")
    kwargs = arguments(path, plan)
    kwargs["state_dir"] = path.parent / "outside"
    with pytest.raises(ValueError, match="fresh and isolated"):
        development.verify_simulator_input(request, frame, **kwargs)
    assert not kwargs["state_dir"].exists()


def test_real_simulator_with_bound_fixture_retains_fill_and_policy_identity(packet, monkeypatch):
    from scripts.full_system_backtest import FullSystemSimulator

    path, plan = packet
    frame, request = development.load_input(path, "PLUS")
    kwargs = arguments(path, plan)
    model = plan["native_contracts"]["PLUS"]["body"]["cost_model"]
    simulator = FullSystemSimulator(
        fresh=True, symbol="ETH/USDT", timeframe="1h",
        state_dir=kwargs["state_dir"], report_path=kwargs["report_path"],
        data_manifest_id=kwargs["manifest"], source_df_override=frame,
        execution_cycle_strategy=kwargs["strategy"], execution_cycle_development_input=request,
        expected_execution_contract_id=kwargs["contract_id"],
        commission=model["commission_rate"], slippage=model["slippage_rate"], gap_policy="reject",
    )
    sources = []
    execute = simulator.engine.execute_strategy

    def record_source(runtime, data, observation, **kwargs):
        sources.append(observation.source)
        return execute(runtime, data, observation, **kwargs)

    monkeypatch.setattr(simulator.engine, "execute_strategy", record_source)
    try:
        assert simulator.strategy_runtime.artifact_metadata["policy"] == fill_policy_fingerprint_plus()
        report = simulator.run(start=240, end=280)
        assert report["total_trades"] == 1
        assert sources and set(sources) == {"development_fill_cycle"}
        assert report["open_positions"] == 0
        assert report["active_config"]["execution_contract"] == plan["native_contracts"]["PLUS"]
    finally:
        simulator.engine.store.close()


def test_packet_cannot_be_reprepared(packet):
    path, _ = packet
    with pytest.raises(ValueError, match="new packet"):
        development.prepare_packet(path.parent, {"user_instruction": "again"})


@pytest.mark.parametrize("worker_status,expected", [
    ("TIMEOUT", "TIMEOUT"), ("COMPLETED", "INCOMPLETE_MISSING_WORKER_RECEIPT"),
])
def test_missing_or_failed_worker_never_produces_complete_summary(packet, monkeypatch, worker_status, expected):
    path, _ = packet
    monkeypatch.setattr(development, "_supervise_validation", lambda *a, **k: {
        "status": worker_status, "worker_exit_code": -9 if worker_status == "TIMEOUT" else 0,
    })
    assert development.run_packet(path) == 2
    summary = json.loads((path.parent / "summary.json").read_bytes())
    assert summary["status"] == "INCOMPLETE"
    assert summary["outcomes"][0]["status"] == expected
    assert len(summary["outcomes"]) == 1
    assert summary["qualified"] is False
    assert not (path.parent / "cells" / "PLUS").exists()
    with pytest.raises(FileExistsError):
        development.run_packet(path)


def test_bad_binding_blocks_real_constructor_before_state_creation(packet):
    from scripts.full_system_backtest import FullSystemSimulator

    path, plan = packet
    frame, request = development.load_input(path, "PLUS")
    kwargs = arguments(path, plan)
    with pytest.raises(ValueError, match="binding mismatch"):
        FullSystemSimulator(
            fresh=True, symbol="ETH/USDT", timeframe="1h",
            state_dir=kwargs["state_dir"], report_path=kwargs["report_path"],
            data_manifest_id="development:wrong", source_df_override=frame,
            execution_cycle_strategy=kwargs["strategy"], execution_cycle_development_input=request,
            expected_execution_contract_id=kwargs["contract_id"],
        )
    assert not kwargs["state_dir"].exists()


def test_development_budget_is_explicit_and_fixed():
    assert development.BUDGET == {"cell_timeout_seconds": 600, "total_timeout_seconds": 1000,
        "max_rss_mb": 2048, "workers": 1, "max_retries": 0}
