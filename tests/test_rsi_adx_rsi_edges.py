"""Independent expected values for the approved rolling-RSI edge contract."""

import polars as pl
import pytest

from trading_agent.strategies.rsi_adx_regime_filter import RsiAdxRegimeFilterStrategy


def _indicators(prices, period=14):
    frame = pl.DataFrame({"close": prices}).with_columns(
        open=pl.col("close"), high=pl.col("close") + 1,
        low=pl.col("close") - 1, volume=pl.lit(1000.0),
    )
    strategy = RsiAdxRegimeFilterStrategy({"rsi_period": period})
    return strategy.compute_indicators(frame)


@pytest.mark.parametrize("prices,expected", [
    ([100.0] * 160, 50.0),
    ([100.0 + i for i in range(160)], 100.0),
    ([300.0 - i for i in range(160)], 0.0),
    ([100.0 + i * 1e-10 for i in range(160)], 100.0),
    ([100.0 - i * 1e-10 for i in range(160)], 0.0),
])
def test_one_sided_and_flat_windows_have_exact_values(prices, expected):
    result = _indicators(prices)
    assert result["rsi"].slice(100).to_list() == [expected] * 60


def test_mixed_window_keeps_existing_rolling_formula():
    # Last four differences are +2,-1,+3,-2 => means1.25/0.75.
    result = _indicators([100.0, 102.0, 101.0, 104.0, 102.0], period=4)
    expected = 100 - 100 / (1 + 1.25 / (0.75 + 1e-9))
    assert result["rsi"][-1] == pytest.approx(expected, rel=1e-12)


def test_warmup_stays_null_and_flat_tail_forgets_old_trend():
    result = _indicators([100.0 + i for i in range(50)] + [149.0] * 30)
    assert result["rsi"].head(13).null_count() == 13
    assert result["rsi"].tail(10).to_list() == [50.0] * 10
