"""
CCXT Unified Adapter - Multi-Exchange Trading Interface

Provides unified interface for multiple exchanges via CCXT.
Supports: Binance, Bybit, OKX, Coinbase, Kraken, Gate.io, KuCoin, HTX, etc.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    Ticker, OrderBook, OrderBookLevel, Candle, OrderConstraintError
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

            # Sandbox/testnet mode (CCXT: binance → testnet.binance.vision
            # for spot, testnet.binancefuture.com for futures)
            if self.config.sandbox or self.config.testnet:
                self.exchange.set_sandbox_mode(True)
                logger.info(f"Sandbox/testnet mode ENABLED for {self.config.name}")

            # Load markets (sync ccxt returns dict, ccxt.pro returns coroutine)
            markets_load = self.exchange.load_markets()
            if asyncio.iscoroutine(markets_load):
                await markets_load
            self._markets = self.exchange.markets
            self._build_symbol_maps()

            # Test connection
            time_res = self.exchange.fetch_time()
            if asyncio.iscoroutine(time_res):
                await time_res
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
            await self._maybe_await(self.exchange.close())
        if self.ws_exchange:
            await self._maybe_await(self.ws_exchange.close())
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
        # Fallback: use the canonical ccxt format (futures get :SETTLE suffix,
        # so the wire symbol is correct even before markets are loaded).
        return symbol.ccxt_symbol

    async def fetch_tickers(self, symbols: list[Symbol]) -> dict[str, float]:
        """Batch-fetch last prices for many symbols with a single API call."""
        ex_symbols = [self._unified_to_ccxt_symbol(s) for s in symbols]
        try:
            raw = await self._maybe_await(self.exchange.fetch_tickers(ex_symbols))
            prices: dict[str, float] = {}
            for ex_sym, ticker in raw.items():
                last = ticker.get('last') or ticker.get('close')
                if last is None:
                    continue
                market = self.exchange.markets.get(ex_sym)
                unified = self._ccxt_to_unified_symbol(market) if market else None
                if unified is not None:
                    prices[unified.pair] = float(last)
            return prices
        except Exception as e:
            logger.error(f"fetch_tickers failed for {len(symbols)} symbols: {e}")
            return {}

    async def fetch_markets(self) -> list[dict]:
        """Get all available markets"""
        return list(self._markets.values())

    def get_unified_symbol(self, exchange_symbol: str) -> Symbol | None:
        """Get unified symbol from exchange symbol"""
        return self._symbol_map.get(exchange_symbol)

    def get_exchange_symbol(self, symbol: Symbol) -> str:
        """Get exchange-specific symbol from unified symbol"""
        return self._unified_to_ccxt_symbol(symbol)

    def has_market(self, ex_symbol: str) -> bool:
        """True if the exchange exposes this market symbol.

        Avoids slow/erroring ticker calls for junk pairs (e.g. testnet
        faucet coins that have no tradeable market)."""
        return self.exchange is not None and ex_symbol in self.exchange.markets

    def normalize_order_amount(
        self,
        symbol: Symbol,
        amount: Decimal,
        *,
        reference_price: Decimal | None = None,
    ) -> Decimal:
        """Apply exchange precision and hard amount/notional market filters."""

        if self.exchange is None:
            raise RuntimeError("exchange is not connected")
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        market = self.exchange.market(ex_symbol)
        if not market or not market.get("active", True):
            raise OrderConstraintError(
                f"market is unavailable or inactive: {ex_symbol}",
                constraint="market_unavailable",
            )
        normalized = Decimal(str(self.exchange.amount_to_precision(ex_symbol, float(amount))))
        if normalized <= 0:
            raise OrderConstraintError(
                f"amount rounds to zero for {ex_symbol}",
                constraint="amount_zero",
            )

        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        minimum_amount = amount_limits.get("min")
        maximum_amount = amount_limits.get("max")
        if minimum_amount is not None and normalized < Decimal(str(minimum_amount)):
            raise OrderConstraintError(
                f"amount is below market minimum for {ex_symbol}",
                constraint="minimum_amount",
            )
        if maximum_amount is not None and normalized > Decimal(str(maximum_amount)):
            raise OrderConstraintError(
                f"amount exceeds market maximum for {ex_symbol}",
                constraint="maximum_amount",
            )
        if reference_price is not None:
            if reference_price <= 0:
                raise OrderConstraintError(
                    "reference price must be positive",
                    constraint="reference_price",
                )
            cost = normalized * reference_price
            minimum_cost = cost_limits.get("min")
            maximum_cost = cost_limits.get("max")
            if minimum_cost is not None and cost < Decimal(str(minimum_cost)):
                raise OrderConstraintError(
                    f"order notional is below market minimum for {ex_symbol}",
                    constraint="minimum_notional",
                )
            if maximum_cost is not None and cost > Decimal(str(maximum_cost)):
                raise OrderConstraintError(
                    f"order notional exceeds market maximum for {ex_symbol}",
                    constraint="maximum_notional",
                )
        return normalized

    # --- Market Data ---

    @staticmethod
    async def _maybe_await(result: object):
        """ccxt (sync) returns plain values; ccxt.pro returns coroutines."""
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch ticker for a symbol"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            request_started_at = time.monotonic()
            ticker = await self._maybe_await(self.exchange.fetch_ticker(ex_symbol))
            return self._parse_ticker(
                ticker,
                symbol,
                request_started_at=request_started_at,
                received_at=time.monotonic(),
            )
        except Exception as e:
            logger.error(f"fetch_ticker failed for {symbol}: {e}")
            raise

    async def fetch_order_book(self, symbol: Symbol, limit: int = 100) -> OrderBook:
        """Fetch order book"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            request_started_at = time.monotonic()
            ob = await self._maybe_await(self.exchange.fetch_order_book(ex_symbol, limit))
            return self._parse_order_book(
                ob,
                symbol,
                request_started_at=request_started_at,
                received_at=time.monotonic(),
            )
        except Exception as e:
            logger.error(f"fetch_order_book failed for {symbol}: {e}")
            raise

    async def fetch_ohlcv(self, symbol: Symbol, timeframe: str,
                          since: int | None = None, limit: int = 100) -> list[Candle]:
        """Fetch OHLCV candles"""
        ex_symbol = self.get_exchange_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            ohlcv = await self._maybe_await(self.exchange.fetch_ohlcv(ex_symbol, timeframe, since, limit))
            return [self._parse_candle(c, symbol, timeframe) for c in ohlcv]
        except Exception as e:
            logger.error(f"fetch_ohlcv failed for {symbol}: {e}")
            raise

    # --- Account ---

    async def fetch_balance(self) -> dict[AssetClass, Balance]:
        """Fetch account balances"""
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            balance = await self._maybe_await(self.exchange.fetch_balance())
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
            result = await self._maybe_await(self.exchange.create_order(
                ex_symbol,
                self._ccxt_order_type(order),
                order.side.value.lower(),
                float(order.size),
                float(order.price) if order.price else None,
                params
            ))
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

    async def replace_order(self, order_id: str, order: Order) -> Order:
        """Cancel-replace a Binance Spot order through CCXT's edit API.

        The caller must reconcile both client order IDs after a timeout because
        cancellation can succeed while placement of the replacement fails.
        """

        ex_symbol = self.get_exchange_symbol(order.symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)
        try:
            result = await self._maybe_await(self.exchange.edit_order(
                order_id,
                ex_symbol,
                self._ccxt_order_type(order),
                order.side.value.lower(),
                float(order.size),
                float(order.price) if order.price else None,
                self._order_to_ccxt_params(order),
            ))
            return self._parse_order(result, order.symbol)
        except Exception as exc:
            logger.error(f"replace_order failed: {exc}")
            raise

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        """Cancel an order"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            await self._maybe_await(self.exchange.cancel_order(order_id, ex_symbol))
            return True
        except Exception as e:
            logger.error(f"cancel_order failed: {e}")
            return False

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """Fetch order status"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            order = await self._maybe_await(self.exchange.fetch_order(order_id, ex_symbol))
            return self._parse_order(order, symbol)
        except Exception as e:
            logger.error(f"fetch_order failed: {e}")
            raise

    async def fetch_order_by_client_id(
        self,
        client_order_id: str,
        symbol: Symbol,
    ) -> Order | None:
        """Fetch an order by client ID, including bounded history fallbacks.

        Some exchanges stop returning terminal orders from ``fetch_order``.  A
        missing direct lookup therefore falls back to open/closed order history
        and finally trade history.  Trade-only evidence is deliberately parsed
        with an ``unknown`` lifecycle status so the caller records fills but
        still fails closed until an operator can establish the terminal state.
        """

        ex_symbol = self._unified_to_ccxt_symbol(symbol)
        order_payload: dict | None = None
        await self._rate_limiter.acquire(self.config.id, weight=1)
        try:
            order_payload = await self._maybe_await(self.exchange.fetch_order(
                None,
                ex_symbol,
                {"origClientOrderId": client_order_id},
            ))
        except Exception as exc:
            lookup_miss = ccxt is not None and isinstance(
                exc,
                (ccxt.OrderNotFound, ccxt.NotSupported),
            )
            if not lookup_miss:
                logger.error(f"fetch_order_by_client_id failed: {exc}")
                raise

        if order_payload is None:
            for method_name in ("fetch_open_orders", "fetch_closed_orders"):
                method = getattr(self.exchange, method_name, None)
                if not callable(method):
                    continue
                await self._rate_limiter.acquire(self.config.id, weight=1)
                try:
                    candidates = await self._maybe_await(
                        method(ex_symbol, None, 100)
                    )
                except Exception as exc:
                    unsupported = ccxt is not None and isinstance(
                        exc,
                        (ccxt.OrderNotFound, ccxt.NotSupported),
                    )
                    if unsupported:
                        continue
                    logger.error(f"{method_name} client-ID fallback failed: {exc}")
                    raise
                order_payload = next((
                    candidate for candidate in candidates or []
                    if self._payload_client_order_id(candidate) == client_order_id
                ), None)
                if order_payload is not None:
                    break

        method = getattr(self.exchange, "fetch_my_trades", None)
        if not callable(method):
            return self._parse_order(order_payload, symbol) if order_payload else None
        await self._rate_limiter.acquire(self.config.id, weight=1)
        try:
            trades = await self._maybe_await(method(ex_symbol, None, 200))
        except Exception as exc:
            unsupported = ccxt is not None and isinstance(exc, ccxt.NotSupported)
            if unsupported:
                return self._parse_order(order_payload, symbol) if order_payload else None
            logger.error(f"fetch_my_trades client-ID fallback failed: {exc}")
            raise
        exchange_order_id = str(order_payload.get('id') or '') if order_payload else ""
        matched = [
            trade for trade in trades or []
            if (
                self._payload_client_order_id(trade) == client_order_id
                or (
                    exchange_order_id
                    and str(trade.get('order') or '') == exchange_order_id
                )
            )
        ]
        if order_payload is not None:
            if matched:
                order_payload = self._merge_order_trade_evidence(
                    order_payload,
                    matched,
                    client_order_id=client_order_id,
                )
            return self._parse_order(order_payload, symbol)
        return self._order_from_trade_history(
            matched,
            client_order_id=client_order_id,
            symbol=symbol,
        )

    async def fetch_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        """Fetch all open orders"""
        ex_symbol = self._unified_to_ccxt_symbol(symbol) if symbol else None
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            orders = await self._maybe_await(self.exchange.fetch_open_orders(ex_symbol))
            parsed = []
            for order in orders:
                unified = self.get_unified_symbol(order['symbol'])
                if unified is None:
                    base, sep, quote = order['symbol'].partition('/')
                    if not sep:
                        logger.warning(
                            f"fetch_open_orders: cannot map market {order['symbol']}"
                        )
                        continue
                    unified = Symbol(
                        base=base,
                        quote=quote,
                        asset_class=AssetClass.CRYPTO,
                        market_type=MarketType.SPOT,
                        exchange=self.config.id,
                    )
                parsed.append(self._parse_order(order, unified))
            return parsed
        except Exception as e:
            logger.error(f"fetch_open_orders failed: {e}")
            raise

    async def fetch_positions(self, symbol: Symbol | None = None) -> list[Position]:
        """Fetch positions (for futures/perp)"""
        await self._rate_limiter.acquire(self.config.id, weight=1)

        try:
            positions = await self._maybe_await(self.exchange.fetch_positions(
                [self._unified_to_ccxt_symbol(symbol)] if symbol else None
            ))
            return [self._parse_position(p) for p in positions if float(p.get('contracts', 0)) != 0]
        except Exception as e:
            logger.error(f"fetch_positions failed: {e}")
            raise

    # --- Parsing helpers ---

    def _parse_ticker(
        self,
        ticker: dict,
        symbol: Symbol,
        *,
        request_started_at: Optional[float] = None,
        received_at: Optional[float] = None,
    ) -> Ticker:
        timestamp_ms = ticker.get('timestamp')
        if timestamp_ms is None:
            raise ValueError(f"Ticker for {symbol.pair} has no timestamp")
        return Ticker(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC),
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
            info=ticker.get('info', {}),
            request_started_at=request_started_at,
            received_at=received_at,
        )

    def _parse_order_book(
        self,
        ob: dict,
        symbol: Symbol,
        *,
        request_started_at: Optional[float] = None,
        received_at: Optional[float] = None,
    ) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(ob['timestamp'] / 1000, tz=UTC) if ob['timestamp'] else datetime.now(UTC),
            bids=[OrderBookLevel(price=float(b[0]), size=float(b[1])) for b in ob['bids']],
            asks=[OrderBookLevel(price=float(a[0]), size=float(a[1])) for a in ob['asks']],
            sequence=ob.get('nonce') or ob.get('lastUpdateId'),
            request_started_at=request_started_at,
            received_at=received_at,
        )

    def _parse_candle(self, candle: list, symbol: Symbol, timeframe: str) -> Candle:
        return Candle(
            symbol=symbol,
            timestamp=datetime.fromtimestamp(candle[0] / 1000, tz=UTC),
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
            if currency in ('info', 'free', 'used', 'total', 'timestamp', 'datetime'):
                continue
            if not isinstance(amounts, dict):
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

    @staticmethod
    def _ccxt_order_type(order: Order) -> str:
        """Map internal protective types onto CCXT conditional orders."""

        if order.type == OrderType.STOP:
            return "market"
        if order.type == OrderType.STOP_LIMIT:
            return "limit"
        return order.type.value.lower()

    def _order_to_ccxt_params(self, order: Order) -> dict:
        params = {}
        # Binance chỉ chấp nhận timeInForce cho limit orders; market orders
        # sẽ bị reject (-1106) nếu gửi kèm.
        if order.time_in_force and order.type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
            params['timeInForce'] = order.time_in_force.value
        if order.type in {OrderType.STOP, OrderType.STOP_LIMIT}:
            if order.stop_price is None or order.stop_price <= 0:
                raise InvalidOrder("protective stop orders require a positive stop price")
            if order.type == OrderType.STOP_LIMIT and (
                order.price is None or order.price <= 0
            ):
                raise InvalidOrder("stop-limit orders require a positive limit price")
            params['stopLossPrice'] = float(order.stop_price)
        if order.reduce_only:
            params['reduceOnly'] = True
        if order.post_only:
            params['postOnly'] = True
        if order.client_order_id:
            params['clientOrderId'] = order.client_order_id
        return params

    @staticmethod
    def _payload_client_order_id(payload: dict) -> str:
        """Extract common unified/raw client-order ID fields without guessing."""

        direct = payload.get('clientOrderId') or payload.get('client_order_id')
        if direct:
            return str(direct)
        info = payload.get('info')
        if not isinstance(info, dict):
            return ""
        for name in (
            'clientOrderId', 'origClientOrderId', 'newClientOrderId',
            'clientOid', 'clOrdId',
        ):
            value = info.get(name)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _fee_breakdown(order: dict) -> dict[str, Decimal]:
        """Return cumulative fee amounts grouped by their original currency."""

        entries: list[dict] = []
        raw_fees = order.get('fees')
        if isinstance(raw_fees, list) and raw_fees:
            entries.extend(item for item in raw_fees if isinstance(item, dict))
        elif isinstance(order.get('fee'), dict):
            entries.append(order['fee'])
        else:
            for trade in order.get('trades') or []:
                if not isinstance(trade, dict):
                    continue
                trade_fees = trade.get('fees')
                if isinstance(trade_fees, list) and trade_fees:
                    entries.extend(
                        item for item in trade_fees if isinstance(item, dict)
                    )
                elif isinstance(trade.get('fee'), dict):
                    entries.append(trade['fee'])

        totals: dict[str, Decimal] = {}
        for entry in entries:
            cost = Decimal(str(entry.get('cost') or 0))
            if cost < 0:
                raise ValueError("exchange returned a negative order fee")
            currency = str(entry.get('currency') or 'UNKNOWN').upper()
            totals[currency] = totals.get(currency, Decimal(0)) + cost
        return totals

    @staticmethod
    def _merge_order_trade_evidence(
        order: dict,
        trades: list[dict],
        *,
        client_order_id: str,
    ) -> dict:
        """Attach authoritative per-fill IDs and currencies to an order snapshot."""

        merged = dict(order)
        merged['clientOrderId'] = (
            CCXTAdapter._payload_client_order_id(order) or client_order_id
        )
        merged['trades'] = trades
        trade_fees: list[dict] = []
        for trade in trades:
            fees = trade.get('fees')
            if isinstance(fees, list) and fees:
                trade_fees.extend(item for item in fees if isinstance(item, dict))
            elif isinstance(trade.get('fee'), dict):
                trade_fees.append(trade['fee'])
        if trade_fees:
            merged['fees'] = trade_fees
            merged['fee'] = None
        return merged

    def _order_from_trade_history(
        self,
        trades: list[dict],
        *,
        client_order_id: str,
        symbol: Symbol,
    ) -> Order | None:
        if not trades:
            return None
        side = str(trades[0].get('side') or '').lower()
        if side not in {OrderSide.BUY.value, OrderSide.SELL.value}:
            return None
        filled = sum(Decimal(str(trade.get('amount') or 0)) for trade in trades)
        quote_cost = sum(
            Decimal(str(trade.get('cost') or 0))
            if trade.get('cost') is not None
            else Decimal(str(trade.get('amount') or 0))
            * Decimal(str(trade.get('price') or 0))
            for trade in trades
        )
        average = quote_cost / filled if filled > 0 else Decimal(0)
        timestamps = [
            int(trade['timestamp']) for trade in trades if trade.get('timestamp')
        ]
        order_ids = [str(trade.get('order') or '') for trade in trades]
        exchange_order_id = next((value for value in order_ids if value), "")
        synthetic = {
            'id': exchange_order_id or f"trade-history:{client_order_id}",
            'clientOrderId': client_order_id,
            'status': 'trade_history_only',
            'symbol': symbol.ccxt_symbol,
            'side': side,
            'type': str(trades[0].get('type') or 'market').lower(),
            'amount': filled,
            'filled': filled,
            'average': average,
            'cost': quote_cost,
            'price': None,
            'stopPrice': None,
            'fees': [],
            'fee': None,
            'trades': trades,
            'timeInForce': None,
            'timestamp': min(timestamps) if timestamps else None,
            'lastTradeTimestamp': max(timestamps) if timestamps else None,
        }
        return self._parse_order(synthetic, symbol)

    def _parse_order(self, order: dict, symbol: Symbol) -> Order:
        status_map = {
            'open': OrderStatus.OPEN,
            'closed': OrderStatus.FILLED,
            'canceled': OrderStatus.CANCELLED,
            'cancelled': OrderStatus.CANCELLED,
            'rejected': OrderStatus.REJECTED,
            'expired': OrderStatus.EXPIRED,
        }
        filled_size = Decimal(str(order.get('filled') or 0))
        raw_status = str(order.get('status') or '').strip().lower()
        parsed_status = status_map.get(raw_status, OrderStatus.UNKNOWN)
        if parsed_status == OrderStatus.OPEN and filled_size > 0:
            parsed_status = OrderStatus.PARTIAL
        raw_type = str(order['type']).lower()
        parsed_type = {
            'stop': OrderType.STOP,
            'stop_loss': OrderType.STOP,
            'stop_market': OrderType.STOP,
            'stop_limit': OrderType.STOP_LIMIT,
            'stop_loss_limit': OrderType.STOP_LIMIT,
        }.get(raw_type)
        if parsed_type is None:
            parsed_type = OrderType(raw_type)
        stop_price = order.get('stopPrice')
        if stop_price is None:
            stop_price = order.get('triggerPrice')
        trades = [trade for trade in order.get('trades') or [] if isinstance(trade, dict)]
        trade_ids = tuple(
            dict.fromkeys(
                str(trade.get('id')) for trade in trades if trade.get('id')
            )
        )
        fee_breakdown = self._fee_breakdown(order)
        quote_cost = Decimal(str(order.get('cost') or 0))
        if quote_cost == 0 and filled_size > 0:
            quote_cost = sum(
                Decimal(str(trade.get('cost') or 0))
                if trade.get('cost') is not None
                else Decimal(str(trade.get('amount') or 0))
                * Decimal(str(trade.get('price') or 0))
                for trade in trades
            )
        if quote_cost == 0 and filled_size > 0 and order.get('average') is not None:
            quote_cost = filled_size * Decimal(str(order.get('average') or 0))
        return Order(
            id=order['id'],
            client_order_id=order.get('clientOrderId'),
            symbol=symbol,
            side=OrderSide(str(order['side']).lower()),
            type=parsed_type,
            status=parsed_status,
            size=Decimal(str(order.get('amount') or 0)),
            filled_size=filled_size,
            avg_fill_price=Decimal(str(order.get('average') or 0)),
            quote_cost=quote_cost,
            price=Decimal(str(order['price'])) if order.get('price') is not None else None,
            stop_price=Decimal(str(stop_price)) if stop_price is not None else None,
            fee=sum(fee_breakdown.values(), Decimal(0)),
            fee_currency=next(iter(fee_breakdown)) if len(fee_breakdown) == 1 else "",
            fee_breakdown=fee_breakdown,
            trade_ids=trade_ids,
            raw_status=raw_status,
            time_in_force=TimeInForce(str(order['timeInForce']).lower()) if order.get('timeInForce') else TimeInForce.GTC,
            reduce_only=order.get('reduceOnly', False),
            post_only=order.get('postOnly', False),
            created_at=datetime.fromtimestamp(order['timestamp'] / 1000, tz=UTC) if order.get('timestamp') else datetime.now(UTC),
            updated_at=datetime.fromtimestamp(order['lastTradeTimestamp'] / 1000, tz=UTC) if order.get('lastTradeTimestamp') else None,
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
            updated_at=datetime.fromtimestamp(pos['timestamp'] / 1000, tz=UTC) if pos.get('timestamp') else datetime.now(UTC),
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
