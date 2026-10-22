"""Native policy preregistration/transport; synthetic-only tests."""

import pytest

from scripts.full_system_backtest import describe_execution_contract
from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE


def declared():
    return describe_execution_contract(symbol="BNB/USDT", timeframe="4h", strategy_name="ma_adx",
        parameters={"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25},
        commission=.001, slippage=.0005)


def test_declaration_needs_no_engine_or_data(monkeypatch):
    from scripts.full_system_backtest import FullSystemSimulator
    def forbidden(*args, **kwargs):
        pytest.fail("declaration must not initialize an engine")
    monkeypatch.setattr(FullSystemSimulator, "__init__", forbidden)
    contract = declared()
    body = contract["body"]
    assert body["strategy"]["parameters"]["adx_threshold"] == 25
    assert body["risk"]["cooldown_hours"] == 24
    assert body["authority"]["risk_profile"] == "moderate"
    assert body["sizing"]["requested_target_exposure_pct"] == 25
    assert body["cooldown_trigger"] == "risk_circuit_breaker_not_every_sell_fill"


@pytest.mark.parametrize("inline", [True, False])
def test_frozen_id_transported_without_changing_planned_cell_id(monkeypatch, inline):
    import trading_agent.backtest.tournament as tournament
    spec = EvaluationCellSpec("ma_adx", "BNB/USDT", cost_scenario=SCENARIO_BASE)
    before = spec.cell_id
    seen = {}
    def direct(primitives, **kwargs):
        seen.update(primitives)
        return "sentinel"
    def isolated(**kwargs):
        seen.update(kwargs["spec_primitives"])
        return "sentinel"
    monkeypatch.setattr(tournament, "_run_cell_impl", direct)
    monkeypatch.setattr(tournament, "_run_cell_with_retry", isolated)
    assert tournament.run_cell(spec, frozen_execution_contract_id="a"*64,
        _use_multiprocessing=not inline) == "sentinel"
    assert seen["frozen_execution_contract_id"] == "a"*64
    assert spec.cell_id == before


def test_conflicting_explicit_and_protocol_ids_rejected():
    from trading_agent.backtest.tournament import run_cell
    spec = EvaluationCellSpec("ma_adx", "BNB/USDT", expected_execution_contract_id="a"*64)
    with pytest.raises(ValueError, match="conflict"):
        run_cell(spec, frozen_execution_contract_id="b"*64)


def test_worker_forwards_frozen_id_to_real_cell_boundary(tmp_path, monkeypatch):
    from scripts import run_wfo_parallel as module
    contract = declared()
    spec = EvaluationCellSpec("ma_adx", "BNB/USDT")
    seen = {}
    def fake(spec, **kwargs):
        seen.update(kwargs)
        from trading_agent.backtest.tournament import _failed_artifact
        return _failed_artifact(spec, None, "synthetic_failure")
    monkeypatch.setattr(module, "run_cell", fake)
    module._run_cell_worker(module.ParallelCellRunner._primitives(spec), str(tmp_path),
        0, 2, True, 0, 2, 2, 0, {"native_execution_contract": contract})
    assert seen["frozen_execution_contract_id"] == contract["contract_id"]


def test_legacy_qualification_protocol_not_silently_upgraded(tmp_path):
    from scripts.run_wfo_parallel import QualificationCellRunner
    with pytest.raises(ValueError, match="frozen native"):
        QualificationCellRunner(tmp_path, {}, [])


def test_unregistered_qualification_contract_rejected_before_dispatch():
    from scripts.run_wfo_parallel import QualificationCellRunner
    runner = QualificationCellRunner.__new__(QualificationCellRunner)
    runner.protocol = {"native_execution_contracts": {}}
    with pytest.raises(ValueError, match="unregistered"):
        runner._run_measured(EvaluationCellSpec("ma_adx", "BNB/USDT"))
