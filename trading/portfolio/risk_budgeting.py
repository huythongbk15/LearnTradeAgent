"""
Risk Budgeting & Correlation Monitoring

Implements:
- Risk Parity / Equal Risk Contribution (ERC)
- Maximum Diversification
- Rolling Correlation Matrix
- Regime-aware clustering
- Portfolio Drawdown Control
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf
from sklearn.mixture import GaussianMixture

from trading.exchanges.models import Symbol, AssetClass, MarketType

logger = logging.getLogger(__name__)


class RiskBudgetMethod(str, Enum):
    """Risk budgeting method"""
    EQUAL_RISK_CONTRIBUTION = "erc"  # Equal Risk Contribution
    RISK_PARITY = "risk_parity"  # Classic risk parity
    MAX_DIVERSIFICATION = "max_div"  # Maximum Diversification
    MIN_VARIANCE = "min_var"  # Minimum Variance
    INVERSE_VOL = "inv_vol"  # Inverse Volatility
    HIERARCHICAL_RP = "hrp"  # Hierarchical Risk Parity


class CorrelationMethod(str, Enum):
    """Correlation calculation method"""
    PEARSON = "pearson"
    SPEARMAN = "spearman"
    KENDALL = "kendall"
    EWMA = "ewma"  # Exponentially Weighted Moving Average
    LEDOIT_WOLF = "ledoit_wolf"  # Ledoit-Wolf shrinkage


@dataclass
class RiskBudgetResult:
    """Result of risk budgeting optimization"""
    weights: dict[Symbol, Decimal]
    risk_contributions: dict[Symbol, Decimal]
    portfolio_vol: Decimal
    diversification_ratio: Decimal
    method: RiskBudgetMethod
    timestamp: datetime = field(default_factory=datetime.now)
    success: bool = True
    message: str = ""


@dataclass
class CorrelationMatrix:
    """Rolling correlation matrix with metadata"""
    symbols: list[Symbol]
    matrix: np.ndarray
    method: CorrelationMethod
    window: int
    timestamp: datetime = field(default_factory=datetime.now)
    regime: Optional[int] = None  # Current regime label

    def get_correlation(self, s1: Symbol, s2: Symbol) -> float:
        """Get correlation between two symbols"""
        try:
            i = self.symbols.index(s1)
            j = self.symbols.index(s2)
            return float(self.matrix[i, j])
        except ValueError:
            return 0.0

    def get_cluster(self, n_clusters: int = 3) -> dict[int, list[Symbol]]:
        """Cluster symbols by correlation"""
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform

        # Convert correlation to distance (1 - |corr|)
        # Set diagonal to 0 for valid distance matrix
        corr_matrix = np.abs(self.matrix).copy()
        np.fill_diagonal(corr_matrix, 1.0)
        dist = squareform(1 - corr_matrix)
        link = linkage(dist, method='ward')
        labels = fcluster(link, n_clusters, criterion='maxclust')

        clusters = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(label, []).append(self.symbols[idx])
        return clusters


class RiskBudgeter:
    """
    Risk Budgeting Engine

    Implements multiple risk budgeting methods:
    - ERC: Equal Risk Contribution
    - Risk Parity: Risk proportional to weights
    - Max Diversification: Maximize diversification ratio
    - Min Variance: Minimize portfolio variance
    - Inverse Vol: Weight inversely proportional to volatility
    - HRP: Hierarchical Risk Parity (cluster-based)
    """

    def __init__(
        self,
        method: RiskBudgetMethod = RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION,
        min_weight: float = 0.0,
        max_weight: float = 1.0,
        long_only: bool = True,
        cov_method: CorrelationMethod = CorrelationMethod.LEDOIT_WOLF,
        ewma_lambda: float = 0.94,
    ):
        self.method = method
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.long_only = long_only
        self.cov_method = cov_method
        self.ewma_lambda = ewma_lambda

    def estimate_covariance(self, returns: pd.DataFrame) -> np.ndarray:
        """Estimate covariance matrix"""
        if self.cov_method == CorrelationMethod.LEDOIT_WOLF:
            lw = LedoitWolf()
            lw.fit(returns)
            return lw.covariance_
        elif self.cov_method == CorrelationMethod.EWMA:
            return self._ewma_covariance(returns)
        else:
            # Sample covariance
            return returns.cov().values

    def _ewma_covariance(self, returns: pd.DataFrame) -> np.ndarray:
        """Exponentially weighted covariance"""
        n = len(returns)
        weights = np.array([self.ewma_lambda ** (n - 1 - i) for i in range(n)])
        weights = weights / weights.sum()

        weighted_returns = returns.values * np.sqrt(weights[:, np.newaxis])
        return np.cov(weighted_returns, rowvar=False)

    def _risk_contribution(self, weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
        """Calculate risk contribution of each asset"""
        port_vol = np.sqrt(weights @ cov @ weights)
        if port_vol == 0:
            return np.zeros_like(weights)
        marginal_risk = cov @ weights / port_vol
        return weights * marginal_risk

    def _objective_erc(self, weights: np.ndarray, cov: np.ndarray, target_risk: np.ndarray | None = None) -> float:
        """ERC objective: minimize sum of squared risk contribution differences"""
        rc = self._risk_contribution(weights, cov)
        if target_risk is None:
            target_risk = np.ones_like(weights) / len(weights)
        return np.sum((rc - target_risk) ** 2)

    def _objective_max_div(self, weights: np.ndarray, cov: np.ndarray) -> float:
        """Maximize diversification ratio"""
        weights = np.maximum(weights, 1e-8)
        port_vol = np.sqrt(weights @ cov @ weights)
        avg_vol = np.sum(weights * np.sqrt(np.diag(cov)))
        if port_vol == 0:
            return 1e6
        return -avg_vol / port_vol  # Negative for minimization

    def _objective_min_var(self, weights: np.ndarray, cov: np.ndarray) -> float:
        """Minimize portfolio variance"""
        return weights @ cov @ weights

    def optimize(self, returns: pd.DataFrame, target_risk: np.ndarray | None = None) -> RiskBudgetResult:
        """Run risk budgeting optimization"""
        symbols = [Symbol(s, "USD", AssetClass.CRYPTO, MarketType.SPOT, "test") for s in returns.columns]  # Simplified
        n = len(symbols)

        # Estimate covariance
        cov = self.estimate_covariance(returns)

        # Initial guess: equal weights
        x0 = np.ones(n) / n

        # Bounds
        bounds = [(self.min_weight, self.max_weight)] * n

        # Constraints: sum to 1
        constraints = [{'type': 'eq', 'fun': lambda x: np.sum(x) - 1}]

        # Long only constraint
        if self.long_only:
            bounds = [(0, self.max_weight)] * n

        # Select objective
        if self.method == RiskBudgetMethod.EQUAL_RISK_CONTRIBUTION:
            objective = lambda x: self._objective_erc(x, cov, target_risk)
        elif self.method == RiskBudgetMethod.MAX_DIVERSIFICATION:
            objective = lambda x: self._objective_max_div(x, cov)
        elif self.method == RiskBudgetMethod.MIN_VARIANCE:
            objective = lambda x: self._objective_min_var(x, cov)
        elif self.method == RiskBudgetMethod.INVERSE_VOL:
            vol = np.sqrt(np.diag(cov))
            inv_vol = 1 / vol
            inv_vol = inv_vol / inv_vol.sum()
            weights = np.clip(inv_vol, self.min_weight, self.max_weight)
            weights = weights / weights.sum()
            return self._create_result(weights, cov, symbols)
        elif self.method == RiskBudgetMethod.HIERARCHICAL_RP:
            return self._hrp_optimize(returns, cov, symbols)
        else:
            raise ValueError(f"Unknown method: {self.method}")

        # Optimize
        result = minimize(
            objective,
            x0,
            method='SLSQP',
            bounds=bounds,
            constraints=constraints,
            options={'maxiter': 1000, 'ftol': 1e-10}
        )

        if not result.success:
            logger.warning(f"Optimization failed: {result.message}")
            return RiskBudgetResult(
                weights={},
                risk_contributions={},
                portfolio_vol=Decimal(0),
                diversification_ratio=Decimal(0),
                method=self.method,
                success=False,
                message=result.message
            )

        weights = np.maximum(result.x, 0)
        weights = weights / weights.sum()

        return self._create_result(weights, cov, symbols)

    def _create_result(self, weights: np.ndarray, cov: np.ndarray, symbols: list[Symbol]) -> RiskBudgetResult:
        """Create result object from weights"""
        weight_dict = {s: Decimal(str(w)) for s, w in zip(symbols, weights)}

        # Risk contributions
        rc = self._risk_contribution(weights, cov)
        rc_dict = {s: Decimal(str(r)) for s, r in zip(symbols, rc)}

        # Portfolio volatility
        port_vol = Decimal(str(np.sqrt(weights @ cov @ weights)))

        # Diversification ratio
        avg_vol = np.sum(weights * np.sqrt(np.diag(cov)))
        div_ratio = Decimal(str(avg_vol / float(port_vol))) if port_vol > 0 else Decimal(0)

        return RiskBudgetResult(
            weights=weight_dict,
            risk_contributions=rc_dict,
            portfolio_vol=port_vol,
            diversification_ratio=div_ratio,
            method=self.method,
            success=True
        )

    def _hrp_optimize(self, returns: pd.DataFrame, cov: np.ndarray, symbols: list[Symbol]) -> RiskBudgetResult:
        """Hierarchical Risk Parity optimization"""
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform

        # Correlation matrix
        corr = returns.corr().values
        dist = squareform(1 - np.abs(corr))
        link = linkage(dist, method='ward')

        # Recursive bisection
        def get_quasi_diag(link):
            link = link.astype(int)
            sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
            num_items = link[-1, 3]
            while sort_ix.max() >= num_items:
                sort_ix.index = range(0, len(sort_ix) * 2, 2)
                df0 = sort_ix[sort_ix >= num_items]
                i = df0.index
                j = df0.values - num_items
                sort_ix[i] = link[j, 0]
                sort_ix[i + 1] = link[j, 1]
                df0 = sort_ix[sort_ix < num_items]
            return sort_ix.values

        sort_ix = get_quasi_diag(link)
        sorted_cov = cov[np.ix_(sort_ix, sort_ix)]
        sorted_symbols = [symbols[i] for i in sort_ix]

        # Recursive bisection for weights
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

            alpha = 1 - left_vol / (left_vol + right_vol)

            left_w = get_rec_bipart(cov, left_items)
            right_w = get_rec_bipart(cov, right_items)

            result = {}
            for k, v in left_w.items():
                result[k] = v * alpha
            for k, v in right_w.items():
                result[k] = v * (1 - alpha)
            return result

        # Map back to original symbols
        weights_dict = get_rec_bipart(cov, list(range(len(sorted_symbols))))
        weights = np.array([weights_dict.get(i, 0) for i in range(len(sorted_symbols))])

        # Reorder to original order
        inv_sort = np.argsort(sort_ix)
        weights = weights[inv_sort]

        return self._create_result(weights, cov, symbols)


class CorrelationMonitor:
    """
    Rolling Correlation Monitor with Regime Detection

    Features:
    - Multiple correlation methods (Pearson, Spearman, Kendall, EWMA)
    - Rolling windows (30d, 90d, etc.)
    - Regime detection via Gaussian Mixture Model
    - Correlation clustering
    - Alert on correlation breakdown/spikes
    """

    def __init__(
        self,
        window: int = 30,
        method: CorrelationMethod = CorrelationMethod.PEARSON,
        min_periods: int = 10,
        n_regimes: int = 3,
        alert_threshold: float = 0.3,  # Alert if correlation changes by this much
    ):
        self.window = window
        self.method = method
        self.min_periods = min_periods
        self.n_regimes = n_regimes
        self.alert_threshold = alert_threshold
        self._regime_model: Optional[GaussianMixture] = None
        self._current_correlation: Optional[CorrelationMatrix] = None
        self._history: list[CorrelationMatrix] = []

    def update(self, returns: pd.DataFrame) -> CorrelationMatrix:
        """Update correlation matrix with new returns"""
        if len(returns) < self.min_periods:
            logger.warning(f"Insufficient data for correlation: {len(returns)} < {self.min_periods}")
            return self._current_correlation

        # Calculate correlation
        if self.method == CorrelationMethod.EWMA:
            corr_matrix = self._ewma_correlation(returns)
        else:
            corr_matrix = returns.rolling(self.window, min_periods=self.min_periods).corr().iloc[-len(returns.columns):]

        # Get latest correlation matrix
        symbols = [Symbol(s, "USD", AssetClass.CRYPTO, MarketType.SPOT, "test") for s in returns.columns]  # Simplified
        latest_corr = corr_matrix.iloc[-len(symbols):].values

        # Create correlation matrix object
        corr_obj = CorrelationMatrix(
            symbols=symbols,
            matrix=latest_corr,
            method=self.method,
            window=self.window,
        )

        # Detect regime
        if self.n_regimes > 1:
            corr_obj.regime = self._detect_regime(latest_corr)

        # Check for alerts
        self._check_alerts(corr_obj)

        self._current_correlation = corr_obj
        self._history.append(corr_obj)

        # Keep limited history
        if len(self._history) > 1000:
            self._history = self._history[-500:]

        return corr_obj

    def _ewma_correlation(self, returns: pd.DataFrame) -> pd.DataFrame:
        """EWMA correlation"""
        n = len(returns)
        weights = np.array([self.ewma_lambda ** (n - 1 - i) for i in range(n)])
        weights = weights / weights.sum()

        weighted_returns = returns.values * np.sqrt(weights[:, np.newaxis])
        return pd.DataFrame(np.corrcoef(weighted_returns, rowvar=False), index=returns.columns, columns=returns.columns)

    def _detect_regime(self, corr_matrix: np.ndarray) -> int:
        """Detect correlation regime using GMM"""
        # Use upper triangle of correlation matrix as features
        n = corr_matrix.shape[0]
        triu_idx = np.triu_indices(n, k=1)
        features = corr_matrix[triu_idx].reshape(1, -1)

        if self._regime_model is None or len(self._history) % 20 == 0:
            # Retrain periodically
            hist_features = []
            for h in self._history[-100:]:
                h_triu = h.matrix[triu_idx]
                hist_features.append(h_triu)
            if len(hist_features) >= self.n_regimes:
                hist_features = np.array(hist_features)
                self._regime_model = GaussianMixture(
                    n_components=self.n_regimes,
                    random_state=42,
                    covariance_type='full'
                )
                self._regime_model.fit(hist_features)

        if self._regime_model is not None:
            return int(self._regime_model.predict(features)[0])
        return 0

    def _check_alerts(self, current: CorrelationMatrix) -> None:
        """Check for correlation alerts"""
        if len(self._history) < 2:
            return

        prev = self._history[-1]
        if prev is None:
            return

        # Check max correlation change
        diff = np.abs(current.matrix - prev.matrix)
        max_change = np.max(diff)

        if max_change > self.alert_threshold:
            logger.warning(f"Correlation regime change detected: max change = {max_change:.3f}")

    def get_correlation(self, s1: Symbol, s2: Symbol) -> float:
        """Get current correlation between two symbols"""
        if self._current_correlation is None:
            return 0.0
        return self._current_correlation.get_correlation(s1, s2)

    def get_clustered_symbols(self, n_clusters: int = 3) -> dict[int, list[Symbol]]:
        """Get symbols clustered by correlation"""
        if self._current_correlation is None:
            return {}
        return self._current_correlation.get_cluster(n_clusters)

    def get_regime(self) -> Optional[int]:
        """Get current regime"""
        if self._current_correlation is None:
            return None
        return self._current_correlation.regime


class DrawdownController:
    """
    Portfolio Drawdown Control

    Features:
    - Maximum drawdown limit
    - Drawdown-based position scaling
    - Recovery tracking
    - Multi-level drawdown limits (warning, reduce, stop)
    """

    def __init__(
        self,
        max_drawdown: float = 0.15,  # 15% max DD
        warning_threshold: float = 0.05,  # 5% warning
        reduce_threshold: float = 0.10,  # 10% reduce positions
        stop_threshold: float = 0.15,  # 15% stop trading
        recovery_factor: float = 0.5,  # Scale back at 50% recovery
        lookback_days: int = 252,
    ):
        self.max_drawdown = max_drawdown
        self.warning_threshold = warning_threshold
        self.reduce_threshold = reduce_threshold
        self.stop_threshold = stop_threshold
        self.recovery_factor = recovery_factor
        self.lookback_days = lookback_days

        self._peak_equity: Decimal = Decimal(0)
        self._current_equity: Decimal = Decimal(0)
        self._max_dd_pct: Decimal = Decimal(0)
        self._current_dd_pct: Decimal = Decimal(0)
        self._equity_history: list[tuple[datetime, Decimal]] = []
        self._is_trading_allowed = True
        self._position_multiplier = Decimal(1)

    def update_equity(self, equity: Decimal, timestamp: datetime | None = None) -> None:
        """Update equity and calculate drawdown"""
        self._current_equity = equity

        if equity > self._peak_equity:
            self._peak_equity = equity
            self._current_dd_pct = Decimal(0)
        else:
            self._current_dd_pct = (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else Decimal(0)

        self._max_dd_pct = max(self._max_dd_pct, self._current_dd_pct)

        if timestamp is None:
            timestamp = datetime.now()
        self._equity_history.append((timestamp, equity))

        # Trim history
        cutoff = timestamp - timedelta(days=self.lookback_days)
        self._equity_history = [(t, e) for t, e in self._equity_history if t > cutoff]

        # Update trading status
        self._update_trading_status()

    def _update_trading_status(self) -> None:
        """Update trading allowed status and position multiplier"""
        dd = float(self._current_dd_pct)

        if dd >= self.stop_threshold:
            self._is_trading_allowed = False
            self._position_multiplier = Decimal(0)
            logger.critical(f"DRAWDOWN STOP: {dd:.2%} >= {self.stop_threshold:.0%}")
        elif dd >= self.reduce_threshold:
            self._is_trading_allowed = True
            # Linear reduction from reduce_threshold to stop_threshold
            reduction = (dd - self.reduce_threshold) / (self.stop_threshold - self.reduce_threshold)
            self._position_multiplier = Decimal(1 - reduction * 0.8)  # Reduce to 20% max
            logger.warning(f"DRAWDOWN REDUCE: {dd:.2%}, multiplier = {self._position_multiplier:.2f}")
        elif dd >= self.warning_threshold:
            self._is_trading_allowed = True
            self._position_multiplier = Decimal(0.8)
            logger.warning(f"DRAWDOWN WARNING: {dd:.2%}")
        else:
            self._is_trading_allowed = True
            # Recovery: gradually increase multiplier back to 1
            if self._position_multiplier < 1:
                self._position_multiplier = min(
                    Decimal(1),
                    self._position_multiplier + Decimal(str(self.recovery_factor * 0.1))
                )

    def get_position_multiplier(self) -> Decimal:
        """Get current position size multiplier (0-1)"""
        return self._position_multiplier

    def is_trading_allowed(self) -> bool:
        """Check if trading is allowed"""
        return self._is_trading_allowed

    def get_drawdown_pct(self) -> Decimal:
        """Get current drawdown percentage"""
        return self._current_dd_pct

    def get_max_drawdown_pct(self) -> Decimal:
        """Get maximum drawdown percentage"""
        return self._max_dd_pct

    def get_status(self) -> dict:
        """Get full status"""
        return {
            'current_equity': float(self._current_equity),
            'peak_equity': float(self._peak_equity),
            'current_drawdown_pct': float(self._current_dd_pct),
            'max_drawdown_pct': float(self._max_dd_pct),
            'position_multiplier': float(self._position_multiplier),
            'trading_allowed': self._is_trading_allowed,
            'thresholds': {
                'warning': self.warning_threshold,
                'reduce': self.reduce_threshold,
                'stop': self.stop_threshold,
            }
        }

    def reset(self, equity: Decimal | None = None) -> None:
        """Reset drawdown controller"""
        if equity is not None:
            self._peak_equity = equity
            self._current_equity = equity
        self._current_dd_pct = Decimal(0)
        self._max_dd_pct = Decimal(0)
        self._is_trading_allowed = True
        self._position_multiplier = Decimal(1)