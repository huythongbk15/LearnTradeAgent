"""
Configuration loader — reads config.yaml and exposes typed settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# Project root detection: walk up from this file to find project root
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[2]  # src/trading_agent/config → project root
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"


class Config:
    """Typed config wrapper. Attributes are populated from YAML + env vars."""

    def __init__(self, path: str | Path | None = None) -> None:
        path = Path(path) if path else _DEFAULT_CONFIG_PATH
        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        self._raw = raw
        self._load_exchanges(raw)
        self._load_data(raw)
        self._load_symbols(raw)
        self._load_backtest(raw)
        self._load_llm(raw)
        self._load_logging(raw)

    # ── helpers ──────────────────────────────────────────────────────────

    def _get(self, raw: dict, *keys: str, default: Any = None) -> Any:
        for k in keys:
            raw = raw.get(k, {})
        return raw if raw else default

    def _load_exchanges(self, raw: dict) -> None:
        self.exchanges: dict[str, dict] = raw.get("exchanges", {})
        # Filter only enabled
        self.enabled_exchanges: list[str] = [
            name for name, cfg in self.exchanges.items() if cfg.get("enable")
        ]

    def _load_data(self, raw: dict) -> None:
        d = raw.get("data", {})
        self.default_exchange: str = d.get("default_exchange", "binance")
        self.default_timeframe: str = d.get("default_timeframe", "1h")
        self.data_storage: str = d.get("storage", "parquet")
        self.storage_path: str = d.get("storage_path", "data/raw")
        self.batch_size: int = d.get("batch_size", 1000)
        self.max_retries: int = d.get("max_retries", 3)
        self.retry_delay_sec: int = d.get("retry_delay_sec", 5)
        self.timeframes: list[str] = d.get("timeframes", ["1h"])

    def _load_symbols(self, raw: dict) -> None:
        self.symbols: dict[str, list[str]] = raw.get("symbols", {})

    def _load_backtest(self, raw: dict) -> None:
        b = raw.get("backtest", {})
        self.initial_capital: float = b.get("initial_capital", 10000.0)
        self.commission: float = b.get("commission", 0.001)
        self.slippage: float = b.get("slippage", 0.0005)

    def _load_llm(self, raw: dict) -> None:
        llm = raw.get("llm", {})
        self.llm_provider: str = llm.get("provider", "openai")
        self.llm_model: str = llm.get("model", "gpt-4o-mini")
        self.llm_temperature: float = llm.get("temperature", 0.1)
        self.llm_max_tokens: int = llm.get("max_tokens", 2000)
        self.llm_fallback: list[dict] = llm.get("fallback", [])

    def _load_logging(self, raw: dict) -> None:
        log = raw.get("logging", {})
        self.log_level: str = log.get("level", "INFO")
        self.log_format: str = log.get(
            "format", "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )
        self.log_file: str | None = log.get("file")

    @property
    def storage_abs_path(self) -> Path:
        return _PROJECT_ROOT / self.storage_path

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    @classmethod
    def default_path(cls) -> Path:
        return _DEFAULT_CONFIG_PATH


# Singleton — load once, use everywhere
config = Config()
