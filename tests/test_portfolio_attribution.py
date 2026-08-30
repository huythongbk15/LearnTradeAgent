"""Deterministic S6-0608 attribution contracts."""

from datetime import UTC, datetime

import pytest

from trading_agent.backtest.portfolio_backtest import (
    PortfolioBacktestResult,
    SimFill,
    _build_attribution,
)


def test_strategy_pair_regime_factor_and_cost_attribution_reconciles() -> None:
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    fills = [
        SimFill(
            idempotency_key="buy-1",
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            price=100.1,
            fee=0.2,
            slippage_cost=0.1,
            notional=100.1,
            decision_time=timestamp,
            fill_time=timestamp,
            strategy_id="ma_adx",
            regime="trend",
            factor_exposures=(("momentum", 2.0), ("value", 1.0)),
        )
    ]

    records = _build_attribution(fills, {"BTC/USDT": 110.0})

    assert len(records) == 1
    record = records[0]
    assert (record.strategy_id, record.symbol, record.regime) == (
        "ma_adx",
        "BTC/USDT",
        "trend",
    )
    assert record.gross_pnl == pytest.approx(10.0)
    assert record.net_pnl == pytest.approx(9.7)
    assert record.fees == pytest.approx(0.2)
    assert record.slippage_cost == pytest.approx(0.1)
    assert sum(value for _name, value in record.factor_contributions) == pytest.approx(
        record.net_pnl
    )

    result = PortfolioBacktestResult(100.0, 109.7, attribution=records)
    result.attribution_by_strategy = {"ma_adx": record.net_pnl}
    result.attribution_by_regime = {"trend": record.net_pnl}
    result.attribution_by_factor = dict(record.factor_contributions)
    result.execution_cost_by_strategy = {"ma_adx": 0.3}
    payload = result.to_dict()
    assert payload["attribution"][0]["strategy_id"] == "ma_adx"
    assert payload["execution_cost_by_strategy"]["ma_adx"] == pytest.approx(0.3)
