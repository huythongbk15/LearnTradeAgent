"""Property-based tests (audit Phase 4: hypothesis).

Contract invariants that hold for *any* valid input — these catch
regressions the hand-written cases miss.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol

pytest_importorskip = __import__("pytest").importorskip("ccxt")


def make_adapter() -> CCXTAdapter:
    from trading_agent.exchanges.ccxt_adapter import ExchangeConfig

    return CCXTAdapter(ExchangeConfig(id="binance", name="Binance", rate_limit=0))


BASE = st.from_regex(r"^[A-Z0-9]{2,8}$", fullmatch=True)


@given(base=BASE, quote=BASE)
@settings(max_examples=200, deadline=None)
def test_unified_symbol_roundtrip(base: str, quote: str) -> None:
    """Symbol(pair) -> ccxt symbol -> Symbol(pair) is lossless for spot."""
    adapter = make_adapter()
    symbol = Symbol(
        base=base,
        quote=quote,
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        exchange="binance",
    )
    ex = adapter.get_exchange_symbol(symbol)
    assert ex == f"{base.upper()}/{quote.upper()}"


@given(
    base=BASE,
    quote=BASE,
    market_type=st.sampled_from([MarketType.FUTURES, MarketType.PERPETUAL]),
)
@settings(max_examples=200, deadline=None)
def test_futures_symbol_uses_settle_suffix(
    base: str, quote: str, market_type: MarketType
) -> None:
    """Futures symbols carry the :SETTLE suffix in ccxt format."""
    adapter = make_adapter()
    symbol = Symbol(
        base=base,
        quote=quote,
        asset_class=AssetClass.CRYPTO,
        market_type=market_type,
        exchange="binance",
    )
    ex = adapter.get_exchange_symbol(symbol)
    assert ex.endswith(f"/{quote.upper()}:{quote.upper()}")


@given(
    ts=st.integers(min_value=0, max_value=4_000_000_000_000),
    price=st.floats(
        min_value=1e-8, max_value=1e8, allow_nan=False, allow_infinity=False
    ),
    volume=st.floats(
        min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=200, deadline=None)
def test_candle_parse_preserves_values(ts: int, price: float, volume: float) -> None:
    """Every ccxt candle field survives parsing losslessly."""
    from datetime import UTC, datetime

    from trading_agent.exchanges.ccxt_adapter import ExchangeConfig

    adapter = CCXTAdapter(ExchangeConfig(id="binance", name="Binance", rate_limit=0))
    symbol = Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")
    candle = adapter._parse_candle(
        [ts, price, price, price, price, volume], symbol, "1h"
    )
    assert candle.timestamp == datetime.fromtimestamp(ts / 1000, tz=UTC)
    assert candle.open == price
    assert candle.high == price
    assert candle.low == price
    assert candle.close == price
    assert candle.volume == volume
