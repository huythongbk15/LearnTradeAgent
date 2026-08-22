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

import asyncio
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from dotenv import load_dotenv

load_dotenv(".env")

from datetime import UTC, datetime

import ccxt
import numpy as np
import polars as pl
from live_config import (
    ATR_SL_MULT,
    ATR_SL_WINDOW,
    DRAWDOWN_TIERS,
    LOOKBACK,
    STRATEGY_PARAMS,
)
from live_config import (
    SYMBOLS_ALPACA as SYMBOLS,
)

from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig
from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
)
from trading_agent.execution.canonical import (
    BrokerGateway,
)
from trading_agent.execution.canonical.adapters import LiveBrokerExecutionAdapter
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    EmergencyReduceRequest,
    ExecutionLifecycle,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.risk.portfolio_risk import DrawdownConfig, PortfolioRiskManager
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

# ── Risk guard (P0) ────────────────────────────────────────────────────
# (config: ATR_SL_MULT, ATR_SL_WINDOW, DRAWDOWN_TIERS ở live_config.py)
PEAK_STATE_FILE = "data/live_peak_equity.json"  # persist peak equity giữa các lần chạy

DRY_RUN = "--execute" not in sys.argv


def load_peak_equity() -> float:
    """Đọc peak equity đã lưu (None nếu chưa có)."""
    path = Path(PEAK_STATE_FILE)
    if not path.exists():
        return 0.0
    try:
        with path.open() as f:
            peak = float(json.load(f)["peak"])
        if not np.isfinite(peak) or peak <= 0:
            raise ValueError("peak must be finite and positive")
        return peak
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Corrupt peak-equity state; refusing to trade: {path}"
        ) from exc


def save_peak_equity(peak: float) -> None:
    path = Path(PEAK_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"peak": round(peak, 2), "updated": datetime.now(UTC).isoformat()}, f
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


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
    ma_fast = (
        float(last[f"ma_{STRATEGY_PARAMS['fast_period']}"][0])
        if f"ma_{STRATEGY_PARAMS['fast_period']}" in last.columns
        else None
    )
    ma_slow = (
        float(last[f"ma_{STRATEGY_PARAMS['slow_period']}"][0])
        if f"ma_{STRATEGY_PARAMS['slow_period']}" in last.columns
        else None
    )
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
    """Fetch only fully closed 1h candles from Binance via CCXT."""
    exchange = ccxt.binance({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    ohlcv = [bar for bar in ohlcv if int(bar[0]) + 3_600_000 <= now_ms]
    if not ohlcv:
        raise RuntimeError(f"No closed 1h candles for {symbol}")
    return pl.DataFrame(
        {
            "timestamp": [datetime.fromtimestamp(b[0] / 1000, tz=UTC) for b in ohlcv],
            "open": [b[1] for b in ohlcv],
            "high": [b[2] for b in ohlcv],
            "low": [b[3] for b in ohlcv],
            "close": [b[4] for b in ohlcv],
            "volume": [b[5] for b in ohlcv],
        }
    ).sort("timestamp")


def _canonical_submit(
    broker,
    lifecycle: ExecutionLifecycle,
    gateway: BrokerGateway,
    symbol,
    side: str,
    qty: float,
    correlation_id: str,
    risk_level: str = "LOW",
    reduce_only: bool = False,
    risk_decision_id: str = "",
    forecast_fingerprint: str = "cli-manual",
    model_artifact_id: str = "cli-manual",
) -> dict:
    """Submit only lifecycle-authorized risk-reducing legacy actions.

    This runner has no promoted forecast/risk artifact, so exposure-increasing
    orders fail closed.  Canonical paper BUYs must use ``ExecutionEngine``.
    """
    if side.lower() != "sell":
        return {
            "success": False,
            "error": "legacy live runner cannot increase exposure without real risk evidence",
            "side": side,
            "qty": qty,
            "symbol": str(symbol),
            "status": "blocked",
        }
    symbol_str = symbol.pair if hasattr(symbol, "pair") else str(symbol)
    auth_event = lifecycle.emergency_reduce(
        EmergencyReduceRequest(
            intent_id=correlation_id,
            symbol=symbol_str,
            side="sell",
            quantity=qty,
            reason="LEGACY_RUNNER_REDUCE",
            metadata={"order_type": "market", "time_in_force": "gtc"},
        )
    )
    result = gateway.submit(
        auth_event.payload["authorization_id"],
        correlation_id=correlation_id,
    )
    if result.success and result.broker_order_id:
        lifecycle.submit_order(
            intent_id=correlation_id,
            exchange_order_id=result.broker_order_id,
        )
    lifecycle.record_broker_submit_result(correlation_id, result)

    return {
        "success": result.success,
        "broker_order_id": result.broker_order_id,
        "error": result.error,
        "side": side,
        "qty": qty,
        "symbol": str(symbol),
        "status": "submitted" if result.success else "rejected",
    }


def main():
    if not DRY_RUN:
        if os.getenv("TRADING_EXECUTION_ENABLED", "false").lower() != "true":
            raise RuntimeError(
                "Paper execution is disabled (TRADING_EXECUTION_ENABLED=false)"
            )
        if os.getenv("TRADING_MODE", "paper").lower() != "paper":
            raise RuntimeError("Only TRADING_MODE=paper is supported")

    print("=" * 80)
    print("🚀 LIVE PAPER TRADING — Single 1h Enhanced MA (20,80,40)")
    print(
        f"  Mode: {'DRY RUN (no orders placed)' if DRY_RUN else 'EXECUTE (real paper orders)'}"
    )
    print("=" * 80)

    # Connect Alpaca
    adapter = AlpacaAdapter(
        AlpacaConfig(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_API_SECRET"],
            paper=True,
        )
    )
    asyncio.run(adapter.connect())
    broker = LiveBroker("alpaca", adapter)
    # Canonical execution: lifecycle + gateway (P0 §11: runner canonical migration)
    store = ExecutionEventStore("data/execution/events.db").connect()

    def _venue_symbol(value):
        if isinstance(value, Symbol):
            return value
        raw = str(value)
        base, _, quote = raw.partition("/")
        return Symbol(
            base=base,
            quote=quote or "USD",
            asset_class=AssetClass.CRYPTO,
            market_type=MarketType.SPOT,
            exchange="alpaca",
        )

    def _price_source(value):
        ticker = broker.get_ticker(_venue_symbol(value))
        price = ticker.get("last")
        exchange_timestamp = ticker.get("timestamp")
        if not price or not isinstance(exchange_timestamp, datetime):
            return None
        return TrustedPrice(
            price=float(price),
            exchange_timestamp=exchange_timestamp.astimezone(UTC),
            received_at=datetime.now(UTC),
        )

    def _inventory_source(value, side):
        if side != "sell":
            return 0.0
        pair = _venue_symbol(value).pair
        for position in broker.get_positions():
            if position.get("symbol") == pair:
                return float(position.get("qty") or 0.0)
        return 0.0

    def _portfolio_source(value):
        account = broker.get_account()
        try:
            equity = float(account["equity"])
            cash = float(account["cash"])
        except (KeyError, TypeError, ValueError):
            return None
        pair = _venue_symbol(value).pair
        quantity = _inventory_source(value, "sell")
        return PortfolioRiskSnapshot(
            symbol=pair,
            position_quantity=quantity,
            available_quantity=quantity,
            equity=equity,
            available_cash=cash,
            observed_at=datetime.now(UTC),
            source="alpaca",
        )

    lifecycle = ExecutionLifecycle(
        store,
        price_source=_price_source,
        inventory_source=_inventory_source,
        portfolio_source=_portfolio_source,
    )
    gateway = BrokerGateway(
        adapter=LiveBrokerExecutionAdapter(broker),
        store=store,
        lifecycle=lifecycle,
    )

    acct = broker.get_account()
    print("\n✅ Alpaca Paper connected")
    print(f"  Equity: ${acct['equity']:,.2f} | Cash: ${acct['cash']:,.2f}")

    # ── Risk guard: portfolio drawdown controller ─────────────────────
    pm = PortfolioRiskManager(DrawdownConfig(tiers=DRAWDOWN_TIERS))
    peak_seen = load_peak_equity()
    if peak_seen > 0:
        pm.update_equity(peak_seen)  # seed peak từ các lần chạy trước
    pm.update_equity(float(acct["equity"]))
    save_peak_equity(pm.peak_equity)
    halted = pm.is_trading_halted()
    scale = pm.position_scale_factor()
    print(
        f"  🛡 Risk: equity ${float(acct['equity']):,.2f} | peak ${pm.peak_equity:,.2f} | "
        f"DD {pm.current_dd:.1%} | scale {scale:.0%} | "
        f"{'⛔ HALTED (đóng hết vị thế)' if halted else '✅ trading allowed'}"
    )

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
            print(
                f"  MA({STRATEGY_PARAMS['fast_period']}): {state['ma_fast']:.2f} | MA({STRATEGY_PARAMS['slow_period']}): {state['ma_slow']:.2f} "
                f"({'BULL' if state['ma_fast'] > state['ma_slow'] else 'BEAR'})"
            )
            print(
                f"  ADX: {state['adx']:.1f} (threshold 40) | Trend: {'UP' if state['trend_up'] else 'DOWN'}"
            )
            print(
                f"  Crossovers last 24h: {state['n_buy_24h']} BUY / {state['n_sell_24h']} SELL"
            )
            print(f"  Strategy state: {state['state']} | Alpaca: {current_state}")

            # ── RISK 1: ATR trailing stop — đóng lệnh nếu phá stop ──
            risk_exit = False
            if has_position and current_qty > 0:
                stop = trailing_stop_price(float(existing["avg_entry_price"]), df)
                if stop is not None:
                    if state["price"] <= stop:
                        risk_exit = True
                        print(
                            f"  🛑 RISK EXIT: giá ${state['price']:,.2f} ≤ ATR stop ${stop:,.2f} "
                            f"(entry ${float(existing['avg_entry_price']):,.2f})"
                        )
                    else:
                        print(
                            f"  🛡 ATR trailing stop: ${stop:,.2f} (buffer ${state['price'] - stop:,.2f})"
                        )
                else:
                    print("  ⚠️ Không tính được ATR stop (thiếu dữ liệu)")

            if risk_exit:
                decisions.append(
                    {
                        "market_symbol": market_symbol,
                        "alpaca_symbol": alpaca_symbol,
                        "action": "SELL",
                        "qty": current_qty,
                        "price": state["price"],
                        "size_pct": alloc,
                        "reason": "ATR_TRAILING_STOP",
                    }
                )
                print(
                    f"  → ACTION: SELL {current_qty:.6f} {alpaca_symbol} (ATR trailing stop)"
                )

            # ── RISK 2: portfolio halt — đóng hết, dừng mua ──────────
            elif halted and has_position:
                decisions.append(
                    {
                        "market_symbol": market_symbol,
                        "alpaca_symbol": alpaca_symbol,
                        "action": "SELL",
                        "qty": current_qty,
                        "price": state["price"],
                        "size_pct": alloc,
                        "reason": "PORTFOLIO_HALT",
                    }
                )
                print(
                    f"  ⛔ HALT: đóng {current_qty:.6f} {alpaca_symbol} (drawdown > 20%)"
                )
            elif halted:
                print(f"  ⛔ HALTED: bỏ qua {alpaca_symbol} (drawdown > 20%)")

            # ── Rebalance vs strategy target state (deadband 5%) ──
            #    target bị scale theo drawdown tier (75/50/25%)
            else:
                target_notional = equity * alloc * scale
                current_notional = current_qty * state["price"]
                delta_usd = target_notional - current_notional
                deadband = (
                    alloc * equity * 0.05
                )  # 5% of target — tránh lệnh nhỏ lặt vặt

                if state["state"] == "LONG" and delta_usd > deadband:
                    qty = delta_usd / state["price"]
                    decisions.append(
                        {
                            "market_symbol": market_symbol,
                            "alpaca_symbol": alpaca_symbol,
                            "action": "BUY",
                            "qty": qty,
                            "price": state["price"],
                            "size_pct": alloc,
                            "reason": "REBALANCE",
                        }
                    )
                    print(
                        f"  → ACTION: BUY {qty:.6f} {alpaca_symbol} "
                        f"(rebalance {current_notional:,.0f} → {target_notional:,.0f} USD, scale {scale:.0%})"
                    )
                elif state["state"] == "LONG" and delta_usd < -deadband:
                    decisions.append(
                        {
                            "market_symbol": market_symbol,
                            "alpaca_symbol": alpaca_symbol,
                            "action": "SELL",
                            "qty": abs(delta_usd) / state["price"],
                            "price": state["price"],
                            "size_pct": alloc,
                            "reason": "REBALANCE",
                        }
                    )
                    print(
                        f"  → ACTION: SELL (trim excess {current_notional:,.0f} → {target_notional:,.0f} USD)"
                    )
                elif state["state"] == "FLAT" and has_position:
                    decisions.append(
                        {
                            "market_symbol": market_symbol,
                            "alpaca_symbol": alpaca_symbol,
                            "action": "SELL",
                            "qty": current_qty,
                            "price": state["price"],
                            "size_pct": alloc,
                            "reason": "STRATEGY_FLAT",
                        }
                    )
                    print(
                        f"  → ACTION: SELL {current_qty:.6f} {alpaca_symbol} (strategy flat → close)"
                    )
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
            print(
                f"  [DRY-RUN] Would {d['action']} {d['qty']:.6f} {d['alpaca_symbol']} @ ${d['price']:.2f}"
            )
            continue

        # Cap BUY size by REAL available cash (re-fetch before each order)
        # → tránh "insufficient balance" khi nhiều lệnh BUY cùng chu kỳ
        try:
            acct_now = broker.get_account()
            cash_now = float(acct_now["cash"])
        except Exception:
            cash_now = float(acct_now["cash"]) if "acct_now" in dir() else 0.0

        qty = float(d["qty"])

        # For SELL: use EXACT broker position qty to avoid rounding mismatch
        if d["action"] == "SELL":
            try:
                positions_now = broker.get_positions()
                pos = next(
                    (
                        p
                        for p in positions_now
                        if p["symbol"] == f"{d['alpaca_symbol']}/USD"
                    ),
                    None,
                )
                if pos:
                    qty = float(pos["qty"])
                else:
                    print(f"  ⏭️  SKIP — no position found for {d['alpaca_symbol']}")
                    continue
            except Exception as e:
                print(f"  ⚠️  Could not fetch exact position qty: {e}, using calculated")

        if round(qty, 6) <= 0:
            print(
                f"  ⏭️  SKIP — {d['action']} qty {qty:.6f} ≈ 0 (dust position, không đáng lệnh)"
            )
            continue
        if d["action"] == "BUY":
            cost = qty * d["price"]
            if cost > cash_now:
                qty = (cash_now * 0.95) / d[
                    "price"
                ]  # buffer 5% cho slippage (market order)
                print(
                    f"  ℹ️  cash-limited: {d['qty']:.6f} → {qty:.6f} (cash ${cash_now:,.2f})"
                )
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
                size=Decimal(str(qty)),  # use exact qty (already floored for SELL)
                time_in_force=TimeInForce.GTC,
            )
            correlation_id = f"{d['alpaca_symbol']}-{d['action']}-{int(datetime.now(UTC).timestamp())}"
            result = _canonical_submit(
                broker,
                lifecycle,
                gateway,
                symbol,
                d["action"].lower(),
                float(qty),
                correlation_id,
                risk_level="LOW" if not halted else "HIGH",
                reduce_only=halted,
            )
            print(
                f"  ✅ Order: {result['side']} {result['qty']} {result['symbol']} → {result['status']}"
            )
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
        print(
            "\n✅ Xem dashboard: https://app.alpaca.markets/paper/dashboard/positions"
        )


if __name__ == "__main__":
    main()
