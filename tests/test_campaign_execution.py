"""Execution optimization checks; not strategy qualification."""

import time
from types import SimpleNamespace as NS

import pytest

from trading_agent.backtest.campaign_execution import (
    CampaignCellRunner,
    preflight_workload,
)


def test_parallel_preserves_order_and_worker_bound(tmp_path):
    runner = CampaignCellRunner(tmp_path, {}, workers=2)

    def fake(spec, **kwargs):
        time.sleep((4 - spec) * 0.005)
        return spec

    runner.__class__ = type(
        "FakeRunner",
        (CampaignCellRunner,),
        {"__call__": lambda self, spec, **kw: fake(spec, **kw)},
    )
    assert runner.run_batch([(i, 1, 2, True, 1, 2) for i in range(4)]) == list(range(4))
    with pytest.raises(ValueError):
        CampaignCellRunner(tmp_path, {}, workers=8)


def test_context_snapshot_and_preflight_budget(tmp_path):
    context = {"source": {"sha": "old"}}
    runner = CampaignCellRunner(tmp_path, context)
    context["source"]["sha"] = "new"
    assert runner.context["source"]["sha"] == "old"
    spec = NS(param_grid={"fast": [1, 2], "slow": [3, 4]}, cost_scenarios=[1, 2])
    assert preflight_workload(spec, [1, 2])["inner_cells"] == 16
    for budget in [0, -1, 15, True]:
        with pytest.raises(ValueError):
            preflight_workload(spec, [1, 2], max_inner_cells=budget)
    spec.param_grid["fast"] = [1, 1]
    with pytest.raises(ValueError):
        preflight_workload(spec, [1])


def test_changed_input_rejected_before_execution(tmp_path):
    from trading_agent.backtest.tournament import EvaluationCellSpec

    path = tmp_path / "input.parquet"
    path.touch()
    runner = CampaignCellRunner(
        tmp_path,
        {"data": {"path": str(path), "sha256": "wrong"}},
        resume=True,
        runner=lambda *a, **kw: pytest.fail("must not execute mutated input"),
    )
    with pytest.raises(ValueError, match="input changed"):
        runner(EvaluationCellSpec("enhanced_ma", "BTC/USDT"), start=0, end=10)


def test_failed_cell_persists_reason_without_success_cache(tmp_path):
    import json
    from trading_agent.backtest.tournament import EvaluationCellSpec, _failed_artifact

    spec = EvaluationCellSpec("enhanced_ma", "BTC/USDT")
    artifact = _failed_artifact(spec, None, "execution_failed_after_3_attempts:timeout:300s")
    runner = CampaignCellRunner(tmp_path, {"source": {"sha": "test"}},
                                runner=lambda *a, **kw: artifact)
    assert runner(spec, start=0, end=10).status == "FAILED"
    records = list((tmp_path / "cell_failures").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text())
    assert record["artifact"]["failure_reasons"] == list(artifact.failure_reasons)
    assert record["identity"]["context"]["source"]["sha"] == "test"
    assert not list((tmp_path / "cell_resume_cache").glob("*.json"))
