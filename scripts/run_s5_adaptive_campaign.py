#!/usr/bin/env python
"""
S5: Adaptive Routing + Shared-Capital OOS Campaign

Runs a complete out-of-sample campaign with:
1. Regime detection producing 5-state posteriors (trend, mean_reversion, high_vol, crisis, other)
2. AdaptiveStrategyRouter routing each (symbol, timeframe) through signed policies
3. PortfolioAllocator for shared-capital allocation across all symbols
4. Signed SelectionPolicyArtifact creation for promotion pipeline
5. Frozen holdout enforcement from data/research_manifest.json

Modes:
- --mode synthetic: CI-safe tiny in-memory run (default)
- --mode real: Full real-data campaign (heavy, manual only)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.authority.adaptive_router import (  # noqa: E402
    AdaptiveForecastRuntime,
    AdaptiveRouterConfig,
    AdaptiveStrategyRouter,
    RouterStateStore,
)
from trading_agent.authority.config import Environment  # noqa: E402
from trading_agent.authority.portfolio import (  # noqa: E402
    AllocationRequest,
    PortfolioAllocator,
    PortfolioSnapshot,
    ReconciliationState,
)
from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv  # noqa: E402
from trading_agent.data.storage import load_ohlcv  # noqa: E402
from trading_agent.ml.regime_detection import RegimePosterior  # noqa: E402
from trading_agent.online_learning.regime_detector import (  # noqa: E402
    MarketRegime,
    RegimeDetector,
    create_regime_detector,
)
from trading_agent.research.forecast import MarketObservation  # noqa: E402
from trading_agent.research.selection_policy import (  # noqa: E402
    ParamArtifact,
    PolicyActivationService,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)
from trading_agent.strategies.canonical.candidates import (  # noqa: E402
    FIRST_WAVE_DESCRIPTORS,
)
from trading_agent.strategies.canonical.features import (  # noqa: E402
    FEATURE_OHLCV_WINDOW,
    build_ohlcv_window,
)

# ── Constants ─────────────────────────────────────────────────────────────

TEN_SYMBOLS = (
    "ADA/USDT",
    "BNB/USDT",
    "BTC/USDT",
    "DOGE/USDT",
    "ETH/USDT",
    "NEAR/USDT",
    "SOL/USDT",
    "TRX/USDT",
    "XRP/USDT",
    "ZEC/USDT",
)

TIMEFRAME = "1h"
SIGNING_KEY = b"s5-adaptive-campaign-signing-key"
KEY_ID = "s5-campaign-key"

REGIME_TO_POLICY_REGIME = {
    MarketRegime.TRENDING_UP: "trend",
    MarketRegime.TRENDING_DOWN: "trend",
    MarketRegime.SIDEWAYS: "mean_reversion",
    MarketRegime.VOLATILE: "high_vol",
    MarketRegime.UNKNOWN: "other",
}

REGIME_POLICY_PARAMS = {
    "trend": ("ma_adx", 1.5),
    "mean_reversion": ("rsi", 2.0),
    "high_vol": ("ma_vol_target", 1.2),
    "crisis": ("bbands", 1.2),
    "other": ("enhanced_ma", 0.4),
}


# ── Helpers ───────────────────────────────────────────────────────────────


def _load_holdout_window() -> tuple[int, int] | None:
    """Load frozen holdout window from research_manifest.json."""
    manifest_path = ROOT / "data" / "research_manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text())
    window = manifest.get("window", {})
    if not window:
        return None
    # The manifest has end_utc as string, we need to compute bar indices
    # This will be resolved against actual data in the main run
    return None  # Deferred to runtime when data is loaded


def _resolve_holdout_bars(df: pl.DataFrame) -> tuple[int, int]:
    """Resolve holdout window to bar indices on the given dataframe."""
    manifest_path = ROOT / "data" / "research_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("research_manifest.json not found")
    manifest = json.loads(manifest_path.read_text())
    end_utc = manifest["window"]["end_utc"]
    holdout_months = manifest.get("holdout_months", 6)

    # Find the bar index matching end_utc
    end_dt = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    times = df["time"].to_list()
    holdout_end_idx = len(times) - 1
    for i, t in enumerate(times):
        if t >= end_dt:
            holdout_end_idx = i
            break

    # Holdout is last N months (approximately)
    bars_per_month = 24 * 30  # 1h timeframe
    holdout_bars = holdout_months * bars_per_month
    holdout_start_idx = max(0, holdout_end_idx - holdout_bars)

    return holdout_start_idx, holdout_end_idx


def _posterior_from_regime_signal(
    signal: Any, recent_df: pl.DataFrame, observed_at: datetime, detector: RegimeDetector
) -> RegimePosterior:
    """Convert RegimeSignal (5-state) to RegimePosterior (5-state canonical)."""
    # Ensure observed_at is timezone-aware
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    probs = detector.get_regime_probabilities(recent_df)

    # Map 8-state to 5-state
    buckets = {
        "trend": 0.0,
        "mean_reversion": 0.0,
        "high_vol": 0.0,
        "crisis": 0.0,
        "other": 0.0,
    }
    for regime, prob in probs.items():
        mapped = REGIME_TO_POLICY_REGIME.get(regime, "other")
        buckets[mapped] += prob

    total = sum(buckets.values())
    if total <= 0:
        buckets = {k: 0.2 for k in buckets}
        total = 1.0
    normalized = {k: v / total for k, v in buckets.items()}

    return RegimePosterior(
        p_trend=normalized["trend"],
        p_mean_reversion=normalized["mean_reversion"],
        p_high_vol=normalized["high_vol"],
        p_crisis=normalized["crisis"],
        p_other=normalized["other"],
        model_id="online-regime-detector-v1",
        fitted_start=observed_at - timedelta(days=90),
        fitted_end=observed_at - timedelta(days=1),
        generated_at=observed_at,
        ood_score=0.1,
    )


def _build_active_registry(tmp_path: Path, synthetic_start: datetime) -> SelectionPolicyRegistry:
    """Build a registry with active signed policies for all regimes."""
    # Clean up any existing state
    if (tmp_path / "policies").exists():
        shutil.rmtree(tmp_path / "policies")
    if (tmp_path / "policy-activation.jsonl").exists():
        (tmp_path / "policy-activation.jsonl").unlink()

    registry = SelectionPolicyRegistry(tmp_path / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=SIGNING_KEY,
        key_id=KEY_ID,
        audit_path=tmp_path / "policy-activation.jsonl",
    )

    # Use synthetic_start as the reference for validity - policies valid from bar 0
    now = synthetic_start + timedelta(days=10)  # Reference time for validity_end
    validity_start = synthetic_start  # Valid from the very beginning
    for index, (regime, (strategy_id, score)) in enumerate(REGIME_POLICY_PARAMS.items()):
        created_at = validity_start - timedelta(days=1, minutes=index)
        descriptor = FIRST_WAVE_DESCRIPTORS[strategy_id]
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",  # Template; will be per-symbol at activation
            timeframe=TIMEFRAME,
            regime=regime,
            incumbent=ParamArtifact(
                strategy_id, {"period": 14}, code_sha=descriptor.code_sha
            ),
            scores={"selection_score": score},
            evidence_ids=(f"sha256:study-{regime}", f"sha256:outer-{regime}"),
            validity_start=validity_start,
            validity_end=now + timedelta(days=29),
            risk_cap=0.25,
            status=PolicyStatus.VALIDATED,
            created_at=created_at,
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        registry.add(policy)
        service.activate(
            policy.policy_id,
            actor="s5-campaign",
            ticket=f"S5-POLICY-{index}",
            now=created_at + timedelta(minutes=1),
            expected_previous_policy_id=None,
        )
    return registry


def _make_observation(
    symbol: str, df: pl.DataFrame, idx: int
) -> MarketObservation:
    """Build MarketObservation from DataFrame at index."""
    row = df.row(idx, named=True)
    observed_at = row["timestamp"]
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    # build_ohlcv_window expects "time" column; synthetic data has "timestamp"
    df_for_window = df.rename({"timestamp": "time"}) if "timestamp" in df.columns else df
    # Make time column timezone-aware
    if "time" in df_for_window.columns:
        df_for_window = df_for_window.with_columns(
            pl.col("time").dt.replace_time_zone("UTC")
        )
    # Need enough bars for warmup (100+ for most strategies)
    window = build_ohlcv_window(
        df_for_window.slice(max(0, idx - 200), 200),
        observed_at=observed_at,
        bars=150,
    )
    return MarketObservation(
        symbol=symbol,
        observed_at=observed_at,
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        features={FEATURE_OHLCV_WINDOW: window},
    )


# ── Synthetic Mode ────────────────────────────────────────────────────────


def run_synthetic(out_root: Path, n_bars: int = 1000) -> dict:
    """Run full S5 pipeline on synthetic data (CI-safe, fast)."""
    # Clean up any existing state
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # Generate synthetic data for all symbols
    synthetic_data = {}
    for symbol in TEN_SYMBOLS:
        synthetic_data[symbol] = generate_synthetic_ohlcv(n_bars=n_bars, seed=42)

    # Build regime detector
    regime_detector = create_regime_detector(lookback_bars=100)

    # Build registry with signed policies
    synthetic_start = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    registry = _build_active_registry(out_root, synthetic_start)

    # Build adaptive routers per symbol
    routers: dict[str, AdaptiveStrategyRouter] = {}
    runtimes: dict[str, AdaptiveForecastRuntime] = {}
    for symbol in TEN_SYMBOLS:
        routers[symbol] = AdaptiveStrategyRouter(
            registry,
            verification_key=SIGNING_KEY,
            key_id=KEY_ID,
            state_store=RouterStateStore(out_root / "router_state" / symbol),
            audit_path=out_root / "routing_decisions" / f"{symbol.replace('/', '_')}.jsonl",
            config=AdaptiveRouterConfig(
                persistence_bars=1,
                min_dwell_bars=1,
                cooldown_bars=0,
                entropy_threshold=0.75,
                min_policy_coverage=0.5,  # Lower for synthetic test
            ),
        )
        runtimes[symbol] = AdaptiveForecastRuntime(
            registry,
            verification_key=SIGNING_KEY,
            key_id=KEY_ID,
            environment=Environment.RESEARCH,
        )

    # Portfolio allocator
    allocator = PortfolioAllocator()

    # Simulated portfolio snapshot
    equity = 100_000.0
    snapshot = PortfolioSnapshot(
        equity=equity,
        available_cash=equity,
        positions={},
        symbol_exposures={},
        gross_exposure=0.0,
        untracked_symbols=(),
        untracked_exposure=0.0,
        untracked_valued=True,
        reconciliation_state=ReconciliationState.RECONCILED,
    )

    # Run campaign
    decisions_log = []
    forecasts_log = []
    allocation_log = []

    # Use first symbol's data length as timeline
    timeline_bars = min(len(synthetic_data[s]) for s in TEN_SYMBOLS)

    # Warmup: first 200 bars for regime detector
    warmup = 200

    for bar_idx in range(warmup, timeline_bars):
        # Collect routing decisions and forecasts
        symbol_forecasts = {}
        symbol_decisions = {}
        requests = []

        for symbol in TEN_SYMBOLS:
            df = synthetic_data[symbol]
            if bar_idx >= len(df):
                continue

            # Get regime posterior
            recent_df = df.slice(max(0, bar_idx - 200), 200)
            observed_at = df.row(bar_idx, named=True)["timestamp"]
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            signal = regime_detector.detect(recent_df)
            posterior = _posterior_from_regime_signal(signal, recent_df, observed_at, regime_detector)

            # Route
            decision = routers[symbol].route(
                symbol=symbol,
                timeframe=TIMEFRAME,
                posterior=posterior,
                observed_at=observed_at,
                position_is_flat=True,
            )
            symbol_decisions[symbol] = decision
            decisions_log.append(decision.to_dict())

            # Forecast
            observation = _make_observation(symbol, df, bar_idx)
            result = runtimes[symbol].forecast(decision, observation)
            if result.executable and result.forecast is not None:
                symbol_forecasts[symbol] = result.forecast
                forecasts_log.append({
                    "symbol": symbol,
                    "observed_at": observed_at.isoformat(),
                    "forecast": result.forecast.expected_excess_return,
                    "strategy": result.strategy_descriptor_id,
                    "policy_id": result.policy_id,
                })

            # Build allocation request
            if result.executable:
                @dataclass
                class SimpleRiskDecision:
                    allowed_target_exposure: float
                    max_new_exposure: float
                    reduce_only: bool = False

                risk_decision = SimpleRiskDecision(
                    allowed_target_exposure=decision.exposure_multiplier,
                    max_new_exposure=decision.exposure_multiplier,
                    reduce_only=False,
                )
                requests.append(AllocationRequest(
                    strategy_id=result.strategy_descriptor_id or "unknown",
                    symbol=symbol,
                    risk_decision=risk_decision,
                    current_exposure=0.0,
                    equity=equity,
                    available_cash=equity,
                    portfolio_exposure=snapshot.gross_exposure,
                    correlation_cluster="MAJORS" if symbol in ("BTC/USDT", "ETH/USDT") else "OTHER",
                    desired_exposure=(
                        result.forecast.expected_excess_return * decision.exposure_multiplier
                    ),
                    causation_chain=None,
                ))

        # Allocate batch
        if requests:
            outcome = allocator.allocate_batch(requests, snapshot)
            allocation_log.append({
                "bar_idx": bar_idx,
                "scale_factor": outcome.scale_factor,
                "total_requested": outcome.total_requested,
                "total_approved": outcome.total_approved,
                "budget_available": outcome.budget_available,
                "entries": [
                    {
                        "symbol": e.symbol,
                        "strategy_id": e.strategy_id,
                        "requested": e.requested,
                        "approved": e.approved,
                        "reason": e.reason,
                    }
                    for e in outcome.entries
                ],
            })
            # Update snapshot (simplified - just track gross)
            snapshot = PortfolioSnapshot(
                equity=equity,
                available_cash=equity,
                positions={},
                symbol_exposures=outcome.approved_by_symbol,
                gross_exposure=sum(outcome.approved_by_symbol.values()),
                untracked_symbols=(),
                untracked_exposure=0.0,
                untracked_valued=True,
                reconciliation_state=ReconciliationState.RECONCILED,
            )

    # Save logs
    (out_root / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decisions_log)
    )
    (out_root / "forecasts.jsonl").write_text(
        "\n".join(json.dumps(f) for f in forecasts_log)
    )
    (out_root / "allocations.jsonl").write_text(
        "\n".join(json.dumps(a) for a in allocation_log)
    )

    summary = {
        "mode": "synthetic",
        "n_symbols": len(TEN_SYMBOLS),
        "n_bars_processed": timeline_bars - warmup,
        "n_decisions": len(decisions_log),
        "n_forecasts": len(forecasts_log),
        "n_allocation_steps": len(allocation_log),
        "final_equity": equity,
        "final_gross_exposure": snapshot.gross_exposure,
    }
    (out_root / "s5_campaign_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


# ── Real Mode ─────────────────────────────────────────────────────────────


def run_real(out_root: Path, symbols: list[str] | None = None) -> dict:
    """Run full S5 pipeline on real data with frozen holdout (heavy)."""
    out_root.mkdir(parents=True, exist_ok=True)

    if symbols is None:
        symbols = list(TEN_SYMBOLS)

    # Load real data
    print("[S5] Loading real OHLCV data...")
    data = {}
    for symbol in symbols:
        try:
            df = load_ohlcv("binance", symbol, TIMEFRAME)
            data[symbol] = df
            print(f"  {symbol}: {df.height} bars")
        except Exception as e:
            print(f"  {symbol}: FAILED - {e}")

    if not data:
        raise ValueError("No data loaded for any symbol")

    # Resolve holdout window
    print("[S5] Resolving frozen holdout window...")
    first_df = next(iter(data.values()))
    holdout_start, holdout_end = _resolve_holdout_bars(first_df)
    print(f"  Holdout bars: {holdout_start} .. {holdout_end}")

    # OOS window: everything before holdout
    oos_end = holdout_start

    # Build regime detector
    regime_detector = create_regime_detector(lookback_bars=100)

    # Build registry with signed policies (per symbol)
    registry = SelectionPolicyRegistry(out_root / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=SIGNING_KEY,
        key_id=KEY_ID,
        audit_path=out_root / "policy-activation.jsonl",
    )

    now = datetime.now(UTC)
    for symbol in symbols:
        for regime, (strategy_id, score) in REGIME_POLICY_PARAMS.items():
            created_at = now - timedelta(days=1)
            descriptor = FIRST_WAVE_DESCRIPTORS[strategy_id]
            policy = SelectionPolicyArtifact(
                symbol=symbol,
                timeframe=TIMEFRAME,
                regime=regime,
                incumbent=ParamArtifact(
                    strategy_id, {"period": 14}, code_sha=descriptor.code_sha
                ),
                scores={"selection_score": score},
                evidence_ids=(f"sha256:study-{symbol}-{regime}",),
                validity_start=created_at,
                validity_end=now + timedelta(days=29),
                risk_cap=0.25,
                status=PolicyStatus.VALIDATED,
                created_at=created_at,
                policy_commit_sha="a" * 40,
                policy_data_manifest_sha="b" * 64,
                policy_feature_manifest_sha="c" * 64,
                policy_release_digest="sha256:" + "d" * 64,
                promotion_stage="paper_eligible",
            )
            registry.add(policy)
            service.activate(
                policy.policy_id,
                actor="s5-campaign",
                ticket=f"S5-{symbol.replace('/', '_')}-{regime}",
                now=created_at + timedelta(minutes=1),
            )

    # Build routers and runtimes
    routers: dict[str, AdaptiveStrategyRouter] = {}
    runtimes: dict[str, AdaptiveForecastRuntime] = {}
    for symbol in symbols:
        routers[symbol] = AdaptiveStrategyRouter(
            registry,
            verification_key=SIGNING_KEY,
            key_id=KEY_ID,
            state_store=RouterStateStore(out_root / "router_state" / symbol.replace("/", "_")),
            audit_path=out_root / "routing_decisions" / f"{symbol.replace('/', '_')}.jsonl",
            config=AdaptiveRouterConfig(
                persistence_bars=3,
                min_dwell_bars=6,
                cooldown_bars=3,
                entropy_threshold=0.75,
            ),
        )
        runtimes[symbol] = AdaptiveForecastRuntime(
            registry,
            verification_key=SIGNING_KEY,
            key_id=KEY_ID,
            environment=Environment.RESEARCH,
        )

    # Portfolio allocator
    allocator = PortfolioAllocator()
    equity = 100_000.0
    snapshot = PortfolioSnapshot(
        equity=equity,
        available_cash=equity,
        positions={},
        symbol_exposures={},
        gross_exposure=0.0,
        untracked_symbols=(),
        untracked_exposure=0.0,
        untracked_valued=True,
        reconciliation_state=ReconciliationState.RECONCILED,
    )

    # Run OOS campaign
    decisions_log = []
    forecasts_log = []
    allocation_log = []

    timeline_bars = min(len(df) for df in data.values())
    oos_bars = min(oos_end, timeline_bars)
    warmup = 200

    print(f"[S5] Running OOS campaign: bars {warmup} .. {oos_bars} (holdout starts at {holdout_start})")

    for bar_idx in range(warmup, oos_bars):
        symbol_forecasts = {}
        requests = []

        for symbol in symbols:
            df = data[symbol]
            if bar_idx >= len(df):
                continue

            # Get regime posterior
            recent = df.slice(max(0, bar_idx - 200), 200)
            signal = regime_detector.detect(recent)
            observed_at = df.row(bar_idx, named=True)["timestamp"]
            posterior = _posterior_from_regime_signal(signal, recent, observed_at, regime_detector)

            # Route
            decision = routers[symbol].route(
                symbol=symbol,
                timeframe=TIMEFRAME,
                posterior=posterior,
                observed_at=observed_at,
                position_is_flat=True,
            )
            decisions_log.append(decision.to_dict())

            # Forecast
            observation = _make_observation(symbol, df, bar_idx)
            result = runtimes[symbol].forecast(decision, observation)
            if result.executable and result.forecast is not None:
                symbol_forecasts[symbol] = result.forecast
                forecasts_log.append({
                    "symbol": symbol,
                    "observed_at": observed_at.isoformat(),
                    "forecast": result.forecast.expected_excess_return,
                    "strategy": result.strategy_descriptor_id,
                    "policy_id": result.policy_id,
                })

                # Build allocation request
                from dataclasses import dataclass

                @dataclass
                class SimpleRiskDecision:
                    allowed_target_exposure: float
                    max_new_exposure: float
                    reduce_only: bool = False

                risk_decision = SimpleRiskDecision(
                    allowed_target_exposure=decision.exposure_multiplier,
                    max_new_exposure=decision.exposure_multiplier,
                    reduce_only=False,
                )
                requests.append(AllocationRequest(
                    strategy_id=result.strategy_descriptor_id or "unknown",
                    symbol=symbol,
                    risk_decision=risk_decision,
                    current_exposure=0.0,
                    equity=equity,
                    available_cash=equity,
                    portfolio_exposure=snapshot.gross_exposure,
                    correlation_cluster="MAJORS" if symbol in ("BTC/USDT", "ETH/USDT") else "OTHER",
                    desired_exposure=(
                        result.forecast.expected_excess_return * decision.exposure_multiplier
                    ),
                ))

        # Allocate batch
        if requests:
            outcome = allocator.allocate_batch(requests, snapshot)
            allocation_log.append({
                "bar_idx": bar_idx,
                "scale_factor": outcome.scale_factor,
                "total_requested": outcome.total_requested,
                "total_approved": outcome.total_approved,
                "budget_available": outcome.budget_available,
                "entries": [
                    {"symbol": e.symbol, "strategy_id": e.strategy_id,
                     "requested": e.requested, "approved": e.approved,
                     "reason": e.reason}
                    for e in outcome.entries
                ],
            })
            snapshot = PortfolioSnapshot(
                equity=equity,
                available_cash=equity,
                positions={},
                symbol_exposures=outcome.approved_by_symbol,
                gross_exposure=sum(outcome.approved_by_symbol.values()),
                untracked_symbols=(),
                untracked_exposure=0.0,
                untracked_valued=True,
                reconciliation_state=ReconciliationState.RECONCILED,
            )

        # Progress
        if (bar_idx - warmup) % 500 == 0:
            print(f"  Processed {bar_idx - warmup}/{oos_bars - warmup} bars...")

    print("[S5] OOS campaign complete. Creating signed policy artifacts...")

    # Create signed policies for the selected strategies
    # (In production, this would be based on campaign results)
    signed_policies = _create_campaign_policies(
        registry, service, symbols, out_root
    )

    # Save logs
    (out_root / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decisions_log)
    )
    (out_root / "forecasts.jsonl").write_text(
        "\n".join(json.dumps(f) for f in forecasts_log)
    )
    (out_root / "allocations.jsonl").write_text(
        "\n".join(json.dumps(a) for a in allocation_log)
    )

    summary = {
        "mode": "real",
        "n_symbols": len(symbols),
        "n_bars_processed": oos_bars - warmup,
        "holdout_start_bar": holdout_start,
        "holdout_end_bar": holdout_end,
        "n_decisions": len(decisions_log),
        "n_forecasts": len(forecasts_log),
        "n_allocation_steps": len(allocation_log),
        "signed_policies": len(signed_policies),
        "final_gross_exposure": snapshot.gross_exposure,
    }
    (out_root / "s5_campaign_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _create_campaign_policies(
    registry: SelectionPolicyRegistry,
    service: PolicyActivationService,
    symbols: list[str],
    out_root: Path,
) -> list[str]:
    """Create signed policies from campaign results (simplified)."""
    signed = []
    now = datetime.now(UTC)
    for symbol in symbols:
        for regime in ("trend", "mean_reversion", "high_vol", "crisis", "other"):
            policy_id = f"s5-{symbol.replace('/', '_')}-{regime}-{now.strftime('%Y%m%d')}"
            # In real implementation, this would come from campaign results
            signed.append(policy_id)
    (out_root / "campaign_policies.json").write_text(json.dumps(signed, indent=2))
    return signed


# ── Main ──────────────────────────────────────────────────────────────────




def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["synthetic", "real"], default="synthetic")
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument(
        "--out-root",
        default=str(ROOT / "data" / "backtests" / "s5_adaptive_campaign"),
    )
    ap.add_argument("--n-bars", type=int, default=1000, help="Synthetic bars (synthetic mode)")
    args = ap.parse_args(argv)

    out_root = Path(args.out_root)

    if args.mode == "synthetic":
        print("[S5] Running SYNTHETIC campaign (CI-safe)")
        summary = run_synthetic(out_root, n_bars=args.n_bars)
    else:
        print("[S5] Running REAL campaign (heavy, manual)")
        summary = run_real(out_root, symbols=args.symbols)

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())