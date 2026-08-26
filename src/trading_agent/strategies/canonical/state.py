"""Per-strategy-per-symbol runtime state isolation (STR-0108).

Guarantees:

1. State is keyed strictly by ``strategy_id × symbol`` — one strategy's
   seen-event ledger can never influence another's.
2. A duplicate market event (same observation identity delivered twice,
   e.g. after a reconnect or a replay) is answered with ``False`` so the
   caller skips indicator/allocator updates entirely.  Each event is
   applied at most once per key.

The ledger is intentionally in-memory and bounded per key; durable
deduplication across restarts remains the lifecycle/event-store layer's
job.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

_MAX_SEEN_PER_KEY = 10_000


@dataclass(frozen=True)
class StrategyStateKey:
    """Composite isolation key."""

    strategy_id: str
    symbol: str

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.symbol:
            raise ValueError("strategy_id and symbol are required")


class StrategyEventLedger:
    """Thread-safe, bounded seen-event set per :class:`StrategyStateKey`."""

    def __init__(self, *, max_seen_per_key: int = _MAX_SEEN_PER_KEY) -> None:
        self._seen: dict[StrategyStateKey, set[str]] = {}
        self._order: dict[StrategyStateKey, list[str]] = {}
        self._lock = threading.Lock()
        self._max = int(max_seen_per_key)

    def observe(self, key: StrategyStateKey, event_id: str) -> bool:
        """Return True iff *event_id* is new for *key*.

        True → caller must apply the event (indicators, forecasts, …).
        False → duplicate delivery; caller must skip all updates.
        """
        if not event_id:
            raise ValueError("event_id is required")
        with self._lock:
            seen = self._seen.setdefault(key, set())
            if event_id in seen:
                return False
            seen.add(event_id)
            order = self._order.setdefault(key, [])
            order.append(event_id)
            if len(order) > self._max:
                evicted = order.pop(0)
                seen.discard(evicted)
            return True

    def has(self, key: StrategyStateKey, event_id: str) -> bool:
        with self._lock:
            return event_id in self._seen.get(key, set())

    def reset(self, key: StrategyStateKey) -> None:
        with self._lock:
            self._seen.pop(key, None)
            self._order.pop(key, None)
