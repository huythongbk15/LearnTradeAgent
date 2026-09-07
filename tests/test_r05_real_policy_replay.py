"""R05 — Real policy and replay clock tests.

Verifies that:
1. EventClock rejects wall_time < event_time (no future events).
2. LineageRecord rejects inconsistent fit/permission ordering.
3. RealPolicyResolver rejects:
   - Synthetic bundles when require_real=True
   - Synthetic bundles accepted when require_real=False
   - Expired policies (validity_end < event_time)
   - Not-yet-valid policies (validity_start > event_time)
   - Policies whose training_data_cutoff is after event_time (future data)
   - Policies whose permitted_at is after event_time (not yet permitted)
   - Tampered policies (lineage.policy_id mismatch)
   - Missing policies
4. build_lineage_from_policy uses activated_at when available.
5. attach_lineage_to_bundle produces a new bundle (immutable update).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.r05,
]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.research.policy_resolver import (
    EventClock,
    ExpiredPolicyError,
    FutureTrainingDataError,
    LineageRecord,
    MissingPolicyError,
    NotYetValidPolicyError,
    PolicyBundle,
    PolicyConsumerError,
    RealPolicyResolver,
    SyntheticPolicyRejectedError,
    attach_lineage_to_bundle,
    build_lineage_from_policy,
    lineage_digest,
    reject_synthetic_for_real,
    verify_bundle_integrity,
)
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_policy(
    *,
    policy_id: str = "abc123def456",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    regime: str = "TRENDING_UP",
    validity_start: datetime | None = None,
    validity_end: datetime | None = None,
    status: PolicyStatus = PolicyStatus.ACTIVE,
) -> SelectionPolicyArtifact:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return SelectionPolicyArtifact(
        symbol=symbol,
        timeframe=timeframe,
        regime=regime,
        incumbent=ParamArtifact(
            strategy_id="rsi",
            params={"period": 14, "oversold": 30, "overbought": 70},
            code_sha="e" * 64,
        ),
        scores={"selection_score": 1.2},
        evidence_ids=("sha256:study", "sha256:outer", "sha256:holdout"),
        validity_start=validity_start or now,
        validity_end=validity_end,
        status=status,
        created_at=now,
        activated_at=now,
        activated_by="test",
        activation_ticket="R05-test",
        policy_commit_sha="a" * 40,
        policy_data_manifest_sha="b" * 64,
        policy_feature_manifest_sha="c" * 64,
        policy_release_digest="sha256:" + "d" * 64,
        promotion_stage="paper_eligible",
    )


def _make_lineage(
    *,
    policy_id: str = "abc123def456",
    training_data_cutoff: datetime | None = None,
    fit_at: datetime | None = None,
    permitted_at: datetime | None = None,
    source_hash: str = "sha256:source1234567890",
    actor: str = "research_system",
    ticket: str = "R04-campaign",
) -> LineageRecord:
    base = datetime(2025, 12, 1, tzinfo=UTC)
    return LineageRecord(
        policy_id=policy_id,
        training_data_cutoff=training_data_cutoff or base,
        fit_at=fit_at or (base + timedelta(days=10)),
        permitted_at=permitted_at or (base + timedelta(days=20)),
        source_hash=source_hash,
        actor=actor,
        ticket=ticket,
    )


def _make_bundle(
    *,
    policy: SelectionPolicyArtifact | None = None,
    lineage: LineageRecord | None = None,
    evidence_class: str = "REAL_MARKET",
) -> PolicyBundle:
    actual_policy = policy or _make_policy()
    if lineage is None:
        # Default: derive lineage.policy_id from the actual policy
        lineage = _make_lineage(policy_id=actual_policy.policy_id)
    return PolicyBundle(
        policy=actual_policy,
        lineage=lineage,
        evidence_class=evidence_class,
    )


# =============================================================================
# Test 1: EventClock
# =============================================================================


class TestEventClock:
    """R05.1: EventClock enforces wall_time >= event_time."""

    def test_naive_event_time_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            EventClock(event_time=datetime(2026, 1, 1))  # noqa: DTZ001

    def test_naive_wall_time_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            EventClock(
                event_time=datetime(2026, 1, 1, tzinfo=UTC),
                wall_time=datetime(2026, 1, 1),  # noqa: DTZ001
            )

    def test_wall_before_event_rejected(self):
        with pytest.raises(ValueError, match="before"):
            EventClock(
                event_time=datetime(2026, 1, 2, tzinfo=UTC),
                wall_time=datetime(2026, 1, 1, tzinfo=UTC),
            )

    def test_wall_equal_event_ok(self):
        EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 1, tzinfo=UTC),
        )

    def test_to_dict(self):
        clock = EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 2, tzinfo=UTC),
        )
        d = clock.to_dict()
        assert "2026-01-01" in d["event_time"]
        assert "2026-01-02" in d["wall_time"]


# =============================================================================
# Test 2: LineageRecord
# =============================================================================


class TestLineageRecord:
    """R05.2: LineageRecord enforces training < fit < permitted ordering."""

    def test_valid_lineage(self):
        lineage = _make_lineage()
        assert lineage.training_data_cutoff < lineage.fit_at < lineage.permitted_at

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            LineageRecord(
                policy_id="x",
                training_data_cutoff=datetime(2025, 1, 1),  # noqa: DTZ001
                fit_at=datetime(2025, 2, 1, tzinfo=UTC),
                permitted_at=datetime(2025, 3, 1, tzinfo=UTC),
                source_hash="sha256:abc",
            )

    def test_fit_before_training_rejected(self):
        with pytest.raises(ValueError, match="fit_at"):
            LineageRecord(
                policy_id="x",
                training_data_cutoff=datetime(2026, 1, 2, tzinfo=UTC),
                fit_at=datetime(2026, 1, 1, tzinfo=UTC),
                permitted_at=datetime(2026, 1, 3, tzinfo=UTC),
                source_hash="sha256:abc",
            )

    def test_permitted_before_fit_rejected(self):
        with pytest.raises(ValueError, match="permitted_at"):
            LineageRecord(
                policy_id="x",
                training_data_cutoff=datetime(2025, 1, 1, tzinfo=UTC),
                fit_at=datetime(2025, 2, 2, tzinfo=UTC),
                permitted_at=datetime(2025, 2, 1, tzinfo=UTC),
                source_hash="sha256:abc",
            )

    def test_source_hash_required(self):
        with pytest.raises(ValueError, match="source_hash"):
            LineageRecord(
                policy_id="x",
                training_data_cutoff=datetime(2025, 1, 1, tzinfo=UTC),
                fit_at=datetime(2025, 2, 1, tzinfo=UTC),
                permitted_at=datetime(2025, 3, 1, tzinfo=UTC),
                source_hash="",
            )

    def test_source_hash_too_short(self):
        with pytest.raises(ValueError, match="source_hash"):
            LineageRecord(
                policy_id="x",
                training_data_cutoff=datetime(2025, 1, 1, tzinfo=UTC),
                fit_at=datetime(2025, 2, 1, tzinfo=UTC),
                permitted_at=datetime(2025, 3, 1, tzinfo=UTC),
                source_hash="abc",
            )

    def test_is_consistent_with_event_time(self):
        lineage = _make_lineage()
        # event_time = 2026-01-01 (after cutoff and permitted_at)
        event_time = datetime(2026, 1, 1, tzinfo=UTC)
        assert lineage.is_consistent_with_event_time(event_time)
        # event_time before training_data_cutoff
        event_time_early = lineage.training_data_cutoff - timedelta(days=1)
        assert not lineage.is_consistent_with_event_time(event_time_early)
        # event_time before permitted_at
        event_time_between = lineage.permitted_at - timedelta(days=1)
        assert not lineage.is_consistent_with_event_time(event_time_between)

    def test_to_dict(self):
        lineage = _make_lineage()
        d = lineage.to_dict()
        assert d["policy_id"] == lineage.policy_id
        assert d["actor"] == lineage.actor
        assert d["ticket"] == lineage.ticket
        assert "2025" in d["training_data_cutoff"]

    def test_lineage_digest_stable(self):
        lineage = _make_lineage()
        d1 = lineage_digest(lineage)
        d2 = lineage_digest(lineage)
        assert d1 == d2
        assert d1.startswith("sha256:")

    def test_lineage_digest_changes_with_source_hash(self):
        lineage1 = _make_lineage(source_hash="sha256:sourceAAAAAAAA")
        lineage2 = _make_lineage(source_hash="sha256:sourceBBBBBBBB")
        assert lineage_digest(lineage1) != lineage_digest(lineage2)


# =============================================================================
# Test 3: PolicyBundle
# =============================================================================


class TestPolicyBundle:
    """R05.3: PolicyBundle is content-addressed and validates policy_id."""

    def test_invalid_evidence_class_rejected(self):
        with pytest.raises(ValueError, match="evidence_class"):
            PolicyBundle(
                policy=_make_policy(),
                lineage=_make_lineage(),
                evidence_class="INVALID",
            )

    def test_policy_id_mismatch_rejected(self):
        policy = _make_policy()
        wrong_lineage = _make_lineage(policy_id="00000000different")
        with pytest.raises(ValueError, match="does not match"):
            PolicyBundle(policy=policy, lineage=wrong_lineage)

    def test_to_dict(self):
        bundle = _make_bundle()
        d = bundle.to_dict()
        assert d["policy_id"] == bundle.policy.policy_id
        assert d["evidence_class"] == "REAL_MARKET"
        assert "source_hash" in d["lineage"]


# =============================================================================
# Test 4: RealPolicyResolver — synthetic/real separation
# =============================================================================


class TestResolverSyntheticReal:
    """R05.4: Resolver refuses synthetic bundles when require_real=True."""

    def _resolver_with(
        self,
        bundle: PolicyBundle,
        *,
        require_real: bool = True,
    ) -> RealPolicyResolver:
        return RealPolicyResolver(
            bundles={
                f"{bundle.policy.symbol}|{bundle.policy.timeframe}|{bundle.policy.regime}": bundle
            },
            require_real=require_real,
        )

    def test_real_bundle_resolves(self):
        bundle = _make_bundle(evidence_class="REAL_MARKET")
        resolver = self._resolver_with(bundle)
        # Use event_time after policy's validity_start (2026-01-01)
        # AND after lineage.permitted_at
        event_time = max(
            bundle.policy.validity_start,
            bundle.lineage.permitted_at,
        ) + timedelta(days=1)
        clock = EventClock(
            event_time=event_time,
            wall_time=event_time + timedelta(hours=1),
        )
        result = resolver.resolve(
            symbol=bundle.policy.symbol,
            timeframe=bundle.policy.timeframe,
            regime=bundle.policy.regime,
            clock=clock,
        )
        assert result.policy.policy_id == bundle.policy.policy_id

    def test_synthetic_bundle_rejected_when_require_real(self):
        bundle = _make_bundle(evidence_class="SYNTHETIC_TEST_ONLY")
        resolver = self._resolver_with(bundle)
        event_time = max(
            bundle.policy.validity_start,
            bundle.lineage.permitted_at,
        ) + timedelta(days=1)
        clock = EventClock(
            event_time=event_time,
            wall_time=event_time + timedelta(hours=1),
        )
        with pytest.raises(SyntheticPolicyRejectedError):
            resolver.resolve(
                symbol=bundle.policy.symbol,
                timeframe=bundle.policy.timeframe,
                regime=bundle.policy.regime,
                clock=clock,
            )

    def test_synthetic_bundle_accepted_when_require_real_false(self):
        bundle = _make_bundle(evidence_class="SYNTHETIC_TEST_ONLY")
        resolver = self._resolver_with(bundle, require_real=False)
        event_time = max(
            bundle.policy.validity_start,
            bundle.lineage.permitted_at,
        ) + timedelta(days=1)
        clock = EventClock(
            event_time=event_time,
            wall_time=event_time + timedelta(hours=1),
        )
        result = resolver.resolve(
            symbol=bundle.policy.symbol,
            timeframe=bundle.policy.timeframe,
            regime=bundle.policy.regime,
            clock=clock,
        )
        assert result.evidence_class == "SYNTHETIC_TEST_ONLY"

    def test_missing_bundle_raises(self):
        resolver = RealPolicyResolver(bundles={}, require_real=True)
        clock = EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(MissingPolicyError):
            resolver.resolve(
                symbol="BTC/USDT",
                timeframe="1h",
                regime="TRENDING_UP",
                clock=clock,
            )


# =============================================================================
# Test 5: RealPolicyResolver — window checks
# =============================================================================


class TestResolverWindows:
    """R05.5: Resolver refuses expired and not-yet-valid policies."""

    def test_not_yet_valid_raises(self):
        policy = _make_policy(
            validity_start=datetime(2026, 6, 1, tzinfo=UTC),
        )
        bundle = _make_bundle(policy=policy)
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
        )
        clock = EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(NotYetValidPolicyError):
            resolver.resolve(
                symbol=policy.symbol,
                timeframe=policy.timeframe,
                regime=policy.regime,
                clock=clock,
            )

    def test_expired_raises(self):
        policy = _make_policy(
            validity_start=datetime(2025, 1, 1, tzinfo=UTC),
            validity_end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        bundle = _make_bundle(policy=policy)
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
        )
        clock = EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(ExpiredPolicyError):
            resolver.resolve(
                symbol=policy.symbol,
                timeframe=policy.timeframe,
                regime=policy.regime,
                clock=clock,
            )

    def test_no_expiry_always_valid(self):
        policy = _make_policy(validity_end=None)
        bundle = _make_bundle(policy=policy)
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
        )
        clock = EventClock(
            event_time=datetime(2030, 1, 1, tzinfo=UTC),  # far future
            wall_time=datetime(2030, 1, 1, tzinfo=UTC),
        )
        result = resolver.resolve(
            symbol=policy.symbol,
            timeframe=policy.timeframe,
            regime=policy.regime,
            clock=clock,
        )
        assert result.policy.policy_id == policy.policy_id

    def test_draft_status_rejected(self):
        policy = _make_policy(status=PolicyStatus.DRAFT)
        bundle = _make_bundle(policy=policy)
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
        )
        clock = EventClock(
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            wall_time=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(PolicyConsumerError, match="DRAFT"):
            resolver.resolve(
                symbol=policy.symbol,
                timeframe=policy.timeframe,
                regime=policy.regime,
                clock=clock,
            )


# =============================================================================
# Test 6: RealPolicyResolver — lineage consistency
# =============================================================================


class TestResolverLineage:
    """R05.6: Resolver refuses future-data leakage via lineage check."""

    def test_event_before_training_data_raises(self):
        # Policy with validity_start well in the past (so window check
        # passes), but training_data_cutoff in the future relative to
        # event_time. This tests the lineage check directly.
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="TRENDING_UP",
            incumbent=ParamArtifact(
                strategy_id="rsi", params={"period": 14}, code_sha="e" * 64
            ),
            evidence_ids=("sha256:study",),
            validity_start=datetime(2025, 1, 1, tzinfo=UTC),
            validity_end=datetime(2026, 12, 31, tzinfo=UTC),
            status=PolicyStatus.ACTIVE,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            activated_at=datetime(2025, 1, 2, tzinfo=UTC),
            activated_by="test",
            activation_ticket="R05",
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        lineage = _make_lineage(
            policy_id=policy.policy_id,
            training_data_cutoff=datetime(2026, 6, 1, tzinfo=UTC),
            fit_at=datetime(2026, 6, 10, tzinfo=UTC),
            permitted_at=datetime(2026, 6, 15, tzinfo=UTC),
        )
        bundle = _make_bundle(policy=policy, lineage=lineage)
        resolver = RealPolicyResolver(
            bundles={
                f"{bundle.policy.symbol}|{bundle.policy.timeframe}|{bundle.policy.regime}": bundle
            },
        )
        # event_time is before training_data_cutoff (2026-06-01)
        clock = EventClock(
            event_time=datetime(2026, 1, 15, tzinfo=UTC),
            wall_time=datetime(2026, 1, 15, tzinfo=UTC),
        )
        with pytest.raises(FutureTrainingDataError):
            resolver.resolve(
                symbol=bundle.policy.symbol,
                timeframe=bundle.policy.timeframe,
                regime=bundle.policy.regime,
                clock=clock,
            )

    def test_event_before_permitted_at_raises(self):
        # training_data_cutoff is in the past (OK),
        # but permitted_at is in the future (not yet authorized)
        policy = _make_policy(
            validity_start=datetime(2025, 1, 1, tzinfo=UTC),
            validity_end=datetime(2026, 12, 31, tzinfo=UTC),
        )
        lineage = _make_lineage(
            policy_id=policy.policy_id,
            training_data_cutoff=datetime(2025, 6, 1, tzinfo=UTC),
            fit_at=datetime(2025, 6, 10, tzinfo=UTC),
            permitted_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
        bundle = _make_bundle(policy=policy, lineage=lineage)
        resolver = RealPolicyResolver(
            bundles={
                f"{bundle.policy.symbol}|{bundle.policy.timeframe}|{bundle.policy.regime}": bundle
            },
        )
        clock = EventClock(
            event_time=datetime(2026, 6, 1, tzinfo=UTC),
            wall_time=datetime(2026, 6, 1, tzinfo=UTC),
        )
        with pytest.raises(NotYetValidPolicyError):
            resolver.resolve(
                symbol=bundle.policy.symbol,
                timeframe=bundle.policy.timeframe,
                regime=bundle.policy.regime,
                clock=clock,
            )

    def test_event_after_all_lineage_resolves(self):
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="TRENDING_UP",
            incumbent=ParamArtifact(
                strategy_id="rsi", params={"period": 14}, code_sha="e" * 64
            ),
            evidence_ids=("sha256:study",),
            validity_start=datetime(2025, 1, 1, tzinfo=UTC),
            validity_end=datetime(2026, 12, 31, tzinfo=UTC),
            status=PolicyStatus.ACTIVE,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            activated_at=datetime(2025, 1, 2, tzinfo=UTC),
            activated_by="test",
            activation_ticket="R05",
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        lineage = _make_lineage(policy_id=policy.policy_id)
        bundle = _make_bundle(policy=policy, lineage=lineage)
        resolver = RealPolicyResolver(
            bundles={
                f"{bundle.policy.symbol}|{bundle.policy.timeframe}|{bundle.policy.regime}": bundle
            },
        )
        event_time = max(
            bundle.policy.validity_start,
            lineage.permitted_at,
        ) + timedelta(days=1)
        clock = EventClock(
            event_time=event_time,
            wall_time=event_time + timedelta(hours=1),
        )
        result = resolver.resolve(
            symbol=bundle.policy.symbol,
            timeframe=bundle.policy.timeframe,
            regime=bundle.policy.regime,
            clock=clock,
        )
        assert result.policy.policy_id == bundle.policy.policy_id


# =============================================================================
# Test 7: build_lineage_from_policy
# =============================================================================


class TestBuildLineageFromPolicy:
    """R05.7: build_lineage_from_policy uses activated_at when available."""

    def test_active_policy_uses_activated_at(self):
        activated = datetime(2026, 1, 1, tzinfo=UTC)
        created = datetime(2025, 12, 1, tzinfo=UTC)
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="TRENDING_UP",
            incumbent=ParamArtifact(
                strategy_id="rsi", params={"period": 14}, code_sha="e" * 64
            ),
            evidence_ids=("sha256:study",),
            validity_start=created,
            status=PolicyStatus.ACTIVE,
            created_at=created,
            activated_at=activated,
            activated_by="test",
            activation_ticket="R05",
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        lineage = build_lineage_from_policy(
            policy,
            training_data_cutoff=datetime(2025, 11, 1, tzinfo=UTC),
            source_hash="sha256:src",
        )
        # Lineage.policy_id must match the policy's auto-computed id
        assert lineage.policy_id == policy.policy_id
        assert lineage.fit_at == activated
        assert lineage.permitted_at == activated

    def test_draft_policy_uses_created_at(self):
        created = datetime(2025, 12, 1, tzinfo=UTC)
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="TRENDING_UP",
            incumbent=ParamArtifact(
                strategy_id="rsi", params={"period": 14}, code_sha="e" * 64
            ),
            validity_start=created,
            status=PolicyStatus.DRAFT,
            created_at=created,
        )
        lineage = build_lineage_from_policy(
            policy,
            training_data_cutoff=datetime(2025, 11, 1, tzinfo=UTC),
            source_hash="sha256:src",
        )
        assert lineage.policy_id == policy.policy_id
        assert lineage.fit_at == created
        assert lineage.permitted_at == created

    def test_naive_training_data_cutoff_rejected(self):
        policy = _make_policy()
        with pytest.raises(ValueError, match="timezone-aware"):
            build_lineage_from_policy(
                policy,
                training_data_cutoff=datetime(2025, 11, 1),  # noqa: DTZ001
                source_hash="sha256:src",
            )


# =============================================================================
# Test 8: attach_lineage_to_bundle
# =============================================================================


class TestAttachLineage:
    """R05.8: attach_lineage_to_bundle produces a new bundle (immutable)."""

    def test_new_bundle_with_replaced_lineage(self):
        bundle = _make_bundle()
        new_lineage = _make_lineage(
            policy_id=bundle.policy.policy_id,
            training_data_cutoff=datetime(2025, 5, 1, tzinfo=UTC),
            source_hash="sha256:new_source",
        )
        new_bundle = attach_lineage_to_bundle(bundle, new_lineage)
        assert new_bundle is not bundle
        assert new_bundle.lineage.source_hash == "sha256:new_source"
        # Original unchanged
        assert bundle.lineage.source_hash != "sha256:new_source"

    def test_preserves_policy_and_evidence_class(self):
        bundle = _make_bundle(evidence_class="REAL_MARKET")
        new_lineage = _make_lineage(policy_id=bundle.policy.policy_id)
        new_bundle = attach_lineage_to_bundle(bundle, new_lineage)
        assert new_bundle.policy.policy_id == bundle.policy.policy_id
        assert new_bundle.evidence_class == "REAL_MARKET"

    def test_policy_id_mismatch_in_new_lineage_rejected(self):
        bundle = _make_bundle()
        wrong_lineage = _make_lineage(policy_id="00000000wrong_xx")
        with pytest.raises(ValueError, match="does not match"):
            attach_lineage_to_bundle(bundle, wrong_lineage)


# =============================================================================
# Test 9: Helpers
# =============================================================================


class TestHelpers:
    """R05.9: reject_synthetic_for_real and verify_bundle_integrity."""

    def test_reject_synthetic_raises(self):
        bundle = _make_bundle(evidence_class="SYNTHETIC_TEST_ONLY")
        with pytest.raises(SyntheticPolicyRejectedError):
            reject_synthetic_for_real(bundle)

    def test_reject_synthetic_passes_for_real(self):
        bundle = _make_bundle(evidence_class="REAL_MARKET")
        # Should not raise
        reject_synthetic_for_real(bundle)

    def test_verify_bundle_integrity_no_expectation(self):
        bundle = _make_bundle()
        assert verify_bundle_integrity(bundle) is True

    def test_verify_bundle_integrity_matching_hash(self):
        bundle = _make_bundle()
        assert (
            verify_bundle_integrity(
                bundle, expected_source_hash=bundle.lineage.source_hash
            )
            is True
        )

    def test_verify_bundle_integrity_mismatch(self):
        bundle = _make_bundle()
        assert (
            verify_bundle_integrity(bundle, expected_source_hash="sha256:wrong")
            is False
        )


# =============================================================================
# Test 10: End-to-end R05 routing scenario
# =============================================================================


class TestEndToEndRouting:
    """R05.10: Real routing scenario — produces valid bundle for valid clock."""

    def test_valid_real_policy_resolves(self):
        # Build a real, activated policy with complete lineage
        now = datetime(2026, 1, 15, tzinfo=UTC)
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime="TRENDING_UP",
            incumbent=ParamArtifact(
                strategy_id="rsi", params={"period": 14}, code_sha="e" * 64
            ),
            evidence_ids=("sha256:study",),
            validity_start=now - timedelta(days=30),
            validity_end=now + timedelta(days=30),
            status=PolicyStatus.ACTIVE,
            created_at=now - timedelta(days=15),
            activated_at=now - timedelta(days=10),
            activated_by="R04-campaign",
            activation_ticket="R04-campaign",
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        lineage = build_lineage_from_policy(
            policy,
            training_data_cutoff=now - timedelta(days=60),
            source_hash="sha256:real_evidence_bundle_abc",
            actor="R04-campaign",
            ticket="R04-campaign",
        )
        bundle = PolicyBundle(
            policy=policy, lineage=lineage, evidence_class="REAL_MARKET"
        )
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
        )
        clock = EventClock(
            event_time=now,
            wall_time=now + timedelta(seconds=1),
        )
        result = resolver.resolve(
            symbol=policy.symbol,
            timeframe=policy.timeframe,
            regime=policy.regime,
            clock=clock,
        )
        assert result.policy.policy_id == policy.policy_id
        assert result.lineage.source_hash == lineage.source_hash

    def test_synthetic_routing_in_ci_accepted(self):
        """CI / integration tests can use synthetic bundles by
        explicitly setting ``require_real=False``."""
        now = datetime(2026, 1, 15, tzinfo=UTC)
        policy = _make_policy(
            policy_id="syn123abc456",
            symbol="BTC/USDT",
            status=PolicyStatus.ACTIVE,
            validity_start=now - timedelta(days=30),
        )
        lineage = _make_lineage(
            policy_id=policy.policy_id,
            training_data_cutoff=now - timedelta(days=60),
        )
        bundle = PolicyBundle(
            policy=policy,
            lineage=lineage,
            evidence_class="SYNTHETIC_TEST_ONLY",
        )
        resolver = RealPolicyResolver(
            bundles={f"{policy.symbol}|{policy.timeframe}|{policy.regime}": bundle},
            require_real=False,  # CI mode
        )
        clock = EventClock(event_time=now, wall_time=now + timedelta(seconds=1))
        result = resolver.resolve(
            symbol=policy.symbol,
            timeframe=policy.timeframe,
            regime=policy.regime,
            clock=clock,
        )
        assert result.evidence_class == "SYNTHETIC_TEST_ONLY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
