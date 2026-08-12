"""POV (Percentage of Volume) execution (Wave D, spec §15).

Slice size is strictly capped by the observed market volume:

    slice <= max_participation * recent_volume

The algorithm never exceeds the participation cap — even if that means the
parent order is left partially unfilled (``reason="deadline"``).  This is the
honest POV semantics: when the market does not provide enough volume, the
order waits or expires rather than chasing.
"""

from __future__ import annotations

from trading_agent.execution.algorithms.base import (
    SliceContext,
    SliceResult,
)


class PovExecution:
    """Deterministic participation-capped slice selector."""

    def __init__(self, *, min_slice_qty: float = 0.0) -> None:
        if min_slice_qty < 0:
            raise ValueError(f"min_slice_qty must be >= 0, got {min_slice_qty}")
        self.min_slice_qty = min_slice_qty

    def next_slice(self, ctx: SliceContext) -> SliceResult:
        ctx.validate()
        if ctx.remaining_qty <= 0:
            return SliceResult(0.0, reason="done")
        if ctx.remaining_bars <= 0:
            return SliceResult(0.0, reason="deadline")
        if ctx.snapshot.recent_volume <= 0:
            return SliceResult(0.0, reason="no_volume")

        participation_cap = ctx.max_participation * ctx.snapshot.recent_volume
        slice_qty = min(participation_cap, ctx.remaining_qty)

        if slice_qty < self.min_slice_qty:
            return SliceResult(0.0, reason="below_min_slice")
        if slice_qty <= 0:
            return SliceResult(0.0, reason="no_volume")
        return SliceResult(round(slice_qty, 10), reason="ok")
