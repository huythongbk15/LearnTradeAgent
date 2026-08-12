"""CLI shared helpers — lazy config singleton + rich console."""

from __future__ import annotations

from rich.console import Console


class _LazyConfig:
    """Config loaded on first access — avoids heavy deps at import time."""

    _cached = None

    def __getattr__(self, name):
        if self._cached is None:
            from trading_agent.config.loader import config as _cfg
            self.__class__._cached = _cfg
        return getattr(self._cached, name)


config = _LazyConfig()
console = Console()