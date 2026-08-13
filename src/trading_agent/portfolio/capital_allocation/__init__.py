"""Capital Allocation module for multi-strategy portfolio management."""

from trading_agent.portfolio.capital_allocation.allocation import (
    CapitalAllocator,
    AllocationMethod,
)
from trading_agent.portfolio.capital_allocation.kelly import KellySizer, HalfKellySizer

__all__ = [
    "CapitalAllocator",
    "AllocationMethod",
    "KellySizer",
    "HalfKellySizer",
]
