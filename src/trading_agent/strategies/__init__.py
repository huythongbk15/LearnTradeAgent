"""Strategy library — đăng ký tự động qua import."""

from trading_agent.strategies import ma_crossover, rsi, bbands
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
