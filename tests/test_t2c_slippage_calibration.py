"""T2C: Per-asset slippage calibration tests.

Acceptance criteria:
- Calibration loads from data/slippage_calibration.json
- Per-symbol base_slippage_bps overrides global default (5.0 bps)
- Per-symbol volatility factor overrides global default (1.0)
- create_execution_simulator() auto-loads calibration when not explicitly provided
- _compute_slippage uses per-symbol values when book.symbol matches
- Falls back to global default when symbol not in calibration
- Fallback to empty dict when calibration file not found
"""

from __future__ import annotations


import pytest

from trading_agent.execution.backtest_sim.models import (
    OrderBookSnapshot,
    OrderSide,
    create_execution_simulator,
)
from trading_agent.execution.backtest_sim.t2c_calibration import (
    build_all_simulator_config_overrides,
    build_simulator_config_overrides,
    load_slippage_calibration,
)


class TestCalibrationLoader:
    """Verify T2C calibration file loading."""

    def test_loads_all_three_assets(self):
        """Calibration should load BTCUSDT, ETHUSDT, BNBUSDT."""
        cal = load_slippage_calibration()
        assert set(cal.keys()) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}

    def test_btc_calibrated_above_default(self):
        """BTC base_slippage_bps (9.5) > global default (5.0)."""
        cal = load_slippage_calibration()
        assert cal["BTCUSDT"]["base_slippage_bps"] == 9.5
        assert cal["BTCUSDT"]["base_slippage_bps"] > 5.0

    def test_eth_has_highest_slippage(self):
        """ETH should have highest slippage (most volatile)."
        """""
        cal = load_slippage_calibration()
        assert cal["ETHUSDT"]["base_slippage_bps"] == 12.8
        assert cal["ETHUSDT"]["base_slippage_bps"] > cal["BTCUSDT"]["base_slippage_bps"]

    def test_bnb_between_btc_and_eth(self):
        """BNB slippage should be between BTC and ETH."""
        cal = load_slippage_calibration()
        assert cal["BNBUSDT"]["base_slippage_bps"] == 11.6
        assert cal["BTCUSDT"]["base_slippage_bps"] < cal["BNBUSDT"]["base_slippage_bps"]
        assert cal["BNBUSDT"]["base_slippage_bps"] < cal["ETHUSDT"]["base_slippage_bps"]

    def test_all_have_volatility_factor(self):
        """Each asset must have slippage_volatility_factor."""
        cal = load_slippage_calibration()
        for symbol, params in cal.items():
            assert "slippage_volatility_factor" in params
            assert 0.0 < params["slippage_volatility_factor"] <= 1.0

    def test_file_not_found_returns_empty(self, tmp_path):
        """Missing calibration file → empty dict (global defaults used)."""
        result = load_slippage_calibration(tmp_path / "nonexistent.json")
        assert result == {}

    def test_build_overrides_for_symbol(self):
        """build_simulator_config_overrides returns per-symbol params."""
        overrides = build_simulator_config_overrides("BTCUSDT")
        assert overrides == {
            "base_slippage_bps": 9.5,
            "slippage_volatility_factor": 0.55,
        }

    def test_build_overrides_unknown_symbol(self):
        """Unknown symbol → empty overrides (falls back to global default)."""
        overrides = build_simulator_config_overrides("UNKNOWN")
        assert overrides == {}

    def test_build_all_overrides(self):
        """build_all returns dicts for all 3 assets."""
        bps, vol = build_all_simulator_config_overrides()
        assert set(bps.keys()) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
        assert set(vol.keys()) == {"BTCUSDT", "ETHUSDT", "BNBUSDT"}
        assert bps["BTCUSDT"] == 9.5
        assert vol["ETHUSDT"] == 0.65


class TestSimulatorConfigIntegration:
    """Verify per-symbol slippage propagates to SimulatorConfig."""

    def test_auto_load_calibration(self):
        """create_execution_simulator() auto-loads T2C calibration."""
        sim = create_execution_simulator()
        assert sim.config.symbol_slippage_bps == {
            "BTCUSDT": 9.5,
            "ETHUSDT": 12.8,
            "BNBUSDT": 11.6,
        }
        # Global default still 5.0 bps
        assert sim.config.base_slippage_bps == 5.0

    def test_explicit_override_takes_precedence(self):
        """Explicit overrides override auto-loaded calibration."""
        sim = create_execution_simulator(
            base_slippage_bps=8.0,
            symbol_slippage_bps={"BTCUSDT": 20.0},
        )
        assert sim.config.base_slippage_bps == 8.0
        assert sim.config.symbol_slippage_bps == {"BTCUSDT": 20.0}

    def test_per_symbol_vol_factor_loaded(self):
        """slippage_volatility_factor per symbol is loaded."""
        sim = create_execution_simulator()
        assert sim.config.symbol_slippage_vol_factor["BTCUSDT"] == 0.55
        assert sim.config.symbol_slippage_vol_factor["ETHUSDT"] == 0.65
        assert sim.config.symbol_slippage_vol_factor["BNBUSDT"] == 0.70


class TestSlippageComputation:
    """Verify _compute_slippage uses per-symbol values."""

    def test_btc_uses_calibrated_bps(self):
        """BTC orders use 9.5 bps base instead of default 5.0 bps."""
        sim = create_execution_simulator()
        book = OrderBookSnapshot(
            symbol="BTCUSDT",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            bid_price=100.0,
            bid_size=10.0,
            ask_price=100.1,
            ask_size=10.0,
            mid_price=100.05,
            spread=0.1,
            spread_bps=1.0,
            volume_24h=1_000_000,
            volatility=0.0,
            bar_duration_hours=1.0,
        )
        slippage = sim._compute_slippage(book, quantity=1.0, side=OrderSide.BUY)
        # With vol=0: slippage = base_bps(9.5) * (1+0) * (1 + (1.0/10.0)*0.5)
        # = 9.5 * 1.0 * 1.05 = 9.975 bps → 0.0009975
        assert abs(slippage - 9.5 / 10000 * 1.0 * 1.05) < 1e-8

    def test_unknown_symbol_falls_back_to_default(self):
        """Unknown symbol uses global base_slippage_bps (5.0)."""
        sim = create_execution_simulator(base_slippage_bps=5.0)
        book = OrderBookSnapshot(
            symbol="UNKNOWN",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            bid_price=100.0,
            bid_size=10.0,
            ask_price=100.1,
            ask_size=10.0,
            mid_price=100.05,
            spread=0.1,
            spread_bps=1.0,
            volume_24h=1_000_000,
            volatility=0.0,
            bar_duration_hours=1.0,
        )
        slippage = sim._compute_slippage(book, quantity=1.0, side=OrderSide.BUY)
        # With vol=0: slippage = 5.0 bps * 1.0 * 1.05 = 5.25 bps → 0.000525
        assert abs(slippage - 5.0 / 10000 * 1.0 * 1.05) < 1e-8

    def test_high_vol_asset_slippage_scales_up(self):
        """ETH has higher base_slippage + higher vol_factor → more slippage."""
        sim = create_execution_simulator()
        book_btc = OrderBookSnapshot(
            symbol="BTCUSDT",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            bid_price=100.0,
            bid_size=10.0,
            ask_price=100.1,
            ask_size=10.0,
            mid_price=100.05,
            spread=0.1,
            spread_bps=1.0,
            volume_24h=1_000_000,
            volatility=0.01,
            bar_duration_hours=1.0,
        )
        book_eth = OrderBookSnapshot(
            symbol="ETHUSDT",
            timestamp=__import__("datetime").datetime.now(__import__("datetime").UTC),
            bid_price=100.0,
            bid_size=10.0,
            ask_price=100.1,
            ask_size=10.0,
            mid_price=100.05,
            spread=0.1,
            spread_bps=1.0,
            volume_24h=1_000_000,
            volatility=0.01,
            bar_duration_hours=1.0,
        )
        slippage_btc = sim._compute_slippage(book_btc, quantity=1.0, side=OrderSide.BUY)
        slippage_eth = sim._compute_slippage(book_eth, quantity=1.0, side=OrderSide.BUY)
        assert slippage_eth > slippage_btc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
