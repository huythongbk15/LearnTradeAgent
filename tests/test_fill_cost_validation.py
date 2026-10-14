import pytest

from trading_agent.execution.backtest_sim.models import decompose_fill_cost


@pytest.mark.parametrize(
    "is_buy,rate,limit,fill", [(True, 0.01, 100.5, 100.5), (False, -0.01, 99.5, 99.5)]
)
def test_limit_cap_credit_is_explicit_not_hidden_as_impact(is_buy, rate, limit, fill):
    costs = decompose_fill_cost(
        quantity=2.0,
        mid_price=100.0,
        side_price=100.0,
        slippage_rate=0.0,
        impact_rate=rate,
        fill_price=fill,
        fee_rate=0.001,
        is_buy=is_buy,
        limit_price=limit,
    )
    assert costs["impact_cost"] == pytest.approx(2.0)
    assert costs["price_cap_credit"] == pytest.approx(-1.0)
    assert sum(
        costs[key]
        for key in ("spread_cost", "slippage_cost", "impact_cost", "price_cap_credit")
    ) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", float("nan")),
        ("mid_price", float("inf")),
        ("slippage_rate", float("nan")),
        ("impact_rate", None),
        ("fee_rate", True),
        ("is_buy", "sell"),
        ("fill_price", 101.0),
    ],
)
def test_invalid_or_unreconciled_cost_chain_is_rejected(field, value):
    values = dict(
        quantity=1.0,
        mid_price=100.0,
        side_price=100.0,
        slippage_rate=0.0,
        impact_rate=0.0,
        fill_price=100.0,
        fee_rate=0.001,
        is_buy=True,
    )
    values[field] = value
    with pytest.raises(ValueError):
        decompose_fill_cost(**values)
