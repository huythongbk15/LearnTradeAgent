"""
Configuration loader — reads config.yaml, validates, and exposes typed settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


# ── Validation helpers ────────────────────────────────────────────────────


class ConfigError(Exception):
    """Raised when config validation fails."""


_VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
_VALID_STORAGE = {"parquet", "csv", "duckdb"}
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _check_type(value: Any, name: str, expected: type) -> None:
    if not isinstance(value, expected):
        raise ConfigError(
            f"'{name}' must be {expected.__name__}, got {type(value).__name__}"
        )


def _check_in(value: Any, name: str, valid_set: set) -> None:
    if value not in valid_set:
        raise ConfigError(
            f"'{name}' = '{value}' is invalid. Valid: {sorted(valid_set)}"
        )


def _validate(raw: dict) -> None:
    """Validate config structure, raise ConfigError on issues."""
    # Exchanges
    exchanges = raw.get("exchanges", {})
    _check_type(exchanges, "exchanges", dict)
    for name, cfg in exchanges.items():
        _check_type(cfg, f"exchanges.{name}", dict)
        if cfg.get("enable", False):
            _check_type(cfg.get("type", "spot"), f"exchanges.{name}.type", str)

    # Data
    data = raw.get("data", {})
    _check_type(data, "data", dict)
    if "default_timeframe" in data:
        _check_in(data["default_timeframe"], "data.default_timeframe", _VALID_TIMEFRAMES)
    if "storage" in data:
        _check_in(data["storage"], "data.storage", _VALID_STORAGE)
    if "timeframes" in data:
        _check_type(data["timeframes"], "data.timeframes", list)
        for tf in data["timeframes"]:
            _check_in(tf, "data.timeframes[]", _VALID_TIMEFRAMES)
    if "max_retries" in data:
        _check_type(data["max_retries"], "data.max_retries", int)
    if "batch_size" in data:
        _check_type(data["batch_size"], "data.batch_size", int)

    # Symbols
    symbols = raw.get("symbols", {})
    _check_type(symbols, "symbols", dict)
    for exch, syms in symbols.items():
        _check_type(syms, f"symbols.{exch}", list)
        for sym in syms:
            _check_type(sym, f"symbols.{exch}[]", str)

    # Backtest
    bt = raw.get("backtest", {})
    _check_type(bt, "backtest", dict)
    if "initial_capital" in bt:
        _check_type(bt["initial_capital"], "backtest.initial_capital", (int, float))

    # Logging
    log = raw.get("logging", {})
    _check_type(log, "logging", dict)
    if "level" in log:
        _check_in(log["level"].upper(), "logging.level", _VALID_LOG_LEVELS)


# ── Project root detection ────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[2]  # src/trading_agent/config/ → project root
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"


# ── Config class ──────────────────────────────────────────────────────────


class Config:
    """Typed config wrapper. Validates on load. All attributes are read-only
    after construction (convention — mutating is allowed but not advised)."""

    def __init__(self, path: str | Path | None = None) -> None:
        path = Path(path) if path else _DEFAULT_CONFIG_PATH
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")

        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise ConfigError(f"Config file must be a YAML dict, got {type(raw).__name__}")

        _validate(raw)

        self._raw = raw
        self._load_exchanges(raw)
        self._load_data(raw)
        self._load_symbols(raw)
        self._load_backtest(raw)
        self._load_llm(raw)
        self._load_logging(raw)

    # ── helpers ──────────────────────────────────────────────────────────

    def _load_exchanges(self, raw: dict) -> None:
        self.exchanges: dict[str, dict] = raw.get("exchanges", {})
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
        self.llm_timeout: int = llm.get("timeout", 30)
        self.llm_fallback: list[dict] = llm.get("fallback", [])

    def _load_logging(self, raw: dict) -> None:
        log = raw.get("logging", {})
        self.log_level: str = log.get("level", "INFO").upper()
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
try:
    config = Config()
except ConfigError as e:
    import sys
    print(f"[red]Config error: {e}[/red]", file=sys.stderr)
    sys.exit(1)
