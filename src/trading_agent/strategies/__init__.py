# Trading Agent Strategies Package
"""
Strategy implementations for the trading agent system.
"""

from trading_agent.strategies.base import (
    Strategy,
    register_strategy,
    get_strategy,
    list_strategies,
)
from trading_agent.strategies.options_strategies import (
    OptionStrategyType,
    OptionsStrategy,
    CoveredCallStrategy,
    CashSecuredPutStrategy,
    ShortStraddleStrategy,
    ShortStrangleStrategy,
    IronCondorStrategy,
    GammaScalpStrategy,
    CalendarSpreadStrategy,
    DispersionStrategy,
    Position,
)

__all__ = [
    "Strategy",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "OptionStrategyType",
    "OptionsStrategy",
    "CoveredCallStrategy",
    "CashSecuredPutStrategy",
    "ShortStraddleStrategy",
    "ShortStrangleStrategy",
    "IronCondorStrategy",
    "GammaScalpStrategy",
    "CalendarSpreadStrategy",
    "DispersionStrategy",
    "Position",
]