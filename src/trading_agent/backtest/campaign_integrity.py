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

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


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
                "  no result at all for: "
                + ", ".join(sorted(self.missing_strategies))
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
        notes.append(
            "no result list supplied; verified against cells on disk only"
        )
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


@dataclass(frozen=True)
class BundleReport:
    """Verdict on a campaign evidence bundle."""

    admissible: bool
    problems: tuple[str, ...] = ()
    bundle_id: str | None = None

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
        return BundleReport(
            admissible=False, problems=(f"unreadable bundle: {exc}",)
        )
    if not isinstance(bundle, dict):
        return BundleReport(
            admissible=False, problems=("bundle is not an object",)
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
            problems.append(
                "data_manifest.window.bars disagrees with input_rows"
            )
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
            if any(f.get("overlap_group") is None for f in folds
                   if isinstance(f, dict)):
                problems.append(
                    "overlap_policy is overlapping but a fold has no "
                    "overlap_group"
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
        problems.append(
            "trades absent without trades_absent_reason"
        )

    # Rule 8 — the verdict must be derivable.
    verdict = bundle.get("verdict")
    failed = bundle.get("failed_gates")
    if verdict not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        problems.append(f"verdict {verdict!r} is not PASS/FAIL/INCONCLUSIVE")
    if verdict == "PASS" and failed:
        problems.append("verdict is PASS but failed_gates is non-empty")
    if verdict in {"FAIL", "INCONCLUSIVE"} and not failed:
        problems.append(
            f"verdict {verdict} but failed_gates is empty — name the gates"
        )

    return BundleReport(
        admissible=not problems,
        problems=tuple(problems),
        bundle_id=bundle.get("campaign_id"),
    )
