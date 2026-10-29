"""Bounded development contrast for fill-timed BASE/PLUS; never qualification.

Only the already exposed, hash-pinned ETH/BNB targets with BTC context and
the original three cost scenarios are supported. No registry registration, holdout, tuning, network or promotion.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
from dataclasses import dataclass
from functools import partial

import polars as pl

from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.campaign_execution import (
    _durable_json, execution_environment,
)
from trading_agent.backtest.execution_contract import require_execution_contract
from trading_agent.backtest.validation_supervisor import (
    _supervise_validation, _validation_group_pids, _validation_group_rss_mb,
    _wait_for_all_reaped,
)
from trading_agent.research.btc_context_prereg import INPUTS, TARGETS, keep_added_arm
from trading_agent.backtest.tournament import DEFAULT_SCENARIOS
from trading_agent.strategies.own24_context import attach_btc_close
from trading_agent.strategies.own24_fill_context import (
    Own24FillContextStrategy, fill_policy_fingerprint_plus,
)

ROOT = Path(__file__).resolve().parents[3]
BUDGET = {"cell_timeout_seconds": 600, "total_timeout_seconds": 1000,
          "max_rss_mb": 2048, "workers": 1, "max_retries": 0}
WINDOW = {"start": 216, "end": 25198}
ARMS = ["A0", "PLUS"]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def frame_digest(frame):
    buffer = io.BytesIO()
    frame.write_ipc(buffer, compression="uncompressed")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _scope(symbol, cost):
    scenarios = {item.name: item for item in DEFAULT_SCENARIOS}
    if symbol not in TARGETS or cost not in scenarios:
        raise ValueError("unregistered development symbol/cost")
    return scenarios[cost]


def _native_contract(arm, symbol="ETH/USDT", cost="1x"):
    from scripts import full_system_backtest as simulator
    from trading_agent.config.loader import config

    scenario = _scope(symbol, cost)
    return simulator.describe_execution_contract(
        symbol=symbol, timeframe="1h", strategy_name="own24_fill_context",
        parameters={"arm": arm}, commission=config.commission * scenario.fee_multiplier,
        slippage=config.slippage * scenario.slippage_multiplier,
    )


def prepare_packet(packet, authorization, *, symbol="ETH/USDT", cost="1x"):
    """Freeze one two-cell attempt. Authorization is preserved human evidence."""
    _scope(symbol, cost)
    packet = Path(packet).resolve()
    if not isinstance(authorization, dict) or not authorization.get("user_instruction"):
        raise ValueError("missing human authorization evidence")
    if packet.exists():
        raise ValueError("use a new packet; no overwrite or resume")
    environment = execution_environment()
    if environment["simulator_settings"]["EXCHANGE"] != "binance":
        raise ValueError("development contrast requires the existing Binance profile")
    plan = {
        "version": 1, "kind": "EXPOSED_DEVELOPMENT_NOT_QUALIFICATION",
        "subject": {"strategy_id": "own24_fill_context", "symbol": symbol, "timeframe": "1h"},
        "arms": ARMS, "cost": cost, "window": WINDOW, "budget": BUDGET,
        "inputs": {s: INPUTS[s] for s in (symbol, "BTC/USDT")},
        "source": capture_campaign_source(ROOT), "environment": environment,
        "fill_policy_fingerprint_plus": fill_policy_fingerprint_plus(),
        "native_contracts": {arm: _native_contract(arm, symbol, cost) for arm in ARMS},
        "authorization": authorization, "holdout": False, "live_authorized": False,
        "command": [".venv/bin/python", "scripts/run_wfo_parallel.py",
                    "--fill-cycle-development", str(packet / "protocol.json"),
                    "--no-real-sensitivity"],
    }
    plan["fingerprint"] = digest(plan)
    packet.mkdir(parents=True)
    _durable_json(packet / "protocol.json", plan)
    return plan


def verify_plan(path):
    path = Path(path).resolve()
    plan = json.loads(path.read_bytes())
    body = {k: v for k, v in plan.items() if k != "fingerprint"}
    if plan.get("fingerprint") != digest(body):
        raise ValueError("development protocol fingerprint mismatch")
    symbol = plan.get("subject", {}).get("symbol")
    cost = plan.get("cost")
    _scope(symbol, cost)
    expected = {
        "version": 1, "kind": "EXPOSED_DEVELOPMENT_NOT_QUALIFICATION",
        "subject": {"strategy_id": "own24_fill_context", "symbol": symbol, "timeframe": "1h"},
        "arms": ARMS, "cost": cost, "window": WINDOW, "budget": BUDGET,
        "inputs": {s: INPUTS[s] for s in (symbol, "BTC/USDT")},
        "fill_policy_fingerprint_plus": fill_policy_fingerprint_plus(),
        "holdout": False, "live_authorized": False,
        "command": [".venv/bin/python", "scripts/run_wfo_parallel.py",
                    "--fill-cycle-development", str(path), "--no-real-sensitivity"],
    }
    for name, value in expected.items():
        if plan.get(name) != value:
            raise ValueError(f"development binding mismatch: {name}")
    if not plan.get("authorization", {}).get("user_instruction"):
        raise ValueError("missing human authorization evidence")
    if capture_campaign_source(ROOT)["tree_fingerprint"] != plan["source"]["tree_fingerprint"]:
        raise ValueError("development source mismatch")
    if execution_environment() != plan["environment"]:
        raise ValueError("development runtime environment mismatch")
    if plan["native_contracts"] != {arm: _native_contract(arm, symbol, cost) for arm in ARMS}:
        raise ValueError("development native contract mismatch")
    if os.environ.get("TRADING_FROZEN_RESEARCH_DATA"):
        raise ValueError("conflicting frozen input binding")
    for binding in plan["inputs"].values():
        if hashlib.sha256((ROOT / binding["path"]).read_bytes()).hexdigest() != binding["sha256"]:
            raise ValueError("development input hash mismatch")
    return plan


@dataclass(frozen=True)
class DevelopmentCycleInput:
    protocol_path: Path
    protocol_fingerprint: str
    arm: str
    frame_sha256: str


def load_input(path, arm):
    plan = verify_plan(path)
    if arm not in ARMS:
        raise ValueError("unregistered arm")
    frames = {}
    for symbol, binding in plan["inputs"].items():
        raw = (ROOT / binding["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ValueError("input changed before decode")
        frames[symbol] = pl.read_parquet(io.BytesIO(raw)).sort("timestamp")
    frame = attach_btc_close(frames[plan["subject"]["symbol"]], frames["BTC/USDT"])
    if frame.height != WINDOW["end"]:
        raise ValueError("development clock length mismatch")
    return frame, DevelopmentCycleInput(Path(path).resolve(), plan["fingerprint"], arm, frame_digest(frame))


def verify_simulator_input(request, frame, *, symbol, timeframe, strategy, manifest, contract_id,
                           state_dir, report_path, fresh):
    from trading_agent.backtest.thinning_execution import (
        ThinningCycleInput, verify_simulator_input as verify_thinning_input,
    )

    if type(request) is ThinningCycleInput:
        return verify_thinning_input(request, frame, symbol=symbol, timeframe=timeframe,
            strategy=strategy, manifest=manifest, contract_id=contract_id,
            state_dir=state_dir, report_path=report_path, fresh=fresh)
    if type(request) is not DevelopmentCycleInput or frame is None:
        raise ValueError("missing bound development cycle input")
    plan = verify_plan(request.protocol_path)
    cell_root = request.protocol_path.parent / "cells" / request.arm
    if (fresh is not True or state_dir is None or report_path is None
            or Path(state_dir).resolve() != cell_root / "execution"
            or Path(report_path).resolve() != cell_root / "report.json"
            or (cell_root / "report.json").exists()
            or ((cell_root / "execution").exists() and any((cell_root / "execution").iterdir()))):
        raise ValueError("development output must be fresh and isolated in the registered cell")
    if (request.protocol_fingerprint != plan["fingerprint"] or request.arm not in ARMS
            or type(strategy) is not Own24FillContextStrategy
            or strategy.params != {"arm": request.arm}
            or symbol != plan["subject"]["symbol"] or timeframe != "1h"
            or manifest != "development:" + plan["fingerprint"]
            or contract_id != plan["native_contracts"][request.arm]["contract_id"]
            or request.frame_sha256 != frame_digest(frame)):
        raise ValueError("development simulator binding mismatch")
    expected_frame, _ = load_input(request.protocol_path, request.arm)
    if request.frame_sha256 != frame_digest(expected_frame):
        raise ValueError("development frame differs from registered input")
    return plan


def run_worker(path, arm):
    from scripts.full_system_backtest import FullSystemSimulator

    frame, request = load_input(path, arm)
    plan = verify_plan(path)
    out = Path(path).resolve().parent / "cells" / arm
    out.mkdir(parents=True, exist_ok=True)
    _durable_json(out / "started.json", {"arm": arm, "pid": os.getpid(),
        "protocol_fingerprint": plan["fingerprint"], "max_retries": 0})
    model = plan["native_contracts"][arm]["body"]["cost_model"]
    simulator = FullSystemSimulator(
        fresh=True, symbol=plan["subject"]["symbol"], timeframe="1h", state_dir=out / "execution",
        report_path=out / "report.json", run_id=out.parent.parent.name + "_" + arm,
        data_manifest_id="development:" + plan["fingerprint"], gap_policy="reject",
        source_df_override=frame, execution_cycle_strategy=Own24FillContextStrategy({"arm": arm}),
        execution_cycle_development_input=request,
        expected_execution_contract_id=plan["native_contracts"][arm]["contract_id"],
        commission=model["commission_rate"], slippage=model["slippage_rate"],
        funnel_enabled=True,
    )
    try:
        report = simulator.run(start=WINDOW["start"], end=WINDOW["end"])
        require_execution_contract(plan["native_contracts"][arm]["contract_id"],
                                   report["active_config"]["execution_contract"])
        if report.get("measured_accounting") is None or report.get("measured_accounting_error"):
            raise ValueError("missing measured accounting")
        verify_plan(path)
        _durable_json(out / "result.json", {"status": "COMPLETED", "arm": arm,
            "report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest(),
            "protocol_fingerprint": plan["fingerprint"], "market_run": True,
            "evidence_class": "DEVELOPMENT_EXPOSED_NOT_CONFIRMATION", "live_authorized": False})
    finally:
        simulator.engine.store.close()


def summarize_report(report):
    """Primary PnL includes any marked carry; closed-trade PnL stays explicit."""
    row = {k: report[k] for k in ("total_trades", "total_return_pct", "sharpe",
        "max_drawdown_pct", "profit_factor", "open_positions", "open_orders")}
    row["closed_trade_net_pnl"] = report["metrics"]["net_pnl"]
    row["net_pnl"] = report["final_equity"] - report["initial_capital"]
    row["measured_accounting_error"] = report.get("measured_accounting_error")
    return row


def run_packet(path):
    plan = verify_plan(path)
    root = Path(path).resolve().parent
    _durable_json(root / "execution_started.json", {"protocol_fingerprint": plan["fingerprint"],
        "started_at_epoch": time.time(), "live_authorized": False})
    started = time.monotonic()
    outcomes = []
    for arm in ARMS:
        out = root / "cells" / arm
        out.mkdir(parents=True, exist_ok=True)
        remaining = BUDGET["total_timeout_seconds"] - (time.monotonic() - started)
        if remaining <= 0:
            outcomes.append({"arm": arm, "status": "NOT_ATTEMPTED_TOTAL_TIMEOUT"})
            break
        _durable_json(out / "execution_started.json", {"arm": arm, "started_at_epoch": time.time(),
            "max_retries": 0, "protocol_fingerprint": plan["fingerprint"]})
        receipt = _supervise_validation(
            [sys.executable, "-m", __name__, str(Path(path).resolve()), arm], out,
            total_timeout_seconds=min(BUDGET["cell_timeout_seconds"], remaining),
            max_rss_mb=BUDGET["max_rss_mb"], cwd=ROOT,
            group_rss_mb=_validation_group_rss_mb, group_pids=_validation_group_pids,
            wait_for_all_reaped=partial(_wait_for_all_reaped, group_pids=_validation_group_pids),
        )
        if receipt["status"] == "COMPLETED" and not all(
                (out / name).is_file() for name in ("result.json", "report.json")):
            receipt["status"] = "INCOMPLETE_MISSING_WORKER_RECEIPT"
        receipt["arm"] = arm
        _durable_json(out / "execution.json", receipt)
        outcomes.append(receipt)
        if receipt["status"] != "COMPLETED":
            break
    complete = len(outcomes) == 2 and all(r["status"] == "COMPLETED" for r in outcomes)
    result = {"status": "COMPLETED" if complete else "INCOMPLETE", "outcomes": outcomes,
        "subject": plan["subject"], "cost": plan["cost"],
        "elapsed_seconds": time.monotonic() - started, "protocol_fingerprint": plan["fingerprint"],
        "live_authorized": False, "qualified": False, "holdout_opened": False,
        "evidence_class": "DEVELOPMENT_EXPOSED_NOT_CONFIRMATION"}
    try:
        if complete:
            rows = {}
            for arm in ARMS:
                report = json.loads((root / "cells" / arm / "report.json").read_bytes())
                proof = json.loads((root / "cells" / arm / "result.json").read_bytes())
                if hashlib.sha256((root / "cells" / arm / "report.json").read_bytes()).hexdigest() != proof["report_sha256"]:
                    raise ValueError("completed report hash mismatch")
                rows[arm] = summarize_report(report)
                if not all(math.isfinite(float(rows[arm][k])) for k in ("total_return_pct", "sharpe", "max_drawdown_pct")):
                    raise ValueError("non-finite development metrics")
            common = []
            for arm in ARMS:
                body = json.loads(json.dumps(plan["native_contracts"][arm]["body"]))
                body["strategy"]["parameters"].pop("arm")
                common.append(digest(body))
            same_execution = len(set(common)) == 1
            result["execution_comparability"] = {"normalized_native_contract_sha256": common,
                "same_execution_policy_except_arm": same_execution,
                "same_source_and_inputs": True, "trade_vectors_required_equal": False}
            base, plus = rows["A0"], rows["PLUS"]
            result["comparisons"] = rows
            result["return_delta_percentage_points"] = plus["total_return_pct"] - base["total_return_pct"]
            result["diagnostic_verdict"] = keep_added_arm(
                trades=min(base["total_trades"], plus["total_trades"]), sharpe=plus["sharpe"],
                marked_net_return=plus["total_return_pct"], previous_marked_net_return=base["total_return_pct"],
                max_drawdown=plus["max_drawdown_pct"], previous_max_drawdown=base["max_drawdown_pct"],
                same_execution_path=same_execution,
            )
        verify_plan(path)
    except Exception as exc:
        result["status"] = "INCOMPLETE"
        result["diagnostic_verdict"] = "INCONCLUSIVE"
        result["verification_error"] = f"{type(exc).__name__}: {exc}"
    _durable_json(root / "summary.json", result)
    return 0 if result["status"] == "COMPLETED" else 2


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("worker requires protocol path and registered arm")
    from trading_agent.backtest.fill_cycle_development import run_worker as worker

    worker(Path(sys.argv[1]), sys.argv[2])
