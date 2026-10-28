"""Synthetic checks for the frozen own-24 entry. No market files."""

from datetime import UTC, datetime, timedelta

from trading_agent.research.btc_context_features import FeatureValue, own_simple_return_24
from trading_agent.research.btc_context_rule import (
    ENTRY_RULE_ID,
    FILL_DELAY_BARS,
    HOLD_BARS,
    PLUS_ARM,
    PLUS_ENTRY_RULE_ID,
    entry_allowed,
    entry_allowed_plus,
    rule_fingerprint,
    rule_fingerprint_plus,
)


def feature(name, value):
    reason = None if value is not None else "missing"
    return FeatureValue(name, value, reason)


def context(**values):
    names = {
        "btc_log_return_1": 0.01,
        "btc_simple_return_24": 0.02,
        "btc_realized_vol_24": 0.01,
        "beta_168_ex_current": 1.0,
        "corr_168_ex_current": 0.5,
        "residual_sum_24": 0.01,
    }
    names.update(values)
    return {name: feature(name, value) for name, value in names.items()}


def own(value):
    return feature("own_simple_return_24", value)


def test_a0_follows_only_the_pair_return():
    rising = context(btc_simple_return_24=-0.05, residual_sum_24=-0.05)
    assert entry_allowed("A0", own(0.01), rising) is True
    assert entry_allowed("A0", own(0.0), rising) is False
    assert entry_allowed("A0", own(-0.01), rising) is False
    assert entry_allowed("A0", own(None)) is False


def test_context_gates_do_not_buy_a_falling_usdt_price():
    favorable = context()
    assert entry_allowed("A1", own(-0.01), favorable) is False
    assert entry_allowed("A2", own(-0.01), favorable) is False


def test_each_arm_adds_one_predicate():
    both_up = context()
    btc_down = context(btc_simple_return_24=-0.01)
    weak_residual = context(residual_sum_24=-0.01)
    assert entry_allowed("A0", own(0.01), btc_down) is True
    assert entry_allowed("A1", own(0.01), btc_down) is False
    assert entry_allowed("A2", own(0.01), btc_down) is False
    assert entry_allowed("A1", own(0.01), weak_residual) is True
    assert entry_allowed("A2", own(0.01), weak_residual) is False
    assert entry_allowed("A2", own(0.01), both_up) is True


def test_missing_context_blocks_the_arm_that_needs_it():
    missing_vol = context(btc_realized_vol_24=None)
    missing_beta = context(beta_168_ex_current=None)
    assert entry_allowed("A0", own(0.01), missing_vol) is True
    assert entry_allowed("A1", own(0.01), missing_vol) is False
    assert entry_allowed("A1", own(0.01), missing_beta) is True
    assert entry_allowed("A2", own(0.01), missing_beta) is False


def test_own_return_ignores_a_future_close():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    stamps = tuple(start + timedelta(hours=i) for i in range(30))
    closes = tuple(100.0 + i for i in range(30))
    past = own_simple_return_24(closes, stamps, 24)
    mutated = list(closes)
    mutated[-1] = 1.0
    assert own_simple_return_24(mutated, stamps, 24).value == past.value
    assert past.value is not None and past.value > 0.0


def test_hold_matches_the_signal_horizon_and_keeps_native_delay():
    assert HOLD_BARS == 24
    assert FILL_DELAY_BARS == 2


def test_plus_predicate_btc_return_only_changes_decision():
    """Same pair data: flip BTC return +/- must flip PLUS BUY/HOLD, BASE unchanged."""
    own_up = own(0.01)
    own_down = own(-0.01)
    btc_up = context(btc_simple_return_24=0.02)  # log1/vol may be present
    btc_down = context(btc_simple_return_24=-0.02)

    # BASE: pair-only, BTC ignored
    assert entry_allowed("A0", own_up, btc_down) is True
    assert entry_allowed("A0", own_up, btc_up) is True

    # PLUS: own > 0 AND btc_simple_return_24 > 0
    assert entry_allowed_plus(own_up, btc_up) is True
    assert entry_allowed_plus(own_up, btc_down) is False  # BTC down => HOLD
    assert entry_allowed_plus(own_down, btc_up) is False  # own down => HOLD


def test_plus_missing_btc_return_holds_without_log1_or_vol():
    """Missing btc_simple_return_24 => HOLD; no log1/vol requirement."""
    own_up = own(0.01)
    missing_return = context(btc_simple_return_24=None)
    assert entry_allowed_plus(own_up, missing_return) is False


def test_plus_predicate_uses_distinct_identity_and_fingerprint():
    assert PLUS_ENTRY_RULE_ID != ENTRY_RULE_ID
    assert PLUS_ARM != "A1"
    fp = rule_fingerprint_plus()
    assert len(fp) == 64
    # Predicate content must be reflected in the fingerprint.
    assert rule_fingerprint_plus() != rule_fingerprint()
