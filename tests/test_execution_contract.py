from copy import deepcopy
from types import SimpleNamespace

import pytest

from trading_agent.backtest.execution_contract import (
    native_execution_contract, require_execution_contract,
)


def fixture_contract(**overrides):
    values = dict(symbol="BNB/USDT", timeframe="4h", strategy={"id": "ma_adx", "adx": 25},
        protection={"stop": .05}, sizing={"entry": .25}, risk={"cooldown": 24},
        cost_model={"fee": .001}, authority={"risk_profile": "moderate"},
        instrument_rules={"qty_step": .001})
    return native_execution_contract(**{**values, **overrides})


@pytest.mark.parametrize("change", [
    {"sizing": {"entry": .1875}}, {"risk": {"cooldown": 0}},
    {"protection": {"stop": .1}}, {"cost_model": {"fee": .002}},
    {"authority": {"risk_profile": "aggressive"}},
    {"instrument_rules": {"qty_step": .01}}, {"cadence": 2}, {"timeframe": "1h"},
])
def test_each_runtime_difference_changes_identity(change):
    base = fixture_contract()
    with pytest.raises(ValueError, match="mismatch"):
        require_execution_contract(base["contract_id"], fixture_contract(**change))


def test_identity_detached_and_deterministic():
    sizing = {"entry": .25}
    a = fixture_contract(sizing=sizing)
    sizing["entry"] = .5
    assert a == fixture_contract()
    require_execution_contract(a["contract_id"], a)
    broken = deepcopy(a)
    broken["body"]["risk"]["cooldown"] = 0
    with pytest.raises(ValueError):
        require_execution_contract(a["contract_id"], broken)


@pytest.mark.parametrize("bad", [True, "", "a"*63, "A"*64, {}, 1])
def test_invalid_expected_id_rejected_before_constructor_io(bad):
    from scripts.full_system_backtest import FullSystemSimulator
    with pytest.raises(ValueError, match="SHA256"):
        FullSystemSimulator(expected_execution_contract_id=bad)


def test_mismatch_stops_before_first_engine_price_update():
    from scripts.full_system_backtest import FullSystemSimulator
    sim = FullSystemSimulator.__new__(FullSystemSimulator)
    sim.expected_execution_contract_id = "0"*64
    sim._active_config = lambda: {"execution_contract": fixture_contract()}
    def unexpected(*args):
        pytest.fail("engine I/O must not run on contract mismatch")
    sim.engine = SimpleNamespace(update_prices=unexpected)
    with pytest.raises(ValueError, match="mismatch"):
        sim.run()


@pytest.mark.parametrize("inline", [True, False])
def test_cell_transports_expected_identity(monkeypatch, inline):
    import trading_agent.backtest.tournament as tournament
    expected = fixture_contract()["contract_id"]
    spec = tournament.EvaluationCellSpec("ma_adx", "BNB/USDT", expected_execution_contract_id=expected)
    seen = {}
    def direct(primitives, **kwargs):
        seen.update(primitives)
        return "sentinel"
    def isolated(**kwargs):
        seen.update(kwargs["spec_primitives"])
        return "sentinel"
    monkeypatch.setattr(tournament, "_run_cell_impl", direct)
    monkeypatch.setattr(tournament, "_run_cell_with_retry", isolated)
    assert tournament.run_cell(spec, _use_multiprocessing=not inline) == "sentinel"
    assert seen["expected_execution_contract_id"] == expected
    assert expected in spec.cell_id
    assert "__e" not in tournament.EvaluationCellSpec("ma_adx", "BNB/USDT").cell_id


def test_missing_legacy_contract_cannot_satisfy_expected():
    with pytest.raises(ValueError):
        require_execution_contract(fixture_contract()["contract_id"], {})
