from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trading_agent.backtest.accounting_evidence import export_closed_trade_accounting
from trading_agent.execution.backtest_sim.backtest_integration import (
    SimulatorBacktestEngine,
)
from trading_agent.execution.backtest_sim.models import OrderSide


def engine():
    result = SimulatorBacktestEngine.__new__(SimulatorBacktestEngine)
    result._position = 0.0
    result._cash = 10000.0
    result._entry_price = result._entry_reference = 0.0
    result._entry_time = None
    result._entry_costs = {}
    result._seen_fill_ids = set()
    result._trades = []
    return result


def fill(identity, side, quantity, price, fee):
    return SimpleNamespace(
        fill_id=identity,
        side=side,
        quantity=quantity,
        price=price,
        fee=fee,
        mid_price_at_fill=price,
        timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        slippage_cost=0.0,
        spread_cost=0.0,
        impact_cost=0.0,
        price_cap_credit=0.0,
    )


@pytest.mark.parametrize(
    "side,exit_side,first,last",
    [
        (OrderSide.BUY, OrderSide.SELL, 110.0, 90.0),
        (OrderSide.SELL, OrderSide.BUY, 90.0, 110.0),
    ],
)
def test_partial_exit_allocates_only_filled_quantity_and_both_fees(
    side, exit_side, first, last
):
    ledger = engine()
    ledger._process_fill(fill("entry", side, 10.0, 100.0, 1.0))
    partial = fill("partial", exit_side, 4.0, first, 0.4)
    ledger._process_fill(partial)
    assert abs(ledger._position) == 6.0
    assert ledger._trades[0]["quantity"] == 4.0
    assert ledger._trades[0]["pnl"] == pytest.approx(39.2)
    assert ledger._entry_costs["fees"] == pytest.approx(0.6)
    snapshot = (ledger._cash, ledger._position, len(ledger._trades))
    ledger._process_fill(partial)
    assert snapshot == (ledger._cash, ledger._position, len(ledger._trades))
    ledger._process_fill(fill("last", exit_side, 6.0, last, 0.6))
    assert ledger._cash == pytest.approx(9978.0)
    assert ledger._position == 0.0
    exported = export_closed_trade_accounting(
        ledger._trades, equity_delta=-22.0, open_inventory=False
    )
    assert exported["net_pnl"] == pytest.approx(-22.0)
    assert exported["fees"] == pytest.approx(2.0)


def test_reversal_splits_close_and_new_short_costs():
    ledger = engine()
    ledger._process_fill(fill("entry", OrderSide.BUY, 10.0, 100.0, 1.0))
    ledger._process_fill(fill("reversal", OrderSide.SELL, 15.0, 110.0, 1.5))
    assert ledger._position == -5.0
    assert ledger._trades[0]["pnl"] == pytest.approx(98.0)
    assert ledger._entry_costs["fees"] == pytest.approx(0.5)
    ledger._process_fill(fill("close", OrderSide.BUY, 5.0, 100.0, 0.5))
    assert ledger._cash == pytest.approx(10147.0)
    assert sum(t["pnl"] for t in ledger._trades) == pytest.approx(147.0)
    export_closed_trade_accounting(
        ledger._trades, equity_delta=147.0, open_inventory=False
    )
