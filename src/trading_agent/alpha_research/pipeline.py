#!/usr/bin/env python3
"""
Alpha Research Pipeline — Feature Store + Alpha Library + AutoML.

Components:
1. FeatureStore — parquet-backed, compute + cache alpha factors
2. AlphaLibrary — registry of 40+ alpha factors (momentum, vol, microstructure, etc.)
3. AlphaEvaluator — IC, turnover, correlation, decay analysis
4. AutoMLPipeline — grid search over alpha combos + weighting
5. AlphaReport — JSON summary for each alpha's quality

Designed for daily cron: compute → evaluate → report → persist.
"""

from __future__ import annotations

import math
from typing import Callable

import numpy as np


# ---------------------------------------------------------------------------
# Feature Store - provenance-bound content-addressed artifacts
# ---------------------------------------------------------------------------
from .feature_store import (  # noqa: F401 -- compatibility re-exports
    FeatureArtifact,
    FeatureStore,
    FeatureStoreError,
)

# ---------------------------------------------------------------------------
# Alpha Library — 40+ factor implementations
# ---------------------------------------------------------------------------
class AlphaLibrary:
    """
    Registry of alpha factor functions.

    Each alpha: name → fn(df, **params) → pd.Series
    where df has columns: open, high, low, close, volume
    """

    def __init__(self):
        self._registry: dict[str, Callable] = {}

    def register(self, name: str, category: str = ""):
        def decorator(fn):
            fn._alpha_name = name
            fn._alpha_category = category
            self._registry[name] = fn
            return fn

        return decorator

    def compute(self, name: str, df, **params):
        if name not in self._registry:
            raise KeyError(
                f"Alpha '{name}' not found. Available: {list(self._registry.keys())}"
            )
        return self._registry[name](df, **params)

    def list_alphas(self, category: str = "") -> list[dict]:
        result = []
        for name, fn in self._registry.items():
            if not category or getattr(fn, "_alpha_category", "") == category:
                result.append(
                    {
                        "name": name,
                        "category": getattr(fn, "_alpha_category", ""),
                    }
                )
        return result

    @property
    def registry(self) -> dict[str, Callable]:
        return self._registry


def _make_library() -> AlphaLibrary:
    """Create and populate the default alpha library with 40+ factors."""
    lib = AlphaLibrary()

    # ── MOMENTUM ──────────────────────────────────────────────

    @lib.register("roc_5", "momentum")
    def roc_5(df, **kw):
        c = df["close"]
        return c.pct_change(5)

    @lib.register("roc_10", "momentum")
    def roc_10(df, **kw):
        return df["close"].pct_change(10)

    @lib.register("roc_20", "momentum")
    def roc_20(df, **kw):
        return df["close"].pct_change(20)

    @lib.register("momentum_5_20", "momentum")
    def momentum_5_20(df, **kw):
        return df["close"].pct_change(5) - df["close"].pct_change(20)

    @lib.register("momentum_20_60", "momentum")
    def momentum_20_60(df, **kw):
        return df["close"].pct_change(20) - df["close"].pct_change(60)

    @lib.register("acceleration", "momentum")
    def acceleration(df, **kw):
        roc = df["close"].pct_change(5)
        return roc - roc.shift(5)

    @lib.register("relative_strength", "momentum")
    def relative_strength(df, **kw):
        c = df["close"]
        return (c / c.rolling(60).min() - 1) * 100

    @lib.register("high_low_ratio", "momentum")
    def high_low_ratio(df, **kw):
        return (df["high"] - df["low"]) / df["close"]

    @lib.register("close_to_high", "momentum")
    def close_to_high(df, **kw):
        high_20 = df["high"].rolling(20).max()
        return (df["close"] - high_20) / high_20

    @lib.register("close_to_low", "momentum")
    def close_to_low(df, **kw):
        low_20 = df["low"].rolling(20).min()
        return (df["close"] - low_20) / low_20

    # ── VOLATILITY ────────────────────────────────────────────

    @lib.register("realized_vol_10", "volatility")
    def realized_vol_10(df, **kw):
        return df["close"].pct_change().rolling(10).std() * math.sqrt(252)

    @lib.register("realized_vol_20", "volatility")
    def realized_vol_20(df, **kw):
        return df["close"].pct_change().rolling(20).std() * math.sqrt(252)

    @lib.register("vol_ratio", "volatility")
    def vol_ratio(df, **kw):
        short_vol = df["close"].pct_change().rolling(5).std()
        long_vol = df["close"].pct_change().rolling(20).std()
        return short_vol / (long_vol + 1e-9)

    @lib.register("parkinson_vol", "volatility")
    def parkinson_vol(df, **kw):
        hl_ratio = np.log(df["high"] / df["low"])
        return np.sqrt(
            hl_ratio.rolling(20).apply(
                lambda x: np.mean(x**2) / (4 * math.log(2)), raw=True
            )
        )

    @lib.register("garman_klass_vol", "volatility")
    def garman_klass_vol(df, **kw):
        o, h, low_, c = df["open"], df["high"], df["low"], df["close"]
        gk = 0.5 * np.log(h / low_) ** 2 - (2 * math.log(2) - 1) * np.log(c / o) ** 2
        return np.sqrt(gk.rolling(20).mean())

    @lib.register("vol_of_vol", "volatility")
    def vol_of_vol(df, **kw):
        vol = df["close"].pct_change().rolling(20).std()
        return vol.rolling(20).std()

    @lib.register("downside_vol", "volatility")
    def downside_vol(df, **kw):
        rets = df["close"].pct_change()
        neg = rets.where(rets < 0, 0)
        return neg.rolling(20).std() * math.sqrt(252)

    @lib.register("max_drawdown_20", "volatility")
    def max_drawdown_20(df, **kw):
        c = df["close"].rolling(20)
        peak = c.max()
        return (df["close"] - peak) / peak

    @lib.register("range_ratio", "volatility")
    def range_ratio(df, **kw):
        rng = df["high"] - df["low"]
        avg_rng = rng.rolling(20).mean()
        return rng / (avg_rng + 1e-9)

    @lib.register("intraday_vol", "volatility")
    def intraday_vol(df, **kw):
        return (df["high"] - df["low"]) / df["close"]

    # ── VOLUME ────────────────────────────────────────────────

    @lib.register("volume_sma_ratio", "volume")
    def volume_sma_ratio(df, **kw):
        return df["volume"] / df["volume"].rolling(20).mean()

    @lib.register("obv_slope", "volume")
    def obv_slope(df, **kw):
        direction = np.sign(df["close"].diff())
        obv = (direction * df["volume"]).cumsum()
        return obv.rolling(10).apply(
            lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) == 10 else 0,
            raw=True,
        )

    @lib.register("volume_price_trend", "volume")
    def volume_price_trend(df, **kw):
        vpt = (df["close"].pct_change() * df["volume"]).cumsum()
        return vpt.pct_change(10)

    @lib.register("volume_weighted_momentum", "volume")
    def volume_weighted_momentum(df, **kw):
        vwap = (df["close"] * df["volume"]).rolling(10).sum() / df["volume"].rolling(
            10
        ).sum()
        return df["close"] / vwap - 1

    @lib.register("volume_divergence", "volume")
    def volume_divergence(df, **kw):
        price_roc = df["close"].pct_change(10)
        vol_roc = df["volume"].pct_change(10)
        return price_roc - vol_roc

    @lib.register("accumulation_distribution", "volume")
    def accumulation_distribution(df, **kw):
        mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (
            df["high"] - df["low"] + 1e-9
        )
        ad = (mfm * df["volume"]).cumsum()
        return ad.pct_change(10)

    @lib.register("volume_cv", "volume")
    def volume_cv(df, **kw):
        vol = df["volume"].rolling(20)
        return vol.std() / (vol.mean() + 1e-9)

    @lib.register("dollar_volume_20", "volume")
    def dollar_volume_20(df, **kw):
        return (df["close"] * df["volume"]).rolling(20).sum()

    # ── TECHNICAL ─────────────────────────────────────────────

    @lib.register("rsi_14", "technical")
    def rsi_14(df, **kw):
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    @lib.register("bollinger_position", "technical")
    def bollinger_position(df, period=20, std_mult=2, **kw):
        sma = df["close"].rolling(period).mean()
        std = df["close"].rolling(period).std()
        upper = sma + std_mult * std
        lower = sma - std_mult * std
        return (df["close"] - lower) / (upper - lower + 1e-9)

    @lib.register("macd_histogram", "technical")
    def macd_histogram(df, **kw):
        ema12 = df["close"].ewm(span=12).mean()
        ema26 = df["close"].ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()
        return macd - signal

    @lib.register("adx_14", "technical")
    def adx_14(df, **kw):
        plus_dm = df["high"].diff()
        minus_dm = -df["low"].diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                abs(df["high"] - df["close"].shift()),
                abs(df["low"] - df["close"].shift()),
            ),
        )
        atr14 = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / (atr14 + 1e-9)
        minus_di = 100 * minus_dm.rolling(14).mean() / (atr14 + 1e-9)
        dx = abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9) * 100
        return dx.rolling(14).mean()

    @lib.register("stoch_rsi", "technical")
    def stoch_rsi(df, **kw):
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        rsi_min = rsi.rolling(14).min()
        rsi_max = rsi.rolling(14).max()
        return (rsi - rsi_min) / (rsi_max - rsi_min + 1e-9)

    @lib.register("williams_r", "technical")
    def williams_r(df, **kw):
        hh = df["high"].rolling(14).max()
        ll = df["low"].rolling(14).min()
        return (hh - df["close"]) / (hh - ll + 1e-9) * -100

    @lib.register("cci_20", "technical")
    def cci_20(df, **kw):
        tp = (df["high"] + df["low"] + df["close"]) / 3
        sma = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.mean(abs(x - np.mean(x))), raw=True)
        return (tp - sma) / (0.015 * mad + 1e-9)

    @lib.register("mfi_14", "technical")
    def mfi_14(df, **kw):
        tp = (df["high"] + df["low"] + df["close"]) / 3
        mf = tp * df["volume"]
        pos_mf = mf.where(tp > tp.shift(), 0).rolling(14).sum()
        neg_mf = mf.where(tp < tp.shift(), 0).rolling(14).sum()
        mfr = pos_mf / (neg_mf + 1e-9)
        return 100 - (100 / (1 + mfr))

    # ── MICROSTRUCTURE ────────────────────────────────────────

    @lib.register("amihud_illiquidity", "microstructure")
    def amihud_illiquidity(df, **kw):
        ret = abs(df["close"].pct_change())
        dollar_vol = df["close"] * df["volume"]
        return (ret / (dollar_vol + 1e-9)).rolling(20).mean()

    @lib.register("kyle_lambda", "microstructure")
    def kyle_lambda(df, **kw):
        sign_vol = np.sign(df["close"].diff()) * df["volume"]
        ret = df["close"].pct_change()
        return ret.rolling(20).apply(
            lambda x: (
                np.polyfit(sign_vol.iloc[-20:].values, x.values, 1)[0]
                if len(x) == 20
                else 0
            ),
            raw=True,
        )

    @lib.register("spread_estimate", "microstructure")
    def spread_estimate(df, **kw):
        """Roll's spread estimator: 2 * sqrt(-cov(ret_t, ret_{t-1}))"""
        ret = df["close"].pct_change()
        cov = ret.rolling(20).apply(
            lambda x: np.cov(x[1:], x[:-1])[0, 1] if len(x) > 1 else 0, raw=True
        )
        return 2 * np.sqrt(np.maximum(-cov, 0))

    @lib.register("trade_intensity", "microstructure")
    def trade_intensity(df, **kw):
        """Volume per unit price range — higher = more trades per tick."""
        rng = df["high"] - df["low"]
        return df["volume"] / (rng + 1e-9)

    @lib.register("price_impact", "microstructure")
    def price_impact(df, **kw):
        """Kyle's lambda proxy: return per unit volume."""
        ret = abs(df["close"].pct_change())
        return ret / (df["volume"] + 1e-9)

    # ── REGIME ────────────────────────────────────────────────

    @lib.register("trend_strength", "regime")
    def trend_strength(df, **kw):
        c = df["close"]
        sma20 = c.rolling(20).mean()
        sma60 = c.rolling(60).mean()
        return (sma20 - sma60) / (c.rolling(20).std() + 1e-9)

    @lib.register("regime_volatility", "regime")
    def regime_volatility(df, **kw):
        vol = df["close"].pct_change().rolling(20).std()
        vol_200 = df["close"].pct_change().rolling(200).std()
        return vol / (vol_200 + 1e-9)

    @lib.register("breadth_proxy", "regime")
    def breadth_proxy(df, **kw):
        """Above/below SMA as breadth proxy for single asset."""
        above_20 = (df["close"] > df["close"].rolling(20).mean()).astype(float)
        above_60 = (df["close"] > df["close"].rolling(60).mean()).astype(float)
        return (above_20 + above_60) / 2

    return lib


# ---------------------------------------------------------------------------
# Alpha evaluation and nested walk-forward selection
# ---------------------------------------------------------------------------
from .methodology import (  # noqa: F401 -- compatibility re-exports
    AlphaEvaluation,
    AlphaEvaluator,
    AlphaReport,
    AutoMLPipeline,
    ChronologicalFold,
    FactorTransform,
    ReturnSeries,
    apply_factor_transform,
    fit_factor_transform,
    make_chronological_folds,
    periods_per_year_for_timeframe,
)

# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import pandas as pd

    print("=" * 60)
    print("ALPHA RESEARCH PIPELINE — DEMO")
    print("=" * 60)

    # Generate synthetic data
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2024-01-01", periods=n, freq="1h")
    close = 50000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 50,
            "high": close + abs(np.random.randn(n) * 100),
            "low": close - abs(np.random.randn(n) * 100),
            "close": close,
            "volume": np.random.exponential(100, n) * 1000,
        },
        index=dates,
    )

    # 1. Create library
    lib = _make_library()
    alphas = lib.list_alphas()
    print(f"\nAlpha Library: {len(alphas)} factors")
    cats = {}
    for a in alphas:
        cats[a["category"]] = cats.get(a["category"], 0) + 1
    for c, n in sorted(cats.items()):
        print(f"  {c}: {n}")

    # 2. Feature Store
    store = FeatureStore(base_path="features")
    print(f"\nFeature Store: {store.base}")

    # 3. Evaluate all
    evaluator = AlphaEvaluator(forward_periods=5)
    automl = AutoMLPipeline(lib, evaluator)

    print("\nRunning Alpha Scan...")
    report = automl.scan(df, report_path="alpha_reports")

    print(f"\nTotal alphas: {report['total_alphas']}")
    print(f"Grade distribution: {report['grade_distribution']}")
    print("\nTop 10:")
    for a in report["top_10"][:10]:
        print(
            f"  {a['name']:30s} | {a['category']:15s} | IC={a['ic_mean']:+.4f} | IR={a['ic_ir']:.2f} | Grade={a['grade']}"
        )
    print(f"\nBest combo: {report['best_combo']}")
    print("\nDone!")
