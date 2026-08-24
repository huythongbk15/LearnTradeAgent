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
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Tắt toàn bộ log INFO/WARNING của thư viện (giữ console output sạch)
logging.disable(logging.CRITICAL)

# TẮT LLM — dùng rule-based fallback cho tốc độ
os.environ["USE_LLM"] = os.environ.get("USE_LLM", "false")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import polars as pl

from trading_agent.agents.base import AgentMessage
from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution import risk_controller as rc_module
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.instrument_registry import (
    get_instrument_rules,
)
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.risk_controller import RiskController
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover


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


def _timeframe_delta(timeframe: str) -> timedelta:
    """Convert a compact timeframe such as 15m/1h/4h/1d to a duration."""
    if len(timeframe) < 2 or not timeframe[:-1].isdigit():
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    amount = int(timeframe[:-1])
    unit = timeframe[-1].lower()
    if amount <= 0 or unit not in {"m", "h", "d"}:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class FullSystemSimulator:
    def __init__(
        self,
        fresh: bool = False,
        symbol: str | None = None,
        timeframe: str | None = None,
        state_dir: str | Path | None = None,
        report_path: str | Path | None = None,
        run_id: str | None = None,
        allow_new_exposure: bool = True,
        state_flush_bars: int = 100,
        data_manifest_id: str | None = None,
    ):
        self.symbol = symbol or os.getenv("SYMBOL", SYMBOL)
        self.timeframe = timeframe or os.getenv("TIMEFRAME", TIMEFRAME)
        self.exchange = EXCHANGE
        self.timeframe_delta = _timeframe_delta(self.timeframe)

        safe_symbol = self.symbol.replace("/", "_").replace(":", "_")
        resolved_run_id = (
            run_id
            or os.getenv("BACKTEST_RUN_ID")
            or datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        )
        self.run_id = resolved_run_id
        if state_dir is None:
            self.run_dir = (
                ROOT
                / "data"
                / "backtests"
                / "full_system"
                / resolved_run_id
                / safe_symbol
            )
            self.state_dir = self.run_dir / "execution"
        else:
            self.state_dir = Path(state_dir).resolve()
            self.run_dir = self.state_dir.parent
        self.report_path = (
            Path(report_path).resolve()
            if report_path is not None
            else self.run_dir / "report.json"
        )

        if fresh and self.state_dir.exists() and any(self.state_dir.iterdir()):
            backup = self.state_dir.with_name(
                f"{self.state_dir.name}.bak-"
                f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S_%fZ')}"
            )
            self.state_dir.rename(backup)
            print(f"🗑  Paper state reset (backup → {backup.name})")
        self.state_dir.mkdir(parents=True, exist_ok=True)

        data_path = (
            config.storage_abs_path
            / self.exchange
            / safe_symbol
            / f"{self.timeframe}.parquet"
        )
        self.data_manifest_id = data_manifest_id or _sha256_file(data_path)

        # Load data
        print(f"📥 Loading {self.symbol} {self.timeframe} from {self.exchange}...")
        self.df = load_ohlcv(self.exchange, self.symbol, self.timeframe).sort(
            "timestamp"
        )
        print(
            f"   {self.df.height} bars: {self.df['timestamp'].min()} → {self.df['timestamp'].max()}"
        )

        # Initialize strategy
        strategy_params = {
            "fast_period": FAST_MA,
            "slow_period": SLOW_MA,
            "adx_threshold": ADX_THRESHOLD,
            "atr_sl_mult": ATR_SL_MULT,
            "atr_tp_mult": ATR_TP_MULT,
        }
        self.strategy = EnhancedMaCrossover(strategy_params)
        feature_identity = json.dumps(
            {
                "data_manifest_id": self.data_manifest_id,
                "strategy": "enhanced_ma",
                "params": strategy_params,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.feature_artifact_id = (
            f"sha256:{hashlib.sha256(feature_identity).hexdigest()}"
        )

        # Pre-compute indicators on full dataset
        print("🔧 Computing strategy indicators...")
        self.df = self.strategy.compute_indicators(self.df)

        # Generate all signals upfront
        print("🔧 Generating signals...")
        signal_series = self.strategy.generate_signals(self.df).rename("signal")
        if "signal" not in self.df.columns:
            self.df = self.df.with_columns(signal_series)
        self.signals = self.df.select(pl.col("signal")).to_series().to_list()

        # Khởi tạo execution engine + risk controller
        self.engine = ExecutionEngine(
            exchange_name=EXCHANGE,
            initial_capital=INITIAL_CAPITAL,
            instrument_rules=get_instrument_rules(self.symbol),
            state_dir=self.state_dir,
            event_store_path=self.state_dir / "events.db",
            allow_backtest_new_exposure=allow_new_exposure,
            paper_price_persist_interval=state_flush_bars,
            disable_paper_telemetry=True,
        )
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
        self._entry_state: dict[str, dict] = {}

    def _position_pct(self, price: float) -> float:
        """% portfolio đang nằm trong vị thế."""
        pos = self.engine.exchange.get_position(self.symbol)
        if not pos or not pos.is_active:
            return 0.0
        equity = self.engine.exchange.get_total_equity()
        return (pos.quantity * price) / equity if equity > 0 else 0.0

    def run(
        self, start: int = 0, end: int | None = None, freq: int = 1
    ) -> dict[str, object]:
        if start < 0 or freq <= 0:
            raise ValueError("start must be non-negative and freq must be positive")
        end = end if end is not None else self.df.height
        end = min(end, self.df.height)
        if end <= start:
            raise ValueError(f"Invalid bar window: start={start}, end={end}")
        n = end - start
        print(f"🚀 Simulating bars {start}→{end} ({n} bars, decision mỗi {freq}h)")
        print(
            f"   SL={STOP_LOSS_PCT:.0%} TP={TAKE_PROFIT_PCT:.0%} Trail={TRAILING_STOP_PCT:.0%} | Cooldown={COOLDOWN_HOURS:.0f}h\n"
        )

        for i in range(start, end):
            row = self.df.row(i, named=True)
            ts = row["timestamp"]
            price = float(row["open"])
            signal_index = i - 1
            decision_row = self.df.row(signal_index, named=True) if i > start else row
            decision_ts = decision_row["timestamp"]
            signal = (
                int(self.signals[signal_index])
                if i > start and self.signals[signal_index] is not None
                else 0
            )

            # The prior closed bar may execute only at this bar's open.
            bar_open_at = datetime.fromisoformat(str(ts))
            if bar_open_at.tzinfo is None:
                bar_open_at = bar_open_at.replace(tzinfo=UTC)
            else:
                bar_open_at = bar_open_at.astimezone(UTC)
            bar_close_at = bar_open_at + self.timeframe_delta
            decision_bar_open_at = datetime.fromisoformat(str(decision_ts))
            if decision_bar_open_at.tzinfo is None:
                decision_bar_open_at = decision_bar_open_at.replace(tzinfo=UTC)
            else:
                decision_bar_open_at = decision_bar_open_at.astimezone(UTC)
            decision_bar_close_at = decision_bar_open_at + self.timeframe_delta
            _SimClock.current = bar_open_at

            # 1. Mark at the next tradable open before any decision.
            self.engine.update_prices({self.symbol: price})

            # 2. Risk checks (max DD, daily loss, circuit breaker)
            alerts = self.risk.check_all()
            breaker_on = any("CIRCUIT BREAKER ACTIVE" in a for a in alerts)
            if breaker_on and not self._breaker_active:
                self.circuit_breakers.append(f"{ts}: {alerts[0]}")
                print(
                    f"   🚨 CIRCUIT BREAKER ON @ {ts} — đóng toàn bộ vị thế, tạm dừng {COOLDOWN_HOURS:.0f}h"
                )
            elif not breaker_on and self._breaker_active:
                print(f"   ✅ CIRCUIT BREAKER OFF @ {ts} — giao dịch trở lại")
            self._breaker_active = breaker_on

            # 3. Execute signal theo chu kỳ (chỉ khi chưa bị chặn)
            if (
                i > start
                and signal_index % freq == 0
                and not breaker_on
                and not any("Cooldown" in a for a in alerts)
            ):
                equity = self.engine.exchange.get_total_equity()
                pos_pct = self._position_pct(price)

                # Only act on crossover signals (non-zero)
                if signal != 0:
                    # Calculate position size using risk controller dynamic sizing
                    # Get ATR for this bar
                    atr = (
                        float(decision_row.get("atr", 0))
                        if decision_row.get("atr")
                        else None
                    )

                    # Get regime info
                    regime_info = {
                        "vol_regime": decision_row.get("vol_regime"),
                        "trend_regime": decision_row.get("trend_regime"),
                        "trend_dir": decision_row.get("trend_dir"),
                        "adx": decision_row.get("adx"),
                        "atr_pctl": decision_row.get("atr_pctl"),
                    }

                    position = self.engine.exchange.get_position(self.symbol)
                    max_target_exposure = 0.0
                    if signal == 1:  # BUY
                        if position and position.is_active and position.quantity > 0:
                            signal = 0  # Already long, skip
                        else:
                            pos_size = self.risk.calculate_position_size(
                                symbol=self.symbol,
                                price=price,
                                atr=atr,
                                regime_info=regime_info
                                if any(v is not None for v in regime_info.values())
                                else None,
                            )
                            if pos_size <= 0:
                                signal = 0
                            else:
                                max_target_exposure = min(
                                    MAX_POS_SIZE_PCT,
                                    pos_size * price / equity,
                                )
                    elif signal == -1:  # SELL (exit to flat)
                        if (
                            not position
                            or not position.is_active
                            or position.quantity <= 0
                        ):
                            # No position to exit, skip SELL signal
                            signal = 0
                        else:
                            # Exit signal - use full position size to flatten
                            max_target_exposure = 0.0  # target 0 exposure

                    if signal != 0:
                        msg = AgentMessage(
                            role="trader",
                            signal="BUY" if signal == 1 else "SELL",
                            confidence=0.65,
                            reasoning=f"enhanced_ma: MA{FAST_MA}/{SLOW_MA} crossover with ADX>{ADX_THRESHOLD}",
                            details={
                                "symbol": self.symbol,
                                "strategy": "enhanced_ma",
                            },
                            max_position_size_pct=max_target_exposure,
                            risk_level="medium",
                        )

                        self.signal_log.append(
                            {
                                "timestamp": str(decision_ts),
                                "executed_at": str(ts),
                                "price": price,
                                "position_pct": pos_pct,
                                "signal": msg.signal,
                                "confidence": msg.confidence,
                                "risk": msg.risk_level,
                                "max_pos": msg.max_position_size_pct,
                            }
                        )

                        # Build observation from current bar for canonical pipeline
                        observation = EnrichedMarketObservation(
                            observation_id=f"obs-{self.symbol}-{signal_index}",
                            symbol=self.symbol,
                            observed_at=decision_bar_close_at,
                            open=float(decision_row["open"]),
                            high=float(decision_row["high"]),
                            low=float(decision_row["low"]),
                            close=float(decision_row["close"]),
                            volume=float(decision_row.get("volume", 0.0)),
                            features={
                                k: float(decision_row[k])
                                for k in ["fast_ma", "slow_ma", "adx", "atr"]
                                if k in decision_row and decision_row[k] is not None
                            },
                            venue=self.exchange,
                            source="historical_parquet",
                            timeframe=self.timeframe,
                            bar_open_at=decision_bar_open_at,
                            bar_close_at=decision_bar_close_at,
                            is_closed=True,
                            data_manifest_id=self.data_manifest_id,
                            feature_artifact_id=self.feature_artifact_id,
                        )

                        # Execute
                        for order in self.engine.execute_signal(
                            msg, observation=observation
                        ):
                            pos = self.engine.exchange.get_position(self.symbol)
                            side = order.side.value
                            amount = float(order.filled_amount or order.amount)
                            if side == "buy":
                                self._entry_state[self.symbol] = {
                                    "price": price,
                                    "amount": amount,
                                }
                                pnl = 0.0
                            elif side == "sell":
                                entry = self._entry_state.pop(self.symbol, None)
                                if entry:
                                    pnl = (price - entry["price"]) * entry["amount"]
                                else:
                                    pnl = 0.0
                            else:
                                pnl = 0.0
                            self.trade_log.append(
                                {
                                    "timestamp": str(ts),
                                    "side": side,
                                    "amount": amount,
                                    "price": price,
                                    "pnl": pnl,
                                    "equity": float(
                                        self.engine.exchange.get_total_equity()
                                    ),
                                }
                            )
                            side = "🟢 BUY" if order.side.value == "buy" else "🔴 SELL"
                            print(
                                f"   {side} {order.filled_amount or order.amount:.4f} @ ${price:,.2f} @ {ts}"
                            )

            # 4. Simulate adverse excursion, then mark at this bar's close.
            _SimClock.current = bar_close_at
            low_price = float(row["low"])
            close_price = float(row["close"])
            if low_price < price:
                self.engine.update_prices({self.symbol: low_price})
            self.engine.update_prices({self.symbol: close_price})
            self.equity_curve.append(
                (bar_close_at.isoformat(), self.engine.exchange.get_total_equity())
            )

            # Progress
            if (i - start) % 2000 == 0 and i > start:
                eq = self.equity_curve[-1][1]
                print(
                    f"   ... {i - start}/{n} bars — equity ${eq:,.2f} "
                    f"({(eq / INITIAL_CAPITAL - 1) * 100:+.2f}%)"
                )

        print("\n✅ Simulation complete")
        self.engine.exchange.flush_state()
        return self._report()

    # ── Báo cáo ────────────────────────────────────────────────────────
    def _report(self) -> dict[str, object]:
        if not self.equity_curve:
            print("❌ No data")
            return {}

        self.trade_log = [
            trade.to_dict()
            for trade in self.engine.exchange.get_trade_history(limit=1_000_000)
        ]
        eq_values = np.array([e[1] for e in self.equity_curve])
        equity_with_initial = np.concatenate(([INITIAL_CAPITAL], eq_values))
        prior_equity = equity_with_initial[:-1]
        returns = np.diff(equity_with_initial) / prior_equity
        returns = returns[np.isfinite(returns)]

        total_return = (eq_values[-1] / INITIAL_CAPITAL - 1) * 100
        periods_per_year = timedelta(days=365).total_seconds() / (
            self.timeframe_delta.total_seconds()
        )
        sharpe = (
            (returns.mean() / returns.std() * np.sqrt(periods_per_year))
            if len(returns) > 1 and returns.std() > 0
            else 0
        )
        running_peak = np.maximum.accumulate(equity_with_initial)
        max_dd = float(
            np.max((running_peak - equity_with_initial) / running_peak) * 100
        )

        wins = [t for t in self.trade_log if t.get("pnl", 0) > 0]
        losses = [t for t in self.trade_log if t.get("pnl", 0) < 0]
        win_rate = len(wins) / len(self.trade_log) * 100 if self.trade_log else 0
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 0
        gross_profit = float(sum(t["pnl"] for t in wins))
        gross_loss = float(abs(sum(t["pnl"] for t in losses)))
        profit_factor: float | None = (
            gross_profit / gross_loss if gross_loss > 0 else None
        )

        print(f"\n{'=' * 55}")
        print(
            f"📊 KETA QUẢ FULL SYSTEM — {self.symbol} {self.timeframe} ({self.exchange})"
        )
        print(f"{'=' * 55}")
        print(f"   Vốn ban đầu:      ${INITIAL_CAPITAL:,.2f}")
        print(f"   Vốn cuối:         ${eq_values[-1]:,.2f}")
        print(f"   Tổng lợi nhuận:   {total_return:+.2f}%")
        print(f"   Sharpe (hourly):  {sharpe:.2f}")
        print(f"   Max Drawdown:     {max_dd:.2f}%")
        print(f"   Tổng trades:      {len(self.trade_log)}")
        print(f"   Win rate:         {win_rate:.1f}%")
        print(f"   Avg win:          ${avg_win:,.2f}")
        print(f"   Avg loss:         ${avg_loss:,.2f}")
        profit_factor_display = (
            f"{profit_factor:.2f}" if profit_factor is not None else "N/A"
        )
        print(f"   Profit factor:    {profit_factor_display}")
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
        open_positions = self.engine.exchange.get_all_positions()
        open_orders = self.engine.exchange.get_open_orders()
        protected_symbols = {
            order.symbol
            for order in open_orders
            if order.side.value == "sell"
            and order.type.value in {"stop_loss", "stop_loss_limit"}
        }
        unprotected_positions = sorted(
            position.symbol
            for position in open_positions
            if position.quantity > 0 and position.symbol not in protected_symbols
        )
        lifecycle_state = self.engine.lifecycle.state
        manual_intent_ids = sorted(lifecycle_state.unresolved_manual_intents)
        unknown_order_ids = sorted(
            intent_id
            for intent_id, order in lifecycle_state.orders.items()
            if order.status.value == "manual"
        )
        execution_health = {
            "status": lifecycle_state.execution_health.value,
            "unknown_orders": len(unknown_order_ids),
            "unknown_order_ids": unknown_order_ids,
            "manual_interventions": len(manual_intent_ids),
            "manual_intent_ids": manual_intent_ids,
            "active_sell_reservations": float(
                self.engine.lifecycle.active_sell_reservations()
            ),
            "unprotected_positions": unprotected_positions,
        }
        run_passed = (
            execution_health["status"] == "normal"
            and not unknown_order_ids
            and not manual_intent_ids
            and not unprotected_positions
        )

        print("\n🧾 10 TRADES GẦN NHẤT")
        for t in self.trade_log[-10:]:
            pnl = t.get("pnl", 0)
            side = "🟢" if pnl > 0 else "🔴"
            exit_price = float(t.get("exit_price") or 0.0)
            print(
                f"   {side} {t['side']} {t['quantity']:.4f} "
                f"${t['entry_price']:,.2f} → ${exit_price:,.2f} "
                f"pnl ${pnl:+.2f} ({t.get('pnl_pct', 0.0):+.1f}%) "
                f"[{t.get('reason') or 'signal'}] "
                f"{str(t.get('exit_time') or '')[:19]}"
            )

        report: dict[str, object] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "status": "passed" if run_passed else "failed",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "exchange": EXCHANGE,
            "initial_capital": INITIAL_CAPITAL,
            "final_equity": float(eq_values[-1]),
            "total_return_pct": float(total_return),
            "sharpe": float(sharpe),
            "max_drawdown_pct": float(max_dd),
            "total_trades": len(self.trade_log),
            "win_rate_pct": float(win_rate),
            "profit_factor": profit_factor,
            "circuit_breakers": len(self.circuit_breakers),
            "signals_seen": len(self.signal_log),
            "open_positions": len(open_positions),
            "open_orders": len(open_orders),
            "execution_timing": "decision_on_closed_bar_execute_next_bar_open",
            "execution_health": execution_health,
            "data_manifest_id": self.data_manifest_id,
            "feature_artifact_id": self.feature_artifact_id,
            "state_dir": str(self.state_dir),
            "report_path": str(self.report_path),
            "equity_curve": [[str(ts), float(eq)] for ts, eq in self.equity_curve],
            "trades": self.trade_log,
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with self.report_path.open("w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
        print(f"\n💾 Saved → {self.report_path}")
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fresh", action="store_true", help="Reset paper state trước khi chạy"
    )
    parser.add_argument("--start", type=int, default=0, help="Bar bắt đầu (mặc định 0)")
    parser.add_argument(
        "--end", type=int, default=None, help="Bar kết thúc (mặc định hết)"
    )
    parser.add_argument(
        "--freq", type=int, default=1, help="Phân tích mỗi N bar (mặc định 1h)"
    )
    parser.add_argument("--symbol", default=None, help="Symbol, vd BTC/USDT")
    parser.add_argument("--timeframe", default=None, help="Timeframe, vd 1h/4h")
    parser.add_argument(
        "--state-dir", default=None, help="Isolated paper state directory"
    )
    parser.add_argument("--report-path", default=None, help="Output JSON report path")
    parser.add_argument("--run-id", default=None, help="Stable identifier for this run")
    parser.add_argument(
        "--state-flush-bars",
        type=int,
        default=int(os.getenv("BACKTEST_STATE_FLUSH_BARS", "100")),
        help="Persist mark-to-market state every N bars",
    )
    parser.add_argument(
        "--tail-bars",
        type=int,
        default=None,
        help="Run only the most recent N bars",
    )
    parser.add_argument(
        "--allow-new-exposure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Explicitly authorize new exposure for this paper backtest only",
    )
    args = parser.parse_args()

    sim = FullSystemSimulator(
        fresh=args.fresh,
        symbol=args.symbol,
        timeframe=args.timeframe,
        state_dir=args.state_dir,
        report_path=args.report_path,
        run_id=args.run_id,
        allow_new_exposure=args.allow_new_exposure,
        state_flush_bars=args.state_flush_bars,
    )
    start = args.start
    if args.tail_bars is not None:
        if args.tail_bars <= 0:
            parser.error("--tail-bars must be positive")
        if args.start != 0 or args.end is not None:
            parser.error("--tail-bars cannot be combined with --start/--end")
        start = max(0, sim.df.height - args.tail_bars)
    report = sim.run(start=start, end=args.end, freq=args.freq)
    if report.get("status") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
