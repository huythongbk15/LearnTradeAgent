"""Bounded inner-cell dispatch, measured timing and source-bound resume cache.

Outer selection remains serial in WFO. Cache is an execution optimization,
never a qualification certificate; normal evidence gates still run afterwards.
"""

import hashlib
import json
import math
import os
import platform
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from importlib.metadata import version, PackageNotFoundError
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_integrity import capture_campaign_source
from trading_agent.backtest.tournament import EvaluationArtifact, run_cell
from trading_agent.backtest.preregistration_binding import (
    load_approved_payload,
    verify_registered_payload_binding,
)
from trading_agent.strategies.canonical.candidates import SCOPED_CANDIDATE_SCOPE_TOKENS
from trading_agent.research.btc_context_prereg import PLUS_BUDGET

ROOT = Path(__file__).resolve().parents[3]
CACHE_VERSION = 2


def execution_environment():
    """Non-secret runtime identity; no credentials or account environment captured."""
    packages = {}
    for name in ("numpy", "scipy", "polars"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    # Explicit allowlist: these non-secret simulator settings otherwise change
    # results without a source change. Never snapshot the whole environment.
    settings = {name: os.environ.get(name, default) for name, default in {
        "INITIAL_CAPITAL": "10000", "MAX_DRAWDOWN_PCT": "0.15",
        "DAILY_LOSS_LIMIT_PCT": "0.08", "MAX_POSITION_PCT": "0.50",
        "STOP_LOSS_PCT": "0.05", "COOLDOWN_HOURS": "24", "MAX_POS_SIZE_PCT": "0.25",
        "FAST_MA": "15", "SLOW_MA": "50", "ADX_THRESHOLD": "40",
        "ATR_SL_MULT": "2.0", "ATR_TP_MULT": "3.0", "USE_LLM": "false",
        "EXCHANGE": "binance",
    }.items()}
    from trading_agent.config.loader import Config
    path = Path(os.environ.get("TRADING_CONFIG_PATH") or Config.default_path()).resolve()
    # Hash only: never serialize config contents, credentials or account settings.
    config_binding = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return {"python": platform.python_version(), "packages": packages,
            "simulator_settings": settings, "config_binding": config_binding}


def simulator_runtime_config():
    """Freeze actual loaded simulator constants, not merely env declarations."""
    from scripts import full_system_backtest as simulator
    return {
        "initial_capital": simulator.INITIAL_CAPITAL,
        "protection": {"stop_loss": {"enabled": True, "distance_pct": simulator.STOP_LOSS_PCT * 100},
                       "take_profit": {"enabled": False}, "trailing_stop": {"enabled": False}},
        "sizing": {"method": "artifact_target_authority_capped",
                   "requested_target_exposure_pct": simulator.MAX_POS_SIZE_PCT * 100,
                   "max_target_exposure_pct": simulator.MAX_POS_SIZE_PCT * 100,
                   "max_position_pct": simulator.MAX_POSITION_PCT * 100},
        "risk": {"max_drawdown_pct": simulator.MAX_DRAWDOWN_PCT * 100,
                 "daily_loss_limit_pct": simulator.DAILY_LOSS_LIMIT_PCT * 100,
                 "cooldown_hours": simulator.COOLDOWN_HOURS},
        "intrabar_price_path": "open_then_low_then_close",
    }


def _durable_json(path, payload):
    """Publish a complete exclusive record; never overwrite existing evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.pending"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic no-replace publication, same filesystem.
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)  # Only this invocation's owned temp file.


def _window_identity(kwargs):
    # Explicit defaults make omission and explicit None semantically equivalent.
    values = dict(start=0, end=None, tail_bars=None, fresh=True, simulation_start=None,
                  measurement_start=None, measurement_end=None, signal_delay_bars=0,
                  gap_policy="record", gap_exceptions_path=None, timeout_seconds=300,
                  max_retries=2, resource_budget=None, _use_multiprocessing=True)
    values.update({key: value for key, value in kwargs.items() if key != "out_root"})
    if values["gap_exceptions_path"] is not None:
        path = Path(values["gap_exceptions_path"]).resolve()
        values["gap_exceptions_path"] = str(path)
        values["gap_exceptions_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(json.dumps(values, allow_nan=False))


@contextmanager
def _cell_lock(path):
    """OS advisory lock, released on owner exit; persistent file is not evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class CampaignCellRunner:
    def __init__(self, out_root, context, *, workers=1, resume=False, runner=None, reuse_from=None):
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ValueError("workers must be 1..4")
        self.root = Path(out_root).resolve()
        self.context = json.loads(json.dumps(context, allow_nan=False))
        self.workers = workers
        self.resume = resume
        self.runner = runner or run_cell
        self.lock = threading.Lock()
        self.completed = 0
        self.cache_hits = 0
        self.environment = execution_environment()
        self.reuse_root = Path(reuse_from).resolve() if reuse_from is not None else None
        if self.reuse_root is not None and (
            self.root.is_relative_to(self.reuse_root) or self.reuse_root.is_relative_to(self.root)
        ):
            raise ValueError("reuse requires disjoint source and destination packets")

    def _bound_context(self):
        source, data = self.context.get("source", {}), self.context.get("data", {})
        return bool(source.get("code_commit") and source.get("tree_fingerprint")
                    and data.get("path") and data.get("sha256") and self.context.get("plan"))

    def _verify_bindings(self, spec):
        binding = self.context.get("data")
        if self.context.get("require_frozen_input") and json.loads(os.environ.get("TRADING_FROZEN_RESEARCH_DATA", "null")) != binding:
            raise ValueError("active frozen loader binding differs from campaign input")
        if binding:
            if hashlib.sha256(Path(binding["path"]).read_bytes()).hexdigest() != binding["sha256"]:
                raise ValueError("campaign input changed during cached execution")
            if binding.get("symbol", spec.symbol) != spec.symbol or binding.get("timeframe", spec.timeframe) != spec.timeframe:
                raise ValueError("campaign input subject differs from cell")
        if self._bound_context():
            from trading_agent.backtest.campaign_integrity import capture_campaign_source
            if capture_campaign_source(ROOT) != self.context["source"]:
                raise ValueError("campaign source changed during cached execution")
            if execution_environment() != self.environment:
                raise ValueError("campaign environment changed during cached execution")
            if self.context.get("runtime_config") is not None and simulator_runtime_config() != self.context["runtime_config"]:
                raise ValueError("campaign simulator config changed during cached execution")

    def _validate_artifact(self, spec, artifact, raw, window):
        from trading_agent.backtest.tournament import _artifact_from_report
        from trading_agent.backtest.reporting import fingerprint_payload
        from trading_agent.strategies.canonical.candidates import build_default_registry
        if window["tail_bars"] is not None:
            raise ValueError("tail-relative windows are not resume-certifiable")
        sim_start = window["simulation_start"] if window["simulation_start"] is not None else window["start"]
        measurement = (window["measurement_start"], window["measurement_end"])
        measurement = None if measurement == (None, None) else measurement
        if (artifact.status != "COMPLETED" or artifact.cell_id != spec.cell_id
                or artifact.strategy_id != spec.strategy_id or artifact.symbol != spec.symbol
                or artifact.timeframe != spec.timeframe or artifact.params_hash != spec.params_hash
                or artifact.cost_scenario != spec.cost_scenario.name or artifact.fault_profile != spec.fault.name
                or artifact.commission != spec.cost_scenario.commission or artifact.slippage != spec.cost_scenario.slippage
                or artifact.measurement_window != measurement or artifact.simulation_window != (sim_start, window["end"])
                or artifact.signal_delay_bars != window["signal_delay_bars"]
                or artifact.commit_sha != self.context["source"]["code_commit"]):
            raise ValueError("cached artifact identity/window/source mismatch")
        report = json.loads(raw)
        if "native_execution_contracts" in self.context:
            from trading_agent.backtest.execution_contract import require_frozen_native_evidence

            require_frozen_native_evidence(
                self.context["native_execution_contracts"], spec.cell_id, report, artifact)
        config = report.get("active_config", {})
        runtime = self.context.get("runtime_config")
        if runtime is not None:
            if any(config.get(key) != value for key, value in runtime.items() if key != "initial_capital"):
                raise ValueError("cached simulator risk/sizing/protection differs from frozen config")
            if measurement and measurement[0] > sim_start:
                # run_cell rebases measured capital after warm-up/train.
                # It is NOT the simulator's starting capital in this case.
                opening = report.get("initial_capital")
                if (not isinstance(opening, (int, float)) or isinstance(opening, bool)
                        or not math.isfinite(opening) or opening < 0
                        or opening != report.get("measurement_evidence", {}).get("opening_equity")):
                    raise ValueError("cached measurement opening equity differs from measured capital")
            elif report.get("initial_capital") != runtime["initial_capital"]:
                raise ValueError("cached simulator starting capital differs from frozen config")
        strategy, cost = config.get("strategy", {}), config.get("cost_model", {})
        if (config.get("config_id") != fingerprint_payload({key: value for key, value in config.items() if key != "config_id"})
                or strategy.get("strategy_id") != spec.strategy_id
                or any(strategy.get("parameters", {}).get(key) != value for key, value in spec.params.items())
                or cost.get("commission_rate") != spec.cost_scenario.commission
                or cost.get("slippage_rate") != spec.cost_scenario.slippage
                or config.get("execution_timing") != "decision_on_closed_bar_execute_next_bar_open"
                or config.get("data_policy", {}).get("gap_policy") != window["gap_policy"]):
            raise ValueError("cached effective config differs from requested cell")
        if (report.get("symbol") != spec.symbol or report.get("timeframe") != spec.timeframe
                or report.get("commit_sha") != artifact.commit_sha
                or report.get("data_manifest_id") != artifact.data_manifest_sha
                or report.get("measurement_window") != (list(measurement) if measurement else None)
                or not isinstance(report.get("trades"), list)
                or not isinstance(report.get("equity_curve"), list) or not report["equity_curve"]):
            raise ValueError("cached raw report identity/window/ledger missing or mismatched")
        rebuilt = _artifact_from_report(
            spec,
            build_default_registry(
                scope_token=self.context.get("scope_token")
            ).describe(spec.strategy_id),
                                        Path(artifact.report_path).parent, report, artifact.data_manifest_sha,
                                        measurement_window=measurement, simulation_window=artifact.simulation_window,
                                        signal_delay_bars=artifact.signal_delay_bars)
        if (rebuilt.status != "COMPLETED" or rebuilt.metrics != artifact.metrics
                or rebuilt.execution_health != artifact.execution_health
                or rebuilt.descriptor_id != artifact.descriptor_id):
            raise ValueError("cached raw report differs from artifact")

    def _load_cache(self, path, packet, identity, spec, window):
        # Confinement BEFORE reads, including symlinks.
        if not path.resolve().is_relative_to(packet):
            raise ValueError("cache path escapes source packet")
        record = json.loads(path.read_bytes())
        if record.get("version") != CACHE_VERSION or record.get("identity") != identity:
            raise ValueError("legacy or mismatched cache identity; use fresh output")
        payload = record["artifact"]
        artifact = EvaluationArtifact.from_dict(payload)
        report_path = Path(artifact.report_path).resolve()
        if not report_path.is_relative_to(packet):
            raise ValueError("cached report escapes source packet")
        raw = report_path.read_bytes()
        if (record["artifact_hash"] != content_hash(payload)
                or record["report_sha256"] != hashlib.sha256(raw).hexdigest()):
            raise ValueError("cached report or artifact tampered; use fresh output")
        self._validate_artifact(spec, artifact, raw, window)
        return artifact, raw

    def _publish_cache(self, path, identity, artifact, raw, lineage=None):
        _durable_json(path, {"version": CACHE_VERSION, "identity": identity,
                            "artifact": artifact.to_dict(), "artifact_hash": content_hash(artifact.to_dict()),
                            "report_sha256": hashlib.sha256(raw).hexdigest(), "lineage": lineage,
                            "scope": "execution_reuse_not_qualification", "live_authorized": False})

    def __call__(self, spec, **kwargs):
        for name in ("cell_locks", "cell_resume_cache", "cell_attempts", "reused_reports",
                     "cell_failures", "cell_timings.jsonl"):
            if not (self.root / name).resolve().is_relative_to(self.root):
                raise ValueError("execution output namespace escapes destination packet")
        lock_kwargs = dict(kwargs)
        stage = lock_kwargs.pop("_cache_stage", "CELL")
        key = content_hash(self._identity(spec, _window_identity(lock_kwargs), stage))
        with _cell_lock(self.root / "cell_locks" / f"{key}.lock"):
            return self._execute_cell(spec, **kwargs)

    def _identity(self, spec, window, stage):
        return dict(version=CACHE_VERSION, environment=self.environment,
                    stage=stage, context=self.context, cell_id=spec.cell_id,
                    params=dict(spec.params), cost=spec.cost_scenario.to_dict(),
                    fault=asdict(spec.fault), window=window)

    def _execute_cell(self, spec, **kwargs):
        started = time.perf_counter()
        stage = kwargs.pop("_cache_stage", "CELL")
        if Path(kwargs.get("out_root", self.root)).resolve() != self.root:
            raise ValueError("cell output root differs from runner root")
        window = _window_identity(kwargs)
        identity = self._identity(spec, window, stage)
        key = content_hash(identity)
        cache_dir = self.root / "cell_resume_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{key}.json"
        cache_hit = False
        cache_reason = "FRESH_EXECUTION"
        artifact = None
        error = None
        try:
            self._verify_bindings(spec)
            # Preregistration binding gate for scoped candidates (PLUS): refuse
            # before any worker is spawned or market data is read.
            scope_token = self.context.get("scope_token")
            if scope_token and spec.strategy_id in set(SCOPED_CANDIDATE_SCOPE_TOKENS.values()):
                source = self.context.get("source") or {}
                captured = capture_campaign_source(ROOT)
                source_fingerprint = captured["tree_fingerprint"]
                if not source_fingerprint:
                    raise ValueError("scoped candidate run requires source.tree_fingerprint")
                payload, _manifest = load_approved_payload(scope_token, ROOT)
                approved_contract = payload.get("execution_contract") or {}
                approved_bodies = dict(approved_contract.get("native_contract_bodies_by_cost") or {})
                # Build one execution contract per approved cost, keyed by cell id.
                contracts = {}
                inner_cells = []
                for idx, (cost_name, body) in enumerate(approved_bodies.items()):
                    cell_id = f"c{idx + 1}"
                    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    contracts[cell_id] = {
                        "contract_id": hashlib.sha256(encoded.encode()).hexdigest(),
                        "body": body,
                    }
                    inner_cells.append({"cell_id": cell_id, "cost": {"name": cost_name}})
                protocol = {
                    "spec": {
                        "strategy_id": spec.strategy_id,
                        "symbol": spec.symbol,
                        "timeframe": spec.timeframe,
                        "param_grid": {k: [v] for k, v in dict(spec.params).items()},
                        "cost_scenarios": [
                            {
                                "name": name,
                                "fee_multiplier": value["fee_multiplier"],
                                "slippage_multiplier": value["slippage_multiplier"],
                            }
                            for name, value in payload["costs"].items()
                        ],
                        "evidence_class": "SYNTHETIC_TEST_ONLY",
                    },
                    "execution": {
                        "cell_timeout": PLUS_BUDGET["cell_timeout_seconds"],
                        "runtime_config": dict(approved_contract.get("runtime_config") or {}),
                        "max_retries": payload["budget"]["max_retries"],
                        "scope_token": scope_token,
                    },
                    "validation_budget": {
                        "total_timeout_seconds": PLUS_BUDGET["run_total_seconds"],
                        "max_rss_mb": payload["budget"]["max_rss_mb"],
                    },
                    "folds": [
                        {"inner_val_start": w[0], "inner_val_end": w[1]}
                        for w in payload["geometry"]["inner_validation_windows"]
                    ],
                    "input_binding": {"sha256": payload["data"]["sha256"]},
                    "native_execution_contracts": contracts,
                    "expected_inner_cells": inner_cells,
                }
                failed = verify_registered_payload_binding(
                    protocol, scope_token, root=ROOT, source_fingerprint=source_fingerprint
                )
                if failed:
                    raise ValueError(f"preregistration binding failed: {', '.join(failed)}")
            if (self.resume or self.reuse_root is not None) and not self._bound_context():
                raise ValueError("resume requires bound source/data/plan")
            if cache_path.exists():
                if not self.resume:
                    raise ValueError("existing cache requires resume or a fresh output packet")
                artifact, raw = self._load_cache(cache_path, self.root, identity, spec, window)
                cache_hit = True
                cache_reason = "SAME_PACKET_HIT"
            elif self.reuse_root is not None:
                cache_reason = "NO_MATCHING_SOURCE_CACHE_KEY"
                source_cache = self.reuse_root / "cell_resume_cache" / f"{key}.json"
                if source_cache.exists():
                    original, raw = self._load_cache(source_cache, self.reuse_root, identity, spec, window)
                    # An interrupted copy is retained, but cannot block a new
                    # verified import attempt or be mistaken for a complete cache.
                    directory = self.root / "reused_reports" / key / uuid.uuid4().hex
                    directory.mkdir(parents=True, exist_ok=False)
                    destination = directory / "report.json"
                    with destination.open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    artifact = replace(original, report_path=str(destination))
                    self._publish_cache(cache_path, identity, artifact, raw,
                                        {"packet": str(self.reuse_root), "report_sha256": hashlib.sha256(raw).hexdigest()})
                    cache_hit = True
                    cache_reason = "VERIFIED_COLD_IMPORT"
            if artifact is None:
                attempt_root = self.root / "cell_attempts" / uuid.uuid4().hex
                _durable_json(attempt_root / "execution_started.json", {
                    "identity": identity,
                    "started_at_epoch": time.time(),
                                                                       "live_authorized": False})
                runner_kwargs = {
                    **kwargs,
                    "out_root": attempt_root,
                    "scope_token": self.context.get("scope_token"),
                }
                if scope_token and spec.strategy_id in set(SCOPED_CANDIDATE_SCOPE_TOKENS.values()):
                    runner_kwargs["timeout_seconds"] = PLUS_BUDGET["cell_timeout_seconds"]
                artifact = self.runner(spec, **runner_kwargs)
                self._verify_bindings(spec)
                if artifact.status == "COMPLETED" and artifact.report_path and self._bound_context():
                    report_path = Path(artifact.report_path).resolve()
                    if not report_path.is_relative_to(self.root):
                        raise ValueError("completed report escapes destination packet")
                    raw = report_path.read_bytes()
                    self._validate_artifact(spec, artifact, raw, window)
                    self._publish_cache(cache_path, identity, artifact, raw)
            self._verify_bindings(spec)
            return artifact
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            raise  # Never rerun/overwrite an invalid cache or old report.
        finally:
            elapsed = time.perf_counter() - started
            with self.lock:
                self.completed += 1
                self.cache_hits += int(cache_hit and error is None)
                with (self.root / "cell_timings.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        json.dumps(
                            dict(
                                key=key,
                                cell_id=spec.cell_id,
                                window=window,
                                elapsed_sec=elapsed,
                                cache_hit=cache_hit,
                                cache_reason=cache_reason,
                                status="ERROR" if error else getattr(artifact, "status", "ERROR"),
                                error=error,
                                strategy_id=spec.strategy_id,
                                params=dict(spec.params),
                                execution_control=dict(getattr(artifact, "execution_control", {}) or {}),
                                workers=self.workers,
                            )
                        )
                        + "\n"
                    )
                # Evaluation-level ledger row: ONE row per EVALUATION (the
                # cache key), merged across the attempt history of that
                # evaluation. Several attempt records under one evaluation are
                # the history of that evaluation and are never counted as
                # separate evaluations.
                try:
                    from trading_agent.backtest.attempt_ledger import (
                        evaluation_attempt_dirs,
                        record_ledger_error,
                        write_ledger_row,
                    )

                    ledger_dir = self.root / "cell_ledger"
                    attempt_dirs = evaluation_attempt_dirs(self.root, key)
                    write_ledger_row(
                        ledger_dir,
                        key,
                        {
                            "identity": identity,
                            "cell_id": spec.cell_id,
                            "strategy_id": spec.strategy_id,
                            "cost_scenario": spec.cost_scenario.name,
                        },
                        attempt_dirs,
                        extra={
                            "artifact_status": getattr(artifact, "status", None),
                            "error": error,
                            "elapsed_seconds": elapsed,
                            "cache_hit": cache_hit,
                            "execution_path": "cache_hit" if cache_hit else "fresh",
                        },
                    )
                except Exception as ledger_exc:  # noqa: BLE001
                    # A ledger gap must not vanish silently: it is recorded
                    # durably in an append-only error log.
                    try:
                        from trading_agent.backtest.attempt_ledger import (
                            record_ledger_error,
                        )

                        record_ledger_error(self.root / "cell_ledger", key, ledger_exc)
                    except Exception:
                        pass
                    print(
                        f"[campaign] cell={self.completed} LEDGER_WRITE_FAILED "
                        f"key={key} {type(ledger_exc).__name__}: {ledger_exc}",
                        flush=True,
                    )
                if error or (artifact is not None and artifact.status != "COMPLETED"):
                    failure_dir = self.root / "cell_failures"
                    failure_dir.mkdir(parents=True, exist_ok=True)
                    failure_path = failure_dir / f"{key}__{uuid.uuid4().hex}.json"
                    _durable_json(failure_path, {"identity": identity,
                                               "artifact": artifact.to_dict() if artifact else None,
                                               "error": error})
                print(
                    f"[campaign] cell={self.completed} status={'ERROR' if error else getattr(artifact, 'status', 'ERROR')} "
                    f"elapsed={elapsed:.2f}s cache_hit={cache_hit}",
                    flush=True,
                )

    def run_batch(self, cells):
        def execute(cell):
            spec, start, end, fresh, measurement_start, measurement_end = cell
            return self(
                spec,
                out_root=self.root,
                start=start,
                end=end,
                fresh=fresh,
                measurement_start=measurement_start,
                measurement_end=measurement_end,
                _cache_stage="INNER_VALIDATION",
            )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            # map preserves input ordering for deterministic selection/tie breaks.
            return list(pool.map(execute, cells))


def preflight_workload(spec, folds, *, max_inner_cells=None):
    if max_inner_cells is not None and (
        type(max_inner_cells) is not int or max_inner_cells < 1
    ):
        raise ValueError("max_inner_cells must be a positive integer")
    combinations = 1
    for key, values in spec.param_grid.items():
        if (
            not isinstance(values, list)
            or not values
            or len({content_hash(v) for v in values}) != len(values)
        ):
            raise ValueError(f"empty/duplicate candidate values for {key}")
        combinations *= len(values)
    inner = combinations * len(spec.cost_scenarios) * len(folds)
    if max_inner_cells is not None and inner > max_inner_cells:
        raise ValueError(f"planned inner cells {inner} exceed budget {max_inner_cells}")
    return dict(
        inner_cells=inner,
        outer_cells=len(folds),
        additional_work="sensitivity/holdout evaluations are additional; no fixed ETA until timing exists",
    )
