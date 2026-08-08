"""
Alpaca Adapter - US Stocks Trading (Paper + Live)

Supports:
- Alpaca Trade API v2 (REST + WebSocket)
- Paper trading and live trading
- Fractional shares
- Real-time market data via WebSocket
- Polygon.io integration for historical data
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

try:
    import alpaca.trading.client as trading_client
    import alpaca.trading.enums as trading_enums
    import alpaca.trading.models as trading_models
    import alpaca.trading.requests as trading_requests
    import alpaca.data.historical as data_historical
    import alpaca.data.requests as data_requests
    import alpaca.data as data_module
    from alpaca.data import TimeFrame, TimeFrameUnit
    from alpaca.common.exceptions import APIError
    ALPACA_AVAILABLE = True
except ImportError:
    # Optional SDK — adapter is importable without alpaca-py (e.g. in CI),
    # but raises a clear error when used.
    trading_client = trading_enums = trading_models = trading_requests = None
    data_historical = data_requests = data_module = None
    TimeFrame = TimeFrameUnit = None
    APIError = Exception
    ALPACA_AVAILABLE = False

from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, OrderSide, OrderType,
    OrderStatus, TimeInForce, Order, Position, Balance,
    Ticker, Candle
)

logger = logging.getLogger(__name__)

if not ALPACA_AVAILABLE:
    logger.warning("alpaca-py not installed — AlpacaAdapter disabled (pip install alpaca-py)")


@dataclass
class AlpacaConfig:
    """Alpaca API configuration"""
    api_key: str
    secret_key: str
    paper: bool = True
    base_url: str = ""  # Auto-detected from paper flag
    data_feed: str = "sip"  # "sip" or "iex" or "otc"
    timeout: int = 30


class AlpacaAdapter:
    """Alpaca trading adapter for US stocks"""

    def __init__(self, config: AlpacaConfig):
        self.config = config
        self._trading_client: Optional[trading_client.TradingClient] = None
        self._data_client: Optional[data_historical.StockHistoricalDataClient] = None
        self._ws_client = None
        self._connected = False
        self._symbol_cache: dict[str, dict] = {}

    async def connect(self) -> None:
        """Initialize Alpaca clients"""
        if not ALPACA_AVAILABLE:
            raise RuntimeError("alpaca-py is not installed — run `pip install alpaca-py`")
        try:
            self._trading_client = trading_client.TradingClient(
                api_key=self.config.api_key,
                secret_key=self.config.secret_key,
                paper=self.config.paper,
            )
            self._data_client = data_historical.StockHistoricalDataClient(
                api_key=self.config.api_key,
                secret_key=self.config.secret_key,
            )

            # Test connection
            account = self._trading_client.get_account()
            logger.info(f"Alpaca connected: {account.id}, paper={self.config.paper}, equity={account.equity}")
            self._connected = True

        except APIError as e:
            logger.error(f"Alpaca connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Close connections"""
        if self._ws_client:
            await self._ws_client.close()
        self._connected = False
        logger.info("Alpaca disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # --- Account & Positions ---

    async def fetch_balance(self) -> dict[AssetClass, Balance]:
        """Fetch account balances"""
        try:
            account = self._trading_client.get_account()
            assets = {
                'USD': {
                    'free': float(account.cash),
                    'used': float(account.initial_margin),
                    'total': float(account.equity),
                }
            }
            return {AssetClass.STOCK: Balance(asset_class=AssetClass.STOCK, assets=assets)}
        except APIError as e:
            logger.error(f"fetch_balance failed: {e}")
            raise

    async def fetch_positions(self, symbol: Symbol | None = None) -> list[Position]:
        """Fetch current positions"""
        try:
            positions = self._trading_client.get_all_positions()
            result = []
            for pos in positions:
                sym = self._alpaca_to_unified_symbol(pos.symbol)
                if symbol and sym != symbol:
                    continue
                result.append(Position(
                    symbol=sym,
                    size=Decimal(str(pos.qty)),
                    entry_price=Decimal(str(pos.avg_entry_price)),
                    mark_price=Decimal(str(pos.current_price)),
                    unrealized_pnl=Decimal(str(pos.unrealized_pl)),
                    realized_pnl=Decimal(0),
                    leverage=Decimal(1),
                    updated_at=datetime.now(),
                ))
            return result
        except APIError as e:
            logger.error(f"fetch_positions failed: {e}")
            raise

    # --- Market Data ---

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch latest ticker"""
        try:
            request = data_requests.StockLatestQuoteRequest(
                symbol_or_symbols=symbol.base,
                feed=self.config.data_feed,
            )
            quote = self._data_client.get_stock_latest_quote(request)
            q = quote[symbol.base]

            return Ticker(
                symbol=symbol,
                timestamp=q.timestamp,
                bid=Decimal(str(q.bid_price)) if q.bid_price else None,
                ask=Decimal(str(q.ask_price)) if q.ask_price else None,
                last=Decimal(str(q.bid_price)) if q.bid_price else None,
            )
        except APIError as e:
            logger.error(f"fetch_ticker failed for {symbol}: {e}")
            raise

    async def fetch_bars(
        self,
        symbol: Symbol,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100
    ) -> list[Candle]:
        """Fetch OHLCV bars"""
        try:
            tf_map = {
                "1m": TimeFrame(1, TimeFrameUnit.Minute),
                "5m": TimeFrame(5, TimeFrameUnit.Minute),
                "15m": TimeFrame(15, TimeFrameUnit.Minute),
                "1h": TimeFrame(1, TimeFrameUnit.Hour),
                "1d": TimeFrame(1, TimeFrameUnit.Day),
            }
            tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Day))

            request = data_requests.StockBarsRequest(
                symbol_or_symbols=symbol.base,
                timeframe=tf,
                start=start,
                end=end,
                limit=limit,
                feed=self.config.data_feed,
            )
            bars = self._data_client.get_stock_bars(request)
            return [self._parse_bar(b, symbol, timeframe) for b in bars[symbol.base]]
        except APIError as e:
            logger.error(f"fetch_bars failed for {symbol}: {e}")
            raise

    # --- Trading ---

    async def create_order(self, order: Order) -> Order:
        """Submit order to Alpaca"""
        try:
            side_map = {
                OrderSide.BUY: trading_enums.OrderSide.BUY,
                OrderSide.SELL: trading_enums.OrderSide.SELL,
            }
            type_map = {
                OrderType.MARKET: trading_enums.OrderType.MARKET,
                OrderType.LIMIT: trading_enums.OrderType.LIMIT,
                OrderType.STOP: trading_enums.OrderType.STOP,
                OrderType.STOP_LIMIT: trading_enums.OrderType.STOP_LIMIT,
            }
            tif_map = {
                TimeInForce.GTC: trading_enums.TimeInForce.GTC,
                TimeInForce.IOC: trading_enums.TimeInForce.IOC,
                TimeInForce.FOK: trading_enums.TimeInForce.FOK,
                # GTD natively unsupported by alpaca-py — gracefully fall back to GTC
                TimeInForce.GTD: getattr(trading_enums.TimeInForce, "GTD", trading_enums.TimeInForce.GTC),
            }

            req = trading_requests.MarketOrderRequest(
                symbol=order.symbol.base,
                qty=float(order.size),
                side=side_map[order.side],
                type=type_map.get(order.type, trading_enums.OrderType.MARKET),
                time_in_force=tif_map.get(order.time_in_force, trading_enums.TimeInForce.GTC),
                limit_price=float(order.price) if order.price else None,
                stop_price=float(order.stop_price) if order.stop_price else None,
                client_order_id=order.client_order_id,
            )

            if order.type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
                req = trading_requests.LimitOrderRequest(
                    symbol=order.symbol.base,
                    qty=float(order.size),
                    side=side_map[order.side],
                    limit_price=float(order.price),
                    time_in_force=tif_map.get(order.time_in_force, trading_enums.TimeInForce.GTC),
                    client_order_id=order.client_order_id,
                )
            elif order.type == OrderType.STOP:
                req = trading_requests.StopOrderRequest(
                    symbol=order.symbol.base,
                    qty=float(order.size),
                    side=side_map[order.side],
                    stop_price=float(order.stop_price),
                    time_in_force=tif_map.get(order.time_in_force, trading_enums.TimeInForce.GTC),
                    client_order_id=order.client_order_id,
                )
            elif order.type == OrderType.STOP_LIMIT:
                req = trading_requests.StopLimitOrderRequest(
                    symbol=order.symbol.base,
                    qty=float(order.size),
                    side=side_map[order.side],
                    stop_price=float(order.stop_price),
                    limit_price=float(order.price),
                    time_in_force=tif_map.get(order.time_in_force, trading_enums.TimeInForce.GTC),
                    client_order_id=order.client_order_id,
                )

            result = self._trading_client.submit_order(req)
            return self._parse_order(result, order.symbol)

        except APIError as e:
            logger.error(f"create_order failed: {e}")
            raise

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        """Cancel an order"""
        try:
            self._trading_client.cancel_order_by_id(order_id)
            return True
        except APIError as e:
            logger.error(f"cancel_order failed: {e}")
            return False

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """Fetch order status"""
        try:
            result = self._trading_client.get_order_by_id(order_id)
            return self._parse_order(result, symbol)
        except APIError as e:
            logger.error(f"fetch_order failed: {e}")
            raise

    async def fetch_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        """Fetch open orders"""
        try:
            orders = self._trading_client.get_orders(
                filter=trading_requests.GetOrdersRequest(
                    status=trading_enums.QueryOrderStatus.OPEN,
                    symbols=[symbol.base] if symbol else None,
                )
            )
            return [self._parse_order(o, self._alpaca_to_unified_symbol(o.symbol)) for o in orders]
        except APIError as e:
            logger.error(f"fetch_open_orders failed: {e}")
            raise

    # --- Helpers ---

    def _alpaca_to_unified_symbol(self, alpaca_symbol: str) -> Symbol:
        """Convert Alpaca symbol to unified Symbol"""
        return Symbol(
            base=alpaca_symbol,
            quote="USD",
            asset_class=AssetClass.STOCK,
            market_type=MarketType.SPOT,
            exchange="alpaca",
        )

    def _parse_order(self, order: trading_models.Order, symbol: Symbol) -> Order:
        status_map = {
            trading_enums.OrderStatus.NEW: OrderStatus.OPEN,
            trading_enums.OrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIAL,
            trading_enums.OrderStatus.FILLED: OrderStatus.FILLED,
            trading_enums.OrderStatus.CANCELED: OrderStatus.CANCELLED,
            trading_enums.OrderStatus.REJECTED: OrderStatus.REJECTED,
            trading_enums.OrderStatus.EXPIRED: OrderStatus.EXPIRED,
        }
        side_map = {trading_enums.OrderSide.BUY: OrderSide.BUY, trading_enums.OrderSide.SELL: OrderSide.SELL}
        type_map = {
            trading_enums.OrderType.MARKET: OrderType.MARKET,
            trading_enums.OrderType.LIMIT: OrderType.LIMIT,
            trading_enums.OrderType.STOP: OrderType.STOP,
            trading_enums.OrderType.STOP_LIMIT: OrderType.STOP_LIMIT,
        }
        tif_map = {
            trading_enums.TimeInForce.GTC: TimeInForce.GTC,
            trading_enums.TimeInForce.IOC: TimeInForce.IOC,
            trading_enums.TimeInForce.FOK: TimeInForce.FOK,
            trading_enums.TimeInForce.DAY: TimeInForce.IOC,  # DAY → IOC approximation
        }

        return Order(
            id=order.id,
            client_order_id=order.client_order_id,
            symbol=symbol,
            side=side_map.get(order.side, OrderSide.BUY),
            type=type_map.get(order.order_type, OrderType.MARKET),
            status=status_map.get(order.status, OrderStatus.OPEN),
            size=Decimal(str(order.qty)),
            filled_size=Decimal(str(order.filled_qty or 0)),
            avg_fill_price=Decimal(str(order.filled_avg_price or 0)),
            price=Decimal(str(order.limit_price or 0)) if order.limit_price else None,
            stop_price=Decimal(str(order.stop_price or 0)) if order.stop_price else None,
            time_in_force=tif_map.get(order.time_in_force, TimeInForce.GTC),
            created_at=order.submitted_at,
            updated_at=order.updated_at,
        )

    def _parse_bar(self, bar: Any, symbol: Symbol, timeframe: str) -> Candle:
        return Candle(
            symbol=symbol,
            timestamp=bar.timestamp,
            timeframe=timeframe,
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            volume=Decimal(str(bar.volume)),
        )

    def get_account_info(self) -> dict:
        """Get detailed account info"""
        account = self._trading_client.get_account()
        return {
            'id': account.id,
            'status': account.status,
            'equity': float(account.equity),
            'cash': float(account.cash),
            'buying_power': float(account.buying_power),
            'initial_margin': float(account.initial_margin),
            'maintenance_margin': float(account.maintenance_margin),
            'daytrade_count': account.daytrade_count,
            'pattern_day_trader': account.pattern_day_trader,
        }


async def create_alpaca_adapter(config: AlpacaConfig) -> AlpacaAdapter:
    """Create and connect Alpaca adapter"""
    if not ALPACA_AVAILABLE:
        raise RuntimeError("alpaca-py is not installed — run `pip install alpaca-py`")
    adapter = AlpacaAdapter(config)
    await adapter.connect()
    return adapter