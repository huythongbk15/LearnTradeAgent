from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from trading_agent.backtest.campaign_writer import publish_campaign_bundle


def publish(tmp_path, payload):
    return publish_campaign_bundle(
        object(),
        payload,
        tmp_path / "run",
        data_root=tmp_path,
        source_binding={
            "code_commit": "a" * 40,
            "tree_fingerprint": "b" * 64,
        },
    )


def test_invalid_evidence_is_never_published(tmp_path):
    with patch(
        "trading_agent.backtest.campaign_writer.require_wfo_campaign_evidence",
        side_effect=ValueError("invalid measured report"),
    ):
        with pytest.raises(ValueError):
            publish(tmp_path, {"measured": "invalid"})
    assert not (tmp_path / "run" / "campaign_evidence.json").exists()
    assert not list((tmp_path / "run").glob(".campaign-stage-*"))


def test_real_validator_refuses_incomplete_payload(tmp_path):
    with pytest.raises(ValueError):
        publish(tmp_path, {})
    assert not (tmp_path / "run" / "campaign_evidence.json").exists()


def test_atomic_idempotent_publication_and_no_overwrite(tmp_path):
    # Stub only to isolate publication mechanics, not qualification evidence.
    with patch(
        "trading_agent.backtest.campaign_writer.require_wfo_campaign_evidence"
    ) as check:
        target = publish(tmp_path, {"revision": "one"})
        original = target.read_bytes()
        assert publish(tmp_path, {"revision": "one"}) == target
        assert check.call_count == 2
        with pytest.raises(ValueError, match="existing campaign"):
            publish(tmp_path, {"revision": "two"})
        assert target.read_bytes() == original


def test_concurrent_publishers_cannot_replace_winner(tmp_path):
    with patch("trading_agent.backtest.campaign_writer.require_wfo_campaign_evidence"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            paths = list(
                pool.map(lambda _: publish(tmp_path, {"revision": "same"}), range(2))
            )
    assert paths[0] == paths[1]
    assert not list((tmp_path / "run").glob(".campaign-stage-*"))


def test_nan_never_reaches_publication(tmp_path):
    with pytest.raises(ValueError):
        publish(tmp_path, {"metric": float("nan")})
    assert not (tmp_path / "run").exists()
