import importlib.util
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest


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
    with pytest.raises(RuntimeError, match="verified runtime binding"):
        module.main()
    assert "TRADING_FROZEN_RESEARCH_DATA" not in os.environ
