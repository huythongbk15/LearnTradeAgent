"""Shadow Mainnet Mode — real data, real config, real rules, NO orders.

Wave C — Execution State & Resilience.

Shadow mode replays the production execution pipeline against real mainnet
market data and production configuration but **never submits an order**:

* real mainnet market data (prices, bid/ask depth);
* real production config (symbols, strategies, sizing, exchange rules);
* simulated fills (slippage + fee model);
* shadow protective order state;
* shadow PnL;
* execution metrics;
* reality-gap comparison (simulated fill vs. subsequent market move).

Hard guard (triple):

1. config  — ``ShadowConfig.submit_orders`` must be False;
2. runtime — engine refuses to start unless ``SHADOW_MAINNET=1`` is set and
   asserts on every ``_submit_live_order`` call;
3. tests   — a monkeypatched live broker that raises proves no code path
   can reach order submission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable
import math
import os
import uuid

SHADOW_ENV_GUARD = "SHADOW_MAINNET"
SHADOW_ENV_VALUE = "1"


class ShadowModeError(RuntimeError):
    """Raised when shadow mode is misconfigured or tries to go live."""


class ShadowOrderStatus(str, Enum):
    INTENT = "intent"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class ExchangeRules:
    """Real exchange rules used for shadow validation."""

    min_qty: float
    step_size: float
    min_notional: float
    max_qty: float = math.inf
    tick_size: float = 0.01
    taker_fee: float = 0.001
    maker_fee: float = 0.0008

    def round_qty(self, qty: float) -> float:
        if self.step_size <= 0:
            return qty
        return math.floor(qty / self.step_size) * self.step_size

    def round_price(self, price: float) -> float:
        if self.tick_size <= 0:
            return price
        return round(price / self.tick_size) * self.tick_size


@dataclass
class ShadowConfig:
    """Shadow mode configuration. ``submit_orders`` must stay False."""

    enabled: bool = True
    submit_orders: bool = False  # HARD GUARD — never True in shadow mode
    require_env_guard: bool = True
    env_guard_var: str = SHADOW_ENV_GUARD
    env_guard_value: str = SHADOW_ENV_VALUE
    symbols: list[str] = field(default_factory=list)
    strategy_ids: list[str] = field(default_factory=list)
    initial_cash: float = 100_000.0
    commission: float = 0.001
    slippage: float = 0.0005
    max_price_age_seconds: float = 60.0
    exchange_rules: dict[str, ExchangeRules] = field(default_factory=dict)

    def validate(self, env: dict[str, str] | None = None) -> None:
        """Hard guard #1 (config) + #2 (runtime env assertion)."""
        if self.submit_orders:
            raise ShadowModeError(
                "shadow mainnet: submit_orders must be False — "
                "refusing to start with order submission enabled"
            )
        if self.require_env_guard:
            env = env if env is not None else os.environ
            if env.get(self.env_guard_var) != self.env_guard_value:
                raise ShadowModeError(
                    f"shadow mainnet: env {self.env_guard_var}={self.env_guard_value} "
                    "required — refusing to start without the explicit guard"
                )
        if self.initial_cash <= 0:
            raise ShadowModeError("initial_cash must be positive")


@dataclass
class ShadowOrder:
    order_id: str
    symbol: str
    side: str
    size: float
    strategy_id: str
    status: ShadowOrderStatus = ShadowOrderStatus.INTENT
    mid_at_intent: float = 0.0
    simulated_fill_price: float | None = None
    fee: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ShadowPosition:
    symbol: str
    side: str
    quantity: float = 0.0
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass
class RealityGapReport:
    """Reality-gap: simulated fill vs. actual subsequent market move."""

    fills: list[dict[str, Any]] = field(default_factory=list)

    def add_fill(
        self,
        symbol: str,
        side: str,
        size: float,
        sim_price: float,
        mid_after: list[float],
    ) -> None:
        self.fills.append(
            {
                "symbol": symbol,
                "side": side,
                "size": size,
                "sim_price": sim_price,
                "mid_after": list(mid_after),
                "realized_opportunity": [
                    round(mid - sim_price, 8) for mid in mid_after
                ],
            }
        )

    def summary(self) -> dict[str, Any]:
        if not self.fills:
            return {"fills": 0}
        total_gap = 0.0
        count = 0
        for fill in self.fills:
            if fill["mid_after"]:
                total_gap += abs(fill["mid_after"][-1] - fill["sim_price"])
                count += 1
        return {
            "fills": len(self.fills),
            "avg_abs_gap_to_last_mid": round(total_gap / count, 8) if count else None,
        }


class ShadowMainnetEngine:
    """Runs the production execution pipeline in shadow (no submission)."""

    def __init__(
        self,
        config: ShadowConfig,
        *,
        env: dict[str, str] | None = None,
        price_source: Callable[[str], float | None] | None = None,
    ):
        config.validate(env=env)
        self.config = config
        self._price_source = price_source or (lambda symbol: None)
        self.cash = float(config.initial_cash)
        self.orders: dict[str, ShadowOrder] = {}
        self.positions: dict[str, ShadowPosition] = {}
        self._prices: dict[str, float] = {}
        self._bids: dict[str, float] = {}
        self._asks: dict[str, float] = {}
        self._price_ts: dict[str, datetime] = {}
        self._mid_history: dict[str, list[float]] = {}
        self.reality_gap = RealityGapReport()
        self._guard_checked = False

    # ── Hard guard #2: the only "submission" path raises ────────────────

    def _submit_live_order(self, *args: Any, **kwargs: Any) -> None:
        """Dead code path — must never be reached in shadow mode."""
        raise ShadowModeError(
            "shadow mainnet: order submission is disabled; "
            "no code path may call _submit_live_order"
        )

    def _assert_no_live_submission(self) -> None:
        """Runtime assertion executed on every pipeline step."""
        if self.config.submit_orders:
            raise ShadowModeError("submit_orders must be False in shadow mode")
        self._guard_checked = True

    # ── Market data ingestion (real mainnet data) ───────────────────────

    def ingest_market_data(
        self,
        prices: dict[str, float],
        bids: dict[str, float] | None = None,
        asks: dict[str, float] | None = None,
        timestamps: dict[str, datetime] | None = None,
    ) -> None:
        """Feed real mainnet market data into the shadow pipeline."""
        now = datetime.now(UTC)
        for symbol, price in prices.items():
            if not math.isfinite(float(price)) or price <= 0:
                raise ShadowModeError(f"invalid price for {symbol}: {price}")
        self._prices = dict(prices)
        self._bids = dict(bids or {})
        self._asks = dict(asks or {})
        self._price_ts = {s: (timestamps or {}).get(s, now) for s in prices}
        for symbol, price in prices.items():
            self._mid_history.setdefault(symbol, []).append(float(price))

    def _fresh_price(self, symbol: str) -> float | None:
        price = self._prices.get(symbol)
        ts = self._price_ts.get(symbol)
        if price is None or price <= 0:
            return None
        if ts is not None:
            age = (datetime.now(UTC) - ts).total_seconds()
            if age > self.config.max_price_age_seconds:
                return None
        return float(price)

    # ── Shadow pipeline ─────────────────────────────────────────────────

    def create_shadow_intent(
        self,
        symbol: str,
        side: str,
        size: float,
        strategy_id: str,
    ) -> ShadowOrder:
        """Create a shadow order intent. No order is submitted."""
        self._assert_no_live_submission()
        if side not in ("buy", "sell"):
            raise ShadowModeError(f"side must be buy|sell, got {side!r}")
        price = self._fresh_price(symbol)
        if price is None:
            raise ShadowModeError(
                f"no fresh market data for {symbol} — shadow intent rejected"
            )
        rules = self.config.exchange_rules.get(symbol)
        if rules is not None:
            if size < rules.min_qty:
                raise ShadowModeError(
                    f"shadow {symbol}: size {size} < min_qty {rules.min_qty}"
                )
            if size > rules.max_qty:
                raise ShadowModeError(
                    f"shadow {symbol}: size {size} > max_qty {rules.max_qty}"
                )
            notional = size * price
            if notional < rules.min_notional:
                raise ShadowModeError(
                    f"shadow {symbol}: notional {notional:.2f} < min_notional "
                    f"{rules.min_notional}"
                )
        order = ShadowOrder(
            order_id=f"shadow_{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=side,
            size=float(size),
            strategy_id=strategy_id,
            mid_at_intent=price,
        )
        self.orders[order.order_id] = order
        return order

    def simulate_fill(self, order_id: str) -> ShadowOrder:
        """Simulated fill (slippage + fee). Never touches the broker."""
        self._assert_no_live_submission()
        order = self.orders.get(order_id)
        if order is None:
            raise ShadowModeError(f"unknown shadow order {order_id}")
        if order.status != ShadowOrderStatus.INTENT:
            return order
        price = self._fresh_price(order.symbol)
        if price is None:
            raise ShadowModeError(
                f"no fresh price for {order.symbol} — shadow fill cannot proceed"
            )
        slippage_mult = 1 + (
            self.config.slippage if order.side == "buy" else -self.config.slippage
        )
        fill_price = price * slippage_mult
        fee = order.size * fill_price * self.config.commission

        order.simulated_fill_price = fill_price
        order.fee = fee
        order.status = ShadowOrderStatus.FILLED

        # Shadow cash + position accounting.
        cost = order.size * fill_price
        if order.side == "buy":
            if cost + fee > self.cash + 1e-9:
                order.status = ShadowOrderStatus.REJECTED
                raise ShadowModeError(
                    f"shadow insufficient cash for {order.symbol}: need {cost + fee:.2f}"
                )
            self.cash -= cost + fee
            pos = self.positions.get(order.symbol)
            if pos and pos.side == "buy":
                total_qty = pos.quantity + order.size
                pos.entry_price = (pos.quantity * pos.entry_price + cost) / total_qty
                pos.quantity = total_qty
            else:
                self.positions[order.symbol] = ShadowPosition(
                    symbol=order.symbol,
                    side="buy",
                    quantity=order.size,
                    entry_price=fill_price,
                )
        else:  # sell
            pos = self.positions.get(order.symbol)
            if not pos or pos.quantity < order.size - 1e-9:
                order.status = ShadowOrderStatus.REJECTED
                raise ShadowModeError(
                    f"shadow insufficient inventory for {order.symbol}"
                )
            self.cash += cost - fee
            pos.quantity -= order.size
            if pos.quantity <= 1e-9:
                del self.positions[order.symbol]
            else:
                pos.entry_price = pos.entry_price  # unchanged avg for remaining

        self.reality_gap.add_fill(
            symbol=order.symbol,
            side=order.side,
            size=order.size,
            sim_price=fill_price,
            mid_after=[],  # filled in by reality_gap_report()
        )
        return order

    def set_shadow_protective_order(
        self,
        symbol: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ShadowPosition:
        """Shadow protective order state (never sent to any venue)."""
        self._assert_no_live_submission()
        pos = self.positions.get(symbol)
        if pos is None:
            raise ShadowModeError(f"no shadow position for {symbol}")
        if stop_loss is not None:
            pos.stop_loss = float(stop_loss)
        if take_profit is not None:
            pos.take_profit = float(take_profit)
        return pos

    # ── Metrics ─────────────────────────────────────────────────────────

    def execution_metrics(self) -> dict[str, Any]:
        """Execution metrics from simulated fills."""
        fills = [
            o for o in self.orders.values() if o.status == ShadowOrderStatus.FILLED
        ]
        slippage_bps: list[float] = []
        for order in fills:
            mid = order.mid_at_intent
            if mid > 0:
                bps = (
                    (order.simulated_fill_price - mid) / mid * 10_000
                    if order.side == "buy"
                    else (mid - order.simulated_fill_price) / mid * 10_000
                )
                slippage_bps.append(bps)
        return {
            "shadow_orders": len(self.orders),
            "filled": len(fills),
            "rejected": sum(
                1
                for o in self.orders.values()
                if o.status == ShadowOrderStatus.REJECTED
            ),
            "avg_slippage_bps": round(sum(slippage_bps) / len(slippage_bps), 4)
            if slippage_bps
            else None,
            "total_fees": round(sum(o.fee for o in fills), 8),
            "shadow_equity": round(
                self.cash
                + sum(
                    p.quantity
                    * (self._prices.get(p.symbol, p.entry_price) or p.entry_price)
                    for p in self.positions.values()
                ),
                8,
            ),
        }

    def reality_gap_report(self) -> RealityGapReport:
        """Compare simulated fill prices to subsequent market moves.

        For each fill, the last known mid after the fill is appended to the
        fill's ``mid_after`` list (the caller may also extend it later via
        ``observe_mid_after_fill``).
        """
        for fill in self.reality_gap.fills:
            history = self._mid_history.get(fill["symbol"], [])
            idx = len(history) - 1
            fill["mid_after"] = history[idx:] if idx >= 0 else []
        return self.reality_gap

    def observe_mid_after_fill(self, symbol: str, mid: float) -> None:
        """Feed the latest mid after a fill to compute the reality gap."""
        self._mid_history.setdefault(symbol, []).append(float(mid))

    def shadow_pnl(self) -> dict[str, float]:
        """Shadow PnL by symbol (unrealized mark-to-market)."""
        result: dict[str, float] = {}
        for symbol, pos in self.positions.items():
            mark = self._prices.get(symbol, pos.entry_price) or pos.entry_price
            pnl = (mark - pos.entry_price) * pos.quantity
            result[symbol] = round(pnl, 8)
        return result


__all__ = [
    "ExchangeRules",
    "RealityGapReport",
    "SHADOW_ENV_GUARD",
    "SHADOW_ENV_VALUE",
    "ShadowConfig",
    "ShadowMainnetEngine",
    "ShadowModeError",
    "ShadowOrder",
    "ShadowOrderStatus",
    "ShadowPosition",
]
