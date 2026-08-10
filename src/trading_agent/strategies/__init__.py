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
# Import strategy modules to register them
import trading_agent.strategies.ma_crossover  # noqa: F401
import trading_agent.strategies.rsi  # noqa: F401
import trading_agent.strategies.bbands  # noqa: F401
import trading_agent.strategies.enhanced_ma  # noqa: F401
import trading_agent.strategies.regime_switching  # noqa: F401
import trading_agent.strategies.agent_ensemble  # noqa: F401
import trading_agent.strategies.online_learning_strategy  # noqa: F401

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
