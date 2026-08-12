"""FeeModel — maker/taker fees with fee-asset handling.

Deterministic.  Fees are computed on fill notional and booked either in the
quote currency (default) or in the base currency.  ``min_fee`` is applied per
fill when configured.
"""

from __future__ import annotations

from trading_agent.execution.simulator.models import Fill, SimulationConfig


class FeeModel:
    """Versioned fee model (see ``FEE_MODEL_VERSION``)."""

    def __init__(self, config: SimulationConfig):
        config.validate()
        self.config = config

    def compute_fee(self, fill: Fill, is_maker: bool) -> float:
        """Fee for a single fill, in the configured fee asset.

        ``is_maker`` selects taker/maker rate.  Aggressive (market) fills are
        taker; passive limit fills are maker.
        """
        rate = self.config.maker_fee if is_maker else self.config.taker_fee
        notional = fill.notional
        fee = notional * rate
        if self.config.min_fee > 0:
            fee = max(fee, self.config.min_fee)
        # Round to 8 decimals (standard for quote-asset fee accounting).
        return round(fee, 8)
