#!/usr/bin/env python3
"""Run one canonical strategy artifact on one pair (STR-0106).

Fail-closed by construction:

- ``--strategy-id`` must be a key of the canonical registry allowlist —
  arbitrary classes/modules are never accepted from the command line.
- Symbols must be listed in the descriptor's ``supported_symbols``.
- Research-only strategies resolve in the RESEARCH environment only.
- OHLCV input passes :func:`assess_ohlcv` (duplicate/null/OHLC checks) and
  windows are cut strictly point-in-time via :func:`build_ohlcv_window`.
- The whole forecast pass is executed twice; the run only succeeds when
  both passes produce identical fingerprint sequences (determinism gate).
- Output JSON carries a content-addressed ``run_manifest_sha256`` binding
  descriptor_id + inputs + every fingerprint.

This is the S1 *forecast harness*; full execution tournaments belong to S2
(STR-0201 CanonicalEvaluationRunner).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import polars as pl

from trading_agent.backtest.reporting import GapPolicy, assess_ohlcv
from trading_agent.authority.config import Environment
from trading_agent.research.forecast import MarketObservation
from trading_agent.strategies.canonical import (
    FEATURE_OHLCV_WINDOW,
    RegistryIntegrityError,
    UnknownStrategyError,
    build_default_registry,
    build_ohlcv_window,
)

EXPECTED_INTERVAL = timedelta(hours=1)  # 1h canonical pairs


def _git_commit_sha() -> str:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
        )
    except Exception:  # pragma: no cover - git unavailable
        return "unknown"


def load_ohlcv(path: Path) -> pl.DataFrame:
    if path.suffix == ".parquet":
        frame = pl.read_parquet(path)
    elif path.suffix == ".csv":
        frame = pl.read_csv(path)
    else:
        raise SystemExit(f"unsupported data format: {path.suffix} (.parquet/.csv)")
    required = {"time", "open", "high", "low", "close", "volume"}
    if "timestamp" in frame.columns and "time" not in frame.columns:
        frame = frame.rename({"timestamp": "time"})
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"data missing columns: {sorted(missing)}")
    if frame.schema["time"] == pl.Utf8:
        frame = frame.with_columns(pl.col("time").str.to_datetime(time_zone="UTC"))
    return frame.sort("time")


def run_pass(adapter, frame: pl.DataFrame, warmup_bars: int):
    """One deterministic forecast sweep over closed bars."""
    records = []
    times = frame["time"].to_list()
    for i in range(warmup_bars + 1, len(frame)):
        observed_at = times[i]
        window = build_ohlcv_window(
            frame.head(i), observed_at=observed_at, bars=warmup_bars + 1
        )
        row = frame.row(i, named=True)
        observation = MarketObservation(
            symbol=adapter.strategy_id.split(":")[0],
            observed_at=observed_at,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            features={FEATURE_OHLCV_WINDOW: window},
        )
        forecast = adapter.forecast(observation)
        records.append(
            {
                "observed_at": observed_at.isoformat(),
                "fingerprint": forecast.fingerprint,
                "action": forecast.metadata.get("canonical_action"),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-id", required=True,
                        help="allowlisted canonical strategy id (see registry)")
    parser.add_argument("--symbol", required=True, help="BASE/QUOTE, e.g. BTC/USDT")
    parser.add_argument("--data", required=True, type=Path,
                        help="OHLCV .csv/.parquet with time/open/high/low/close/volume")
    parser.add_argument("--out", type=Path, default=None,
                        help="output JSON path (default: data/canonical_runs/<id>_<symbol>.json)")
    args = parser.parse_args()

    registry = build_default_registry()

    # ── Fail-closed resolution ────────────────────────────────────────
    try:
        descriptor = registry.describe(args.strategy_id)
    except UnknownStrategyError:
        raise SystemExit(
            f"strategy {args.strategy_id!r} is not allowlisted; "
            f"available: {registry.list_ids()}"
        )
    if not descriptor.supports_symbol(args.symbol):
        raise SystemExit(
            f"symbol {args.symbol!r} not supported by {args.strategy_id!r}; "
            f"allowed: {descriptor.supported_symbols}"
        )
    environment = Environment.RESEARCH  # research_only gate lives in the registry
    try:
        _, adapter = registry.get(args.strategy_id, environment=environment)
    except RegistryIntegrityError as exc:
        raise SystemExit(f"blocked: {exc}")

    # ── Data quality gate ─────────────────────────────────────────────
    frame = load_ohlcv(args.data)
    quality_frame = frame.rename({"time": "timestamp"}) if "time" in frame.columns else frame
    quality = assess_ohlcv(
        quality_frame, expected_interval=EXPECTED_INTERVAL, gap_policy="reject"
    )
    if getattr(quality, "errors", None):
        raise SystemExit(f"data quality gate failed: {quality.errors}")

    # ── Deterministic double pass ─────────────────────────────────────
    pass_a = run_pass(adapter, frame, descriptor.warmup_bars)
    pass_b = run_pass(adapter, frame, descriptor.warmup_bars)
    fingerprints_a = [r["fingerprint"] for r in pass_a]
    fingerprints_b = [r["fingerprint"] for r in pass_b]
    if fingerprints_a != fingerprints_b:
        raise SystemExit("DETERMINISM GATE FAILED: passes diverge")

    actions: dict[str, int] = {}
    for record in pass_a:
        actions[record["action"]] = actions.get(record["action"], 0) + 1

    manifest_payload = {
        "descriptor_id": descriptor.descriptor_id,
        "strategy_id": descriptor.strategy_id,
        "semantic_version": descriptor.semantic_version,
        "symbol": args.symbol,
        "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
        "commit_sha": _git_commit_sha(),
        "n_observations": len(pass_a),
        "fingerprints": fingerprints_a,
    }
    manifest_sha = hashlib.sha256(
        json.dumps(manifest_payload, sort_keys=True).encode()
    ).hexdigest()

    report = {
        **manifest_payload,
        "fingerprints": None,  # keep the file compact; count summary instead
        "action_counts": actions,
        "first_fingerprint": fingerprints_a[0] if fingerprints_a else None,
        "last_fingerprint": fingerprints_a[-1] if fingerprints_a else None,
        "run_manifest_sha256": manifest_sha,
        "environment": environment.value,
        "research_only": descriptor.research_only,
    }

    out_path = args.out or (
        ROOT / "data" / "canonical_runs"
        / f"{args.strategy_id}_{args.symbol.replace('/', '')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"OK strategy={args.strategy_id} symbol={args.symbol}")
    print(f"   observations={len(pass_a)} actions={actions}")
    print(f"   descriptor_id={descriptor.descriptor_id}")
    print(f"   run_manifest_sha256={manifest_sha[:16]}…")
    print(f"   report={out_path}")


if __name__ == "__main__":
    main()
