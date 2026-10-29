"""Prepare and verify BTC thinning development admission. No evaluation command.

Reads only existing hash-bound development inputs. Preparation never authorizes
market cells; the execution boundary remains explicitly disabled in this module.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path

import polars as pl

from trading_agent.backtest.campaign_execution import _durable_json, execution_environment
from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.fill_cycle_development import digest
from trading_agent.backtest.thinning_synthetic import cells, policy
from trading_agent.backtest.tournament import DEFAULT_SCENARIOS
from trading_agent.research.btc_context_features import own_simple_return_24
from trading_agent.research.btc_context_prereg import INPUTS

ROOT = Path(__file__).resolve().parents[3]
DESIGN = "data/btc_filter_prereg_freeze_20261029_01/preregistration_btc_filter_v5.md"
DESIGN_SHA = "3e2c39dfa2c808ed103b97360c01c73892e75fbe00fe89fa44a06da10a1d4d81"
CALIBRATION = (216, 8760)
EVALUATION = (8760, 25198)
BUDGET = {"cell_timeout_seconds": 600, "hard_deadline_seconds": 23400,
          "workers": 1, "max_retries": 0, "max_rss_mb": 2048, "cell_count": 36}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def counts(own, btc, stamps):
    numerator = denominator = missing = 0
    start, end = CALIBRATION
    if len(own) < end or len(btc) < end or len(stamps) < end:
        raise ValueError("short calibration data")
    # Slice BEFORE passing to the feature helper: evaluation cannot affect p.
    own, btc, stamps = tuple(own[:end]), tuple(btc[:end]), tuple(stamps[:end])
    for k in range(start, end):
        a = own_simple_return_24(own, stamps, k)
        b = own_simple_return_24(btc, stamps, k)
        if a.value is None or b.value is None:
            missing += 1
        elif a.value > 0:
            denominator += 1
            numerator += int(b.value > 0)
    if denominator == 0:
        raise ValueError("zero calibration denominator; cannot admit")
    return {"numerator": numerator, "denominator": denominator,
            "missing": missing, "calibration_rows": end - start}


def read_calibration():
    frames, bindings, clocks = {}, {}, {}
    for symbol, binding in INPUTS.items():
        raw = (ROOT / binding["path"]).read_bytes()
        if sha(raw) != binding["sha256"]:
            raise ValueError(f"input hash mismatch: {symbol}")
        frame = pl.read_parquet(io.BytesIO(raw), columns=["timestamp", "close"])
        stamps = frame["timestamp"].to_list()
        if len(stamps) != 25198 or any(t is None for t in stamps):
            raise ValueError(f"clock length/null: {symbol}")
        utc = [t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC) for t in stamps]
        if (utc[0] != datetime(2023, 3, 24, 14, tzinfo=UTC)
                or utc[-1] != datetime(2026, 2, 6, 11, tzinfo=UTC)
                or any(b-a != timedelta(hours=1) for a,b in zip(utc, utc[1:]))):
            raise ValueError(f"clock order/gap: {symbol}")
        frames[symbol] = frame["close"].to_list()
        clocks[symbol] = utc
        bindings[symbol] = dict(binding)
    reference = clocks["BTC/USDT"]
    if any(clock != reference for clock in clocks.values()):
        raise ValueError("cross-symbol clock mismatch")
    rows = {pair: counts(frames[pair], frames["BTC/USDT"], reference)
            for pair in ("ETH/USDT", "BNB/USDT")}
    return {"inputs": bindings, "calibration": rows,
            "calibration_window": list(CALIBRATION), "evaluation_window": list(EVALUATION),
            "read_scope": "Whole pre-exposed files hashed; timestamp+close columns decoded for all25198 rows; only calibration prefix used to compute p. No sort/fill.",
            "clock": {"rows": 25198, "first": reference[0].isoformat(), "last": reference[-1].isoformat(),
                      "calibration_start": reference[216].isoformat(), "evaluation_start": reference[8760].isoformat(),
                      "evaluation_end_exclusive": (reference[-1]+timedelta(hours=1)).isoformat(),
                      "identical": True, "naive_convention": "UTC"},
            "market_cells_executed": 0, "qualified": False}


def native_contracts(calibration):
    from scripts.full_system_backtest import describe_execution_contract
    from trading_agent.config.loader import config
    scenarios = {s.name: s for s in DEFAULT_SCENARIOS}
    result = {}
    for cell in cells():
        strat = policy(cell, calibration[cell["pair"]])
        cost = scenarios[cell["cost"]]
        result[cell["id"]] = describe_execution_contract(symbol=cell["pair"], timeframe="1h",
            strategy_name=strat.name, parameters=strat.params,
            commission=config.commission*cost.fee_multiplier,
            slippage=config.slippage*cost.slippage_multiplier)
    return result


def expected_plan(calibration):
    design = (ROOT / DESIGN).read_bytes()
    if sha(design) != DESIGN_SHA:
        raise ValueError("frozen design changed")
    environment = execution_environment()
    if environment["simulator_settings"]["INITIAL_CAPITAL"] != "10000":
        raise ValueError("initial capital must remain10000")
    return {"kind": "EXPOSED_DEVELOPMENT_PREPARATION_ONLY", "design_sha256": DESIGN_SHA,
            "source": capture_campaign_source(ROOT), "environment": environment,
            "calibration_sha256": digest(calibration), "inputs": calibration["inputs"],
            "calibration": calibration["calibration"], "calibration_window": list(CALIBRATION),
            "evaluation_window": list(EVALUATION), "evaluation_initialization": "flat_10000_no_calibration_position_or_equity_carry",
            "cells": cells(), "budget": BUDGET, "native_contracts": native_contracts(calibration["calibration"]),
            "metric": {"numerator": "final_marked_equity_minus_initial_equity",
                       "denominator": "sum_price_times_incremental_buy_fill_qty", "zero_denominator": None},
            "authorization": {"state": "NOT_AUTHORIZED_TO_RUN", "approved_by": None, "approved_at": None},
            "market_run": False, "live_authorized": False, "qualification_possible": False,
            "execution_entrypoint": None}


def prepare(out):
    if out.exists():
        raise ValueError("fresh packet required")
    calibration = read_calibration()
    plan = expected_plan(calibration)
    plan["fingerprint"] = digest(plan)
    out.mkdir(parents=True)
    _durable_json(out / "calibration.json", calibration)
    _durable_json(out / "protocol.json", plan)
    return out / "protocol.json"


def verify(path):
    plan = json.loads(path.read_bytes())
    body = {k:v for k,v in plan.items() if k != "fingerprint"}
    if plan.get("fingerprint") != digest(body):
        raise ValueError("preparation fingerprint mismatch")
    calibration = json.loads((path.parent / "calibration.json").read_bytes())
    if calibration != read_calibration():
        raise ValueError("calibration differs from bound inputs/window")
    expected = expected_plan(calibration)
    if body.get("source", {}).get("tree_fingerprint") != expected["source"]["tree_fingerprint"]:
        raise ValueError("preparation source mismatch")
    expected["source"] = body["source"]
    for field, value in expected.items():
        if body.get(field) != value:
            raise ValueError(f"preparation binding mismatch: {field}")
    if set(body) != set(expected):
        raise ValueError("unexpected preparation fields")
    return plan


def require_execution_authority(path):
    verify(path)
    raise PermissionError("NOT_AUTHORIZED_TO_RUN: preparation never grants market execution")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", type=Path)
    group.add_argument("--verify", type=Path)
    args = parser.parse_args()
    path = prepare(args.prepare.resolve()) if args.prepare else args.verify.resolve()
    plan = verify(path)
    print(json.dumps({"status": "PREPARED_NOT_AUTHORIZED", "cells": len(plan["cells"]),
                      "calibration": plan["calibration"], "fingerprint": plan["fingerprint"], "market_cells_executed": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
