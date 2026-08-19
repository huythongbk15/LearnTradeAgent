"""P0.3 — trusted time and market data tests."""

from __future__ import annotations

import time

import pytest

from trading_agent.execution.data_trust import (
    ClockSkewError,
    DataTrustError,
    DataTrustMonitor,
    DiffStreamState,
    HighLatencyError,
    OrderBookSequenceTracker,
    ServerClock,
    StaleQuoteError,
    TimeStampedFetch,
    TrustedPrice,
    reject_high_latency,
    reject_stale_exchange_timestamp,
)


def make_fetch(exchange_ms: float | None, latency_s: float) -> TimeStampedFetch:
    now = time.time()
    received = now
    started = now - latency_s
    return TimeStampedFetch(
        exchange_timestamp=exchange_ms,
        request_started_at=started,
        received_at=received,
    )


# ── three timestamps / latency ─────────────────────────────────────────


def test_fetch_latency_uses_received_minus_started():
    fetch = make_fetch(time.time() * 1000, latency_s=0.25)
    assert fetch.latency_s == pytest.approx(0.25, abs=1e-3)


def test_reject_high_latency_fails_closed():
    fetch = make_fetch(time.time() * 1000, latency_s=12.0)
    with pytest.raises(HighLatencyError, match="latency"):
        reject_high_latency(fetch, max_latency_s=5.0, context="BTC/USDT")


def test_reject_high_latency_passes_within_bounds():
    fetch = make_fetch(time.time() * 1000, latency_s=0.3)
    reject_high_latency(fetch, max_latency_s=5.0, context="BTC/USDT")  # no raise


def test_reject_high_latency_rejects_bad_limit():
    fetch = make_fetch(time.time() * 1000, latency_s=0.1)
    with pytest.raises(DataTrustError):
        reject_high_latency(fetch, max_latency_s=0)


def test_reject_stale_missing_exchange_timestamp():
    fetch = make_fetch(None, latency_s=0.1)
    with pytest.raises(StaleQuoteError, match="no exchange timestamp"):
        reject_stale_exchange_timestamp(fetch, context="BTC/USDT")


def test_reject_stale_future_dated():
    future_ms = (time.time() + 60) * 1000
    fetch = make_fetch(future_ms, latency_s=0.1)
    with pytest.raises(StaleQuoteError, match="future"):
        reject_stale_exchange_timestamp(fetch, context="BTC/USDT")


def test_reject_stale_old_quote():
    old_ms = (time.time() - 120) * 1000
    fetch = make_fetch(old_ms, latency_s=0.1)
    with pytest.raises(StaleQuoteError, match="stale"):
        reject_stale_exchange_timestamp(fetch, max_age_s=30.0, context="BTC/USDT")


def test_reject_stale_accepts_fresh_quote():
    fresh_ms = (time.time() - 2) * 1000
    fetch = make_fetch(fresh_ms, latency_s=0.1)
    reject_stale_exchange_timestamp(fetch, max_age_s=30.0, context="BTC/USDT")


# ── clock skew ─────────────────────────────────────────────────────────


def test_server_clock_needs_sync_before_check():
    clock = ServerClock(tolerance_s=2.0)
    with pytest.raises(ClockSkewError, match="never been synced"):
        clock.check()


def test_server_clock_accepts_small_skew():
    clock = ServerClock(tolerance_s=2.0)
    clock.sample(server_time_ms=(time.time() + 0.5) * 1000)
    assert clock.check() == pytest.approx(0.5, abs=0.1)


def test_server_clock_rejects_excessive_skew():
    clock = ServerClock(tolerance_s=2.0)
    clock.sample(server_time_ms=(time.time() + 30) * 1000)
    with pytest.raises(ClockSkewError, match="skew"):
        clock.check()


def test_server_clock_sync_with_fetch_fn():
    clock = ServerClock()
    offset_s = clock.sync(fetch_fn=lambda: (time.time() + 0.25) * 1000)
    assert offset_s == pytest.approx(0.25, abs=0.1)
    assert clock.sync_count == 1
    clock.check()


def test_server_clock_rejects_invalid_sample():
    clock = ServerClock()
    with pytest.raises(DataTrustError):
        clock.sample(server_time_ms=0)


# ── order book sequence tracker ────────────────────────────────────────


def test_sequence_tracker_monotonic_flow():
    tracker = OrderBookSequenceTracker()
    assert tracker.on_update("BTC/USDT", 100) == "uninitialized"
    assert tracker.on_update("BTC/USDT", 101) == "ok"
    assert tracker.on_update("BTC/USDT", 102) == "ok"
    assert tracker.last_sequence("BTC/USDT") == 102


def test_sequence_tracker_detects_duplicate():
    tracker = OrderBookSequenceTracker()
    tracker.on_update("BTC/USDT", 100)
    assert tracker.on_update("BTC/USDT", 100) == "duplicate"
    assert tracker.duplicate_count("BTC/USDT") == 1


def test_sequence_tracker_detects_gap():
    tracker = OrderBookSequenceTracker()
    tracker.on_update("BTC/USDT", 100)
    assert tracker.on_update("BTC/USDT", 150) == "gap"
    assert tracker.gap_count("BTC/USDT") == 1


def test_sequence_tracker_rejects_invalid():
    tracker = OrderBookSequenceTracker()
    assert tracker.on_update("BTC/USDT", -5) == "invalid"
    assert tracker.on_update("BTC/USDT", None) == "uninitialized"


def test_sequence_tracker_snapshot_resets():
    tracker = OrderBookSequenceTracker()
    tracker.on_snapshot("BTC/USDT", 1000)
    assert tracker.last_sequence("BTC/USDT") == 1000
    assert tracker.on_update("BTC/USDT", 1001) == "ok"


def test_sequence_tracker_snapshot_rejects_bad_id():
    tracker = OrderBookSequenceTracker()
    with pytest.raises(DataTrustError):
        tracker.on_snapshot("BTC/USDT", -1)


def test_rest_snapshot_jumps_are_not_gaps():
    """REST depth lastUpdateId jumps by thousands between calls — expected."""
    tracker = OrderBookSequenceTracker()
    assert tracker.on_rest_snapshot("BTC/USDT", 100) == "ok"
    assert tracker.on_rest_snapshot("BTC/USDT", 99_999_000) == "ok"
    assert tracker.last_sequence("BTC/USDT") == 99_999_000


def test_rest_snapshot_rejects_backwards_id():
    tracker = OrderBookSequenceTracker()
    tracker.on_rest_snapshot("BTC/USDT", 500)
    assert tracker.on_rest_snapshot("BTC/USDT", 499) == "stale"
    assert tracker.duplicate_count("BTC/USDT") == 1


def test_rest_snapshot_rejects_duplicate_id():
    tracker = OrderBookSequenceTracker()
    tracker.on_rest_snapshot("BTC/USDT", 500)
    assert tracker.on_rest_snapshot("BTC/USDT", 500) == "duplicate"


# ── Binance diff stream protocol (snapshot + diff) ─────────────────────


def test_diff_stream_first_diff_straddles_snapshot():
    state = DiffStreamState("BTC/USDT")
    state.initialize(1000)
    status = state.apply_diff(
        first_update_id=999,
        final_update_id=1002,
        previous_update_id=None,
        bids=[],
        asks=[],
    )
    assert status == "ready_first"
    assert state.last_u == 1002
    assert not state.needs_resync


def test_diff_stream_continuation_requires_matching_pu():
    state = DiffStreamState("BTC/USDT")
    state.initialize(100)
    state.apply_diff(
        first_update_id=99,
        final_update_id=105,
        previous_update_id=None,
        bids=[],
        asks=[],
    )
    status = state.apply_diff(
        first_update_id=106,
        final_update_id=110,
        previous_update_id=105,
        bids=[],
        asks=[],
    )
    assert status == "ok"
    assert state.last_u == 110


def test_diff_stream_gap_triggers_resync():
    state = DiffStreamState("BTC/USDT")
    state.initialize(100)
    state.apply_diff(
        first_update_id=99,
        final_update_id=105,
        previous_update_id=None,
        bids=[],
        asks=[],
    )
    status = state.apply_diff(
        first_update_id=106,
        final_update_id=200,
        previous_update_id=105,
        bids=[],
        asks=[],
    )
    assert status == "ok"
    assert state.last_u == 200
    # Next diff claims pu=205 but our last_u is 200 → real gap → resync.
    status2 = state.apply_diff(
        first_update_id=206,
        final_update_id=210,
        previous_update_id=205,
        bids=[],
        asks=[],
    )
    assert status2 == "gap"
    assert state.needs_resync
    assert state.gap_count == 1


def test_diff_stream_stale_duplicate_skipped():
    state = DiffStreamState("BTC/USDT")
    state.initialize(100)
    state.apply_diff(
        first_update_id=99,
        final_update_id=105,
        previous_update_id=None,
        bids=[],
        asks=[],
    )
    status = state.apply_diff(
        first_update_id=100,
        final_update_id=104,
        previous_update_id=99,
        bids=[],
        asks=[],
    )
    assert status == "stale"
    assert state.stale_count == 1


def test_diff_stream_rejects_invalid_id_order():
    state = DiffStreamState("BTC/USDT")
    state.initialize(100)
    with pytest.raises(DataTrustError):
        state.apply_diff(
            first_update_id=500,
            final_update_id=400,
            previous_update_id=None,
            bids=[],
            asks=[],
        )


def test_diff_stream_unsynced_returns_gap():
    state = DiffStreamState("BTC/USDT")
    assert state.needs_resync
    assert (
        state.apply_diff(
            first_update_id=1,
            final_update_id=2,
            previous_update_id=None,
            bids=[],
            asks=[],
        )
        == "gap"
    )


# ── monitor metrics export ─────────────────────────────────────────────


def test_monitor_metrics_exports_p03_dashboard():
    monitor = DataTrustMonitor()
    monitor.clock.sample(server_time_ms=(time.time() + 0.2) * 1000)
    monitor.record_fetch(
        "BTC/USDT",
        make_fetch((time.time() - 1) * 1000, latency_s=0.2),
    )
    monitor.sequences.on_update("BTC/USDT", 100)
    metrics = monitor.metrics()
    assert metrics["clock_synced"] is True
    assert metrics["clock_skew_s"] == pytest.approx(0.2, abs=0.1)
    assert "BTC/USDT" in metrics["quote_age_s"]
    assert "BTC/USDT" in metrics["request_latency_s"]
    assert metrics["sequence"]["BTC/USDT"]["last_sequence"] == 100
    assert metrics["fetches"] == 1
    assert metrics["rejections"] == 0


def test_monitor_rejects_and_counts():
    monitor = DataTrustMonitor(max_age_s=5.0)
    monitor.clock.sample(server_time_ms=(time.time()) * 1000)
    with pytest.raises(StaleQuoteError):
        monitor.record_fetch(
            "BTC/USDT",
            make_fetch((time.time() - 120) * 1000, latency_s=0.1),
        )
    assert monitor.rejection_count() == 1
    assert monitor.metrics()["rejections"] == 1


# ── TrustedPrice exchange timestamp validation ─────────────────────────


class TestTrustedPriceValidation:
    def test_mandatory_exchange_timestamp_has_value(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=time.time() * 1000,
            received_at=time.monotonic(),
        )
        assert tp.exchange_timestamp is not None

    def test_fresh_price_passes(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=(time.time() - 1) * 1000,
            received_at=time.monotonic(),
        )
        tp.validate_freshness(max_age_s=30.0)  # no raise

    def test_stale_price_raises(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=(time.time() - 120) * 1000,
            received_at=time.monotonic(),
        )
        with pytest.raises(StaleQuoteError, match="stale"):
            tp.validate_freshness(max_age_s=30.0)

    def test_future_timestamp_raises(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=(time.time() + 60) * 1000,
            received_at=time.monotonic(),
        )
        with pytest.raises(StaleQuoteError, match="future"):
            tp.validate_freshness(max_age_s=30.0)

    def test_monotonicity_passes_on_first_sample(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=time.time() * 1000,
            received_at=time.monotonic(),
            previous_exchange_timestamp=None,
        )
        tp.validate_monotonicity()  # no raise

    def test_monotonicity_passes_when_increasing(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=(time.time() + 1) * 1000,
            received_at=time.monotonic(),
            previous_exchange_timestamp=time.time() * 1000,
        )
        tp.validate_monotonicity()  # no raise

    def test_monotonicity_raises_when_decreasing(self):
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=(time.time() - 1) * 1000,
            received_at=time.monotonic(),
            previous_exchange_timestamp=time.time() * 1000,
        )
        with pytest.raises(DataTrustError, match="monotonicity violated"):
            tp.validate_monotonicity()

    def test_monotonicity_raises_when_frozen(self):
        now_ms = time.time() * 1000
        tp = TrustedPrice(
            symbol="BTC/USDT",
            price=50_000.0,
            exchange_timestamp=now_ms,
            received_at=time.monotonic(),
            previous_exchange_timestamp=now_ms,
        )
        with pytest.raises(DataTrustError, match="monotonicity violated"):
            tp.validate_monotonicity()
