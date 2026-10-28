"""X2 preregistration for the BTC-context contrast. Not a run.

The entry rule is frozen. A market evaluation still needs a separate
authorization. This module does not grant it.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

from trading_agent.research.btc_context_features import (
    CONTRACT_VERSION,
    FEATURE_NAMES,
    contract_fingerprint,
)
from trading_agent.research.btc_context_rule import (
    CANONICAL_WARMUP_BARS,
    ENTRY_RULE_ID,
    ENTRY_THRESHOLD,
    FILL_DELAY_BARS,
    HOLD_BARS,
    PLUS_ENTRY_RULE_ID,
    PLUS_ARM,
    SHARED_ACROSS_TARGETS,
    rule_fingerprint,
    rule_fingerprint_plus,
)
from trading_agent.strategies.own24_fill_context import (
    fill_policy_fingerprint_plus,
)

C1_FEATURES: tuple[str, ...] = FEATURE_NAMES[:3]
C2_FEATURES: tuple[str, ...] = FEATURE_NAMES[3:]

TARGETS: tuple[str, ...] = ("ETH/USDT", "BNB/USDT")
ARMS: tuple[str, ...] = ("A0", "A1", "A2")
COSTS: tuple[str, ...] = ("1x", "2x", "slip_stress")
FOLDS = 1
CELL_TIMEOUT_SECONDS = 600
WORKER_COUNT = 1
MAX_RETRIES = 0

# ── BASE vs PLUS contrast (new identity, not A1 old) ────────────────────
BASE_ARM = "A0"  # pair-only, H-OWN24-CONTEXT-01
PLUS_ARMS = (BASE_ARM, PLUS_ARM)  # A0 vs A0 + btc_simple_return_24 > 0
PLUS_COSTS = COSTS
PLUS_FOLDS = FOLDS

INPUTS: dict[str, dict[str, str]] = {
    "BTC/USDT": {
        "role": "context_not_target",
        "path": "data/btc1h_preholdout_revision_20261024_01/input.parquet",
        "sha256": "45ba1b1bc6a91986eda445de8026cab9995fcabe011a735244a4c7cf0e0825ce",
        "exposure": "DEVELOPMENT_EXPOSED_NOT_CONFIRMATION",
    },
    "ETH/USDT": {
        "role": "target",
        "path": "data/existing_research_audit_20261008/ETH_USDT__1h__pre_holdout.parquet",
        "sha256": "50c1edff5ac70a62c5939eaf32c52d711af6aa95a4a75d9551c4d86494f9cb23",
        "exposure": "QUALITY_ACCEPTED_DEVELOPMENT_INPUT_NOT_CONFIRMATION",
    },
    "BNB/USDT": {
        "role": "target",
        "path": "data/existing_research_audit_20261008/BNB_USDT__1h__pre_holdout.parquet",
        "sha256": "2301c4ee9f3fecc791150f62d10c57b98fedb68679388bc3489ef4aaf6e42eb6",
        "exposure": "QUALITY_ACCEPTED_DEVELOPMENT_INPUT_NOT_CONFIRMATION",
    },
}

CLOCK_ROWS = 25198
CLOCK_FIRST_UTC = "2023-03-24T14:00:00+00:00"
CLOCK_LAST_UTC = "2026-02-06T11:00:00+00:00"
HOLDOUT_START_UTC = "2026-02-06T16:00:00+00:00"
HOLDOUT_END_UTC = "2026-08-05T16:00:00+00:00"

# Canonical signal windows are a fixed tail, so the first bar that can both
# enter and see an exit HOLD_BARS later is the warmup length, not 192.
FIRST_DECISION_INDEX = CANONICAL_WARMUP_BARS
SIMULATION_START = 0
SIMULATION_STOP = CLOCK_ROWS
MEASUREMENT_START = FIRST_DECISION_INDEX
MEASUREMENT_STOP = CLOCK_ROWS
PURGE_BARS = CANONICAL_WARMUP_BARS
EMBARGO_BARS = CANONICAL_WARMUP_BARS

MIN_TRADES = 30
PRIMARY_METRIC = "marked_net_usdt_return"

# ── Budget (điểm 2) ─────────────────────────────────────────────────────
# Cell timeout là thời gian chạy thị trường của một cell. Mỗi cell còn có
# overhead orchestration (setup, teardown, kiểm evidence, cleanup) riêng.
# Không tự tăng giới hạn launcher; hard cap là tổng trần toàn run.
CELL_ORCHESTRATION_OVERHEAD_SECONDS = 60
PLUS_CELL_TOTAL_SECONDS = CELL_TIMEOUT_SECONDS + CELL_ORCHESTRATION_OVERHEAD_SECONDS
PLUS_RUN_TOTAL_SECONDS = (
    len(TARGETS) * len(PLUS_ARMS) * len(PLUS_COSTS) * PLUS_FOLDS * PLUS_CELL_TOTAL_SECONDS
)
PLUS_RUN_BUFFER_SECONDS = 600
PLUS_RUN_HARD_CAP_SECONDS = PLUS_RUN_TOTAL_SECONDS + PLUS_RUN_BUFFER_SECONDS
PLUS_BUDGET_SECONDS = PLUS_RUN_TOTAL_SECONDS

PLUS_BUDGET = {
    "cell_timeout_seconds": CELL_TIMEOUT_SECONDS,
    "cell_orchestration_overhead_seconds": CELL_ORCHESTRATION_OVERHEAD_SECONDS,
    "cell_total_seconds": PLUS_CELL_TOTAL_SECONDS,
    "cell_count": len(TARGETS) * len(PLUS_ARMS) * len(PLUS_COSTS) * PLUS_FOLDS,
    "run_total_seconds": PLUS_RUN_TOTAL_SECONDS,
    "run_buffer_seconds": PLUS_RUN_BUFFER_SECONDS,
    "run_hard_cap_seconds": PLUS_RUN_HARD_CAP_SECONDS,
    "launcher_limit_not_increased": True,
}

# Eligibility / timing / cost / budget (development bundle only)
PLUS_ELIGIBILITY = {
    "min_trades": MIN_TRADES,
    "primary_metric": PRIMARY_METRIC,
    "hold_bars": HOLD_BARS,
    "fill_delay_bars": FILL_DELAY_BARS,
    "threshold": ENTRY_THRESHOLD,
    "shared_across_targets": SHARED_ACROSS_TARGETS,
}
PLUS_TIMING = {
    "simulation_start": SIMULATION_START,
    "simulation_stop": SIMULATION_STOP,
    "measurement_start": MEASUREMENT_START,
    "measurement_stop": MEASUREMENT_STOP,
    "purge_bars": PURGE_BARS,
    "embargo_bars": EMBARGO_BARS,
    "holdout_start_utc": HOLDOUT_START_UTC,
    "holdout_end_utc": HOLDOUT_END_UTC,
    "clock_first_utc": CLOCK_FIRST_UTC,
    "clock_last_utc": CLOCK_LAST_UTC,
}

LAUNCHER_BLOCKERS: tuple[str, ...] = ()
CLOSED_RULES_NOT_CONTROLS: tuple[str, ...] = (
    "H-PULLBACK-01",
    "H-PULLBACK-TEMPORAL-01",
    "H-VOLBREAK-01",
    "H-LIQ-REVERSAL-01",
    "MA20/50",
    "RSI_FIXED",
)


def arm_features(arm: str) -> tuple[str, ...]:
    """Context columns an arm may read. Own price history is outside this list."""
    if arm == "A0":
        return ()
    if arm == "A1":
        return C1_FEATURES
    if arm == "A2":
        return C1_FEATURES + C2_FEATURES
    if arm == PLUS_ARM:
        return ("btc_simple_return_24",)
    raise ValueError("arm must be A0, A1, A2 or PLUS")


def cell_count() -> int:
    """One fixed config per target. Personalization cells are not included."""
    return len(TARGETS) * len(ARMS) * len(COSTS) * FOLDS


def cell_budget_seconds() -> int:
    return cell_count() * CELL_TIMEOUT_SECONDS


def plus_cell_count() -> int:
    """BASE vs PLUS cell count (development bundle only)."""
    return len(TARGETS) * len(PLUS_ARMS) * len(PLUS_COSTS) * PLUS_FOLDS


def plus_cell_budget_seconds() -> int:
    return plus_cell_count() * CELL_TIMEOUT_SECONDS


def execution_authority() -> dict[str, object]:
    """The rule is frozen. This module still does not authorize a run."""
    if ENTRY_RULE_ID in CLOSED_RULES_NOT_CONTROLS:
        raise RuntimeError("closed rule reused as the context control")
    return {
        "market_run": False,
        "live_authorized": False,
        "qualification_possible_on_this_bundle": False,
        "reason": "PATH_READY_RUN_NOT_STARTED",
        "blockers": LAUNCHER_BLOCKERS,
        "entry_rule_id": ENTRY_RULE_ID,
        "rule_fingerprint": rule_fingerprint(),
        "feature_contract": CONTRACT_VERSION,
        "feature_fingerprint": contract_fingerprint(),
    }


def plussed_execution_authority() -> dict[str, object]:
    """Authority snapshot for the BASE vs PLUS contrast.

    Not a market authorization. IMPROVES on this bundle is diagnostic only
    and does not qualify a strategy or authorize confirmation.
    """
    return {
        "market_run": False,
        "live_authorized": False,
        "qualification_possible_on_this_bundle": False,
        "reason": "PATH_READY_RUN_NOT_STARTED",
        "blockers": LAUNCHER_BLOCKERS,
        "entry_rule_id": PLUS_ENTRY_RULE_ID,
        "rule_fingerprint": rule_fingerprint_plus(),
        "feature_contract": CONTRACT_VERSION,
        "feature_fingerprint": contract_fingerprint(),
        "arms": PLUS_ARMS,
        "costs": PLUS_COSTS,
        "targets": TARGETS,
        "folds": PLUS_FOLDS,
        "cell_count": plus_cell_count(),
        "budget_seconds": PLUS_BUDGET["run_total_seconds"],
        "budget": PLUS_BUDGET,
        "eligibility": PLUS_ELIGIBILITY,
        "timing": PLUS_TIMING,
        "packet_contract": plus_packet_contract(),
        "verdict_criteria": {
            "improves_label": "marked_net_return higher AND max_drawdown <= previous AND sharpe > 0 AND marked_net_return > 0 AND same_execution_path",
            "improves_is_not_qualification": True,
            "holdout_not_opened": True,
            "six_comparisons": "ETH/USDT × {1x, 2x, slip_stress} and BNB/USDT × {1x, 2x, slip_stress}",
            "per_pair_reporting": True,
        },
    }


def keep_added_arm(
    *,
    trades: int,
    sharpe: float,
    marked_net_return: float,
    previous_marked_net_return: float,
    max_drawdown: float,
    previous_max_drawdown: float,
    same_execution_path: bool,
) -> str:
    """Preregistered label for one pair at one cost. Not an engine gate.

    ``IMPROVES`` is only a diagnostic label. It does not qualify a strategy
    and it does not authorize confirmation.
    """
    values = (
        sharpe, marked_net_return, previous_marked_net_return,
        max_drawdown, previous_max_drawdown,
    )
    if any(not math.isfinite(value) for value in values):
        return "INCONCLUSIVE"
    if trades < MIN_TRADES:
        return "INCONCLUSIVE"
    if not same_execution_path:
        return "INCONCLUSIVE"
    delta = marked_net_return - previous_marked_net_return
    if delta <= 0.0 or max_drawdown > previous_max_drawdown:
        return "NO_INCREMENT"
    if sharpe <= 0.0 or marked_net_return <= 0.0:
        return "NO_CONFIRMATION"
    return "IMPROVES"


# ── Verdict criteria for the six pair–cost comparisons (điểm 4) ─────────
PLUS_VERDICT_RULES: dict[str, object] = {
    "comparison_count": 6,
    "comparison_shape": "pair × cost",
    "pairs": list(TARGETS),
    "costs": list(PLUS_COSTS),
    "arms": {"previous": BASE_ARM, "candidate": PLUS_ARM},
    "reporting": "per_pair_per_cost",
    "aggregate_rule": "never_pick_a_winning_cell_and_call_the_hypothesis_improves",
    "improves": {
        "label": "IMPROVES",
        "conditions": [
            "marked_net_return > previous_marked_net_return",
            "max_drawdown <= previous_max_drawdown",
            "sharpe > 0",
            "marked_net_return > 0",
            "same_execution_path",
        ],
    },
    "non_improves": {
        "NO_INCREMENT": "candidate not strictly better on primary metric or drawdown worse",
        "NO_CONFIRMATION": "sharpe <= 0 or marked_net_return <= 0",
        "INCONCLUSIVE": "missing context, insufficient sample, timeout, or non-finite values",
    },
    "edge_handling": {
        "missing_context": "missing context ⇒ HOLD for that cell ⇒ trades may fall below min_trades ⇒ INCONCLUSIVE, never counted as IMPROVES",
        "insufficient_sample": "trades < min_trades ⇒ INCONCLUSIVE, excluded from improvement verdict",
        "timeout": "cell timeout ⇒ cell marked TIMEOUT, excluded from improvement verdict",
        "divergent_results": "ETH IMPROVES and BNB not IMPROVES ⇒ hypothesis is NOT IMPROVES overall; each pair reported separately",
    },
}


def plus_code_sha() -> dict[str, str]:
    """Source SHA for every module bound into the packet contract."""
    files = [
        "src/trading_agent/research/btc_context_rule.py",
        "src/trading_agent/research/btc_context_prereg.py",
        "src/trading_agent/research/btc_context_features.py",
        "src/trading_agent/strategies/own24_fill_context.py",
    ]
    return {
        str(f): hashlib.sha256(Path(f).read_bytes()).hexdigest()
        for f in files
    }


PLUS_TEST_IDS: tuple[str, ...] = (
    "tests/test_btc_context_rule.py::test_a0_follows_only_the_pair_return",
    "tests/test_btc_context_rule.py::test_context_gates_do_not_buy_a_falling_usdt_price",
    "tests/test_btc_context_rule.py::test_each_arm_adds_one_predicate",
    "tests/test_btc_context_rule.py::test_missing_context_blocks_the_arm_that_needs_it",
    "tests/test_btc_context_rule.py::test_own_return_ignores_a_future_close",
    "tests/test_btc_context_rule.py::test_hold_matches_the_signal_horizon_and_keeps_native_delay",
    "tests/test_btc_context_rule.py::test_plus_predicate_btc_return_only_changes_decision",
    "tests/test_btc_context_rule.py::test_plus_missing_btc_return_holds_without_log1_or_vol",
    "tests/test_btc_context_rule.py::test_plus_predicate_uses_distinct_identity_and_fingerprint",
)


def plus_packet_contract() -> dict[str, object]:
    """Fully bound packet contract before execution.

    Binds source, rule/fill fingerprints, input hash, windows, costs,
    command and authorization. This contract does NOT grant execution
    authority; plussed_execution_authority() still returns
    market_run=False / live_authorized=False.
    """
    return {
        "packet_id": "btc-plus-contrast-20261028",
        "revision": "a550dad0",
        "source": plus_code_sha(),
        "rule_fingerprint": rule_fingerprint_plus(),
        "fill_policy_fingerprint": fill_policy_fingerprint_plus(),
        "input_hash": {sym: meta["sha256"] for sym, meta in INPUTS.items()},
        "windows": {
            "simulation": [SIMULATION_START, SIMULATION_STOP],
            "measurement": [MEASUREMENT_START, MEASUREMENT_STOP],
            "holdout": [HOLDOUT_START_UTC, HOLDOUT_END_UTC],
        },
        "costs": list(PLUS_COSTS),
        "command": "pytest tests/research/test_btc_plus_contrast.py -k plus --timeout=600",
        "authorization": {
            "market_run": False,
            "live_authorized": False,
            "qualification_possible_on_this_bundle": False,
            "reason": "PATH_READY_RUN_NOT_STARTED",
        },
        "verdict_rules": PLUS_VERDICT_RULES,
        "test_ids": PLUS_TEST_IDS,
    }
