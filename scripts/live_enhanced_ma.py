#!/usr/bin/env python3
"""
LIVE Trading: SOL, BTC, AVAX, BNB — Single 1h Enhanced MA (10,30,40)
Executes on Alpaca Paper

Strategy replay: simulate position state over recent history (same as backtest),
then sync with Alpaca:
  - Desired state LONG + flat     → BUY
  - Desired state FLAT + in pos   → SELL (close)
  - Desired state == current      → HOLD
"""

import sys, os, asyncio
from decimal import Decimal
sys.path.insert(0, 'src')

from dotenv import load_dotenv
load_dotenv('.env')

import polars as pl
import ccxt
import numpy as np
from datetime import datetime

from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover
from trading_agent.exchanges.models import (
    Symbol, AssetClass, MarketType, Order, OrderSide, OrderType, TimeInForce,
)
from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig
from trading_agent.exchanges.live_broker import LiveBroker

# ── Config ─────────────────────────────────────────────────────────────
# NOTE: Alpaca does NOT support BNB — replace with ETH (liquid, supported)
SYMBOLS = [
    ("BTC/USDT", "BTCUSD", 0.30),   # 30% capital
    ("SOL/USDT", "SOLUSD", 0.25),   # 25%
    ("AVAX/USDT", "AVAXUSD", 0.20), # 20%
    ("ETH/USDT", "ETHUSD", 0.25),   # 25% (BNB not on Alpaca)
]
TIMEFRAME = "1h"
LOOKBACK = 1000
# Verified champion: fast_period=20, slow_period=80, adx_threshold=40
# (earlier "10,30,40" tests used wrong keys — actual result was 20/80 all along)
STRATEGY_PARAMS = {"fast_period": 20, "slow_period": 80, "adx_threshold": 40}
DRY_RUN = "--execute" not in sys.argv

def compute_state(df: pl.DataFrame) -> dict:
    """Replay strategy over history, return current desired state + signals."""
    strat = EnhancedMaCrossover(STRATEGY_PARAMS)
    df = strat.compute_indicators(df)
    sig = strat.generate_signals(df).to_numpy()
    
    # Replay: position = enter on +1, exit on -1 (like backtest engine)
    pos = np.zeros(len(sig), dtype=np.int8)
    in_pos = False
    for i in range(len(sig)):
        if not in_pos and sig[i] == 1:
            in_pos = True
        elif in_pos and sig[i] == -1:
            in_pos = False
        pos[i] = 1 if in_pos else 0
    
    last_state = "LONG" if in_pos else "FLAT"
    
    # Indicator snapshot on last bar
    last = df.tail(1)
    ma_fast = float(last[f"ma_{STRATEGY_PARAMS['fast_period']}"][0]) if f"ma_{STRATEGY_PARAMS['fast_period']}" in last.columns else None
    ma_slow = float(last[f"ma_{STRATEGY_PARAMS['slow_period']}"][0]) if f"ma_{STRATEGY_PARAMS['slow_period']}" in last.columns else None
    adx = float(last["adx"][0]) if "adx" in last.columns else None
    trend_up = bool(last["trend_up"][0]) if "trend_up" in last.columns else None
    price = float(last["close"][0])
    
    # Recent crossover events (last 24h)
    recent_sigs = sig[-24:]
    n_buy = int((recent_sigs == 1).sum())
    n_sell = int((recent_sigs == -1).sum())
    
    return {
        "state": last_state,
        "price": price,
        "ma_fast": ma_fast,
        "ma_slow": ma_slow,
        "adx": adx,
        "trend_up": trend_up,
        "n_buy_24h": n_buy,
        "n_sell_24h": n_sell,
    }


def get_recent_df(symbol: str) -> pl.DataFrame:
    """Fetch recent 1h data from Binance via CCXT."""
    exchange = ccxt.binance({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    if not ohlcv:
        raise RuntimeError(f"No data for {symbol}")
    return pl.DataFrame({
        "timestamp": [datetime.fromtimestamp(b[0]/1000) for b in ohlcv],
        "open": [b[1] for b in ohlcv],
        "high": [b[2] for b in ohlcv],
        "low": [b[3] for b in ohlcv],
        "close": [b[4] for b in ohlcv],
        "volume": [b[5] for b in ohlcv],
    }).sort("timestamp")


def main():
    print("=" * 80)
    print("🚀 LIVE PAPER TRADING — Single 1h Enhanced MA (20,80,40)")
    print(f"  Mode: {'DRY RUN (no orders placed)' if DRY_RUN else 'EXECUTE (real paper orders)'}")
    print("=" * 80)

    # Connect Alpaca
    adapter = AlpacaAdapter(AlpacaConfig(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_API_SECRET"],
        paper=True,
    ))
    asyncio.run(adapter.connect())
    broker = LiveBroker("alpaca", adapter)

    acct = broker.get_account()
    print(f"\n✅ Alpaca Paper connected")
    print(f"  Equity: ${acct['equity']:,.2f} | Cash: ${acct['cash']:,.2f}")

    positions = broker.get_positions()
    pos_map = {p["symbol"].split("/")[0]: p for p in positions}
    print(f"  Open positions: {len(positions)}")
    for p in positions:
        print(f"    {p['symbol']}: {p['qty']} @ ${p['avg_entry_price']:.2f}")

    # ── Compute signals & decisions ───────────────────────────────────
    print("\n" + "=" * 80)
    print("📡 SIGNAL COMPUTATION")
    print("=" * 80)

    equity = float(acct["equity"])
    decisions = []

    for market_symbol, alpaca_symbol, alloc in SYMBOLS:
        print(f"\n--- {market_symbol} (Alpaca: {alpaca_symbol}) ---")
        try:
            df = get_recent_df(market_symbol)
            state = compute_state(df)

            existing = pos_map.get(alpaca_symbol)
            has_position = existing is not None
            current_qty = float(existing["qty"]) if has_position else 0.0
            current_state = "LONG" if has_position else "FLAT"

            print(f"  Price: ${state['price']:,.2f}")
            print(f"  MA({STRATEGY_PARAMS['fast_period']}): {state['ma_fast']:.2f} | MA({STRATEGY_PARAMS['slow_period']}): {state['ma_slow']:.2f} "
                  f"({'BULL' if state['ma_fast'] > state['ma_slow'] else 'BEAR'})")
            print(f"  ADX: {state['adx']:.1f} (threshold 40) | Trend: {'UP' if state['trend_up'] else 'DOWN'}")
            print(f"  Crossovers last 24h: {state['n_buy_24h']} BUY / {state['n_sell_24h']} SELL")
            print(f"  Strategy state: {state['state']} | Alpaca: {current_state}")

            # ── Rebalance vs strategy target state ──
            target_notional = equity * alloc
            current_notional = current_qty * state["price"]
            delta_usd = target_notional - current_notional

            if state["state"] == "LONG" and delta_usd > alloc * equity * 0.05:  # >5% off target
                qty = delta_usd / state["price"]
                decisions.append({
                    "market_symbol": market_symbol,
                    "alpaca_symbol": alpaca_symbol,
                    "action": "BUY",
                    "qty": qty,
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: BUY {qty:.6f} {alpaca_symbol} "
                      f"(rebalance {current_notional:,.0f} → {target_notional:,.0f} USD)")
            elif state["state"] == "LONG" and delta_usd < 0:
                decisions.append({
                    "market_symbol": market_symbol,
                    "alpaca_symbol": alpaca_symbol,
                    "action": "SELL",
                    "qty": abs(delta_usd) / state["price"],
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: SELL (trim excess {current_notional:,.0f} → {target_notional:,.0f} USD)")
            elif state["state"] == "FLAT" and has_position:
                decisions.append({
                    "market_symbol": market_symbol,
                    "alpaca_symbol": alpaca_symbol,
                    "action": "SELL",
                    "qty": current_qty,
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: SELL {current_qty:.6f} {alpaca_symbol} (strategy flat → close)")
            else:
                print(f"  → NO ACTION (state matches)")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # ── Execute ────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🎯 EXECUTION")
    print("=" * 80)

    if not decisions:
        print("\n[dim]No trades to execute — all positions in sync[/dim]")
        return

    for d in decisions:
        print(f"\n--- {d['market_symbol']} → {d['action']} {d['qty']:.6f} ---")
        if DRY_RUN:
            print(f"  [DRY-RUN] Would {d['action']} {d['qty']:.6f} {d['alpaca_symbol']} @ ${d['price']:.2f}")
            continue

        try:
            symbol = Symbol(
                base=d["alpaca_symbol"],
                quote="USD",
                asset_class=AssetClass.CRYPTO,
                market_type=MarketType.SPOT,
                exchange="alpaca",
            )
            order = Order(
                id="",
                symbol=symbol,
                side=OrderSide.BUY if d["action"] == "BUY" else OrderSide.SELL,
                type=OrderType.MARKET,
                size=Decimal(str(round(d["qty"], 6))),
                time_in_force=TimeInForce.GTC,
            )
            result = broker.place_order(order)
            print(f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} → {result['status']}")
            if result.get("error"):
                print(f"  ⚠️  {result['error']}")
        except Exception as e:
            print(f"  ❌ Order failed: {e}")

    # ── Final state ────────────────────────────────────────────────────
    if not DRY_RUN:
        print("\n" + "=" * 80)
        print("📊 FINAL STATE")
        print("=" * 80)
        acct = broker.get_account()
        positions = broker.get_positions()
        print(f"  Equity: ${acct['equity']:,.2f}")
        print(f"  Positions: {len(positions)}")
        for p in positions:
            print(f"    {p['symbol']}: {p['qty']} @ ${p['avg_entry_price']:.2f}")
        print("\n✅ Xem dashboard: https://app.alpaca.markets/paper/dashboard/positions")


if __name__ == "__main__":
    main()