from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_enhanced_ma_binance as runner
from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
)


def test_allocations_are_not_normalized():
    allocations = runner.parse_allocations(
        "BTC/USDT,SOL/USDT", "20,10", LiveRiskLimits()
    )
    assert allocations == [("BTC/USDT", 0.2), ("SOL/USDT", 0.1)]


def test_allocations_cannot_exceed_gross_limit():
    with pytest.raises(LiveSafetyError, match="gross limit"):
        runner.parse_allocations(
            "BTC/USDT,SOL/USDT,AVAX/USDT", "20,20,11", LiveRiskLimits()
        )


def test_live_data_drops_forming_candle(monkeypatch):
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    current_hour = now_ms // 3_600_000 * 3_600_000
    old_start = current_hour - 150 * 3_600_000
    bars = [
        [old_start + index * 3_600_000, 100, 101, 99, 100, 1]
        for index in range(150)
    ]
    bars.append([now_ms - 1_000, 200, 201, 199, 200, 1])

    class FakeExchange:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return bars

    monkeypatch.setattr(runner.ccxt, "binance", lambda config: FakeExchange())
    frame = runner.get_recent_df("BTC/USDT")
    assert len(frame) == 150
    assert frame["close"].tail(1).item() == 100


def test_live_data_rejects_hourly_gap():
    now = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
    hour_ms = 3_600_000
    start = int((now - timedelta(hours=4, minutes=30)).timestamp() * 1_000)
    bars = [
        [start, 100, 101, 99, 100, 1],
        [start + 2 * hour_ms, 100, 101, 99, 100, 1],
    ]
    with pytest.raises(LiveSafetyError, match="gap"):
        runner.validate_live_hourly_bars(bars, symbol="BTC/USDT", now=now)


def test_live_data_rejects_stale_and_inconsistent_ohlc():
    now = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
    stale = int((now - timedelta(hours=4, minutes=30)).timestamp() * 1_000)
    with pytest.raises(LiveSafetyError, match="stale"):
        runner.validate_live_hourly_bars(
            [[stale, 100, 101, 99, 100, 1]],
            symbol="BTC/USDT",
            now=now,
        )
    recent = int((now - timedelta(hours=1, minutes=30)).timestamp() * 1_000)
    with pytest.raises(LiveSafetyError, match="inconsistent"):
        runner.validate_live_hourly_bars(
            [[recent, 100, 99, 98, 100, 1]],
            symbol="BTC/USDT",
            now=now,
        )


def test_market_data_failure_cancels_entire_batch(monkeypatch):
    monkeypatch.setattr(runner, "get_recent_df", lambda symbol: (_ for _ in ()).throw(RuntimeError(symbol)))
    with pytest.raises(RuntimeError):
        runner.get_recent_df("BTC/USDT")


def test_old_long_is_not_sold_only_because_entry_left_replay_window():
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "FLAT",
                "price": 100.0,
                "ma_fast": 110.0,
                "ma_slow": 100.0,
                "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
            }
        },
        positions=[{"symbol": "BTC/USDT", "qty": 2.0}],
        equity=1_000.0,
        locked_reason=None,
    )
    assert decisions == []


def test_existing_long_exits_when_fast_ma_is_below_slow_ma():
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "FLAT",
                "price": 100.0,
                "ma_fast": 90.0,
                "ma_slow": 100.0,
                "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
            }
        },
        positions=[{"symbol": "BTC/USDT", "qty": 2.0}],
        equity=1_000.0,
        locked_reason=None,
    )
    assert decisions[0]["action"] == "SELL"
    assert decisions[0]["reason"] == "STRATEGY_FLAT"


class ExecutionBroker:
    def __init__(self, *, result=None, error: Exception | None = None, reconciled=None):
        self.result = result
        self.error = error
        self.reconciled = reconciled

    def get_account(self):
        return {"equity": 1_000.0, "cash": 1_000.0}

    def get_positions(self):
        return []

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
            "bid": 99.9,
            "ask": 100.0,
            "last": 100.0,
        }

    def get_order_book(self, symbol, limit=50):
        return {
            "timestamp": datetime.now(UTC),
            "bids": [(99.9, 10.0), (99.8, 10.0)],
            "asks": [(100.0, 10.0), (100.1, 10.0)],
        }

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        return round(amount, 6)

    def place_order(self, order):
        if self.error is not None:
            raise self.error
        return self.result

    def get_order_by_client_id(self, client_order_id, symbol):
        return self.reconciled


def planned_buy():
    return {
        "market_symbol": "BTC/USDT",
        "action": "BUY",
        "qty": 0.1,
        "signal_price": 100.0,
        "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
        "reason": "test",
    }


def order_result(status: str, *, filled_qty: float) -> dict:
    return {
        "id": "exchange-1",
        "client_order_id": "ignored",
        "status": status,
        "symbol": "BTC/USDT",
        "side": "buy",
        "qty": 0.1,
        "filled_qty": filled_qty,
        "avg_fill_price": 100.0 if filled_qty else 0.0,
        "error": None,
    }


def test_partial_fill_stops_batch_and_is_persisted(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = ExecutionBroker(result=order_result("partial", filled_qty=0.04))
    with pytest.raises(LiveSafetyError, match="batch stopped"):
        runner.execute_orders(
            orders=[planned_buy()],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
        )
    record = next(iter(store.state.order_ledger.values()))
    assert record["status"] == "partial"
    assert record["filled_quantity"] == pytest.approx(0.04)


def test_timeout_after_accept_is_reconciled_and_stops_batch(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = ExecutionBroker(
        error=TimeoutError("client timed out"),
        reconciled=order_result("filled", filled_qty=0.1),
    )
    with pytest.raises(LiveSafetyError, match="exchange reports filled"):
        runner.execute_orders(
            orders=[planned_buy()],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
        )
    record = next(iter(store.state.order_ledger.values()))
    assert record["status"] == "filled"
    assert store.unfinished_orders() == {}


def test_unfinished_order_blocks_new_batch_when_exchange_cannot_find_it(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    store.reserve_order(
        "old-intent",
        symbol="BTC/USDT",
        side="BUY",
        quantity=0.1,
        signal_timestamp=datetime(2026, 8, 10, tzinfo=UTC),
    )
    with pytest.raises(LiveSafetyError, match="reconciliation blocked"):
        runner.reconcile_unfinished_orders(
            broker=ExecutionBroker(reconciled=None),
            store=store,
        )
    assert store.state.order_ledger["old-intent"]["status"] == "unknown"


def test_atr_trail_forces_exit_and_persists_peak(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    states = {
        "BTC/USDT": {
            "state": "LONG",
            "price": 90.0,
            "atr": 5.0,
            "recent_high": 110.0,
        }
    }
    runner.apply_atr_protection(
        states=states,
        positions=[{"symbol": "BTC/USDT", "qty": 0.1}],
        store=store,
    )
    assert states["BTC/USDT"]["state"] == "FLAT"
    assert states["BTC/USDT"]["atr_stop"] == pytest.approx(100.0)
    assert store.state.position_risk["BTC/USDT"]["peak_price"] == pytest.approx(110.0)
    _, widened_atr_stop = store.observe_position_risk(
        "BTC/USDT",
        quantity=0.1,
        observed_high=110.0,
        atr=20.0,
        atr_multiplier=2.0,
    )
    assert widened_atr_stop == pytest.approx(100.0)
