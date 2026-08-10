from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from generate_live_strategy_evidence import fold_ranges
from trading_agent.execution.live_safety import LiveSafetyError


def test_fold_ranges_are_chronological_and_non_overlapping():
    latest = datetime(2026, 8, 10, 22, 0)
    ranges = fold_ranges(latest, 6, 90)
    assert len(ranges) == 6
    assert all(end - start == timedelta(days=90) for start, end in ranges)
    assert all(ranges[index][1] == ranges[index + 1][0] for index in range(5))
    assert ranges[-1][1] == latest + timedelta(hours=1)


def test_fold_policy_rejects_too_few_folds():
    with pytest.raises(LiveSafetyError, match="at least 6"):
        fold_ranges(datetime(2026, 8, 10), 5, 90)
