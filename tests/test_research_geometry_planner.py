"""Metadata-only planning and retired-entrypoint fail-closed regressions."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from scripts import plan_promotable_campaign as planner
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.native_gate_contract import freeze_native_gate_contract


def fixture_protocol():
    times = [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=4 * i) for i in range(9000)]
    protocol = {
        "spec": {"strategy_id": "ma_vol_target", "symbol": "BTC/USDT", "timeframe": "4h",
                 "param_grid": {"fast_period": [20, 30], "slow_period": [60, 80]},
                 "cost_scenarios": [{"name": "1x"}], "purge_bars": 0, "embargo_bars": 0},
        "input_bars": len(times), "input_first_timestamp": str(times[0]), "input_last_timestamp": str(times[-1]),
        "native_gate_contract": freeze_native_gate_contract(SimpleNamespace(effective_min_oos_trades=30)),
    }
    protocol["fingerprint"] = content_hash(protocol)
    manifest = {"window": {"start_utc": "2023-01-01T00:00:00+00:00", "end_utc": "2023-07-01T00:00:00+00:00"}}
    return protocol, times, manifest


def test_current_binomial_threshold_and_geometry_not_qualification():
    protocol, times, manifest = fixture_protocol()
    result = planner.geometry_plan(protocol, times, manifest)
    assert result["cost_clearing_p_max"] == .1
    assert planner.required_k(2, .1) is None
    assert planner.required_k(4, .1) == 4
    assert result["input_extends_into_or_past_holdout"]
    assert result["pre_holdout_bars"] == sum(t < datetime(2023, 1, 1, tzinfo=UTC) for t in times)
    assert result["purge"] >= 80 and result["embargo"] >= 80
    assert not result["execution_authorized"] and not result["live_authorized"]
    for option in result["plans"]:
        assert option["planned_inner_cells"] == 4 * option["folds"]
        assert option["qualification"] == "NOT_EVALUATED"
        assert option["validation_trade_adequacy"] == "UNKNOWN"
        assert option["eta"] is None


def test_protocol_tamper_and_timestamp_drift_rejected():
    protocol, times, manifest = fixture_protocol()
    protocol["input_bars"] -= 1
    with pytest.raises(ValueError, match="fingerprint"):
        planner.geometry_plan(protocol, times, manifest)
    protocol, times, manifest = fixture_protocol()
    with pytest.raises(ValueError, match="coverage"):
        planner.geometry_plan(protocol, times[:-1], manifest)
    times[1] = times[0]
    with pytest.raises(ValueError, match="increasing"):
        planner.geometry_plan(protocol, times, manifest)


def test_cli_reads_only_timestamps_and_writes_no_packet(tmp_path, monkeypatch, capsys):
    protocol, times, manifest = fixture_protocol()
    data = tmp_path / "data"
    data.mkdir()
    parquet = data / "fixture.parquet"
    pl.DataFrame({"timestamp": times}).write_parquet(parquet)
    protocol["input_binding"] = {"path": str(parquet), "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest()}
    protocol["fingerprint"] = content_hash({k: v for k, v in protocol.items() if k != "fingerprint"})
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol))
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(planner, "ROOT", tmp_path)
    monkeypatch.setattr(planner, "load_manifest", lambda path: manifest)
    original = pl.read_parquet
    columns = []

    def metadata_only(path, **kwargs):
        assert kwargs["columns"] == ["timestamp"]
        columns.append(kwargs["columns"])
        return original(path, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", metadata_only)
    planner.main(["--protocol", str(path)])
    assert json.loads(capsys.readouterr().out)["status"] == "DRAFT_GEOMETRY_ONLY_NOT_APPROVED"
    assert columns == [["timestamp"]]
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("flags", [[], ["--dry-run"]])
def test_retired_policy_cli_stops_before_data_or_registry(monkeypatch, flags):
    from scripts import build_campaign_policy as legacy

    def forbidden(*args, **kwargs):
        pytest.fail("retired path reached old planner")

    monkeypatch.setattr(legacy, "fold_plan", forbidden)
    monkeypatch.setattr("sys.argv", ["build_campaign_policy.py", *flags])
    with pytest.raises(SystemExit, match="RETIRED_ENTRYPOINT"):
        legacy.main()
