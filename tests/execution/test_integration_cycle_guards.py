"""Integration review regressions; synthetic inputs only."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trading_agent.authority.portfolio import PortfolioSnapshot
from trading_agent.execution.position_cycle import (
    PositionCycleRecord,
    cycle_record_from_position,
)


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), -1.0, True])
def test_invalid_inventory_cannot_become_known_flat(quantity):
    with pytest.raises(ValueError):
        cycle_record_from_position(SimpleNamespace(quantity=quantity, is_active=False))


def test_snapshot_cycle_map_does_not_alias_caller():
    cycles = {"ETH/USDT": PositionCycleRecord(0.0, True)}
    positions = {"ETH/USDT": 0.0}
    snapshot = PortfolioSnapshot(
        equity=1000, available_cash=1000, positions=positions,
        symbol_exposures={}, gross_exposure=0,
        position_cycles=cycles, observed_at=datetime.now(UTC),
    )
    cycles.clear()
    positions["ETH/USDT"] = 9.0
    assert snapshot.position_cycles["ETH/USDT"].quantity == 0
    assert snapshot.positions["ETH/USDT"] == 0
    with pytest.raises(TypeError):
        snapshot.position_cycles["OTHER"] = PositionCycleRecord(0.0, True)


@pytest.mark.parametrize("extra", [{}, {"execution_cycle_synthetic_only": True}])
def test_cycle_driver_refuses_unspecified_or_missing_synthetic_input(tmp_path, extra):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy

    with pytest.raises(ValueError, match="explicit synthetic-only input"):
        FullSystemSimulator(
            execution_cycle_strategy=Own24FillContextStrategy({"arm": "A0"}),
            state_dir=tmp_path / "execution", **extra,
        )
    assert not (tmp_path / "execution").exists()
