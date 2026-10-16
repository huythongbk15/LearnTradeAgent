#!/usr/bin/env python3
"""Metadata-only geometry planner; no execution, ranking or promotion authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.alpha_research.holdout import holdout_window, load_manifest
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_plan import MIN_FOLDS_FOR_VERDICT
from trading_agent.backtest.native_gate_contract import verify_native_contract
from trading_agent.backtest.nested_wfo import _binomial_upper_tail, _get_fold_indices
from trading_agent.strategies.canonical.candidates import campaign_history_contract

CONFIGS = ((12, 3, 3, 3), (12, 6, 3, 3), (12, 9, 3, 3), (12, 12, 3, 3))


def utc(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def required_k(n, p_max):
    if type(n) is not int or n < 1 or not 0 < p_max < 1:
        raise ValueError("positive fold count and probability in (0,1) required")
    return next((k for k in range(n + 1) if _binomial_upper_tail(k, n) <= p_max), None)


def validate_protocol(protocol):
    if protocol.get("fingerprint") != content_hash({k: v for k, v in protocol.items() if k != "fingerprint"}):
        raise ValueError("protocol fingerprint mismatch")
    verify_native_contract(protocol["native_gate_contract"])


def geometry_plan(protocol, timestamps, manifest):
    validate_protocol(protocol)
    spec = protocol["spec"]
    if len(timestamps) != protocol["input_bars"] or not timestamps:
        raise ValueError("timestamp coverage differs from protocol")
    times = [utc(t) for t in timestamps]
    if any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("timestamps must be strictly increasing")
    if times[0] != utc(protocol["input_first_timestamp"]) or times[-1] != utc(protocol["input_last_timestamp"]):
        raise ValueError("timestamp bounds differ from protocol")
    holdout_start, _ = holdout_window(manifest)
    n = sum(t < holdout_start for t in times)
    history = campaign_history_contract(spec["strategy_id"], spec["param_grid"])
    purge = max(int(spec.get("purge_bars", 0)), history["warmup_bars"])
    embargo = max(int(spec.get("embargo_bars", 0)), history["buffer_bars"])
    gate = next(g for g in protocol["native_gate_contract"]["gates"]
                if g["gate_id"] == "cost_clearing_folds_above_chance_p_le_010")
    p_max = gate["threshold"]
    combinations = math.prod(len(v) for v in spec["param_grid"].values())
    costs = len(spec["cost_scenarios"])
    plans = []
    for train, val, test, step in CONFIGS:
        folds = _get_fold_indices(n, spec["timeframe"], train, val, test, step, purge, embargo)
        count = len(folds)
        k = required_k(count, p_max) if count else None
        plans.append({"months": dict(train=train, validation=val, test=test, step=step),
            "folds": count, "validation_bars": [f.inner_val_end - f.inner_val_start for f in folds],
            "minimum_cost_clearing_folds": k,
            "fold_count_and_binomial_geometry_possible": count >= MIN_FOLDS_FOR_VERDICT and k is not None,
            "planned_inner_cells": count * combinations * costs, "outer_primary_upper_bound": count,
            "sensitivity_cells": "REQUIRED_NOT_ESTIMATED", "validation_trade_adequacy": "UNKNOWN",
            "qualification": "NOT_EVALUATED", "eta": None})
    return {"status": "DRAFT_GEOMETRY_ONLY_NOT_APPROVED", "source_protocol": protocol["fingerprint"],
        "subject": {k: spec[k] for k in ("strategy_id", "symbol", "timeframe")},
        "input_bars": len(times), "pre_holdout_bars": n,
        "input_extends_into_or_past_holdout": n != len(times),
        "holdout_start_utc": holdout_start.isoformat(), "purge": purge, "embargo": embargo,
        "cost_clearing_p_max": p_max, "aggregate_minimum_folds": MIN_FOLDS_FOR_VERDICT,
        "plans": plans, "live_authorized": False, "execution_authorized": False,
        "limitations": ["geometry is not power/profitability proof", "no ranking or sensitivity bypass",
                       "new execution requires exact pre-holdout input and frozen source/budget approval"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, type=Path)
    args = parser.parse_args(argv)
    protocol = json.loads(args.protocol.read_text())
    validate_protocol(protocol)
    manifest = load_manifest(ROOT / "data/research_manifest.json")
    binding = protocol["input_binding"]
    path = Path(binding["path"]).resolve(strict=True)
    if not path.is_relative_to((ROOT / "data").resolve()):
        raise ValueError("input binding must stay inside repository data")
    if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
        raise ValueError("input bytes changed")
    import polars as pl
    timestamps = pl.read_parquet(path, columns=["timestamp"])["timestamp"].to_list()
    print(json.dumps(geometry_plan(protocol, timestamps, manifest), sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
