"""
Online Learning Module - Adaptive strategies with regime detection.

Provides:
- RegimeDetector: Classifies market regime from price data
- AdaptiveStrategy: Wraps base strategies with dynamic parameter adaptation
- OnlineLearningEngine: High-level engine for adaptive trading
"""

from trading_agent.online_learning.regime_detector import (
    MarketRegime,
    RegimeFeatures,
    RegimeSignal,
    RegimeDetector,
    create_regime_detector,
)

from trading_agent.online_learning.adaptive_strategy import (
    AdaptiveStrategy,
    AdaptiveSignal,
    OnlineLearningEngine,
    create_adaptive_ma_crossover,
)

__all__ = [
    "MarketRegime",
    "RegimeFeatures",
    "RegimeSignal",
    "RegimeDetector",
    "create_regime_detector",
    "AdaptiveStrategy",
    "AdaptiveSignal",
    "OnlineLearningEngine",
    "create_adaptive_ma_crossover",
]
