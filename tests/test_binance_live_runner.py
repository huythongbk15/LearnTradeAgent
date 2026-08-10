from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_enhanced_ma_binance as runner
from trading_agent.execution.live_safety import LiveRiskLimits, LiveSafetyError


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
    old_start = now_ms - 200 * 3_600_000
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


def test_market_data_failure_cancels_entire_batch(monkeypatch):
    monkeypatch.setattr(runner, "get_recent_df", lambda symbol: (_ for _ in ()).throw(RuntimeError(symbol)))
    with pytest.raises(RuntimeError):
        runner.get_recent_df("BTC/USDT")
