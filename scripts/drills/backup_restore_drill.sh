#!/usr/bin/env bash
# scripts/drills/backup_restore_drill.sh — backup/restore drill (P3 gate).
#
# Creates a small fixture (risk state + audit log), backs it up, destroys
# the originals, restores from the backup, then verifies checksums and
# content. Uses a local file backup so the drill runs on any machine
# without TimescaleDB/Redis/S3. The infrastructure backup path is verified
# separately by CI Compose validation.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=drill_lib.sh
source "${SCRIPT_DIR}/drill_lib.sh"

drill_setup "${1:-}"

TMP_DIR="$(mktemp -d /tmp/backup_restore_drill.XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT
SRC_DIR="${TMP_DIR}/live"
BAK_DIR="${TMP_DIR}/backup"
RST_DIR="${TMP_DIR}/restored"

mkdir -p "${SRC_DIR}" "${BAK_DIR}" "${RST_DIR}"

echo "== backup/restore drill =="

# 1. Fixture: write a deterministic risk state and audit trail
python3 - "$SRC_DIR" <<'EOF'
import json, sys, os
src = sys.argv[1]
state = {
    "version": 1,
    "profile": "testnet",
    "equity": 100000.0,
    "positions": [{"symbol": "BTC/USDT", "qty": 0.01}],
}
with open(os.path.join(src, "binance_live_risk_state.json"), "w") as fh:
    json.dump(state, fh)
with open(os.path.join(src, "binance_live_audit.jsonl"), "w") as fh:
    fh.write('{"timestamp":"2026-08-12T00:00:00+00:00","event":"run_completed","details":{}}\n')
EOF
drill_assert "fixture created" test -f "${SRC_DIR}/binance_live_risk_state.json"

# 2. Backup: copy + checksum manifest
drill_run "backup live files to ${BAK_DIR}" bash -c "
    cp ${SRC_DIR}/binance_live_risk_state.json ${SRC_DIR}/binance_live_audit.jsonl ${BAK_DIR}/
    (cd ${BAK_DIR} && sha256sum * > MANIFEST.sha256)
"
# Even in dry-run we can still back up to a scratch dir to prove the script.
cp "${SRC_DIR}/binance_live_risk_state.json" "${SRC_DIR}/binance_live_audit.jsonl" "${BAK_DIR}/"
(cd "${BAK_DIR}" && sha256sum ./* > MANIFEST.sha256)
drill_assert "backup manifest written" test -s "${BAK_DIR}/MANIFEST.sha256"

# 3. Disaster: destroy the originals
rm -f "${SRC_DIR}/binance_live_risk_state.json" "${SRC_DIR}/binance_live_audit.jsonl"
drill_assert "originals destroyed" test ! -e "${SRC_DIR}/binance_live_risk_state.json"

# 4. Restore from backup
cp "${BAK_DIR}/binance_live_risk_state.json" "${BAK_DIR}/binance_live_audit.jsonl" "${RST_DIR}/"
drill_assert "restore produced files" test -f "${RST_DIR}/binance_live_risk_state.json"

# 5. Verify checksums match the manifest
if (cd "${RST_DIR}" && sha256sum -c "${BAK_DIR}/MANIFEST.sha256" >/dev/null 2>&1); then
    DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] restored files match backup checksums"
else
    DRILL_FAIL=$((DRILL_FAIL + 1)); echo "  [FAIL] restored files do not match backup checksums"
fi

# 6. Content verification
if python3 - "$RST_DIR" <<'EOF'
import json, os, sys
rst = sys.argv[1]
state = json.load(open(os.path.join(rst, "binance_live_risk_state.json")))
assert state["profile"] == "testnet"
assert state["equity"] == 100000.0
lines = open(os.path.join(rst, "binance_live_audit.jsonl")).read().strip().splitlines()
assert len(lines) == 1 and "run_completed" in lines[0]
EOF
then
    DRILL_PASS=$((DRILL_PASS + 1)); echo "  [PASS] restored content correct"
else
    DRILL_FAIL=$((DRILL_FAIL + 1)); echo "  [FAIL] restored content incorrect"
fi

drill_summary