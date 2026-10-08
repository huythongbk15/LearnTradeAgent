import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts/strategy_research/run_campaign.py"
    spec = importlib.util.spec_from_file_location("existing_campaign_manifest_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_is_forwarded_without_global_environment_mutation(tmp_path, monkeypatch):
    module = load_script()
    source = tmp_path / "input.parquet"
    source.write_bytes(b"test-bound-input")
    module.FROZEN_INPUTS = {("BTC/USDT", "1h"): {
        "path": str(source), "byte_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }}
    monkeypatch.delenv("TRADING_FROZEN_RESEARCH_DATA", raising=False)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        return SimpleNamespace(returncode=1, stderr="intentional test error")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = module.run_single_strategy_cell("trend_pullback", "BTC/USDT", "1h", tmp_path)
    assert result["status"] == "ERROR"
    assert "--run-holdout" not in seen["cmd"]
    assert seen["cmd"][0] == module.sys.executable
    assert seen["timeout"] == module.JOB_TIMEOUT_SECONDS
    binding = json.loads(seen["env"]["TRADING_FROZEN_RESEARCH_DATA"])
    assert binding["symbol"] == "BTC/USDT"
    assert binding["sha256"] == module.FROZEN_INPUTS[("BTC/USDT", "1h")]["byte_sha256"]
    assert "TRADING_FROZEN_RESEARCH_DATA" not in module.os.environ
    assert module.run_single_strategy_cell("funding_carry", "BTC/USDT", "1h", tmp_path)["status"] == "BLOCKED_DATA"
    assert module.run_single_strategy_cell("trend_pullback", "ETH/USDT", "1h", tmp_path)["status"] == "BLOCKED_DATA"


def test_coverage_maps_catalog_names_without_changing_verdict(tmp_path, monkeypatch):
    module = load_script()
    seen = {}

    def verify(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(ok=True, summary=lambda: "test coverage")

    monkeypatch.setattr("trading_agent.backtest.campaign_integrity.verify_campaign_coverage", verify)
    results = [{"strategy_id": "vol_target", "status": "TIMEOUT"}]
    module._assert_coverage([("vol_target", "BTC/USDT", "4h")], results, tmp_path)
    assert seen["requested_strategies"] == {"ma_vol_target"}
    assert seen["results"] == [{"strategy_id": "ma_vol_target", "status": "TIMEOUT"}]
    assert results[0]["strategy_id"] == "vol_target"
