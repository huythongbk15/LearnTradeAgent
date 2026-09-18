"""Shadow-mode E2E paper trading for the Strategy Tournament Engine.

Runs every strategy in the tournament pool on historical data per bar,
tracks shadow Sharpe in a rolling window, and logs promotion/demotion
candidates — WITHOUT executing any real orders.  Kill switch
(TOURNAMENT_SHADOW_MODE=1 by default) ensures no live portfolio changes.

Usage:
    python scripts/run_tournament_shadow.py --symbol BTC/USDT --days 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
    parser = argparse.ArgumentParser(description="Strategy Tournament Shadow Run")
    parser.add_argument("--symbol", default="BTC/USDT",
                        help="Single symbol (backward compatible)")
    parser.add_argument("--symbols", default=None,
                        help="Comma-separated list of symbols, e.g. BTC/USDT,ETH/USDT,SOL/USDT "
                             "(overrides --symbol for multi-asset shadow run)")
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

    # Resolve symbols list
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = [args.symbol]

    tmp_dir = Path(
        f"data/tournament_shadow/multi_{'_'.join(s.replace('/', '_') for s in symbols)}"
    )
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    pool = {k: v for k, v in FIRST_WAVE_DESCRIPTORS.items() if k not in EXCLUDED}
    pool_strategies = list(pool.keys())

    # ── Build tournament with policies for ALL symbols ───────────────────
    registry = SelectionPolicyRegistry(tmp_dir / "policies")
    service = PolicyActivationService(
        registry, signing_key=signing_key, key_id=key_id,
        audit_path=tmp_dir / "audit.jsonl",
    )

    regimes = ["trend", "mean_reversion", "high_vol", "crisis", "other"]
    now = datetime.now(UTC)
    validity_start = datetime(2020, 1, 1, tzinfo=UTC)

    # One active policy per (symbol, regime) pair, using the first
    # strategy in the pool as the incumbent. Remaining strategies are
    # added as non-active challengers.
    first_sid = pool_strategies[0]
    params = TournamentStrategyParams.get(first_sid, {})

    for symbol in symbols:
        for regime in regimes:
            policy = SelectionPolicyArtifact(
                symbol=symbol, timeframe=args.timeframe, regime=regime,
                incumbent=ParamArtifact(first_sid, params, code_sha="a" * 64),
                scores={
                    "selection_score": 0.50,
                    "median_test_sharpe": 0.30,
                    "median_oos_return_pct": 0.05,
                    "median_max_dd_pct": 0.30,
                    "median_calmar": 0.50,
                    "median_oos_trades": 40,
                    "n_passing_folds": 9, "total_folds": 9,
                },
                evidence_ids=(f"sha256:shadow-{first_sid}-{regime}",),
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
            service.activate(
                policy.policy_id, actor="tournament-init",
                ticket=f"SHADOW-{symbol}-{regime}", now=now,
            )

    # Add remaining strategies as inactive (challengers only)
    for symbol in symbols:
        for sid in pool_strategies[1:]:
            for regime in regimes:
                params = TournamentStrategyParams.get(sid, {})
                policy = SelectionPolicyArtifact(
                    symbol=symbol, timeframe=args.timeframe, regime=regime,
                    incumbent=ParamArtifact(sid, params, code_sha="f" * 64),
                    scores={
                        "selection_score": 0.40,
                        "median_test_sharpe": 0.20,
                        "median_oos_return_pct": 0.02,
                        "median_max_dd_pct": 0.40,
                        "median_calmar": 0.30,
                        "median_oos_trades": 30,
                        "n_passing_folds": 7, "total_folds": 9,
                    },
                    evidence_ids=(f"sha256:shadow-chal-{sid}-{regime}",),
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

    # ── Load data for all symbols ─────────────────────────────────────────
    warmup_bars = 200
    eval_bars = args.days * 24
    total_needed = eval_bars + warmup_bars

    symbol_data: dict[str, "pl.DataFrame"] = {}
    for symbol in symbols:
        df = load_ohlcv("binance", symbol, "1h").sort("timestamp")
        start_idx = max(0, len(df) - total_needed)
        df = df[start_idx:]
        if len(df) < warmup_bars + 1:
            logger.warning(f"  Not enough data for {symbol}: {len(df)} bars")
            continue
        symbol_data[symbol] = df
        logger.info(f"  Loaded {symbol}: {len(df)} bars")

    min_len = min(len(df) for df in symbol_data.values()) if symbol_data else 0
    if min_len < warmup_bars + 1:
        print(f"Not enough data for any symbol: min {min_len} bars")
        return

    logger.info(f"\n{'=' * 60}")
    logger.info("Strategy Tournament Shadow Mode (Multi-Asset)")
    logger.info(f"Symbols: {symbols} | Days: {args.days} | Timeframe: {args.timeframe} | Bars: {min_len}")
    logger.info(f"Pool: {pool_strategies}")
    mode_str = "SHADOW (kill switch)" if shadow_mode else "LIVE (promotion active)"
    logger.info(f"Kill switch: TOURNAMENT_SHADOW_MODE={os.getenv('TOURNAMENT_SHADOW_MODE', 'unset')} | Mode: {mode_str}")
    logger.info(f"{'=' * 60}\n")

    shadow_returns: dict[str, dict[str, list[float]]] = {
        s: {sid: [] for sid in pool_strategies} for s in symbols
    }
    inc_returns: dict[str, list[float]] = {s: [] for s in symbols}

    step = 1  # Evaluate every bar
    total_bars = min_len - warmup_bars

    for i in range(warmup_bars, min_len, step):
        for symbol in symbols:
            df = symbol_data[symbol]
            bar_time = df["timestamp"].item(i).replace(tzinfo=UTC)

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
                symbol=symbol,
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

            decision = router.route(
                symbol=symbol,
                timeframe=args.timeframe,
                posterior=posterior,
                observed_at=bar_time,
                position_is_flat=True,
                observation=observation,
                bar_return=bar_ret,
            )

            chosen = decision.chosen_strategy_id or "NONE"

            for sid in pool_strategies:
                fc = router.shadow_forecast(sid, observation)
                if fc is not None and fc.expected_excess_return != 0:
                    weight = 1.0 if fc.expected_excess_return > 0 else -1.0
                    shadow_returns[symbol][sid].append(bar_ret * weight)
                else:
                    shadow_returns[symbol][sid].append(0.0)

            inc_ret = bar_ret if chosen != "NONE" else 0.0
            inc_returns[symbol].append(inc_ret)

        # Progress every ~10%
        pct = (i - warmup_bars) / total_bars * 100
        if (i - warmup_bars) % max(10, total_bars // 10) == 0:
            logger.info(f"  Bar {i}/{min_len} ({pct:.0f}%) | {symbol} chosen={chosen}")

    # ── Final shadow performance summary ─────────────────────────────────
    logger.info(f"\n{'=' * 60}")
    logger.info("Shadow Performance Summary (Portfolio-Aggregated)")
    logger.info(f"{'=' * 60}")

    # Aggregate shadow returns across all symbols per strategy
    for sid in pool_strategies:
        agg_rets: list[float] = []
        for symbol in symbols:
            rets = shadow_returns[symbol][sid]
            if len(rets) < 2:
                continue
            agg_rets.extend(rets)
        if len(agg_rets) < 2:
            continue
        mean = float(np.mean(agg_rets))
        std = float(np.std(agg_rets, ddof=1))
        sharpe = mean / std * np.sqrt(8760) if std > 0 else 0.0
        total_ret = float(np.prod([1 + r for r in agg_rets]) - 1.0)
        non_zero = sum(1 for r in agg_rets if r != 0)
        flag = "🎯 PROMOTE" if sharpe > 0.3 else ""
        logger.info(f"  {sid:25s} Sharpe={sharpe:+.6f}  Return={total_ret:+.1f}%  "
                     f"ActiveBars={non_zero}/{len(agg_rets)}  {flag}")

    # Portfolio-level (equal-weighted across symbols)
    portfolio_rets: list[float] = []
    for i in range(total_bars):
        symbol_rets = []
        for symbol in symbols:
            if inc_returns[symbol]:
                symbol_rets.append(inc_returns[symbol][i % len(inc_returns[symbol])])
        if symbol_rets:
            portfolio_rets.append(float(np.mean(symbol_rets)))

    inc_sharpe_by_symbol: dict[str, float] = {}
    for symbol in symbols:
        rets = inc_returns[symbol]
        if len(rets) < 2:
            inc_sharpe_by_symbol[symbol] = 0.0
            continue
        mean = float(np.mean(rets))
        std = float(np.std(rets, ddof=1))
        inc_sharpe_by_symbol[symbol] = mean / std * np.sqrt(8760) if std > 0 else 0.0

    if portfolio_rets:
        pm = float(np.mean(portfolio_rets))
        ps = float(np.std(portfolio_rets, ddof=1))
        port_sharpe = pm / ps * np.sqrt(8760) if ps > 0 else 0.0
        port_total = float(np.prod([1 + r for r in portfolio_rets]) - 1.0)
        logger.info(f"\n  {'PORTFOLIO (avg)':25s} Sharpe={port_sharpe:+.6f}  Return={port_total:+.1f}%")

    for symbol in symbols:
        rets = inc_returns[symbol]
        if len(rets) < 2:
            continue
        total = float(np.prod([1 + r for r in rets]) - 1.0)
        logger.info(f"  {symbol:25s} Sharpe={inc_sharpe_by_symbol[symbol]:+.6f}  Return={total:+.1f}%")

    logger.info(f"\n  Kill switch: {'ACTIVE' if router.tournament_config.shadow_mode else 'INACTIVE'}")
    logger.info(f"  Tournament state: {tmp_dir / 'tournament_state'}")
    logger.info(f"  Audit DB: {tmp_dir / 'audit/tournament_audit.sqlite3'}")

    # Save shadow results
    shadow_perf: dict[str, Any] = {}
    for sid in pool_strategies:
        agg_rets = []
        per_symbol = {}
        for symbol in symbols:
            rets = shadow_returns[symbol][sid]
            per_symbol[symbol] = {
                "sharpe": (float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(8760))
                          if len(rets) >= 2 and np.std(rets, ddof=1) > 0 else 0.0),
                "total_return": float(np.prod([1 + r for r in rets]) - 1.0) if rets else 0.0,
                "active_bars": int(sum(1 for r in rets if r != 0)),
                "total_bars": len(rets),
            }
            agg_rets.extend(rets)
        if len(agg_rets) >= 2:
            std = float(np.std(agg_rets, ddof=1))
            shadow_perf[sid] = {
                "portfolio_sharpe": float(np.mean(agg_rets) / std * np.sqrt(8760)) if std > 0 else 0.0,
                "total_return": float(np.prod([1 + r for r in agg_rets]) - 1.0),
                "active_bars": int(sum(1 for r in agg_rets if r != 0)),
                "total_bars": len(agg_rets),
                "per_symbol": per_symbol,
            }

    results = {
        "symbols": symbols,
        "timeframe": args.timeframe,
        "days": args.days,
        "bars_evaluated": total_bars,
        "shadow_mode": shadow_mode,
        "incumbent_by_symbol": {
            s: (router._live_state.get((s, args.timeframe)) or type("S", (), {"incumbent_strategy_id": "unknown"})).incumbent_strategy_id
            for s in symbols
        },
        "incumbent_sharpe_by_symbol": inc_sharpe_by_symbol,
        "pool_strategies": pool_strategies,
        "excluded": list(EXCLUDED),
        "shadow_performance": shadow_perf,
    }
    with open(tmp_dir / "shadow_results.json", "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results saved to: {tmp_dir / 'shadow_results.json'}")

    # Run audit report on the SelectionAudit database
    audit_db = tmp_dir / "audit" / "tournament_audit.sqlite3"
    if audit_db.exists():
        from scripts.audit_report import generate_report
        report = generate_report(audit_db, symbol=symbols[0] if len(symbols) == 1 else None)
        logger.info(f"\n  SelectionAudit: {report.get('entry_count', 0)} entries logged")

    # Cleanup temp policy files
    if (tmp_dir / "policies").exists():
        shutil.rmtree(tmp_dir / "policies")


if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=FutureWarning)
    main()
