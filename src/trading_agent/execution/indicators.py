"""
Technical indicators for risk management: ATR, volatility metrics.
"""

from __future__ import annotations

import polars as pl


def compute_atr(
    df: pl.DataFrame,
    period: int = 14,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pl.Series:
    """Compute Average True Range (ATR).

    Parameters
    ----------
    df : pl.DataFrame
        OHLCV data
    period : int
        ATR period (default 14)
    high_col : str
        High price column
    low_col : str
        Low price column
    close_col : str
        Close price column

    Returns
    -------
    pl.Series
        ATR values
    """
    # True Range = max(H-L, |H-C_prev|, |L-C_prev|)
    prev_close = pl.col(close_col).shift(1)

    tr = pl.max_horizontal([
        pl.col(high_col) - pl.col(low_col),
        (pl.col(high_col) - prev_close).abs(),
        (pl.col(low_col) - prev_close).abs(),
    ])

    atr = tr.rolling_mean(window_size=period)
    return atr.alias("atr")


def compute_volatility(
    df: pl.DataFrame,
    period: int = 20,
    close_col: str = "close",
) -> pl.Series:
    """Compute rolling standard deviation of returns (volatility proxy)."""
    returns = pl.col(close_col).pct_change()
    vol = returns.rolling_std(window_size=period)
    return vol.alias("volatility")


def compute_atr_trailing_stop(
    df: pl.DataFrame,
    period: int = 14,
    multiplier: float = 2.0,
    side: str = "long",
) -> pl.Series:
    """Compute ATR-based trailing stop levels.

    For long positions: stop = close - multiplier * ATR
    For short positions: stop = close + multiplier * ATR

    The stop only ratchets in the favorable direction.

    Parameters
    ----------
    df : pl.DataFrame
        OHLCV data
    period : int
        ATR period
    multiplier : float
        ATR multiplier for stop distance
    side : str
        "long" or "short"

    Returns
    -------
    pl.Series
        Trailing stop levels
    """
    atr = compute_atr(df, period=period)
    close = pl.col("close")

    if side == "long":
        # Initial stop = close - multiplier * ATR
        raw_stop = close - multiplier * atr
    else:
        raw_stop = close + multiplier * atr

    # Ratchet: only move stop in favorable direction
    if side == "long":
        # For long: stop only goes up (never down)
        # Use cummax to ratchet
        trailing_stop = raw_stop.cum_max()
    else:
        # For short: stop only goes down (never up)
        trailing_stop = raw_stop.cum_min()

    return trailing_stop.alias("trailing_stop")


def compute_kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """Compute Kelly criterion fraction.

    f* = (bp - q) / b
    where b = avg_win / avg_loss (payoff ratio)
    p = win_rate
    q = 1 - win_rate

    Returns fraction of capital to risk per trade.
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0

    b = avg_win / avg_loss
    p = win_rate
    q = 1 - p
    kelly = (b * p - q) / b

    # Cap at reasonable max (e.g., 25%)
    return max(0.0, min(kelly, 0.25))


def compute_volatility_target_size(
    equity: float,
    target_vol_pct: float,
    asset_vol_pct: float,
    max_leverage: float = 1.0,
) -> float:
    """Compute position size for volatility targeting.

    position_size = equity * (target_vol / asset_vol)

    Parameters
    ----------
    equity : float
        Total portfolio equity
    target_vol_pct : float
        Target portfolio volatility (e.g., 0.15 for 15%)
    asset_vol_pct : float
        Asset's current volatility (e.g., 0.50 for 50%)
    max_leverage : float
        Maximum leverage allowed

    Returns
    -------
    float
        Position size in quote currency
    """
    if asset_vol_pct <= 0:
        return 0.0

    leverage = target_vol_pct / asset_vol_pct
    leverage = min(leverage, max_leverage)
    return equity * leverage


def compute_atr_position_size(
    equity: float,
    atr: float,
    current_price: float,
    risk_pct: float = 0.02,
    atr_multiplier: float = 2.0,
) -> float:
    """Compute position size based on ATR risk.

    stop_distance = ATR * multiplier
    position_size = equity * risk_pct / stop_distance_pct

    Parameters
    ----------
    equity : float
        Total equity
    atr : float
        Current ATR value
    current_price : float
        Current price
    risk_pct : float
        Risk per trade as fraction of equity (e.g., 0.02 = 2%)
    atr_multiplier : float
        ATR multiplier for stop distance

    Returns
    -------
    float
        Position size in base currency
    """
    stop_distance = atr * atr_multiplier
    stop_distance_pct = stop_distance / current_price

    if stop_distance_pct <= 0:
        return 0.0

    risk_amount = equity * risk_pct
    position_value = risk_amount / stop_distance_pct
    position_size = position_value / current_price

    return position_size