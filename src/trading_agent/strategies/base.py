"""
Strategy base class — abstract interface cho mọi chiến lược giao dịch.

Mọi strategy đều kế thừa ``Strategy`` và implement 2 phương thức:

- ``compute_indicators(df)`` — thêm cột indicator vào DataFrame
- ``generate_signals(df)`` — sinh tín hiệu -1/0/+1 từ indicators

Strategy được đăng ký tự động qua ``@register_strategy`` decorator.
"""

from __future__ import annotations

import importlib
import importlib.util
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import polars as pl

# ── Registry ──────────────────────────────────────────────────────────────

_STRATEGY_REGISTRY: dict[str, type[Strategy]] = {}

# Strategies whose modules pull in heavy optional stacks (LLM agents, ml and
# sklearn). They register on first lookup, not when trading_agent.strategies is
# imported, so the live runners do not load those stacks.
_LAZY_STRATEGY_MODULES: dict[str, str] = {
    "agent_ensemble": "trading_agent.strategies.agent_ensemble",
    "regime_switching": "trading_agent.strategies.regime_switching",
}


class LazyStrategyClass:
    """Stand-in for a strategy class whose module is imported on first use.

    Calling it constructs the strategy like the class would. ``source_path``
    locates the defining file without importing it, for source hashing.
    """

    __slots__ = ("module", "attr")

    def __init__(self, module: str, attr: str) -> None:
        self.module = module
        self.attr = attr

    def resolve(self) -> type[Strategy]:
        return getattr(importlib.import_module(self.module), self.attr)

    def __call__(self, *args: Any, **kwargs: Any) -> Strategy:
        return self.resolve()(*args, **kwargs)

    @property
    def source_path(self) -> Path:
        spec = importlib.util.find_spec(self.module)
        if spec is None or not spec.origin:
            raise ImportError(f"cannot locate module {self.module!r}")
        return Path(spec.origin)

    def __repr__(self) -> str:
        return f"LazyStrategyClass({self.module!r}, {self.attr!r})"


def _load_lazy_strategies(name: str | None = None) -> None:
    for key, module in _LAZY_STRATEGY_MODULES.items():
        if (name is None or key == name) and key not in _STRATEGY_REGISTRY:
            importlib.import_module(module)


def register_strategy(name: str | None = None):
    """Decorator: đăng ký strategy vào global registry."""

    def wrapper(cls: type[Strategy]) -> type[Strategy]:
        key = name or cls.__name__
        _STRATEGY_REGISTRY[key] = cls
        return cls

    return wrapper


def get_strategy(name: str) -> type[Strategy]:
    if name not in _STRATEGY_REGISTRY:
        _load_lazy_strategies(name)
    if name not in _STRATEGY_REGISTRY:
        available = ", ".join(
            [*_STRATEGY_REGISTRY, *(n for n in _LAZY_STRATEGY_MODULES if n not in _STRATEGY_REGISTRY)]
        )
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return _STRATEGY_REGISTRY[name]


def list_strategies() -> dict[str, str]:
    """Return {name: description} for all registered strategies."""
    _load_lazy_strategies()
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
