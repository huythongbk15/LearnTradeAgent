"""Tests for EffectiveConfig secret merging."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trading_agent.config.loader import Config, ConfigError, EffectiveConfig


def _write_config(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(payload))
    return p


class TestEffectiveConfig:
    def test_env_token_merged_for_testnet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg_path = _write_config(
            tmp_path,
            {
                "schema_version": 1,
                "deployment": {
                    "mode": "testnet",
                    "position_limit_pct": 0.1,
                    "max_slippage_pct": 0.001,
                    "stale_data_max_age_s": 30,
                },
                "alerts": {
                    "telegram": {"enabled": True, "bot_token": "", "chat_id": ""}
                },
            },
        )
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        cfg = EffectiveConfig(Config(cfg_path))
        assert cfg.alert_telegram_bot_token == "env-token"
        assert cfg.alert_telegram_chat_id == "env-chat"

    def test_yaml_token_used_when_env_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg_path = _write_config(
            tmp_path,
            {
                "schema_version": 1,
                "deployment": {
                    "mode": "testnet",
                    "position_limit_pct": 0.1,
                    "max_slippage_pct": 0.001,
                    "stale_data_max_age_s": 30,
                },
                "alerts": {
                    "telegram": {
                        "enabled": True,
                        "bot_token": "yaml-token",
                        "chat_id": "yaml-chat",
                    }
                },
            },
        )
        cfg = EffectiveConfig(Config(cfg_path))
        assert cfg.alert_telegram_bot_token == "yaml-token"
        assert cfg.alert_telegram_chat_id == "yaml-chat"

    def test_paper_mode_allows_missing_telegram(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg_path = _write_config(
            tmp_path,
            {
                "schema_version": 1,
                "deployment": {"mode": "paper", "alerting_required": False},
            },
        )
        cfg = EffectiveConfig(Config(cfg_path))
        assert cfg.deploy_mode == "paper"

    def test_testnet_missing_telegram_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        cfg_path = _write_config(
            tmp_path,
            {
                "schema_version": 1,
                "deployment": {
                    "mode": "testnet",
                    "position_limit_pct": 0.1,
                    "max_slippage_pct": 0.001,
                    "stale_data_max_age_s": 30,
                    "alerting_required": True,
                },
            },
        )
        with pytest.raises(ConfigError):
            Config(cfg_path)
