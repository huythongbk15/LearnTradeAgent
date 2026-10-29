# Trading Agent Strategies Package
"""
Strategy implementations for the trading agent system.
"""

import trading_agent.strategies.bbands  # noqa: F401
import trading_agent.strategies.enhanced_ma  # noqa: F401

# Import strategy modules to register them. agent_ensemble and regime_switching
# register on first lookup (see strategies.base._LAZY_STRATEGY_MODULES);
# online_learning_strategy registers nothing and is imported by its users.
import trading_agent.strategies.cross_sectional_momentum  # noqa: F401
import trading_agent.strategies.ma_crossover  # noqa: F401
import trading_agent.strategies.range_mean_reversion  # noqa: F401
import trading_agent.strategies.rsi  # noqa: F401
import trading_agent.strategies.stat_arbitrage  # noqa: F401
import trading_agent.strategies.trend_pullback  # noqa: F401
import trading_agent.strategies.volatility_breakout  # noqa: F401
from trading_agent.strategies.base import (
    Strategy,
    get_strategy,
    list_strategies,
    register_strategy,
)
from trading_agent.strategies.options_strategies import (
    CalendarSpreadStrategy,
    CashSecuredPutStrategy,
    CoveredCallStrategy,
    DispersionStrategy,
    GammaScalpStrategy,
    IronCondorStrategy,
    OptionsStrategy,
    OptionStrategyType,
    Position,
    ShortStraddleStrategy,
    ShortStrangleStrategy,
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
