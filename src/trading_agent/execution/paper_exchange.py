"""
Paper Exchange — simulated exchange for safe testing.

Mô phỏng order matching, slippage, và fee mà không cần API thật.
State lưu dưới dạng JSON để persist qua các lần chạy.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.execution.types import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Trade,
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
            close_trade(
                trade_id=trade.id,
                exit_price=pnl or 0,
                pnl=pnl,
                pnl_pct=pnl_pct,
                fee=trade.fee or 0.0,
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
    ):
        self.exchange_name = exchange_name
        self.commission = commission
        self.slippage = slippage
        self.state_dir = Path(state_dir)

        # In-memory state
        self.balances: dict[str, float] = {
            "USDT": initial_balance,  # quote currency
        }
        self.orders: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_history: list[dict[str, Any]] = []
        self._last_price_cache: dict[str, float] = {}
        self._peak_equity: float = initial_balance
        self._equity_snapshot_counter: int = 0

        # Load existing state if any
        self._load_state()

    # ── Public API ─────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: OrderSide | str,
        order_type: OrderType | str = OrderType.MARKET,
        amount: float = 0.0,
        price: float | None = None,
        stop_price: float | None = None,
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
        )

        self.orders[order_id] = order
        logger.info(f"Order {order_id}: {side.value.upper()} {amount} {symbol} @ {price or 'market'}")

        # Market orders fill immediately
        if order_type == OrderType.MARKET:
            self._fill_market_order(order_id)

        return order

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

    def update_prices(self, prices: dict[str, float]):
        """Update current prices and evaluate pending/stop orders.

        Called periodically with latest prices.
        """
        self._last_price_cache.update(prices)

        # Check stop-loss / take-profit orders
        for symbol, price in prices.items():
            pos = self.positions.get(symbol)
            if pos and pos.is_active:
                # Update mark price
                pos.current_price = price
                pos.unrealized_pnl = pos.quantity * (price - pos.entry_price)
                pos.unrealized_pnl_pct = ((price / pos.entry_price) - 1) * 100
                pos.updated_at = datetime.now(UTC)

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

    def close_all_positions(self, reason: str = "manual"):
        """Emergency close — kill switch."""
        prices = self._last_price_cache
        for symbol in list(self.positions.keys()):
            pos = self.positions.get(symbol)
            if pos and pos.is_active:
                price = prices.get(symbol, pos.entry_price)
                self._close_position(symbol, price, reason=reason)
        logger.warning(f"All positions closed: {reason}")

    def reset(self):
        """Reset all state — start fresh."""
        self.balances = {"USDT": 10_000.0}
        self.orders.clear()
        self.positions.clear()
        self.trades.clear()
        self.equity_history.clear()
        self._delete_state()
        logger.info("Paper exchange reset to initial state")

    # ── Order Filling ──────────────────────────────────────────────────

    def _fill_market_order(self, order_id: str):
        """Fill a market order immediately."""
        order = self.orders[order_id]
        price = self._last_price_cache.get(order.symbol)
        if not price:
            logger.warning(f"Cannot fill {order_id}: no price data for {order.symbol}")
            order.status = OrderStatus.REJECTED
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

    def _execute_fill(self, order: Order, fill_price: float):
        """Execute a fill — update balances, position, create trade record."""
        fee_amount = order.amount * fill_price * self.commission
        total_cost = order.amount * fill_price

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
                total_qty = pos.quantity + order.amount
                total_cost_basis = (pos.quantity * pos.entry_price) + total_cost
                pos.entry_price = total_cost_basis / total_qty
                pos.quantity = total_qty
            else:
                # New position
                self.positions[order.symbol] = Position(
                    symbol=order.symbol,
                    side=OrderSide.BUY,
                    quantity=order.amount,
                    entry_price=fill_price,
                    current_price=fill_price,
                )

        elif order.side == OrderSide.SELL:
            # Decrease or close position
            pos = self.positions.get(order.symbol)
            if not pos or pos.quantity < order.amount - 0.0001:
                logger.warning(f"Cannot sell: insufficient position for {order.symbol}")
                order.status = OrderStatus.REJECTED
                self._save_state()
                return

            # Calculate P&L
            pnl = order.amount * (fill_price - pos.entry_price) - fee_amount
            pnl_pct = ((fill_price / pos.entry_price) - 1) * 100

            # Add proceeds
            self.balances["USDT"] = self.balances.get("USDT", 0.0) + (total_cost - fee_amount)

            # Reduce position
            if order.amount >= pos.quantity - 0.0001:
                # Full close
                self._record_trade(pos, fill_price, order, pnl, pnl_pct)
                del self.positions[order.symbol]
            else:
                # Partial close
                pos.quantity -= order.amount
                pos.realized_pnl += pnl

        # Update order
        order.status = OrderStatus.FILLED
        order.filled_amount = order.amount
        order.avg_fill_price = fill_price
        order.cost = total_cost
        order.fee = fee_amount
        order.updated_at = datetime.now(UTC)

        logger.info(f"Filled {order.id}: {order.amount} {order.symbol} @ {fill_price:.2f} (fee: {fee_amount:.4f})")
        self._save_state()

        # Log trade entry to SQLite on BUY fill (new position opened)
        if order.side == OrderSide.BUY:
            pos = self.positions.get(order.symbol)
            if pos and pos.entry_price == fill_price:
                from trading_agent.execution.types import Trade as TradeType
                dummy_trade = TradeType(
                    id=order.id,
                    symbol=order.symbol,
                    side=OrderSide.BUY,
                    entry_price=fill_price,
                    exit_price=None,
                    quantity=order.amount,
                    pnl=0,
                    pnl_pct=0,
                    entry_time=datetime.now(UTC),
                    exit_time=None,
                    entry_order_id=order.id,
                    reason="entry",
                )
                _log_trade_to_db(dummy_trade, action="open")

    def _close_position(self, symbol: str, price: float, reason: str = "manual"):
        """Force-close a position at given price."""
        pos = self.positions.get(symbol)
        if not pos or not pos.is_active:
            return

        # Close order
        order_id = self._next_id("close")
        order = Order(
            id=order_id,
            symbol=symbol,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=pos.quantity,
            price=price,
            status=OrderStatus.FILLED,
            filled_amount=pos.quantity,
            avg_fill_price=price,
            cost=pos.quantity * price,
            fee=pos.quantity * price * self.commission,
        )
        self.orders[order_id] = order

        # P&L
        pnl = pos.quantity * (price - pos.entry_price) - order.fee
        pnl_pct = ((price / pos.entry_price) - 1) * 100

        # Add proceeds
        self.balances["USDT"] = self.balances.get("USDT", 0.0) + (pos.quantity * price - order.fee)

        self._record_trade(pos, price, order, pnl, pnl_pct, reason=reason)
        del self.positions[symbol]
        self._save_state()

    def _record_trade(
        self,
        pos: Position,
        exit_price: float,
        order: Order,
        pnl: float,
        pnl_pct: float,
        reason: str | None = None,
    ):
        """Record a completed trade."""
        trade = Trade(
            id=self._next_id("trade"),
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=pos.opened_at,
            exit_time=datetime.now(UTC),
            entry_order_id=order.id,
            reason=reason or "signal",
        )
        self.trades.append(trade)
        _log_trade_to_db(trade, action="close", pnl=pnl, pnl_pct=pnl_pct, reason=reason)
        logger.info(f"Trade closed: {pos.symbol} P&L={pnl:+.2f} ({pnl_pct:+.2f}%)")

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
            self.balances = data.get("balances", {"USDT": 10_000.0})
            self.orders = {k: Order.from_dict(v) for k, v in data.get("orders", {}).items()}
            self.positions = {k: Position.from_dict(v) for k, v in data.get("positions", {}).items()}
            self.trades = [Trade.from_dict(t) for t in data.get("trades", [])]
            self.equity_history = data.get("equity_history", [])
            logger.info(f"Loaded paper state from {path} ({len(self.trades)} trades)")
        except Exception as e:
            logger.warning(f"Failed to load paper state: {e}")

    def _save_state(self):
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "balances": self.balances,
            "orders": {k: v.to_dict() for k, v in self.orders.items()},
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trades": [t.to_dict() for t in self.trades],
            "equity_history": self.equity_history[-5000:],  # keep last 5000 snapshots
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _delete_state(self):
        path = self._state_path()
        if path.exists():
            path.unlink()
            logger.info(f"Deleted paper state: {path}")

    @staticmethod
    def _next_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"
