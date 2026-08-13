"""Final-holdout guard for research (P2).

Downstream tools (sweeps, backtests, evidence generation) must never train or
select parameters on the frozen holdout window.  This module loads the
immutable ``data/research_manifest.json`` and exposes helpers to:

  * read the holdout window for a symbol/timeframe,
  * reject a requested training window that overlaps the holdout
    (``guard_training_window``).

The manifest is created by ``scripts/generate_holdout_manifest.py``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST = Path("data/research_manifest.json")


class HoldoutError(RuntimeError):
    """Raised when a research window illegally overlaps the frozen holdout."""


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise HoldoutError(
            f"research manifest not found at {manifest_path}; run "
            "scripts/generate_holdout_manifest.py first"
        )
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    body = json.dumps(
        {k: v for k, v in manifest.items() if k != "integrity"},
        sort_keys=True,
        ensure_ascii=False,
    )
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if actual != manifest.get("integrity"):
        raise HoldoutError(
            f"research manifest integrity mismatch at {manifest_path}: "
            "manifest was modified after freezing"
        )
    return manifest


def holdout_window(
    manifest: dict[str, Any] | None = None,
) -> tuple[datetime, datetime]:
    """Return (start, end) of the frozen holdout window (UTC, aware)."""
    manifest = manifest or load_manifest()
    window = manifest["window"]
    start = datetime.fromisoformat(window["start_utc"])
    end = datetime.fromisoformat(window["end_utc"])
    # Normalize: dataset end timestamps are naive epoch-derived values; keep
    # the window timezone-aware so comparisons with caller datetimes always
    # work regardless of which side carries tzinfo.
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return start, end


def guard_training_window(
    *,
    start: datetime,
    end: datetime,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Fail if [start, end] overlaps the frozen holdout.

    Use from sweeps/backtests before any parameter selection so the holdout
    can never influence a research decision.
    """
    manifest = manifest or load_manifest()
    holdout_start, holdout_end = holdout_window(manifest)
    overlap_start = max(start, holdout_start)
    overlap_end = min(end, holdout_end)
    if overlap_start < overlap_end:
        raise HoldoutError(
            f"training window {start.date()} -> {end.date()} overlaps the "
            f"frozen holdout {holdout_start.date()} -> {holdout_end.date()}; "
            "the holdout must never be used for parameter selection"
        )


def fingerprint_datasets(manifest: dict[str, Any]) -> dict[str, str]:
    """Return dataset key -> sha256 from the manifest."""
    return {key: meta["sha256"] for key, meta in manifest.get("datasets", {}).items()}
