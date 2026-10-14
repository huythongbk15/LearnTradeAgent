"""Exercise script fail-closed ordering without running a WFO campaign."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("failure_stage", ["accounting", "bundle", "source"])
def test_real_campaign_refusal_never_builds_or_registers_policy(
    monkeypatch, tmp_path, failure_stage
):
    from trading_agent.backtest import campaign_integrity, campaign_writer, nested_wfo
    from trading_agent.research import selection_policy

    path = Path(__file__).resolve().parents[1] / "scripts/run_real_wfo_campaign.py"
    spec = importlib.util.spec_from_file_location(
        "campaign_entrypoint_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "EVIDENCE", tmp_path / "evidence")
    monkeypatch.setattr(module, "OUT_STORE", tmp_path / "store")
    monkeypatch.setattr("sys.argv", [str(path)])
    monkeypatch.setattr(
        nested_wfo, "run_nested_wfo", Mock(return_value=SimpleNamespace())
    )
    source = (
        Mock(side_effect=[{"tree": "before"}, {"tree": "after"}])
        if failure_stage == "source"
        else Mock(return_value={"tree": "same"})
    )
    monkeypatch.setattr(campaign_integrity, "capture_campaign_source", source)
    collect = Mock(return_value=[{}])
    require = Mock()
    if failure_stage == "accounting":
        collect.side_effect = ValueError("invalid measured ledger")
    if failure_stage == "bundle":
        require.side_effect = ValueError("missing frozen bundle")
    monkeypatch.setattr(campaign_writer, "collect_wfo_campaign_rows", collect)
    monkeypatch.setattr(campaign_integrity, "require_wfo_campaign_evidence", require)
    build = Mock(side_effect=AssertionError("policy builder must not be called"))
    monkeypatch.setattr(
        selection_policy.SelectionPolicyBuilder, "from_wfo_result", build
    )

    with pytest.raises(SystemExit, match="NOT_QUALIFIED"):
        module.main()

    build.assert_not_called()
    assert not module.OUT_STORE.exists()
    if failure_stage in {"source", "accounting"}:
        require.assert_not_called()
    if failure_stage == "source":
        collect.assert_not_called()
