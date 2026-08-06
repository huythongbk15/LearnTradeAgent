#!/usr/bin/env python3
"""
Market Regime Detection Utilities

Independent module for regime detection — no circular imports.
Can be used by agents, strategies, and risk management.
"""

from __future__ import annotations

import polars as pl


def add_regime_indicators(
    df: pl.DataFrame,
    atr_period: int = 14,
    lookback: int = 252
) -> pl.DataFrame:
    """
    Add market regime indicators to dataframe.
    
    Regimes:
    - Volatility: low_vol (ATR pctl < 33), mid_vol (33-67), high_vol (>67)
    - Trend: trending (ADX > 25), ranging (ADX <= 25)
    - Trend direction: up (DI+ > DI-), down (DI- > DI+)
    
    Returns df with columns: atr, atr_pctl, vol_regime, adx, trend_regime, trend_dir
    """
    high = pl.col("high")
    low = pl.col("low")
    close = pl.col("close")
    prev_close = close.shift(1)
    
    # ATR - compute first
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pl.max_horizontal(tr1, tr2, tr3)
    atr_expr = tr.rolling_mean(window_size=atr_period)
    
    # Materialize ATR first
    df = df.with_columns(atr_expr.alias("atr"))
    
    # ATR percentile (using rolling quantile approximation)
    atr_pctl_expr = pl.col("atr").rolling_map(
        lambda s: (s < s[-1]).sum() / len(s) if len(s) > 1 else 0.5,
        window_size=lookback
    ).alias("atr_pctl")
    
    # Materialize ATR percentile
    df = df.with_columns(atr_pctl_expr)
    
    # ADX - use materialized ATR column
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
    minus_dm = pl.when((down_move > up_move) & (down_move > 0)).then(down_move).otherwise(0.0)
    plus_di = 100 * (plus_dm.rolling_mean(window_size=atr_period) / (pl.col("atr") + 1e-9))
    minus_di = 100 * (minus_dm.rolling_mean(window_size=atr_period) / (pl.col("atr") + 1e-9))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx_expr = dx.rolling_mean(window_size=atr_period).alias("adx")
    
    # Materialize ADX
    df = df.with_columns(adx_expr)
    
    # Regime classification
    vol_regime = pl.when(pl.col("atr_pctl") < 0.33).then(pl.lit("low_vol")) \
                   .when(pl.col("atr_pctl") < 0.67).then(pl.lit("mid_vol")) \
                   .otherwise(pl.lit("high_vol")).alias("vol_regime")
    
    trend_regime = pl.when(pl.col("adx") > 25).then(pl.lit("trending")).otherwise(pl.lit("ranging")).alias("trend_regime")
    trend_dir = pl.when(plus_di > minus_di).then(pl.lit("up")).otherwise(pl.lit("down")).alias("trend_dir")
    
    return df.with_columns([vol_regime, trend_regime, trend_dir])


def get_regime_params(regime: str, base_params: dict) -> dict:
    """
    Adjust strategy parameters based on detected regime.
    
    Regime-based adjustments:
    - low_vol: Tighter stops, wider MA (less noise), lower ADX threshold
    - high_vol: Wider stops, faster MA, higher ADX threshold
    - trending: Trend-following params (slower MA, ADX filter on)
    - ranging: Mean-reversion params (faster MA, ADX filter off)
    """
    params = base_params.copy()
    
    if "low_vol" in regime:
        params["adx_threshold"] = params.get("adx_threshold", 25) * 0.7
        params["atr_sl_mult"] = params.get("atr_sl_mult", 2.0) * 0.8
    elif "high_vol" in regime:
        params["adx_threshold"] = params.get("adx_threshold", 25) * 1.3
        params["atr_sl_mult"] = params.get("atr_sl_mult", 2.0) * 1.3
    
    if "trending" in regime:
        params["slow_period"] = int(params.get("slow_period", 80) * 1.2)
    elif "ranging" in regime:
        params["fast_period"] = int(params.get("fast_period", 20) * 0.7)
        params["adx_threshold"] = 0  # Disable ADX filter in ranging
    
    return params