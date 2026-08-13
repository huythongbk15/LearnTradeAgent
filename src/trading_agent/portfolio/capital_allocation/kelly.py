"""Kelly Criterion position sizing for strategy allocation."""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np


@dataclass
class KellyParams:
    """Kelly criterion parameters."""

    win_rate: Decimal  # Probability of winning trade
    avg_win: Decimal  # Average win amount (as fraction of capital)
    avg_loss: Decimal  # Average loss amount (as fraction of capital)
    max_leverage: Decimal = Decimal("1.0")  # Maximum leverage allowed


@dataclass
class KellyResult:
    """Kelly criterion result."""

    kelly_fraction: Decimal
    half_kelly: Decimal
    quarter_kelly: Decimal
    expected_growth: Decimal
    risk_of_ruin: Decimal
    optimal_leverage: Decimal


class KellySizer:
    """Kelly Criterion position sizing calculator."""

    @staticmethod
    def calculate(params: KellyParams) -> KellyResult:
        """Calculate Kelly fraction from win/loss statistics."""
        w = float(params.win_rate)
        a = float(params.avg_win)
        avg_loss = float(params.avg_loss)

        if avg_loss <= 0:
            raise ValueError("Average loss must be positive")

        # Kelly formula: f* = (w * a - (1-w) * avg_loss) / (a * avg_loss) ... simplified for binary outcomes
        # Full Kelly: f = (p * b - q) / b where b = a/avg_loss, p = w, q = 1-w
        b = a / avg_loss
        kelly_f = (w * b - (1 - w)) / b if b > 0 else 0

        # Cap at max leverage
        kelly_f = min(kelly_f, float(params.max_leverage))
        kelly_f = max(kelly_f, 0)  # No shorting

        half_kelly = kelly_f * 0.5
        quarter_kelly = kelly_f * 0.25

        # Expected growth rate: G = p*log(1+f*b) + q*log(1-f)
        if kelly_f > 0 and kelly_f < 1:
            expected_growth = w * np.log(1 + kelly_f * b) + (1 - w) * np.log(
                1 - kelly_f
            )
        else:
            expected_growth = 0

        # Risk of ruin approximation
        if kelly_f > 0:
            risk_of_ruin = ((1 - w) / w) ** (1 / (kelly_f * b)) if w > 0 else 1
        else:
            risk_of_ruin = 1

        return KellyResult(
            kelly_fraction=Decimal(str(kelly_f)),
            half_kelly=Decimal(str(half_kelly)),
            quarter_kelly=Decimal(str(quarter_kelly)),
            expected_growth=Decimal(str(expected_growth)),
            risk_of_ruin=Decimal(str(min(risk_of_ruin, 1))),
            optimal_leverage=Decimal(str(kelly_f)),
        )

    @staticmethod
    def calculate_from_trades(
        wins: list[Decimal],
        losses: list[Decimal],
        max_leverage: Decimal = Decimal("1.0"),
    ) -> KellyResult:
        """Calculate Kelly from trade history."""
        if not wins and not losses:
            raise ValueError("No trades provided")

        win_rate = Decimal(len(wins)) / Decimal(len(wins) + len(losses))
        avg_win = sum(wins) / Decimal(len(wins)) if wins else Decimal(0)
        avg_loss = abs(sum(losses) / Decimal(len(losses))) if losses else Decimal(1)

        params = KellyParams(
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_leverage=max_leverage,
        )

        return KellySizer.calculate(params)

    @staticmethod
    def fractional_kelly(
        kelly_fraction: Decimal, fraction: Decimal = Decimal("0.5")
    ) -> Decimal:
        """Apply fractional Kelly (e.g., 0.5 for half-Kelly)."""
        return kelly_fraction * fraction

    @staticmethod
    def kelly_with_drawdown_constraint(
        params: KellyParams,
        max_drawdown: Decimal = Decimal("0.2"),
    ) -> KellyResult:
        """Kelly with maximum drawdown constraint."""
        result = KellySizer.calculate(params)

        # Adjust for drawdown constraint
        # Approximate: max DD ≈ -log(1 - f) / (f * b) for small f
        # Use heuristic: reduce Kelly until expected DD < max
        f = float(result.kelly_fraction)
        b = float(params.avg_win / params.avg_loss)

        if f > 0 and b > 0:
            # Approximate max drawdown
            approx_dd = -np.log(1 - f) / (f * b)
            if approx_dd > float(max_drawdown):
                # Scale down
                scale = float(max_drawdown) / approx_dd
                f = f * scale

                # Recalculate
                w = float(params.win_rate)
                expected_growth = w * np.log(1 + f * b) + (1 - w) * np.log(1 - f)
                risk_of_ruin = ((1 - w) / w) ** (1 / (f * b)) if w > 0 else 1

                return KellyResult(
                    kelly_fraction=Decimal(str(f)),
                    half_kelly=Decimal(str(f * 0.5)),
                    quarter_kelly=Decimal(str(f * 0.25)),
                    expected_growth=Decimal(str(expected_growth)),
                    risk_of_ruin=Decimal(str(min(risk_of_ruin, 1))),
                    optimal_leverage=Decimal(str(f)),
                )

        return result


class HalfKellySizer:
    """Half-Kelly sizer (conservative Kelly)."""

    @staticmethod
    def calculate(params: KellyParams) -> KellyResult:
        """Calculate half-Kelly fraction."""
        result = KellySizer.calculate(params)
        half_kelly = result.kelly_fraction * Decimal("0.5")

        # Recalculate metrics for half-Kelly
        w = float(params.win_rate)
        b = float(params.avg_win / params.avg_loss)
        f = float(half_kelly)

        if f > 0:
            expected_growth = w * np.log(1 + f * b) + (1 - w) * np.log(1 - f)
            risk_of_ruin = ((1 - w) / w) ** (1 / (f * b)) if w > 0 else 1
        else:
            expected_growth = 0
            risk_of_ruin = 1

        return KellyResult(
            kelly_fraction=result.kelly_fraction,
            half_kelly=half_kelly,
            quarter_kelly=result.kelly_fraction * Decimal("0.25"),
            expected_growth=Decimal(str(expected_growth)),
            risk_of_ruin=Decimal(str(min(risk_of_ruin, 1))),
            optimal_leverage=half_kelly,
        )


def kelly_position_size(
    capital: Decimal,
    kelly_fraction: Decimal,
    price: Decimal,
    stop_loss: Decimal,
) -> Decimal:
    """Calculate position size from Kelly fraction."""
    if stop_loss >= price:
        raise ValueError("Stop loss must be below entry price for long")

    risk_per_unit = price - stop_loss
    risk_capital = capital * kelly_fraction
    position_size = risk_capital / risk_per_unit

    return position_size.quantize(Decimal("0.01"))
