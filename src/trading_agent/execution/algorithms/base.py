"""Base types for execution algorithms (Wave D).

``MarketSnapshot`` describes what the algorithm can see about the market at a
decision point; ``SliceContext`` adds the parent-order state.  An
``ExecutionAlgorithm`` maps a context to the quantity of the next child
slice.  Everything is frozen dataclasses — deterministic and hashable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trading_agent.execution.simulator.versions import ALGORITHMS_VERSION


@dataclass(frozen=True)
class MarketSnapshot:
    """What the algorithm observes about the market at a decision point."""

    mid: float  # current mid price
    spread_bps: float  # current bid-ask spread in bps
    bid_depth: float  # total resting size on the bid side
    ask_depth: float  # total resting size on the ask side
    recent_volume: float  # observed (recent bar) traded volume
    volatility_bps: float  # realized volatility estimate in bps

    def depth_for_side(self, is_buy: bool) -> float:
        return self.ask_depth if is_buy else self.bid_depth

    def validate(self) -> None:
        if self.mid <= 0:
            raise ValueError(f"mid must be > 0, got {self.mid}")
        if self.spread_bps < 0:
            raise ValueError(f"spread_bps must be >= 0, got {self.spread_bps}")
        if self.bid_depth < 0 or self.ask_depth < 0:
            raise ValueError("depths must be >= 0")
        if self.recent_volume < 0:
            raise ValueError(f"recent_volume must be >= 0, got {self.recent_volume}")
        if self.volatility_bps < 0:
            raise ValueError(f"volatility_bps must be >= 0, got {self.volatility_bps}")


@dataclass(frozen=True)
class SliceContext:
    """Parent-order state at a decision point."""

    snapshot: MarketSnapshot
    remaining_qty: float  # quantity still to work
    elapsed_bars: int  # bars since the parent order started
    total_bars: int  # deadline in bars
    filled_qty: float  # quantity filled so far
    slippage_paid_bps: float  # accumulated slippage vs arrival mid
    slippage_budget_bps: float  # hard cap on slippage (fail closed)
    max_participation: float  # 0..1 — cap as fraction of observed volume
    is_buy: bool = True

    @property
    def remaining_bars(self) -> int:
        return max(0, self.total_bars - self.elapsed_bars)

    @property
    def participation_remaining(self) -> float:
        """Fraction of total parent qty still to fill."""
        total = self.filled_qty + self.remaining_qty
        if total <= 0:
            return 0.0
        return self.remaining_qty / total

    def validate(self) -> None:
        self.snapshot.validate()
        if self.remaining_qty < 0:
            raise ValueError(f"remaining_qty must be >= 0, got {self.remaining_qty}")
        if self.elapsed_bars < 0 or self.total_bars < 0:
            raise ValueError("bars must be >= 0")
        if self.elapsed_bars > self.total_bars:
            raise ValueError("elapsed_bars must be <= total_bars")
        if self.filled_qty < 0:
            raise ValueError(f"filled_qty must be >= 0, got {self.filled_qty}")
        if self.slippage_paid_bps < 0:
            raise ValueError("slippage_paid_bps must be >= 0")
        if self.slippage_budget_bps < 0:
            raise ValueError("slippage_budget_bps must be >= 0")
        if not 0 < self.max_participation <= 1:
            raise ValueError(
                f"max_participation must be in (0, 1], got {self.max_participation}"
            )


@dataclass(frozen=True)
class SliceResult:
    """Outcome of asking an algorithm for the next slice."""

    quantity: float  # 0 → no slice this bar
    reason: str = "ok"  # e.g. "done", "budget_exhausted", "deadline"

    @property
    def has_slice(self) -> bool:
        return self.quantity > 0


@runtime_checkable
class ExecutionAlgorithm(Protocol):
    """Any deterministic slice-selection algorithm."""

    def next_slice(
        self, ctx: SliceContext
    ) -> SliceResult:  # pragma: no cover - protocol
        ...


@dataclass(frozen=True)
class AlgorithmVersion:
    """Version tag carried by algorithm results (spec §3 versioning)."""

    algorithms_version: str = ALGORITHMS_VERSION
