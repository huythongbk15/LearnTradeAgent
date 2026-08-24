"""Binding and safety tests for the reviewed 10-pair instrument registry."""

from __future__ import annotations

import math

import pytest

from trading_agent.execution.canonical.instrument_registry import (
    INSTRUMENT_RULES_1H,
    TEN_PAIR_1H_SYMBOLS,
    UnsupportedInstrumentError,
    get_instrument_rules,
)


EXPECTED_BINDINGS = {
    "BTC/USDT": (0.00001, 2),
    "ETH/USDT": (0.0001, 2),
    "SOL/USDT": (0.001, 2),
    "XRP/USDT": (0.1, 4),
    "BNB/USDT": (0.001, 2),
    "ZEC/USDT": (0.001, 2),
    "DOGE/USDT": (1.0, 5),
    "TRX/USDT": (0.1, 5),
    "ADA/USDT": (0.1, 4),
    "NEAR/USDT": (0.01, 3),
}


def test_registry_binds_exactly_the_reviewed_10_pair_universe() -> None:
    assert TEN_PAIR_1H_SYMBOLS == tuple(EXPECTED_BINDINGS)
    assert tuple(INSTRUMENT_RULES_1H) == TEN_PAIR_1H_SYMBOLS
    assert len(INSTRUMENT_RULES_1H) == 10


@pytest.mark.parametrize(("symbol", "expected"), EXPECTED_BINDINGS.items())
def test_each_symbol_has_its_reviewed_quantity_and_price_grid(
    symbol: str, expected: tuple[float, int]
) -> None:
    expected_qty_step, expected_price_precision = expected
    rules = get_instrument_rules(symbol)

    assert rules.symbol == symbol
    assert rules.qty_step == expected_qty_step
    assert rules.min_order_qty == expected_qty_step
    assert rules.price_precision == expected_price_precision
    assert rules.asset_class == "spot"
    assert rules.spot_long_only is True
    assert rules.max_leverage == 1.0


@pytest.mark.parametrize("symbol", TEN_PAIR_1H_SYMBOLS)
def test_every_numeric_constraint_is_finite_and_ordered(symbol: str) -> None:
    rules = get_instrument_rules(symbol)
    numeric_constraints = (
        rules.min_order_qty,
        rules.max_order_qty,
        rules.qty_step,
        rules.max_leverage,
        rules.min_notional,
        rules.max_notional,
    )

    assert all(
        value is not None and math.isfinite(value) for value in numeric_constraints
    )
    assert 0.0 < rules.min_order_qty <= rules.max_order_qty
    assert rules.min_order_qty == rules.qty_step
    assert 0.0 < rules.min_notional <= rules.max_notional
    assert rules.price_precision >= 0


def test_lookup_normalizes_case_and_surrounding_whitespace() -> None:
    assert get_instrument_rules("  btc/usdt ") is INSTRUMENT_RULES_1H["BTC/USDT"]


@pytest.mark.parametrize("symbol", ["LTC/USDT", "BTC/USDT:USDT", "BTCUSDT", ""])
def test_lookup_fails_closed_for_unreviewed_or_ambiguous_symbols(symbol: str) -> None:
    with pytest.raises(UnsupportedInstrumentError, match="Unsupported instrument"):
        get_instrument_rules(symbol)


def test_public_registry_is_immutable() -> None:
    with pytest.raises(TypeError):
        INSTRUMENT_RULES_1H["BTC/USDT"] = get_instrument_rules("BTC/USDT")  # type: ignore[index]
