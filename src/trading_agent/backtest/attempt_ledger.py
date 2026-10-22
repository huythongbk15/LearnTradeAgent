"""Attempt and evaluation classification for the evaluation ledger.

Two distinctions the earlier ledger rule got wrong:

1. **A start record without a result record is not yet INTERRUPTED.** The
   worker writes ``started.json`` before it executes, so a missing
   ``result.json`` can simply mean the worker is **still running**. An attempt
   is INTERRUPTED only when the worker process is **gone**.

2. **The cache key identifies the EVALUATION, not each ATTEMPT.** One planned
   cell (one evaluation) may have several attempt records. Those attempts are
   the history of one evaluation and must not be counted as separate
   evaluations, nor joined to the wrong evaluation, nor silently dropped.

The evaluation identity is taken from the attempt's own request record
(``cell_id`` + ``cost_scenario``), so it is the same in the campaign path and
in the validation path, where the attempt directories live in different places.
"""
from __future__ import annotations

import json
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COMPLETED = "COMPLETED"
FAILED = "FAILED"
INTERRUPTED = "INTERRUPTED"
RUNNING = "RUNNING"
UNKNOWN = "UNKNOWN"


def _process_alive(pid: object) -> bool:
    """True when the pid still exists. A missing /proc entry means it is gone."""
    try:
        pid_int = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    return Path(f"/proc/{pid_int}").exists()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def attempt_started_epoch(attempt_dir: Path) -> float:
    """The attempt's real start time.

    ``started_at_epoch`` is written by the producer and is the authoritative
    field. ``started_at`` (ISO-8601) is the fallback for records written
    before the epoch field existed. A record with neither is treated as the
    oldest, never as the newest.
    """
    started = _read_json(attempt_dir / "started.json")
    epoch = started.get("started_at_epoch")
    if isinstance(epoch, (int, float)) and epoch > 0:
        return float(epoch)
    iso = started.get("started_at")
    if isinstance(iso, str) and iso:
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def attempt_sequence(attempt_dir: Path) -> int:
    """Producer-assigned ordering, when the record carries one."""
    started = _read_json(attempt_dir / "started.json")
    sequence = started.get("attempt_sequence")
    if isinstance(sequence, int) and sequence >= 0:
        return sequence
    return -1


def classify_attempt(attempt_dir: Path) -> str:
    """Classify ONE attempt directory (the worker's ``cell_execution/<uuid>``).

    COMPLETED   the worker wrote a result whose artifact completed
    FAILED      the worker wrote a result whose artifact did not complete
    INTERRUPTED the worker wrote a start record, no result, and the worker
                process is gone
    RUNNING     the worker wrote a start record, no result, and the worker
                process is still alive
    UNKNOWN     neither a start nor a result record
    """
    result_path = attempt_dir / "result.json"
    if result_path.exists():
        result = _read_json(result_path)
        artifact = result.get("artifact") or {}
        status = artifact.get("status")
        if status == COMPLETED:
            return COMPLETED
        return FAILED if status else UNKNOWN

    started_path = attempt_dir / "started.json"
    if not started_path.exists():
        return UNKNOWN
    started = _read_json(started_path)
    if _process_alive(started.get("worker_pid")):
        return RUNNING
    return INTERRUPTED


def attempt_identity(attempt_dir: Path) -> tuple[str, str]:
    """The evaluation identity of one attempt, from its own request record."""
    started = _read_json(attempt_dir / "started.json")
    if not started:
        result = _read_json(attempt_dir / "result.json")
        started = result.get("request") or {}
    return (
        str(started.get("cell_id") or ""),
        str(started.get("cost_scenario") or ""),
    )


def collect_attempt_dirs(roots: Iterable[Path]) -> list[Path]:
    """Every attempt directory under the given roots, newest first.

    The attempt directory is the worker's ``cell_execution/<uuid>``; a root may
    be a packet root, a ``cell_attempts/<key>`` directory or a
    ``cell_execution`` directory itself.
    """
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.name == "cell_execution" and root.is_dir():
            found.extend(child for child in sorted(root.iterdir()) if child.is_dir())
            continue
        for candidate in root.rglob("cell_execution/*"):
            if candidate.is_dir():
                found.append(candidate)
    unique = {path: None for path in found}
    return sorted(unique, key=attempt_started_epoch, reverse=True)


def summarise_evaluation(attempt_dirs: list[Path]) -> dict[str, Any]:
    """Summarise ONE evaluation from all of its attempt directories.

    The evaluation's outcome is that of its **latest** attempt, ordered by the
    producer's real start time (epoch, then the producer sequence), never by
    directory name.
    """
    if not attempt_dirs:
        return {"attempts": 0, "outcome": UNKNOWN, "attempt_outcomes": []}
    ordered = sorted(
        set(attempt_dirs),
        key=lambda path: (attempt_started_epoch(path), attempt_sequence(path)),
        reverse=True,
    )
    outcomes = [classify_attempt(path) for path in ordered]
    return {
        "attempts": len(ordered),
        "outcome": outcomes[0],
        "attempt_outcomes": outcomes,
    }


def attempt_roots_for_evaluation(root: Path, evaluation_key: str) -> list[Path]:
    """Every attempt root of ONE evaluation (one cache key).

    The runner creates a fresh ``cell_attempts/<uuid>/`` per attempt, so the
    attempt history of one evaluation is spread over several uuid directories.
    They are matched by the identity recorded in each attempt's
    ``execution_started.json``, whose content hash IS the cache key.
    """
    from trading_agent.backtest.campaign_evidence import content_hash

    attempts_root = root / "cell_attempts"
    if not attempts_root.is_dir():
        return []
    matched: list[Path] = []
    for child in sorted(attempts_root.iterdir()):
        if not child.is_dir():
            continue
        record = _read_json(child / "execution_started.json")
        identity = record.get("identity")
        if not isinstance(identity, dict) or not identity:
            continue
        if content_hash(identity) == evaluation_key:
            matched.append(child)
    return matched


def evaluation_attempt_dirs(root: Path, evaluation_key: str) -> list[Path]:
    """The worker attempt directories of every attempt of one evaluation."""
    dirs: list[Path] = []
    for attempt_root in attempt_roots_for_evaluation(root, evaluation_key):
        dirs.extend(collect_attempt_dirs([attempt_root]))
    return dirs


def write_ledger_row(
    ledger_dir: Path,
    evaluation_key: str,
    identity: dict[str, Any],
    attempt_dirs: list[Path],
    extra: dict[str, Any] | None = None,
) -> Path:
    """Publish a derived snapshot of all unique attempts of one evaluation.

    Durable attempt receipts are authoritative. Rewriting this snapshot (for
    example on a cache hit) must not append the same history a second time.
    Registry accounting remains unknown until separately reconciled.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{evaluation_key}.json"
    summary = summarise_evaluation(attempt_dirs)
    # ``extra`` may add context, but it may never overwrite the accounting
    # fields this module promises to keep unknown.
    merged: dict[str, Any] = dict(extra or {})
    merged.update({
        "identity": identity,
        "evaluation": "cell_id+cost_scenario",
        "evaluation_key": evaluation_key,
        "attempts": summary["attempts"],
        "attempt_outcomes": summary["attempt_outcomes"],
        "outcome": summary["outcome"],
        "ledger_updated_at": datetime.now(timezone.utc).isoformat(),
        "registry_counted": None,
        "registry_accounting_status": "UNKNOWN_NOT_RECONCILED",
        "live_authorized": False,
    })
    # A cache hit may have no local attempt root: the row then has no attempt
    # history, and its outcome must not contradict the artifact it reports.
    if not attempt_dirs:
        artifact_status = merged.get("artifact_status")
        if artifact_status in (COMPLETED, FAILED):
            merged["outcome"] = artifact_status
            merged["attempt_history_complete"] = False
    # The ledger is a replaceable index, unlike immutable attempt receipts.
    # Publish atomically so interruption preserves either the old or new row.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ledger_dir,
            prefix=f".{evaluation_key}.", suffix=".pending", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(merged, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            fd = os.open(ledger_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def record_ledger_error(ledger_dir: Path, evaluation_key: str, error: BaseException) -> Path:
    """Durably record a ledger failure.

    A ledger gap must never disappear silently: the error is appended to an
    append-only error log so a reader can see that the ledger is incomplete.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / "_ledger_errors.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "evaluation_key": evaluation_key,
            "error": f"{type(error).__name__}: {error}",
            "at": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
        }) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path
