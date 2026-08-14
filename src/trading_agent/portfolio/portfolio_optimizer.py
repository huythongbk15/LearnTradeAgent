"""
Portfolio Optimizer

Implements:
- Mean-Variance Optimization (Markowitz)
- Hierarchical Risk Parity (HRP)
- Black-Litterman Model (with agent views)
- Resampled Efficient Frontier
- Monte Carlo Simulation
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

from trading_agent.exchanges.models import AssetClass, Symbol
from trading_agent.portfolio.risk_budgeting import RiskBudgeter, RiskBudgetMethod

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=RuntimeWarning)


class OptimizerMethod(str, Enum):
    """Portfolio optimization method"""

    MEAN_VARIANCE = "mean_variance"
    MEAN_VARIANCE_CONSTRAINED = "mv_constrained"
    HRP = "hrp"
    BLACK_LITTERMAN = "black_litterman"
    RESAMPLED_EF = "resampled_ef"
    MAX_SHARPE = "max_sharpe"
    MIN_VARIANCE = "min_variance"
    RISK_PARITY = "risk_parity"
    MAX_DIVERSIFICATION = "max_div"
    EQUAL_WEIGHT = "equal_weight"


@dataclass
class BlackLittermanViews:
    """Views for Black-Litterman model"""

    absolute: dict[Symbol, float] = field(default_factory=dict)
    relative: dict[tuple[Symbol, Symbol], float] = field(default_factory=dict)
    confidence: dict = field(default_factory=dict)


@dataclass
class OptimizationConstraints:
    """Portfolio optimization constraints"""

    min_weight: float = 0.0
    max_weight: float = 1.0
    long_only: bool = True
    max_positions: int | None = None
    asset_class_limits: dict[AssetClass, tuple[float, float]] = field(
        default_factory=dict
    )
    max_turnover: float | None = None
    current_weights: dict[Symbol, float] | None = None
    target_return: float | None = None
    risk_budget: dict[Symbol, float] | None = None
    cardinality: int | None = None


@dataclass
class OptimizationResult:
    """Result of portfolio optimization"""

    weights: dict[Symbol, Decimal]
    expected_return: Decimal
    expected_volatility: Decimal
    sharpe_ratio: Decimal
    method: OptimizerMethod
    success: bool = True
    message: str = ""
    diversification_ratio: Decimal = Decimal("0")
    max_drawdown_estimate: Decimal = Decimal("0")
    var_95: Decimal = Decimal("0")
    cvar_95: Decimal = Decimal("0")
    posterior_returns: dict[Symbol, Decimal] | None = None
    posterior_covariance: np.ndarray | None = None
    frontier_weights: list[dict[Symbol, Decimal]] = field(default_factory=list)
    frontier_returns: list[float] = field(default_factory=list)
    frontier_volatilities: list[float] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class PortfolioOptimizer:
    """Portfolio Optimization Engine"""

    def __init__(
        self,
        risk_free_rate: float = 0.02,
        method: OptimizerMethod = OptimizerMethod.MEAN_VARIANCE,
        constraints: OptimizationConstraints | None = None,
        cov_method: str = "ledoit_wolf",
        ewma_lambda: float = 0.94,
        lookback: int = 252,
    ):
        self.risk_free_rate = risk_free_rate
        self.method = method
        self.constraints = constraints or OptimizationConstraints()
        self.cov_method = cov_method
        self.ewma_lambda = ewma_lambda
        self.lookback = lookback

        self._symbols: list[Symbol] = []
        self._returns: pd.DataFrame | None = None
        self._mean_returns: np.ndarray | None = None
        self._cov_matrix: np.ndarray | None = None
        self._current_weights: dict[Symbol, float] | None = None

    def set_universe(
        self,
        symbols: list[Symbol],
        returns: pd.DataFrame,
        current_weights: dict[Symbol, float] | None = None,
    ) -> "PortfolioOptimizer":
        """Set the optimization universe"""
        self._symbols = symbols
        # Convert Symbol objects to string keys for DataFrame indexing
        symbol_keys = [f"{s.base}/{s.quote}" for s in symbols]
        self._returns = returns[symbol_keys] if not returns.empty else pd.DataFrame()
        self._current_weights = current_weights
        self.constraints.current_weights = current_weights
        self._estimate_moments()
        return self

    def _estimate_moments(self) -> None:
        """Estimate expected returns and covariance matrix"""
        if self._returns is None or len(self._returns) == 0:
            return

        rets = self._returns.tail(self.lookback)
        self._mean_returns = rets.mean().values * 252

        if self.cov_method == "ledoit_wolf":
            from sklearn.covariance import LedoitWolf

            lw = LedoitWolf()
            lw.fit(rets)
            self._cov_matrix = lw.covariance_ * 252
        elif self.cov_method == "ewma":
            self._cov_matrix = self._ewma_covariance(rets) * 252
        else:
            self._cov_matrix = rets.cov().values * 252

        self._cov_matrix = self._make_positive_definite(self._cov_matrix)

    def _ewma_covariance(self, returns: pd.DataFrame) -> np.ndarray:
        """EWMA covariance estimation"""
        n = len(returns)
        weights = np.array([self.ewma_lambda ** (n - 1 - i) for i in range(n)])
        weights = weights / weights.sum()
        weighted_returns = returns.values * np.sqrt(weights[:, np.newaxis])
        return np.cov(weighted_returns, rowvar=False)

    def _make_positive_definite(
        self, matrix: np.ndarray, epsilon: float = 1e-8
    ) -> np.ndarray:
        """Ensure covariance matrix is positive definite"""
        eigvals, eigvecs = np.linalg.eigh(matrix)
        eigvals = np.maximum(eigvals, epsilon)
        return eigvecs @ np.diag(eigvals) @ eigvecs.T

    def optimize(
        self,
        views: BlackLittermanViews | None = None,
        benchmark_weights: dict[Symbol, float] | None = None,
    ) -> OptimizationResult:
        """Run optimization based on selected method"""
        if self._mean_returns is None or self._cov_matrix is None:
            raise ValueError("Universe not set. Call set_universe() first.")

        n = len(self._symbols)

        if self.method == OptimizerMethod.EQUAL_WEIGHT:
            return self._equal_weight()
        elif self.method == OptimizerMethod.MIN_VARIANCE:
            return self._min_variance()
        elif self.method == OptimizerMethod.MAX_SHARPE:
            return self._max_sharpe()
        elif self.method == OptimizerMethod.MEAN_VARIANCE:
            return self._mean_variance()
        elif self.method == OptimizerMethod.MEAN_VARIANCE_CONSTRAINED:
            return self._mean_variance_constrained()
        elif self.method == OptimizerMethod.HRP:
            return self._hrp()
        elif self.method == OptimizerMethod.BLACK_LITTERMAN:
            if views is None:
                raise ValueError("Black-Litterman requires views")
            return self._black_litterman(views, benchmark_weights)
        elif self.method == OptimizerMethod.RESAMPLED_EF:
            return self._resampled_ef()
        elif self.method == OptimizerMethod.RISK_PARITY:
            return self._risk_parity()
        elif self.method == OptimizerMethod.MAX_DIVERSIFICATION:
            return self._max_diversification()
        else:
            raise ValueError(f"Unknown method: {self.method}")

    def _equal_weight(self) -> OptimizationResult:
        n = len(self._symbols)
        weights = {s: Decimal("1") / n for s in self._symbols}
        mu = self._mean_returns
        cov = self._cov_matrix
        w_arr = np.array([float(w) for w in weights.values()])
        port_return = float(np.sum(w_arr * mu))
        port_vol = float(np.sqrt(w_arr @ cov @ w_arr))
        return OptimizationResult(
            weights=weights,
            expected_return=Decimal(str(port_return)),
            expected_volatility=Decimal(str(port_vol)),
            sharpe_ratio=Decimal(str((port_return - self.risk_free_rate) / port_vol))
            if port_vol > 0
            else Decimal("0"),
            method=self.method,
        )

    def _min_variance(self) -> OptimizationResult:
        n = len(self._symbols)
        cov = self._cov_matrix

        def objective(w):
            return w @ cov @ w

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = np.ones(n) / n

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return self._create_result(weights)

    def _max_sharpe(self) -> OptimizationResult:
        n = len(self._symbols)
        mu = self._mean_returns
        cov = self._cov_matrix

        def objective(w):
            port_return = w @ mu
            port_vol = np.sqrt(w @ cov @ w)
            if port_vol == 0:
                return 1e6
            return -(port_return - self.risk_free_rate) / port_vol

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        if self.constraints.target_return is not None:
            constraints.append(
                {"type": "eq", "fun": lambda w: w @ mu - self.constraints.target_return}
            )

        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = np.ones(n) / n

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return self._create_result(weights)

    def _mean_variance(self) -> OptimizationResult:
        n = len(self._symbols)
        mu = self._mean_returns
        cov = self._cov_matrix

        if self.constraints.target_return is None:
            return self._max_sharpe()

        target = self.constraints.target_return

        def objective(w):
            return w @ cov @ w

        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
            {"type": "eq", "fun": lambda w: w @ mu - target},
        ]

        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = np.ones(n) / n

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return self._create_result(weights)

    def _mean_variance_constrained(self) -> OptimizationResult:
        n = len(self._symbols)
        mu = self._mean_returns
        cov = self._cov_matrix

        if self.constraints.target_return is None:
            return self._max_sharpe()

        target = self.constraints.target_return

        def objective(w):
            return w @ cov @ w

        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
            {"type": "eq", "fun": lambda w: w @ mu - target},
        ]

        if self.constraints.max_turnover is not None and self._current_weights:
            curr = np.array([self._current_weights.get(s, 0) for s in self._symbols])
            constraints.append(
                {
                    "type": "ineq",
                    "fun": lambda w: (
                        self.constraints.max_turnover - np.sum(np.abs(w - curr))
                    ),
                }
            )

        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = np.ones(n) / n

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 2000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return self._create_result(weights)

    def _hrp(self) -> OptimizationResult:
        try:
            from scipy.cluster.hierarchy import linkage
            from scipy.spatial.distance import squareform
        except ImportError:
            logger.warning("scipy not available for HRP, falling back to risk parity")
            return self._risk_parity()

        n = len(self._symbols)
        cov = self._cov_matrix

        vol = np.sqrt(np.diag(cov))
        corr = cov / np.outer(vol, vol)
        # Ensure symmetry
        corr = (corr + corr.T) / 2
        np.fill_diagonal(corr, 1.0)
        dist = squareform(1 - np.abs(corr), checks=False)
        # squareform(2D square) → 1D condensed — linkage cần condensed.
        # Chỉ chuyển nếu input bất thường vẫn còn dạng 2D (defensive).
        if dist.ndim == 2:
            dist = squareform(dist, checks=False)
        link = linkage(dist, method="ward")

        # Simplified HRP - use dendrogram ordering directly
        from scipy.cluster.hierarchy import dendrogram

        dendro = dendrogram(link, no_plot=True)
        sort_ix = np.array(dendro["leaves"])
        sorted_cov = cov[np.ix_(sort_ix, sort_ix)]
        sorted_symbols = [self._symbols[i] for i in sort_ix]

        # Recursive bisection
        def get_rec_bipart(cov, items):
            if len(items) == 1:
                return {items[0]: 1.0}
            mid = len(items) // 2
            left_items = items[:mid]
            right_items = items[mid:]
            left_cov = cov[np.ix_(left_items, left_items)]
            right_cov = cov[np.ix_(right_items, right_items)]
            left_vol = np.sqrt(np.diag(left_cov)).sum()
            right_vol = np.sqrt(np.diag(right_cov)).sum()
            alpha = (
                1 - left_vol / (left_vol + right_vol)
                if (left_vol + right_vol) > 0
                else 0.5
            )
            left_w = get_rec_bipart(cov, left_items)
            right_w = get_rec_bipart(cov, right_items)
            result = {}
            for k, v in left_w.items():
                result[k] = v * alpha
            for k, v in right_w.items():
                result[k] = v * (1 - alpha)
            return result

        weights_dict = get_rec_bipart(sorted_cov, list(range(len(sorted_symbols))))
        weights = {
            sorted_symbols[i]: Decimal(str(weights_dict.get(i, 0)))
            for i in range(len(sorted_symbols))
        }
        return self._create_result(weights)

    def _black_litterman(
        self,
        views: BlackLittermanViews,
        benchmark_weights: dict[Symbol, float] | None = None,
    ) -> OptimizationResult:
        n = len(self._symbols)
        mu = self._mean_returns
        cov = self._cov_matrix

        if benchmark_weights is not None:
            w_mkt = np.array([benchmark_weights.get(s, 0) for s in self._symbols])
            if w_mkt.sum() > 0:
                w_mkt = w_mkt / w_mkt.sum()
        else:
            inv_vol = 1 / np.sqrt(np.diag(cov))
            w_mkt = inv_vol / inv_vol.sum()

        market_portfolio_return = w_mkt @ mu
        market_portfolio_vol = np.sqrt(w_mkt @ cov @ w_mkt)
        delta = (market_portfolio_return - self.risk_free_rate) / (
            market_portfolio_vol**2
        )
        pi = delta * cov @ w_mkt

        view_list = []
        confidence_list = []

        for symbol, expected_return in views.absolute.items():
            if symbol not in self._symbols:
                continue
            p = np.zeros(n)
            p[self._symbols.index(symbol)] = 1
            view_list.append(p)
            confidence_list.append(views.confidence.get(("absolute", symbol), 0.5))

        for (s1, s2), outperformance in views.relative.items():
            if s1 not in self._symbols or s2 not in self._symbols:
                continue
            p = np.zeros(n)
            p[self._symbols.index(s1)] = 1
            p[self._symbols.index(s2)] = -1
            view_list.append(p)
            confidence_list.append(views.confidence.get(("relative", s1, s2), 0.5))

        if not view_list:
            logger.warning("No valid views provided, using implied equilibrium")
            return self._mean_variance()

        P = np.array(view_list)

        # Build Q vector from views
        Q = []
        for i, p in enumerate(P):
            # Check if it's an absolute view (only one non-zero)
            nonzeros = np.where(p != 0)[0]
            if len(nonzeros) == 1:
                # Absolute view
                sym_idx = nonzeros[0]
                sym = self._symbols[sym_idx]
                Q.append(views.absolute.get(sym, 0))
            elif len(nonzeros) == 2:
                # Relative view (long-short)
                long_idx = nonzeros[p[nonzeros] > 0][0]
                short_idx = nonzeros[p[nonzeros] < 0][0]
                long_sym = self._symbols[long_idx]
                short_sym = self._symbols[short_idx]
                Q.append(views.relative.get((long_sym, short_sym), 0))
            else:
                Q.append(0)
        Q = np.array(Q)

        tau = 1.0 / len(self._returns) if self._returns is not None else 0.025
        P_cov_P = P @ cov @ P.T
        omega = np.diag(
            np.diag(P_cov_P)
            * (1 - np.array(confidence_list))
            / np.array(confidence_list)
        )

        try:
            inv_term = np.linalg.inv(P @ cov @ P.T + omega)
            posterior_mu = pi + cov @ P.T @ inv_term @ (Q - P @ pi)
            posterior_cov = cov - cov @ P.T @ inv_term @ P @ cov
        except np.linalg.LinAlgError:
            logger.warning("Singular matrix in BL, using prior")
            posterior_mu = pi
            posterior_cov = cov

        def objective(w):
            port_return = w @ posterior_mu
            port_vol = np.sqrt(w @ posterior_cov @ w)
            if port_vol == 0:
                return 1e6
            return -(port_return - self.risk_free_rate) / port_vol

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = w_mkt

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        w_arr = np.array([float(v) for v in weights.values()])
        port_return = float(w_arr @ posterior_mu)
        port_vol = float(np.sqrt(w_arr @ posterior_cov @ w_arr))

        return OptimizationResult(
            weights=weights,
            expected_return=Decimal(str(port_return)),
            expected_volatility=Decimal(str(port_vol)),
            sharpe_ratio=Decimal(str((port_return - self.risk_free_rate) / port_vol))
            if port_vol > 0
            else Decimal("0"),
            method=self.method,
            posterior_returns={
                s: Decimal(str(posterior_mu[i])) for i, s in enumerate(self._symbols)
            },
            posterior_covariance=posterior_cov,
        )

    def _resampled_ef(self, n_portfolios: int = 500) -> OptimizationResult:
        n = len(self._symbols)
        mu = self._mean_returns
        cov = self._cov_matrix

        resampled_weights = []
        resampled_returns = []
        resampled_vols = []

        for _ in range(n_portfolios):
            sample_idx = np.random.choice(
                len(self._returns), len(self._returns), replace=True
            )
            sample_returns = self._returns.iloc[sample_idx]
            sample_mu = sample_returns.mean().values * 252
            from sklearn.covariance import LedoitWolf

            lw = LedoitWolf()
            lw.fit(sample_returns)
            sample_cov = lw.covariance_ * 252
            target = np.random.uniform(sample_mu.min(), sample_mu.max())

            def obj(w):
                return w @ sample_cov @ w

            constraints = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1},
                {"type": "eq", "fun": lambda w: w @ sample_mu - target},
            ]
            bounds = [(0, 1)] * n
            x0 = np.ones(n) / n

            res = minimize(
                obj,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 500},
            )
            if res.success:
                w = res.x
                resampled_weights.append(w)
                resampled_returns.append(w @ sample_mu)
                resampled_vols.append(np.sqrt(w @ sample_cov @ w))

        if not resampled_weights:
            return self._equal_weight()

        avg_weights = np.mean(resampled_weights, axis=0)
        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(avg_weights)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        result = self._create_result(weights)
        result.frontier_weights = [
            {self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(w)}
            for w in resampled_weights[:100]
        ]
        result.frontier_returns = resampled_returns[:100]
        result.frontier_volatilities = resampled_vols[:100]
        return result

    def _risk_parity(self) -> OptimizationResult:
        risk_budgeter = RiskBudgeter(method=RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION)
        rb_result = risk_budgeter.optimize(self._returns)

        if not rb_result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=rb_result.message or "Risk parity failed",
            )

        weights = {s: Decimal(str(float(w))) for s, w in rb_result.weights.items()}
        return self._create_result(weights)

    def _max_diversification(self) -> OptimizationResult:
        n = len(self._symbols)
        cov = self._cov_matrix

        def objective(w):
            w = np.maximum(w, 1e-8)
            port_vol = np.sqrt(w @ cov @ w)
            avg_vol = np.sum(w * np.sqrt(np.diag(cov)))
            if port_vol == 0:
                return 1e6
            return -avg_vol / port_vol

        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
        bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
        x0 = np.ones(n) / n

        result = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-10},
        )

        if not result.success:
            return OptimizationResult(
                weights={},
                expected_return=Decimal("0"),
                expected_volatility=Decimal("0"),
                sharpe_ratio=Decimal("0"),
                method=self.method,
                success=False,
                message=result.message,
            )

        weights = {
            self._symbols[i]: Decimal(str(max(0, w))) for i, w in enumerate(result.x)
        }
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
        return self._create_result(weights)

    def _create_result(self, weights: dict[Symbol, Decimal]) -> OptimizationResult:
        w = np.array([float(weights.get(s, Decimal("0"))) for s in self._symbols])
        mu = self._mean_returns
        cov = self._cov_matrix

        port_return = float(w @ mu)
        port_vol = float(np.sqrt(w @ cov @ w))
        sharpe = (port_return - self.risk_free_rate) / port_vol if port_vol > 0 else 0

        avg_vol = np.sum(w * np.sqrt(np.diag(cov)))
        div_ratio = float(avg_vol / port_vol) if port_vol > 0 else 0

        z_95 = norm.ppf(0.05)
        var_95 = port_return + z_95 * port_vol
        cvar_95 = port_return - port_vol * norm.pdf(z_95) / 0.05

        return OptimizationResult(
            weights=weights,
            expected_return=Decimal(str(port_return)),
            expected_volatility=Decimal(str(port_vol)),
            sharpe_ratio=Decimal(str(sharpe)),
            method=self.method,
            diversification_ratio=Decimal(str(div_ratio)),
            var_95=Decimal(str(var_95)),
            cvar_95=Decimal(str(cvar_95)),
        )

    def efficient_frontier(
        self,
        n_points: int = 50,
        min_return: float | None = None,
        max_return: float | None = None,
    ) -> tuple[list[float], list[float], list[dict[Symbol, Decimal]]]:
        if self._mean_returns is None:
            raise ValueError("Universe not set")

        mu = self._mean_returns
        cov = self._cov_matrix
        n = len(self._symbols)

        if min_return is None:
            min_return = float(mu.min())
        if max_return is None:
            max_return = float(mu.max())

        target_returns = np.linspace(min_return, max_return, n_points)
        frontier_returns = []
        frontier_vols = []
        frontier_weights = []

        for target in target_returns:

            def obj(w):
                return w @ cov @ w

            constraints = [
                {"type": "eq", "fun": lambda w: np.sum(w) - 1},
                {"type": "eq", "fun": lambda w: w @ mu - target},
            ]
            bounds = [(self.constraints.min_weight, self.constraints.max_weight)] * n
            x0 = np.ones(n) / n

            res = minimize(
                obj,
                x0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 1000},
            )
            if res.success:
                w = res.x
                w = np.maximum(w, 0)
                w = w / w.sum() if w.sum() > 0 else w
                frontier_weights.append(
                    {self._symbols[i]: Decimal(str(w[i])) for i in range(n)}
                )
                frontier_returns.append(float(w @ mu))
                frontier_vols.append(float(np.sqrt(w @ cov @ w)))

        return frontier_returns, frontier_vols, frontier_weights

    def monte_carlo_simulation(
        self,
        weights: dict[Symbol, Decimal] | None = None,
        n_simulations: int = 200,
        time_horizon: int = 252,
        initial_value: float = 100000,
    ) -> dict:
        if weights is None:
            weights = {s: Decimal("1") / len(self._symbols) for s in self._symbols}

        w = np.array([float(weights.get(s, Decimal("0"))) for s in self._symbols])
        mu = self._mean_returns
        cov = self._cov_matrix

        simulations = []
        final_values = []

        for _ in range(n_simulations):
            random_returns = np.random.multivariate_normal(
                mu / 252, cov / 252, time_horizon
            )
            port_returns = random_returns @ w
            cum_returns = np.cumprod(1 + port_returns)
            portfolio_values = initial_value * cum_returns
            simulations.append(portfolio_values)
            final_values.append(portfolio_values[-1])

        simulations = np.array(simulations)

        return {
            "simulations": simulations,
            "final_values": np.array(final_values),
            "mean_final": float(np.mean(final_values)),
            "median_final": float(np.median(final_values)),
            "pct_5": float(np.percentile(final_values, 5)),
            "pct_95": float(np.percentile(final_values, 95)),
            "prob_loss": float(np.mean(np.array(final_values) < initial_value)),
            "prob_10pct_gain": float(
                np.mean(np.array(final_values) > initial_value * 1.1)
            ),
            "prob_20pct_gain": float(
                np.mean(np.array(final_values) > initial_value * 1.2)
            ),
            "initial_value": initial_value,
            "time_horizon": time_horizon,
            "n_simulations": n_simulations,
        }


def create_optimizer(
    method: OptimizerMethod = OptimizerMethod.MAX_SHARPE,
    risk_free_rate: float = 0.02,
    **kwargs,
) -> PortfolioOptimizer:
    """Create a portfolio optimizer with given method"""
    return PortfolioOptimizer(risk_free_rate=risk_free_rate, method=method, **kwargs)
