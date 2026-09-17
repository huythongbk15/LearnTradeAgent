"""
LLM Decision Trace — audit trail of LLM→deterministic interaction.

For every bar where LLM produces a MarketContext, this trace records:
- The timestamp and symbol
- The raw MarketContext (regime_tags, anomaly_flags, confidence_adjustment)
- The deterministic decision (forecast, risk decision, target exposure)
- Whether the context was used (and how) to enrich the decision

This trace is the backbone of the A/B test: comparing deterministic-only
vs. LLM-enriched runs on the same historical data with deterministic LLM mode
enabled (temperature=0, fixed seed, no-cache).

**Invariants:**
- Trace is immutable (content-addressed by fingerprint).
- No PII or secrets logged — only market state + decisions.
- Trace can be disabled with ``LLM_DECISION_TRACE=0``.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from hashlib import sha256


def _hash_fingerprint(**kwargs: Any) -> str:
    """Deterministic content hash of trace fields."""
    payload = json.dumps(kwargs, sort_keys=True, default=str, allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class LLMDecisionTrace:
    """Immutable record of one LLM→deterministic decision interaction.

    Stored in ResearchMemory for audit and A/B test analysis.
    """

    symbol: str
    timeframe: str
    bar_timestamp: datetime
    # LLM side
    market_context: dict[str, Any]
    context_fingerprint: str
    # Deterministic side
    deterministic_forecast: dict[str, Any]
    exposure_applied: float  # final target exposure after enrichment
    confidence_before: float  # deterministic confidence
    confidence_after: float  # after LLM adjustment
    # Audit
    context_ignored: bool  # True if anomaly flags caused skip
    reason: str
    # Trace
    trace_id: str = ""

    def __post_init__(self) -> None:
        if self.trace_id == "":
            object.__setattr__(
                self,
                "trace_id",
                _hash_fingerprint(
                    symbol=self.symbol,
                    bar_timestamp=self.bar_timestamp.isoformat(),
                    context_fingerprint=self.context_fingerprint,
                    exposure_applied=str(self.exposure_applied),
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bar_timestamp": self.bar_timestamp.isoformat(),
            "market_context": self.market_context,
            "context_fingerprint": self.context_fingerprint,
            "deterministic_forecast": self.deterministic_forecast,
            "exposure_applied": self.exposure_applied,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "context_ignored": self.context_ignored,
            "reason": self.reason,
        }


class DecisionTraceBuffer:
    """Ephemeral in-memory buffer for decision traces.

    Flushed to ResearchMemory at the end of each evaluation cycle.
    Uses a deque with maxlen to cap memory in long-running sessions.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self._buffer: deque[LLMDecisionTrace] = deque(maxlen=max_size)

    def add(self, trace: LLMDecisionTrace) -> None:
        self._buffer.append(trace)

    def drain(self) -> list[LLMDecisionTrace]:
        """Return all traces and clear the buffer."""
        traces = list(self._buffer)
        self._buffer.clear()
        return traces

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def traces(self) -> list[LLMDecisionTrace]:
        return list(self._buffer)
