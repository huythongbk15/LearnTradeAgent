"""
Execution Engine — unified interface to trade.

Kết nối Phase 2 (signals) với Phase 3 (execution).
Tự động chọn paper/live mode dựa trên config.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.agents.base import AgentMessage
from trading_agent.config.loader import config
from trading_agent.execution.indicators import (
    compute_atr,
    compute_atr_position_size,
)
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.canonical import BrokerGateway, AuthorizedOrder

from trading_agent.execution.types import (
    Order,
    OrderStatus,
)

logger = logging.getLogger(__name__)

# ── Graceful shutdown handling ────────────────────────────────────────

_shutdown_handlers: list[Callable[[], None]] = []
_shutdown_lock = threading.Lock()
_shutdown_initiated = False


def register_shutdown_handler(handler: Callable[[], None]) -> None:
    """Register a function to be called on graceful shutdown (SIGTERM/SIGINT)."""
    with _shutdown_lock:
        _shutdown_handlers.append(handler)


def _run_shutdown_handlers() -> None:
    """Execute all registered shutdown handlers."""
    global _shutdown_initiated
    with _shutdown_lock:
        if _shutdown_initiated:
            return
        _shutdown_initiated = True
        handlers = list(_shutdown_handlers)
        _shutdown_handlers.clear()

    for handler in handlers:
        try:
            handler()
        except Exception as e:
            logger.error(f"Shutdown handler error: {e}", exc_info=True)


def _signal_handler(signum: int, frame) -> None:
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, initiating graceful shutdown...")
    _run_shutdown_handlers()
    sys.exit(0)


def setup_graceful_shutdown() -> None:
    """Install signal handlers for SIGTERM and SIGINT."""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    logger.debug("Graceful shutdown handlers installed (SIGTERM, SIGINT)")


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

        # ── Canonical broker gateway ───────────────────────────────

        # Use config default values
        self.exchange = PaperExchange(
            exchange_name=self.exchange_name,
            initial_balance=(
                config.initial_capital if initial_capital is None else initial_capital
            ),
            commission=config.commission if commission is None else commission,
            slippage=config.slippage if slippage is None else slippage,
        )

        # ── Canonical broker gateway ───────────────────────────────
        self.gateway = BrokerGateway(adapter=self.exchange)

        # Register graceful shutdown handler
        register_shutdown_handler(self._graceful_shutdown)

    def _graceful_shutdown(self) -> None:
        """Called on SIGTERM/SIGINT to close positions and persist state."""
        logger.info("Graceful shutdown: closing all positions...")
        try:
            self.close_all(reason="graceful_shutdown")
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")

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
            - atr: current ATR value (optional, for risk-based sizing)
            - risk_reward: target R:R ratio (optional, default 2.0)
            - trailing_atr_mult: ATR multiplier for trailing stop (optional, default 2.0)

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
        symbol = (
            signal.details.get("symbol", "BTC/USDT") if signal.details else "BTC/USDT"
        )
        max_pos_pct = signal.max_position_size_pct or 0.25
        confidence = signal.confidence or 0.5

        # Calculate amount based on portfolio allocation
        equity = self.exchange.get_total_equity()
        current_price = self._get_current_price(symbol)
        if current_price is None or current_price <= 0:
            logger.warning(f"Cannot execute: no price data for {symbol}")
            return orders

        # Get ATR for risk-based sizing
        atr = None
        if signal.details and "atr" in signal.details:
            atr = signal.details["atr"]
        else:
            # Try to load pre-computed ATR from storage first
            try:
                from trading_agent.data.storage import load_ohlcv

                df = load_ohlcv(self.exchange_name, symbol, "1h")
                if not df.is_empty() and "atr" in df.columns:
                    atr = float(df["atr"].tail(1).item())
                elif not df.is_empty():
                    # Fallback: compute ATR on-demand
                    atr_expr = compute_atr(df, period=14)
                    atr_series = df.select(atr_expr).to_series()
                    atr = (
                        float(atr_series.tail(1).item())
                        if not atr_series.is_empty()
                        else None
                    )
            except Exception as e:
                logger.warning(f"ATR load failed for {symbol}: {e}")

        # Get existing position
        existing_pos = self.exchange.get_position(symbol)
        existing_qty = existing_pos.quantity if existing_pos else 0.0

        if signal_str == "BUY":
            # Calculate buy amount
            if not existing_pos or not existing_pos.is_active:
                # ── Enhanced position sizing ──────────────────────────────
                if atr and atr > 0:
                    # ATR-based position sizing: risk 2% per trade
                    risk_pct = 0.02 * confidence  # Scale risk by confidence
                    atr_mult = (
                        signal.details.get("trailing_atr_mult", 2.0)
                        if signal.details
                        else 2.0
                    )
                    amount = compute_atr_position_size(
                        equity=equity,
                        atr=atr,
                        current_price=current_price,
                        risk_pct=risk_pct,
                        atr_multiplier=atr_mult,
                    )
                    # Cap at max position size
                    max_amount = (equity * max_pos_pct) / current_price
                    amount = min(amount, max_amount)
                    sizing_method = "ATR"
                else:
                    # Fallback: percentage of equity
                    max_cost = equity * max_pos_pct
                    amount = max_cost / current_price
                    sizing_method = "fixed_pct"

                # Round to reasonable precision (0.001 for BTC)
                amount = max(0.001, round(amount, 4))

                logger.info(
                    f"Signal: BUY {amount} {symbol} "
                    f"(${amount * current_price:,.2f}, "
                    f"{max_pos_pct * 100:.0f}% of ${equity:,.2f}) "
                    f"[sizing: {sizing_method}, ATR={atr:.2f}]"
                    if atr
                    else f"Signal: BUY {amount} {symbol} "
                    f"(${amount * current_price:,.2f}, "
                    f"{max_pos_pct * 100:.0f}% of ${equity:,.2f}) "
                    f"[sizing: {sizing_method}]"
                )
                order = self.gateway.submit(
                    AuthorizedOrder(
                        intent_id=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
                        symbol=symbol,
                        side="buy",
                        quantity=amount,
                        idempotency_key=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
                        price_reference=current_price,
                    ),
                    correlation_id=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
                )
                orders.append(order)

                # ── Set ATR-based trailing stop and take-profit ──────────
                if order.status == OrderStatus.FILLED:
                    pos = self.exchange.get_position(symbol)
                    if pos:
                        # Store sizing method in position metadata for trade history
                        pos.metadata["sizing_method"] = sizing_method

                        # ATR-based trailing stop
                        if atr and atr > 0:
                            trailing_mult = (
                                signal.details.get("trailing_atr_mult", 2.0)
                                if signal.details
                                else 2.0
                            )
                            pos.trailing_stop_pct = (
                                trailing_mult  # repurpose field as ATR multiplier
                            )
                            pos.stop_loss = current_price - (atr * trailing_mult)
                            pos.metadata["trailing_stop_type"] = "atr"
                            logger.info(
                                f"ATR trailing stop set: {symbol} @ {pos.stop_loss:.2f} (ATR={atr:.2f}, mult={trailing_mult})"
                            )
                        else:
                            # Fixed percentage fallback
                            stop_pct = 0.05
                            pos.stop_loss = current_price * (1 - stop_pct)
                            pos.metadata["trailing_stop_type"] = "fixed_pct"
                            logger.info(
                                f"Fixed stop-loss set: {symbol} @ {pos.stop_loss:.2f} ({stop_pct * 100:.1f}%)"
                            )

                        # Active take-profit: R:R based (default 2:1)
                        risk_reward = (
                            signal.details.get("risk_reward", 2.0)
                            if signal.details
                            else 2.0
                        )
                        if atr and atr > 0:
                            take_profit_dist = atr * trailing_mult * risk_reward
                            pos.take_profit = current_price + take_profit_dist
                        else:
                            # Fallback: fixed R:R from stop
                            stop_dist = current_price - pos.stop_loss
                            pos.take_profit = current_price + (stop_dist * risk_reward)
                        pos.metadata["risk_reward"] = risk_reward
                        logger.info(
                            f"Take-profit set: {symbol} @ {pos.take_profit:.2f} (R:R={risk_reward})"
                        )

                        # Persist position metadata (sizing_method, trailing_stop_type, risk_reward)
                        self.exchange._save_state()

            else:
                logger.info(
                    f"BUY signal but already in position: {existing_pos.quantity} {symbol}"
                )

        elif signal_str == "SELL":
            if existing_pos and existing_pos.is_active:
                amount = existing_pos.quantity
                logger.info(f"Signal: SELL {amount} {symbol}")
                order = self.gateway.submit(
                    AuthorizedOrder(
                        intent_id=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
                        symbol=symbol,
                        side="sell",
                        quantity=amount,
                        idempotency_key=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
                        price_reference=current_price,
                    ),
                    correlation_id=f"engine-{symbol.replace('/', '-')}-{int(datetime.now(UTC).timestamp())}",
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
            logger.info(
                f"Stop-loss set: {symbol} @ {pos.stop_loss:.2f} "
                f"({stop_pct * 100:.1f}% below entry)"
            )

    # ── Price feed ─────────────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for all tracked symbols.

        Call this regularly (e.g. every new candle) to:
        - Update unrealized P&L
        - Check stop-loss/take-profit triggers
        - Fill pending limit/stop orders
        """
        self.exchange.update_prices(prices)

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        tf = timeframe.lower().strip()
        units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
        if len(tf) < 2 or tf[-1] not in units:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        try:
            amount = int(tf[:-1])
        except ValueError as exc:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}") from exc
        if amount <= 0:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        return amount * units[tf[-1]]

    def update_market_price(
        self,
        symbol: str,
        price: float,
        candle_open_time: datetime,
        timeframe: str,
    ) -> None:
        """Accept a price only from a recently closed candle."""
        if not isinstance(candle_open_time, datetime):
            raise ValueError("Market data timestamp must be a datetime")
        if candle_open_time.tzinfo is None:
            candle_open_time = candle_open_time.replace(tzinfo=UTC)
        else:
            candle_open_time = candle_open_time.astimezone(UTC)

        duration = self._timeframe_seconds(timeframe)
        bar_close = candle_open_time + timedelta(seconds=duration)
        now = datetime.now(UTC)
        if now < bar_close:
            raise ValueError("Refusing execution from an incomplete candle")
        if (now - bar_close).total_seconds() > max(duration * 2, 300):
            raise ValueError("Refusing execution from stale market data")
        self.update_prices({symbol: price})

    def update_from_dataframe(self, symbol: str, df: Any, timeframe: str = "1h"):
        """Update from the latest recently closed OHLCV candle."""
        if df.is_empty():
            return
        latest_close = float(df["close"].tail(1).item())
        if "timestamp" not in df.columns:
            raise ValueError("OHLCV data must contain timestamp for execution")
        latest_timestamp = df["timestamp"].tail(1).item()
        self.update_market_price(symbol, latest_close, latest_timestamp, timeframe)

    def update_with_atr(self, symbol: str, df: Any):
        """Update prices with OHLCV data for ATR-based trailing stop.

        Computes ATR and passes OHLCV to paper exchange for dynamic trailing stops.
        """
        if df.is_empty():
            return
        latest_close = float(df["close"].tail(1).item())
        prices = {symbol: latest_close}

        # Use pre-computed ATR if available, otherwise compute
        if "atr" not in df.columns:
            atr_series = compute_atr(df, period=14)
            df = df.with_columns(atr_series)

        ohlcv_data = {symbol: df}
        self.exchange.update_prices(prices, ohlcv_data)

    def update_all_with_atr(self, data: dict[str, Any]):
        """Update all tracked symbols with OHLCV data for ATR trailing stops.

        Parameters
        ----------
        data : dict[str, Any]
            Symbol -> OHLCV DataFrame mapping
        """
        prices = {}
        ohlcv_data = {}
        for symbol, df in data.items():
            if not df.is_empty():
                latest_close = float(df["close"].tail(1).item())
                prices[symbol] = latest_close
                if "atr" not in df.columns:
                    atr_series = compute_atr(df, period=14)
                    df = df.with_columns(atr_series)
                ohlcv_data[symbol] = df

        if prices:
            self.exchange.update_prices(prices, ohlcv_data)

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
            "return_pct": round(((total_equity / config.initial_capital) - 1) * 100, 2),
            "open_positions": len(positions),
            "open_orders": len(open_orders),
            "total_trades": len(self.exchange.trades),
        }

    def get_positions_summary(self) -> list[dict[str, Any]]:
        """Get detailed position info."""
        result = []
        for pos in self.exchange.get_all_positions():
            result.append(
                {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "current_price": pos.current_price,
                    "pnl": round(pos.unrealized_pnl, 2),
                    "pnl_pct": round(pos.unrealized_pnl_pct, 2),
                    "value": round(pos.market_value, 2),
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                }
            )
        return result

    def get_trade_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent trade history."""
        return [t.to_dict() for t in self.exchange.get_trade_history(limit)]

    def close_all(self, reason: str = "manual_kill") -> dict[str, list[str]]:
        """Emergency close all positions."""
        return self.exchange.close_all_positions(reason=reason)

    def reset(self):
        """Reset paper exchange to initial state."""
        self.exchange.reset()

    # ── Helpers ────────────────────────────────────────────────────────

    def _get_current_price(self, symbol: str) -> float | None:
        """Return only a recently timestamped price; never revive stale storage."""
        return self.exchange._fresh_price(symbol)
