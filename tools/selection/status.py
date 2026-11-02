"""status.json, the file the page reads. Decision, reasons, data health and the tripwire state, nothing else.

No return, no Sharpe, no P&L of any kind, before the read date or after it: a page that shows profit turns the single reading into a watch. So the fields are an allowlist, write refuses anything outside it, and the only numbers are the phase, the due book and counts.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from tools.selection import decide

FIELDS = frozenset({
    "as_of", "phase", "due_book", "decision", "main_reason", "reasons", "held", "list", "list_monday",
    "data_health", "tripwire", "next_run", "cohort_start", "read_date", "weeks_done", "rs_blocks",
})
HEALTH_FIELDS = frozenset({"checked", "readable", "missing", "stale", "gaps", "daily_pairs", "daily_failed", "hourly_failed", "new_coins", "weeks_waiting", "errors"})
TRIPWIRE_STATES = ("NOT_ARMED", "OK", "FIRED")


def status_path(root: Path) -> Path:
    return root / "status.json"


def build(decision: dict, tripwire: str, cohort: dict | None, weeks_done: int, errors: int, rs_blocks: int = 0) -> dict:
    if tripwire not in TRIPWIRE_STATES:
        raise ValueError(f"unknown tripwire state {tripwire}")
    next_run = date.fromisoformat(decision["as_of"]) + timedelta(days=7)
    return {
        "as_of": decision["as_of"],
        "phase": decision["phase"],
        "due_book": decision["due_book"],
        "decision": decision["decision"],
        "main_reason": decision["main_reason"],
        "reasons": decision["reasons"],
        "held": decision["held"],
        "list": decision["list"],
        "list_monday": decision["list_monday"],
        "data_health": {**decision["health"], "errors": errors},
        "tripwire": tripwire,
        "next_run": next_run.isoformat(),
        "cohort_start": cohort["start"] if cohort else None,
        "read_date": cohort["read_date"] if cohort else None,
        "weeks_done": weeks_done,
        "rs_blocks": rs_blocks,
    }


def last_known(root: Path) -> tuple[str, int]:
    """The tripwire and weeks done of the last status, for a run that cannot read its ledger: a fired tripwire stays fired. Nothing to read means nothing was armed."""
    try:
        status = decide.read_json(status_path(root))
        return status["tripwire"], status["weeks_done"]
    except (OSError, ValueError, KeyError, TypeError):
        return "NOT_ARMED", 0


def write(root: Path, status: dict) -> None:
    extra = (set(status) - FIELDS) | (set(status["data_health"]) - HEALTH_FIELDS)
    if extra:
        raise ValueError(f"not a page field: {sorted(extra)}")
    decide.write_text(status_path(root), decide.dumps(status))
