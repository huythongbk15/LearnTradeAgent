#!/usr/bin/env python3
"""
Full System Real-Time Simulation — chạy toàn bộ hệ thống như thật trên dữ liệu lịch sử.

Mô phỏng đầy đủ pipeline production:
  data → enhanced_ma strategy (MA+ADX+ATR) → ExecutionEngine (paper) → RiskController

Cách dùng:
  python3 scripts/full_system_backtest.py                 # full 3 năm
  python3 scripts/full_system_backtest.py --freq 1        # phân tích mỗi bar (1h)
  python3 scripts/full_system_backtest.py --fresh         # reset paper state trước khi chạy
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Tắt toàn bộ log INFO/WARNING của thư viện (giữ console output sạch)
logging.disable(logging.CRITICAL)

# TẮT LLM — dùng rule-based fallback cho tốc độ
os.environ["USE_LLM"] = os.environ.get("USE_LLM", "false")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import polars as pl

from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution import risk_controller as rc_module
from trading_agent.execution.risk_controller import RiskController
from trading_agent.agents.base import AgentMessage


class _SimClock(datetime):
    """Đồng hồ giả lập: datetime.now(UTC) trả về timestamp của bar hiện tại."""

    current: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return cls.current or datetime.now(tz or UTC)


# ── Config ────────────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
EXCHANGE = os.getenv("EXCHANGE", "binance")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.50"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.05"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.15"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.07"))
COOLDOWN_HOURS = float(os.getenv("COOLDOWN_HOURS", "24"))
MAX_POS_SIZE_PCT = float(os.getenv("MAX_POS_SIZE_PCT", "0.25"))

# Strategy params (tuned from parameter sweep)
FAST_MA = int(os.getenv("FAST_MA", "15"))
SLOW_MA = int(os.getenv("SLOW_MA", "50"))
ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", "40"))
ATR_SL_MULT = float(os.getenv("ATR_SL_MULT", "2.0"))
ATR_TP_MULT = float(os.getenv("ATR_TP_MULT", "3.0"))

PAPER_STATE = ROOT / "data" / "execution" / f"paper_{EXCHANGE}.json"


class FullSystemSimulator:
    def __init__(self, fresh: bool = False):
        # Reset paper state nếu cần
        if fresh and PAPER_STATE.exists():
            backup = PAPER_STATE.with_suffix(".json.bak")
            PAPER_STATE.rename(backup)
            print(f"🗑  Paper state reset (backup → {backup.name})")

        # Load data
        print(f"📥 Loading {SYMBOL} {TIMEFRAME} from {EXCHANGE}...")
        self.df = load_ohlcv(EXCHANGE, SYMBOL, TIMEFRAME).sort("timestamp")
        print(f"   {self.df.height} bars: {self.df['timestamp'].min()} → {self.df['timestamp'].max()}")

        # Initialize strategy
        self.strategy = EnhancedMaCrossover({
            "fast_period": FAST_MA,
            "slow_period": SLOW_MA,
            "adx_threshold": ADX_THRESHOLD,
            "atr_sl_mult": ATR_SL_MULT,
            "atr_tp_mult": ATR_TP_MULT,
        })
        
        # Pre-compute indicators on full dataset
        print("🔧 Computing strategy indicators...")
        self.df = self.strategy.compute_indicators(self.df)
        
        # Generate all signals upfront
        print("🔧 Generating signals...")
        self.signals = self.df.with_columns(self.strategy.generate_signals(self.df)).select(pl.col("signal")).to_series().to_list()

        # Khởi tạo execution engine + risk controller
        self.engine = ExecutionEngine(exchange_name=EXCHANGE, initial_capital=INITIAL_CAPITAL)
        self.risk = RiskController(
            self.engine,
            max_drawdown_pct=MAX_DRAWDOWN_PCT,
            daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
            max_position_pct=MAX_POSITION_PCT,
            default_stop_loss_pct=STOP_LOSS_PCT,
            cooldown_hours=COOLDOWN_HOURS,
        )
        
        # Thay datetime.now(UTC) trong risk_controller bằng đồng hồ giả lập
        rc_module.datetime = _SimClock

        # Tracking
        self.equity_curve: list[tuple] = []
        self.trade_log: list[dict] = []
        self.signal_log: list[dict] = []
        self.circuit_breakers: list[str] = []
        self._breaker_active = False

    def _position_pct(self, price: float) -> float:
        """% portfolio đang nằm trong vị thế."""
        pos = self.engine.exchange.get_position(SYMBOL)
        if not pos or not pos.is_active:
            return 0.0
        equity = self.engine.exchange.get_total_equity()
        return (pos.quantity * price) / equity if equity > 0 else 0.0

    def run(self, start: int = 0, end: int | None = None, freq: int = 1):
        end = end if end is not None else self.df.height
        n = end - start
        print(f"🚀 Simulating bars {start}→{end} ({n} bars, decision mỗi {freq}h)")
        print(f"   SL={STOP_LOSS_PCT:.0%} TP={TAKE_PROFIT_PCT:.0%} Trail={TRAILING_STOP_PCT:.0%} | Cooldown={COOLDOWN_HOURS:.0f}h\n")

        for i in range(start, end):
            row = self.df.row(i, named=True)
            ts = row["timestamp"]
            price = float(row["close"])
            signal = int(self.signals[i]) if self.signals[i] is not None else 0

            # Đồng hồ giả lập theo bar hiện tại
            _SimClock.current = datetime.fromisoformat(str(ts)).replace(tzinfo=UTC)

            # 1. Cập nhật giá → unrealized PnL + stop-loss trigger
            self.engine.update_prices({SYMBOL: price})

            # 2. Risk checks (max DD, daily loss, circuit breaker)
            alerts = self.risk.check_all()
            breaker_on = any("CIRCUIT BREAKER ACTIVE" in a for a in alerts)
            if breaker_on and not self._breaker_active:
                self.circuit_breakers.append(f"{ts}: {alerts[0]}")
                print(f"   🚨 CIRCUIT BREAKER ON @ {ts} — đóng toàn bộ vị thế, tạm dừng {COOLDOWN_HOURS:.0f}h")
            elif not breaker_on and self._breaker_active:
                print(f"   ✅ CIRCUIT BREAKER OFF @ {ts} — giao dịch trở lại")
            self._breaker_active = breaker_on

            # 3. Execute signal theo chu kỳ (chỉ khi chưa bị chặn)
            if i % freq == 0 and not breaker_on and not any("Cooldown" in a for a in alerts):
                equity = self.engine.exchange.get_total_equity()
                pos_pct = self._position_pct(price)
                
                # Only act on crossover signals (non-zero)
                if signal != 0:
                    # Calculate position size using risk controller dynamic sizing
                    # Get ATR for this bar
                    atr = float(row.get("atr", 0)) if row.get("atr") else None
                    
                    # Get regime info
                    regime_info = {
                        "vol_regime": row.get("vol_regime"),
                        "trend_regime": row.get("trend_regime"),
                        "trend_dir": row.get("trend_dir"),
                        "adx": row.get("adx"),
                        "atr_pctl": row.get("atr_pctl"),
                    }
                    
                    # Calculate position size
                    if signal == 1:  # BUY
                        # Check if we already have a long position
                        pos = self.engine.exchange.get_position(SYMBOL)
                        if pos and pos.is_active and pos.quantity > 0:
                            signal = 0  # Already long, skip
                    
                    if signal != 0:
                        # Use risk controller for dynamic position sizing
                        pos_size = self.risk.calculate_position_size(
                            symbol=SYMBOL,
                            price=price,
                            atr=atr,
                            regime_info=regime_info if any(v is not None for v in regime_info.values()) else None,
                        )
                        
                        if pos_size > 0:
                            msg = AgentMessage(
                                role="trader",
                                signal="BUY" if signal == 1 else "SELL",
                                confidence=0.65,
                                reasoning=f"enhanced_ma: MA{FAST_MA}/{SLOW_MA} crossover with ADX>{ADX_THRESHOLD}",
                                details={"symbol": SYMBOL, "strategy": "enhanced_ma"},
                                max_position_size_pct=pos_size * price / equity,
                                risk_level="medium",
                            )
                            
                            self.signal_log.append({
                                "timestamp": str(ts), "price": price, "position_pct": pos_pct,
                                "signal": msg.signal, "confidence": msg.confidence,
                                "risk": msg.risk_level, "max_pos": msg.max_position_size_pct,
                            })

                            # Execute
                            for order in self.engine.execute_signal(msg):
                                pos = self.engine.exchange.get_position(SYMBOL)
                                self.trade_log.append({
                                    "timestamp": str(ts), "side": order.side.value,
                                    "amount": float(order.filled_amount or order.amount),
                                    "price": price,
                                    "pnl": float(pos.unrealized_pnl) if pos else 0.0,
                                    "equity": float(self.engine.exchange.get_total_equity()),
                                })
                                side = "🟢 BUY" if order.side.value == "buy" else "🔴 SELL"
                                print(f"   {side} {order.filled_amount or order.amount:.4f} @ ${price:,.2f} @ {ts}")
                                # Exit plan
                                self.risk.set_stop_loss_on_all_positions(STOP_LOSS_PCT)
                                self.risk.set_take_profit_on_all_positions(TAKE_PROFIT_PCT)
                                self.risk.set_trailing_stop_on_all_positions(TRAILING_STOP_PCT)

            # 4. Equity tracking
            self.equity_curve.append((ts, self.engine.exchange.get_total_equity()))

            # Progress
            if (i - start) % 2000 == 0 and i > start:
                eq = self.equity_curve[-1][1]
                print(f"   ... {i - start}/{n} bars — equity ${eq:,.2f} "
                      f"({(eq/INITIAL_CAPITAL-1)*100:+.2f}%)")

        print("\n✅ Simulation complete")
        self._report()

    # ── Báo cáo ────────────────────────────────────────────────────────
    def _report(self):
        if not self.equity_curve:
            print("❌ No data")
            return

        eq_values = np.array([e[1] for e in self.equity_curve])
        returns = np.diff(eq_values) / eq_values[:-1]
        returns = returns[~np.isnan(returns)]

        total_return = (eq_values[-1] / eq_values[0] - 1) * 100
        sharpe = (returns.mean() / returns.std() * np.sqrt(24 * 252)) if len(returns) > 1 and returns.std() > 0 else 0
        max_dd = ((eq_values.max() - eq_values) / eq_values.max()).max() * 100

        wins = [t for t in self.trade_log if t.get("pnl", 0) > 0]
        losses = [t for t in self.trade_log if t.get("pnl", 0) <= 0]
        win_rate = len(wins) / len(self.trade_log) * 100 if self.trade_log else 0
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 0
        profit_factor = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses and sum(t["pnl"] for t in losses) != 0 else (float('inf') if wins else 0)

        print(f"\n{'='*55}")
        print(f"📊 KẾT QUẢ FULL SYSTEM — {SYMBOL} {TIMEFRAME} ({EXCHANGE})")
        print(f"{'='*55}")
        print(f"   Vốn ban đầu:      ${INITIAL_CAPITAL:,.2f}")
        print(f"   Vốn cuối:         ${eq_values[-1]:,.2f}")
        print(f"   Tổng lợi nhuận:   {total_return:+.2f}%")
        print(f"   Sharpe (hourly):  {sharpe:.2f}")
        print(f"   Max Drawdown:     {max_dd:.2f}%")
        print(f"   Tổng trades:      {len(self.trade_log)}")
        print(f"   Win rate:         {win_rate:.1f}%")
        print(f"   Avg win:          ${avg_win:,.2f}")
        print(f"   Avg loss:         ${avg_loss:,.2f}")
        print(f"   Profit factor:    {profit_factor:.2f}")
        print(f"   Circuit breakers: {len(self.circuit_breakers)}")

        # Phân bố theo năm
        print("\n📅 PHÂN BỐ THEO NĂM")
        from collections import defaultdict
        yearly = defaultdict(list)
        for ts, eq in self.equity_curve:
            year = datetime.fromisoformat(str(ts)).year
            yearly[year].append(eq)
        
        for year in sorted(yearly.keys()):
            eqs = yearly[year]
            ret = (eqs[-1] / eqs[0] - 1) * 100
            print(f"   {year}: ${eqs[0]:,.2f} → ${eqs[-1]:,.2f}  ({ret:+.2f}%)")

        # 10 trades gần nhất
        print("\n🧾 10 TRADES GẦN NHẤT")
        for t in self.trade_log[-10:]:
            pnl = t.get("pnl", 0)
            side = "🟢" if pnl > 0 else "🔴"
            print(f"   {side} {t['side']}  {t['amount']:.4f} @ ${t['price']:,.2f} pnl ${pnl:+.2f} ({pnl/t['price']/t['amount']*100:+.1f}%) [{t.get('exit_reason', 'signal')}] {t['timestamp'][:19]}")

        # Save
        out_path = ROOT / "data" / "full_system_backtest.json"
        out_path.parent.mkdir(exist_ok=True)
        with open(out_path, "w") as f:
            json.dump({
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "exchange": EXCHANGE,
                "initial_capital": INITIAL_CAPITAL,
                "final_equity": float(eq_values[-1]),
                "total_return_pct": total_return,
                "sharpe": sharpe,
                "max_drawdown_pct": max_dd,
                "total_trades": len(self.trade_log),
                "win_rate_pct": win_rate,
                "profit_factor": profit_factor,
                "circuit_breakers": len(self.circuit_breakers),
                "equity_curve": [[str(ts), float(eq)] for ts, eq in self.equity_curve],
                "trades": self.trade_log,
            }, f, indent=2)
        print(f"\n💾 Saved → {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="Reset paper state trước khi chạy")
    parser.add_argument("--start", type=int, default=0, help="Bar bắt đầu (mặc định 0)")
    parser.add_argument("--end", type=int, default=None, help="Bar kết thúc (mặc định hết)")
    parser.add_argument("--freq", type=int, default=1, help="Phân tích mỗi N bar (mặc định 1h)")
    args = parser.parse_args()

    sim = FullSystemSimulator(fresh=args.fresh)
    sim.run(start=args.start, end=args.end, freq=args.freq)


if __name__ == "__main__":
    main()