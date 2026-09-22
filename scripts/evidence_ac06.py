"""
AC06 Evidence Script — Policy to Runner (Routing Decision).

Independent oracle: constructs policy bundles with known attributes and
hand-verifies each decision the resolver makes. The resolver is the public
entrypoint that binds a validated policy to a routing decision — it must
fail-closed on synthetic bundles, expired/not-yet-valid windows, missing
policies, and temporal leakage (future training data).
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_agent.research.selection_policy import (
    SelectionPolicyArtifact, PolicyStatus, ParamArtifact,
)
from trading_agent.research.policy_resolver import (
    RealPolicyResolver, PolicyBundle, LineageRecord, EventClock,
    SyntheticPolicyRejectedError, ExpiredPolicyError, NotYetValidPolicyError,
    MissingPolicyError, FutureTrainingDataError, PolicyConsumerError,
)

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC06 evidence FAIL: {name}")

PAST = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
PRESENT = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
FUTURE = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

def make_policy(status=PolicyStatus.ACTIVE, validity_start=PAST,
                scores=None,
                validity_end=FUTURE, symbol="ADA_USDT", regime="TRENDING_UP"):
    params = ParamArtifact(
        strategy_id="volatility_breakout_v2",
        params={"window": 20, "multiplier": 2.0},
        code_sha="abc123",
    )
    return SelectionPolicyArtifact(
        symbol=symbol,
        timeframe="1h",
        regime=regime,
        incumbent=params,
        challengers=[],
        scores=(scores or {"sharpe": 1.5, "pf": 1.3}),
        evidence_ids=["ev_001"],
        validity_start=validity_start,
        validity_end=validity_end,
        fallback="NO_TRADE",
        risk_cap=0.25,
        status=status,
        created_at=PAST,
        policy_commit_sha="5d0e477b" + "0" * 56,
        policy_data_manifest_sha="deadbeef" + "0" * 56,
        policy_feature_manifest_sha="cafebabe" + "0" * 56,
        policy_release_digest="registry/app:v1",
        activated_at=PAST + timedelta(hours=2),
        activated_by="research_system",
        activation_ticket="AC06-evidence",
    )

def make_bundle(policy, lineage, evidence_class="REAL_MARKET"):
    return PolicyBundle(
        policy=policy,
        lineage=lineage,
        evidence_class=evidence_class,
    )

def make_lineage(policy_id, training_cutoff, fit_at, permitted_at):
    return LineageRecord(
        policy_id=policy_id,
        training_data_cutoff=training_cutoff,
        fit_at=fit_at,
        permitted_at=permitted_at,
        source_hash="abcdef1234567890",
        actor="research_system",
        ticket="AC06-evidence",
    )

# C1: Valid REAL_MARKET bundle resolves
policy = make_policy()
lineage = make_lineage(policy.policy_id, training_cutoff=PAST,
                       fit_at=PAST + timedelta(hours=1), permitted_at=PAST + timedelta(hours=2))
bundle = make_bundle(policy, lineage, evidence_class="REAL_MARKET")
resolver = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": bundle}, require_real=True)
clock = EventClock(event_time=PRESENT, wall_time=PRESENT)
result = resolver.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
check("C1_valid_resolves", result is bundle,
      f"resolved policy_id={result.policy.policy_id[:16]}...")

# C2: SYNTHETIC bundle rejected
syn_policy = make_policy()
syn_lineage = make_lineage(syn_policy.policy_id, training_cutoff=PAST,
                           fit_at=PAST + timedelta(hours=1), permitted_at=PAST + timedelta(hours=2))
syn_bundle = make_bundle(syn_policy, syn_lineage, evidence_class="SYNTHETIC_TEST_ONLY")
resolver_syn = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": syn_bundle}, require_real=True)
try:
    resolver_syn.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C2_synthetic_rejected", False, "should have raised")
except SyntheticPolicyRejectedError as e:
    check("C2_synthetic_rejected", True, "rejected")

# C3: Not-yet-valid
future_policy = make_policy(validity_start=FUTURE, validity_end=FUTURE + timedelta(days=1))
future_lineage = make_lineage(future_policy.policy_id, training_cutoff=PAST,
                              fit_at=PAST + timedelta(hours=1), permitted_at=PAST + timedelta(hours=2))
res_future = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": make_bundle(future_policy, future_lineage, "REAL_MARKET")}, require_real=True)
try:
    res_future.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C3_not_yet_valid_rejected", False, "should have raised")
except NotYetValidPolicyError:
    check("C3_not_yet_valid_rejected", True, "rejected")

# C4: Expired
expired_policy = make_policy(validity_start=PAST - timedelta(days=10), validity_end=PAST - timedelta(days=1))
exp_lineage = make_lineage(expired_policy.policy_id, training_cutoff=PAST,
                           fit_at=PAST + timedelta(hours=1), permitted_at=PAST + timedelta(hours=2))
res_exp = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": make_bundle(expired_policy, exp_lineage, "REAL_MARKET")}, require_real=True)
try:
    res_exp.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C4_expired_rejected", False, "should have raised")
except (ExpiredPolicyError, PolicyConsumerError):
    check("C4_expired_rejected", True, "rejected")

# C5: Missing
res_missing = RealPolicyResolver(bundles={}, require_real=True)
try:
    res_missing.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C5_missing_rejected", False, "should have raised")
except MissingPolicyError:
    check("C5_missing_rejected", True, "rejected")

# C6: DRAFT status
draft = make_policy(status=PolicyStatus.DRAFT)
draft_lin = make_lineage(draft.policy_id, PAST, PAST + timedelta(hours=1), PAST + timedelta(hours=2))
res_draft = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": make_bundle(draft, draft_lin, "REAL_MARKET")}, require_real=True)
try:
    res_draft.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C6_draft_rejected", False, "should have raised")
except PolicyConsumerError:
    check("C6_draft_rejected", True, "rejected")

# C7: FutureTrainingDataError
ftd = make_policy()
ftd_cutoff = FUTURE
ftd_lin = make_lineage(ftd.policy_id, ftd_cutoff, ftd_cutoff + timedelta(hours=1), ftd_cutoff + timedelta(hours=2))
res_ftd = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": make_bundle(ftd, ftd_lin, "REAL_MARKET")}, require_real=True)
try:
    res_ftd.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C7_future_training_rejected", False, "should have raised")
except FutureTrainingDataError:
    check("C7_future_training_rejected", True, "rejected")

# C8: Content-addressed policy_id
pa = make_policy()
pb = make_policy(scores={"sharpe": 1.5, "pf": 1.31})
check("C8_content_addressed", pa.policy_id != pb.policy_id, "different ids")

all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC06 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")
evidence = {
    "ac_id": "AC06",
    "oracle": "independent: constructs bundles with known attributes, verifies resolver fail-closed decisions",
    "cases": results, "all_pass": all_pass,
    "total_checks": len(results), "passed": sum(1 for r in results if r["status"] == "PASS"),
}
out = Path("/tmp/ac06_evidence.json")
out.write_text(json.dumps(evidence, indent=2, default=str))
print(f"Evidence written to {out}")
sys.exit(0 if all_pass else 1)
