"""Multiple-testing governance (Section 10 of the hardening brief).

Every trial is recorded immutably: strategy + parameter hash + metric +
search-space hash.  Renaming a strategy does NOT reset its trial history —
the trials are keyed by content (``param_hash``), not by display name, so
"optimization by relabeling" is impossible.

``search_space_hash`` lets us detect when a grid-search family shares the
same search space, so effective trial counts (and hence multiple-testing
corrections) are honest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def param_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:24]


def search_space_hash(spaces: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(spaces, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:24]


@dataclass
class TrialRecord:
    trial_id: str
    strategy_name: str
    param_hash: str
    search_space_hash: str
    metric_name: str
    metric_value: float
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "strategy_name": self.strategy_name,
            "param_hash": self.param_hash,
            "search_space_hash": self.search_space_hash,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


class TrialsRegistry:
    """Immutable, content-keyed trial registry.

    Trial identity = (param_hash, search_space_hash) — the *experiment
    content*.  Renaming a strategy does NOT create a fresh trial or reset its
    history; the new name is recorded as an alias on the existing record.
    Rerunning the same experiment returns the same trial id (idempotent).
    """

    def __init__(self) -> None:
        self._by_content: dict[tuple[str, str], TrialRecord] = {}
        self._history: list[TrialRecord] = []

    def record(
        self,
        *,
        strategy_name: str,
        params: dict[str, Any],
        search_space: dict[str, Any],
        metric_value: float,
        metric_name: str = "total_return_pct",
        metadata: dict[str, Any] | None = None,
    ) -> TrialRecord:
        p_hash = param_hash(params)
        s_hash = search_space_hash(search_space)
        key = (p_hash, s_hash)
        existing = self._by_content.get(key)
        if existing is not None:
            # Same experiment content: stable id; update metric + aliases.
            existing.metric_value = metric_value
            if metadata:
                existing.metadata.update(metadata)
            aliases = existing.metadata.setdefault("alias_names", [])
            if strategy_name != existing.strategy_name and strategy_name not in aliases:
                aliases.append(strategy_name)
            self._history.append(existing)
            return existing
        trial = TrialRecord(
            trial_id=f"trial_{len(self._history) + 1:04d}",
            strategy_name=strategy_name,
            param_hash=p_hash,
            search_space_hash=s_hash,
            metric_name=metric_name,
            metric_value=metric_value,
            metadata=metadata or {},
        )
        self._by_content[key] = trial
        self._history.append(trial)
        return trial

    def trials_for_strategy(self, strategy_name: str) -> list[TrialRecord]:
        return [t for t in self._history if t.strategy_name == strategy_name]

    def unique_strategies(self) -> set[str]:
        return {t.strategy_name for t in self._history}

    def total_trials(self) -> int:
        """Number of UNIQUE experiments (content-addressed)."""
        return len(self._by_content)

    def evaluation_count(self) -> int:
        """Number of evaluation attempts (may exceed unique trials)."""
        return len(self._history)

    def best_trial(
        self, strategy_name: str | None = None, metric_name: str = "total_return_pct"
    ) -> TrialRecord | None:
        """Highest metric among trials (optionally for one strategy)."""
        candidates = (
            self._history
            if strategy_name is None
            else self.trials_for_strategy(strategy_name)
        )
        candidates = [t for t in candidates if t.metric_name == metric_name]
        if not candidates:
            return None
        return max(candidates, key=lambda t: t.metric_value)
