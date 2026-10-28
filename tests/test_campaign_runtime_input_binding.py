import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest


def test_interrupted_fixture_restores_the_callers_binding(tmp_path):
    """Exercise the actual leaking test in isolation with a prior binding."""
    script = '''
import json, os, pytest
key = "TRADING_FROZEN_RESEARCH_DATA"
prior = json.dumps({"sentinel": "caller-owned-binding"})
os.environ[key] = prior
rc = pytest.main(["-o", "addopts=", "-q",
    "tests/test_validation_timeout_and_ledger_faults.py::test_f2b_interrupted_attempt_is_refused_by_the_aggregation"])
assert rc == 0, rc
assert os.environ.get(key) == prior, "fault fixture leaked or erased the caller binding"
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(), capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


@pytest.mark.parametrize("count", [1, 2])
def test_entrypoint_passes_frozen_input_and_fold_limit(monkeypatch, tmp_path, count):
    from trading_agent.backtest import nested_wfo
    from trading_agent.data.storage import load_research_ohlcv

    path = Path(__file__).resolve().parents[1] / "scripts/run_real_wfo_campaign.py"
    module_spec = importlib.util.spec_from_file_location("bound_campaign", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    frame = pl.DataFrame(
        {
            "timestamp": [
                datetime(2023, 1, 1) + timedelta(hours=i) for i in range(5000)
            ],
            "open": [100.0] * 5000,
            "high": [101.0] * 5000,
            "low": [99.0] * 5000,
            "close": [100.0] * 5000,
            "volume": [10.0] * 5000,
        }
    )
    data = tmp_path / "explicit.parquet"
    frame.write_parquet(data)
    output = tmp_path / "campaign"
    monkeypatch.setattr(
        "sys.argv",
        [
            str(path),
            "--data-file",
            str(data),
            "--out-root",
            str(output),
            "--train-months",
            "1",
            "--val-months",
            "1",
            "--test-months",
            "1",
            "--n-outer",
            str(count),
            "--no-publish",
        ],
    )

    def inspect_runtime(spec, *, out_root, max_outer_folds, native_gate_contract, cell_runner,
                        selection_contract):
        assert selection_contract is None
        assert max_outer_folds == count
        assert load_research_ohlcv("binance", spec.symbol, spec.timeframe).equals(frame)
        plan = json.loads((out_root / "enhanced_ma/campaign_plan.json").read_text())
        assert plan["fold_count"] == count
        assert plan["native_gate_contract"] == native_gate_contract
        assert plan["folds"][0]["purge_bars"] > 0
        raise RuntimeError("verified runtime binding")

    monkeypatch.setattr(nested_wfo, "run_nested_wfo", inspect_runtime)
    before = os.environ.get("TRADING_FROZEN_RESEARCH_DATA")
    with pytest.raises(RuntimeError, match="verified runtime binding"):
        module.main()
    assert os.environ.get("TRADING_FROZEN_RESEARCH_DATA") == before
