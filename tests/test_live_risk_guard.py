"""Unit tests cho risk guard trong live runner (ATR trailing stop + drawdown halt)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import live_enhanced_ma as live_script
import numpy as np
import polars as pl
import pytest
from live_enhanced_ma import ATR_SL_MULT, trailing_stop_price

from trading_agent.risk.portfolio_risk import DrawdownConfig, PortfolioRiskManager


def make_df(closes, highs=None, atr=None, n=1000):
    """Dựng df đủ dài với cột high/close/atr (atr hằng số nếu không truyền)."""
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes
    atr = np.full(n, atr if atr is not None else 1.0)
    return pl.DataFrame(
        {
            "close": np.concatenate([np.full(n - len(closes), closes[0]), closes]),
            "high": np.concatenate([np.full(n - len(highs), highs[0]), highs]),
            "atr": atr,
        }
    )


class TestTrailingStopPrice:
    def test_returns_none_without_atr_column(self):
        df = pl.DataFrame({"close": [1.0, 2.0], "high": [1.0, 2.0]})
        assert trailing_stop_price(1.0, df) is None

    def test_initial_stop_below_entry(self):
        """Chưa có peak cao hơn entry → stop = entry - k*ATR."""
        closes = [100.0] * 100  # giá đứng yên
        df = make_df(closes, atr=1.0)
        stop = trailing_stop_price(100.0, df)
        assert stop == pytest.approx(100.0 - ATR_SL_MULT * 1.0)

    def test_trailing_ratchets_up_with_peak(self):
        """Giá tăng lên 110 rồi về 105 → stop = 110 - k*ATR (cao hơn initial)."""
        closes = [100.0] * 100 + [110.0] * 10 + [105.0] * 10
        highs = [100.0] * 100 + [112.0] * 10 + [112.0] * 10
        df = make_df(closes, highs, atr=1.0)
        stop = trailing_stop_price(100.0, df)
        assert stop == pytest.approx(112.0 - ATR_SL_MULT * 1.0)

    def test_stop_never_below_initial(self):
        """Peak thấp (giá giảm ngay) → stop vẫn ≥ entry - k*ATR."""
        closes = [100.0] * 100 + [98.0] * 10
        highs = [100.0] * 100 + [99.0] * 10
        df = make_df(closes, highs, atr=2.0)
        stop = trailing_stop_price(100.0, df)
        assert stop >= 100.0 - ATR_SL_MULT * 2.0 - 1e-9


class TestDrawdownHalt:
    def test_scale_tiers(self):
        pm = PortfolioRiskManager(
            DrawdownConfig(
                tiers=[
                    (0.05, 0.75),
                    (0.10, 0.50),
                    (0.15, 0.25),
                    (0.20, 0.00),
                ]
            )
        )
        pm.update_equity(100_000)
        assert pm.position_scale_factor() == 1.0
        pm.update_equity(93_000)  # DD 7%
        assert pm.position_scale_factor() == 0.75
        pm.update_equity(85_000)  # DD 15%
        assert pm.position_scale_factor() == 0.25
        pm.update_equity(79_000)  # DD 21% → halt
        assert pm.is_trading_halted()
        assert pm.position_scale_factor() == 0.0

    def test_peak_persistence(self):
        """Seed peak cũ → DD tính đúng dù equity hiện tại cao hơn khởi tạo."""
        pm = PortfolioRiskManager(DrawdownConfig())
        pm.update_equity(110_000)  # peak từ lần chạy trước
        pm.update_equity(100_000)  # equity hiện tại
        assert pm.current_dd == pytest.approx(10_000 / 110_000)
        assert pm.position_scale_factor() == 0.75  # DD 9.1% → tier đầu (-5% → 0.75)


def test_corrupt_peak_state_fails_closed(tmp_path, monkeypatch):
    peak_file = tmp_path / "peak.json"
    peak_file.write_text("corrupt")
    monkeypatch.setattr(live_script, "PEAK_STATE_FILE", str(peak_file))

    with pytest.raises(RuntimeError, match="refusing to trade"):
        live_script.load_peak_equity()


def test_live_data_drops_forming_candle(monkeypatch):
    now_ms = int(live_script.datetime.now(live_script.UTC).timestamp() * 1000)

    class FakeExchange:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return [
                [now_ms - 7_200_000, 100, 101, 99, 100, 1],
                [now_ms - 1_000, 200, 201, 199, 200, 1],
            ]

    monkeypatch.setattr(live_script.ccxt, "binance", lambda config: FakeExchange())
    frame = live_script.get_recent_df("BTC/USDT")

    assert len(frame) == 1
    assert frame["close"].item() == 100
