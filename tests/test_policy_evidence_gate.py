"""The measured-evidence gate on policy promotion.

POLICY_RETURN_AUDIT.md found 2690 promoted policies whose scores were
literal constants written by generator scripts (three distinct return
values across the whole store). These tests pin the gate that makes that
unrepresentable: a policy cannot reach VALIDATED or ACTIVE without the OOS
metric family that its selection_score is supposed to summarise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)

MEASURED = {
    "selection_score": 1.2,
    "median_oos_return_pct": 6.5,
    "median_oos_trades": 30,
    "n_passing_folds": 9,
    "total_folds": 9,
}


def _policy(**overrides):
    kwargs = {
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "regime": "trend",
        "incumbent": ParamArtifact("rsi", {"period": 14}, code_sha="e" * 64),
        "scores": dict(MEASURED),
        "evidence_ids": ("sha256:study", "sha256:outer", "sha256:holdout"),
        "validity_start": NOW,
        "validity_end": NOW + timedelta(days=30),
        "status": PolicyStatus.VALIDATED,
        "created_at": NOW,
        "policy_commit_sha": "a" * 40,
        "policy_data_manifest_sha": "b" * 64,
        "policy_feature_manifest_sha": "c" * 64,
        "policy_release_digest": "sha256:" + "d" * 64,
        "promotion_stage": "paper_eligible",
    }
    kwargs.update(overrides)
    return SelectionPolicyArtifact(**kwargs)


def test_measured_policy_validates():
    assert _policy().status is PolicyStatus.VALIDATED


def test_selection_score_without_metrics_is_rejected():
    # This is the exact shape the generators wrote: a score and nothing else.
    with pytest.raises(ValueError, match="requires measured OOS metrics"):
        _policy(scores={"selection_score": 0.5})


def test_empty_scores_rejected_for_validated_status():
    with pytest.raises(ValueError, match="requires measured scores"):
        _policy(scores={})


def test_empty_scores_allowed_in_draft():
    # An unevaluated artifact is representable; it just cannot be promoted.
    draft = _policy(scores={}, status=PolicyStatus.DRAFT,
                    promotion_stage="exploratory")
    assert draft.status is PolicyStatus.DRAFT
    assert draft.scores == {}


@pytest.mark.parametrize(
    "missing", ["median_oos_return_pct", "median_oos_trades", "n_passing_folds", "total_folds"]
)
def test_each_metric_is_required(missing: str):
    scores = {k: v for k, v in MEASURED.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        _policy(scores=scores)


def test_fold_counts_must_be_consistent():
    with pytest.raises(ValueError, match="fold counts are inconsistent"):
        _policy(scores={**MEASURED, "n_passing_folds": 12, "total_folds": 9})


def test_more_passing_than_total_is_rejected():
    with pytest.raises(ValueError, match="fold counts are inconsistent"):
        _policy(scores={**MEASURED, "n_passing_folds": -1})


# ── stored artifacts ──────────────────────────────────────────────────────
# Stored JSON bypasses __post_init__, so the 2690 pre-existing policies with
# fabricated scores still load through get(). require_trusted() is the
# boundary that refuses them, and activate() goes through it.


def _store_legacy_fabricated(reg, policy: SelectionPolicyArtifact) -> str:
    """Write the on-disk shape left by the pre-gate generators.

    The policy id is content-addressed over the *draft* artifact, then the
    stored JSON is edited to look promoted. Loading it back re-runs
    __post_init__, which is the point: a legacy artifact with fabricated
    scores cannot be reconstructed as a promotable object at all.
    """
    import json

    payload = json.loads(policy.to_json())
    payload["status"] = "active"
    payload["activated_at"] = NOW.isoformat()
    payload["activated_by"] = "test"
    payload["activation_ticket"] = "T-1"
    payload["scores"] = {"selection_score": 0.5}
    payload["promotion_stage"] = "paper_eligible"
    path = reg._policy_path(policy.policy_id)
    path.write_text(json.dumps(payload))
    return policy.policy_id


def test_legacy_fabricated_policy_cannot_be_loaded(tmp_path):
    from trading_agent.research.selection_policy import SelectionPolicyRegistry

    reg = SelectionPolicyRegistry(tmp_path)
    draft = _policy(
        scores={}, status=PolicyStatus.DRAFT, promotion_stage="exploratory"
    )
    policy_id = _store_legacy_fabricated(reg, draft)

    # Reading it back raises, because __post_init__ re-applies the gate.
    with pytest.raises(ValueError, match="measured"):
        reg.get(policy_id)

    with pytest.raises(ValueError, match="measured"):
        reg.require_trusted(policy_id)


def test_activation_refuses_legacy_fabricated_policy(tmp_path):
    from trading_agent.research.selection_policy import (
        PolicyActivationService,
        SelectionPolicyRegistry,
    )

    reg = SelectionPolicyRegistry(tmp_path)
    draft = _policy(
        scores={}, status=PolicyStatus.DRAFT, promotion_stage="exploratory"
    )
    policy_id = _store_legacy_fabricated(reg, draft)

    svc = PolicyActivationService(
        reg, signing_key=b"k" * 32, key_id="kid", audit_path=tmp_path / "a.jsonl"
    )
    with pytest.raises(ValueError, match="measured"):
        svc.activate(policy_id, actor="t", ticket="T", now=NOW)


def test_measured_stored_policy_activates(tmp_path):
    from trading_agent.research.selection_policy import (
        PolicyActivationService,
        SelectionPolicyRegistry,
    )

    reg = SelectionPolicyRegistry(tmp_path)
    validated = _policy()
    reg.add(validated)

    svc = PolicyActivationService(
        reg, signing_key=b"k" * 32, key_id="kid", audit_path=tmp_path / "a.jsonl"
    )
    active = svc.activate(validated.policy_id, actor="t", ticket="T", now=NOW)
    assert active.status is PolicyStatus.ACTIVE
