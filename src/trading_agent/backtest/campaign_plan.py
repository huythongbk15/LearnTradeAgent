"""Frozen campaign plan and the native schema-v2 bundle producer.

A plan is what a campaign commits to *before* it runs. Thresholds, fold
spans, data binding and cost schedule all live here, so nothing in the
decision can be chosen after the results are visible.

The producer turns a frozen plan plus measured fold rows into a schema-v2
bundle. It recomputes every aggregate, gate outcome and hash from the
measurements rather than accepting any of them from the caller. If a number
cannot be derived from what was measured, the producer refuses instead of
defaulting it.

Ordering matters: the plan must be frozen and hashed first, the campaign
runs against it, and only then may the producer build a bundle. A bundle
whose results do not match the plan it claims was frozen under is refused,
which is what stops a plan from being rewritten once the numbers arrive.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from trading_agent.backtest.campaign_evidence import (
    COMPARISONS,
    _time,
    NUMERIC_RESULTS,
    OPEN_RESULT_FIELDS,
    measured_row_total_pnl,
    content_hash,
    finite,
    hash_id,
    validate_v2,
)

SCHEMA_VERSION = 2
PLAN_VERSION = 1
# Rows a campaign must produce before a verdict means anything.
MIN_FOLDS_FOR_VERDICT = 2


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _iso(value: object, name: str) -> str:
    """Normalize a boundary to a canonical tz-aware UTC timestamp.

    Stored data frequently carries naive timestamps. Normalizing rather than
    rejecting means the bundle records what the validator will actually read,
    and two equivalent boundaries can never produce different plan hashes.
    """
    if not isinstance(value, (str, datetime)) or (
        isinstance(value, str) and not value.strip()
    ):
        raise ValueError(f"{name} must be a timestamp")
    try:
        return _time(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} is not a parseable timestamp: {value!r}") from exc


def _boundaries(value: object, name: str) -> list[str]:
    """Normalize a span to the two-boundary list the validator reads.

    Schema-v2 spans are ``[start, end]`` ISO strings. Accepting the two forms
    a caller might reasonably type keeps the plan ergonomic while guaranteeing
    what lands in the bundle is the shape validation will re-read.
    """
    if isinstance(value, Mapping):
        start, end = value.get("start"), value.get("end")
    elif isinstance(value, list) and len(value) == 2:
        start, end = value
    else:
        raise ValueError(f"{name} must be a [start, end] span or start/end mapping")
    start_iso, end_iso = _iso(start, f"{name}.start"), _iso(end, f"{name}.end")
    if _time(start_iso) >= _time(end_iso):
        raise ValueError(f"{name} boundaries must be increasing")
    return [start_iso, end_iso]


def freeze_campaign_plan(
    *,
    subject: Mapping,
    folds: Sequence[Mapping],
    data_manifest: Mapping,
    cost_schedule: Mapping,
    gate_set: Sequence[Mapping],
    param_grid: Mapping | None = None,
    frozen_by: str,
    frozen_at: datetime | None = None,
    native_gate_contract: dict | None = None,
    selection_contract: dict | None = None,
    validation_cost_scenarios: list[dict] | None = None,
    execution_source: dict | None = None,
    execution_context: dict | None = None,
) -> dict:
    """Build a frozen plan and bind it to its own content hash.

    ``plan_fingerprint`` is the hash of the plan without the fingerprint
    field, so any later edit to a span, threshold or data binding changes it
    and the campaign can no longer claim it ran under this plan.
    """
    _require(
        isinstance(subject, Mapping)
        and all(
            isinstance(subject.get(key), str) and subject[key].strip()
            for key in ("strategy_id", "symbol", "market_type", "timeframe")
        ),
        "plan subject requires strategy_id/symbol/market_type/timeframe",
    )
    _require(bool(frozen_by), "plan requires the operator who froze it")
    _require(bool(folds), "plan requires at least one fold")
    _require(bool(gate_set), "plan requires a non-empty gate_set")

    fold_records = []
    seen = set()
    for fold in folds:
        _require(isinstance(fold, Mapping), "each fold must be a mapping")
        fold_id = fold.get("fold_id")
        _require(
            isinstance(fold_id, str) and fold_id and fold_id not in seen,
            "each fold requires a unique nonempty fold_id",
        )
        seen.add(fold_id)
        record = {
            "fold_id": fold_id,
            "train_span": _boundaries(fold["train_span"], "train_span"),
            "val_span": _boundaries(fold["val_span"], "val_span"),
            "test_span": _boundaries(fold["test_span"], "test_span"),
            "purge_bars": _require_nonneg_int(fold.get("purge_bars"), "purge_bars"),
            "embargo_bars": _require_nonneg_int(
                fold.get("embargo_bars"), "embargo_bars"
            ),
        }
        if fold.get("overlap_group"):
            record["overlap_group"] = fold["overlap_group"]
        fold_records.append(record)

    for key in ("commission_bps", "slippage_bps", "spread_bps", "round_trip_bps"):
        _require(finite(cost_schedule.get(key)), f"cost_schedule.{key} must be finite")
    _require(
        all(cost_schedule[key] >= 0 for key in cost_schedule),
        "cost_schedule values must be nonnegative",
    )
    _require(
        math.isclose(
            cost_schedule["round_trip_bps"],
            2 * cost_schedule["commission_bps"]
            + 2 * cost_schedule["slippage_bps"]
            + cost_schedule["spread_bps"],
            abs_tol=1e-6,
        ),
        "cost_schedule.round_trip_bps does not recompute from the legs",
    )

    gates = []
    gate_ids = set()
    for gate in gate_set:
        _require(isinstance(gate, Mapping), "each gate must be a mapping")
        gate_id = gate.get("gate_id")
        _require(
            isinstance(gate_id, str) and gate_id and gate_id not in gate_ids,
            "each gate requires a unique nonempty gate_id",
        )
        gate_ids.add(gate_id)
        metric = gate.get("metric")
        _require(
            isinstance(metric, str) and metric.startswith("aggregate."),
            f"gate {gate_id} must measure a recorded aggregate field",
        )
        _require(
            gate.get("comparison") in COMPARISONS,
            f"gate {gate_id} has an unsupported comparator",
        )
        _require(
            finite(gate.get("threshold")), f"gate {gate_id} threshold must be finite"
        )
        _require(bool(gate.get("unit")), f"gate {gate_id} requires a unit")
        gates.append(
            {
                "gate_id": gate_id,
                "metric": metric,
                "comparison": gate["comparison"],
                "threshold": float(gate["threshold"]),
                "unit": gate["unit"],
            }
        )

    minimum_fold_gate = {
        "gate_id": "campaign_minimum_folds",
        "metric": "aggregate.fold_count",
        "comparison": ">=",
        "threshold": float(MIN_FOLDS_FOR_VERDICT),
        "unit": "folds",
    }
    _require("campaign_minimum_folds" not in gate_ids, "reserved minimum-fold gate id")
    gates.append(minimum_fold_gate)
    policies = {fold.get("overlap_policy", "independent") for fold in folds}
    _require(
        len(policies) == 1 and policies <= {"independent", "overlapping"},
        "fold overlap policy must consistently be independent or overlapping",
    )
    plan = {
        "plan_version": PLAN_VERSION,
        "gate_version": "campaign-aggregate-v1",
        "subject": dict(subject),
        "param_grid": dict(param_grid or {}),
        "folds": fold_records,
        "fold_count": len(fold_records),
        "overlap_policy": policies.pop(),
        "data_manifest": dict(data_manifest),
        "cost_schedule": dict(cost_schedule),
        "gate_set": gates,
        "gate_set_fingerprint": content_hash(gates),
        "frozen_by": frozen_by,
        "frozen_at": (frozen_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    if execution_source is not None:
        plan["execution_source"] = execution_source
    if execution_context is not None:
        plan["execution_context"] = json.loads(json.dumps(execution_context, allow_nan=False))
    if selection_contract is not None:
        from trading_agent.backtest.selection_eligibility import (
            verify_selection_contract,
        )

        verify_selection_contract(selection_contract)
        plan["selection_contract"] = json.loads(json.dumps(selection_contract))
        if not validation_cost_scenarios:
            raise ValueError("selection requires frozen validation cost scenarios")
        plan["validation_cost_scenarios"] = json.loads(json.dumps(validation_cost_scenarios))
        from trading_agent.strategies.canonical.candidates import campaign_history_contract
        plan["history_contract"] = campaign_history_contract(
            subject["strategy_id"], plan["param_grid"]
        )
    if native_gate_contract is not None:
        from trading_agent.backtest.native_gate_contract import verify_native_contract

        verify_native_contract(native_gate_contract)
        plan["native_gate_contract"] = native_gate_contract
    # Semantic fingerprint excludes metadata that changes on each freeze
    # (frozen_at, plan_fingerprint) but includes all semantic fields.
    # This is used for reuse identity matching across packets.
    plan["semantic_fingerprint"] = semantic_fingerprint(plan)
    # plan_fingerprint MUST be computed LAST, after all fields including semantic_fingerprint are set
    plan["plan_fingerprint"] = content_hash(plan)
    return plan


def semantic_fingerprint(plan: Mapping) -> str:
    """Compute semantic identity for reuse matching.

    Excludes metadata that changes on each freeze (frozen_at, plan_fingerprint)
    but includes all semantic fields that determine execution equivalence:
    subject, folds, data_manifest, cost_schedule, gate_set, param_grid,
    selection_contract, native_gate_contract, validation_cost_scenarios,
    history_contract, execution_source, overlap_policy.
    """
    # Derived identity must never hash itself. Source/dirty state is deliberately
    # retained: same HEAD is not sufficient to reuse a measurement.
    excluded = {"plan_fingerprint", "semantic_fingerprint", "frozen_at", "frozen_by"}
    body = {k: v for k, v in plan.items() if k not in excluded}
    return content_hash(body)


def _require_nonneg_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative int")
    return value


def verify_frozen_plan(plan: Mapping) -> None:
    """Refuse a plan whose fingerprint does not bind its own content."""
    _require(isinstance(plan, Mapping), "plan must be a mapping")
    declared = plan.get("plan_fingerprint")
    _require(hash_id(declared), "plan is missing a plan_fingerprint")
    body = {key: value for key, value in plan.items() if key != "plan_fingerprint"}
    _require(
        content_hash(body) == declared,
        "plan_fingerprint does not bind the plan; it was edited after freezing",
    )
    if "semantic_fingerprint" in plan:
        _require(plan["semantic_fingerprint"] == semantic_fingerprint(plan),
                 "plan semantic fingerprint mismatch")


def build_source_binding(root: Path) -> dict:
    """Bind the bundle to the exact source tree that produced the numbers.

    A dirty tree is allowed but must be identified, because a bundle claiming
    a commit while running on uncommitted edits would make the commit a lie.
    """
    root = Path(root).resolve()

    def _git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    diff = _git("status", "--porcelain")
    dirty = bool(diff)
    dirty_sha = content_hash(diff) if dirty else None
    return {
        "code_commit": commit,
        "tree_fingerprint": tree,
        "worktree_dirty": dirty,
        "dirty_diff_sha256": dirty_sha,
    }


def plan_folds_from_spec(
    frame,
    *,
    timeframe: str,
    train_months: int,
    val_months: int,
    test_months: int,
    step_months: int,
    purge: int,
    embargo: int,
) -> list[dict]:
    """Derive the fold structure from the same builder the campaign will use.

    Recomputing the spans independently would let the plan and the run drift
    apart silently. This calls the campaign's own fold generator, so the
    frozen plan describes the folds that will actually execute, and turns
    bar indices into the timestamps the bundle records.
    """
    from trading_agent.backtest.nested_wfo import _get_fold_indices

    folds = _get_fold_indices(
        frame.height,
        timeframe,
        train_months,
        val_months,
        test_months,
        step_months,
        purge,
        embargo,
    )
    timestamps = frame["timestamp"].to_list()

    def span(start_index: int, end_index: int, name: str) -> list[str]:
        """Half-open [start, end) bar indices to the inclusive timestamps.

        The fold generator's end is exclusive and can equal the bar count, so
        reading it as an index would run off the end of the series. The last
        bar inside the fold is end - 1.
        """
        if end_index <= start_index or end_index > len(timestamps):
            raise ValueError(
                f"{name} [{start_index}, {end_index}) does not lie inside the "
                f"{len(timestamps)}-bar series"
            )
        return [
            _time(timestamps[start_index]).isoformat(),
            _time(timestamps[end_index - 1]).isoformat(),
        ]

    return [
        {
            "fold_id": fold.fold_id,
            "train_span": span(
                fold.inner_train_start, fold.inner_train_end, "train_span"
            ),
            "val_span": span(fold.inner_val_start, fold.inner_val_end, "val_span"),
            "test_span": span(fold.outer_test_start, fold.outer_test_end, "test_span"),
            "purge_bars": fold.purge,
            "embargo_bars": fold.embargo,
        }
        for fold in folds
    ]


def build_data_manifest(
    path: Path,
    *,
    data_root: Path,
    subject: Mapping,
    window: Mapping,
    cutoff_policy: str = "fixed",
    gaps: Sequence[Mapping] | None = None,
) -> dict:
    """Derive the data binding from the actual parquet, not from the caller.

    A plan that trusted a declared row count or hash would bind to whatever
    the caller claimed. This reads the file, hashes the bytes, resolves the
    window against real timestamps and hashes the resolved content, so the
    manifest in the bundle describes the data that was actually used.
    """
    import hashlib

    import polars as pl

    from trading_agent.backtest.campaign_evidence import read_ohlcv, window_hash

    data_root = Path(data_root).resolve()
    resolved = Path(path)
    resolved = (resolved if resolved.is_absolute() else data_root / resolved).resolve()
    if not resolved.is_relative_to(data_root):
        raise ValueError("input path escapes the declared data root")
    if cutoff_policy not in {"fixed", "as_of", "trailing"}:
        raise ValueError(f"unsupported cutoff_policy {cutoff_policy!r}")

    frame = read_ohlcv(resolved)
    start, end = (
        _time(_iso(window.get("start"), "window.start")),
        _time(_iso(window.get("end"), "window.end")),
    )
    if start > end:
        raise ValueError("window boundaries reversed")
    selected = frame.filter(
        (pl.col("timestamp") >= start) & (pl.col("timestamp") <= end)
    )
    if selected.height == 0:
        raise ValueError("window resolves to zero bars")
    if window.get("timeframe") != subject.get("timeframe") or window.get(
        "market"
    ) != subject.get("market_type"):
        raise ValueError("window subject mismatch")
    return {
        "input_path": str(resolved.relative_to(data_root)),
        "file_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "input_rows": frame.height,
        "window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bars": selected.height,
            "timeframe": window["timeframe"],
            "market": window["market"],
        },
        "cutoff_policy": cutoff_policy,
        "gaps": list(gaps or []),
        "data_manifest": window_hash(selected),
    }


def build_campaign_bundle(
    *,
    plan: Mapping,
    rows: Sequence[Mapping],
    source: Mapping,
    data_root: Path,
    subject: Mapping | None = None,
    native_evidence: list | None = None,
) -> dict:
    """Produce a schema-v2 bundle from a frozen plan and measured rows.

    Everything derived is derived here: the aggregate is recomputed from the
    rows, gate outcomes are evaluated against the frozen thresholds, and the
    bundle id binds the result. The caller supplies measurements and a source
    binding and nothing else.
    """
    verify_frozen_plan(plan)

    declared_subject = plan["subject"]
    effective_subject = dict(subject or declared_subject)
    for key in ("strategy_id", "symbol", "market_type", "timeframe"):
        _require(
            effective_subject.get(key) == declared_subject.get(key),
            f"subject.{key} differs from the frozen plan",
        )

    _require(bool(rows), "no measured fold rows")
    frozen_folds = {fold["fold_id"]: fold for fold in plan["folds"]}
    measured_ids = [row.get("fold_id") for row in rows]
    _require(
        len(set(measured_ids)) == len(measured_ids),
        "duplicate measured fold_id",
    )
    _require(
        set(measured_ids) == set(frozen_folds),
        "measured folds do not match the frozen plan",
    )

    results: list[dict[str, Any]] = []
    for row in rows:
        _require(isinstance(row, Mapping), "each measured row must be a mapping")
        fold_id = row["fold_id"]
        record: dict[str, Any] = {"fold_id": fold_id}
        if row.get("report_sha256"):
            record["report_sha256"] = row["report_sha256"]
        for key in NUMERIC_RESULTS:
            _require(finite(row.get(key)), f"fold {fold_id} is missing finite {key}")
            record[key] = float(row[key])
        _require(
            type(row.get("trades")) is int and row["trades"] >= 0,
            f"fold {fold_id} requires a nonnegative int trade count",
        )
        record["trades"] = row["trades"]
        # Derived, not copied. A caller that cleared a losing fold by
        # passing cost_cleared=True would otherwise publish it as sound.
        for key in OPEN_RESULT_FIELDS:
            if key in row:
                record[key] = row[key]
        record["cost_cleared"] = measured_row_total_pnl(record) > 0
        results.append(record)

    aggregate = {
        "fold_count": len(results),
        "median_return_pct": median(r["return_pct"] for r in results),
        "median_sharpe": median(r["sharpe"] for r in results),
        "total_trades": sum(r["trades"] for r in results),
    }
    for key, value in aggregate.items():
        _require(finite(value), f"aggregate.{key} did not recompute to a finite number")

    # Verdict gates the fold count as well as the thresholds. One fold has no
    # dispersion, so a median computed from it would pass a median-sharpe
    # gate for reasons that have nothing to do with the strategy.
    failed: list[dict] = []
    for gate in plan["gate_set"]:
        metric = gate["metric"].removeprefix("aggregate.")
        observed = aggregate.get(metric)
        _require(
            finite(observed), f"gate {gate['gate_id']} has no measured aggregate field"
        )
        if not COMPARISONS[gate["comparison"]](observed, gate["threshold"]):
            failed.append(
                {
                    "gate_id": gate["gate_id"],
                    "observed": observed,
                    "unit": gate["unit"],
                    "threshold": gate["threshold"],
                    "comparison": gate["comparison"],
                }
            )

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "gate_version": plan["gate_version"],
        "trades_absent_reason": "Per-trade records are retained in source reports bound by results.report_sha256; bundle contains fold summaries only.",
        "subject": effective_subject,
        "code_commit": source["code_commit"],
        "tree_fingerprint": source["tree_fingerprint"],
        "worktree_dirty": source["worktree_dirty"],
        "dirty_diff_sha256": source["dirty_diff_sha256"],
        "data_manifest": dict(plan["data_manifest"]),
        "cost_schedule": dict(plan["cost_schedule"]),
        "folds": [dict(fold) for fold in plan["folds"]],
        "fold_count": len(plan["folds"]),
        "overlap_policy": plan["overlap_policy"],
        "results": results,
        "aggregate": aggregate,
        "gate_set": [dict(gate) for gate in plan["gate_set"]],
        "gate_set_fingerprint": plan["gate_set_fingerprint"],
        "failed_gates": failed,
        "verdict": "FAIL" if failed else "PASS",
        "plan_fingerprint": plan["plan_fingerprint"],
    }
    if plan.get("selection_contract") is not None:
        bundle["selection_contract"] = plan["selection_contract"]
    if plan.get("native_gate_contract") is not None:
        from trading_agent.backtest.native_gate_contract import validate_native_evidence

        native_failed = validate_native_evidence(
            plan["native_gate_contract"],
            native_evidence,
            [row["trades"] for row in results],
        )
        bundle["native_gate_contract"] = plan["native_gate_contract"]
        bundle["native_gate_evidence"] = native_evidence
        bundle["failed_gates"] += native_failed
        bundle["verdict"] = "FAIL" if bundle["failed_gates"] else "PASS"
    elif native_evidence is not None:
        raise ValueError("native evidence requires a pre-run frozen contract")
    bundle["campaign_id"] = content_hash(bundle)
    return bundle


def verified_bundle(
    *,
    plan: Mapping,
    rows: Sequence[Mapping],
    source: Mapping,
    data_root: Path,
    subject: Mapping | None = None,
    native_evidence: list | None = None,
) -> dict:
    """Produce and self-validate in one step, so an invalid bundle never escapes.

    ``data_root`` is not used to build anything; it is passed to validation so
    the produced bundle is checked against the same data root a publisher will
    use. A producer that skipped this would emit a bundle whose data binding
    only fails later, at publication.
    """
    bundle = build_campaign_bundle(
        plan=plan,
        rows=rows,
        source=source,
        data_root=data_root,
        subject=subject,
        native_evidence=native_evidence,
    )
    errors = validate_v2(
        bundle,
        data_root=Path(data_root),
        expected_tree_fingerprint=source["tree_fingerprint"],
        expected_gate_set_fingerprint=plan["gate_set_fingerprint"],
    )
    _require(
        not errors,
        "produced bundle failed its own schema-v2 validation: " + "; ".join(errors[:4]),
    )
    return bundle
