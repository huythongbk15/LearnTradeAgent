"""Execution Simulator V2 — event-driven, deterministic execution simulation.

Wave A of the reality-gap hardening program.  Backtest strategy performance
must be evaluated *after* realistic execution assumptions.  This package
provides:

* MarketReplayEngine (event-driven, OHLCV replay with optional L2 books)
* OrderBookState (synthetic depth from OHLCV, or real L2 snapshots)
* FillModel (market sweep, limit passive fills, partial fills, queue)
* ImpactModel (temporary impact + decay + adverse selection)
* FeeModel (maker/taker, fee asset)
* ExecutionLedger (deterministic accounting)
* ExecutionMetrics + P&L attribution
* RealityGapReport / RealityGapScore
* versioned execution model (``versions.py``)

Everything is deterministic given ``random_seed`` + ``market_data_manifest``.
"""

from trading_agent.execution.simulator.models import (
    Fill,
    OrderIntent,
    OrderResult,
    RejectReason,
    SimOrderStatus,
    SimOrderType,
    SimSide,
    SimulationConfig,
    quantize_price,
    quantize_qty,
)
from trading_agent.execution.simulator.orderbook import (
    OrderBookState,
    build_book_from_bar,
    build_book_from_l2,
)
from trading_agent.execution.simulator.fill_model import FillModel
from trading_agent.execution.simulator.impact_model import ImpactModel
from trading_agent.execution.simulator.fee_model import FeeModel
from trading_agent.execution.simulator.ledger import ExecutionLedger
from trading_agent.execution.simulator.metrics import (
    Attribution,
    ExecutionMetrics,
    attribution_report,
    compute_execution_metrics,
)
from trading_agent.execution.simulator.engine import (
    MarketReplayEngine,
    SimulatedExecutionResult,
    run_strategy_through_simulator,
)
from trading_agent.execution.simulator.reality_gap import (
    REALITY_GAP_METRICS,
    RealityGapReport,
    compute_reality_gap,
    promotion_check,
    reality_gap_between,
)
from trading_agent.execution.simulator.versions import model_versions

__all__ = [
    "Attribution",
    "ExecutionLedger",
    "ExecutionMetrics",
    "FeeModel",
    "Fill",
    "FillModel",
    "ImpactModel",
    "MarketReplayEngine",
    "OrderBookState",
    "OrderIntent",
    "OrderResult",
    "REALITY_GAP_METRICS",
    "RealityGapReport",
    "RejectReason",
    "SimOrderStatus",
    "SimOrderType",
    "SimSide",
    "SimulatedExecutionResult",
    "SimulationConfig",
    "attribution_report",
    "build_book_from_bar",
    "build_book_from_l2",
    "compute_execution_metrics",
    "compute_reality_gap",
    "model_versions",
    "promotion_check",
    "quantize_price",
    "quantize_qty",
    "reality_gap_between",
    "run_strategy_through_simulator",
]