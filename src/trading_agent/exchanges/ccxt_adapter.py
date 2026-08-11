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
    # Optional SDK â€” adapter stays importable without ccxt (e.g. light env/CI).
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
                "ccxt is not installed â€” run `pip install ccxt` (or `poetry add ccxt`) "
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

            # Sandbox/testnet mode (CCXT: binance â†’ testnet.binance.vision
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
        # Fallback: construct standard format
        return f"{symbol.base}/{symbol.quote}"

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
            raise InvalidOrder(f"market is unavailable or inactive: {ex_symbol}")
        normalized = Decimal(str(self.exchange.amount_to_precision(ex_symbol, float(amount))))
        if normalized <= 0:
            raise InvalidOrder(f"amount rounds to zero for {ex_symbol}")

        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        minimum_amount = amount_limits.get("min")
        maximum_amount = amount_limits.get("max")
        if minimum_amount is not None and normalized < Decimal(str(minimum_amount)):
            raise InvalidOrder(f"amount is below market minimum for {ex_symbol}")
        if maximum_amount is not None and normalized > Decimal(str(maximum_amount)):
            raise InvalidOrder(f"amount exceeds market maximum for {ex_symbol}")
        if reference_price is not None:
            if reference_price <= 0:
                raise InvalidOrder("reference price must be positive")
            cost = normalized * reference_price
            minimum_cost = cost_limits.get("min")
            maximum_cost = cost_limits.get("max")
            if minimum_cost is not None and cost < Decimal(str(minimum_cost)):
                raise InvalidOrder(f"order notional is below market minimum for {ex_symbol}")
            if maximum_cost is not None and cost > Decimal(str(maximum_cost×¾{¶‰žËkºwµçT¡Í•±˜¹½¹™¥œ¹¥°Ý•¥¡ÐôÄ¤4(4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½É‘•È€ô…Ý…¥ÐÍ•±˜¹}µ…å‰•}…Ý…¥Ð¡Í•±˜¹•á¡…¹”¹™•Ñ¡}½É‘•È¡½É‘•É}¥°•á}Íåµ‰½°¤¤4(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}Á…ÉÍ•}½É‘•È¡½É‘•È°Íåµ‰½°¤4(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì”è4(€€€€€€€€€€€±½•È¹•ÉÉ½È¡˜‰™•Ñ¡}½É‘•È™…¥±•èí•ôˆ¤(€€€€€€€€€€€É…¥Í”((€€€…Íå¹Œ‘•˜™•Ñ¡}½É‘•É}‰å}±¥•¹Ñ}¥ (€€€€€€€Í•±˜°(€€€€€€€±¥•¹Ñ}½É‘•É}¥èÍÑÈ°(€€€€€€€Íåµ‰½°èMåµ‰½°°(€€€€¤€´ø=É‘•Èð9½¹”è(€€€€€€€€ˆˆ‰•Ñ „	¥¹…¹”µÍÑå±”½É‘•È‰äÑ¡”‘•Ñ•Éµ¥¹¥ÍÑ¥Œ±¥•¹Ð½É‘•È%¸ˆˆˆ((€€€€€€€•á}Íåµ‰½°€ôÍ•±˜¹}Õ¹¥™¥•‘}Ñ½}áÑ}Íåµ‰½°¡Íåµ‰½°¤(€€€€€€€…Ý…¥ÐÍ•±˜¹}É…Ñ•}±¥µ¥Ñ•È¹…ÅÕ¥É”¡Í•±˜¹½¹™¥œ¹¥°Ý•¥¡ÐôÄ¤(€€€€€€€ÑÉäè(€€€€€€€€€€€½É‘•È€ô…Ý…¥ÐÍ•±˜¹}µ…å‰•}…Ý…¥Ð¡Í•±˜¹•á¡…¹”¹™•Ñ¡}½É‘•È (€€€€€€€€€€€€€€€9½¹”°(€€€€€€€€€€€€€€€•á}Íåµ‰½°°(€€€€€€€€€€€€€€€ì‰½É¥±¥•¹Ñ=É‘•É%ˆè±¥•¹Ñ}½É‘•É}¥‘ô°(€€€€€€€€€€€€¤¤(€€€€€€€€€€€É•ÑÕÉ¸Í•±˜¹}Á…ÉÍ•}½É‘•È¡½É‘•È°Íåµ‰½°¤(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì•áŒè(€€€€€€€€€€€¥˜áÐ¥Ì¹½Ð9½¹”…¹¥Í¥¹ÍÑ…¹”¡•áŒ°áÐ¹=É‘•É9½Ñ½Õ¹¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€€€€€±½•È¹•ÉÉ½È¡˜‰™•Ñ¡}½É‘•É}‰å}±¥•¹Ñ}¥™…¥±•èí•áôˆ¤(€€€€€€€€€€€É…¥Í”(4(€€€…Íå¹Œ‘•˜™•Ñ¡}½Á•¹}½É‘•ÉÌ¡Í•±˜°Íåµ‰½°èMåµ‰½°ð9½¹”€ô9½¹”¤€´ø±¥ÍÑm=É‘•Étè4(€€€€€€€€ˆˆ‰•Ñ …±°½Á•¸½É‘•ÉÌˆˆˆ4(€€€€€€€•á}Íåµ‰½°€ôÍ•±˜¹}Õ¹¥™¥•‘}Ñ½}áÑ}Íåµ‰½°¡Íåµ‰½°¤¥˜Íåµ‰½°•±Í”9½¹”4(€€€€€€€…Ý…¥ÐÍ•±˜¹}É…Ñ•}±¥µ¥Ñ•È¹…ÅÕ¥É”¡Í•±˜¹½¹™¥œ¹¥°Ý•¥¡ÐôÄ¤4(4(€€€€€€€ÑÉäè4(€€€€€€€€€€€½É‘•ÉÌ€ô…Ý…¥ÐÍ•±˜¹}µ…å‰•}…Ý…¥Ð¡Í•±˜¹•á¡…¹”¹™•Ñ¡}½Á•¹}½É‘•ÉÌ¡•á}Íåµ‰½°¤¤4(€€€€€€€€€€€É•ÑÕÉ¸mÍ•±˜¹}Á…ÉÍ•}½É‘•È¡¼°Í•±˜¹}áÑ}Ñ½}Õ¹¥™¥•‘}Íåµ‰½°¡½lÍåµ‰½°t¤¤™½È¼¥¸½É‘•ÉÍt4(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì”è4(€€€€€€€€€€€±½•È¹•ÉÉ½È¡˜‰™•Ñ¡}½Á•¹}½É‘•ÉÌ™…¥±•èí•ôˆ¤4(€€€€€€€€€€€É…¥Í”4(4(€€€…Íå¹Œ‘•˜™•Ñ¡}Á½Í¥Ñ¥½¹Ì¡Í•±˜°Íåµ‰½°èMåµ‰½°ð9½¹”€ô9½¹”¤€´ø±¥ÍÑmA½Í¥Ñ¥½¹tè4(€€€€€€€€ˆˆ‰•Ñ Á½Í¥Ñ¥½¹Ì€¡™½È™ÕÑÕÉ•Ì½Á•ÉÀ¤ˆˆˆ4(€€€€€€€…Ý…¥ÐÍ•±˜¹}É…Ñ•}±¥µ¥Ñ•È¹…ÅÕ¥É”¡Í•±˜¹½¹™¥œ¹¥°Ý•¥¡ÐôÄ¤4(4(€€€€€€€ÑÉäè4(€€€€€€€€€€€Á½Í¥Ñ¥½¹Ì€ô…Ý…¥ÐÍ•±˜¹}µ…å‰•}…Ý…¥Ð¡Í•±˜¹•á¡…¹”¹™•Ñ¡}Á½Í¥Ñ¥½¹Ì 4(€€€€€€€€€€€€€€€mÍ•±˜¹}Õ¹¥™¥•‘}Ñ½}áÑ}Íåµ‰½°¡Íåµ‰½°¥t¥˜Íåµ‰½°•±Í”9½¹”4(€€€€€€€€€€€€¤¤4(€€€€€€€€€€€É•ÑÕÉ¸mÍ•±˜¹}Á…ÉÍ•}Á½Í¥Ñ¥½¸¡À¤™½ÈÀ¥¸Á½Í¥Ñ¥½¹Ì¥˜™±½…Ð¡À¹•Ð ½¹ÑÉ…ÑÌœ°€À¤¤€„ô€Át4(€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì”è4(€€€€€€€€€€€±½•È¹•ÉÉ½È¡˜‰™•Ñ¡}Á½Í¥Ñ¥½¹Ì™…¥±•èí•ôˆ¤4(€€€€€€€€€€€É…¥Í”4(4(€€€€Œ€´´´A…ÉÍ¥¹œ¡•±Á•ÉÌ€´´´4(4(€€€‘•˜}Á…ÉÍ•}Ñ¥­•È¡Í•±˜°Ñ¥­•Èè‘¥Ð°Íåµ‰½°èMåµ‰½°¤€´øQ¥­•Èè(€€€€€€€Ñ¥µ•ÍÑ…µÁ}µÌ€ôÑ¥­•È¹•Ð Ñ¥µ•ÍÑ…µÀœ¤(€€€€€€€¥˜Ñ¥µ•ÍÑ…µÁ}µÌ¥Ì9½¹”è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È¡˜‰Q¥­•È™½ÈíÍåµ‰½°¹Á…¥Éô¡…Ì¹¼Ñ¥µ•ÍÑ…µÀˆ¤(€€€€€€€É•ÑÕÉ¸Q¥­•È (€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Ñ¥µ•ÍÑ…µÁ}µÌ€¼€ÄÀÀÀ°ÑèõUQ¤°(€€€€€€€€€€€‰¥õÑ¥­•Él‰¥t°4(€€€€€€€€€€€…Í¬õÑ¥­•Él…Í¬t°4(€€€€€€€€€€€±…ÍÐõÑ¥­•Él±…ÍÐt°4(€€€€€€€€€€€¡¥ õÑ¥­•Él¡¥ t°4(€€€€€€€€€€€±½ÜõÑ¥­•Él±½Üt°4(€€€€€€€€€€€½Á•¸õÑ¥­•Él½Á•¸t°4(€€€€€€€€€€€±½Í”õÑ¥­•Él±½Í”t°4(€€€€€€€€€€€‰…Í•}Ù½±Õµ”õÑ¥­•Él‰…Í•Y½±Õµ”t°4(€€€€€€€€€€€ÅÕ½Ñ•}Ù½±Õµ”õÑ¥­•ÉlÅÕ½Ñ•Y½±Õµ”t°4(€€€€€€€€€€€¡…¹”õÑ¥­•Él¡…¹”t°4(€€€€€€€€€€€Á•É•¹Ñ…”õÑ¥­•ÉlÁ•É•¹Ñ…”t°4(€€€€€€€€¤4(4(€€€‘•˜}Á…ÉÍ•}½É‘•É}‰½½¬¡Í•±˜°½ˆè‘¥Ð°Íåµ‰½°èMåµ‰½°¤€´ø=É‘•É	½½¬è4(€€€€€€€É•ÑÕÉ¸=É‘•É	½½¬ 4(€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°4(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡½‰lÑ¥µ•ÍÑ…µÀt€¼€ÄÀÀÀ°ÑèõUQ¤¥˜½‰lÑ¥µ•ÍÑ…µÀt•±Í”‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤°(€€€€€€€€€€€‰¥‘Ìõm=É‘•É	½½­1•Ù•°¡ÁÉ¥”õ™±½…Ð¡‰lÁt¤°Í¥é”õ™±½…Ð¡‰lÅt¤¤™½Èˆ¥¸½‰l‰¥‘Ìut°4(€€€€€€€€€€€…Í­Ìõm=É‘•É	½½­1•Ù•°¡ÁÉ¥”õ™±½…Ð¡…lÁt¤°Í¥é”õ™±½…Ð¡…lÅt¤¤™½È„¥¸½‰l…Í­Ìut°4(€€€€€€€€¤4(4(€€€‘•˜}Á…ÉÍ•}…¹‘±”¡Í•±˜°…¹‘±”è±¥ÍÐ°Íåµ‰½°èMåµ‰½°°Ñ¥µ•™É…µ”èÍÑÈ¤€´ø…¹‘±”è4(€€€€€€€É•ÑÕÉ¸…¹‘±” 4(€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°4(€€€€€€€€€€€Ñ¥µ•ÍÑ…µÀõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡…¹‘±•lÁt€¼€ÄÀÀÀ°ÑèõUQ¤°(€€€€€€€€€€€Ñ¥µ•™É…µ”õÑ¥µ•™É…µ”°4(€€€€€€€€€€€½Á•¸õ…¹‘±•lÅt°4(€€€€€€€€€€€¡¥ õ…¹‘±•lÉt°4(€€€€€€€€€€€±½Üõ…¹‘±•lÍt°4(€€€€€€€€€€€±½Í”õ…¹‘±•lÑt°4(€€€€€€€€€€€Ù½±Õµ”õ…¹‘±•lÕt°4(€€€€€€€€¤4(4(€€€‘•˜}Á…ÉÍ•}‰…±…¹”¡Í•±˜°‰…±…¹”è‘¥Ð¤€´ø‘¥ÑmÍÍ•Ñ±…ÍÌ°	…±…¹•tè4(€€€€€€€É•ÍÕ±Ð€ôíô4(€€€€€€€™½ÈÕÉÉ•¹ä°…µ½Õ¹ÑÌ¥¸‰…±…¹”¹¥Ñ•µÌ ¤è4(€€€€€€€€€€€¥˜ÕÉÉ•¹ä¥¸€ ¥¹™¼œ°€™É•”œ°€ÕÍ•œ°€Ñ½Ñ…°œ°€Ñ¥µ•ÍÑ…µÀœ°€‘…Ñ•Ñ¥µ”œ¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡…µ½Õ¹ÑÌ°‘¥Ð¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€™É•”€ô™±½…Ð¡…µ½Õ¹ÑÌ¹•Ð ™É•”œ°€À¤¤4(€€€€€€€€€€€ÕÍ•€ô™±½…Ð¡…µ½Õ¹ÑÌ¹•Ð ÕÍ•œ°€À¤¤4(€€€€€€€€€€€Ñ½Ñ…°€ô™±½…Ð¡…µ½Õ¹ÑÌ¹•Ð Ñ½Ñ…°œ°€À¤¤4(€€€€€€€€€€€¥˜Ñ½Ñ…°€ø€Àè4(€€€€€€€€€€€€€€€€Œ•Ñ•Éµ¥¹”…ÍÍ•Ð±…ÍÌ™É½´ÕÉÉ•¹ä4(€€€€€€€€€€€€€€€…ÍÍ•Ñ}±…ÍÌ€ôÍÍ•Ñ±…ÍÌ¹IeAQ<¥˜ÕÉÉ•¹ä¹½Ð¥¸€ UMœ°€UHœ°€	@œ°€)Adœ¤•±Í”ÍÍ•Ñ±…ÍÌ¹=I`4(€€€€€€€€€€€€€€€¥˜…ÍÍ•Ñ}±…ÍÌ¹½Ð¥¸É•ÍÕ±Ðè4(€€€€€€€€€€€€€€€€€€€É•ÍÕ±Ñm…ÍÍ•Ñ}±…ÍÍt€ô	…±…¹”¡…ÍÍ•Ñ}±…ÍÌõ…ÍÍ•Ñ}±…ÍÌ¤4(€€€€€€€€€€€€€€€É•ÍÕ±Ñm…ÍÍ•Ñ}±…ÍÍt¹…ÍÍ•ÑÍmÕÉÉ•¹åt€ôì4(€€€€€€€€€€€€€€€€€€€€™É•”œè™É•”°€ÕÍ•œèÕÍ•°€Ñ½Ñ…°œèÑ½Ñ…°4(€€€€€€€€€€€€€€€ô4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}áÑ}½É‘•É}ÑåÁ”¡½É‘•Èè=É‘•È¤€´øÍÑÈè(€€€€€€€€ˆˆ‰5…À¥¹Ñ•É¹…°ÁÉ½Ñ•Ñ¥Ù”ÑåÁ•Ì½¹Ñ¼aP½¹‘¥Ñ¥½¹…°½É‘•ÉÌ¸ˆˆˆ((€€€€€€€¥˜½É‘•È¹ÑåÁ”€ôô=É‘•ÉQåÁ”¹MQ=@è(€€€€€€€€€€€É•ÑÕÉ¸€‰µ…É­•Ðˆ(€€€€€€€¥˜½É‘•È¹ÑåÁ”€ôô=É‘•ÉQåÁ”¹MQ=A}1%5%Pè(€€€€€€€€€€€É•ÑÕÉ¸€‰±¥µ¥Ðˆ(€€€€€€€É•ÑÕÉ¸½É‘•È¹ÑåÁ”¹Ù…±Õ”¹±½Ý•È ¤((€€€‘•˜}½É‘•É}Ñ½}áÑ}Á…É…µÌ¡Í•±˜°½É‘•Èè=É‘•È¤€´ø‘¥Ðè(€€€€€€€Á…É…µÌ€ôíô(€€€€€€€€Œ	¥¹…¹”£†î$£†ê•À¹£†êµ¸Ñ¥µ•%¹½É”¡¼±¥µ¥Ð½É‘•ÉÌìµ…É­•Ð½É‘•ÉÌ4(€€€€€€€€ŒÏ†êô‹†î,É•©•Ð€ ´ÄÄÀØ¤»†êýÔŸ†îµ¤¯¡´¸4(€€€€€€€¥˜½É‘•È¹Ñ¥µ•}¥¹}™½É”…¹½É‘•È¹ÑåÁ”¥¸í=É‘•ÉQåÁ”¹1%5%P°=É‘•ÉQåÁ”¹MQ=A}1%5%Qôè(€€€€€€€€€€€Á…É…µÍlÑ¥µ•%¹½É”t€ô½É‘•È¹Ñ¥µ•}¥¹}™½É”¹Ù…±Õ”(€€€€€€€¥˜½É‘•È¹ÑåÁ”¥¸í=É‘•ÉQåÁ”¹MQ=@°=É‘•ÉQåÁ”¹MQ=A}1%5%Qôè(€€€€€€€€€€€¥˜½É‘•È¹ÍÑ½Á}ÁÉ¥”¥Ì9½¹”½È½É‘•È¹ÍÑ½Á}ÁÉ¥”€ðô€Àè(€€€€€€€€€€€€€€€É…¥Í”%¹Ù…±¥‘=É‘•È ‰ÁÉ½Ñ•Ñ¥Ù”ÍÑ½À½É‘•ÉÌÉ•ÅÕ¥É”„Á½Í¥Ñ¥Ù”ÍÑ½ÀÁÉ¥”ˆ¤(€€€€€€€€€€€¥˜½É‘•È¹ÑåÁ”€ôô=É‘•ÉQåÁ”¹MQ=A}1%5%P…¹€ (€€€€€€€€€€€€€€€½É‘•È¹ÁÉ¥”¥Ì9½¹”½È½É‘•È¹ÁÉ¥”€ðô€À(€€€€€€€€€€€€¤è(€€€€€€€€€€€€€€€É…¥Í”%¹Ù…±¥‘=É‘•È ‰ÍÑ½Àµ±¥µ¥Ð½É‘•ÉÌÉ•ÅÕ¥É”„Á½Í¥Ñ¥Ù”±¥µ¥ÐÁÉ¥”ˆ¤(€€€€€€€€€€€Á…É…µÍlÍÑ½Á1½ÍÍAÉ¥”t€ô™±½…Ð¡½É‘•È¹ÍÑ½Á}ÁÉ¥”¤(€€€€€€€¥˜½É‘•È¹É•‘Õ•}½¹±äè4(€€€€€€€€€€€Á…É…µÍlÉ•‘Õ•=¹±ät€ôQÉÕ”4(€€€€€€€¥˜½É‘•È¹Á½ÍÑ}½¹±äè4(€€€€€€€€€€€Á…É…µÍlÁ½ÍÑ=¹±ät€ôQÉÕ”4(€€€€€€€¥˜½É‘•È¹±¥•¹Ñ}½É‘•É}¥è4(€€€€€€€€€€€Á…É…µÍl±¥•¹Ñ=É‘•É%t€ô½É‘•È¹±¥•¹Ñ}½É‘•É}¥4(€€€€€€€É•ÑÕÉ¸Á…É…µÌ4(4(€€€‘•˜}Á…ÉÍ•}½É‘•È¡Í•±˜°½É‘•Èè‘¥Ð°Íåµ‰½°èMåµ‰½°¤€´ø=É‘•Èè4(€€€€€€€ÍÑ…ÑÕÍ}µ…À€ôì4(€€€€€€€€€€€€½Á•¸œè=É‘•ÉMÑ…ÑÕÌ¹=A8°4(€€€€€€€€€€€€±½Í•œè=É‘•ÉMÑ…ÑÕÌ¹%11°4(€€€€€€€€€€€€…¹•±•œè=É‘•ÉMÑ…ÑÕÌ¹911°4(€€€€€€€€€€€€É•©•Ñ•œè=É‘•ÉMÑ…ÑÕÌ¹I)Q°4(€€€€€€€€€€€€•áÁ¥É•œè=É‘•ÉMÑ…ÑÕÌ¹aA%I°4(€€€€€€€ô4(€€€€€€€™¥±±•‘}Í¥é”€ô•¥µ…°¡ÍÑÈ¡½É‘•È¹•Ð ™¥±±•œ¤½È€À¤¤(€€€€€€€Á…ÉÍ•‘}ÍÑ…ÑÕÌ€ôÍÑ…ÑÕÍ}µ…À¹•Ð¡½É‘•ÉlÍÑ…ÑÕÌt°=É‘•ÉMÑ…ÑÕÌ¹=A8¤(€€€€€€€¥˜Á…ÉÍ•‘}ÍÑ…ÑÕÌ€ôô=É‘•ÉMÑ…ÑÕÌ¹=A8…¹™¥±±•‘}Í¥é”€ø€Àè(€€€€€€€€€€€Á…ÉÍ•‘}ÍÑ…ÑÕÌ€ô=É‘•ÉMÑ…ÑÕÌ¹AIQ%0(€€€€€€€É…Ý}ÑåÁ”€ôÍÑÈ¡½É‘•ÉlÑåÁ”t¤¹±½Ý•È ¤(€€€€€€€Á…ÉÍ•‘}ÑåÁ”€ôì(€€€€€€€€€€€€ÍÑ½Àœè=É‘•ÉQåÁ”¹MQ=@°(€€€€€€€€€€€€ÍÑ½Á}±½ÍÌœè=É‘•ÉQåÁ”¹MQ=@°(€€€€€€€€€€€€ÍÑ½Á}µ…É­•Ðœè=É‘•ÉQåÁ”¹MQ=@°(€€€€€€€€€€€€ÍÑ½Á}±¥µ¥Ðœè=É‘•ÉQåÁ”¹MQ=A}1%5%P°(€€€€€€€€€€€€ÍÑ½Á}±½ÍÍ}±¥µ¥Ðœè=É‘•ÉQåÁ”¹MQ=A}1%5%P°(€€€€€€€ô¹•Ð¡É…Ý}ÑåÁ”¤(€€€€€€€¥˜Á…ÉÍ•‘}ÑåÁ”¥Ì9½¹”è(€€€€€€€€€€€Á…ÉÍ•‘}ÑåÁ”€ô=É‘•ÉQåÁ”¡É…Ý}ÑåÁ”¤(€€€€€€€ÍÑ½Á}ÁÉ¥”€ô½É‘•È¹•Ð ÍÑ½ÁAÉ¥”œ¤(€€€€€€€¥˜ÍÑ½Á}ÁÉ¥”¥Ì9½¹”è(€€€€€€€€€€€ÍÑ½Á}ÁÉ¥”€ô½É‘•È¹•Ð ÑÉ¥•ÉAÉ¥”œ¤(€€€€€€€É•ÑÕÉ¸=É‘•È (€€€€€€€€€€€¥õ½É‘•Él¥t°(€€€€€€€€€€€±¥•¹Ñ}½É‘•É}¥õ½É‘•È¹•Ð ±¥•¹Ñ=É‘•É%œ¤°(€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°(€€€€€€€€€€€Í¥‘”õ=É‘•ÉM¥‘”¡ÍÑÈ¡½É‘•ÉlÍ¥‘”t¤¹±½Ý•È ¤¤°(€€€€€€€€€€€ÑåÁ”õÁ…ÉÍ•‘}ÑåÁ”°(€€€€€€€€€€€ÍÑ…ÑÕÌõÁ…ÉÍ•‘}ÍÑ…ÑÕÌ°(€€€€€€€€€€€Í¥é”õ•¥µ…°¡ÍÑÈ¡½É‘•È¹•Ð …µ½Õ¹Ðœ¤½È€À¤¤°(€€€€€€€€€€€™¥±±•‘}Í¥é”õ™¥±±•‘}Í¥é”°(€€€€€€€€€€€…Ù}™¥±±}ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡½É‘•È¹•Ð …Ù•É…”œ¤½È€À¤¤°(€€€€€€€€€€€ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡½É‘•ÉlÁÉ¥”t¤¤¥˜½É‘•È¹•Ð ÁÉ¥”œ¤¥Ì¹½Ð9½¹”•±Í”9½¹”°(€€€€€€€€€€€ÍÑ½Á}ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡ÍÑ½Á}ÁÉ¥”¤¤¥˜ÍÑ½Á}ÁÉ¥”¥Ì¹½Ð9½¹”•±Í”9½¹”°(€€€€€€€€€€€™•”õ•¥µ…°¡ÍÑÈ¡½É‘•Él™•”ul½ÍÐt¤¤¥˜½É‘•È¹•Ð ™•”œ¤•±Í”•¥µ…° À¤°(€€€€€€€€€€€Ñ¥µ•}¥¹}™½É”õQ¥µ•%¹½É”¡ÍÑÈ¡½É‘•ÉlÑ¥µ•%¹½É”t¤¹±½Ý•È ¤¤¥˜½É‘•È¹•Ð Ñ¥µ•%¹½É”œ¤•±Í”Q¥µ•%¹½É”¹Q°4(€€€€€€€€€€€É•‘Õ•}½¹±äõ½É‘•È¹•Ð É•‘Õ•=¹±äœ°…±Í”¤°4(€€€€€€€€€€€Á½ÍÑ}½¹±äõ½É‘•È¹•Ð Á½ÍÑ=¹±äœ°…±Í”¤°4(€€€€€€€€€€€É•…Ñ•‘}…Ðõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡½É‘•ÉlÑ¥µ•ÍÑ…µÀt€¼€ÄÀÀÀ°ÑèõUQ¤¥˜½É‘•È¹•Ð Ñ¥µ•ÍÑ…µÀœ¤•±Í”‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤°(€€€€€€€€€€€ÕÁ‘…Ñ•‘}…Ðõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡½É‘•Él±…ÍÑQÉ…‘•Q¥µ•ÍÑ…µÀt€¼€ÄÀÀÀ°ÑèõUQ¤¥˜½É‘•È¹•Ð ±…ÍÑQÉ…‘•Q¥µ•ÍÑ…µÀœ¤•±Í”9½¹”°(€€€€€€€€¤4(4(€€€‘•˜}Á…ÉÍ•}Á½Í¥Ñ¥½¸¡Í•±˜°Á½Ìè‘¥Ð¤€´øA½Í¥Ñ¥½¸è4(€€€€€€€Íåµ‰½°€ôÍ•±˜¹•Ñ}Õ¹¥™¥•‘}Íåµ‰½°¡Á½ÍlÍåµ‰½°t¤4(€€€€€€€É•ÑÕÉ¸A½Í¥Ñ¥½¸ 4(€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°4(€€€€€€€€€€€Í¥é”õ•¥µ…°¡ÍÑÈ¡Á½Íl½¹ÑÉ…ÑÌt¤¤°4(€€€€€€€€€€€•¹ÑÉå}ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡Á½Íl•¹ÑÉåAÉ¥”t¤¤°4(€€€€€€€€€€€µ…É­}ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡Á½Ílµ…É­AÉ¥”t¤¤°4(€€€€€€€€€€€Õ¹É•…±¥é•‘}Á¹°õ•¥µ…°¡ÍÑÈ¡Á½Ì¹•Ð Õ¹É•…±¥é•‘A¹°œ°€À¤¤¤°4(€€€€€€€€€€€É•…±¥é•‘}Á¹°õ•¥µ…°¡ÍÑÈ¡Á½Ì¹•Ð É•…±¥é•‘A¹°œ°€À¤¤¤°4(€€€€€€€€€€€±•Ù•É…”õ•¥µ…°¡ÍÑÈ¡Á½Íl±•Ù•É…”t¤¤¥˜Á½Ì¹•Ð ±•Ù•É…”œ¤•±Í”•¥µ…° Ä¤°4(€€€€€€€€€€€±¥ÅÕ¥‘…Ñ¥½¹}ÁÉ¥”õ•¥µ…°¡ÍÑÈ¡Á½Íl±¥ÅÕ¥‘…Ñ¥½¹AÉ¥”t¤¤¥˜Á½Ì¹•Ð ±¥ÅÕ¥‘…Ñ¥½¹AÉ¥”œ¤•±Í”9½¹”°4(€€€€€€€€€€€ÕÁ‘…Ñ•‘}…Ðõ‘…Ñ•Ñ¥µ”¹™É½µÑ¥µ•ÍÑ…µÀ¡Á½ÍlÑ¥µ•ÍÑ…µÀt€¼€ÄÀÀÀ°ÑèõUQ¤¥˜Á½Ì¹•Ð Ñ¥µ•ÍÑ…µÀœ¤•±Í”‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤°(€€€€€€€€¤4(4(€€€‘•˜•Ñ}ÍÑ…ÑÕÌ¡Í•±˜¤€´øá¡…¹•MÑ…ÑÕÌè4(€€€€€€€É•ÑÕÉ¸Í•±˜¹}ÍÑ…ÑÕÌ4(4(€€€‘•˜¥Í}¡•…±Ñ¡ä¡Í•±˜¤€´ø‰½½°è4(€€€€€€€É•ÑÕÉ¸Í•±˜¹}ÍÑ…ÑÕÌ€ôôá¡…¹•MÑ…ÑÕÌ¹!1Q!d…¹Í•±˜¹}½¹¹•Ñ•4(4(4)±…ÍÌ5Õ±Ñ¥á¡…¹•5…¹…•Èè4(€€€€ˆˆ‰5…¹…•ÌµÕ±Ñ¥Á±”•á¡…¹”½¹¹•Ñ¥½¹Ìˆˆˆ4(4(€€€‘•˜}}¥¹¥Ñ}|¡Í•±˜¤è4(€€€€€€€Í•±˜¹•á¡…¹•Ìè‘¥ÑmÍÑÈ°aQ‘…ÁÑ•Ét€ôíô4(€€€€€€€Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”èÍÑÈð9½¹”€ô9½¹”4(4(€€€‘•˜…‘‘}•á¡…¹”¡Í•±˜°½¹™¥œèá¡…¹•½¹™¥œ¤€´øaQ‘…ÁÑ•Èè4(€€€€€€€€ˆˆ‰‘…¸•á¡…¹”Ñ¼Ñ¡”µ…¹…•Èˆˆˆ4(€€€€€€€…‘…ÁÑ•È€ôaQ‘…ÁÑ•È¡½¹™¥œ¤4(€€€€€€€Í•±˜¹•á¡…¹•Ím½¹™¥œ¹¥‘t€ô…‘…ÁÑ•È4(€€€€€€€¥˜Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”¥Ì9½¹”è4(€€€€€€€€€€€Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”€ô½¹™¥œ¹¥4(€€€€€€€É•ÑÕÉ¸…‘…ÁÑ•È4(4(€€€‘•˜•Ñ}•á¡…¹”¡Í•±˜°•á¡…¹•}¥èÍÑÈ¤€´øaQ‘…ÁÑ•Èð9½¹”è4(€€€€€€€É•ÑÕÉ¸Í•±˜¹•á¡…¹•Ì¹•Ð¡•á¡…¹•}¥¤4(4(€€€‘•˜•Ñ}ÁÉ¥µ…Éä¡Í•±˜¤€´øaQ‘…ÁÑ•Èð9½¹”è4(€€€€€€€É•ÑÕÉ¸Í•±˜¹•á¡…¹•Ì¹•Ð¡Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”¤¥˜Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”•±Í”9½¹”4(4(€€€‘•˜Í•Ñ}ÁÉ¥µ…Éä¡Í•±˜°•á¡…¹•}¥èÍÑÈ¤€´ø9½¹”è4(€€€€€€€¥˜•á¡…¹•}¥¥¸Í•±˜¹•á¡…¹•Ìè4(€€€€€€€€€€€Í•±˜¹}ÁÉ¥µ…Éå}•á¡…¹”€ô•á¡…¹•}¥4(4(€€€…Íå¹Œ‘•˜½¹¹•Ñ}…±°¡Í•±˜¤€´ø‘¥ÑmÍÑÈ°‰½½±tè4(€€€€€€€€ˆˆ‰½¹¹•ÐÑ¼…±°•¹…‰±••á¡…¹•Ìˆˆˆ4(€€€€€€€É•ÍÕ±ÑÌ€ôíô4(€€€€€€€™½È•á¡…¹•}¥°…‘…ÁÑ•È¥¸Í•±˜¹•á¡…¹•Ì¹¥Ñ•µÌ ¤è4(€€€€€€€€€€€¥˜¹½Ð…‘…ÁÑ•È¹½¹™¥œ¹•¹…‰±•è4(€€€€€€€€€€€€€€€É•ÍÕ±ÑÍm•á¡…¹•}¥‘t€ô…±Í”4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€…Ý…¥Ð…‘…ÁÑ•È¹½¹¹•Ð ¤4(€€€€€€€€€€€€€€€É•ÍÕ±ÑÍm•á¡…¹•}¥‘t€ôQÉÕ”4(€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì”è4(€€€€€€€€€€€€€€€±½•È¹•ÉÉ½È¡˜‰…¥±•Ñ¼½¹¹•ÐÑ¼í•á¡…¹•}¥‘ôèí•ôˆ¤4(€€€€€€€€€€€€€€€É•ÍÕ±ÑÍm•á¡…¹•}¥‘t€ô…±Í”4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±ÑÌ4(4(€€€…Íå¹Œ‘•˜‘¥Í½¹¹•Ñ}…±°¡Í•±˜¤€´ø9½¹”è4(€€€€€€€™½È…‘…ÁÑ•È¥¸Í•±˜¹•á¡…¹•Ì¹Ù…±Õ•Ì ¤è4(€€€€€€€€€€€…Ý…¥Ð…‘…ÁÑ•È¹‘¥Í½¹¹•Ð ¤4(4(€€€‘•˜•Ñ}¡•…±Ñ¡å}•á¡…¹•Ì¡Í•±˜¤€´ø±¥ÍÑmaQ‘…ÁÑ•Étè4(€€€€€€€É•ÑÕÉ¸m•à™½È•à¥¸Í•±˜¹•á¡…¹•Ì¹Ù…±Õ•Ì ¤¥˜•à¹¥Í}¡•…±Ñ¡ä ¥t4(4(€€€…Íå¹Œ‘•˜™•Ñ¡}Ñ¥­•É}…±°¡Í•±˜°Íåµ‰½°èMåµ‰½°¤€´ø‘¥ÑmÍÑÈ°Q¥­•Étè4(€€€€€€€€ˆˆ‰•Ñ Ñ¥­•È™É½´…±°¡•…±Ñ¡ä•á¡…¹•Ìˆˆˆ4(€€€€€€€É•ÍÕ±ÑÌ€ôíô4(€€€€€€€™½È•á¡…¹•}¥°…‘…ÁÑ•È¥¸Í•±˜¹•á¡…¹•Ì¹¥Ñ•µÌ ¤è4(€€€€€€€€€€€¥˜¹½Ð…‘…ÁÑ•È¹¥Í}¡•…±Ñ¡ä ¤è4(€€€€€€€€€€€€€€€½¹Ñ¥¹Õ”4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€Ñ¥­•È€ô…Ý…¥Ð…‘…ÁÑ•È¹™•Ñ¡}Ñ¥­•È¡Íåµ‰½°¤4(€€€€€€€€€€€€€€€É•ÍÕ±ÑÍm•á¡…¹•}¥‘t€ôÑ¥­•È4(€€€€€€€€€€€•á•ÁÐá•ÁÑ¥½¸…Ì”è4(€€€€€€€€€€€€€€€±½•È¹‘•‰Õœ¡˜‰™•Ñ¡}Ñ¥­•È™…¥±•½¸í•á¡…¹•}¥‘ôèí•ôˆ¤4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±ÑÌ4(4(€€€…Íå¹Œ‘•˜™•Ñ¡}‰•ÍÑ}‰¥‘}…Í¬¡Í•±˜°Íåµ‰½°èMåµ‰½°¤€´øÑÕÁ±•mQ¥­•Èð9½¹”°Q¥­•Èð9½¹•tè4(€€€€€€€€ˆˆ‰•Ð‰•ÍÐ‰¥½…Í¬…É½ÍÌ…±°•á¡…¹•Ìˆˆˆ4(€€€€€€€Ñ¥­•ÉÌ€ô…Ý…¥ÐÍ•±˜¹™•Ñ¡}Ñ¥­•É}…±°¡Íåµ‰½°¤4(€€€€€€€¥˜¹½ÐÑ¥­•ÉÌè4(€€€€€€€€€€€É•ÑÕÉ¸9½¹”°9½¹”4(4(€€€€€€€‰•ÍÑ}‰¥€ôµ…à¡Ñ¥­•ÉÌ¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„ÐèÐ¹‰¥½È€À¤4(€€€€€€€‰•ÍÑ}…Í¬€ôµ¥¸¡Ñ¥­•ÉÌ¹Ù…±Õ•Ì ¤°­•äõ±…µ‰‘„ÐèÐ¹…Í¬½È™±½…Ð ¥¹˜œ¤¤4(€€€€€€€É•ÑÕÉ¸‰•ÍÑ}‰¥°‰•ÍÑ}…Í¬4(4(4(Œ€´´´AÉ•‘•™¥¹••á¡…¹”½¹™¥Ì€´´´4(4)‘•˜•Ñ}‘•™…Õ±Ñ}•á¡…¹•}½¹™¥Ì ¤€´ø±¥ÍÑmá¡…¹•½¹™¥tè4(€€€€ˆˆ‰•Ð‘•™…Õ±Ð½¹™¥ÕÉ…Ñ¥½¹Ì™½Èµ…©½È•á¡…¹•Ìˆˆˆ4(€€€É•ÑÕÉ¸l4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô‰¥¹…¹”œ°4(€€€€€€€€€€€¹…µ”ô	¥¹…¹”œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÈÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU1t°4(€€€€€€€€€€€½ÁÑ¥½¹Ìõì‘•™…Õ±ÑQåÁ”œè€ÍÁ½Ðô°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô‰å‰¥Ðœ°4(€€€€€€€€€€€¹…µ”ô	å‰¥Ðœ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU1t°4(€€€€€€€€€€€½ÁÑ¥½¹Ìõì‘•™…Õ±ÑQåÁ”œè€ÍÁ½Ðô°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô½­àœ°4(€€€€€€€€€€€¹…µ”ô=-`œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU0°5…É­•ÑQåÁ”¹=AQ%=9Mt°4(€€€€€€€€€€€½ÁÑ¥½¹Ìõì‘•™…Õ±ÑQåÁ”œè€ÍÁ½Ðô°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô½¥¹‰…Í”œ°4(€€€€€€€€€€€¹…µ”ô½¥¹‰…Í”œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=Qt°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô­É…­•¸œ°4(€€€€€€€€€€€¹…µ”ô-É…­•¸œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIMt°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô…Ñ•¥¼œ°4(€€€€€€€€€€€¹…µ”ô…Ñ”¹¥¼œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU1t°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô­Õ½¥¸œ°4(€€€€€€€€€€€¹…µ”ô-Õ½¥¸œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU1t°4(€€€€€€€€¤°4(€€€€€€€á¡…¹•½¹™¥œ 4(€€€€€€€€€€€¥ô¡Ñàœ°4(€€€€€€€€€€€¹…µ”ô!Q`€¡!Õ½‰¤¤œ°4(€€€€€€€€€€€Í…¹‘‰½àõ…±Í”°4(€€€€€€€€€€€É…Ñ•}±¥µ¥ÐôÄÀÀÀ°4(€€€€€€€€€€€µ…É­•ÑÌõm5…É­•ÑQåÁ”¹MA=P°5…É­•ÑQåÁ”¹UQUIL°5…É­•ÑQåÁ”¹AIAQU1t°4(€€€€€€€€¤°4(€€€t4(4(4)…Íå¹Œ‘•˜É•…Ñ•}µÕ±Ñ¥}•á¡…¹•}µ…¹…•È¡½¹™¥Ìè±¥ÍÑmá¡…¹•½¹™¥tð9½¹”€ô9½¹”¤€´ø5Õ±Ñ¥á¡…¹•5…¹…•Èè4(€€€€ˆˆ‰É•…Ñ”…¹½¹¹•ÐµÕ±Ñ¤µ•á¡…¹”µ…¹…•Èˆˆˆ4(€€€µ…¹…•È€ô5Õ±Ñ¥á¡…¹•5…¹…•È ¤4(4(€€€¥˜½¹™¥Ì¥Ì9½¹”è4(€€€€€€€½¹™¥Ì€ô•Ñ}‘•™…Õ±Ñ}•á¡…¹•}½¹™¥Ì ¤4(4(€€€™½È½¹™¥œ¥¸½¹™¥Ìè4(€€€€€€€µ…¹…•È¹…‘‘}•á¡…¹”¡½¹™¥œ¤4(4(€€€…Ý…¥Ðµ…¹…•È¹½¹¹•Ñ}…±° ¤4(€€€É•ÑÕÉ¸µ…¹…•È