#!/usr/bin/env python3
"""
LIVE Trading on Binance SPOT (real money!) — Single 1h Enhanced MA (20,80,40)

Cách dùng:
    python scripts/live_enhanced_ma_binance.py              # DRY-RUN (mặc định, không lệnh)
    python scripts/live_enhanced_ma_binance.py --execute    # THẬT — đặt lệnh bằng tiền thật

⚠️  BINANCE LÀ TIỀN THẬT. Chỉ chạy --execute khi đã hiểu rõ rủi ro:
    - Key API chỉ cần quyền: đọc + giao dịch spot; TẮT quyền rút tiền (withdraw).
    - Chiến lược trend-following có thể giữ lệnh lâu và chịu drawdown sâu.
"""
import sys
import os
import asyncio
import argparse
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
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.live_broker import LiveBroker
from live_config import STRATEGY_PARAMS, LOOKBACK

# ── Config ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser()
_parser.add_argument("--execute", action="store_true", help="Đặt lệnh thật (mặc định dry-run)")
_parser.add_argument("--testnet", action="store_true", help="Dùng Binance Spot Testnet (tiền ảo)")
_parser.add_argument("--symbols", default="BTC/USDT,SOL/USDT,AVAX/USDT", help="Comma-separated symbols")
_parser.add_argument("--weights", default="40,30,30", help="Comma-separated allocation weights (%)")
_args = _parser.parse_args()
DRY_RUN = not _args.execute
TESTNET = _args.testnet

# ── SYMBOLS từ CLI (weights tự chuẩn hoá về 100%) ─────────────────────────
_cli_syms = [s.strip() for s in _args.symbols.split(",") if s.strip()]
_cli_ws = []
for w in _args.weights.split(","):
    w = w.strip()
    if w:
        _cli_ws.append(float(w))
while len(_cli_ws) < len(_cli_syms):
    _cli_ws.append(20.0)
_cli_ws = _cli_ws[:len(_cli_syms)]
_total_w = sum(_cli_ws) or 1.0
SYMBOLS = [(s, w / _total_w) for s, w in zip(_cli_syms, _cli_ws)]


def compute_state(df: pl.DataFrame) -> dict:
    """Replay strategy over history, return current desired state + signals."""
    strat = EnhancedMaCrossover(STRATEGY_PARAMS)
    df = strat.compute_indicators(df)
    sig = strat.generate_signals(df).to_numpy()

    pos = np.zeros(len(sig), dtype=np.int8)
    in_pos = False
    for i in range(len(sig)):
        if not in_pos and sig[i] == 1:
            in_pos = True
        elif in_pos and sig[i] == -1:
            in_pos = False
        pos[i] = 1 if in_pos else 0

    last_state = "LONG" if in_pos else "FLAT"

    last = df.tail(1)
    ma_fast = float(last[f"ma_{STRATEGY_PARAMS['fast_period']}"][0]) if f"ma_{STRATEGY_PARAMS['fast_period']}" in last.columns else None
    ma_slow = float(last[f"ma_{STRATEGY_PARAMS['slow_period']}"][0]) if f"ma_{STRATEGY_PARAMS['slow_period']}" in last.columns else None
    adx = float(last["adx"][0]) if "adx" in last.columns else None
    trend_up = bool(last["trend_up"][0]) if "trend_up" in last.columns else None
    price = float(last["close"][0])

    recent_sigs = sig[-24:]
    return {
        "state": last_state,
        "price": price,
        "ma_fast": ma_fast,
        "ma_slow": ma_slow,
        "adx": adx,
        "trend_up": trend_up,
        "n_buy_24h": int((recent_sigs == 1).sum()),
        "n_sell_24h": int((recent_sigs == -1).sum()),
    }


def get_recent_df(symbol: str) -> pl.DataFrame:
    # HYBRID: signal luôn dùng dữ liệu Binance PUBLIC (live data, 1000+ candles)
    # để chiến lược warmup đầy đủ. Chỉ LỆNH mới đi qua testnet (tiền ảo).
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
    print("🚀 LIVE BINANCE SPOT — Single 1h Enhanced MA (20,80,40)")
    if TESTNET:
        mode = "HYBRID: signal from Binance PUBLIC data | LỆNH trên TESTNET (tiền ảo)"
    else:
        mode = "DRY RUN (no orders placed)" if DRY_RUN else "EXECUTE — TIỀN THẬT"
    print(f"  Mode: {mode}")
    print("=" * 80)

    key_env = "BINANCE_TESTNET_API_KEY" if TESTNET else "BINANCE_API_KEY"
    secret_env = "BINANCE_TESTNET_API_SECRET" if TESTNET else "BINANCE_API_SECRET"
    binance_key = os.environ.get(key_env, "")
    binance_secret = os.environ.get(secret_env, "")
    if not binance_key or not binance_secret:
        if TESTNET:
            print(f"❌ {key_env} / {secret_env} chưa set trong .env")
            print("   → Tạo key testnet tại https://testnet.binance.vision/ (key RIÊNG, không dùng key live)")
            print("   → Bấm nút faucet để nhận USDT ảo, rồi thêm 2 biến trên vào .env")
        else:
            print("❌ BINANCE_API_KEY / BINANCE_API_SECRET chưa set trong .env")
            print("   Tạo key tại https://www.binance.com/en/my/settings/api-management")
            print("   Chỉ bật: Enable Reading + Enable Spot & Futures Trading; TẮT withdrawals.")
        return

    # Connect Binance
    adapter = CCXTAdapter(ExchangeConfig(
        id="binance",
        name="Binance",
        api_key=binance_key,
        secret=binance_secret,
        testnet=TESTNET,
        enable_rate_limit=False,  # rate_limit 1200ms của adapter gây delay ~20s/request trên testnet
        markets=[MarketType.SPOT, MarketType.FUTURES],
        options={"defaultType": "spot"},
    ))
    asyncio.run(adapter.connect())
    broker = LiveBroker("binance", adapter, pricing_symbols=[m for m, _ in SYMBOLS])

    acct = broker.get_account()
    print("\n✅ Binance connected")
    print(f"  Equity: ${acct['equity']:,.2f} | Cash: ${acct['cash']:,.2f}")

    positions = broker.get_positions()
    pos_map = {p['symbol'].split('/')[0]: p for p in positions}
    print(f"  Open positions: {len(positions)}")
    for p in positions:
        print(f"    {p['symbol']}: {p['qty']} @ ${p['avg_entry_price']:.2f}")

    # ── Compute signals & decisions ───────────────────────────────────
    print("\n" + "=" * 80)
    print("📡 SIGNAL COMPUTATION")
    print("=" * 80)

    equity = float(acct["equity"])
    decisions = []

    for market_symbol, alloc in SYMBOLS:
        print(f"\n--- {market_symbol} ---")
        try:
            df = get_recent_df(market_symbol)
            state = compute_state(df)

            existing = pos_map.get(market_symbol.split('/')[0])
            has_position = existing is not None
            current_qty = float(existing["qty"]) if has_position else 0.0
            current_state = "LONG" if has_position else "FLAT"

            print(f"  Price: ${state['price']:,.2f}")
            print(f"  MA({STRATEGY_PARAMS['fast_period']}): {state['ma_fast']:.2f} | MA({STRATEGY_PARAMS['slow_period']}): {state['ma_slow']:.2f} "
                  f"({'BULL' if state['ma_fast'] > state['ma_slow'] else 'BEAR'})")
            print(f"  ADX: {state['adx']:.1f} (threshold 40) | Trend: {'UP' if state['trend_up'] else 'DOWN'}")
            print(f"  Crossovers last 24h: {state['n_buy_24h']} BUY / {state['n_sell_24h']} SELL")
            print(f"  Strategy state: {state['state']} | Binance: {current_state}")

            target_notional = equity * alloc
            current_notional = current_qty * state["price"]
            delta_usd = target_notional - current_notional
            deadband = alloc * equity * 0.05  # 5% target — tránh lệnh nhỏ

            if state["state"] == "LONG" and delta_usd > deadband:
                decisions.append({
                    "market_symbol": market_symbol,
                    "action": "BUY",
                    "qty": delta_usd / state["price"],
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: BUY {delta_usd / state['price']:.6f} {market_symbol.split('/')[0]} "
                      f"(rebalance {current_notional:,.0f} → {target_notional:,.0f} USD)")
            elif state["state"] == "LONG" and delta_usd < -deadband:
                decisions.append({
                    "market_symbol": market_symbol,
                    "action": "SELL",
                    "qty": abs(delta_usd) / state["price"],
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: SELL (trim excess {current_notional:,.0f} → {target_notional:,.0f} USD)")
            elif state["state"] == "FLAT" and has_position:
                decisions.append({
                    "market_symbol": market_symbol,
                    "action": "SELL",
                    "qty": current_qty,
                    "price": state["price"],
                    "size_pct": alloc,
                })
                print(f"  → ACTION: SELL {current_qty:.6f} {market_symbol.split('/')[0]} (strategy flat → close)")
            else:
                print("  → NO ACTION (state matches)")

        except Exception as e:
            print(f"  ❌ Error: {e}")

    # ── Execute ────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("🎯 EXECUTION")
    print("=" * 80)

    if not decisions:
        print("\n[No trades to execute — all positions in sync]")
        return

    for d in decisions:
        print(f"\n--- {d['market_symbol']} → {d['action']} {d['qty']:.6f} ---")
        if DRY_RUN:
            print(f"  [DRY-RUN] Would {d['action']} {d['qty']:.6f} {d['market_symbol']} @ ${d['price']:.2f}")
            continue

        # Cap BUY size by real available cash (re-fetch before each order)
        try:
            acct_now = broker.get_account()
            cash_now = float(acct_now["cash"])
        except Exception:
            cash_now = 0.0

        qty = float(d["qty"])
        if d["action"] == "BUY":
            cost = qty * d["price"]
            if cost > cash_now:
                qty = (cash_now * 0.95) / d["price"]
                print(f"  ℹ️ cash-limited: {d['qty']:.6f} → {qty:.6f} (cash ${cash_now:,.2f})")
            if qty <= 0:
                print(f"  ⏭️ SKIP — no available cash (${cash_now:,.2f})")
                continue

        try:
            base, quote = d["market_symbol"].split('/')
            symbol = Symbol(base=base, quote=quote, asset_class=AssetClass.CRYPTO,
                            market_type=MarketType.SPOT, exchange="binance")
            order = Order(
                id="", symbol=symbol,
                side=OrderSide.BUY if d["action"] == "BUY" else OrderSide.SELL,
                type=OrderType.MARKET,
                size=Decimal(str(round(qty, 6))),
                time_in_force=TimeInForce.GTC,
            )
            result = broker.place_order(order)
            print(f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} → {result['status']}")
            if result.get("error"):
                print(f"  ⚠️ {result['error']}")
        except Exception as e:
            print(f"  ❌ Order failed: {e}")

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


if __name__ == "__main__":
    main()