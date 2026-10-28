"""Synthetic checks for the frozen own-24 entry. No market files."""

from datetime import UTC, datetime, timedelta

from trading_agent.research.btc_context_features import FeatureValue, own_simple_return_24
from trading_agent.research.btc_context_rule import (
    FILL_DELAY_BARS,
    HOLD_BARS,
    PLUS_ARM,
    PLUS_ENTRY_RULE_ID,
    entry_allowed,
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
    """PLUS adds only BTC return > 0; missing BTC return => HOLD."""
    btc_up = context(btc_simple_return_24=0.02)
    btc_down = context(btc_simple_return_24=-0.01)
    # BASE ignores BTC; PLUS flips with BTC only.
    assert entry_allowed("A0", own(0.01), btc_down) is True
    assert entry_allowed(PLUS_ARM, own(0.01), btc_down) is False
    assert entry_allowed("A0", own(0.01), btc_up) is True
    assert entry_allowed(PLUS_ARM, own(0.01), btc_up) is True
    # Missing BTC return => HOLD even when log1/vol are present.
    missing_btc = context(btc_simple_return_24=None)
    assert entry_allowed(PLUS_ARM, own(0.01), missing_btc) is False
    # Negative own return blocks both arms.
    assert entry_allowed(PLUS_ARM, own(-0.01), btc_up) is False


def test_plus_missing_btc_return_holds_without_log1_or_vol():
    """PLUS must not require btc_log_return_1 or btc_realized_vol_24."""
    btc_up_only = context(btc_log_return_1=None, btc_realized_vol_24=None, btc_simple_return_24=0.02)
    assert entry_allowed(PLUS_ARM, own(0.01), btc_up_only) is True
    btc_down_only = context(btc_log_return_1=0.01, btc_realized_vol_24=0.01, btc_simple_return_24=-0.01)
    assert entry_allowed(PLUS_ARM, own(0.01), btc_down_only) is False


def test_plus_predicate_uses_distinct_identity_and_fingerprint():
    """PLUS is a distinct rule from A0/A1/A2, bound by its own fingerprint."""
    assert PLUS_ENTRY_RULE_ID != "H-OWN24-CONTEXT-01"
    assert PLUS_ARM not in ("A0", "A1", "A2")
    assert rule_fingerprint_plus() != rule_fingerprint()
    assert PLUS_ENTRY_RULE_ID == "H-OWN24-PLUS-BTCRETURN-01"
