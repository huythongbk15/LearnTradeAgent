"""Liquidity-aware TWAP (Wave D, spec §15).

Slice size adapts to:

* spread         — wider spread → smaller slices (avoid paying the crossing cost)
* depth          — deeper book → larger slices (more absorption capacity)
* volatility     — higher volatility → smaller slices
* recent volume  — more observed volume → larger slices
* max participation — hard cap as fraction of observed volume
* slippage budget  — hard cap on remaining budget; never overspend

The base is a classic TWAP slice (remaining / remaining bars).  Liquidity
adjustments are deterministic, bounded, and every cap is fail-closed.
"""

from __future__ import annotations

import math

from trading_agent.execution.algorithms.base import (
    MarketSnapshot,
    SliceContext,
    SliceResult,
)


class LiquidityAwareTwap:
    """Deterministic liquidity-aware TWAP slice selector.

    Parameters are all validated at construction (fail closed).  ``None``
    means "use the market-dependent default" for that dimension.
    """

    def __init__(
        self,
        *,
        spread_ref_bps: float = 5.0,
        volume_ref: float | None = None,
        vol_ref_bps: float = 20.0,
        depth_ref_share: float = 0.25,
        spread_floor: float = 0.5,
        spread_cap: float = 1.5,
        vol_floor: float = 0.5,
        vol_cap: float = 1.5,
        depth_floor: float = 0.5,
        depth_cap: float = 2.0,
        min_slice_qty: float = 0.0,
    ) -> None:
        if spread_ref_bps <= 0:
            raise ValueError(f"spread_ref_bps must be > 0, got {spread_ref_bps}")
        if vol_ref_bps <= 0:
            raise ValueError(f"vol_ref_bps must be > 0, got {vol_ref_bps}")
        if volume_ref is not None and volume_ref <= 0:
            raise ValueError(f"volume_ref must be > 0, got {volume_ref}")
        if not 0 < depth_ref_share <= 1:
            raise ValueError(
                f"depth_ref_share must be in (0, 1], got {depth_ref_share}"
            )
        if not 0 < spread_floor <= spread_cap:
            raise ValueError("spread adjustment must satisfy 0 < floor <= cap")
        if not 0 < vol_floor <= vol_cap:
            raise ValueError("volatility adjustment must satisfy 0 < floor <= cap")
        if not 0 < depth_floor <= depth_cap:
            raise ValueError("depth adjustment must satisfy 0 < floor <= cap")
        if min_slice_qty < 0:
            raise ValueError(f"min_slice_qty must be >= 0, got {min_slice_qty}")

        self.spread_ref_bps = spread_ref_bps
        self.volume_ref = volume_ref
        self.vol_ref_bps = vol_ref_bps
        self.depth_ref_share = depth_ref_share
        self.spread_floor = spread_floor
        self.spread_cap = spread_cap
        self.vol_floor = vol_floor
        self.vol_cap = vol_cap
        self.depth_floor = depth_floor
        self.depth_cap = depth_cap
        self.min_slice_qty = min_slice_qty

    # ── Adjustment factors ───────────────────────────────────────────────

    def _spread_adj(self, snapshot: MarketSnapshot) -> float:
        if snapshot.spread_bps <= 0:
            return 1.0
        return min(
            self.spread_cap,
            max(self.spread_floor, self.spread_ref_bps / snapshot.spread_bps),
        )

    def _vol_adj(self, snapshot: MarketSnapshot) -> float:
        if snapshot.volatility_bps <= 0:
            return 1.0
        # Higher volatility → smaller slice (bounded).
        return min(
            self.vol_cap,
            max(self.vol_floor, self.vol_ref_bps / snapshot.volatility_bps),
        )

    def _depth_adj(self, snapshot: MarketSnapshot, is_buy: bool) -> float:
        depth = snapshot.depth_for_side(is_buy)
        ref = self.volume_ref
        if ref is None:
            # Neutral at the simulator's typical book shape:
            # depth ≈ depth_ref_share * recent_volume.
            ref = max(snapshot.recent_volume * self.depth_ref_share, 1e-12)
        if ref <= 0 or depth <= 0:
            return 1.0
        # sqrt so large depth does not dominate (impact is sqrt-shaped).
        return min(self.depth_cap, max(self.depth_floor, math.sqrt(depth / ref)))

    def _volume_adj(self, snapshot: MarketSnapshot) -> float:
        ref = self.volume_ref
        if ref is None:
            return 1.0
        if ref <= 0 or snapshot.recent_volume <= 0:
            return 1.0
        return min(self.depth_cap, max(self.depth_floor, snapshot.recent_volume / ref))

    # ── Core ─────────────────────────────────────────────────────────────

    def next_slice(self, ctx: SliceContext) -> SliceResult:
        ctx.validate()
        if ctx.remaining_qty <= 0:
            return SliceResult(0.0, reason="done")
        if ctx.remaining_bars <= 0:
            return SliceResult(0.0, reason="deadline")

        base = ctx.remaining_qty / ctx.remaining_bars

        spread_adj = self._spread_adj(ctx.snapshot)
        vol_adj = self._vol_adj(ctx.snapshot)
        depth_adj = self._depth_adj(ctx.snapshot, ctx.is_buy)
        volume_adj = self._volume_adj(ctx.snapshot)

        # Liquidity aggregate: spread × volatility dampen; depth × volume amplify.
        liquidity_adj = spread_adj * vol_adj * (0.5 + 0.5 * depth_adj) * volume_adj

        slice_qty = base * liquidity_adj
        slice_qty = max(slice_qty, 0.0)

        # Cap 1: participation (fraction of observed volume).
        participation_cap = ctx.max_participation * ctx.snapshot.recent_volume
        if participation_cap > 0:
            slice_qty = min(slice_qty, participation_cap)

        # Cap 2: remaining slippage budget (spread + estimated impact cost).
        # Budget is tracked in bps of notional: convert the whole budget to
        # bps-units (bps × qty), subtract what has already been spent, and
        # cap the slice so its estimated cost fits in what remains.
        total_parent_qty = ctx.filled_qty + ctx.remaining_qty
        if total_parent_qty > 0:
            allowed_bps_units = ctx.slippage_budget_bps * total_parent_qty
            spent_bps_units = ctx.slippage_paid_bps * ctx.filled_qty
            remaining_bps_units = max(0.0, allowed_bps_units - spent_bps_units)
            if remaining_bps_units <= 0:
                # Already at/over budget with quantity still to trade — fail
                # closed instead of chipping away.
                return SliceResult(0.0, reason="budget_exhausted")
            mid = ctx.snapshot.mid
            if mid > 0:
                est_cost_bps = (
                    ctx.snapshot.spread_bps / 2.0 + 1.0
                )  # +1 bps impact proxy
                if est_cost_bps > 0:
                    budget_qty = remaining_bps_units / est_cost_bps
                    slice_qty = min(slice_qty, budget_qty)

        # Cap 3: never exceed the remaining parent quantity.
        slice_qty = min(slice_qty, ctx.remaining_qty)

        if slice_qty < self.min_slice_qty:
            return SliceResult(0.0, reason="below_min_slice")

        if slice_qty <= 0:
            return SliceResult(0.0, reason="budget_exhausted")
        return SliceResult(round(slice_qty, 10), reason="ok")
