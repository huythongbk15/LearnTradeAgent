"""
Execution Simulator — Realistic Fill Modeling for Backtest & Paper Trading.

Provides:
- ExecutionSimulator: High-fidelity order execution simulation
- SimulatorBacktestEngine: BacktestEngine wrapper with realistic fills
- Models: Orders, fills, order book, config
"""

from trading_agent.execution.backtest_sim.models import (
    ExecutionSimulator,
    SimulatorConfig,
    SimulatorState,
    SimulatedOrder,
    SimulatedFill,
    OrderBookSnapshot,
    OrderType,
    OrderSide,
    OrderStatus,
    FillModel,
    ImpactModel,
    create_execution_simulator,
)

from trading_agent.execution.backtest_sim.backtest_integration import (
    SimulatorBacktestEngine,
    SimulatorBacktestResult,
    run_simulator_backtest,
)

__all__ = [
    "ExecutionSimulator",
    "SimulatorConfig",
    "SimulatorState",
    "SimulatedOrder",
    "SimulatedFill",
    "OrderBookSnapshot",
    "OrderType",
    "OrderSide",
    "OrderStatus",
    "FillModel",
    "ImpactModel",
    "create_execution_simulator",
    "SimulatorBacktestEngine",
    "SimulatorBacktestResult",
    "run_simulator_backtest",
]
