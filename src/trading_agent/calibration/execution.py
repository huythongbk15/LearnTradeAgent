"""Explicit-input, serial validation calibration; no qualification or live path.

Heavy imports and price reads occur only after freezing input timestamps strictly
before holdout. Each measurement runs in a fresh, supervised Linux process group.
"""

from __future__ import annotations

import bisect
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .packet import (
    calibration_grid,
    canonical,
    contained,
    default_budget,
    digest,
    inventory,
    read,
    registry_snapshot,
    scoped_snapshot,
    validate_plan,
    validate_approval,
    verify_calibration_packet,
)

ROOT = Path(__file__).resolve().parents[3]
STRATEGIES = ("rsi", "bbands", "enhanced_ma", "ma_adx", "ma_vol_target")


def write(path, value):
    from trading_agent.backtest.campaign_execution import _durable_json

    _durable_json(Path(path), value)


def utc(value):
    value = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def capture_identity(input_path):
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    from trading_agent.backtest.campaign_execution import (
        execution_environment,
        simulator_runtime_config,
    )

    return {
        "source": capture_campaign_source(ROOT),
        "environment": execution_environment(),
        "runtime_config": simulator_runtime_config(),
        "input_sha256": hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
    }


def freeze_plan(out, input_path, *, synthetic=False, boundary_path=None, strategies=None):
    from trading_agent.calibration.packet import _validate_expected_strategies

    # Validate explicit input before imports, file access, or output creation.
    requested_strategies = _validate_expected_strategies(strategies)
    active_strategies = ("rsi",) if synthetic else requested_strategies
    import polars as pl
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract
    from trading_agent.research.trials import (
        ExperimentRegistry,
        ExperimentSpec,
        param_hash,
        search_space_hash,
    )
    from trading_agent.strategies.canonical.candidates import (
        build_default_registry,
        validate_params,
        resolved_warmup_bars,
    )

    out, input_path = Path(out).resolve(), Path(input_path).resolve(strict=True)
    # Price columns are not loaded until timestamps establish the input boundary.
    times = [
        utc(t)
        for t in pl.read_parquet(input_path, columns=["timestamp"])[
            "timestamp"
        ].to_list()
    ]
    if not times or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError("input needs strictly increasing unique timestamps")
    if synthetic and boundary_path is None:
        # Create a synthetic boundary for testing
        boundary = {
            "holdout_start_utc": "2026-01-01T00:00:00+00:00",
            "research_manifest_sha256": hashlib.sha256(ROOT.joinpath("data/research_manifest.json").read_bytes()).hexdigest(),
            "fixture_only": True,
        }
    else:
        if boundary_path is None:
            raise ValueError("explicit frozen boundary JSON required")
        boundary_path = Path(boundary_path).resolve(strict=True)
        boundary = read(boundary_path)
        if synthetic:
            boundary = {
                **boundary,
                "fixture_only": True,
            }
        # Boundary JSON must be bound to the repository's preregistered manifest.
        registered = ROOT / "data/research_manifest.json"
        if (
            boundary.get("research_manifest_sha256")
            != hashlib.sha256(registered.read_bytes()).hexdigest()
        ):
            raise ValueError("research manifest binding mismatch")
        manifest = read(registered)
        # Metadata only: never resolve the boundary by loading holdout prices.
        from trading_agent.alpha_research.holdout import holdout_window

        start, _ = holdout_window(manifest)
        if utc(boundary["holdout_start_utc"]) != utc(start):
            raise ValueError("holdout boundary differs from preregistration")
    holdout = utc(boundary["holdout_start_utc"])
    if times[-1].replace(tzinfo=UTC) >= holdout:
        raise ValueError(
            "input includes holdout; provide an existing pre-holdout input"
        )
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("fresh empty packet directory required; no resume/reseal")
    out.mkdir(parents=True, exist_ok=True)
    timeframe = "1h" if synthetic else "4h"
    frame = pl.read_parquet(input_path)
    # Same serialized data identity used by canonical WFO; input bytes also bound.
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    data_sha = hashlib.sha256(buffer.getvalue()).hexdigest()
    identity = capture_identity(input_path)
    registry = (
        out / "trials.sqlite3"
        if synthetic
        else ROOT / "data/wfo/experiments_parallel.sqlite3"
    )
    if synthetic:
        ExperimentRegistry(str(registry))
    elif not registry.is_file():
        raise ValueError(
            "operational registry missing; never initialize a new market history"
        )
    before = registry_snapshot(registry)
    windows = (
        [("early", 200, len(times) // 2), ("late", len(times) // 2, len(times))]
        if synthetic
        else [
            (
                "early",
                bisect.bisect_left(times, utc("2024-01-01")),
                bisect.bisect_left(times, utc("2024-07-01")),
            ),
            (
                "late",
                bisect.bisect_left(times, utc("2025-07-01")),
                bisect.bisect_left(times, utc("2026-01-01")),
            ),
        ]
    )
    cells = []
    for strategy in active_strategies:
        params = validate_params(strategy, None)
        descriptor = build_default_registry().describe(strategy)
        grid = calibration_grid(strategy, synthetic=synthetic)
        feature_sha = digest(sorted(descriptor.required_features))
        experiment = ExperimentSpec.build(
            strategy_name=strategy,
            strategy_code_sha=descriptor.code_sha,
            data_manifest_sha=data_sha,
            feature_schema_hash=feature_sha,
            params_hash=param_hash(params | {"cost_scenario": "1x"}),
            search_family="s3_wfo_parallel_canonical",
            search_space_hash=search_space_hash(grid),
            target_horizon=f"{timeframe}_wfo",
            evaluator_version="v1",
            seed=42,
        )
        for name, start, end in windows:
            if not 0 < start < end <= len(times):
                raise ValueError("validation window unavailable")
            if not synthetic and (
                times[start] != utc("2024-01-01" if name == "early" else "2025-07-01")
                or end == len(times)
            ):
                raise ValueError("input does not cover preregistered validation window")
            cells.append(
                {
                    "key": f"{strategy}_cal_{name}",
                    "fold_id": f"cal_{name}",
                    "strategy_id": strategy,
                    "params": params,
                    "identity_grid": grid,
                    "experiment": asdict(experiment)
                    | {"created_at": experiment.created_at.isoformat()},
                    "measurement_window": [start, end],
                    "simulation_window": [
                        max(0, start - resolved_warmup_bars(strategy, params) - 100),
                        end,
                    ],
                    "start_utc": times[start].isoformat(),
                    "end_utc": times[end - 1].isoformat(),
                }
            )
    budget = default_budget()
    plan = {
        "worker_authority_contract": "owned_pipe_intent_v1",
        "scope": "INNER_VALIDATION",
        "evidence_class": "SYNTHETIC_TEST_ONLY" if synthetic else "REAL_MARKET",
        "symbol": "BTC/USDT",
        "timeframe": timeframe,
        "budget": budget,
        "source_before": identity["source"],
        "identity_before": identity,
        "input": {
            "path": str(input_path),
            "sha256": identity["input_sha256"],
            "rows": len(times),
            "data_manifest_sha": data_sha,
        },
        "holdout_start_utc": holdout.isoformat(),
        "boundary": boundary,
        "registry_path": str(registry),
        "cells": cells,
        "expected_strategies": list(active_strategies),
        "cost": {"name": "1x", "fee_multiplier": 1.0, "slippage_multiplier": 1.0},
        "selection_contract": freeze_selection_contract(
            min_validation_trades=30, min_validation_sharpe=0.0
        ),
        "execution_context": {
            "trial_identity_version": 2,
            "source": identity["source"],
            "environment": identity["environment"],
            "runtime_config": identity["runtime_config"]
            | {"calibration_budget": budget},
        },
    }
    manifest = {
        "protocol_version": "2.0",
        "run_id": f"cal_{digest(plan)[:12]}",
        **plan,
        "plan": plan,
        "protocol_fingerprint": digest(plan),
    }
    write(out / "registry_before.json", before)
    write(out / "calibration_manifest.json", manifest)
    return manifest


def group_usage(group):
    """Linux /proc group sampling. Zombie PIDs cannot write or consume resources."""
    rss, cpu, active, zombies = 0, 0.0, [], []
    tick = os.sysconf("SC_CLK_TCK")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) != group:
                continue
            if fields[0] in ("Z", "X"):
                zombies.append(int(entry.name))
                continue
            active.append(int(entry.name))
            rss += int(fields[21]) * os.sysconf("SC_PAGE_SIZE")
            # cutime/cstime retain CPU of waited-for children after they vanish
            # from /proc. They exclude living children, so summing the active
            # group does not double-count them.
            cpu += sum(int(fields[i]) for i in (11, 12, 13, 14)) / tick
        except (OSError, ValueError, IndexError):
            continue
    return rss, cpu, active, zombies


AUTHORITY_FD_ENV = "TRADING_CALIBRATION_AUTHORITY_FD"


def _consume_worker_authority(out, key):
    """Internal workers require a one-shot pipe from their live owning parent.

    This prevents accidental standalone/replay execution, not hostile code by
    the same OS user. Approval and registered intent are checked independently.
    """
    descriptor = os.environ.pop(AUTHORITY_FD_ENV, None)
    if descriptor is None:
        raise ValueError("worker requires supervised parent authority")
    fd = int(descriptor)
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise ValueError("worker authority must be an inherited pipe")
        authority = json.loads(os.read(fd, 8192))
    finally:
        os.close(fd)
    if (
        authority.get("parent_pid") != os.getppid()
        or os.getpgrp() != os.getpid()
        or authority.get("key") != key
        or authority.get("out") != str(Path(out).resolve())
    ):
        raise ValueError("worker authority owner/scope mismatch")
    return authority


def _validate_worker_intent(out, key, authority):
    from trading_agent.research.trials import execution_scope_identity

    manifest = read(contained(out, "calibration_manifest.json"))
    fingerprint = manifest["protocol_fingerprint"]
    if (
        manifest.get("protocol_version") != "2.0"
        or digest({k: v for k, v in manifest.items() if k not in ("protocol_version", "run_id", "protocol_fingerprint", "plan")}) != fingerprint
        or authority.get("protocol_fingerprint") != fingerprint
    ):
        raise ValueError("worker protocol differs from parent authority")
    plan = manifest
    cell = next(c for c in manifest["cells"] if c["key"] == key)
    intent = read(contained(out, f"attempts/{key}.json"))
    if intent["key"] != key or intent["evaluation_id"] != authority.get(
        "intent_evaluation_id"
    ):
        raise ValueError("worker intent differs from parent authority")
    context = plan["execution_context"] | {"protocol_fingerprint": fingerprint}
    rows = scoped_snapshot(registry_snapshot(plan["registry_path"]), fingerprint)[
        "evaluation_records"
    ]
    matching = [row for row in rows if row["evaluation_id"] == intent["evaluation_id"]]
    if len(matching) != 1:
        raise ValueError("worker requires one registered intent")
    row = matching[0]
    metadata = row["metadata_json"]
    if (
        row["experiment_id"] != cell["experiment"]["experiment_id"]
        or row["fold_id"] != cell["fold_id"]
        or row["trial_phase"] != "UNKNOWN"
        or row["metric_name"] != "inner_validation_attempt"
        or row["metric_value"] != 0.0
        or metadata.get("status") != "STARTED"
        or metadata.get("bookkeeping_only") is not True
        or metadata.get("evaluation_execution_identity")
        != execution_scope_identity(context)
        or metadata.get("evaluation_identity", {}).get("execution_context") != context
    ):
        raise ValueError("worker intent identity/status mismatch")
    if any(
        r["experiment_id"] == row["experiment_id"]
        and r["fold_id"] == row["fold_id"]
        and r["trial_phase"] == "INNER_VALIDATION"
        for r in rows
    ):
        raise ValueError("worker outcome already recorded; no replay")
    if plan["evidence_class"] == "REAL_MARKET":
        validate_approval(
            read(contained(out, "operator_approval.json")),
            fingerprint,
            not_after=row["created_at"],
        )
    return plan, cell


def supervise(
    command, *, budget, log_path, remaining_wall, remaining_cpu, authority=None
):
    """Only terminate this invocation's process group, including descendants."""
    if sys.platform != "linux":
        raise RuntimeError("calibration supervision requires Linux /proc")
    started, rss_peak, cpu_peak, limit = time.monotonic(), 0, 0.0, None
    with Path(log_path).open("xb") as log:
        inherited, environment = None, None
        try:
            if authority is not None:
                inherited, writer = os.pipe()
                try:
                    os.write(
                        writer,
                        canonical(authority | {"parent_pid": os.getpid()}).encode(),
                    )
                finally:
                    os.close(writer)
                environment = os.environ.copy()
                environment[AUTHORITY_FD_ENV] = str(inherited)
            proc = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
                pass_fds=(inherited,) if inherited is not None else (),
            )
        finally:
            if inherited is not None:
                os.close(inherited)
        try:
            while proc.poll() is None:
                rss, cpu, _, _ = group_usage(proc.pid)
                rss_peak, cpu_peak = max(rss_peak, rss), max(cpu_peak, cpu)
                if time.monotonic() - started > min(
                    budget["cell_wall_seconds"], remaining_wall
                ):
                    limit = "wall"
                elif cpu_peak > min(budget["cell_cpu_seconds"], remaining_cpu):
                    limit = "cpu"
                elif rss_peak > budget["rss_limit_bytes"]:
                    limit = "rss"
                if limit:
                    break
                time.sleep(0.02)
        finally:
            _, _, active, _ = group_usage(proc.pid)
            if active:
                if proc.poll() is not None and limit is None:
                    limit = "active_descendants_after_exit"
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                time.sleep(0.1)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=5)
    _, _, active, zombies = group_usage(proc.pid)
    return {
        "wall_seconds": time.monotonic() - started,
        "cpu_seconds": cpu_peak,
        "sampled_cpu_seconds": cpu_peak,
        "sampled_rss_peak_bytes": rss_peak,
        "worker_pid": proc.pid,
        "supervisor_pid": os.getpid(),
        "rss_peak_bytes": rss_peak,
        "limit_exceeded": limit,
        "returncode": proc.returncode,
        "active_descendants": active,
        "zombie_pids": zombies,
        "enforcement": "owned_process_group_watchdog",
    }


def worker(out, key):
    out = Path(out).resolve()
    authority = _consume_worker_authority(out, key)
    plan, cell = _validate_worker_intent(out, key, authority)
    import resource
    from trading_agent.backtest.tournament import (
        CostScenario,
        EvaluationCellSpec,
        run_cell,
    )
    from trading_agent.data.storage import frozen_research_data

    identity_at_start = capture_identity(plan["input"]["path"])
    if identity_at_start != plan["identity_before"]:
        raise ValueError("source/input/config drift before worker measurement")
    spec = EvaluationCellSpec(
        strategy_id=cell["strategy_id"],
        symbol=plan["symbol"],
        timeframe=plan["timeframe"],
        params=cell["params"],
        cost_scenario=CostScenario(**plan["cost"]),
    )
    with frozen_research_data(
        Path(plan["input"]["path"]),
        exchange="binance",
        symbol=plan["symbol"],
        timeframe=plan["timeframe"],
    ):
        artifact = run_cell(
            spec,
            out_root=out / "cells" / key,
            start=cell["simulation_window"][0],
            end=cell["simulation_window"][1],
            measurement_start=cell["measurement_window"][0],
            measurement_end=cell["measurement_window"][1],
            fresh=True,
            timeout_seconds=plan["budget"]["cell_wall_seconds"],
            max_retries=0,
            _use_multiprocessing=False,
        )
    source = {"artifact": artifact.to_dict()}
    if artifact.report_path:
        path = Path(artifact.report_path)
        source |= {
            "report_path": path.relative_to(out).as_posix(),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    write(
        out / "worker_results" / f"{key}.json",
        {
            "status": artifact.status,
            "identity_at_start": identity_at_start,
            "worker_pid": os.getpid(),
            "worker_authority": authority,
            "candidate": {
                "params": cell["params"],
                "cost_scenario": "1x",
                "val_metrics": dict(artifact.metrics),
                "validation_source": source,
            },
            "cpu_seconds": cpu.ru_utime
            + cpu.ru_stime
            + children.ru_utime
            + children.ru_stime,
            "rss_peak_bytes": max(cpu.ru_maxrss, children.ru_maxrss) * 1024,
        },
    )


def execute(out, approval_path=None):
    from trading_agent.backtest.selection_eligibility import (
        assess_validation_candidates,
    )
    from trading_agent.research.trials import ExperimentRegistry, ExperimentSpec

    out = Path(out).resolve()
    manifest = read(out / "calibration_manifest.json")
    fingerprint = manifest["protocol_fingerprint"]
    if manifest.get("protocol_version") != "2.0" or digest({k: v for k, v in manifest.items() if k not in ("protocol_version", "run_id", "protocol_fingerprint", "plan")}) != fingerprint:
        raise ValueError("invalid frozen protocol")
    plan = manifest
    validate_plan(plan, out)
    if (out / TERMINAL_NAME).exists() or (out / "attempts").exists():
        raise FileExistsError("no resume or reseal; create a new packet")
    if capture_identity(plan["input"]["path"]) != plan["identity_before"]:
        raise ValueError("source/input/config changed after freeze")
    if plan["evidence_class"] == "REAL_MARKET":
        if approval_path is None:
            raise ValueError("operator approval required for exact frozen fingerprint")
        approval = read(approval_path)
        validate_approval(approval, fingerprint, not_after=datetime.now(UTC))
        write(out / "operator_approval.json", approval)
    elif plan["evidence_class"] != "SYNTHETIC_TEST_ONLY":
        raise ValueError("unknown evidence class")
    registry = ExperimentRegistry(plan["registry_path"])
    ctx = plan["execution_context"] | {"protocol_fingerprint": fingerprint}
    started, cpu_total, keys = time.monotonic(), 0.0, []
    previous = signal.signal(
        signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    error = None
    try:
        for cell in plan["cells"]:
            budget = plan["budget"]
            remaining_wall = budget["total_wall_seconds"] - (time.monotonic() - started)
            remaining_cpu = budget["total_cpu_seconds"] - cpu_total
            if remaining_wall <= 0 or remaining_cpu <= 0:
                raise TimeoutError("aggregate budget exhausted")
            spec_data = cell["experiment"] | {
                "created_at": utc(cell["experiment"]["created_at"])
            }
            spec = registry.register_experiment(ExperimentSpec(**spec_data))
            identity = {
                "commit_sha": plan["source_before"]["code_commit"],
                "execution_context": ctx,
            }
            # Append-only intent before measurement. Never hide an interrupted attempt.
            intent = registry.append_evaluation(
                experiment_id=spec.experiment_id,
                fold_id=cell["fold_id"],
                metric_name="inner_validation_attempt",
                metric_value=0.0,
                environment_hash=digest(ctx),
                trial_phase="UNKNOWN",
                idempotent=True,
                execution_context=ctx,
                metadata={
                    "evaluation_identity": identity,
                    "status": "STARTED",
                    "bookkeeping_only": True,
                },
            )
            write(
                out / "attempts" / f"{cell['key']}.json",
                {"evaluation_id": intent.evaluation_id, "key": cell["key"]},
            )
            resources = supervise(
                [
                    sys.executable,
                    str(ROOT / "scripts/run_calibration.py"),
                    "--worker",
                    "--out",
                    str(out),
                    "--key",
                    cell["key"],
                ],
                budget=budget,
                log_path=out / f"{cell['key']}.log",
                remaining_wall=remaining_wall,
                remaining_cpu=remaining_cpu,
                authority={
                    "out": str(out),
                    "key": cell["key"],
                    "protocol_fingerprint": fingerprint,
                    "intent_evaluation_id": intent.evaluation_id,
                },
            )
            result_path = out / "worker_results" / f"{cell['key']}.json"
            result = (
                read(result_path)
                if result_path.is_file()
                else {"status": "FAILED", "candidate": None}
            )
            # Exact fresh worker CPU includes user AND system time, not parent lifetime.
            resources["cpu_seconds"] = max(
                resources["cpu_seconds"], result.get("cpu_seconds", 0)
            )
            resources["rss_peak_bytes"] = max(
                resources["rss_peak_bytes"], result.get("rss_peak_bytes", 0)
            )
            if resources["cpu_seconds"] > min(
                budget["cell_cpu_seconds"], remaining_cpu
            ):
                resources["limit_exceeded"] = "cpu"
            if resources["rss_peak_bytes"] > budget["rss_limit_bytes"]:
                resources["limit_exceeded"] = "rss"
            if resources["wall_seconds"] > min(
                budget["cell_wall_seconds"], remaining_wall
            ):
                resources["limit_exceeded"] = "wall"
            status = (
                "COMPLETED"
                if result["status"] == "COMPLETED"
                and resources["returncode"] == 0
                and not resources["limit_exceeded"]
                and not resources["active_descendants"]
                else "FAILED"
            )
            candidate = result["candidate"]
            record = registry.append_evaluation(
                experiment_id=spec.experiment_id,
                fold_id=cell["fold_id"],
                metric_name="inner_validation_sharpe",
                metric_value=candidate["val_metrics"]["sharpe"]
                if status == "COMPLETED"
                else 0.0,
                environment_hash=digest(ctx),
                trial_phase="INNER_VALIDATION",
                idempotent=True,
                execution_context=ctx,
                metadata={
                    "evaluation_identity": identity,
                    "status": status,
                    "candidate": candidate,
                    "metric_available": status == "COMPLETED",
                },
            )
            receipt = {
                "key": cell["key"],
                "scope": "INNER_VALIDATION",
                "status": status,
                "evaluation_id": record.evaluation_id,
                "candidate": candidate,
                "resources": resources,
            }
            write(out / "receipts" / f"{cell['key']}.json", receipt)
            keys.append(cell["key"])
            cpu_total += resources["cpu_seconds"]
            if status != "COMPLETED":
                raise RuntimeError(
                    f"invalid/failed measurement: {cell['key']}; not ABSTAIN"
                )
            decision = assess_validation_candidates(
                plan["selection_contract"], [candidate]
            )
            write(out / "decisions" / f"{cell['key']}.json", decision)
            if decision["decision"] == "SELECT":
                write(
                    out / "inner_selection_freezes" / f"{cell['key']}.json",
                    {
                        "protocol_fingerprint": fingerprint,
                        "key": cell["key"],
                        "selected": decision["selected"],
                        "validation_only": True,
                    },
                )
            print(
                f"{cell['key']}: {status}, {decision['decision']}, wall={resources['wall_seconds']:.2f}s cpu={resources['cpu_seconds']:.2f}s",
                flush=True,
            )
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        signal.signal(signal.SIGTERM, previous)
    write(
        out / "execution_summary.json",
        {
            "cell_keys": keys,
            "wall_seconds": time.monotonic() - started,
            "cpu_seconds": cpu_total,
            "error": error,
        },
    )
    write(out / "identity_after.json", capture_identity(plan["input"]["path"]))
    scoped = scoped_snapshot(registry_snapshot(plan["registry_path"]), fingerprint)
    write(out / "registry_snapshot.json", scoped)
    verdict = verify_calibration_packet(out, require_terminal=False)
    complete = error is None and verdict["status"] == "ACCEPTED_CALIBRATION"
    items = inventory(out)
    write(
        out / TERMINAL_NAME,
        {
            "protocol_version": "2.0",
            "protocol_fingerprint": fingerprint,
            "status": "COMPLETED" if complete else "INCOMPLETE",
            "acceptance_checks": verdict["checks"],
            "inventory": items,
            "evidence_hash": digest(items),
            "registry_digest": digest(scoped),
            "live_authorized": False,
            "qualified": False,
            "error": error,
        },
    )
    return verify_calibration_packet(out)


TERMINAL_NAME = "calibration_manifest_terminal.json"
