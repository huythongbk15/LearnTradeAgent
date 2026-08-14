"""Capital Allocation module for multi-strategy portfolio management."""

from trading_agent.portfolio.capital_allocation.allocation import (
    AllocationMethod,
    CapitalAllocator,
)
from trading_agent.portfolio.capital_allocation.kelly import HalfKellySizer, KellySizer

__all__ = [
    "CapitalAllocator",
    "AllocationMethod",
    "KellySizer",
    "HalfKellySizer",
]
