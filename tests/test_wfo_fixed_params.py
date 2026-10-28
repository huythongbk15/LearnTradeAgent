"""Fixed policy CLI contracts; all input files are synthetic temporary files."""

import hashlib
import json
import math
import sys
from datetime import datetime, timedelta

import polars as pl
import pytest

from scripts import run_wfo_parallel as worker


DEFAULTS = dict(rsi_period=14, rsi_oversold=30, rsi_overbought=70,
                ma_fast=20, ma_slow=80, adx_period=14, adx_threshold=20.0, ma_type="sma")


def test_fixed_defaults_are_not_the_legacy_grid():
    fixed = worker.fixed_parameter_grid("rsi_adx_regime_filter", "{}")
    assert fixed == {k: [v] for k, v in DEFAULTS.items()}
    assert worker.get_param_grid_for_strategy("rsi_adx_regime_filter")["adx_threshold"] == [15]
    assert worker.get_param_grid_for_strategy("rsi_adx_regime_filter")["ma_slow"] == [50]


@pytest.mark.parametrize("payload", [
    '[]', 'null', '1', '{bad', '{"ma_fast":[20]}', '{"ma_fast":[20,30]}',
    '{"ma_fast":true}', '{"ma_fast":20.5}', '{"ma_fast":"20"}',
    '{"adx_threshold":NaN}', '{"adx_threshold":Infinity}', '{"adx_threshold":1e999}',
    '{"ma_type":"invalid"}', '{"unknown":1}', '{"ma_fast":0}',
    '{"ma_fast":100,"ma_slow":80}', '{"ma_fast":20,"ma_fast":21}',
    '{"ma_type":null}', '{"ma_type":{}}',
])
def test_bad_fixed_input_fails_before_output_or_loader(tmp_path, monkeypatch, payload):
    out = tmp_path / "never_created"
    monkeypatch.setattr(worker, "prepare_parallel_protocol", lambda *a, **k: pytest.fail("loaded data"))
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--strategy", "rsi_adx_regime_filter",
                                    "--fixed-params", payload, "--preflight-only", "--out", str(out)])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2
    assert not out.exists()


@pytest.mark.parametrize("argv", [[], ["--validation-worker", "never_read"]])
def test_fixed_input_rejects_qualification_and_worker_override(tmp_path, monkeypatch, argv):
    out = tmp_path / "absent"
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--fixed-params", "{}",
                                    "--out", str(out), *argv])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2
    assert not out.exists()


def test_scoped_resolution_remains_required():
    with pytest.raises((ValueError, KeyError)):
        worker.fixed_parameter_grid("di_adx_state_edge", "{}")
    assert worker.fixed_parameter_grid("di_adx_state_edge", "{}",
        "bnb_di_adx_stateedge_prereg_20261020_01") == {"adx_period": [14], "adx_threshold": [25.0]}


def test_cache_identity_distinguishes_fixed_params(tmp_path):
    from trading_agent.backtest.campaign_execution import CampaignCellRunner
    from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE

    runner = CampaignCellRunner(tmp_path, {})
    def identity(threshold):
        params = {**DEFAULTS, "adx_threshold": threshold}
        cell = EvaluationCellSpec(strategy_id="rsi_adx_regime_filter", symbol="BTC/USDT",
                                  timeframe="1h", params=params, cost_scenario=SCENARIO_BASE)
        return runner._identity(cell, {"start": 0, "end": 300}, "INNER_VALIDATION")
    assert identity(20) != identity(21)
    assert identity(20) == identity(20)


def _bind(tmp_path, monkeypatch):
    # Preserve the production spec/runner path, isolate only registry location.
    original_spec = worker.WFOSpec

    def isolated_spec(*args, **kwargs):
        kwargs["registry_path"] = str(tmp_path / "synthetic_registry.sqlite3")
        return original_spec(*args, **kwargs)

    monkeypatch.setattr(worker, "WFOSpec", isolated_spec)
    # Daily cadence makes a single full native fold small; not an economic test.
    prices = [100.0 if i < 230 else 100 + 4 * math.sin(i / 4) for i in range(500)]
    frame = pl.DataFrame({
        "timestamp": [datetime(2020, 1, 1) + timedelta(days=i) for i in range(500)],
        "open": prices, "close": prices, "high": [p + .2 for p in prices],
        "low": [p - .2 for p in prices], "volume": [1000.0] * 500,
    })
    path = tmp_path / "synthetic.parquet"
    frame.write_parquet(path)
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps({
        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exchange": "binance", "symbol": "BTC/USDT", "timeframe": "1d",
    }))


def _cli(monkeypatch, out, payload, preflight=True):
    argv = ["run_wfo_parallel.py", "--strategy", "rsi_adx_regime_filter", "--symbol", "BTC/USDT",
            "--timeframe", "1d", "--workers", "1", "--cost", "1x", "--fixed-params", payload,
            "--train-months", "1", "--val-months", "1", "--test-months", "1", "--step-months", "1",
            "--cell-timeout", "90", "--total-timeout", "180", "--max-rss-mb", "2048",
            "--max-retries", "0", "--validation-only", "--no-real-sensitivity",
            "--synthetic-test-only", "--out", str(out)]
    if preflight:
        argv.append("--preflight-only")
    monkeypatch.setattr(sys, "argv", argv)
    worker.main()
    return json.loads((out / "research_protocol.json").read_text())


def test_cli_protocol_freezes_values_and_distinguishes_changed_params(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    first = _cli(monkeypatch, tmp_path / "first", "{}")
    second = _cli(monkeypatch, tmp_path / "second", '{"adx_threshold":21}')
    assert first["spec"]["param_grid"] == {k: [v] for k, v in DEFAULTS.items()}
    assert len(first["folds"]) == 1
    assert len(first["expected_inner_cells"]) == 1
    assert first["fingerprint"] != second["fingerprint"]
    assert first["expected_inner_cells"][0] != second["expected_inner_cells"][0]
    # A changed value cannot be substituted into the already-frozen protocol.
    from trading_agent.backtest.nested_wfo import NestedFold, WFOSpec
    altered = json.loads(json.dumps(first))
    altered["spec"]["param_grid"]["adx_threshold"] = [21]
    declared = dict(altered["spec"])
    declared["cost_scenarios"] = tuple(worker.CostScenario(**c) for c in declared["cost_scenarios"])
    with pytest.raises(ValueError):
        worker._verify_validation_protocol(WFOSpec(**declared), altered,
                                          [NestedFold(**f) for f in altered["folds"]])


@pytest.mark.slow
def test_fixed_defaults_reach_native_worker_and_terminal_summary(tmp_path, monkeypatch):
    _bind(tmp_path, monkeypatch)
    out = tmp_path / "run"
    protocol = _cli(monkeypatch, out, "{}", preflight=False)
    assert protocol["spec"]["param_grid"] == {k: [v] for k, v in DEFAULTS.items()}
    assert json.loads((out / "execution_finished.json").read_text())["status"] == "COMPLETED"
    assert json.loads((out / "exact_coverage.json").read_text())["status"] == "PASS"
    summary = json.loads((out / "parallel_canonical_summary.json").read_text())
    assert summary["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    reports = list(out.rglob("report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["active_config"]["strategy"]["parameters"] == DEFAULTS
    assert report["active_config"]["execution_contract"]["body"]["strategy"]["parameters"] == DEFAULTS
