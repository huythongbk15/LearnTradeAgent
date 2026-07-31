"""Strategy library — đăng ký tự động qua import."""

from trading_agent.strategies import agent_ensemble as agent_ensemble, bbands as bbands, ma_crossover as ma_crossover, rsi as rsi
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
