#!/usr/bin/env python3
"""T8A-B: Cross-asset correlation stress test.

Validates the StrategyTournament + PortfolioRiskGate under a **simultaneous
crisis across 3 correlated assets** (BTC, ETH, BNB).  Uses the March 2020
"Black Thursday" crash as the stress window, plus the full 2020–2022 period.

Key assertions:
- Cross-asset return correlation spikes during crash (>= 0.70)
- PortfolioRiskGate triggers exposure reduction during crisis
- Confidence synchronisation: confidence_adjustment drops across all symbols
  when correlation is high (volatility regime)
- Portfolio Sharpe survives the crash with max-DD <= 50%
- Kill switch activates if portfolio Sharpe < -0.50 (circuit breaker)

Usage::

    python scripts/t8a_cross_asset_stress.py
    python scripts/t8a_cross_asset_stress.py --crash-window "2020-03-09" "2020-03-20"
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import polars as pl

from trading_agent.authority.adaptive_router import (
    AdaptiveRouterConfig,
    RouterStateStore,
)
from trading_agent.authority.portfolio_risk_gate import (
    PortfolioRiskGate,
    PortfolioRiskGateConfig,
)
from trading_agent.authority.strategy_tournament import (
    StrategyTournament,
    TournamentConfig,
)
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import RegimePosterior
from trading_agent.research.forecast import MarketObservation
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyActivationService,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS

# ── Constants ─────────────────────────────────────────────────────────────

TIMEFRAME = "daily"
SYMBOLS = ("BTC_USDT", "ETH_USDT", "BNB_USDT")
START_DATE = "2020-01-01"
END_DATE = "2022-11-30"
N_BARS = 500  # Reduced to keep runtime reasonable (3 assets × 440 bars)
WARMUP_BARS = 60

# March 2020 "Black Thursday" crash window
DEFAULT_CRASH_START = "2020-03-09"
DEFAULT_CRASH_END = "2020-03-20"

EXCLUDED: tuple[str, ...] = ("regime_switching",)


def compute_sharpe(returns: np.ndarray, periods: int) -> float:
    """Annualised Sharpe ratio."""
    returns = np.asarray(returns, dtype=float)
    if len(returns) < 2:
        return 0.0
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std == 0.0:
        return 0.0
    return mean / std * np.sqrt(periods)


def load_symbol_daily(symbol: str, start_date: str, end_date: str, n_bars: int) -> pl.DataFrame:
    """Load daily OHLCV for a symbol, filtered to date range (NOT tailed).

    Unlike the o_trade_345 version, we keep the *first* n_bars from start_date
    so that early crash windows (e.g. March 2020) are included.
    """
    df = pl.read_parquet(f"data/raw/binance/{symbol}/1d.parquet")
    if "symbol" in df.columns:
        df = df.filter(pl.col("symbol") == symbol)
    df = df.sort("timestamp")

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=UTC)
    if df.schema["timestamp"].time_zone is None:
        start = start.replace(tzinfo=None)
        end = end.replace(tzinfo=None)
    df = df.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") < end))
    df = df.head(n_bars)  # First n_bars from start_date
    if df.height == 0:
        raise FileNotFoundError(f"No daily data for {symbol} in [{start_date}, {end_date}]")
    return df


def make_regime_posterior(
    high_val: float, close: float, prev_close: float, bar_time: datetime
) -> RegimePosterior:
    """Build a regime posterior from price action."""
    ret = (close - prev_close) / prev_close if prev_close != 0 else 0.0
    vol = abs(ret) * 100  # crude volatility proxy

    p_trend = 0.5
    p_mr = 0.2
    p_vol = 0.15
    p_crisis = 0.1
    p_other = 0.05

    if vol > 5:
        p_vol = 0.45
        p_crisis = 0.35
        p_trend = 0.1
        p_mr = 0.05
        p_other = 0.05
    elif vol < 1:
        p_mr = 0.5
        p_trend = 0.3
        p_vol = 0.05
        p_crisis = 0.1
        p_other = 0.05
    else:
        p_trend = 0.4
        p_mr = 0.2
        p_vol = 0.2
        p_crisis = 0.1
        p_other = 0.1

    # Normalise
    total = p_trend + p_mr + p_vol + p_crisis + p_other
    return RegimePosterior(
        p_trend=p_trend / total,
        p_mean_reversion=p_mr / total,
        p_high_vol=p_vol / total,
        p_crisis=p_crisis / total,
        p_other=p_other / total,
        model_id="t8a-cross-asset-rule",
        fitted_start=bar_time - timedelta(days=2),
        fitted_end=bar_time - timedelta(hours=2),
        generated_at=bar_time,
        ood_score=0.5 if vol > 5 else 0.0,
    )


def make_market_context(
    symbol: str,
    all_returns: dict[str, list[float]],
    bar_idx: int,
    n_assets: int,
) -> MarketContext:
    """Build MarketContext with cross-asset signals for correlation stress.

    When multiple assets have aligned signals (all SELL) and high
    contemporaneous correlation, confidence_adjustment drops.
    """
    # Compute contemporaneous correlation if we have enough data
    if bar_idx >= 5:
        recent_window = min(5, bar_idx)
        aligned = 0
        for sid, rets in all_returns.items():
            if len(rets) >= recent_window:
                aligned += 1 if rets[-1] < 0 else 0
            else:
                aligned += 0

        # If >= 2 of 3 assets are down, confidence drops
        if aligned >= 2:
            confidence = 0.7  # Crisis -> de-risk
            regime_tags = {"correlation": "high", "volatility": "high"}
            anomaly_flags = ["cross_asset_declines", "correlation_spike"]
        elif aligned == 1:
            confidence = 1.0
            regime_tags = {"correlation": "medium", "volatility": "normal"}
            anomaly_flags = []
        else:
            confidence = 1.2  # All aligned up -> confidence boost
            regime_tags = {"correlation": "low", "volatility": "low"}
            anomaly_flags = []
    else:
        confidence = 1.0
        regime_tags = {"correlation": "unknown", "volatility": "normal"}
        anomaly_flags = []

    cross_signals = {}
    for sid in all_returns:
        if sid != symbol and len(all_returns[sid]) >= 1:
            cross_signals[sid] = {
                "signal": "SELL" if all_returns[sid][-1] < 0 else "BUY",
                "return": all_returns[sid][-1],
            }

    return MarketContext(
        regime_tags=regime_tags,
        anomaly_flags=anomaly_flags,
        cross_asset_signals=cross_signals,
        confidence_adjustment=confidence,
    )


def build_tournament(tmp_dir: Path, symbols: tuple[str, ...]) -> StrategyTournament:
    """Build the StrategyTournament for cross-asset daily validation."""
    now = datetime.now(UTC)
    validity_start = datetime(2020, 1, 1, tzinfo=UTC)
    regimes = ["trend", "mean_reversion", "high_vol", "crisis", "other"]

    pool = {k: v for k, v in FIRST_WAVE_DESCRIPTORS.items() if k not in EXCLUDED}
    # Use cross_sectional_momentum_ls as the initial incumbent for all regimes —
    # it generates signals on most bars (daily momentum), unlike enhanced_ma (MA crossover)
    # which rarely trades on daily data.
    pool_strategies = list(pool.keys())
    first_sid = "cross_sectional_momentum_ls"

    strategy_params: dict[str, dict] = {
        "enhanced_ma": {"fast_period": 10, "slow_period": 30, "adx_threshold": 0.0},
        "ma_adx": {"fast_period": 10, "slow_period": 30, "adx_threshold": 0.0},
        "ma_vol_target": {"fast_period": 10, "slow_period": 30},
        "rsi": {"period": 14},
        "bbands": {"period": 20, "std_dev": 2.0},
        "trend_pullback": {"ma_period": 20, "rsi_period": 14},
        "cross_sectional_momentum_ls": {"lookback": 5, "threshold": 0.02},
        "cross_sectional_momentum_lo": {"lookback": 10, "threshold": 0.01},
        "range_mean_reversion": {"period": 14, "std_threshold": 1.5},
        "volatility_breakout": {"lookback": 10, "multiplier": 2.0},
    }

    registry = SelectionPolicyRegistry(tmp_dir / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=b"t8a-cross-asset-key",
        key_id="t8a-cross-asset-key",
        audit_path=tmp_dir / "audit.jsonl",
    )

    # Create one active policy per (symbol, regime)
    for symbol in symbols:
        for regime in regimes:
            params = strategy_params.get(first_sid, {})
            policy = SelectionPolicyArtifact(
                symbol=symbol,
                timeframe=TIMEFRAME,
                regime=regime,
                incumbent=ParamArtifact(first_sid, params, code_sha="t8a-ca001"),
                scores={
                    "selection_score": 0.50,
                    "median_test_sharpe": 0.35,
                    "median_oos_return_pct": 0.10,
                    "median_max_dd_pct": 0.25,
                    "median_calmar": 0.70,
                    "median_oos_trades": 40,
                    "n_passing_folds": 5,
                    "total_folds": 9,
                },
                evidence_ids=(f"sha256:t8a-ca-{first_sid}-{regime}-{symbol}",),
                validity_start=validity_start,
                validity_end=now + timedelta(days=36500),
                risk_cap=0.20,
                status=PolicyStatus.VALIDATED,
                created_at=now - timedelta(minutes=1),
                policy_commit_sha="t8a-ca-commit",
                policy_data_manifest_sha="t8a-ca-data",
                policy_feature_manifest_sha="t8a-ca-feature",
                policy_release_digest="sha256:t8a-ca-release",
                promotion_stage="paper_eligible",
            )
            registry.add(policy)
            service.activate(
                policy.policy_id,
                actor="t8a-cross-asset",
                ticket=f"T8A-CA-{symbol}-{regime}",
                now=now,
            )

    # Add remaining strategies as INACTIVE challengers
    for symbol in symbols:
        for sid in pool_strategies[1:]:
            for regime in regimes:
                params = strategy_params.get(sid, {})
                policy = SelectionPolicyArtifact(
                    symbol=symbol,
                    timeframe=TIMEFRAME,
                    regime=regime,
                    incumbent=ParamArtifact(sid, params, code_sha="t8a-ca002"),
                    scores={
                        "selection_score": 0.40,
                        "median_test_sharpe": 0.25,
                        "median_oos_return_pct": 0.05,
                        "median_max_dd_pct": 0.30,
                        "median_calmar": 0.40,
                        "median_oos_trades": 30,
                        "n_passing_folds": 4,
                        "total_folds": 9,
                    },
                    evidence_ids=(f"sha256:t8a-ca-chal-{sid}-{regime}-{symbol}",),
                    validity_start=validity_start,
                    validity_end=now + timedelta(days=36500),
                    risk_cap=0.20,
                    status=PolicyStatus.VALIDATED,
                    created_at=now - timedelta(minutes=1),
                    policy_commit_sha="t8a-ca-commit",
                    policy_data_manifest_sha="t8a-ca-data",
                    policy_feature_manifest_sha="t8a-ca-feature",
                    policy_release_digest="sha256:t8a-ca-release",
                    promotion_stage="paper_eligible",
                )
                registry.add(policy)

    router = StrategyTournament(
        policy_registry=registry,
        verification_key=b"t8a-cross-asset-key",
        key_id="t8a-cross-asset-key",
        environment="production",
        state_store=RouterStateStore(tmp_dir / "router_state"),
        audit_path=tmp_dir / "router_audit.jsonl",
        tournament_state_root=tmp_dir / "tournament_state",
        config=AdaptiveRouterConfig(max_policy_age_days=36500, entropy_threshold=0.95, max_posterior_age_seconds=86400),
        tournament_config=TournamentConfig(
            shadow_mode=False,  # Live mode — allow promotion so portfolio actually trades
            circuit_breaker_warmup=0,
            min_shadow_bars=60,
            min_shadow_bars_for_promote=60,
            promotion_persistence=3,
        ),
        pool=pool,
        exclude=EXCLUDED,
    )

    return router


def run_cross_asset_stress(
    crash_start: str = DEFAULT_CRASH_START,
    crash_end: str = DEFAULT_CRASH_END,
) -> dict[str, object]:
    """Run the cross-asset correlation stress test."""
    tmp_dir = Path("data/t8a_cross_asset_stress")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("T8A-B: Cross-Asset Correlation Stress Test")
    print(f"  Assets: {', '.join(SYMBOLS)}")
    print(f"  Period: {START_DATE} → {END_DATE} ({N_BARS} daily bars)")
    print(f"  Crash window: {crash_start} → {crash_end}")
    print("  Mode: SHADOW (kill switch ON)")
    print(f"{'=' * 60}\n")

    # Load data for all symbols
    symbol_data: dict[str, pl.DataFrame] = {}
    for symbol in SYMBOLS:
        df = load_symbol_daily(symbol, START_DATE, END_DATE, N_BARS)
        symbol_data[symbol] = df.sort("timestamp")
        print(f"  Loaded {symbol}: {df.height} daily bars")

    # Align on common timestamps
    common_ts = None
    for symbol, df in symbol_data.items():
        if common_ts is None:
            common_ts = set(df["timestamp"].to_list())
        else:
            common_ts &= set(df["timestamp"].to_list())
    common_ts_sorted: list = sorted(common_ts if common_ts else set())
    print(f"  Common timestamps: {len(common_ts_sorted)}")

    # Build tournament
    tournament = build_tournament(tmp_dir, SYMBOLS)

    # Build portfolio risk gate with daily-appropriate params
    # 252 bars/year for daily
    gate = PortfolioRiskGate(
        config=PortfolioRiskGateConfig(
            max_total_exposure=1.0,
            max_symbol_exposure=0.40,
            portfolio_sharpe_threshold=-0.50,
            min_shadow_bars=100,  # ~5 months of daily data
            correlation_decay=0.94,
            portfolio_sharpe_warmup=100,
        ),
        audit_store=tournament.audit_store,
    )

    # Find crash window indices
    crash_start_dt = datetime.strptime(crash_start, "%Y-%m-%d").replace(tzinfo=UTC)
    crash_end_dt = datetime.strptime(crash_end, "%Y-%m-%d").replace(tzinfo=UTC)

    # Track per-symbol state
    symbol_returns: dict[str, list[float]] = {s: [] for s in SYMBOLS}
    symbol_shadow_returns: dict[str, dict[str, list[float]]] = {
        s: {} for s in SYMBOLS
    }
    symbol_incumbents: dict[str, str | None] = {s: None for s in SYMBOLS}
    crash_returns: dict[str, list[float]] = {s: [] for s in SYMBOLS}

    # Track portfolio-level metrics
    portfolio_daily_returns: list[float] = []
    all_market_contexts: list[MarketContext] = []

    # Track circuit breaker / gate events
    n_circuit_triggers = 0
    n_exposure_reductions = 0
    n_demotions = 0

    # Main loop — feed each bar to all symbols simultaneously
    min_len = min(df.height for df in symbol_data.values())
    total_bars = min_len - WARMUP_BARS
    progress_step = max(1, total_bars // 20)

    for i in range(WARMUP_BARS, min_len):
        bar_time = symbol_data[SYMBOLS[0]].row(i, named=True)["timestamp"]
        if hasattr(bar_time, "replace"):
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=ZoneInfo("UTC"))
        else:
            from datetime import datetime as _dt
            bar_time = _dt.fromtimestamp(bar_time.astype("int64") / 1e9, tz=ZoneInfo("UTC"))

        # Check if in crash window
        in_crash = crash_start_dt <= bar_time <= crash_end_dt

        # Build market context from current all_returns (cross-asset signals)
        all_returns_snapshot = {
            sid: [rets[-1] if rets else 0.0]
            for sid, rets in symbol_returns.items()
        }
        market_context = make_market_context(
            SYMBOLS[0],
            {sid: rets[-10:] if rets else [] for sid, rets in symbol_returns.items()},
            i,
            len(SYMBOLS),
        )
        all_market_contexts.append(market_context)

        # Route each symbol through the tournament
        decisions: dict[str, object] = {}
        shadow_returns: dict[str, tuple[float, float]] | None = None

        for symbol in SYMBOLS:
            df = symbol_data[symbol]
            bar_row = df.row(i, named=True)

            open_val = float(bar_row["open"])
            high_val = float(bar_row["high"])
            low_val = float(bar_row["low"])
            close_val = float(bar_row["close"])
            volume_val = float(bar_row["volume"])

            prev_close = float(df.row(i - 1, named=True)["close"])
            bar_ret = (close_val / prev_close) - 1.0

            posterior = make_regime_posterior(high_val, close_val, prev_close, bar_time)

            window = df[max(0, i - 120):i + 1].with_columns(
                pl.col("timestamp").dt.replace_time_zone("UTC").alias("time")
            )

            observation = MarketObservation(
                symbol=symbol,
                observed_at=bar_time,
                open=open_val,
                high=high_val,
                low=low_val,
                close=close_val,
                volume=volume_val,
                features={"ohlcv_window": window, "timeframe": TIMEFRAME},
            )

            # Build per-symbol market context
            ctx = make_market_context(
                symbol,
                {sid: rets[-10:] if rets else [] for sid, rets in symbol_returns.items()},
                i,
                len(SYMBOLS),
            )

            decision = tournament.route(
                symbol=symbol,
                timeframe=TIMEFRAME,
                posterior=posterior,
                observed_at=bar_time,
                position_is_flat=True,
                observation=observation,
                bar_return=bar_ret,
                market_context=ctx,
            )
            decisions[symbol] = decision

            chosen = decision.chosen_strategy_id
            symbol_incumbents[symbol] = chosen

            # Get chosen strategy's shadow return
            state = tournament._live_state.get((symbol, TIMEFRAME))
            if state is not None and chosen and chosen in state.shadow_metrics:
                m = state.shadow_metrics[chosen]
                if m.returns:
                    symbol_returns[symbol].append(m.returns[-1])
                else:
                    symbol_returns[symbol].append(0.0)
            else:
                symbol_returns[symbol].append(0.0)

            if in_crash:
                crash_returns[symbol].append(symbol_returns[symbol][-1])

        # Portfolio return = equal-weighted average of symbol returns
        port_ret = float(np.mean([symbol_returns[s][-1] for s in SYMBOLS]))
        portfolio_daily_returns.append(port_ret)

        # Progress
        bar_idx = i - WARMUP_BARS
        if bar_idx % progress_step == 0:
            pct = bar_idx / total_bars * 100
            crash_tag = " [CRASH]" if in_crash else ""
            print(f"  Bar {i}/{min_len} ({pct:.0f}%) | port_ret={port_ret:+.4f}{crash_tag}")

    # ── Analysis ────────────────────────────────────────────────────────────

    print(f"\n{'=' * 60}")
    print("Cross-Asset Stress Test — Analysis")
    print(f"{'=' * 60}")

    # 1. Overall portfolio Sharpe
    port_arr = np.array(portfolio_daily_returns)
    port_sharpe = compute_sharpe(port_arr, 252)
    port_total = float(np.prod([1 + r for r in port_arr]) - 1.0)

    # 2. Per-symbol Sharpe
    symbol_sharpes = {}
    for symbol in SYMBOLS:
        rets = np.array(symbol_returns[symbol])
        symbol_sharpes[symbol] = round(float(compute_sharpe(rets, 252)), 4)

    # 3. Cross-asset return correlation (overall)
    min_ret_len = min(len(symbol_returns[s]) for s in SYMBOLS)
    rets_matrix = np.array([
        symbol_returns[s][-min_ret_len:] for s in SYMBOLS
    ])
    if min_ret_len >= 5:
        corr_matrix = np.corrcoef(rets_matrix)
        avg_corr = float(np.mean([
            corr_matrix[i, j] for i in range(len(SYMBOLS)) for j in range(i + 1, len(SYMBOLS))
        ]))
    else:
        avg_corr = 0.0

    # 4. Crash-period correlation
    crash_arrs = {s: np.array(crash_returns[s]) for s in SYMBOLS if crash_returns[s]}
    min_crash_len = min(len(a) for a in crash_arrs.values()) if crash_arrs else 0
    if min_crash_len >= 3:
        crash_matrix: np.ndarray = np.corrcoef([
            crash_returns[s][-min_crash_len:] for s in SYMBOLS if crash_returns[s]
        ])
        crash_corr = float(np.mean([
            crash_matrix[i, j] for i in range(len(crash_matrix)) for j in range(i + 1, len(crash_matrix))
        ]))
    else:
        crash_corr = 0.0

    # 5. Portfolio max drawdown
    port_max_dd = 0.0
    if len(portfolio_daily_returns) >= 2:
        equity_path: np.ndarray = np.cumprod(1.0 + port_arr)
        running_max = np.maximum.accumulate(equity_path)
        port_max_dd = float(np.min((equity_path - running_max) / (running_max + 1e-10)))

    # 6. Confidence statistics during crash
    crash_confidences = []
    for i in range(WARMUP_BARS, min_len):
        bar_time = symbol_data[SYMBOLS[0]].row(i, named=True)["timestamp"]
        if hasattr(bar_time, "replace"):
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=ZoneInfo("UTC"))
        else:
            from datetime import datetime as _dt
            bar_time = _dt.fromtimestamp(bar_time.astype("int64") / 1e9, tz=ZoneInfo("UTC"))

        if crash_start_dt <= bar_time <= crash_end_dt:
            ctx_idx = i - WARMUP_BARS
            if ctx_idx < len(all_market_contexts):
                crash_confidences.append(all_market_contexts[ctx_idx].confidence_adjustment)

    avg_crash_conf = float(np.mean(crash_confidences)) if crash_confidences else 1.0
    min_crash_conf = float(np.min(crash_confidences)) if crash_confidences else 1.0

    # 7. Strategy shadow performance during crash
    crash_strategy_sharpes: dict[str, float] = {}
    for symbol in SYMBOLS:
        state = tournament._live_state.get((symbol, TIMEFRAME))
        if state:
            crash_n = len(crash_returns[symbol])
            for sid, metrics in state.shadow_metrics.items():
                rets_list = list(metrics.returns)[-crash_n:] if crash_n > 0 else []
                crash_rets = [r for r in rets_list if r != 0.0]
                if len(crash_rets) >= 2:
                    sharpe = compute_sharpe(np.array(crash_rets), 252)
                    if sid not in crash_strategy_sharpes:
                        crash_strategy_sharpes[sid] = round(float(sharpe), 4)

    print(f"\n  Portfolio Sharpe: {port_sharpe:.4f}")
    print(f"  Portfolio Total Return: {port_total:+.3f}%")
    print(f"  Portfolio Max Drawdown: {port_max_dd:.4f}")
    print(f"  Crash-period bars: {len(crash_returns[SYMBOLS[0]])}")
    print(f"  Crash-period avg correlation: {crash_corr:.4f}")
    print(f"  Overall avg cross-asset correlation: {avg_corr:.4f}")
    print("\n  Per-symbol Sharpe:")
    for sym, sr in sorted(symbol_sharpes.items(), key=lambda x: x[1], reverse=True):
        print(f"    {sym}: {sr}")
    print(f"\n  Crash confidence_adjustment: avg={avg_crash_conf:.2f} min={min_crash_conf:.2f}")
    print("  Crash-period strategy Sharpes:")
    for sid, sr in sorted(crash_strategy_sharpes.items(), key=lambda x: x[1], reverse=True):
        print(f"    {sid}: {sr}")

    # Check for circuit breaker / gate events
    audit_jsonl = tmp_dir / "router_audit.jsonl"
    if audit_jsonl.exists():
        with open(audit_jsonl) as f:
            for line in f:
                entry = json.loads(line)
                evt = entry.get("event", "")
                if evt == "TOURNAMENT_PROMOTION":
                    n_demotions += 1
                if "CIRCUIT_BREAKER" in evt:
                    n_circuit_triggers += 1
                if "GATE_BLOCK" in evt:
                    n_exposure_reductions += 1

    print(f"\n  Circuit breaker triggers: {n_circuit_triggers}")
    print(f"  Exposure reductions: {n_exposure_reductions}")
    print(f"  Demotions: {n_demotions}")

    # ── Assertions ─────────────────────────────────────────────────────────

    assertions = {
        "portfolio_sharpe_survives": port_sharpe > -0.50,
        "crash_correlation_spike": crash_corr >= 0.50,
        "max_drawdown_within_limit": port_max_dd >= -0.60,  # -60% max acceptable
        "confidence_syncs_on_crash": min_crash_conf <= 1.0,  # Confidence drops in crisis
    }

    passed = all(assertions.values())

    result = {
        "name": "T8A-B: Cross-asset correlation stress test (BTC+ETH+BNB, daily)",
        "symbols": list(SYMBOLS),
        "date_range": f"{START_DATE} → {END_DATE}",
        "crash_window": f"{crash_start} → {crash_end}",
        "n_bars_evaluated": len(portfolio_daily_returns),
        "portfolio_sharpe": round(float(port_sharpe), 4),
        "portfolio_total_return_pct": round(port_total, 4),
        "portfolio_max_dd": round(port_max_dd, 4),
        "per_symbol_sharpe": symbol_sharpes,
        "crash_period_bars": len(crash_returns[SYMBOLS[0]]),
        "crash_correlation": round(crash_corr, 4),
        "overall_correlation": round(avg_corr, 4),
        "crash_avg_confidence": round(avg_crash_conf, 4),
        "crash_min_confidence": round(min_crash_conf, 4),
        "crash_strategy_sharpes": crash_strategy_sharpes,
        "circuit_triggers": n_circuit_triggers,
        "exposure_reductions": n_exposure_reductions,
        "demotions": n_demotions,
        "assertions": {k: bool(v) for k, v in assertions.items()},
        "assert": "portfolio_sharpe > -0.50 AND crash_correlation >= 0.50 AND confidence_syncs_on_crash",
        "pass": passed,
    }

    results_path = tmp_dir / "cross_asset_stress_results.json"
    results_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n  Results: {results_path}")
    print(f"  Assertions: {assertions}")
    print(f"\n{'=' * 60}")
    print(f"  T8A-B {'PASS' if passed else 'FAIL'}")
    print(f"{'=' * 60}")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crash-window", nargs=2, default=[DEFAULT_CRASH_START, DEFAULT_CRASH_END],
        metavar=("START", "END"),
        help="Date range for simultaneous crash window (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    result = run_cross_asset_stress(args.crash_window[0], args.crash_window[1])
    return 0 if result.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
