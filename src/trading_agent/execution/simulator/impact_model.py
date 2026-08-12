"""ImpactModel — temporary market impact, decay, and adverse selection.

All deterministic with the configured seed.  The model is deliberately simple
(sqrt-impact + exponential decay) and documented as such; it is *not* a
calibrated market microstructure model.  RealityGapReport is the mechanism
that exposes how much of the observed cost this simplification explains.

Adverse selection is recorded as post-fill mid windows; with OHLCV-derived
books the "windows" map to fractions of the current bar's high/low range,
which is the honest granularity available without tick data.
"""

from __future__ import annotations

import math
import random

from trading_agent.execution.simulator.models import (
    Fill,
    SimSide,
    SimulationConfig,
)
from trading_agent.execution.simulator.orderbook import OrderBookState


class ImpactModel:
    """Versioned impact model (see ``IMPACT_MODEL_VERSION``)."""

    def __init__(self, config: SimulationConfig):
        config.validate()
        self.config = config
        self._rng = random.Random(f"impact:{config.random_seed}")

    # ── Temporary impact ────────────────────────────────────────────────

    def temporary_impact_bps(
        self,
        quantity: float,
        side: SimSide,
        book: OrderBookState,
        volatility_bps: float,
        remaining_impact_bps: float = 0.0,
    ) -> float:
        """Sqrt-impact: ``impact = coeff * sigma * sqrt(qty / depth)``.

        ``volatility_bps`` is a per-bar realized volatility estimate
        (e.g. from the previous bar's high-low range).  ``remaining_impact_bps``
        carries over undecayed impact from earlier fills.  Depth is measured
        on the side we are consuming (asks for buys, bids for sells).
        """
        if quantity <= 0:
            return remaining_impact_bps
        depth = max(
            book.total_ask_size() if side == SimSide.BUY else book.total_bid_size(),
            1e-12,
        )
        participation = quantity / depth
        own = self.config.impact_coeff * volatility_bps * math.sqrt(participation)
        return own + remaining_impact_bps

    def decay(self, impact_bps: float, bars_since: int) -> float:
        """Exponential decay of temporary impact over bars."""
        if bars_since <= 0:
            return impact_bps
        half_life = self.config.impact_decay_half_life_bars
        return impact_bps * (0.5 ** (bars_since / half_life))

    # ── Adverse selection ───────────────────────────────────────────────

    def adverse_selection_bps(self, fill: Fill, aggressor_aggressive: bool) -> float:
        """Deterministic adverse mid move after an aggressive fill.

        A seeded draw scaled by ``adverse_selection_bps``; aggressive
        (marketable) fills get the full effect, passive fills a smaller one.
        """
        scale = 1.0 if aggressor_aggressive else 0.25
        jitter = self._rng.uniform(0.5, 1.5)
        return self.config.adverse_selection_bps * scale * jitter

    def post_fill_mid_windows(
        self,
        fill: Fill,
        bar_high: float,
        bar_low: float,
        adverse_bps: float,
    ) -> dict[str, float]:
        """Record post-fill mid at t0/t+100ms/t+1s/t+5s/t+30s.

        With bar granularity we approximate intra-bar windows as linear
        interpolation between ``mid_before`` (t0) and the bar's close-side
        adverse move.  The result is a dict of window label → mid estimate.
        """
        mid_before = fill.mid_before or (bar_high + bar_low) / 2.0
        # For a buy, adverse selection pushes the mid up (we overpaid); for a
        # sell it pushes the mid down.  Direction flips by side.
        direction = 1.0 if fill.side == SimSide.BUY else -1.0
        adverse_abs = mid_before * adverse_bps / 10_000.0 * direction
        mid_after = mid_before + adverse_abs
        # Cap the move inside the bar range to avoid nonsense prices.
        lo, hi = min(bar_low, mid_before), max(bar_high, mid_before)
        mid_after = max(lo, min(hi, mid_after))
        return {
            "mid_t0": mid_before,
            "mid_t+100ms": mid_before + (mid_after - mid_before) * 0.10,
            "mid_t+1s": mid_before + (mid_after - mid_before) * 0.25,
            "mid_t+5s": mid_before + (mid_after - mid_before) * 0.50,
            "mid_t+30s": mid_after,
        }
