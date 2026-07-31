"""Trading Exchanges Package - Unified Exchange Interface"""

from trading.exchanges.ccxt_adapter import (
    CCXTAdapter,
    MultiExchangeManager,
    ExchangeConfig,
    RateLimitManager,
    get_default_exchange_configs,
    create_multi_exchange_manager,
)
from trading.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig, create_alpaca_adapter
from trading.exchanges.oanda_adapter import OANDAAdapter, OANDAConfig, create_oanda_adapter
from trading.exchanges.order_router import (
    OrderRouter,
    RoutingStrategy,
    BestPriceRouter,
    ExecutionPlan,
    VenueQuote,
)
from trading.exchanges.models import (
    Symbol, AssetClass, MarketType,
    OrderSide, OrderType, OrderStatus, TimeInForce,
    Order, Position, Balance, Ticker, OrderBook, OrderBookLevel,
    Trade, Candle, AccountBalance,
    crypto_symbol, stock_symbol, forex_symbol, futures_symbol, option_symbol,
    COMMON_CRYPTO, COMMON_STOCKS, COMMON_FOREX,
)

__all__ = [
    # CCXT
    'CCXTAdapter', 'MultiExchangeManager', 'ExchangeConfig', 'RateLimitManager',
    'get_default_exchange_configs', 'create_multi_exchange_manager',
    # Alpaca (US Stocks)
    'AlpacaAdapter', 'AlpacaConfig', 'create_alpaca_adapter',
    # OANDA (Forex)
    'OANDAAdapter', 'OANDAConfig', 'create_oanda_adapter',
    # Order Router
    'OrderRouter', 'RoutingStrategy', 'BestPriceRouter', 'ExecutionPlan', 'VenueQuote',
    # Models
    'Symbol', 'AssetClass', 'MarketType',
    'OrderSide', 'OrderType', 'OrderStatus', 'TimeInForce',
    'Order', 'Position', 'Balance', 'Ticker', 'OrderBook', 'OrderBookLevel',
    'Trade', 'Candle', 'AccountBalance',
    'crypto_symbol', 'stock_symbol', 'forex_symbol', 'futures_symbol', 'option_symbol',
    'COMMON_CRYPTO', 'COMMON_STOCKS', 'COMMON_FOREX',
]