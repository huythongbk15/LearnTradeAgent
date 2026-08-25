from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.execution import paper_exchange as paper_module
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.types import OrderSide, OrderStatus


@pytest.fixture(autouse=True)
def no_database_logging(monkeypatch):
    monkeypatch.setattr(paper_module, "_log_trade_to_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        paper_module, "_log_equity_snapshot", lambda *args, **kwargs: None
    )


def test_roundtrip_reconciles_cash_pnl_and_both_fees(tmp_path):
    exchange = PaperExchange(
        exchange_name="test",
        initial_balance=10_000,
        commission=0.001,
        slippage=0.0005,
        state_dir=tmp_path,
    )
    exchange.update_prices({"BTC/USDT": 100.0})
    entry = exchange.place_order("BTC/USDT", OrderSide.BUY, amount=10)
    exchange.update_prices({"BTC/USDT": 110.0})
    exit_order = exchange.place_order("BTC/USDT", OrderSide.SELL, amount=10)

    assert exchange.get_all_positions() == []
    trade = exchange.trades[-1]
    assert trade.entry_fee == pytest.approx(entry.fee)
    assert trade.exit_fee == pytest.approx(exit_order.fee)
    assert trade.entry_order_id == entry.id
    assert trade.exit_order_id == exit_order.id
    assert exchange.get_balance() - 10_000 == pytest.approx(trade.pnl)

    reloaded = PaperExchange(
        exchange_name="test", initial_balance=10_000, state_dir=tmp_path
    )
    restored = reloaded.trades[-1]
    assert restored.entry_order_id == entry.id
    assert restored.exit_order_id == exit_order.id


def test_kill_switch_refuses_stale_or_missing_fill_price(tmp_path):
    exchange = PaperExchange(exchange_name="test", state_dir=tmp_path)
    exchange.update_prices({"BTC/USDT": 100.0})
    exchange.place_order("BTC/USDT", "buy", amount=1)
    exchange._last_price_cache.clear()

    result = exchange.close_all_positions(reason="test")

    assert result == {
        "closed": [],
        "skipped": ["BTC/USDT"],
        "remaining": ["BTC/USDT"],
    }
    assert exchange.get_position("BTC/USDT") is not None


def test_kill_switch_refuses_stale_price(tmp_path):
    exchange = PaperExchange(
        exchange_name="test",
        state_dir=tmp_path,
        max_price_age_seconds=1,
    )
    exchange.update_prices({"BTC/USDT": 100.0})
    exchange.place_order("BTC/USDT", "buy", amount=1)
    exchange._last_price_timestamps["BTC/USDT"] = 0.0

    result = exchange.close_all_positions(reason="test")

    assert result["skipped"] == ["BTC/USDT"]
    assert result["remaining"] == ["BTC/USDT"]


def test_market_order_rejects_stale_price(tmp_path):
    exchange = PaperExchange(
        exchange_name="test",
        state_dir=tmp_path,
        max_price_age_seconds=1,
    )
    exchange.update_prices({"BTC/USDT": 100.0})
    exchange._last_price_timestamps["BTC/USDT"] = 0.0

    order = exchange.place_order("BTC/USDT", "buy", amount=1)

    assert order.status is OrderStatus.REJECTED
    assert exchange.get_position("BTC/USDT") is None


def test_partial_exits_are_individually_recorded_and_reconcile(tmp_path):
    exchange = PaperExchange(exchange_name="test", state_dir=tmp_path)
    exchange.update_prices({"BTC/USDT": 100.0})
    exchange.place_order("BTC/USDT", "buy", amount=10)
    exchange.update_prices({"BTC/USDT": 110.0})
    exchange.place_order("BTC/USDT", "sell", amount=4)
    exchange.place_order("BTC/USDT", "sell", amount=6)

    assert [trade.quantity for trade in exchange.trades] == [4, 6]
    assert [trade.reason for trade in exchange.trades] == ["partial_exit", "signal"]
    assert sum(trade.pnl for trade in exchange.trades) == pytest.approx(
        exchange.get_balance() - exchange.initial_balance
    )


def test_corrupt_state_fails_closed_instead_of_resetting_balance(tmp_path):
    state_path = tmp_path / "paper_test.json"
    state_path.write_text("not-json")

    with pytest.raises(RuntimeError, match="refusing unsafe reset"):
        PaperExchange(exchange_name="test", state_dir=tmp_path)

    assert state_path.read_text() == "not-json"


def test_state_file_is_valid_json_after_repeated_mutations(tmp_path):
    exchange = PaperExchange(exchange_name="test", state_dir=tmp_path)
    for price in (100.0, 101.0, 99.0):
        exchange.update_prices({"BTC/USDT": price})

    data = json.loads((tmp_path / "paper_test.json").read_text())
    assert data["balances"]["USDT"] == 10_000
    assert data["peak_equity"] == 10_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_balance": 0},
        {"commission": -0.1},
        {"slippage": 1.0},
        {"max_price_age_seconds": 0},
    ],
)
def test_invalid_exchange_configuration_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        PaperExchange(exchange_name="test", state_dir=tmp_path, **kwargs)


def test_execution_engine_preserves_explicit_zero_costs():
    engine = ExecutionEngine(
        exchange_name="zero_cost_test",
        initial_capital=1_000,
        commission=0,
        slippage=0,
    )

    assert engine.exchange.initial_balance == 1_000
    assert engine.exchange.commission == 0
    assert engine.exchange.slippage == 0


def test_execution_engine_accepts_only_recently_closed_candle():
    engine = ExecutionEngine(exchange_name="timestamp_guard_test")
    now = datetime.now(UTC)

    # Candle stamped "now" for 1h timeframe has not closed yet
    with pytest.raises(ValueError, match="incomplete"):
        engine.update_market_price("BTC/USDT", 100, now, "1h")
    # 4h-old open time on 1h timeframe → closed 3h ago > 2× duration
    with pytest.raises(ValueError, match="stale"):
        engine.update_market_price(
            "BTC/USDT",
            100,
            now - timedelta(hours=4),
            "1h",
        )

    # Closed one second ago → acceptable
    engine.update_market_price(
        "BTC/USDT",
        100,
        now - timedelta(hours=1, seconds=1),
        "1h",
    )
    assert engine.exchange._fresh_price("BTC/USDT") == 100


def test_update_market_price_rejects_bad_inputs():
    engine = ExecutionEngine(exchange_name="timestamp_guard_test2")
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        engine.update_market_price("BTC/USDT", 100, now, "banana")
    with pytest.raises(ValueError, match="invalid price"):
        engine.update_market_price("BTC/USDT", float("nan"), now, "1h")

    # Naive timestamp is interpreted as UTC and accepted when closed recently
    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=1, seconds=1
    )
    engine.update_market_price("BTC/USDT", 100, naive, "1h")
    assert engine.exchange._fresh_price("BTC/USDT") == 100
