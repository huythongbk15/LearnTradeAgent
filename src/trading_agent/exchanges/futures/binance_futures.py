"""Binance Futures adapter using CCXT."""

import ccxt.async_support as ccxt
import logging
from decimal import Decimal
from typing import Optional, List

from trading_agent.exchanges.models import (
    Symbol,
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    Ticker,
    OrderBook,
    Balance,
    Position,
)
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter

logger = logging.getLogger(__name__)


class BinanceFuturesAdapter(CCXTAdapter):
    """Binance Futures adapter for USDT-M and COIN-M futures."""

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
        market_type: str = "futures",  # "futures" (USDT-M) or "delivery" (COIN-M)
    ):
        # Binance has separate endpoints for different market types
        if market_type == "futures":
            exchange_class = ccxt.binanceusdm if not testnet else ccxt.binanceusdmtest
        elif market_type == "delivery":
            exchange_class = ccxt.binancecoinm if not testnet else ccxt.binancecoinmtest
        else:
            raise ValueError(f"Unknown market_type: {market_type}")

        super().__init__(
            exchange_id="binance_futures",
            api_key=api_key,
            secret=secret,
            password=password,
            testnet=testnet,
        )

        self._exchange_class = exchange_class
        self._market_type = market_type
        self._exchange: Optional[ccxt.Exchange] = None

    async def connect(self) -> bool:
        """Connect to Binance Futures."""
        try:
            self._exchange = self._exchange_class(
                {
                    "apiKey": self.api_key,
                    "secret": self.secret,
                    "password": self.password,
                    "enableRateLimit": True,
                    "options": {
                        "defaultType": self._market_type,  # "future" or "delivery"
                    },
                }
            )

            await self._exchange.load_markets()

            # Test connection
            if self.api_key:
                await self._exchange.fetch_balance()

            self._connected = True
            logger.info(f"Connected to Binance {self._market_type} futures")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Binance Futures: {e}")
            return False

    def _convert_symbol(self, symbol: Symbol) -> str:
        """Convert unified symbol to Binance format."""
        if self._market_type == "delivery":
            # COIN-M: BTCUSD_20241227
            if symbol.expiry:
                return f"{symbol.base}{symbol.quote}_{symbol.expiry.replace('-', '')}"
        # USDT-M: BTC/USDT:USDT
        return f"{symbol.base}/{symbol.quote}:{symbol.quote}"

    def _parse_symbol(self, market: dict) -> Symbol:
        """Parse Binance market to unified Symbol."""
        info = market.get("info", {})
        base = market["base"]
        quote = market["quote"]

        if self._market_type == "delivery":
            # COIN-M futures
            expiry = info.get("deliveryDate") or info.get("expiry")
            return Symbol(
                base=base,
                quote=quote,
                asset_class=AssetClass.FUTURES,
                market_type=MarketType.FUTURES,
                exchange="binance",
                expiry=expiry,
            )
        else:
            # USDT-M perpetual
            return Symbol(
                base=base,
                quote=quote,
                asset_class=AssetClass.FUTURES,
                market_type=MarketType.PERPETUAL,
                exchange="binance",
            )

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch ticker for futures."""
        ccxt_symbol = self._convert_symbol(symbol)
        ticker = await self._exchange.fetch_ticker(ccxt_symbol)

        return Ticker(
            symbol=symbol,
            timestamp=self._parse_timestamp(ticker["timestamp"]),
            bid=Decimal(str(ticker["bid"])) if ticker["bid"] else None,
            ask=Decimal(str(ticker["ask"])) if ticker["ask"] else None,
            last=Decimal(str(ticker["last"])) if ticker["last"] else None,
            high=Decimal(str(ticker["high"])) if ticker["high"] else None,
            low=Decimal(str(ticker["low"])) if ticker["low"] else None,
            open=Decimal(str(ticker["open"])) if ticker["open"] else None,
            close=Decimal(str(ticker["close"])) if ticker["close"] else None,
            base_volume=Decimal(str(ticker["baseVolume"]))
            if ticker["baseVolume"]
            else None,
            quote_volume=Decimal(str(ticker["quoteVolume"]))
            if ticker["quoteVolume"]
            else None,
            change=Decimal(str(ticker["change"])) if ticker["change"] else None,
            percentage=Decimal(str(ticker["percentage"]))
            if ticker["percentage"]
            else None,
        )

    async def fetch_order_book(self, symbol: Symbol, limit: int = 20) -> OrderBook:
        """Fetch order book."""
        ccxt_symbol = self._convert_symbol(symbol)
        ob = await self._exchange.fetch_order_book(ccxt_symbol, limit)

        from trading_agent.exchanges.models import OrderBookLevel

        return OrderBook(
            symbol=symbol,
            timestamp=self._parse_timestamp(ob["timestamp"]),
            bids=[
                OrderBookLevel(Decimal(str(b[0])), Decimal(str(b[1])))
                for b in ob["bids"]
            ],
            asks=[
                OrderBookLevel(Decimal(str(a[0])), Decimal(str(a[1])))
                for a in ob["asks"]
            ],
        )

    async def fetch_balance(self) -> List[Balance]:
        """Fetch futures account balance."""
        bal = await self._exchange.fetch_balance()

        balances = {}
        for currency, amounts in bal.items():
            if currency in ("info", "free", "used", "total"):
                continue
            if isinstance(amounts, dict):
                balances[currency] = {
                    "free": Decimal(str(amounts.get("free", 0))),
                    "used": Decimal(str(amounts.get("used", 0))),
                    "total": Decimal(str(amounts.get("total", 0))),
                }

        return [
            Balance(
                asset_class=AssetClass.FUTURES,
                assets=balances,
            )
        ]

    async def fetch_positions(
        self, symbols: Optional[List[Symbol]] = None
    ) -> List[Position]:
        """Fetch open positions."""
        positions = await self._exchange.fetch_positions(
            [self._convert_symbol(s) for s in symbols] if symbols else None
        )

        result = []
        for p in positions:
            if p["contracts"] and float(p["contracts"]) != 0:
                sym = self._parse_symbol(
                    {
                        "symbol": p["symbol"],
                        "base": p["symbol"].split("/")[0],
                        "quote": p["symbol"].split("/")[1].split(":")[0],
                    }
                )
                result.append(
                    Position(
                        symbol=sym,
                        size=Decimal(str(p["contracts"])),
                        entry_price=Decimal(str(p["entryPrice"]))
                        if p["entryPrice"]
                        else Decimal(0),
                        mark_price=Decimal(str(p["markPrice"]))
                        if p["markPrice"]
                        else Decimal(0),
                        unrealized_pnl=Decimal(str(p["unrealizedPnl"]))
                        if p["unrealizedPnl"]
                        else Decimal(0),
                        realized_pnl=Decimal(str(p["realizedPnl"]))
                        if p["realizedPnl"]
                        else Decimal(0),
                        leverage=Decimal(str(p["leverage"]))
                        if p["leverage"]
                        else Decimal(1),
                        margin_used=Decimal(str(p["initialMargin"]))
                        if p["initialMargin"]
                        else Decimal(0),
                        liquidation_price=Decimal(str(p["liquidationPrice"]))
                        if p["liquidationPrice"]
                        else None,
                    )
                )

        return result

    async def create_order(self, order: Order) -> Order:
        """Create futures order."""
        ccxt_symbol = self._convert_symbol(order.symbol)

        # Binance futures specific params
        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.post_only:
            params["postOnly"] = True

        # Set leverage if provided
        if hasattr(order, "leverage") and order.leverage:
            await self._exchange.set_leverage(int(order.leverage), ccxt_symbol)

        ccxt_order = await self._exchange.create_order(
            symbol=ccxt_symbol,
            type=order.type.value,
            side=order.side.value,
            amount=float(order.size),
            price=float(order.price) if order.price else None,
            params=params,
        )

        return self._parse_order(ccxt_order, order.symbol)

    async def cancel_order(self, order_id: str, symbol: Symbol) -> Order:
        """Cancel futures order."""
        ccxt_symbol = self._convert_symbol(symbol)
        ccxt_order = await self._exchange.cancel_order(order_id, ccxt_symbol)
        return self._parse_order(ccxt_order, symbol)

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """Fetch order status."""
        ccxt_symbol = self._convert_symbol(symbol)
        ccxt_order = await self._exchange.fetch_order(order_id, ccxt_symbol)
        return self._parse_order(ccxt_order, symbol)

    async def fetch_open_orders(self, symbol: Optional[Symbol] = None) -> List[Order]:
        """Fetch open orders."""
        ccxt_symbol = self._convert_symbol(symbol) if symbol else None
        orders = await self._exchange.fetch_open_orders(ccxt_symbol)
        return [
            self._parse_order(o, self._parse_symbol({"symbol": o["symbol"]}))
            for o in orders
        ]

    async def set_leverage(self, symbol: Symbol, leverage: int) -> None:
        """Set leverage for a symbol."""
        ccxt_symbol = self._convert_symbol(symbol)
        await self._exchange.set_leverage(leverage, ccxt_symbol)

    async def set_margin_mode(self, symbol: Symbol, margin_mode: str) -> None:
        """Set margin mode (isolated/cross)."""
        ccxt_symbol = self._convert_symbol(symbol)
        await self._exchange.set_margin_mode(margin_mode, ccxt_symbol)

    def _parse_order(self, ccxt_order: dict, symbol: Symbol) -> Order:
        """Parse CCXT order to unified Order."""
        status_map = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
            "expired": OrderStatus.EXPIRED,
        }

        return Order(
            id=ccxt_order["id"],
            symbol=symbol,
            side=OrderSide(ccxt_order["side"]),
            type=OrderType(ccxt_order["type"]),
            size=Decimal(str(ccxt_order["amount"])),
            price=Decimal(str(ccxt_order["price"])) if ccxt_order["price"] else None,
            status=status_map.get(ccxt_order["status"], OrderStatus.OPEN),
            filled_size=Decimal(str(ccxt_order["filled"]))
            if ccxt_order["filled"]
            else Decimal(0),
            avg_fill_price=Decimal(str(ccxt_order["average"]))
            if ccxt_order["average"]
            else Decimal(0),
            fee=Decimal(str(ccxt_order["fee"]["cost"]))
            if ccxt_order.get("fee")
            else Decimal(0),
            time_in_force=ccxt_order.get("timeInForce", "GTC"),
            reduce_only=ccxt_order.get("reduceOnly", False),
            post_only=ccxt_order.get("postOnly", False),
            client_order_id=ccxt_order.get("clientOrderId"),
            exchange_order_id=ccxt_order["id"],
        )

    async def close(self) -> None:
        """Close connection."""
        if self._exchange:
            await self._exchange.close()
        self._connected = False
