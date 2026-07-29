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
