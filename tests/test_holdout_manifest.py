"""P2: frozen final-holdout manifest and training-window guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from trading_agent.alpha_research.holdout import (
    HoldoutError,
    guard_training_window,
    holdout_window,
    load_manifest,
)


@pytest.fixture()
def manifest(tmp_path):
    """A small manifest with a frozen holdout 2026-01-01 -> 2026-07-01."""
    path = tmp_path / "research_manifest.json"
    body = {
        "schema_version": 1,
        "freeze_date": "2026-07-01T00:00:00+00:00",
        "holdout_months": 6,
        "window": {
            "start_utc": "2026-01-01T00:00:00+00:00",
            "end_utc": "2026-07-01T00:00:00+00:00",
        },
        "datasets": {
            "BTC_USDT/1h": {"sha256": "a" * 64, "size_bytes": 1},
            "SOL_USDT/1h": {"sha256": "b" * 64, "size_bytes": 2},
        },
    }
    import hashlib

    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    body["integrity"] = digest
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_load_manifest_and_window(manifest):
    loaded = load_manifest(manifest)
    start, end = holdout_window(loaded)
    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2026, 7, 1, tzinfo=UTC)


def test_manifest_integrity_mismatch_is_rejected(manifest):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["window"]["end_utc"] = "2026-06-01T00:00:00+00:00"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(HoldoutError, match="integrity mismatch"):
        load_manifest(manifest)


def test_training_window_inside_holdout_is_blocked(manifest):
    loaded = load_manifest(manifest)
    with pytest.raises(HoldoutError, match="overlaps"):
        guard_training_window(
            start=datetime(2026, 2, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
            manifest=loaded,
        )


def test_training_window_partially_overlapping_is_blocked(manifest):
    loaded = load_manifest(manifest)
    with pytest.raises(HoldoutError, match="overlaps"):
        guard_training_window(
            start=datetime(2025, 12, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, tzinfo=UTC),
            manifest=loaded,
        )


def test_training_window_before_holdout_is_allowed(manifest):
    loaded = load_manifest(manifest)
    guard_training_window(
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 12, 31, tzinfo=UTC),
        manifest=loaded,
    )


def test_missing_manifest_fails_closed(tmp_path):
    with pytest.raises(HoldoutError, match="not found"):
        load_manifest(tmp_path / "nope.json")
