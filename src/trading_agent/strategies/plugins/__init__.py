"""Strategy Plugin Architecture

Exports:
- BaseStrategy: Abstract base class for strategies
- StrategyMetadata: Strategy metadata
- Signal: Trading signal
- StrategyContext: Context passed to strategy
- StrategyRegistry: Plugin registry
- StrategySandbox: Sandboxed execution
"""

# Import adapters to trigger auto-registration
import trading_agent.strategies.plugins.adapters  # noqa: F401
from trading_agent.strategies.plugins.strategy_plugin import (
    BaseStrategy,
    RiskProfile,
    Signal,
    StrategyContext,
    StrategyMetadata,
    StrategyRegistry,
    StrategySandbox,
    StrategyStatus,
    StrategyType,
    get_registry,
)

__all__ = [
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
