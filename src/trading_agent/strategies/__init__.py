"""Strategy library — đăng ký tự động qua import."""

from trading_agent.strategies import agent_ensemble, bbands, ma_crossover, rsi
from trading_agent.strategies.base import (
    Strategy,
    get_strategy,
    list_strategies,
    register_strategy,
)

__all__ = [
    "Strategy",
    "get_strategy",
    "list_strategies",
    "register_strategy",
]
