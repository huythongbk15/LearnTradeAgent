"""Shadow-mode E2E paper trading for the Strategy Tournament Engine.

Runs every strategy in the tournament pool on historical data per bar,
tracks shadow Sharpe in a rolling window, and logs promotion/demotion
candidates — WITHOUT executing any real orders.  Kill switch
(TOURNAMENT_SHADOW_MODE=1 by default) ensures no live portfolio changes.

Usage:
    python scripts/run_tournament_shadow.py --symbol BTC/USDT --days 3
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure shadow mode (kill switch ON by default)
os.environ.setdefault("TOURNAMENT_SHADOW_MODE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from trading_agent.authority.adaptive_router import (  # noqa: E402
    AdaptiveRouterConfig,
    RouterStateStore,
)
from trading_agent.authority.strategy_tournament import (  # noqa: E402
    StrategyTournament,
    TournamentConfig,
)
from trading_agent.data.storage import load_ohlcv  # noqa: E402
from trading_agent.ml.regime_detection import (  # noqa: E402
    RegimePosterior,
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

logger = logging.getLogger("tournament_shadow")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── Defaults ─────────────────────────────────────────────────────────────

TournamentStrategyParams: dict[str, dict] = {
    "enhanced_ma": {"fast_period": 10, "slow_period": 30, "adx_period": 14, "adx_threshold": 25.0, "atr_period": 14, "atr_sl_mult": 1.5, "atr_tp_mult": 3.0},
    "ma_adx": {"fast_period": 10, "slow_period": 30, "adx_period": 14, "adx_threshold": 25.0},
    "rsi": {"period": 14, "oversold": 30, "overbought": 70, "atr_period": 14, "atr_sl_mult": 2.0, "atr_tp_mult": 3.0},
    "bbands": {"period": 20, "std_dev": 2.0},
    "ma_vol_target": {"fast_period": 10, "slow_period": 30},
}

# Exclude strategies that failed WFO gates
EXCLUDED = ("regime_switching",)  # 12-18 trades < 30 threshold


def _build_registry(tmp_dir: Path, signing_key: bytes, key_id: str,
                    symbol: str, timeframe: str, strategies: list[str]) -> SelectionPolicyRegistry:
    """Build a policy registry with active policies for each regime."""
    registry = SelectionPolicyRegistry(tmp_dir / "policies")
    service = PolicyActivationService(
        registry, signing_key=signing_key, key_id=key_id,
        audit_path=tmp_dir / "audit.jsonl",
    )

    # Canonical regime keys from RegimePosterior.as_mapping
    regimes = [
        "trend",
        "mean_reversion",
        "high_vol",
        "crisis",
        "other",
    ]

    now = datetime.now(UTC)
    # Validity window must span historical replay data (2023) — start far in
    # the past to avoid the router rejecting policies as "not yet valid".
    validity_start = datetime(2020, 1, 1, tzinfo=UTC)
    policy_id = 0
    for sid in strategies:
        params = TournamentStrategyParams.get(sid, {})
        for regime in regimes:
            policy = SelectionPolicyArtifact(
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                incumbent=ParamArtifact(sid, params, code_sha="a" * 64),
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
                evidence_ids=(f"sha256:shadow-{sid}-{regime}",),
                validity_start=validity_start,
                validity_end=now + timedelta(days=90),
                risk_cap=0.25,
                status=PolicyStatus.VALIDATED,
                created_at=now - timedelta(minutes=1),
                policy_commit_sha="b" * 40,
                policy_data_manifest_sha="c" * 64,
                policy_feature_manifest_sha="d" * 64,
                policy_release_digest="sha256:e" * 64,
                promotion_stage="paper_eligible",
            )
            registry.add(policy)
            # Only activate ONE policy per regime (the first = incumbent)
            if policy_id < len(regimes):
                service.activate(
                    policy.policy_id, actor="tournament-init",
                    ticket=f"SHADOW-{policy_id}", now=now,
                )
            policy_id += 1

    return registry


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Strategy Tournament Shadow Run")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument(
        "--live", action="store_true",
        help="ENABLE LIVE promotion/demotion (disables kill switch). "
             "Requires TOURNAMENT_SHADOW_MODE=0 or explicit --live flag.",
    )
    args = parser.parse_args()

    # Kill switch logic:
    #   TOURNAMENT_SHADOW_MODE=1 (default): shadow only, no live changes
    #   TOURNAMENT_SHADOW_MODE=0 OR --live: live promotion/demotion enabled
    env_mode = os.getenv("TOURNAMENT_SHADOW_MODE", "1")
    shadow_mode = env_mode != "0" and not args.live

    if not shadow_mode:
        # Override env so StrategyTournament.__init__ doesn't re-enable shadow mode
        os.environ["TOURNAMENT_SHADOW_MODE"] = "0"
        logger.warning("=" * 60)
        logger.warning("⚠️  LIVE MODE ENABLED — promotion/demotion active!")
        logger.warning("  Kill switch INACTIVE. Tournament will auto-promote")
        logger.warning("  strategies via policy_registry.update().")
        logger.warning("  Set TOURNAMENT_SHADOW_MODE=1 to re-enable shadow mode.")
        logger.warning("=" * 60)

    signing_key = b"tournament-shadow-key"
    key_id = "shadow-release-key"

    tmp_dir = Path(f"data/tournament_shadow/{args.symbol.replace('/', '_')}")
    # Clean up from any previous run
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    pool = {k: v for k, v in FIRST_WAVE_DESCRIPTORS.items() if k not in EXCLUDED}
    pool_strategies = list(pool.keys())

    registry = _build_registry(
        tmp_dir, signing_key, key_id, args.symbol, args.timeframe, pool_strategies
    )

    router = StrategyTournament(
        policy_registry=registry,
        verification_key=signing_key,
        key_id=key_id,
        environment="research",
        state_store=RouterStateStore(tmp_dir / "router_state"),
        audit_path=tmp_dir / "router_audit.jsonl",
        tournament_state_root=tmp_dir / "tournament_state",
        config=AdaptiveRouterConfig(max_policy_age_days=36500),
        tournament_config=TournamentConfig(shadow_mode=shadow_mode),
        pool=pool,
        exclude=EXCLUDED,
    )

    # Load data — keep warmup period before the evaluation window so strategies
    # have enough history for their indicator computations.
    df = load_ohlcv("binance", args.symbol, "1h").sort("timestamp")
    warmup_bars = 200  # Must match the loop start index below
    eval_bars = args.days * 24
    total_needed = eval_bars + warmup_bars
    start_idx = max(0, len(df) - total_needed)
    df = df[start_idx:]

    if len(df) < warmup_bars + 1:
        print(f"Not enough data: {len(df)} bars (need {warmup_bars + 1})")
        return

    logger.info(f"\n{'=' * 60}")
    logger.info("Strategy Tournament Shadow Mode")
    logger.info(f"Symbol: {args.symbol} | Days: {args.days} | Bars: {len(df)}")
    logger.info(f"Pool: {pool_strategies}")
    mode_str = "SHADOW (kill switch)" if shadow_mode else "LIVE (promotion active)"
    logger.info(f"Kill switch: TOURNAMENT_SHADOW_MODE={os.getenv('TOURNAMENT_SHADOW_MODE', 'unset')} | Mode: {mode_str}")
    logger.info(f"{'=' * 60}\n")

    # Simplified regime posterior (trending_up dominant) — fully populated
    # so AdaptiveStrategyRouter.route() accepts it (is_production_ready=True).
    # generated_at is refreshed per-bar inside the loop to stay within the
    # router's freshness window (max_age_seconds=7200 by default).

    shadow_returns: dict[str, list[float]] = {s: [] for s in pool_strategies}
    inc_returns: list[float] = []

    step = 1  # Evaluate every bar
    total_bars = len(df) - 200  # Need warmup

    for i in range(200, len(df), step):
        # Polars Series.item(i) — returns a Python scalar safely on 1.x
        bar_time = df["timestamp"].item(i).replace(tzinfo=UTC)

        # Per-bar posterior with generated_at aligned to the bar so the router's
        # freshness check (max_age_seconds=7200) passes.
        posterior = RegimePosterior(
            p_trend=0.8, p_mean_reversion=0.1,
            p_high_vol=0.05, p_crisis=0.05, p_other=0.0,
            model_id="hybrid-regime-detector-v1",
            fitted_start=bar_time - timedelta(hours=1),
            fitted_end=bar_time - timedelta(minutes=5),
            generated_at=bar_time,
            ood_score=0.0,
        )

        window = df[max(0, i - 120):i + 1].with_columns(
            pl.col("timestamp").dt.replace_time_zone("UTC").alias("time")
        )

        observation = MarketObservation(
            symbol=args.symbol,
            observed_at=bar_time,
            open=float(df["open"].item(i)),
            high=float(df["high"].item(i)),
            low=float(df["low"].item(i)),
            close=float(df["close"].item(i)),
            volume=float(df["volume"].item(i)),
            features={"ohlcv_window": window, "timeframe": args.timeframe},
        )

        prev_close = df["close"].item(i - 1)
        curr_close = df["close"].item(i)
        bar_ret = float((curr_close / prev_close) - 1.0)

        # Get routing decision (parent router handles incumbent/challenger)
        decision = router.route(
            symbol=args.symbol,
            timeframe=args.timeframe,
            posterior=posterior,
            observed_at=bar_time,
            position_is_flat=True,
            observation=observation,
            bar_return=bar_ret,
        )

        chosen = decision.chosen_strategy_id or "NONE"

        # Track shadow returns for each strategy in the pool
        for sid in pool_strategies:
            fc = router.shadow_forecast(sid, observation)
            if fc is not None and fc.expected_excess_return != 0:
                weight = 1.0 if fc.expected_excess_return > 0 else -1.0
                shadow_returns[sid].append(bar_ret * weight)
            else:
                shadow_returns[sid].append(0.0)

        inc_ret = bar_ret if chosen != "NONE" else 0.0
        inc_returns.append(inc_ret)

        # Progress every ~10%
        pct = (i - 200) / total_bars * 100
        if i % max(10, total_bars // 10) == 0:
            logger.info(f"  Bar {i}/{len(df)} ({pct:.0f}%) | chosen={chosen}")

    # ── Final shadow performance summary ─────────────────────────────────
    logger.info(f"\n{'=' * 60}")
    logger.info("Shadow Performance Summary")
    logger.info(f"{'=' * 60}")

    incumbent_sharpe = 0.0
    for sid in pool_strategies:
        rets = shadow_returns[sid]
        if len(rets) < 2:
            continue
        mean = float(np.mean(rets))
        std = float(np.std(rets, ddof=1))
        sharpe = mean / std * np.sqrt(8760) if std > 0 else 0.0
        total_ret = float(np.prod([1 + r for r in rets]) - 1.0)
        non_zero = sum(1 for r in rets if r != 0)
        flag = "🎯 PROMOTE" if sharpe > 0.3 else ""
        logger.info(f"  {sid:25s} Sharpe={sharpe:+.2f}  Return={total_ret:+.1f}%  "
                     f"ActiveBars={non_zero}/{len(rets)}  {flag}")

    inc_mean = float(np.mean(inc_returns)) if inc_returns else 0.0
    inc_std = float(np.std(inc_returns, ddof=1)) if len(inc_returns) > 1 else 0.0
    inc_sharpe = inc_mean / inc_std * np.sqrt(8760) if inc_std > 0 else 0.0
    inc_total = float(np.prod([1 + r for r in inc_returns]) - 1.0) if inc_returns else 0.0
    logger.info(f"\n  {'INCUMBENT (router decision)':25s} Sharpe={inc_sharpe:+.2f}  Return={inc_total:+.1f}%")

    logger.info(f"\n  Kill switch: {'ACTIVE' if router.tournament_config.shadow_mode else 'INACTIVE'}")
    logger.info(f"  Tournament state: {tmp_dir / 'tournament_state'}")

    # Save shadow results
    shadow_perf = {}
    for sid, rets in shadow_returns.items():
        if len(rets) < 2:
            continue
        std = float(np.std(rets, ddof=1))
        shadow_perf[sid] = {
            "sharpe": float(np.mean(rets) / std * np.sqrt(8760)) if std > 0 else 0.0,
            "total_return": float(np.prod([1 + r for r in rets]) - 1.0),
            "active_bars": int(sum(1 for r in rets if r != 0)),
            "total_bars": len(rets),
        }

    results = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "days": args.days,
        "bars_evaluated": total_bars,
        "shadow_mode": shadow_mode,
        "pool_strategies": pool_strategies,
        "excluded": list(EXCLUDED),
        "shadow_performance": shadow_perf,
    }
    with open(tmp_dir / "shadow_results.json", "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results saved to: {tmp_dir / 'shadow_results.json'}")

    # Cleanup temp policy files
    if (tmp_dir / "policies").exists():
        shutil.rmtree(tmp_dir / "policies")


if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=FutureWarning)
    main()
