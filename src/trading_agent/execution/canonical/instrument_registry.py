"""Deterministic instrument rules for the supported 1h spot universe.

The registry is intentionally explicit and fail-closed. It provides stable
fallback constraints for research and paper execution; a live venue adapter
should still validate these values against fresh exchange metadata before
submitting an order.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from trading_agent.execution.canonical.order_planner import InstrumentRules


TEN_PAIR_1H_SYMBOLS: Final[tuple[str, ...]] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ZEC/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "ADA/USDT",
    "NEAR/USDT",
)

_MIN_NOTIONAL: Final = 10.0
_MAX_NOTIONAL: Final = 1_000_000.0

# Quantity grids and price precisions match the granularity needed by the
# supported USDT spot pairs. max_order_qty is a finite system ceiling; the
# common max_notional cap remains the tighter guard for normal operation.
_RULE_SPECS: Final[dict[str, tuple[float, float, int]]] = {
    "BTC/USDT": (0.00001, 100.0, 2),
    "ETH/USDT": (0.0001, 1_000.0, 2),
    "SOL/USDT": (0.001, 100_000.0, 2),
    "XRP/USDT": (0.1, 10_000_000.0, 4),
    "BNB/USDT": (0.001, 10_000.0, 2),
    "ZEC/USDT": (0.001, 100_000.0, 2),
    "DOGE/USDT": (1.0, 100_000_000.0, 5),
    "TRX/USDT": (0.1, 100_000_000.0, 5),
    "ADA/USDT": (0.1, 10_000_000.0, 4),
    "NEAR/USDT": (0.01, 1_000_000.0, 3),
}


def _build_registry() -> dict[str, InstrumentRules]:
    return {
        symbol: InstrumentRules(
            symbol=symbol,
            asset_class="spot",
            min_order_qty=qty_step,
            max_order_qty=max_order_qty,
            qty_step=qty_step,
            price_precision=price_precision,
            spot_long_only=True,
            max_leverage=1.0,
            min_notional=_MIN_NOTIONAL,
            max_notional=_MAX_NOTIONAL,
        )
        for symbol, (qty_step, max_order_qty, price_precision) in _RULE_SPECS.items()
    }


INSTRUMENT_RULES_1H: Final[Mapping[str, InstrumentRules]] = MappingProxyType(
    _build_registry()
)


class UnsupportedInstrumentError(LookupError):
    """Raised when no reviewed rule set exists for a requested instrument."""


def get_instrument_rules(symbol: str) -> InstrumentRules:
    """Return reviewed rules for *symbol*, rejecting unregistered instruments."""

    normalized = symbol.strip().upper()
    try:
        return INSTRUMENT_RULES_1H[normalized]
    except KeyError as exc:
        supported = ", ".join(TEN_PAIR_1H_SYMBOLS)
        raise UnsupportedInstrumentError(
            f"Unsupported instrument {symbol!r}; supported instruments: {supported}"
        ) from exc


__all__ = [
    "INSTRUMENT_RULES_1H",
    "TEN_PAIR_1H_SYMBOLS",
    "UnsupportedInstrumentError",
    "get_instrument_rules",
]
