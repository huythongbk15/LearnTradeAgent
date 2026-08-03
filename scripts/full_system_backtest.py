#!/usr/bin/env python3
"""
Full System Real-Time Simulation — chạy toàn bộ hệ thống như thật trên dữ liệu lịch sử.

Mô phỏng đầy đủ pipeline production:
  data → multi-agent analysis (Technical + Sentiment + Risk → Trader)
      → ExecutionEngine (paper) → RiskController (stop-loss, DD, cooldown, circuit breaker)

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

# TẮT LLM — dùng rule-based fallback cho tốc độ (bật lại để dùng LLM thật)
os.environ["USE_LLM"] = os.environ.get("USE_LLM", "false")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import polars as pl

from trading_agent.agents.base import AgentMessage
from trading_agent.agents.orchestrator import Orchestrator
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution import risk_controller as rc_module
from trading_agent.execution.risk_controller import RiskController


class _SimClock(datetime):
    """Đồng hồ giả lập: datetime.now(UTC) trả về timestamp của bar hiện tại,
    để daily tracking + cooldown + circuit breaker chạy theo thời gian backtest."""

    current: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return cls.current or datetime.now(tz or UTC)

# ── Config (có thể override qua env) ────────────────────────────────────
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

        # Khởi tạo pipeline production
        self.engine = ExecutionEngine(exchange_name=EXCHANGE, initial_capital=INITIAL_CAPITAL)
        self.risk = RiskController(
            self.engine,
            max_drawdown_pct=MAX_DRAWDOWN_PCT,
            daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
            max_position_pct=MAX_POSITION_PCT,
            default_stop_loss_pct=STOP_LOSS_PCT,
            cooldown_hours=COOLDOWN_HOURS,
        )
        self.orchestrator = Orchestrator()
        self.risk.set_stop_loss_on_all_positions(STOP_LOSS_PCT)

        # Thay datetime.now(UTC) trong risk_controller bằng đồng hồ giả lập
        rc_module.datetime = _SimClock

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

    def run(self, start: int = 0, end: int | None = None, freq: int = 4):
        end = end if end is not None else self.df.height
        n = end - start
        print(f"🚀 Simulating bars {start}→{end} ({n} bars, agent decision mỗi {freq}h)")
        print(f"   USE_LLM={os.environ.get('USE_LLM')} | SL={STOP_LOSS_PCT:.0%} TP={TAKE_PROFIT_PCT:.0%} "
              f"Trail={TRAILING_STOP_PCT:.0%} | Cooldown={COOLDOWN_HOURS:.0f}h | Sizing=volatility\n")

        for i in range(start, end):
            row = self.df.row(i, named=True)
            ts = row["timestamp"]
            price = float(row["close"])

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

            # 3. Agent analysis theo chu kỳ (chỉ khi chưa bị chặn)
            #    QUAN TRỌNG: phân tích chỉ dùng dữ liệu ĐẾN bar hiện tại (df.head(i+1))
            #    để tránh lookahead bias — không nhìn thấy tương lai.
            if i % freq == 0 and not breaker_on and not any("Cooldown" in a for a in alerts):
                equity = self.engine.exchange.get_total_equity()
                pos_pct = self._position_pct(price)
                report = self.orchestrator.analyze(
                    symbol=SYMBOL, timeframe=TIMEFRAME,
                    current_position_pct=pos_pct, portfolio_value=equity,
                    df=self.df.head(i + 1),
                )
                d = report.final_decision
                # Position sizing theo volatility — ưu tiên max_position_size_pct từ risk manager
                pos_size_pct = d.max_position_size_pct if d.max_position_size_pct else MAX_POS_SIZE_PCT
                msg = AgentMessage(
                    role="trader", signal=d.signal, confidence=d.confidence,
                    reasoning=d.reasoning, details={"symbol": SYMBOL},
                    max_position_size_pct=pos_size_pct, risk_level=d.risk_level,
                )
                self.signal_log.append({
                    "timestamp": str(ts), "price": price, "position_pct": pos_pct,
                    "signal": d.signal, "confidence": float(d.confidence),
                    "risk": d.risk_level, "max_pos": pos_size_pct,
                })

                # 4. Execute → orders
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
                    # Exit plan cho vị thế mới: SL cố định + TP chủ động + trailing stop
                    self.risk.set_stop_loss_on_all_positions(STOP_LOSS_PCT)
                    self.risk.set_take_profit_on_all_positions(TAKE_PROFIT_PCT)
                    self.risk.set_trailing_stop_on_all_positions(TRAILING_STOP_PCT)

            # 5. Equity tracking
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

        eq_vals = np.array([e[1] for e in self.equity_curve])
        final = eq_vals[-1]
        ret = (final / INITIAL_CAPITAL - 1) * 100

        rets = np.diff(eq_vals) / eq_vals[:-1]
        sharpe = rets.mean() / rets.std() * np.sqrt(252 * 24) if rets.std() > 0 else 0.0
        peak = np.maximum.accumulate(eq_vals)
        max_dd = ((eq_vals - peak) / peak * 100).min()

        trades_raw = self.engine.exchange.get_trade_history(limit=1000)
        trades = [t.to_dict() for t in trades_raw]
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0.0
        gross_p = sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0)
        gross_l = abs(sum(t.get("pnl", 0) for t in trades if t.get("pnl", 0) <= 0))
        pf = gross_p / gross_l if gross_l > 0 else float("inf") if gross_p > 0 else 0.0
        reasons = {}
        for t in trades:
            r = t.get("reason") or "signal"
            reasons[r] = reasons.get(r, 0) + 1

        # Phân bổ tín hiệu
        sig_counts = {}
        for s in self.signal_log:
            sig_counts[s["signal"]] = sig_counts.get(s["signal"], 0) + 1

        print("=" * 60)
        print(f"📊 KẾT QUẢ FULL SYSTEM — {SYMBOL} {TIMEFRAME} ({EXCHANGE})")
        print("=" * 60)
        print(f"   Vốn ban đầu:      ${INITIAL_CAPITAL:,.2f}")
        print(f"   Vốn cuối:         ${final:,.2f}")
        print(f"   Tổng lợi nhuận:   {ret:+.2f}%")
        print(f"   Sharpe (hourly):  {sharpe:.2f}")
        print(f"   Max Drawdown:     {max_dd:.2f}%")
        print(f"   Tổng trades:      {len(trades)}")
        print(f"   Win rate:         {win_rate:.1f}%")
        print(f"   Profit factor:    {pf:.2f}" if pf != float("inf") else "   Profit factor:    ∞")
        print(f"   Circuit breakers: {len(self.circuit_breakers)}")
        print(f"   Signal phân bố:   {sig_counts}")
        print(f"   Exit reasons:     {reasons}")

        # Theo năm
        eq_df = pl.DataFrame({
            "timestamp": [e[0] for e in self.equity_curve],
            "equity": [e[1] for e in self.equity_curve],
        }).with_columns(pl.col("timestamp").dt.year().alias("year"))
        print("\n📅 PHÂN BỐ THEO NĂM")
        for row in eq_df.group_by("year").agg(
            pl.col("equity").first().alias("start"),
            pl.col("equity").last().alias("end"),
        ).sort("year").iter_rows(named=True):
            y_ret = (row["end"] / row["start"] - 1) * 100
            print(f"   {row['year']}: ${row['start']:>10,.2f} → ${row['end']:>10,.2f}  ({y_ret:+.2f}%)")

        # Trades gần nhất
        if trades:
            print("\n🧾 10 TRADES GẦN NHẤT")
            for t in trades[-10:]:
                pnl = t.get("pnl", 0)
                mark = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
                reason = t.get("reason") or "signal"
                print(f"   {mark} {str(t.get('side','?')):<4} {t.get('quantity',0):.4f} "
                      f"@ ${t.get('entry_price',0):>10,.2f} pnl ${pnl:+.2f} "
                      f"({t.get('pnl_pct',0):+.1f}%) [{reason}] "
                      f"{str(t.get('exit_time') or t.get('entry_time'))[:19]}")

        # Save
        out = ROOT / "data" / "full_system_backtest.json"
        out.write_text(json.dumps({
            "symbol": SYMBOL, "timeframe": TIMEFRAME, "exchange": EXCHANGE,
            "initial_capital": INITIAL_CAPITAL, "final_equity": float(final),
            "total_return_pct": float(ret), "sharpe": float(sharpe),
            "max_drawdown_pct": float(max_dd), "trades": len(trades),
            "win_rate_pct": float(win_rate),
            "equity_curve": [{"t": str(t), "e": e} for t, e in self.equity_curve],
            "trade_log": self.trade_log, "signals": self.signal_log,
            "circuit_breakers": self.circuit_breakers,
        }, indent=2, default=str))
        print(f"\n💾 Saved → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--freq", type=int, default=4, help="Agent decision mỗi N bar (mặc định 4h)")
    ap.add_argument("--fresh", action="store_true", help="Reset paper state")
    args = ap.parse_args()

    sim = FullSystemSimulator(fresh=args.fresh)
    sim.run(start=args.start, end=args.end, freq=args.freq)
