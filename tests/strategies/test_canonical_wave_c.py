"""S1 Wave C tests — features (STR-0105), state ledger (STR-0108),
default candidate registry (STR-0107 deliverables)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.authority.config import Environment
from trading_agent.strategies.canonical import (
    FEATURE_OHLCV_WINDOW,
    FeatureUnavailableError,
    StrategyEventLedger,
    StrategyStateKey,
    build_default_registry,
    build_ohlcv_window,
    validate_point_in_time,
)

_OBS_AT = datetime(2026, 1, 10, tzinfo=UTC)


def _frame(n: int = 30, *, end: datetime | None = None) -> pl.DataFrame:
    end = end or _OBS_AT
    prices = [100.0 + i for i in range(n)]
    frame = pl.DataFrame({"close": prices}).with_columns(
        open=pl.col("close"),
        high=pl.col("close") * 1.01,
        low=pl.col("close") * 0.99,
        volume=pl.lit(1.0),
    )
    return frame.with_columns(
        (
            pl.lit(end)
            - pl.duration(hours=(n - 1) - pl.int_range(n))
            - pl.duration(hours=1)
        ).alias("time")
    )


# ── STR-0105 features ──────────────────────────────────────────────────────


class TestPointInTimeFeatures:
    def test_build_window_keeps_only_closed_bars(self):
        frame = _frame(30)  # last bar at _OBS_AT - 1h → all closed
        window = build_ohlcv_window(frame, observed_at=_OBS_AT, bars=20)
        assert window.height == 20
        validate_point_in_time(window, observed_at=_OBS_AT)

    def test_build_window_excludes_unclosed_current_bar(self):
        # Last row stamped AT the observation time — not closed yet.
        frame = _frame(30).vstack(
            _frame(1, end=_OBS_AT + timedelta(hours=0)).select(_frame(1).columns)
        )
        with pytest.raises((FeatureUnavailableError, Exception)):
            build_ohlcv_window(
                frame.filter(pl.col("time") >= _OBS_AT), observed_at=_OBS_AT, bars=1
            )

    def test_build_window_insufficient_history(self):
        frame = _frame(5)
        with pytest.raises(FeatureUnavailableError, match="closed bars"):
            build_ohlcv_window(frame, observed_at=_OBS_AT, bars=50)

    def test_missing_time_column_rejected(self):
        frame = _frame(30).drop("time")
        with pytest.raises(FeatureUnavailableError, match="time"):
            build_ohlcv_window(frame, observed_at=_OBS_AT, bars=5)

    def test_future_leak_rejected_by_validate(self):
        leaky = _frame(3, end=_OBS_AT + timedelta(hours=2))
        with pytest.raises(FeatureUnavailableError, match="point-in-time"):
            validate_point_in_time(leaky, observed_at=_OBS_AT)


# ── STR-0108 state ledger ──────────────────────────────────────────────────


class TestStrategyEventLedger:
    def test_first_delivery_applies_duplicate_skips(self):
        key = StrategyStateKey("enhanced_ma", "BTC/USDT")
        ledger = StrategyEventLedger()
        assert ledger.observe(key, "obs-001") is True
        assert ledger.observe(key, "obs-001") is False

    def test_isolation_between_strategy_and_symbol(self):
        k1 = StrategyStateKey("enhanced_ma", "BTC/USDT")
        k2 = StrategyStateKey("ma_adx", "BTC/USDT")
        k3 = StrategyStateKey("enhanced_ma", "ETH/USDT")
        ledger = StrategyEventLedger()
        assert ledger.observe(k1, "obs-9") is True
        assert ledger.observe(k2, "obs-9") is True
        assert ledger.observe(k3, "obs-9") is True

    def test_bounded_memory_evicts_oldest(self):
        key = StrategyStateKey("rsi", "SOL/USDT")
        ledger = StrategyEventLedger(max_seen_per_key=3)
        for i in range(5):
            ledger.observe(key, f"e{i}")
        assert ledger.has(key, "e0") is False
        assert ledger.has(key, "e4") is True
        # Replaying an evicted event re-applies (bounded-memory trade-off).
        assert ledger.observe(key, "e0") is True

    def test_reset(self):
        key = StrategyStateKey("a", "B/USDT")
        ledger = StrategyEventLedger()
        ledger.observe(key, "x")
        ledger.reset(key)
        assert ledger.observe(key, "x") is True

    def test_empty_event_id_rejected(self):
        with pytest.raises(ValueError):
            StrategyEventLedger().observe(StrategyStateKey("a", "B/USDT"), "")


# ── Default candidate registry (deliverable: 5 adapters) ──────────────────


class TestDefaultCandidateRegistry:
    def test_five_candidates_registered_with_verified_hashes(self):
        registry = build_default_registry()
        assert sorted(registry.list_ids()) == sorted(
            ["enhanced_ma", "ma_adx", "ma_vol_target", "rsi", "bbands"]
        )
        for strategy_id in registry.list_ids():
            desc = registry.describe(strategy_id)
            assert desc.research_only is True
            assert desc.required_features == (FEATURE_OHLCV_WINDOW,)
            assert desc.supports_symbol("BTC/USDT")
            assert not desc.supports_symbol("XXX/USDT")

    @pytest.mark.parametrize(
        "strategy_id",
        ["enhanced_ma", "ma_adx", "ma_vol_target", "rsi", "bbands"],
    )
    def test_resolution_blocked_outside_research(self, strategy_id):
        registry = build_default_registry()
        from trading_agent.strategies.canonical import RegistryIntegrityError

        with pytest.raises(RegistryIntegrityError, match="research_only"):
            registry.get(strategy_id, environment=Environment.PAPER)

    def test_adapters_forecast_deterministically_in_research(self):
        registry = build_default_registry()
        window = build_ohlcv_window(_frame(140), observed_at=_OBS_AT, bars=120)
        obs_features = {FEATURE_OHLCV_WINDOW: window}
        from trading_agent.research.forecast import MarketObservation

        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=_OBS_AT,
            open=129.0,
            high=130.0,
            low=128.0,
            close=129.5,
            volume=1.0,
            features=obs_features,
        )
        for strategy_id in registry.list_ids():
            _, adapter_a = registry.get(strategy_id, environment=Environment.RESEARCH)
            _, adapter_b = registry.get(strategy_id, environment=Environment.RESEARCH)
            f_a = adapter_a.forecast(observation)
            f_b = adapter_b.forecast(observation)
            assert f_a.fingerprint == f_b.fingerprint
            assert -1.0 <= f_a.expected_excess_return <= 1.0
