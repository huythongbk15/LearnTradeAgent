"""Wave D — Execution Optimization algorithms.

Pure, deterministic slice-selection algorithms for working a parent order
through the Execution Simulator V2:

* ``LiquidityAwareTwap``   — slice size adapts to spread, depth, volatility,
  recent volume, max participation and the remaining slippage budget.
* ``PovExecution``         — strict participation-rate cap on observed volume.
* ``MpcFeasibilityLayer``  — model-predictive-control *abstraction*: typed
  objectives + constraints with a deterministic reference solver and a
  fail-closed infeasibility result.  No RL yet (spec §15).

Each algorithm is a pure function of a ``SliceContext``; the driver
(``ParentOrderExecutor``) turns the returned quantities into child orders in
the simulator.  All computations are deterministic — no uncontrolled
randomness.
"""

from trading_agent.execution.algorithms.base import (
    AlgorithmVersion,
    ExecutionAlgorithm,
    MarketSnapshot,
    SliceContext,
    SliceResult,
)
from trading_agent.execution.algorithms.driver import (
    ParentOrder,
    ParentOrderExecutor,
    ParentOrderResult,
    run_parent_through_engine,
)
from trading_agent.execution.algorithms.liquidity_aware_twap import LiquidityAwareTwap
from trading_agent.execution.algorithms.mpc import (
    MpcConstraints,
    MpcFeasibilityLayer,
    MpcObjectives,
    MpcPlan,
    MpcResult,
    objective_cost,
)
from trading_agent.execution.algorithms.pov import PovExecution

__all__ = [
    "AlgorithmVersion",
    "ExecutionAlgorithm",
    "LiquidityAwareTwap",
    "MarketSnapshot",
    "MpcConstraints",
    "MpcFeasibilityLayer",
    "MpcObjectives",
    "MpcPlan",
    "MpcResult",
    "ParentOrder",
    "ParentOrderExecutor",
    "ParentOrderResult",
    "PovExecution",
    "SliceContext",
    "SliceResult",
    "objective_cost",
    "run_parent_through_engine",
]
