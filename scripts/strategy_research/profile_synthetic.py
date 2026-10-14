"""Bounded simulator profiling, synthetic only; never qualification or pilot ETA.

Each preset runs inline in a disposable process, supervised by a Linux session
deadline/RSS ceiling. Inline tournament controls are NOT claimed to enforce
limits: the supervisor does that. No WFO registry, policy or market loader.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
import os
import pstats
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUBJECTS = ("trend_pullback", "range_mean_reversion", "volatility_breakout",
            "vol_target", "regime_ensemble")


def identity():
    from trading_agent.backtest.campaign_execution import execution_environment, simulator_runtime_config
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    return {"source": capture_campaign_source(ROOT), "environment": execution_environment(),
            "runtime": simulator_runtime_config()}


def publish(path, payload):
    from trading_agent.backtest.campaign_execution import _durable_json
    _durable_json(path, payload)


def _rss_mb(pid):
    """Sample worker RSS, not virtual address space; Linux only, fail closed."""
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except FileNotFoundError:
        return None  # Process exited between poll and sample.
    for line in lines:
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return None  # Zombie has no address space.


def supervise(command, directory, *, timeout, memory_mb):
    """Own exactly one process session; retain full logs, stop before returning."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("profiling supervisor requires Linux /proc and process sessions")
    started = time.monotonic()
    peak = 0.0
    reason = None
    with (directory / "stdout.log").open("x") as stdout, (directory / "stderr.log").open("x") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            while process.poll() is None:
                rss = _rss_mb(process.pid)
                peak = max(peak, rss or 0)
                if time.monotonic() - started >= timeout:
                    reason = "TIMEOUT"
                    break
                if rss is not None and rss > memory_mb:
                    reason = "MEMORY_LIMIT"
                    break
                time.sleep(0.05)
        finally:
            # This PID is our session leader, never an unrelated process group.
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
    return {"status": reason or ("COMPLETED" if process.returncode == 0 else "WORKER_ERROR"),
            "worker_exit_code": process.returncode, "wall_seconds": time.monotonic() - started,
            "sampled_peak_rss_mb": peak, "rss_sample_interval_seconds": 0.05,
            "timeout_seconds": timeout, "memory_ceiling_mb": memory_mb,
            "memory_enforcement": "sampled_RSS_not_hard_allocator_limit", "attempts": 1}


def worker(directory):
    import resource
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
    from trading_agent.backtest.tournament import EvaluationCellSpec, run_cell
    from trading_agent.data.storage import frozen_research_data
    from trading_agent.research.param_grids import get_param_grid, get_strategy_code_name

    request = json.loads((directory / "started.json").read_text())
    if request["identity"] != identity() or request["strategy"] not in SUBJECTS:
        raise ValueError("worker identity/subject mismatch")
    strategy = request["strategy"]
    # First catalog tuple, predetermined before simulation; not tuned on returns.
    params = {key: values[0] for key, values in get_param_grid(strategy).items()}
    frame = generate_synthetic_ohlcv(n_bars=request["bars"], seed=7)
    input_path = directory / "synthetic.parquet"
    if input_path.exists():
        raise FileExistsError(input_path)
    frame.write_parquet(input_path)
    spec = EvaluationCellSpec(get_strategy_code_name(strategy), "BTC/USDT", "1h", params)
    profiler = cProfile.Profile()
    with frozen_research_data(input_path, exchange="binance", symbol="BTC/USDT", timeframe="1h"):
        artifact = profiler.runcall(run_cell, spec, out_root=directory / "simulation",
                                   end=request["bars"], _use_multiprocessing=False)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    profiler.dump_stats(str(directory / "profile.pstats"))
    stats = pstats.Stats(profiler)
    rows = [{"file": key[0], "line": key[1], "function": key[2],
             "exclusive_seconds": value[2], "cumulative_seconds": value[3]}
            for key, value in stats.stats.items()]
    if request["identity"] != identity():
        raise ValueError("worker source/config drift")
    publish(directory / "measurement.json", {
        "identity": request["identity"], "strategy": strategy, "params": params,
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "artifact": artifact.to_dict(), "cpu_seconds": usage.ru_utime + usage.ru_stime,
        "process_lifetime_peak_rss_mb": usage.ru_maxrss / 1024,
        "profile_total_exclusive_seconds": stats.total_tt,
        "top_exclusive": sorted(rows, key=lambda row: row["exclusive_seconds"], reverse=True)[:15],
        "cost": spec.cost_scenario.to_dict(), "scope": "SYNTHETIC_ONLY_BTC_1H",
        "qualified": False, "live_authorized": False,
    })
    return 0 if artifact.status == "COMPLETED" else 1


def run(output, *, bars=300, cell_timeout=60, total_timeout=330, memory_mb=1024):
    if (not 300 <= bars <= 1200 or not math.isfinite(cell_timeout)
            or not math.isfinite(total_timeout) or not 0 < cell_timeout <= 120
            or not 0 < total_timeout <= 650):
        raise ValueError("synthetic budget outside bounded limits")
    if not 128 <= memory_mb <= 4096:
        raise ValueError("memory budget outside bounded limits")
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Linux required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = identity()
    controls = dict(bars=bars, cell_timeout_seconds=cell_timeout, total_timeout_seconds=total_timeout,
                    memory_ceiling_mb=memory_mb, workers=1, retries=0)
    publish(output / "started.json", {"identity": before, "controls": controls,
                                     "scope": "SYNTHETIC_ONLY_BTC_1H", "live_authorized": False})
    deadline = time.monotonic() + total_timeout
    rows = []
    status = "INCOMPLETE"
    error = None
    try:
        for strategy in SUBJECTS:
            if identity() != before:
                raise ValueError("source/config drift before cell")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                rows.append({"strategy": strategy, "status": "NOT_STARTED_BUDGET_EXHAUSTED"})
                continue
            directory = output / strategy
            directory.mkdir()
            publish(directory / "started.json", {"identity": before, "strategy": strategy, "bars": bars})
            measured = supervise([sys.executable, str(Path(__file__).resolve()), "--worker", str(directory)],
                                 directory, timeout=min(cell_timeout, remaining), memory_mb=memory_mb)
            measured["strategy"] = strategy
            path = directory / "measurement.json"
            if measured["status"] == "COMPLETED":
                raw = json.loads(path.read_text())
                if raw["identity"] != before or raw["artifact"]["status"] != "COMPLETED":
                    raise ValueError("invalid completed measurement")
                measured.update(cpu_seconds=raw["cpu_seconds"],
                                process_lifetime_peak_rss_mb=raw["process_lifetime_peak_rss_mb"],
                                measurement_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            publish(directory / "finished.json", measured)
            rows.append(measured)
            print(json.dumps(measured), flush=True)
        if identity() != before:
            raise ValueError("source/config drift after profiling")
        status = "PASS" if all(row["status"] == "COMPLETED" for row in rows) else "FAIL"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        publish(output / "finished.json", {"status": status, "error": error, "identity": before,
            "identity_after": identity(), "controls": controls, "cells": rows,
            "market_evaluations": 0, "qualified": False, "live_authorized": False,
            "campaign_eta_seconds": None, "representative_calibration": False,
            "coverage": "one fixed preset/cost/window per strategy; no p50/p95 or WFO ETA",
            "limits": "profiled inline simulator; supervisor wall/RSS, not tournament retry controls"})
    return 0 if status == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--cell-timeout", type=float, default=60)
    parser.add_argument("--total-timeout", type=float, default=330)
    parser.add_argument("--memory-mb", type=int, default=1024)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker.resolve())
    if args.output is None:
        parser.error("--output must name a new evidence directory")
    # The outer controlled toolkit may terminate us; always stop our child.
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("supervisor terminated")
    signal.signal(signal.SIGTERM, interrupted)
    return run(args.output, bars=args.bars, cell_timeout=args.cell_timeout,
               total_timeout=args.total_timeout, memory_mb=args.memory_mb)


if __name__ == "__main__":
    raise SystemExit(main())
