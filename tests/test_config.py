"""Tests for config/loader.py"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from trading_agent.config.loader import Config, ConfigError, _validate


def _make_cfg(**overrides) -> dict:
    """Return a minimal valid config dict."""
    cfg = {
        "exchanges": {"binance": {"enable": True, "type": "spot"}},
        "data": {
            "default_exchange": "binance",
            "default_timeframe": "1h",
            "storage": "parquet",
            "timeframes": ["1h", "4h"],
        },
        "symbols": {"binance": ["BTC/USDT", "ETH/USDT"]},
        "backtest": {"initial_capital": 10000},
        "logging": {"level": "INFO"},
    }
    cfg.update(overrides)
    return cfg


def _write_tmp(cfg: dict) -> Path:
    p = Path(tempfile.mktemp(suffix=".yaml"))
    with open(p, "w") as f:
        yaml.dump(cfg, f)
    return p


class TestValidate:
    def test_valid(self):
        _validate(_make_cfg())  # should not raise

    def test_invalid_timeframe(self):
        with pytest.raises(ConfigError, match="invalid"):
            _validate(_make_cfg(data={"default_timeframe": "1z"}))

    def test_invalid_storage(self):
        with pytest.raises(ConfigError, match="invalid"):
            _validate(_make_cfg(data={"storage": "excel"}))

    def test_invalid_log_level(self):
        with pytest.raises(ConfigError, match="invalid"):
            _validate(_make_cfg(logging={"level": "TRACE"}))

    def test_non_dict_exchanges(self):
        with pytest.raises(ConfigError, match="must be dict"):
            _validate(_make_cfg(exchanges="not_a_dict"))

    def test_non_list_symbols(self):
        with pytest.raises(ConfigError, match="must be dict"):
            _validate(_make_cfg(symbols="not_a_dict"))

    def test_invalid_numeric_type_has_useful_error(self):
        with pytest.raises(ConfigError, match="int or float"):
            _validate(_make_cfg(backtest={"initial_capital": "10000"}))

    @pytest.mark.parametrize(
        "backtest",
        [
            {"initial_capital": True},
            {"initial_capital": 0},
            {"commission": -0.1},
            {"slippage": 1.0},
        ],
    )
    def test_invalid_backtest_numbers(self, backtest):
        with pytest.raises(ConfigError):
            _validate(_make_cfg(backtest=backtest))


class TestSchemaVersion:
    def test_unknown_schema_version_rejected(self):
        with pytest.raises(ConfigError, match="schema_version"):
            _validate(_make_cfg(schema_version=999))

    def test_current_schema_version_accepted(self):
        _validate(_make_cfg(schema_version=1))  # should not raise


class TestDeploymentFailClosed:
    def test_paper_mode_defaults_ok(self):
        _validate(_make_cfg(deployment={"mode": "paper"}))

    def test_testnet_mode_requires_risk_fields(self):
        with pytest.raises(ConfigError, match="requires fail-closed"):
            _validate(_make_cfg(deployment={"mode": "testnet"}))

    def test_testnet_mode_with_risk_fields_ok(self):
        dep = {
            "mode": "testnet",
            "position_limit_pct": 0.1,
            "max_slippage_pct": 0.002,
            "stale_data_max_age_s": 10.0,
        }
        cfg = _make_cfg(
            deployment=dep,
            alerts={"telegram": {"enabled": True, "bot_token": "x", "chat_id": "1"}},
        )
        _validate(cfg)  # should not raise

    def test_mainnet_mode_requires_telegram_alerting(self):
        dep = {
            "mode": "mainnet-normal",
            "position_limit_pct": 0.1,
            "max_slippage_pct": 0.002,
            "stale_data_max_age_s": 10.0,
        }
        with pytest.raises(ConfigError, match="alerts.telegram"):
            _validate(_make_cfg(deployment=dep))

    def test_invalid_mode_rejected(self):
        with pytest.raises(ConfigError, match="invalid"):
            _validate(_make_cfg(deployment={"mode": "production"}))

    def test_invalid_execution_algorithm_rejected(self):
        with pytest.raises(ConfigError, match="invalid"):
            _validate(_make_cfg(deployment={"execution_algorithm": "iceberg"}))

    def test_invalid_position_limit_pct(self):
        with pytest.raises(ConfigError, match="position_limit_pct"):
            _validate(_make_cfg(deployment={"position_limit_pct": 1.5}))


class TestDeploymentLoaded:
    def test_load_deployment_defaults(self):
        p = _write_tmp(_make_cfg())
        cfg = Config(p)
        assert cfg.deploy_mode == "paper"
        assert cfg.execution_algorithm == "market"
        assert cfg.position_limit_pct == 0.25
        assert cfg.live_trading_enabled is False

    def test_load_deployment_custom(self):
        cfg = _make_cfg(
            deployment={
                "mode": "mainnet-canary",
                "execution_algorithm": "twap",
                "position_limit_pct": 0.1,
                "max_slippage_pct": 0.002,
                "stale_data_max_age_s": 5.0,
            },
            alerts={
                "telegram": {"enabled": True, "bot_token": "x", "chat_id": "1"},
            },
        )
        p = _write_tmp(cfg)
        loaded = Config(p)
        assert loaded.deploy_mode == "mainnet-canary"
        assert loaded.execution_algorithm == "twap"
        assert loaded.live_trading_enabled is True


class TestConfig:
    def test_load_valid(self):
        p = _write_tmp(_make_cfg())
        cfg = Config(p)
        assert cfg.default_exchange == "binance"
        assert cfg.default_timeframe == "1h"
        assert "binance" in cfg.enabled_exchanges
        assert len(cfg.symbols.get("binance", [])) == 2
        assert cfg.initial_capital == 10000.0

    def test_load_missing_file(self):
        with pytest.raises(ConfigError, match="not found"):
            Config(Path("/nonexistent/config.yaml"))

    def test_config_default_timeframe(self):
        """Should default to 1h when not specified."""
        p = _write_tmp(_make_cfg(data={}))
        cfg = Config(p)
        assert cfg.default_timeframe == "1h"

    def test_symbols_default_exchange(self):
        p = _write_tmp(_make_cfg(symbols={"binance": ["BTC/USDT"]}))
        cfg = Config(p)
        assert cfg.symbols["binance"] == ["BTC/USDT"]

    def test_environment_config_path(self, monkeypatch):
        p = _write_tmp(_make_cfg(data={"default_timeframe": "4h"}))
        monkeypatch.setenv("TRADING_CONFIG_PATH", str(p))
        assert Config().default_timeframe == "4h"
