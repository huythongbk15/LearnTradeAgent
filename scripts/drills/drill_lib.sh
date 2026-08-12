#!/usr/bin/env bash
# scripts/drills/drill_lib.sh — shared helpers for operational drills.
# Source this file from drill scripts. Safe by default: unless --execute is
# passed every destructive/network action is printed instead of run.

DRILL_MODE="${DRILL_MODE:-dry-run}"
DRILL_PASS=0
DRILL_FAIL=0

drill_setup() {
    if [[ "${1:-}" == "--execute" ]]; then
        DRILL_MODE="execute"
    fi
    echo "== drill mode: ${DRILL_MODE} =="
}

drill_assert() {
    # drill_assert <description> <condition-command...>
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        DRILL_PASS=$((DRILL_PASS + 1))
        echo "  [PASS] ${desc}"
    else
        DRILL_FAIL=$((DRILL_FAIL + 1))
        echo "  [FAIL] ${desc}"
    fi
}

drill_run() {
    # drill_run <description> <command...> — executes only in execute mode.
    local desc="$1"; shift
    if [[ "${DRILL_MODE}" == "execute" ]]; then
        echo "  [RUN ] ${desc}"
        "$@"
        return $?
    fi
    echo "  [SKIP] ${desc} (dry-run; pass --execute to run)"
    return 0
}

drill_summary() {
    echo "== drill summary: ${DRILL_PASS} passed, ${DRILL_FAIL} failed =="
    if [[ "${DRILL_FAIL}" -gt 0 ]]; then
        return 1
    fi
    return 0
}