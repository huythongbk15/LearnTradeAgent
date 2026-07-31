"""Capital Allocation module for multi-strategy portfolio management."""

from trading.portfolio.capital_allocation.allocation import CapitalAllocator, AllocationMethod
from trading.portfolio.capital_allocation.kelly import KellySizer, HalfKellySizer
from trading.portfolio.capital_allocation.risk_parity import RiskParityAllocator

__all__ = [
    "CapitalAllocator",
    "AllocationMethod",
    "KellySizer",
    "HalfKellySizer",
    "RiskParityAllocator",
]