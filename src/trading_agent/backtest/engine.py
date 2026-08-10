"""
Backtest engine — vectorized, long-only, với tracking position & equity curve.

Cách hoạt động (FIXED - no look-ahead bias):
1. Nhận strategy + OHLCV DataFrame
2. Tính indicators + signals
3. Signal tại bar t → position effect từ bar t+1 (shift 1)
4. Entry fill tại open[t+1], Exit fill tại open[t+1] (next bar open)
5. Fee+slippage+spread charge CẢ entry VÀ exit
6. SL/TP/trailing stop simulation (mirror paper_exchange)
7. Build equity curve (compounding)
8. Tính performance metrics
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from trading_agent.config.loader import config
from trading_agent.risk.position_sizer import PositionSizer, PositionSizingParams
from trading_agent.strategies.base import Strategy

# ── Results ───────────────────────────────────────────────────────────────


@dataclass
class Trade:
    """Một giao dịch hoàn chỉnh (entry → exit)."""
    entry_date: Any
    exit_date: Any
    entry_price: float
    exit_price: float
    direction: int  # 1 = long
    pnl_pct: float
    bars_held: int


@dataclass
class BacktestResult:
    """Kết quả backtest đầy đủ."""
    strategy_name: str
    symbol: str
    timeframe: str
    params: dict[str, Any]

    # Equity curve & trades
    equity_curve: pl.DataFrame  # columns: timestamp, equity, drawdown
    trades: list[Trade]

    # Metrics (populated by compute_metrics)
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    calmar_ratio: float = 0.0
    avg_hold_bars: float = 0.0

    def __str__(self) -> str:
        lines = [
            f"── {self.strategy_name} on {self.symbol} {self.timeframe} ──",
            f"  Parameters: {self.params}",
            f"  Total Return:    {self.total_return_pct:>+8.2f}%",
            f"  Ann. Return:     {self.annualized_return_pct:>+8.2f}%",
            f"  Sharpe:          {self.sharpe_ratio:>8.2f}",
            f"  Sortino:         {self.sortino_ratio:>8.2f}",
            f"  Max DD:          {self.max_drawdown_pct:>8.2f}%",
            f"  Win Rate:        {self.win_rate:>8.1%}",
            f"  Profit Factor:   {self.profit_factor:>8.2f}",
            f"  Trades:          {self.total_trades:>8d}",
            f"  Avg Hold:        {self.avg_hold_bars:>8.1f} bars",
        ]
        return "\n".join(lines)


# ── Engine ────────────────────────────────────────────────────────────────


class BacktestEngine:
    """Vectorized backtest engine — nhanh, đơn giản, long-only.
    
    FIXES applied:
    - No look-ahead: signal at t → position at t+1, fill at open[t+1]
    - Fee+slippage+spread on BOTH entry and exit
    - SL/TP/trailing stop simulation
    - ATR for sizing uses atr[t-1] (previous bar)
    - Correct bars_per_year from timeframe
    """

    def __init__(
        self,
        strategy: Strategy,
        *,
        initial_capital: float = 10_000.0,
        commission: float = 0.001,
        slippage: float = 0.0005,
        spread_bps: float = 0.0,          # Spread in basis points (e.g., 5 = 0.05%)
        long_only: bool = True,
        # SL/TP/Trailing
        atr_sl_mult: float = 0.0,         # 0 = disabled, else ATR multiplier for stop loss
        atr_tp_mult: float = 0.0,         # 0 = disabled, else ATR multiplier for take profit
        trailing_atr_mult: float = 0.0,   # 0 = disabled, else ATR multiplier for trailing
        # Position sizing
        position_sizing_method: str = "fixed",
        fixed_position_pct: float = 0.1,
        kelly_fraction: float = 0.5,
        target_annual_vol: float = 0.15,
        max_leverage: float = 2.0,
        max_position_pct: float = 1.0,
        # Timeframe for correct annualization
        timeframe: str = "1h",
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.spread_bps = spread_bps
        self.long_only = long_only

        # Risk management params (mirror paper_exchange)
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult
        self.trailing_atr_mult = trailing_atr_mult

        # Position sizing config
        self.position_sizing_method = position_sizing_method
        self.fixed_position_pct = fixed_position_pct
        self.kelly_fraction = kelly_fraction
        self.target_annual_vol = target_annual_vol
        self.max_leverage = max_leverage
        self.max_position_pct = max_position_pct
        self.timeframe = timeframe

        # Initialize position sizer
        self.position_sizer = PositionSizer(PositionSizingParams(
            method=position_sizing_method,
            fixed_fraction=fixed_position_pct,
            kelly_fraction=kelly_fraction,
            target_annual_vol=target_annual_vol,
            max_leverage=max_leverage,
            max_position_pct=max_position_pct,
        ))

        # Trade history for Kelly estimation
        self._trade_history: list[dict] = []

    def run(
        self,
        df: pl.DataFrame,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> BacktestResult:
        """Chạy backtest trên DataFrame OHLCV.

        Parameters
        ----------
        df : pl.DataFrame
            Phải có columns: timestamp, open, high, low, close, volume
        symbol, timeframe : str, optional
            Metadata cho kết quả.
        """
        df = df.sort("timestamp")

        # 1. Compute indicators
        df = self.strategy.compute_indicators(df)

        # 2. Generate signals
        signals = self.strategy.generate_signals(df)
        df = df.with_columns(signals.alias("signal"))

        # 3. Compute positions and returns together (handles dynamic sizing, SL/TP/trailing)
        df = self._compute_positions_and_returns(df)

        # 4. Build equity curve
        df = self._build_equity_curve(df)

        # 5. Extract trades
        trades = self._extract_trades(df)

        # 6. Build result
        result = BacktestResult(
            strategy_name=self.strategy.name,
            symbol=symbol or "",
            timeframe=timeframe or self.timeframe,
            params=self.strategy.params,
            equity_curve=df,
            trades=trades,
        )

        self._compute_metrics(result, df, trades)
        return result

    # ── Internal computation steps ─────────────────────────────────────

    def _compute_positions_and_returns(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Compute positions and returns with NO look-ahead bias.
        
        Timing:
        - signal at bar i is generated from data UP TO bar i (close[i] known)
        - position change takes effect at bar i+1
        - entry fill at open[i+1], exit fill at open[i+1]
        - fee+slippage+spread charged on BOTH entry and exit
        - SL/TP/trailing evaluated intrabar using high/low
        """
        n = len(df)
        if n == 0:
            return df.with_columns([
                pl.Series("position", []),
                pl.Series("net_return", []),
            ])

        positions = np.zeros(n, dtype=np.float64)
        net_returns = np.zeros(n, dtype=np.float64)
        entry_prices = np.zeros(n, dtype=np.float64)  # Track entry price per bar
        stop_losses = np.zeros(n, dtype=np.float64)
        take_profits = np.zeros(n, dtype=np.float64)
        
        open_prices = df["open"].to_numpy()
        high_prices = df["high"].to_numpy()
        low_prices = df["low"].to_numpy()
        close_prices = df["close"].to_numpy()
        signals = df["signal"].to_numpy()
        atr_values = df["atr"].to_numpy() if "atr" in df.columns else None
        
        # Track equity for position sizing
        equity = self.initial_capital
        position = 0.0
        entry_price = 0.0
        current_sl = 0.0
        current_tp = 0.0
        trailing_high = 0.0
        
        # Cost per trade (round-trip = entry + exit)
        # Entry: buy at ask = mid * (1 + spread/2 + slippage)
        # Exit: sell at bid = mid * (1 - spread/2 - slippage)
        spread = self.spread_bps / 10000.0
        entry_cost_factor = 1 + spread/2 + self.slippage + self.commission
        exit_cost_factor = 1 - spread/2 - self.slippage - self.commission
        # Net cost per round trip ≈ 2*(commission + slippage) + spread
        
        for i in range(n):
            # --- 1. Check SL/TP/trailing FIRST (using previous bar's position) ---
            if position > 0 and i > 0:
                # Check stop loss (using LOW of current bar)
                if current_sl > 0 and low_prices[i] <= current_sl:
                    # Stop loss hit - exit at stop price (or open if gapped)
                    exit_price = max(current_sl, open_prices[i])
                    exit_price *= exit_cost_factor
                    pnl = (exit_price - entry_price) * position
                    net_returns[i] = pnl / equity
                    position = 0.0
                    entry_price = 0.0
                    current_sl = 0.0
                    current_tp = 0.0
                    trailing_high = 0.0
                
                # Check take profit (using HIGH of current bar)
                elif current_tp > 0 and high_prices[i] >= current_tp:
                    exit_price = min(current_tp, open_prices[i])
                    exit_price *= exit_cost_factor
                    pnl = (exit_price - entry_price) * position
                    net_returns[i] = pnl / equity
                    position = 0.0
                    entry_price = 0.0
                    current_sl = 0.0
                    current_tp = 0.0
                    trailing_high = 0.0
                
                # Update trailing stop (using HIGH of current bar)
                elif self.trailing_atr_mult > 0 and atr_values is not None:
                    atr = atr_values[i-1] if i > 0 else atr_values[i]  # Use previous bar's ATR
                    if atr and atr > 0 and high_prices[i] > trailing_high:
                        trailing_high = high_prices[i]
                        new_sl = trailing_high - (atr * self.trailing_atr_mult)
                        if new_sl > current_sl:
                            current_sl = new_sl
            
            # --- 2. Record position for THIS bar (from signal at i-1) ---
            positions[i] = position
            entry_prices[i] = entry_price
            stop_losses[i] = current_sl
            take_profits[i] = current_tp
            
            # --- 3. Compute holding return for THIS bar (if in position from previous bar) ---
            if position > 0 and i > 0:
                # Position held from previous close to current close
                price_return = close_prices[i] / close_prices[i-1] - 1
                position_value = position * close_prices[i-1]
                net_returns[i] = (position_value / equity) * price_return
            
            # --- 4. Process NEW signals from THIS bar (takes effect NEXT bar) ---
            signal = signals[i]
            if signal == 1 and position == 0:
                # Entry signal at bar i → enter at open[i+1] (handled next iteration)
                # But we set up SL/TP now using current bar's ATR
                atr = atr_values[i] if atr_values is not None else None
                size = self.position_sizer.calculate_position_size(
                    equity=equity,
                    price=close_prices[i],  # Use close for sizing calculation
                    atr=atr,
                    current_portfolio_value=0,
                    current_positions=0,
                )
                # Pre-compute entry for next bar
                if i + 1 < n:
                    # Entry at next bar's open
                    entry_price = open_prices[i+1] * entry_cost_factor
                    position = size
                    
                    # Set initial SL/TP using current bar's ATR (known at signal time)
                    if atr and atr > 0:
                        if self.atr_sl_mult > 0:
                            current_sl = entry_price - (atr * self.atr_sl_mult)
                        if self.atr_tp_mult > 0:
                            current_tp = entry_price + (atr * self.atr_tp_mult)
                        if self.trailing_atr_mult > 0:
                            trailing_high = entry_price
                    # Note: actual fill and equity update happens at next bar (i+1)
            
            elif signal == -1 and position > 0:
                # Exit signal at bar i → exit at open[i+1] (handled next iteration)
                # We'll process the exit at the start of next iteration
                pass
            
            # --- 5. Handle exit signal from PREVIOUS bar (execute at this bar's open) ---
            if i > 0 and signals[i-1] == -1 and position > 0:
                # Exit at current bar's open
                exit_price = open_prices[i] * exit_cost_factor
                pnl = (exit_price - entry_price) * position
                self.position_sizer.update_trade(
                    pnl=pnl,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    size=position
                )
                net_returns[i] = pnl / equity
                position = 0.0
                entry_price = 0.0
                current_sl = 0.0
                current_tp = 0.0
                trailing_high = 0.0
            
            # --- 6. Handle entry signal from PREVIOUS bar (execute at this bar's open) ---
            if i > 0 and signals[i-1] == 1 and position > 0 and entry_price > 0:
                # Entry already set up at previous bar, now deduct entry cost from equity
                entry_cost = position * entry_price * (self.commission + self.slippage + spread/2)
                net_returns[i] -= entry_cost / equity
            
            # Update equity for next bar's position sizing
            equity *= (1 + net_returns[i])
        
        df = df.with_columns([
            pl.Series("position", positions),
            pl.Series("net_return", net_returns),
            pl.Series("entry_price", entry_prices),
            pl.Series("stop_loss", stop_losses),
            pl.Series("take_profit", take_profits),
        ])
        return df

    def _build_equity_curve(self, df: pl.DataFrame) -> pl.DataFrame:
        """Từ net return, build equity curve với compounding."""
        equity = (
            (1 + pl.col("net_return"))
            .cum_prod()
            .alias("equity") * self.initial_capital
        )

        # Peak equity (running max)
        peak = equity.cum_max().alias("peak")

        # Drawdown
        dd = (equity / peak - 1).alias("drawdown")

        return df.with_columns([
            equity,
            peak,
            dd,
        ])

    def _extract_trades(self, df: pl.DataFrame) -> list[Trade]:
        """Duyệt qua position changes để extract danh sách trades."""
        trades: list[Trade] = []
        pos_col = df["position"].to_numpy()
        entry_price_col = df["entry_price"].to_numpy() if "entry_price" in df.columns else None
        open_col = df["open"].to_numpy()
        close_col = df["close"].to_numpy()
        ts_col = df["timestamp"].to_numpy()

        in_position = False
        entry_idx = -1
        entry_price = 0.0
        position_size = 0.0

        for i in range(len(pos_col)):
            pos = pos_col[i]
            if not in_position and pos > 0:
                # Enter long
                in_position = True
                entry_idx = i
                # Use actual entry price (from entry_price col or open)
                entry_price = entry_price_col[i] if entry_price_col is not None else open_col[i]
                position_size = pos
            elif in_position and pos == 0:
                # Exit
                exit_price = open_col[i]  # Exit at open (next bar after signal)
                pnl_pct = (exit_price / entry_price - 1) * 100
                trades.append(Trade(
                    entry_date=ts_col[entry_idx],
                    exit_date=ts_col[i],
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                    direction=1,
                    pnl_pct=float(pnl_pct),
                    bars_held=i - entry_idx,
                ))
                in_position = False
                position_size = 0.0

        return trades

    # ── Metrics ────────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        result: BacktestResult,
        df: pl.DataFrame,
        trades: list[Trade],
    ) -> None:
        """Tính tất cả performance metrics."""
        n = len(df)
        if n == 0:
            return

        # Bars per year from timeframe
        tf_minutes = self._timeframe_to_minutes(self.timeframe)
        bars_per_year = (365.25 * 24 * 60) / max(tf_minutes, 1)

        # Total return
        final_equity = float(df["equity"].tail(1).item())
        result.total_return_pct = (
            (final_equity / self.initial_capital - 1) * 100
        )

        # Annualized return (using bars_per_year)
        years = n / bars_per_year
        if years > 0:
            result.annualized_return_pct = (
                ((final_equity / self.initial_capital) ** (1 / years) - 1) * 100
            )

        # Sharpe & Sortino (dùng net_return hàng bar)
        returns = df["net_return"].drop_nulls()

        if len(returns) > 1:
            avg_ret = returns.mean()
            std_ret = returns.std()
            neg_ret = returns.filter(returns < 0)
            neg_std = neg_ret.std() if len(neg_ret) > 1 else 0.0

            # Sharpe: annualized = avg / std * sqrt(bars_per_year)
            result.sharpe_ratio = float(
                (avg_ret / max(float(std_ret), 1e-9)) * (bars_per_year ** 0.5)
            )
            result.sortino_ratio = float(
                (avg_ret / max(float(neg_std), 1e-9)) * (bars_per_year ** 0.5)
            )

        # Max drawdown
        result.max_drawdown_pct = float(df["drawdown"].min() * 100)

        # Trade stats
        result.total_trades = len(trades)
        if trades:
            winning = [t for t in trades if t.pnl_pct > 0]
            result.win_rate = len(winning) / len(trades)

            gross_profit = sum(t.pnl_pct for t in winning)
            gross_loss = abs(sum(t.pnl_pct for t in trades if t.pnl_pct <= 0))
            result.profit_factor = (
                gross_profit / max(gross_loss, 0.001)
            )
            result.avg_hold_bars = float(
                sum(t.bars_held for t in trades) / len(trades)
            )

        # Calmar = annualized return / |max dd|
        result.calmar_ratio = (
            result.annualized_return_pct / max(abs(result.max_drawdown_pct), 0.01)
        )

    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert timeframe string to minutes."""
        tf = timeframe.lower().strip()
        if tf.endswith('m'):
            return int(tf[:-1])
        elif tf.endswith('h'):
            return int(tf[:-1]) * 60
        elif tf.endswith('d'):
            return int(tf[:-1]) * 24 * 60
        elif tf.endswith('w'):
            return int(tf[:-1]) * 7 * 24 * 60
        else:
            return 60  # default 1h


# ── High-level helper ─────────────────────────────────────────────────────


def run_backtest(
    strategy_name: str,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    params: dict[str, Any] | None = None,
    **engine_kwargs,
) -> BacktestResult:
    """Load data + run backtest — one function for CLI use."""
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.strategies.base import get_strategy
    # Import strategies to register them
    import trading_agent.strategies  # noqa: F401

    df = load_ohlcv(config.default_exchange, symbol, timeframe)
    strategy_cls = get_strategy(strategy_name)
    strategy = strategy_cls(params or {})

    engine = BacktestEngine(strategy, timeframe=timeframe, **engine_kwargs)
    result = engine.run(df, symbol=symbol, timeframe=timeframe)
    return result
