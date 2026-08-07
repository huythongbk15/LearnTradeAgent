"""LiveBroker — sync facade over async broker adapters for the CLI.

The exchange adapters (Alpaca/OANDA) implement an async interface
(``fetch_balance``, ``fetch_positions``, ``create_order`` ...). The CLI lives
in sync-land, so this facade exposes the small sync surface the CLI needs
(``get_account``, ``get_positions``, ``get_orders``, ``place_order``) by
running the underlying coroutines with ``asyncio.run``.
"""

from __future__ import annotations

import asyncio

from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, Order,
)


def _run(coro):
    return asyncio.run(coro)


class LiveBroker:
    """Sync facade over an async Alpaca/OANDA adapter."""

    def __init__(self, broker: str, adapter):
        self.broker = broker
        self.adapter = adapter

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
        positions = _run(self.adapter.fetch_positions())
        out = []
        for p in positions:
            mark = float(p.mark_price)
            entry = float(p.entry_price)
            qty = float(p.size)
            pnl = (mark - entry) * qty if p.is_long else (entry - mark) * qty
            pnl_pct = (mark - entry) / entry * 100 if entry else 0.0
            out.append({
                "symbol": p.symbol.pair,
                "side": "long" if p.is_long else "short",
                "qty": qty,
                "avg_entry_price": entry,
                "current_price": mark,
                "unrealized_pl": pnl,
                "unrealized_plpc": pnl_pct / 100,
                "market_value": float(p.notional),
            })
        return out

    # ── orders ─────────────────────────────────────────────────────────────

    def get_orders(self, status: str = "open", limit: int = 20) -> list[dict]:
        if status == "open":
            orders = _run(self.adapter.fetch_open_orders())
        else:
            orders = _run(self.adapter.fetch_open_orders())  # closed fetch not in async surface
        out = []
        for o in orders:
            out.append({
                "id": o.id,
                "symbol": o.symbol.pair,
                "side": o.side.value,
                "type": o.type.value,
                "qty": float(o.size),
                "filled_qty": float(o.filled_size),
                "avg_fill_price": float(o.avg_fill_price),
                "status": o.status.value,
                "submitted_at": o.created_at.isoformat() if o.created_at else "",
            })
        return out[:limit]

    def place_order(self, order: Order) -> dict:
        if not order.client_order_id:
            order.client_order_id = "cli"
        result = _run(self.adapter.create_order(order))
        return {
            "id": result.id,
            "status": result.status.value,
            "symbol": result.symbol.pair,
            "side": result.side.value,
            "type": result.type.value,
            "qty": float(result.size),
            "filled_qty": float(result.filled_size),
            "avg_fill_price": float(result.avg_fill_price),
            "error": result.error,
        }

    def cancel_order(self, order_id: str) -> bool:
        # cancel_order needs a symbol; best-effort with a pseudo BTC symbol.
        # CLI's live loop only cancels when we have a concrete order, so this
        # is acceptable for the paper/live demo paths that don't rely on it.
        sym = Symbol(base="BTC", quote="USD", asset_class=AssetClass.STOCK,
                     market_type=MarketType.SPOT, exchange="alpaca")
        return _run(self.adapter.cancel_order(order_id, sym))