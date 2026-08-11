from __future__ import annotations

import polars as pl

from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover


def test_bearish_crossover_exits_even_when_adx_is_weak():
    strategy = EnhancedMaCrossover(
        {"fast_period": 2, "slow_period": 3, "adx_threshold": 40}
    )
    frame = pl.DataFrame({
        "ma_2": [2.0, 2.0, 1.0],
        "ma_3": [1.0, 1.0, 1.5],
        "adx": [50.0, 50.0, 10.0],
        "trend_up": [True, True, False],
    })
    signals = strategy.generate_signals(frame).to_list()
    assert signals[-1] == -1
