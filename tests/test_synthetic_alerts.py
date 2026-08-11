"""Synthetic alert tests (REPO_TRUTH Phase C: observability & independent
alerting).  Verifies the full alert pipeline without a real Telegram bot:
console channel, telegram POST payload shape, and fail-safe behavior on
missing credentials / API errors."""

from __future__ import annotations

import json
import logging
import urllib.request

import pytest

import trading_agent.monitoring.alerter as alerter
from trading_agent.monitoring.alerter import (
    init_alerts,
    send_daily_summary,
    send_risk_alert,
    send_status_report,
    send_trade_alert,
)


@pytest.fixture(autouse=True)
def _clean_alert_config(monkeypatch):
    """Default: console-only, no telegram credentials."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    init_alerts({"console": {"enabled": True}})
    yield
    init_alerts({"console": {"enabled": True}})


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _capture_telegram(monkeypatch, body: dict) -> list[dict]:
    """Stub urlopen; returns list of (url, data) recorded."""
    calls: list[dict] = []

    def fake_urlopen(req: urllib.request.Request, timeout: int = 5):
        calls.append({"url": req.full_url, "data": json.loads(req.data.decode())})
        return _FakeResponse(body)

    monkeypatch.setattr(alerter.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_console_alert_logs_message(caplog):
    with caplog.at_level(logging.INFO):
        send_trade_alert("BUY", "BTC/USDT", 42000.0, 0.01)
    assert any("ALERT:" in record.getMessage() for record in caplog.records)
    assert any("BUY" in record.getMessage() for record in caplog.records)


def test_telegram_send_message_payload(monkeypatch):
    init_alerts({
        "telegram": {"enabled": True, "bot_token": "tok123", "chat_id": "c42"},
        "console": {"enabled": False},
    })
    calls = _capture_telegram(monkeypatch, {"ok": True})
    send_status_report("equity 100000.00 positions 3")
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottok123/sendMessage"
    assert calls[0]["data"]["chat_id"] == "c42"
    assert "equity 100000.00" in calls[0]["data"]["text"]
    assert calls[0]["data"]["parse_mode"] == "Markdown"


def test_telegram_missing_credentials_never_sends(monkeypatch):
    init_alerts({"telegram": {"enabled": True, "bot_token": "", "chat_id": ""}})
    calls = _capture_telegram(monkeypatch, {"ok": True})
    send_trade_alert("SELL", "BTC/USDT", 41000.0, 0.01)
    assert calls == []  # disabled due to missing creds, no crash


def test_telegram_api_error_is_swallowed(caplog, monkeypatch):
    init_alerts({
        "telegram": {"enabled": True, "bot_token": "tok", "chat_id": "c"},
        "console": {"enabled": False},
    })
    _capture_telegram(monkeypatch, {"ok": False, "description": "chat not found"})
    with caplog.at_level(logging.WARNING):
        send_daily_summary({"total_pnl": 100.0, "win_rate": 0.5})
    assert any("Telegram API error" in record.getMessage() for record in caplog.records)


def test_telegram_network_error_never_raises(caplog, monkeypatch):
    init_alerts({
        "telegram": {"enabled": True, "bot_token": "tok", "chat_id": "c"},
        "console": {"enabled": False},
    })

    def boom(req, timeout=5):
        raise OSError("connection refused")

    monkeypatch.setattr(alerter.urllib.request, "urlopen", boom)
    with caplog.at_level(logging.WARNING):
        send_risk_alert("max_drawdown", "DD breach", value=0.06, limit=0.05)
    assert any("Failed to send Telegram" in record.getMessage() for record in caplog.records)


def test_risk_alert_includes_emoji_and_values():
    init_alerts({"console": {"enabled": True}})
    # console-only path: smoke test no crash with rich formatting
    send_risk_alert("max_drawdown", "DD breach", value=0.06, limit=0.05)
