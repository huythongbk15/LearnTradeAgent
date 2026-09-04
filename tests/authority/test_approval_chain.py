"""Tests for the multi-party approval chain (S7 gate)."""

import pytest

from trading_agent.authority.approval import (
    Approval,
    ApprovalChain,
    ApprovalDecision,
    ApprovalRole,
    ApprovalStore,
    DuplicateApprovalError,
    PromotionStage,
    make_unsigned_approval,
)
from trading_agent.log_config import get_logger

logger = get_logger(__name__)


@pytest.fixture
def store(tmp_path):
    """Fresh approval store per test (function-scoped tmp_path)."""
    return ApprovalStore(db_path=tmp_path / "approvals.sqlite3")


@pytest.fixture
def chain(store):
    return ApprovalChain(store=store, approval_window_hours=24)


def _approval(
    role: ApprovalRole,
    approver: str,
    stage: PromotionStage = PromotionStage.SHADOW,
    decision: ApprovalDecision = ApprovalDecision.APPROVED,
) -> Approval:
    """Helper to build an approval with a unique artifact per test."""
    import uuid

    artifact = f"sha256:{uuid.uuid4().hex}"
    return make_unsigned_approval(
        role=role.value,
        approver_id=approver,
        artifact_id=artifact,
        stage=stage.value,
        decision=decision.value,
        reason="test",
    )


class TestApprovalStore:
    def test_record_and_list(self, store):
        a = _approval(ApprovalRole.RESEARCH, "alice")
        store.record(a)
        approvals = store.list_for_artifact(a.artifact_id)
        assert len(approvals) == 1
        assert approvals[0].role == "research"
        assert approvals[0].approver_id == "alice"

    def test_duplicate_raises(self, store):
        a = _approval(ApprovalRole.RESEARCH, "alice")
        store.record(a)
        with pytest.raises(DuplicateApprovalError):
            store.record(a)

    def test_multiple_approvals(self, store):
        a1 = _approval(ApprovalRole.RESEARCH, "alice")
        a2 = _approval(ApprovalRole.RISK, "bob")
        a3 = _approval(ApprovalRole.COMPLIANCE, "carol")
        # Use same artifact
        for orig, new_role, new_app in [
            (a1, ApprovalRole.RESEARCH, "alice"),
            (a2, ApprovalRole.RISK, "bob"),
            (a3, ApprovalRole.COMPLIANCE, "carol"),
        ]:
            approval = make_unsigned_approval(
                role=new_role.value,
                approver_id=new_app,
                artifact_id=a1.artifact_id,
                stage=PromotionStage.TESTNET.value,
                decision=ApprovalDecision.APPROVED.value,
                reason="test",
            )
            store.record(approval)
        approvals = store.list_for_artifact(a1.artifact_id)
        assert len(approvals) == 3


class TestApprovalChain:
    def test_shadow_requires_only_research(self, chain):
        a = _approval(ApprovalRole.RESEARCH, "alice", stage=PromotionStage.SHADOW)
        chain.store.record(a)
        result = chain.evaluate(a.artifact_id, PromotionStage.SHADOW)
        assert result["passed"] is True
        assert result["missing_roles"] == set()

    def test_testnet_requires_three_roles(self, chain):
        a1 = _approval(ApprovalRole.RESEARCH, "alice", stage=PromotionStage.TESTNET)
        a2 = _approval(ApprovalRole.RISK, "bob", stage=PromotionStage.TESTNET)
        chain.store.record(a1)
        chain.store.record(a2)
        result = chain.evaluate(a1.artifact_id, PromotionStage.TESTNET)
        assert result["passed"] is False
        assert "compliance" in result["missing_roles"]

    def test_testnet_full_approval_passes(self, chain):
        artifact = f"sha256:{__import__('uuid').uuid4().hex}"
        for role, approver in [
            (ApprovalRole.RESEARCH, "alice"),
            (ApprovalRole.RISK, "bob"),
            (ApprovalRole.COMPLIANCE, "carol"),
        ]:
            approval = make_unsigned_approval(
                role=role.value,
                approver_id=approver,
                artifact_id=artifact,
                stage=PromotionStage.TESTNET.value,
                decision=ApprovalDecision.APPROVED.value,
                reason="test",
            )
            chain.store.record(approval)
        result = chain.evaluate(artifact, PromotionStage.TESTNET)
        assert result["passed"] is True
        assert result["approved_roles"] == {"research", "risk", "compliance"}

    def test_production_requires_all_five(self, chain):
        artifact = f"sha256:{__import__('uuid').uuid4().hex}"
        for role, approver in [
            (ApprovalRole.RESEARCH, "alice"),
            (ApprovalRole.RISK, "bob"),
            (ApprovalRole.COMPLIANCE, "carol"),
            (ApprovalRole.OPERATOR, "dave"),
        ]:
            approval = make_unsigned_approval(
                role=role.value,
                approver_id=approver,
                artifact_id=artifact,
                stage=PromotionStage.PRODUCTION.value,
                decision=ApprovalDecision.APPROVED.value,
                reason="test",
            )
            chain.store.record(approval)
        result = chain.evaluate(artifact, PromotionStage.PRODUCTION)
        assert result["passed"] is False
        assert "admin" in result["missing_roles"]

    def test_rejection_blocks_promotion(self, chain):
        artifact = f"sha256:{__import__('uuid').uuid4().hex}"
        for role, approver, decision in [
            (ApprovalRole.RESEARCH, "alice", ApprovalDecision.APPROVED),
            (ApprovalRole.RISK, "bob", ApprovalDecision.REJECTED),
            (ApprovalRole.COMPLIANCE, "carol", ApprovalDecision.APPROVED),
        ]:
            approval = make_unsigned_approval(
                role=role.value,
                approver_id=approver,
                artifact_id=artifact,
                stage=PromotionStage.TESTNET.value,
                decision=decision.value,
                reason="test",
            )
            chain.store.record(approval)
        result = chain.evaluate(artifact, PromotionStage.TESTNET)
        assert result["passed"] is False
        assert "risk" in result["rejected_roles"]

    def test_role_separation_violation(self, chain):
        artifact = f"sha256:{__import__('uuid').uuid4().hex}"
        for role, approver in [
            (ApprovalRole.RESEARCH, "alice"),
            (ApprovalRole.RISK, "alice"),  # Same approver, different role
            (ApprovalRole.COMPLIANCE, "carol"),
        ]:
            approval = make_unsigned_approval(
                role=role.value,
                approver_id=approver,
                artifact_id=artifact,
                stage=PromotionStage.TESTNET.value,
                decision=ApprovalDecision.APPROVED.value,
                reason="test",
            )
            chain.store.record(approval)
        result = chain.evaluate(artifact, PromotionStage.TESTNET)
        assert result["passed"] is False
        assert any("separation" in r.lower() for r in result["reasons"])

    def test_expired_approvals_excluded(self, tmp_path):
        store = ApprovalStore(db_path=tmp_path / "expired.db")
        chain = ApprovalChain(store=store, approval_window_hours=1)
        artifact = f"sha256:{__import__('uuid').uuid4().hex}"
        old_approval = make_unsigned_approval(
            role="research",
            approver_id="alice",
            artifact_id=artifact,
            stage="shadow",
            decision="approved",
            reason="old",
        )
        # Override timestamp to be very old
        old_approval = Approval(
            role=old_approval.role,
            approver_id=old_approval.approver_id,
            artifact_id=old_approval.artifact_id,
            stage=old_approval.stage,
            decision=old_approval.decision,
            reason=old_approval.reason,
            signature=old_approval.signature,
            timestamp="2020-01-01T00:00:00+00:00",
            metadata=old_approval.metadata,
        )
        store.record(old_approval)
        result = chain.evaluate(artifact, PromotionStage.SHADOW)
        assert result["passed"] is False
        assert "research" in result["missing_roles"]


class TestApprovalStages:
    def test_stage_required_roles_mapping(self):
        from trading_agent.authority.approval import _STAGE_REQUIRED_ROLES

        assert ApprovalRole.RESEARCH in _STAGE_REQUIRED_ROLES[PromotionStage.SHADOW]
        assert len(_STAGE_REQUIRED_ROLES[PromotionStage.PRODUCTION]) == 5
        assert len(_STAGE_REQUIRED_ROLES[PromotionStage.CANARY]) == 4


class TestApprovalMessage:
    def test_message_format(self):
        a = make_unsigned_approval(
            role="research",
            approver_id="alice",
            artifact_id="sha256:msg1",
            stage="shadow",
        )
        msg = a.message()
        assert msg.startswith("research|alice|sha256:msg1|shadow|approved|")

    def test_signature_hash_deterministic(self):
        a1 = make_unsigned_approval(
            role="research",
            approver_id="alice",
            artifact_id="sha256:msg2",
            stage="shadow",
        )
        a2 = make_unsigned_approval(
            role="research",
            approver_id="alice",
            artifact_id="sha256:msg2",
            stage="shadow",
        )
        assert a1.signature_hash() == a2.signature_hash()
