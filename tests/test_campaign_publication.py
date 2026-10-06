"""Unit tests for the atomic publication step.

publish_campaign_bundle must only write a bundle whose verdict is PASS, must
write the exact bytes it staged (no rewrite in flight), and must never replace
a differing pre-existing artifact -- that would be the publication silently
erasing evidence. These exercise the publisher directly, with the validator
stubbed, so they prove the publish contract independently of a full WFO run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.backtest import campaign_writer as cw


def _passing_bundle() -> dict:
    return {
        "schema_version": 2,
        "subject": {"strategy_id": "s", "symbol": "BTC/USDT", "market_type": "spot", "timeframe": "1h"},
        "verdict": "PASS",
        "code_commit": "a" * 40,
        "tree_fingerprint": "b" * 64,
        "worktree_dirty": False,
        "dirty_diff_sha256": None,
        "plan_fingerprint": "c" * 64,
        "campaign_id": "d" * 64,
        "aggregate": {"median_sharpe": 1.2, "median_return_pct": 3.0, "total_trades": 90},
        "failed_gates": [],
    }


def test_publish_writes_exact_bytes_when_verdict_passes(monkeypatch, tmp_path):
    bundle = _passing_bundle()
    captured = {}
    monkeypatch.setattr(
        cw,
        "require_wfo_campaign_evidence",
        lambda result, out_root, *, data_root, source_binding: captured.setdefault(
            "called", True
        ),
    )

    target = cw.publish_campaign_bundle(
        result=object(),
        bundle=bundle,
        out_root=tmp_path,
        data_root=tmp_path,
        source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64},
    )

    assert captured.get("called") is True
    assert target == tmp_path / "campaign_evidence.json"
    written = json.loads(target.read_text())
    assert written == bundle  # exact bytes, no rewrite during publication
    assert written["verdict"] == "PASS"


def test_publish_refuses_a_fail_verdict(monkeypatch, tmp_path):
    bundle = {**_passing_bundle(), "verdict": "FAIL"}
    monkeypatch.setattr(
        cw,
        "require_wfo_campaign_evidence",
        lambda result, out_root, *, data_root, source_binding: (_ for _ in ()).throw(
            ValueError("campaign is measured but NOT_QUALIFIED")
        ),
    )
    with pytest.raises(ValueError, match="NOT_QUALIFIED"):
        cw.publish_campaign_bundle(
            result=object(),
            bundle=bundle,
            out_root=tmp_path,
            data_root=tmp_path,
            source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64},
        )
    assert not (tmp_path / "campaign_evidence.json").exists()


def test_publish_is_idempotent_for_identical_bytes(monkeypatch, tmp_path):
    # Re-publishing the same bundle must not fail, so a flaky runner can
    # retry publication without losing evidence it already owns.
    bundle = _passing_bundle()
    monkeypatch.setattr(
        cw,
        "require_wfo_campaign_evidence",
        lambda result, out_root, *, data_root, source_binding: None,
    )
    first = cw.publish_campaign_bundle(
        object(), bundle, tmp_path, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
    )
    second = cw.publish_campaign_bundle(
        object(), bundle, tmp_path, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
    )
    assert second == first
    assert (tmp_path / "campaign_evidence.json").read_bytes().count(b"schema_version") == 1


def test_publish_refuses_to_overwrite_differing_evidence(monkeypatch, tmp_path):
    # Different evidence must not be replaced: that is the publication
    # silently erasing evidence, which is exactly what the atomic link refused.
    bundle = _passing_bundle()
    monkeypatch.setattr(
        cw,
        "require_wfo_campaign_evidence",
        lambda result, out_root, *, data_root, source_binding: None,
    )
    cw.publish_campaign_bundle(
        object(), bundle, tmp_path, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
    )
    other = {**bundle, "campaign_id": "e" * 64}
    with pytest.raises(ValueError, match="differs"):
        cw.publish_campaign_bundle(
            object(), other, tmp_path, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
        )


def test_publish_refuses_symlink_artifact(monkeypatch, tmp_path):
    bundle = _passing_bundle()
    monkeypatch.setattr(cw, "require_wfo_campaign_evidence", lambda *a, **k: None)
    existing = tmp_path / "campaign_evidence.json"
    existing.symlink_to(tmp_path / "phantom.json")
    with pytest.raises(ValueError, match="symlink"):
        cw.publish_campaign_bundle(
            object(), bundle, tmp_path, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
        )


def test_publish_refuses_output_outside_publication_root(monkeypatch, tmp_path):
    bundle = _passing_bundle()
    monkeypatch.setattr(cw, "require_wfo_campaign_evidence", lambda *a, **k: None)
    escape = Path("/tmp") / "campaign_escape_test"
    escape.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="escapes"):
        cw.publish_campaign_bundle(
            object(), bundle, escape, data_root=tmp_path, source_binding={"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
        )