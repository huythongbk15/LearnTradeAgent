"""Synthetic-only contract tests; no data or broker reads."""
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.execution.position_cycle import PositionCycleView
from trading_agent.strategies.own24_thinned_fill import (
    Own24ThinnedFillStrategy, accepts_entry, canonical_timestamp,
)

STAMP = datetime(2024, 3, 23, 14, tzinfo=UTC)


def test_exact_hash_boundary_vector():
    h = 3428679087176717547
    assert not accepts_entry(42, "ETH/USDT", STAMP, h, 1 << 64)
    assert accepts_entry(42, "ETH/USDT", STAMP, h + 1, 1 << 64)


def test_utc_naive_convention_and_hour_validation():
    assert canonical_timestamp(STAMP.replace(tzinfo=None)) == canonical_timestamp(STAMP)
    with pytest.raises(ValueError):
        canonical_timestamp(STAMP.replace(microsecond=1))


@pytest.mark.parametrize("n,d", [(0, 0), (-1, 2), (3, 2), (True, 2), (1.0, 2)])
def test_invalid_calibration_counts_rejected(n, d):
    with pytest.raises(ValueError):
        accepts_entry(42, "ETH/USDT", STAMP, n, d)


def test_probability_endpoints():
    assert not accepts_entry(42, "ETH/USDT", STAMP, 0, 10)
    assert accepts_entry(42, "ETH/USDT", STAMP, 10, 10)


def test_entry_only_filter_preserves_expiry_and_unknown_inventory():
    policy = Own24ThinnedFillStrategy(pair="ETH/USDT", seed=42, numerator=0, denominator=1)
    frame = pl.DataFrame({"timestamp": [STAMP + timedelta(hours=i) for i in range(27)],
                          "close": [100.0] * 25 + [110.0, 110.0]})
    def cycle(qty=0.0, hours=0, known=True):
        return PositionCycleView(symbol="ETH/USDT", quantity=qty,
            decision_at=STAMP + timedelta(hours=hours), inventory_known=known,
            first_fill_at=STAMP if qty else None,
            entry_order_id="fixture" if qty else None)
    assert policy.generate_signal_with_position(frame, cycle()).item() == 0
    assert policy.generate_signal_with_position(pl.DataFrame(), cycle(1.0, 23)).item() == 0
    assert policy.generate_signal_with_position(pl.DataFrame(), cycle(1.0, 24)).item() == -1
    assert policy.generate_signal_with_position(frame, cycle(known=False)).item() == 0
    full = Own24ThinnedFillStrategy(pair="ETH/USDT", seed=42, numerator=1, denominator=1)
    assert full.generate_signal_with_position(frame, cycle()).item() == 1


@pytest.mark.parametrize("seed,pair", [(99, "ETH/USDT"), (True, "ETH/USDT"), (42, "BTC/USDT")])
def test_unregistered_subject_or_seed_rejected(seed, pair):
    with pytest.raises(ValueError):
        accepts_entry(seed, pair, STAMP, 1, 2)


def test_timezone_conversion_preserves_hash():
    from datetime import timezone
    local = STAMP.astimezone(timezone(timedelta(hours=7)))
    assert canonical_timestamp(local) == canonical_timestamp(STAMP)
    assert accepts_entry(42, "ETH/USDT", local, 1, 2) == accepts_entry(
        42, "ETH/USDT", STAMP, 1, 2)


def test_hash_uses_last_closed_signal_bar_not_decision_bar():
    policy = Own24ThinnedFillStrategy(pair="ETH/USDT", seed=42, numerator=1, denominator=2)
    stamps = [STAMP + timedelta(hours=i) for i in range(27)]
    frame = pl.DataFrame({"timestamp": stamps, "close": [100.0] * 25 + [110.0, 110.0]})
    cycle = PositionCycleView(symbol="ETH/USDT", quantity=0.0,
        decision_at=stamps[-1], inventory_known=True,
        first_fill_at=None, entry_order_id=None)
    expected = int(accepts_entry(42, "ETH/USDT", stamps[-2], 1, 2))
    actual = policy.generate_signal_with_position(frame, cycle).item()
    assert actual == expected
    # A decision-row price spike must never change the closed-row decision.
    changed = frame.with_columns(pl.Series("close", [100.0] * 25 + [110.0, 0.01]))
    assert policy.generate_signal_with_position(changed, cycle).item() == actual


def test_pair_mismatch_rejected_and_precomputed_signals_disabled():
    policy = Own24ThinnedFillStrategy(pair="ETH/USDT", seed=42, numerator=1, denominator=2)
    cycle = PositionCycleView(symbol="BNB/USDT", quantity=0.0,
        decision_at=STAMP, inventory_known=True,
        first_fill_at=None, entry_order_id=None)
    with pytest.raises(ValueError, match="subject"):
        policy.generate_signal_with_position(pl.DataFrame(), cycle)
    with pytest.raises(ValueError, match="runtime position cycle"):
        policy.generate_signals(pl.DataFrame())
