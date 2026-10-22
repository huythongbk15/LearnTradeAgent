#!/usr/bin/env python3
"""Validation-only WFO with an auditable, immutable evidence packet.

Design rules (STR-0304/STR-0308 + audit findings 2026-10-20):
  * The packet directory MUST NOT already exist — evidence is immutable and a
    re-run must never overwrite a previous packet.
  * Every fold produces a receipt (window geometry + full metrics + gate
    verdict) so the headline numbers can be re-verified from disk.
  * The selection contract threshold (min_validation_trades) defaults to the
    project contract value 30; 15 is only reachable via an explicit flag and
    is recorded in the packet as a non-contract variant.
  * Cells run through the canonical ``run_cell`` (no inline/serialisation
    bypass), so the packet reflects the production execution path.
  * Scope is INNER_VALIDATION ONLY. No outer OOS, no holdout, no sensitivity,
    no promotion, no live.

Usage:
    .venv/bin/python scripts/run_wfo_validation.py --out data/<new_packet_dir> \
        [--min-trades 30] [--folds 3] [--cost 1x]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_execution import (
    execution_environment,
    simulator_runtime_config,
)
from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.native_gate_contract import freeze_native_gate_contract
from trading_agent.backtest.nested_wfo import (
    _default_purge_embargo,
    _get_fold_indices,
    run_nested_wfo,
    WFOSpec,
)
from trading_agent.backtest.selection_eligibility import (
    assess_validation_candidates,
    freeze_selection_contract,
)
from trading_agent.backtest.tournament import (
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_TRIPLE,
)
from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.canonical.candidates import build_default_registry

# FROZEN configuration. These values were selected on tournament data during
# the 2026-10-19 screening round; they are NOT independent OOS and must not be
# re-selected here. Changing them invalidates the comparison.
PARAM_GRID = {
    "rsi_period": [14],
    "rsi_oversold": [35],
    "rsi_overbought": [65],
    "ma_fast": [20],
    "ma_slow": [50],
    "adx_period": [14],
    "adx_threshold": [15],
    "ma_type": ["sma"],
}

COST_SCENARIOS = {
    "1x": (SCENARIO_BASE,),
    "2x": (SCENARIO_DOUBLE,),
    "3x": (SCENARIO_TRIPLE,),
}

METRIC_KEYS = (
    "total_trades",
    "sharpe",
    "total_return_pct",
    "max_drawdown_pct",
    "profit_factor",
    "win_rate",
)


def _metrics_subset(metrics: dict) -> dict:
    return {key: metrics.get(key) for key in METRIC_KEYS}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="NEW packet directory (must not exist)")
    parser.add_argument("--min-trades", type=int, default=30,
                        help="min_validation_trades; contract value is 30")
    parser.add_argument("--folds", type=int, default=3, help="number of inner folds to run")
    parser.add_argument("--cost", choices=sorted(COST_SCENARIOS), default="1x")
    parser.add_argument("--cell-timeout", type=int, default=900,
                        help="per-cell wall-clock budget enforced by the multiprocessing path")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--val-months", type=int, default=3)
    args = parser.parse_args()

    out_root = Path(args.out)
    if out_root.exists():
        print(f"REFUSED: {out_root} already exists — research packets are immutable.")
        return 2
    out_root.mkdir(parents=True)

    registry = build_default_registry()
    descriptor = registry.describe("rsi_adx_regime_filter")
    purge, embargo = _default_purge_embargo(descriptor)

    df = load_ohlcv("binance", args.symbol, args.timeframe)
    folds = _get_fold_indices(
        n_bars=df.height,
        timeframe=args.timeframe,
        train_months=12,
        val_months=args.val_months,
        test_months=3,
        step_months=3,
        purge=purge,
        embargo=embargo,
    )
    folds = folds[: args.folds]
    print(f"data={df.height} bars | folds={len(folds)} | cost={args.cost} | "
          f"min_validation_trades={args.min_trades}")

    spec = WFOSpec(
        strategy_id="rsi_adx_regime_filter",
        symbol=args.symbol,
        timeframe=args.timeframe,
        param_grid=PARAM_GRID,
        cost_scenarios=COST_SCENARIOS[args.cost],
        train_months=12,
        val_months=args.val_months,
        test_months=3,
        step_months=3,
        purge_bars=purge,
        embargo_bars=embargo,
        min_trades_per_fold=args.min_trades,
        min_oos_trades=args.min_trades,
        registry_path="data/wfo/experiments.sqlite3",
        search_family="s3_regime_wfo",
        evaluator_version="v1",
        seed=42,
        evidence_class="REAL_MARKET",
    )

    source = capture_campaign_source(Path("."))
    env = execution_environment()
    rt = dict(simulator_runtime_config())
    rt["cell_timeout_seconds"] = args.cell_timeout
    rt["max_retries"] = 0

    protocol_body = {
        "version": 1,
        "authority": "run_nested_wfo",
        "spec": spec.to_dict(),
        "source": source,
        "scope": "inner_validation_only_not_qualification",
    }
    protocol_fingerprint = content_hash(protocol_body)

    execution_context = {
        "trial_identity_version": 2,
        "protocol_fingerprint": protocol_fingerprint,
        "source": source,
        "environment": env,
        "runtime_config": rt,
    }

    selection_contract = freeze_selection_contract(
        min_validation_trades=args.min_trades,
        min_validation_sharpe=0.0,
    )
    native_gate_contract = freeze_native_gate_contract(spec, run_holdout=False)

    print("\n--- running validation-only WFO (canonical run_cell path) ---")
    result = run_nested_wfo(
        spec,
        out_root=out_root,
        run_holdout=False,
        real_sensitivity=False,
        validation_only=True,
        native_gate_contract=native_gate_contract,
        selection_contract=selection_contract,
        execution_context=execution_context,
        folds=folds,
    )

    # ---- per-fold receipts -------------------------------------------------
    receipts = []
    for inner in result.inner_results:
        # The canonical decision is computed by the same function the WFO
        # engine uses, so the receipt cannot disagree with the engine.
        decision = assess_validation_candidates(
            selection_contract, inner.candidate_metrics
        )
        eligible_rows = [row for row in decision["candidates"] if row.get("eligible")]
        # ``val_metrics`` is empty when no candidate is eligible (ABSTAIN).
        # The measured metrics still exist on the candidate rows.
        metrics = inner.val_metrics or {}
        if not metrics:
            for candidate in inner.candidate_metrics:
                candidate_metrics = candidate.get("val_metrics") or {}
                if candidate_metrics:
                    metrics = candidate_metrics
                    break
        trades = metrics.get("total_trades", 0)
        sharpe = metrics.get("sharpe")
        trades_ok = isinstance(trades, int) and trades >= args.min_trades
        sharpe_ok = sharpe is not None and sharpe > selection_contract["min_validation_sharpe"]
        reasons = sorted({reason for row in decision["candidates"] for reason in row.get("reasons", [])})
        receipts.append({
            "fold_id": inner.fold_id,
            "train_window": [inner.train_start, inner.train_end],
            "validation_window": [inner.val_start, inner.val_end],
            "validation_window_bars": inner.val_end - inner.val_start,
            "best_params": inner.best_params,
            "best_val_sharpe": (
                inner.best_val_sharpe if inner.best_val_sharpe not in (None, float("-inf")) else None
            ),
            "n_trials": inner.n_trials,
            "metrics": _metrics_subset(metrics),
            "gate": {
                "min_validation_trades": args.min_trades,
                "trades_ok": trades_ok,
                "min_validation_sharpe": selection_contract["min_validation_sharpe"],
                "sharpe_ok": sharpe_ok,
                "eligible": bool(eligible_rows),
                "reasons": reasons,
                "decision": decision.get("decision"),
            },
            "candidates": [
                {
                    "params": candidate.get("params"),
                    "cost_scenario": candidate.get("cost_scenario"),
                    "val_sharpe": candidate.get("val_sharpe"),
                    "metrics": _metrics_subset(candidate.get("val_metrics") or {}),
                }
                for candidate in inner.candidate_metrics
            ],
        })

    contract_compliant = args.min_trades == 30
    # Threshold sensitivity is a DECISION-layer view over the same measured
    # metrics — min_validation_trades changes SELECT/ABSTAIN only, never the
    # measurements. Recording 15 alongside 30 avoids a second run while making
    # the contract-compliant verdict the primary one.
    gate_variants = {}
    for threshold in (15, 30):
        per_fold = []
        for receipt in receipts:
            trades = receipt["metrics"].get("total_trades")
            sharpe = receipt["metrics"].get("sharpe")
            trades_ok = isinstance(trades, int) and trades >= threshold
            sharpe_ok = sharpe is not None and sharpe > selection_contract["min_validation_sharpe"]
            per_fold.append({
                "fold_id": receipt["fold_id"],
                "trades": trades,
                "sharpe": sharpe,
                "trades_ok": trades_ok,
                "sharpe_ok": sharpe_ok,
                "eligible": trades_ok and sharpe_ok,
            })
        gate_variants[str(threshold)] = {
            "min_validation_trades": threshold,
            "is_contract_value": threshold == 30,
            "folds_eligible": sum(1 for row in per_fold if row["eligible"]),
            "folds_total": len(per_fold),
            "per_fold": per_fold,
        }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "inner_validation_only_not_qualification",
        "strategy_id": "rsi_adx_regime_filter",
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "frozen_param_grid": PARAM_GRID,
        "cost_scenarios": [args.cost],
        "selection_contract": selection_contract,
        "native_gate_contract_fingerprint": native_gate_contract.get("fingerprint"),
        "protocol_fingerprint": protocol_fingerprint,
        "source_fingerprint": source.get("fingerprint") or content_hash(source),
        "data_bars": df.height,
        "n_folds": len(result.inner_results),
        "passes_hard_gates": result.passes_hard_gates,
        "gate_failures": list(result.gate_failures),
        "contract_compliant_min_trades_30": contract_compliant,
        "min_trades_used": args.min_trades,
        "folds": receipts,
        "gate_variants": gate_variants,
        "aggregate_metrics": result.aggregate_metrics,
        "known_limitations": [
            "cost stress covers only the scenarios listed in cost_scenarios",
            "no outer OOS, no holdout, no sensitivity, no promotion gate run",
            "parameters were selected on tournament data — not independent OOS",
            "INNER_VALIDATION scope only",
            (
                f"cell_timeout_seconds={args.cell_timeout} exceeds the "
                "run_wfo_parallel.py --validation-only ceiling of 180s; the "
                "sanctioned entrypoint cannot run this strategy at current speed"
                if args.cell_timeout > 180 else "cell timeout within the "
                "run_wfo_parallel.py --validation-only ceiling"
            ),
        ],
    }
    summary_path = out_root / "wfo_validation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    # ---- terminal manifest with content hashes ----------------------------
    manifest_entries = []
    for path in sorted(out_root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest_entries.append({
                "path": str(path.relative_to(out_root)),
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
            })
    manifest = {
        "packet": str(out_root),
        "protocol_fingerprint": protocol_fingerprint,
        "files": manifest_entries,
        "file_count": len(manifest_entries),
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print("\n=== FOLD RECEIPTS ===")
    for receipt in receipts:
        m = receipt["metrics"]
        print(f"  {receipt['fold_id']}: trades={m['total_trades']} "
              f"sharpe={m['sharpe']} ret={m['total_return_pct']}% "
              f"mdd={m['max_drawdown_pct']}% eligible={receipt['gate']['eligible']}")
    print(f"\npasses_hard_gates={result.passes_hard_gates} "
          f"gate_failures={list(result.gate_failures)}")
    print(f"packet: {out_root}")
    print(f"summary: {summary_path}")
    return 0 if result.passes_hard_gates else 1


if __name__ == "__main__":
    sys.exit(main())
