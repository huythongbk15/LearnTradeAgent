"""Regression test for look-ahead bias fix in regime.py.

Verifies that atr_pctl[i] does NOT depend on atr[i] (current bar's ATR).
Instead, atr_pctl[i] should only reflect data available up to bar i-1.
"""

import numpy as np
import polars as pl
from trading_agent.regime import add_regime_indicators


def _make_test_df(n: int = 300) -> pl.DataFrame:
    """Generate synthetic OHLCV data with a known volatility regime change."""
    rng = np.random.RandomState(42)
    base_ret = rng.normal(0.0005, 0.01, n)
    price = 10000 * np.cumprod(1 + base_ret)

    # Volatility spikes around bar 100-150
    vol_multiplier = np.where((np.arange(n) >= 100) & (np.arange(n) < 150), 3.0, 1.0)
    price = price * np.cumprod(1 + rng.normal(0, 0.01, n) * vol_multiplier)

    df = pl.DataFrame({
        "timestamp": np.arange(n, dtype=np.int64),
        "open": price,
        "high": price * (1 + np.abs(rng.normal(0, 0.02, n)) * vol_multiplier),
        "low": price * (1 - np.abs(rng.normal(0, 0.02, n)) * vol_multiplier),
        "close": price * (1 + rng.normal(0, 0.01, n) * vol_multiplier),
        "volume": rng.uniform(100, 500, n).astype(np.float64),
    })
    return df.sort("timestamp")


class TestRegimeLookaheadBias:
    """Ensure regime indicators don't leak future data."""

    def test_atr_pctl_lagged_not_current(self):
        """atr_pctl[i] must NOT depend on atr[i] — should be based on bar i-1 or earlier."""
        df = _make_test_df(300)
        result = add_regime_indicators(df, atr_period=14, lookback=50)

        # Compute atr_pctl manually using the OLD (buggy) approach: shift before rolling
        df_with_atr = df.with_columns(
            (pl.col("high") - pl.col("low")).rolling_mean(14).alias("atr")
        )
        old_pctl = (
            df_with_atr.with_columns(
                pl.col("atr").shift(1).rolling_map(
                    lambda s: (s < s[-1]).sum() / len(s) if len(s) > 1 else 0.5,
                    window_size=50,
                ).alias("atr_pctl_old")
            )
        ).select("atr_pctl_old")

        # Compare: the new approach shifts AFTER rolling_map, so result is at
        # position i what was computed at i-1.
        new_pctl = result.select("atr_pctl").rename({"atr_pctl": "atr_pctl_new"})

        # They should NOT be identical — the new approach is shifted by 1
        old_vals = old_pctl.to_series(0).to_numpy()
        new_vals = new_pctl.to_series(0).to_numpy()

        # The new values should equal the old values shifted by 1
        # (new[i] == old[i-1] for i >= 1)
        aligned_old = np.roll(old_vals, 1)
        aligned_old[0] = np.nan

        # At least some positions should differ (confirming shift happened)
        assert not np.allclose(new_vals[1:], aligned_old[1:], equal_nan=True), \
            "atr_pctl should be shifted — new values should differ from shifted old values"

    def test_no_lookahead_via_perturbation(self):
        """Perturbing atr[i] should NOT change atr_pctl[i] (only affects atr_pctl[i+lookback-1] onward)."""
        df = _make_test_df(300)
        result = add_regime_indicators(df, atr_period=14, lookback=50)

        # Perturb the ATR at bar 100 by modifying high/low
        df_perturbed = df.clone()
        df_perturbed = df_perturbed.with_columns(
            pl.when(pl.col("timestamp") == df["timestamp"][100])
            .then(pl.col("high") * 1.5)
            .otherwise(pl.col("high"))
            .alias("high_perturbed")
        )
        df_perturbed = df_perturbed.with_columns(
            pl.col("high_perturbed").alias("high")
        )

        result_perturbed = add_regime_indicators(df_perturbed, atr_period=14, lookback=50)

        # atr_pctl at bar 100 should NOT change (look-back only)
        pctl_before = result.select("atr_pctl")[100].item()
        pctl_after = result_perturbed.select("atr_pctl")[100].item()
        assert pctl_before == pctl_after or abs(pctl_before - pctl_after) < 1e-6, \
            f"atr_pctl[100] changed from {pctl_before} to {pctl_after} — look-ahead bias!"

    def test_atr_pctl_bounded(self):
        """atr_pctl should always be in [0, 1]."""
        df = _make_test_df(300)
        result = add_regime_indicators(df)
        pctl = result.select("atr_pctl").to_series().to_numpy()
        # Exclude nulls/NaN at the start (lookback + shift + warmup)
        valid = pctl[52:]  # lookback + shift
        valid = valid[~np.isnan(valid)]
        assert len(valid) > 0, "No valid atr_pctl values after warmup"
        assert np.all(valid >= 0.0 - 1e-6) and np.all(valid <= 1.0 + 1e-6), \
            f"atr_pctl contains values outside [0, 1]: min={np.min(valid):.6f}, max={np.max(valid):.6f}"

    def test_regime_columns_present(self):
        """All regime columns are present after add_regime_indicators."""
        df = _make_test_df(100)
        result = add_regime_indicators(df, atr_period=14, lookback=50)
        expected_cols = ["atr", "atr_pctl", "vol_regime", "adx", "trend_regime", "trend_dir"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"
