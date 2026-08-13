"""
Portfolio Manager Agent — phân bổ vốn giữa nhiều symbols.

Nhận signals từ Trader của mỗi symbol, tính toán allocation tối ưu:
- Capital được phân bổ dựa trên weighted confidence
- Diversification: không symbol nào > 40% portfolio
- Risk budget: tổng risk-weighted position ≤ 100%
- Phần còn lại giữ cash làm buffer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from trading_agent.agents.base import AgentMessage

logger = logging.getLogger(__name__)

MAX_SINGLE_POSITION = 0.40  # không symbol nào quá 40%
MAX_TOTAL_RISK = 1.0  # tổng risk budget
CASH_BUFFER = 0.15  # luôn giữ 15% tiền mặt


@dataclass
class PortfolioAllocation:
    """Recommendation for capital allocation across symbols."""

    symbol: str
    allocation_pct: float  # % of total portfolio
    conviction: float  # 0.0 - 1.0
    signal: str  # BUY / HOLD / SELL
    reasoning: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "allocation_pct": self.allocation_pct,
            "conviction": self.conviction,
            "signal": self.signal,
            "reasoning": self.reasoning,
        }


@dataclass
class PortfolioDecision:
    """Full portfolio allocation decision."""

    allocations: list[PortfolioAllocation]
    cash_pct: float
    total_risk_level: str
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocations": [a.to_dict() for a in self.allocations],
            "cash_pct": self.cash_pct,
            "total_risk_level": self.total_risk_level,
            "warnings": self.warnings,
            "details": self.details,
        }


class PortfolioManager:
    """Manages portfolio-level capital allocation.

    Input: list of Trader final signals (AgentMessage) per symbol.
    Output: PortfolioDecision with allocation recommendations.
    """

    def __init__(self, portfolio_value: float = 10000.0):
        self.portfolio_value = portfolio_value

    def allocate(
        self,
        signals: list[tuple[str, AgentMessage]],
    ) -> PortfolioDecision:
        """Allocate capital across symbols based on their signals.

        Args:
            signals: List of (symbol, trader_agent_message) pairs.

        Returns:
            PortfolioDecision with allocation recommendations.
        """
        if not signals:
            return PortfolioDecision(
                allocations=[],
                cash_pct=1.0,
                total_risk_level="NONE",
                warnings=["No signals provided — 100% cash"],
            )

        # Step 1: Compute raw scores for each signal
        scored: list[tuple[str, AgentMessage, float]] = []
        total_raw_score = 0.0

        for symbol, msg in signals:
            # Only BUY signals get allocation
            if msg.signal != "BUY":
                scored.append((symbol, msg, 0.0))
                continue

            # Risk override: HIGH/EXTREME → no allocation
            if msg.risk_level in ("HIGH", "EXTREME"):
                scored.append((symbol, msg, 0.0))
                continue

            # Raw score = confidence * weight from msg details
            weight = msg.max_position_size_pct or 0.25
            raw_score = msg.confidence * weight
            scored.append((symbol, msg, raw_score))
            total_raw_score += raw_score

        # Step 2: Normalize allocations, respecting MAX_SINGLE_POSITION
        if total_raw_score == 0:
            # No BUY signals → 100% cash
            return PortfolioDecision(
                allocations=[
                    PortfolioAllocation(
                        symbol=s,
                        allocation_pct=0.0,
                        conviction=float(m.confidence) if m.signal == "BUY" else 0.0,
                        signal=m.signal,
                        reasoning=m.reasoning or "No allocation — not a BUY signal",
                    )
                    for s, m, _ in scored
                ],
                cash_pct=1.0,
                total_risk_level="LOW",
                warnings=["No BUY signals strong enough — holding cash"],
            )

        # Budget = 1.0 - CASH_BUFFER
        budget = 1.0 - CASH_BUFFER

        # Compute preliminary allocations
        raw_allocations: list[tuple[str, float, AgentMessage, float]] = []
        for symbol, msg, score in scored:
            prelim = (score / total_raw_score) * budget
            # Clamp to MAX_SINGLE_POSITION
            final = min(prelim, MAX_SINGLE_POSITION)
            raw_allocations.append((symbol, final, msg, score))

        # Step 3: Redistribute leftover from clamping
        clamped_total = sum(a[1] for a in raw_allocations)
        if clamped_total < budget and clamped_total > 0:
            leftover = budget - clamped_total
            # Redistribute to non-clamped positions proportionally
            unclamped = [
                (i, a)
                for i, a in enumerate(raw_allocations)
                if a[1] < MAX_SINGLE_POSITION
            ]
            if unclamped:
                raw_score_unclamped = sum(a[3] for _, a in unclamped)
                for idx, alloc in unclamped:
                    if raw_score_unclamped > 0:
                        extra = (alloc[3] / raw_score_unclamped) * leftover
                        raw_allocations[idx] = (
                            alloc[0],
                            min(alloc[1] + extra, MAX_SINGLE_POSITION),
                            alloc[2],
                            alloc[3],
                        )

        total_allocated = sum(a[1] for a in raw_allocations)
        cash_pct = 1.0 - total_allocated

        # Step 4: Determine overall risk level
        risk_levels = [m.risk_level for _, m, _ in scored if m.risk_level]
        if "EXTREME" in risk_levels:
            total_risk = "EXTREME"
        elif "HIGH" in risk_levels:
            total_risk = "HIGH"
        elif "MEDIUM" in risk_levels:
            total_risk = "MEDIUM"
        else:
            total_risk = "LOW"

        # Step 5: Build warnings
        warnings = []
        if cash_pct < 0:
            warnings.append("Total allocation exceeds 100% — reduce positions")
        if cash_pct > 0.5:
            warnings.append(
                f"Large cash position ({cash_pct:.0%}) — consider more deployment"
            )
        if total_risk in ("HIGH", "EXTREME"):
            warnings.append(f"Elevated portfolio risk ({total_risk})")

        allocations = [
            PortfolioAllocation(
                symbol=s,
                allocation_pct=alloc,
                conviction=float(m.confidence),
                signal=m.signal,
                reasoning=m.reasoning or "",
            )
            for s, alloc, m, _ in raw_allocations
        ]

        return PortfolioDecision(
            allocations=allocations,
            cash_pct=cash_pct,
            total_risk_level=total_risk,
            warnings=warnings,
            details={
                "portfolio_value": self.portfolio_value,
                "budget": budget,
                "total_allocated_pct": total_allocated,
            },
        )

    def summary(self, decision: PortfolioDecision) -> str:
        """Generate human-readable portfolio summary."""
        lines = [
            f"💰 Portfolio Allocation (${self.portfolio_value:,.0f})",
            f"{'─' * 50}",
        ]

        for alloc in sorted(decision.allocations, key=lambda a: -a.allocation_pct):
            bar = "█" * int(alloc.allocation_pct * 30)
            lines.append(
                f"  {alloc.symbol:12s} {alloc.allocation_pct * 100:5.1f}% "
                f"{alloc.signal:4s} {bar}"
            )

        lines.append(
            f"  {'CASH':12s} {decision.cash_pct * 100:5.1f}% "
            f"{'█' * int(decision.cash_pct * 30)}"
        )
        lines.append(f"{'─' * 50}")
        lines.append(f"  Risk Level: {decision.total_risk_level}")

        if decision.warnings:
            lines.append("")
            for w in decision.warnings:
                lines.append(f"  ⚠ {w}")

        return "\n".join(lines)
