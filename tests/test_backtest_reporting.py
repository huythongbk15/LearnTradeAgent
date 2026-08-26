"""Unit tests for deterministic report-v2 evidence helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.reporting import (
    DataQualityError,
    assess_ohlcv,
    calculate_cost_attribution,
    calculate_performance_metrics,
    calendar_returns,
    fixed_allocation_buy_and_hold,
)


def _ohlcv(hours: list[int]) -> pl.DataFrame:
    timestamps = [datetime(2024, 1, 1, hour, tzinfo=UTC) for hour in hours]
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0 + hour for hour in hours],
            "high": [102.0 + hour for hour in hours],
            "low": [99.0 + hour for hour in hours],
            "close": [101.0 + hour for hour in hours],
            "volume": [10.0 for _ in hours],
        }
    )


def test_data_quality_records_gaps_without_imputation() -> None:
    report = assess_ohlcv(
        _ohlcv([0, 1, 3]),
        expected_interval=timedelta(hours=1),
        gap_policy="record",
    )

    assert report.accepted is True
    assert report.status == "accepted_with_recorded_gaps"
    assert report.gap_count == 1
    assert report.missing_bar_count == 1
    assert report.row_count == 3
    assert report.fingerprint.startswith("sha256:")


def test_data_quality_rejects_gaps_when_policy_requires_continuity() -> None:
    with pytest.raises(DataQualityError) as raised:
        assess_ohlcv(
            _ohlcv([0, 1, 3]),
            expected_interval=timedelta(hours=1),
            gap_policy="reject",
        )

    assert raised.value.report.status == "failed_gap_policy"
    assert raised.value.report.accepted is False


def test_data_quality_rejects_duplicate_timestamps_under_record_policy() -> None:
    with pytest.raises(DataQualityError) as raised:
        assess_ohlcv(
            _ohlcv([0, 1, 1]),
            expected_interval=timedelta(hours=1),
            gap_policy="record",
        )

    assert raised.value.report.duplicate_timestamps == 1
    assert raised.value.report.accepted is False


def test_cost_attribution_reconciles_reference_alpha_to_net_pnl() -> None:
    entry_reference = 100.0
    exit_reference = 110.0
    entry_fill = 100.05
    exit_fill = 109.945
    entry_fee = entry_fill * 0.001
    exit_fee = exit_fill * 0.001
    net_pnl = exit_fill - entry_fill - entry_fee - exit_fee
    trades = [
        {
            "quantity": 1.0,
            "entry_price": entry_fill,
            "exit_price": exit_fill,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "pnl": net_pnl,
            "metadata": {
                "simulation": {
                    "entry_reference_price": entry_reference,
                    "exit_reference_price": exit_reference,
                }
            },
        }
    ]

    attribution = calculate_cost_attribution(trades)

    assert attribution["complete"] is True
    assert attribution["gross_alpha_pnl"] == pytest.approx(10.0)
    assert attribution["net_pnl"] == pytest.approx(net_pnl)
    assert attribution["reconciliation_error"] == pytest.approx(0.0)


def test_performance_and_benchmark_metrics_are_annualized_and_auditable() -> None:
    curve = [
        ("2024-01-01T01:00:00+00:00", 1_010.0),
        ("2024-01-01T02:00:00+00:00", 1_005.0),
        ("2024-01-01T03:00:00+00:00", 1_020.0),
    ]
    trades = [
        {
            "pnl": 20.0,
            "quantity": 1.0,
            "entry_price": 100.0,
            "exit_price": 120.0,
            "exit_time": curve[-1][0],
            "reason": "signal",
            "metadata": {
                "simulation": {
                    "holding_bars": 2,
                    "entry_reference_price": 100.0,
                    "exit_reference_price": 120.0,
                }
            },
        }
    ]

    metrics = calculate_performance_metrics(
        curve,
        initial_capital=1_000.0,
        timeframe_delta=timedelta(hours=1),
        trades=trades,
    )
    benchmark = fixed_allocation_buy_and_hold(
        [101.0, 105.0, 110.0],
        entry_reference_price=100.0,
        initial_capital=1_000.0,
        allocation_pct=0.25,
        commission_rate=0.001,
        slippage_rate=0.0005,
        timeframe_delta=timedelta(hours=1),
    )

    assert metrics["total_return_pct"] == pytest.approx(2.0)
    assert metrics["total_trades"] == 1
    assert metrics["time_in_market_pct"] == pytest.approx(200 / 3)
    assert metrics["exit_reason_counts"] == {"signal": 1}
    assert benchmark["name"] == "fixed_allocation_buy_and_hold"
    assert benchmark["allocation_pct"] == pytest.approx(25.0)
    assert benchmark["commission"] > 0
    assert benchmark["slippage"] > 0
    assert calendar_returns(curve, initial_capital=1_000.0)["2024"] == pytest.approx(
        2.0
    )
