"""Window lock for W-INFORMATION-GATE-01. The entry rule is unset.

This is not a market run and not a new strategy. H-OWN24-CONTEXT-01 stays
closed and cannot be copied in as the baseline.
"""
from __future__ import annotations

from trading_agent.research.btc_context_prereg import (
    CLOCK_LAST_UTC,
    CLOCK_ROWS,
    CLOSED_RULES_NOT_CONTROLS,
    EMBARGO_BARS,
    HOLDOUT_END_UTC,
    HOLDOUT_START_UTC,
    MEASUREMENT_START,
    MEASUREMENT_STOP,
    MIN_TRADES,
    PURGE_BARS,
)
from trading_agent.research.btc_context_rule import ENTRY_RULE_ID as CLOSED_CONTEXT_RULE

WAVE_ID = "W-INFORMATION-GATE-01"
ENTRY_RULE_STATUS = "UNSET"
ENTRY_RULE_ID = None
INFORMATION_GROUP = "UNSET"
BASE_PREDICATE = "UNSET"
PLUS_PREDICATE = "UNSET"

TARGETS: tuple[str, ...] = ("ETH/USDT", "BNB/USDT")
ARMS: tuple[str, ...] = ("BASE", "PLUS")
COSTS: tuple[str, ...] = ("1x", "2x", "slip_stress")
CELL_TIMEOUT_SECONDS = 600
SESSION_CAP_SECONDS = 7200
SESSIONS = 2

CLOSED_RULES: tuple[str, ...] = CLOSED_RULES_NOT_CONTROLS + (CLOSED_CONTEXT_RULE,)


def cell_count() -> int:
    return len(TARGETS) * len(ARMS) * len(COSTS)


def cells_per_session() -> int:
    """One symbol per session, so a session stays inside the 7200s cap."""
    if cell_count() % SESSIONS != 0:
        raise RuntimeError("sessions do not divide the locked cell count")
    return cell_count() // SESSIONS


def session_timeout_seconds() -> int:
    budget = cells_per_session() * CELL_TIMEOUT_SECONDS
    if budget >= SESSION_CAP_SECONDS:
        raise RuntimeError("a locked session stands on or above the 7200s cap")
    return budget


def execution_authority() -> dict[str, object]:
    """Refuse a run while the mechanism is unnamed."""
    if ENTRY_RULE_ID is not None or ENTRY_RULE_STATUS != "UNSET":
        raise RuntimeError("this lock refuses a named entry rule")
    if CLOSED_CONTEXT_RULE not in CLOSED_RULES:
        raise RuntimeError("H-OWN24-CONTEXT-01 must stay closed")
    if not CLOCK_LAST_UTC < HOLDOUT_START_UTC:
        raise RuntimeError("locked clock reaches the holdout")
    if MEASUREMENT_START != 216 or MEASUREMENT_STOP != CLOCK_ROWS:
        raise RuntimeError("measurement window drifted from the pre-holdout clock")
    if PURGE_BARS != 216 or EMBARGO_BARS != 216:
        raise RuntimeError("purge or embargo drifted")
    return {
        "wave_id": WAVE_ID,
        "market_run": False,
        "live_authorized": False,
        "qualification_possible": False,
        "reason": "RULE_UNSET",
        "entry_rule_status": ENTRY_RULE_STATUS,
        "information_group": INFORMATION_GROUP,
        "closed_context_rule": CLOSED_CONTEXT_RULE,
        "cells": cell_count(),
        "sessions": SESSIONS,
        "cells_per_session": cells_per_session(),
        "session_timeout_seconds": session_timeout_seconds(),
        "measurement": (MEASUREMENT_START, MEASUREMENT_STOP),
        "min_trades": MIN_TRADES,
        "holdout_end_utc": HOLDOUT_END_UTC,
    }
