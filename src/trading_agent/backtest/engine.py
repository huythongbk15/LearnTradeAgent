"""
Backtest engine — vectorized, long-only, với tracking position & equity curve.

Cách hoạt động:
1. Nhận strategy + OHLCV DataFrame
2. Tính indicators + signals
3. Track position: signal +1 → long, -1 → flat (long-only mode)
4. Tính daily returns từ position + price change
5. Build equity curve (compounding)
6. Tính performance metrics
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
    """Vectorized backtest engine — nhanh, đơn giản, long-only."""

    def __init__(
        self,
        strategy: Strategy,
        *,
        initial_capital: float = 10_000.0,
        commission: float = 0.001,
        slippage: float = 0.0005,
        long_only: bool = True,
        # Position sizing
        position_sizing_method: str = "fixed",  # fixed, kelly, half_kelly, vol_target, optimal_f
        fixed_position_pct: float = 0.1,        # Fixed fraction of equity per trade
        kelly_fraction: float = 0.5,            # 0.5 = half-Kelly, 0.25 = quarter-Kelly
        target_annual_vol: float = 0.15,        # For vol targeting
        max_leverage: float = 2.0,              # Max portfolio leverage
        max_position_pct: float = 1.0,          # Max single position as fraction of equity
    ) -> None:
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.long_only = long_only

        # Position sizing config
        self.position_sizing_method = position_sizing_method
        self.fixed_position_pct = fixed_position_pct
        self.kelly_fraction = kelly_fraction
        self.target_annual_vol = target_annual_vol
        self.max_leverage = max_leverage
        self.max_position_pct = max_position_pct

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

        # 3. Compute positions and returns together (handles dynamic sizing)
        df = self._compute_positions_and_returns(df)

        # 4. Build equity curve
        df = self._build_equity_curve(df)

        # 6. Extract trades
        trades = self._extract_trades(df)

        # 7. Build result
        result = BacktestResult(
            strategy_name=self.strategy.name,
            symbol=symbol or "",
            timeframe=timeframe or "",
            params=self.strategy.params,
            equity_curve=df,
            trades=trades,
        )

        self._compute_metrics(result, df, trades)
        return result

    # ── Internal computation steps ─────────────────────────────────────

    def _compute_positions_and_returns(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Compute positions and returns in a single pass.
        This avoids circular dependency between equity and position sizing.
        """
        n = len(df)
        positions = np.zeros(n, dtype=np.float64)
        net_returns = np.zeros(n, dtype=np.float64)
        
        close_prices = df["close"].to_numpy()
        signals = df["signal"].to_numpy()
        atr_values = df["atr"].to_numpy() if "atr" in df.columns else None
        
        # Track equity for position sizing
        equity = self.initial_capital
        position = 0.0
        entry_price = 0.0
        
        for i in range(n):
            signal = signals[i]
            price = close_prices[i]
            
            if signal == 1 and position == 0:
                # Enter long
                atr = atr_values[i] if atr_values is not None else None
                size = self.position_sizer.calculate_position_size(
                    equity=equity,
                    price=price,
                    atr=atr,
                    current_portfolio_value=0,
                    current_positions=0,
                )
                position = size
                entry_price = price
                
            elif signal == -1 and position > 0:
                # Exit long
                pnl = (price - entry_price) * position
                self.position_sizer.update_trade(
                    pnl=pnl,
                    entry_price=entry_price,
                    exit_price=price,
                    size=position
                )
                # Net return for this bar (exit bar)
                net_returns[i] = pnl / equity - (self.commission + self.slippage)
                position = 0.0
                entry_price = 0.0
            
            positions[i] = position
            
            # Compute bar return for holding period
            if position > 0 and i > 0:
                price_return = price / close_prices[i-1] - 1
                # Return on equity = position_value / equity * price_return
                position_value = position * close_prices[i-1]
                net_returns[i] = (position_value / equity) * price_return
            
            # Update equity for next bar's position sizing
            equity *= (1 + net_returns[i])
        
        df = df.with_columns([
            pl.Series("position", positions),
            pl.Series("net_return", net_returns),
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
                entry_price = close_col[i]
                position_size = pos
            elif in_position and pos == 0:
                # Exit
                exit_price = close_col[i]
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

        # Tổng số bars (để annualized)
        date_range = (df["timestamp"].max() - df["timestamp"].min())
        years = date_range.total_seconds() / (365.25 * 24 * 3600)
        if years <= 0:
            years = 1.0

        # Total return
        final_equity = float(df["equity"].tail(1).item())
        result.total_return_pct = (
            (final_equity / self.initial_capital - 1) * 100
        )

        # Annualized return
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
            bars_per_year = n / max(years, 0.01)
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

    engine = BacktestEngine(strategy, **engine_kwargs)
    result = engine.run(df, symbol=symbol, timeframe=timeframe)
    return result
