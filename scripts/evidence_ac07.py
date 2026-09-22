"""AC07 Evidence Script — Router Switching & Submission Ownership.
Independent oracle: hand-verifies claim_submission atomicity."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from trading_agent.execution.lifecycle.store import ExecutionEventStore

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC07 FAIL: {name}")

INTENT_ID = "intent_router_001"
tmpdir = Path(tempfile.mkdtemp())
db_path = tmpdir / "ac07.db"
store = ExecutionEventStore(db_path).connect()

ok1 = store.claim_submission(intent_id=INTENT_ID, claimed_by="router_A",
    idempotency_key="idem_07", payload_hash="abc123")
check("C1_first_claim", ok1 is True, f"first={ok1}")

ok2 = store.claim_submission(intent_id=INTENT_ID, claimed_by="router_B",
    idempotency_key="idem_07b", payload_hash="diff456")
check("C2_dup_rejected", ok2 is False, f"second={ok2}")

claim = store.submission_claim(INTENT_ID)
check("C3_holder_unchanged", claim and claim["claimed_by"] == "router_A", f"holder={claim['claimed_by'] if claim else None}")

INTENT_DUP = "intent_concurrent_001"
box = []
def attempt(name):
    local_store = ExecutionEventStore(db_path).connect()
    ok = local_store.claim_submission(intent_id=INTENT_DUP, claimed_by=name,
        idempotency_key=f"idem_{name}", payload_hash="same")
    box.append((name, ok))
    local_store.close()
t1 = threading.Thread(target=attempt, args=("router_A",))
t2 = threading.Thread(target=attempt, args=("router_B",))
t1.start()
t2.start()
t1.join()
t2.join()
succ = [r for r in box if r[1] is True]
check("C4_atomic_concurrent", len(succ) == 1, f"successes={len(succ)}, box={box}")
winner = succ[0][0]
claim2 = store.submission_claim(INTENT_DUP)
check("C4_winner_holder", claim2 and claim2["claimed_by"] == winner, f"winner={winner}")

store.release_submission_claim(INTENT_ID)
check("C5_released", store.submission_claim(INTENT_ID) is None, "")
ok_rec = store.claim_submission(intent_id=INTENT_ID, claimed_by="router_C",
    idempotency_key="idem_07c", payload_hash="def456")
check("C5_reclaim", ok_rec is True, f"reclaim={ok_rec}")

INTENT_IDEM = "intent_idem_001"
ok_f = store.claim_submission(intent_id=INTENT_IDEM, claimed_by="X",
    idempotency_key="uniq_key", payload_hash="h_a")
ok_d = store.claim_submission(intent_id=INTENT_IDEM, claimed_by="Y",
    idempotency_key="uniq_key", payload_hash="h_b")
check("C6_idempotency_key", ok_f is True and ok_d is False, f"first={ok_f}, dup={ok_d}")

store.close()
all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC07 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")
ev = {"ac_id":"AC07","oracle":"independent atomic claim","cases":results,"all_pass":all_pass,
      "total_checks":len(results),"passed":sum(1 for r in results if r["status"]=="PASS"),
      "intent_id":INTENT_ID}
Path("/tmp/ac07_evidence.json").write_text(json.dumps(ev, indent=2, default=str))
print("Evidence: /tmp/ac07_evidence.json")
sys.exit(0 if all_pass else 1)
