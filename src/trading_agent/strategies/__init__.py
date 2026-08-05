"""
Trading Strategies Package — core strategy library + plugin architecture.

Core (long-only, vectorized): Strategy, get_strategy, list_strategies.
Plugins (Phase 6): BaseStrategy, Signal, StrategyContext, get_registry, ...
"""

from trading_agent.strategies import agent_ensemble as agent_ensemble, bbands as bbands, ma_crossover as ma_crossover, rsi as rsi
from trading_agent.strategies.base import (
    Strategy,
    get_strategy,
    list_strategies,
    register_strategy,
)
from trading_agent.strategies.plugins import (
    BaseStrategy,
    StrategyMetadata,
    Signal,
    StrategyContext,
    StrategyRegistry,
    StrategySandbox,
    StrategyType,
    RiskProfile,
    StrategyStatus,
    get_registry,
)

__all__ = [
    "Strategy",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "BaseStrategy",
    "StrategyMetadata",
    "Signal",
    "StrategyContext",
    "StrategyRegistry",
    "StrategySandbox",
    "StrategyType",
    "RiskProfile",
    "StrategyStatus",
    "get_registry",
]