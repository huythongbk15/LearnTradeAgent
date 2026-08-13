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

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


# ---------------------------------------------------------------------------
# Feature Store — parquet-backed factor cache
# ---------------------------------------------------------------------------
class FeatureStore:
    """
    Parquet-based feature cache with versioning.

    Each alpha factor is stored as:
        features/{symbol}/{alpha_name}/{version}.parquet

    Version = hash of input params.
    """

    def __init__(self, base_path: str = "features"):
        self.base = Path(base_path)
        self.base.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}  # in-memory L1

    def _key(self, symbol: str, alpha_name: str, version: str) -> str:
        return f"{symbol}/{alpha_name}/{version}"

    def get(self, symbol: str, alpha_name: str, params: dict | None = None) -> Any:
        version = self._version_hash(params)
        key = self._key(symbol, alpha_name, version)
        if key in self._cache:
            return self._cache[key]
        path = self.base / key.replace("/", os.sep) + ".parquet"
        if path.exists():
            try:
                import pandas as pd

                df = pd.read_parquet(path)
                self._cache[key] = df
                return df
            except Exception:
                return None
        return None

    def put(self, symbol: str, alpha_name: str, df, params: dict | None = None) -> str:
        version = self._version_hash(params)
        key = self._key(symbol, alpha_name, version)
        dirpath = self.base / symbol / alpha_name
        dirpath.mkdir(parents=True, exist_ok=True)
        path = dirpath / f"{version}.parquet"
        try:
            df.to_parquet(path, index=False)
        except Exception:
            # Fallback to CSV
            path = path.with_suffix(".csv")
            df.to_csv(path, index=False)
        self._cache[key] = df
        return version

    def list_alphas(self, symbol: str) -> list[str]:
        spath = self.base / symbol
        if not spath.exists():
            return []
        return [d.name for d in spath.iterdir() if d.is_dir()]

    def versions(self, symbol: str, alpha_name: str) -> list[str]:
        apath = self.base / symbol / alpha_name
        if not apath.exists():
            return []
        return [f.stem for f in apath.iterdir() if f.suffix in (".parquet", ".csv")]

    def _version_hash(self, params: dict | None) -> str:
        if not params:
            return "default"
        raw = json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()[:12]


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
# Alpha Evaluator — IC, Sharpe, turnover, decay
# ---------------------------------------------------------------------------
@dataclass
class AlphaReport:
    """Report for a single alpha factor."""

    name: str
    category: str
    ic_mean: float = 0.0
    ic_ir: float = 0.0  # IC / std(IC)
    sharpe: float = 0.0
    turnover: float = 0.0  # daily avg position changes
    decay_halflife: int = 0  # periods until IC halves
    monotonicity: float = 0.0  # 1.0 = perfectly monotonic
    correlation_with_others: dict = field(default_factory=dict)
    grade: str = ""  # A/B/C/D/F
    details: dict = field(default_factory=dict)


class AlphaEvaluator:
    """
    Evaluate alpha quality using:
    - Rank IC (Spearman correlation with forward returns)
    - IC Information Ratio
    - Turnover
    - Decay analysis
    - Monotonicity of quantile returns
    """

    def __init__(self, forward_periods: int = 5):
        self.forward_periods = forward_periods

    def evaluate(
        self,
        alpha_values: np.ndarray,
        forward_returns: np.ndarray,
        name: str = "",
        category: str = "",
    ) -> AlphaReport:
        report = AlphaReport(name=name, category=category)

        valid = ~(np.isnan(alpha_values) | np.isnan(forward_returns))
        if valid.sum() < 30:
            report.grade = "F"
            return report

        a = alpha_values[valid]
        f = forward_returns[valid]

        # Rank IC (Spearman)
        from scipy import stats as sp_stats

        ic, _ = sp_stats.spearmanr(a, f)
        report.ic_mean = float(ic) if not np.isnan(ic) else 0.0

        # IC IR
        # Rolling IC
        window = min(20, len(a) // 3)
        if window > 5:
            rolling_ic = []
            for i in range(0, len(a) - window, window):
                with np.errstate(invalid="ignore", divide="ignore"):
                    ic_win = sp_stats.spearmanr(a[i : i + window], f[i : i + window])[0]
                rolling_ic.append(ic_win)
            rolling_ic = [x for x in rolling_ic if not np.isnan(x)]
            if rolling_ic:
                report.ic_ir = report.ic_mean / (np.std(rolling_ic) + 1e-9)

        # Sharpe (long-short quintile)
        q = 5
        if len(a) >= q * 10:
            labels = np.argsort(np.argsort(a)) // (len(a) // q)
            q_rets = [f[labels == qi].mean() for qi in range(q)]
            ls_ret = q_rets[-1] - q_rets[0]
            ls_std = np.std([q_rets[-1], q_rets[0]])
            report.sharpe = (
                ls_ret / (ls_std + 1e-9) * math.sqrt(252 / self.forward_periods)
            )

        # Turnover
        alpha_sorted = np.argsort(a)
        report.turnover = float(np.mean(np.abs(np.diff(alpha_sorted)) / len(a)))

        # Decay
        report.decay_halflife = self._estimate_decay(a, f)

        # Monotonicity
        if len(a) >= q * 10:
            labels = np.argsort(np.argsort(a)) // (len(a) // q)
            q_rets = [f[labels == qi].mean() for qi in range(q)]
            mono = sum(1 for i in range(1, len(q_rets)) if q_rets[i] > q_rets[i - 1])
            report.monotonicity = mono / (len(q_rets) - 1)

        # Grade
        # Sharpe can be extreme for quintile returns; clamp to [-5, 5]
        clamped_sharpe = max(-5, min(5, report.sharpe))
        score = (
            abs(report.ic_mean) * 10
            + abs(report.ic_ir) * 2
            + abs(clamped_sharpe)
            + report.monotonicity
            - report.turnover * 5
        )
        if score > 2:
            report.grade = "A"
        elif score > 1:
            report.grade = "B"
        elif score > 0:
            report.grade = "C"
        elif score > -0.5:
            report.grade = "D"
        else:
            report.grade = "F"

        report.details = {
            "n_valid": int(valid.sum()),
            "ic_mean": round(report.ic_mean, 4),
            "ic_ir": round(report.ic_ir, 3),
            "sharpe": round(report.sharpe, 3),
            "turnover": round(report.turnover, 4),
            "decay_halflife": report.decay_halflife,
            "monotonicity": round(report.monotonicity, 3),
        }
        return report

    def _estimate_decay(self, alpha: np.ndarray, forward_returns: np.ndarray) -> int:
        """Estimate IC halflife by checking IC at increasing lags."""
        from scipy import stats as sp_stats

        max_lag = min(20, len(alpha) // 5)
        base_ic = sp_stats.spearmanr(alpha, forward_returns)[0]
        if np.isnan(base_ic) or abs(base_ic) < 0.01:
            return 0
        half_ic = abs(base_ic) / 2
        for lag in range(1, max_lag):
            a_lag = alpha[:-lag]
            f_lag = forward_returns[lag:]
            ic = sp_stats.spearmanr(a_lag, f_lag)[0]
            if not np.isnan(ic) and abs(ic) < half_ic:
                return lag
        return max_lag

    def correlation_matrix(
        self, alpha_values: dict[str, np.ndarray]
    ) -> dict[str, dict[str, float]]:
        """Pairwise IC correlation between alphas."""
        names = list(alpha_values.keys())
        corr = {}
        for n1 in names:
            corr[n1] = {}
            for n2 in names:
                valid = ~(np.isnan(alpha_values[n1]) | np.isnan(alpha_values[n2]))
                if valid.sum() > 10:
                    from scipy import stats as sp_stats

                    c, _ = sp_stats.spearmanr(
                        alpha_values[n1][valid], alpha_values[n2][valid]
                    )
                    corr[n1][n2] = round(float(c), 3) if not np.isnan(c) else 0.0
                else:
                    corr[n1][n2] = 0.0
        return corr


# ---------------------------------------------------------------------------
# AutoML Pipeline — search over alpha combos
# ---------------------------------------------------------------------------
class AutoMLPipeline:
    """
    Automated alpha combination search:
    1. Grid over alpha subsets (top N by IC)
    2. Equal-weight and IC-weight composites
    3. Select best by composite Sharpe / IC
    """

    def __init__(self, alpha_lib: AlphaLibrary, evaluator: AlphaEvaluator):
        self.lib = alpha_lib
        self.eval = evaluator

    def scan(
        self,
        df,
        target_col: str = "close",
        forward_periods: int = 5,
        max_alphas: int = 40,
        report_path: str = "alpha_reports",
    ) -> dict:
        """
        Compute all alphas, evaluate each, return top performers.
        Returns: { "alphas": [AlphaReport...], "top_10": [...], "best_combo": {...} }
        """

        Path(report_path).mkdir(parents=True, exist_ok=True)
        forward_ret = (
            df[target_col].pct_change(forward_periods).shift(-forward_periods).values
        )

        results = []
        alpha_values = {}
        available = self.lib.list_alphas()

        for alpha_info in available[:max_alphas]:
            name = alpha_info["name"]
            cat = alpha_info["category"]
            try:
                vals = self.lib.compute(name, df)
                if hasattr(vals, "values"):
                    vals = vals.values
                vals = np.array(vals, dtype=float)
                report = self.eval.evaluate(vals, forward_ret, name=name, category=cat)
                results.append(report)
                alpha_values[name] = vals
            except Exception:
                continue

        results.sort(key=lambda r: (r.grade, abs(r.ic_mean)), reverse=True)

        # Pairwise correlation
        corr_matrix = self.eval.correlation_matrix(alpha_values) if alpha_values else {}

        # Top 10
        top_10 = [
            {"name": r.name, "category": r.category, "grade": r.grade, **r.details}
            for r in results[:10]
        ]

        # Best composite (equal weight of top 5 uncorrelated)
        best_combo = self._find_best_combo(
            results, alpha_values, forward_ret, corr_matrix
        )

        # Save report
        report_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_alphas": len(results),
            "top_10": top_10,
            "best_combo": best_combo,
            "grade_distribution": {
                g: sum(1 for r in results if r.grade == g) for g in "ABCDF"
            },
        }
        report_file = os.path.join(
            report_path,
            f"alpha_scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json",
        )
        with open(report_file, "w") as f:
            json.dump(report_data, f, indent=2, default=str)

        return report_data

    def _find_best_combo(
        self,
        results: list[AlphaReport],
        alpha_values: dict[str, np.ndarray],
        forward_ret: np.ndarray,
        corr_matrix: dict,
    ) -> dict:
        """Find best 3-5 alpha combo (low correlation, high IC)."""
        from scipy import stats as sp_stats

        top = [r for r in results if r.grade in ("A", "B")][:10]
        if len(top) < 2:
            if results:
                top = results[:5]
            else:
                return {"names": [], "composite_sharpe": 0}

        # Greedy selection: pick least correlated with already-selected
        selected = [top[0].name]
        for _ in range(4):
            best_name = None
            best_score = -999
            for r in top:
                if r.name in selected:
                    continue
                # Score = IC - max_corr_with_selected
                max_corr = (
                    max(abs(corr_matrix.get(r.name, {}).get(s, 0)) for s in selected)
                    if selected
                    else 0
                )
                score = abs(r.ic_mean) * 10 - max_corr
                if score > best_score:
                    best_score = score
                    best_name = r.name
            if best_name:
                selected.append(best_name)

        # Build composite
        stack = [alpha_values[n] for n in selected if n in alpha_values]
        if not stack:
            return {"names": selected, "composite_ic": 0, "n_alphas": len(selected)}
        with np.errstate(invalid="ignore", divide="ignore"):
            combo_values = np.nanmean(stack, axis=0)
        if combo_values.size == 0 or not np.all(np.isfinite(combo_values)):
            # Không đủ dữ liệu alpha hợp lệ → trả 0 thay vì NaN
            return {"names": selected, "composite_ic": 0, "n_alphas": len(selected)}
        ic = (
            sp_stats.spearmanr(combo_values, forward_ret)[0]
            if len(combo_values) > 10
            else 0
        )

        return {
            "names": selected,
            "composite_ic": round(float(ic), 4) if not np.isnan(ic) else 0,
            "n_alphas": len(selected),
        }


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
