"""Deribit Options adapter using CCXT."""

import ccxt.async_support as ccxt
import logging
from decimal import Decimal
from typing import Optional, List

from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, Order, OrderSide, OrderType, 
    OrderStatus, Ticker, OrderBook, Balance
)
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter

logger = logging.getLogger(__name__)


class DeribitOptionsAdapter(CCXTAdapter):
    """Deribit Options adapter."""

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        password: str = "",
        testnet: bool = False,
    ):
        super().__init__("deribit", api_key, secret, password, testnet)
        self._market_type = MarketType.OPTIONS
        self._asset_class = AssetClass.OPTIONS

    async def _init_exchange(self) -> None:
        """Initialize Deribit exchange."""
        self._exchange = ccxt.deribit({
            "apiKey": self.api_key,
            "secret": self.secret,
            "password": self.password,
            "enableRateLimit": True,
            "options": {
                "defaultType": "option",
            },
        })
        
        if self.testnet:
            self._exchange.set_sandbox_mode(True)

        await self._exchange.load_markets()
        self._connected = True
        logger.info(f"Connected to Deribit Options (testnet={self.testnet})")

    def _convert_symbol(self, symbol: Symbol) -> str:
        """Convert unified symbol to Deribit format."""
        # Deribit format: BTC-29MAR24-50000-C
        if symbol.expiry and symbol.strike and symbol.option_type:
            expiry_str = symbol.expiry.replace("-", "").upper()  # 29MAR24
            return f"{symbol.base}-{expiry_str}-{int(symbol.strike)}-{symbol.option_type[0].upper()}"
        return symbol.ccxt_symbol

    def _parse_symbol(self, market: dict) -> Symbol:
        """Parse Deribit market to unified Symbol."""
        # Parse Deribit option format: BTC-29MAR24-50000-C
        parts = market["id"].split("-")
        if len(parts) == 4:
            base, expiry_str, strike_str, opt_type = parts
            # Convert expiry: 29MAR24 -> 2024-03-29
            try:
                from datetime import datetime
                expiry = datetime.strptime(expiry_str, "%d%b%y").strftime("%Y-%m-%d")
            except Exception:
                expiry = expiry_str
            
            return Symbol(
                base=base,
                quote="USDC",  # Deribit quotes in USDC
                asset_class=AssetClass.OPTIONS,
                market_type=MarketType.OPTIONS,
                exchange="deribit",
                expiry=expiry,
                strike=Decimal(strike_str),
                option_type="call" if opt_type == "C" else "put",
            )
        return Symbol(
            base=market["base"],
            quote=market["quote"],
            asset_class=self._asset_class,
            market_type=self._market_type,
            exchange="deribit",
        )

    async def create_order(self, order: Order) -> Order:
        """Create options order."""
        ccxt_symbol = self._convert_symbol(order.symbol)
        
        params = {}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.post_only:
            params["postOnly"] = True

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
        """Cancel options order."""
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
        return [self._parse_order(o, self._parse_symbol({"symbol": o["symbol"], "id": o["symbol"]})) for o in orders]

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
            filled_size=Decimal(str(ccxt_order["filled"])) if ccxt_order["filled"] else Decimal(0),
            avg_fill_price=Decimal(str(ccxt_order["average"])) if ccxt_order["average"] else Decimal(0),
            fee=Decimal(str(ccxt_order["fee"]["cost"])) if ccxt_order.get("fee") else Decimal(0),
            time_in_force=ccxt_order.get("timeInForce", "GTC"),
            reduce_only=ccxt_order.get("reduceOnly", False),
            post_only=ccxt_order.get("postOnly", False),
            client_order_id=ccxt_order.get("clientOrderId"),
            exchange_order_id=ccxt_order["id"],
        )

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        """Fetch ticker for option."""
        ccxt_symbol = self._convert_symbol(symbol)
        ticker = await self._exchange.fetch_ticker(ccxt_symbol)
        
        return Ticker(
            symbol=symbol,
            timestamp=ticker["timestamp"],
            bid=Decimal(str(ticker["bid"])) if ticker["bid"] else None,
            ask=Decimal(str(ticker["ask"])) if ticker["ask"] else None,
            last=Decimal(str(ticker["last"])) if ticker["last"] else None,
            high=Decimal(str(ticker["high"])) if ticker["high"] else None,
            low=Decimal(str(ticker["low"])) if ticker["low"] else None,
            open=Decimal(str(ticker["open"])) if ticker["open"] else None,
            close=Decimal(str(ticker["close"])) if ticker["close"] else None,
            base_volume=Decimal(str(ticker["baseVolume"])) if ticker["baseVolume"] else None,
            quote_volume=Decimal(str(ticker["quoteVolume"])) if ticker["quoteVolume"] else None,
        )

    async def fetch_order_book(self, symbol: Symbol, limit: int = 20) -> OrderBook:
        """Fetch order book for option."""
        ccxt_symbol = self._convert_symbol(symbol)
        ob = await self._exchange.fetch_order_book(ccxt_symbol, limit)
        
        return OrderBook(
            symbol=symbol,
            timestamp=ob["timestamp"],
            bids=[{"price": Decimal(str(b[0])), "size": Decimal(str(b[1]))} for b in ob["bids"]],
            asks=[{"price": Decimal(str(a[0])), "size": Decimal(str(a[1]))} for a in ob["asks"]],
        )

    async def fetch_balance(self) -> Balance:
        """Fetch options account balance."""
        balance = await self._exchange.fetch_balance()
        
        assets = {}
        for currency, amounts in balance.items():
            if currency in ("info", "free", "used", "total"):
                continue
            assets[currency] = {
                "free": Decimal(str(amounts["free"])),
                "used": Decimal(str(amounts["used"])),
                "total": Decimal(str(amounts["total"])),
            }
        
        return Balance(
            asset_class=self._asset_class,
            assets=assets,
        )

    async def close(self) -> None:
        """Close connection."""
        if self._exchange:
            await self._exchange.close()
        self._connected = False