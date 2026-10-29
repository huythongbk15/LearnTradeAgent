"""Scoped 36-cell SYNTHETIC launcher proof. No market input or market mode."""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import sys

import polars as pl

from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.campaign_execution import _durable_json, execution_environment
from trading_agent.backtest.fill_cycle_development import digest, frame_digest
from trading_agent.backtest.fill_cycle_synthetic import synthetic_frame
from trading_agent.backtest.tournament import DEFAULT_SCENARIOS
from trading_agent.backtest.validation_supervisor import (
    _supervise_validation, _validation_group_pids, _validation_group_rss_mb,
    _wait_for_all_reaped,
)
from trading_agent.research.btc_context_features import own_simple_return_24
from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy
from trading_agent.strategies.own24_thinned_fill import Own24ThinnedFillStrategy

ROOT = Path(__file__).resolve().parents[3]
PAIRS = ("ETH/USDT", "BNB/USDT")
SEEDS = (42, 123, 2026, 777, 31415)
SCOPE = "synthetic-btc-thinning-launcher-v1"
BUDGET = {"cell_seconds": 60, "total_seconds": 600, "max_rss_mb": 2048,
          "workers": 1, "retries": 0}


def fixture():
    frame = synthetic_frame()
    return frame.with_columns(pl.Series("btc_close", [1000.0] * 235 + [1100.0] * 45))


def calibration_counts(frame, start=216, end=240):
    """Synthetic calibration only; never reads evaluation return values."""
    stamps = tuple(frame["timestamp"].to_list())
    own = tuple(frame["close"].to_list())
    btc = tuple(frame["btc_close"].to_list())
    numerator = denominator = missing = 0
    for k in range(start, end):
        a = own_simple_return_24(own, stamps, k)
        b = own_simple_return_24(btc, stamps, k)
        if a.value is None or b.value is None:
            missing += 1
            continue
        if a.value > 0:
            denominator += 1
            numerator += int(b.value > 0)
    if denominator == 0:
        raise ValueError("calibration denominator is zero")
    return {"numerator": numerator, "denominator": denominator, "missing": missing}


def cells():
    return [{"pair": pair, "cost": cost, "seed": seed,
             "arm": "PLUS" if seed is None else "THINNED",
             "id": f"{pair.replace('/', '_')}__{cost}__{seed if seed is not None else 'PLUS'}"}
            for pair in PAIRS for cost in ("1x", "2x", "slip_stress")
            for seed in (None, *SEEDS)]


def policy(cell, counts):
    if cell["arm"] == "PLUS":
        return Own24FillContextStrategy({"arm": "PLUS"})
    return Own24ThinnedFillStrategy(pair=cell["pair"], seed=cell["seed"],
        numerator=counts["numerator"], denominator=counts["denominator"])


def contracts(counts):
    from scripts.full_system_backtest import describe_execution_contract
    from trading_agent.config.loader import config
    scenarios = {s.name: s for s in DEFAULT_SCENARIOS}
    result = {}
    for cell in cells():
        strat = policy(cell, counts)
        cost = scenarios[cell["cost"]]
        result[cell["id"]] = describe_execution_contract(
            symbol=cell["pair"], timeframe="1h", strategy_name=strat.name,
            parameters=strat.params, commission=config.commission * cost.fee_multiplier,
            slippage=config.slippage * cost.slippage_multiplier)
    return result


def expected_body():
    frame = fixture()
    counts = calibration_counts(frame)
    return {"scope": SCOPE, "market_run": False, "live_authorized": False,
            "qualification_possible": False, "source": capture_campaign_source(ROOT),
            "environment": execution_environment(), "frame_sha256": frame_digest(frame),
            "calibration_window": [216, 240], "evaluation_window": [240, 280],
            "calibration": {pair: counts for pair in PAIRS}, "cells": cells(),
            "budget": BUDGET, "native_contracts": contracts(counts)}


def freeze(out):
    if out.exists():
        raise ValueError("fresh output required")
    body = expected_body()
    body["fingerprint"] = digest(body)
    out.mkdir(parents=True)
    _durable_json(out / "protocol.json", body)
    return out / "protocol.json"


def verify(path):
    plan = json.loads(path.read_bytes())
    body = {k: v for k, v in plan.items() if k != "fingerprint"}
    if digest(body) != plan.get("fingerprint"):
        raise ValueError("synthetic protocol digest mismatch")
    expected = expected_body()
    # Commit metadata may differ after a code-identical docs commit; source tree must not.
    if body.get("source", {}).get("tree_fingerprint") != expected["source"]["tree_fingerprint"]:
        raise ValueError("synthetic source mismatch")
    expected["source"] = body["source"]
    for field, value in expected.items():
        if body.get(field) != value:
            raise ValueError(f"synthetic binding mismatch: {field}")
    if set(body) != set(expected):
        raise ValueError("unexpected synthetic protocol fields")
    if os.environ.get("TRADING_FROZEN_RESEARCH_DATA"):
        raise ValueError("market data binding forbidden in synthetic proof")
    return plan


def worker(path, cell_id):
    from scripts.full_system_backtest import FullSystemSimulator
    plan = verify(path)
    cell = next((c for c in plan["cells"] if c["id"] == cell_id), None)
    if cell is None:
        raise ValueError("unregistered cell")
    out = path.parent / "cells" / cell_id
    if (out / "started.json").exists() or (out / "report.json").exists():
        raise ValueError("cell cannot retry or overwrite")
    out.mkdir(parents=True, exist_ok=True)
    _durable_json(out / "started.json", {"pid": os.getpid(), "cell": cell,
        "protocol_fingerprint": plan["fingerprint"], "max_retries": 0})
    native = plan["native_contracts"][cell_id]
    costs = native["body"]["cost_model"]
    sim = FullSystemSimulator(fresh=True, symbol=cell["pair"], timeframe="1h",
        state_dir=out / "execution", report_path=out / "report.json",
        run_id=cell_id, data_manifest_id=SCOPE, gap_policy="reject",
        source_df_override=fixture(), execution_cycle_synthetic_only=True,
        execution_cycle_strategy=policy(cell, plan["calibration"][cell["pair"]]),
        expected_execution_contract_id=native["contract_id"],
        commission=costs["commission_rate"], slippage=costs["slippage_rate"])
    try:
        report = sim.run(start=240, end=280)
        if report["total_trades"] < 1 or report["open_positions"] or report["open_orders"]:
            raise ValueError("synthetic proof requires actual fill and terminal flat")
        if abs(report["cost_attribution"]["reconciliation_error"]) > 1e-6:
            raise ValueError("accounting mismatch")
        verify(path)
        _durable_json(out / "result.json", {"status": "COMPLETED", "cell": cell,
            "protocol_fingerprint": plan["fingerprint"], "market_run": False,
            "report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest()})
    finally:
        sim.engine.store.close()


def coverage(path):
    plan = verify(path)
    rows = []
    for cell in plan["cells"]:
        out = path.parent / "cells" / cell["id"]
        try:
            result = json.loads((out / "result.json").read_bytes())
            receipt = json.loads((out / "execution.json").read_bytes())
            valid = (result["status"] == receipt["status"] == "COMPLETED"
                and result["cell"] == cell
                and result["protocol_fingerprint"] == plan["fingerprint"]
                and result["report_sha256"] == hashlib.sha256((out / "report.json").read_bytes()).hexdigest())
        except (OSError, ValueError, KeyError):
            valid = False
        rows.append({"cell": cell["id"], "verified": valid})
    return {"status": "SYNTHETIC_COMPLETE" if all(r["verified"] for r in rows) else "INCOMPLETE",
            "coverage": rows, "market_run": False, "qualified": False,
            "live_authorized": False, "protocol_fingerprint": plan["fingerprint"]}


def run(path):
    import time
    plan = verify(path)
    if (path.parent / "execution_started.json").exists():
        raise ValueError("no resume or retry")
    _durable_json(path.parent / "execution_started.json", {"fingerprint": plan["fingerprint"]})
    started = time.monotonic()
    for cell in plan["cells"]:
        remaining = BUDGET["total_seconds"] - (time.monotonic() - started)
        if remaining <= 0:
            break
        out = path.parent / "cells" / cell["id"]
        out.mkdir(parents=True)
        receipt = _supervise_validation(
            [sys.executable, "-m", "trading_agent.backtest.thinning_synthetic", "--worker", str(path), cell["id"]],
            out, total_timeout_seconds=min(BUDGET["cell_seconds"], remaining),
            max_rss_mb=BUDGET["max_rss_mb"], cwd=ROOT,
            group_rss_mb=_validation_group_rss_mb, group_pids=_validation_group_pids,
            wait_for_all_reaped=partial(_wait_for_all_reaped, group_pids=_validation_group_pids))
        _durable_json(out / "execution.json", receipt)
        if receipt["status"] != "COMPLETED":
            break
    summary = coverage(path)
    summary["elapsed_seconds"] = time.monotonic() - started
    _durable_json(path.parent / "summary.json", summary)
    return 0 if summary["status"] == "SYNTHETIC_COMPLETE" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs=2, metavar=("PROTOCOL", "CELL"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    if args.worker:
        if args.out or args.plan_only:
            parser.error("worker takes only protocol and cell")
        worker(Path(args.worker[0]).resolve(), args.worker[1])
        return 0
    if args.out is None:
        parser.error("--out required")
    path = freeze(args.out.resolve())
    return 0 if args.plan_only else run(path)


if __name__ == "__main__":
    raise SystemExit(main())
