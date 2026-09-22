"""
AC08 Evidence Script — Permission & Instrument Control.

Independent oracle: constructs PermissionContext with known field values
and hand-verifies the expected ALLOW/BLOCK/REDUCE_ONLY decision that
evaluate_order_permission returns.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from trading_agent.execution.permission import (
    evaluate_order_permission, PermissionContext,
    OrderPermission, PermissionReason,
)
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionHealth, ExposureEffect, TrustedPrice,
)
from trading_agent.execution.canonical import EvidenceState, UnifiedRiskDecision
from trading_agent.execution.canonical.risk_decision import RiskLevel

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC08 evidence FAIL: {name}")

NOW = datetime.now(timezone.utc)
TRUSTED_PRICE = TrustedPrice(price=100.0, exchange_timestamp=NOW, received_at=NOW, sequence_id=1)

def make_risk(allowed_target=0.25, max_new=0.25, reduce_only=False,
              calib=EvidenceState.KNOWN, ood=EvidenceState.KNOWN, regime=EvidenceState.KNOWN):
    return UnifiedRiskDecision(
        decision_id="dec_001", forecast_fingerprint="fp_001", model_artifact_id="m_001",
        requested_target_exposure=0.50, allowed_target_exposure=allowed_target,
        max_new_exposure=max_new, reduce_only=reduce_only, risk_level=RiskLevel.LOW,
        reason_codes=(), calibration_state=calib, calibration_artifact_id="c1",
        calibration_ece=0.05, ood_state=ood, ood_score=0.1, regime_state=regime,
        regime_entropy=0.3, interval_width=0.4, created_at=NOW,
    )

def base(**kw):
    d = dict(execution_health=ExecutionHealth.NORMAL, exposure_effect=ExposureEffect.INCREASE,
             risk_decision=None, trusted_price=TRUSTED_PRICE, max_price_age_seconds=60.0,
             reconciliation_state="resolved", protection_state="protected",
             manual_blocked=False, kill_switch_active=False, data_trust="trusted",
             inventory_state="known", free_inventory=1000.0, order_size=10.0,
             order_side="buy", require_fresh_market_data=True, enforce_inventory=True)
    d.update(kw)
    return PermissionContext(**d)

risk = make_risk()
r = evaluate_order_permission(base(risk_decision=risk, exposure_effect=ExposureEffect.INCREASE))
check("C1_valid_buy", r.permission == OrderPermission.ALLOW, f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(order_size=-10.0, order_side="buy"))
check("C2_invalid_size", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.INVALID_ORDER,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(order_size=10.0, order_side="left"))
check("C3_invalid_side", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.INVALID_ORDER,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(order_side="sell", order_size=2000.0, free_inventory=100.0,
                                    exposure_effect=ExposureEffect.REDUCE))
check("C4_insufficient_inventory", r.permission == OrderPermission.BLOCK and
      r.reason == PermissionReason.INSUFFICIENT_INVENTORY, f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(kill_switch_active=True, risk_decision=risk,
                                    exposure_effect=ExposureEffect.NEUTRAL))
check("C5_kill_switch", r.permission == OrderPermission.BLOCK, f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(manual_blocked=True, risk_decision=risk,
                                    exposure_effect=ExposureEffect.INCREASE))
check("C6_manual_blocked", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.MANUAL_BLOCKED,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(trusted_price=None, risk_decision=risk,
                                    exposure_effect=ExposureEffect.INCREASE))
check("C7_stale_price", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.STALE_MARKET_DATA,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(reconciliation_state="started", risk_decision=risk,
                                    exposure_effect=ExposureEffect.INCREASE))
check("C8_reconciliation", r.permission in (OrderPermission.BLOCK, OrderPermission.REDUCE_ONLY),
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(broker_state="flying", risk_decision=risk,
                                    exposure_effect=ExposureEffect.INCREASE))
check("C9_unknown_broker", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.UNKNOWN_BROKER_STATE,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(risk_decision=None, exposure_effect=ExposureEffect.INCREASE, draft=False))
check("C10_missing_risk", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.MISSING_RISK_DECISION,
      f"perm={r.permission}, reason={r.reason}")

r = evaluate_order_permission(base(risk_decision=None, exposure_effect=ExposureEffect.INCREASE, draft=True))
check("C11_draft_no_risk", r.permission == OrderPermission.ALLOW, f"perm={r.permission}, reason={r.reason}")

risk_nc = make_risk(calib=EvidenceState.MISSING)
r = evaluate_order_permission(base(risk_decision=risk_nc, exposure_effect=ExposureEffect.INCREASE))
check("C12_missing_calib", r.permission == OrderPermission.BLOCK and r.reason == PermissionReason.MISSING_CALIBRATION_EVIDENCE,
      f"perm={r.permission}, reason={r.reason}")

all_pass = all(x["status"] == "PASS" for x in results)
print("\n" + "=" * 60)
print(f"AC08 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print("=" * 60)
evidence = {"ac_id": "AC08", "oracle": "independent hand-verified decisions",
            "cases": results, "all_pass": all_pass,
            "total_checks": len(results),
            "passed": sum(1 for x in results if x["status"] == "PASS")}
Path("/tmp/ac08_evidence.json").write_text(json.dumps(evidence, indent=2, default=str))
print("Evidence written to /tmp/ac08_evidence.json")
sys.exit(0 if all_pass else 1)
