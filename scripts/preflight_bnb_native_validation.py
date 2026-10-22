"""Freeze a fixed ADX25 native validation-only packet. Never executes cells.

Run through controlled_exec. Existing packets, data and trial history are immutable.
This is reused-data descriptive validation, not untouched OOS or qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATASET = ROOT / "data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet"
DATASET_SHA = "379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f"
MANIFEST_SHA = "c1802ba532f7a7a17e7794fb2fb001f36c08f404b3a530be4026b870a65b2247"


def fixed_spec():
    from trading_agent.backtest.nested_wfo import WFOSpec
    from trading_agent.backtest.tournament import DEFAULT_SCENARIOS

    return WFOSpec(
        "ma_adx", "BNB/USDT", "4h",
        param_grid={"fast_period": [20], "slow_period": [80],
                    "adx_period": [14], "adx_threshold": [25]},
        cost_scenarios=DEFAULT_SCENARIOS, train_months=12, val_months=6,
        test_months=3, step_months=3, purge_bars=100, embargo_bars=100,
        registry_path=str(ROOT / "data/wfo/experiments_parallel.sqlite3"),
        search_family="bnb_native_adx25_revalidation", evaluator_version="v1",
        min_oos_trades=30, seed=42, evidence_class="REAL_MARKET",
    )


def preflight(out_root):
    from datetime import UTC, timedelta
    import polars as pl
    from scripts.run_wfo_parallel import prepare_parallel_protocol
    from trading_agent.alpha_research.holdout import holdout_window
    from trading_agent.backtest.campaign_execution import _durable_json
    from trading_agent.data.storage import frozen_research_data

    manifest = ROOT / "data/research_manifest.json"
    approved_manifest_bytes = manifest.read_bytes()
    if hashlib.sha256(approved_manifest_bytes).hexdigest() != MANIFEST_SHA:
        raise ValueError("holdout manifest identity changed")
    approved_bytes = DATASET.read_bytes()
    if hashlib.sha256(approved_bytes).hexdigest() != DATASET_SHA:
        raise ValueError("pre-holdout dataset identity changed")
    approved_manifest = json.loads(approved_manifest_bytes)
    if not isinstance(approved_manifest, dict) or not approved_manifest:
        raise ValueError("missing approved holdout manifest")
    # Pinned SHA authenticates the already integrity-reviewed manifest bytes.
    boundary, _ = holdout_window(approved_manifest)
    # Verify the cut before the canonical loader opens any price columns.
    times = pl.read_parquet(io.BytesIO(approved_bytes), columns=["timestamp"])["timestamp"].to_list()
    if not times or any(t is None for t in times):
        raise ValueError("missing pre-holdout timestamps")
    times = [t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC) for t in times]
    if (len(times) != 6795 or len(set(times)) != len(times)
            or any(b-a != timedelta(hours=4) for a,b in zip(times,times[1:]))
            or times[-1]+timedelta(hours=4) > boundary):
        raise ValueError("dataset geometry/holdout boundary differs from reviewed plan")
    root = Path(out_root).resolve()
    if not root.is_relative_to(ROOT / "data") or root == ROOT / "data":
        raise ValueError("output must be a new packet under workspace data")
    root.mkdir(parents=True, exist_ok=False)
    _durable_json(root / "review_scope.json", {
        "scope": "reused_data_descriptive_native_inner_validation_only",
        "untouched_oos": False, "live_authorized": False,
        "market_execution_authorized": False,
        "budget": {"workers": 1, "cell_timeout_seconds": 90,
                   "total_wall_seconds": 1800, "max_rss_mb": 2048, "max_retries": 0},
        "known_measured_exposure_lower_bound": 3360,
        "holdout_manifest_sha256": MANIFEST_SHA,
        "full_qualification_blocker": "P2 sensitivity contract preregistration OPEN",
    })
    try:
        with frozen_research_data(DATASET, exchange="binance", symbol="BNB/USDT", timeframe="4h") as binding:
            if binding["sha256"] != DATASET_SHA:
                raise ValueError("dataset changed before freeze")
            if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA:
                raise ValueError("holdout manifest changed before freeze")
            protocol, folds = prepare_parallel_protocol(
                fixed_spec(), root, workers=1, cell_timeout=90,
                validation_budget={"total_timeout_seconds": 1800, "max_rss_mb": 2048})
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != MANIFEST_SHA:
            raise ValueError("holdout manifest changed during freeze")
        if len(folds) != 5 or len(protocol["expected_inner_cells"]) != 15:
            raise ValueError("canonical schedule differs from reviewed 5-fold/15-cell scope")
        _durable_json(root / "preflight_terminal.json", {
            "status": "PREFLIGHT_PASS_EXECUTION_NOT_AUTHORIZED",
            "protocol_fingerprint": protocol["fingerprint"],
            "folds": 5, "inner_cells": 15, "cells_executed": 0,
            "live_authorized": False, "untouched_oos": False,
        })
        return protocol
    except Exception as exc:
        _durable_json(root / "preflight_terminal.json", {
            "status": "PREFLIGHT_FAILED_DO_NOT_EXECUTE", "error": f"{type(exc).__name__}: {exc}",
            "cells_executed": 0, "live_authorized": False,
        })
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    protocol = preflight(args.out)
    print(f"PREFLIGHT_ONLY {protocol['fingerprint']}; no cells executed")


if __name__ == "__main__":
    main()
