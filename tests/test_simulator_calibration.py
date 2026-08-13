"""Tests for SimulatorCalibrator (Wave E)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from trading_agent.execution.simulator import (
    MarketReplayEngine,
    OrderIntent,
    SimOrderType,
    SimSide,
    SimulationConfig,
)
from trading_agent.execution.simulator.calibration import (
    CalibrationSample,
    FillModelParams,
    ImpactModelParams,
    SimulatorCalibrator,
    collect_testnet_fills,
    validate_calibration,
)


def make_df(n: int = 50) -> pl.DataFrame:
    """Deterministic OHLCV frame."""
    import datetime as dt

    rows = []
    for i in range(n):
        o = 100.0 + i * 0.2
        c = o + 0.1
        rows.append({
            "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC) + dt.timedelta(hours=i),
            "open": o,
            "high": o + 0.3,
            "low": o - 0.3,
            "close": c,
            "volume": 10.0 + i * 0.1,
        })
    return pl.DataFrame(rows)


class TestCalibrationSample:
    def test_slippage_bps_calculation(self):
        # Buy: positive slippage = paid more than arrival mid
        s = CalibrationSample(
            bar_index=0, side="buy", quantity=1.0,
            arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
            latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
            aggressor="market"
        )
        # (100.05 - 100.0) / 100.0 * 10000 = 5 bps
        assert s.slippage_bps == pytest.approx(5.0, rel=1e-3)

        # Sell: positive slippage = received less than arrival mid
        s = CalibrationSample(
            bar_index=0, side="sell", quantity=1.0,
            arrival_mid=100.0, fill_vwap=99.95, spread_bps=5.0,
            latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
            aggressor="market"
        )
        assert s.slippage_bps == pytest.approx(5.0, rel=1e-3)

    def test_serialization(self):
        s = CalibrationSample(
            bar_index=1, side="buy", quantity=0.5,
            arrival_mid=100.0, fill_vwap=100.02, spread_bps=3.0,
            latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
            aggressor="limit_passive", fee_bps=0.2
        )
        d = s.to_dict()
        s2 = CalibrationSample.from_dict(d)
        assert s2.bar_index == s.bar_index
        assert s2.side == s.side
        assert s2.fee_bps == s.fee_bps


class TestSimulatorCalibrator:
    def setup_method(self):
        self.config = SimulationConfig(random_seed=42)
        self.calibrator = SimulatorCalibrator(self.config)

    def test_fit_fill_model_insufficient_data(self):
        # Add only 3 maker samples
        for i in range(3):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.01, spread_bps=5.0,
                latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive"
            ))
        params = self.calibrator.fit_fill_model()
        # Should return default with sample count
        assert params.passive_fill_prob == self.config.passive_fill_prob
        assert params.sample_count == 3

    def test_fit_fill_model_with_data(self):
        # Add enough maker samples
        for i in range(20):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy" if i % 2 == 0 else "sell", quantity=1.0,
                arrival_mid=100.0 + i * 0.1, fill_vwap=100.0 + i * 0.1 + 0.01, spread_bps=5.0,
                latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive"
            ))
        params = self.calibrator.fit_fill_model()
        assert params.sample_count == 20
        assert 0.05 <= params.passive_fill_prob <= 0.8

    def test_fit_impact_model_insufficient_data(self):
        for i in range(5):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        params = self.calibrator.fit_impact_model()
        # Should return defaults with sample count
        assert params.impact_coeff == self.config.impact_coeff
        assert params.sample_count == 5

    def test_fit_impact_model_with_data(self):
        for i in range(30):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy" if i % 2 == 0 else "sell", quantity=1.0,
                arrival_mid=100.0 + i * 0.1, fill_vwap=100.0 + i * 0.1 + 0.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        params = self.calibrator.fit_impact_model()
        assert params.sample_count == 30
        assert params.impact_coeff > 0
        assert params.impact_decay_half_life_bars > 0
        assert params.adverse_selection_bps > 0

    def test_calibrate_full(self):
        # Add both maker and aggressive samples
        for i in range(15):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.01, spread_bps=5.0,
                latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive"
            ))
        for i in range(20):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        result = self.calibrator.calibrate()
        assert isinstance(result.fill_model, FillModelParams)
        assert isinstance(result.impact_model, ImpactModelParams)
        assert result.config_fingerprint == self.config.fingerprint()

    def test_apply_to_config(self):
        for i in range(20):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        result = self.calibrator.calibrate()
        new_config = self.calibrator.apply_to_config(result)

        assert new_config.passive_fill_prob == result.fill_model.passive_fill_prob
        assert new_config.impact_coeff == result.impact_model.impact_coeff
        assert new_config.impact_decay_half_life_bars == result.impact_model.impact_decay_half_life_bars
        assert new_config.adverse_selection_bps == result.impact_model.adverse_selection_bps
        # Other fields preserved
        assert new_config.random_seed == self.config.random_seed
        assert new_config.spread_bps == self.config.spread_bps

    def test_save_load_roundtrip(self):
        for i in range(20):
            self.calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        result = self.calibrator.calibrate()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "calibration.json"
            self.calibrator.save(result, path)
            loaded = SimulatorCalibrator.load(path)

        assert loaded.fill_model.passive_fill_prob == result.fill_model.passive_fill_prob
        assert loaded.impact_model.impact_coeff == result.impact_model.impact_coeff
        assert loaded.config_fingerprint == result.config_fingerprint
        assert len(loaded.samples) == len(result.samples)

    def test_add_samples_from_dataframe(self):
        df = pl.DataFrame({
            "bar_index": [0, 1, 2],
            "side": ["buy", "sell", "buy"],
            "quantity": [1.0, 1.0, 0.5],
            "arrival_mid": [100.0, 100.1, 100.2],
            "fill_vwap": [100.02, 100.08, 100.21],
            "spread_bps": [5.0, 5.0, 5.0],
            "latency_ms": [50.0, 50.0, 50.0],
            "is_maker": [False, False, True],
            "timestamp": [datetime.now(UTC).isoformat()] * 3,
            "aggressor": ["market", "market", "limit_passive"],
            "fee_bps": [0.5, 0.5, 0.2],
        })
        self.calibrator.add_samples_from_dataframe(df)
        assert len(self.calibrator.samples) == 3

    def test_dataframe_missing_columns_raises(self):
        df = pl.DataFrame({"bar_index": [0], "side": ["buy"]})
        with pytest.raises(ValueError, match="missing columns"):
            self.calibrator.add_samples_from_dataframe(df)


class TestCollectTestnetFills:
    def test_collect_from_engine(self):
        df = make_df(30)
        config = SimulationConfig(random_seed=42)
        engine = MarketReplayEngine(df, config=config, symbol="TEST", initial_cash=10_000.0)

        def provider(i, eng):
            if i == 5:
                return [OrderIntent(order_id="buy1", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)]
            if i == 15:
                return [OrderIntent(order_id="sell1", side=SimSide.SELL, order_type=SimOrderType.MARKET, quantity=eng.ledger.inventory_base)]
            return []

        samples = collect_testnet_fills(engine, provider)
        assert len(samples) >= 2  # buy + sell
        for s in samples:
            assert isinstance(s, CalibrationSample)
            assert s.side in ("buy", "sell")
            assert s.quantity > 0
            assert s.arrival_mid > 0
            assert s.aggressor in ("market", "limit_passive")


class TestValidateCalibration:
    def test_insufficient_samples(self):
        config = SimulationConfig(random_seed=42)
        samples = []
        result = validate_calibration(config, samples)
        assert "error" in result

    def test_returns_calibrated_params(self):
        config = SimulationConfig(random_seed=42)
        calibrator = SimulatorCalibrator(config)
        for i in range(30):
            calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        for i in range(15):
            calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0, fill_vwap=100.01, spread_bps=5.0,
                latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive"
            ))
        samples = calibrator.samples
        result = validate_calibration(config, samples, holdout_frac=0.2)

        assert "fill_model_passive_fill_prob" in result
        assert "impact_model_impact_coeff" in result
        assert "impact_model_decay_half_life" in result
        assert "impact_model_adverse_bps" in result
        assert result["train_samples"] > 0
        assert result["holdout_samples"] > 0


class TestIntegration:
    def test_calibrated_config_runs_engine(self):
        """End-to-end: calibrate -> apply config -> run engine -> no errors."""
        df = make_df(50)
        config = SimulationConfig(random_seed=42)
        calibrator = SimulatorCalibrator(config)

        # Add synthetic calibration data
        for i in range(25):
            calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0 + i * 0.2, fill_vwap=100.0 + i * 0.2 + 0.05, spread_bps=5.0,
                latency_ms=50.0, is_maker=False, timestamp=datetime.now(UTC).isoformat(),
                aggressor="market"
            ))
        for i in range(15):
            calibrator.add_sample(CalibrationSample(
                bar_index=i, side="buy", quantity=1.0,
                arrival_mid=100.0 + i * 0.2, fill_vwap=100.0 + i * 0.2 + 0.01, spread_bps=5.0,
                latency_ms=30.0, is_maker=True, timestamp=datetime.now(UTC).isoformat(),
                aggressor="limit_passive"
            ))

        result = calibrator.calibrate()
        calibrated_config = calibrator.apply_to_config(result)

        # Run engine with calibrated config
        engine = MarketReplayEngine(df, config=calibrated_config, symbol="TEST", initial_cash=10_000.0)

        # BUY at 5, SELL at 15 (after BUY filled), BUY at 25, SELL at 35
        def provider(i, e):
            if i == 5:
                return [OrderIntent(order_id="buy1", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)]
            if i == 15:
                return [OrderIntent(order_id="sell1", side=SimSide.SELL, order_type=SimOrderType.MARKET, quantity=e.ledger.inventory_base)]
            if i == 25:
                return [OrderIntent(order_id="buy2", side=SimSide.BUY, order_type=SimOrderType.MARKET, quantity=1.0)]
            if i == 35:
                return [OrderIntent(order_id="sell2", side=SimSide.SELL, order_type=SimOrderType.MARKET, quantity=e.ledger.inventory_base)]
            return []

        sim_result = engine.run(provider)

        assert sim_result.metrics.trade_count > 0
        # Verify config was actually used (fill prob should match calibrated)
        assert engine.fill_model.config.passive_fill_prob == result.fill_model.passive_fill_prob
        assert engine.impact_model.config.impact_coeff == result.impact_model.impact_coeff