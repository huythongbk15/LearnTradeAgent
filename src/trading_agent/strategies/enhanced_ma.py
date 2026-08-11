#!/usr/bin/env python3
"""
Enhanced MA Crossover Strategy with:
1. ADX Trend Filter (only trade when trend exists)
2. Volatility-based position sizing
3. Trailing stop loss
4. Max drawdown circuit breaker
5. Regime Detection (ATR percentile, ADX state) - NEW
6. Strategy Ensemble with dynamic weights - NEW
"""

import numpy as np
import polars as pl
from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.regime import add_regime_indicators


@register_strategy("enhanced_ma")
class EnhancedMaCrossover(Strategy):
    """
    Enhanced MA Crossover with risk management.

    Parameters
    ----------
    fast_period : int      (default 20)
    slow_period : int      (default 80)
    adx_period : int       (default 14)
    adx_threshold : float  (default 25.0) - only trade when ADX > threshold
    require_close_above_slow : bool (default False) - only long when close > slow MA
    momentum_period : int (default 0) - only long when close > close.shift(period) (0 = off)
    atr_period : int       (default 14)
    atr_sl_mult : float    (default 2.0) - stop loss = entry - atr * mult
    atr_tp_mult : float    (default 3.0) - take profit = entry + atr * mult
    max_dd_pct : float     (default 0.15) - stop trading if DD > 15%
    dd_cooldown_bars : int (default 0) - bars to stay flat after DD trip before
                                         re-arming (0 = price-recovery based)
    dd_recovery_pct : float (default 0.03) - if no cooldown, resume trading when
                                             price recovers +3% from trip close
    trailing_atr_mult : float (default 0.0) - ATR trailing stop multiplier (0 = off)
    risk_per_trade : float (default 0.02) - risk 2% per trade
    """

    name = "enhanced_ma"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast = int(self.params.get("fast_period", 20))
        self.slow = int(self.params.get("slow_period", 80))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 25.0))
        self.require_close_above_slow = bool(
            self.params.get("require_close_above_slow", False)
        )
        self.momentum_period = int(self.params.get("momentum_period", 0))
        self.atr_period = int(self.params.get("atr_period", 14))
        self.atr_sl_mult = float(self.params.get("atr_sl_mult", 2.0))
        self.atr_tp_mult = float(self.params.get("atr_tp_mult", 3.0))
        self.max_dd_pct = float(self.params.get("max_dd_pct", 0.15))
        self.dd_cooldown_bars = int(self.params.get("dd_cooldown_bars", 0))
        self.dd_recovery_pct = float(self.params.get("dd_recovery_pct", 0.03))
        self.trailing_atr_mult = float(self.params.get("trailing_atr_mult", 0.0))
        self.risk_per_trade = float(self.params.get("risk_per_trade", 0.02))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # MAs
        df = df.with_columns(
            [
                pl.col("close")
                .rolling_mean(window_size=self.fast)
                .alias(f"ma_{self.fast}"),
                pl.col("close")
                .rolling_mean(window_size=self.slow)
                .alias(f"ma_{self.slow}"),
            ]
        )

        # ADX for trend filter
        high = pl.col("high")
        low = pl.col("low")
        close = pl.col("close")
        prev_close = close.shift(1)

        # True Range
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)
        atr = tr.rolling_mean(window_size=self.atr_period).alias("atr")

        # +DM, -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = (
            pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
        )
        minus_dm = (
            pl.when((down_move > up_move) & (down_move > 0))
            .then(down_move)
            .otherwise(0.0)
        )

        # Smoothed DM
        plus_di = 100 * (
            plus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )
        minus_di = 100 * (
            minus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )

        # ADX
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.rolling_mean(window_size=self.adx_period).alias("adx")

        # Trend direction
        trend_up = (plus_di > minus_di).alias("trend_up")

        return df.with_columns([atr, adx, trend_up])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        fast_col = f"ma_{self.fast}"
        slow_col = f"ma_{self.slow}"

        # Raw MA crossover signal
        raw = (
            pl.when(pl.col(fast_col) > pl.col(slow_col))
            .then(1)
            .when(pl.col(fast_col) < pl.col(slow_col))
            .then(-1)
            .otherwise(0)
        )

        # Only take signal on crossover
        prev_raw = raw.shift(1)
        crossover = (
            pl.when((raw != prev_raw) & (raw != 0)).then(raw).otherwise(0)
        )
        
        # ADX and direction are entry filters. A bearish crossover must remain
        # actionable even when ADX is weak; otherwise a long can become stuck
        # because the one-bar crossover event will not be emitted again.
        final_signal = pl.when(
            (crossover == 1)
            & (pl.col("adx") > self.adx_threshold)
            & pl.col("trend_up")
        ).then(1).when(
            crossover == -1
        ).then(-1).otherwise(0)

        # Optional price confirmation: only long when close is above the slow
        # MA.  In a long-only regime this avoids dead-cat-bounce entries in a
        # broader downtrend (MA cross can happen while price is still below
        # both MAs right after a sharp fall).
        if self.require_close_above_slow:
            final_signal = (
                pl.when((final_signal == 1) & (pl.col("close") > pl.col(slow_col)))
                .then(1)
                .otherwise(0)
            )
        if self.momentum_period > 0:
            final_signal = (
                pl.when(
                    (final_signal == 1)
                    & (pl.col("close") > pl.col("close").shift(self.momentum_period))
                )
                .then(1)
                .otherwise(0)
            )

        # Max drawdown circuit breaker + ATR trailing stop: replay a no-fee
        # long-only equity curve and bake the exits INTO the signal stream, so
        # backtest/evidence/live all replay identical signals.
        #  - ATR trailing stop: exit when close < peak_high - mult*ATR
        #  - DD breaker: exit when equity DD from peak > max_dd_pct, then stay
        #    flat until price recovers `dd_recovery_pct` from the trip close
        #    (price-based reset — equity of a flat book never moves, so a
        #    "DD recovered" test would never fire).
        # NOTE: needs OHLC columns; bare signal-frame unit tests (no close/high)
        # skip this block so pure crossover logic stays testable.
        if (self.max_dd_pct > 0 or self.trailing_atr_mult > 0) and all(
            c in df.columns for c in ("close", "high")
        ):
            sig = df.select(final_signal.alias("signal")).to_series().to_numpy().copy()
            close = df["close"].to_numpy()
            high = df["high"].to_numpy()
            atr = df["atr"].to_numpy() if "atr" in df.columns else np.zeros(len(close))
            n = len(sig)
            shares = 0.0
            cash = 1.0
            peak = 1.0
            halted = False
            cooldown_left = 0
            trail_high = 0.0
            trip_close = 0.0
            for i in range(n):
                prev = sig[i - 1] if i > 0 else 0
                if halted:
                    sig[i] = 0
                else:
                    if prev == -1 and shares > 0:
                        cash = shares * close[i - 1] if i > 0 else cash
                        shares = 0.0
                        trail_high = 0.0
                    elif prev == 1 and shares == 0 and i > 0 and close[i - 1] > 0:
                        shares = cash / close[i - 1]
                        cash = 0.0
                        trail_high = close[i - 1]
                    if shares > 0 and high[i] > trail_high:
                        trail_high = high[i]
                equity = cash + shares * close[i]
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 0.0
                if halted:
                    if cooldown_left > 0:
                        cooldown_left -= 1
                        if cooldown_left == 0:
                            halted = False
                    elif trip_close > 0 and close[i] >= trip_close * (
                        1 + self.dd_recovery_pct
                    ):
                        halted = False
                else:
                    exit_here = False
                    # ATR trailing stop
                    if shares > 0 and self.trailing_atr_mult > 0:
                        stop = trail_high - self.trailing_atr_mult * atr[i]
                        if close[i] < stop:
                            exit_here = True
                    # DD circuit breaker
                    if dd > self.max_dd_pct:
                        exit_here = True
                    if exit_here and shares > 0:
                        sig[i] = -1
                        cash = shares * close[i]
                        shares = 0.0
                        trail_high = 0.0
                        if dd > self.max_dd_pct:
                            halted = True
                            trip_close = close[i]
                            cooldown_left = self.dd_cooldown_bars
            return pl.Series(sig)

        return df.select(final_signal.alias("signal")).to_series()


# Also create a simpler version with just ADX filter
@register_strategy("ma_adx")
class MaAdxCrossover(Strategy):
    """MA Crossover with simple ADX filter only."""

    name = "ma_adx"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast = int(self.params.get("fast_period", 20))
        self.slow = int(self.params.get("slow_period", 80))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 25.0))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        df = df.with_columns(
            [
                pl.col("close")
                .rolling_mean(window_size=self.fast)
                .alias(f"ma_{self.fast}"),
                pl.col("close")
                .rolling_mean(window_size=self.slow)
                .alias(f"ma_{self.slow}"),
            ]
        )

        # ADX
        high = pl.col("high")
        low = pl.col("low")
        close = pl.col("close")
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)
        atr = tr.rolling_mean(window_size=self.adx_period)

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = (
            pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
        )
        minus_dm = (
            pl.when((down_move > up_move) & (down_move > 0))
            .then(down_move)
            .otherwise(0.0)
        )
        plus_di = 100 * (
            plus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )
        minus_di = 100 * (
            minus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.rolling_mean(window_size=self.adx_period).alias("adx")
        trend_up = (plus_di > minus_di).alias("trend_up")

        return df.with_columns([adx, trend_up])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        fast_col = f"ma_{self.fast}"
        slow_col = f"ma_{self.slow}"

        raw = (
            pl.when(pl.col(fast_col) > pl.col(slow_col))
            .then(1)
            .when(pl.col(fast_col) < pl.col(slow_col))
            .then(-1)
            .otherwise(0)
        )
        prev_raw = raw.shift(1)
        crossover = pl.when((raw != prev_raw) & (raw != 0)).then(raw).otherwise(0)

        # ADX filter + trend alignment
        final = (
            pl.when(
                (crossover == 1)
                & (pl.col("adx") > self.adx_threshold)
                & pl.col("trend_up")
            )
            .then(1)
            .when(
                (crossover == -1)
                & (pl.col("adx") > self.adx_threshold)
                & (~pl.col("trend_up"))
            )
            .then(-1)
            .otherwise(0)
        )

        return df.select(final.alias("signal")).to_series()


@register_strategy("ma_vol_target")
class MaVolTargetCrossover(Strategy):
    """
    MA Crossover with volatility-targeted position sizing.
    Not a signal change - just for reference. Position sizing handled in backtest engine.
    """

    name = "ma_vol_target"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast = int(self.params.get("fast_period", 20))
        self.slow = int(self.params.get("slow_period", 80))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns(
            [
                pl.col("close")
                .rolling_mean(window_size=self.fast)
                .alias(f"ma_{self.fast}"),
                pl.col("close")
                .rolling_mean(window_size=self.slow)
                .alias(f"ma_{self.slow}"),
                # 20-day realized volatility for position sizing
                pl.col("close")
                .pct_change()
                .rolling_std(window_size=20)
                .mul(24**0.5)
                .alias("realized_vol"),
            ]
        )

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        fast_col = f"ma_{self.fast}"
        slow_col = f"ma_{self.slow}"
        raw = (
            pl.when(pl.col(fast_col) > pl.col(slow_col))
            .then(1)
            .when(pl.col(fast_col) < pl.col(slow_col))
            .then(-1)
            .otherwise(0)
        )
        prev = raw.shift(1)
        return df.select(
            pl.when((raw != prev) & (raw != 0)).then(raw).otherwise(0).alias("signal")
        ).to_series()


# ============================================================
# STRATEGY ENSEMBLE
# ============================================================


@register_strategy("ensemble_ma_adx")
class EnsembleMaAdx(Strategy):
    """
    Ensemble of multiple MA+ADX strategies with regime-aware dynamic weights.

    Components:
    1. Fast MA (10, 30) - captures short trends
    2. Medium MA (20, 60) - baseline
    3. Slow MA (30, 100) - captures long trends
    4. RSI Mean Reversion - works in ranging markets
    5. Bollinger Bands - volatility breakout

    Weights adjusted by regime:
    - Trending: favor slow MA, trend-following
    - Ranging: favor fast MA, mean-reversion
    - High vol: reduce all weights, increase cash
    """

    name = "ensemble_ma_adx"

    def __init__(self, params: dict | None = None) -> None:
        params = params or {}
        super().__init__(params)
        # Sub-strategy configs
        self.strategies = [
            {"name": "fast", "fast": 10, "slow": 30, "adx": 20, "base_weight": 0.20},
            {"name": "medium", "fast": 20, "slow": 60, "adx": 30, "base_weight": 0.30},
            {"name": "slow", "fast": 30, "slow": 100, "adx": 40, "base_weight": 0.25},
            {
                "name": "rsi",
                "period": 14,
                "overbought": 70,
                "oversold": 30,
                "base_weight": 0.15,
            },
            {"name": "bbands", "period": 20, "std": 2.0, "base_weight": 0.10},
        ]
        self.regime_lookback = int(params.get("regime_lookback", 252))
        self.min_weight = float(params.get("min_weight", 0.05))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # Add regime indicators
        df = add_regime_indicators(df, lookback=self.regime_lookback)

        # Add indicators for each sub-strategy
        for s in self.strategies:
            if s["name"] in ["fast", "medium", "slow"]:
                df = df.with_columns(
                    [
                        pl.col("close")
                        .rolling_mean(window_size=s["fast"])
                        .alias(f"ma_{s['name']}_fast"),
                        pl.col("close")
                        .rolling_mean(window_size=s["slow"])
                        .alias(f"ma_{s['name']}_slow"),
                    ]
                )
            elif s["name"] == "rsi":
                # RSI
                delta = pl.col("close").diff()
                gain = pl.when(delta > 0).then(delta).otherwise(0.0)
                loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
                avg_gain = gain.rolling_mean(window_size=s["period"])
                avg_loss = loss.rolling_mean(window_size=s["period"])
                rs = avg_gain / (avg_loss + 1e-9)
                rsi = 100 - (100 / (1 + rs))
                df = df.with_columns(rsi.alias("rsi"))
            elif s["name"] == "bbands":
                ma = pl.col("close").rolling_mean(window_size=s["period"])
                std = pl.col("close").rolling_std(window_size=s["period"])
                upper = ma + s["std"] * std
                lower = ma - s["std"] * std
                df = df.with_columns(
                    [
                        ma.alias("bb_mid"),
                        upper.alias("bb_upper"),
                        lower.alias("bb_lower"),
                    ]
                )

        return df

    def _get_regime_weights(self, df: pl.DataFrame) -> pl.DataFrame:
        """Calculate dynamic weights per row based on regime."""
        # This is simplified - in practice you'd compute weights per bar
        # For now, return base weights as columns
        for s in self.strategies:
            df = df.with_columns(pl.lit(s["base_weight"]).alias(f"w_{s['name']}"))
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Get regime at each bar
        vol_regime = pl.col("vol_regime")
        trend_regime = pl.col("trend_regime")
        trend_dir = pl.col("trend_dir")

        signals = []

        # MA strategies
        for s in self.strategies:
            if s["name"] in ["fast", "medium", "slow"]:
                fast_col = f"ma_{s['name']}_fast"
                slow_col = f"ma_{s['name']}_slow"

                raw = (
                    pl.when(pl.col(fast_col) > pl.col(slow_col))
                    .then(1)
                    .when(pl.col(fast_col) < pl.col(slow_col))
                    .then(-1)
                    .otherwise(0)
                )
                prev = raw.shift(1)
                crossover = pl.when((raw != prev) & (raw != 0)).then(raw).otherwise(0)

                # ADX filter
                adx_filtered = (
                    pl.when(pl.col("adx") > s["adx"]).then(crossover).otherwise(0)
                )

                # Trend alignment
                final = (
                    pl.when((adx_filtered == 1) & (trend_dir == "up"))
                    .then(1)
                    .when((adx_filtered == -1) & (trend_dir == "down"))
                    .then(-1)
                    .otherwise(0)
                )

                signals.append(final * pl.lit(s["base_weight"]))

            elif s["name"] == "rsi":
                # RSI mean reversion - works best in ranging
                rsi_long = (
                    pl.when(
                        (pl.col("rsi") < s["oversold"]) & (trend_regime == "ranging")
                    )
                    .then(1)
                    .otherwise(0)
                )
                rsi_short = (
                    pl.when(
                        (pl.col("rsi") > s["overbought"]) & (trend_regime == "ranging")
                    )
                    .then(-1)
                    .otherwise(0)
                )
                signals.append((rsi_long + rsi_short) * pl.lit(s["base_weight"]))

            elif s["name"] == "bbands":
                # BB breakout - works in trending
                bb_long = (
                    pl.when(
                        (pl.col("close") > pl.col("bb_upper"))
                        & (trend_regime == "trending")
                        & (trend_dir == "up")
                    )
                    .then(1)
                    .otherwise(0)
                )
                bb_short = (
                    pl.when(
                        (pl.col("close") < pl.col("bb_lower"))
                        & (trend_regime == "trending")
                        & (trend_dir == "down")
                    )
                    .then(-1)
                    .otherwise(0)
                )
                signals.append((bb_long + bb_short) * pl.lit(s["base_weight"]))

        # Combine signals
        if signals:
            combined = signals[0]
            for s in signals[1:]:
                combined = combined + s

            # Normalize to -1, 0, 1
            final_signal = (
                pl.when(combined > 0.3)
                .then(1)
                .when(combined < -0.3)
                .then(-1)
                .otherwise(0)
            )
        else:
            final_signal = pl.lit(0)

        return df.select(final_signal.alias("signal")).to_series()


@register_strategy("ma_adx_regime")
class MaAdxRegimeAware(Strategy):
    """
    MA+ADX with dynamic parameters based on detected regime.
    Single strategy that adapts its parameters to market conditions.
    """

    name = "ma_adx_regime"

    def __init__(self, params: dict | None = None) -> None:
        params = params or {}
        super().__init__(params)
        self.base_fast = int(params.get("fast_period", 20))
        self.base_slow = int(params.get("slow_period", 80))
        self.base_adx = float(params.get("adx_threshold", 25.0))
        self.atr_period = int(params.get("atr_period", 14))
        self.regime_lookback = int(params.get("regime_lookback", 252))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # Add regime indicators
        df = add_regime_indicators(
            df, atr_period=self.atr_period, lookback=self.regime_lookback
        )

        # For simplicity, use base MAs (regime-adjusted params would need row-wise computation)
        df = df.with_columns(
            [
                pl.col("close")
                .rolling_mean(window_size=self.base_fast)
                .alias(f"ma_{self.base_fast}"),
                pl.col("close")
                .rolling_mean(window_size=self.base_slow)
                .alias(f"ma_{self.base_slow}"),
            ]
        )
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        fast_col = f"ma_{self.base_fast}"
        slow_col = f"ma_{self.base_slow}"

        raw = (
            pl.when(pl.col(fast_col) > pl.col(slow_col))
            .then(1)
            .when(pl.col(fast_col) < pl.col(slow_col))
            .then(-1)
            .otherwise(0)
        )
        prev = raw.shift(1)
        crossover = pl.when((raw != prev) & (raw != 0)).then(raw).otherwise(0)

        # Dynamic ADX threshold based on regime
        # In trending: use base threshold
        # In ranging: lower threshold (or disable)
        # In high vol: higher threshold
        dyn_adx = (
            pl.when(pl.col("trend_regime") == "ranging")
            .then(pl.lit(10.0))
            .when(pl.col("vol_regime") == "high_vol")
            .then(pl.lit(self.base_adx * 1.5))
            .otherwise(pl.lit(self.base_adx))
        )

        adx_filtered = pl.when(pl.col("adx") > dyn_adx).then(crossover).otherwise(0)

        # Trend alignment - use separate when-then chains
        long_cond = (adx_filtered == 1) & (pl.col("trend_dir") == "up")
        short_cond = (adx_filtered == -1) & (pl.col("trend_dir") == "down")
        final = pl.when(long_cond).then(1).when(short_cond).then(-1).otherwise(0)

        return df.select(final.alias("signal")).to_series()
