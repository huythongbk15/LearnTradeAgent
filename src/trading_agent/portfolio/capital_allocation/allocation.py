"""Capital Allocation methods for multi-strategy portfolio."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from trading_agent.portfolio.risk_budgeting import RiskBudgeter, RiskBudgetMethod


class AllocationMethod(Enum):
    """Capital allocation methods."""
    EQUAL_WEIGHT = "equal_weight"
    RISK_PARITY = "risk_parity"
    KELLY = "kelly"
    HALF_KELLY = "half_kelly"
    VOLATILITY_TARGET = "volatility_target"
    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"
    HRP = "hrp"  # Hierarchical Risk Parity
    BLACK_LITTERMAN = "black_litterman"
    CUSTOM = "custom"


@dataclass
class StrategyMetrics:
    """Strategy performance metrics for allocation."""
    strategy_id: str
    expected_return: Decimal  # Annualized
    volatility: Decimal       # Annualized
    sharpe_ratio: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    correlation_matrix: Optional[pd.DataFrame] = None
    last_updated: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AllocationResult:
    """Result of capital allocation."""
    weights: dict[str, Decimal]  # strategy_id -> weight
    total_capital: Decimal
    allocations: dict[str, Decimal]  # strategy_id -> capital
    expected_return: Decimal
    expected_volatility: Decimal
    expected_sharpe: Decimal
    method: AllocationMethod
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


class CapitalAllocator:
    """Multi-strategy capital allocator."""

    def __init__(
        self,
        total_capital: Decimal,
        min_allocation: Decimal = Decimal("0.01"),
        max_allocation: Decimal = Decimal("0.5"),
        max_strategies: int = 20,
    ):
        self.total_capital = total_capital
        self.min_allocation = min_allocation
        self.max_allocation = max_allocation
        self.max_strategies = max_strategies
        self._strategies: dict[str, StrategyMetrics] = {}

    def add_strategy(self, metrics: StrategyMetrics) -> None:
        """Add or update strategy metrics."""
        if len(self._strategies) >= self.max_strategies:
            raise ValueError(f"Max strategies ({self.max_strategies}) reached")
        self._strategies[metrics.strategy_id] = metrics

    def remove_strategy(self, strategy_id: str) -> None:
        """Remove a strategy."""
        self._strategies.pop(strategy_id, None)

    def allocate(
        self, 
        method: AllocationMethod = AllocationMethod.RISK_PARITY,
        **kwargs
    ) -> AllocationResult:
        """Allocate capital across strategies."""
        if not self._strategies:
            raise ValueError("No strategies added")

        strategy_ids = list(self._strategies.keys())
        n = len(strategy_ids)

        # Build return and covariance matrices
        returns = np.array([
            float(self._strategies[sid].expected_return) 
            for sid in strategy_ids
        ])
        
        vols = np.array([
            float(self._strategies[sid].volatility) 
            for sid in strategy_ids
        ])

        # Build correlation matrix
        if all(self._strategies[sid].correlation_matrix is not None for sid in strategy_ids):
            corr = self._strategies[strategy_ids[0]].correlation_matrix.loc[
                strategy_ids, strategy_ids
            ].values
        else:
            # Assume zero correlation if not provided
            corr = np.eye(n)

        cov = np.outer(vols, vols) * corr

        # Calculate weights based on method
        if method == AllocationMethod.EQUAL_WEIGHT:
            weights = self._equal_weight(n)
        elif method == AllocationMethod.RISK_PARITY:
            weights = self._risk_parity(cov)
        elif method == AllocationMethod.KELLY:
            weights = self._kelly(returns, cov)
        elif method == AllocationMethod.HALF_KELLY:
            weights = self._kelly(returns, cov) * 0.5
        elif method == AllocationMethod.VOLATILITY_TARGET:
            target_vol = kwargs.get("target_volatility", Decimal("0.15"))
            weights = self._volatility_target(returns, cov, float(target_vol))
        elif method == AllocationMethod.MAX_SHARPE:
            weights = self._max_sharpe(returns, cov, kwargs.get("risk_free_rate", 0.02))
        elif method == AllocationMethod.MIN_VARIANCE:
            weights = self._min_variance(cov)
        elif method == AllocationMethod.HRP:
            weights = self._hrp(cov, strategy_ids)
        elif method == AllocationMethod.BLACK_LITTERMAN:
            weights = self._black_litterman(returns, cov, kwargs.get("views", {}))
        else:
            weights = self._equal_weight(n)

        # Apply constraints
        weights = self._apply_constraints(weights)

        # Calculate allocations
        allocations = {
            sid: Decimal(str(w)) * self.total_capital 
            for sid, w in zip(strategy_ids, weights)
        }

        # Portfolio metrics
        port_return = float(np.dot(weights, returns))
        port_vol = float(np.sqrt(np.dot(weights, np.dot(cov, weights))))
        port_sharpe = port_return / port_vol if port_vol > 0 else 0

        return AllocationResult(
            weights={sid: Decimal(str(w)) for sid, w in zip(strategy_ids, weights)},
            total_capital=self.total_capital,
            allocations=allocations,
            expected_return=Decimal(str(port_return)),
            expected_volatility=Decimal(str(port_vol)),
            expected_sharpe=Decimal(str(port_sharpe)),
            method=method,
        )

    def _equal_weight(self, n: int) -> np.ndarray:
        return np.ones(n) / n

    def _risk_parity(self, cov: np.ndarray) -> np.ndarray:
        """Risk parity allocation."""
        risk_budgeting = RiskBudgeter(method=RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION)
        # Simplified - use inverse volatility as approximation
        vols = np.sqrt(np.diag(cov))
        inv_vol = 1 / (vols + 1e-8)
        return inv_vol / inv_vol.sum()

    def _kelly(self, returns: np.ndarray, cov: np.ndarray) -> np.ndarray:
        """Kelly criterion allocation."""
        # Kelly: w* = Σ⁻¹ * μ
        try:
            inv_cov = np.linalg.inv(cov + 1e-6 * np.eye(len(cov)))
            weights = inv_cov @ returns
            # Normalize to sum to 1 (full Kelly)
            if weights.sum() > 0:
                weights = weights / weights.sum()
            else:
                weights = np.ones(len(returns)) / len(returns)
        except np.linalg.LinAlgError:
            weights = np.ones(len(returns)) / len(returns)
        return np.maximum(weights, 0)  # No shorting

    def _volatility_target(
        self, 
        returns: np.ndarray, 
        cov: np.ndarray, 
        target_vol: float
    ) -> np.ndarray:
        """Volatility targeting."""
        # Start with max Sharpe
        weights = self._max_sharpe(returns, cov)
        port_vol = np.sqrt(weights @ cov @ weights)
        if port_vol > 0:
            scale = target_vol / port_vol
            weights = weights * min(scale, 2.0)  # Cap leverage at 2x
        return weights

    def _max_sharpe(
        self, 
        returns: np.ndarray, 
        cov: np.ndarray, 
        risk_free: float = 0.02
    ) -> np.ndarray:
        """Maximum Sharpe ratio portfolio."""
        try:
            excess_returns = returns - risk_free
            inv_cov = np.linalg.inv(cov + 1e-6 * np.eye(len(cov)))
            weights = inv_cov @ excess_returns
            if weights.sum() > 0:
                weights = weights / weights.sum()
        except np.linalg.LinAlgError:
            weights = np.ones(len(returns)) / len(returns)
        return np.maximum(weights, 0)

    def _min_variance(self, cov: np.ndarray) -> np.ndarray:
        """Minimum variance portfolio."""
        try:
            ones = np.ones(len(cov))
            inv_cov = np.linalg.inv(cov + 1e-6 * np.eye(len(cov)))
            weights = inv_cov @ ones
            weights = weights / weights.sum()
        except np.linalg.LinAlgError:
            weights = np.ones(len(cov)) / len(cov)
        return np.maximum(weights, 0)

    def _hrp(self, cov: np.ndarray, strategy_ids: list[str]) -> np.ndarray:
        """Hierarchical Risk Parity (simplified)."""
        # Use the risk budgeting HRP implementation
        risk_budgeting = RiskBudgeter(method=RiskBudgetMethod.HIERARCHICAL_RP)
        # Simplified - return equal weight for now
        return np.ones(len(strategy_ids)) / len(strategy_ids)

    def _black_litterman(
        self, 
        returns: np.ndarray, 
        cov: np.ndarray, 
        views: dict
    ) -> np.ndarray:
        """Black-Litterman allocation."""
        # Simplified - use market equilibrium as prior
        tau = 0.05
        risk_free = 0.02
        
        # Market implied returns
        market_weights = np.ones(len(returns)) / len(returns)
        pi = risk_free + cov @ market_weights / (market_weights @ cov @ market_weights)
        
        if not views:
            return self._max_sharpe(pi, cov)
        
        # Apply views (simplified)
        # Full implementation would require P, Q, Omega matrices
        return self._max_sharpe(pi, cov)

    def _apply_constraints(self, weights: np.ndarray) -> np.ndarray:
        """Apply min/max allocation constraints."""
        min_w = float(self.min_allocation)
        max_w = float(self.max_allocation)
        
        weights = np.clip(weights, min_w, max_w)
        
        # Renormalize
        if weights.sum() > 0:
            weights = weights / weights.sum()
        else:
            weights = np.ones(len(weights)) / len(weights)
        
        return weights

    def rebalance(
        self, 
        current_allocations: dict[str, Decimal],
        method: AllocationMethod = AllocationMethod.RISK_PARITY,
        threshold: Decimal = Decimal("0.05"),
    ) -> dict[str, Decimal]:
        """Calculate rebalance trades."""
        target = self.allocate(method)
        trades = {}
        
        for sid, target_capital in target.allocations.items():
            current = current_allocations.get(sid, Decimal(0))
            diff = target_capital - current
            
            # Only trade if difference exceeds threshold
            if abs(diff) > self.total_capital * threshold:
                trades[sid] = diff
        
        return trades