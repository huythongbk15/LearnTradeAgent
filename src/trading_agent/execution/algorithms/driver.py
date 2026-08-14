"""Parent-order driver for execution algorithms (Wave D).

``ParentOrderExecutor`` works a parent order through the ``MarketReplayEngine``
as a sequence of child slices.  Each bar it asks the configured algorithm for
the next slice quantity, submits a market child order, and tracks actual
fills, participation and slippage from the ledger (never from the intended
quantity — reality over intent).

The driver is deterministic: it only uses the engine's public accessors
(``current_book``, ``bar_volume``, ``volatility_bps``, ledger state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_agent.execution.algorithms.base import (
    ExecutionAlgorithm,
    SliceContext,
)
from trading_agent.execution.simulator.engine import MarketReplayEngine
from trading_agent.execution.simulator.models import SimOrderType, SimSide


@dataclass(frozen=True)
class ParentOrder:
    """A parent order to be worked as slices."""

    order_id: str
    side: SimSide
    quantity: float
    deadline_bars: int
    max_participation: float = 0.1
    slippage_budget_bps: float = 30.0
    start_bar: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.order_id:
            raise ValueError("order_id must not be empty")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.deadline_bars <= 0:
            raise ValueError(f"deadline_bars must be > 0, got {self.deadline_bars}")
        if not 0 < self.max_participation <= 1:
            raise ValueError(
                f"max_participation must be in (0, 1], got {self.max_participation}"
            )
        if self.slippage_budget_bps < 0:
            raise ValueError(
                f"slippage_budget_bps must be >= 0, got {self.slippage_budget_bps}"
            )
        if self.start_bar < 0:
            raise ValueError(f"start_bar must be >= 0, got {self.start_bar}")


@dataclass
class ParentOrderResult:
    """Outcome of working a parent order through the engine."""

    parent: ParentOrder
    status: str  # "filled" | "partial" | "rejected"
    filled_qty: float
    residual_qty: float
    fill_vwap: float
    arrival_mid: float
    slippage_bps: float  # avg execution slippage vs arrival mid
    slices: list[dict[str, Any]]  # per-bar record (bar, qty, price, participation)
    reason: str = "ok"
    algorithms_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.parent.order_id,
            "side": self.parent.side.value,
            "status": self.status,
            "requested_qty": self.parent.quantity,
            "filled_qty": self.filled_qty,
            "residual_qty": self.residual_qty,
            "fill_vwap": self.fill_vwap,
            "arrival_mid": self.arrival_mid,
            "slippage_bps": self.slippage_bps,
            "deadline_bars": self.parent.deadline_bars,
            "max_participation": self.parent.max_participation,
            "slippage_budget_bps": self.parent.slippage_budget_bps,
            "slices": self.slices,
            "reason": self.reason,
            "algorithms_version": self.algorithms_version,
        }


class ParentOrderExecutor:
    """Drives one parent order through the engine using an algorithm."""

    def __init__(
        self,
        engine: MarketReplayEngine,
        algorithm: ExecutionAlgorithm,
        *,
        algorithms_version: str = "",
    ) -> None:
        self.engine = engine
        self.algorithm = algorithm
        if not algorithms_version:
            from trading_agent.execution.simulator.versions import ALGORITHMS_VERSION

            algorithms_version = ALGORITHMS_VERSION
        self.algorithms_version = algorithms_version

    # ── Ledger helpers ───────────────────────────────────────────────────

    def _child_ids(self, parent: ParentOrder) -> list[str]:
        prefix = f"{parent.order_id}:"
        return [
            oid for oid in self.engine.ledger.order_results if oid.startswith(prefix)
        ]

    def _filled(self, parent: ParentOrder) -> float:
        return sum(
            self.engine.ledger.order_results[oid].filled_quantity
            for oid in self._child_ids(parent)
        )

    def _weighted_slippage(self, parent: ParentOrder) -> tuple[float, float]:
        """(weighted_slippage_notional, filled_qty) vs arrival mid."""
        arrival = self.engine.ledger.order_results.get(
            next(iter(self._child_ids(parent)), "")
        )
        base_mid = self.engine.current_book.mid if self.engine.current_book else None
        weighted = 0.0
        filled = 0.0
        for oid in self._child_ids(parent):
            res = self.engine.ledger.order_results[oid]
            mid = res.arrival_price or base_mid or 0.0
            if mid <= 0:
                continue
            for fill in res.fills:
                delta_bps = (fill.price - mid) / mid * 10_000.0
                if parent.side == SimSide.SELL:
                    delta_bps = -delta_bps
                weighted += delta_bps * fill.quantity
                filled += fill.quantity
        if filled <= 0:
            return 0.0, 0.0
        return weighted, filled

    # ── Run ──────────────────────────────────────────────────────────────

    def run(self, parent: ParentOrder) -> ParentOrderResult:
        parent.validate()
        engine = self.engine
        deadline_end = parent.start_bar + parent.deadline_bars
        submitted: dict[int, str] = {}  # bar -> child order id
        slices: list[dict[str, Any]] = []

        def provider(i: int, eng: MarketReplayEngine) -> list:
            from trading_agent.execution.simulator.models import OrderIntent

            if i < parent.start_bar or i >= deadline_end:
                return []

            filled = self._filled(parent)
            remaining = max(0.0, parent.quantity - filled)
            weighted, filled_qty = self._weighted_slippage(parent)
            slippage_bps = weighted / filled_qty if filled_qty > 0 else 0.0

            snapshot = eng.market_snapshot(i)
            ctx = SliceContext(
                snapshot=snapshot,
                remaining_qty=remaining,
                elapsed_bars=i - parent.start_bar,
                total_bars=parent.deadline_bars,
                filled_qty=filled_qty,
                slippage_paid_bps=slippage_bps,
                slippage_budget_bps=parent.slippage_budget_bps,
                max_participation=parent.max_participation,
                is_buy=parent.side == SimSide.BUY,
            )
            decision = self.algorithm.next_slice(ctx)
            if not decision.has_slice:
                return []

            qty = decision.quantity
            child_id = f"{parent.order_id}:{i}"
            intent = OrderIntent(
                order_id=child_id,
                side=parent.side,
                order_type=SimOrderType.MARKET,
                quantity=qty,
                metadata={
                    "parent_order_id": parent.order_id,
                    "algorithm": type(self.algorithm).__name__,
                    "bar": i,
                    "reason": decision.reason,
                },
            )
            submitted[i] = child_id
            slices.append(
                {
                    "bar": i,
                    "requested_qty": qty,
                    "recent_volume": snapshot.recent_volume,
                    "participation": (
                        qty / snapshot.recent_volume
                        if snapshot.recent_volume > 0
                        else None
                    ),
                    "spread_bps": snapshot.spread_bps,
                    "volatility_bps": snapshot.volatility_bps,
                }
            )
            return [intent]

        engine.run(provider)

        # Assemble result from actual ledger state.
        filled_qty = self._filled(parent)
        residual = max(0.0, parent.quantity - filled_qty)
        if filled_qty <= 0:
            status = "rejected"
        elif residual <= 1e-9:
            status = "filled"
        else:
            status = "partial"

        weighted, fq = self._weighted_slippage(parent)
        slippage_bps = weighted / fq if fq > 0 else 0.0

        # Fill VWAP from actual fills.
        fill_prices: list[tuple[float, float]] = []
        for oid in self._child_ids(parent):
            res = engine.ledger.order_results[oid]
            fill_prices.extend((f.quantity, f.price) for f in res.fills)
        total_f = sum(q for q, _ in fill_prices)
        vwap = sum(q * p for q, p in fill_prices) / total_f if total_f > 0 else 0.0

        arrival_mid = None
        for oid in self._child_ids(parent):
            res = engine.ledger.order_results[oid]
            if res.arrival_price is not None:
                arrival_mid = res.arrival_price
                break

        # Augment slice records with actual fill data.
        for rec in slices:
            oid = submitted.get(rec["bar"])
            if oid and oid in engine.ledger.order_results:
                res = engine.ledger.order_results[oid]
                rec["filled_qty"] = res.filled_quantity
                rec["fill_vwap"] = res.avg_fill_price
                rec["status"] = res.status.value
                rec["reject_reason"] = res.reject_reason.value
            else:
                rec["filled_qty"] = 0.0
                rec["fill_vwap"] = None
                rec["status"] = "not_submitted"

        return ParentOrderResult(
            parent=parent,
            status=status,
            filled_qty=filled_qty,
            residual_qty=residual,
            fill_vwap=vwap,
            arrival_mid=arrival_mid if arrival_mid is not None else 0.0,
            slippage_bps=slippage_bps,
            slices=slices,
            algorithms_version=self.algorithms_version,
        )


def run_parent_through_engine(
    df,
    parent: ParentOrder,
    algorithm: ExecutionAlgorithm,
    *,
    config=None,
    symbol: str = "ALGO",
    initial_cash: float = 1_000_000.0,
) -> tuple[MarketReplayEngine, ParentOrderResult]:
    """One-shot helper: build an engine, run a parent order, return both."""
    from trading_agent.execution.simulator import MarketReplayEngine, SimulationConfig

    cfg = config or SimulationConfig(random_seed=42)
    engine = MarketReplayEngine(
        df, config=cfg, symbol=symbol, initial_cash=initial_cash
    )
    executor = ParentOrderExecutor(engine, algorithm)
    return engine, executor.run(parent)
