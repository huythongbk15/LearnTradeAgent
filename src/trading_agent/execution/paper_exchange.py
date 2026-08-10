"""
Paper Exchange — simulated exchange for safe testing.

Mô phỏng order matching, slippage, và fee mà không cần API thật.
State lưu dưới dạng JSON để persist qua các lần chạy.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from trading_agent.execution.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Trade,
    generate_idempotency_key,
)
from trading_agent.log_config import get_logger

logger = get_logger(__name__)

# Lazy import for DB — avoids circular import at module level
_db_initialized = False


def _lazy_db():
    """Initialize and return the database module on first call."""
    global _db_initialized
    if not _db_initialized:
        from trading_agent.monitoring.database import init_db
        init_db()
        _db_initialized = True


def _log_trade_to_db(
    trade: Trade,
    action: str,  # "open" or "close"
    pnl: float | None = None,
    pnl_pct: float | None = None,
    reason: str | None = None,
):
    """Write trade event to SQLite database."""
    try:
        _lazy_db()
        from trading_agent.monitoring.database import close_trade, insert_trade

        if action == "open":
            insert_trade(
                trade_id=trade.id,
                symbol=trade.symbol,
                side=trade.side.value if hasattr(trade.side, 'value') else str(trade.side),
                amount=trade.quantity,
                entry_price=trade.entry_price,
                entry_time=trade.entry_time.isoformat() if hasattr(trade.entry_time, 'isoformat') else str(trade.entry_time),
                entry_order_id=trade.entry_order_id,
                strategy="agent",
            )
        elif action == "close":
            # Persist completed fills as self-contained rows. This also models
            # partial exits correctly instead of leaving an unmatched open row.
            insert_trade(
                trade_id=trade.id,
                symbol=trade.symbol,
                side=trade.side.value if hasattr(trade.side, "value") else str(trade.side),
                amount=trade.quantity,
                entry_price=trade.entry_price,
                entry_time=trade.entry_time.isoformat() if trade.entry_time else None,
                entry_order_id=trade.entry_order_id,
                strategy="agent",
            )
            close_trade(
                trade_id=trade.id,
                exit_price=trade.exit_price or 0,
                exit_time=trade.exit_time.isoformat() if trade.exit_time else None,
                exit_order_id=trade.exit_order_id,
                pnl=pnl,
                pnl_pct=pnl_pct,
                fee=(trade.entry_fee or 0.0) + (trade.exit_fee or 0.0),
                reason=reason,
            )
    except Exception as e:
        logger.warning("Failed to log trade to DB: %s", e)


def _log_equity_snapshot(equity: float, cash: float, pos_value: float, drawdown: float = 0.0, peak: float | None = None):
    """Write equity snapshot to SQLite."""
    try:
        _lazy_db()
        from trading_agent.monitoring.database import save_equity_snapshot
        save_equity_snapshot(
            equity=equity,
            cash=cash,
            position_value=pos_value,
            drawdown_pct=drawdown,
            peak_equity=peak,
        )
    except Exception as e:
        logger.warning("Failed to log equity snapshot: %s", e)

# ── Defaults ──────────────────────────────────────────────────────────────

DEFAULT_COMMISSION = 0.001   # 0.1% (Binance spot)
DEFAULT_SLIPPAGE = 0.0005   # 0.05%
STATE_DIR = Path("data/execution")


def _synchronized(method):
    """Serialize state mutations within a PaperExchange instance."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return wrapper


class PaperExchange:
    """Simulated exchange for paper trading.

    Features:
    - Market orders fill at latest price + slippage
    - Limit orders fill when price crosses limit
    - Stop-loss triggers when price crosses stop
    - State persisted as JSON (survives restarts)
    - Configurable commission + slippage
    """

    def __init__(
        self,
        exchange_name: str = "binance",
        initial_balance: float = 10_000.0,
        commission: float = DEFAULT_COMMISSION,
        slippage: float = DEFAULT_SLIPPAGE,
        state_dir: str | Path = STATE_DIR,
        max_price_age_seconds: float = 300.0,
    ):
        if initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 <= commission < 1:
            raise ValueError("commission must be in [0, 1)")
        if not 0 <= slippage < 1:
            raise ValueError("slippage must be in [0, 1)")
        if max_price_age_seconds <= 0:
            raise ValueError("max_price_age_seconds must be positive")

        self.exchange_name = exchange_name
        self.commission = commission
        self.slippage = slippage
        self.state_dir = Path(state_dir)
        self.initial_balance = float(initial_balance)
        self.max_price_age_seconds = float(max_price_age_seconds)
        self._state_lock = threading.RLock()

        # In-memory state
        self.balances: dict[str, float] = {
            "USDT": initial_balance,  # quote currency
        }
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_history: list[dict[str, Any]] = []
        self._last_price_cache: dict[str, float] = {}
        self._last_price_timestamps: dict[str, float] = {}
        self._peak_equity: float = initial_balance
        self._equity_snapshot_counter: int = 0

        # Load existing state if any
        self._load_state()

    # ── Public API ─────────────────────────────────────────────────────

    @_synchronized
    def place_order(
        self,
        symbol: str,
        side: OrderSide | str,
        order_type: OrderType | str = OrderType.MARKET,
        amount: float = 0.0,
        price: float | None = None,
        stop_price: float | None = None,
        idempotency_key: str | None = None,
        client_order_id: str | None = None,
    ) -> Order:
        """Place an order on the paper exchange.

        Parameters
        ----------
        symbol : str
            e.g. "BTC/USDT"
        side : OrderSide | str
            "buy" or "sell"
        order_type : OrderType | str
            "market", "limit", "stop_loss"
        amount : float
            Quantity in base currency (e.g. BTC)
        price : float, optional
            Required for limit/stop_limit orders
        stop_price : float, optional
            Required for stop_loss orders
        idempotency_key : str, optional
            Deduplication key. If provided, duplicate orders with same key are rejected.
            If not provided, auto-generated from order parameters + current minute.
        client_order_id : str, optional
            User-provided correlation ID for tracking across systems.
        """
        side = OrderSide(side) if isinstance(side, str) else side
        order_type = OrderType(order_type) if isinstance(order_type, str) else order_type

        # Validate
        if amount <= 0:
            raise ValueError(f"Invalid amount: {amount}")

        if order_type in (OrderType.LIMIT, OrderType.STOP_LOSS_LIMIT) and price is None:
            raise ValueError(f"Price required for {order_type.value} orders")

        if order_type == OrderType.STOP_LOSS and stop_price is None:
            raise ValueError("stop_price required for stop_loss orders")

        # Generate idempotency key if not provided
        if idempotency_key is None:
            idempotency_key = generate_idempotency_key(symbol, side, order_type, amount, price)

        # Check for duplicate order (same idempotency key)
        # Return existing order if found (regardless of status - allows idempotent retries)
        for existing_order in self.orders.values():
            if existing_order.idempotency_key == idempotency_key:
                logger.warning(f"Duplicate order detected: idempotency_key={idempotency_key[:16]}... (existing: {existing_order.id}, status={existing_order.status.value})")
                return existing_order

        # Create order
        order_id = self._next_id("ord")
        order = Order(
            id=order_id,
            symbol=symbol,
            side=side,
            type=order_type,
            amount=amount,
            price=price,
            stop_price=stop_price,
            status=OrderStatus.OPEN if order_type in (OrderType.LIMIT, OrderType.STOP_LOSS) else OrderStatus.PENDING,
            idempotency_key=idempotency_key,
            client_order_id=client_order_id,
        )

        self.orders[order_id] = order
        logger.info(f"Order {order_id}: {side.value.upper()} {amount} {symbol} @ {price or 'market'} (idem={idempotency_key[:16]}...)")

        # Market orders fill immediately
        if order_type == OrderType.MARKET:
            self._fill_market_order(order_id)

        return order

    @_synchronized
    def cancel_order(self, order_id: str) -> Order | None:
        """Cancel an open order."""
        order = self.orders.get(order_id)
        if order and order.is_open:
            order.status = OrderStatus.CANCELED
            order.updated_at = datetime.now(UTC)
            logger.info(f"Order {order_id} canceled")
            self._save_state()
        return order

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = [o for o in self.orders.values() if o.is_open]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def get_all_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.is_active]

    def get_trade_history(self, limit: int = 20) -> list[Trade]:
        return sorted(self.trades, key=lambda t: t.entry_time or datetime.min, reverse=True)[:limit]

    def get_balance(self, currency: str = "USDT") -> float:
        return self.balances.get(currency, 0.0)

    def get_total_equity(self) -> float:
        """Total equity = cash + market value of all positions."""
        cash = self.balances.get("USDT", 0.0)
        pos_value = sum(p.market_value for p in self.positions.values() if p.is_active)
        return cash + pos_value

    def _fresh_price(self, symbol: str) -> float | None:
        price = self._last_price_cache.get(symbol)
        price_timestamp = self._last_price_timestamps.get(symbol, 0.0)
        age = datetime.now(UTC).timestamp() - price_timestamp
        if price is None or price <= 0 or age > self.max_price_age_seconds:
            return None
        return price

    @_synchronized
    def update_prices(self, prices: dict[str, float], ohlcv_data: dict[str, Any] | None = None):
        """Update current prices and evaluate pending/stop orders.

        Called periodically with latest prices.

        Parameters
        ----------
        prices : dict[str, float]
            Current prices for each symbol
        ohlcv_data : dict[str, Any], optional
            OHLCV DataFrames for ATR calculation (symbol -> DataFrame)
        """
        invalid = {
            symbol: price
            for symbol, price in prices.items()
            if not math.isfinite(float(price)) or float(price) <= 0
        }
        if invalid:
            raise ValueError(f"Prices must be finite and positive: {invalid}")
        now_timestamp = datetime.now(UTC).timestamp()
        self._last_price_cache.update({k: float(v) for k, v in prices.items()})
        self._last_price_timestamps.update({symbol: now_timestamp for symbol in prices})

        # Check stop-loss / take-profit orders
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos and pos.is_active:
                # Update mark price
                pos.current_price = price
                pos.unrealized_pnl = pos.quantity * (price - pos.entry_price)
                pos.unrealized_pnl_pct = ((price / pos.entry_price) - 1) * 100
                pos.updated_at = datetime.now(UTC)

                # ── Enhanced trailing stop: ATR-based or fixed percentage ────
                if pos.trailing_stop_pct and pos.side == OrderSide.BUY and price > pos.entry_price:
                    # If ATR data available, use ATR-based trailing stop
                    if ohlcv_data and symbol in ohlcv_data and not ohlcv_data[symbol].is_empty():
                        df = ohlcv_data[symbol]
                        if "atr" in df.columns:
                            # Get latest ATR
                            latest_atr = df["atr"].tail(1).item()
                            if latest_atr and latest_atr > 0:
                                # ATR multiplier stored in trailing_stop_pct (repurposed)
                                atr_multiplier = pos.trailing_stop_pct
                                new_sl = price - (latest_atr * atr_multiplier)
                                if pos.stop_loss is None or new_sl > pos.stop_loss:
                                    pos.stop_loss = new_sl
                                    pos.metadata["trailing_stop_high_water"] = new_sl
                                    pos.metadata["trailing_stop_type"] = "atr"
                                    logger.debug(f"ATR trailing stop updated: {symbol} @ {new_sl:.2f} (ATR={latest_atr:.2f}, mult={atr_multiplier})")
                    else:
                        # Fallback: fixed percentage trailing stop
                        new_sl = price * (1.0 - pos.trailing_stop_pct)
                        if pos.stop_loss is None or new_sl > pos.stop_loss:
                            pos.stop_loss = new_sl
                            pos.metadata["trailing_stop_high_water"] = new_sl
                            pos.metadata["trailing_stop_type"] = "fixed_pct"

                # Check stop-loss
                if pos.stop_loss and (
                    pos.side == OrderSide.BUY and price <= pos.stop_loss  # long
                ):
                    logger.warning(f"Stop-loss triggered for {symbol} at {price:.2f}")
                    self._close_position(symbol, price, reason="stop_loss")

                # Check take-profit
                if pos.take_profit and price >= pos.take_profit:
                    logger.info(f"Take-profit triggered for {symbol} at {price:.2f}")
                    self._close_position(symbol, price, reason="take_profit")

        # Check pending limit / stop orders
        for order in list(self.orders.values()):
            if not order.is_open:
                continue
            self._check_limit_order(order, prices)
            self._check_stop_order(order, prices)

        # Record equity snapshot
        equity = self.get_total_equity()
        cash = self.balances.get("USDT", 0.0)
        pos_value = sum(p.market_value for p in self.positions.values() if p.is_active)

        # Track peak equity for drawdown
        peak = self._peak_equity
        if equity > peak:
            self._peak_equity = equity
            peak = equity
        drawdown = (peak - equity) / peak if peak > 0 else 0

        self.equity_history.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "equity": equity,
            "cash": cash,
            "positions_value": pos_value,
        })

        # Log to SQLite every ~20 updates (throttled)
        self._equity_snapshot_counter = getattr(self, '_equity_snapshot_counter', 0) + 1
        if self._equity_snapshot_counter % 20 == 0:
            _log_equity_snapshot(equity, cash, pos_value, drawdown, peak)

        self._save_state()

    @_synchronized
    def close_all_positions(self, reason: str = "manual") -> dict[str, list[str]]:
        """Emergency close using only fresh known prices; never invent a fill."""
        for order in self.get_open_orders():
            self.cancel_order(order.id)

        closed: list[str] = []
        skipped: list[str] = []
        for symbol in list(self.positions.keys()):
            pos = self.positions.get(symbol)
            if pos and pos.is_active:
                price = self._fresh_price(symbol)
                if price is None:
                    skipped.append(symbol)
                    logger.error("Kill switch skipped %s: no fresh market price", symbol)
                    continue
                self._close_position(symbol, price, reason=reason)
                closed.append(symbol)
        logger.warning(f"All positions closed: {reason}")
        remaining = [p.symbol for p in self.get_all_positions()]
        return {"closed": closed, "skipped": skipped, "remaining": remaining}

    @_synchronized
    def reset(self):
        """Reset all state — start fresh."""
        self.balances = {"USDT": self.initial_balance}
        self.orders.clear()
        self.positions.clear()
        self.trades.clear()
        self.equity_history.clear()
        self._last_price_cache.clear()
        self._last_price_timestamps.clear()
        self._peak_equity = self.initial_balance
        self._equity_snapshot_counter = 0
        self._delete_state()
        logger.info("Paper exchange reset to initial state")

    # ── Order Filling ──────────────────────────────────────────────────

    def _fill_market_order(self, order_id: str):
        """Fill a market order immediately (can be partial in future extensions)."""
        order = self.orders[order_id]
        price = self._fresh_price(order.symbol)
        if price is None:
            logger.warning(f"Cannot fill {order_id}: no fresh price for {order.symbol}")
            order.status = OrderStatus.REJECTED
            self._save_state()
            return

        # Apply slippage
        slippage_mult = 1 + (self.slippage if order.side == OrderSide.BUY else -self.slippage)
        fill_price = price * slippage_mult
        self._execute_fill(order, fill_price)

    def _check_limit_order(self, order: Order, prices: dict[str, float]):
        """Check if a limit order should be filled."""
        if order.type != OrderType.LIMIT or order.price is None:
            return
        current_price = prices.get(order.symbol)
        if current_price is None:
            return

        should_fill = False
        if order.side == OrderSide.BUY and current_price <= order.price or order.side == OrderSide.SELL and current_price >= order.price:
            should_fill = True

        if should_fill:
            logger.info(f"Limit order {order.id} filled at {current_price:.2f}")
            self._execute_fill(order, order.price)

    def _check_stop_order(self, order: Order, prices: dict[str, float]):
        """Check if a stop-loss order should be triggered."""
        if order.type != OrderType.STOP_LOSS or order.stop_price is None:
            return
        current_price = prices.get(order.symbol)
        if current_price is None:
            return

        should_trigger = False
        if order.side == OrderSide.SELL and current_price <= order.stop_price or order.side == OrderSide.BUY and current_price >= order.stop_price:
            should_trigger = True

        if should_trigger:
            logger.info(f"Stop order {order.id} triggered at {current_price:.2f}")
            # Fill at current market price (with slippage)
            slippage_mult = 1 + (-self.slippage if order.side == OrderSide.SELL else self.slippage)
            fill_price = current_price * slippage_mult
            self._execute_fill(order, fill_price)

    def _execute_fill(self, order: Order, fill_price: float, fill_amount: float | None = None):
        """Execute a fill — update balances, position, create trade record.
        
        Supports partial fills via fill_amount parameter.
        """
        if fill_amount is None:
            fill_amount = order.amount
        
        # Clamp to remaining amount
        remaining = order.remaining_amount
        if fill_amount > remaining + 1e-8:
            fill_amount = remaining
        
        if fill_amount <= 1e-8:
            return

        fee_amount = fill_amount * fill_price * self.commission
        total_cost = fill_amount * fill_price

        if order.side == OrderSide.BUY:
            # Check if we have enough quote currency
            required = total_cost + fee_amount
            if self.balances.get("USDT", 0.0) < required:
                logger.warning(f"Insufficient balance: need {required:.2f}, have {self.balances.get('USDT', 0.0):.2f}")
                order.status = OrderStatus.REJECTED
                self._save_state()
                return

            # Deduct cost
            self.balances["USDT"] = self.balances.get("USDT", 0.0) - required

            # Update or create position
            pos = self.positions.get(order.symbol)
            if pos and pos.is_active:
                # Increase position (average entry)
                total_qty = pos.quantity + fill_amount
                total_cost_basis = (pos.quantity * pos.entry_price) + total_cost
                pos.entry_price = total_cost_basis / total_qty
                pos.quantity = total_qty
            else:
                # New position
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    side=OrderSide.BUY,
                    quantity=fill_amount,
                    entry_price=fill_price,
                    current_price=fill_price,
                )
            active_position = self.positions[order.symbol]
            active_position.metadata["entry_fees"] = (
                float(active_position.metadata.get("entry_fees", 0.0))
                + fee_amount
            )
            entry_order_ids = active_position.metadata.setdefault("entry_order_ids", [])
            if order.id not in entry_order_ids:
                entry_order_ids.append(order.id)

        elif order.side == OrderSide.SELL:
            # Decrease or close position
            pos = self.positions.get(order.symbol)
            if not pos or pos.quantity < fill_amount - 0.0001:
                logger.warning(f"Cannot sell: insufficient position for {order.symbol}")
                order.status = OrderStatus.REJECTED
                self._save_state()
                return

            # Calculate P&L
            entry_fees_total = float(pos.metadata.get("entry_fees", 0.0))
            entry_fee_alloc = entry_fees_total * min(1.0, fill_amount / pos.quantity)
            pnl = (
                fill_amount * (fill_price - pos.entry_price)
                - entry_fee_alloc
                - fee_amount
            )
            entry_cost = fill_amount * pos.entry_price + entry_fee_alloc
            pnl_pct = pnl / entry_cost * 100 if entry_cost else 0.0

            # Add proceeds
            self.balances["USDT"] = self.balances.get("USDT", 0.0) + (total_cost - fee_amount)

            # Record each completed exit fill so partial closes reconcile too.
            is_full_close = fill_amount >= pos.quantity - 0.0001
            self._record_trade(
                pos,
                fill_price,
                order,
                pnl,
                pnl_pct,
                quantity=fill_amount,
                entry_fee=entry_fee_alloc,
                exit_fee=fee_amount,
                reason="signal" if is_full_close else "partial_exit",
            )

            # Reduce position
            if is_full_close:
                # Full close
                del self.positions[order.symbol]
            else:
                # Partial close
                pos.quantity -= fill_amount
                pos.realized_pnl += pnl
                pos.metadata["entry_fees"] = max(
                    0.0, entry_fees_total - entry_fee_alloc
                )

        # Update order fill status
        prev_filled = order.filled_amount
        order.filled_amount += fill_amount
        if order.avg_fill_price is None:
            order.avg_fill_price = fill_price
        else:
            # VWAP
            order.avg_fill_price = (prev_filled * order.avg_fill_price + fill_amount * fill_price) / order.filled_amount
        order.cost += total_cost
        order.fee += fee_amount
        order.updated_at = datetime.now(UTC)

        if order.filled_amount >= order.amount - 1e-8:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        logger.info(f"Filled {order.id}: {fill_amount}/{order.amount} {order.symbol} @ {fill_price:.2f} (fee: {fee_amount:.4f}, status: {order.status.value})")
        self._save_state()

    def _close_position(self, symbol: str, price: float, reason: str = "manual"):
        """Force-close a position at given price."""
        pos = self.positions.get(symbol)
        if not pos or not pos.is_active:
            return

        fill_price = price * (1.0 - self.slippage)

        # Close order
        order_id = self._next_id("close")
        order = Order(
            id=order_id,
            symbol=symbol,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=pos.quantity,
            price=fill_price,
            status=OrderStatus.FILLED,
            filled_amount=pos.quantity,
            avg_fill_price=fill_price,
            cost=pos.quantity * fill_price,
            fee=pos.quantity * fill_price * self.commission,
        )
        self.orders[order_id] = order

        # P&L
        entry_fee = float(pos.metadata.get("entry_fees", 0.0))
        pnl = pos.quantity * (fill_price - pos.entry_price) - entry_fee - order.fee
        entry_cost = pos.quantity * pos.entry_price + entry_fee
        pnl_pct = pnl / entry_cost * 100 if entry_cost else 0.0

        # Add proceeds
        self.balances["USDT"] = self.balances.get("USDT", 0.0) + (pos.quantity * fill_price - order.fee)

        self._record_trade(pos, fill_price, order, pnl, pnl_pct, reason=reason)
        del self.positions[symbol]
        self._save_state()

    def _record_trade(
        self,
        pos: Position,
        exit_price: float,
        order: Order,
        pnl: float,
        pnl_pct: float,
        quantity: float | None = None,
        entry_fee: float | None = None,
        exit_fee: float | None = None,
        reason: str | None = None,
    ):
        """Record a completed trade."""
        sizing_method = pos.metadata.get("sizing_method", "unknown")
        trade = Trade(
            id=self._next_id("trade"),
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity if quantity is None else quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_fee=(
                float(pos.metadata.get("entry_fees", 0.0))
                if entry_fee is None
                else entry_fee
            ),
            exit_fee=order.fee if exit_fee is None else exit_fee,
            entry_time=pos.opened_at,
            exit_time=datetime.now(UTC),
            entry_order_id=(pos.metadata.get("entry_order_ids") or [None])[0],
            exit_order_id=order.id,
            reason=reason or "signal",
            metadata={"sizing_method": sizing_method},
        )
        self.trades.append(trade)
        _log_trade_to_db(trade, action="close", pnl=pnl, pnl_pct=pnl_pct, reason=reason)
        logger.info(f"Trade closed: {pos.symbol} P&L={pnl:+.2f} ({pnl_pct:+.2f}%) sizing={sizing_method}")

    # ── Order Reconciliation ──────────────────────────────────────────────

    @_synchronized
    def reconcile_orders(self, exchange_orders: dict[str, dict]) -> dict[str, int]:
        """Reconcile local orders with exchange state.
        
        Parameters
        ----------
        exchange_orders : dict
            Dict from exchange order_id to order info: {"status": "...", "filled": ..., "avg_price": ...}
        
        Returns
        -------
        dict
            Summary: {"synced": N, "mismatched": M, "missing": K}
        """
        synced = 0
        mismatched = 0
        missing = 0
        
        for local_id, local_order in self.orders.items():
            # Try to match by client_order_id first, then by id
            exchange_order = None
            if local_order.client_order_id and local_order.client_order_id in exchange_orders:
                exchange_order = exchange_orders[local_order.client_order_id]
            elif local_id in exchange_orders:
                exchange_order = exchange_orders[local_id]
            
            if exchange_order is None:
                # Order not found on exchange — might be pending or canceled
                if local_order.is_open:
                    logger.warning(f"Order {local_id} not found on exchange (missing)")
                    missing += 1
                continue
            
            ex_status = exchange_order.get("status")
            ex_filled = exchange_order.get("filled", 0.0)
            ex_avg_price = exchange_order.get("avg_price")
            
            # Sync status
            if ex_status == "closed" or ex_status == "filled":
                if local_order.status != OrderStatus.FILLED:
                    local_order.status = OrderStatus.FILLED
                    synced += 1
            elif ex_status == "canceled":
                if local_order.status != OrderStatus.CANCELED:
                    local_order.status = OrderStatus.CANCELED
                    synced += 1
            elif ex_status == "rejected":
                if local_order.status != OrderStatus.REJECTED:
                    local_order.status = OrderStatus.REJECTED
                    synced += 1
            elif ex_status in ("open", "partial"):
                if local_order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
                    local_order.status = OrderStatus.PARTIALLY_FILLED
                    synced += 1
            
            # Sync fill amount
            if ex_filled > local_order.filled_amount + 1e-8 and ex_avg_price:
                # Partial fill detected
                fill_delta = ex_filled - local_order.filled_amount
                logger.info(f"Reconciliation: partial fill {local_id} +{fill_delta} @ {ex_avg_price}")
                self._execute_fill(local_order, ex_avg_price, fill_amount=fill_delta)
                synced += 1
            elif abs(ex_filled - local_order.filled_amount) > 1e-8:
                logger.warning(f"Fill mismatch {local_id}: local={local_order.filled_amount}, exchange={ex_filled}")
                mismatched += 1
            
            local_order.updated_at = datetime.now(UTC)
        
        if synced > 0 or mismatched > 0 or missing > 0:
            self._save_state()
        
        return {"synced": synced, "mismatched": mismatched, "missing": missing}

    # ── State Persistence ──────────────────────────────────────────────

    def _state_path(self) -> Path:
        return self.state_dir / f"paper_{self.exchange_name}.json"

    def _load_state(self):
        path = self._state_path()
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.balances = data.get("balances", {"USDT": self.initial_balance})
            self.orders = {k: Order.from_dict(v) for k, v in data.get("orders", {}).items()}
            self.positions = {k: Position.from_dict(v) for k, v in data.get("positions", {}).items()}
            self.trades = [Trade.from_dict(t) for t in data.get("trades", [])]
            self.equity_history = data.get("equity_history", [])
            self._peak_equity = float(data.get("peak_equity", self.initial_balance))
            logger.info(f"Loaded paper state from {path} ({len(self.trades)} trades)")
        except Exception as e:
            raise RuntimeError(f"Paper state is unreadable; refusing unsafe reset: {path}") from e

    def _save_state(self):
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._state_lock:
            data = {
                "balances": self.balances,
                "orders": {k: v.to_dict() for k, v in self.orders.items()},
                "positions": {k: v.to_dict() for k, v in self.positions.items()},
                "trades": [t.to_dict() for t in self.trades],
                "equity_history": self.equity_history[-5000:],
                "peak_equity": self._peak_equity,
            }
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, path)
            finally:
                Path(tmp_name).unlink(missing_ok=True)

    def _delete_state(self):
        path = self._state_path()
        if path.exists():
            path.unlink()
            logger.info(f"Deleted paper state: {path}")

    @staticmethod
    def _next_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
