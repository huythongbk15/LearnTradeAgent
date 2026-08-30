"""S4 selection-policy lifecycle, signing and WFO bridge tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from types import SimpleNamespace

import pytest

from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyActivationService,
    PolicySignatureEnvelope,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyBuilder,
    SelectionPolicyRegistry,
)


NOW = datetime(2026, 8, 30, tzinfo=UTC)
KEY = b"unit-test-policy-signing-key"


def _validated(
    strategy_id: str = "rsi",
    *,
    now: datetime = NOW,
    params: dict | None = None,
) -> SelectionPolicyArtifact:
    return SelectionPolicyArtifact(
        symbol="BTC/USDT",
        timeframe="1h",
        regime="trend",
        incumbent=ParamArtifact(
            strategy_id, params or {"period": 14}, code_sha="e" * 64
        ),
        scores={"selection_score": 1.2},
        evidence_ids=("sha256:study", "sha256:outer", "sha256:holdout"),
        validity_start=now,
        validity_end=now + timedelta(days=30),
        status=PolicyStatus.VALIDATED,
        created_at=now,
        policy_commit_sha="a" * 40,
        policy_data_manifest_sha="b" * 64,
        policy_feature_manifest_sha="c" * 64,
        policy_release_digest="sha256:" + "d" * 64,
        promotion_stage="paper_eligible",
    )


def test_lifecycle_transition_creates_new_immutable_policy_id():
    validated = _validated()
    active = validated.activate("operator", "TICKET-1", NOW + timedelta(minutes=1))

    assert validated.policy_id != active.policy_id
    assert validated.status is PolicyStatus.VALIDATED
    assert active.status is PolicyStatus.ACTIVE
    assert active.activated_by == "operator"


def test_active_policy_requires_evidence_provenance_and_approval():
    with pytest.raises(ValueError, match="evidence_ids"):
        SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="trend",
            incumbent=ParamArtifact("rsi", {}),
            status=PolicyStatus.ACTIVE,
            activated_at=NOW,
            activated_by="operator",
            activation_ticket="TICKET-1",
        )

    with pytest.raises(ValueError, match="only a validated policy"):
        SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="trend",
            incumbent=ParamArtifact("rsi", {}),
            validity_start=NOW,
            created_at=NOW,
        ).activate("operator", "TICKET-1", NOW)


def test_detached_signature_detects_policy_or_signature_tampering():
    policy = _validated()
    envelope = PolicySignatureEnvelope.sign(policy, key=KEY, key_id="release-key")

    assert envelope.verify(policy, key=KEY)
    assert not envelope.verify(policy, key=b"wrong-key")
    tampered = _validated(params={"period": 21})
    assert not envelope.verify(tampered, key=KEY)


def test_registry_is_append_only_and_rejects_tampered_policy(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path)
    policy = _validated()
    registry.add(policy)
    registry.add(policy)
    path = tmp_path / f"{policy.policy_id}.json"
    payload = json.loads(path.read_text())
    payload["risk_cap"] = 0.9
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="content tampered"):
        registry.get(policy.policy_id)


def test_activation_requires_compare_and_swap_and_verified_signature(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    validated = _validated()
    registry.add(validated)
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "activation.jsonl",
    )

    active = service.activate(
        validated.policy_id,
        actor="operator",
        ticket="TICKET-1",
        now=NOW + timedelta(minutes=1),
        expected_previous_policy_id=None,
    )

    assert registry.get_active_verified(
        "BTC/USDT",
        "1h",
        "trend",
        key=KEY,
        key_id="release-key",
        now=NOW + timedelta(minutes=2),
    ) == active
    assert registry.get_active_verified(
        "BTC/USDT",
        "1h",
        "trend",
        key=b"wrong",
        key_id="release-key",
        now=NOW + timedelta(minutes=2),
    ) is None
    with pytest.raises(ValueError, match="active policy changed"):
        service.activate(
            validated.policy_id,
            actor="operator",
            ticket="TICKET-2",
            now=NOW + timedelta(minutes=3),
            expected_previous_policy_id=None,
        )


def test_activation_cannot_skip_canonical_promotion_ladder(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    policy = replace(_validated(), promotion_stage="research_validated")
    registry.add(policy)
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "activation.jsonl",
    )
    with pytest.raises(ValueError, match="paper_eligible"):
        service.activate(
            policy.policy_id,
            actor="operator",
            ticket="TICKET-SKIP",
            now=NOW + timedelta(minutes=1),
        )


def test_stage_bridge_advances_one_step_then_allows_activation(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    policy = replace(_validated(), promotion_stage="research_validated")
    registry.add(policy)
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "activation.jsonl",
    )
    paper = service.advance_stage(
        policy.policy_id,
        to_stage="paper_eligible",
        actor="research-gate",
        ticket="GATE-1",
        evidence_ids=("sha256:paper-gate",),
        now=NOW + timedelta(minutes=1),
    )
    active = service.activate(
        paper.policy_id,
        actor="operator",
        ticket="TICKET-ACTIVATE",
        now=NOW + timedelta(minutes=2),
    )
    assert paper.policy_id != policy.policy_id
    assert active.promotion_stage == "paper_eligible"


def test_rollback_activates_new_copy_of_previous_known_good_policy(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "activation.jsonl",
    )
    first = _validated("rsi", now=NOW)
    registry.add(first)
    first_active = service.activate(
        first.policy_id,
        actor="operator",
        ticket="TICKET-1",
        now=NOW + timedelta(minutes=1),
    )
    second = _validated(
        "bbands", now=NOW + timedelta(minutes=2), params={"period": 20}
    )
    registry.add(second)
    second_active = service.activate(
        second.policy_id,
        actor="operator",
        ticket="TICKET-2",
        now=NOW + timedelta(minutes=3),
        expected_previous_policy_id=first_active.policy_id,
    )

    restored = service.rollback(
        symbol="BTC/USDT",
        timeframe="1h",
        regime="trend",
        previous_policy_id=first_active.policy_id,
        actor="operator",
        ticket="TICKET-3",
        reason="challenger regression",
        now=NOW + timedelta(minutes=4),
    )

    assert restored.incumbent.strategy_id == "rsi"
    assert restored.previous_policy_id == second_active.policy_id
    assert restored.policy_id not in {first_active.policy_id, second_active.policy_id}
    assert len(registry.get_lineage(restored.policy_id)) >= 3


def test_wfo_builder_rejects_non_promotable_and_builds_validated_policy():
    manifest = SimpleNamespace(
        manifest_id="sha256:study",
        provenance_eligible=True,
        commit_sha="a" * 40,
        data_manifest_sha="b" * 64,
        feature_schema_hash="c" * 64,
        strategy_code_sha="e" * 64,
    )
    artifact = SimpleNamespace(artifact_id="sha256:outer")
    result = SimpleNamespace(
        spec=SimpleNamespace(
            strategy_id="rsi", symbol="BTC/USDT", timeframe="1h"
        ),
        passes_hard_gates=True,
        aggregate_metrics={
            "promotable": True,
            "median_test_sharpe": 1.1,
            "median_test_return_pct": 4.0,
            "positive_outer_folds_pct": 75.0,
        },
        final_holdout={"status": "COMPLETED", "holdout_id": "sha256:holdout"},
        study_manifest=manifest,
        outer_results=[
            SimpleNamespace(params={"period": 14, "cost_scenario": "1x"}, artifact=artifact)
        ],
    )

    policy = SelectionPolicyBuilder.from_wfo_result(
        result,
        regime="trend",
        release_digest="sha256:" + "d" * 64,
        now=NOW,
    )
    assert policy.status is PolicyStatus.VALIDATED
    assert policy.incumbent.params == {"period": 14}
    assert len(policy.evidence_ids) == 3

    result.aggregate_metrics["promotable"] = False
    with pytest.raises(ValueError, match="not promotion-eligible"):
        SelectionPolicyBuilder.from_wfo_result(
            result,
            regime="trend",
            release_digest="sha256:" + "d" * 64,
            now=NOW,
        )
