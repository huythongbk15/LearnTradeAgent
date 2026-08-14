#!/usr/bin/env python3
"""
Local Trading Runner — runs the full trading loop on your machine.

Components:
1. Data Collector - fetch/update OHLCV from exchange
2. Multi-Agent Analysis - Technical + Sentiment + Risk → Trader signal
3. Execution Engine - PaperExchange (safe, no real money)
4. Risk Controller - drawdown limits, daily loss limits, stop-loss
5. Telegram Notifications - trade alerts, PnL updates, circuit breaker

Usage:
    python scripts/trade_local.py

Environment:
    TELEGRAM_BOT_TOKEN=xxx
    TELEGRAM_CHAT_ID=xxx
    EXCHANGE=binance
    SYMBOLS=BTC/USDT,ETH/USDT,SOL/USDT
    TIMEFRAME=1h
    INITIAL_CAPITAL=10000
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# Load .env files (project .env + user ~/.env)
load_dotenv()  # .env in current dir
load_dotenv(Path.home() / ".env")  # ~/.env

# ── Add project root to path ─────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ── Local imports (lazy to speed up startup) ─────────────────────────────
def _import_modules():
    """Lazy import heavy modules after path setup."""
    from trading_agent.agents.orchestrator import Orchestrator
    from trading_agent.config.loader import config
    from trading_agent.data.collector import Collector
    from trading_agent.execution.engine import ExecutionEngine
    from trading_agent.execution.risk_controller import RiskController
    from trading_agent.log_config import get_logger

    return config, Collector, Orchestrator, ExecutionEngine, RiskController, get_logger


# ── Config from env ──────────────────────────────────────────────────────
EXCHANGE = os.getenv("EXCHANGE", "binance")
SYMBOLS = [
    s.strip() for s in os.getenv("SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT").split(",")
]
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOOP_INTERVAL = int(os.getenv("LOOP_INTERVAL", "300"))  # seconds (5 min)
DATA_UPDATE_INTERVAL = int(os.getenv("DATA_UPDATE_INTERVAL", "3600"))  # 1 hour
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.50"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.05"))
COOLDOWN_HOURS = float(os.getenv("COOLDOWN_HOURS", "24"))
MAX_POS_SIZE_PCT = float(os.getenv("MAX_POS_SIZE_PCT", "0.25"))  # max % per trade
# Skip LLM calls for fast local testing (uses rule-based fallbacks)
USE_LLM = os.getenv("USE_LLM", "true").lower() != "false"

# ── Global state ─────────────────────────────────────────────────────────
shutdown = False
last_data_update = 0
last_pnl_notify = 0
run_once = False  # set True when --once flag is passed


def signal_handler(signum, frame):
    global shutdown
    print(f"\n🛑 Signal {signum} received — graceful shutdown…")
    shutdown = True


def send_telegram(text: str) -> bool:
    """Send Telegram message. Returns True if successful."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"⚠️  Telegram send failed: {e}")
        return False


def notify_trade(
    symbol: str, side: str, qty: float, price: float, pnl: float | None = None
):
    """Format and send trade notification."""
    emoji = "🟢" if side == "BUY" else "🔴"
    pnl_str = f" | PnL: {pnl:+.2f}" if pnl is not None else ""
    text = (
        f"{emoji} **{side}** {qty:.4f} {symbol} @ ${price:,.2f}{pnl_str}\n"
        f"💰 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    send_telegram(text)


def notify_status(engine, risk_ctl):
    """Send periodic portfolio status update."""
    summary = engine.get_summary()
    risk = risk_ctl.get_status()
    text = (
        f"📊 **Portfolio Update**\n"
        f"Equity: ${summary['equity']:,.2f} ({summary['return_pct']:+.2f}%)\n"
        f"Cash: ${summary['cash']:,.2f} | Positions: ${summary['positions_value']:,.2f}\n"
        f"Open: {summary['open_positions']} | Trades: {summary['total_trades']}\n"
        f"Drawdown: {risk['drawdown_pct']:.1f}% (limit: {risk['max_drawdown_limit_pct']:.0f}%)\n"
        f"Daily Loss: {risk['daily_loss_pct']:.1f}% (limit: {risk['daily_loss_limit_pct']:.0f}%)\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    send_telegram(text)


def notify_circuit_breaker(reason: str):
    text = (
        f"🚨 **CIRCUIT BREAKER ACTIVATED**\n"
        f"Reason: {reason}\n"
        f"All positions closed. Trading halted.\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    send_telegram(text)


def fetch_and_update_data(
    collector: Any, symbols: list[str], timeframe: str
) -> dict[str, Any]:
    """Fetch latest data for all symbols. Returns dict of symbol -> latest_price."""
    prices = {}
    for symbol in symbols:
        try:
            # Incremental update (only new candles)
            df = collector.update_ohlcv(symbol, timeframe)
            if not df.is_empty():
                latest_close = float(df["close"].tail(1).item())
                prices[symbol] = latest_close
                print(f"  📈 {symbol}: ${latest_close:,.2f} ({len(df)} new candles)")
            else:
                # No new data, get last known price from storage
                from trading_agent.data.storage import load_ohlcv

                stored = load_ohlcv(EXCHANGE, symbol, timeframe)
                if not stored.is_empty():
                    prices[symbol] = float(stored["close"].tail(1).item())
        except Exception as e:
            print(f"  ⚠️  {symbol}: {e}")
    return prices


def run_agent_analysis(
    orchestrator: Any, symbol: str, timeframe: str, equity: float
) -> Any:
    """Run multi-agent analysis for a symbol."""
    try:
        report = orchestrator.analyze(
            symbol=symbol,
            timeframe=timeframe,
            current_position_pct=0.0,  # will calculate from engine
            portfolio_value=equity,
        )
        return report
    except Exception as e:
        print(f"  ❌ Agent analysis failed for {symbol}: {e}")
        return None


def process_signals(
    engine: Any, risk_ctl: Any, signals: dict[str, Any], prices: dict[str, float]
):
    """Process agent signals and execute trades."""
    for symbol, report in signals.items():
        if not report:
            continue

        decision = report.final_decision
        signal = decision.signal
        confidence = decision.confidence
        risk_level = decision.risk_level

        print(f"  🤖 {symbol}: {signal} (conf={confidence:.0%}, risk={risk_level})")

        # Create AgentMessage for engine
        from trading_agent.agents.base import AgentMessage

        msg = AgentMessage(
            role="trader",
            signal=signal,
            confidence=confidence,
            reasoning=decision.reasoning,
            details={"symbol": symbol},
            max_position_size_pct=MAX_POS_SIZE_PCT,
            risk_level=risk_level,
        )

        # Execute
        orders = engine.execute_signal(msg)

        # Handle results
        for order in orders:
            price = prices.get(symbol, order.avg_fill_price or 0)
            if order.side.value == "buy":
                notify_trade(symbol, "BUY", order.filled_amount or order.amount, price)
            else:
                # Calculate PnL for sell
                pos = engine.exchange.get_position(symbol)
                pnl = pos.unrealized_pnl if pos else None
                notify_trade(
                    symbol, "SELL", order.filled_amount or order.amount, price, pnl
                )


def main_loop():
    """Main trading loop."""
    global last_data_update, last_pnl_notify, shutdown

    # Import heavy modules
    config, Collector, Orchestrator, ExecutionEngine, RiskController, get_logger = (
        _import_modules()
    )
    logger = get_logger("trade_local")

    # Initialize components
    print("🚀 Starting Local Trading Runner")
    print(f"   Exchange: {EXCHANGE}")
    print(f"   Symbols: {', '.join(SYMBOLS)}")
    print(f"   Timeframe: {TIMEFRAME}")
    print(f"   Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"   Loop: {LOOP_INTERVAL}s | Data update: {DATA_UPDATE_INTERVAL}s")
    print()

    # Send startup notification
    send_telegram(
        f"🚀 **Trading Bot Started**\n"
        f"Exchange: {EXCHANGE}\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Capital: ${INITIAL_CAPITAL:,.2f}\n"
        f"Mode: Paper Trading\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )

    # Initialize
    collector = Collector(EXCHANGE)
    orchestrator = Orchestrator()
    engine = ExecutionEngine(
        exchange_name=EXCHANGE,
        initial_capital=INITIAL_CAPITAL,
    )
    risk_ctl = RiskController(
        engine,
        max_drawdown_pct=MAX_DRAWDOWN_PCT,
        daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
        max_position_pct=MAX_POSITION_PCT,
        default_stop_loss_pct=STOP_LOSS_PCT,
        cooldown_hours=COOLDOWN_HOURS,
    )

    # Initial data fetch
    print("📥 Initial data fetch…")
    fetch_and_update_data(collector, SYMBOLS, TIMEFRAME)
    last_data_update = time.time()

    # Set stop-loss on existing positions
    risk_ctl.set_stop_loss_on_all_positions(STOP_LOSS_PCT)

    print("✅ Ready — entering main loop\n")

    while not shutdown:
        loop_start = time.time()

        try:
            # 1. Update market data periodically
            now = time.time()
            if now - last_data_update >= DATA_UPDATE_INTERVAL:
                print(f"📡 {datetime.now().strftime('%H:%M:%S')} Updating market data…")
                prices = fetch_and_update_data(collector, SYMBOLS, TIMEFRAME)
                last_data_update = now
            else:
                # Get latest prices from cache/storage
                prices = {}
                for symbol in SYMBOLS:
                    from trading_agent.data.storage import load_ohlcv

                    try:
                        df = load_ohlcv(EXCHANGE, symbol, TIMEFRAME)
                        if not df.is_empty():
                            prices[symbol] = float(df["close"].tail(1).item())
                    except Exception:
                        pass

            # 2. Update engine with latest prices
            if prices:
                engine.update_prices(prices)

            # 3. Run risk checks
            alerts = risk_ctl.check_all()
            if alerts:
                for alert in alerts:
                    print(f"  ⚠️  {alert}")
                    if "CIRCUIT BREAKER" in alert:
                        notify_circuit_breaker(alert)

            # 4. Get current equity for position sizing
            equity = engine.exchange.get_total_equity()

            # 5. Run agent analysis for each symbol
            print(f"🤖 {datetime.now().strftime('%H:%M:%S')} Running agent analysis…")
            signals = {}
            for symbol in SYMBOLS:
                report = run_agent_analysis(orchestrator, symbol, TIMEFRAME, equity)
                signals[symbol] = report

            # 6. Process signals → execute trades
            process_signals(engine, risk_ctl, signals, prices)

            # 7. Periodic PnL notification
            if now - last_pnl_notify >= 3600:  # every hour
                notify_status(engine, risk_ctl)
                last_pnl_notify = now

            # 8. Print summary
            summary = engine.get_summary()
            risk = risk_ctl.get_status()
            print(
                f"💼 Equity: ${summary['equity']:,.2f} ({summary['return_pct']:+.2f}%) | "
                f"Pos: {summary['open_positions']} | "
                f"DD: {risk['drawdown_pct']:.1f}% | "
                f"Daily: {risk['daily_loss_pct']:.1f}%"
            )

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            print(f"  ❌ Loop error: {e}")

        # Sleep until next loop
        elapsed = time.time() - loop_start
        sleep_time = max(1, LOOP_INTERVAL - elapsed)
        if shutdown:
            break
        time.sleep(sleep_time)

        # Exit after one iteration if --once flag
        if run_once:
            break

    # Graceful shutdown
    print("\n🛑 Shutting down…")
    summary = engine.get_summary()
    send_telegram(
        f"🛑 **Trading Bot Stopped**\n"
        f"Final Equity: ${summary['equity']:,.2f} ({summary['return_pct']:+.2f}%)\n"
        f"Total Trades: {summary['total_trades']}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    print("✅ Stopped cleanly")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="Local Trading Runner")
    parser.add_argument(
        "--once", action="store_true", help="Run one iteration and exit"
    )
    parser.add_argument(
        "--test-notify", action="store_true", help="Send test Telegram message"
    )
    args = parser.parse_args()

    if args.test_notify:
        ok = send_telegram("✅ Test message from trading bot")
        print("Telegram test:", "OK" if ok else "FAILED")
        sys.exit(0)

    if args.once:
        LOOP_INTERVAL = 1
        DATA_UPDATE_INTERVAL = 0
        globals()["run_once"] = True

    try:
        main_loop()
    except KeyboardInterrupt:
        pass
    finally:
        if args.once:
            print("\n✅ Single run complete")
