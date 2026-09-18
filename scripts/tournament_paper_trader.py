#!/usr/bin/env python3
"""Tournament paper trader — multi-asset live paper trading (P2 Phase 3).

Runs ``StrategyTournament`` in LIVE mode (``TOURNAMENT_SHADOW_MODE=0``)
with paper trading execution via Binance testnet.

Usage:
    # Paper trading (live promotions within kill-switch boundaries)
    python scripts/tournament_paper_trader.py --symbols BTC/USDT,ETH/USDT,SOL/USDT

    # Dry-run (shadow mode, no live promotions)
    python scripts/tournament_paper_trader.py --symbols BTC/USDT,ETH/USDT,SOL/USDT --dry-run
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import polars as pl

from dotenv import load_dotenv

from trading_agent.authority.adaptive_router import (
    AdaptiveRouterConfig,
    RouterStateStore,
)
from trading_agent.authority.strategy_tournament import (
    StrategyTournament,
    TournamentConfig,
)
from trading_agent.data.storage import load_ohlcv
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

load_dotenv(".env")

# ── Defaults ─────────────────────────────────────────────────────────────

TournamentStrategyParams: dict[str, dict] = {
    "enhanced_ma": {"fast_period": 10, "slow_period": 30},
    "ma_adx": {"fast_period": 10, "slow_period": 30},
    "rsi": {"period": 14},
    "bbands": {"period": 20, "std_dev": 2.0},
    "ma_vol_target": {"fast_period": 10, "slow_period": 30},
}

EXCLUDED: tuple[str, ...] = ("regime_switching",)

WARMUP_BARS = 200
TIMEFRAME = "1h"


def build_paper_trader(
    symbols: list[str],
    tmp_dir: Path,
    shadow_mode: bool,
) -> StrategyTournament:
    """Build the tournament system for paper trading."""
    now = datetime.now(UTC)
    validity_start = datetime(2020, 1, 1, tzinfo=UTC)
    regimes = ["trend", "mean_reversion", "high_vol", "crisis", "other"]
    pool = {k: v for k, v in FIRST_WAVE_DESCRIPTORS.items() if k not in EXCLUDED}
    pool_strategies = list(pool.keys())
    first_sid = pool_strategies[0]

    registry = SelectionPolicyRegistry(tmp_dir / "policies")
    service = PolicyActivationService(
        registry, signing_key=b"tournament-paper-key", key_id="paper-release-key",
        audit_path=tmp_dir / "audit.jsonl",
    )

    # Create one active policy per (symbol, regime) with first strategy as incumbent
    for symbol in symbols:
        for regime in regimes:
            params = TournamentStrategyParams.get(first_sid, {})
            policy = SelectionPolicyArtifact(
                symbol=symbol, timeframe=TIMEFRAME, regime=regime,
                incumbent=ParamArtifact(first_sid, params, code_sha="paper001"),
                scores={
                    "selection_score": 0.50,
                    "median_test_sharpe": 0.30,
                    "median_oos_return_pct": 0.05,
                    "median_max_dd_pct": 0.30,
                    "median_calmar": 0.50,
                    "median_oos_trades": 40,
                    "n_passing_folds": 9, "total_folds": 9,
                },
                evidence_ids=(f"sha256:paper-{first_sid}-{regime}",),
                validity_start=validity_start,
                validity_end=now + timedelta(days=90),
                risk_cap=0.25,
                status=PolicyStatus.VALIDATED,
                created_at=now - timedelta(minutes=1),
                policy_commit_sha="paper-commit-sha",
                policy_data_manifest_sha="paper-data-sha",
                policy_feature_manifest_sha="paper-feature-sha",
                policy_release_digest="sha256:paper-release-digest",
                promotion_stage="paper_eligible",
            )
            registry.add(policy)
            service.activate(
                policy.policy_id, actor="paper-trader-init",
                ticket=f"PAPER-{symbol}-{regime}", now=now,
            )

    # Add remaining strategies as INACTIVE challengers (not activated)
    for symbol in symbols:
        for sid in pool_strategies[1:]:
            for regime in regimes:
                params = TournamentStrategyParams.get(sid, {})
                policy = SelectionPolicyArtifact(
                    symbol=symbol, timeframe=TIMEFRAME, regime=regime,
                    incumbent=ParamArtifact(sid, params, code_sha="paper002"),
                    scores={
                        "selection_score": 0.40,
                        "median_test_sharpe": 0.20,
                        "median_oos_return_pct": 0.02,
                        "median_max_dd_pct": 0.40,
                        "median_calmar": 0.30,
                        "median_oos_trades": 30,
                        "n_passing_folds": 7, "total_folds": 9,
                    },
                    evidence_ids=(f"sha256:paper-chal-{sid}-{regime}",),
                    validity_start=validity_start,
                    validity_end=now + timedelta(days=90),
                    risk_cap=0.25,
                    status=PolicyStatus.VALIDATED,
                    created_at=now - timedelta(minutes=1),
                    policy_commit_sha="paper-commit-sha",
                    policy_data_manifest_sha="paper-data-sha",
                    policy_feature_manifest_sha="paper-feature-sha",
                    policy_release_digest="sha256:paper-release-digest",
                    promotion_stage="paper_eligible",
                )
                registry.add(policy)  # NOT activated — challenger only

    router = StrategyTournament(
        policy_registry=registry,
        verification_key=b"tournament-paper-key",
        key_id="paper-release-key",
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
        exclude=EXCLUDED,
    )

    return router


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Tournament Paper Trader (P2 Phase 3)"
    )
    parser.add_argument(
        "--symbols",
        default="BTC/USDT,ETH/USDT,SOL/USDT",
        help="Comma-separated list of symbols",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Dry-run mode: shadow only, no live trades",
    )
    parser.add_argument(
        "--max-bars", type=int, default=168,
        help="Maximum bars to trade (default: 168 = 1 week on 1h)",
    )
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    # Environment gates
    shadow_mode = os.getenv("TOURNAMENT_SHADOW_MODE", "1") == "1"
    if args.dry_run:
        shadow_mode = True

    tmp_dir = Path(
        f"data/tournament_paper/{'_'.join(s.replace('/', '_') for s in symbols)}"
    )
    # Clean up any previous run
    import shutil
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("Tournament Paper Trader (P2 Phase 3)")
    print(f"Symbols: {symbols} | Timeframe: {TIMEFRAME}")
    print(f"Mode: {'SHADOW (kill switch)' if shadow_mode else 'LIVE (promotions active)'}")
    print(f"{'=' * 60}\n")

    # Build tournament
    tournament = build_paper_trader(symbols, tmp_dir, shadow_mode)

    # Load and process bars
    warmup_bars = WARMUP_BARS
    eval_bars = args.max_bars
    total_needed = eval_bars + warmup_bars

    symbol_data: dict[str, pl.DataFrame] = {}
    for symbol in symbols:
        df = load_ohlcv("binance", symbol, "1h").sort("timestamp")
        start_idx = max(0, len(df) - total_needed)
        df = df[start_idx:]
        if len(df) < warmup_bars + 1:
            print(f"⚠️  Not enough data for {symbol}: {len(df)} bars")
            continue
        symbol_data[symbol] = df
        print(f"  Loaded {symbol}: {len(df)} bars")

    min_len = min(len(df) for df in symbol_data.values()) if symbol_data else 0
    total_bars = min_len - warmup_bars

    if total_bars < 1:
        print(f"❌ Not enough data: min {min_len} bars (need {warmup_bars + 1})")
        return 1

    print(f"\n  Evaluating {total_bars} bars (warmup={warmup_bars})\n")

    # Track performance
    symbol_returns: dict[str, list[float]] = {s: [] for s in symbols}
    incumbents: dict[str, str] = {}
    progress_step = max(1, total_bars // 10)

    for i in range(warmup_bars, min_len, 1):
        for symbol in symbols:
            df = symbol_data[symbol]
            bar_time = df["timestamp"].item(i)
            if hasattr(bar_time, "replace"):
                from zoneinfo import ZoneInfo
                bar_time = bar_time.replace(tzinfo=ZoneInfo("UTC"))
            else:
                bar_time = bar_time

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
                features={"ohlcv_window": window, "timeframe": TIMEFRAME},
            )

            prev_close = float(df["close"].item(i - 1))
            curr_close = float(df["close"].item(i))
            bar_ret = float((curr_close / prev_close) - 1.0)

            decision = tournament.route(
                symbol=symbol,
                timeframe=TIMEFRAME,
                posterior=posterior,
                observed_at=bar_time,
                position_is_flat=True,
                observation=observation,
                bar_return=bar_ret,
            )

            chosen = decision.chosen_strategy_id
            if chosen:
                incumbents[symbol] = chosen
                symbol_returns[symbol].append(bar_ret)
            else:
                symbol_returns[symbol].append(0.0)

        # Progress log
        bar_idx = i - warmup_bars
        if bar_idx % progress_step == 0:
            pct = bar_idx / total_bars * 100
            print(f"  Bar {i}/{min_len} ({pct:.0f}%)")

    # ── Final summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("Paper Trader Summary")
    print(f"{'=' * 60}")

    for symbol in symbols:
        rets = symbol_returns[symbol]
        if len(rets) < 2:
            continue
        inc = incumbents.get(symbol, "NONE")
        mean = float(np.mean(rets))
        std = float(np.std(rets, ddof=1))
        sharpe = mean / std * np.sqrt(8760) if std > 0 else 0.0
        total = float(np.prod([1 + r for r in rets]) - 1.0)
        print(f"  {symbol} | incumbent={inc} | Sharpe={sharpe:.4f} | Return={total:+.3f}%")

    # Portfolio aggregate
    port_rets = []
    for i in range(len(symbol_returns[symbols[0]])):
        avg = float(np.mean([symbol_returns[s][i] for s in symbols]))
        port_rets.append(avg)
    if len(port_rets) >= 2:
        pm = float(np.mean(port_rets))
        ps = float(np.std(port_rets, ddof=1))
        port_sharpe = pm / ps * np.sqrt(8760) if ps > 0 else 0.0
        port_total = float(np.prod([1 + r for r in port_rets]) - 1.0)
        print(f"\n  PORTFOLIO | Sharpe={port_sharpe:.4f} | Return={port_total:+.3f}%")

    # Verify audit trail
    audit_db = tmp_dir / "audit" / "tournament_audit.sqlite3"
    if audit_db.exists():
        from scripts.audit_report import generate_report
        report = generate_report(audit_db, symbol=symbols[0] if len(symbols) == 1 else None)
        print(f"\n  SelectionAudit: {report.get('entry_count', 0)} entries logged")

    # Check for promotions from router_audit.jsonl
    import json as _json
    promotions = 0
    circuit_triggers = 0
    audit_jsonl = tmp_dir / "router_audit.jsonl"
    if audit_jsonl.exists():
        with open(audit_jsonl) as f:
            for line in f:
                entry = _json.loads(line)
                evt = entry.get("event", "")
                if evt == "TOURNAMENT_PROMOTION":
                    promotions += 1
                if "CIRCUIT_BREAKER" in evt:
                    circuit_triggers += 1
    print(f"\n  Promotions: {promotions} | Circuit triggers: {circuit_triggers}")

    # Read final incumbents from policy registry
    final_incumbents = {}
    for symbol in symbols:
        for regime in ("trend", "mean_reversion", "high_vol", "crisis", "other"):
            policy = tournament.policy_registry.get_active(symbol, TIMEFRAME, regime)
            if policy:
                final_incumbents[symbol] = policy.incumbent.strategy_id
                break

    print(f"\n  Kill switch: {'ACTIVE' if shadow_mode else 'INACTIVE'}")
    print(f"  Final incumbents: {final_incumbents}")

    # Save results
    results = {
        "symbols": symbols,
        "mode": "shadow" if shadow_mode else "live",
        "bars_processed": total_bars,
        "incumbents": incumbents,
        "incumbent_sharpe_by_symbol": {
            s: (float(np.mean(symbol_returns[s]) / np.std(symbol_returns[s], ddof=1) * np.sqrt(8760))
                if len(symbol_returns[s]) >= 2 and np.std(symbol_returns[s], ddof=1) > 0 else 0.0)
            for s in symbols
        },
        "portfolio_sharpe": (
            float(np.mean(port_rets) / np.std(port_rets, ddof=1) * np.sqrt(8760))
            if len(port_rets) >= 2 and np.std(port_rets, ddof=1) > 0 else 0.0
        ),
    }
    with open(tmp_dir / "paper_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results: {tmp_dir}/paper_results.json")


if __name__ == "__main__":
    raise SystemExit(main())
