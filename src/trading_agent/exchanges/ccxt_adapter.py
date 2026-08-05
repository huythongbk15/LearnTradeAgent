"""
CCXT Unified Adapter - Multi-Exchange Trading Interface

Provides unified interface for multiple exchanges via CCXT.
Supports: Binance, Bybit, OKX, Coinbase, Kraken, Gate.io, KuCoin, HTX, etc.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from collections import defaultdict

try:
    import ccxt
    import ccxt.pro as ccxtpro
    from ccxt.base.errors import (
        AuthenticationError,
        InsufficientFunds, InvalidOrder
    )
except ImportError:
    # Optional SDK — adapter stays importable without ccxt (e.g. light env/CI).
    # Runtime methods raise a clear error via CCXTAdapter.__init__.
    ccxt = None  # type: ignore[assignment]
    ccxtpro = None  # type: ignore[assignment]
    AuthenticationError = None  # type: ignore[assignment,misc]
    InsufficientFunds = None  # type: ignore[assignment,misc]
    InvalidOrder = None  # type: ignore[assignment,misc]

from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, OrderSide, OrderType,
    OrderStatus, TimeInForce, Order, Position, Balance,
    Ticker, OrderBook, OrderBookLevel, Candle
)

logger = logging.getLogger(__name__)


class ExchangeStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    MAINTENANCE = "maintenance"


@dataclass
class ExchangeConfig:
    """Configuration for a single exchange"""
    id: str  # ccxt exchange id
    name: str  # human readable name
    api_key: str = ""
    secret: str = ""
    password: str = ""  # for exchanges requiring passphrase
    sandbox: bool = False
    testnet: bool = False
    rate_limit: int = 1200  # ms between requests
    enable_rate_limit: bool = True
    timeout: int = 30000  # ms
    options: dict = field(default_factory=dict)
    markets: list[MarketType] = field(default_factory=lambda: [MarketType.SPOT])
    enabled: bool = True

    def to_ccxt_config(self) -> dict:
        config = {
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.password,
            'enableRateLimit': self.enable_rate_limit,
            'rateLimit': self.rate_limit,
            'timeout': self.timeout,
            'options': self.options,
        }
        if self.sandbox:
            config['options']['defaultType'] = 'spot'
        return config


@dataclass
class RateLimitState:
    """Track rate limit state per exchange"""
    exchange_id: str
    remaining: int = 100
    reset_time: float = 0
    last_request: float = 0
    weight_used: int = 0


class RateLimitManager:
    """Token bucket rate limiter per exchange"""

    def __init__(self):
        self._limits: dict[str, RateLimitState] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get_state(self, exchange_id: str) -> RateLimitState:
        if exchange_id not in self._limits:
            self._limits[exchange_id] = RateLimitState(exchange_id=exchange_id)
        return self._limits[exchange_id]

    def get_lock(self, exchange_id: str) -> asyncio.Lock:
        return self._locks[exchange_id]

    async def acquire(self, exchange_id: str, weight: int = 1) -> None:
        """Acquire permission to make a request"""
        state = self.get_state(exchange_id)
        lock = self.get_lock(exchange_id)

        async with lock:
            now = asyncio.get_event_loop().time()

            # Reset if window expired
            if state.reset_time and now > state.reset_time:
                state.remaining = 100  # reset to default
                state.weight_used = 0

            # Wait if needed
            while state.remaining < weight:
                wait_time = max(0.1, (state.reset_time - now) if state.reset_time else 0.1)
                await asyncio.sleep(wait_time)
                now = asyncio.get_event_loop().time()
                if state.reset_time and now > state.reset_time:
                    state.remaining = 100
                    state.weight_used = 0

            state.remaining -= weight
            state.weight_used += weight
            state.last_request = now

    def update_from_headers(self, exchange_id: str, headers: dict) -> None:
        """Update rate limit state from response headers"""
        state = self.get_state(exchange_id)
        # CCXT handles this internally but we track for monitoring
        if 'x-ratelimit-remaining' in headers:
            state.remaining = int(headers['x-ratelimit-remaining'])
        if 'x-ratelimit-reset' in headers:
            state.reset_time = float(headers['x-ratelimit-reset']) / 1000


class ExchangeAdapter(ABC):
    """Abstract base for exchange adapters"""

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def fetch_markets(self) -> list[dict]: ...

    @abstractmethod
    async def fetch_ticker(self, symbol: Symbol) -> Ticker: ...

    @abstractmethod
    async def fetch_order_book(self, symbol: Symbol, limit: int = 100) -> OrderBook: ...

    @abstractmethod
    async def fetch_balance(self) -> dict[AssetClass, Balance]: ...

    @abstractmethod
    async def create_order(self, order: Order) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool: ...

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order: ...

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[Symbol] = None) -> list[Order]: ...

    @abstractmethod
    async def fetch_positions(self, symbol: Optional[Symbol] = None) -> list[Position]: ...

    @abstractmethod
    def get_status(self) -> ExchangeStatus: ...


class CCXTAdapter(ExchangeAdapter):
    """CCXT-based exchange adapter supporting spot, futures, options"""

    def __init__(self, config: ExchangeConfig):
        if ccxt is None:
            raise ImportError(
                "ccxt is not installed — run `pip install ccxt` (or `poetry add ccxt`) "
                "to use live exchange adapters. Data model and backtest layers work without it."
            )
        self.config = config
        self.exchange: ccxt.Exchange | None = None
        self.ws_exchange: ccxtpro.Exchange | None = None
        self._status = ExchangeStatus.DOWN
        self._markets: dict[str, dict] = {}
        self._symbol_map: dict[str, Symbol] = {}  # exchange symbol -> unified Symbol
        self._reverse_symbol_map: dict[Symbol, str] = {}  # unified -> exchange
        self._rate_limiter = RateLimitManager()
        self._connected = False

    async def connect(self) -> None:
        """Initialize CCXT exchange instance"""
        try:
            exchange_class = getattr(ccxt, self.config.id)
            self.exchange = exchange_class(self.config.to_ccxt_config())

            # Load markets
            await self.exchange.load_markets()
            self._markets = self.exchange.markets
            self._build_symbol_maps()

            # Test connection
            await self.exchange.fetch_time()
            self._status = ExchangeStatus.HEALTHY
            self._connected = True
            logger.info(f"Connected to {self.config.name} ({self.config.id})")

        except AuthenticationError:
            self._status = ExchangeStatus.DOWN
            logger.error(f"Authentication failed for {self.config.name}")
            raise
        except Exception as e:
            self._status = ExchangeStatus.DOWN
            logger.error(f"Failed to connect to {self.config.name}: {e}")
            raise

    async def disconnect(self) -> None:
        """Close connections"""
        if self.exchange:
            await self.exchange.close()
        if self.ws_exchange:
            await self.ws_exchange.close()
        self._connected = False
        self._status = ExchangeStatus.DOWN
        logger.info(f"Disconnected from {self.config.name}")

    def _build_symbol_maps(self) -> None:
        """Build unified symbol mappings"""
        self._symbol_map.clear()
        self._reverse_symbol_map.clear()

        for market in self._markets.values():
            if not market.get('active', True):
                continue

            unified = self._ccxt_to_unified_symbol(market)
            if unified:
                exchange_symbol = market['symbol']
                self._symbol_map[exchange_symbol] = unified
                self._reverse_symbol_map[unified] = exchange_symbol

    def _ccxt_to_unified_symbol(self, market: dict) -> Symbol | None:
        """Convert CCXT market to unified Symbol"""
        try:
            base = market['base']
            quote = market['quote']

            # Determine asset class
            if market.get('type') == 'spot':
                if quote in ('USDT', 'USDC', 'BUSD', 'DAI', 'FDUSD', 'TUSD'):
                    asset_class = AssetClass.CRYPTO
                elif quote in ('USD', 'EUR', 'GBP', 'JPY'):
                    asset_class = AssetClass.CRYPTO  # crypto/fiat pairs
                else:
                    asset_class = AssetClass.CRYPTO
            else:
                asset_class = AssetClass.CRYPTO

            # Determine market type
            market_type = MarketType.SPOT
            if market.get('future'):
                market_type = MarketType.FUTURES
            elif market.get('option'):
                market_type = MarketType.OPTIONS
            elif market.get('swap') or market.get('perpetual'):
                market_type = MarketType.PERPETUAL

            return Symbol(
                base=base,
                quote=quote,
                asset_class=asset_class,
                market_type=market_type,
                exchange=self.config.id
            )
        except Exception as e:
            logger.debug(f"Failed to parse market {market.get('symbol')}: {e}")
            return None

    def _unified_to_ccxt_symbol(self, symbol: Symbol) -> str:
        """Convert unified Symbol to exchange-specific symbol"""
        if symbol in self._reverse_symbol_map:
            return self._reverse_symbol_map[symbol]
        # Fallback: construct standard format
        return f"{symbol.base}/{symbol.quote}"

    async def fetch_markets(self) -> list[dict]:
        """Get all available markets"""
        return list(self._markets.values())

    def get_unified_symbol(self, exchange_symbol: str) -> Symbol | None:
        """Get unified symbol from exchange symbol"""
        return self._symbol_map.get(exchange_symbol)

    def get_exchange_symbol(self, symbol: Symbol) -> str:
        """Get exchange-specific symbol from unified symbol"""
        return self._unified_to_ccxt_symbol(symbol)

    # --- Market Data ---

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch ticker for a symbol"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            ticker = await self.exchange.fetch_ticker(ex_symbol)
            return self._parse_ticker(ticker, symbol)
        except Exception as e:
            logger.error(f"fetch_ticker failed for {symbol}: {e}")
            raise

    async def fetch_order_book(self, symbol: Symbol, limit: int = 100) -> OrderBook:
        """Fetch order book"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            ob = await self.exchange.fetch_order_book(ex_symbol, limit)
            return self._parse_order_book(ob, symbol)
        except Exception as e:
            logger.error(f"fetch_order_book failed for {symbol}: {e}")
            raise

    async def fetch_ohlcv(self, symbol: Symbol, timeframe: str,
                          since: int | None = None, limit: int = 100) -> list[Candle]:
        """Fetch OHLCV candles"""
        ex_symbol = self.get_exchange_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            ohlcv = await self.exchange.fetch_ohlcv(ex_symbol, timeframe, since, limit)
            return [self._parse_candle(c, symbol, timeframe) for c in ohlcv]
        except Exception as e:
            logger.error(f"fetch_ohlcv failed for {symbol}: {e}")
            raise

    # --- Account ---

    async def fetch_balance(self) -> dict[AssetClass, Balance]:
        """Fetch account balances"""
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            balance = await self.exchange.fetch_balance()
            return self._parse_balance(balance)
        except Exception as e:
            logger.error(f"fetch_balance failed: {e}")
            raise

    # --- Trading ---

    async def create_order(self, order: Order) -> Order:
        """Create a new order"""
        ex_symbol = self.get_exchange_symbol(order.symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            # Convert order to CCXT params
            params = self._order_to_ccxt_params(order)
            result = await self.exchange.create_order(
                ex_symbol,
                order.type.value.lower(),
                order.side.value.lower(),
                float(order.size),
                float(order.price) if order.price else None,
                params
            )
            return self._parse_order(result, order.symbol)
        except InsufficientFunds:
            logger.error(f"Insufficient funds for order {order}")
            raise
        except InvalidOrder as e:
            logger.error(f"Invalid order {order}: {e}")
            raise
        except Exception as e:
            logger.error(f"create_order failed: {e}")
            raise

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        """Cancel an order"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            await self.exchange.cancel_order(order_id, ex_symbol)
            return True
        except Exception as e:
            logger.error(f"cancel_order failed: {e}")
            return False

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """Fetch order status"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            order = await self.exchange.fetch_order(order_id, ex_symbol)
            return self._parse_order(order, symbol)
        except Exception as e:
            logger.error(f"fetch_order failed: {e}")
            raise

    async def fetch_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        """Fetch all open orders"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol) if symbol else None
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            orders = await self.exchange.fetch_open_orders(ex_symbol)
            return [self._parse_order(o, self._ccxt_to_unified_symbol(o['symbol'])) for o in orders]
        except Exception as e:
            logger.error(f"fetch_open_orders failed: {e}")
            raise

    async def fetch_positions(self, symbol: Symbol | None = None) -> list[Position]:
        """Fetch positions (for futures/perp)"""
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            positions = await self.exchange.fetch_positions(
                [self._unified_to_ccxt_symbol(symbol)] if symbol else None
            )
            return [self._parse_position(p) for p in positions if float(p.get('contracts', 0)) != 0]
        except Exception as e:
            logger.error(f"fetch_positions failed: {e}")
            raise

    # --- Parsing helpers ---

    def _parse_ticker(self, ticker: dict, symbol: Symbol) -> Ticker:
        return Ticker(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(ticker['timestamp'] / 1000),
            bid=ticker['bid'],
            ask=ticker['ask'],
            last=ticker['last'],
            high=ticker['high'],
            low=ticker['low'],
            open=ticker['open'],
            close=ticker['close'],
            base_volume=ticker['baseVolume'],
            quote_volume=ticker['quoteVolume'],
            change=ticker['change'],
            percentage=ticker['percentage'],
        )

    def _parse_order_book(self, ob: dict, symbol: Symbol) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(ob['timestamp'] / 1000) if ob['timestamp'] else datetime.now(),
            bids=[OrderBookLevel(price=float(b[0]), size=float(b[1])) for b in ob['bids']],
            asks=[OrderBookLevel(price=float(a[0]), size=float(a[1])) for a in ob['asks']],
        )

    def _parse_candle(self, candle: list, symbol: Symbol, timeframe: str) -> Candle:
        return Candle(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(candle[0] / 1000),
            timeframe=timeframe,
            open=candle[1],
            high=candle[2],
            low=candle[3],
            close=candle[4],
            volume=candle[5],
        )

    def _parse_balance(self, balance: dict) -> dict[AssetClass, Balance]:
        result = {}
        for currency, amounts in balance.items():
            if currency in ('info', 'free', 'used', 'total'):
                continue
            free = float(amounts.get('free', 0))
            used = float(amounts.get('used', 0))
            total = float(amounts.get('total', 0))
            if total > 0:
                # Determine asset class from currency
                asset_class = AssetClass.CRYPTO if currency not in ('USD', 'EUR', 'GBP', 'JPY') else AssetClass.FOREX
                if asset_class not in result:
                    result[asset_class] = Balance(asset_class=asset_class)
                result[asset_class].assets[currency] = {
                    'free': free, 'used': used, 'total': total
                }
        return result

    def _order_to_ccxt_params(self, order: Order) -> dict:
        params = {}
        if order.time_in_force:
            params['timeInForce'] = order.time_in_force.value
        if order.reduce_only:
            params['reduceOnly'] = True
        if order.post_only:
            params['postOnly'] = True
        if order.client_order_id:
            params['clientOrderId'] = order.client_order_id
        return params

    def _parse_order(self, order: dict, symbol: Symbol) -> Order:
        status_map = {
            'open': OrderStatus.OPEN,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELLED,
            'rejected': OrderStatus.REJECTED,
            'expired': OrderStatus.EXPIRED,
        }
        return Order(
            id=order['id'],
            client_order_id=order.get('clientOrderId'),
            symbol=symbol,
            side=OrderSide(order['side'].upper()),
            type=OrderType(order['type'].upper()),
            status=status_map.get(order['status'], OrderStatus.OPEN),
            size=order['amount'],
            filled_size=order['filled'],
            avg_fill_price=order['average'],
            price=order['price'],
            fee=order['fee']['cost'] if order.get('fee') else Decimal(0),
            fee_currency=order['fee']['currency'] if order.get('fee') else "",
            time_in_force=TimeInForce(order['timeInForce']) if order.get('timeInForce') else TimeInForce.GTC,
            reduce_only=order.get('reduceOnly', False),
            post_only=order.get('postOnly', False),
            created_at=datetime.fromtimestamp(order['timestamp'] / 1000) if order.get('timestamp') else datetime.now(),
            updated_at=datetime.fromtimestamp(order['lastTradeTimestamp'] / 1000) if order.get('lastTradeTimestamp') else None,
            info=order,
        )

    def _parse_position(self, pos: dict) -> Position:
        symbol = self.get_unified_symbol(pos['symbol'])
        return Position(
            symbol=symbol,
            size=Decimal(str(pos['contracts'])),
            entry_price=Decimal(str(pos['entryPrice'])),
            mark_price=Decimal(str(pos['markPrice'])),
            unrealized_pnl=Decimal(str(pos.get('unrealizedPnl', 0))),
            realized_pnl=Decimal(str(pos.get('realizedPnl', 0))),
            leverage=Decimal(str(pos['leverage'])) if pos.get('leverage') else Decimal(1),
            liquidation_price=Decimal(str(pos['liquidationPrice'])) if pos.get('liquidationPrice') else None,
            updated_at=datetime.fromtimestamp(pos['timestamp'] / 1000) if pos.get('timestamp') else datetime.now(),
        )

    def get_status(self) -> ExchangeStatus:
        return self._status

    def is_healthy(self) -> bool:
        return self._status == ExchangeStatus.HEALTHY and self._connected


class MultiExchangeManager:
    """Manages multiple exchange connections"""

    def __init__(self):
        self.exchanges: dict[str, CCXTAdapter] = {}
        self._primary_exchange: str | None = None

    def add_exchange(self, config: ExchangeConfig) -> CCXTAdapter:
        """Add an exchange to the manager"""
        adapter = CCXTAdapter(config)
        self.exchanges[config.id] = adapter
        if self._primary_exchange is None:
            self._primary_exchange = config.id
        return adapter

    def get_exchange(self, exchange_id: str) -> CCXTAdapter | None:
        return self.exchanges.get(exchange_id)

    def get_primary(self) -> CCXTAdapter | None:
        return self.exchanges.get(self._primary_exchange) if self._primary_exchange else None

    def set_primary(self, exchange_id: str) -> None:
        if exchange_id in self.exchanges:
            self._primary_exchange = exchange_id

    async def connect_all(self) -> dict[str, bool]:
        """Connect to all enabled exchanges"""
        results = {}
        for exchange_id, adapter in self.exchanges.items():
            if not adapter.config.enabled:
                results[exchange_id] = False
                continue
            try:
                await adapter.connect()
                results[exchange_id] = True
            except Exception as e:
                logger.error(f"Failed to connect to {exchange_id}: {e}")
                results[exchange_id] = False
        return results

    async def disconnect_all(self) -> None:
        for adapter in self.exchanges.values():
            await adapter.disconnect()

    def get_healthy_exchanges(self) -> list[CCXTAdapter]:
        return [ex for ex in self.exchanges.values() if ex.is_healthy()]

    async def fetch_ticker_all(self, symbol: Symbol) -> dict[str, Ticker]:
        """Fetch ticker from all healthy exchanges"""
        results = {}
        for exchange_id, adapter in self.exchanges.items():
            if not adapter.is_healthy():
                continue
            try:
                ticker = await adapter.fetch_ticker(symbol)
                results[exchange_id] = ticker
            except Exception as e:
                logger.debug(f"fetch_ticker failed on {exchange_id}: {e}")
        return results

    async def fetch_best_bid_ask(self, symbol: Symbol) -> tuple[Ticker | None, Ticker | None]:
        """Get best bid/ask across all exchanges"""
        tickers = await self.fetch_ticker_all(symbol)
        if not tickers:
            return None, None

        best_bid = max(tickers.values(), key=lambda t: t.bid or 0)
        best_ask = min(tickers.values(), key=lambda t: t.ask or float('inf'))
        return best_bid, best_ask


# --- Predefined exchange configs ---

def get_default_exchange_configs() -> list[ExchangeConfig]:
    """Get default configurations for major exchanges"""
    return [
        ExchangeConfig(
            id='binance',
            name='Binance',
            sandbox=False,
            rate_limit=1200,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
            options={'defaultType': 'spot'},
        ),
        ExchangeConfig(
            id='bybit',
            name='Bybit',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
            options={'defaultType': 'spot'},
        ),
        ExchangeConfig(
            id='okx',
            name='OKX',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL, MarketType.OPTIONS],
            options={'defaultType': 'spot'},
        ),
        ExchangeConfig(
            id='coinbase',
            name='Coinbase',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT],
        ),
        ExchangeConfig(
            id='kraken',
            name='Kraken',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES],
        ),
        ExchangeConfig(
            id='gateio',
            name='Gate.io',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
        ),
        ExchangeConfig(
            id='kucoin',
            name='KuCoin',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
        ),
        ExchangeConfig(
            id='htx',
            name='HTX (Huobi)',
            sandbox=False,
            rate_limit=1000,
            markets=[MarketType.SPOT, MarketType.FUTURES, MarketType.PERPETUAL],
        ),
    ]


async def create_multi_exchange_manager(configs: list[ExchangeConfig] | None = None) -> MultiExchangeManager:
    """Create and connect multi-exchange manager"""
    manager = MultiExchangeManager()

    if configs is None:
        configs = get_default_exchange_configs()

    for config in configs:
        manager.add_exchange(config)

    await manager.connect_all()
    return manager