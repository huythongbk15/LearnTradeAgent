"""Live Pipeline (Binance spot/futures → router feed) tests."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from trading_agent.data.live_pipeline import (
    LivePipeline,
    RoutingDecisionStub,
    _coerce_symbol,
)
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
from trading_agent.data.pipeline import MockSource


class MockCCXTSource:
    """Mock source that generates deterministic candles for testing."""

    name = "mock_ccxt"

    def __init__(self, symbol: Symbol, n_bars: int = 350):
        self._symbol = symbol
        self._n_bars = n_bars

    async def fetch_recent(
        self, symbol: Symbol, timeframe: str, limit: int = 200
    ) -> list:
        from trading_agent.exchanges.models import Candle

        rng = np.random.RandomState(42)
        base = 100.0
        prices = [base]
        for i in range(limit - 1):
            ret = rng.normal(0.001, 0.02)
            prices.append(prices[-1] * (1 + ret))

        ts_start = datetime.now(UTC) - timedelta(days=limit)
        candles = []
        for i, p in enumerate(prices):
            candles.append(
                Candle(
                    symbol=symbol,
                    timestamp=ts_start + timedelta(days=i),
                    timeframe=timeframe,
                    open=__import__("decimal").Decimal(str(p * 0.99)),
                    high=__import__("decimal").Decimal(str(p * 1.01)),
                    low=__import__("decimal").Decimal(str(p * 0.98)),
                    close=__import__("decimal").Decimal(str(p)),
                    volume=__import__("decimal").Decimal(str(100.0)),
                )
            )
        return candles

    async def close(self) -> None:
        pass


class TestCoerceSymbol:
    def test_spot_symbol(self):
        s = _coerce_symbol("BTCUSDT")
        assert s.base == "BTC"
        assert s.quote == "USDT"
        assert s.asset_class == AssetClass.CRYPTO
        assert s.market_type == MarketType.SPOT
        assert s.exchange == "binance"

    def test_futures_symbol(self):
        s = _coerce_symbol("BTCUSDT:USDT")
        assert s.base == "BTC"
        assert s.quote == "USDT"
        assert s.asset_class == AssetClass.FUTURES
        assert s.market_type == MarketType.PERPETUAL

    def test_passes_through_symbol_object(self):
        sym = Symbol(base="ETH", quote="USDT", asset_class=AssetClass.CRYPTO,
                     market_type=MarketType.SPOT, exchange="binance")
        s = _coerce_symbol(sym)
        assert s is sym

    def test_spot_with_slash(self):
        s = _coerce_symbol("BTC/USDT")
        assert s.base == "BTC"
        assert s.quote == "USDT"


class TestLivePipeline:
    def test_init_with_mock_source(self):
        pipe = LivePipeline(
            symbols=["BTCUSDT", "ETHUSDT"],
            source=MockSource(),
            dry_run=True,
        )
        assert len(pipe.symbols) == 2
        assert pipe.symbols[0].base == "BTC"
        assert pipe.dry_run is True

    def test_init_from_binance_requires_api_key(self):
        """from_binance should accept api_key/secret."""
        # Just verify the factory exists and has correct parameters
        import inspect
        sig = inspect.signature(LivePipeline.from_binance)
        assert "api_key" in sig.parameters
        assert "secret" in sig.parameters
        assert "testnet" in sig.parameters

    async def test_run_yields_decisions(self):
        """Pipeline should yield at least one RoutingDecisionStub per symbol."""
        pipe = LivePipeline(
            symbols=["BTCUSDT", "ETHUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            dry_run=True,
            poll_interval=0.1,
        )
        results = []
        async for stub in pipe.run(timeframe="1d", lookback=300, max_iterations=1):
            results.append(stub)
        assert len(results) > 0, "Pipeline should yield at least one decision"
        assert isinstance(results[0], RoutingDecisionStub)
        assert results[0].symbol in ("BTC/USDT", "ETH/USDT")
        assert results[0].posterior is not None
        assert results[0].context is not None

    async def test_min_lookback_enforced(self):
        """lookback must be >= min_lookback (300)."""
        pipe = LivePipeline(
            symbols=["BTCUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            dry_run=True,
        )
        with pytest.raises(AssertionError, match="lookback"):
            async for _ in pipe.run(timeframe="1d", lookback=50, max_iterations=1):
                pass

    async def test_no_router_observation_only(self):
        """Without router, yield RoutingDecisionStub."""
        pipe = LivePipeline(
            symbols=["BTCUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            router=None,
            dry_run=True,
            poll_interval=0.1,
        )
        async for stub in pipe.run(timeframe="1d", lookback=300, max_iterations=1):
            assert isinstance(stub, RoutingDecisionStub)
            assert stub.decision == "observe"
            break

    async def test_regime_posterior_valid(self):
        """RegimePosterior probabilities sum to 1.0."""
        pipe = LivePipeline(
            symbols=["BTCUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            dry_run=True,
            poll_interval=0.1,
        )
        async for stub in pipe.run(timeframe="1d", lookback=300, max_iterations=1):
            posterior = stub.posterior
            total = posterior.p_trend + posterior.p_mean_reversion + posterior.p_high_vol + posterior.p_crisis + posterior.p_other
            assert abs(total - 1.0) < 1e-9, f"Probabilities sum to {total}, expected 1.0"
            assert 0.0 <= posterior.ood_score <= 1.0
            break

    async def test_regime_confidence_clamped(self):
        """confidence_adjustment must be in [0.5, 1.5]."""
        pipe = LivePipeline(
            symbols=["BTCUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            dry_run=True,
            poll_interval=0.1,
        )
        async for stub in pipe.run(timeframe="1d", lookback=300, max_iterations=1):
            conf = stub.context.confidence_adjustment
            assert 0.5 <= conf <= 1.5, f"confidence={conf} not in [0.5, 1.5]"
            break

    async def test_multiple_iterations(self):
        """Pipeline should yield multiple bars over iterations."""
        pipe = LivePipeline(
            symbols=["BTCUSDT"],
            source=MockCCXTSource(Symbol(base="BTC", quote="USDT",
                                         asset_class=AssetClass.CRYPTO,
                                         market_type=MarketType.SPOT,
                                         exchange="binance")),
            dry_run=True,
            poll_interval=0.01,
        )
        count = 0
        async for stub in pipe.run(timeframe="1d", lookback=300, max_iterations=3):
            count += 1
        assert count == 3, f"Expected 3 iterations, got {count}"
