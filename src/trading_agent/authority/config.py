"""
AuthorityConfig — Single source of truth for all authority-chain parameters.

This replaces scattered YAML configs with a single Pydantic schema that:
- Validates on load (fail-fast)
- Provides type-safe access everywhere
- Enforces invariants (exposure caps ≤ 1.0, positive timeouts, etc.)
- Supports env var overrides for deployment flexibility
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic.types import PositiveFloat, PositiveInt


# ── Primitive type aliases with constraints ─────────────────────────────

ExposurePct = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveSeconds = Annotated[float, Field(gt=0.0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


# ── Enums ───────────────────────────────────────────────────────────────

from enum import Enum


class Environment(str, Enum):
    """Deployment environment — drives defaults and safety rails."""

    RESEARCH = "research"
    PAPER = "paper"
    TESTNET = "testnet"
    SHADOW = "shadow"
    CANARY = "canary"
    PRODUCTION = "production"


class RiskProfile(str, Enum):
    """Pre-calibrated risk parameter sets."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


# ── Sub-configs ─────────────────────────────────────────────────────────


class ExposureConfig(BaseModel):
    """Exposure limits — single source of truth for all caps."""

    # Portfolio-level caps
    max_portfolio_exposure: ExposurePct = Field(
        default=0.95,
        description="Maximum total portfolio exposure (sum of all positions)",
    )
    max_single_strategy_exposure: ExposurePct = Field(
        default=0.30,
        description="Maximum exposure for any single strategy",
    )
    max_single_symbol_exposure: ExposurePct = Field(
        default=0.25,
        description="Maximum exposure for any single symbol",
    )
    max_correlated_exposure: ExposurePct = Field(
        default=0.40,
        description="Maximum exposure to correlated symbols (BTC+ETH, etc.)",
    )

    # Per-trade caps
    max_trade_notional: PositiveFloat = Field(
        default=1_000_000.0,
        description="Maximum notional per single order",
    )
    min_trade_notional: PositiveFloat = Field(
        default=10.0,
        description="Minimum notional per single order",
    )

    # Risk scaling
    risk_scale_min: ExposurePct = Field(default=0.25, ge=0.0, le=1.0)
    risk_scale_max: ExposurePct = Field(default=1.0, ge=0.0, le=1.0)
    risk_scale_default: ExposurePct = Field(default=0.75, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> "ExposureConfig":
        if self.max_single_strategy_exposure > self.max_portfolio_exposure:
            raise ValueError(
                "max_single_strategy_exposure cannot exceed max_portfolio_exposure"
            )
        if self.max_single_symbol_exposure > self.max_single_strategy_exposure:
            raise ValueError(
                "max_single_symbol_exposure cannot exceed max_single_strategy_exposure"
            )
        if self.max_correlated_exposure > self.max_portfolio_exposure:
            raise ValueError(
                "max_correlated_exposure cannot exceed max_portfolio_exposure"
            )
        if self.min_trade_notional > self.max_trade_notional:
            raise ValueError("min_trade_notional cannot exceed max_trade_notional")
        if self.risk_scale_min > self.risk_scale_max:
            raise ValueError("risk_scale_min cannot exceed risk_scale_max")
        if not (self.risk_scale_min <= self.risk_scale_default <= self.risk_scale_max):
            raise ValueError(
                "risk_scale_default must be between risk_scale_min and risk_scale_max"
            )
        return self


class ExecutionConfig(BaseModel):
    """Execution-layer parameters."""

    # Price freshness
    max_price_age_seconds: PositiveSeconds = Field(
        default=60.0,
        description="Maximum age of trusted price before rejecting new orders",
    )
    max_stale_bars: PositiveInt = Field(
        default=3,
        description="Maximum closed bars without fresh data before blocking",
    )

    # Order lifecycle
    order_timeout_seconds: PositiveSeconds = Field(
        default=300.0,
        description="Maximum time an order can remain in SUBMITTED/PENDING",
    )
    claim_timeout_seconds: PositiveSeconds = Field(
        default=10.0,
        description="Maximum time to claim an intent before expiry",
    )

    # Reconciliation
    reconciliation_interval_seconds: PositiveSeconds = Field(
        default=30.0,
        description="Interval between broker reconciliation sweeps",
    )
    max_reconciliation_drift: ExposurePct = Field(
        default=0.02,
        description="Maximum position drift before alert (fraction of position)",
    )

    # Protection orders
    default_sl_pct: ExposurePct = Field(
        default=0.02,
        description="Default stop-loss as fraction of entry",
    )
    default_tp_pct: ExposurePct = Field(
        default=0.04,
        description="Default take-profit as fraction of entry",
    )
    protection_order_timeout_seconds: PositiveSeconds = Field(
        default=86_400.0,
        description="Protection order lifetime (default 24h)",
    )


class ResearchConfig(BaseModel):
    """Research governance parameters."""

    # Promotion thresholds
    minimum_trades: PositiveInt = Field(default=30)
    minimum_dsr: ExposurePct = Field(default=0.95)
    maximum_pbo: ExposurePct = Field(default=0.20)
    maximum_reality_gap: ExposurePct = Field(default=0.50)
    maximum_ece: ExposurePct = Field(default=0.10)

    # Evidence requirements
    testnet_soak_days: PositiveInt = Field(default=30)
    shadow_soak_days: PositiveInt = Field(default=30)
    canary_soak_days: PositiveInt = Field(default=30)

    # Calibration
    calibration_min_samples: PositiveInt = Field(default=30)
    calibration_window_days: PositiveInt = Field(default=90)

    # Drift detection
    drift_ks_threshold: ExposurePct = Field(default=0.05)
    drift_psi_threshold: ExposurePct = Field(default=0.20)
    drift_check_interval_hours: PositiveInt = Field(default=24)


class SimulatorConfig(BaseModel):
    """Simulator fidelity parameters (for research validation)."""

    # Fee model
    maker_fee_bps: PositiveFloat = Field(default=1.0)
    taker_fee_bps: PositiveFloat = Field(default=5.0)

    # Impact model
    impact_model: Literal["square_root", "linear", "almgren_chriss"] = Field(
        default="square_root"
    )
    impact_coefficient: PositiveFloat = Field(default=0.1)

    # Fill model
    fill_model: Literal["queue", "pro_rata", "instant"] = Field(default="queue")
    partial_fill_prob: ExposurePct = Field(default=0.1)

    # Latency
    base_latency_ms: PositiveInt = Field(default=50)
    latency_jitter_ms: PositiveInt = Field(default=20)

    # Slippage
    slippage_bps: PositiveFloat = Field(default=2.0)


class LiveConfig(BaseModel):
    """Live trading specific parameters."""

    # Broker
    broker_type: Literal["paper", "ccxt_binance", "ccxt_bybit", "alpaca", "oanda"] = (
        Field(default="paper")
    )
    exchange_name: str = Field(default="binance")

    # Connection
    ws_reconnect_interval_seconds: PositiveSeconds = Field(default=5.0)
    ws_max_reconnect_attempts: PositiveInt = Field(default=10)
    rest_timeout_seconds: PositiveSeconds = Field(default=30.0)

    # Safety
    kill_switch_enabled: bool = Field(default=True)
    manual_block_persist: bool = Field(default=True)
    max_daily_loss_pct: ExposurePct = Field(default=0.05)
    max_drawdown_pct: ExposurePct = Field(default=0.15)

    # State persistence
    state_dir: str = Field(default="data/live_state")
    event_store_path: str = Field(default="data/execution/events.db")
    snapshot_interval_seconds: PositiveSeconds = Field(default=60.0)


class LoggingConfig(BaseModel):
    """Observability configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")
    json_format: bool = Field(default=True)
    causation_log_path: str = Field(default="logs/causation.jsonl")
    decision_log_path: str = Field(default="logs/decisions.jsonl")
    audit_log_path: str = Field(default="logs/audit.jsonl")

    # Structured logging fields
    include_causation_id: bool = Field(default=True)
    include_authority_chain: bool = Field(default=True)
    include_exposure_delta: bool = Field(default=True)


# ── Root Config ─────────────────────────────────────────────────────────


class AuthorityConfig(BaseModel):
    """
    Single canonical configuration for the entire authority chain.

    Usage:
        config = AuthorityConfig.load()  # loads from YAML + env overrides
        config = AuthorityConfig.for_environment("paper")
    """

    # Meta
    environment: Environment = Field(default=Environment.PAPER)
    risk_profile: RiskProfile = Field(default=RiskProfile.MODERATE)
    version: str = Field(default="1.0.0")

    # Sub-configs
    exposure: ExposureConfig = Field(default_factory=ExposureConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    live: LiveConfig = Field(default_factory=LiveConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Symbol universe (validated against instrument registry)
    symbols: tuple[str, ...] = Field(
        default=(
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "XRP/USDT",
            "BNB/USDT",
            "ZEC/USDT",
            "DOGE/USDT",
            "TRX/USDT",
            "ADA/USDT",
            "NEAR/USDT",
        ),
        description="Supported trading symbols (must match instrument registry)",
    )

    # Timeframe
    timeframe: str = Field(default="1h", pattern=r"^\d+[mhdw]$")

    # ── Class methods for loading ──────────────────────────────────────

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        env_prefix: str = "TA_",
    ) -> "AuthorityConfig":
        """
        Load config from YAML file with environment variable overrides.

        Priority (highest first):
        1. Environment variables (TA_*)
        2. YAML config file
        3. Pydantic defaults

        Env var format: TA_EXPOSURE__MAX_PORTFOLIO_EXPOSURE=0.9
        """
        import yaml

        data: dict = {}

        # 1. Load YAML if provided
        if config_path:
            path = Path(config_path)
            if path.exists():
                with open(path) as f:
                    data = yaml.safe_load(f) or {}

        # 2. Apply env overrides
        env_data = cls._parse_env_overrides(env_prefix)
        data = cls._deep_merge(data, env_data)

        # 3. Validate and construct
        return cls.model_validate(data)

    @classmethod
    def _parse_env_overrides(cls, prefix: str) -> dict:
        """Parse TA_* environment variables into nested dict."""
        result: dict = {}
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            # TA_EXPOSURE__MAX_PORTFOLIO_EXPOSURE → exposure.max_portfolio_exposure
            parts = key[len(prefix) :].lower().split("__")
            if len(parts) < 2:
                continue
            cls._set_nested(result, parts, cls._parse_value(value))
        return result

    @staticmethod
    def _parse_value(value: str):
        """Parse string to appropriate Python type."""
        # Booleans
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        # Numbers
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        # Lists (comma-separated)
        if "," in value:
            return [v.strip() for v in value.split(",")]
        return value

    @staticmethod
    def _set_nested(d: dict, parts: list[str], value) -> None:
        """Set nested dict value: ['exposure', 'max_portfolio_exposure'] → d['exposure']['max_portfolio_exposure']"""
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """Deep merge override into base."""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = AuthorityConfig._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @classmethod
    def for_environment(cls, env: Environment) -> "AuthorityConfig":
        """Create config with environment-specific presets."""
        config = cls(environment=env)

        if env == Environment.RESEARCH:
            config.exposure.max_portfolio_exposure = 1.0
            config.exposure.max_single_strategy_exposure = 1.0
            config.execution.max_price_age_seconds = 3600.0
            config.live.kill_switch_enabled = False

        elif env == Environment.TESTNET:
            config.exposure.max_portfolio_exposure = 0.50
            config.exposure.max_single_strategy_exposure = 0.20
            config.execution.max_price_age_seconds = 30.0
            config.live.max_daily_loss_pct = 0.02

        elif env == Environment.SHADOW:
            config.exposure.max_portfolio_exposure = 0.0  # Shadow = no real exposure
            config.live.kill_switch_enabled = True

        elif env == Environment.CANARY:
            config.exposure.max_portfolio_exposure = 0.10
            config.exposure.max_single_strategy_exposure = 0.05
            config.live.max_daily_loss_pct = 0.01
            config.live.max_drawdown_pct = 0.05

        elif env == Environment.PRODUCTION:
            config.exposure.risk_scale_default = 1.0
            config.live.kill_switch_enabled = True
            config.live.manual_block_persist = True

        return config

    # ── Validation ──────────────────────────────────────────────────────

    @model_validator(mode="after")
    def validate_symbols_match_registry(self) -> "AuthorityConfig":
        """Ensure configured symbols exist in instrument registry."""
        from trading_agent.execution.canonical.instrument_registry import (
            TEN_PAIR_1H_SYMBOLS,
            UnsupportedInstrumentError,
            get_instrument_rules,
        )

        for symbol in self.symbols:
            try:
                get_instrument_rules(symbol)
            except UnsupportedInstrumentError as exc:
                raise ValueError(
                    f"Symbol {symbol!r} not in instrument registry: {exc}"
                ) from exc

        # Ensure all registry symbols are covered if we claim to support them
        configured_set = set(self.symbols)
        registry_set = set(TEN_PAIR_1H_SYMBOLS)
        if not configured_set.issubset(registry_set):
            raise ValueError(
                f"Configured symbols {configured_set - registry_set} not in registry"
            )

        return self

    # ── Convenience properties ──────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        """True if real capital at risk (testnet/canary/production)."""
        return self.environment in (
            Environment.TESTNET,
            Environment.SHADOW,
            Environment.CANARY,
            Environment.PRODUCTION,
        )

    @property
    def is_shadow(self) -> bool:
        return self.environment == Environment.SHADOW

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


# ── Singleton accessor ─────────────────────────────────────────────────

_config: AuthorityConfig | None = None


def get_authority_config() -> AuthorityConfig:
    """Get the global authority config (loads on first call)."""
    global _config
    if _config is None:
        # Check for config file in standard locations
        for path in [
            Path("config/authority.yaml"),
            Path("config/authority.yml"),
            Path("/etc/trading-agent/authority.yaml"),
        ]:
            if path.exists():
                _config = AuthorityConfig.load(path)
                break
        else:
            _config = AuthorityConfig.for_environment(Environment.PAPER)
    return _config


def set_authority_config(config: AuthorityConfig) -> None:
    """Override global config (primarily for tests)."""
    global _config
    _config = config


def reset_authority_config() -> None:
    """Reset global config (primarily for tests)."""
    global _config
    _config = None


__all__ = [
    "AuthorityConfig",
    "ExposureConfig",
    "ExecutionConfig",
    "ResearchConfig",
    "SimulatorConfig",
    "LiveConfig",
    "LoggingConfig",
    "Environment",
    "RiskProfile",
    "get_authority_config",
    "set_authority_config",
    "reset_authority_config",
]
