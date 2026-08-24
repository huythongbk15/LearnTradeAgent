"""
Online Regime Detection for Adaptive Strategy Parameters.

Detects market regime (trending/sideways/volatile) from recent price data
and recommends optimal strategy parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import numpy as np
import polars as pl


class MarketRegime(str, Enum):
    """Market regime classification."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    SIDEWAYS = "sideways"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimeFeatures:
    """Computed features for regime classification."""

    adx: float  # Average Directional Index (trend strength)
    atr_pct: float  # ATR as % of price (volatility)
    sma_slope: float  # SMA slope (trend direction)
    rsi: float  # RSI level
    bb_width: float  # Bollinger Band width (volatility)
    returns_std: float  # Return standard deviation
    hurst_exponent: float  # Hurst exponent (trend persistence)
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class RegimeSignal:
    """Regime detection output."""

    regime: MarketRegime
    confidence: float  # 0-1 confidence
    features: RegimeFeatures
    recommended_params: dict[str, Any]  # Strategy params for this regime
    timestamp: datetime


class RegimeDetector:
    """
    Online regime detector using multiple indicators.

    Computes features from recent window and classifies regime.
    Provides parameter recommendations per regime.
    """

    # Default parameter recommendations per regime
    REGIME_PARAMS = {
        MarketRegime.TRENDING_UP: {
            "ma_crossover": {"fast_period": 10, "slow_period": 30},
            "rsi": {"period": 14, "oversold": 30, "overbought": 70},
            "bbands": {"period": 20, "std_dev": 2.0},
        },
        MarketRegime.TRENDING_DOWN: {
            "ma_crossover": {"fast_period": 10, "slow_period": 30},
            "rsi": {"period": 14, "oversold": 25, "overbought": 65},
            "bbands": {"period": 20, "std_dev": 2.0},
        },
        MarketRegime.SIDEWAYS: {
            "ma_crossover": {
                "fast_period": 5,
                "slow_period": 15,
            },  # Very fast for mean reversion
            "rsi": {"period": 7, "oversold": 35, "overbought": 65},
            "bbands": {"period": 14, "std_dev": 1.5},
        },
        MarketRegime.VOLATILE: {
            "ma_crossover": {"fast_period": 8, "slow_period": 25},
            "rsi": {"period": 14, "oversold": 20, "overbought": 80},
            "bbands": {"period": 20, "std_dev": 2.5},
        },
        MarketRegime.UNKNOWN: {
            "ma_crossover": {"fast_period": 20, "slow_period": 50},
            "rsi": {"period": 14, "oversold": 30, "overbought": 70},
            "bbands": {"period": 20, "std_dev": 2.0},
        },
    }

    def __init__(
        self,
        lookback_bars: int = 100,
        adx_period: int = 14,
        atr_period: int = 14,
        sma_period: int = 20,
        rsi_period: int = 14,
        bb_period: int = 20,
    ):
        self.lookback_bars = lookback_bars
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.sma_period = sma_period
        self.rsi_period = rsi_period
        self.bb_period = bb_period

    def compute_features(self, df: pl.DataFrame) -> RegimeFeatures:
        """Compute regime features from OHLCV data."""
        if len(df) < self.lookback_bars:
            raise ValueError(f"Need at least {self.lookback_bars} bars, got {len(df)}")

        recent = df.tail(self.lookback_bars)
        close = recent["close"].to_numpy()
        high = recent["high"].to_numpy()
        low = recent["low"].to_numpy()

        # ADX - trend strength
        adx = self._compute_adx(high, low, close)

        # ATR % of price
        atr = self._compute_atr(high, low, close)
        atr_pct = atr / close[-1] if close[-1] > 0 else 0.0

        # SMA slope (trend direction)
        sma = self._compute_sma(close, self.sma_period)
        if len(sma) >= 2:
            sma_slope = (sma[-1] - sma[-2]) / sma[-2] if sma[-2] != 0 else 0.0
        else:
            sma_slope = 0.0

        # RSI
        rsi = self._compute_rsi(close, self.rsi_period)

        # Bollinger Band width
        bb_width = self._compute_bb_width(close, self.bb_period)

        # Returns std
        returns = np.diff(close) / close[:-1]
        returns_std = float(np.std(returns)) if len(returns) > 1 else 0.0

        # Hurst exponent (simplified)
        hurst = self._compute_hurst(close)

        return RegimeFeatures(
            adx=adx,
            atr_pct=atr_pct,
            sma_slope=sma_slope,
            rsi=rsi,
            bb_width=bb_width,
            returns_std=returns_std,
            hurst_exponent=hurst,
            timestamp=datetime.now(UTC),
        )

    def _compute_adx(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> float:
        """Compute ADX (Average Directional Index)."""
        if len(high) < self.adx_period + 1:
            return 0.0

        # True Range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )

        # Directional Movement
        up_move = high[1:] - high[:-1]
        down_move = low[:-1] - low[1:]

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # Smoothed averages
        tr_smooth = self._rma(tr, self.adx_period)
        plus_di = 100 * self._rma(plus_dm, self.adx_period) / (tr_smooth + 1e-10)
        minus_di = 100 * self._rma(minus_dm, self.adx_period) / (tr_smooth + 1e-10)

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = self._rma(dx, self.adx_period)

        return float(adx[-1]) if len(adx) > 0 else 0.0

    def _rma(self, arr: np.ndarray, period: int) -> np.ndarray:
        """Rolling moving average (Wilder's smoothing)."""
        if len(arr) < period:
            return np.array([np.mean(arr)])
        result = np.zeros_like(arr)
        result[period - 1] = np.mean(arr[:period])
        alpha = 1.0 / period
        for i in range(period, len(arr)):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
        return result[period - 1 :]

    def _compute_atr(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> float:
        """Compute Average True Range."""
        if len(high) < self.atr_period + 1:
            return 0.0
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr = np.mean(tr[-self.atr_period :])
        return float(atr)

    def _compute_sma(self, close: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average."""
        if len(close) < period:
            return np.array([np.mean(close)])
        return np.convolve(close, np.ones(period) / period, mode="valid")

    def _compute_rsi(self, close: np.ndarray, period: int) -> float:
        """Compute RSI."""
        if len(close) < period + 1:
            return 50.0
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi)

    def _compute_bb_width(self, close: np.ndarray, period: int) -> float:
        """Compute Bollinger Band width as % of middle band."""
        if len(close) < period:
            return 0.0
        recent = close[-period:]
        mid = np.mean(recent)
        std = np.std(recent)
        upper = mid + 2 * std
        lower = mid - 2 * std
        return float((upper - lower) / mid) if mid != 0 else 0.0

    def _compute_hurst(self, close: np.ndarray) -> float:
        """Simplified Hurst exponent estimation."""
        if len(close) < 20:
            return 0.5
        # R/S analysis on log returns
        log_rets = np.diff(np.log(close + 1e-10))
        if len(log_rets) < 10:
            return 0.5

        # Simplified: ratio of range to std dev
        n = len(log_rets)
        mean_ret = np.mean(log_rets)
        cum_dev = np.cumsum(log_rets - mean_ret)
        r = np.max(cum_dev) - np.min(cum_dev)
        s = np.std(log_rets)

        if s == 0:
            return 0.5
        rs = r / s
        # Approximate H from R/S ~ n^H
        h = np.log(rs) / np.log(n) if n > 1 else 0.5
        return float(np.clip(h, 0.0, 1.0))

    def classify_regime(self, features: RegimeFeatures) -> tuple[MarketRegime, float]:
        """
        Classify regime from features.
        Returns (regime, confidence).
        """
        adx = features.adx
        sma_slope = features.sma_slope
        atr_pct = features.atr_pct
        hurst = features.hurst_exponent

        # Thresholds
        ADX_TREND_THRESHOLD = 25.0
        ADX_STRONG_TREND = 40.0
        VOLATILITY_HIGH = 0.03  # 3% daily ATR
        HURST_TRENDING = 0.6
        HURST_MEAN_REVERT = 0.4

        # Volatile regime
        if atr_pct > VOLATILITY_HIGH:
            return MarketRegime.VOLATILE, min(
                0.9, atr_pct / VOLATILITY_HIGH * 0.5 + 0.4
            )

        # Strong trending
        if adx > ADX_STRONG_TREND:
            if sma_slope > 0:
                return MarketRegime.TRENDING_UP, min(0.95, adx / 60.0)
            else:
                return MarketRegime.TRENDING_DOWN, min(0.95, adx / 60.0)

        # Moderate trending
        if adx > ADX_TREND_THRESHOLD:
            if sma_slope > 0.001:
                return MarketRegime.TRENDING_UP, min(0.8, adx / 40.0)
            elif sma_slope < -0.001:
                return MarketRegime.TRENDING_DOWN, min(0.8, adx / 40.0)

        # Hurst-based
        if hurst > HURST_TRENDING:
            return (
                MarketRegime.TRENDING_UP
                if sma_slope >= 0
                else MarketRegime.TRENDING_DOWN,
                0.6,
            )
        elif hurst < HURST_MEAN_REVERT:
            return MarketRegime.SIDEWAYS, 0.6

        # Default: sideways
        return MarketRegime.SIDEWAYS, 0.5

    def get_recommended_params(self, regime: MarketRegime, strategy: str) -> dict:
        """Get recommended parameters for regime and strategy."""
        return self.REGIME_PARAMS.get(
            regime, self.REGIME_PARAMS[MarketRegime.UNKNOWN]
        ).get(strategy, self.REGIME_PARAMS[MarketRegime.UNKNOWN]["ma_crossover"])

    def detect(self, df: pl.DataFrame, strategy: str = "ma_crossover") -> RegimeSignal:
        """Full detection pipeline."""
        features = self.compute_features(df)
        regime, confidence = self.classify_regime(features)
        params = self.get_recommended_params(regime, strategy)

        return RegimeSignal(
            regime=regime,
            confidence=confidence,
            features=features,
            recommended_params=params,
            timestamp=datetime.now(UTC),
        )


def create_regime_detector(
    lookback_bars: int = 100,
    adx_period: int = 14,
    atr_period: int = 14,
    sma_period: int = 20,
    rsi_period: int = 14,
    bb_period: int = 20,
) -> RegimeDetector:
    """Factory function."""
    return RegimeDetector(
        lookback_bars=lookback_bars,
        adx_period=adx_period,
        atr_period=atr_period,
        sma_period=sma_period,
        rsi_period=rsi_period,
        bb_period=bb_period,
    )
