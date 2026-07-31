"""
Trading Strategies Package - Plugin Architecture

Exports:
- BaseStrategy: Abstract base class for strategies
- StrategyMetadata: Strategy metadata
- Signal: Trading signal
- StrategyContext: Context passed to strategies
- StrategyRegistry: Plugin registry
- StrategySandbox: Sandboxed execution
"""
from trading.strategies.plugins import (
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