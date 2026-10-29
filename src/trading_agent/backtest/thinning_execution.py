"""Scoped development execution for the frozen BTC thinning diagnostic.

Pending packets cannot run. Approval is scope control, not authentication;
operator provenance must be reviewed outside these editable local files.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from functools import partial
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time

import polars as pl

from trading_agent.backtest import thinning_preparation as prep
from trading_agent.backtest.campaign_execution import _durable_json
from trading_agent.backtest.fill_cycle_development import digest, frame_digest
from trading_agent.backtest.validation_supervisor import (
    _supervise_validation, _validation_group_pids, _validation_group_rss_mb,
    _wait_for_all_reaped,
)
from trading_agent.backtest.thinning_synthetic import policy
from trading_agent.strategies.own24_context import attach_btc_close

SCOPE = "btc-thinning-development-execution-v1"


def command(path):
    return [".venv/bin/python", "-m", "trading_agent.backtest.thinning_execution", "--run", str(path.resolve())]


def freeze(out):
    if out.exists():
        raise ValueError("fresh execution packet required")
    prepared_path = prep.prepare(out / "prepared")
    base = prep.verify(prepared_path)
    path = out / "protocol.json"
    plan = {"scope": SCOPE, "prepared_fingerprint": base["fingerprint"],
            "prepared_protocol_sha256": hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
            "command": command(path), "expected_approved_scope": "development-thinning:" + base["fingerprint"],
            "approval": {"approval_state": "PENDING_OPERATOR_APPROVAL", "approved_by": None,
                         "approved_at": None, "approved_scope": None},
            "market_run_authorized": False, "live_authorized": False, "qualified": False}
    plan["fingerprint"] = digest(plan)
    _durable_json(path, plan)
    _durable_json(out / "binding.json", {"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return path


def verify(path, *, approved=True):
    path = path.resolve()
    plan = json.loads(path.read_bytes())
    body = {k:v for k,v in plan.items() if k != "fingerprint"}
    if plan.get("fingerprint") != digest(body):
        raise ValueError("execution fingerprint mismatch")
    binding = json.loads((path.parent / "binding.json").read_bytes())
    if binding != {"protocol_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}:
        raise ValueError("execution binding hash mismatch")
    approval = plan.get("approval", {})
    if set(approval) != {"approval_state", "approved_by", "approved_at", "approved_scope"}:
        raise ValueError("approval schema mismatch")
    state = approval["approval_state"]
    if approved and state != "APPROVED":
        raise PermissionError("NOT_AUTHORIZED_TO_RUN")
    if state == "APPROVED":
        if not isinstance(approval["approved_by"], str) or not approval["approved_by"].strip():
            raise ValueError("approved_by missing")
        stamp = datetime.fromisoformat(approval["approved_at"])
        if stamp.tzinfo is None:
            raise ValueError("approval timestamp must carry timezone")
        if approval["approved_scope"] != plan.get("expected_approved_scope"):
            raise ValueError("approved_scope mismatch")
    elif state != "PENDING_OPERATOR_APPROVAL" or any(approval[k] is not None for k in (
            "approved_by", "approved_at", "approved_scope")):
        raise ValueError("invalid pending approval")
    prepared_path = path.parent / "prepared" / "protocol.json"
    base = prep.verify(prepared_path)
    expected = {"scope": SCOPE, "prepared_fingerprint": base["fingerprint"],
        "prepared_protocol_sha256": hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
        "command": command(path), "expected_approved_scope": "development-thinning:" + base["fingerprint"],
        "approval": approval, "market_run_authorized": state == "APPROVED",
        "live_authorized": False, "qualified": False}
    if body != expected:
        raise ValueError("execution plan differs from registered preparation")
    return plan, base


@dataclass(frozen=True)
class ThinningCycleInput:
    protocol_path: Path
    protocol_fingerprint: str
    cell_id: str
    frame_sha256: str


def cell_for(base, cell_id):
    cell = next((c for c in base["cells"] if c["id"] == cell_id), None)
    if cell is None:
        raise ValueError("unregistered thinning cell")
    return cell


def load_frame(path, cell_id):
    plan, base = verify(path)
    cell = cell_for(base, cell_id)
    frames = {}
    for pair in (cell["pair"], "BTC/USDT"):
        binding = base["inputs"][pair]
        raw = (prep.ROOT / binding["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError("input changed before decode")
        frames[pair] = pl.read_parquet(io.BytesIO(raw))
    frame = attach_btc_close(frames[cell["pair"]], frames["BTC/USDT"])
    return frame, ThinningCycleInput(path.resolve(), plan["fingerprint"], cell_id, frame_digest(frame))


def verify_simulator_input(request, frame, *, symbol, timeframe, strategy, manifest,
                           contract_id, state_dir, report_path, fresh):
    if type(request) is not ThinningCycleInput or frame is None:
        raise ValueError("missing typed thinning request")
    plan, base = verify(request.protocol_path)
    cell = cell_for(base, request.cell_id)
    expected_strategy = policy(cell, base["calibration"][cell["pair"]])
    out = request.protocol_path.parent / "cells" / request.cell_id
    if (fresh is not True or state_dir is None or report_path is None
            or Path(state_dir).resolve() != out / "execution"
            or Path(report_path).resolve() != out / "report.json"
            or (out / "report.json").exists()
            or ((out / "execution").exists() and any((out / "execution").iterdir()))):
        raise ValueError("thinning output must be fresh and isolated")
    if (request.protocol_fingerprint != plan["fingerprint"]
            or type(strategy) is not type(expected_strategy) or strategy.params != expected_strategy.params
            or symbol != cell["pair"] or timeframe != "1h"
            or manifest != "development-thinning:" + plan["fingerprint"]
            or contract_id != base["native_contracts"][cell["id"]]["contract_id"]
            or request.frame_sha256 != frame_digest(frame)):
        raise ValueError("thinning simulator binding mismatch")
    original, _ = load_frame(request.protocol_path, request.cell_id)
    if frame_digest(original) != request.frame_sha256:
        raise ValueError("thinning frame differs from registered input")
    return plan


def fill_metrics(events, report):
    """FillReceived.size is incremental, not the cumulative order snapshot."""
    seen, intents, buys, sells = {}, {}, [], []
    records = []
    for event in events:
        encoded = digest(event.to_row())
        if event.event_id in seen:
            if seen[event.event_id] != encoded:
                raise ValueError("conflicting duplicate fill event")
            continue
        seen[event.event_id] = encoded
        kind = event.event_type.value
        if kind == "exec.order_intent_created":
            identity = (event.payload["symbol"], event.payload["side"])
            if event.aggregate_id in intents and intents[event.aggregate_id] != identity:
                raise ValueError("conflicting intent identity")
            intents[event.aggregate_id] = identity
        elif kind in {"exec.partial_fill_received", "exec.fill_received"}:
            if event.aggregate_id not in intents:
                raise ValueError("fill has no intent identity")
            symbol, side = intents[event.aggregate_id]
            if symbol != report["symbol"] or side not in {"buy", "sell"}:
                raise ValueError("fill subject mismatch")
            quantity, price = float(event.payload["size"]), float(event.payload["price"])
            if not all(math.isfinite(v) and v > 0 for v in (quantity, price)):
                raise ValueError("invalid fill amount or price")
            (buys if side == "buy" else sells).append(quantity * price)
            records.append({"event_id": event.event_id, "intent_id": event.aggregate_id,
                            "side": side, "quantity_delta": quantity, "price": price})
    denominator = math.fsum(buys)
    pnl = float(report["final_equity"]) - float(report["initial_capital"])
    if not math.isfinite(pnl) or not math.isfinite(denominator):
        raise ValueError("nonfinite accounting")
    measured = report.get("measured_accounting")
    if measured is None or abs(float(measured["equity_delta"]) - pnl) > 1e-6:
        raise ValueError("marked equity accounting mismatch")
    rows = report["execution_funnel"]["records"]
    bars = report["simulation_window"]["bar_count"]
    if bars <= 0 or len(rows) != bars or any(type(r.get("position_after")) is not bool for r in rows):
        raise ValueError("missing complete bar exposure records")
    return {"marked_net_pnl": pnl, "buy_fill_notional": denominator,
        "primary_net_return_on_entry_notional": pnl / denominator if denominator else None,
        "marked_portfolio_return": pnl / float(report["initial_capital"]),
        "turnover": (denominator + math.fsum(sells)) / float(report["initial_capital"]),
        "time_in_market_end_of_bar_pct": 100 * sum(r["position_after"] for r in rows) / len(rows),
        "tim_convention": "end_of_bar_inventory_fraction_not_continuous_intrabar_exposure",
        "max_drawdown_pct": report["max_drawdown_pct"], "fills": records,
        "terminal_open_positions": report["open_positions"], "terminal_open_orders": report["open_orders"]}


def worker(path, cell_id):
    from scripts.full_system_backtest import FullSystemSimulator
    plan, base = verify(path)
    cell = cell_for(base, cell_id)
    out = path.parent / "cells" / cell_id
    if (out / "started.json").exists() or (out / "report.json").exists():
        raise ValueError("no worker retry or overwrite")
    frame, request = load_frame(path, cell_id)
    out.mkdir(parents=True, exist_ok=True)
    _durable_json(out / "started.json", {"cell": cell, "pid": os.getpid(),
        "protocol_fingerprint": plan["fingerprint"], "max_retries": 0, "started_at_epoch": time.time()})
    sim = None
    try:
        native = base["native_contracts"][cell_id]
        model = native["body"]["cost_model"]
        sim = FullSystemSimulator(fresh=True, symbol=cell["pair"], timeframe="1h",
            state_dir=out / "execution", report_path=out / "report.json", run_id=cell_id,
            data_manifest_id="development-thinning:" + plan["fingerprint"], gap_policy="reject",
            source_df_override=frame, execution_cycle_development_input=request,
            execution_cycle_strategy=policy(cell, base["calibration"][cell["pair"]]),
            expected_execution_contract_id=native["contract_id"],
            commission=model["commission_rate"], slippage=model["slippage_rate"], funnel_enabled=True)
        if sim.engine.exchange.get_position(cell["pair"]) is not None:
            raise ValueError("evaluation did not initialize flat")
        start, end = base["evaluation_window"]
        report = sim.run(start=start, end=end)
        health = report["execution_health"]
        if health["unknown_orders"] or health["manual_interventions"] or health["unprotected_positions"]:
            raise ValueError("unresolved execution health")
        metrics = fill_metrics(sim.engine.store.read_events(order_by_global=True), report)
        _durable_json(out / "metrics.json", metrics)
        verify(path)
        _durable_json(out / "result.json", {"status": "COMPLETED", "cell": cell,
            "protocol_fingerprint": plan["fingerprint"], "live_authorized": False,
            "report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest(),
            "metrics_sha256": hashlib.sha256((out / "metrics.json").read_bytes()).hexdigest()})
    except Exception as exc:
        _durable_json(out / "result.json", {"status": "FAILED", "cell": cell,
            "reason": f"{type(exc).__name__}: {exc}", "protocol_fingerprint": plan["fingerprint"]})
        raise
    finally:
        if sim is not None:
            sim.engine.store.close()


def coverage(path):
    plan, base = verify(path)
    rows, metrics_by_cell = [], {}
    for cell in base["cells"]:
        out = path.parent / "cells" / cell["id"]
        try:
            proof = json.loads((out / "result.json").read_bytes())
            receipt = json.loads((out / "execution.json").read_bytes())
            good = (proof["status"] == receipt["status"] == "COMPLETED"
                and proof["cell"] == cell and proof["protocol_fingerprint"] == plan["fingerprint"]
                and proof["report_sha256"] == hashlib.sha256((out / "report.json").read_bytes()).hexdigest()
                and proof["metrics_sha256"] == hashlib.sha256((out / "metrics.json").read_bytes()).hexdigest())
            if good:
                report = json.loads((out / "report.json").read_bytes())
                metrics = json.loads((out / "metrics.json").read_bytes())
                good = (report["symbol"] == cell["pair"] and report["timeframe"] == "1h"
                    and report["initial_capital"] == 10000
                    and report["active_config"]["execution_contract"] == base["native_contracts"][cell["id"]]
                    and report["simulation_window"]["bar_count"] == base["evaluation_window"][1]-base["evaluation_window"][0]
                    and math.isfinite(metrics["marked_net_pnl"])
                    and abs(metrics["marked_net_pnl"]-(report["final_equity"]-10000)) < 1e-6)
        except (OSError, ValueError, KeyError):
            good = False
        rows.append({"cell": cell["id"], "verified": good})
        if good:
            metrics_by_cell[cell["id"]] = json.loads((out / "metrics.json").read_bytes())
    comparisons = []
    if all(r["verified"] for r in rows):
        for pair in ("ETH/USDT", "BNB/USDT"):
            for cost in ("1x", "2x", "slip_stress"):
                group = [c for c in base["cells"] if c["pair"] == pair and c["cost"] == cost]
                plus = next(c for c in group if c["arm"] == "PLUS")
                pm = metrics_by_cell[plus["id"]]
                controls = []
                for cell in group:
                    if cell["arm"] != "THINNED":
                        continue
                    cm = metrics_by_cell[cell["id"]]
                    a = pm["primary_net_return_on_entry_notional"]
                    b = cm["primary_net_return_on_entry_notional"]
                    controls.append({"seed": cell["seed"], "metrics": {k:v for k,v in cm.items() if k != "fills"},
                                     "plus_minus_control_primary": a-b if a is not None and b is not None else None})
                comparisons.append({"pair": pair, "cost": cost,
                    "PLUS": {k:v for k,v in pm.items() if k != "fills"}, "controls": controls,
                    "statistical_conclusion": "DESCRIPTIVE_ONLY_NO_CI_OR_EQUIVALENCE_CLAIM"})
    return {"comparisons": comparisons, "status": "DEVELOPMENT_COMPLETE" if all(r["verified"] for r in rows) else "INCOMPLETE",
            "coverage": rows, "qualified": False, "live_authorized": False,
            "evidence_class": "EXPOSED_DEVELOPMENT_NOT_CONFIRMATION"}


def run(path):
    started = time.monotonic()
    plan, base = verify(path)
    root = path.parent
    if (root / "execution_started.json").exists():
        raise ValueError("no resume or retry")
    _durable_json(root / "execution_started.json", {"fingerprint": plan["fingerprint"], "started_at_epoch": time.time()})
    for cell in base["cells"]:
        remaining = base["budget"]["hard_deadline_seconds"] - (time.monotonic()-started)
        if remaining <= 0:
            break
        out = root / "cells" / cell["id"]
        out.mkdir(parents=True)
        receipt = _supervise_validation([sys.executable, "-m", "trading_agent.backtest.thinning_execution",
            "--worker", str(path), cell["id"]], out,
            total_timeout_seconds=min(base["budget"]["cell_timeout_seconds"], remaining),
            max_rss_mb=base["budget"]["max_rss_mb"], cwd=prep.ROOT,
            group_rss_mb=_validation_group_rss_mb, group_pids=_validation_group_pids,
            wait_for_all_reaped=partial(_wait_for_all_reaped, group_pids=_validation_group_pids))
        _durable_json(out / "execution.json", receipt)
        if receipt["status"] != "COMPLETED":
            break
    try:
        summary = coverage(path)
    except Exception as exc:
        summary = {"status": "INCOMPLETE", "qualified": False, "live_authorized": False,
                   "verification_error": f"{type(exc).__name__}: {exc}", "comparisons": [],
                   "coverage": [{"cell": c["id"], "verified": False} for c in base["cells"]]}
    summary["elapsed_seconds"] = time.monotonic()-started
    if summary["elapsed_seconds"] > base["budget"]["hard_deadline_seconds"]:
        summary["status"] = "INCOMPLETE"
        summary["budget_reason"] = "TOTAL_DEADLINE_EXCEEDED"
    _durable_json(root / "summary.json", summary)
    return 0 if summary["status"] == "DEVELOPMENT_COMPLETE" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", type=Path)
    group.add_argument("--run", type=Path)
    group.add_argument("--worker", nargs=2)
    args = parser.parse_args()
    if args.prepare:
        path = freeze(args.prepare.resolve())
        plan, _ = verify(path, approved=False)
        print(json.dumps({"status": "PENDING_OPERATOR_APPROVAL", "protocol": str(path), "fingerprint": plan["fingerprint"]}))
        return 0
    if args.worker:
        from trading_agent.backtest.thinning_execution import worker as canonical_worker
        canonical_worker(Path(args.worker[0]).resolve(), args.worker[1])
        return 0
    return run(args.run.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
