"""
Stateless technical indicator functions operating on polars expressions.

Each function accepts ``pl.Expr`` objects (e.g. ``pl.col("close")``) and
returns a ``pl.Expr`` so they compose naturally inside
``df.with_columns([...])``.

Design notes
------------
* No DataFrame is mutated; all functions return expression expressions.
* Window sizes are passed positionally where the strategy code expects
  ``func(expr, window)`` but polars uses ``window_size`` keyword.
* Wilder smoothing (RSI, ADX) uses the standard Wilder EMA approximation
  via ``rolling_mean`` — this matches the convention used in
  ``trading_agent/strategies/enhanced_ma.py`` and ``rsi.py``.
"""

from __future__ import annotations

import polars as pl


# ── Simple / Moving Average ──────────────────────────────────────────────

def sma_func(expr: pl.Expr, window: int) -> pl.Expr:
    """Simple moving average over *window* bars."""
    return expr.rolling_mean(window_size=window)


def ma_func(expr: pl.Expr, window: int) -> pl.Expr:
    """Alias for ``sma_func`` — simple moving average."""
    return expr.rolling_mean(window_size=window)


def std_func(expr: pl.Expr, window: int) -> pl.Expr:
    """Rolling sample standard deviation over *window* bars."""
    return expr.rolling_std(window_size=window)


# ── RSI ──────────────────────────────────────────────────────────────────

def rsi_func(expr: pl.Expr, period: int) -> pl.Expr:
    """
    Wilder RSI using simple-average approximation for avg gain/loss.

    Returns a ``pl.Expr`` yielding values in [0, 100].
    """
    delta = expr - expr.shift(1)
    gain = delta.clip(lower_bound=0)
    loss = (-delta).clip(lower_bound=0)

    avg_gain = gain.rolling_mean(window_size=period)
    avg_loss = loss.rolling_mean(window_size=period)

    rs = avg_gain / (avg_loss + 1e-10)
    return (100.0 - (100.0 / (1.0 + rs)))


# ── Bollinger Bands ──────────────────────────────────────────────────────

def bollinger_bands_func(
    expr: pl.Expr,
    window: int,
    std_dev: float = 2.0,
) -> tuple[pl.Expr, pl.Expr, pl.Expr]:
    """
    Bollinger Bands.

    Returns ``(middle, upper, lower)`` expressions where:
    - middle = SMA(window)
    - upper  = middle + std_dev * rolling_std(window)
    - lower  = middle - std_dev * rolling_std(window)
    """
    middle = sma_func(expr, window)
    std = std_func(expr, window)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return middle, upper, lower


# ── ATR ──────────────────────────────────────────────────────────────────

def atr_func(
    high: pl.Expr,
    low: pl.Expr,
    close: pl.Expr,
    period: int,
) -> pl.Expr:
    """
    Average True Range (Wilder smoothed).

    Uses Wilder's smoothing: ``ATR_t = (ATR_{t-1} * (n-1) + TR_t) / n``
    which is approximated via ``rolling_mean`` of the True Range, matching
    the convention in ``enhanced_ma.py``.
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pl.max_horizontal(tr1, tr2, tr3)

    return tr.rolling_mean(window_size=period)


# ── ADX ──────────────────────────────────────────────────────────────────

def adx_func(
    high: pl.Expr,
    low: pl.Expr,
    close: pl.Expr,
    period: int,
) -> pl.Expr:
    """
    Average Directional Index (ADX).

    Computes +DI, -DI, then DX, and finally ADX as the Wilder-smoothed
    rolling mean of DX.  Returns a single ``pl.Expr`` for ADX.

    This mirrors the implementation in ``enhanced_ma.py`` so that
    ``trend_pullback`` and ``ma_adx`` strategies remain consistent.
    """
    prev_close = close.shift(1)

    # True Range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pl.max_horizontal(tr1, tr2, tr3)
    atr = tr.rolling_mean(window_size=period)
    atr_safe = atr + 1e-9

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
    minus_dm = pl.when((down_move > up_move) & (down_move > 0)).then(down_move).otherwise(0.0)

    # Directional Indicators
    plus_di = 100.0 * (plus_dm.rolling_mean(window_size=period) / atr_safe)
    minus_di = 100.0 * (minus_dm.rolling_mean(window_size=period) / atr_safe)

    # Directional Index
    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx = dx.rolling_mean(window_size=period)

    return adx


__all__ = [
    "sma_func",
    "std_func",
    "ma_func",
    "rsi_func",
    "bollinger_bands_func",
    "atr_func",
    "adx_func",
]
