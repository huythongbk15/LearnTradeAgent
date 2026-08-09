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

import sys
import os
import asyncio
import json
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
from trading_agent.risk.portfolio_risk import PortfolioRiskManager, DrawdownConfig
from live_config import (
    SYMBOLS_ALPACA as SYMBOLS, STRATEGY_PARAMS, LOOKBACK,
    ATR_SL_MULT, ATR_SL_WINDOW, DRAWDOWN_TIERS,
)

# ── Risk guard (P0) ────────────────────────────────────────────────────
# (config: ATR_SL_MULT, ATR_SL_WINDOW, DRAWDOWN_TIERS ở live_config.py)
PEAK_STATE_FILE = "data/live_peak_equity.json"   # persist peak equity giữa các lần chạy

DRY_RUN = "--execute" not in sys.argv

def load_peak_equity() -> float:
    """Đọc peak equity đã lưu (None nếu chưa có)."""
    try:
        with open(PEAK_STATE_FILE) as f:
            return float(json.load(f).get("peak", 0.0))
    except (OSError, ValueError, KeyError):
        return 0.0

def save_peak_equity(peak: float) -> None:
    os.makedirs("data", exist_ok=True)
    with open(PEAK_STATE_FILE, "w") as f:
        json.dump({"peak": round(peak, 2), "updated": datetime.now().isoformat()}, f)

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
    atr = float(last["atr"][0]) if "atr" in last.columns else None
    
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
        "atr": atr,
        "n_buy_24h": n_buy,
        "n_sell_24h": n_sell,
    }


def trailing_stop_price(entry_price: float, df: pl.DataFrame) -> float | None:
    """ATR trailing stop = max(initial stop, peak(highs, window) - k*ATR).

    - Không cần state file: peak lấy từ highs của window gần nhất.
    - Stop chỉ được nâng lên (không hạ xuống dưới initial stop).
    - Trả None nếu thiếu dữ liệu ATR.
    """
    if "atr" not in df.columns or "high" not in df.columns:
        return None
    atr_arr = df["atr"].to_numpy()
    atr = float(atr_arr[~np.isnan(atr_arr)][-1]) if np.isfinite(atr_arr[-1]) else None
    if atr is None or atr <= 0:
        return None
    peak = float(df["high"][-ATR_SL_WINDOW:].max())
    trail = peak - ATR_SL_MULT * atr
    initial = entry_price - ATR_SL_MULT * atr
    return max(trail, initial)


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
    print("\n✅ Alpaca Paper connected")
    print(f"  Equity: ${acct['equity']:,.2f} | Cash: ${acct['cash']:,.2f}")

    # ── Risk guard: portfolio drawdown controller ─────────────────────
    pm = PortfolioRiskManager(DrawdownConfig(tiers=DRAWDOWN_TIERS))
    peak_seen = load_peak_equity()
    if peak_seen > 0:
        pm.update_equity(peak_seen)      # seed peak từ các lần chạy trước
    pm.update_equity(float(acct["equity"]))
    save_peak_equity(pm.peak_equity)
    halted = pm.is_trading_halted()
    scale = pm.position_scale_factor()
    print(f"  🛡 Risk: equity ${float(acct['equity']):,.2f} | peak ${pm.peak_equity:,.2f} | "
          f"DD {pm.current_dd:.1%} | scale {scale:.0%} | "
          f"{'⛔ HALTED (đóng hết vị thế)' if halted else '✅ trading allowed'}")

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
            # Tính indicators TRƯỚC để downstream (ATR stop) dùng được cột atr
            df = EnhancedMaCrossover(STRATEGY_PARAMS).compute_indicators(df)
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

            # ── RISK 1: ATR trailing stop — đóng lệnh nếu phá stop ──
            risk_exit = False
            if has_position and current_qty > 0:
                stop = trailing_stop_price(float(existing["avg_entry_price"]), df)
                if stop is not None:
                    if state["price"] <= stop:
                        risk_exit = True
                        print(f"  🛑 RISK EXIT: giá ${state['price']:,.2f} ≤ ATR stop ${stop:,.2f} "
                              f"(entry ${float(existing['avg_entry_price']):,.2f})")
                    else:
                        print(f"  🛡 ATR trailing stop: ${stop:,.2f} (buffer ${state['price'] - stop:,.2f})")
                else:
                    print("  ⚠️ Không tính được ATR stop (thiếu dữ liệu)")

            if risk_exit:
                decisions.append({
                    "market_symbol": market_symbol,
                    "alpaca_symbol": alpaca_symbol,
                    "action": "SELL",
                    "qty": current_qty,
                    "price": state["price"],
                    "size_pct": alloc,
                    "reason": "ATR_TRAILING_STOP",
                })
                print(f"  → ACTION: SELL {current_qty:.6f} {alpaca_symbol} (ATR trailing stop)")

            # ── RISK 2: portfolio halt — đóng hết, dừng mua ──────────
            elif halted and has_position:
                decisions.append({
                    "market_symbol": market_symbol,
                    "alpaca_symbol": alpaca_symbol,
                    "action": "SELL",
                    "qty": current_qty,
                    "price": state["price"],
                    "size_pct": alloc,
                    "reason": "PORTFOLIO_HALT",
                })
                print(f"  ⛔ HALT: đóng {current_qty:.6f} {alpaca_symbol} (drawdown > 20%)")
            elif halted:
                print(f"  ⛔ HALTED: bỏ qua {alpaca_symbol} (drawdown > 20%)")

            # ── Rebalance vs strategy target state (deadband 5%) ──
            #    target bị scale theo drawdown tier (75/50/25%)
            else:
                target_notional = equity * alloc * scale
                current_notional = current_qty * state["price"]
                delta_usd = target_notional - current_notional
                deadband = alloc * equity * 0.05  # 5% of target — tránh lệnh nhỏ lặt vặt

                if state["state"] == "LONG" and delta_usd > deadband:
                    qty = delta_usd / state["price"]
                    decisions.append({
                        "market_symbol": market_symbol,
                        "alpaca_symbol": alpaca_symbol,
                        "action": "BUY",
                        "qty": qty,
                        "price": state["price"],
                        "size_pct": alloc,
                        "reason": "REBALANCE",
                    })
                    print(f"  → ACTION: BUY {qty:.6f} {alpaca_symbol} "
                          f"(rebalance {current_notional:,.0f} → {target_notional:,.0f} USD, scale {scale:.0%})")
                elif state["state"] == "LONG" and delta_usd < -deadband:
                    decisions.append({
                        "market_symbol": market_symbol,
                        "alpaca_symbol": alpaca_symbol,
                        "action": "SELL",
                        "qty": abs(delta_usd) / state["price"],
                        "price": state["price"],
                        "size_pct": alloc,
                        "reason": "REBALANCE",
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
                        "reason": "STRATEGY_FLAT",
                    })
                    print(f"  → ACTION: SELL {current_qty:.6f} {alpaca_symbol} (strategy flat → close)")
                else:
                    print("  → NO ACTION (state matches)")

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

        # Cap BUY size by REAL available cash (re-fetch before each order)
        # → tránh "insufficient balance" khi nhiều lệnh BUY cùng chu kỳ
        try:
            acct_now = broker.get_account()
            cash_now = float(acct_now["cash"])
        except Exception:
            cash_now = float(acct_now["cash"]) if "acct_now" in dir() else 0.0

        qty = float(d["qty"])
        if round(qty, 6) <= 0:
            print(f"  ⏭️  SKIP — {d['action']} qty {qty:.6f} ≈ 0 (dust position, không đáng lệnh)")
            continue
        if d["action"] == "BUY":
            cost = qty * d["price"]
            if cost > cash_now:
                qty = (cash_now * 0.95) / d["price"]   # buffer 5% cho slippage (market order)
                print(f"  ℹ️  cash-limited: {d['qty']:.6f} → {qty:.6f} (cash ${cash_now:,.2f})")
            if qty <= 0:
                print(f"  ⏭️  SKIP — no available cash (${cash_now:,.2f})")
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
                size=Decimal(str(round(qty, 6))),
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