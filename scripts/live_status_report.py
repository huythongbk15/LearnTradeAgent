#!/usr/bin/env python3
"""
Báo cáo status hệ thống live trading lên Telegram (P2.1):
equity, cash, positions + giá hiện tại, peak/drawdown, trạng thái risk.

Cron gợi ý (báo cáo tối 20:00):
    0 20 * * * cd /home/huythong/.qwenpaw/workspaces/trading && python scripts/live_status_report.py >/dev/null 2>&1
"""

import sys
import os
import asyncio
import json
from datetime import datetime

sys.path.insert(0, "src")

from dotenv import load_dotenv

load_dotenv(".env")

import ccxt

from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig
from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.monitoring.alerter import init_alerts, send_status_report
from live_config import SYMBOLS_ALPACA

PEAK_STATE_FILE = "data/live_peak_equity.json"


def load_peak() -> dict:
    try:
        with open(PEAK_STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    init_alerts()

    adapter = AlpacaAdapter(
        AlpacaConfig(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_API_SECRET"],
            paper=True,
        )
    )
    asyncio.run(adapter.connect())
    broker = LiveBroker("alpaca", adapter)

    acct = broker.get_account()
    equity = float(acct["equity"])
    cash = float(acct["cash"])
    positions = broker.get_positions()

    # Giá hiện tại từ Binance public (cùng nguồn với signal)
    try:
        ex = ccxt.binance({"enableRateLimit": True})
        prices = {}
        for market_symbol, _, _ in SYMBOLS_ALPACA:
            try:
                prices[market_symbol.split("/")[0]] = float(
                    ex.fetch_ticker(market_symbol)["last"]
                )
            except Exception:
                continue
    except Exception:
        prices = {}

    # Peak / drawdown
    peak_state = load_peak()
    peak = float(peak_state.get("peak", equity))
    dd = max(0.0, (peak - equity) / peak) if peak else 0.0
    skipped = "⏭️" if dd >= 0.20 else ("⚠️" if dd >= 0.10 else "✅")

    lines = [
        f"📊 *Live Status — {datetime.now():%Y-%m-%d %H:%M}*",
        f"Equity: `${equity:,.2f}` | Cash: `${cash:,.2f}`",
        f"Peak: `${peak:,.2f}` | DD: `{dd:.1%}` {skipped}",
        "",
        f"*Positions ({len(positions)}):*",
    ]
    if not positions:
        lines.append("  (không có vị thế)")
    for p in positions:
        sym = p["symbol"].split("/")[0]
        qty = float(p["qty"])
        avg = float(p["avg_entry_price"])
        cur = prices.get(sym)
        if cur:
            pnl = (cur - avg) * qty
            lines.append(
                f"  • {sym}: {qty:,.4f} @ ${avg:,.2f} → ${cur:,.2f} ({pnl:+,.0f}$)"
            )
        else:
            lines.append(f"  • {sym}: {qty:,.4f} @ ${avg:,.2f}")

    msg = "\n".join(lines)
    print(msg)
    send_status_report(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
