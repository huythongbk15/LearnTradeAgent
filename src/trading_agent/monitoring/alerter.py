"""
Alerting — Telegram / console alerts for trades, risks, and daily summaries.

Configure via config.yaml:
    alerts:
      telegram:
        enabled: true
        bot_token: "..."    # from @BotFather
        chat_id: "123456"
      console:
        enabled: true
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from trading_agent.log_config import get_logger

logger = get_logger(__name__)


@dataclass
class AlertConfig:
    """Alert configuration from config.yaml."""

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    console_enabled: bool = True


# Global config, set once during init
_alert_config: AlertConfig = AlertConfig()


def init_alerts(config: dict | None = None) -> None:
    """Initialize alert system with config dict.

    Expected config structure:
        alerts:
          telegram:
            enabled: bool
            bot_token: str   (or env TELEGRAM_BOT_TOKEN)
            chat_id: str     (or env TELEGRAM_CHAT_ID)
          console:
            enabled: bool
    """
    global _alert_config
    conf = config or {}

    telegram_cfg = conf.get("telegram", {})
    _alert_config = AlertConfig(
        telegram_enabled=telegram_cfg.get("enabled", False),
        telegram_bot_token=telegram_cfg.get("bot_token", "")
        or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=telegram_cfg.get("chat_id", "")
        or os.environ.get("TELEGRAM_CHAT_ID", ""),
        console_enabled=conf.get("console", {}).get("enabled", True),
    )
    if _alert_config.telegram_enabled:
        if not _alert_config.telegram_bot_token or not _alert_config.telegram_chat_id:
            logger.warning("Telegram alerts enabled but missing bot_token or chat_id")
            _alert_config.telegram_enabled = False


# ---------------------------------------------------------------------------
# Alert functions
# ---------------------------------------------------------------------------


def send_trade_alert(
    action: str,  # BUY / SELL / STOP_LOSS
    symbol: str,
    price: float,
    amount: float,
    pnl: float | None = None,
    reason: str | None = None,
) -> None:
    """Send a trade execution alert."""
    emoji = {"BUY": "🟢", "SELL": "🔴", "STOP_LOSS": "🛑"}.get(action, "🔵")
    msg = f"{emoji} *{action}* {symbol}\nPrice: `${price:.2f}`\nAmount: `{amount:.6f}`"
    if pnl is not None:
        pnl_emoji = "📈" if pnl > 0 else "📉"
        msg += f"\nP&L: {pnl_emoji} `${pnl:.2f}`"
    if reason:
        msg += f"\nReason: `{reason}`"
    _send(msg)


def send_risk_alert(
    alert_type: str,  # max_drawdown / daily_loss / circuit_breaker
    message: str,
    value: float | None = None,
    limit: float | None = None,
) -> None:
    """Send a risk/alert notification."""
    emoji_map = {
        "max_drawdown": "🚨",
        "daily_loss": "⚠️",
        "circuit_breaker": "🛑",
        "cooldown": "⏳",
    }
    emoji = emoji_map.get(alert_type, "🔔")
    msg = f"{emoji} *Risk Alert: {alert_type}*\n{message}"
    if value is not None:
        msg += f"\nCurrent: `{value:.2%}`"
    if limit is not None:
        msg += f"\nLimit: `{limit:.2%}`"
    _send(msg)


def send_daily_summary(stats: dict[str, Any]) -> None:
    """Send a daily performance summary."""
    emoji = "📊" if stats.get("total_pnl", 0) >= 0 else "📉"
    msg = (
        f"{emoji} *Daily Summary*\n"
        f"Trades: `{stats.get('total_trades', 0)}`\n"
        f"Win Rate: `{stats.get('win_rate', 0):.1%}`\n"
        f"P&L: `${stats.get('total_pnl', 0):.2f}`\n"
        f"Sharpe: `{stats.get('sharpe_ratio', 0):.2f}`\n"
        f"Max DD: `{stats.get('max_drawdown_pct', 0):.2f}%`"
    )
    _send(msg)


def send_status_report(msg: str) -> None:
    """Send a free-form status message (live equity/positions heartbeat)."""
    _send(msg)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _send(message: str) -> None:
    """Send a message through all enabled channels."""
    if _alert_config.console_enabled:
        logger.info("ALERT: %s", message.replace("*", "").replace("`", ""))

    if _alert_config.telegram_enabled:
        _send_telegram(message)


def _send_telegram(message: str) -> None:
    """Send a message via Telegram Bot API."""
    bot_token = _alert_config.telegram_bot_token
    chat_id = _alert_config.telegram_chat_id
    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
            if not result.get("ok"):
                logger.warning("Telegram API error: %s", result)
    except Exception as e:
        logger.warning("Failed to send Telegram message: %s", e)
