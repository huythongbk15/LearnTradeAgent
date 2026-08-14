#!/usr/bin/env python3
"""Tests for Smart Execution Engine (Tier 1.3) and Alpha Research Pipeline (Tier 2)."""

import asyncio

import numpy as np
import pandas as pd
import pytest

# ════════════════════════════════════════════════════════════════
# Tier 1.3: Smart Execution Engine Tests
# ════════════════════════════════════════════════════════════════


class TestSlippageModel:
    def test_slippage_estimation(self):
        from src.trading_agent.execution.smart_router import SlippageModel

        sm = SlippageModel(k=0.15)
        bps = sm.estimate_slippage_bps(qty=1.0, adv=1_000_000, volatility=0.02)
        assert 0 < bps < 100, f"Slippage {bps:.1f} bps out of range"
        print(f"  Slippage 1 BTC (1M ADV, 2% vol): {bps:.1f} bps")

    def test_slippage_scales_with_size(self):
        from src.trading_agent.execution.smart_router import SlippageModel

        sm = SlippageModel(k=1.0)
        small = sm.estimate_slippage_bps(qty=0.1, adv=100_000, volatility=0.05)
        large = sm.estimate_slippage_bps(qty=10.0, adv=100_000, volatility=0.05)
        assert large > small, "Larger order should have higher slippage"
        print(
            f"  0.1 BTC: {small:.1f} bps → 10 BTC: {large:.1f} bps ({large / small:.1f}x)"
        )

    def test_slippage_book_depth_penalty(self):
        from src.trading_agent.execution.smart_router import SlippageModel

        sm = SlippageModel(k=1.0)
        no_depth = sm.estimate_slippage_bps(qty=10.0, adv=100_000, volatility=0.05)
        shallow = sm.estimate_slippage_bps(
            qty=10.0, adv=100_000, volatility=0.05, book_depth_usd=10_000
        )
        assert shallow > no_depth, "Shallow book should increase slippage"
        print(f"  No depth: {no_depth:.1f} bps → Shallow: {shallow:.1f} bps")

    def test_calibrate(self):
        from src.trading_agent.execution.smart_router import SlippageModel

        sm = SlippageModel()
        fills = [
            {"qty": 1.0, "adv": 1_000_000, "vol": 0.02, "slippage_bps": 8.0},
            {"qty": 0.5, "adv": 500_000, "vol": 0.015, "slippage_bps": 6.0},
            {"qty": 2.0, "adv": 2_000_000, "vol": 0.03, "slippage_bps": 12.0},
        ]
        k = sm.calibrate(fills)
        assert k > 0, "Calibrated k should be positive"
        print(f"  Calibrated k = {k:.4f}")


class TestSmartExecution:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_twap_order_creation(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        order = engine.create_twap_order(
            "BTC/USDT", "buy", 1.0, duration_s=100, n_slices=10
        )
        assert len(order.slices) == 10
        assert order.total_qty == 1.0
        assert order.algorithm == "twap"
        total = sum(s.qty for s in order.slices)
        assert abs(total - 1.0) < 1e-8, f"Slice sum {total} != 1.0"
        print(f"  TWAP: {len(order.slices)} slices, total={total}")

    def test_vwap_order_creation(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        profile = [100, 200, 150, 300, 250, 180, 120, 90]
        order = engine.create_vwap_order(
            "ETH/USDT", "sell", 8.0, profile, duration_s=80
        )
        assert len(order.slices) == 8
        assert order.algorithm == "vwap"
        # VWAP slices should be proportional to volume
        max_slice = max(s.qty for s in order.slices)
        min_slice = min(s.qty for s in order.slices)
        assert max_slice / min_slice > 1.5, "VWAP should vary slice sizes significantly"
        print(f"  VWAP: slices range {min_slice:.3f} – {max_slice:.3f}")

    def test_iceberg_order(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        order = engine.create_iceberg_order("SOL/USDT", "buy", 100.0, display_qty=10.0)
        assert len(order.slices) == 10
        assert order.algorithm == "iceberg"
        print(f"  Iceberg: {len(order.slices)} slices of ~10")

    def test_twap_execute_dry_run(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        order = engine.create_twap_order(
            "BTC/USDT", "buy", 0.5, duration_s=10, n_slices=5
        )
        summary = self._run(engine.execute(order, dry_run=True))
        assert summary["filled_qty"] > 0
        assert summary["slices_filled"] == 5
        assert summary["simulated"] is True
        print(
            f"  TWAP dry-run: filled {summary['filled_qty']:.4f} @ avg {summary['avg_price']:.0f}"
        )

    def test_vwap_execute_dry_run(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        profile = [1, 2, 1.5, 3, 2]
        order = engine.create_vwap_order("ETH/USDT", "sell", 5.0, profile, duration_s=5)
        summary = self._run(engine.execute(order, dry_run=True))
        assert summary["filled_qty"] > 0
        print(f"  VWAP dry-run: filled {summary['filled_qty']:.4f}")

    def test_vwap_profile_resample(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        hourly = [100, 120, 80, 150, 200, 180, 90, 110, 140, 160, 130, 100]
        profile = engine.generate_vwap_volume_profile(hourly, n_slices=6)
        assert len(profile) == 6
        assert all(v > 0 for v in profile)
        print(f"  Profile resample: {len(hourly)}h → {len(profile)} slices")

    def test_summary(self):
        from src.trading_agent.execution.smart_router import SmartExecutionEngine

        engine = SmartExecutionEngine()
        engine.create_twap_order("BTC/USDT", "buy", 1.0, duration_s=10, n_slices=3)
        engine.create_vwap_order("ETH/USDT", "sell", 5.0, [1, 2, 1], duration_s=3)
        s = engine.summary()
        assert len(s) == 2
        print(f"  Summary: {len(s)} active orders")


# ════════════════════════════════════════════════════════════════
# Tier 2: Alpha Research Pipeline Tests
# ════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_ohlcv():
    np.random.seed(42)
    n = 500
    close = 50000 + np.cumsum(np.random.randn(n) * 100)
    dates = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 50,
            "high": close + abs(np.random.randn(n) * 100),
            "low": close - abs(np.random.randn(n) * 100),
            "close": close,
            "volume": np.random.exponential(100, n) * 1000,
        },
        index=dates,
    )


class TestAlphaLibrary:
    def test_library_creation(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import _make_library

        lib = _make_library()
        alphas = lib.list_alphas()
        assert len(alphas) >= 40, f"Expected 40+ alphas, got {len(alphas)}"
        categories = set(a["category"] for a in alphas)
        assert "momentum" in categories
        assert "volatility" in categories
        assert "volume" in categories
        print(f"  Library: {len(alphas)} alphas in {len(categories)} categories")

    def test_compute_alpha(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import _make_library

        lib = _make_library()
        result = lib.compute("rsi_14", sample_ohlcv)
        assert len(result) == len(sample_ohlcv)
        valid = result.dropna()
        assert len(valid) > 0
        assert valid.min() >= 0
        assert valid.max() <= 100
        print(
            f"  RSI_14: {len(valid)} valid, range [{valid.min():.1f}, {valid.max():.1f}]"
        )

    def test_all_alphas_compute(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import _make_library

        lib = _make_library()
        computed = 0
        failed = 0
        for alpha_info in lib.list_alphas():
            try:
                vals = lib.compute(alpha_info["name"], sample_ohlcv)
                if hasattr(vals, "values"):
                    vals = vals.values
                assert len(vals) == len(sample_ohlcv)
                computed += 1
            except Exception:
                failed += 1
        assert computed >= 35, f"Expected 35+ alphas to compute, got {computed}"
        print(f"  Computed: {computed}/{computed + failed}")

    def test_missing_alpha_raises(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import _make_library

        lib = _make_library()
        with pytest.raises(KeyError, match="not found"):
            lib.compute("nonexistent_alpha", sample_ohlcv)


class TestAlphaEvaluator:
    def test_evaluate_good_alpha(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import AlphaEvaluator

        evaluator = AlphaEvaluator(forward_periods=5)
        # Perfect alpha: forward return itself
        close = sample_ohlcv["close"].values
        forward_ret = pd.Series(close).pct_change(5).shift(-5).values
        alpha = (
            forward_ret + np.random.randn(len(forward_ret)) * 0.001
        )  # nearly perfect
        report = evaluator.evaluate(alpha, forward_ret, name="perfect_alpha")
        assert report.grade in ("A", "B"), (
            f"Near-perfect alpha got grade {report.grade}"
        )
        print(f"  Near-perfect alpha: IC={report.ic_mean:.4f}, Grade={report.grade}")

    def test_evaluate_random_alpha(self, sample_ohlcv):
        from src.trading_agent.alpha_research.pipeline import AlphaEvaluator

        evaluator = AlphaEvaluator(forward_periods=5)
        alpha = np.random.randn(500)
        forward_ret = np.random.randn(500)
        report = evaluator.evaluate(alpha, forward_ret, name="random_alpha")
        # Random alpha should have low IC (not predictive)
        assert abs(report.ic_mean) < 0.15, (
            f"Random alpha IC {report.ic_mean:.4f} too high"
        )
        print(f"  Random alpha: IC={report.ic_mean:.4f}, Grade={report.grade}")

    def test_correlation_matrix(self):
        from src.trading_agent.alpha_research.pipeline import AlphaEvaluator

        evaluator = AlphaEvaluator()
        n = 200
        alpha_values = {
            "alpha_a": np.random.randn(n),
            "alpha_b": np.random.randn(n),
            "alpha_c": np.random.randn(n) * 0.5,
        }
        corr = evaluator.correlation_matrix(alpha_values)
        assert "alpha_a" in corr
        assert corr["alpha_a"]["alpha_a"] == 1.0
        print(f"  Correlation matrix: {len(corr)}x{len(corr)}")


class TestFeatureStore:
    def test_put_and_get(self, sample_ohlcv, tmp_path):
        from src.trading_agent.alpha_research.pipeline import FeatureStore

        store = FeatureStore(base_path=str(tmp_path / "features"))
        store.put("BTC/USDT", "rsi_14", sample_ohlcv, params={"period": 14})
        result = store.get("BTC/USDT", "rsi_14", params={"period": 14})
        assert result is not None
        assert len(result) == len(sample_ohlcv)
        print(f"  FeatureStore: put/get OK, {len(result)} rows")

    def test_versioning(self, sample_ohlcv, tmp_path):
        from src.trading_agent.alpha_research.pipeline import FeatureStore

        store = FeatureStore(base_path=str(tmp_path / "features"))
        v1 = store.put("BTC/USDT", "rsi_14", sample_ohlcv, params={"period": 14})
        v2 = store.put("BTC/USDT", "rsi_14", sample_ohlcv, params={"period": 20})
        assert v1 != v2, "Different params should produce different versions"
        versions = store.versions("BTC/USDT", "rsi_14")
        assert len(versions) == 2
        print(f"  Versions: {versions}")


class TestAutoMLPipeline:
    def test_scan(self, sample_ohlcv, tmp_path):
        from src.trading_agent.alpha_research.pipeline import (
            AlphaEvaluator,
            AutoMLPipeline,
            _make_library,
        )

        lib = _make_library()
        evaluator = AlphaEvaluator(forward_periods=5)
        automl = AutoMLPipeline(lib, evaluator)
        report = automl.scan(sample_ohlcv, report_path=str(tmp_path / "reports"))
        assert report["total_alphas"] >= 30
        assert len(report["top_10"]) > 0
        assert "names" in report["best_combo"]
        print(
            f"  AutoML scan: {report['total_alphas']} alphas, top={report['top_10'][0]['name']}"
        )
        print(f"  Best combo: {report['best_combo']['names']}")
        print(f"  Grades: {report['grade_distribution']}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
