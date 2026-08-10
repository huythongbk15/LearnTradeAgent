"""Financial invariants for the core backtest ledger."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.strategies.base import Strategy


class StaticSignals(Strategy):
    name = "static_signals"

    def __init__(self, signals: list[int]) -> None:
        super().__init__()
        self.signals = signals

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return pl.Series("signal", self.signals)


def candles(
    opens: list[float],
    closes: list[float] | None = None,
) -> pl.DataFrame:
    closes = closes or opens
    timestamps = [
        datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)
        for i in range(len(opens))
    ]
    return pl.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": [max(open_, close) for open_, close in zip(opens, closes)],
        "low": [min(open_, close) for open_, close in zip(opens, closes)],
        "close": closes,
        "volume": [1.0] * len(opens),
    })


def test_round_trip_does_not_double_count_holding_return() -> None:
    # Signal at bar 0 enters at bar 1 open=100. Signal at bar 2 exits at
    # bar 3 open=121. A 10% allocation earns exactly 10 * (121 - 100) = 210.
    result = BacktestEngine(
        StaticSignals([1, 0, -1, 0]),
        initial_capital=10_000,
        commission=0,
        slippage=0,
        fixed_position_pct=0.1,
        timeframe="1d",
    ).run(candles([100.0, 100.0, 110.0, 121.0]))

    assert result.equity_curve["equity"][-1] == pytest.approx(10_210.0)
    assert result.total_return_pct == pytest.approx(2.1)
    assert result.total_trades == 1
    assert result.trades[0].pnl_abs == pytest.approx(210.0)
    assert result.trades[0].pnl_pct == pytest.approx(21.0)


def test_cash_plus_marked_position_always_equals_equity() -> None:
    result = BacktestEngine(
        StaticSignals([1, 0, -1, 0]),
        commission=0.001,
        slippage=0.0005,
        spread_bps=5,
        timeframe="1d",
    ).run(candles([100.0, 100.0, 110.0, 121.0]))

    for row in result.equity_curve.iter_rows(named=True):
        reconciled = row["cash"] + row["position"] * row["close"]
        assert row["equity"] == pytest.approx(reconciled)


def test_flat_price_loses_exactly_entry_and_exit_commission() -> None:
    result = BacktestEngine(
        StaticSignals([1, 0, -1, 0]),
        initial_capital=10_000,
        commission=0.001,
        slippage=0,
        fixed_position_pct=0.1,
        timeframe="1d",
    ).run(candles([100.0, 100.0, 100.0, 100.0]))

    assert result.equity_curve["equity"][-1] == pytest.approx(9_998.0)
    assert result.trades[0].fees == pytest.approx(2.0)
    assert result.trades[0].pnl_abs == pytest.approx(-2.0)


def test_signal_cannot_profit_from_gap_before_next_open_fill() -> None:
    result = BacktestEngine(
        StaticSignals([1, 0, 0]),
        initial_capital=10_000,
        commission=0,
        slippage=0,
        fixed_position_pct=0.1,
        timeframe="1d",
    ).run(candles([100.0, 150.0, 150.0]))

    assert result.equity_curve["equity"][-1] == pytest.approx(10_000.0)
    assert len(result.trades) == 1
    assert result.trades[0].is_open is True
    assert result.trades[0].entry_price == pytest.approx(150.0)


def test_invalid_ohlc_is_rejected() -> None:
    df = candles([100.0, 100.0])
    df = df.with_columns(pl.Series("high", [99.0, 100.0]))
    engine = BacktestEngine(StaticSignals([0, 0]), timeframe="1d")

    with pytest.raises(ValueError, match="Invalid OHLC"):
        engine.run(df)


def test_unknown_timeframe_is_rejected() -> None:
    engine = BacktestEngine(StaticSignals([0, 0]), timeframe="fortnight")

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        engine.run(candles([100.0, 100.0]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_capital": 0},
        {"commission": -0.1},
        {"commission": 1.0},
        {"slippage": -0.1},
        {"spread_bps": -1},
    ],
)
def test_invalid_cost_configuration_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        BacktestEngine(StaticSignals([0, 0]), **kwargs)
