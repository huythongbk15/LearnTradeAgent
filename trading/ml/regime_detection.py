"""
Regime Detection & Adaptive ML

Implements:
- Hidden Markov Model (HMM) for regime detection
- Gaussian Mixture Model (GMM) for regime clustering
- Online learning with River/Cremer
- Adaptive position sizing based on regime
- Volatility targeting
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from hmmlearn import hmm

warnings.filterwarnings('ignore', category=DeprecationWarning)

logger = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    """Market regime labels"""
    BULL_TREND = "bull_trend"
    BEAR_TREND = "bear_trend"
    SIDEWAYS = "sideways"
    HIGH_VOLATILITY = "high_vol"
    LOW_VOLATILITY = "low_vol"
    CRISIS = "crisis"
    RECOVERY = "recovery"
    UNKNOWN = "unknown"


class RegimeMethod(str, Enum):
    """Regime detection method"""
    HMM = "hmm"  # Hidden Markov Model
    GMM = "gmm"  # Gaussian Mixture Model
    RULE_BASED = "rule_based"  # Simple rule-based
    HYBRID = "hybrid"  # Combination


@dataclass
class RegimeState:
    """Current regime state"""
    regime: MarketRegime
    confidence: float
    probability: dict[MarketRegime, float]
    timestamp: datetime
    features: dict[str, float] = field(default_factory=dict)
    expected_duration: Optional[int] = None  # Days


@dataclass
class RegimeTransition:
    """Regime transition event"""
    from_regime: MarketRegime
    to_regime: MarketRegime
    timestamp: datetime
    confidence: float


class HMMStrategy:
    """
    Hidden Markov Model for regime detection

    Uses price returns, volatility, and volume as observations
    to infer hidden market states.
    """

    def __init__(
        self,
        n_regimes: int = 4,
        n_iter: int = 100,
        covariance_type: str = 'full',
        random_state: int = 42,
        lookback: int = 252,
    ):
        self.n_regimes = n_regimes
        self.n_iter = n_iter
        self.covariance_type = covariance_type
        self.random_state = random_state
        self.lookback = lookback

        self.model: Optional[hmm.GaussianHMM] = None
        self._regime_names: list[MarketRegime] = []
        self._fitted = False
        self._history: list[RegimeState] = []

    def _prepare_features(self, prices: pd.Series, volume: pd.Series | None = None) -> np.ndarray:
        """Prepare observation features for HMM"""
        # Log returns
        returns = np.log(prices / prices.shift(1)).dropna()

        # Rolling volatility (20-day) - fill NaN with expanding std
        vol = returns.rolling(20).std() * np.sqrt(252)
        vol = vol.bfill()  # Backfill to handle initial NaN

        # Volume features
        if volume is not None:
            # Align volume with returns
            vol_aligned = volume.reindex(returns.index).ffill()
            vol_change = vol_aligned.pct_change().bfill()  # Backfill initial NaN
            # Align all series
            min_len = min(len(returns), len(vol), len(vol_change))
            returns = returns.iloc[-min_len:]
            vol = vol.iloc[-min_len:]
            vol_change = vol_change.iloc[-min_len:]
            features = np.column_stack([returns.values, vol.values, vol_change.values])
        else:
            min_len = min(len(returns), len(vol))
            returns = returns.iloc[-min_len:]
            vol = vol.iloc[-min_len:]
            features = np.column_stack([returns.values, vol.values])

        # Standardize
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        return scaler.fit_transform(features)

    def fit(self, prices: pd.Series, volume: pd.Series | None = None) -> 'HMMStrategy':
        """Fit HMM to historical data"""
        features = self._prepare_features(prices, volume)

        self.model = hmm.GaussianHMM(
            n_components=self.n_regimes,
            covariance_type=self.covariance_type,
            n_iter=self.n_iter,
            random_state=self.random_state,
        )

        self.model.fit(features)
        self._fitted = True

        # Assign regime names based on characteristics
        self._assign_regime_names(features)
        return self

    def _assign_regime_names(self, features: np.ndarray) -> None:
        """Assign meaningful names to regimes based on characteristics"""
        # Get regime means
        means = self.model.means_
        # First feature is returns, second is volatility
        avg_returns = means[:, 0]
        avg_vol = means[:, 1]

        names = []
        for i in range(self.n_regimes):
            ret = avg_returns[i]
            vol = avg_vol[i]

            if ret > 0.0005 and vol < 0.02:
                names.append(MarketRegime.BULL_TREND)
            elif ret < -0.0005 and vol < 0.02:
                names.append(MarketRegime.BEAR_TREND)
            elif vol > 0.03:
                names.append(MarketRegime.HIGH_VOLATILITY)
            elif vol < 0.01:
                names.append(MarketRegime.LOW_VOLATILITY)
            elif ret > 0:
                names.append(MarketRegime.RECOVERY)
            elif ret < 0:
                names.append(MarketRegime.CRISIS)
            else:
                names.append(MarketRegime.SIDEWAYS)

        self._regime_names = names

    def predict(self, prices: pd.Series, volume: pd.Series | None = None) -> RegimeState:
        """Predict current regime"""
        if not self._fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        features = self._prepare_features(prices, volume)
        if len(features) == 0:
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now())

        # Get current state probabilities
        logprob, posteriors = self.model.score_samples(features)
        current_probs = posteriors[-1]

        # Most likely regime
        regime_idx = np.argmax(current_probs)
        regime = self._regime_names[regime_idx]
        confidence = float(current_probs[regime_idx])

        prob_dict = {self._regime_names[i]: float(current_probs[i]) for i in range(self.n_regimes)}

        # Expected duration (from transition matrix)
        transmat = self.model.transmat_
        expected_dur = int(1 / (1 - transmat[regime_idx, regime_idx])) if transmat[regime_idx, regime_idx] < 1 else None

        state = RegimeState(
            regime=regime,
            confidence=confidence,
            probability=prob_dict,
            timestamp=datetime.now(),
            expected_duration=expected_dur,
        )

        self._history.append(state)
        return state

    def get_transition_matrix(self) -> np.ndarray:
        """Get regime transition matrix"""
        if self.model is None:
            return np.eye(self.n_regimes)
        return self.model.transmat_

    def get_regime_history(self) -> list[RegimeState]:
        return self._history


class GMMStrategy:
    """
    Gaussian Mixture Model for regime clustering

    Clusters market states based on return/volatility characteristics
    """

    def __init__(
        self,
        n_regimes: int = 4,
        covariance_type: str = 'full',
        random_state: int = 42,
    ):
        self.n_regimes = n_regimes
        self.covariance_type = covariance_type
        self.random_state = random_state
        self.model: Optional[GaussianMixture] = None
        self._regime_names: list[MarketRegime] = []
        self._fitted = False

    def _prepare_features(self, returns: pd.Series) -> np.ndarray:
        """Prepare features: returns, rolling vol, skew, kurtosis"""
        # Rolling statistics
        roll_vol = returns.rolling(20).std() * np.sqrt(252)
        roll_skew = returns.rolling(60).skew()
        roll_kurt = returns.rolling(60).kurt()

        # Combine
        df = pd.DataFrame({
            'return': returns,
            'vol': roll_vol,
            'skew': roll_skew,
            'kurt': roll_kurt,
        }).dropna()

        # Standardize
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        return scaler.fit_transform(df)

    def fit(self, returns: pd.Series) -> 'GMMStrategy':
        """Fit GMM to returns"""
        features = self._prepare_features(returns)

        self.model = GaussianMixture(
            n_components=self.n_regimes,
            covariance_type=self.covariance_type,
            random_state=self.random_state,
        )
        self.model.fit(features)
        self._fitted = True
        self._assign_regime_names(features)
        return self

    def _assign_regime_names(self, features: np.ndarray) -> None:
        """Assign names based on cluster centers"""
        centers = self.model.means_
        names = []

        for i in range(self.n_regimes):
            avg_ret = centers[i, 0]
            avg_vol = centers[i, 1]

            if avg_ret > 0.0003 and avg_vol < 0.02:
                names.append(MarketRegime.BULL_TREND)
            elif avg_ret < -0.0003 and avg_vol < 0.02:
                names.append(MarketRegime.BEAR_TREND)
            elif avg_vol > 0.03:
                names.append(MarketRegime.HIGH_VOLATILITY)
            elif avg_vol < 0.01:
                names.append(MarketRegime.LOW_VOLATILITY)
            else:
                names.append(MarketRegime.SIDEWAYS)

        self._regime_names = names

    def predict(self, returns: pd.Series) -> RegimeState:
        """Predict current regime"""
        if not self._fitted:
            raise ValueError("Model not fitted")

        features = self._prepare_features(returns)
        if len(features) == 0:
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now())

        probs = self.model.predict_proba(features[-1:].reshape(1, -1))[0]
        regime_idx = np.argmax(probs)
        regime = self._regime_names[regime_idx]
        confidence = float(probs[regime_idx])

        prob_dict = {self._regime_names[i]: float(probs[i]) for i in range(self.n_regimes)}

        return RegimeState(
            regime=regime,
            confidence=confidence,
            probability=prob_dict,
            timestamp=datetime.now(),
        )


class RuleBasedStrategy:
    """
    Simple rule-based regime detection

    Uses moving averages, volatility, and momentum
    """

    def __init__(
        self,
        fast_ma: int = 50,
        slow_ma: int = 200,
        vol_window: int = 20,
        vol_threshold_high: float = 0.03,
        vol_threshold_low: float = 0.01,
    ):
        self.fast_ma = fast_ma
        self.slow_ma = slow_ma
        self.vol_window = vol_window
        self.vol_threshold_high = vol_threshold_high
        self.vol_threshold_low = vol_threshold_low

    def detect(self, prices: pd.Series) -> RegimeState:
        """Detect regime using simple rules"""
        if len(prices) < self.slow_ma:
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now())

        # Moving averages
        fast = prices.rolling(self.fast_ma).mean().iloc[-1]
        slow = prices.rolling(self.slow_ma).mean().iloc[-1]

        # Returns
        returns = np.log(prices / prices.shift(1)).dropna()
        vol = returns.rolling(self.vol_window).std().iloc[-1] * np.sqrt(252)

        # Momentum
        momentum = (prices.iloc[-1] / prices.iloc[-self.fast_ma] - 1) if len(prices) >= self.fast_ma else 0

        # Determine regime
        probs = {r: 0.0 for r in MarketRegime}

        if vol > self.vol_threshold_high:
            regime = MarketRegime.HIGH_VOLATILITY
            probs[regime] = 0.8
        elif vol < self.vol_threshold_low:
            regime = MarketRegime.LOW_VOLATILITY
            probs[regime] = 0.7
        elif fast > slow and momentum > 0:
            regime = MarketRegime.BULL_TREND
            probs[regime] = 0.7
        elif fast < slow and momentum < 0:
            regime = MarketRegime.BEAR_TREND
            probs[regime] = 0.7
        else:
            regime = MarketRegime.SIDEWAYS
            probs[regime] = 0.5

        return RegimeState(
            regime=regime,
            confidence=probs[regime],
            probability=probs,
            timestamp=datetime.now(),
            features={'fast_ma': float(fast), 'slow_ma': float(slow), 'vol': float(vol), 'momentum': float(momentum)},
        )


class HybridRegimeDetector:
    """
    Hybrid regime detector combining multiple methods
    """

    def __init__(
        self,
        methods: list[RegimeMethod] = None,
        weights: dict[RegimeMethod, float] = None,
    ):
        self.methods = methods or [RegimeMethod.HMM, RegimeMethod.RULE_BASED]
        self.weights = weights or {
            RegimeMethod.HMM: 0.5,
            RegimeMethod.GMM: 0.3,
            RegimeMethod.RULE_BASED: 0.2,
        }

        self._detectors: dict[RegimeMethod, Any] = {}
        self._history: list[RegimeState] = []

    def initialize(self, prices: pd.Series, volume: pd.Series | None = None) -> None:
        """Initialize all detectors"""
        returns = np.log(prices / prices.shift(1)).dropna()

        if RegimeMethod.HMM in self.methods:
            self._detectors[RegimeMethod.HMM] = HMMStrategy().fit(prices, volume)

        if RegimeMethod.GMM in self.methods:
            self._detectors[RegimeMethod.GMM] = GMMStrategy().fit(returns)

        if RegimeMethod.RULE_BASED in self.methods:
            self._detectors[RegimeMethod.RULE_BASED] = RuleBasedStrategy()

    def detect(self, prices: pd.Series, volume: pd.Series | None = None) -> RegimeState:
        """Aggregate predictions from all methods"""
        if not self._detectors:
            self.initialize(prices, volume)

        votes: dict[MarketRegime, float] = defaultdict(float)

        for method, detector in self._detectors.items():
            if method == RegimeMethod.HMM:
                state = detector.predict(prices, volume)
            elif method == RegimeMethod.GMM:
                returns = np.log(prices / prices.shift(1)).dropna()
                state = detector.predict(returns)
            elif method == RegimeMethod.RULE_BASED:
                state = detector.detect(prices)
            else:
                continue

            weight = self.weights.get(method, 1.0)
            for regime, prob in state.probability.items():
                votes[regime] += prob * weight

        # Normalize
        total = sum(votes.values())
        if total > 0:
            probs = {r: v / total for r, v in votes.items()}
        else:
            probs = {r: 1.0 / len(MarketRegime) for r in MarketRegime}

        # Get top regime
        final_regime = max(probs, key=probs.get)
        confidence = probs[final_regime]

        state = RegimeState(
            regime=final_regime,
            confidence=confidence,
            probability=probs,
            timestamp=datetime.now(),
        )

        self._history.append(state)
        return state


class AdaptivePositionSizer:
    """
    Adaptive Position Sizing based on Regime

    Features:
    - Kelly Criterion with regime-adjusted win rate
    - Volatility targeting
    - Regime-aware risk scaling
    - Correlation-adjusted sizing
    """

    def __init__(
        self,
        target_vol: float = 0.15,  # 15% annual target vol
        max_leverage: float = 3.0,
        kelly_fraction: float = 0.5,  # Half-Kelly
        min_position: float = 0.01,
        max_position: float = 1.0,
        regime_scalers: dict[MarketRegime, float] = None,
    ):
        self.target_vol = target_vol
        self.max_leverage = max_leverage
        self.kelly_fraction = kelly_fraction
        self.min_position = min_position
        self.max_position = max_position

        # Regime-specific position multipliers
        self.regime_scalers = regime_scalers or {
            MarketRegime.BULL_TREND: 1.2,
            MarketRegime.BEAR_TREND: 0.8,
            MarketRegime.SIDEWAYS: 0.6,
            MarketRegime.HIGH_VOLATILITY: 0.5,
            MarketRegime.LOW_VOLATILITY: 1.3,
            MarketRegime.CRISIS: 0.3,
            MarketRegime.RECOVERY: 1.0,
            MarketRegime.UNKNOWN: 0.5,
        }

    def calculate_kelly(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Calculate Kelly fraction"""
        if avg_loss == 0:
            return 0
        b = avg_win / avg_loss
        p = win_rate
        q = 1 - p
        kelly = (b * p - q) / b if b > 0 else 0
        return max(0, min(kelly * self.kelly_fraction, 1))

    def size_position(
        self,
        signal_strength: float,  # 0-1
        current_vol: float,  # Annualized volatility
        regime: MarketRegime,
        win_rate: float = 0.55,
        avg_win: float = 0.02,
        avg_loss: float = 0.015,
        correlation_penalty: float = 1.0,
    ) -> Decimal:
        """
        Calculate position size

        Args:
            signal_strength: Strength of trading signal (0-1)
            current_vol: Current asset volatility (annualized)
            regime: Current market regime
            win_rate: Historical win rate
            avg_win: Average win size
            avg_loss: Average loss size
            correlation_penalty: Portfolio correlation adjustment (0-1)

        Returns:
            Position size as fraction of capital
        """
        # Volatility targeting
        vol_scalar = self.target_vol / max(current_vol, 0.01)
        vol_scalar = min(vol_scalar, self.max_leverage)

        # Kelly sizing
        kelly = self.calculate_kelly(win_rate, avg_win, avg_loss)

        # Regime adjustment
        regime_scalar = self.regime_scalers.get(regime, 1.0)

        # Combined
        position = signal_strength * vol_scalar * kelly * regime_scalar * correlation_penalty

        # Clamp
        position = max(self.min_position, min(position, self.max_position))

        return Decimal(str(position))

    def size_portfolio(
        self,
        signals: dict[str, float],  # symbol -> signal strength
        volatilities: dict[str, float],  # symbol -> vol
        regime: MarketRegime,
        correlation_matrix: np.ndarray | None = None,
    ) -> dict[str, Decimal]:
        """Size multiple positions with correlation adjustment"""
        n = len(signals)
        if n == 0:
            return {}

        symbols = list(signals.keys())

        # Base sizes
        base_sizes = {}
        for sym in symbols:
            base_sizes[sym] = self.size_position(
                signal_strength=signals[sym],
                current_vol=volatilities.get(sym, 0.2),
                regime=regime,
            )

        # Correlation adjustment if matrix provided
        if correlation_matrix is not None:
            # Simple correlation penalty: reduce size if highly correlated
            for i, sym in enumerate(symbols):
                avg_corr = np.mean([abs(correlation_matrix[i, j]) for j in range(n) if i != j])
                penalty = 1 - avg_corr * 0.5  # Reduce up to 50%
                base_sizes[sym] *= Decimal(str(max(0.3, penalty)))

        # Normalize to max leverage
        total = sum(float(s) for s in base_sizes.values())
        if total > self.max_leverage:
            scale = self.max_leverage / total
            base_sizes = {k: v * Decimal(str(scale)) for k, v in base_sizes.items()}

        return base_sizes


class OnlineLearner:
    """
    Online Learning for Adaptive Parameters

    Uses River/Cremer-style online learning for:
    - Adaptive win rate estimation
    - Volatility forecasting
    - Regime transition probability
    """

    def __init__(self, learning_rate: float = 0.01):
        self.learning_rate = learning_rate
        self._win_rate = 0.5
        self._avg_win = 0.02
        self._avg_loss = 0.015
        self._vol_forecast = 0.2
        self._n_trades = 0

    def update_trade(self, pnl: float, entry_price: float, exit_price: float) -> None:
        """Update with trade result"""
        is_win = pnl > 0

        # Update win rate (exponential moving average)
        self._win_rate = (1 - self.learning_rate) * self._win_rate + self.learning_rate * (1 if is_win else 0)

        # Update avg win/loss
        if is_win:
            self._avg_win = (1 - self.learning_rate) * self._avg_win + self.learning_rate * abs(pnl / entry_price)
        else:
            self._avg_loss = (1 - self.learning_rate) * self._avg_loss + self.learning_rate * abs(pnl / entry_price)

        self._n_trades += 1

    def update_volatility(self, returns: pd.Series) -> None:
        """Update volatility forecast"""
        if len(returns) > 20:
            recent_vol = returns.iloc[-20:].std() * np.sqrt(252)
            self._vol_forecast = (1 - self.learning_rate) * self._vol_forecast + self.learning_rate * recent_vol

    def get_params(self) -> dict:
        return {
            'win_rate': self._win_rate,
            'avg_win': self._avg_win,
            'avg_loss': self._avg_loss,
            'vol_forecast': self._vol_forecast,
            'n_trades': self._n_trades,
        }


from collections import defaultdict
from typing import Any