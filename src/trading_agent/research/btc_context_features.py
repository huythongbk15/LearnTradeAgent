"""X1 contract for BTC context features. Synthetic math only.

The six values are a baseline, not an edge and not a trading policy. A missing
value stays missing. Zero is returned only when the formula produces zero.
This module does not read market files and does not emit orders.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

CONTRACT_VERSION = "x1-20261027-01"
BAR = timedelta(hours=1)
BETA_WINDOW = 168
RESIDUAL_SUM_WINDOW = 24
RETURN_24_WINDOW = 24
VOL_WINDOW = 24
VARIANCE_EPSILON = 1e-18

FEATURE_NAMES: tuple[str, ...] = (
    "btc_log_return_1",
    "btc_simple_return_24",
    "btc_realized_vol_24",
    "beta_168_ex_current",
    "corr_168_ex_current",
    "residual_sum_24",
)

FEATURE_ROLES: dict[str, str] = {
    "btc_log_return_1": "direction_or_risk",
    "btc_simple_return_24": "direction_or_risk",
    "btc_realized_vol_24": "risk",
    "beta_168_ex_current": "risk",
    "corr_168_ex_current": "risk",
    "residual_sum_24": "conditional_direction_not_usdt_return",
}

EVIDENCE_ROW_FIELDS: tuple[str, ...] = (
    "contract_version",
    "contract_fingerprint",
    "arm",
    "symbol",
    "bar_index",
    "feature",
    "role",
    "available",
    "value",
    "unavailable_reason",
)


def contract_fingerprint() -> str:
    """Identity of the locked windows and estimator, not of any market file."""
    payload = {
        "version": CONTRACT_VERSION,
        "bar_seconds": int(BAR.total_seconds()),
        "beta_window": BETA_WINDOW,
        "beta_excludes_current_return": True,
        "beta_estimator": "ols_slope_with_intercept_ddof1",
        "residual": "simple_return_minus_beta_times_btc_without_alpha",
        "residual_sum_window": RESIDUAL_SUM_WINDOW,
        "return_24": "simple_close_ratio",
        "log_return_1": "natural_log",
        "realized_vol_24": "sample_std_ddof1_of_simple_returns",
        "variance_epsilon": VARIANCE_EPSILON,
        "unavailable": "null_not_zero_fill",
        "features": list(FEATURE_NAMES),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FeatureValue:
    """One feature at one closed bar. ``value`` is None when unavailable."""

    name: str
    value: float | None
    unavailable_reason: str | None

    @property
    def available(self) -> bool:
        return self.value is not None


def _finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def _simple_return(previous: float, current: float) -> float | None:
    if not _finite_positive(previous) or not _finite_positive(current):
        return None
    return current / previous - 1.0


def _step_regular(timestamps: tuple[datetime, ...], left: int, right: int) -> bool:
    for index in range(left + 1, right + 1):
        if timestamps[index] - timestamps[index - 1] != BAR:
            return False
    return True


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values)


def _sample_var(values: tuple[float, ...]) -> float:
    center = _mean(values)
    return sum((item - center) ** 2 for item in values) / (len(values) - 1)


def _sample_cov(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_center = _mean(left)
    right_center = _mean(right)
    paired = zip(left, right, strict=True)
    return sum((a - left_center) * (b - right_center) for a, b in paired) / (
        len(left) - 1
    )


def _ols_beta(btc: tuple[float, ...], target: tuple[float, ...]) -> float | None:
    """Slope of target on BTC. Intercept is estimated and then discarded."""
    variance = _sample_var(btc)
    if not math.isfinite(variance) or variance <= VARIANCE_EPSILON:
        return None
    beta = _sample_cov(btc, target) / variance
    if not math.isfinite(beta):
        return None
    return beta


def _corr(btc: tuple[float, ...], target: tuple[float, ...]) -> float | None:
    btc_var = _sample_var(btc)
    target_var = _sample_var(target)
    if (
        not math.isfinite(btc_var)
        or not math.isfinite(target_var)
        or btc_var <= VARIANCE_EPSILON
        or target_var <= VARIANCE_EPSILON
    ):
        return None
    correlation = _sample_cov(btc, target) / math.sqrt(btc_var * target_var)
    if not math.isfinite(correlation):
        return None
    return correlation


def _missing(name: str, reason: str) -> FeatureValue:
    return FeatureValue(name, None, reason)


def _present(name: str, value: float) -> FeatureValue:
    return FeatureValue(name, value, None)


def _return_pair(
    btc_closes: tuple[float, ...],
    target_closes: tuple[float, ...],
    timestamps: tuple[datetime, ...],
    index: int,
) -> tuple[float, float] | None:
    if index < 1 or not _step_regular(timestamps, index - 1, index):
        return None
    btc_return = _simple_return(btc_closes[index - 1], btc_closes[index])
    target_return = _simple_return(target_closes[index - 1], target_closes[index])
    if btc_return is None or target_return is None:
        return None
    return btc_return, target_return


def _history_before_current(
    btc_closes: tuple[float, ...],
    target_closes: tuple[float, ...],
    timestamps: tuple[datetime, ...],
    index: int,
) -> tuple[str | None, tuple[float, ...], tuple[float, ...]]:
    """168 returns strictly before the return at ``index``.

    The reason is None only when both return tuples are usable.
    """
    start = index - BETA_WINDOW
    if start < 1:
        return "short_history", (), ()
    if not _step_regular(timestamps, start - 1, index - 1):
        return "gap", (), ()
    btc_returns: list[float] = []
    target_returns: list[float] = []
    for cursor in range(start, index):
        pair = _return_pair(btc_closes, target_closes, timestamps, cursor)
        if pair is None:
            return "nonfinite", (), ()
        btc_returns.append(pair[0])
        target_returns.append(pair[1])
    return None, tuple(btc_returns), tuple(target_returns)


def context_at(
    index: int,
    btc_closes: tuple[float, ...] | list[float],
    target_closes: tuple[float, ...] | list[float],
    timestamps: tuple[datetime, ...] | list[datetime],
) -> tuple[FeatureValue, ...]:
    """Features known at closed bar ``index``. Later bars are not read."""
    btc = tuple(btc_closes)
    target = tuple(target_closes)
    clock = tuple(timestamps)
    if not (len(btc) == len(target) == len(clock)):
        raise ValueError("BTC, target and timestamps must have equal length")
    if index < 0 or index >= len(btc):
        raise ValueError("index is outside the closed series")
    btc = btc[: index + 1]
    target = target[: index + 1]
    clock = clock[: index + 1]

    values = [
        _log_return(btc, clock, index),
        _simple_return_24(btc, clock, index, name="btc_simple_return_24"),
        _realized_vol(btc, clock, index),
        _beta_feature(btc, target, clock, index),
        _corr_feature(btc, target, clock, index),
        _residual_sum(btc, target, clock, index),
    ]
    names = tuple(item.name for item in values)
    if names != FEATURE_NAMES:
        raise RuntimeError("feature catalog drifted")
    return tuple(values)


def _log_return(
    btc: tuple[float, ...], clock: tuple[datetime, ...], index: int
) -> FeatureValue:
    name = "btc_log_return_1"
    if index < 1:
        return _missing(name, "short_history")
    if not _step_regular(clock, index - 1, index):
        return _missing(name, "gap")
    if not _finite_positive(btc[index - 1]) or not _finite_positive(btc[index]):
        return _missing(name, "nonfinite")
    return _present(name, math.log(btc[index] / btc[index - 1]))


def _simple_return_24(
    closes: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
    *,
    name: str,
) -> FeatureValue:
    if index < RETURN_24_WINDOW:
        return _missing(name, "short_history")
    if not _step_regular(clock, index - RETURN_24_WINDOW, index):
        return _missing(name, "gap")
    value = _simple_return(closes[index - RETURN_24_WINDOW], closes[index])
    if value is None:
        return _missing(name, "nonfinite")
    return _present(name, value)


def own_simple_return_24(
    closes: tuple[float, ...] | list[float],
    timestamps: tuple[datetime, ...] | list[datetime],
    index: int,
) -> FeatureValue:
    """Pair-only 24-bar simple return. Later closes are not read."""
    series = tuple(closes)[: index + 1]
    clock = tuple(timestamps)[: index + 1]
    if len(series) != len(clock):
        raise ValueError("closes and timestamps must have equal length")
    if index < 0 or index >= len(tuple(closes)):
        raise ValueError("index is outside the closed series")
    return _simple_return_24(series, clock, index, name="own_simple_return_24")


def _realized_vol(
    btc: tuple[float, ...], clock: tuple[datetime, ...], index: int
) -> FeatureValue:
    name = "btc_realized_vol_24"
    if index < VOL_WINDOW:
        return _missing(name, "short_history")
    if not _step_regular(clock, index - VOL_WINDOW, index):
        return _missing(name, "gap")
    returns: list[float] = []
    for cursor in range(index - VOL_WINDOW + 1, index + 1):
        value = _simple_return(btc[cursor - 1], btc[cursor])
        if value is None:
            return _missing(name, "nonfinite")
        returns.append(value)
    return _present(name, math.sqrt(_sample_var(tuple(returns))))


def _relation_inputs(
    btc: tuple[float, ...],
    target: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
) -> tuple[str | None, tuple[float, ...], tuple[float, ...]]:
    if index < BETA_WINDOW + 1:
        return "short_history", (), ()
    return _history_before_current(btc, target, clock, index)


def _beta_feature(
    btc: tuple[float, ...],
    target: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
) -> FeatureValue:
    name = "beta_168_ex_current"
    reason, btc_returns, target_returns = _relation_inputs(btc, target, clock, index)
    if reason is not None:
        return _missing(name, reason)
    beta = _ols_beta(btc_returns, target_returns)
    if beta is None:
        return _missing(name, "zero_variance")
    return _present(name, beta)


def _corr_feature(
    btc: tuple[float, ...],
    target: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
) -> FeatureValue:
    name = "corr_168_ex_current"
    reason, btc_returns, target_returns = _relation_inputs(btc, target, clock, index)
    if reason is not None:
        return _missing(name, reason)
    correlation = _corr(btc_returns, target_returns)
    if correlation is None:
        return _missing(name, "zero_variance")
    return _present(name, correlation)


def _residual_at(
    btc: tuple[float, ...],
    target: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
) -> tuple[str | None, float | None]:
    pair = _return_pair(btc, target, clock, index)
    if pair is None:
        if index < 1:
            return "short_history", None
        if not _step_regular(clock, index - 1, index):
            return "gap", None
        return "nonfinite", None
    reason, btc_returns, target_returns = _relation_inputs(btc, target, clock, index)
    if reason is not None:
        return reason, None
    beta = _ols_beta(btc_returns, target_returns)
    if beta is None:
        return "zero_variance", None
    return None, pair[1] - beta * pair[0]


def _residual_sum(
    btc: tuple[float, ...],
    target: tuple[float, ...],
    clock: tuple[datetime, ...],
    index: int,
) -> FeatureValue:
    name = "residual_sum_24"
    if index < BETA_WINDOW + RESIDUAL_SUM_WINDOW:
        return _missing(name, "short_history")
    total = 0.0
    for cursor in range(index - RESIDUAL_SUM_WINDOW + 1, index + 1):
        reason, residual = _residual_at(btc, target, clock, cursor)
        if reason is not None or residual is None:
            return _missing(name, reason or "nonfinite")
        total += residual
    return _present(name, total)


def evidence_row(
    *,
    arm: str,
    symbol: str,
    bar_index: int,
    feature: FeatureValue,
) -> dict[str, object]:
    """Schema row for a later evidence table. It does not score a strategy."""
    if arm not in {"A0", "A1", "A2"}:
        raise ValueError("arm must be A0, A1 or A2")
    if feature.name not in FEATURE_ROLES:
        raise ValueError("unknown feature")
    return {
        "contract_version": CONTRACT_VERSION,
        "contract_fingerprint": contract_fingerprint(),
        "arm": arm,
        "symbol": symbol,
        "bar_index": bar_index,
        "feature": feature.name,
        "role": FEATURE_ROLES[feature.name],
        "available": feature.available,
        "value": feature.value,
        "unavailable_reason": feature.unavailable_reason,
    }
