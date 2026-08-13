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
    exit_date: Any | None
    entry_price: float
    exit_price: float
    direction: int  # 1 = long
    pnl_pct: float
    bars_held: int
    pnl_abs: float = 0.0
    fees: float = 0.0
    exit_reason: str = "signal"
    is_open: bool = False


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
        spread_bps: float = 0.0,  # Spread in basis points (e.g., 5 = 0.05%)
        long_only: bool = True,
        # SL/TP/Trailing
        atr_sl_mult: float = 0.0,  # 0 = disabled, else ATR multiplier for stop loss
        atr_tp_mult: float = 0.0,  # 0 = disabled, else ATR multiplier for take profit
        trailing_atr_mult: float = 0.0,  # 0 = disabled, else ATR multiplier for trailing
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
        if initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not 0 <= commission < 1:
            raise ValueError("commission must be in [0, 1)")
        if not 0 <= slippage < 1:
            raise ValueError("slippage must be in [0, 1)")
        if spread_bps < 0 or spread_bps >= 20_000:
            raise ValueError("spread_bps must be in [0, 20000)")
        if not long_only:
            raise NotImplementedError(
                "BacktestEngine currently supports long_only=True"
            )

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
        self.position_sizer = PositionSizer(
            PositionSizingParams(
                method=position_sizing_method,
                fixed_fraction=fixed_position_pct,
                kelly_fraction=kelly_fraction,
                target_annual_vol=target_annual_vol,
                max_leverage=max_leverage,
                max_position_pct=max_position_pct,
            )
        )

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
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing OHLCV columns: {sorted(missing)}")
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
        float_columns = {
            "position": np.zeros(n, dtype=np.float64),
            "net_return": np.zeros(n, dtype=np.float64),
            "entry_price": np.zeros(n, dtype=np.float64),
            "stop_loss": np.zeros(n, dtype=np.float64),
            "take_profit": np.zeros(n, dtype=np.float64),
            "cash": np.zeros(n, dtype=np.float64),
            "ledger_equity": np.zeros(n, dtype=np.float64),
            "entry_fill": np.zeros(n, dtype=np.float64),
            "exit_fill": np.zeros(n, dtype=np.float64),
            "fees": np.zeros(n, dtype=np.float64),
            "realized_pnl": np.zeros(n, dtype=np.float64),
        }
        if n == 0:
            return df.with_columns(
                [
                    pl.Series(name, values, dtype=pl.Float64)
                    for name, values in float_columns.items()
                ]
            )

        open_prices = df["open"].to_numpy().astype(np.float64)
        high_prices = df["high"].to_numpy().astype(np.float64)
        low_prices = df["low"].to_numpy().astype(np.float64)
        close_prices = df["close"].to_numpy().astype(np.float64)
        signals = df["signal"].to_numpy()
        timestamps = df["timestamp"].to_numpy()
        atr_values = df["atr"].to_numpy() if "atr" in df.columns else None

        if (
            np.any(~np.isfinite(open_prices))
            or np.any(~np.isfinite(high_prices))
            or np.any(~np.isfinite(low_prices))
            or np.any(~np.isfinite(close_prices))
            or np.any(open_prices <= 0)
            or np.any(high_prices <= 0)
            or np.any(low_prices <= 0)
            or np.any(close_prices <= 0)
        ):
            raise ValueError("OHLC prices must be finite and positive")
        if np.any(high_prices < np.maximum(open_prices, close_prices)):
            raise ValueError("Invalid OHLC: high is below open/close")
        if np.any(low_prices > np.minimum(open_prices, close_prices)):
            raise ValueError("Invalid OHLC: low is above open/close")

        position = 0.0
        cash = float(self.initial_capital)
        entry_price = 0.0
        entry_fee = 0.0
        entry_idx = -1
        current_sl = 0.0
        current_tp = 0.0
        trailing_high = 0.0
        previous_equity = float(self.initial_capital)
        spread_half = self.spread_bps / 20_000.0
        buy_factor = 1.0 + spread_half + self.slippage
        sell_factor = 1.0 - spread_half - self.slippage
        self._ledger_trades: list[Trade] = []

        def close_position(i: int, reference_price: float, reason: str) -> None:
            nonlocal cash, position, entry_price, entry_fee, entry_idx
            nonlocal current_sl, current_tp, trailing_high

            exit_price = reference_price * sell_factor
            exit_notional = position * exit_price
            exit_fee = exit_notional * self.commission
            cash += exit_notional - exit_fee
            net_pnl = (exit_price - entry_price) * position - entry_fee - exit_fee
            entry_notional = entry_price * position

            float_columns["exit_fill"][i] = exit_price
            float_columns["fees"][i] += exit_fee
            float_columns["realized_pnl"][i] = net_pnl
            self.position_sizer.update_trade(
                pnl=net_pnl,
                entry_price=entry_price,
                exit_price=exit_price,
                size=position,
            )
            self._ledger_trades.append(
                Trade(
                    entry_date=timestamps[entry_idx],
                    exit_date=timestamps[i],
                    entry_price=float(entry_price),
                    exit_price=float(exit_price),
                    direction=1,
                    pnl_pct=float(net_pnl / entry_notional * 100)
                    if entry_notional
                    else 0.0,
                    bars_held=max(i - entry_idx, 0),
                    pnl_abs=float(net_pnl),
                    fees=float(entry_fee + exit_fee),
                    exit_reason=reason,
                )
            )

            position = 0.0
            entry_price = 0.0
            entry_fee = 0.0
            entry_idx = -1
            current_sl = 0.0
            current_tp = 0.0
            trailing_high = 0.0

        for i in range(n):
            # A signal is only actionable at the next bar's open.
            previous_signal = signals[i - 1] if i > 0 else 0

            if previous_signal == -1 and position > 0:
                close_position(i, open_prices[i], "signal")
            elif previous_signal == 1 and position == 0:
                signal_atr = atr_values[i - 1] if atr_values is not None else None
                signal_atr = (
                    float(signal_atr)
                    if signal_atr is not None
                    and np.isfinite(signal_atr)
                    and signal_atr > 0
                    else None
                )
                buy_price = open_prices[i] * buy_factor
                size = self.position_sizer.calculate_position_size(
                    equity=previous_equity,
                    price=buy_price,
                    atr=signal_atr,
                    current_portfolio_value=0,
                    current_positions=0,
                )
                # Commission is separate from the slippage-adjusted fill price.
                max_affordable = cash / (buy_price * (1.0 + self.commission))
                position = max(0.0, min(float(size), max_affordable))
                if position > 0:
                    entry_price = buy_price
                    entry_idx = i
                    entry_notional = position * entry_price
                    entry_fee = entry_notional * self.commission
                    cash -= entry_notional + entry_fee
                    float_columns["entry_fill"][i] = entry_price
                    float_columns["fees"][i] += entry_fee
                    if signal_atr:
                        if self.atr_sl_mult > 0:
                            current_sl = entry_price - signal_atr * self.atr_sl_mult
                        if self.atr_tp_mult > 0:
                            current_tp = entry_price + signal_atr * self.atr_tp_mult
                        if self.trailing_atr_mult > 0:
                            trailing_high = entry_price

            # Intrabar protective orders. If both SL and TP are touched, use the
            # conservative stop-first assumption because tick order is unknown.
            if position > 0:
                if current_sl > 0 and low_prices[i] <= current_sl:
                    stop_reference = (
                        open_prices[i] if open_prices[i] <= current_sl else current_sl
                    )
                    close_position(i, stop_reference, "stop_loss")
                elif current_tp > 0 and high_prices[i] >= current_tp:
                    tp_reference = (
                        open_prices[i] if open_prices[i] >= current_tp else current_tp
                    )
                    close_position(i, tp_reference, "take_profit")
                elif self.trailing_atr_mult > 0 and atr_values is not None:
                    known_atr = atr_values[i - 1] if i > 0 else None
                    if (
                        known_atr is not None
                        and np.isfinite(known_atr)
                        and known_atr > 0
                    ):
                        trailing_high = max(trailing_high, high_prices[i])
                        current_sl = max(
                            current_sl,
                            trailing_high - float(known_atr) * self.trailing_atr_mult,
                        )

            end_equity = cash + position * close_prices[i]
            float_columns["position"][i] = position
            float_columns["entry_price"][i] = entry_price
            float_columns["stop_loss"][i] = current_sl
            float_columns["take_profit"][i] = current_tp
            float_columns["cash"][i] = cash
            float_columns["ledger_equity"][i] = end_equity
            float_columns["net_return"][i] = (
                end_equity / previous_equity - 1.0 if previous_equity else 0.0
            )
            previous_equity = end_equity

        if position > 0:
            mark_price = close_prices[-1]
            unrealized_pnl = (mark_price - entry_price) * position - entry_fee
            entry_notional = entry_price * position
            self._ledger_trades.append(
                Trade(
                    entry_date=timestamps[entry_idx],
                    exit_date=None,
                    entry_price=float(entry_price),
                    exit_price=float(mark_price),
                    direction=1,
                    pnl_pct=float(unrealized_pnl / entry_notional * 100)
                    if entry_notional
                    else 0.0,
                    bars_held=max(n - 1 - entry_idx, 0),
                    pnl_abs=float(unrealized_pnl),
                    fees=float(entry_fee),
                    exit_reason="open",
                    is_open=True,
                )
            )

        return df.with_columns(
            [
                pl.Series(name, values, dtype=pl.Float64)
                for name, values in float_columns.items()
            ]
        )

    def _build_equity_curve(self, df: pl.DataFrame) -> pl.DataFrame:
        """Build equity directly from the reconciled cash/position ledger."""
        equity = pl.col("ledger_equity").alias("equity")

        # Peak equity (running max)
        peak = equity.cum_max().alias("peak")

        # Drawdown
        dd = (equity / peak - 1).alias("drawdown")

        return df.with_columns(
            [
                equity,
                peak,
                dd,
            ]
        )

    def _extract_trades(self, df: pl.DataFrame) -> list[Trade]:
        """Return trades recorded by the reconciled execution ledger."""
        return list(getattr(self, "_ledger_trades", []))

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
        result.total_return_pct = (final_equity / self.initial_capital - 1) * 100

        # Annualized return (using bars_per_year)
        years = n / bars_per_year
        if years > 0:
            result.annualized_return_pct = (
                (final_equity / self.initial_capital) ** (1 / years) - 1
            ) * 100

        # Sharpe & Sortino (dùng net_return hàng bar)
        returns = df["net_return"].drop_nulls()

        if len(returns) > 1:
            avg_ret = returns.mean()
            std_ret = returns.std()
            neg_ret = returns.filter(returns < 0)
            neg_std = neg_ret.std() if len(neg_ret) > 1 else 0.0

            # Sharpe: annualized = avg / std * sqrt(bars_per_year)
            result.sharpe_ratio = float(
                (avg_ret / max(float(std_ret), 1e-9)) * (bars_per_year**0.5)
            )
            result.sortino_ratio = float(
                (avg_ret / max(float(neg_std), 1e-9)) * (bars_per_year**0.5)
            )

        # Max drawdown
        result.max_drawdown_pct = float(df["drawdown"].min() * 100)

        # Trade stats
        closed_trades = [trade for trade in trades if not trade.is_open]
        result.total_trades = len(closed_trades)
        if closed_trades:
            winning = [t for t in closed_trades if t.pnl_abs > 0]
            result.win_rate = len(winning) / len(closed_trades)

            gross_profit = sum(t.pnl_abs for t in winning)
            gross_loss = abs(sum(t.pnl_abs for t in closed_trades if t.pnl_abs <= 0))
            result.profit_factor = gross_profit / max(gross_loss, 0.001)
            result.avg_hold_bars = float(
                sum(t.bars_held for t in closed_trades) / len(closed_trades)
            )

        # Calmar = annualized return / |max dd|
        result.calmar_ratio = result.annualized_return_pct / max(
            abs(result.max_drawdown_pct), 0.01
        )

    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert timeframe string to minutes."""
        tf = timeframe.lower().strip()
        if tf.endswith("m"):
            return int(tf[:-1])
        elif tf.endswith("h"):
            return int(tf[:-1]) * 60
        elif tf.endswith("d"):
            return int(tf[:-1]) * 24 * 60
        elif tf.endswith("w"):
            return int(tf[:-1]) * 7 * 24 * 60
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")


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
