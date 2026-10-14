"""Calibration v2 contract. Read-only checks, shared by producer and reviewer.

An accepted packet proves validation execution, not qualification or live consent.
Legacy packets are deliberately not upgraded. Seals detect drift, not authorship.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

TERMINAL = "calibration_manifest_terminal.json"
TABLES = ("experiment_specs", "experiment_aliases", "evaluation_records")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def contained(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise ValueError("noncanonical packet path")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("packet path escapes root")
    if any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
        raise ValueError("symlinks are not packet evidence")
    return target


def inventory(root):
    """All files, including backups; only terminal and SQLite sidecars excluded.

    SQLite contents are covered separately by a transactional logical snapshot.
    Sidecar bytes are not stable across WAL checkpoints.
    """
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are not packet evidence")
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in (
            TERMINAL,
            "trials.sqlite3",
            "trials.sqlite3-wal",
            "trials.sqlite3-shm",
        ):
            continue
        raw = path.read_bytes()
        result.append(
            {"path": rel, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    return result


def registry_snapshot(path):
    """Read committed WAL data; never initialize/migrate/checkpoint a registry."""
    path = Path(path).resolve(strict=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        result = {}
        for table in TABLES:
            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for row in rows:
                if "metadata_json" in row:
                    row["metadata_json"] = json.loads(row["metadata_json"])
            result[table] = sorted(rows, key=canonical)
        return result


def scoped_snapshot(snapshot, fingerprint):
    def carries_scope(metadata):
        if not isinstance(metadata, dict):
            return False
        marker = metadata.get("evaluation_execution_identity")
        identity = metadata.get("evaluation_identity")
        context = (
            identity.get("execution_context") if isinstance(identity, dict) else None
        )
        return any(
            isinstance(value, dict) and value.get("protocol_fingerprint") == fingerprint
            for value in (marker, context)
        )

    rows = [
        r for r in snapshot["evaluation_records"] if carries_scope(r["metadata_json"])
    ]
    ids = {r["experiment_id"] for r in rows}
    return {
        "evaluation_records": rows,
        "experiment_specs": [
            r for r in snapshot["experiment_specs"] if r["experiment_id"] in ids
        ],
        "experiment_aliases": [
            r for r in snapshot["experiment_aliases"] if r["experiment_id"] in ids
        ],
    }


def preserved(before, after):
    return all(
        {canonical(r) for r in before[t]} <= {canonical(r) for r in after[t]}
        for t in TABLES
    )


def finite_positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_approval(approval, fingerprint, *, not_after):
    """Shared scope/time check. Content hash is not proof of human authorship."""
    fields = {
        key: approval[key]
        for key in ("approved_by", "approved_at", "protocol_fingerprint")
    }
    if (
        not isinstance(fields["approved_by"], str)
        or not fields["approved_by"].strip()
        or not isinstance(fields["approved_at"], str)
        or not fields["approved_at"].strip()
        or fields["protocol_fingerprint"] != fingerprint
        or digest(fields) != approval["approval_sha256"]
    ):
        raise ValueError("approval invalid or outside frozen scope")
    timestamp = datetime.fromisoformat(fields["approved_at"].replace("Z", "+00:00"))
    ceiling = (
        datetime.fromisoformat(not_after.replace("Z", "+00:00"))
        if isinstance(not_after, str)
        else not_after
    )
    if (
        timestamp.tzinfo is None
        or ceiling.tzinfo is None
        or timestamp.astimezone(UTC) > ceiling.astimezone(UTC)
    ):
        raise ValueError("approval must be timezone-aware and precede execution")
    return True


def validate_plan(plan, root):
    """Producer preflight and reviewer share the same frozen-plan semantics."""
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract
    from trading_agent.research.trials import (
        ExperimentSpec,
        param_hash,
        search_space_hash,
    )
    from trading_agent.strategies.canonical.candidates import (
        build_default_registry,
        validate_params,
    )

    root = Path(root).resolve()
    if plan.get("worker_authority_contract") != "owned_pipe_intent_v1":
        raise ValueError(
            "unsupported worker authority contract; refreeze, do not reseal"
        )
    if plan["scope"] != "INNER_VALIDATION" or plan["cost"] != {
        "name": "1x",
        "fee_multiplier": 1.0,
        "slippage_multiplier": 1.0,
    }:
        raise ValueError("scope/cost contract mismatch")
    if plan["budget"] != default_budget():
        raise ValueError("budget differs from frozen calibration contract")
    if plan["selection_contract"] != freeze_selection_contract(
        min_validation_trades=30, min_validation_sharpe=0.0
    ):
        raise ValueError("selection thresholds changed")
    identity = plan["identity_before"]
    context = plan["execution_context"]
    if (
        plan["source_before"] != identity["source"]
        or context["source"] != identity["source"]
        or context["environment"] != identity["environment"]
        or context["runtime_config"]
        != identity["runtime_config"] | {"calibration_budget": plan["budget"]}
        or plan["input"]["sha256"] != identity["input_sha256"]
    ):
        raise ValueError("source/input/environment/runtime binding mismatch")
    source = identity["source"]
    if not all(
        isinstance(source.get(k), str)
        and len(source[k]) == n
        and all(ch in "0123456789abcdef" for ch in source[k])
        for k, n in (("code_commit", 40), ("tree_fingerprint", 64))
    ):
        raise ValueError("invalid captured source fingerprint")
    if plan["evidence_class"] == "REAL_MARKET":
        strategies = {"rsi", "bbands", "enhanced_ma", "ma_adx", "ma_vol_target"}
        expected = {f"{s}_cal_{w}" for s in strategies for w in ("early", "late")}
        operational = (
            Path(__file__).resolve().parents[3]
            / "data/wfo/experiments_parallel.sqlite3"
        )
        if (
            Path(plan["registry_path"]).resolve() != operational.resolve()
            or plan["timeframe"] != "4h"
        ):
            raise ValueError("market history/timeframe mismatch")
        boundary = plan["boundary"]
        registered = operational.parents[1] / "research_manifest.json"
        if (
            hashlib.sha256(registered.read_bytes()).hexdigest()
            != boundary["research_manifest_sha256"]
        ):
            raise ValueError("research manifest drift")
        from trading_agent.alpha_research.holdout import holdout_window

        start, _ = holdout_window(read(registered))
        from .execution import utc

        if utc(plan["holdout_start_utc"]) != utc(start):
            raise ValueError("holdout exclusion changed")
    elif plan["evidence_class"] == "SYNTHETIC_TEST_ONLY":
        expected = {"rsi_cal_early", "rsi_cal_late"}
        if (
            Path(plan["registry_path"]).resolve() != root / "trials.sqlite3"
            or plan["timeframe"] != "1h"
        ):
            raise ValueError("synthetic registry must be isolated")
    else:
        raise ValueError("unknown evidence class")
    cells = plan["cells"]
    if (
        len(cells) != len(expected)
        or {c["key"] for c in cells} != expected
        or plan["symbol"] != "BTC/USDT"
    ):
        raise ValueError("exact strategy/window matrix mismatch")
    # Metadata-only reads establish real bar bounds, including missing timestamps.
    # Never read price columns, initialize a data loader, or open a holdout ledger.
    import polars as pl
    from .execution import utc

    times = [
        utc(t)
        for t in pl.read_parquet(plan["input"]["path"], columns=["timestamp"])[
            "timestamp"
        ].to_list()
    ]
    if (
        len(times) != plan["input"]["rows"]
        or not times
        or any(a >= b for a, b in zip(times, times[1:]))
        or times[-1] >= utc(plan["holdout_start_utc"])
    ):
        raise ValueError("input timestamps violate frozen holdout exclusion")
    for cell in cells:
        if cell["key"] != f"{cell['strategy_id']}_{cell['fold_id']}" or cell[
            "fold_id"
        ] not in ("cal_early", "cal_late"):
            raise ValueError("cell/fold identity mismatch")
        params = validate_params(cell["strategy_id"], None)
        descriptor = build_default_registry().describe(cell["strategy_id"])
        if cell["params"] != params:
            raise ValueError("preset is not the canonical default")
        grid = calibration_grid(
            cell["strategy_id"],
            synthetic=plan["evidence_class"] == "SYNTHETIC_TEST_ONLY",
        )
        if cell["identity_grid"] != grid:
            raise ValueError("search space identity changed")
        spec = ExperimentSpec.build(
            strategy_name=cell["strategy_id"],
            strategy_code_sha=descriptor.code_sha,
            data_manifest_sha=plan["input"]["data_manifest_sha"],
            feature_schema_hash=digest(sorted(descriptor.required_features)),
            params_hash=param_hash(params | {"cost_scenario": "1x"}),
            search_family="s3_wfo_parallel_canonical",
            search_space_hash=search_space_hash(grid),
            target_horizon=f"{plan['timeframe']}_wfo",
            evaluator_version="v1",
            seed=42,
        )
        if any(
            getattr(spec, k) != value
            for k, value in cell["experiment"].items()
            if k != "created_at"
        ):
            raise ValueError("canonical experiment identity mismatch")
        start, end = cell["measurement_window"]
        sim_start, sim_end = cell["simulation_window"]
        if (
            any(type(v) is not int for v in (start, end, sim_start, sim_end))
            or not 0 <= sim_start <= start < end <= plan["input"]["rows"]
            or sim_end != end
        ):
            raise ValueError("invalid measurement/simulation window")
        if (
            times[start].isoformat() != cell["start_utc"]
            or times[end - 1].isoformat() != cell["end_utc"]
        ):
            raise ValueError("bar index does not match actual timestamp")
        if plan["evidence_class"] == "REAL_MARKET":
            import bisect

            dates = (
                ("2024-01-01", "2024-07-01")
                if cell["fold_id"] == "cal_early"
                else ("2025-07-01", "2026-01-01")
            )
            if [start, end] != [bisect.bisect_left(times, utc(d)) for d in dates]:
                raise ValueError(
                    "validation dates differ from fixed calibration windows"
                )
    return True


def verify_calibration_packet(root, *, require_terminal=True):
    """Fail closed. Malformed/unavailable evidence returns errors, not a traceback.

    This function verifies calibration packets (protocol_version "2.0"),
    not WFO qualification packets (schema_version 2).
    """
    root = Path(root).resolve()
    checks, errors = {}, []

    def check(name, action):
        try:
            if action() is not True:
                raise ValueError("condition not satisfied")
            checks[name] = True
        except Exception as exc:
            checks[name] = False
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    try:
        manifest = read(root / "calibration_manifest.json")
        if manifest.get("protocol_version") != "2.0":
            raise ValueError(
                "unsupported legacy contract; expected protocol_version 2.0"
            )
        # Calibration manifest has plan fields directly, not nested under "plan"
        fingerprint = manifest["protocol_fingerprint"]
        cells = manifest["cells"]
        source = manifest["source_before"]
        context = manifest["execution_context"] | {"protocol_fingerprint": manifest["protocol_fingerprint"]}
        budget = manifest["budget"]
        terminal = read(root / TERMINAL) if (root / TERMINAL).exists() else None
        results = [read(contained(root, f"receipts/{c['key']}.json")) for c in manifest["cells"]]
        registry_path = Path(manifest["registry_path"])
        snapshot = registry_snapshot(registry_path)
        scoped = scoped_snapshot(snapshot, manifest["protocol_fingerprint"])
    except Exception as exc:
        return {
            "status": "REJECTED",
            "checks": {"packet_readable": False},
            "errors": [f"{type(exc).__name__}: {exc}"],
            "live_authorized": False,
            "qualified": False,
        }

    def protocol():
        validate_plan(manifest, root)
        from trading_agent.backtest.selection_eligibility import (
            freeze_selection_contract,
        )

        fingerprint = manifest["protocol_fingerprint"]
        cells = manifest["cells"]
        plan_fields = {k: v for k, v in manifest.items() if k not in ("protocol_version", "run_id", "protocol_fingerprint", "plan")}
        if (
            digest(plan_fields) != fingerprint
            or not manifest["cells"]
            or len({c["key"] for c in manifest["cells"]}) != len(manifest["cells"])
        ):
            return False
        if manifest["scope"] != "INNER_VALIDATION" or manifest["cost"] != {
            "name": "1x",
            "fee_multiplier": 1.0,
            "slippage_multiplier": 1.0,
        }:
            return False
        if manifest["selection_contract"] != freeze_selection_contract(
            min_validation_trades=30, min_validation_sharpe=0.0
        ):
            return False
        if manifest["evidence_class"] == "REAL_MARKET":
            expected = (
                Path(__file__).resolve().parents[3]
                / "data/wfo/experiments_parallel.sqlite3"
            )
            if Path(manifest["registry_path"]).resolve() != expected.resolve() or len(manifest["cells"]) != 10:
                return False
            approval = read(root / "operator_approval.json")
            intents = [
                row
                for row in scoped["evaluation_records"]
                if row["metric_name"] == "inner_validation_attempt"
            ]
            validate_approval(
                approval,
                fingerprint,
                not_after=min(row["created_at"] for row in intents),
            )
        elif manifest["evidence_class"] != "SYNTHETIC_TEST_ONLY":
            return False
        source = manifest["source_before"]
        context = manifest["execution_context"] | {"protocol_fingerprint": manifest["protocol_fingerprint"]}
        if context["source"] != source or not all(
            source.get(k) for k in ("code_commit", "tree_fingerprint")
        ):
            return False
        for c in manifest["cells"]:
            start, end = c["measurement_window"]
            sim_start, sim_end = c["simulation_window"]
            if not (
                0 <= sim_start <= start < end <= manifest["input"]["rows"]
                and sim_end == end
            ):
                return False
            if c["end_utc"] > manifest["holdout_start_utc"]:
                return False
        return True

    check("protocol_binding", protocol)
    check(
        "C1_exact_completed_coverage",
        lambda: (
            all(
                r["key"] == c["key"] and r["status"] == "COMPLETED"
                for c, r in zip(cells, results)
            )
            and sorted(p.stem for p in (root / "receipts").glob("*.json"))
            == sorted(c["key"] for c in cells)
        ),
    )
    check(
        "C2_validation_scope",
        lambda: (
            all(r["scope"] == "INNER_VALIDATION" for r in results)
            and all(
                r["trial_phase"]
                == (
                    "UNKNOWN"
                    if r["metric_name"] == "inner_validation_attempt"
                    else "INNER_VALIDATION"
                )
                for r in scoped["evaluation_records"]
            )
        ),
    )

    def accounting():
        from trading_agent.research.trials import (
            evaluation_scope_from_metadata,
            execution_scope_identity,
        )

        expected_marker = execution_scope_identity(context)
        rows = scoped["evaluation_records"]
        if len(rows) != 2 * len(cells):
            return False
        for row in rows:
            meta = row["metadata_json"]
            evaluation_scope_from_metadata(meta)
            if (
                meta["evaluation_execution_identity"] != expected_marker
                or meta["evaluation_identity"]["execution_context"] != context
            ):
                return False
        for cell, receipt in zip(cells, results):
            intent = read(root / f"attempts/{cell['key']}.json")
            starts = [r for r in rows if r["evaluation_id"] == intent["evaluation_id"]]
            if (
                len(starts) != 1
                or starts[0]["metric_name"] != "inner_validation_attempt"
                or starts[0]["metric_value"] != 0.0
                or starts[0]["metadata_json"].get("status") != "STARTED"
                or starts[0]["metadata_json"].get("bookkeeping_only") is not True
                or starts[0]["experiment_id"] != cell["experiment"]["experiment_id"]
                or starts[0]["fold_id"] != cell["fold_id"]
            ):
                return False
            matching = [
                r for r in rows if r["evaluation_id"] == receipt["evaluation_id"]
            ]
            if len(matching) != 1:
                return False
            row = matching[0]
            meta = row["metadata_json"]
            evaluation_scope_from_metadata(meta)
            if (
                meta["evaluation_execution_identity"] != expected_marker
                or meta["evaluation_identity"]["execution_context"] != context
            ):
                return False
            if (
                row["experiment_id"] != cell["experiment"]["experiment_id"]
                or row["fold_id"] != cell["fold_id"]
            ):
                return False
            if (
                row["metric_name"] != "inner_validation_sharpe"
                or row["metric_value"] != receipt["candidate"]["val_metrics"]["sharpe"]
            ):
                return False
            if (
                meta["status"] != "COMPLETED"
                or meta["candidate"] != receipt["candidate"]
            ):
                return False
            spec = next(
                s
                for s in scoped["experiment_specs"]
                if s["experiment_id"] == row["experiment_id"]
            )
            if any(
                spec[k] != v for k, v in cell["experiment"].items() if k != "created_at"
            ):
                return False
        return True

    check("C3_all_versioned_trials", accounting)

    def decisions():
        from trading_agent.backtest.selection_eligibility import (
            assess_validation_candidates,
        )

        selected = []
        for cell, receipt in zip(cells, results):
            decision = assess_validation_candidates(
                manifest["selection_contract"], [receipt["candidate"]]
            )
            if read(root / f"decisions/{cell['key']}.json") != decision:
                return False
            if decision["decision"] == "SELECT":
                selected.append(cell["key"])
                freeze = read(root / f"inner_selection_freezes/{cell['key']}.json")
                if freeze != {
                    "protocol_fingerprint": fingerprint,
                    "key": cell["key"],
                    "selected": decision["selected"],
                    "validation_only": True,
                }:
                    return False
        return sorted(p.stem for p in (root / "decisions").glob("*.json")) == sorted(
            c["key"] for c in cells
        ) and sorted(
            p.stem for p in (root / "inner_selection_freezes").glob("*.json")
        ) == sorted(selected)

    check("C4_canonical_decisions", decisions)

    def reports():
        from trading_agent.backtest.campaign_execution import (
            CampaignCellRunner,
            _window_identity,
        )
        from trading_agent.backtest.selection_eligibility import (
            verify_validation_reports,
        )
        from trading_agent.backtest.tournament import CostScenario

        for cell, receipt in zip(cells, results):
            candidate = receipt["candidate"]
            if (
                candidate["params"] != cell["params"]
                or candidate["cost_scenario"] != "1x"
            ):
                return False
            contained(root, candidate["validation_source"]["report_path"])
            spec = SimpleNamespace(
                strategy_id=cell["strategy_id"],
                symbol=manifest["symbol"],
                timeframe=manifest["timeframe"],
                cost_scenarios=[CostScenario(**manifest["cost"])],
            )
            verify_validation_reports(
                manifest["selection_contract"],
                [candidate],
                root=root,
                validation_window=cell["measurement_window"],
                expected_spec=spec,
                expected_commit=source["code_commit"],
                expected_simulation_window=cell["simulation_window"],
            )
            from trading_agent.backtest.tournament import (
                EvaluationArtifact,
                EvaluationCellSpec,
            )

            typed = EvaluationArtifact.from_dict(
                candidate["validation_source"]["artifact"]
            )
            request = EvaluationCellSpec(
                cell["strategy_id"],
                manifest["symbol"],
                manifest["timeframe"],
                cell["params"],
                CostScenario(**manifest["cost"]),
            )
            raw = contained(
                root, candidate["validation_source"]["report_path"]
            ).read_bytes()
            CampaignCellRunner._validate_artifact(
                SimpleNamespace(
                    context={
                        "source": source,
                        "runtime_config": manifest["identity_before"]["runtime_config"],
                    }
                ),
                request,
                typed,
                raw,
                _window_identity(
                    {
                        "start": cell["simulation_window"][0],
                        "end": cell["simulation_window"][1],
                        "measurement_start": cell["measurement_window"][0],
                        "measurement_end": cell["measurement_window"][1],
                    }
                ),
            )
            from trading_agent.strategies.canonical.candidates import (
                build_default_registry,
            )

            if (
                build_default_registry().describe(cell["strategy_id"]).code_sha
                != cell["experiment"]["strategy_code_sha"]
            ):
                return False
        return True

    check("C5_attributed_raw_reports", reports)

    def resources():
        fields = (
            "cell_wall_seconds",
            "cell_cpu_seconds",
            "total_wall_seconds",
            "total_cpu_seconds",
            "rss_limit_bytes",
        )
        if (
            not all(finite_positive(budget[k]) for k in fields)
            or budget["workers"] != 1
            or budget["retries"] != 0
        ):
            return False
        if manifest["evidence_class"] == "REAL_MARKET" and budget != default_budget():
            return False
        total_cpu = 0
        pids = set()
        for receipt in results:
            r = receipt["resources"]
            worker_result = read(root / f"worker_results/{receipt['key']}.json")
            if (
                type(r.get("supervisor_pid")) is not int
                or r["supervisor_pid"] <= 0
                or worker_result.get("worker_authority")
                != {
                    "parent_pid": r["supervisor_pid"],
                    "out": str(root),
                    "key": receipt["key"],
                    "protocol_fingerprint": fingerprint,
                    "intent_evaluation_id": read(
                        root / f"attempts/{receipt['key']}.json"
                    )["evaluation_id"],
                }
            ):
                return False
            if (
                worker_result["status"] != "COMPLETED"
                or worker_result["candidate"] != receipt["candidate"]
                or worker_result["identity_at_start"] != manifest["identity_before"]
            ):
                return False
            if (
                type(r["worker_pid"]) is not int
                or r["worker_pid"] in pids
                or r["worker_pid"] != worker_result["worker_pid"]
            ):
                return False
            pids.add(r["worker_pid"])
            if r["cpu_seconds"] != max(
                r["sampled_cpu_seconds"], worker_result["cpu_seconds"]
            ) or r["rss_peak_bytes"] != max(
                r["sampled_rss_peak_bytes"], worker_result["rss_peak_bytes"]
            ):
                return False
            if not all(
                finite_positive(r[k])
                for k in ("wall_seconds", "cpu_seconds", "rss_peak_bytes")
            ):
                return False
            if (
                r["wall_seconds"] > budget["cell_wall_seconds"]
                or r["cpu_seconds"] > budget["cell_cpu_seconds"]
                or r["rss_peak_bytes"] > budget["rss_limit_bytes"]
            ):
                return False
            if (
                r["enforcement"] != "owned_process_group_watchdog"
                or r["active_descendants"]
                or r["returncode"] != 0
                or r["limit_exceeded"]
            ):
                return False
            total_cpu += r["cpu_seconds"]
        summary = read(root / "execution_summary.json")
        return (
            finite_positive(summary["wall_seconds"])
            and summary["wall_seconds"] <= budget["total_wall_seconds"]
            and summary["cpu_seconds"] == total_cpu
            and total_cpu <= budget["total_cpu_seconds"]
        )

    check("C6_enforced_resources", resources)

    check(
        "C7_no_oos_holdout_promotion",
        lambda: (
            not any(
                p.is_file()
                and any(
                    p.name.startswith(prefix)
                    for prefix in ("holdout_", "promotion_", "policy_", "outer_oos")
                )
                for p in root.rglob("*")
            )
        ),
    )
    check(
        "C8_audit_complete",
        lambda: (
            read(root / "execution_summary.json")["cell_keys"]
            == [c["key"] for c in cells]
        ),
    )
    check(
        "C9_logical_registry_seal",
        lambda: (
            scoped == read(root / "registry_snapshot.json")
            and preserved(read(root / "registry_before.json"), snapshot)
        ),
    )
    check(
        "C10_source_input_config_stable",
        lambda: (
            read(root / "identity_after.json") == manifest["identity_before"]
            and hashlib.sha256(Path(manifest["input"]["path"]).read_bytes()).hexdigest()
            == manifest["input"]["sha256"]
        ),
    )
    if require_terminal:
        check(
            "terminal_integrity_seal",
            lambda: (
                terminal is not None
                and terminal.get("protocol_version") == "2.0"
                and terminal["protocol_fingerprint"] == fingerprint
                and terminal["inventory"] == inventory(root)
                and terminal["evidence_hash"] == digest(inventory(root))
                and terminal["registry_digest"] == digest(scoped)
                and terminal["status"] == "COMPLETED"
                and terminal.get("qualified") is False
                and terminal.get("live_authorized") is False
                and terminal.get("error") is None
                and terminal["acceptance_checks"]
                == {k: v for k, v in checks.items() if k != "terminal_integrity_seal"}
            ),
        )
    return {
        "status": "ACCEPTED_CALIBRATION"
        if checks and all(checks.values())
        else "REJECTED",
        "checks": checks,
        "errors": errors,
        "live_authorized": False,
        "qualified": False,
    }


def verify_packet(root, *, require_terminal=True):
    """Verify a calibration or qualification packet.

    Detects the protocol version from the manifest and delegates to the appropriate verifier.
    """
    root = Path(root).resolve()
    manifest = read(root / "calibration_manifest.json")

    # Detect protocol version
    if manifest.get("protocol_version") == "2.0":
        # Calibration protocol v2.0
        return verify_calibration_packet(root, require_terminal=require_terminal)

    # Legacy or unsupported version — reject, do not upgrade
    return {
        "status": "REJECTED",
        "checks": {"unsupported_version": False},
        "errors": [
            f"Unsupported protocol version: {manifest.get('protocol_version') or manifest.get('schema_version')}. "
            "This verifier only supports calibration protocol v2.0. "
            "Use verify_calibration_packet directly for calibration packets."
        ],
        "live_authorized": False,
        "qualified": False,
    }


def default_budget():
    return {
        "cell_wall_seconds": 180,
        "cell_cpu_seconds": 120,
        "total_wall_seconds": 1800,
        "total_cpu_seconds": 1200,
        "rss_limit_bytes": 2_147_483_648,
        "workers": 1,
        "retries": 0,
    }


def calibration_grid(strategy, *, synthetic):
    """Keep market search-family/space history; calibration is a measured subset.

    The synthetic singleton belongs only to its isolated test registry.
    """
    if synthetic:
        from trading_agent.strategies.canonical.candidates import validate_params

        return {k: [v] for k, v in validate_params(strategy, None).items()}
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from run_wfo_parallel import get_param_grid_for_strategy

    grid = get_param_grid_for_strategy(strategy)
    if not grid:
        raise ValueError("canonical research grid missing")
    return grid
