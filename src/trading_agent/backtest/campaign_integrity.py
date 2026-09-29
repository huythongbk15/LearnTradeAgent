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
