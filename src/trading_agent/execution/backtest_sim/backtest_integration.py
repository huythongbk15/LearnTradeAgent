"""
Backtest Integration for Execution Simulator.

Wraps ExecutionSimulator to work with BacktestEngine,
providing realistic fill simulation during backtests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl

from trading_agent.execution.backtest_sim.models import (
    ExecutionSimulator,
    SimulatedOrder,
    SimulatedFill,
    OrderBookSnapshot,
    OrderType,
    OrderSide,
    FillModel,
    ImpactModel,
    create_execution_simulator,
)
from trading_agent.strategies.base import Strategy


@dataclass
class SimulatorBacktestResult:
    """Results from simulator-enhanced backtest."""
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    avg_hold_bars: float
    calmar_ratio: float
    equity_curve: pl.DataFrame
    trades: pl.DataFrame
    fills: list[SimulatedFill]
    total_fees: float
    total_slippage: float
    avg_latency_ms: float
    maker_ratio: float


class SimulatorBacktestEngine:
    """
    Backtest engine with realistic execution simulation.
    
    Uses ExecutionSimulator to model:
    - Latency
    - Slippage
    - Market impact
    - Partial fills
    - Maker/taker fees
    - Queue position for limit orders
    """
    
    def __init__(
        self,
        strategy: Strategy,
        initial_capital: float = 100000,
        commission: float = 0.0005,  # Fallback if simulator not used
        slippage: float = 0.0002,    # Fallback
        long_only: bool = True,
        simulator: ExecutionSimulator | None = None,
        use_simulator: bool = True,
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.long_only = long_only
        
        self.simulator = simulator or create_execution_simulator()
        self.use_simulator = use_simulator
        
        # State
        self._position = 0.0
        self._cash = initial_capital
        self._equity = initial_capital
        self._entry_price = 0.0
        self._entry_time = None
        self._trades = []
        self._equity_curve = []
        self._order_counter = 0
    
    def _generate_order_id(self) -> str:
        self._order_counter += 1
        return f"ord_{self._order_counter:06d}"
    
    def _create_order_book_snapshot(
        self, row: dict, symbol: str, timestamp: datetime
    ) -> OrderBookSnapshot:
        """Create order book snapshot from OHLCV row."""
        close = row["close"]
        high = row["high"]
        low = row["low"]
        volume = row.get("volume", 0)
        
        # For daily OHLCV, we don't have true bid-ask spread.
        # Use fixed spread for major pairs (1-5 bps) or estimate from volatility.
        # Typical spreads: BTC ~1-2 bps, ETH ~2-3 bps, alts ~5-20 bps.
        if "BTC" in symbol:
            spread_bps = 2.0
        elif "ETH" in symbol:
            spread_bps = 3.0
        else:
            spread_bps = 10.0  # Conservative for alts
        
        spread = spread_bps / 10000
        
        # Rough bid/ask
        mid = close
        half_spread = mid * spread / 2
        bid = mid - half_spread
        ask = mid + half_spread
        
        # Estimate volatility (2% default)
        volatility = 0.02
        
        # Book depth: use 10x daily volume as depth (deep book for major pairs)
        daily_volume_quote = volume * close
        depth_quote = daily_volume_quote * 10
        depth_base = depth_quote / close
        
        return OrderBookSnapshot(
            symbol=symbol,
            timestamp=timestamp,
            bid_price=bid,
            bid_size=depth_base / 2,
            ask_price=ask,
            ask_size=depth_base / 2,
            mid_price=mid,
            spread=ask - bid,
            spread_bps=spread_bps,
            volume_24h=daily_volume_quote,
            volatility=volatility,
        )
    
    def run(self, df: pl.DataFrame, symbol: str, timeframe: str) -> SimulatorBacktestResult:
        """Run backtest with execution simulation."""
        
        # Generate signals from strategy
        df_with_indicators = self.strategy.compute_indicators(df)
        signals = self.strategy.generate_signals(df_with_indicators)
        
        # Add signals to dataframe
        df_signals = df_with_indicators.with_columns(signals.alias("signal"))
        
        # Reset state
        self._position = 0.0
        self._cash = self.initial_capital
        self._equity = self.initial_capital
        self._entry_price = 0.0
        self._entry_time = None
        self._trades = []
        self._equity_curve = []
        self._order_counter = 0
        self.simulator.state = self.simulator.state.__class__()  # Reset simulator state
        
        # Process each bar
        for row in df_signals.iter_rows(named=True):
            timestamp = row["timestamp"]
            close = row["close"]
            signal = row["signal"]
            
            # Update order book
            book = self._create_order_book_snapshot(row, symbol, timestamp)
            self.simulator.update_order_book(book)
            
            # Advance simulator (process fills)
            fills = self.simulator.step(timestamp)
            for fill in fills:
                self._process_fill(fill)
            
            # Handle new signals
            if signal != 0 and self._position == 0:
                # Enter position
                order = self._create_entry_order(symbol, signal, close, timestamp)
                self.simulator.submit_order(order)
                # Market orders fill immediately in step()
            elif signal != 0 and self._position != 0:
                # Check for exit signal (opposite direction)
                if (self._position > 0 and signal < 0) or (self._position < 0 and signal > 0):
                    order = self._create_exit_order(symbol, close, timestamp)
                    self.simulator.submit_order(order)
            
            # Update equity
            self._equity = self._cash + self._position * close
            self._equity_curve.append({
                "timestamp": timestamp,
                "equity": self._equity,
                "position": self._position,
                "cash": self._cash,
            })
        
        # Close any remaining position at end
        if self._position != 0:
            last_row = df_signals.tail(1).to_dicts()[0]
            order = self._create_exit_order(symbol, last_row["close"], last_row["timestamp"])
            self.simulator.submit_order(order)
            # Process final fills
            fills = self.simulator.step(last_row["timestamp"])
            for fill in fills:
                self._process_fill(fill)
        
        return self._compute_results(symbol, timeframe)
    
    def _create_entry_order(
        self, symbol: str, signal: int, price: float, timestamp: datetime
    ) -> SimulatedOrder:
        """Create entry order from signal."""
        side = OrderSide.BUY if signal > 0 else OrderSide.SELL
        qty = self._cash * 0.95 / price  # 95% of cash
        
        if self.long_only and side == OrderSide.SELL:
            qty = 0
        
        return SimulatedOrder(
            order_id=self._generate_order_id(),
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            price=price,
            timestamp=timestamp,
            strategy_id=self.strategy.name,
        )
    
    def _create_exit_order(
        self, symbol: str, price: float, timestamp: datetime
    ) -> SimulatedOrder:
        """Create exit order for current position."""
        side = OrderSide.SELL if self._position > 0 else OrderSide.BUY
        
        return SimulatedOrder(
            order_id=self._generate_order_id(),
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=abs(self._position),
            price=price,
            timestamp=timestamp,
            strategy_id=self.strategy.name,
        )
    
    def _process_fill(self, fill: SimulatedFill) -> None:
        """Process a fill and update position/cash."""
        if fill.side == OrderSide.BUY:
            cost = fill.quantity * fill.price + fill.fee
            self._cash -= cost
            self._position += fill.quantity
            if self._entry_price == 0:
                self._entry_price = fill.price
                self._entry_time = fill.timestamp
        else:
            proceeds = fill.quantity * fill.price - fill.fee
            self._cash += proceeds
            
            # Record trade
            if self._entry_price > 0:
                pnl = (fill.price - self._entry_price) * self._position
                pnl_pct = (fill.price - self._entry_price) / self._entry_price * 100
                hold_bars = (fill.timestamp - self._entry_time).total_seconds() / 3600 if self._entry_time else 0
                
                self._trades.append({
                    "entry_time": self._entry_time,
                    "exit_time": fill.timestamp,
                    "entry_price": self._entry_price,
                    "exit_price": fill.price,
                    "quantity": self._position,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "hold_bars": hold_bars,
                    "fees": fill.fee,
                    "side": "long" if self._position > 0 else "short",
                })
            
            self._position -= fill.quantity
            if self._position <= 0:
                self._position = 0
                self._entry_price = 0.0
                self._entry_time = None
    
    def _compute_results(self, symbol: str, timeframe: str) -> SimulatorBacktestResult:
        """Compute final backtest metrics."""
        eq_df = pl.DataFrame(self._equity_curve)
        trades_df = pl.DataFrame(self._trades) if self._trades else pl.DataFrame()
        
        # Returns
        if len(eq_df) > 1:
            returns = eq_df["equity"].diff() / eq_df["equity"].shift(1)
            returns = returns.drop_nulls()
            
            total_return = (eq_df["equity"][-1] - self.initial_capital) / self.initial_capital * 100
            
            # Annualize (assume daily bars)
            bars_per_year = 365 if timeframe == "1d" else 365 * 24
            n_bars = len(returns)
            annual_factor = np.sqrt(bars_per_year / max(n_bars, 1))
            
            if returns.std() > 0:
                sharpe = float(returns.mean() / returns.std() * annual_factor)
            else:
                sharpe = 0.0
            
            # Sortino
            downside = returns.filter(returns < 0)
            if len(downside) > 0 and downside.std() > 0:
                sortino = float(returns.mean() / downside.std() * annual_factor)
            else:
                sortino = sharpe
            
            # Max drawdown
            equity = eq_df["equity"].to_numpy()
            peak = equity[0]
            max_dd = 0.0
            for v in equity:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak
                if dd > max_dd:
                    max_dd = dd
            max_dd_pct = max_dd * 100
            
            # Calmar
            calmar = total_return / max_dd_pct if max_dd_pct > 0 else 0.0
        else:
            total_return = 0.0
            sharpe = 0.0
            sortino = 0.0
            max_dd_pct = 0.0
            calmar = 0.0
        
        # Trade stats
        if len(self._trades) > 0:
            wins = sum(1 for t in self._trades if t["pnl"] > 0)
            win_rate = wins / len(self._trades)
            
            gross_profit = sum(t["pnl"] for t in self._trades if t["pnl"] > 0)
            gross_loss = abs(sum(t["pnl"] for t in self._trades if t["pnl"] < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
            
            avg_hold = sum(t["hold_bars"] for t in self._trades) / len(self._trades)
        else:
            win_rate = 0.0
            profit_factor = 0.0
            avg_hold = 0.0
        
        # Fill stats
        all_fills = self.simulator.state.fill_history
        total_fees = sum(f.fee for f in all_fills)
        maker_fills = sum(1 for f in all_fills if f.is_maker)
        maker_ratio = maker_fills / len(all_fills) if all_fills else 0.0
        avg_latency = sum(f.latency_ms for f in all_fills) / len(all_fills) if all_fills else 0.0
        
        # Estimate slippage from fills vs mid price at fill time
        total_slippage = 0.0
        for fill in all_fills:
            mid_at_fill = fill.mid_price_at_fill
            if mid_at_fill > 0:
                if fill.side == OrderSide.BUY:
                    slippage = (fill.price - mid_at_fill) / mid_at_fill
                else:
                    slippage = (mid_at_fill - fill.price) / mid_at_fill
                total_slippage += slippage * fill.quantity * fill.price
        
        return SimulatorBacktestResult(
            total_return_pct=total_return,
            annualized_return_pct=total_return * (365 / max(len(eq_df), 1)),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown_pct=max_dd_pct,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=len(self._trades),
            avg_hold_bars=avg_hold,
            calmar_ratio=calmar,
            equity_curve=eq_df,
            trades=trades_df,
            fills=all_fills,
            total_fees=total_fees,
            total_slippage=total_slippage,
            avg_latency_ms=avg_latency,
            maker_ratio=maker_ratio,
        )


def run_simulator_backtest(
    strategy: Strategy,
    df: pl.DataFrame,
    symbol: str,
    timeframe: str,
    initial_capital: float = 100000,
    simulator_config: dict | None = None,
) -> SimulatorBacktestResult:
    """Convenience function to run simulator backtest."""
    
    if simulator_config:
        simulator = create_execution_simulator(**simulator_config)
    else:
        simulator = create_execution_simulator()
    
    engine = SimulatorBacktestEngine(
        strategy=strategy,
        initial_capital=initial_capital,
        simulator=simulator,
        use_simulator=True,
    )
    
    return engine.run(df, symbol, timeframe)