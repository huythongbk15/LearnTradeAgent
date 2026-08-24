"""
CausationLogger & DecisionAuditCLI — Observability & replay for the authority chain.

Every decision through the authority chain emits a structured JSONL log entry
with full causation chain. The CLI enables forensic audit of any decision.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.authority.causation import CausationChain
from trading_agent.authority.config import LoggingConfig, get_authority_config

logger = logging.getLogger(__name__)


# ── Log Entry Types ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CausationLogEntry:
    """Single causation log entry — one authority decision."""

    timestamp: datetime
    causation_id: str
    authority: str
    symbol: str
    strategy_id: str | None
    inputs_hash: str
    outputs_hash: str
    prev_causation_id: str | None
    decision: dict[str, Any]  # Full decision context
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "causation_id": self.causation_id,
                "authority": self.authority,
                "symbol": self.symbol,
                "strategy_id": self.strategy_id,
                "inputs_hash": self.inputs_hash,
                "outputs_hash": self.outputs_hash,
                "prev_causation_id": self.prev_causation_id,
                "decision": self.decision,
                "metadata": self.metadata,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "CausationLogEntry":
        data = json.loads(json_str)
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class DecisionAuditRecord:
    """Complete audit record for a causation chain."""

    root_causation_id: str
    chain: CausationChain
    log_entries: list[CausationLogEntry]
    final_decision: dict[str, Any]
    outcome: str  # "executed", "denied", "failed", "pending"


# ── CausationLogger ─────────────────────────────────────────────────────


class CausationLogger:
    """
    Thread-safe JSONL logger for authority chain decisions.

    Each authority decision appends one line to the causation log.
    The log is the SOURCE OF TRUTH for audit and replay.
    """

    def __init__(self, config: LoggingConfig | None = None):
        self.config = config or get_authority_config().logging
        self._lock = threading.Lock()
        self._file_handle = None
        self._ensure_log_file()

    def _ensure_log_file(self) -> None:
        """Ensure log file exists and is writable."""
        log_path = Path(self.config.causation_log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append mode, line-buffered
        self._file_handle = open(log_path, "a", buffering=1, encoding="utf-8")

    def log(
        self,
        *,
        causation_chain: CausationChain,
        authority: str,
        symbol: str,
        strategy_id: str | None,
        decision: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Log a decision from the authority chain.

        Returns the causation_id that was logged.
        """
        if not causation_chain.links:
            raise ValueError("Cannot log empty causation chain")

        latest_link = causation_chain.links[-1]
        prev_id = (
            causation_chain.links[-2].causation_id
            if len(causation_chain.links) > 1
            else None
        )

        entry = CausationLogEntry(
            timestamp=datetime.now(UTC),
            causation_id=latest_link.causation_id,
            authority=authority,
            symbol=symbol,
            strategy_id=strategy_id,
            inputs_hash=latest_link.inputs_hash,
            outputs_hash=latest_link.outputs_hash,
            prev_causation_id=prev_id,
            decision=decision,
            metadata=metadata or {},
        )

        with self._lock:
            if self._file_handle:
                self._file_handle.write(entry.to_json() + "\n")
                self._file_handle.flush()

        return latest_link.causation_id

    def log_chain(self, chain: CausationChain, final_decision: dict[str, Any]) -> str:
        """Log an entire causation chain as a single atomic entry (for replay)."""
        root_id = chain.root_inputs_hash[:24] if chain.root_inputs_hash else "root"

        entry = CausationLogEntry(
            timestamp=datetime.now(UTC),
            causation_id=f"chain_{root_id}",
            authority="CAUSATION_CHAIN",
            symbol=final_decision.get("symbol", ""),
            strategy_id=final_decision.get("strategy_id"),
            inputs_hash=chain.root_inputs_hash,
            outputs_hash=chain.links[-1].outputs_hash if chain.links else "",
            prev_causation_id=None,
            decision={
                "chain_length": len(chain.links),
                "authorities": [link.authority for link in chain.links],
                "final_decision": final_decision,
            },
            metadata={"full_chain": chain.to_json()},
        )

        with self._lock:
            if self._file_handle:
                self._file_handle.write(entry.to_json() + "\n")
                self._file_handle.flush()

        return entry.causation_id

    def close(self) -> None:
        """Close the log file."""
        with self._lock:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None

    def __enter__(self) -> "CausationLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ── DecisionAuditCLI ────────────────────────────────────────────────────


class DecisionAuditCLI:
    """
    CLI for auditing decisions by causation_id.

    Usage:
        cli = DecisionAuditCLI()
        record = cli.audit("ca_abc123...")
        print(record.final_decision)
    """

    def __init__(self, config: LoggingConfig | None = None):
        self.config = config or get_authority_config().logging
        self._log_path = Path(self.config.causation_log_path)

    def audit(self, causation_id: str) -> DecisionAuditRecord | None:
        """
        Reconstruct full audit record for a causation_id.

        Returns None if not found.
        """
        entries = self._load_entries_for_causation(causation_id)
        if not entries:
            return None

        # Reconstruct chain from entries
        chain = self._reconstruct_chain(entries)
        final_decision = entries[-1].decision if entries else {}

        return DecisionAuditRecord(
            root_causation_id=causation_id,
            chain=chain,
            log_entries=entries,
            final_decision=final_decision,
            outcome=self._infer_outcome(entries),
        )

    def search(
        self,
        *,
        symbol: str | None = None,
        strategy_id: str | None = None,
        authority: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 100,
    ) -> list[CausationLogEntry]:
        """Search log entries with filters."""
        entries = []
        count = 0

        if not self._log_path.exists():
            return entries

        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                if count >= limit:
                    break
                try:
                    entry = CausationLogEntry.from_json(line.strip())
                except Exception:
                    continue

                if symbol and entry.symbol != symbol:
                    continue
                if strategy_id and entry.strategy_id != strategy_id:
                    continue
                if authority and entry.authority != authority:
                    continue
                if from_time and entry.timestamp < from_time:
                    continue
                if to_time and entry.timestamp > to_time:
                    continue

                entries.append(entry)
                count += 1

        return entries

    def replay_chain(self, causation_id: str) -> tuple[bool, str | None]:
        """
        Verify a causation chain can be deterministically replayed.

        Returns (success, error_message).
        """
        record = self.audit(causation_id)
        if not record:
            return False, "Causation ID not found in log"

        # Verify chain integrity
        ok, err = record.chain.verify_chain()
        if not ok:
            return False, f"Chain integrity broken: {err}"

        # Verify each link's inputs/outputs match log entries
        for i, (link, entry) in enumerate(zip(record.chain.links, record.log_entries)):
            if link.causation_id != entry.causation_id:
                return (
                    False,
                    f"Link {i} causation_id mismatch: {link.causation_id} != {entry.causation_id}",
                )
            if link.inputs_hash != entry.inputs_hash:
                return False, f"Link {i} inputs_hash mismatch"
            if link.outputs_hash != entry.outputs_hash:
                return False, f"Link {i} outputs_hash mismatch"

        return True, None

    def export_chain(self, causation_id: str, output_path: Path) -> bool:
        """Export full audit record to JSON file."""
        record = self.audit(causation_id)
        if not record:
            return False

        export = {
            "causation_id": causation_id,
            "chain": record.chain.to_json(),
            "log_entries": [e.to_json() for e in record.log_entries],
            "final_decision": record.final_decision,
            "outcome": record.outcome,
            "exported_at": datetime.now(UTC).isoformat(),
        }

        output_path.write_text(json.dumps(export, indent=2))
        return True

    def _load_entries_for_causation(self, causation_id: str) -> list[CausationLogEntry]:
        """Load all log entries for a causation chain."""
        entries = []

        if not self._log_path.exists():
            return entries

        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = CausationLogEntry.from_json(line.strip())
                except Exception:
                    continue

                # Match by causation_id or chain membership
                if entry.causation_id == causation_id:
                    entries.append(entry)
                elif entry.causation_id.startswith(
                    "chain_"
                ) and causation_id in entry.decision.get("authorities", []):
                    entries.append(entry)

        # Sort by timestamp
        entries.sort(key=lambda e: e.timestamp)
        return entries

    def _reconstruct_chain(self, entries: list[CausationLogEntry]) -> CausationChain:
        """Reconstruct CausationChain from log entries."""
        from trading_agent.authority.causation import CausationLink

        links = []
        for entry in entries:
            if entry.authority == "CAUSATION_CHAIN":
                continue  # Skip chain summary entries

            link = CausationLink(
                authority=entry.authority,
                causation_id=entry.causation_id,
                inputs_hash=entry.inputs_hash,
                outputs_hash=entry.outputs_hash,
                timestamp=entry.timestamp,
                metadata=entry.metadata,
            )
            links.append(link)

        root_hash = entries[0].inputs_hash if entries else ""
        return CausationChain(links=tuple(links), root_inputs_hash=root_hash)

    def _infer_outcome(self, entries: list[CausationLogEntry]) -> str:
        """Infer final outcome from log entries."""
        if not entries:
            return "unknown"

        last = entries[-1]
        decision = last.decision

        if "allowed" in decision:
            return "executed" if decision["allowed"] else "denied"
        if "error" in decision:
            return "failed"
        if "pending" in decision:
            return "pending"

        return "unknown"


# ── Convenience Functions ───────────────────────────────────────────────


_default_logger: CausationLogger | None = None
_default_logger_lock = threading.Lock()


def get_causation_logger() -> CausationLogger:
    """Get or create the default causation logger."""
    global _default_logger
    with _default_logger_lock:
        if _default_logger is None:
            _default_logger = CausationLogger()
        return _default_logger


def log_authority_decision(
    chain: CausationChain,
    authority: str,
    symbol: str,
    strategy_id: str | None,
    decision: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> str:
    """Convenience function to log an authority decision."""
    return get_causation_logger().log(
        causation_chain=chain,
        authority=authority,
        symbol=symbol,
        strategy_id=strategy_id,
        decision=decision,
        metadata=metadata,
    )


__all__ = [
    "CausationLogEntry",
    "DecisionAuditRecord",
    "CausationLogger",
    "DecisionAuditCLI",
    "get_causation_logger",
    "log_authority_decision",
]
