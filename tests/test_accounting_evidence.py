from copy import deepcopy

import pytest

from trading_agent.backtest.accounting_evidence import export_closed_trade_accounting


def test_maker_credit_is_preserved_and_reconciled():
    row = trade()
    row["entry_price"] = 99.9
    row["pnl"] = 18.4
    row["metadata"]["simulation"]["execution_components"]["entry"] = {
        "slippage": 0.0,
        "spread": -0.1,
        "market_impact": -0.1,
        "price_cap_credit": 0.0,
    }
    actual = export_closed_trade_accounting(
        [row], equity_delta=18.4, open_inventory=False
    )
    assert actual["spread_cost"] == pytest.approx(0.2)
    assert actual["market_impact"] == pytest.approx(0.1)
    assert actual["net_pnl"] == pytest.approx(18.4)


def trade(side="buy"):
    direction = 1 if side == "buy" else -1
    return {
        "trade_id": "one",
        "side": side,
        "quantity": 2.0,
        "entry_price": 100.0 + direction * 0.3,
        "exit_price": 110.0 - direction * 0.4,
        "entry_fee": 0.5,
        "exit_fee": 0.5,
        "pnl": direction * 20.0 - 2.4,
        "metadata": {
            "simulation": {
                "entry_reference_price": 100.0,
                "exit_reference_price": 110.0,
                "execution_components": {
                    "entry": {
                        "slippage": 0.2,
                        "spread": 0.3,
                        "market_impact": 0.1,
                        "price_cap_credit": 0.0,
                    },
                    "exit": {
                        "slippage": 0.3,
                        "spread": 0.3,
                        "market_impact": 0.2,
                        "price_cap_credit": 0.0,
                    },
                },
            }
        },
    }


def test_cap_credit_survives_accounting_export():
    row = trade()
    row["entry_price"] = 100.2
    row["pnl"] = 17.8
    row["metadata"]["simulation"]["execution_components"]["entry"][
        "price_cap_credit"
    ] = -0.2
    actual = export_closed_trade_accounting(
        [row], equity_delta=17.8, open_inventory=False
    )
    assert actual["price_cap_credit"] == pytest.approx(-0.2)
    assert actual["gross_pnl"] - sum(
        actual[key]
        for key in (
            "fees",
            "slippage",
            "spread_cost",
            "market_impact",
            "price_cap_credit",
        )
    ) == pytest.approx(actual["net_pnl"])


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_measured_components_reconcile_with_independent_equity(side):
    row = trade(side)
    result = export_closed_trade_accounting(
        [row], equity_delta=row["pnl"], open_inventory=False
    )
    assert result["gross_pnl"] == pytest.approx(20.0 if side == "buy" else -20.0)
    assert result["slippage"] == pytest.approx(0.5)
    assert result["spread_cost"] == pytest.approx(0.6)
    assert result["market_impact"] == pytest.approx(0.3)
    assert result["fees"] == 1.0
    assert result["strategy_qualified"] is False


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "nan",
        "wrong_drag",
        "wrong_net",
        "wrong_equity",
        "duplicate",
        "inventory",
        "boundary",
    ],
)
def test_no_export_for_unproven_accounting(case):
    row = deepcopy(trade())
    equity = row["pnl"]
    rows = [row]
    if case == "missing":
        del row["metadata"]["simulation"]["execution_components"]["entry"]["spread"]
    elif case == "nan":
        row["entry_fee"] = float("nan")
    elif case == "wrong_drag":
        row["metadata"]["simulation"]["execution_components"]["exit"]["slippage"] += 1
    elif case == "wrong_net":
        row["pnl"] += 1
    elif case == "wrong_equity":
        equity += 1
    elif case == "duplicate":
        rows.append(deepcopy(row))
    elif case == "boundary":
        row["measurement_attribution"] = "marked_at_window_open"
    with pytest.raises(ValueError):
        export_closed_trade_accounting(
            rows, equity_delta=equity, open_inventory=case == "inventory"
        )
