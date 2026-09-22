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

import hashlib
import json
import logging
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping, Optional

import numpy as np
import pandas as pd
from hmmlearn import hmm
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from scipy.special import logsumexp

warnings.filterwarnings("ignore", category=DeprecationWarning)

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


@dataclass(frozen=True)
class RegimePosterior:
    """Canonical soft regime probabilities used by strategy mixtures."""

    p_trend: float
    p_mean_reversion: float
    p_high_vol: float
    p_crisis: float
    p_other: float
    model_id: str = "unknown"
    fitted_start: datetime | None = None
    fitted_end: datetime | None = None
    generated_at: datetime | None = None
    ood_score: float = 1.0
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        values = self.values
        if any(not np.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("regime probabilities must be finite and non-negative")
        if not np.isclose(sum(values), 1.0, atol=1e-9):
            raise ValueError("regime probabilities must sum to one")
        if not np.isfinite(self.ood_score) or not 0.0 <= self.ood_score <= 1.0:
            raise ValueError("ood_score must be finite and in [0, 1]")
        for timestamp in (self.fitted_start, self.fitted_end, self.generated_at):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("regime posterior timestamps must be timezone-aware")
        if (
            self.fitted_start is not None
            and self.fitted_end is not None
            and self.fitted_end <= self.fitted_start
        ):
            raise ValueError("fitted_end must be after fitted_start")
        payload = {
            "probabilities": self.as_mapping,
            "model_id": self.model_id,
            "fitted_start": self.fitted_start.isoformat()
            if self.fitted_start
            else None,
            "fitted_end": self.fitted_end.isoformat() if self.fitted_end else None,
            "generated_at": self.generated_at.isoformat()
            if self.generated_at
            else None,
            "ood_score": self.ood_score,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        object.__setattr__(self, "fingerprint", hashlib.sha256(encoded).hexdigest())

    @property
    def values(self) -> tuple[float, float, float, float, float]:
        return (
            self.p_trend,
            self.p_mean_reversion,
            self.p_high_vol,
            self.p_crisis,
            self.p_other,
        )

    @property
    def as_mapping(self) -> dict[str, float]:
        return {
            "trend": self.p_trend,
            "mean_reversion": self.p_mean_reversion,
            "high_vol": self.p_high_vol,
            "crisis": self.p_crisis,
            "other": self.p_other,
        }

    def is_fresh(
        self, *, now: datetime | None = None, max_age_seconds: int = 7200
    ) -> bool:
        now = now or datetime.now(UTC)
        if self.generated_at is None or max_age_seconds <= 0:
            return False
        age = (now - self.generated_at).total_seconds()
        return 0.0 <= age <= max_age_seconds

    def is_production_ready(
        self,
        *,
        now: datetime | None = None,
        max_age_seconds: int = 7200,
        max_ood_score: float = 0.5,
    ) -> bool:
        if self.model_id in {"", "unknown"}:
            return False
        if self.fitted_start is None or self.fitted_end is None:
            return False
        if self.generated_at is None or self.fitted_end > self.generated_at:
            return False
        if self.ood_score > max_ood_score:
            return False
        return self.is_fresh(now=now, max_age_seconds=max_age_seconds)

    @property
    def entropy(self) -> float:
        probabilities: np.ndarray = np.asarray(self.values, dtype=float)
        positive = probabilities[probabilities > 0.0]
        return float(-np.sum(positive * np.log(positive)))

    @property
    def normalized_entropy(self) -> float:
        return float(self.entropy / np.log(len(self.values)))

    @property
    def conviction_multiplier(self) -> float:
        """Entropy-only shrinkage: more uncertainty can never raise exposure."""

        return float(np.clip(1.0 - self.normalized_entropy, 0.0, 1.0))


@dataclass(frozen=True)
class RegimeMixtureForecast:
    forecast: float
    raw_forecast: float
    entropy: float
    normalized_entropy: float
    exposure_multiplier: float
    abstained: bool
    reason: str | None = None


def regime_posterior_from_state(state: RegimeState) -> RegimePosterior:
    """Collapse detector-specific labels into a normalized five-state posterior."""

    buckets = {
        "trend": 0.0,
        "mean_reversion": 0.0,
        "high_vol": 0.0,
        "crisis": 0.0,
        "other": 0.0,
    }
    mapping = {
        MarketRegime.BULL_TREND: "trend",
        MarketRegime.BEAR_TREND: "trend",
        MarketRegime.SIDEWAYS: "mean_reversion",
        MarketRegime.LOW_VOLATILITY: "mean_reversion",
        MarketRegime.HIGH_VOLATILITY: "high_vol",
        MarketRegime.CRISIS: "crisis",
        MarketRegime.RECOVERY: "other",
        MarketRegime.UNKNOWN: "other",
    }
    for label, probability in state.probability.items():
        try:
            regime = label if isinstance(label, MarketRegime) else MarketRegime(label)
        except ValueError:
            buckets["other"] += max(0.0, float(probability))
            continue
        buckets[mapping[regime]] += max(0.0, float(probability))

    total = sum(buckets.values())
    if total <= 0.0 or (
        state.regime == MarketRegime.UNKNOWN and float(state.confidence) < 0.5
    ):
        buckets = {name: 0.2 for name in buckets}
        total = 1.0
    elif total < 1.0:
        buckets["other"] += 1.0 - total
        total = 1.0
    normalized = {name: value / total for name, value in buckets.items()}
    return RegimePosterior(
        p_trend=normalized["trend"],
        p_mean_reversion=normalized["mean_reversion"],
        p_high_vol=normalized["high_vol"],
        p_crisis=normalized["crisis"],
        p_other=normalized["other"],
    )


def mix_regime_forecasts(
    posterior: RegimePosterior,
    expert_forecasts: Mapping[str, float],
    *,
    max_exposure: float = 1.0,
    abstain_entropy: float = 0.95,
) -> RegimeMixtureForecast:
    """Soft mixture with entropy shrinkage outside the normalization denominator."""

    weights = {
        "trend": posterior.p_trend,
        "mean_reversion": posterior.p_mean_reversion,
        "high_vol": posterior.p_high_vol,
        "crisis": posterior.p_crisis,
        "other": posterior.p_other,
    }
    raw = float(
        sum(weights[name] * float(expert_forecasts.get(name, 0.0)) for name in weights)
    )
    exposure_cap = max(0.0, float(max_exposure))
    multiplier = posterior.conviction_multiplier
    abstained = posterior.normalized_entropy >= float(abstain_entropy)
    forecast = 0.0 if abstained else raw * multiplier * exposure_cap
    return RegimeMixtureForecast(
        forecast=float(np.clip(forecast, -exposure_cap, exposure_cap)),
        raw_forecast=raw,
        entropy=posterior.entropy,
        normalized_entropy=posterior.normalized_entropy,
        exposure_multiplier=multiplier,
        abstained=abstained,
        reason="REGIME_ENTROPY_HIGH" if abstained else None,
    )


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
        covariance_type: str = "full",
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
        self._fitted = True
        self._history: list[RegimeState] = []
        self._scaler: Optional[StandardScaler] = None  # Fit on training only
        self._predict_cache: np.ndarray | None = None  # Cached FILTERED posteriors
        self._predict_cache_len: int | None = None  # Cache identity: input length

    def _prepare_features(
        self, prices: pd.Series, volume: pd.Series | None = None
    ) -> np.ndarray:
        """Prepare raw observation features for HMM (unscaled).

        PIT-safe: rolling volatility uses backward-looking windows only.
        NaN values from the warmup period are forward-filled (causal: only
        uses past values) then zero-filled for the initial NaN. No bfill()
        which would backfill with future observations.
        """
        # Log returns
        returns = np.log(prices / prices.shift(1)).dropna()

        # Rolling volatility (20-day) — backward-looking. Use causal fill
        # (ffill + 0.0 for initial NaN) instead of bfill which leaks future data.
        vol = returns.rolling(20).std() * np.sqrt(252)
        vol = vol.ffill().fillna(0.0)

        # Volume features
        if volume is not None:
            # Align volume with returns
            vol_aligned = volume.reindex(returns.index).ffill()
            vol_change = vol_aligned.pct_change().ffill().fillna(0.0)
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

        # Clip inf/nan to prevent sklearn errors (NaN already handled above)
        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
        return features

    def _scale_features(self, features: np.ndarray) -> np.ndarray:
        """Scale features using the scaler fit during training.

        Uses the stored ``self._scaler`` (fit on training data only).
        In training (``fit``) the scaler is fit AND transformed; in
        prediction the stored scaler transforms — no refit on test data,
        preventing transductive leakage.
        """
        if self._scaler is None:
            raise ValueError("Scaler not fitted. Call fit() first.")
        return self._scaler.transform(features)

    def _compute_filtered_posteriors(self, features: np.ndarray) -> np.ndarray:
        """Compute **causal** (filtered) posteriors via the forward algorithm.

        Unlike ``hmmlearn``'s ``score_samples`` which uses the
        forward-backward algorithm (smoothing — posterior at time *t*
        depends on observations *t+1...T*, i.e. look-ahead leakage),
        this computes P(q_t = j | o_0...o_t) — using only observations
        known at or before time *t*.

        Complexity: O(n × k²) where k = n_components.
        """
        if self.model is None:
            raise ValueError("Model not fitted")

        n_samples, _ = features.shape
        n_components = self.model.n_components

        # Emission log-probabilities: log P(o_t | q_t = j)
        emis = self.model._compute_log_likelihood(features)  # (n, k)

        # Forward pass (log-scale).  Add small epsilon to avoid log(0)=-inf
        # when some states are unreachable (startprob_/transmat_ have zeros).
        eps = 1e-300
        log_alpha = np.full((n_samples, n_components), -np.inf)
        log_alpha[0] = np.log(np.maximum(self.model.startprob_, eps)) + emis[0]
        log_transmat = np.log(np.maximum(self.model.transmat_, eps))
        for t in range(1, n_samples):
            for j in range(n_components):
                log_alpha[t, j] = (
                    logsumexp(log_alpha[t - 1] + log_transmat[:, j]) + emis[t, j]
                )

        # Normalize each row → filtered posteriors
        log_norm = logsumexp(log_alpha, axis=1, keepdims=True)
        filtered = np.exp(log_alpha - log_norm)
        return filtered

    def fit(self, prices: pd.Series, volume: pd.Series | None = None) -> "HMMStrategy":
        """Fit HMM to historical data.

        The StandardScaler is fit on the training data and stored, so
        that prediction uses the *same* scaler — no transductive leakage.
        """
        features = self._prepare_features(prices, volume)

        # Fit scaler on training data ONLY, then transform for training
        self._scaler = StandardScaler()
        features = self._scaler.fit_transform(features)

        self.model = hmm.GaussianHMM(
            n_components=self.n_regimes,
            covariance_type=self.covariance_type,
            n_iter=self.n_iter,
            random_state=self.random_state,
        )

        self.model.fit(features)
        self._fitted = True
        self._predict_cache = None  # Clear cache after refit

        # Assign regime names based on characteristics
        self._assign_regime_names(features)
        return self

    def _assign_regime_names(self, features: np.ndarray) -> None:
        """Assign meaningful names to regimes based on characteristics"""
        # Get regime means
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")
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

    def predict(
        self, prices: pd.Series, volume: pd.Series | None = None
    ) -> RegimeState:
        """Predict current regime"""
        if not self._fitted:
            raise ValueError("Model not fitted. Call fit() first.")
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")

        features = self._prepare_features(prices, volume)
        features = self._scale_features(features)  # reuse training scaler
        if len(features) == 0:
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC))

        # Causal forward-filter posteriors — NO look-ahead.
        # (Replaces score_samples which used forward-backward smoothing.)
        posteriors = self._compute_filtered_posteriors(features)
        current_probs = posteriors[-1]
        self._predict_cache = posteriors  # Cache for batch access

        # Most likely regime
        regime_idx = np.argmax(current_probs)
        regime = self._regime_names[regime_idx]
        confidence = float(current_probs[regime_idx])

        prob_dict: dict[MarketRegime, float] = {}
        for index, probability in enumerate(current_probs):
            label = self._regime_names[index]
            prob_dict[label] = prob_dict.get(label, 0.0) + float(probability)

        # Expected duration (from transition matrix)
        transmat = self.model.transmat_
        expected_dur = (
            int(1 / (1 - transmat[regime_idx, regime_idx]))
            if transmat[regime_idx, regime_idx] < 1
            else None
        )

        state = RegimeState(
            regime=regime,
            confidence=confidence,
            probability=prob_dict,
            timestamp=datetime.now(UTC),
            expected_duration=expected_dur,
        )

        self._history.append(state)
        return state

    def predict_all(
        self, prices: pd.Series, volume: pd.Series | None = None
    ) -> list[RegimeState]:
        """Predict regime for ALL bars at once — O(n) instead of O(n) × O(n).

        Uses causal forward-filter posteriors (not forward-backward smoothing),
        so each bar's regime state depends only on observations up to that bar.
        """
        if not self._fitted:
            raise ValueError("Model not fitted. Call fit() first.")
        if self.model is None:
            raise ValueError("Model not fitted. Call fit() first.")

        features = self._prepare_features(prices, volume)
        features = self._scale_features(features)  # reuse training scaler
        if len(features) == 0:
            return []

        # Cache validation: invalidate when input length changes to prevent
        # stale cache reuse across different prediction windows.
        if self._predict_cache is not None and self._predict_cache_len == len(features):
            posteriors = self._predict_cache
        else:
            # Causal forward-filter posteriors — NO look-ahead.
            posteriors = self._compute_filtered_posteriors(features)
            self._predict_cache = posteriors
            self._predict_cache_len = len(features)

        transmat = self.model.transmat_
        states: list[RegimeState] = []
        for i in range(len(posteriors)):
            current_probs = posteriors[i]
            regime_idx = np.argmax(current_probs)
            regime = self._regime_names[regime_idx]
            confidence = float(current_probs[regime_idx])
            prob_dict: dict[MarketRegime, float] = {}
            for idx, probability in enumerate(current_probs):
                label = self._regime_names[idx]
                prob_dict[label] = prob_dict.get(label, 0.0) + float(probability)
            expected_dur = (
                int(1 / (1 - transmat[regime_idx, regime_idx]))
                if transmat[regime_idx, regime_idx] < 1
                else None
            )
            states.append(RegimeState(
                regime=regime, confidence=confidence,
                probability=prob_dict, timestamp=datetime.now(UTC),
                expected_duration=expected_dur,
            ))
        return states

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
        covariance_type: str = "full",
        random_state: int = 42,
    ):
        self.n_regimes = n_regimes
        self.covariance_type = covariance_type
        self.random_state = random_state
        self.model: Optional[GaussianMixture] = None
        self._regime_names: list[MarketRegime] = []
        self._fitted = False
        self._scaler = None  # Fit on training data only

    def _prepare_features(self, returns: pd.Series) -> np.ndarray:
        """Prepare raw features: returns, rolling vol, skew, kurtosis.

        PIT-safe: uses ``dropna()`` (not ``bfill``), so only bars with a
        full rolling window are included. Returns unscaled features;
        scaling is performed by ``_scale_features`` using the scaler
        fitted during ``fit()``.
        """
        # Rolling statistics — backward-looking, dropna handles warmup
        roll_vol = returns.rolling(20).std() * np.sqrt(252)
        roll_skew = returns.rolling(60).skew()
        roll_kurt = returns.rolling(60).kurt()

        # Combine
        df = pd.DataFrame(
            {
                "return": returns,
                "vol": roll_vol,
                "skew": roll_skew,
                "kurt": roll_kurt,
            }
        ).dropna()

        # Clip inf/nan for numerical safety
        features = np.nan_to_num(df.to_numpy(), nan=0.0, posinf=0.0, neginf=0.0)
        return features

    def _scale_features(self, features: np.ndarray) -> np.ndarray:
        """Scale features using the scaler fit during training only.

        Uses the stored ``self._scaler``. In training (``fit``) the scaler
        is fit AND transformed; in prediction it transforms — no refit on
        test data, preventing transductive leakage.
        """
        if self._scaler is None:
            raise ValueError("Scaler not fitted. Call fit() first.")
        return self._scaler.transform(features)

    def fit(self, returns: pd.Series) -> "GMMStrategy":
        """Fit GMM to returns. Fits scaler on training data only."""
        features = self._prepare_features(returns)

        # Fit scaler on training data ONLY, then transform for training
        self._scaler = StandardScaler()
        features = self._scaler.fit_transform(features)

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
        if self.model is None:
            raise ValueError("Model not fitted")
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
        if self.model is None:
            raise ValueError("Model not fitted")

        features = self._prepare_features(returns)
        features = self._scale_features(features)  # reuse training scaler
        if len(features) == 0:
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC))

        probs = self.model.predict_proba(features[-1:].reshape(1, -1))[0]
        regime_idx = np.argmax(probs)
        regime = self._regime_names[regime_idx]
        confidence = float(probs[regime_idx])

        prob_dict: dict[MarketRegime, float] = {}
        for index, probability in enumerate(probs):
            label = self._regime_names[index]
            prob_dict[label] = prob_dict.get(label, 0.0) + float(probability)

        return RegimeState(
            regime=regime,
            confidence=confidence,
            probability=prob_dict,
            timestamp=datetime.now(UTC),
        )

    def predict_all(self, returns: pd.Series) -> list[RegimeState]:
        """Predict regime for ALL bars at once — O(n) instead of O(n) × O(n)."""
        if not self._fitted:
            raise ValueError("Model not fitted")
        if self.model is None:
            raise ValueError("Model not fitted")

        features = self._prepare_features(returns)
        features = self._scale_features(features)  # reuse training scaler
        if len(features) == 0:
            return []

        all_probs = self.model.predict_proba(features)
        states: list[RegimeState] = []
        for i in range(len(all_probs)):
            probs = all_probs[i]
            regime_idx = np.argmax(probs)
            regime = self._regime_names[regime_idx]
            confidence = float(probs[regime_idx])
            prob_dict: dict[MarketRegime, float] = {}
            for index, probability in enumerate(probs):
                label = self._regime_names[index]
                prob_dict[label] = prob_dict.get(label, 0.0) + float(probability)
            states.append(RegimeState(
                regime=regime, confidence=confidence,
                probability=prob_dict, timestamp=datetime.now(UTC),
            ))
        return states


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
            return RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC))

        # Moving averages
        fast = prices.rolling(self.fast_ma).mean().iloc[-1]
        slow = prices.rolling(self.slow_ma).mean().iloc[-1]

        # Returns
        returns = np.log(prices / prices.shift(1))
        vol = returns.rolling(self.vol_window).std().fillna(0.0).iloc[-1] * np.sqrt(252)

        # Momentum
        momentum = (
            (prices.iloc[-1] / prices.iloc[-self.fast_ma] - 1)
            if len(prices) >= self.fast_ma
            else 0
        )

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
            timestamp=datetime.now(UTC),
            features={
                "fast_ma": float(fast),
                "slow_ma": float(slow),
                "vol": float(vol),
                "momentum": float(momentum),
            },
        )


    def detect_all(self, prices: pd.Series) -> list[RegimeState]:
        """Detect regime for ALL bars at once — O(n) instead of O(n) × O(n).

        Computes rolling indicators once on the full series and returns
        a RegimeState per bar.
        """
        n = len(prices)
        if n < self.slow_ma:
            return [RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC)) for _ in range(n)]

        # Compute rolling indicators once
        fast_ma_series = prices.rolling(self.fast_ma).mean()
        slow_ma_series = prices.rolling(self.slow_ma).mean()
        # Compute returns WITHOUT dropna -- keep full index aligned with prices.
        # rolling().std() on the first vol_window bars yields NaN which we
        # fill with 0.0 (causal: no future leakage).
        returns = np.log(prices / prices.shift(1))
        vol_series = returns.rolling(self.vol_window).std() * np.sqrt(252)
        vol_series = vol_series.fillna(0.0)  # causal warmup fill, index-aligned  # causal: no future leakage

        states: list[RegimeState] = []
        for i in range(n):
            if i < self.slow_ma - 1:
                states.append(RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC)))
                continue

            fast = fast_ma_series.iloc[i] if i < len(fast_ma_series) else np.nan
            slow = slow_ma_series.iloc[i] if i < len(slow_ma_series) else np.nan
            vol = vol_series.iloc[i] if i < len(vol_series) else np.nan

            if pd.isna(fast) or pd.isna(slow) or pd.isna(vol):
                states.append(RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC)))
                continue

            momentum = (prices.iloc[i] / prices.iloc[max(0, i - self.fast_ma)] - 1) if i >= self.fast_ma else 0

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

            states.append(RegimeState(
                regime=regime, confidence=probs[regime], probability=probs,
                timestamp=datetime.now(UTC),
            ))

        return states


class HybridRegimeDetector:
    """
    Hybrid regime detector combining multiple methods
    """

    def __init__(
        self,
        methods: list[RegimeMethod] | None = None,
        weights: dict[RegimeMethod, float] | None = None,
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

    def detect(
        self, prices: pd.Series, volume: pd.Series | None = None,
        training_cutoff: int | None = None,
    ) -> RegimeState:
        """Aggregate predictions from all methods.

        PIT-safe: when training_cutoff is provided, detectors are
        initialized (fit) on prices.iloc[:training_cutoff] only.
        """
        if not self._detectors:
            if training_cutoff is not None:
                self.initialize(
                    prices.iloc[:training_cutoff],
                    volume.iloc[:training_cutoff] if volume is not None else None,
                )
            else:
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
        final_regime = max(probs, key=lambda regime: probs[regime])
        confidence = probs[final_regime]

        state = RegimeState(
            regime=final_regime,
            confidence=confidence,
            probability=probs,
            timestamp=datetime.now(UTC),
        )

        self._history.append(state)
        return state

    def detect_all(
        self, prices: pd.Series, volume: pd.Series | None = None,
        training_cutoff: int | None = None,
    ) -> list[RegimeState]:
        """Aggregate predictions from all methods for ALL bars at once -- O(n).

        Each sub-detector computes all-bar predictions in one batch call,
        then results are aggregated per bar.

        PIT-safe: when training_cutoff is provided, all sub-detectors
        are initialized (fit) on prices.iloc[:training_cutoff] only.
        """
        if not self._detectors:
            if training_cutoff is not None:
                self.initialize(
                    prices.iloc[:training_cutoff],
                    volume.iloc[:training_cutoff] if volume is not None else None,
                )
            else:
                self.initialize(prices, volume)

        n = len(prices)
        votes_list: list[dict[MarketRegime, float]] = [
            defaultdict(float) for _ in range(n)
        ]

        for method, detector in self._detectors.items():
            if method == RegimeMethod.HMM:
                all_states = detector.predict_all(prices, volume)
            elif method == RegimeMethod.GMM:
                returns = np.log(prices / prices.shift(1)).dropna()
                all_states = detector.predict_all(returns)
                # GMM features have fewer bars due to dropna; pad
                pad = n - len(all_states)
                all_states = [RegimeState(MarketRegime.UNKNOWN, 0, {}, datetime.now(UTC))] * pad + all_states
            elif method == RegimeMethod.RULE_BASED:
                all_states = detector.detect_all(prices)
            else:
                continue

            weight = self.weights.get(method, 1.0)
            for i, state in enumerate(all_states[:n]):
                for regime, prob in state.probability.items():
                    votes_list[i][regime] += prob * weight

        states: list[RegimeState] = []
        for votes in votes_list:
            total = sum(votes.values())
            if total > 0:
                probs = {r: v / total for r, v in votes.items()}
            else:
                probs = {r: 1.0 / len(MarketRegime) for r in MarketRegime}

            final_regime = max(probs, key=lambda regime: probs[regime])
            confidence = probs[final_regime]
            states.append(RegimeState(
                regime=final_regime,
                confidence=confidence,
                probability=probs,
                timestamp=datetime.now(UTC),
            ))

        self._history.extend(states)
        return states


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
        regime_scalers: dict[MarketRegime, float] | None = None,
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

    def calculate_kelly(
        self, win_rate: float, avg_win: float, avg_loss: float
    ) -> float:
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
        position = (
            signal_strength * vol_scalar * kelly * regime_scalar * correlation_penalty
        )

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
                avg_corr = np.mean(
                    [abs(correlation_matrix[i, j]) for j in range(n) if i != j]
                )
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
        self._win_rate = (
            1 - self.learning_rate
        ) * self._win_rate + self.learning_rate * (1 if is_win else 0)

        # Update avg win/loss
        if is_win:
            self._avg_win = (
                1 - self.learning_rate
            ) * self._avg_win + self.learning_rate * abs(pnl / entry_price)
        else:
            self._avg_loss = (
                1 - self.learning_rate
            ) * self._avg_loss + self.learning_rate * abs(pnl / entry_price)

        self._n_trades += 1

    def update_volatility(self, returns: pd.Series) -> None:
        """Update volatility forecast"""
        if len(returns) > 20:
            recent_vol = returns.iloc[-20:].std() * np.sqrt(252)
            self._vol_forecast = (
                1 - self.learning_rate
            ) * self._vol_forecast + self.learning_rate * recent_vol

    def get_params(self) -> dict:
        return {
            "win_rate": self._win_rate,
            "avg_win": self._avg_win,
            "avg_loss": self._avg_loss,
            "vol_forecast": self._vol_forecast,
            "n_trades": self._n_trades,
        }


from collections import defaultdict
from typing import Any
