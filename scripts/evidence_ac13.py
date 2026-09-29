"""
AC13 Evidence Script — Approval Consumer (Verifiable Approvals).
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
    verify_bundle_integrity, reject_synthetic_for_real,
    SyntheticPolicyRejectedError, MissingPolicyError, PolicyConsumerError,
)

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC13 FAIL: {name}")

def _real_code_sha(strategy_id: str = "volatility_breakout") -> str:
    """Source hash of a strategy that actually exists on the allowlist.

    AC13/AC06 previously used "abc123def456". The measured-evidence gate
    refuses a policy whose code_sha matches no registered source, which is
    the check added in 8870e03 after POLICY_RETURN_AUDIT.md found 4,516 of
    4,520 promotable policies carried placeholder SHAs. A fixture that
    violates the rule it is meant to exercise tests nothing.
    """
    from trading_agent.strategies.canonical import build_default_registry

    return build_default_registry().describe(strategy_id).code_sha


# OOS metric family required by the promotion gate. These are fixture
# values for a lifecycle test, not a performance claim.
_MEASURED = {
    "selection_score": 1.5,
    "median_oos_return_pct": 4.2,
    "median_oos_trades": 30,
    "n_passing_folds": 7,
    "total_folds": 9,
    "net_fold_edge_pct": 2.9,   # positive: the fixture exercises the accept path
}


PAST = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
PRESENT = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
FUTURE = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

def make_policy(status=PolicyStatus.ACTIVE, scores=None):
    params = ParamArtifact(strategy_id="volatility_breakout",
        params={"window": 20, "multiplier": 2.0}, code_sha=_real_code_sha())
    return SelectionPolicyArtifact(
        symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP",
        incumbent=params, challengers=[], scores=(scores or _MEASURED),
        evidence_ids=["ev_001"], validity_start=PAST, validity_end=FUTURE,
        fallback="NO_TRADE", risk_cap=0.25, status=status, created_at=PAST,
        policy_commit_sha="5d0e477b" + "0" * 56,
        policy_data_manifest_sha="deadbeef" + "0" * 56,
        policy_feature_manifest_sha="cafebabe" + "0" * 56,
        policy_release_digest="registry/app:v1",
        activated_at=PAST + timedelta(hours=2), activated_by="research_system",
        activation_ticket="AC13-evidence",
    )

def make_lineage(pid, source_hash="source_hash_abc123"):
    return LineageRecord(policy_id=pid, training_data_cutoff=PAST,
        fit_at=PAST + timedelta(hours=1), permitted_at=PAST + timedelta(hours=2),
        source_hash=source_hash, actor="research_system", ticket="AC13-evidence")

policy = make_policy()
lineage = make_lineage(policy.policy_id)
bundle = PolicyBundle(policy=policy, lineage=lineage, evidence_class="REAL_MARKET")
check("C1_correct_hash", verify_bundle_integrity(bundle, expected_source_hash="source_hash_abc123") is True, "")
check("C2_tamper_detected", verify_bundle_integrity(bundle, expected_source_hash="tampered") is False, "")
check("C3_no_expect_passes", verify_bundle_integrity(bundle, expected_source_hash=None) is True, "")

syn_bundle = PolicyBundle(policy=make_policy(), lineage=lineage, evidence_class="SYNTHETIC_TEST_ONLY")
try:
    reject_synthetic_for_real(syn_bundle)
    check("C4_synthetic_rejected", False, "")
except SyntheticPolicyRejectedError:
    check("C4_synthetic_rejected", True, "rejected")
try:
    reject_synthetic_for_real(bundle)
    check("C5_real_approves", True, "")
except SyntheticPolicyRejectedError:
    check("C5_real_approves", False, "")

draft_p = make_policy(status=PolicyStatus.DRAFT)
draft_bundle = PolicyBundle(policy=draft_p, lineage=make_lineage(draft_p.policy_id), evidence_class="REAL_MARKET")
res = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": draft_bundle}, require_real=True)
clock = EventClock(event_time=PRESENT, wall_time=PRESENT)
try:
    res.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C6_draft_rejected", False, "")
except (MissingPolicyError, PolicyConsumerError):
    check("C6_draft_rejected", True, "rejected")

exp_p = make_policy(status=PolicyStatus.EXPIRED)
exp_bundle = PolicyBundle(policy=exp_p, lineage=make_lineage(exp_p.policy_id), evidence_class="REAL_MARKET")
res2 = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": exp_bundle}, require_real=True)
try:
    res2.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C7_expired_rejected", False, "")
except PolicyConsumerError:
    check("C7_expired_rejected", True, "rejected")

val_p = make_policy(status=PolicyStatus.VALIDATED)
val_bundle = PolicyBundle(policy=val_p, lineage=make_lineage(val_p.policy_id), evidence_class="REAL_MARKET")
res3 = RealPolicyResolver(bundles={"ADA_USDT|1h|TRENDING_UP": val_bundle}, require_real=True)
try:
    r = res3.resolve(symbol="ADA_USDT", timeframe="1h", regime="TRENDING_UP", clock=clock)
    check("C8_validated_accepted", r is val_bundle, "")
except PolicyConsumerError as e:
    check("C8_validated_accepted", False, str(e))

all_pass = all(x["status"] == "PASS" for x in results)
print(f"\n{'='*60}")
print(f"AC13 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")
ev = {"ac_id":"AC13","oracle":"independent bundle construction + decision verification",
      "cases":results,"all_pass":all_pass,"total_checks":len(results),
      "passed":sum(1 for x in results if x["status"]=="PASS")}
Path("/tmp/ac13_evidence.json").write_text(json.dumps(ev, indent=2, default=str))
print("Evidence: /tmp/ac13_evidence.json")
sys.exit(0 if all_pass else 1)
