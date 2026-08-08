"""
Risk Controller — real-time risk management layer.

Chạy tự động sau mỗi lần update giá để kiểm tra:
- Max drawdown limit
- Daily loss limit
- Position concentration limit
- Circuit breaker (kill switch)
- Cooldown after stop-loss
- Dynamic position sizing (Half-Kelly + Vol Targeting)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from trading_agent.execution.engine import ExecutionEngine
from trading_agent.risk.position_sizer import PositionSizer, PositionSizingParams

logger = logging.getLogger(__name__)

# ── Default limits ────────────────────────────────────────────────────────

DEFAULT_MAX_DRAWDOWN_PCT = 0.15       # 15% max drawdown → kill
DEFAULT_DAILY_LOSS_LIMIT_PCT = 0.08  # 8% loss in a day → halt
DEFAULT_MAX_POSITION_PCT = 0.50      # 50% max in one position
DEFAULT_STOP_LOSS_PCT = 0.05         # 5% default stop-loss
DEFAULT_TAKE_PROFIT_PCT = 0.15       # 15% default take-profit (RR ~ 1:3)
DEFAULT_TRAILING_STOP_PCT = 0.07     # 7% default trailing stop
DEFAULT_COOLDOWN_HOURS = 24          # 24h cooldown after stop-loss trigger


class RiskController:
    """Real-time risk management system.

    Monitors positions and portfolio continuously.
    Can force-close positions when risk limits are breached.
    Calculates dynamic position sizes using Half-Kelly + Vol Targeting.

    Usage:
        rc = RiskController(engine)
        rc.check_all()  # called after each price update
        size = rc.calculate_position_size(symbol, price, atr, regime_info)
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        *,
        max_drawdown_pct: float = DEFAULT_MAX_DRAWDOWN_PCT,
        daily_loss_limit_pct: float = DEFAULT_DAILY_LOSS_LIMIT_PCT,
        max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
        default_stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        default_take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
        default_trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT,
        cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
        # Position sizing config
        position_sizing_method: str = "half_kelly",
        target_annual_vol: float = 0.15,
        kelly_fraction: float = 0.5,
    ):
        self.engine = engine
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_position_pct = max_position_pct
        self.default_stop_loss_pct = default_stop_loss_pct
        self.default_take_profit_pct = default_take_profit_pct
        self.default_trailing_stop_pct = default_trailing_stop_pct
        self.cooldown_hours = cooldown_hours

        # Dynamic position sizer
        self.position_sizer = PositionSizer(PositionSizingParams(
            method=position_sizing_method,
            kelly_fraction=kelly_fraction,
            target_annual_vol=target_annual_vol,
            max_position_pct=max_position_pct,
            max_portfolio_heat=0.8,
        ))

        # Trade history for Kelly calculation
        self._trade_history: list[dict] = []

        # State
        self._peak_equity: float = engine.exchange.get_total_equity()
        self._initial_equity: float = engine.exchange.get_total_equity()
        self._daily_start_equity: float = engine.exchange.get_total_equity()
        self._last_trade_date: str = datetime.now(UTC).strftime("%Y-%m-%d")
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
        self._peak_equity = max(self._peak_equity, equity)

        # Update daily tracking
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._last_trade_date:
            self._daily_start_equity = equity
            self._last_trade_date = today

        # Auto-reset circuit breaker sau khi cooldown hết hạn
        if self._circuit_breaker_active and self._cooldown_until and datetime.now(UTC) >= self._cooldown_until:
            self._circuit_breaker_active = False
            self._circuit_breaker_reason = None
            self._cooldown_until = None
            self._peak_equity = self.engine.exchange.get_total_equity()
            logger.warning("Circuit breaker auto-reset after cooldown (new baseline)")

        # Skip if circuit breaker is active
        if self._circuit_breaker_active:
            self._alerts.append(f"⚠️  CIRCUIT BREAKER ACTIVE: {self._circuit_breaker_reason}")
            return self._alerts

        # Skip if in cooldown
        if self._cooldown_until and datetime.now(UTC) < self._cooldown_until:
            remaining = (self._cooldown_until - datetime.now(UTC)).total_seconds() / 3600
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
        self._cooldown_until = datetime.now(UTC) + timedelta(hours=self.cooldown_hours)

        logger.warning(f"CIRCUIT BREAKER ACTIVATED: {reason}")

    def reset_circuit_breaker(self):
        """Manual reset after review."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        self._cooldown_until = None
        self._peak_equity = self.engine.exchange.get_total_equity()
        logger.info("Circuit breaker reset manually")

    # ── Dynamic Position Sizing ────────────────────────────────────────

    def record_trade(self, pnl: float, entry_price: float, exit_price: float, size: float):
        """Record a completed trade for Kelly/Optimal-f calculation."""
        self._trade_history.append({
            "pnl": pnl,
            "entry": entry_price,
            "exit": exit_price,
            "size": size,
            "return_pct": pnl / (entry_price * size) if entry_price * size != 0 else 0,
            "win": pnl > 0,
        })
        # Update position sizer
        self.position_sizer.update_trade(pnl, entry_price, exit_price, size)

    def calculate_position_size(
        self,
        symbol: str,
        price: float,
        atr: Optional[float] = None,
        regime_info: Optional[dict] = None,
    ) -> float:
        """
        Calculate dynamic position size for a new trade.
        
        Uses volatility targeting with regime adjustments.
        
        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")
            price: Current price
            atr: ATR value for stop-loss sizing
            regime_info: Dict with vol_regime, trend_regime, trend_dir, adx, atr_pctl
            
        Returns:
            Position size in base currency units (e.g., BTC amount)
        """
        equity = self.engine.exchange.get_total_equity()
        current_portfolio_value = sum(
            pos.market_value for pos in self.engine.exchange.get_all_positions()
        )
        
        # Base: volatility targeting
        # Estimate daily vol from ATR percentile or use default
        if regime_info and regime_info.get("atr_pctl") is not None:
            atr_pctl = regime_info["atr_pctl"]
            # Map ATR percentile to daily vol estimate (30%-90% annual = ~2%-6% daily)
            est_daily_vol = 0.02 + atr_pctl * 0.04
        else:
            est_daily_vol = 0.03  # 3% daily default
        
        target_daily_vol = 0.15 / (252 ** 0.5)  # ~0.94% daily for 15% annual
        vol_scale = min(target_daily_vol / est_daily_vol, 2.0) if est_daily_vol > 0 else 1.0
        
        # Base position: 25% of equity, vol-adjusted
        base_pct = 0.25 * vol_scale
        
        # Regime adjustments
        if regime_info:
            trend_regime = regime_info.get("trend_regime", "ranging")
            vol_regime = regime_info.get("vol_regime", "mid_vol")
            
            if trend_regime == "trending":
                base_pct *= 1.3
            elif trend_regime == "ranging":
                base_pct *= 0.7
            
            if vol_regime == "high_vol":
                base_pct *= 0.6
            elif vol_regime == "low_vol":
                base_pct *= 1.2
        
        # Cap at max
        base_pct = max(0.05, min(base_pct, 0.40))  # 5%-40%
        
        # Risk amount
        risk_amount = equity * base_pct
        
        # ATR-based stop loss sizing
        if atr and atr > 0:
            risk_per_unit = atr * 2.0  # 2x ATR stop
            position_size = risk_amount / risk_per_unit
        else:
            position_size = risk_amount / price
        
        # Cap at max position %
        max_size = equity * 0.40 / price
        position_size = min(position_size, max_size)
        
        # Cap at available capital
        available_capital = equity - current_portfolio_value
        max_affordable = available_capital * 0.95 / price
        position_size = min(position_size, max_affordable)
        
        # Cap at portfolio heat limit
        max_heat_value = equity * 0.85
        if current_portfolio_value + position_size * price > max_heat_value:
            position_size = max(0, (max_heat_value - current_portfolio_value) / price)
        
        # Minimum size
        min_size = equity * 0.005 / price
        if position_size < min_size:
            return 0.0
        
        return position_size

    def _estimate_kelly_params(self, regime_info: Optional[dict]) -> tuple:
        """Estimate win_rate, avg_win, avg_loss based on regime."""
        if not regime_info:
            return 0.55, 0.035, 0.025
        
        trend_regime = regime_info.get("trend_regime", "ranging")
        vol_regime = regime_info.get("vol_regime", "mid_vol")
        
        if trend_regime == "trending":
            win_rate, avg_win, avg_loss = 0.58, 0.04, 0.03
        elif trend_regime == "ranging":
            win_rate, avg_win, avg_loss = 0.52, 0.025, 0.025
        else:
            win_rate, avg_win, avg_loss = 0.55, 0.035, 0.025
        
        if vol_regime == "high_vol":
            win_rate *= 0.9
            avg_loss *= 1.3
        elif vol_regime == "low_vol":
            win_rate *= 1.05
            avg_win *= 1.1
        
        return win_rate, avg_win, avg_loss

    def get_dynamic_stop_loss(self, entry_price: float, atr: float, regime_info: Optional[dict] = None) -> float:
        """Calculate dynamic stop loss based on ATR and regime."""
        mult = 2.0  # Base multiplier
        
        if regime_info:
            vol_regime = regime_info.get("vol_regime", "mid_vol")
            trend_regime = regime_info.get("trend_regime", "ranging")
            
            if vol_regime == "high_vol":
                mult = 2.5
            elif vol_regime == "low_vol":
                mult = 1.5
            
            if trend_regime == "trending":
                mult *= 0.9  # Tighter in trends
            elif trend_regime == "ranging":
                mult *= 1.1  # Wider in ranging
        
        return entry_price * (1 - mult * atr / entry_price)

    def get_dynamic_take_profit(self, entry_price: float, atr: float, regime_info: Optional[dict] = None) -> float:
        """Calculate dynamic take profit based on ATR and regime."""
        # Target 2:1 reward:risk ratio
        stop_mult = 2.0
        if regime_info:
            vol_regime = regime_info.get("vol_regime", "mid_vol")
            if vol_regime == "high_vol":
                stop_mult = 2.5
            elif vol_regime == "low_vol":
                stop_mult = 1.5
        
        return entry_price * (1 + stop_mult * atr / entry_price * 2)

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
            "cooldown_active": bool(self._cooldown_until and datetime.now(UTC) < self._cooldown_until),
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "warnings": self._alerts,
            "trade_count": len(self._trade_history),
            "position_sizing_method": self.position_sizer.params.method,
        }

    def set_stop_loss_on_all_positions(self, stop_pct: float | None = None):
        """Set stop-loss on all open positions."""
        pct = stop_pct or self.default_stop_loss_pct
        for pos in self.engine.exchange.get_all_positions():
            pos.stop_loss = pos.entry_price * (1 - pct)
            logger.info(f"Stop-loss set: {pos.symbol} @ {pos.stop_loss:.2f}")

    def set_take_profit_on_all_positions(self, tp_pct: float | None = None):
        """Set take-profit on all open positions (active profit taking)."""
        pct = tp_pct or self.default_take_profit_pct
        for pos in self.engine.exchange.get_all_positions():
            pos.take_profit = pos.entry_price * (1 + pct)
            logger.info(f"Take-profit set: {pos.symbol} @ {pos.take_profit:.2f}")

    def set_trailing_stop_on_all_positions(self, trail_pct: float | None = None):
        """Enable trailing stop on all open positions (ratchets SL as price rises)."""
        pct = trail_pct or self.default_trailing_stop_pct
        for pos in self.engine.exchange.get_all_positions():
            pos.trailing_stop_pct = pct
            logger.info(f"Trailing stop enabled: {pos.symbol} trail={pct:.2%}")

    def update_atr_trailing_stops(self, ohlcv_data: dict[str, Any]):
        """
        Update ATR-based trailing stops for all open positions.
        
        Uses compute_atr_trailing_stop to calculate dynamic trailing stops
        that ratchet in favorable direction.
        
        Parameters
        ----------
        ohlcv_data : dict[str, DataFrame]
            Symbol -> OHLCV DataFrame mapping with 'close', 'high', 'low' columns
        """
        from trading_agent.execution.indicators import compute_atr_trailing_stop
        
        for pos in self.engine.exchange.get_all_positions():
            if not pos.is_active:
                continue
            
            df = ohlcv_data.get(pos.symbol)
            if df is None or df.is_empty():
                continue
            
            # Determine side
            side = "long" if pos.side.value == "buy" else "short"
            
            # Use existing ATR multiplier from position metadata or default
            atr_mult = pos.metadata.get("trailing_atr_mult", 2.0)
            
            # Compute trailing stop
            trailing_series = compute_atr_trailing_stop(
                df, period=14, multiplier=atr_mult, side=side
            )
            
            # Get latest trailing stop level
            latest_stop = float(trailing_series.tail(1).item())
            if latest_stop and latest_stop > 0:
                if side == "long" and (pos.stop_loss is None or latest_stop > pos.stop_loss):
                    pos.stop_loss = latest_stop
                    pos.metadata["trailing_stop_type"] = "atr"
                    pos.metadata["trailing_atr_mult"] = atr_mult
                    logger.debug(f"ATR trailing stop updated: {pos.symbol} @ {latest_stop:.2f} (mult={atr_mult})")
                elif side == "short" and (pos.stop_loss is None or latest_stop < pos.stop_loss):
                    pos.stop_loss = latest_stop
                    pos.metadata["trailing_stop_type"] = "atr"
                    pos.metadata["trailing_atr_mult"] = atr_mult
                    logger.debug(f"ATR trailing stop updated: {pos.symbol} @ {latest_stop:.2f} (mult={atr_mult})")
