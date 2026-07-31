"""
Online Learning Strategy Integration

Integrates adaptive online indicators into the trading strategy pipeline.
Allows strategies to adapt parameters in real-time based on market regime.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from trading.exchanges.models import OrderSide, Position
from trading.ml.online.indicators import (
    OnlineATR, OnlineVWAP,
    OnlineStandardDeviation, OnlineCorrelation,
)
from trading.ml.online.adaptive import (
    AdaptiveConfig, AdaptiveEMA, AdaptiveRSI, 
    AdaptiveBollingerBands, AdaptiveMACD,
)
from trading.strategies.plugins import BaseStrategy, Signal, StrategyContext, StrategyMetadata, StrategyType, RiskProfile

logger = logging.getLogger(__name__)


@dataclass
class OnlineLearningConfig:
    """Configuration for online learning strategy."""
    # Base periods (will be adapted)
    ema_fast_period: int = 12
    ema_slow_period: int = 26
    rsi_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    
    # Adaptive settings
    min_period: int = 5
    max_period: int = 50
    adaptation_rate: float = 0.1
    performance_window: int = 100
    min_samples: int = 50
    
    # Signal thresholds
    rsi_oversold: float = 30
    rsi_overbought: float = 70
    macd_threshold: float = 0.0
    bb_position_threshold: float = 0.8  # Position within bands (0-1)
    
    # Risk
    position_size: float = 0.1
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0


class OnlineLearningStrategy(BaseStrategy):
    """
    Strategy using online learning indicators that adapt to market conditions.
    
    Features:
    - Streaming indicators (no lookback window needed)
    - Adaptive parameter adjustment based on performance
    - Regime detection via indicator correlation
    - Real-time learning from each bar
    """
    
    metadata = StrategyMetadata(
        name="OnlineLearningStrategy",
        version="1.0.0",
        author="Trading System",
        description="Adaptive strategy using online learning indicators",
        strategy_type=StrategyType.ML_BASED,
        risk_profile=RiskProfile.MODERATE,
        asset_classes=["crypto", "stocks", "forex", "futures"],
        timeframes=["1m", "5m", "15m", "1h", "4h", "1d"],
        parameters={
            'ema_fast_period': {'type': 'integer', 'min': 5, 'max': 50, 'default': 12, 'required': True},
            'ema_slow_period': {'type': 'integer', 'min': 10, 'max': 100, 'default': 26, 'required': True},
            'rsi_period': {'type': 'integer', 'min': 7, 'max': 30, 'default': 14, 'required': True},
            'bb_period': {'type': 'integer', 'min': 10, 'max': 50, 'default': 20, 'required': True},
            'bb_std': {'type': 'number', 'min': 1.0, 'max': 4.0, 'default': 2.0, 'required': True},
            'position_size': {'type': 'number', 'min': 0.01, 'max': 1.0, 'default': 0.1, 'required': True},
            'adaptation_enabled': {'type': 'boolean', 'default': True, 'required': False},
        },
    )
    
    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.config_obj = OnlineLearningConfig()
        
        # Apply config overrides
        if config:
            for key, value in config.items():
                if hasattr(self.config_obj, key):
                    setattr(self.config_obj, key, value)
        
        # Initialize adaptive indicators
        adaptive_config = AdaptiveConfig(
            min_period=self.config_obj.min_period,
            max_period=self.config_obj.max_period,
            adaptation_rate=self.config_obj.adaptation_rate,
            performance_window=self.config_obj.performance_window,
            min_samples=self.config_obj.min_samples,
        )
        
        self.ema_fast = AdaptiveEMA(adaptive_config)
        self.ema_slow = AdaptiveEMA(adaptive_config)
        self.rsi = AdaptiveRSI(adaptive_config)
        self.bb = AdaptiveBollingerBands(adaptive_config)
        self.macd = AdaptiveMACD(adaptive_config)
        self.atr = OnlineATR(self.config_obj.atr_period)
        self.vwap = OnlineVWAP()
        self.volatility = OnlineStandardDeviation(20)
        self.trend_correlation = OnlineCorrelation(20)
        
        # State
        self.position: Optional[Position] = None
        self.entry_price: Decimal = Decimal("0")
        self.entry_time: Optional[datetime] = None
        self.current_performance: float = 0.0
        self.trade_history: list[dict] = []
        self.signal_history: list[dict] = []
        
        # Regime tracking
        self.current_regime: str = "unknown"
        self.regime_confidence: float = 0.0
    
    def on_start(self, context: StrategyContext) -> None:
        """Initialize strategy state."""
        logger.info(f"Starting OnlineLearningStrategy for {context.symbol}")
        
        # Warm up indicators with initial data if available
        # In practice, would load historical data here
        
        self.position = context.position
        if self.position and self.position.size > 0:
            self.entry_price = self.position.entry_price
            self.entry_time = context.current_time
    
    def on_bar(self, context: StrategyContext) -> list[Signal]:
        """Process new bar and generate signals."""
        bar = context.bar
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        volume = float(bar.volume)
        
        # Update all indicators
        ema_fast_val = self.ema_fast.update(close, self.current_performance)
        ema_slow_val = self.ema_slow.update(close, self.current_performance)
        rsi_val = self.rsi.update(close, self.current_performance)
        bb_mid, bb_up, bb_low = self.bb.update(close, self.current_performance)
        macd_val, macd_sig, macd_hist = self.macd.update(close, self.current_performance)
        atr_val = self.atr.update(high, low, close)
        vwap_val = self.vwap.update(close, volume)
        vol = self.volatility.update(close)
        trend_corr = self.trend_correlation.update(close, ema_slow_val)
        
        # Detect regime
        self._detect_regime(close, ema_fast_val, ema_slow_val, rsi_val, vol, trend_corr)
        
        # Calculate performance if in position
        self._update_performance(close)
        
        # Generate signal
        signal = self._generate_signal(
            close, high, low,
            ema_fast_val, ema_slow_val,
            rsi_val, bb_mid, bb_up, bb_low,
            macd_val, macd_sig, macd_hist,
            atr_val, vwap_val, vol, trend_corr
        )
        
        # Record signal for analysis
        self.signal_history.append({
            "timestamp": bar.timestamp,
            "close": close,
            "signal": signal,
            "regime": self.current_regime,
            "ema_fast": ema_fast_val,
            "ema_slow": ema_slow_val,
            "rsi": rsi_val,
            "bb_position": (close - bb_low) / (bb_up - bb_low) if bb_up != bb_low else 0.5,
            "macd_hist": macd_hist,
            "atr": atr_val,
        })
        
        # Keep history bounded
        if len(self.signal_history) > 1000:
            self.signal_history = self.signal_history[-1000:]
        
        signals = []
        if signal != 0:
            side = OrderSide.BUY if signal > 0 else OrderSide.SELL
            strength = min(abs(signal), 1.0)
            
            # Calculate stop loss and take profit
            stop_loss = None
            take_profit = None
            if atr_val > 0:
                if signal > 0:  # Long
                    stop_loss = Decimal(str(close - atr_val * self.config_obj.stop_loss_atr_mult))
                    take_profit = Decimal(str(close + atr_val * self.config_obj.take_profit_atr_mult))
                else:  # Short
                    stop_loss = Decimal(str(close + atr_val * self.config_obj.stop_loss_atr_mult))
                    take_profit = Decimal(str(close - atr_val * self.config_obj.take_profit_atr_mult))
            
            signals.append(Signal(
                symbol=context.symbol,
                side=side,
                strength=Decimal(str(strength)),
                price=Decimal(str(close)),
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    "regime": self.current_regime,
                    "regime_confidence": self.regime_confidence,
                    "ema_fast": ema_fast_val,
                    "ema_slow": ema_slow_val,
                    "rsi": rsi_val,
                    "macd_hist": macd_hist,
                    "bb_position": (close - bb_low) / (bb_up - bb_low) if bb_up != bb_low else 0.5,
                    "atr": atr_val,
                },
                strategy_name=self.get_metadata().name,
            ))
        
        return signals
    
    def _detect_regime(
        self, 
        close: float, 
        ema_fast: float, 
        ema_slow: float,
        rsi: float,
        volatility: float,
        trend_corr: float,
    ) -> None:
        """Detect current market regime."""
        # Trend direction
        trend_up = ema_fast > ema_slow
        trend_strength = abs(ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0
        
        # Volatility regime
        high_vol = volatility > self.volatility.value * 1.5 if self.volatility.value > 0 else False
        low_vol = volatility < self.volatility.value * 0.5 if self.volatility.value > 0 else False
        
        # RSI regime
        oversold = rsi < 30
        overbought = rsi > 70
        neutral_rsi = 30 <= rsi <= 70
        
        # Determine regime
        if trend_up and trend_strength > 0.02:
            if high_vol:
                self.current_regime = "trending_up_high_vol"
            elif low_vol:
                self.current_regime = "trending_up_low_vol"
            else:
                self.current_regime = "trending_up"
            self.regime_confidence = min(trend_strength * 10, 1.0)
        elif not trend_up and trend_strength > 0.02:
            if high_vol:
                self.current_regime = "trending_down_high_vol"
            elif low_vol:
                self.current_regime = "trending_down_low_vol"
            else:
                self.current_regime = "trending_down"
            self.regime_confidence = min(trend_strength * 10, 1.0)
        elif oversold:
            self.current_regime = "oversold"
            self.regime_confidence = (30 - rsi) / 30
        elif overbought:
            self.current_regime = "overbought"
            self.regime_confidence = (rsi - 70) / 30
        elif abs(trend_corr) > 0.7:
            self.current_regime = "mean_reverting"
            self.regime_confidence = abs(trend_corr)
        else:
            self.current_regime = "choppy"
            self.regime_confidence = 0.3
    
    def _update_performance(self, close: float) -> None:
        """Update current position performance."""
        if self.position and self.position.size != 0:
            if self.position.size > 0:  # Long
                self.current_performance = float((close - self.entry_price) / self.entry_price)
            else:  # Short
                self.current_performance = float((self.entry_price - close) / self.entry_price)
        else:
            self.current_performance = 0.0
    
    def _generate_signal(
        self,
        close: float, high: float, low: float,
        ema_fast: float, ema_slow: float,
        rsi: float, bb_mid: float, bb_up: float, bb_low: float,
        macd: float, macd_sig: float, macd_hist: float,
        atr: float, vwap: float, volatility: float, trend_corr: float,
    ) -> int:
        """Generate trading signal: 1=buy, -1=sell, 0=hold."""
        
        # Position within Bollinger Bands (0 = lower band, 1 = upper band)
        bb_position = (close - bb_low) / (bb_up - bb_low) if bb_up != bb_low else 0.5
        
        # Trend signals
        trend_up = ema_fast > ema_slow
        trend_down = ema_fast < ema_slow
        trend_strength = abs(ema_fast - ema_slow) / ema_slow if ema_slow > 0 else 0
        
        # Momentum signals
        macd_bullish = macd > macd_sig and macd_hist > 0
        macd_bearish = macd < macd_sig and macd_hist < 0
        
        # Mean reversion signals
        oversold = rsi < self.config_obj.rsi_oversold and bb_position < 0.2
        overbought = rsi > self.config_obj.rsi_overbought and bb_position > 0.8
        
        # VWAP signals
        above_vwap = close > vwap
        below_vwap = close < vwap
        
        # Regime-specific logic
        if self.current_regime.startswith("trending_up"):
            # Trend following in uptrend
            if not self.position or self.position.size <= 0:
                # Look for pullback entries
                if (macd_bullish or (trend_up and rsi < 50 and bb_position < 0.5)) and above_vwap:
                    return 1
            elif self.position.size > 0:
                # Exit on trend reversal or overbought
                if trend_down or (overbought and macd_bearish):
                    return -1
        
        elif self.current_regime.startswith("trending_down"):
            # Trend following in downtrend
            if not self.position or self.position.size >= 0:
                if (macd_bearish or (trend_down and rsi > 50 and bb_position > 0.5)) and below_vwap:
                    return -1
            elif self.position.size < 0:
                if trend_up or (oversold and macd_bullish):
                    return 1
        
        elif self.current_regime in ("oversold", "mean_reverting"):
            # Mean reversion
            if not self.position or self.position.size <= 0:
                if oversold and macd_hist > 0:  # Bullish divergence
                    return 1
            elif self.position.size > 0:
                if bb_position > 0.8 or rsi > 60:
                    return -1
        
        elif self.current_regime in ("overbought",):
            # Mean reversion short
            if not self.position or self.position.size >= 0:
                if overbought and macd_hist < 0:  # Bearish divergence
                    return -1
            elif self.position.size < 0:
                if bb_position < 0.2 or rsi < 40:
                    return 1
        
        else:  # choppy
            # Stay flat or very small positions
            if not self.position:
                return 0
            # Quick scalp exits
            elif self.position.size > 0 and (bb_position > 0.7 or rsi > 65):
                return -1
            elif self.position.size < 0 and (bb_position < 0.3 or rsi < 35):
                return 1
        
        # Risk management exits
        if self.position:
            if self.position.size > 0:
                # Stop loss
                if close <= float(self.entry_price) * (1 - self.config_obj.stop_loss_atr_mult * atr / close):
                    return -1
                # Take profit
                if close >= float(self.entry_price) * (1 + self.config_obj.take_profit_atr_mult * atr / close):
                    return -1
            elif self.position.size < 0:
                # Stop loss
                if close >= float(self.entry_price) * (1 + self.config_obj.stop_loss_atr_mult * atr / close):
                    return 1
                # Take profit
                if close <= float(self.entry_price) * (1 - self.config_obj.take_profit_atr_mult * atr / close):
                    return 1
        
        return 0
    
    def on_fill(self, order, fill_price: Decimal, fill_size: Decimal) -> None:
        """Handle order fill."""
        if fill_size > 0:
            self.position = Position(
                symbol=order.symbol,
                size=fill_size if order.side == OrderSide.BUY else -fill_size,
                entry_price=fill_price,
                mark_price=fill_price,
            )
            self.entry_price = fill_price
            self.entry_time = datetime.utcnow()
            logger.info(f"Position opened: {self.position.size} @ {fill_price}")
        else:
            # Position closed
            pnl = float((fill_price - self.entry_price) * self.position.size) if self.position else 0
            self.trade_history.append({
                "entry_price": float(self.entry_price),
                "exit_price": float(fill_price),
                "size": float(self.position.size) if self.position else 0,
                "pnl": pnl,
                "pnl_pct": pnl / float(self.entry_price) if self.entry_price else 0,
                "entry_time": self.entry_time,
                "exit_time": datetime.utcnow(),
                "regime": self.current_regime,
            })
            self.position = None
            self.entry_price = Decimal("0")
            self.entry_time = None
            logger.info(f"Position closed: PnL = {pnl:.2f}")
    
    def on_stop(self) -> None:
        """Cleanup on strategy stop."""
        logger.info(f"Stopping OnlineLearningStrategy. Trades: {len(self.trade_history)}")
        
        # Log adaptive indicator final states
        logger.info(f"Final EMA Fast period: {self.ema_fast.current_period}")
        logger.info(f"Final EMA Slow period: {self.ema_slow.current_period}")
        logger.info(f"Final RSI period: {self.rsi.current_period}")
        logger.info(f"Final BB period: {self.bb.current_period}")
        logger.info(f"Final MACD period: {self.macd.current_period}")
    
    def get_state(self) -> dict:
        """Get persistent state for serialization."""
        return {
            "position_size": float(self.position.size) if self.position else 0,
            "entry_price": float(self.entry_price),
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "trade_history": self.trade_history[-100:],  # Last 100 trades
            "current_regime": self.current_regime,
            "regime_confidence": self.regime_confidence,
            "ema_fast_period": self.ema_fast.current_period,
            "ema_slow_period": self.ema_slow.current_period,
            "rsi_period": self.rsi.current_period,
            "bb_period": self.bb.current_period,
            "macd_period": self.macd.current_period,
        }
    
    def set_state(self, state: dict) -> None:
        """Restore persistent state."""
        if state.get("position_size", 0) != 0:
            # Position would be restored by execution engine
            pass
        self.trade_history = state.get("trade_history", [])
        self.current_regime = state.get("current_regime", "unknown")
        self.regime_confidence = state.get("regime_confidence", 0.0)
        
        # Restore adaptive periods
        self.ema_fast.current_period = state.get("ema_fast_period", self.config_obj.ema_fast_period)
        self.ema_slow.current_period = state.get("ema_slow_period", self.config_obj.ema_slow_period)
        self.rsi.current_period = state.get("rsi_period", self.config_obj.rsi_period)
        self.bb.current_period = state.get("bb_period", self.config_obj.bb_period)
        self.macd.current_period = state.get("macd_period", self.config_obj.macd_fast)


# Register with plugin registry
from trading.strategies.plugins import get_registry

registry = get_registry()
registry.register(OnlineLearningStrategy)