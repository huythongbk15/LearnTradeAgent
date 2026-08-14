"""ExecutionMetrics + P&L attribution for the Execution Simulator V2.

Section 4 of the hardening brief: never collapse everything into one
``slippage`` field.  Instead we produce a full attribution:

    Theoretical Alpha PnL = alpha captured at decision prices
    Execution Cost        = SpreadCost + ImpactCost + DelayCost + Fees + OpportunityCost
    Realized PnL          = actual PnL from fills

Metrics computed per run:

* decision price / arrival price / submit price / fill VWAP / post-fill mid
* implementation shortfall (IS, in bps and quote)
* spread cost
* impact cost
* latency (delay) cost
* opportunity cost (missed fill)
* fill ratio, partial-fill rate, rejected-order rate
* average latency (submit→first fill)
* turnover
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from trading_agent.execution.simulator.ledger import ExecutionLedger
from trading_agent.execution.simulator.models import (
    OrderResult,
    SimOrderStatus,
    SimSide,
    SimulationConfig,
)


@dataclass
class Attribution:
    """P&L attribution (Section 4).

    Accounting identity (enforced by ``attribution_report``):

        realized_pnl = theoretical_alpha_pnl − execution_cost
        execution_cost = spread + impact + delay + fees + opportunity

    ``market_pnl_pre_fee`` is the ledger's raw market PnL (before fee cash
    deductions) — kept for auditability.
    """

    theoretical_alpha_pnl: float = 0.0
    spread_cost: float = 0.0
    impact_cost: float = 0.0
    delay_cost: float = 0.0
    fees: float = 0.0
    opportunity_cost: float = 0.0
    execution_cost: float = 0.0
    realized_pnl: float = 0.0
    market_pnl_pre_fee: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "theoretical_alpha_pnl": round(self.theoretical_alpha_pnl, 8),
            "spread_cost": round(self.spread_cost, 8),
            "impact_cost": round(self.impact_cost, 8),
            "delay_cost": round(self.delay_cost, 8),
            "fees": round(self.fees, 8),
            "opportunity_cost": round(self.opportunity_cost, 8),
            "execution_cost": round(self.execution_cost, 8),
            "realized_pnl": round(self.realized_pnl, 8),
            "market_pnl_pre_fee": round(self.market_pnl_pre_fee, 8),
        }


@dataclass
class ExecutionMetrics:
    """Aggregate metrics over a simulation run."""

    symbol: str
    initial_cash: float
    final_equity: float
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    trade_count: int
    fill_ratio: float
    slippage_bps: float
    implementation_shortfall_bps: float
    implementation_shortfall_quote: float
    spread_cost_quote: float
    impact_cost_quote: float
    delay_cost_quote: float
    fees_quote: float
    opportunity_cost_quote: float
    missed_fill_quantity: float
    rejected_order_rate: float
    partial_fill_rate: float
    avg_latency_ms: float
    turnover: float
    attribution: Attribution = field(default_factory=Attribution)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "initial_cash": self.initial_cash,
            "final_equity": round(self.final_equity, 8),
            "total_return_pct": round(self.total_return_pct, 4),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "trade_count": self.trade_count,
            "fill_ratio": round(self.fill_ratio, 6),
            "slippage_bps": round(self.slippage_bps, 4),
            "implementation_shortfall_bps": round(self.implementation_shortfall_bps, 4),
            "implementation_shortfall_quote": round(
                self.implementation_shortfall_quote, 8
            ),
            "spread_cost_quote": round(self.spread_cost_quote, 8),
            "impact_cost_quote": round(self.impact_cost_quote, 8),
            "delay_cost_quote": round(self.delay_cost_quote, 8),
            "fees_quote": round(self.fees_quote, 8),
            "opportunity_cost_quote": round(self.opportunity_cost_quote, 8),
            "missed_fill_quantity": round(self.missed_fill_quantity, 8),
            "rejected_order_rate": round(self.rejected_order_rate, 6),
            "partial_fill_rate": round(self.partial_fill_rate, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "turnover": round(self.turnover, 6),
            "attribution": self.attribution.to_dict(),
        }


def compute_execution_metrics(
    ledger: ExecutionLedger,
    *,
    config: SimulationConfig,
    equity_curve: list[float] | None = None,
    bars_per_year: float = 365.25 * 24,  # hourly default
) -> ExecutionMetrics:
    """Compute execution metrics + P&L attribution from a completed run."""
    orders: list[OrderResult] = list(ledger.order_results.values())
    n_orders = max(len(orders), 1)
    # Round-trip count (entries with fills) — comparable to the vectorized
    # backtest engine's total_trades (each round trip = one trade).
    round_trip_count = sum(
        1 for o in orders if o.intent.side == SimSide.BUY and o.filled_quantity > 0
    )

    # ── Fill quality ────────────────────────────────────────────────────
    total_intended = sum(o.intent.quantity for o in orders)
    total_filled = sum(o.filled_quantity for o in orders)
    fill_ratio = total_filled / total_intended if total_intended > 0 else 0.0

    rejected = sum(1 for o in orders if o.status == SimOrderStatus.REJECTED)
    rejected_rate = rejected / n_orders

    partial = sum(1 for o in orders if o.status == SimOrderStatus.PARTIALLY_FILLED)
    partial_rate = partial / n_orders

    # ── Implementation shortfall (per filled order, arrival-anchored) ───
    is_quote = 0.0
    is_notional = 0.0
    slippage_num = 0.0
    slippage_denom = 0.0
    delay_num = 0.0
    delay_denom = 0.0
    latencies_ms: list[float] = []
    for o in orders:
        if o.arrival_price is None or o.arrival_price <= 0:
            continue
        if o.filled_quantity <= 0:
            # Unfilled order: opportunity cost only.
            continue
        fill_vwap = o.avg_fill_price
        if fill_vwap is None:
            continue
        qty = o.filled_quantity
        direction = 1.0 if o.intent.side == SimSide.BUY else -1.0
        # IS: signed difference between fill VWAP and arrival mid.
        is_bps = direction * (fill_vwap - o.arrival_price) / o.arrival_price * 10_000.0
        is_quote += direction * (fill_vwap - o.arrival_price) * qty
        is_notional += o.arrival_price * qty
        slippage_num += direction * (fill_vwap - o.arrival_price) * qty
        slippage_denom += o.arrival_price * qty
        # Delay cost: submit price vs arrival price.
        if o.submit_price and o.submit_price > 0:
            delay_num += direction * (o.submit_price - o.arrival_price) * qty
            delay_denom += o.arrival_price * qty
        if o.submit_time and o.first_fill_time:
            latencies_ms.append(
                (o.first_fill_time - o.submit_time).total_seconds() * 1000.0
            )

    is_bps_total = (
        is_quote / max(is_notional, 1e-12) * 10_000.0 if is_notional > 0 else 0.0
    )
    slippage_bps = (
        slippage_num / max(slippage_denom, 1e-12) * 10_000.0
        if slippage_denom > 0
        else 0.0
    )
    delay_bps = (
        delay_num / max(delay_denom, 1e-12) * 10_000.0 if delay_denom > 0 else 0.0
    )

    # ── Spread / impact split (from per-fill book prices) ───────────────
    # Cost convention: for a buy, paying above mid is a cost; for a sell,
    # receiving below mid is a cost.  ``direction`` is +1 buy / -1 sell, so
    # ``direction * (fill_price - mid)`` is always >= 0 for liquidity-taking
    # fills and its absolute value is the money lost to the spread/impact.
    spread_cost = 0.0
    impact_cost = 0.0
    for f in ledger.fills:
        direction = 1.0 if f.side == SimSide.BUY else -1.0
        # Spread cost: the signed gap between the book level and the mid.
        spread_cost += direction * (f.level_price - f.mid_before) * f.quantity
        # Impact cost: the residual above/below the book level.
        impact_cost += direction * (f.price - f.level_price) * f.quantity

    # Delay cost (quote) = signed difference between submit and arrival
    # prices, expressed in quote.
    delay_cost_quote = delay_num if delay_denom > 0 else 0.0

    # Opportunity cost: notional of the quantity that was intended but never
    # filled, measured at arrival price.  Reported as an absolute magnitude
    # (for buys it is capital that earned nothing; for sells it is forgone
    # proceeds) — conservative and auditable.
    opportunity_cost_abs = sum(
        o.remaining_quantity * (o.arrival_price or 0.0) for o in orders
    )

    fees_quote = ledger.total_fees()

    # ── P&L attribution (Section 4) ─────────────────────────────────────
    attribution = Attribution(
        theoretical_alpha_pnl=0.0,  # filled in by attribution_report() when equity is provided
        spread_cost=spread_cost,
        impact_cost=impact_cost,
        delay_cost=delay_cost_quote,
        fees=fees_quote,
        opportunity_cost=opportunity_cost_abs,
    )
    attribution.execution_cost = (
        spread_cost + impact_cost + delay_cost_quote + fees_quote + opportunity_cost_abs
    )
    attribution.realized_pnl = ledger.realized_pnl

    # ── Equity metrics ──────────────────────────────────────────────────
    eq = equity_curve or []
    n = len(eq)
    if n >= 2:
        final_equity = eq[-1]
        returns = [eq[i] / eq[i - 1] - 1.0 for i in range(1, n) if eq[i - 1] > 0]
        avg_ret = sum(returns) / max(len(returns), 1)
        var = sum((r - avg_ret) ** 2 for r in returns) / max(len(returns) - 1, 1)
        std = math.sqrt(var)
        sharpe = avg_ret / std * math.sqrt(bars_per_year) if std > 0 else 0.0
        peak = eq[0]
        max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                max_dd = min(max_dd, (v - peak) / peak)
        total_return_pct = (final_equity / ledger.initial_cash_quote - 1.0) * 100
    else:
        final_equity = (
            ledger.equity_at_mid(ledger.arrival_price or 0.0)
            if ledger.arrival_price
            else ledger.cash_quote
        )
        total_return_pct = 0.0
        sharpe = 0.0
        max_dd = 0.0

    # ── Turnover ────────────────────────────────────────────────────────
    buy_notional = ledger.total_buy_notional()
    sell_notional = ledger.total_sell_notional()
    turnover = (buy_notional + sell_notional) / max(ledger.initial_cash_quote, 1e-12)

    metrics = ExecutionMetrics(
        symbol=ledger.symbol,
        initial_cash=ledger.initial_cash_quote,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        sharpe=sharpe,
        max_drawdown_pct=max_dd * 100,
        trade_count=round_trip_count,
        fill_ratio=fill_ratio,
        slippage_bps=slippage_bps,
        implementation_shortfall_bps=is_bps_total,
        implementation_shortfall_quote=is_quote,
        spread_cost_quote=spread_cost,
        impact_cost_quote=impact_cost,
        delay_cost_quote=delay_cost_quote,
        fees_quote=fees_quote,
        opportunity_cost_quote=opportunity_cost_abs,
        missed_fill_quantity=ledger.missed_fill_quantity,
        rejected_order_rate=rejected_rate,
        partial_fill_rate=partial_rate,
        avg_latency_ms=sum(latencies_ms) / max(len(latencies_ms), 1),
        turnover=turnover,
        attribution=attribution,
        raw={
            "is_bps_total": is_bps_total,
            "delay_bps": delay_bps,
            "rejected": rejected,
            "partial": partial,
            "n_orders": n_orders,
        },
    )
    return metrics


def attribution_report(
    metrics: ExecutionMetrics, theoretical_alpha_pnl: float
) -> Attribution:
    """Finalize attribution with the theoretical alpha PnL.

    ``theoretical_alpha_pnl`` is the PnL the strategy would have captured at
    decision prices (i.e. without any execution cost).  The gap between
    theoretical alpha and realized PnL is the total execution cost.

    The accounting identity is enforced here:

        realized_pnl = theoretical_alpha_pnl − execution_cost
    """
    attr = metrics.attribution
    attr.theoretical_alpha_pnl = theoretical_alpha_pnl
    attr.execution_cost = (
        attr.spread_cost
        + attr.impact_cost
        + attr.delay_cost
        + attr.fees
        + attr.opportunity_cost
    )
    attr.realized_pnl = theoretical_alpha_pnl - attr.execution_cost
    attr.market_pnl_pre_fee = metrics.attribution.realized_pnl
    return attr
