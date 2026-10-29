"""Preparation tests use only tmp_path synthetic parquet; never market inputs."""
from datetime import UTC, datetime, timedelta
import hashlib
import json

import polars as pl
import pytest

from trading_agent.backtest import thinning_preparation as lane


def series():
    stamps = [datetime(2023, 3, 24, 14, tzinfo=UTC) + timedelta(hours=k) for k in range(25198)]
    own = [100.0] * 220 + [110.0] * (25198-220)
    btc = [100.0] * 225 + [110.0] * (25198-225)
    return stamps, own, btc


def test_exact_calibration_and_evaluation_causality():
    stamps, own, btc = series()
    counts = lane.counts(own, btc, stamps)
    assert counts == {"numerator": 19, "denominator": 24, "missing": 0, "calibration_rows": 8544}
    assert lane.counts(own[:8760]+[0.001]*16438, btc[:8760]+[9999.0]*16438, stamps) == counts


def test_zero_denominator_is_not_probability_zero():
    stamps, own, btc = series()
    with pytest.raises(ValueError, match="zero calibration denominator"):
        lane.counts([100.0]*25198, btc, stamps)


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    stamps, own, btc = series()
    bindings = {}
    for pair in ("ETH/USDT", "BNB/USDT", "BTC/USDT"):
        filename = pair.replace("/", "_") + ".parquet"
        path = tmp_path / filename
        pl.DataFrame({"timestamp": stamps, "close": btc if pair == "BTC/USDT" else own}).write_parquet(path)
        bindings[pair] = {"path": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (tmp_path / "design.md").write_bytes(b"synthetic-design")
    monkeypatch.setattr(lane, "ROOT", tmp_path)
    monkeypatch.setattr(lane, "INPUTS", bindings)
    monkeypatch.setattr(lane, "DESIGN", "design.md")
    monkeypatch.setattr(lane, "DESIGN_SHA", hashlib.sha256(b"synthetic-design").hexdigest())
    monkeypatch.setattr(lane, "capture_campaign_source", lambda root: {"tree_fingerprint": "fixture-source"})
    monkeypatch.setattr(lane, "execution_environment", lambda: {"simulator_settings": {"INITIAL_CAPITAL": "10000"}})
    monkeypatch.setattr(lane, "native_contracts", lambda c: {p: {"params": v} for p,v in c.items()})
    return lane.prepare(tmp_path / "packet")


def test_bound_plan_is_not_permission(protocol):
    plan = lane.verify(protocol)
    assert len(plan["cells"]) == 36
    assert plan["calibration"]["ETH/USDT"]["numerator"] == 19
    assert plan["execution_entrypoint"] is None
    assert plan["authorization"]["state"] == "NOT_AUTHORIZED_TO_RUN"
    with pytest.raises(PermissionError, match="NOT_AUTHORIZED_TO_RUN"):
        lane.require_execution_authority(protocol)
    assert not (protocol.parent / "cells").exists()


@pytest.mark.parametrize("field", ["authorization", "budget", "cells", "metric", "native_contracts", "evaluation_window"])
def test_rehashed_field_tamper_cannot_admit(protocol, field):
    plan = json.loads(protocol.read_bytes())
    plan[field] = {"state": "APPROVED"} if field == "authorization" else []
    plan["fingerprint"] = lane.digest({k:v for k,v in plan.items() if k != "fingerprint"})
    protocol.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="binding mismatch"):
        lane.verify(protocol)
    assert not (protocol.parent / "cells").exists()


def test_calibration_receipt_tamper_rejected(protocol):
    path = protocol.parent / "calibration.json"
    value = json.loads(path.read_bytes())
    value["calibration"]["ETH/USDT"]["numerator"] += 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="calibration differs"):
        lane.verify(protocol)


def test_input_drift_rejected(protocol):
    plan = json.loads(protocol.read_bytes())
    path = protocol.parent.parent / plan["inputs"]["BTC/USDT"]["path"]
    path.write_bytes(path.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="input hash mismatch"):
        lane.verify(protocol)


def test_native_contracts_bind_counts_and_seed():
    count = {"numerator": 19, "denominator": 24, "missing": 0}
    native = lane.native_contracts({pair: count for pair in ("ETH/USDT", "BNB/USDT")})
    assert len(native) == 36
    body = native["ETH_USDT__1x__42"]["body"]
    assert body["strategy"]["parameters"]["numerator"] == 19
    assert body["strategy"]["parameters"]["denominator"] == 24
    assert body["strategy"]["parameters"]["seed"] == 42
