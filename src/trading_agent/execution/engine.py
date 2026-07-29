"""
Execution Engine — unified interface to trade.

Kết nối Phase 2 (signals) với Phase 3 (execution).
Tự động chọn paper/live mode dựa trên config.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.types import (
    Order,
    OrderSide,
    OrderType,
    Position,
    Trade,
)
from trading_agent.config.loader import config

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Unified execution engine.

    Currently supports paper trading only (safe, no real money).
    Live mode via CCXT will be added in a later iteration.

    Usage:
        engine = ExecutionEngine()
        engine.execute_signal(signal_message)  # from Phase 2
        engine.update_prices({"BTC/USDT": 65000})
        print(engine.get_summary())
    """

    def __init__(
        self,
        exchange_name: str | None = None,
        initial_capital: float | None = None,
        commission: float | None = None,
        slippage: float | None = None,
    ):
        self.exchange_name = exchange_name or config.default_exchange

        # Use config default values
        self.exchange = PaperExchange(
            exchange_name=self.exchange_name,
            initial_balance=initial_capital or config.initial_capital,
            commission=commission or config.commission,
            slippage=slippage or config.slippage,
        )

    # ── Execute signals from Phase 2 agents ────────────────────────────

    def execute_signal(self, signal: AgentMessage) -> list[Order]:
        """Execute a trading signal from the multi-agent system.

        Takes the final ``Trader`` agent signal and converts it to orders.

        Parameters
        ----------
        signal : AgentMessage
            The ``trader`` agent's output. Expected fields:
            - signal: "BUY" | "SELL" | "HOLD"
            - confidence: 0.0-1.0
            - max_position_size_pct: max % of portfolio to use
            - risk_level: risk assessment

        Returns
        -------
        list[Order]
            Orders that were placed (empty for HOLD)
        """
        signal_str = signal.signal.upper()
        orders: list[Order] = []

        if signal_str == "HOLD":
            logger.info("Signal: HOLD — no action")
            return orders

        # Determine position size
        symbol = signal.details.get("symbol", "BTC/USDT") if signal.details else "BTC/USDT"
        max_pos_pct = signal.max_position_size_pct or 0.25

        # Calculate amount based on portfolio allocation
        equity = self.exchange.get_total_equity()
        current_price = self._get_current_price(symbol)
        if current_price is None or current_price <= 0:
            logger.warning(f"Cannot execute: no price data for {symbol}")
            return orders

        # Get existing position
        existing_pos = self.exchange.get_position(symbol)
        existing_qty = existing_pos.quantity if existing_pos else 0.0

        if signal_str == "BUY":
            # Calculate buy amount
            if not existing_pos or not existing_pos.is_active:
                max_cost = equity * max_pos_pct
                amount = max_cost / current_price
                # Round to reasonable precision (0.001 for BTC)
                amount = max(0.001, round(amount, 4))

                logger.info(
                    f"Signal: BUY {amount} {symbol} "
                    f"(${amount * current_price:,.2f}, "
                    f"{max_pos_pct * 100:.0f}% of ${equity:,.2f})"
                )
                order = self.exchange.place_order(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    amount=amount,
                )
                orders.append(order)
            else:
                logger.info(f"BUY signal but already in position: {existing_pos.quantity} {symbol}")

        elif signal_str == "SELL":
            if existing_pos and existing_pos.is_active:
                amount = existing_pos.quantity
                logger.info(f"Signal: SELL {amount} {symbol}")
                order = self.exchange.place_order(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    amount=amount,
                )
                orders.append(order)
            else:
                logger.info(f"SELL signal but no position in {symbol}")

        return orders

    def set_stop_loss(self, symbol: str, stop_pct: float = 0.05):
        """Set a stop-loss on an existing position.

        Parameters
        ----------
        symbol : str
            Trading pair
        stop_pct : float
            Stop distance from entry (e.g. 0.05 = 5% below entry)
        """
        pos = self.exchange.get_position(symbol)
        if pos and pos.is_active:
            pos.stop_loss = pos.entry_price * (1 - stop_pct)
            logger.info(f"Stop-loss set: {symbol} @ {pos.stop_loss:.2f} "
                        f"({stop_pct * 100:.1f}% below entry)")

    # ── Price feed ─────────────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for all tracked symbols.

        Call this regularly (e.g. every new candle) to:
        - Update unrealized P&L
        - Check stop-loss/take-profit triggers
        - Fill pending limit/stop orders
        """
        self.exchange.update_prices(prices)

    def update_from_dataframe(self, symbol: str, df: Any):
        """Update prices from latest OHLCV data."""
        if df.is_empty():
            return
        latest_close = float(df["close"].tail(1).item())
        self.update_prices({symbol: latest_close})

    # ── Status & Reporting ─────────────────────────────────────────────

    def get_summary(self) -> dict[str, Any]:
        """Get a full execution summary."""
        positions = self.exchange.get_all_positions()
        total_equity = self.exchange.get_total_equity()
        cash = self.exchange.get_balance("USDT")
        open_orders = self.exchange.get_open_orders()

        # Calculate totals
        total_pnl = sum(p.unrealized_pnl for p in positions)
        pos_value = total_equity - cash

        return {
            "equity": round(total_equity, 2),
            "cash": round(cash, 2),
            "positions_value": round(pos_value, 2),
            "unrealized_pnl": round(total_pnl, 2),
            "return_pct": round(
                ((total_equity / config.initial_capital) - 1) * 100, 2
            ),
            "open_positions": len(positions),
            "open_orders": len(open_orders),
            "total_trades": len(self.exchange.trades),
        }

    def get_positions_summary(self) -> list[dict[str, Any]]:
        """Get detailed position info."""
        result = []
        for pos in self.exchange.get_all_positions():
            result.append({
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "pnl": round(pos.unrealized_pnl, 2),
                "pnl_pct": round(pos.unrealized_pnl_pct, 2),
                "value": round(pos.market_value, 2),
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
            })
        return result

    def get_trade_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent trade history."""
        return [t.to_dict() for t in self.exchange.get_trade_history(limit)]

    def close_all(self, reason: str = "manual_kill"):
        """Emergency close all positions."""
        self.exchange.close_all_positions(reason=reason)

    def reset(self):
        """Reset paper exchange to initial state."""
        self.exchange.reset()

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_current_price(self, symbol: str) -> float | None:
        """Try to get current price from cache, then from data."""
        price = self.exchange._last_price_cache.get(symbol)
        if price:
            return price

        # Fall back to latest candle
        try:
            from trading_agent.data.storage import load_ohlcv
            df = load_ohlcv(self.exchange_name, symbol, "1h")
            if not df.is_empty():
                price = float(df["close"].tail(1).item())
                self.exchange._last_price_cache[symbol] = price
                return price
        except Exception:
            pass
        return None
