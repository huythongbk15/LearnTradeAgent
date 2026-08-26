"""S1 contract tests — StrategyDescriptor / AbstainStrategy /
LegacyDataFrameAdapter / CanonicalStrategyRegistry (STR-0101/0103/0104/0107
+ determinism slice of STR-0109)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.authority.config import Environment
from trading_agent.research.forecast import (
    Forecast,
    ForecastRiskPolicy,
    MarketObservation,
)
from trading_agent.strategies.canonical import (
    CANONICAL_ACTION_NO_TRADE,
    OHLCV_WINDOW_FEATURE,
    AbstainStrategy,
    CanonicalStrategyRegistry,
    LegacyAdapterError,
    LegacyDataFrameAdapter,
    RegistryIntegrityError,
    StrategyDescriptor,
    UnknownStrategyError,
)
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy


def _sha_of_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _observation(
    *,
    features: dict | None = None,
    observed_at: datetime | None = None,
) -> MarketObservation:
    return MarketObservation(
        symbol="BTC/USDT",
        observed_at=observed_at or datetime(2026, 1, 1, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        features=features or {},
    )


def _window(
    prices: list[float],
    *,
    end_at: datetime | None = None,
    with_time: bool = True,
) -> pl.DataFrame:
    end = end_at or datetime(2026, 1, 1, tzinfo=UTC)
    frame = pl.DataFrame({"close": prices}).with_columns(
        open=pl.col("close"),
        high=pl.col("close") * 1.01,
        low=pl.col("close") * 0.99,
        volume=pl.lit(10.0),
    )
    if with_time:
        frame = frame.with_columns(
            (
                pl.lit(end)
                - pl.duration(hours=pl.len() - pl.int_range(pl.len()))
            ).alias("time")
        )
    return frame.select(
        *(["time"] if with_time else []), "open", "high", "low", "close", "volume"
    )


# ── STR-0101 StrategyDescriptor ────────────────────────────────────────────


class TestStrategyDescriptor:
    def test_valid_descriptor_is_content_addressed(self):
        d = StrategyDescriptor(
            strategy_id="enhanced_ma",
            semantic_version="1.2.3",
            code_sha="a" * 64,
            required_features=("ema_fast", "ema_slow", "ema_fast"),
            horizon_bars=4,
            warmup_bars=2,
            supported_symbols=("BTC/USDT", "ETH/USDT"),
        )
        assert d.required_features == ("ema_fast", "ema_slow")
        expected_twin = StrategyDescriptor(
            strategy_id="enhanced_ma",
            semantic_version="1.2.3",
            code_sha="a" * 64,
            required_features=("ema_fast", "ema_slow"),
            horizon_bars=4,
            warmup_bars=2,
            supported_symbols=("BTC/USDT", "ETH/USDT"),
        )
        assert d.descriptor_id == expected_twin.descriptor_id

    def test_round_trip(self):
        d = StrategyDescriptor(
            strategy_id="rsi",
            semantic_version="0.1.0",
            code_sha="b" * 64,
            parameters_schema={"type": "object"},
            required_features=("rsi",),
            horizon_bars=2,
            warmup_bars=1,
            research_only=True,
        )
        assert StrategyDescriptor.from_dict(d.to_dict()) == d
        assert d.descriptor_id == StrategyDescriptor.from_dict(d.to_dict()).descriptor_id

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"strategy_id": "Bad-Id", "semantic_version": "1.0.0", "code_sha": "a" * 64},
            {"strategy_id": "ok", "semantic_version": "1.0", "code_sha": "a" * 64},
            {"strategy_id": "ok", "semantic_version": "1.0.0", "code_sha": "xyz"},
            {
                "strategy_id": "ok",
                "semantic_version": "1.0.0",
                "code_sha": "a" * 64,
                "required_features": ("BadFeature",),
            },
            {
                "strategy_id": "ok",
                "semantic_version": "1.0.0",
                "code_sha": "a" * 64,
                "horizon_bars": 0,
            },
            {
                "strategy_id": "ok",
                "semantic_version": "1.0.0",
                "code_sha": "a" * 64,
                "horizon_bars": 3,
                "warmup_bars": 3,
            },
        ],
    )
    def test_invalid_descriptors_raise(self, kwargs):
        with pytest.raises(ValueError):
            StrategyDescriptor(**kwargs)

    def test_invalid_symbol_rejected(self):
        with pytest.raises(ValueError, match="BASE/QUOTE"):
            StrategyDescriptor(
                strategy_id="ok",
                semantic_version="1.0.0",
                code_sha="a" * 64,
                horizon_bars=1,
                supported_symbols=("btcusdt",),
            )

    def test_empty_symbol_allowlist_admits_nothing(self):
        d = StrategyDescriptor(
            strategy_id="ok",
            semantic_version="1.0.0",
            code_sha="a" * 64,
            horizon_bars=1,
        )
        assert d.supports_symbol("BTC/USDT") is False

    def test_unknown_field_rejected_on_load(self):
        with pytest.raises(ValueError, match="unknown descriptor fields"):
            StrategyDescriptor.from_dict(
                {
                    "strategy_id": "ok",
                    "semantic_version": "1.0.0",
                    "code_sha": "a" * 64,
                    "hacker_field": True,
                }
            )


# ── STR-0104 AbstainStrategy ───────────────────────────────────────────────


class TestAbstainStrategy:
    def test_deterministic_fingerprint(self):
        strategy = AbstainStrategy()
        obs = _observation()
        f1 = strategy.forecast(obs)
        f2 = strategy.forecast(obs)
        assert isinstance(f1, Forecast)
        assert f1.fingerprint == f2.fingerprint
        assert f1.metadata["canonical_action"] == CANONICAL_ACTION_NO_TRADE

    def test_no_trade_yields_zero_exposure_via_risk_policy(self):
        forecast = AbstainStrategy().forecast(_observation())
        decision = ForecastRiskPolicy().evaluate(forecast, requested_exposure=0.25)
        assert float(decision.allowed_exposure) == 0.0


# ── STR-0103 LegacyDataFrameAdapter ────────────────────────────────────────


class TestLegacyDataFrameAdapter:
    def _adapter(self, **kw) -> LegacyDataFrameAdapter:
        defaults = dict(model_artifact_id="m-test", warmup_bars=30)
        defaults.update(kw)
        return LegacyDataFrameAdapter(RsiStrategy(), **defaults)

    def test_oversold_window_emits_buy(self):
        falling = [100.0 - i for i in range(40)]
        adapter = self._adapter()
        obs = _observation(features={OHLCV_WINDOW_FEATURE: _window(falling)})
        forecast = adapter.forecast(obs)
        assert forecast.metadata["canonical_action"] == "BUY"
        assert forecast.expected_excess_return > 0
        assert forecast.metadata["research_only"] is True
        # Determinism on identical observation.
        assert forecast.fingerprint == adapter.forecast(
            _observation(features={OHLCV_WINDOW_FEATURE: _window(falling)})
        ).fingerprint

    def test_overbought_window_emits_sell(self):
        rising = [50.0 + i for i in range(40)]
        adapter = self._adapter()
        forecast = adapter.forecast(
            _observation(features={OHLCV_WINDOW_FEATURE: _window(rising)})
        )
        assert forecast.metadata["canonical_action"] == "SELL"
        assert forecast.expected_excess_return < 0
        assert forecast.lower_bound <= forecast.expected_excess_return <= forecast.upper_bound

    def test_flat_window_emits_no_trade(self):
        # ma_crossover on a flat window: fast MA == slow MA → signal 0.
        # (RSI on flat data legitimately reads 0 → "oversold" → BUY, so the
        # NO_TRADE case is asserted on a strategy whose flat behaviour is
        # well-defined.)
        adapter = LegacyDataFrameAdapter(
            MaCrossover(), model_artifact_id="m-test", warmup_bars=60
        )
        forecast = adapter.forecast(
            _observation(features={OHLCV_WINDOW_FEATURE: _window([100.0] * 80)})
        )
        assert forecast.metadata["canonical_action"] == CANONICAL_ACTION_NO_TRADE
        assert float(forecast.expected_excess_return) == 0.0

    def test_fail_closed_missing_window(self):
        with pytest.raises(LegacyAdapterError, match="ohlcv_window"):
            self._adapter().forecast(_observation())

    def test_fail_closed_missing_columns(self):
        bad = pl.DataFrame({"close": [1.0, 2.0]})
        with pytest.raises(LegacyAdapterError, match="missing columns"):
            self._adapter().forecast(
                _observation(features={OHLCV_WINDOW_FEATURE: bad})
            )

    def test_fail_closed_insufficient_warmup(self):
        short = [100.0 - i for i in range(10)]
        with pytest.raises(LegacyAdapterError, match="rows"):
            self._adapter().forecast(
                _observation(features={OHLCV_WINDOW_FEATURE: _window(short)})
            )

    def test_fail_closed_future_window(self):
        falling = [100.0 - i for i in range(40)]
        future_end = datetime(2026, 1, 2, tzinfo=UTC)
        leaky = _window(falling, end_at=future_end)
        obs_at = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(LegacyAdapterError, match="point-in-time"):
            self._adapter(warmup_bars=30).forecast(
                _observation(observed_at=obs_at, features={OHLCV_WINDOW_FEATURE: leaky})
            )

    def test_rejects_non_strategy(self):
        with pytest.raises(TypeError):
            LegacyDataFrameAdapter(object(), model_artifact_id="x")


# ── STR-0107 CanonicalStrategyRegistry ─────────────────────────────────────

_THIS_FILE_SHA = _sha_of_file(__file__)


def _make_abstain_factory():
    return AbstainStrategy()


class TestCanonicalStrategyRegistry:
    def _descriptor(self, **kw) -> StrategyDescriptor:
        defaults = dict(
            strategy_id="abstain_test",
            semantic_version="1.0.0",
            code_sha=_THIS_FILE_SHA,
            horizon_bars=1,
        )
        defaults.update(kw)
        return StrategyDescriptor(**defaults)

    def test_register_and_get(self):
        registry = CanonicalStrategyRegistry()
        registry.register(self._descriptor(), _make_abstain_factory)
        descriptor, instance = registry.get(
            "abstain_test", environment=Environment.RESEARCH
        )
        assert descriptor.strategy_id == "abstain_test"
        assert isinstance(instance, AbstainStrategy)

    def test_hash_mismatch_blocked(self):
        registry = CanonicalStrategyRegistry()
        with pytest.raises(RegistryIntegrityError, match="code_sha mismatch"):
            registry.register(
                self._descriptor(code_sha="f" * 64), _make_abstain_factory
            )

    def test_duplicate_different_content_blocked(self):
        registry = CanonicalStrategyRegistry()
        registry.register(self._descriptor(), _make_abstain_factory)
        with pytest.raises(RegistryIntegrityError, match="already registered"):
            registry.register(
                self._descriptor(semantic_version="2.0.0"), _make_abstain_factory
            )

    def test_idempotent_identical_registration(self):
        registry = CanonicalStrategyRegistry()
        registry.register(self._descriptor(), _make_abstain_factory)
        registry.register(self._descriptor(), _make_abstain_factory)
        assert registry.list_ids() == ["abstain_test"]

    def test_research_only_blocked_outside_research(self):
        registry = CanonicalStrategyRegistry()
        registry.register(
            self._descriptor(research_only=True), _make_abstain_factory
        )
        with pytest.raises(RegistryIntegrityError, match="research_only"):
            registry.get("abstain_test", environment=Environment.PAPER)
        # RESEARCH admits it.
        _, instance = registry.get("abstain_test", environment=Environment.RESEARCH)
        assert instance is not None

    def test_unknown_strategy(self):
        registry = CanonicalStrategyRegistry()
        with pytest.raises(UnknownStrategyError):
            registry.get("ghost", environment=Environment.RESEARCH)

    def test_factory_not_producing_forecast_strategy_blocked(self):
        def _bad_factory():
            return object()

        registry = CanonicalStrategyRegistry()
        registry.register(
            self._descriptor(strategy_id="bad_factory"),
            _bad_factory,
            verify_code_hash=False,
        )
        with pytest.raises(RegistryIntegrityError, match="ForecastStrategy"):
            registry.get("bad_factory", environment=Environment.RESEARCH)
