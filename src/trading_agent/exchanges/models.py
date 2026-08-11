"""
Unified Data Model for Multi-Asset Trading System

Core types: Symbol, AssetClass, MarketType, Bar, OrderBook, Trade, Position, Order
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import hashlib


class AssetClass(str, Enum):
    """Asset classification"""
    CRYPTO = "crypto"
    STOCK = "stock"
    FOREX = "forex"
    FUTURES = "futures"
    OPTIONS = "options"
    ETF = "etf"
    BOND = "bond"
    COMMODITY = "commodity"
    INDEX = "index"
    UNKNOWN = "unknown"


class MarketType(str, Enum):
    """Market type"""
    SPOT = "spot"
    MARGIN = "margin"
    FUTURES = "futures"
    OPTIONS = "options"
    PERPETUAL = "perpetual"
    SPOT_MARGIN = "spot_margin"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    TRAILING_STOP = "trailing_stop"
    POST_ONLY = "post_only"
    FOK = "fok"  # Fill or Kill
    IOC = "ioc"  # Immediate or Cancel


class OrderStatus(str, Enum):
    UNKNOWN = "unknown"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    GTC = "gtc"  # Good Till Cancelled
    IOC = "ioc"
    FOK = "fok"
    GTD = "gtd"  # Good Till Date


class OrderConstraintError(ValueError):
    """Deterministic local exchange-filter rejection for an order amount."""

    def __init__(self, message: str, *, constraint: str):
        super().__init__(message)
        self.constraint = constraint


@dataclass(frozen=True, slots=True)
class Symbol:
    """
    Unified symbol representation across all asset classes and exchanges.

    Examples:
        - Crypto: Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")
        - Stock: Symbol("AAPL", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca")
        - Forex: Symbol("EUR", "USD", AssetClass.FOREX, MarketType.SPOT, "oanda")
        - Futures: Symbol("BTC", "USDT", AssetClass.FUTURES, MarketType.FUTURES, "binance", "2024-12-27")
    """
    base: str
    quote: str
    asset_class: AssetClass
    market_type: MarketType
    exchange: str
    expiry: Optional[str] = None  # For futures/options: "2024-12-27" or "2024-12"
    strike: Optional[Decimal] = None  # For options
    option_type: Optional[str] = None  # "call" or "put"

    def __post_init__(self):
        object.__setattr__(self, 'base', self.base.upper())
        object.__setattr__(self, 'quote', self.quote.upper())
        object.__setattr__(self, 'exchange', self.exchange.lower())

    @property
    def pair(self) -> str:
        """Standard pair notation: BASE/QUOTE"""
        return f"{self.base}/{self.quote}"

    @property
    def ccxt_symbol(self) -> str:
        """CCXT format: BASE/QUOTE:SETTLE for futures"""
        if self.market_type in (MarketType.FUTURES, MarketType.PERPETUAL):
            settle = self.quote if self.asset_class == AssetClass.CRYPTO else "USD"
            return f"{self.base}/{self.quote}:{settle}"
        return self.pair

    @property
    def alpaca_symbol(self) -> str:
        """Alpaca format for stocks"""
        if self.asset_class == AssetClass.STOCK:
            return self.base
        return self.pair

    @property
    def oanda_instrument(self) -> str:
        """OANDA format for forex"""
        if self.asset_class == AssetClass.FOREX:
            return f"{self.base}_{self.quote}"
        return self.pair

    @property
    def unified_id(self) -> str:
        """Unique identifier across all exchanges"""
        parts = [self.exchange, self.asset_class.value, self.market_type.value, self.base, self.quote]
        if self.expiry:
            parts.append(self.expiry)
        if self.strike:
            parts.append(str(self.strike))
        if self.option_type:
            parts.append(self.option_type)
        return ":".join(parts)

    @property
    def hash(self) -> str:
        """Short hash for database keys"""
        return hashlib.md5(self.unified_id.encode()).hexdigest()[:12]

    def __str__(self) -> str:
        if self.expiry:
            return f"{self.exchange}:{self.pair}:{self.market_type.value}:{self.expiry}"
        return f"{self.exchange}:{self.pair}:{self.market_type.value}"

    def __hash__(self) -> int:
        return hash(self.unified_id)


@dataclass(slots=True)
class Bar:
    """OHLCV bar / candlestick"""
    symbol: Symbol
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timeframe: str  # "1m", "5m", "1h", "1d", etc.
    trades: Optional[int] = None
    vwap: Optional[Decimal] = None

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / 3

    @property
    def range_pct(self) -> Decimal:
        return (self.high - self.low) / self.open * 100 if self.open else Decimal(0)

    def to_dict(self) -> dict:
        return {
            "symbol": str(self.symbol),
            "timestamp": self.timestamp.isoformat(),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "timeframe": self.timeframe,
            "trades": self.trades,
            "vwap": str(self.vwap) if self.vwap else None,
        }


@dataclass(slots=True)
class OrderBookLevel:
    """Single level in order book"""
    price: Decimal
    size: Decimal
    orders: int = 1


@dataclass(slots=True)
class OrderBook:
    """Order book snapshot"""
    symbol: Symbol
    timestamp: datetime
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)
    sequence: Optional[int] = None
    # P0.3 trusted-time fields: local monotonic instants around the request.
    request_started_at: Optional[float] = None
    received_at: Optional[float] = None

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[Decimal]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_pct(self) -> Optional[Decimal]:
        if self.spread and self.best_bid:
            return self.spread / self.best_bid * 100
        return None

    @property
    def mid_price(self) -> Optional[Decimal]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None


@dataclass(slots=True)
class Trade:
    """Individual trade / fill"""
    symbol: Symbol
    timestamp: datetime
    side: OrderSide
    price: Decimal
    size: Decimal
    trade_id: str
    order_id: Optional[str] = None
    fee: Decimal = Decimal(0)
    fee_currency: str = ""
    is_maker: bool = False


@dataclass(slots=True)
class Position:
    """Current position"""
    symbol: Symbol
    size: Decimal  # Positive = long, Negative = short
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    leverage: Decimal = Decimal(1)
    margin_used: Decimal = Decimal(0)
    liquidation_price: Optional[Decimal] = None
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def is_long(self) -> bool:
        return self.size > 0

    @property
    def is_short(self) -> bool:
        return self.size < 0

    @property
    def notional(self) -> Decimal:
        return abs(self.size) * self.mark_price

    @property
    def pnl_pct(self) -> Decimal:
        if self.entry_price == 0:
            return Decimal(0)
        if self.is_long:
            return (self.mark_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - self.mark_price) / self.entry_price * 100


@dataclass(slots=True)
class Order:
    """Order request / tracking"""
    id: str
    symbol: Symbol
    side: OrderSide
    type: OrderType
    size: Decimal
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.OPEN
    filled_size: Decimal = Decimal(0)
    avg_fill_price: Decimal = Decimal(0)
    quote_cost: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    fee_currency: str = ""
    fee_breakdown: dict[str, Decimal] = field(default_factory=dict)
    trade_ids: tuple[str, ...] = ()
    raw_status: str = ""
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    client_order_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None

    @property
    def remaining_size(self) -> Decimal:
        return self.size - self.filled_size

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIAL)

    @property
    def is_done(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass(slots=True)
class AccountBalance:
    """Account balance per asset"""
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal
    exchange: str

    @property
    def used_pct(self) -> Decimal:
        if self.total == 0:
            return Decimal(0)
        return self.locked / self.total * 100


@dataclass(slots=True)
class Ticker:
    """Market ticker snapshot"""
    symbol: Symbol
    timestamp: datetime
    bid: Optional[Decimal] = None
    ask: Optional[Decimal] = None
    last: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    open: Optional[Decimal] = None
    close: Optional[Decimal] = None
    base_volume: Optional[Decimal] = None
    quote_volume: Optional[Decimal] = None
    change: Optional[Decimal] = None
    percentage: Optional[Decimal] = None
    info: dict = field(default_factory=dict)
    # P0.3 trusted-time fields: local monotonic instants around the request.
    request_started_at: Optional[float] = None
    received_at: Optional[float] = None

    @property
    def mid(self) -> Optional[Decimal]:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.last

    @property
    def spread(self) -> Optional[Decimal]:
        if self.bid and self.ask:
            return self.ask - self.bid
        return None

    @property
    def spread_pct(self) -> Optional[Decimal]:
        if self.spread and self.mid:
            return self.spread / self.mid * 100
        return None


@dataclass(slots=True)
class Balance:
    """Account balance by asset class"""
    asset_class: AssetClass
    assets: dict[str, dict] = field(default_factory=dict)  # currency -> {free, used, total}

    def get_total_usd(self, prices: dict[str, Decimal]) -> Decimal:
        """Calculate total value in USD"""
        total = Decimal(0)
        for currency, amounts in self.assets.items():
            price = prices.get(currency, Decimal(1) if currency in ('USDT', 'USDC', 'USD') else Decimal(0))
            total += Decimal(amounts['total']) * price
        return total


@dataclass(slots=True)
class Candle:
    """OHLCV candle"""
    symbol: Symbol
    timestamp: datetime
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trades: Optional[int] = None

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / 3

    @property
    def range_pct(self) -> Decimal:
        return (self.high - self.low) / self.open * 100 if self.open else Decimal(0)


@dataclass(slots=True)
class PoolInfo:
    """Liquidity pool information."""
    pool_address: str
    token0: Symbol
    token1: Symbol
    fee_tier: int  # basis points (e.g., 3000 = 0.3%)
    liquidity: Decimal
    sqrt_price_x96: int
    tick: int


@dataclass(slots=True)
class SwapQuote:
    """Quote for a swap."""
    token_in: Symbol
    token_out: Symbol
    amount_in: Decimal
    amount_out: Decimal
    amount_out_min: Decimal  # with slippage
    price_impact_pct: Decimal
    gas_estimate: int
    route: list[str]  # pool addresses in route
    timestamp: datetime


# Factory functions for common symbols
def crypto_symbol(base: str, quote: str = "USDT", exchange: str = "binance",
                  market_type: MarketType = MarketType.SPOT) -> Symbol:
    return Symbol(base, quote, AssetClass.CRYPTO, market_type, exchange)


def stock_symbol(ticker: str, exchange: str = "alpaca") -> Symbol:
    return Symbol(ticker, "USD", AssetClass.STOCK, MarketType.SPOT, exchange)


def forex_symbol(base: str, quote: str, exchange: str = "oanda") -> Symbol:
    return Symbol(base, quote, AssetClass.FOREX, MarketType.SPOT, exchange)


def futures_symbol(base: str, quote: str, expiry: str, exchange: str = "binance") -> Symbol:
    return Symbol(base, quote, AssetClass.FUTURES, MarketType.FUTURES, exchange, expiry=expiry)


def option_symbol(base: str, quote: str, expiry: str, strike: Decimal, option_type: str,
                  exchange: str = "deribit") -> Symbol:
    return Symbol(base, quote, AssetClass.OPTIONS, MarketType.OPTIONS, exchange,
                  expiry=expiry, strike=strike, option_type=option_type)


# Common symbol registry
COMMON_CRYPTO = {
    "BTC/USDT": crypto_symbol("BTC", "USDT"),
    "ETH/USDT": crypto_symbol("ETH", "USDT"),
    "SOL/USDT": crypto_symbol("SOL", "USDT"),
    "BTC/USDT:USDT": crypto_symbol("BTC", "USDT", market_type=MarketType.PERPETUAL),
    "ETH/USDT:USDT": crypto_symbol("ETH", "USDT", market_type=MarketType.PERPETUAL),
}

COMMON_STOCKS = {
    "AAPL": stock_symbol("AAPL"),
    "MSFT": stock_symbol("MSFT"),
    "GOOGL": stock_symbol("GOOGL"),
    "TSLA": stock_symbol("TSLA"),
    "NVDA": stock_symbol("NVDA"),
    "SPY": stock_symbol("SPY"),
    "QQQ": stock_symbol("QQQ"),
}

COMMON_FOREX = {
    "EUR/USD": forex_symbol("EUR", "USD"),
    "GBP/USD": forex_symbol("GBP", "USD"),
    "USD/JPY": forex_symbol("USD", "JPY"),
    "USD/CHF": forex_symbol("USD", "CHF"),
    "AUD/USD": forex_symbol("AUD", "USD"),
    "USD/CAD": forex_symbol("USD", "CAD"),
}
