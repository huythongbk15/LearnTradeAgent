"""
Risk Controller — real-time risk management layer.

Chạy tự động sau mỗi lần update giá để kiểm tra:
- Max drawdown limit
- Daily loss limit
- Position concentration limit
- Circuit breaker (kill switch)
- Cooldown after stop-loss
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from trading_agent.execution.engine import ExecutionEngine

logger = logging.getLogger(__name__)

# ── Default limits ────────────────────────────────────────────────────────

DEFAULT_MAX_DRAWDOWN_PCT = 0.15       # 15% max drawdown → kill
DEFAULT_DAILY_LOSS_LIMIT_PCT = 0.08  # 8% loss in a day → halt
DEFAULT_MAX_POSITION_PCT = 0.50      # 50% max in one position
DEFAULT_STOP_LOSS_PCT = 0.05         # 5% default stop-loss
DEFAULT_COOLDOWN_HOURS = 24          # 24h cooldown after stop-loss trigger


class RiskController:
    """Real-time risk management system.

    Monitors positions and portfolio continuously.
    Can force-close positions when risk limits are breached.

    Usage:
        rc = RiskController(engine)
        rc.check_all()  # called after each price update
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        *,
        max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_PCT,
        daily_loss_limit_pct: float = DEFAULT_DAILY_LOSS_LIMIT_PCT,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        default_stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
    ):
        self.engine = engine
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_position_pct = max_position_pct
        self.default_stop_loss_pct = default_stop_loss_pct
        self.cooldown_hours = cooldown_hours

        # State
        self._peak_equity: float = engine.exchange.get_total_equity()
        self._initial_equity: float = engine.exchange.get_total_equity()
        self._daily_start_equity: float = engine.exchange.get_total_equity()
        self._last_trade_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_reason: str | None = None
        self._cooldown_until: datetime | None = None
        self._alerts: list[str] = []

    # ── Main check ─────────────────────────────────────────────────────

    def check_all(self) -> list[str]:
        """Run all risk checks. Returns list of triggered warnings.

        Call this after every price update.
        """
        self._alerts = []
        equity = self.engine.exchange.get_total_equity()

        # Update peak
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Update daily tracking
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_trade_date:
            self._daily_start_equity = equity
            self._last_trade_date = today

        # Skip if circuit breaker is active
        if self._circuit_breaker_active:
            self._alerts.append(f"⚠️  CIRCUIT BREAKER ACTIVE: {self._circuit_breaker_reason}")
            return self._alerts

        # Skip if in cooldown
        if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
            self._alerts.append(f"⏳ Cooldown: {remaining:.1f}h remaining")
            return self._alerts
        elif self._cooldown_until:
            self._cooldown_until = None

        # Run individual checks
        self._check_max_drawdown(equity)
        self._check_daily_loss(equity)
        self._check_position_concentration()

        return self._alerts

    # ── Individual checks ──────────────────────────────────────────────

    def _check_max_drawdown(self, equity: float):
        """Check if drawdown from peak exceeds limit."""
        if self._peak_equity <= 0:
            return
        drawdown = (self._peak_equity - equity) / self._peak_equity
        if drawdown >= self.max_drawdown_pct:
            msg = (f"🔴 MAX DRAWDOWN TRIGGERED: {drawdown * 100:.1f}% "
                   f"(limit: {self.max_drawdown_pct * 100:.0f}%)")
            self._alerts.append(msg)
            logger.warning(msg)
            self._activate_circuit_breaker(f"Max drawdown {drawdown * 100:.1f}%")

    def _check_daily_loss(self, equity: float):
        """Check if daily loss exceeds limit."""
        if self._daily_start_equity <= 0:
            return
        daily_loss = (self._daily_start_equity - equity) / self._daily_start_equity
        if daily_loss >= self.daily_loss_limit_pct:
            msg = (f"🔴 DAILY LOSS LIMIT: {daily_loss * 100:.1f}% "
                   f"(limit: {self.daily_loss_limit_pct * 100:.0f}%)")
            self._alerts.append(msg)
            logger.warning(msg)
            self._activate_circuit_breaker(f"Daily loss {daily_loss * 100:.1f}%")

    def _check_position_concentration(self):
        """Check if any single position exceeds max percent."""
        equity = self.engine.exchange.get_total_equity()
        if equity <= 0:
            return
        for pos in self.engine.exchange.get_all_positions():
            pos_pct = pos.market_value / equity
            if pos_pct > self.max_position_pct:
                msg = (f"⚠️ Position concentration: {pos.symbol} is {pos_pct * 100:.0f}% "
                       f"of portfolio (limit: {self.max_position_pct * 100:.0f}%)")
                self._alerts.append(msg)
                logger.warning(msg)

    # ── Circuit breaker ────────────────────────────────────────────────

    def _activate_circuit_breaker(self, reason: str):
        """Activate kill switch — close all positions and halt trading."""
        if self._circuit_breaker_active:
            return

        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self.engine.close_all(reason=f"circuit_breaker: {reason}")
        self._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=self.cooldown_hours)

        logger.warning(f"CIRCUIT BREAKER ACTIVATED: {reason}")

    def reset_circuit_breaker(self):
        """Manual reset after review."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._cooldown_until = None
        self._peak_equity = self.engine.exchange.get_total_equity()
        logger.info("Circuit breaker reset manually")

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Get current risk status summary."""
        equity = self.engine.exchange.get_total_equity()
        drawdown = ((self._peak_equity - equity) / self._peak_equity * 100) if self._peak_equity > 0 else 0
        daily_loss = ((self._daily_start_equity - equity) / self._daily_start_equity * 100) if self._daily_start_equity > 0 else 0

        return {
            "circuit_breaker_active": self._circuit_breaker_active,
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "peak_equity": round(self._peak_equity, 2),
            "current_equity": round(equity, 2),
            "drawdown_pct": round(drawdown, 2),
            "daily_loss_pct": round(daily_loss, 2),
            "max_drawdown_limit_pct": self.max_drawdown_pct * 100,
            "daily_loss_limit_pct": self.daily_loss_limit_pct * 100,
            "cooldown_active": bool(self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until),
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "warnings": self._alerts,
        }

    def set_stop_loss_on_all_positions(self, stop_pct: float | None = None):
        """Set stop-loss on all open positions."""
        pct = stop_pct or self.default_stop_loss_pct
        for pos in self.engine.exchange.get_all_positions():
            pos.stop_loss = pos.entry_price * (1 - pct)
            logger.info(f"Stop-loss set: {pos.symbol} @ {pos.stop_loss:.2f}")
