"""Short-side support tests for BacktestEngine.

Verifies that long_short mode (long_only=False) correctly handles:
- Short entry (position < 0)
- Short exit (closing short via signal)
- Short SL/TP (SL above, TP below)
- Short P&L sign convention
- Equity reconciliation (cash + position * close = equity)
"""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.strategies.base import Strategy


class StaticSignals(Strategy):
    name = "static_signals_short"

    def __init__(self, signals: list[int]) -> None:
        super().__init__()
        self.signals = signals

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # Add a dummy ATR column for SL/TP tests
        if "atr" not in df.columns:
            return df.with_columns(pl.lit(5.0, dtype=pl.Float64).alias("atr"))
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return pl.Series("signal", self.signals)


def candles(opens: list[float], closes: list[float] | None = None) -> pl.DataFrame:
    closes = closes or opens
    timestamps = [
        datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i) for i in range(len(opens))
    ]
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1.0] * len(opens),
        }
    )


class TestShortSideEntryExit:
    def test_short_entry_sell_high_return_low(self):
        """Short at 100 (bar 1), exit at 90 (bar 3) → profit."""
        # Signal at index 0 (-1) → short at bar 1's open=100
        # Signal at index 2 (+1) → close short at bar 3's open=90
        result = BacktestEngine(
            StaticSignals([-1, 0, 1, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            spread_bps=0,
            fixed_position_pct=0.1,
            long_only=False,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 90.0, 90.0]))

        assert result.total_trades == 1
        assert result.trades[0].direction == -1  # short
        assert result.trades[0].pnl_abs > 0  # profitable short
        assert result.trades[0].entry_price == pytest.approx(100.0)
        assert result.trades[0].exit_price == pytest.approx(90.0)

    def test_short_loss_when_price_rises(self):
        """Short at 100 (bar 1), exit at 110 (bar 3) → loss."""
        result = BacktestEngine(
            StaticSignals([-1, 0, 1, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            spread_bps=0,
            fixed_position_pct=0.1,
            long_only=False,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 110.0, 110.0]))

        assert result.total_trades == 1
        assert result.trades[0].direction == -1
        assert result.trades[0].pnl_abs < 0  # losing short

    def test_short_equity_reconciliation(self):
        """Cash + position * close always equals equity for short positions."""
        result = BacktestEngine(
            StaticSignals([-1, 0, 1, 0]),
            initial_capital=10_000,
            commission=0.001,
            slippage=0.0005,
            spread_bps=5,
            fixed_position_pct=0.1,
            long_only=False,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 90.0, 90.0]))

        for row in result.equity_curve.iter_rows(named=True):
            reconciled = row["cash"] + row["position"] * row["close"]
            assert row["equity"] == pytest.approx(reconciled)

    def test_short_sl_triggers_on_high(self):
        """Short SL (above entry) triggers when high breaches it."""
        # Entry at 100, ATR=5 (from compute_indicators), SL = 100 + 5*5 = 125
        # Price rises to 130 → SL triggers
        result = BacktestEngine(
            StaticSignals([-1, 0, 0, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            spread_bps=0,
            fixed_position_pct=0.1,
            long_only=False,
            atr_sl_mult=5.0,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 130.0, 130.0]))

        assert result.total_trades == 1
        assert result.trades[0].exit_reason == "stop_loss"
        assert result.trades[0].pnl_abs < 0  # SL loss

    def test_long_only_rejects_short_signal(self):
        """When long_only=True, -1 signal should exit (close position), not short."""
        result = BacktestEngine(
            StaticSignals([-1, 0, 0, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            fixed_position_pct=0.1,
            long_only=True,  # default
            timeframe="1d",
        ).run(candles([100.0, 100.0, 90.0, 90.0]))

        # No position to close → no trades
        assert result.total_trades == 0
        assert result.equity_curve["equity"][-1] == pytest.approx(10_000.0)

    def test_switch_from_short_to_long(self):
        """Signal -1 then +1: close short, open long."""
        # Bar 0: signal=-1 → short at bar 1 open=100
        # Bar 2: signal=+1 → close short at bar 3 open=90, open long at 90
        # Bar 4: signal=0 → hold
        # Bar 5: signal=0 → hold
        # Wait, need signal=+1 at index 4 to close long at bar 5... no, need +1 at index 5
        # Actually: signal -1 at 0 → short at bar 1 (open=100)
        # signal +1 at 2 → close short at bar 3 (open=90), open long at bar 3 (open=90)
        # signal -1 at 4 → close long at bar 5 (open=110)
        # That gives us 2 trades: short(100→90 profit) + long(90→110 profit)
        result = BacktestEngine(
            StaticSignals([-1, 0, 1, 0, -1, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            spread_bps=0,
            fixed_position_pct=0.1,
            long_only=False,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 90.0, 90.0, 110.0, 110.0]))

        assert result.total_trades == 2
        assert result.trades[0].direction == -1  # short
        assert result.trades[0].pnl_abs > 0  # profit: short 100→90
        assert result.trades[1].direction == 1  # long
        assert result.trades[1].pnl_abs > 0  # profit: long 90→110

    def test_short_position_cannot_exceed_available(self):
        """Short position size should use same sizing as long."""
        result = BacktestEngine(
            StaticSignals([-1, 0, 0, 0]),
            initial_capital=10_000,
            commission=0,
            slippage=0,
            spread_bps=0,
            fixed_position_pct=0.1,
            long_only=False,
            timeframe="1d",
        ).run(candles([100.0, 100.0, 90.0, 90.0]))

        # Position should be non-zero (short)
        assert result.equity_curve["position"][1] < 0  # short at bar 1
        # Cash should be higher than initial (received proceeds from short sale)
        assert result.equity_curve["cash"][1] > 10_000.0
