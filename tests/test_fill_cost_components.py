"""Measured cost per fill.

Every fill records what it charged, split into spread, slippage, impact and
commission, rather than one opaque fee field. The point is that a campaign
can reconcile measured cost against its declared schedule: the components
reconstruct the difference between the mid and the price actually filled at.

The execution components are signed. A maker resting inside the spread is
paid, so its spread component is negative; forcing the components positive
would overstate maker cost and break the invariant these tests check.

Run: .venv/bin/python -m pytest tests/test_fill_cost_components.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from trading_agent.execution.backtest_sim.models import (
    FillModel,
    ImpactModel,
    decompose_fill_cost,
)


# ── the invariant ────────────────────────────────────────────────────────

def _assert_invariant(**kwargs) -> dict:
    out = decompose_fill_cost(**kwargs)
    execution = out["spread_cost"] + out["slippage_cost"] + out["impact_cost"]
    expected = (
        (kwargs["fill_price"] - kwargs["mid_price"])
        * kwargs["quantity"]
        * (1.0 if kwargs["is_buy"] else -1.0)
    )
    assert execution == pytest.approx(expected, abs=1e-9)
    assert out["commission"] == pytest.approx(
        kwargs["fill_price"] * kwargs["quantity"] * kwargs["fee_rate"]
    )
    return out


def test_taker_buy_reconstructs_the_mid_to_fill_gap():
    mid, ask = 100.0, 100.02
    slip, impact = 0.001, 0.0005
    fill = ask * (1 + slip) * (1 + impact)
    out = _assert_invariant(
        quantity=10.0, mid_price=mid, side_price=ask,
        slippage_rate=slip, impact_rate=impact,
        fill_price=fill, fee_rate=0.0005, is_buy=True
    )
    assert out["spread_cost"] > 0
    assert out["slippage_cost"] > 0
    assert out["impact_cost"] > 0
    assert out["commission"] > 0


def test_taker_sell_reconstructs_the_mid_to_fill_gap():
    mid, bid = 100.0, 99.98
    slip, impact = 0.001, 0.0005
    fill = bid * (1 - slip) * (1 - impact)
    out = _assert_invariant(
        quantity=10.0, mid_price=mid, side_price=bid,
        slippage_rate=-slip, impact_rate=-impact,
        fill_price=fill, fee_rate=0.0005, is_buy=False
    )
    assert out["spread_cost"] > 0  # a seller crossing down from mid pays
    assert out["slippage_cost"] > 0


def test_maker_inside_the_spread_is_paid_not_charged():
    # A maker resting inside the spread fills better than mid for a buy.
    mid, limit = 100.0, 99.99
    impact = 0.0005
    fill = limit * (1 - impact)
    out = _assert_invariant(
        quantity=10.0, mid_price=mid, side_price=limit,
        slippage_rate=0.0, impact_rate=-impact,
        fill_price=fill, fee_rate=0.0002, is_buy=True
    )
    assert out["spread_cost"] < 0, "maker capture of the spread is a credit"
    assert out["impact_cost"] < 0


def test_zero_spread_book_costs_only_slippage_and_commission():
    mid = side = 100.0
    slip, impact = 0.002, 0.0
    fill = side * (1 + slip)
    out = _assert_invariant(
        quantity=5.0, mid_price=mid, side_price=side,
        slippage_rate=slip, impact_rate=impact,
        fill_price=fill, fee_rate=0.0005, is_buy=True
    )
    assert out["spread_cost"] == pytest.approx(0.0)
    assert out["slippage_cost"] > 0


# ── a missing term is an error, not a free component ─────────────────────

@pytest.mark.parametrize(
    "bad", [
        {"quantity": 0.0},
        {"quantity": -1.0},
        {"mid_price": 0.0},
        {"fill_price": 0.0},
        {"fee_rate": -0.0005},
    ],
)
def test_invalid_input_raises_rather_than_defaulting(bad):
    base = dict(
        quantity=1.0, mid_price=100.0, side_price=100.02,
        slippage_rate=0.001, impact_rate=0.0,
        fill_price=100.03, fee_rate=0.0005, is_buy=True
    )
    base.update(bad)
    with pytest.raises(ValueError):
        decompose_fill_cost(**base)


# ── components survive onto the fill record ─────────────────────────────

def test_fill_records_each_component():
    from trading_agent.execution.backtest_sim.backtest_integration import (
        run_simulator_backtest,
    )
    n = 400
    prices = 100 + np.cumsum(np.sin(np.arange(n) / 12) * 2)
    df = pl.DataFrame({
        "timestamp": [
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i)
            for i in range(n)
        ],
        "open": prices,
        "high": prices + 1,
        "low": prices - 1,
        "close": prices,
        "volume": [1000.0] * n,
    })
    from trading_agent.strategies.ma_crossover import MaCrossover

    result = run_simulator_backtest(
        strategy=MaCrossover(params={"fast_period": 5, "slow_period": 20}),
        df=df, symbol="BTC/USDT", timeframe="1h",
        initial_capital=100_000,
        simulator_config={
            "fill_model": FillModel.IMMEDIATE,
            "impact_model": ImpactModel.NONE,
            "maker_fee_bps": 5.0, "taker_fee_bps": 5.0,
            "base_slippage_bps": 2.0, "partial_fill_prob": 0.0,
        },
    )
    assert result.total_trades > 0, "no fills to attribute cost to"
    assert result.fills, "no fill records to attribute cost to"

    # Every fill carries all four components, and none is silently zero
    # unless the term was genuinely absent from that fill.
    for fill in result.fills:
        assert fill.commission > 0, "commission never omitted, always charged"
        assert fill.spread_cost != 0 or fill.mid_price_at_fill == 0
        assert fill.execution_cost == pytest.approx(
            fill.spread_cost + fill.slippage_cost + fill.impact_cost
        )

    # The declared schedule reconciles with what the fills charged, which is
    # the point of splitting them: commission is 5bps taker on each fill's
    # notional.
    charged = sum(f.commission for f in result.fills)
    assert charged == pytest.approx(result.total_fees, rel=1e-6)

    # Execution cost is positive for a taker crossing a spread.
    execution = sum(f.execution_cost for f in result.fills)
    assert execution > 0