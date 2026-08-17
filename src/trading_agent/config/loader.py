"""
Configuration loader — reads config.yaml, validates, and exposes typed settings.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ── Validation helpers ────────────────────────────────────────────────────


class ConfigError(Exception):
    """Raised when config validation fails."""


_VALID_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"}
_VALID_STORAGE = {"parquet", "csv", "duckdb"}
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_VALID_DEPLOY_MODES = {"paper", "testnet", "mainnet-canary", "mainnet-normal"}
_VALID_EXECUTION_ALGORITHMS = {"market", "twap", "pov"}
CONFIG_SCHEMA_VERSION = 1


def _check_type(
    value: Any,
    name: str,
    expected: type | tuple[type, ...],
) -> None:
    expected_types = expected if isinstance(expected, tuple) else (expected,)
    is_bool_disguised_as_number = isinstance(value, bool) and bool not in expected_types
    if not isinstance(value, expected) or is_bool_disguised_as_number:
        expected_name = " or ".join(item.__name__ for item in expected_types)
        raise ConfigError(
            f"'{name}' must be {expected_name}, got {type(value).__name__}"
        )


def _check_in(value: Any, name: str, valid_set: set) -> None:
    if value not in valid_set:
        raise ConfigError(
            f"'{name}' = '{value}' is invalid. Valid: {sorted(valid_set)}"
        )


def _validate(raw: dict) -> None:
    """Validate config structure, raise ConfigError on issues."""

    # Allow ENV-supplied Telegram secrets to satisfy alerting requirements.
    raw = _merge_env_secrets(raw)

    # Schema version — fail closed on unknown future schema.
    version = raw.get("schema_version", 1)
    _check_type(version, "schema_version", int)
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"config 'schema_version' = {version}, expected {CONFIG_SCHEMA_VERSION}. "
            "Refusing to load an unknown config schema (fail-closed)."
        )

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
        _check_in(
            data["default_timeframe"], "data.default_timeframe", _VALID_TIMEFRAMES
        )
    if "storage" in data:
        _check_in(data["storage"], "data.storage", _VALID_STORAGE)
    if "timeframes" in data:
        _check_type(data["timeframes"], "data.timeframes", list)
        for tf in data["timeframes"]:
            _check_in(tf, "data.timeframes[]", _VALID_TIMEFRAMES)
    if "max_retries" in data:
        _check_type(data["max_retries"], "data.max_retries", int)
        if data["max_retries"] < 1:
            raise ConfigError("'data.max_retries' must be at least 1")
    if "batch_size" in data:
        _check_type(data["batch_size"], "data.batch_size", int)
        if data["batch_size"] < 1:
            raise ConfigError("'data.batch_size' must be at least 1")

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
        if bt["initial_capital"] <= 0:
            raise ConfigError("'backtest.initial_capital' must be positive")
    for field in ("commission", "slippage"):
        if field in bt:
            _check_type(bt[field], f"backtest.{field}", (int, float))
            if not 0 <= bt[field] < 1:
                raise ConfigError(f"'backtest.{field}' must be in [0, 1)")

    # Deployment — safety-critical, fail closed.
    dep = raw.get("deployment", {})
    _check_type(dep, "deployment", dict)
    mode = dep.get("mode", "paper")
    _check_in(mode, "deployment.mode", _VALID_DEPLOY_MODES)
    if "execution_algorithm" in dep:
        _check_in(
            dep["execution_algorithm"],
            "deployment.execution_algorithm",
            _VALID_EXECUTION_ALGORITHMS,
        )
    if "position_limit_pct" in dep:
        _check_type(
            dep["position_limit_pct"], "deployment.position_limit_pct", (int, float)
        )
        if not 0 < dep["position_limit_pct"] <= 1:
            raise ConfigError("'deployment.position_limit_pct' must be in (0, 1]")
    if "max_slippage_pct" in dep:
        _check_type(
            dep["max_slippage_pct"], "deployment.max_slippage_pct", (int, float)
        )
        if not 0 <= dep["max_slippage_pct"] < 1:
            raise ConfigError("'deployment.max_slippage_pct' must be in [0, 1)")
    if "stale_data_max_age_s" in dep:
        _check_type(
            dep["stale_data_max_age_s"], "deployment.stale_data_max_age_s", (int, float)
        )
        if dep["stale_data_max_age_s"] <= 0:
            raise ConfigError("'deployment.stale_data_max_age_s' must be positive")

    # Fail-closed: a non-paper deployment REQUIRES explicit risk fields.
    if mode in {"testnet", "mainnet-canary", "mainnet-normal"}:
        missing = []
        for field in ("position_limit_pct", "max_slippage_pct", "stale_data_max_age_s"):
            if field not in dep:
                missing.append(field)
        if dep.get("alerting_required", True):
            alerts = raw.get("alerts", {})
            telegram = alerts.get("telegram", {}) if isinstance(alerts, dict) else {}
            if not (telegram.get("enabled") and telegram.get("bot_token")):
                missing.append("alerts.telegram.{enabled,bot_token}")
        if missing:
            raise ConfigError(
                f"deployment.mode={mode!r} requires fail-closed settings, missing: {missing}"
            )

    # Logging
    log = raw.get("logging", {})
    _check_type(log, "logging", dict)
    if "level" in log:
        _check_in(log["level"].upper(), "logging.level", _VALID_LOG_LEVELS)


def _merge_env_secrets(raw: dict) -> dict:
    """Merge Telegram secrets and other ENV overrides into config.

    Never logs secrets. Returns a new dict (does not mutate input).
    ENV variables take precedence over YAML values for supported keys.
    """
    merged = dict(raw)

    # ── Telegram secrets ───────────────────────────────────────────────
    alerts = dict(merged.get("alerts", {}))
    telegram = dict(alerts.get("telegram", {}))
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or telegram.get("bot_token", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or telegram.get("chat_id", "")
    telegram["bot_token"] = bot_token
    telegram["chat_id"] = chat_id
    alerts["telegram"] = telegram
    merged["alerts"] = alerts

    # ── General ENV overrides ──────────────────────────────────────────
    # Supported ENV overrides: data.default_timeframe, data.storage, deploy.mode
    env_overrides = {
        "TRADING_DEFAULT_TIMEFRAME": ("data", "default_timeframe"),
        "TRADING_STORAGE": ("data", "storage"),
        "TRADING_DEPLOY_MODE": ("deploy", "mode"),
        "TRADING_INITIAL_CAPITAL": ("backtest", "initial_capital"),
        "TRADING_COMMISSION": ("backtest", "commission"),
        "TRADING_SLIPPAGE": ("backtest", "slippage"),
    }

    for env_var, path in env_overrides.items():
        value = os.getenv(env_var)
        if value is None:
            continue
        # Navigate/create nested dicts
        target = merged
        for key in path[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]
        # Type conversion
        final_key = path[-1]
        if final_key in ("initial_capital", "commission", "slippage"):
            try:
                value = float(value)
            except ValueError:
                continue
        target[final_key] = value

    return merged


# ── Project root detection ────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parents[2]  # src/trading_agent/config/ → project root
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.yaml"


# ── Config class ──────────────────────────────────────────────────────────


class Config:
    """Typed config wrapper. Validates on load. All attributes are read-only
    after construction (convention — mutating is allowed but not advised)."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured_path = path or os.getenv("TRADING_CONFIG_PATH")
        path = Path(configured_path) if configured_path else _DEFAULT_CONFIG_PATH
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")

        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise ConfigError(
                f"Config file must be a YAML dict, got {type(raw).__name__}"
            )

        _validate(raw)

        self._raw = raw
        self._load_schema(raw)
        self._load_exchanges(raw)
        self._load_data(raw)
        self._load_symbols(raw)
        self._load_backtest(raw)
        self._load_llm(raw)
        self._load_monitoring(raw)
        self._load_deployment(raw)
        self._load_logging(raw)

    # ── helpers ──────────────────────────────────────────────────────────

    def _load_schema(self, raw: dict) -> None:
        self.schema_version: int = raw.get("schema_version", 1)

    def _load_deployment(self, raw: dict) -> None:
        d = raw.get("deployment", {})
        self.deploy_mode: str = d.get("mode", "paper")
        self.execution_algorithm: str = d.get("execution_algorithm", "market")
        self.position_limit_pct: float = d.get("position_limit_pct", 0.25)
        self.max_slippage_pct: float = d.get("max_slippage_pct", 0.005)
        self.stale_data_max_age_s: float = d.get("stale_data_max_age_s", 30.0)
        self.alerting_required: bool = d.get("alerting_required", True)

    @property
    def live_trading_enabled(self) -> bool:
        """True only when the deployment mode authorizes live/real orders."""
        return self.deploy_mode in {"mainnet-canary", "mainnet-normal"}

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
        self.llm_model_fallback: list[str] = llm.get("model_fallback", [])

    def _load_monitoring(self, raw: dict) -> None:
        m = raw.get("monitoring", {})
        db = m.get("database", {})
        self.monitoring_db_enabled: bool = db.get("enabled", True)
        self.monitoring_db_path: str = db.get("path", "data/trading.db")
        dash = m.get("dashboard", {})
        self.dashboard_port: int = dash.get("port", 8501)
        self.dashboard_auto_refresh: int = dash.get("auto_refresh", 5)

        a = raw.get("alerts", {})
        self.alert_console_enabled: bool = a.get("console", {}).get("enabled", True)
        telegram = a.get("telegram", {})
        self.alert_telegram_enabled: bool = telegram.get("enabled", False)
        self.alert_telegram_bot_token: str = telegram.get("bot_token", "")
        self.alert_telegram_chat_id: str = telegram.get("chat_id", "")

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


class EffectiveConfig:
    """Config with environment-supplied secrets merged in.

    This wrapper preserves the original ``Config`` validation rules while
    allowing secrets to come from the environment instead of tracked YAML.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    def __getattr__(self, name: str) -> Any:
        return getattr(self._config, name)

    @property
    def alert_telegram_bot_token(self) -> str:
        return os.getenv("TELEGRAM_BOT_TOKEN") or self._config.alert_telegram_bot_token

    @property
    def alert_telegram_chat_id(self) -> str:
        return os.getenv("TELEGRAM_CHAT_ID") or self._config.alert_telegram_chat_id


# Singleton — fail with ConfigError instead of terminating the importing process.
config = Config()
