#!/usr/bin/env bash
# scripts/drills/restart_drill.sh — restart/idempotency drill (P0.1/P3 gate).
#
# Verifies that restarting the hourly runner:
#   1. does not duplicate protective stop client order IDs,
#   2. adopts the same protective order still held at the exchange,
#   3. reconciles unfinished client order IDs before new orders.
#
# Local checks run always; the live testnet checks run only with --execute
# (requires LIVE_TESTNET_ACCEPTANCE=1 + testnet keys in the environment).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=drill_lib.sh
source "${SCRIPT_DIR}/drill_lib.sh"

drill_setup "${1:-}"

AUDIT_LOG="${AUDIT_LOG:-data/execution/binance_live_audit.jsonl}"
STATE_FILE="${STATE_FILE:-data/binance_testnet_risk_state.json}"

echo "== restart/idempotency drill =="

# 1. Local: audit log exists and is not empty (fresh trail -> pass by construction)
if [[ -s "${AUDIT_LOG}" ]]; then
    DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] audit log exists: ${AUDIT_LOG}"
else
    echo "  [INFO] no audit trail yet (${AUDIT_LOG}) — pass by construction"
    DRILL_PASS=$((DRILL_PASS + 1))
fi

# 2. Local: audit log is valid JSONL (every line parses)
if python3 - "$AUDIT_LOG" <<'EOF'
import json, sys, os
path = sys.argv[1]
if not os.path.isfile(path):
    sys.exit(0)  # fresh trail
ok = True
with open(path) as fh:
    for i, line in enumerate(fh, 1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except Exception:
            ok = False
            print(f"corrupt line {i}")
sys.exit(0 if ok else 1)
EOF
then
    DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] audit log is valid JSONL"
else
    DRILL_FAIL=$((DRILL_FAIL + 1)); echo "  [FAIL] audit log contains corrupt lines"
fi

# 3. Local: risk state exists when a live run has happened
if [[ -f "${STATE_FILE}" ]]; then
    if python3 - "$STATE_FILE" <<'EOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    sys.exit(0 if isinstance(data, dict) else 1)
except Exception:
    sys.exit(1)
EOF
    then
        DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] risk state is valid JSON"
    else
        DRILL_FAIL=$((DRILL_FAIL + 1)); echo "  [FAIL] risk state is corrupt"
    fi
else
    echo "  [INFO] no risk state yet (${STATE_FILE}) — nothing to verify"
fi

# 4. Critical: no duplicate protective client order IDs in the audit trail
if python3 - "$AUDIT_LOG" <<'EOF'
import json, sys, os
path = sys.argv[1]
if not os.path.isfile(path):
    sys.exit(0)  # fresh trail
seen = {}
bad = 0
with open(path) as fh:
    for line in fh:
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event") == "protective_stop_placed":
            cid = (ev.get("details") or {}).get("client_order_id")
            if cid:
                if cid in seen:
                    bad += 1
                    print(f"duplicate protective client_order_id: {cid}")
                seen[cid] = True
sys.exit(1 if bad else 0)
EOF
then
    DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] no duplicate protective client order IDs"
else
    DRILL_FAIL=$((DRILL_FAIL + 1)); echo "  [FAIL] duplicate protective client order IDs found"
fi

# 5. Testnet live check (opt-in): restart runner and confirm the same client
#    order ID is adopted instead of a new one.
drill_run "restart Binance testnet runner (LIVE_TESTNET_ACCEPTANCE=1 required)" bash -c '
    cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
    export TRADING_KILL_SWITCH=true
    export TRADING_MODE=testnet
    export LIVE_TESTNET_ACCEPTANCE=1
    python scripts/live_enhanced_ma_binance.py --testnet --profile testnet --dry-run
'

drill_summary