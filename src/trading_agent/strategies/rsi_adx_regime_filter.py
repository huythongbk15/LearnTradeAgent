"""
RSI+ADX Regime Filter Strategy — combines mean-reversion and trend-following
based on ADX regime detection.

Logic:
  - ADX < adx_threshold (default 20)  → RANGE regime → use RSI signal
  - ADX >= adx_threshold              → TREND regime → use MA crossover signal

This addresses regime dependence: RSI loses money in trending markets,
ma_adx has low trade density. Combining them via ADX filter gives
both signal density and regime robustness.

Parameters:
    rsi_period (14), rsi_oversold (30), rsi_overbought (70),
    ma_fast (20), ma_slow (80), adx_period (14), adx_threshold (20)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy


@register_strategy("rsi_adx_regime_filter")
class RsiAdxRegimeFilterStrategy(Strategy):
    """
    RSI + ADX Regime Filter — combines RSI (range) and MA crossover (trend).

    Uses ADX to determine regime:
      - ADX < threshold → RANGE → RSI mean-reversion signal
      - ADX >= threshold → TREND → MA crossover signal

    Parameters
    ----------
    rsi_period : int      (default 14)
    rsi_oversold : int    (default 30)
    rsi_overbought : int  (default 70)
    ma_fast : int         (default 20)
    ma_slow : int         (default 80)
    adx_period : int      (default 14)
    adx_threshold : int   (default 20)
    ma_type : str         (default "sma") — "sma" or "ema"
    rsi_maintain : float  (default 7.0) — EMA alpha for smoothed RSI
    """

    name = "rsi_adx_regime_filter"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.rsi_period = int(self.params.get("rsi_period", 14))
        self.rsi_oversold = int(self.params.get("rsi_oversold", 30))
        self.rsi_overbought = int(self.params.get("rsi_overbought", 70))
        self.ma_fast = int(self.params.get("ma_fast", 20))
        self.ma_slow = int(self.params.get("ma_slow", 80))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = int(self.params.get("adx_threshold", 20))
        self.ma_type = self.params.get("ma_type", "sma")

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # --- RSI computation ---
        delta = pl.col("close").diff()
        gain = pl.when(delta > 0).then(delta).otherwise(0.0)
        loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
        avg_gain = gain.rolling_mean(window_size=self.rsi_period)
        avg_loss = loss.rolling_mean(window_size=self.rsi_period)
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = (100 - 100 / (1 + rs)).alias("rsi")

        # --- MA computation ---
        if self.ma_type == "ema":
            ma_fast = pl.col("close").ewm_mean(alpha=2.0 / (self.ma_fast + 1)).alias("_ma_fast")
            ma_slow = pl.col("close").ewm_mean(alpha=2.0 / (self.ma_slow + 1)).alias("_ma_slow")
        else:
            ma_fast = pl.col("close").rolling_mean(window_size=self.ma_fast).alias("_ma_fast")
            ma_slow = pl.col("close").rolling_mean(window_size=self.ma_slow).alias("_ma_slow")

        # --- ADX computation ---
        # True range, +DM, -DM, DI+, DI-, DX, ADX
        high = pl.col("high")
        low = pl.col("low")
        close = pl.col("close")

        # Daily range
        tr1 = (high - low).alias("_tr1")
        tr2 = (high - close.shift(1)).abs().alias("_tr2")
        tr3 = (low - close.shift(1)).abs().alias("_tr3")
        tr = pl.max_horizontal(tr1, tr2, tr3).alias("_tr")

        # Directional movement
        up_move = (high - high.shift(1)).alias("_up_move")
        down_move = (low.shift(1) - low).alias("_down_move")

        plus_dm = pl.when(up_move > down_move).then(up_move).otherwise(0.0).alias("_plus_dm")
        minus_dm = pl.when(down_move > up_move).then(down_move).otherwise(0.0).alias("_minus_dm")

        # Smooth with Wilder's (EMA-like) — use rolling mean as approximation
        plus_di = (plus_dm / tr * 100).rolling_mean(window_size=self.adx_period).alias("_plus_di")
        minus_di = (minus_dm / tr * 100).rolling_mean(window_size=self.adx_period).alias("_minus_di")

        # DX and ADX
        dx = ((plus_di - minus_di).abs() /
              (plus_di + minus_di + 1e-9) * 100).alias("_dx")
        adx = dx.rolling_mean(window_size=self.adx_period).alias("_adx")

        return df.with_columns(
            rsi,
            ma_fast.alias("_ma_fast"),
            ma_slow.alias("_ma_slow"),
            tr,
            plus_dm,
            minus_dm,
            plus_di,
            minus_di,
            dx,
            adx,
        )

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """
        Generate regime-filtered signals.
        - Range regime (ADX < threshold): RSI signal (buy oversold, sell overbought)
        - Trend regime (ADX >= threshold): MA crossover signal (buy fast>slow)
        """
        # Determine regime per bar
        is_range = pl.col("_adx") < self.adx_threshold

        # RSI signal: buy when oversold, sell when overbought
        rsi_signal = (
            pl.when(pl.col("rsi") < self.rsi_oversold).then(1)
            .when(pl.col("rsi") > self.rsi_overbought).then(-1)
            .otherwise(0)
        )

        # MA crossover signal: buy when fast > slow, sell when fast < slow (on crossover)
        ma_signal = (
            pl.when(pl.col("_ma_fast") > pl.col("_ma_slow")).then(1)
            .when(pl.col("_ma_fast") < pl.col("_ma_slow")).then(-1)
            .otherwise(0)
        )

        # Only generate signal on crossover events (not continuous)
        # For RSI regime: signal is the oversold/overbought condition itself
        # For MA regime: only on crossover (change in direction to avoid stale signals)
        ma_cross_signal = (
            pl.when((ma_signal != ma_signal.shift(1)) & (ma_signal != 0))
            .then(ma_signal)
            .otherwise(0)
        )

        # Regime-switched final signal: RSI for range regime, MA crossover for trend regime
        final_signal = (
            pl.when(is_range).then(rsi_signal)
            .otherwise(ma_cross_signal)
        ).alias("signal")

        df_result = df.with_columns(final_signal)
        return df_result.select("signal").to_series()
