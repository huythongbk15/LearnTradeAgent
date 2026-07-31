"""Strategy Plugin Architecture

Exports:
- BaseStrategy: Abstract base class for strategies
- StrategyMetadata: Strategy metadata
- Signal: Trading signal
- StrategyContext: Context passed to strategy
- StrategyRegistry: Plugin registry
- StrategySandbox: Sandboxed execution
"""
from trading.strategies.plugins.strategy_plugin import (
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

# Import adapters to trigger auto-registration
import trading.strategies.plugins.adapters  # noqa: F401

__all__ = [
    'BaseStrategy',
    'StrategyMetadata',
    'Signal',
    'StrategyContext',
    'StrategyRegistry',
    'StrategySandbox',
    'StrategyType',
    'RiskProfile',
    'StrategyStatus',
    'get_registry',
]