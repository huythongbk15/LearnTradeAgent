"""Synthetic regression: a previous position's stop cannot sell a new entry."""
from types import SimpleNamespace

import pytest

from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.canonical.order_planner import InstrumentRules
from trading_agent.execution.lifecycle import ExecutionHealth
from trading_agent.execution.lifecycle.lifecycle import EmergencyReduceRequest
from trading_agent.execution.types import OrderStatus

SYMBOL = "ETH/USDT"


@pytest.fixture
def engine(tmp_path):
    instance = ExecutionEngine(
        exchange_name="paper", initial_capital=10000, commission=0.001,
        slippage=0.0025,
        instrument_rules=InstrumentRules(
            symbol=SYMBOL, min_order_qty=0.0001, max_order_qty=100,
            qty_step=0.0001, min_notional=10,
        ),
        state_dir=tmp_path / "paper", event_store_path=tmp_path / "events.db",
        disable_paper_telemetry=True,
    )
    instance.update_prices({SYMBOL: 3000.0})
    yield instance
    instance.store.close()


def protected_entry(engine, quantity, name, *, retry=False):
    # Seed only local paper inventory. Protection and close use the real
    # service/lifecycle/gateway; no account or external broker is involved.
    entry = engine.exchange.place_order(SYMBOL, "buy", amount=quantity)
    assert entry.status == OrderStatus.FILLED
    result = engine.execution_service.emergency_protection(
        EmergencyReduceRequest(
            intent_id=f"prot_{name}_submit" + ("_retry1" if retry else ""),
            symbol=SYMBOL, side="sell",
            quantity=quantity, reason="PROTECTIVE_STOP",
            metadata={"order_type": "stop", "stop_price": 2850.0},
        )
    )
    assert result.success and result.evidence
    return engine.exchange.orders[result.evidence.broker_order_id]


@pytest.mark.parametrize("close_all", [False, True])
@pytest.mark.parametrize("retry", [False, True])
def test_emergency_close_cancels_old_stop_before_reentry(engine, close_all, retry):
    old_stop = protected_entry(engine, 0.3511, "old", retry=retry)
    orders = engine.close_all() if close_all else [engine.close_position(SYMBOL)]
    assert len(orders) == 1 and orders[0] is not None
    assert old_stop.status == OrderStatus.CANCELED
    assert engine.exchange.get_position(SYMBOL) is None
    assert engine.lifecycle.active_sell_reservations(SYMBOL) == pytest.approx(0)

    new_stop = protected_entry(engine, 0.3514, "new")
    engine.update_prices({SYMBOL: 2700.0})
    assert old_stop.status == OrderStatus.CANCELED
    assert new_stop.status == OrderStatus.FILLED
    assert engine.exchange.get_position(SYMBOL) is None
    assert engine.exchange.trades[-1].quantity == pytest.approx(0.3514)
    assert engine.lifecycle.active_sell_reservations(SYMBOL) == pytest.approx(0)
    assert sum(trade.pnl for trade in engine.exchange.trades) == pytest.approx(
        engine.exchange.get_balance() - 10000,
    )


def test_unknown_cancel_blocks_emergency_sell_and_keeps_inventory(engine, monkeypatch):
    protected_entry(engine, 0.3511, "unknown")
    before = len(engine.exchange.orders)
    monkeypatch.setattr(
        engine.gateway, "cancel",
        lambda *args, **kwargs: SimpleNamespace(evidence=None, error="unknown"),
    )
    assert engine.close_position(SYMBOL) is None
    assert len(engine.exchange.orders) == before
    assert engine.exchange.get_position(SYMBOL).quantity == pytest.approx(0.3511)
    assert engine.lifecycle.state.execution_health == ExecutionHealth.PROTECTION_GAP


def test_stop_fill_during_cancel_does_not_submit_a_second_sell(engine, monkeypatch):
    protected_entry(engine, 0.3511, "race")
    cancel = engine.gateway.cancel

    def fill_then_cancel(*args, **kwargs):
        engine.exchange.update_prices({SYMBOL: 2700.0})
        return cancel(*args, **kwargs)

    monkeypatch.setattr(engine.gateway, "cancel", fill_then_cancel)
    before = len(engine.exchange.orders)
    assert engine.close_position(SYMBOL) is None
    assert len(engine.exchange.orders) == before
    assert len(engine.exchange.trades) == 1
    assert engine.exchange.trades[0].reason == "stop_loss"
    assert engine.exchange.get_position(SYMBOL) is None
    assert engine.lifecycle.active_sell_reservations(SYMBOL) == pytest.approx(0)


def test_failed_exit_after_cancel_records_protection_gap(engine, monkeypatch):
    old_stop = protected_entry(engine, 0.3511, "failed")

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic submit failure")

    monkeypatch.setattr(engine.execution_service, "emergency_close", fail)
    assert engine.close_position(SYMBOL) is None
    assert old_stop.status == OrderStatus.CANCELED
    assert engine.exchange.get_position(SYMBOL).quantity == pytest.approx(0.3511)
    assert engine.lifecycle.state.execution_health == ExecutionHealth.PROTECTION_GAP
