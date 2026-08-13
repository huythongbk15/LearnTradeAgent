"""LiveBroker — sync facade over async broker adapters for the CLI.

The exchange adapters (Alpaca/OANDA) implement an async interface
(``fetch_balance``, ``fetch_positions``, ``create_order`` ...). The CLI lives
in sync-land, so this facade exposes the small sync surface the CLI needs
(``get_account``, ``get_positions``, ``get_orders``, ``place_order``) by
running the underlying coroutines with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import math
from decimal import Decimal
from typing import Mapping

from trading_agent.exchanges.models import (
    Symbol,
    AssetClass,
    MarketType,
    Order,
)


def _run(coro):
    return asyncio.run(coro)


class LiveBroker:
    """Sync facade over an async Alpaca/OANDA adapter."""

    def __init__(
        self,
        broker: str,
        adapter,
        pricing_symbols: list[str] | None = None,
        *,
        strict_pricing: bool = False,
    ):
        self.broker = broker
        self.adapter = adapter
        # Coins được phép định giá (vd ['BTC/USDT','SOL/USDT']) — tránh gọi ticker
        # cho hàng trăm faucet coins rác trên testnet.
        self.pricing_symbols = pricing_symbols
        self.strict_pricing = strict_pricing

    def _require_prices(
        self, coins: list[str], prices: dict[str, float], quote: str
    ) -> None:
        if not self.strict_pricing:
            return
        missing = [coin for coin in coins if not prices.get(f"{coin}/{quote}")]
        if missing:
            raise RuntimeError(
                f"Missing prices for account assets: {', '.join(missing)}"
            )

    def _need_coins(self, base_total: dict, main_quote: str) -> list[str]:
        """Coins cần fetch giá: whitelist nếu có, ngược lại top 20 theo total."""
        candidates = [
            coin
            for coin, amt in base_total.items()
            if coin != main_quote
            and amt > 0
            and self.adapter.has_market(f"{coin}/{main_quote}")
        ]
        if self.pricing_symbols:
            wanted = {s.split("/")[0].upper() for s in self.pricing_symbols}
            return [c for c in candidates if c.upper() in wanted]
        return sorted(candidates, key=lambda c: base_total[c], reverse=True)[:20]

    @staticmethod
    def _spot_balance_quantities(
        amounts: Mapping[str, object],
    ) -> tuple[float, float, float]:
        """Return validated total, free and locked spot quantities."""

        try:
            total = float(amounts.get("total", 0) or 0)
            raw_free = amounts.get("free")
            raw_used = amounts.get("used")
            free = float(raw_free) if raw_free is not None else None
            locked = float(raw_used) if raw_used is not None else None
        except (TypeError, ValueError) as exc:
            raise RuntimeError("spot balance quantities must be numeric") from exc
        values = [total]
        values.extend(value for value in (free, locked) if value is not None)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise RuntimeError(
                "spot balance quantities must be finite and non-negative"
            )
        if free is None and locked is None:
            free, locked = total, 0.0
        elif free is None:
            free = max(0.0, total - float(locked))
        elif locked is None:
            locked = max(0.0, total - free)
        tolerance = max(1e-12, total * 1e-8)
        if (
            free > total + tolerance
            or locked > total + tolerance
            or abs((free + locked) - total) > tolerance
        ):
            raise RuntimeError(
                "spot free and locked quantities are inconsistent with total"
            )
        return total, min(free, total), min(locked, total)

    # ── account ────────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        if self.broker == "alpaca":
            # adapter exposes sync get_account_info()
            info = self.adapter.get_account_info()
            return {
                "id": info.get("id", "N/A"),
                "status": info.get("status", "N/A"),
                "currency": "USD",
                "cash": float(info.get("cash", 0)),
                "equity": float(info.get("equity", 0)),
                "portfolio_value": float(info.get("equity", 0)),
                "long_market_value": float(info.get("long_market_value", 0)),
                "short_market_value": float(info.get("short_market_value", 0)),
                "buying_power": float(info.get("buying_power", 0)),
                "initial_margin": float(info.get("initial_margin", 0)),
                "maintenance_margin": float(info.get("maintenance_margin", 0)),
                "unrealized_pl": 0.0,
                "realized_pl_day": 0.0,
            }
        if self.broker in ("ccxt", "binance", "bybit", "okx"):
            # Spot-style account from fetch_balance()
            balance = _run(self.adapter.fetch_balance())
            assets = balance.get(AssetClass.CRYPTO, None)
            total_usdt = 0.0
            free_usdt = 0.0
            # Lấy toàn bộ coin khác quote → giá trị qui về USDT
            main_quote = "USDT" if self.broker == "binance" else "USDT"
            base_total = {}
            quote_total = 0.0
            for pair, amounts in assets.assets.items() if assets else {}:
                total, free, _ = self._spot_balance_quantities(amounts)
                base_total[pair] = total
                if pair == main_quote:
                    quote_total = total
                    free_usdt = free
            # Định giá coin khác quote bằng ticker (bỏ coin quote chính)
            need = self._need_coins(base_total, main_quote)
            prices = {}
            if need:
                try:
                    prices = _run(
                        self.adapter.fetch_tickers(
                            [
                                Symbol(
                                    base=c,
                                    quote=main_quote,
                                    asset_class=AssetClass.CRYPTO,
                                    market_type=MarketType.SPOT,
                                    exchange=self.adapter.config.id,
                                )
                                for c in need
                            ]
                        )
                    )
                except Exception:
                    prices = {}
            self._require_prices(need, prices, main_quote)
            for coin, amt in base_total.items():
                if coin == main_quote or amt <= 0:
                    continue
                price = prices.get(f"{coin}/{main_quote}")
                if not price:
                    continue
                total_usdt += amt * float(price)
            total_usdt += quote_total
            return {
                "id": self.broker,
                "status": "active",
                "currency": main_quote,
                "cash": free_usdt,
                "equity": total_usdt,
                "portfolio_value": total_usdt,
                "long_market_value": total_usdt - free_usdt,
                "short_market_value": 0.0,
                "buying_power": free_usdt,
                "initial_margin": 0.0,
                "maintenance_margin": 0.0,
                "unrealized_pl": 0.0,
                "realized_pl_day": 0.0,
            }
        # OANDA
        summary = self.adapter.get_account_summary()
        return {
            "id": summary.get("id", "N/A"),
            "status": summary.get("status", "N/A"),
            "currency": summary.get("currency", "USD"),
            "cash": float(summary.get("balance", 0)),
            "equity": float(summary.get("NAV", 0)),
            "portfolio_value": float(summary.get("NAV", 0)),
            "long_market_value": float(summary.get("positionValue", 0)),
            "short_market_value": 0.0,
            "buying_power": float(summary.get("marginAvailable", 0)),
            "initial_margin": float(summary.get("marginUsed", 0)),
            "maintenance_margin": float(summary.get("marginUsed", 0)),
            "unrealized_pl": float(summary.get("unrealizedPL", 0)),
            "realized_pl_day": float(summary.get("realizedPL", 0)),
        }

    # ── positions ──────────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        if self.broker in ("ccxt", "binance", "bybit", "okx"):
            # CCXT spot: positions = balance coins (excluding main quote)
            balance = _run(self.adapter.fetch_balance())
            assets = balance.get(AssetClass.CRYPTO, None)
            main_quote = "USDT" if self.broker == "binance" else "USDT"
            out = []
            if not assets:
                return out
            need = self._need_coins(
                {c: float(a.get("total", 0)) for c, a in assets.assets.items()},
                main_quote,
            )
            prices = {}
            if need:
                try:
                    prices = _run(
                        self.adapter.fetch_tickers(
                            [
                                Symbol(
                                    base=c,
                                    quote=main_quote,
                                    asset_class=AssetClass.CRYPTO,
                                    market_type=MarketType.SPOT,
                                    exchange=self.adapter.config.id,
                                )
                                for c in need
                            ]
                        )
                    )
                except Exception:
                    prices = {}
            self._require_prices(need, prices, main_quote)
            for coin, amounts in assets.assets.items():
                total, free, locked = self._spot_balance_quantities(amounts)
                if coin == main_quote or total <= 0:
                    continue
                price = prices.get(f"{coin}/{main_quote}")
                if not price:
                    continue  # cặp rác — không gọi ticker
                out.append(
                    {
                        "symbol": f"{coin}/{main_quote}",
                        "side": "long",
                        "qty": total,
                        "free_qty": free,
                        "locked_qty": locked,
                        "avg_entry_price": price,  # không có cost basis — dùng mark
                        "current_price": price,
                        "unrealized_pl": 0.0,
                        "unrealized_plpc": 0.0,
                        "market_value": total * price,
                    }
                )
            return out

        positions = _run(self.adapter.fetch_positions())
        out = []
        for p in positions:
            mark = float(p.mark_price)
            entry = float(p.entry_price)
            qty = float(p.size)
            pnl = (mark - entry) * qty if p.is_long else (entry - mark) * qty
            pnl_pct = (mark - entry) / entry * 100 if entry else 0.0
            out.append(
                {
                    "symbol": p.symbol.pair,
                    "side": "long" if p.is_long else "short",
                    "qty": qty,
                    "avg_entry_price": entry,
                    "current_price": mark,
                    "unrealized_pl": pnl,
                    "unrealized_plpc": pnl_pct / 100,
                    "market_value": float(p.notional),
                }
            )
        return out

    # ── orders ─────────────────────────────────────────────────────────────

    def get_ticker(self, symbol: Symbol) -> dict:
        """Return a fresh broker quote for pre-trade validation."""
        ticker = _run(self.adapter.fetch_ticker(symbol))
        return {
            "timestamp": ticker.timestamp,
            "bid": float(ticker.bid) if ticker.bid is not None else None,
            "ask": float(ticker.ask) if ticker.ask is not None else None,
            "last": float(ticker.last) if ticker.last is not None else None,
            "request_started_at": ticker.request_started_at,
            "received_at": ticker.received_at,
        }

    def get_order_book(self, symbol: Symbol, limit: int = 50) -> dict:
        book = _run(self.adapter.fetch_order_book(symbol, limit=limit))
        return {
            "timestamp": book.timestamp,
            "bids": [(float(level.price), float(level.size)) for level in book.bids],
            "asks": [(float(level.price), float(level.size)) for level in book.asks],
            "sequence": book.sequence,
            "request_started_at": book.request_started_at,
            "received_at": book.received_at,
        }

    def normalize_order_amount(
        self,
        symbol: Symbol,
        amount: float,
        *,
        reference_price: float,
    ) -> float:
        normalized = self.adapter.normalize_order_amount(
            symbol,
            Decimal(str(amount)),
            reference_price=Decimal(str(reference_price)),
        )
        return float(normalized)

    @staticmethod
    def _order_result(order: Order) -> dict:
        """Expose complete cumulative fill evidence to the live safety ledger."""

        return {
            "id": order.id,
            "client_order_id": order.client_order_id,
            "status": order.status.value,
            "exchange_status": order.raw_status,
            "symbol": order.symbol.pair,
            "side": order.side.value,
            "type": order.type.value,
            "qty": float(order.size),
            "filled_qty": float(order.filled_size),
            "avg_fill_price": float(order.avg_fill_price),
            "quote_cost": float(order.quote_cost),
            "fees": {
                currency: float(cost) for currency, cost in order.fee_breakdown.items()
            },
            "trade_ids": list(order.trade_ids),
            "stop_price": (
                float(order.stop_price) if order.stop_price is not None else None
            ),
            "submitted_at": order.created_at.isoformat() if order.created_at else "",
            "error": order.error,
        }

    def get_orders(self, status: str = "open", limit: int = 20) -> list[dict]:
        if status == "open":
            orders = _run(self.adapter.fetch_open_orders())
        else:
            orders = _run(
                self.adapter.fetch_open_orders()
            )  # closed fetch not in async surface
        return [self._order_result(order) for order in orders[:limit]]

    def get_order_by_client_id(
        self, client_order_id: str, symbol: Symbol
    ) -> dict | None:
        """Return a normalized order used to reconcile an uncertain submission."""

        result = _run(self.adapter.fetch_order_by_client_id(client_order_id, symbol))
        if result is None:
            return None
        return self._order_result(result)

    def place_order(self, order: Order) -> dict:
        if not order.client_order_id:
            import uuid

            order.client_order_id = f"cli-{uuid.uuid4().hex[:16]}"
        result = _run(self.adapter.create_order(order))
        return self._order_result(result)

    def replace_order(self, order_id: str, order: Order) -> dict:
        """Cancel-replace one concrete order without guessing its symbol."""

        result = _run(self.adapter.replace_order(order_id, order))
        return self._order_result(result)

    def cancel_order(self, order_id: str, symbol: Symbol | None = None) -> bool:
        if symbol is None:
            # Preserve the historical CLI fallback; live trading must always
            # pass the concrete symbol so it cannot cancel on the wrong market.
            symbol = Symbol(
                base="BTC",
                quote="USD",
                asset_class=AssetClass.STOCK,
                market_type=MarketType.SPOT,
                exchange="alpaca",
            )
        return _run(self.adapter.cancel_order(order_id, symbol))
