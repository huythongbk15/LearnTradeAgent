"""ML module - online learning, meta-learning, and adaptive indicators."""

from trading.ml.online.indicators import (
    OnlineEMA,
    OnlineRSI,
    OnlineBollingerBands,
    OnlineMACD,
    OnlineATR,
    OnlineVWAP,
    OnlineStandardDeviation,
    OnlineCorrelation,
)
from trading.ml.online.adaptive import (
    AdaptiveConfig,
    AdaptiveEMA,
    AdaptiveRSI,
    AdaptiveBollingerBands,
    AdaptiveMACD,
    AdaptiveStrategy,
)
from trading.ml.meta import (
    MetaLearningConfig,
    MAML,
    Reptile,
    MetaSGD,
    ANIL,
    MetaStrategyAdapter,
    StrategyParameterTask,
)

__all__ = [
    # Online indicators
    "OnlineEMA",
    "OnlineRSI",
    "OnlineBollingerBands",
    "OnlineMACD",
    "OnlineATR",
    "OnlineVWAP",
    "OnlineStandardDeviation",
    "OnlineCorrelation",
    # Adaptive indicators
    "AdaptiveConfig",
    "AdaptiveEMA",
    "AdaptiveRSI",
    "AdaptiveBollingerBands",
    "AdaptiveMACD",
    "AdaptiveStrategy",
    # Meta-learning
    "MetaLearningConfig",
    "MAML",
    "Reptile",
    "MetaSGD",
    "ANIL",
    "MetaStrategyAdapter",
    "StrategyParameterTask",
]