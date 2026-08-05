"""
OANDA Adapter - Forex Trading (Paper + Live)

Supports:
- OANDA REST API v20
- Streaming API for real-time prices
- Major/minor/exotic currency pairs
- Position management with proper rollover/swap handling
- Account management across sub-accounts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

try:
    import oandapyV20
    import oandapyV20.endpoints.accounts as accounts
    import oandapyV20.endpoints.instruments as instruments
    import oandapyV20.endpoints.orders as orders
    import oandapyV20.endpoints.positions as positions
    import oandapyV20.endpoints.pricing as pricing
    from oandapyV20.exceptions import V20Error
    OANDA_AVAILABLE = True
except ImportError:
    # Optional SDK — adapter is importable without oandapyV20 (e.g. in CI),
    # but raises a clear error when used.
    oandapyV20 = None
    accounts = instruments = orders = positions = pricing = None
    V20Error = Exception
    OANDA_AVAILABLE = False

from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, OrderSide, OrderType,
    OrderStatus, TimeInForce, Order, Position, Balance,
    Ticker, OrderBook, OrderBookLevel, Candle
)

logger = logging.getLogger(__name__)

if not OANDA_AVAILABLE:
    logger.warning("oandapyV20 not installed — OANDAAdapter disabled (pip install oandapyV20)")


@dataclass
class OANDAConfig:
    """OANDA API configuration"""
    access_token: str
    account_id: str
    environment: str = "practice"  # "practice" or "live"
    timeout: int = 30


class OANDAAdapter:
    """OANDA Forex trading adapter"""

    def __init__(self, config: OANDAConfig):
        self.config = config
        self._client: Optional[oandapyV20.API] = None
        self._connected = False
        self._account_info: dict = {}

    async def connect(self) -> None:
        """Initialize OANDA client"""
        if not OANDA_AVAILABLE:
            raise RuntimeError("oandapyV20 is not installed — run `pip install oandapyV20`")
        try:
            self._client = oandapyV20.API(
                access_token=self.config.access_token,
                environment=self.config.environment,
            )

            # Test connection
            r = accounts.AccountDetails(accountID=self.config.account_id)
            self._client.request(r)
            self._account_info = r.response
            self._connected = True
            logger.info(f"OANDA connected: account={self.config.account_id}, env={self.config.environment}")

        except V20Error as e:
            logger.error(f"OANDA connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Close connection"""
        self._connected = False
        logger.info("OANDA disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # --- Account ---

    async def fetch_balance(self) -> dict[AssetClass, Balance]:
        """Fetch account balances"""
        try:
            r = accounts.AccountDetails(accountID=self.config.account_id)
            self._client.request(r)
            acc = r.response['account']

            assets = {
                acc['currency']: {
                    'free': float(acc['balance']) - float(acc['marginUsed']),
                    'used': float(acc['marginUsed']),
                    'total': float(acc['balance']),
                }
            }
            return {AssetClass.FOREX: Balance(asset_class=AssetClass.FOREX, assets=assets)}
        except V20Error as e:
            logger.error(f"fetch_balance failed: {e}")
            raise

    async def fetch_positions(self, symbol: Symbol | None = None) -> list[Position]:
        """Fetch open positions"""
        try:
            r = positions.OpenPositions(accountID=self.config.account_id)
            self._client.request(r)
            result = []
            for pos in r.response.get('positions', []):
                inst = pos['instrument']
                if symbol and self._oanda_to_unified_symbol(inst) != symbol:
                    continue

                long_units = Decimal(str(pos['long']['units'])) if pos['long']['units'] != '0' else Decimal(0)
                short_units = Decimal(str(pos['short']['units'])) if pos['short']['units'] != '0' else Decimal(0)
                net_units = long_units - short_units

                if net_units == 0:
                    continue

                entry_price = Decimal(str(pos['long']['averagePrice'] if net_units > 0 else pos['short']['averagePrice']))
                
                result.append(Position(
                    symbol=self._oanda_to_unified_symbol(inst),
                    size=net_units,
                    entry_price=entry_price,
                    mark_price=Decimal(str(pos['long']['currentPrice'] if net_units > 0 else pos['short']['currentPrice'])),
                    unrealized_pnl=Decimal(str(pos['unrealizedPL'])),
                    realized_pnl=Decimal(str(pos['pl'])),
                    leverage=Decimal(1),
                    updated_at=datetime.now(),
                ))
            return result
        except V20Error as e:
            logger.error(f"fetch_positions failed: {e}")
            raise

    # --- Market Data ---

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch current price"""
        try:
            inst = self._unified_to_oanda_symbol(symbol)
            r = pricing.PricingInfo(
                accountID=self.config.account_id,
                params={'instruments': inst}
            )
            self._client.request(r)
            price = r.response['prices'][0]

            return Ticker(
                symbol=symbol,
                timestamp=datetime.fromisoformat(price['time'].replace('Z', '+00:00')),
                bid=Decimal(str(price['bids'][0]['price'])) if price['bids'] else None,
                ask=Decimal(str(price['asks'][0]['price'])) if price['asks'] else None,
                last=Decimal(str((float(price['bids'][0]['price']) + float(price['asks'][0]['price'])) / 2)) if price['bids'] and price['asks'] else None,
            )
        except V20Error as e:
            logger.error(f"fetch_ticker failed: {e}")
            raise

    async def fetch_order_book(self, symbol: Symbol) -> OrderBook:
        """OANDA doesn't provide full order book, return best bid/ask"""
        ticker = await self.fetch_ticker(symbol)
        bids = [OrderBookLevel(price=ticker.bid, size=Decimal(1))] if ticker.bid else []
        asks = [OrderBookLevel(price=ticker.ask, size=Decimal(1))] if ticker.ask else []
        return OrderBook(symbol=symbol, timestamp=ticker.timestamp, bids=bids, asks=asks)

    async def fetch_candles(
        self,
        symbol: Symbol,
        granularity: str,
        count: int = 100,
        from_time: datetime | None = None,
        to_time: datetime | None = None
    ) -> list[Candle]:
        """Fetch historical candles"""
        try:
            inst = self._unified_to_oanda_symbol(symbol)
            params = {'granularity': granularity, 'count': count}
            if from_time:
                params['from'] = from_time.isoformat()
            if to_time:
                params['to'] = to_time.isoformat()

            r = instruments.InstrumentsCandles(instrument=inst, params=params)
            self._client.request(r)

            candles = []
            for c in r.response['candles']:
                if not c['complete']:
                    continue
                candles.append(Candle(
                    symbol=symbol,
                    timestamp=datetime.fromisoformat(c['time'].replace('Z', '+00:00')),
                    timeframe=granularity,
                    open=Decimal(str(c['mid']['o'])),
                    high=Decimal(str(c['mid']['h'])),
                    low=Decimal(str(c['mid']['l'])),
                    close=Decimal(str(c['mid']['c'])),
                    volume=Decimal(str(c['volume'])),
                ))
            return candles
        except V20Error as e:
            logger.error(f"fetch_candles failed: {e}")
            raise

    # --- Trading ---

    async def create_order(self, order: Order) -> Order:
        """Create a new order"""
        try:
            inst = self._unified_to_oanda_symbol(order.symbol)

            # Build order request
            order_data = self._build_order_request(order, inst)

            r = orders.OrderCreate(accountID=self.config.account_id, data=order_data)
            self._client.request(r)

            created = r.response['orderCreateTransaction']
            return Order(
                id=created['id'],
                client_order_id=created.get('clientOrderID'),
                symbol=order.symbol,
                side=order.side,
                type=order.type,
                status=OrderStatus.OPEN,
                size=order.size,
                price=order.price,
                stop_price=order.stop_price,
                time_in_force=order.time_in_force,
                created_at=datetime.fromisoformat(created['time'].replace('Z', '+00:00')),
            )

        except V20Error as e:
            logger.error(f"create_order failed: {e}")
            raise

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        """Cancel an order"""
        try:
            r = orders.OrderCancel(accountID=self.config.account_id, orderID=order_id)
            self._client.request(r)
            return True
        except V20Error as e:
            logger.error(f"cancel_order failed: {e}")
            return False

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """Fetch order details"""
        try:
            r = orders.OrderDetails(accountID=self.config.account_id, orderID=order_id)
            self._client.request(r)
            order_data = r.response['order']
            return self._parse_order(order_data, symbol)
        except V20Error as e:
            logger.error(f"fetch_order failed: {e}")
            raise

    async def fetch_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        """Fetch all open orders"""
        try:
            r = orders.OrdersList(accountID=self.config.account_id, params={'state': 'PENDING'})
            self._client.request(r)
            result = []
            for o in r.response['orders']:
                sym = self._oanda_to_unified_symbol(o['instrument'])
                if symbol and sym != symbol:
                    continue
                result.append(self._parse_order(o, sym))
            return result
        except V20Error as e:
            logger.error(f"fetch_open_orders failed: {e}")
            raise

    # --- Helpers ---

    def _unified_to_oanda_symbol(self, symbol: Symbol) -> str:
        """Convert unified symbol to OANDA format (EUR_USD)"""
        return f"{symbol.base}_{symbol.quote}"

    def _oanda_to_unified_symbol(self, oanda_symbol: str) -> Symbol:
        """Convert OANDA symbol to unified"""
        base, quote = oanda_symbol.split('_')
        return Symbol(
            base=base,
            quote=quote,
            asset_class=AssetClass.FOREX,
            market_type=MarketType.SPOT,
            exchange="oanda",
        )

    def _build_order_request(self, order: Order, instrument: str) -> dict:
        """Build OANDA order request"""
        units = str(float(order.size)) if order.side == OrderSide.BUY else str(-float(order.size))

        base_order = {
            'order': {
                'instrument': instrument,
                'units': units,
                'type': self._map_order_type(order.type),
                'timeInForce': self._map_time_in_force(order.time_in_force),
            }
        }

        if order.client_order_id:
            base_order['order']['clientOrderID'] = order.client_order_id

        # Price-dependent orders
        if order.type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            base_order['order']['price'] = str(order.price)
        if order.type in (OrderType.STOP, OrderType.STOP_LIMIT):
            base_order['order']['stopPrice'] = str(order.stop_price)

        return base_order

    def _map_order_type(self, order_type: OrderType) -> str:
        mapping = {
            OrderType.MARKET: 'MARKET',
            OrderType.LIMIT: 'LIMIT',
            OrderType.STOP: 'STOP',
            OrderType.STOP_LIMIT: 'STOP_LIMIT',
        }
        return mapping.get(order_type, 'MARKET')

    def _map_time_in_force(self, tif: TimeInForce) -> str:
        mapping = {
            TimeInForce.GTC: 'GTC',
            TimeInForce.IOC: 'IOC',
            TimeInForce.FOK: 'FOK',
            TimeInForce.GTD: 'GTD',
        }
        return mapping.get(tif, 'GTC')

    def _parse_order(self, order_data: dict, symbol: Symbol) -> Order:
        status_map = {
            'PENDING': OrderStatus.OPEN,
            'FILLED': OrderStatus.FILLED,
            'CANCELLED': OrderStatus.CANCELLED,
            'REJECTED': OrderStatus.REJECTED,
            'EXPIRED': OrderStatus.EXPIRED,
        }
        side_map = {'BUY': OrderSide.BUY, 'SELL': OrderSide.SELL}
        type_map = {
            'MARKET': OrderType.MARKET,
            'LIMIT': OrderType.LIMIT,
            'STOP': OrderType.STOP,
            'STOP_LIMIT': OrderType.STOP_LIMIT,
        }

        return Order(
            id=order_data['id'],
            client_order_id=order_data.get('clientOrderID'),
            symbol=symbol,
            side=side_map.get(order_data.get('side', 'BUY'), OrderSide.BUY),
            type=type_map.get(order_data['type'], OrderType.MARKET),
            status=status_map.get(order_data['state'], OrderStatus.OPEN),
            size=Decimal(str(abs(float(order_data['units'])))),
            filled_size=Decimal(str(abs(float(order_data.get('filledUnits', 0))))),
            avg_fill_price=Decimal(str(order_data.get('price', 0))),
            price=Decimal(str(order_data.get('price', 0))) if order_data.get('price') else None,
            stop_price=Decimal(str(order_data.get('stopPrice', 0))) if order_data.get('stopPrice') else None,
            time_in_force=self._map_time_in_force_enum(order_data.get('timeInForce', 'GTC')),
            created_at=datetime.fromisoformat(order_data['createTime'].replace('Z', '+00:00')),
        )

    def _map_time_in_force_enum(self, tif: str) -> TimeInForce:
        mapping = {'GTC': TimeInForce.GTC, 'IOC': TimeInForce.IOC, 'FOK': TimeInForce.FOK, 'GTD': TimeInForce.GTD}
        return mapping.get(tif, TimeInForce.GTC)

    def get_account_summary(self) -> dict:
        """Get account summary"""
        r = accounts.AccountDetails(accountID=self.config.account_id)
        self._client.request(r)
        return r.response['account']


async def create_oanda_adapter(config: OANDAConfig) -> OANDAAdapter:
    """Create and connect OANDA adapter"""
    if not OANDA_AVAILABLE:
        raise RuntimeError("oandapyV20 is not installed — run `pip install oandapyV20`")
    adapter = OANDAAdapter(config)
    await adapter.connect()
    return adapter