"""
Strategy base class — abstract interface cho mọi chiến lược giao dịch.

Mọi strategy đều kế thừa ``Strategy`` và implement 2 phương thức:

- ``compute_indicators(df)`` — thêm cột indicator vào DataFrame
- ``generate_signals(df)`` — sinh tín hiệu -1/0/+1 từ indicators

Strategy được đăng ký tự động qua ``@register_strategy`` decorator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

import polars as pl


# ── Registry ──────────────────────────────────────────────────────────────

_STRATEGY_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(name: str | None = None):
    """Decorator: đăng ký strategy vào global registry."""

    def wrapper(cls: type[Strategy]) -> type[Strategy]:
        key = name or cls.__name__
        _STRATEGY_REGISTRY[key] = cls
        return cls

    return wrapper


def get_strategy(name: str) -> type[Strategy]:
    if name not in _STRATEGY_REGISTRY:
        available = ", ".join(_STRATEGY_REGISTRY)
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return _STRATEGY_REGISTRY[name]


def list_strategies() -> dict[str, str]:
    """Return {name: description} for all registered strategies."""
    return {
        name: cls.__doc__.strip().split("\n")[0] if cls.__doc__ else ""
        for name, cls in _STRATEGY_REGISTRY.items()
    }


# ── Base class ────────────────────────────────────────────────────────────


class Strategy(ABC):
    """Base class cho tất cả chiến lược giao dịch.

    Subclass phải set ``name`` và implement 2 method bên dưới.
    """

    name: ClassVar[str] = "base"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = params or {}

    @abstractmethod
    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Tính toán các indicator từ OHLCV data, trả về DataFrame có thêm cột."""
        ...

    @abstractmethod
    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Sinh tín hiệu từ DataFrame đã có indicators.

        Returns
        -------
        pl.Series[int]
            -1 = short, 0 = hold, +1 = long
        """
        ...

    def __repr__(self) -> str:
        params_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params_str})"
