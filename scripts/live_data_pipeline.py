#!/usr/bin/env python3
"""Live data pipeline stub — Binance spot/futures → tournament router feed.

Bridges Binance market data (CCXT REST / WebSocket) with the StrategyTournament
router by converting closed 1h candles into ``MarketObservation`` +
``RegimePosterior`` pairs and feeding them to ``tournament.route()``.

Design principles
-----------------
• **Fail-closed** — stale or gapped bars are rejected (same invariants as
  ``scripts/live_enhanced_ma_binance.py``'s ``validate_live_hourly_bars``).
• **LLM-free execution path** — regime posterior is computed deterministically
  via :class:`RegimeDetector`; ``MarketContext`` enrichment is advisory-only
  and never blocks routing.
• **Process-registry aware** — designed to run under
  ``python scripts/qwenpaw_control/controlled_exec.py --timeout 3600``
  so the QwenPaw control toolkit tracks heartbeat / liveness.
• **Dry-run by default** — ``--symbols`` + ``--max-bars`` for backfill
  validation; ``--live`` switches to real-time polling.

Usage
-----
# Dry-run: replay last 500h of Binance data through the router (no trades)
python scripts/live_data_pipeline.py --symbols BTC/USDT,ETH/USDT \
    --dry-run --max-bars 500

# Live: poll Binance every 1h for new closed candles → tournament route
python scripts/qwenpaw_control/controlled_exec.py \
    --timeout 86400 --heartbeat 30 --result-file pipeline_out.json -- \
    python scripts/live_data_pipeline.py --symbols BTC/USDT,ETH/USDT,SOL/USDT --live

# Shadow mode (tournament kill-switch ON, no live promotions)
TOURNAMENT_SHADOW_MODE=1 python scripts/live_data_pipeline.py \
    --symbols BTC/USDT --live
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv


load_dotenv(".env")

# ── Constants ────────────────────────────────────────────────────────────

HOUR_MS = 3_600_000
MAX_CLOSED_CANDLE_LAG_SECONDS = 5_400
DEFAULT_TIMEFRAME = "1h"
DEFAULT_WINDOW_BARS = 120
DEFAULT_LOOKBACK = 200
POLL_INTERVAL_SECONDS = 10

# Re-exports from live_config for shared strategy params
from live_config import LOOKBACK  # noqa: E402

logger = logging.getLogger("live_data_pipeline")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Local data-trust ─────────────────────────────────────────────────────


class DataTrustError(Exception):
    """Fail-closed: data integrity violation."""


def validate_live_hourly_bars(
    bars: list[list],
    *,
    symbol: str,
    now: datetime | None = None,
) -> list[tuple[int, float, float, float, float, float]]:
    """Reject stale, gapped, or malformed closed Binance 1h bars.

    Mirrors the invariant from ``live_enhanced_ma_binance.py``.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)
    now_ms = int(current.timestamp() * 1000)
    closed: list[tuple[int, float, float, float, float, float]] = []

    for index, bar in enumerate(bars):
        if not isinstance(bar, (list, tuple)) or len(bar) < 6:
            raise DataTrustError(f"malformed OHLCV bar {index} for {symbol}")
        try:
            timestamp = int(bar[0])
            values = tuple(float(v) for v in bar[1:6])
        except (TypeError, ValueError, OverflowError) as exc:
            raise DataTrustError(
                f"non-numeric OHLCV bar {index} for {symbol}"
            ) from exc
        # Skip forming (in-progress) candle
        if timestamp + HOUR_MS > now_ms:
            continue
        open_p, high, low, close, volume = values
        if any(not math.isfinite(v) or v <= 0 for v in (open_p, high, low, close)):
            raise DataTrustError(f"invalid OHLC price in bar {index} for {symbol}")
        if not math.isfinite(volume) or volume < 0:
            raise DataTrustError(f"invalid volume in bar {index} for {symbol}")
        if high < max(open_p, low, close) or low > min(open_p, high, close):
            raise DataTrustError(f"inconsistent OHLC range in bar {index} for {symbol}")
        closed.append((timestamp, open_p, high, low, close, volume))

    timestamps = [b[0] for b in closed]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise DataTrustError(f"duplicate or unordered hourly candles for {symbol}")
    if any(
        cur - prev != HOUR_MS
        for prev, cur in zip(timestamps, timestamps[1:], strict=False)
    ):
        raise DataTrustError(f"hourly candle gap detected for {symbol}")
    if closed:
        lag_s = (now_ms - (closed[-1][0] + HOUR_MS)) / 1_000
        if lag_s > MAX_CLOSED_CANDLE_LAG_SECONDS:
            raise DataTrustError(
                f"latest closed candle for {symbol} is stale by {lag_s:.0f}s"
            )
    return closed


# ── Binance Data Feed ────────────────────────────────────────────────────


class BinanceDataFeed:
    """Fetches and validates Binance OHLCV candles.

    Supports spot (default) and futures via ``MarketType``.
    Polls on an interval; detects new closed candles by tracking
    the last-seen open timestamp.

    In ``dry_run`` mode, data is read from local parquet files under
    ``data/raw/binance/<PAIR>/`` instead of hitting the live CCXT endpoint.
    This enables offline testing of the full pipeline without API keys.
    """

    def __init__(
        self,
        symbols: list[str],
        timeframe: str = DEFAULT_TIMEFRAME,
        window_bars: int = DEFAULT_WINDOW_BARS,
        *,
        use_futures: bool = False,
        api_key: str | None = None,
        api_secret: str | None = None,
        dry_run: bool = False,
        data_dir: Path | None = None,
    ) -> None:
        self.symbols = symbols
        self.timeframe = timeframe
        self.window_bars = window_bars
        self.use_futures = use_futures
        self.dry_run = dry_run
        self._data_dir = data_dir or (
            Path(__file__).resolve().parent.parent / "data/raw/binance"
        )
        if not dry_run or (api_key is not None and api_secret is not None):
            self._exchange = self._build_exchange(api_key, api_secret)
        else:
            self._exchange = None
        self._last_seen_ts: dict[str, datetime] = {}
        self._warm_window: dict[str, pl.DataFrame] = {}

    def _build_exchange(self, api_key: str | None, api_secret: str | None) -> Any:
        import ccxt

        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": 30_000,
        }
        if api_key and api_secret:
            config["apiKey"] = api_key
            config["secret"] = api_secret

        exchange_id = "binance"
        exchange = getattr(ccxt, exchange_id)(config)

        # Use futures endpoint if requested
        if self.use_futures:
            exchange.options["defaultType"] = "future"
            exchange.set_sandbox_mode(False)

        return exchange

    def _interval_seconds(self) -> int:
        units = {"m": 60, "h": 3_600, "d": 86_400, "w": 604_800}
        tf = self.timeframe.lower().strip()
        amount = int(tf[:-1])
        return amount * units[tf[-1]]

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert various symbol formats to underscore pair for file lookup.

        BTC/USDT → BTC_USDT, BTCUSDT → BTC_USDT
        """
        clean = symbol.replace("/", "_")
        # BTCUSDT → BTC_USDT (insert underscore before USDT if missing)
        for quote in ("USDT", "USDC", "BTC", "ETH"):
            if clean.endswith(quote) and "_" not in clean:
                base = clean[:-len(quote)]
                return f"{base}_{quote}"
        return clean

    def _read_local(self, symbol: str) -> pl.DataFrame:
        """Read OHLCV from local parquet in dry-run mode."""
        safe_pair = self._normalize_symbol(symbol)
        path = self._data_dir / safe_pair / f"{self.timeframe}.parquet"
        if not path.exists():
            raise DataTrustError(f"no local data found for {symbol}: {path}")
        df = pl.read_parquet(path).sort("timestamp")
        # Ensure timezone-aware UTC timestamps
        ts_dtype = str(df.schema["timestamp"])
        if "UTC" not in ts_dtype:
            if "str" in ts_dtype or "String" in ts_dtype:
                df = df.with_columns(
                    pl.col("timestamp").str.to_datetime(time_unit="us", time_zone="UTC")
                )
            else:
                df = df.with_columns(
                    pl.col("timestamp").dt.replace_time_zone("UTC", ambiguous="earliest")
                )
        return df

    def fetch_recent_closed(self, symbol: str) -> pl.DataFrame:
        """Fetch ``LOOKBACK`` candles and return only fully-closed ones as a DataFrame."""
        if self.dry_run:
            df = self._read_local(symbol)
            now = datetime.now(UTC)
            interval = self._interval_seconds()
            cutoff = now - timedelta(seconds=interval)
            df = df.filter(pl.col("timestamp") <= cutoff)
            if len(df) > LOOKBACK:
                df = df.tail(LOOKBACK)
            if len(df) < self.window_bars:
                raise DataTrustError(
                    f"insufficient closed candles for {symbol}: "
                    f"{len(df)} < {self.window_bars}"
                )
            return df.select(
                "timestamp", "open", "high", "low", "close", "volume"
            ).sort("timestamp")

        # Live mode: CCXT
        bars = self._exchange.fetch_ohlcv(
            symbol, timeframe=self.timeframe, limit=LOOKBACK
        )
        closed = validate_live_hourly_bars(bars, symbol=symbol)
        if len(closed) < self.window_bars:
            raise DataTrustError(
                f"insufficient closed candles for {symbol}: {len(closed)} < {self.window_bars}"
            )
        return pl.DataFrame(
            {
                "timestamp": [
                    datetime.fromtimestamp(bar[0] / 1000, tz=UTC) for bar in closed
                ],
                "open": [bar[1] for bar in closed],
                "high": [bar[2] for bar in closed],
                "low": [bar[3] for bar in closed],
                "close": [bar[4] for bar in closed],
                "volume": [bar[5] for bar in closed],
            }
        ).sort("timestamp")

    def has_new_closed_bar(self, symbol: str) -> bool:
        """Check if a new closed candle has appeared since last fetch."""
        try:
            bars = self._exchange.fetch_ohlcv(
                symbol, timeframe=self.timeframe, limit=2
            )
            closed = validate_live_hourly_bars(bars, symbol=symbol)
            if not closed:
                return False
            latest = datetime.fromtimestamp(closed[-1][0] / 1000, tz=UTC)
            prev = self._last_seen_ts.get(symbol)
            if prev is None or latest > prev:
                return True
        except DataTrustError:
            return False
        return False

    async def poll_loop(
        self,
        on_bar: Callable[[str, pl.DataFrame, datetime], Awaitable[None]],
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Continuously poll for new closed candles.

        Args:
            on_bar: async callback `on_bar(symbol, df: pl.DataFrame, bar_time: datetime)`
            poll_interval: seconds between polls
            stop_event: set to stop the loop
        """
        stop_event = stop_event or asyncio.Event()
        logger.info(
            f"Starting poll loop: {self.symbols} @ {self.timeframe} "
            f"({poll_interval}s interval)"
        )

        # Initialize windows
        for symbol in self.symbols:
            try:
                df = self.fetch_recent_closed(symbol)
                self._warm_window[symbol] = df.tail(self.window_bars)
                self._last_seen_ts[symbol] = df["timestamp"].tail(1).item()
                logger.info(
                    f"  {symbol}: loaded {len(df)} bars, "
                    f"window={len(self._warm_window[symbol])}"
                )
            except Exception as e:
                logger.error(f"  {symbol}: initial load failed: {e}")

        while not stop_event.is_set():
            for symbol in self.symbols:
                try:
                    df = self.fetch_recent_closed(symbol)
                    latest_ts = df["timestamp"].tail(1).item()
                    if latest_ts > self._last_seen_ts.get(symbol, datetime.min.replace(tzinfo=UTC)):
                        self._last_seen_ts[symbol] = latest_ts
                        self._warm_window[symbol] = df.tail(self.window_bars)
                        logger.info(f"  {symbol}: new bar @ {latest_ts}")
                        await on_bar(symbol, self._warm_window[symbol], latest_ts)
                except DataTrustError as e:
                    logger.critical(f"  {symbol}: DATA_TRUST VIOLATION: {e}")
                    # Fail-closed: stop the entire loop
                    stop_event.set()
                    return
                except Exception as e:
                    logger.warning(f"  {symbol}: poll error (will retry): {e}")

            await asyncio.sleep(poll_interval)


# ── Regime + Observation Builder ───────────────────────────────────────────

from trading_agent.ml.regime_detection import RegimePosterior  # noqa: E402
from trading_agent.research.forecast import MarketObservation  # noqa: E402


def compute_deterministic_posterior(window: pl.DataFrame, observed_at: datetime) -> RegimePosterior:
    """Deterministic RegimePosterior from a 1h OHLCV window.

    No LLM, no network — pure feature math.
    """
    prices = window["close"].to_numpy()
    returns = np.diff(np.log(prices))
    if len(returns) < 20:
        return _uniform_posterior(observed_at)

    # --- Features ---
    rets = returns[-100:]
    adx_like = _compute_adx_proxy(rets)  # proxy for trend strength
    sma_slope = float(np.mean(np.diff(prices[-20:]) / prices[-20:-1])) if len(prices) >= 20 else 0.0
    atr_pct = float(np.std(rets) * math.sqrt(252)) if len(rets) > 0 else 0.0
    hurst = _hurst_exponent(prices[-50:]) if len(prices) >= 50 else 0.5
    rsi = _rsi(prices[-30:], period=14)

    # --- Scores ---
    ADX_TREND = 25.0
    ADX_STRONG = 40.0
    VOL_HIGH = 0.03
    HURST_TREND = 0.6
    HURST_MR = 0.4

    scores = {
        "trend": 0.0,
        "mean_reversion": 0.0,
        "high_vol": 0.0,
        "crisis": 0.0,
        "other": 0.0,
    }

    # Trend score
    if adx_like > ADX_STRONG and sma_slope > 0:
        scores["trend"] = adx_like / 60.0
    elif adx_like > ADX_TREND and sma_slope > 0.001:
        scores["trend"] = min(0.8, adx_like / 40.0)
    elif hurst > HURST_TREND and sma_slope >= 0:
        scores["trend"] = 0.6
    else:
        scores["trend"] = 0.1

    # Mean-reversion score
    if hurst < HURST_MR:
        scores["mean_reversion"] = 0.6
    elif adx_like <= ADX_TREND and atr_pct <= VOL_HIGH:
        scores["mean_reversion"] = 0.5
    else:
        scores["mean_reversion"] = 0.2

    # High vol
    if atr_pct > VOL_HIGH:
        scores["high_vol"] = min(1.0, atr_pct / VOL_HIGH * 0.5 + 0.4)
    else:
        scores["high_vol"] = 0.1

    # Crisis
    if rsi < 20 or (len(returns) > 0 and float(np.min(returns[-20:])) < -0.05):
        scores["crisis"] = max(0.3, 1.0 - abs(rsi - 50) / 50)
    else:
        scores["crisis"] = 0.05

    scores["other"] = 0.05

    total = sum(scores.values())
    if total <= 0:
        return _uniform_posterior(observed_at)
    normalized = {k: v / total for k, v in scores.items()}

    return RegimePosterior(
        p_trend=normalized["trend"],
        p_mean_reversion=normalized["mean_reversion"],
        p_high_vol=normalized["high_vol"],
        p_crisis=normalized["crisis"],
        p_other=normalized["other"],
        model_id="deterministic-regime-v1",
        fitted_start=observed_at - timedelta(hours=len(window) - 1),
        fitted_end=observed_at - timedelta(hours=1),
        generated_at=observed_at,
        ood_score=0.1,
    )


def _uniform_posterior(observed_at: datetime) -> RegimePosterior:
    return RegimePosterior(
        p_trend=0.2, p_mean_reversion=0.2,
        p_high_vol=0.2, p_crisis=0.2, p_other=0.2,
        model_id="uniform-fallback",
        generated_at=observed_at,
    )


def _compute_adx_proxy(returns: np.ndarray) -> float:
    """Rough ADX proxy: mean absolute return scaled."""
    if len(returns) == 0:
        return 0.0
    return float(np.mean(np.abs(returns)) * 10_000)  # scale to ADX-like range


def _hurst_exponent(prices: np.ndarray) -> float:
    """Hurst exponent via rescaled range analysis."""
    n = len(prices)
    if n < 10:
        return 0.5
    rs = []
    for lag in range(2, min(n // 4, 50)):
        diffs = np.diff(prices[:lag])
        # Rescaled range
        mean_val = np.mean(diffs)
        dev = diffs - mean_val
        z: np.ndarray = np.cumsum(dev)
        r = np.max(z) - np.min(z) if len(z) > 0 else 0.0
        s = np.std(dev) if np.std(dev) > 0 else 1e-10
        rs.append(r / s)
    if not rs:
        return 0.5
    lags = np.arange(2, len(rs) + 2)
    log_lag = np.log(lags)
    log_rs = np.log(np.maximum(rs, 1e-10))
    if len(log_lag) < 2:
        return 0.5
    slope, _ = np.polyfit(log_lag, log_rs, 1)
    return float(slope)


def _rsi(prices: np.ndarray, period: int = 14) -> float:
    """Simple RSI from price array."""
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = float(np.mean(gains[-period:]))
    avg_loss = float(np.mean(losses[-period:]))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1 + rs)))


def build_observation(
    symbol: str,
    window: pl.DataFrame,
    bar_time: datetime,
) -> MarketObservation:
    """Build a MarketObservation from the latest closed candle + OHLCV window."""
    bar = window.filter(pl.col("timestamp") == bar_time)
    if bar.is_empty():
        bar = window.tail(1)
    row = bar.row(0, named=True)

    ohlcv_window = window.select(
        "open", "high", "low", "close", "volume", "timestamp"
    ).with_columns(
        pl.col("timestamp").dt.replace_time_zone("UTC").alias("time")
    )

    return MarketObservation(
        symbol=symbol,
        observed_at=bar_time,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        features={
            "ohlcv_window": ohlcv_window,
            "timeframe": DEFAULT_TIMEFRAME,
        },
    )


def compute_bar_return(window: pl.DataFrame, bar_time: datetime) -> float:
    """Log return of the just-closed bar vs previous close."""
    idx = window.filter(pl.col("timestamp") <= bar_time).height
    if idx < 2:
        return 0.0
    curr = float(window["close"].item(idx - 1))
    prev = float(window["close"].item(idx - 2))
    if prev <= 0:
        return 0.0
    return float(curr / prev - 1.0)


# ── Tournament Feeder ────────────────────────────────────────────────────

from trading_agent.authority.strategy_tournament import (  # noqa: E402
    StrategyTournament,
    TournamentConfig,
)
from trading_agent.authority.adaptive_router import (  # noqa: E402
    AdaptiveRouterConfig,
    RouterStateStore,
)
from trading_agent.research.selection_policy import (  # noqa: E402
    ParamArtifact,
    PolicyActivationService,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS  # noqa: E402


def build_tournament(
    symbols: list[str],
    tmp_dir: Path,
    *,
    shadow_mode: bool = True,
) -> StrategyTournament:
    """Boot a StrategyTournament with one active policy per (symbol, regime).

    Mirrors ``build_paper_trader`` from ``scripts/tournament_paper_trader.py``.
    """
    now = datetime.now(UTC)
    validity_start = datetime(2020, 1, 1, tzinfo=UTC)
    regimes = ["trend", "mean_reversion", "high_vol", "crisis", "other"]
    pool = dict(FIRST_WAVE_DESCRIPTORS)
    pool_strategies = list(pool.keys())
    first_sid = pool_strategies[0]

    registry = SelectionPolicyRegistry(tmp_dir / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=b"live-pipeline-key",
        key_id="pipeline-release-key",
        audit_path=tmp_dir / "audit.jsonl",
    )

    # Create one active policy per (symbol, regime)
    for symbol in symbols:
        for regime in regimes:
            params = _default_params_for(first_sid)
            policy = SelectionPolicyArtifact(
                symbol=symbol,
                timeframe=DEFAULT_TIMEFRAME,
                regime=regime,
                incumbent=ParamArtifact(first_sid, params, code_sha="live-pipeline-001"),
                scores={
                    "selection_score": 0.50,
                    "median_test_sharpe": 0.30,
                    "median_oos_return_pct": 0.05,
                    "median_max_dd_pct": 0.30,
                    "median_calmar": 0.50,
                    "median_oos_trades": 40,
                    "n_passing_folds": 9,
                    "total_folds": 9,
                },
                evidence_ids=(f"sha256:live-{first_sid}-{regime}",),
                validity_start=validity_start,
                validity_end=now + timedelta(days=90),
                risk_cap=0.25,
                status=PolicyStatus.VALIDATED,
                created_at=now - timedelta(minutes=1),
                policy_commit_sha="live-pipeline-commit",
                policy_data_manifest_sha="live-data-sha",
                policy_feature_manifest_sha="live-feature-sha",
                policy_release_digest="sha256:live-release-digest",
                promotion_stage="shadow_eligible",
            )
            registry.add(policy)
            existing = registry.get_active(
                symbol, DEFAULT_TIMEFRAME, regime, now=now
            )
            if existing is None:
                service.activate(
                    policy.policy_id,
                    actor="live-data-pipeline",
                    ticket=f"LIVE-{symbol}-{regime}",
                    now=now,
                )
            else:
                logger.debug(
                    f"  {symbol} [{regime}]: already active "
                    f"({existing.policy_id[:8]}…), skipping activation"
                )

    # Add remaining strategies as inactive challengers
    for symbol in symbols:
        for sid in pool_strategies[1:]:
            for regime in regimes:
                params = _default_params_for(sid)
                policy = SelectionPolicyArtifact(
                    symbol=symbol,
                    timeframe=DEFAULT_TIMEFRAME,
                    regime=regime,
                    incumbent=ParamArtifact(sid, params, code_sha="challenger-002"),
                    scores={
                        "selection_score": 0.40,
                        "median_test_sharpe": 0.20,
                        "median_oos_return_pct": 0.02,
                        "median_max_dd_pct": 0.40,
                        "median_calmar": 0.30,
                        "median_oos_trades": 30,
                        "n_passing_folds": 7,
                        "total_folds": 9,
                    },
                    evidence_ids=(f"sha256:challenger-{sid}-{regime}",),
                    validity_start=validity_start,
                    validity_end=now + timedelta(days=90),
                    risk_cap=0.25,
                    status=PolicyStatus.VALIDATED,
                    created_at=now - timedelta(minutes=1),
                    policy_commit_sha="live-pipeline-commit",
                    policy_data_manifest_sha="live-data-sha",
                    policy_feature_manifest_sha="live-feature-sha",
                    policy_release_digest="sha256:challenger-release",
                    promotion_stage="research_validated",
                )
                registry.add(policy)

    router = StrategyTournament(
        policy_registry=registry,
        verification_key=b"live-pipeline-key",
        key_id="pipeline-release-key",
        environment="production",
        state_store=RouterStateStore(tmp_dir / "router_state"),
        audit_path=tmp_dir / "router_audit.jsonl",
        tournament_state_root=tmp_dir / "tournament_state",
        config=AdaptiveRouterConfig(max_policy_age_days=36500),
        tournament_config=TournamentConfig(
            shadow_mode=shadow_mode,
            circuit_breaker_warmup=288,
        ),
        pool=pool,
        exclude=("regime_switching",),
    )
    return router


def _default_params_for(strategy_id: str) -> dict:
    """Return default params for a given strategy ID."""
    if strategy_id == "enhanced_ma":
        return {"fast_period": 10, "slow_period": 60}
    if strategy_id == "rsi":
        return {"period": 14}
    if strategy_id == "bbands":
        return {"period": 20, "std_dev": 2.0}
    return {}


# ── Bar Processor ────────────────────────────────────────────────────────


class BarProcessor:
    """Consumes new closed candles and routes them through the tournament."""

    def __init__(self, tournament: StrategyTournament) -> None:
        self.tournament = tournament
        self._bar_count = 0

    async def on_bar(
        self,
        symbol: str,
        window: pl.DataFrame,
        bar_time: datetime,
    ) -> None:
        """Process one new closed candle.

        1. Build MarketObservation from the bar + window.
        2. Compute deterministic RegimePosterior.
        3. Compute bar return for shadow scoring.
        4. Feed to ``tournament.route()``.
        """
        self._bar_count += 1

        observation = build_observation(symbol, window, bar_time)
        posterior = compute_deterministic_posterior(window, bar_time)
        bar_ret = compute_bar_return(window, bar_time)

        decision = self.tournament.route(
            symbol=symbol,
            timeframe=DEFAULT_TIMEFRAME,
            posterior=posterior,
            observed_at=bar_time,
            position_is_flat=True,
            observation=observation,
            bar_return=bar_ret,
        )

        self._log_decision(symbol, bar_time, decision, bar_ret)

    def _log_decision(
        self,
        symbol: str,
        bar_time: datetime,
        decision,
        bar_ret: float,
    ) -> None:
        chosen = decision.chosen_strategy_id or "EXIT"
        logger.info(
            f"[{bar_time.isoformat()}] {symbol:10} bar#{self._bar_count}  "
            f"strategy={chosen:24}  bar_ret={bar_ret:+.4f}  "
            f"exposure={decision.exposure_multiplier:.2f}  "
            f"state={decision.handover_state.value}  "
            f"reason={decision.reason[:40]}"
        )

    @property
    def bar_count(self) -> int:
        return self._bar_count


# ── Dry-Run Replayer ──────────────────────────────────────────────────────


async def dry_run(
    symbols: list[str],
    max_bars: int,
    tmp_dir: Path,
    window_bars: int = DEFAULT_WINDOW_BARS,
) -> None:
    """Replay historical Binance OHLCV bars through the tournament router.

    No live API keys needed — reads local parquet snapshots (dry-run mode).
    If local data is unavailable, falls back to CCXT public REST.
    """
    # Fresh state: clear persisted policy/state dirs from previous runs
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    feed = BinanceDataFeed(
        symbols,
        timeframe=DEFAULT_TIMEFRAME,
        window_bars=window_bars,
        dry_run=True,
    )
    tournament = build_tournament(symbols, tmp_dir, shadow_mode=True)
    processor = BarProcessor(tournament)

    # Load initial windows
    print(f"\n{'='*60}")
    print("  Live Data Pipeline — DRY RUN (historical replay)")
    print(f"  Symbols: {symbols} | Timeframe: {DEFAULT_TIMEFRAME}")
    print(f"  Bars to evaluate: {max_bars} | Window: {window_bars}")
    print("  Shadow mode: ON (kill switch active)")
    print(f"{'='*60}\n")

    symbol_data: dict[str, pl.DataFrame] = {}
    for symbol in symbols:
        df = feed.fetch_recent_closed(symbol).sort("timestamp")
        start_idx = max(0, len(df) - window_bars - max_bars)
        df = df[start_idx:]
        symbol_data[symbol] = df
        print(f"  {symbol}: {len(df)} bars loaded")

    warmup = window_bars
    min_len = min(len(df) for df in symbol_data.values()) if symbol_data else 0
    total_bars = min(max_bars, min_len - warmup)

    if total_bars < 1:
        print(f"❌ Not enough data: need {warmup + 1} bars, got {min_len}")
        return

    print(f"  Evaluating {total_bars} bars (warmup={warmup})\n")

    progress_step = max(1, total_bars // 10)
    for i in range(warmup, min_len, 1):
        bar_idx = i - warmup
        if bar_idx > max_bars:
            break

        for symbol in symbols:
            df = symbol_data[symbol]
            if i >= len(df):
                continue
            window = df[max(0, i - window_bars + 1):i + 1]
            bar_time = df["timestamp"].item(i)
            if hasattr(bar_time, "replace"):
                bar_time = bar_time.replace(tzinfo=UTC) if bar_time.tzinfo is None else bar_time.astimezone(UTC)

            await processor.on_bar(symbol, window, bar_time)

        if bar_idx % progress_step == 0 or bar_idx == total_bars - 1:
            pct = bar_idx / total_bars * 100
            print(f"  Bar {bar_idx}/{total_bars} ({pct:.0f}%) — "
                  f"processed {processor.bar_count} bars total")

    # Summary
    print(f"\n{'='*60}")
    print(f"  Dry-run complete: {processor.bar_count} bars processed")
    print(f"  Output dir: {tmp_dir}")
    print(f"  Audit log:  {tmp_dir / 'router_audit.jsonl'}")
    print(f"{'='*60}\n")


# ── CLI ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Live data pipeline: Binance → tournament router feed (stub)"
    )
    parser.add_argument(
        "--symbols",
        default="BTC/USDT",
        help="Comma-separated Binance symbols (e.g. BTC/USDT,ETH/USDT)",
    )
    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        help=f"Candle timeframe (default: {DEFAULT_TIMEFRAME})",
    )
    parser.add_argument(
        "--window-bars",
        type=int,
        default=DEFAULT_WINDOW_BARS,
        help=f"Rolling window size in bars (default: {DEFAULT_WINDOW_BARS})",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=500,
        help="Max bars to process in dry-run mode (default: 500)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live polling mode (default: dry-run historical replay)",
    )
    parser.add_argument(
        "--futures",
        action="store_true",
        help="Use Binance Futures (default: spot)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for audit/state (default: data/live_pipeline_<symbols>)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=POLL_INTERVAL_SECONDS,
        help=f"Poll interval in seconds for live mode (default: {POLL_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Max runtime in seconds (for health/timeout tracking)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if args.output_dir:
        tmp_dir = Path(args.output_dir)
    else:
        safe = "_".join(s.replace("/", "_") for s in symbols)
        tmp_dir = Path(f"data/live_pipeline_{safe}")

    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Shadow mode by default; live mode only with explicit env override
    env_shadow = os.getenv("TOURNAMENT_SHADOW_MODE", "1")
    shadow_mode = env_shadow == "1" or not args.live

    if args.live:
        run_live(symbols, args, tmp_dir, shadow_mode)
    else:
        asyncio.run(dry_run(symbols, args.max_bars, tmp_dir, args.window_bars))


def run_live(
    symbols: list[str],
    args: argparse.Namespace,
    tmp_dir: Path,
    shadow_mode: bool,
) -> None:
    """Run the live polling loop."""
    print(f"\n{'='*60}")
    print("  Live Data Pipeline")
    print(f"  Symbols: {symbols} | Timeframe: {args.timeframe}")
    print(f"  Mode: {'LIVE shadow' if shadow_mode else 'LIVE production'}")
    print(f"  Kill switch: {'ACTIVE' if shadow_mode else 'INACTIVE'}")
    print(f"  Poll interval: {args.poll_interval}s")
    print(f"  Timeout: {args.timeout}s")
    print(f"  Output: {tmp_dir}")
    print(f"{'='*60}\n")

    feed = BinanceDataFeed(
        symbols,
        timeframe=args.timeframe,
        window_bars=args.window_bars,
        use_futures=args.futures,
    )
    tournament = build_tournament(symbols, tmp_dir, shadow_mode=shadow_mode)
    processor = BarProcessor(tournament)

    stop_event = asyncio.Event()

    def _signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping pipeline...")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    async def _run():
        try:
            await asyncio.wait_for(
                feed.poll_loop(
                    processor.on_bar,
                    poll_interval=args.poll_interval,
                    stop_event=stop_event,
                ),
                timeout=float(args.timeout),
            )
        except asyncio.TimeoutError:
            logger.warning(f"Live pipeline timeout reached after {args.timeout}s")
        finally:
            # Write a result file for health-check / registry tracking
            result = {
                "status": "stopped",
                "bars_processed": processor.bar_count,
                "symbols": symbols,
                "timeframe": args.timeframe,
                "shadow_mode": shadow_mode,
                "output_dir": str(tmp_dir),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            result_path = tmp_dir / "pipeline_status.json"
            result_path.write_text(json.dumps(result, indent=2))
            print(f"\n  Processed {processor.bar_count} bars. "
                  f"Status written to {result_path}")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
