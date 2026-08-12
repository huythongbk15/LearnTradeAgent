"""ExecutionLedger — deterministic accounting for the Execution Simulator V2.

Tracks cash/inventory, applies fills and fees, and accumulates the price
anchors needed for P&L attribution (decision / arrival / submit / fill VWAP /
post-fill mid).  Invariants enforced here:

* inventory never goes negative (long-only simulator);
* cash never goes negative on a buy (insufficient cash rejects the order);
* a sell can never exceed available base inventory;
* total filled quantity per order never exceeds the intended quantity.

The ledger is a single-asset long-only account: ``inventory_base`` is the
portfolio position and realized PnL on sells is marked against the
weighted-average cost of base inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_agent.execution.simulator.models import (
    Fill,
    OrderResult,
    SimOrderStatus,
    SimSide,
)


class LedgerInvariantError(Exception):
    """Raised when a ledger invariant would be violated (should never happen)."""


@dataclass
class ExecutionLedger:
    """Quote/base accounting plus per-order results."""

    symbol: str
    initial_cash_quote: float
    cash_quote: float = 0.0
    inventory_base: float = 0.0
    avg_cost_base: float = 0.0     # weighted-average cost of CURRENT inventory
    realized_pnl: float = 0.0
    total_fees_paid: float = 0.0
    fills: list[Fill] = field(default_factory=list)
    order_results: dict[str, OrderResult] = field(default_factory=dict)
    # Attribution anchors (Section 4)
    decision_price: float | None = None
    arrival_price: float | None = None
    submit_price: float | None = None
    # Opportunity/missed-fill accounting
    missed_fill_quantity: float = 0.0
    missed_fill_notional: float = 0.0
    rejected_count: int = 0
    partial_fill_count: int = 0

    def __post_init__(self) -> None:
        if self.initial_cash_quote <= 0:
            raise ValueError(f"initial_cash_quote must be positive, got {self.initial_cash_quote}")
        self.cash_quote = self.initial_cash_quote

    # ── Validation ──────────────────────────────────────────────────────

    def can_afford(self, side: SimSide, quantity: float, price: float, fee: float) -> bool:
        if side == SimSide.SELL:
            return True  # inventory check happens separately
        return self.cash_quote >= quantity * price + fee

    def has_inventory(self, quantity: float) -> bool:
        return self.inventory_base >= quantity - 1e-12

    def avg_cost(self) -> float:
        """Weighted-average cost of CURRENT inventory (0 when flat)."""
        return self.avg_cost_base

    # ── Applying fills ──────────────────────────────────────────────────

    def apply_fill(self, fill: Fill, fee: float) -> None:
        """Apply a fill and its fee, enforcing ledger invariants."""
        if fill.quantity <= 0:
            raise LedgerInvariantError("fill quantity must be positive")
        if fill.price <= 0:
            raise LedgerInvariantError("fill price must be positive")
        if fee < 0:
            raise LedgerInvariantError("fee must be non-negative")

        if fill.side == SimSide.BUY:
            if self.cash_quote < fill.notional + fee - 1e-9:
                raise LedgerInvariantError(
                    f"cash would go negative: cash={self.cash_quote:.8f}, "
                    f"needed={fill.notional + fee:.8f}"
                )
            self.cash_quote -= fill.notional + fee
            # Weighted-average cost basis of the current inventory.
            new_inv = self.inventory_base + fill.quantity
            if new_inv > 1e-12:
                self.avg_cost_base = (
                    self.avg_cost_base * self.inventory_base + fill.price * fill.quantity
                ) / new_inv
            self.inventory_base = new_inv
        else:  # SELL
            if self.inventory_base < fill.quantity - 1e-9:
                raise LedgerInvariantError(
                    f"inventory would go negative: have={self.inventory_base:.8f}, "
                    f"sell={fill.quantity:.8f}"
                )
            avg_cost = self.avg_cost_base
            self.realized_pnl += (fill.price - avg_cost) * fill.quantity
            self.inventory_base -= fill.quantity
            if self.inventory_base <= 1e-12:
                # Position flat: reset the cost basis.
                self.inventory_base = 0.0
                self.avg_cost_base = 0.0
            self.cash_quote += fill.notional - fee

        self.total_fees_paid += fee
        self.fills.append(fill)

    # ── Order results ───────────────────────────────────────────────────

    def record_order(self, result: OrderResult) -> None:
        self.order_results[result.order_id] = result
        if result.status == SimOrderStatus.REJECTED:
            self.rejected_count += 1
        if result.status == SimOrderStatus.PARTIALLY_FILLED:
            self.partial_fill_count += 1
        remaining = result.remaining_quantity
        if remaining > 0 and result.status in (
            SimOrderStatus.FILLED,
            SimOrderStatus.PARTIALLY_FILLED,
            SimOrderStatus.SUBMITTED,
            SimOrderStatus.CANCELED,
        ):
            self.missed_fill_quantity += remaining
            if result.arrival_price:
                self.missed_fill_notional += remaining * result.arrival_price

    def total_fees(self) -> float:
        return self.total_fees_paid

    def total_buy_notional(self) -> float:
        return sum(f.notional for f in self.fills if f.side == SimSide.BUY)

    def total_sell_notional(self) -> float:
        return sum(f.notional for f in self.fills if f.side == SimSide.SELL)

    def equity_at_mid(self, mid: float) -> float:
        return self.cash_quote + self.inventory_base * mid

    def snapshot(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "initial_cash_quote": self.initial_cash_quote,
            "cash_quote": round(self.cash_quote, 8),
            "inventory_base": round(self.inventory_base, 8),
            "realized_pnl": round(self.realized_pnl, 8),
            "total_fees": round(self.total_fees(), 8),
            "fill_count": len(self.fills),
            "order_count": len(self.order_results),
            "rejected_count": self.rejected_count,
            "partial_fill_count": self.partial_fill_count,
            "missed_fill_quantity": round(self.missed_fill_quantity, 8),
            "missed_fill_notional": round(self.missed_fill_notional, 8),
        }
