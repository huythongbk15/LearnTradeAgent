#!/usr/bin/env bash
# Run the integrated AC01-AC15 evidence suite.
#
# Mandatory after touching the promotion gate, selection policy, or any
# scripts/evidence_ac*.py. The gate refuses a policy whose scores cannot be
# attributed to registered source code, so a change to it can invalidate the
# evidence that exercises it — and did: AC06, AC13 and AC14 were writing
# placeholder code_sha values and score dicts with no OOS metrics, and every
# one of them failed once the gate was tightened (ff8c922).
#
# Runtime is roughly three minutes. CI runs this on every push
# (.github/workflows/ci.yml, job `acceptance-evidence`); this script is the
# local equivalent so the check is not deferred to a remote run.
#
# Usage: .venv/bin/python scripts/run_acceptance_ac01_15.py
set -euo pipefail

cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || PY=python

echo "==> integrated AC01-AC15 evidence run"
"$PY" scripts/run_acceptance_ac01_15.py
