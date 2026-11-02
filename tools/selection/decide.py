"""What a Monday decides: the reasons, the book that is due, the cohort. The real side stays NO_TRADE: nothing here can leave it.

Reasons follow the table of docs/operations/SELECTION_PAPER_RUN.md: all that match, in the page's order, the first is the main one. The page gives no number for "missing or old", so it reads the library's own limit: a last bar more than STALE_H hours old at the last hour before the cutoff is stale, and a hole of more than STALE_H hours inside the last week is a gap. A shorter hole is held through by the library and moves no rule.

Book state is not stored on its own. It is read back from the decisions of finished runs, so it cannot disagree with them. Those decisions hold names only, no score and no return.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from tools.selection import config
from tools.selection.config import BTC, write_text
from tools.selection.library import STALE_H

DECISION = "NO_TRADE"
GAP_WINDOW_HOURS = 168


def dumps(obj) -> str:
    """The one way a record is written, so equal inputs give equal bytes."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf8"))


def stamp_of(as_of: str) -> datetime:
    return datetime.fromisoformat(as_of).replace(tzinfo=UTC)


def is_late(as_of: str, now: datetime) -> bool:
    return now - stamp_of(as_of) > timedelta(hours=config.LATE_LIMIT_HOURS)


@dataclass(frozen=True)
class Health:
    """BTC and the names of the list in force, read at the cutoff. missing: no bar at all. stale: the last bar is too old. gaps: a hole inside the last week that ended."""

    checked: int
    missing: int
    stale: int
    gaps: int

    @property
    def bad(self) -> bool:
        return self.missing + self.stale + self.gaps > 0


def data_health(closes: np.ndarray, coins: list[str], names: list[str]) -> Health:
    """closes T by N, NaN where a coin has no bar, row T-1 the last hour before the cutoff."""
    T = closes.shape[0]
    ix = {c: j for j, c in enumerate(coins)}
    missing = stale = gaps = 0
    wanted = [BTC, *names]
    for c in wanted:
        seen = np.flatnonzero(~np.isnan(closes[:, ix[c]])) if c in ix else np.array([], np.int64)
        if not len(seen):
            missing += 1
            continue
        stale += int(T - 1 - seen[-1] > STALE_H)
        holes = (np.diff(seen) - 1 > STALE_H) & (seen[1:] > T - GAP_WINDOW_HOURS)       # its last missing hour is inside the week
        gaps += int(holes.any())
    return Health(len(wanted), missing, stale, gaps)


@dataclass(frozen=True)
class Facts:
    """All a Monday's reasons read. health is None when no bar could be read. slots is what the rule computed for the due book, None when nothing could be computed."""

    health: Health | None
    list_names: tuple[str, ...]
    daily_fail_share: float
    late: bool
    seal_refusals: int
    slots: tuple[str, ...] | None
    policy_expired: bool
    tripwire_fired: bool


def reasons_for(f: Facts) -> list[str]:
    """Every reason that matches, in the page's order. NO_KEPT_SELECTOR always matches: the study kept 0 of 12, and only the operator can write otherwise. A store that cannot be read because of a masked coin is a seal matter, not a data one."""
    seal = f.seal_refusals > 0
    hit = {
        "NO_KEPT_SELECTOR": True,
        "DATA_STALE_OR_GAP": f.health.bad if f.health else not seal,
        "LIST_INCOMPLETE": len(f.list_names) < config.LIST_N or f.daily_fail_share > config.LIST_FETCH_FAIL_LIMIT,
        "LATE_RUN": f.late,
        "SEAL_SCOPE": seal,
        "ALL_SLOTS_NON_POSITIVE": f.slots is not None and all(s == "cash" for s in f.slots),
        "POLICY_EXPIRED": f.policy_expired,
        "BOOK_TRIPWIRE": f.tripwire_fired,
    }
    return [r for r in config.REASONS if hit[r]]


def selects(reasons: list[str], slots: tuple[str, ...] | None) -> bool:
    """The due book takes a new list only if the rule produced one and no blocking reason matched. All cash is a list."""
    return slots is not None and not config.HOLD_REASONS & set(reasons)


@dataclass(frozen=True)
class Event:
    """A selection: the book of this phase took these slots on this Monday, and BEST_SO_FAR its own."""

    monday: str
    phase: int
    slots: tuple[str, ...]
    best: tuple[str, ...]


def load_history(root: Path) -> list[dict]:
    """Decisions of finished runs, oldest first. A run is finished when its manifest is on disk: that file is written last."""
    return [read_json(f / "decision.json") for f in sorted(config.runs_dir(root).glob("*")) if (f / "manifest.json").exists()]


def events_of(history: list[dict]) -> list[Event]:
    out = []
    for h in history:
        if h["selected"]:
            book = h["books"][str(h["phase"])]
            out.append(Event(h["as_of"], h["phase"], tuple(book["slots"]), tuple(book["best_so_far"])))
    return out


def books_after(events: list[Event]) -> dict[str, dict | None]:
    """The four books, each with its last selection, None until its first."""
    books: dict[str, dict | None] = {str(p): None for p in range(config.PRIMARY_CADENCE_WEEKS)}
    for e in events:
        books[str(e.phase)] = {"since": e.monday, "slots": list(e.slots), "best_so_far": list(e.best)}
    return books


def policy_expired(history: list[dict], as_of: str) -> bool:
    """No valid run last week: the last valid run's draft has run out by this stamp. A valid run is one whose book was not held. Before any run there is no policy to expire."""
    if not history:
        return False
    valid = [h["as_of"] for h in history if h["selected"]]
    if not valid:
        return True
    return stamp_of(valid[-1]) + timedelta(days=config.POLICY_VALIDITY_DAYS) < stamp_of(as_of)


def record(as_of: str, f: Facts, reasons: list[str], selected: bool, events: list[Event], list_monday: str | None, scores_sha256: str | None, counts: dict, errors: list[str]) -> dict:
    """The decision of a Monday. No wall clock, no score, no return: the same inputs write the same bytes, and reading it is not a reading of the book."""
    phase = config.phase_of(as_of)
    return {
        "as_of": as_of,
        "stamp": f"{as_of}T00:00:00Z",
        "decision": DECISION,
        "main_reason": reasons[0],
        "reasons": reasons,
        "phase": phase,
        "due_book": phase,
        "selected": selected,
        "held": not selected,
        "computed_slots": list(f.slots) if f.slots is not None else None,
        "books": books_after(events),
        "list": list(f.list_names),
        "list_monday": list_monday,
        "health": counts,
        "scores_sha256": scores_sha256,
        "errors": errors,
    }


def health_counts(health: Health | None, report, waiting: int = 0) -> dict:
    """The counts of a run's data, for the decision and the page. Counts only. waiting: weeks of the ledger that wait for bars."""
    base = asdict(health) if health else {"checked": 0, "missing": 0, "stale": 0, "gaps": 0}
    return {
        **base,
        "readable": health is not None,
        "daily_pairs": len(report.tradable or []),
        "daily_failed": len(report.daily_failed),
        "hourly_failed": len(report.hourly_failed),
        "new_coins": len(report.new_coins),
        "weeks_waiting": waiting,
    }


def cohort_path(root: Path) -> Path:
    return root / "cohort.json"


def load_cohort(root: Path) -> dict | None:
    path = cohort_path(root)
    return read_json(path) if path.exists() else None


def start_cohort(root: Path, as_of: str, reasons: list[str], selected: bool) -> dict | None:
    """Written once, at the first Monday run on time with no blocking reason, never rewritten. Read once, 60 weeks later."""
    have = load_cohort(root)
    if have is not None or not selected or "LATE_RUN" in reasons:
        return have
    read = date.fromisoformat(as_of) + timedelta(weeks=config.COHORT_WEEKS)
    cohort = {"start": as_of, "read_date": read.isoformat(), "weeks": config.COHORT_WEEKS}
    write_text(cohort_path(root), dumps(cohort))
    return cohort
