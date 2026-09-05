#!/usr/bin/env python3
"""Run the canonical strategy tournament (Phase S2).

Matrix: strategies × symbols × cost scenarios, every cell through the
same full execution path with its own state dir / report / manifest.
Results are merged into ``<out>/tournament_index.json`` (content-addressed
per-cell artifacts). Cells already recorded as COMPLETED are skipped on
re-run unless ``--rerun`` is given.

Examples::

    # Full baseline: 5 deterministic strategies × 10 pairs × scenarios
    python scripts/run_strategy_tournament.py

    # One quick probe on the most recent 2000 bars
    python scripts/run_strategy_tournament.py --strategies rsi \
        --symbols BTC/USDT --scenarios 1x --tail-bars 2000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trading_agent.backtest.tournament import (
    DEFAULT_SCENARIOS,
    EvaluationCellSpec,
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    SCENARIO_TRIPLE,
    run_cell,
    save_index,
)

SCENARIOS = {
    s.name: s
    for s in (
        SCENARIO_BASE,
        SCENARIO_DOUBLE,
        SCENARIO_TRIPLE,
        SCENARIO_SLIPPAGE_STRESS,
    )
}

ALL_STRATEGIES = ("enhanced_ma", "ma_adx", "ma_vol_target", "rsi", "bbands")
ALL_SYMBOLS = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ZEC/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "ADA/USDT",
    "NEAR/USDT",
)


def _load_completed(out_root: Path) -> set[str]:
    index = out_root / "tournament_index.json"
    if not index.exists():
        return set()
    import json

    cells = json.loads(index.read_text()).get("cells", {})
    return {
        cell_id for cell_id, cell in cells.items() if cell.get("status") == "COMPLETED"
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategies",
        default=",".join(ALL_STRATEGIES),
        help="comma-separated allowlisted strategy ids",
    )
    parser.add_argument("--symbols", default=",".join(ALL_SYMBOLS))
    parser.add_argument(
        "--scenarios",
        default=",".join(s.name for s in DEFAULT_SCENARIOS),
        help=f"one or more of {sorted(SCENARIOS)}",
    )
    parser.add_argument(
        "--params",
        default="",
        help="JSON object of strategy parameters applied to every cell, e.g. '{\"period\": 21}'",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "backtests" / "tournament",
    )
    parser.add_argument(
        "--tail-bars",
        type=int,
        default=None,
        help="evaluate only the most recent N bars (smoke runs)",
    )
    parser.add_argument(
        "--gap-policy",
        choices=("record", "reject"),
        default="record",
        help="Record timestamp gaps or reject gaps without a reviewed exception",
    )
    parser.add_argument(
        "--gap-exceptions",
        type=Path,
        default=None,
        help="Reviewed schema-v1 gap exception manifest",
    )
    parser.add_argument(
        "--cell-timeout-seconds",
        type=float,
        default=300.0,
        help="wall-clock deadline for each isolated cell attempt",
    )
    parser.add_argument(
        "--cell-max-retries",
        type=int,
        default=2,
        help="retry count after timeout or transient worker failure",
    )
    parser.add_argument(
        "--cell-max-memory-mb",
        type=int,
        default=None,
        help="optional hard data-memory limit for each cell worker",
    )
    parser.add_argument(
        "--cell-max-cpu-seconds",
        type=int,
        default=None,
        help="optional hard aggregate CPU-time limit across worker threads",
    )
    parser.add_argument(
        "--rerun", action="store_true", help="re-run cells already COMPLETED"
    )
    parser.add_argument("--dry-run", action="store_true", help="list cells and exit")
    args = parser.parse_args()
    if args.tail_bars is not None and args.tail_bars <= 0:
        parser.error("--tail-bars must be positive")
    if args.cell_timeout_seconds <= 0:
        parser.error("--cell-timeout-seconds must be positive")
    if args.cell_max_retries < 0:
        parser.error("--cell-max-retries must be non-negative")
    if args.cell_max_memory_mb is not None and args.cell_max_memory_mb <= 0:
        parser.error("--cell-max-memory-mb must be positive")
    if args.cell_max_cpu_seconds is not None and args.cell_max_cpu_seconds <= 0:
        parser.error("--cell-max-cpu-seconds must be positive")

    resource_budget = {
        key: value
        for key, value in {
            "max_memory_mb": args.cell_max_memory_mb,
            "max_cpu_seconds": args.cell_max_cpu_seconds,
        }.items()
        if value is not None
    }

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    scenarios = [SCENARIOS[s.strip()] for s in args.scenarios.split(",") if s.strip()]
    params = {}
    if args.params:
        import json

        params = json.loads(args.params)

    specs = [
        EvaluationCellSpec(strategy_id=sid, symbol=sym, params=params, cost_scenario=sc)
        for sid in strategies
        for sym in symbols
        for sc in scenarios
    ]

    done = set() if args.rerun else _load_completed(args.out)
    pending = [spec for spec in specs if spec.cell_id not in done]

    print(
        f"Tournament: {len(specs)} cells | {len(done)} already completed "
        f"| {len(pending)} to run"
    )
    for spec in specs[:10]:
        mark = "✓" if spec.cell_id in done else "·"
        print(f"  {mark} {spec.cell_id}")
    if len(specs) > 10:
        print(f"  … and {len(specs) - 10} more")
    if args.dry_run:
        return
    if not pending:
        print("Nothing to do.")
        return

    failures = 0
    for i, spec in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] {spec.cell_id}")
        try:
            artifact = run_cell(
                spec,
                out_root=args.out,
                tail_bars=args.tail_bars,
                gap_policy=args.gap_policy,
                gap_exceptions_path=args.gap_exceptions,
                timeout_seconds=args.cell_timeout_seconds,
                max_retries=args.cell_max_retries,
                resource_budget=resource_budget or None,
            )
        except Exception as exc:  # noqa: BLE001 - a broken cell must not kill the matrix
            print(f"   💥 EXCEPTION: {exc}")
            failures += 1
            continue
        save_index([artifact], args.out)
        if artifact.status == "COMPLETED":
            metrics = artifact.metrics
            print(
                f"   ✅ ret={metrics.get('total_return_pct')}% "
                f"sharpe={metrics.get('sharpe')} "
                f"mdd={metrics.get('max_drawdown_pct')}% "
                f"trades={metrics.get('total_trades')}"
            )
        else:
            failures += 1
            print(f"   ❌ FAILED: {artifact.failure_reasons}")

    print(
        f"\nDone. failures={failures}/{len(pending)} — index: "
        f"{args.out / 'tournament_index.json'}"
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
