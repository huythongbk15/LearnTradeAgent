from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from generate_live_strategy_evidence import (
    build_portfolio_folds,
    fold_ranges,
    validate_hourly_fold,
)
from trading_agent.execution.live_safety import LiveSafetyError


def test_fold_ranges_are_chronological_and_non_overlapping():
    latest = datetime(2026, 8, 10, 22, 0, tzinfo=UTC)
    ranges = fold_ranges(latest, 6, 90)
    assert len(ranges) == 6
    assert all(end - start == timedelta(days=90) for start, end in ranges)
    assert all(ranges[index][1] == ranges[index + 1][0] for index in range(5))
    assert ranges[-1][1] == latest + timedelta(hours=1)


def test_fold_policy_rejects_too_few_folds():
    with pytest.raises(LiveSafetyError, match="at least 6"):
        fold_ranges(datetime(2026, 8, 10), 5, 90)


def test_fold_policy_rejects_short_windows():
    with pytest.raises(LiveSafetyError, match="90 days"):
        fold_ranges(datetime(2026, 8, 10), 6, 30)


def test_hourly_fold_rejects_a_single_gap():
    start = datetime(2026, 1, 1)
    end = start + timedelta(days=90)
    timestamps = [start + timedelta(hours=index) for index in range(90 * 24)]
    frame = pl.DataFrame({"timestamp": timestamps[:100] + timestamps[101:]})
    with pytest.raises(LiveSafetyError, match="hourly gaps"):
        validate_hourly_fold(
            frame,
            symbol="BTC/USDT",
            index=1,
            start=start,
            end=end,
        )


def test_portfolio_fold_combines_weighted_component_pnl():
    timestamps = [
        datetime(2026, 1, 1, hour, tzinfo=UTC)
        for hour in range(3)
    ]
    source_fold = {
        "start": timestamps[0].isoformat(),
        "end": (timestamps[-1] + timedelta(hours=1)).isoformat(),
        "bars": 3,
        "trades": 1,
    }
    symbol_results = {
        "BTC/USDT": {"folds": [dict(source_fold)]},
        "SOL/USDT": {"folds": [dict(source_fold)]},
    }
    curves = {
        "BTC/USDT": [pl.DataFrame({"timestamp": timestamps, "equity": [10_000, 10_100, 10_200]})],
        "SOL/USDT": [pl.DataFrame({"timestamp": timestamps, "equity": [10_000, 9_900, 10_000]})],
    }
    portfolio = build_portfolio_folds(symbol_results, curves)
    assert portfolio[0]["return_pct"] == pytest.approx(2.0)
    assert portfolio[0]["trades"] == 2
