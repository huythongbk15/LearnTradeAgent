#!/usr/bin/env python3
"""
DEX Integration Module — Uniswap V3, Curve, AMM Math.

Provides:
1. Uniswap V3 Pool math (ticks, sqrtPrice, liquidity)
2. Position management (mint, burn, collect, swap)
3. Delta-neutral LP strategies
4. Impermanent loss calculation
5. Curve pool support (stable, crypto, factory pools)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════
# Uniswap V3 Core Math
# ══════════════════════════════════════════════════════════════════════════

Q96 = 2**96
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342


def tick_to_sqrt_price_x96(tick: int) -> int:
    """Convert tick to sqrt price * 2^96."""
    return int(math.floor(math.sqrt(1.0001**tick) * Q96))


def sqrt_price_x96_to_tick(sqrt_price_x96: int) -> int:
    """Convert sqrt price * 2^96 to tick."""
    return int(math.floor(math.log((sqrt_price_x96 / Q96)**2) / math.log(1.0001)))


def price_to_tick(price: float, token0_decimals: int, token1_decimals: int) -> int:
    """Convert human price to tick."""
    adjusted_price = price * (10**token0_decimals) / (10**token1_decimals)
    return int(math.floor(math.log(adjusted_price) / math.log(1.0001)))


def tick_to_price(tick: int, token0_decimals: int, token1_decimals: int) -> float:
    """Convert tick to human price."""
    return (1.0001**tick) * (10**token1_decimals) / (10**token0_decimals)


def sqrt_price_x96_to_price(sqrt_price_x96: int, token0_decimals: int, token1_decimals: int) -> float:
    """Convert sqrt price to human price."""
    price_x192 = (sqrt_price_x96 / Q96) ** 2
    return price_x192 * (10**token1_decimals) / (10**token0_decimals)


def get_tick_at_sqrt_ratio(sqrt_ratio_x96: int) -> int:
    """Get tick from sqrt ratio (Solidity-style)."""
    return int(math.floor(math.log((sqrt_ratio_x96 / Q96)**2) / math.log(1.0001)))


# ══════════════════════════════════════════════════════════════════════════
# Pool State & Position
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class UniswapV3Pool:
    """Uniswap V3 Pool state."""
    address: str
    token0: str
    token1: str
    fee: int  # fee tier in basis points (500, 3000, 10000)
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    token0_decimals: int = 18
    token1_decimals: int = 18

    @property
    def price(self) -> float:
        return sqrt_price_x96_to_price(self.sqrt_price_x96, self.token0_decimals, self.token1_decimals)

    @property
    def tick_lower(self) -> int:
        return (self.tick // self.tick_spacing) * self.tick_spacing

    @property
    def tick_upper(self) -> int:
        return self.tick_lower + self.tick_spacing


@dataclass
class UniswapV3Position:
    """Concentrated liquidity position."""
    pool: UniswapV3Pool
    tick_lower: int
    tick_upper: int
    liquidity: int
    tokens_owed_0: int = 0
    tokens_owed_1: int = 0
    fee_growth_inside_0_last: int = 0
    fee_growth_inside_1_last: int = 0

    def __post_init__(self):
        assert self.tick_lower < self.tick_upper, "tick_lower must be < tick_upper"
        assert self.tick_lower % self.pool.tick_spacing == 0
        assert self.tick_upper % self.pool.tick_spacing == 0

    def get_amounts(self, sqrt_price_x96: int | None = None) -> tuple[int, int]:
        """Get token amounts for current liquidity at given price."""
        if sqrt_price_x96 is None:
            sqrt_price_x96 = self.pool.sqrt_price_x96

        sqrt_lower = tick_to_sqrt_price_x96(self.tick_lower)
        sqrt_upper = tick_to_sqrt_price_x96(self.tick_upper)

        if sqrt_price_x96 <= sqrt_lower:
            # All token0
            amount0 = (self.liquidity * Q96 * (sqrt_upper - sqrt_lower)) // (sqrt_lower * sqrt_upper)
            amount1 = 0
        elif sqrt_price_x96 < sqrt_upper:
            # Mixed
            amount0 = (self.liquidity * Q96 * (sqrt_upper - sqrt_price_x96)) // (sqrt_price_x96 * sqrt_upper)
            amount1 = (self.liquidity * (sqrt_price_x96 - sqrt_lower)) // Q96
        else:
            # All token1
            amount0 = 0
            amount1 = self.liquidity * (sqrt_upper - sqrt_lower) // Q96

        return amount0, amount1

    def get_amounts_human(self, sqrt_price_x96: int | None = None) -> tuple[float, float]:
        a0, a1 = self.get_amounts(sqrt_price_x96)
        return (a0 / 10**self.pool.token0_decimals, a1 / 10**self.pool.token1_decimals)

    def in_range(self, sqrt_price_x96: int | None = None) -> bool:
        if sqrt_price_x96 is None:
            sqrt_price_x96 = self.pool.sqrt_price_x96
        sqrt_lower = tick_to_sqrt_price_x96(self.tick_lower)
        sqrt_upper = tick_to_sqrt_price_x96(self.tick_upper)
        return sqrt_lower <= sqrt_price_x96 < sqrt_upper

    def impermanent_loss(self, sqrt_price_x96: int | None = None) -> float:
        """Calculate impermanent loss vs holding."""
        if sqrt_price_x96 is None:
            sqrt_price_x96 = self.pool.sqrt_price_x96

        a0, a1 = self.get_amounts(sqrt_price_x96)
        value_lp = a0 * sqrt_price_x96_to_price(sqrt_price_x96, self.pool.token0_decimals, self.pool.token1_decimals) + a1

        # Value if held
        a0_init, a1_init = self.get_amounts(self.pool.sqrt_price_x96)
        value_hold = a0_init * self.pool.price + a1_init

        if value_hold == 0:
            return 0.0
        return (value_lp - value_hold) / value_hold


def compute_liquidity_from_amounts(
    amount0: int, amount1: int,
    sqrt_price_x96: int, tick_lower: int, tick_upper: int
) -> int:
    """Compute liquidity from token amounts (for minting)."""
    sqrt_lower = tick_to_sqrt_price_x96(tick_lower)
    sqrt_upper = tick_to_sqrt_price_x96(tick_upper)

    if sqrt_price_x96 <= sqrt_lower:
        liquidity = (amount0 * sqrt_lower * sqrt_upper) // (Q96 * (sqrt_upper - sqrt_lower))
    elif sqrt_price_x96 < sqrt_upper:
        liquidity0 = (amount0 * sqrt_price_x96 * sqrt_upper) // (Q96 * (sqrt_upper - sqrt_price_x96))
        liquidity1 = (amount1 * Q96) // (sqrt_price_x96 - sqrt_lower)
        liquidity = min(liquidity0, liquidity1)
    else:
        liquidity = (amount1 * Q96) // (sqrt_upper - sqrt_lower)

    return liquidity


# ══════════════════════════════════════════════════════════════════════════
# Swap Math
# ══════════════════════════════════════════════════════════════════════════

def swap_math(amount_in: int, sqrt_price_x96: int, liquidity: int, zero_for_one: bool, fee_bps: int) -> tuple[int, int]:
    """
    Simulate Uniswap V3 swap.
    Returns (amount_out, new_sqrt_price_x96).
    """
    fee = fee_bps / 10000
    amount_in_after_fee = int(amount_in * (1 - fee))

    if zero_for_one:
        # token0 -> token1
        new_sqrt = (sqrt_price_x96 * liquidity * Q96) // (liquidity * Q96 + amount_in_after_fee * sqrt_price_x96)
        amount_out = (liquidity * (sqrt_price_x96 - new_sqrt)) // Q96
    else:
        # token1 -> token0
        new_sqrt = (liquidity * Q96 + amount_in_after_fee * sqrt_price_x96) // liquidity
        amount_out = ((new_sqrt - sqrt_price_x96) * liquidity) // Q96

    return amount_out, new_sqrt


# ══════════════════════════════════════════════════════════════════════════
# Delta-Neutral LP Strategy
# ══════════════════════════════════════════════════════════════════════════

class DeltaNeutralLP:
    """
    Delta-neutral LP strategy:
    1. Provide liquidity in Uniswap V3 concentrated range
    2. Hedge delta with perpetual futures
    3. Collect fees + funding rate
    """

    def __init__(self, pool: UniswapV3Position, hedge_ratio: float = 1.0):
        self.position = pool
        self.hedge_ratio = hedge_ratio
        self.hedge_position = 0.0  # perp position (negative = short)
        self.entry_price = pool.pool.price
        self.collected_fees_0 = 0.0
        self.collected_fees_1 = 0.0

    def compute_delta(self) -> float:
        """Compute delta of LP position."""
        # Delta ≈ liquidity * (sqrt_price - sqrt_lower) / sqrt_price for in-range
        # Simplified: use token1 amount as delta proxy
        a0, a1 = self.position.get_amounts()
        # Delta in terms of token1 per unit price change
        price = self.position.pool.price
        if price == 0:
            return 0.0
        # Approximate: delta = -amount0 * price + amount1 (in token1 terms)
        delta = -a0 * price + a1
        return delta / 10**self.position.pool.token1_decimals

    def rebalance_hedge(self, perp_price: float) -> dict:
        """Rebalance perp hedge to target delta neutrality."""
        current_delta = self.compute_delta()
        target_hedge = -current_delta * self.hedge_ratio
        trade_size = target_hedge - self.hedge_position

        result = {
            "action": "REBALANCE_HEDGE",
            "current_delta": current_delta,
            "target_hedge": target_hedge,
            "current_hedge": self.hedge_position,
            "trade_size": trade_size,
            "perp_price": perp_price,
        }

        self.hedge_position = target_hedge
        return result

    def collect_fees(self, tokens_owed_0: int, tokens_owed_1: int) -> dict:
        """Collect accumulated fees."""
        self.collected_fees_0 += tokens_owed_0 / 10**self.position.pool.token0_decimals
        self.collected_fees_1 += tokens_owed_1 / 10**self.position.pool.token1_decimals
        return {
            "collected_0": tokens_owed_0 / 10**self.position.pool.token0_decimals,
            "collected_1": tokens_owed_1 / 10**self.position.pool.token1_decimals,
            "total_fees_0": self.collected_fees_0,
            "total_fees_1": self.collected_fees_1,
        }

    def total_pnl(self, current_price: float, perp_price: float) -> dict:
        """Compute total P&L including IL, fees, and hedge."""
        # LP value
        sqrt_price = int(math.sqrt(current_price * 10**(self.position.pool.token0_decimals - self.position.pool.token1_decimals)) * Q96)
        a0, a1 = self.position.get_amounts(sqrt_price)
        lp_value = a0 * current_price + a1

        # Initial value
        a0_init, a1_init = self.position.get_amounts()
        init_value = a0_init * self.entry_price + a1_init

        # IL
        il = (lp_value - init_value) / init_value if init_value > 0 else 0

        # Hedge P&L
        hedge_pnl = self.hedge_position * (self.entry_price - perp_price)

        # Fees in token1 terms
        fees_value = self.collected_fees_1 + self.collected_fees_0 * current_price

        total = (lp_value - init_value) + hedge_pnl + fees_value

        return {
            "lp_value": lp_value,
            "init_value": init_value,
            "impermanent_loss": il,
            "hedge_pnl": hedge_pnl,
            "fees_collected": fees_value,
            "total_pnl": total,
        }


# ══════════════════════════════════════════════════════════════════════════
# Curve Pool Support
# ══════════════════════════════════════════════════════════════════════════

class CurvePoolType(Enum):
    STABLE = "stable"      # 3pool, etc.
    CRYPTO = "crypto"      # tricrypto, etc.
    FACTORY = "factory"    # factory pools
    LENDING = "lending"    # aave, compound pools


@dataclass
class CurvePool:
    """Curve pool abstraction."""
    address: str
    name: str
    pool_type: CurvePoolType
    coins: list[str]
    decimals: list[int]
    A: int  # amplification parameter
    gamma: int = 0  # for crypto pools
    fee: int = 4_000_000  # fee * 1e10
    admin_fee: int = 0
    virtual_price: int = 10**18

    def get_dy(self, i: int, j: int, dx: int) -> int:
        """Get output amount for input dx (simplified)."""
        # This is a simplified version; real Curve uses Newton's method on invariant
        # For stable pools: x * y = k (with amplification)
        # For crypto pools: more complex invariant
        if self.pool_type == CurvePoolType.STABLE:
            # StableSwap invariant: A * sum(x_i) * prod(x_i) + sum(x_i) = A * D^N + D
            # Simplified: use constant product with amplification
            x_i = 1_000_000  # placeholder balances
            x_j = 1_000_000
            # Very rough approximation
            return int(dx * x_j / (x_i + dx * (1 - self.fee / 1e10)))
        return 0


# ══════════════════════════════════════════════════════════════════════════
# Yield Strategies
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class DeFiYieldStrategy:
    """Base DeFi yield strategy."""
    name: str
    protocol: str
    apy: float
    risk_score: float  # 0-100
    tvl: float
    tokens: list[str]


class DeltaNeutralLPYield(DeFiYieldStrategy):
    """Delta-neutral LP yield farming."""
    def __init__(self, pool: UniswapV3Position, perp_funding_rate: float = 0.0):
        lp_fee_apy = self._estimate_fee_apy(pool)
        total_apy = lp_fee_apy + perp_funding_rate
        super().__init__(
            name=f"DeltaNeutral_{pool.pool.token0}_{pool.pool.token1}",
            protocol="Uniswap V3 + Perp",
            apy=total_apy,
            risk_score=30,
            tvl=0,
            tokens=[pool.pool.token0, pool.pool.token1],
        )
        self.pool = pool
        self.perp_funding_rate = perp_funding_rate

    def _estimate_fee_apy(self, pool: UniswapV3Position) -> float:
        # Rough estimation: fee tier * volume / TVL * 365
        fee_tier = pool.pool.fee / 10000
        # Placeholder: assume 10% fee capture of volume
        return fee_tier * 0.1 * 365


class StakingYield(DeFiYieldStrategy):
    """Native staking yield (ETH, SOL, etc.)."""
    def __init__(self, token: str, apy: float, tvl: float):
        super().__init__(
            name=f"Staking_{token}",
            protocol="Native Staking",
            apy=apy,
            risk_score=10,
            tvl=tvl,
            tokens=[token],
        )


class LendingYield(DeFiYieldStrategy):
    """Lending protocol yield (Aave, Compound)."""
    def __init__(self, protocol: str, token: str, supply_apy: float, borrow_apy: float, tvl: float):
        net_apy = supply_apy  # Simplified
        super().__init__(
            name=f"Lending_{protocol}_{token}",
            protocol=protocol,
            apy=net_apy,
            risk_score=20,
            tvl=tvl,
            tokens=[token],
        )


# ══════════════════════════════════════════════════════════════════════════
# Demo
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Uniswap V3 ETH/USDC 0.05% pool
    pool = UniswapV3Pool(
        address="0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640",
        token0="USDC", token1="ETH",
        fee=500, tick_spacing=10,
        sqrt_price_x96=int(math.sqrt(3000) * Q96),  # ~3000 USDC/ETH
        tick=price_to_tick(3000, 6, 18),
        liquidity=1_000_000_000_000_000_000,
        token0_decimals=6, token1_decimals=18,
    )

    # Position: ±10% range
    tick_lower = price_to_tick(2700, 6, 18)
    tick_upper = price_to_tick(3300, 6, 18)
    tick_lower = (tick_lower // 10) * 10
    tick_upper = (tick_upper // 10) * 10

    liquidity = compute_liquidity_from_amounts(
        amount0=10_000 * 10**6,  # 10k USDC
        amount1=3 * 10**18,       # 3 ETH
        sqrt_price_x96=pool.sqrt_price_x96,
        tick_lower=tick_lower,
        tick_upper=tick_upper,
    )

    position = UniswapV3Position(
        pool=pool, tick_lower=tick_lower, tick_upper=tick_upper, liquidity=liquidity,
    )

    print(f"Pool: {pool.token0}/{pool.token1} @ ${pool.price:,.2f}")
    print(f"Position range: ${tick_to_price(tick_lower, 6, 18):,.2f} - ${tick_to_price(tick_upper, 6, 18):,.2f}")
    print(f"Liquidity: {liquidity}")
    print(f"Amounts: {position.get_amounts_human()}")
    print(f"In range: {position.in_range()}")
    print(f"IL at 2500: {position.impermanent_loss(int(math.sqrt(2500)*Q96)):.2%}")
    print(f"IL at 3500: {position.impermanent_loss(int(math.sqrt(3500)*Q96)):.2%}")

    # Delta-neutral LP
    dn_lp = DeltaNeutralLP(position, hedge_ratio=1.0)
    print(f"\nDelta: {dn_lp.compute_delta():.4f}")
    print(f"Rebalance: {dn_lp.rebalance_hedge(3000)}")
    print(f"PNL @ 2800: {dn_lp.total_pnl(2800, 2800)}")