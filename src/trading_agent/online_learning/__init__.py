"""
Online Learning Module - Adaptive strategies with regime detection.

Provides:
- RegimeDetector: Classifies market regime from price data
- AdaptiveStrategy: Wraps base strategies with dynamic parameter adaptation
- RegimeSwitchStrategy: Switches ENTIRE strategy based on regime
- MultiRegimeStrategy: Blends multiple strategies weighted by regime probability
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

from trading_agent.online_learning.regime_switch import (
    RegimeSwitchStrategy,
    MultiRegimeStrategy,
    RegimeSwitchSignal,
    create_regime_switch_strategy,
    create_multi_regime_strategy,
    REGIME_STRATEGY_MAP,
    REGIME_STRATEGY_PARAMS,
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
    "RegimeSwitchStrategy",
    "MultiRegimeStrategy",
    "RegimeSwitchSignal",
    "create_regime_switch_strategy",
    "create_multi_regime_strategy",
    "REGIME_STRATEGY_MAP",
    "REGIME_STRATEGY_PARAMS",
]
