"""Adaptive indicators and strategies using online learning."""

from dataclasses import dataclass
from typing import Optional
import numpy as np

from trading.ml.online.indicators import (
    OnlineEMA, OnlineRSI, OnlineBollingerBands, 
    OnlineMACD, OnlineATR, OnlineIndicator
)


@dataclass
class AdaptiveConfig:
    """Configuration for adaptive behavior."""
    min_period: int = 10
    max_period: int = 50
    adaptation_rate: float = 0.1
    performance_window: int = 100
    min_samples: int = 50


class AdaptiveIndicator:
    """Indicator that adapts its parameters based on market regime."""
    
    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.current_period = config.min_period
        self.base_indicator: Optional[OnlineIndicator] = None
        self.performance_history = []
        self.regime = "unknown"
    
    def set_indicator(self, indicator: OnlineIndicator) -> None:
        self.base_indicator = indicator
    
    def update(self, value: float, performance: float = 0.0) -> float:
        """Update indicator and adapt if needed."""
        if self.base_indicator is None:
            return 0.0
        
        result = self.base_indicator.update(value)
        
        # Track performance
        self.performance_history.append(performance)
        if len(self.performance_history) > self.config.performance_window:
            self.performance_history.pop(0)
        
        # Adapt periodically
        if len(self.performance_history) >= self.config.min_samples:
            self._adapt()
        
        return result
    
    def _adapt(self) -> None:
        """Adapt period based on recent performance."""
        if len(self.performance_history) < self.config.min_samples:
            return
        
        recent_perf = np.mean(self.performance_history[-20:])
        older_perf = np.mean(self.performance_history[-40:-20]) if len(self.performance_history) >= 40 else recent_perf
        
        # If performance degrading, try adjusting period
        if recent_perf < older_perf * 0.95:
            # Try longer period for smoother signals
            if self.current_period < self.config.max_period:
                self.current_period = min(
                    self.current_period + max(1, int(self.current_period * self.config.adaptation_rate)),
                    self.config.max_period
                )
                self._rebuild_indicator()
        elif recent_perf > older_perf * 1.02:
            # Performance improving, try shorter period for responsiveness
            if self.current_period > self.config.min_period:
                self.current_period = max(
                    self.current_period - max(1, int(self.current_period * self.config.adaptation_rate)),
                    self.config.min_period
                )
                self._rebuild_indicator()
    
    def _rebuild_indicator(self) -> None:
        """Rebuild indicator with new period."""
        # Subclasses should override
        pass
    
    @property
    def is_ready(self) -> bool:
        return self.base_indicator.is_ready if self.base_indicator else False
    
    def reset(self) -> None:
        if self.base_indicator:
            self.base_indicator.reset()
        self.performance_history.clear()


class AdaptiveEMA(AdaptiveIndicator):
    """Adaptive EMA that adjusts period based on volatility."""
    
    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=5, max_period=30)
        super().__init__(config)
        self.ema = OnlineEMA(config.min_period)
        self.set_indicator(self.ema)
        self.volatility = OnlineStandardDeviation(20)
    
    def update(self, value: float, performance: float = 0.0) -> float:
        vol = self.volatility.update(value)
        result = self.ema.update(value)
        
        # Adjust period based on volatility
        if vol > 0 and self.ema.is_ready:
            # High vol -> longer period, low vol -> shorter period
            target_period = int(np.clip(20 / (1 + vol * 10), self.config.min_period, self.config.max_period))
            if abs(target_period - self.current_period) > 2:
                self.current_period = target_period
                self.ema = OnlineEMA(self.current_period)
                # Warm up with recent values
                # (In practice, would need to store recent values)
                self.set_indicator(self.ema)
        
        return super().update(value, performance) or result
    
    def _rebuild_indicator(self) -> None:
        self.ema = OnlineEMA(self.current_period)
        self.set_indicator(self.ema)


class AdaptiveRSI(AdaptiveIndicator):
    """Adaptive RSI that adjusts period based on market regime."""
    
    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=7, max_period=21)
        super().__init__(config)
        self.rsi = OnlineRSI(config.min_period)
        self.set_indicator(self.rsi)
        self.trend_strength = OnlineCorrelation(20)
    
    def update(self, value: float, performance: float = 0.0) -> float:
        result = self.rsi.update(value)
        
        # Track trend strength using autocorrelation
        if self.rsi.prev_close is not None:
            self.trend_strength.update(value, self.rsi.prev_close)
        
        return super().update(value, performance) or result
    
    def _rebuild_indicator(self) -> None:
        self.rsi = OnlineRSI(self.current_period)
        self.set_indicator(self.rsi)


class AdaptiveBollingerBands(AdaptiveIndicator):
    """Adaptive Bollinger Bands with dynamic std multiplier."""
    
    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=10, max_period=30)
        super().__init__(config)
        self.bb = OnlineBollingerBands(config.min_period, 2.0)
        self.set_indicator(self.bb)
        self.volatility = OnlineStandardDeviation(20)
        self.current_std_mult = 2.0
    
    def update(self, value: float, performance: float = 0.0) -> tuple[float, float, float]:
        vol = self.volatility.update(value)
        middle, upper, lower = self.bb.update(value)
        
        # Adjust std multiplier based on volatility regime
        if vol > 0:
            # High vol -> wider bands (higher std mult)
            # Low vol -> tighter bands
            self.current_std_mult = np.clip(1.5 + vol * 5, 1.5, 3.0)
            self.bb.num_std = self.current_std_mult
        
        return super().update(value, performance) or (middle, upper, lower)
    
    def _rebuild_indicator(self) -> None:
        self.bb = OnlineBollingerBands(self.current_period, self.current_std_mult)
        self.set_indicator(self.bb)


class AdaptiveMACD(AdaptiveIndicator):
    """Adaptive MACD with dynamic fast/slow periods."""
    
    def __init__(self, config: Optional[AdaptiveConfig] = None):
        config = config or AdaptiveConfig(min_period=8, max_period=30)
        super().__init__(config)
        self.macd = OnlineMACD(12, 26, 9)
        self.set_indicator(self.macd)
    
    def update(self, value: float, performance: float = 0.0) -> tuple[float, float, float]:
        result = self.macd.update(value)
        return super().update(value, performance) or result
    
    def _rebuild_indicator(self) -> None:
        # Scale both fast and slow proportionally
        ratio = 26 / 12  # ~2.17
        fast = max(5, int(self.current_period / ratio))
        slow = self.current_period
        self.macd = OnlineMACD(fast, slow, 9)
        self.set_indicator(self.macd)


class AdaptiveStrategy:
    """Strategy that adapts its parameters online."""
    
    def __init__(self, config: AdaptiveConfig):
        self.config = config
        self.ema = AdaptiveEMA(config)
        self.rsi = AdaptiveRSI(config)
        self.bb = AdaptiveBollingerBands(config)
        self.macd = AdaptiveMACD(config)
        self.atr = OnlineATR(14)
        
        self.position = 0
        self.entry_price = 0.0
        self.performance = 0.0
        self.trades = []
    
    def update(self, high: float, low: float, close: float, volume: float) -> dict:
        """Update all indicators and generate signal."""
        # Update indicators
        ema_val = self.ema.update(close)
        rsi_val = self.rsi.update(close)
        bb_mid, bb_up, bb_low = self.bb.update(close)
        macd_val, macd_sig, macd_hist = self.macd.update(close)
        atr_val = self.atr.update(high, low, close)
        
        # Generate signal
        signal = self._generate_signal(
            close, ema_val, rsi_val, bb_mid, bb_up, bb_low,
            macd_val, macd_sig, macd_hist, atr_val
        )
        
        # Update performance if in position
        if self.position != 0:
            pnl = (close - self.entry_price) * self.position
            self.performance = pnl
        
        # Feed performance to adaptive indicators
        self.ema.update(close, self.performance)
        self.rsi.update(close, self.performance)
        self.bb.update(close, self.performance)
        self.macd.update(close, self.performance)
        
        return {
            "signal": signal,
            "ema": ema_val,
            "rsi": rsi_val,
            "bb_middle": bb_mid,
            "bb_upper": bb_up,
            "bb_lower": bb_low,
            "macd": macd_val,
            "macd_signal": macd_sig,
            "macd_hist": macd_hist,
            "atr": atr_val,
            "position": self.position,
            "performance": self.performance,
        }
    
    def _generate_signal(
        self, close: float, ema: float, rsi: float,
        bb_mid: float, bb_up: float, bb_low: float,
        macd: float, macd_sig: float, macd_hist: float,
        atr: float
    ) -> int:
        """Generate trading signal: 1=buy, -1=sell, 0=hold."""
        # Trend filter
        trend_up = close > ema
        trend_down = close < ema
        
        # Mean reversion signals
        oversold = rsi < 30 and close < bb_low
        overbought = rsi > 70 and close > bb_up
        
        # Momentum signals
        macd_bullish = macd > macd_sig and macd_hist > 0
        macd_bearish = macd < macd_sig and macd_hist < 0
        
        # Combine signals
        buy_signal = trend_up and (oversold or macd_bullish)
        sell_signal = trend_down and (overbought or macd_bearish)
        
        if self.position == 0:
            if buy_signal:
                self.position = 1
                self.entry_price = close
                return 1
            elif sell_signal:
                self.position = -1
                self.entry_price = close
                return -1
        elif self.position > 0:
            # Exit long
            if sell_signal or (close - self.entry_price) > 2 * atr:
                self.position = 0
                self.trades.append(close - self.entry_price)
                return -1
        elif self.position < 0:
            # Exit short
            if buy_signal or (self.entry_price - close) > 2 * atr:
                self.position = 0
                self.trades.append(self.entry_price - close)
                return 1
        
        return 0
    
    def reset(self) -> None:
        self.ema.reset()
        self.rsi.reset()
        self.bb.reset()
        self.macd.reset()
        self.atr.reset()
        self.position = 0
        self.entry_price = 0.0
        self.performance = 0.0
        self.trades.clear()


# Import needed classes
from trading.ml.online.indicators import OnlineStandardDeviation, OnlineCorrelation
