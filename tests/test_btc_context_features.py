"""X1 synthetic contract for BTC context features. No market files."""

from datetime import UTC, datetime, timedelta
import math

from trading_agent.research.btc_context_features import (
    EVIDENCE_ROW_FIELDS,
    FEATURE_NAMES,
    VARIANCE_EPSILON,
    context_at,
    contract_fingerprint,
    evidence_row,
)


def clock(count):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    stamps = []
    cursor = start
    for _ in range(count):
        stamps.append(cursor)
        cursor += timedelta(hours=1)
    return tuple(stamps)


def closes_from(returns, origin=100.0):
    values = [origin]
    for item in returns:
        values.append(values[-1] * (1.0 + item))
    return tuple(values)


def by_name(values):
    return {item.name: item for item in values}


def reference_beta(btc, target):
    count = len(btc)
    btc_mean = sum(btc) / count
    target_mean = sum(target) / count
    variance = sum((item - btc_mean) ** 2 for item in btc) / (count - 1)
    covariance = sum(
        (left - btc_mean) * (right - target_mean)
        for left, right in zip(btc, target)
    ) / (count - 1)
    return covariance / variance


def test_catalog_is_the_six_value_baseline():
    assert FEATURE_NAMES == (
        "btc_log_return_1",
        "btc_simple_return_24",
        "btc_realized_vol_24",
        "beta_168_ex_current",
        "corr_168_ex_current",
        "residual_sum_24",
    )
    first = contract_fingerprint()
    assert len(first) == 64
    assert first == contract_fingerprint()


def test_short_history_is_missing_not_zero():
    closes = (100.0, 101.0)
    values = by_name(context_at(1, closes, closes, clock(2)))
    assert values["btc_log_return_1"].value == math.log(1.01)
    for name in FEATURE_NAMES[1:]:
        assert values[name].value is None
        assert values[name].unavailable_reason == "short_history"


def test_beta_excludes_the_current_return_and_matches_hand_formula():
    history = [0.01 if index % 2 == 0 else -0.004 for index in range(168)]
    current = 0.05
    btc_returns = history + [current]
    target_returns = [2.0 * item for item in history] + [0.0]
    btc = closes_from(btc_returns)
    target = closes_from(target_returns)
    index = len(btc) - 1
    values = by_name(context_at(index, btc, target, clock(len(btc))))
    expected = reference_beta(tuple(history), tuple(2.0 * item for item in history))
    assert math.isclose(values["beta_168_ex_current"].value, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(values["corr_168_ex_current"].value, 1.0, rel_tol=1e-12, abs_tol=1e-12)
    changed_target = closes_from([2.0 * item for item in history] + [9.0])
    changed = by_name(context_at(index, btc, changed_target, clock(len(btc))))
    assert changed["beta_168_ex_current"].value == values["beta_168_ex_current"].value


def test_residual_does_not_subtract_alpha_and_can_be_positive_while_price_falls():
    flat = [0.01 if index % 2 == 0 else -0.004 for index in range(191)]
    btc_returns = flat + [-0.05]
    target_returns = [item + 0.001 for item in flat] + [-0.02]
    btc = closes_from(btc_returns)
    target = closes_from(target_returns)
    index = len(btc) - 1
    values = by_name(context_at(index, btc, target, clock(len(btc))))
    expected = 0.0
    for cursor in range(index - 23, index + 1):
        window = tuple(btc_returns[cursor - 169 : cursor - 1])
        paired = tuple(target_returns[cursor - 169 : cursor - 1])
        beta = reference_beta(window, paired)
        expected += target_returns[cursor - 1] - beta * btc_returns[cursor - 1]
    assert math.isclose(values["residual_sum_24"].value, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert values["residual_sum_24"].value > 0.0
    assert target[index] < target[index - 1]


def test_zero_btc_variance_is_unavailable_not_a_zero_beta():
    btc = closes_from([0.01] * 180)
    target = closes_from([0.02 if index % 2 == 0 else -0.01 for index in range(180)])
    values = by_name(context_at(len(btc) - 1, btc, target, clock(len(btc))))
    assert values["beta_168_ex_current"].value is None
    assert values["beta_168_ex_current"].unavailable_reason == "zero_variance"
    assert values["btc_log_return_1"].value == math.log(1.01)


def test_constant_target_has_zero_beta_and_undefined_correlation():
    btc = closes_from([0.01 if index % 2 == 0 else -0.004 for index in range(180)])
    target = closes_from([0.0] * 180)
    values = by_name(context_at(len(btc) - 1, btc, target, clock(len(btc))))
    assert values["beta_168_ex_current"].value == 0.0
    assert values["corr_168_ex_current"].value is None
    assert values["corr_168_ex_current"].unavailable_reason == "zero_variance"


def test_gap_blocks_the_feature_that_touches_it():
    btc = closes_from([0.001] * 180)
    stamps = list(clock(len(btc)))
    stamps[100] = stamps[99] + timedelta(hours=3)
    for index in range(101, len(stamps)):
        stamps[index] = stamps[index - 1] + timedelta(hours=1)
    values = by_name(context_at(len(btc) - 1, btc, btc, tuple(stamps)))
    assert values["beta_168_ex_current"].value is None
    assert values["beta_168_ex_current"].unavailable_reason == "gap"
    assert values["btc_log_return_1"].value == math.log(1.001)


def test_future_bar_does_not_change_the_present():
    returns = [0.002 if index % 2 == 0 else -0.001 for index in range(200)]
    btc = closes_from(returns)
    past = by_name(context_at(180, btc, btc, clock(len(btc))))
    mutated = list(btc)
    mutated[-1] = mutated[-1] * 3.0
    again = by_name(context_at(180, mutated, mutated, clock(len(btc))))
    for name in FEATURE_NAMES:
        assert past[name].value == again[name].value


def test_nonfinite_price_is_unavailable():
    btc = list(closes_from([0.001] * 30))
    btc[20] = math.nan
    values = by_name(context_at(25, btc, btc, clock(len(btc))))
    assert values["btc_realized_vol_24"].value is None
    assert values["btc_realized_vol_24"].unavailable_reason == "nonfinite"


def test_realized_vol_is_sample_std_of_24_simple_returns():
    returns = [0.01] * 23 + [0.03]
    btc = closes_from(returns)
    center = sum(returns) / 24
    expected = math.sqrt(sum((item - center) ** 2 for item in returns) / 23)
    values = by_name(context_at(24, btc, btc, clock(len(btc))))
    assert math.isclose(values["btc_realized_vol_24"].value, expected, rel_tol=1e-12, abs_tol=1e-12)
    assert values["btc_simple_return_24"].value == btc[24] / btc[0] - 1.0


def test_evidence_row_records_availability_without_scoring():
    closes = (100.0, 101.0)
    feature = by_name(context_at(0, closes, closes, clock(2)))["btc_log_return_1"]
    row = evidence_row(arm="A1", symbol="ETH/USDT", bar_index=0, feature=feature)
    assert tuple(row) == EVIDENCE_ROW_FIELDS
    assert row["available"] is False
    assert row["value"] is None
    assert VARIANCE_EPSILON == 1e-18
