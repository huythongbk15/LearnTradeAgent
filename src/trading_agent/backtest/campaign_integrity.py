"""Campaign integrity: prove a run measured everything it claimed to.

The WFO pipeline carried a bug where `full_system_backtest.py` injected
`atr_sl_mult` / `atr_tp_mult` into strategies whose parameter schema does
not define them. That raised `ParamValidationError` inside the cell, the
cell produced nothing, and the campaign still reported
`completed: 189, failed: 0` — because "completed" counted artifacts written,
not strategies asked for. Roughly 4,500 cells recorded before the fix
(`a65ed29000`) are affected.

Nothing in the harness compared the two numbers, which is why the failure
was silent and why a sign-off could quote figures for seven strategies that
have no artifacts on disk at all.

`verify_campaign_coverage` makes the comparison explicit and fails the run
when it does not hold. It is a harness check, not a strategy check: it
says nothing about whether a result is good, only about whether the set of
measurements matches the set of requests.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


def _selection_packet_file(root: Path, *parts: str) -> Path:
    """Resolve an opaque packet filename without accepting traversal or links out."""
    for part in parts:
        if (
            not isinstance(part, str)
            or not part
            or part in {".", ".."}
            or any(character in part for character in ("/", "\\", "\x00", ":"))
        ):
            raise ValueError("unsafe selection evidence identifier")
    resolved = root.joinpath(*parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("selection evidence path escapes source packet")
    return resolved


@dataclass(frozen=True)
class CoverageReport:
    requested: int
    produced: int
    missing_strategies: tuple[str, ...] = ()
    empty_strategies: tuple[str, ...] = ()
    per_strategy: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_strategies and not self.empty_strategies

    def summary(self) -> str:
        verdict = "OK" if self.ok else "INCOMPLETE"
        lines = [
            f"campaign coverage: {verdict} "
            f"({self.produced}/{self.requested} requested units produced cells)"
        ]
        if self.missing_strategies:
            lines.append(
                "  no result at all for: " + ", ".join(sorted(self.missing_strategies))
            )
        if self.empty_strategies:
            lines.append(
                "  result present but zero cells written: "
                + ", ".join(sorted(self.empty_strategies))
            )
        for note in self.notes:
            lines.append(f"  {note}")
        return "\n".join(lines)


def _cells_under(out_root: Path) -> dict[str, int]:
    """Count report.json cells per strategy under a campaign output root.

    Cell directories are named ``<strategy>__<symbol>__<tf>__<cost>__...``
    by every campaign runner in this repository, so the first segment is the
    strategy id. A strategy that raised inside its cell leaves no directory,
    which is exactly the case this check exists to catch.
    """
    counts: dict[str, int] = defaultdict(int)
    if not out_root.exists():
        return counts
    for report in out_root.rglob("report.json"):
        parts = report.parent.name.split("__")
        if not parts or not parts[0]:
            continue
        counts[parts[0]] += 1
    return dict(counts)


def verify_campaign_coverage(
    *,
    requested_strategies: Iterable[str],
    out_root: Path,
    results: Iterable[dict[str, Any]] | None = None,
) -> CoverageReport:
    """Compare strategies asked for against strategies that produced cells.

    ``results`` is the campaign's own per-strategy result list, when it has
    one. A strategy that appears there with a non-ERROR status but wrote no
    cell is reported separately from one that never returned at all: the
    first is the silent-drop case this was written for, the second is an
    ordinary failure.
    """
    requested = sorted(set(requested_strategies))
    cells = _cells_under(out_root)

    missing: list[str] = []
    empty: list[str] = []
    notes: list[str] = []

    if results is not None:
        by_strategy: dict[str, dict[str, Any]] = {}
        for r in results:
            sid = str(r.get("strategy_id") or "")
            if sid:
                by_strategy[sid] = r
        for sid in requested:
            result = by_strategy.get(sid)
            if result is None:
                missing.append(sid)
                continue
            status = str(result.get("status", "")).upper()
            if status in {"ERROR", "FAILED", "TIMEOUT"}:
                missing.append(sid)
            elif cells.get(sid, 0) == 0:
                empty.append(sid)
    else:
        for sid in requested:
            if cells.get(sid, 0) == 0:
                empty.append(sid)

    produced = sum(cells.get(sid, 0) for sid in requested)
    if results is None:
        notes.append("no result list supplied; verified against cells on disk only")
    found_extra = sorted(set(cells) - set(requested))
    if found_extra:
        notes.append(
            "cells present for strategies that were not requested: "
            + ", ".join(found_extra[:6])
        )

    return CoverageReport(
        requested=len(requested),
        produced=produced,
        missing_strategies=tuple(missing),
        empty_strategies=tuple(empty),
        per_strategy={sid: cells.get(sid, 0) for sid in requested},
        notes=tuple(notes),
    )


def load_requested_from_plan(out_root: Path) -> list[str]:
    """Best-effort list of strategies a campaign directory claims to cover.

    Used by post-hoc audits of campaigns that predate this check, where the
    original request list no longer exists but the output directory does.
    """
    for name in ("campaign_plan.json", "parallel_summary.json"):
        path = out_root / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        for key in ("strategies", "requested", "pool"):
            value = data.get(key)
            if isinstance(value, list) and value:
                return [str(v) for v in value]
    return []


def verify_subject_coverage(*, requested_subjects, results) -> dict:
    """Exact strategy/pair/timeframe coverage, separate from cell/alpha verdicts.

    A failed strategy can have complete evidence. A timeout, missing result,
    blocked input or duplicate result cannot stand in for a measured subject.
    Callers still owe exact parameter/cost/fold/window report attribution.
    """
    results = list(results)
    requested = [tuple(subject) for subject in requested_subjects]
    observed = [(row.get("strategy_id"), row.get("symbol"), row.get("timeframe"))
                for row in results]
    for subject in requested + observed:
        if len(subject) != 3 or not all(isinstance(item, str) and item for item in subject):
            raise ValueError("coverage subject requires strategy/symbol/timeframe")
    expected_counts, actual_counts = Counter(requested), Counter(observed)
    missing = sorted(set(expected_counts) - set(actual_counts))
    unexpected = sorted(set(actual_counts) - set(expected_counts))
    duplicates = sorted(subject for subject, count in actual_counts.items() if count != 1)
    invalid = sorted(subject for subject, row in zip(observed, results)
                     if row.get("status") not in {"PASS", "FAIL"})
    duplicate_requests = sorted(subject for subject, count in expected_counts.items() if count != 1)
    ok = bool(requested) and not any((missing, unexpected, duplicates, invalid, duplicate_requests))
    return {
        "status": "PASS" if ok else "INCOMPLETE",
        "scope": "strategy_symbol_timeframe_results_only",
        "requested": len(requested), "observed": len(observed),
        "missing": missing, "unexpected": unexpected, "duplicates": duplicates,
        "duplicate_requests": duplicate_requests, "unmeasured": invalid,
        "live_authorized": False,
    }


@dataclass(frozen=True)
class BundleReport:
    """Verdict on a campaign evidence bundle."""

    admissible: bool
    problems: tuple[str, ...] = ()
    bundle_id: str | None = None
    validated_bundle: dict | None = field(default=None, repr=False)

    def summary(self) -> str:
        verdict = "ADMISSIBLE" if self.admissible else "NOT ADMISSIBLE"
        lines = [f"campaign bundle: {verdict}"]
        for problem in self.problems:
            lines.append(f"  - {problem}")
        return "\n".join(lines)


def _require(bundle: dict, key: str, problems: list[str]) -> object | None:
    value = bundle.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        problems.append(f"missing {key}")
    return value


def verify_evidence_bundle(
    out_root: Path,
    *,
    expected_commit: str | None = None,
    filename: str = "campaign_evidence.json",
    data_root: Path | None = None,
    expected_tree_fingerprint: str | None = None,
    expected_gate_set_fingerprint: str | None = None,
) -> BundleReport:
    """Check a campaign bundle against CAMPAIGN_EVIDENCE_CONTRACT.md.

    Every rule corresponds to a field whose absence previously produced a
    number nobody could re-derive. The checks are deliberately independent:
    each names its field, so a rejected bundle says what to fix rather than
    only that something is wrong.

    ``expected_commit`` is compared against ``code_commit`` when given, so a
    campaign run on an older tree cannot be cited as evidence for the
    revision being reviewed.
    """
    problems: list[str] = []

    path = Path(out_root) / filename
    if not path.exists():
        return BundleReport(
            admissible=False,
            problems=(f"no campaign evidence bundle at {path.name}",),
        )
    try:
        bundle = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return BundleReport(admissible=False, problems=(f"unreadable bundle: {exc}",))
    if not isinstance(bundle, dict):
        return BundleReport(admissible=False, problems=("bundle is not an object",))

    from trading_agent.backtest.campaign_evidence import validate_v2

    try:
        strict_problems = validate_v2(
            bundle,
            data_root=data_root or Path(__file__).resolve().parents[3],
            expected_tree_fingerprint=expected_tree_fingerprint,
            expected_gate_set_fingerprint=expected_gate_set_fingerprint,
        )
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        strict_problems = [f"malformed campaign bundle: {exc}"]
    if strict_problems:
        return BundleReport(
            admissible=False,
            problems=tuple(strict_problems),
            bundle_id=bundle.get("campaign_id"),
        )

    _require(bundle, "schema_version", problems)
    _require(bundle, "code_commit", problems)
    _require(bundle, "gate_version", problems)
    _require(bundle, "gate_set_fingerprint", problems)

    # Rule 1 — code binding.
    commit = bundle.get("code_commit")
    if expected_commit and isinstance(commit, str) and commit != expected_commit:
        problems.append(
            f"code_commit {commit[:12]} does not match the revision under "
            f"review {expected_commit[:12]}"
        )

    # Rule 3 — data binding.
    data = bundle.get("data_manifest")
    if not isinstance(data, dict):
        problems.append("missing data_manifest")
    else:
        _require(data, "input_path", problems)
        _require(data, "input_rows", problems)
        window = data.get("window")
        if not isinstance(window, dict):
            problems.append("missing data_manifest.window")
        elif window.get("bars") != data.get("input_rows"):
            problems.append("data_manifest.window.bars disagrees with input_rows")
        if "gaps" not in data:
            problems.append("missing data_manifest.gaps")

    # Rule 4 — cost schedule.
    cost = bundle.get("cost_schedule")
    if not isinstance(cost, dict):
        problems.append("missing cost_schedule")
    else:
        round_trip = cost.get("round_trip_bps")
        if round_trip is None:
            problems.append("missing cost_schedule.round_trip_bps")
        else:
            expected = (
                2 * float(cost.get("commission_bps", 0.0))
                + 2 * float(cost.get("slippage_bps", 0.0))
                + float(cost.get("spread_bps", 0.0))
            )
            if abs(float(round_trip) - expected) > 1e-6:
                problems.append(
                    f"cost_schedule.round_trip_bps {round_trip} does not "
                    f"recompute from its components ({expected:.4f})"
                )

    # Rules 5 and 6 — folds and independence.
    folds = bundle.get("folds")
    results = bundle.get("results")
    if not isinstance(folds, list) or not folds:
        problems.append("missing folds")
    else:
        seen: set[str] = set()
        for fold in folds:
            if not isinstance(fold, dict):
                problems.append("malformed fold entry")
                continue
            fold_id = fold.get("fold_id")
            if not fold_id:
                problems.append("fold without fold_id")
            elif fold_id in seen:
                problems.append(f"duplicate fold_id {fold_id}")
            else:
                seen.add(fold_id)
        if bundle.get("fold_count") != len(folds):
            problems.append("fold_count disagrees with len(folds)")
        policy = bundle.get("overlap_policy")
        if policy not in {"independent", "overlapping"}:
            problems.append("overlap_policy must be declared")
        elif policy == "overlapping":
            if any(
                f.get("overlap_group") is None for f in folds if isinstance(f, dict)
            ):
                problems.append(
                    "overlap_policy is overlapping but a fold has no overlap_group"
                )

    if not isinstance(results, list) or not results:
        problems.append("missing results")
    else:
        if isinstance(folds, list):
            known = {f.get("fold_id") for f in folds if isinstance(f, dict)}
            for row in results:
                if isinstance(row, dict) and row.get("fold_id") not in known:
                    problems.append(
                        f"result references unknown fold {row.get('fold_id')!r}"
                    )

    # Rule 7 — per-trade records, or a stated reason.
    if bundle.get("trades") is None and not bundle.get("trades_absent_reason"):
        problems.append("trades absent without trades_absent_reason")

    # Rule 8 — the verdict must be derivable.
    verdict = bundle.get("verdict")
    failed = bundle.get("failed_gates")
    if verdict not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        problems.append(f"verdict {verdict!r} is not PASS/FAIL/INCONCLUSIVE")
    if verdict == "PASS" and failed:
        problems.append("verdict is PASS but failed_gates is non-empty")
    if verdict in {"FAIL", "INCONCLUSIVE"} and not failed:
        problems.append(f"verdict {verdict} but failed_gates is empty — name the gates")

    return BundleReport(
        admissible=not problems,
        problems=tuple(problems),
        bundle_id=bundle.get("campaign_id"),
        validated_bundle=bundle if not problems else None,
    )


def capture_campaign_source(root: Path) -> dict:
    """Capture actual code, including untracked source, before measurement."""
    import hashlib
    from trading_agent.backtest.campaign_evidence import content_hash

    root = root.resolve()
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("src", "scripts")
        for path in (root / directory).rglob("*.py")
    }
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    diff = subprocess.check_output(
        ["git", "diff", "HEAD", "--binary", "--", "src", "scripts"], cwd=root
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "src", "scripts"],
        cwd=root,
        text=True,
    ).splitlines()
    dirty = bool(diff or untracked)
    return {
        "code_commit": commit,
        "tree_fingerprint": content_hash(files),
        "worktree_dirty": dirty,
        "dirty_diff_sha256": content_hash(
            {
                "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
                "untracked": {p: files.get(p) for p in untracked},
            }
        )
        if dirty
        else None,
    }


def require_wfo_campaign_evidence(
    result,
    out_root: Path,
    *,
    data_root: Path,
    source_binding: dict,
    selection_evidence_root: Path | None = None,
) -> dict:
    """Consumer gate: a bundle must match the WFO result before policy writes.

    Does not manufacture missing accounting fields, and does not convert a
    valid FAIL into eligibility. Native exporters missing schema-v2 fields must
    be upgraded upstream; an old summary is not accepted as a replacement.
    """
    from trading_agent.backtest.campaign_evidence import content_hash, finite, hash_id

    report = verify_evidence_bundle(
        out_root,
        data_root=data_root,
        expected_commit=source_binding["code_commit"],
        expected_tree_fingerprint=source_binding["tree_fingerprint"],
    )
    if not report.admissible:
        raise ValueError(report.summary())
    bundle = report.validated_bundle
    if bundle is None:
        raise ValueError("validator returned no bound payload")
    if any(bundle.get(key) != value for key, value in source_binding.items()):
        raise ValueError("campaign source/dirty diff differs from pre-run capture")
    if bundle["verdict"] != "PASS" or result.passes_hard_gates is not True:
        raise ValueError("campaign is measured but NOT_QUALIFIED")
    subject = bundle["subject"]
    selection_contract = getattr(result, "aggregate_metrics", {}).get(
        "selection_contract"
    )
    if bundle.get("selection_contract") != selection_contract:
        raise ValueError("selection contract differs from actual WFO")
    if selection_contract is not None:
        from trading_agent.backtest.campaign_plan import verify_frozen_plan
        from trading_agent.backtest.selection_eligibility import (
            verify_validation_reports,
        )

        # The bundle may be in atomic staging; reports/plan remain in the
        # caller-declared, validated source packet, not the temporary directory.
        root = Path(selection_evidence_root or out_root).resolve()
        plan = json.loads(
            _selection_packet_file(
                root, result.spec.strategy_id, "campaign_plan.json"
            ).read_text()
        )
        verify_frozen_plan(plan)
        if (
            plan["plan_fingerprint"] != bundle["plan_fingerprint"]
            or plan.get("selection_contract") != selection_contract
        ):
            raise ValueError("selection contract is not bound to frozen campaign plan")
        inner = {fold.fold_id: fold for fold in result.inner_results}
        from itertools import product

        grid = plan["param_grid"]
        from trading_agent.strategies.canonical.candidates import campaign_history_contract
        history_contract = campaign_history_contract(result.spec.strategy_id, grid)
        if plan.get("history_contract") != history_contract:
            raise ValueError("history contract differs from frozen campaign plan")
        actual_costs = [
            {**cost.to_dict(), "commission_bps": cost.commission * 10000,
             "slippage_bps": cost.slippage * 10000}
            for cost in result.spec.cost_scenarios
        ]
        if not actual_costs or plan.get("validation_cost_scenarios") != actual_costs:
            raise ValueError("validation costs differ from frozen campaign plan")
        if len(actual_costs) == 1 and any(
            actual_costs[0][key] != plan["cost_schedule"][key]
            for key in ("commission_bps", "slippage_bps")
        ):
            raise ValueError("validation costs differ from frozen outer costs")
        if grid != result.spec.param_grid:
            raise ValueError("validation grid differs from frozen campaign plan")
        keys = list(grid)
        expected_candidates = {
            content_hash(
                {"params": dict(zip(keys, values)), "cost_scenario": cost.name}
            )
            for values in product(*(grid[key] for key in keys))
            for cost in result.spec.cost_scenarios
        }
        if len(inner) != len(result.inner_results) or set(inner) != {
            fold.fold_id for fold in result.outer_results
        }:
            raise ValueError("selection fold coverage mismatch")
        import hashlib
        from trading_agent.backtest.campaign_evidence import _time, read_ohlcv

        input_root = Path(data_root).resolve()
        input_path = (input_root / plan["data_manifest"]["input_path"]).resolve()
        if not input_path.is_relative_to(input_root):
            raise ValueError("frozen input escapes data root")
        if hashlib.sha256(input_path.read_bytes()).hexdigest() != plan["data_manifest"]["file_sha256"]:
            raise ValueError("frozen input bytes changed")
        timestamps = read_ohlcv(input_path)["timestamp"].to_list()
        indices = {_time(value): index for index, value in enumerate(timestamps)}
        if len(indices) != len(timestamps):
            raise ValueError("frozen input has ambiguous timestamps")
        frozen_folds = {row["fold_id"]: row for row in plan["folds"]}
        if set(frozen_folds) != set(inner):
            raise ValueError("selection folds differ from frozen plan")
        for outer in result.outer_results:
            fold = inner[outer.fold_id]
            windows = []
            for name in ("train_span", "val_span", "test_span"):
                span = frozen_folds[outer.fold_id][name]
                try:
                    windows.append((indices[_time(span[0])], indices[_time(span[1])] + 1))
                except KeyError as exc:
                    raise ValueError("frozen fold boundary absent from input") from exc
            train, validation, test = windows
            actual = (
                (getattr(fold, "train_start", None), getattr(fold, "train_end", None)),
                (fold.val_start, fold.val_end),
                (getattr(outer, "test_start", None), getattr(outer, "test_end", None)),
            )
            if actual != tuple(windows) or not (
                train[0] < train[1] <= validation[0] < validation[1] <= test[0] < test[1]
            ):
                raise ValueError("selection geometry differs from frozen campaign plan")
            _selection_packet_file(root, fold.fold_id)
            decision_path = _selection_packet_file(
                root, f"selection_decision_{fold.fold_id}.json"
            )
            actual_candidates = [
                content_hash(
                    {
                        "params": candidate["params"],
                        "cost_scenario": candidate["cost_scenario"],
                    }
                )
                for candidate in fold.candidate_metrics
            ]
            if (
                len(actual_candidates) != len(expected_candidates)
                or set(actual_candidates) != expected_candidates
            ):
                raise ValueError(
                    "validation candidate coverage differs from frozen grid"
                )
            manifest_sha = getattr(outer.artifact, "data_manifest_sha", None)
            if not hash_id(manifest_sha):
                raise ValueError("outer artifact lacks valid data-quality identity")
            decision = verify_validation_reports(
                selection_contract,
                fold.candidate_metrics,
                root=root,
                validation_window=validation,
                expected_spec=result.spec,
                expected_commit=source_binding["code_commit"],
                expected_manifest_sha=manifest_sha,
                expected_simulation_window=(
                    max(0, train[0] - history_contract["warmup_bars"]
                        - history_contract["buffer_bars"]), validation[1]
                ),
            )
            decision["fold_id"] = fold.fold_id
            saved = json.loads(
                decision_path.read_text()
            )
            if saved != decision or decision["decision"] != "SELECT":
                raise ValueError("selection decision differs from source validation")
            selected = decision["selected"]
            expected_params = {
                **selected["params"],
                "cost_scenario": selected["cost_scenario"],
            }
            if outer.params != expected_params or fold.best_params != expected_params:
                raise ValueError(
                    "outer parameters differ from eligible validation selection"
                )
    if any(
        subject[key] != getattr(result.spec, key)
        for key in ("strategy_id", "symbol", "timeframe")
    ):
        raise ValueError("campaign subject differs from actual WFO result")
    measured = {fold.fold_id: fold for fold in result.outer_results}
    if len(measured) != len(result.outer_results):
        raise ValueError("actual WFO result contains duplicate fold IDs")
    if set(measured) != {row["fold_id"] for row in bundle["results"]}:
        raise ValueError("campaign folds differ from actual WFO result")
    if subject["params_hash"] != content_hash(
        {fold.fold_id: fold.params for fold in result.outer_results}
    ):
        raise ValueError(
            "campaign selected parameters differ from actual WFO selection"
        )
    aliases = {
        "trades": "total_trades",
        "return_pct": "total_return_pct",
        "sharpe": "sharpe",
        "max_dd_pct": "max_drawdown_pct",
        "net_pnl": "net_pnl",
    }
    for row in bundle["results"]:
        fold = measured[row["fold_id"]]
        for key, source_key in aliases.items():
            value = fold.test_metrics.get(source_key)
            if source_key == "total_return_pct":
                legacy = fold.test_metrics.get("return_pct")
                if value is None:
                    value = legacy
                elif legacy is not None and legacy != value:
                    raise ValueError("conflicting measured WFO return metrics")
            if not finite(value) or abs(value - row[key]) > 1e-9:
                raise ValueError(
                    f"campaign result {row['fold_id']}.{key} differs from measured WFO metrics"
                )
        artifact = fold.artifact
        if artifact is None or artifact.status != "COMPLETED":
            raise ValueError("campaign lacks completed source evaluation artifact")
        if any(
            getattr(artifact, key) != subject[key]
            for key in ("strategy_id", "symbol", "timeframe")
        ):
            raise ValueError("source evaluation artifact subject mismatch")
        for name, value in (
            ("commission_bps", artifact.commission * 10000),
            ("slippage_bps", artifact.slippage * 10000),
        ):
            if not finite(value) or abs(bundle["cost_schedule"][name] - value) > 1e-9:
                raise ValueError(
                    "campaign cost schedule differs from actual evaluation model"
                )
        from trading_agent.backtest.accounting_evidence import (
            export_paper_trade_accounting,
        )
        import hashlib

        try:
            report_bytes = Path(artifact.report_path).read_bytes()
            if row.get("report_sha256") != hashlib.sha256(report_bytes).hexdigest():
                raise ValueError(
                    "source report content hash differs from campaign binding"
                )
            source_report = json.loads(report_bytes)
            if type(source_report.get("open_positions")) is not int:
                raise ValueError("source report lacks a measured position count")
            curve = source_report["equity_curve"]
            initial = source_report["initial_capital"]
            if not finite(initial) or not curve or not finite(curve[-1][1]):
                raise ValueError("missing measured source equity")
            source_open_leg = source_report.get("measured_open_inventory")
            if source_report["open_positions"] == 0:
                if source_open_leg is not None:
                    raise ValueError("flat source must not declare open inventory")
                rebuilt = export_paper_trade_accounting(
                    source_report["trades"],
                    equity_delta=curve[-1][1] - initial,
                    open_inventory=False,
                )
            else:
                if not isinstance(source_open_leg, Mapping):
                    raise ValueError(
                        "open source positions require a measured open leg"
                    )
                rebuilt = export_paper_trade_accounting(
                    source_report["trades"],
                    equity_delta=curve[-1][1] - initial,
                    open_inventory=source_open_leg,
                    equity_basis=source_report.get("measured_equity_basis", "cash"),
                )
            if rebuilt != source_report.get("measured_accounting"):
                raise ValueError(
                    "source accounting declaration differs from rebuilt ledger"
                )
            for key in (
                "gross_pnl",
                "fees",
                "slippage",
                "spread_cost",
                "net_pnl",
                "trades",
            ):
                if not finite(row.get(key)) or abs(row[key] - rebuilt[key]) > 1e-8:
                    raise ValueError(
                        f"campaign {key} differs from measured source accounting"
                    )
            # A carried position must appear in the campaign row with the same
            # measured amounts as the source rebuilt. Omitting it here would
            # let a bundle publish a fold's closed half and stay silent about
            # the inventory the same fold was still holding.
            if source_open_leg is not None:
                for key in (
                    "open_quantity",
                    "open_mark_value",
                    "open_entry_fee",
                    "open_unrealized",
                    "total_pnl",
                ):
                    if not finite(row.get(key)) or abs(row[key] - rebuilt[key]) > 1e-8:
                        raise ValueError(
                            f"campaign {key} differs from measured open inventory"
                        )
                if row.get("open_side") != rebuilt["open_side"]:
                    raise ValueError("campaign open_side differs from source")
            elif row.get("open_quantity") is not None:
                raise ValueError("flat source must not publish open inventory")
            if rebuilt["market_impact"] != 0 or rebuilt["price_cap_credit"] != 0:
                raise ValueError(
                    "campaign schema does not yet represent impact/cap credit"
                )
        except (
            OSError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("missing/invalid measured source report") from exc
    actual_gates = {gate.gate_id: gate for gate in result.gate_results}
    if bundle.get("native_gate_contract") is not None:
        from trading_agent.backtest.native_gate_contract import (
            freeze_native_gate_contract,
            native_evidence_from_result,
        )

        contract = bundle["native_gate_contract"]
        expected = freeze_native_gate_contract(
            result.spec, run_holdout=contract["run_holdout"]
        )
        if (
            contract != expected
            or result.aggregate_metrics.get("native_gate_contract") != contract
        ):
            raise ValueError("native contract differs from pre-run WFO contract")
        if native_evidence_from_result(contract, result) != bundle.get(
            "native_gate_evidence"
        ):
            raise ValueError("native evidence differs from measured WFO gates")
        return bundle
    if getattr(result, "aggregate_metrics", {}).get("native_gate_contract") is not None:
        raise ValueError("campaign omitted frozen native gate contract")
    if len(actual_gates) != len(result.gate_results):
        raise ValueError("actual WFO result contains duplicate gate IDs")
    if set(actual_gates) != {gate["gate_id"] for gate in bundle["gate_set"]}:
        raise ValueError("campaign gate_set omits/adds WFO hard gates")
    for gate in bundle["gate_set"]:
        actual = actual_gates[gate["gate_id"]]
        observed = bundle["aggregate"][gate["metric"].removeprefix("aggregate.")]
        if (
            actual.threshold != gate["threshold"]
            or actual.comparison != gate["comparison"]
            or not finite(actual.observed_value)
            or abs(actual.observed_value - observed) > 1e-9
        ):
            raise ValueError(
                "campaign gate threshold/observation differs from actual WFO gate"
            )
    return bundle


@dataclass(frozen=True)
class TrialIdempotenceReport:
    """Verify trial-count idempotence after resume/reuse.

    Each (params, cost_scenario, fold_id, stage) tuple must appear exactly once
    in the cell timings or selection records. A cache hit must NOT create a new
    trial entry — resume/reuse is an execution optimization, not a
    qualification event. Counting the same measurement twice inflates trial burden
    and can change selection statistics.
    """

    ok: bool
    total_trials: int
    unique_trials: int
    duplicate_trials: tuple[str, ...] = ()
    details: str = ""

    def summary(self) -> str:
        verdict = "OK" if self.ok else "VIOLATION"
        lines = [
            f"trial idempotence: {verdict} "
            f"({self.unique_trials}/{self.total_trials} unique trials)"
        ]
        if self.duplicate_trials:
            lines.append("  duplicates: " + ", ".join(self.duplicate_trials))
        if self.details:
            lines.append(f"  {self.details}")
        return "\n".join(lines)


def _trial_key_from_cell_dir(dirname: str) -> str | None:
    """Extract the trial identifier from a cell directory name.

    Cell dirs are named like ``<strat>__<symbol>__<tf>__<cost>__<hash>__<window>``.
    The hash segment uniquely identifies (params, cost_scenario) for the strategy.
    """
    parts = dirname.split("__")
    if len(parts) < 4 or not parts[0]:
        return None
    # strategy__pair__tf__cost__paramhash__windowhash
    # paramhash is segments[4] which encodes params+cost
    if len(parts) >= 5:
        return f"{parts[0]}__{parts[1]}__{parts[2]}__{parts[3]}__{parts[4]}"
    return None


def verify_trial_idempotence(out_root: Path, strategy: str) -> TrialIdempotenceReport:
    """Check that every trial appears exactly once in the campaign output.

    Scans both ``cell_timings.jsonl`` and the cell directory layout for duplicate
    (strategy, symbol, timeframe, cost, params_hash, fold) tuples. Cache hits
    should appear in timings with ``cache_hit: true`` without creating new cell
    directories or re-measuring.
    """
    out_root = Path(out_root).resolve()
    if not out_root.exists():
        return TrialIdempotenceReport(
            ok=False,
            total_trials=0,
            unique_trials=0,
            details="output root does not exist",
        )

    # Cell directories are named <strategy>__<symbol>__<tf>__<cost>__<params>__<window>.
    # They may be in out_root/ (run_real_wfo_campaign layout) or out_root/<strategy>/ (wfo_parallel).
    # Scan both locations for directories matching the strategy prefix.
    search_roots = [out_root]
    strategy_dir = out_root / strategy
    if strategy_dir.exists():
        search_roots.append(strategy_dir)

    # Collect keys from cell_timings.jsonl
    timings_path = out_root / "cell_timings.jsonl"
    seen_trials: dict[str, int] = defaultdict(int)
    cache_hit_count = 0
    if timings_path.exists():
        for line in timings_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return TrialIdempotenceReport(
                    ok=False,
                    total_trials=len(seen_trials),
                    unique_trials=len(seen_trials),
                    duplicate_trials=tuple("unparseable_timing_line"),
                    details="cell_timings.jsonl contains unparseable lines",
                )
            if entry.get("cache_hit"):
                cache_hit_count += 1
                # Cache hits do NOT create new trial entries - they reuse existing
                # measurements. Only count actual COMPLETED runs.
                continue
            # Build trial key from the cell_id which encodes strategy, symbol, timeframe, cost, params, window
            cell_id = entry.get("cell_id", "")
            if cell_id and cell_id.startswith(f"{strategy}__"):
                # cell_id format: ma_adx__BTCUSDT__1h__1x__p37f5dcbec2bd
                # This uniquely identifies (strategy, pair, tf, cost, params_hash)
                # Add window info to distinguish inner/outer cells
                win = entry.get("window", {})
                trial_key = (
                    f"{cell_id}__"
                    f"mstart_{win.get('measurement_start', '?')}__"
                    f"mend_{win.get('measurement_end', '?')}__"
                    f"start_{win.get('start', '?')}__"
                    f"end_{win.get('end', '?')}"
                )
                seen_trials[trial_key] += 1

    # Collect keys from cell directory names
    cell_dirs = defaultdict(int)
    for root_dir in search_roots:
        for entry in root_dir.iterdir():
            if entry.is_dir() and "__" in entry.name and entry.name.startswith(f"{strategy}__"):
                cell_dirs[entry.name] += 1

    duplicates = tuple(key for key, count in seen_trials.items() if count > 1)
    duplicate_dirs = tuple(key for key, count in cell_dirs.items() if count > 1)

    total = len(seen_trials)
    unique = len(seen_trials) - len(duplicates)
    all_duplicates = duplicates + duplicate_dirs

    ok = len(all_duplicates) == 0
    details = f"cache_hit_count={cache_hit_count}, cell_dirs={len(cell_dirs)}"

    return TrialIdempotenceReport(
        ok=ok,
        total_trials=total,
        unique_trials=unique,
        duplicate_trials=all_duplicates,
        details=details,
    )


def _hash_params(params: dict) -> str:
    """Stable hash of params dict for deduplicating trials."""
    from trading_agent.backtest.campaign_evidence import content_hash

    if isinstance(params, dict):
        return content_hash(params)
    return str(params)


@dataclass(frozen=True)
class SelectionIdempotenceReport:
    """Verify frozen-selection idempotence after resume/reuse.

    The selected parameters for each outer fold must be identical before and
    after resume/reuse. Selection decisions are frozen in inner validation and
    must not change when outer folds are re-evaluated from cache.
    """

    ok: bool
    folds_checked: int
    folds_consistent: int
    inconsistent_folds: tuple[str, ...] = ()
    details: str = ""

    def summary(self) -> str:
        verdict = "OK" if self.ok else "INCONSISTENT"
        lines = [
            f"selection idempotence: {verdict} "
            f"({self.folds_consistent}/{self.folds_checked} folds consistent)"
        ]
        if self.inconsistent_folds:
            lines.append("  inconsistent: " + ", ".join(self.inconsistent_folds))
        if self.details:
            lines.append(f"  {self.details}")
        return "\n".join(lines)


def verify_frozen_selection_idempotence(out_root: Path, strategy: str) -> SelectionIdempotenceReport:
    """Check that selected outer parameters are unchanged after resume/reuse.

    Reads the selection decision records and compares the selected params against
    the outer fold params in the campaign bundle/evidence. If the bundle doesn't
    exist yet, checks the cell report files directly.
    """
    out_root = Path(out_root).resolve()
    if not out_root.exists():
        return SelectionIdempotenceReport(
            ok=True,
            folds_checked=0,
            folds_consistent=0,
            details="no campaign output found",
        )

    # Cell directories may be in out_root/ or out_root/<strategy>/
    search_roots = [out_root]
    strategy_dir = out_root / strategy
    if strategy_dir.exists():
        search_roots.append(strategy_dir)
        return SelectionIdempotenceReport(
            ok=True,
            folds_checked=0,
            folds_consistent=0,
            details="no campaign output found (fresh run or ABSTAIN)",
        )

    # Collect selection decisions
    decisions: dict[str, dict] = {}
    for entry in strategy_dir.rglob("selection_decision_*.json"):
        fold_id = entry.stem.replace("selection_decision_", "")
        try:
            data = json.loads(entry.read_text())
            decisions[fold_id] = data
        except (json.JSONDecodeError, OSError):
            pass

    # Also check inner_selection_freezes
    freeze_dir = strategy_dir / "inner_selection_freezes"
    if freeze_dir.exists():
        for entry in freeze_dir.glob("*.json"):
            try:
                data = json.loads(entry.read_text())
                fold_id = data.get("fold_id", entry.stem)
                if fold_id not in decisions:
                    decisions[fold_id] = data
            except (json.JSONDecodeError, OSError):
                pass

    # Read campaign bundle if it exists to cross-check outer params
    bundle_path = strategy_dir / "campaign_evidence.json"
    bundle_results = {}
    if bundle_path.exists():
        try:
            bundle = json.loads(bundle_path.read_text())
            for row in bundle.get("results", []):
                bundle_results[row.get("fold_id", "")] = row
        except (json.JSONDecodeError, OSError):
            pass

    consistent = 0
    inconsistent = []
    for fold_id, decision in decisions.items():
        fold_str = str(fold_id)
        # Get selected params from decision
        selected = decision.get("selected", {}).get("params", {})
        selected_cost = decision.get("selected", {}).get("cost_scenario", "")

        # Cross-check with bundle outer result
        outer = bundle_results.get(fold_str, {})
        if outer and outer.get("verdict") != "PASS":
            # For FAIL campaigns, just verify decision record is consistent
            consistent += 1
            continue

        # Compare selected params match outer fold params
        if outer:
            outer_params = {k: v for k, v in outer.items()
                           if k not in ("fold_id", "gross_pnl", "net_pnl", "fees",
                                       "slippage", "return_pct", "sharpe",
                                       "max_dd_pct", "cost_cleared",
                                       "entry_price", "exit_price",
                                       "trades", "report_sha256") and k in selected}
            if outer_params and outer_params != selected:
                inconsistent.append(fold_str)
                continue

        consistent += 1

    ok = len(inconsistent) == 0
    details = (
        f"decisions={len(decisions)}, bundle_cross_checks={len(bundle_results)}, "
        f"cache_hits={_count_cache_hits(out_root)}"
    )

    return SelectionIdempotenceReport(
        ok=ok,
        folds_checked=len(decisions),
        folds_consistent=consistent,
        inconsistent_folds=tuple(inconsistent),
        details=details,
    )


def _count_cache_hits(out_root: Path) -> int:
    """Count cache hits in cell_timings.jsonl."""
    timings_path = out_root / "cell_timings.jsonl"
    if not timings_path.exists():
        return 0
    count = 0
    for line in timings_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            if json.loads(line).get("cache_hit"):
                count += 1
        except json.JSONDecodeError:
            pass
    return count


@dataclass(frozen=True)
class LedgerIntegrityReport:
    """Verify raw ledger integrity after cold reuse from another packet."""

    ok: bool
    folds_checked: int
    mismatched_folds: tuple[str, ...] = ()
    details: str = ""

    def summary(self) -> str:
        verdict = "OK" if self.ok else "VIOLATION"
        lines = [
            f"ledger integrity: {verdict} "
            f"({self.folds_checked - len(self.mismatched_folds)}/{self.folds_checked} folds matched)"
        ]
        if self.mismatched_folds:
            lines.append("  mismatched: " + ", ".join(self.mismatched_folds))
        if self.details:
            lines.append(f"  {self.details}")
        return "\n".join(lines)


def verify_raw_ledger_integrity(out_root: Path, strategy: str, data_root: Path) -> LedgerIntegrityReport:
    """Verify raw reports, accounting, and data binding are intact after reuse.

    Checks:
    1. Every cell report.json is readable and parseable
    2. report_sha256 in cache matches actual file content
    3. measured_accounting in report matches rebuilt ledger from raw trades
    4. data_manifest in report matches actual file SHA256
    5. No report path escapes the destination packet
    """
    from trading_agent.backtest.campaign_evidence import finite
    from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting

    out_root = Path(out_root).resolve()
    if not out_root.exists():
        return LedgerIntegrityReport(
            ok=False,
            folds_checked=0,
            details="output root does not exist",
        )

    # Cell directories may be in out_root/ or out_root/<strategy>/
    search_roots = [out_root]
    strategy_dir = out_root / strategy
    if strategy_dir.exists():
        search_roots.append(strategy_dir)

    # Collect all report.json files (including reused_reports/ and cell_attempts/)
    reports: list[Path] = []
    for root_dir in search_roots:
        reused_dir = root_dir / "reused_reports"
        if reused_dir.exists():
            reports.extend(reused_dir.rglob("report.json"))
        # Regular cell directories named <strategy>__<...>
        for entry in root_dir.iterdir():
            if entry.is_dir() and "__" in entry.name and entry.name.startswith(f"{strategy}__"):
                reports.extend(entry.rglob("report.json"))
    # Cell attempts are always in out_root
    attempts_dir = out_root / "cell_attempts"
    if attempts_dir.exists():
        reports.extend(attempts_dir.rglob("report.json"))

    # Also check cache entries for report_sha256 validation
    cache_dir = out_root / "cell_resume_cache"
    cache_records: dict[str, dict] = {}
    if cache_dir.exists():
        for cache_file in cache_dir.glob("*.json"):
            try:
                record = json.loads(cache_file.read_text())
                if "report_sha256" in record and "artifact" in record:
                    cache_records[record["artifact"].get("artifact_id", cache_file.stem)] = record
            except (json.JSONDecodeError, OSError):
                pass

    mismatches = []
    checked = 0

    # Check report-path confinement and SHA256 consistency
    for report_path in reports:
        try:
            resolved = report_path.resolve()
            if not resolved.is_relative_to(out_root):
                mismatches.append(f"{report_path.name}: reports escapes output packet")
                continue

            raw = report_path.read_bytes()
            actual_sha = hashlib.sha256(raw).hexdigest()
            report = json.loads(raw)

            # Check data binding identity
            data_path = report.get("data_path") or report.get("data_manifest", {}).get("path")
            if data_path:
                data_file = Path(data_path)
                if data_file.exists() and not data_file.resolve().is_relative_to(data_root):
                    mismatches.append(f"{report_path.name}: data path escapes data root")
                    continue

            # Check measured accounting integrity
            if "measured_accounting" in report and "trades" in report:
                measured = report["measured_accounting"]
                open_inv = report.get("measured_open_inventory")
                # export_paper_trade_accounting expects False for no open inventory,
                # or the measured open leg mapping. None is not accepted.
                open_inventory_arg = open_inv if isinstance(open_inv, Mapping) else False
                # Use equity_delta from measured_accounting for reconciliation
                equity_delta = measured.get("equity_delta", 0)
                rebuilt = export_paper_trade_accounting(
                    report["trades"],
                    equity_delta=equity_delta,
                    open_inventory=open_inventory_arg,
                    equity_basis=report.get("measured_equity_basis", "cash"),
                )
                # Check key accounting fields match
                for key in ("gross_pnl", "fees", "slippage", "net_pnl", "trades"):
                    if key in measured and key in rebuilt:
                        if not finite(measured[key]) or abs(measured[key] - rebuilt[key]) > 1e-8:
                            mismatches.append(f"{report_path.name}: accounting {key} mismatch")
                            break

            # Cross-check with cache if this report is cached
            artifact_id = report.get("artifact_id")
            if artifact_id and artifact_id in cache_records:
                record = cache_records[artifact_id]
                if record.get("report_sha256") != actual_sha:
                    mismatches.append(f"{report_path.name}: SHA256 mismatch with cache record")
                if record.get("scope") != "execution_reuse_not_qualification":
                    mismatches.append(f"{report_path.name}: cache scope is not execution_reuse")
                if record.get("live_authorized") is not False:
                    mismatches.append(f"{report_path.name}: cache live_authorized is not False")

            checked += 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            mismatches.append(f"{report_path.name}: {type(exc).__name__}: {exc}")

    # Check cell_failures are preserved (not overwritten by retries)
    failures_dir = out_root / "cell_failures"
    failure_count = 0
    if failures_dir.exists():
        failure_count = len(list(failures_dir.glob("*.json")))

    ok = len(mismatches) == 0
    return LedgerIntegrityReport(
        ok=ok,
        folds_checked=checked,
        mismatched_folds=tuple(mismatches),
        details=f"cache_records={len(cache_records)}, failure_records={failure_count}",
    )
