"""STR-0206 fault-scenario tests.

Unit tests run in the default suite; the per-profile integration cells hit
real binance data and are marked ``slow`` (run explicitly via
``pytest -m slow`` before a baseline tournament).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.backtest.tournament import (
    ALL_FAULT_PROFILES,
    FAULT_CANCEL_RACE,
    FAULT_GAP_EVERY_50,
    FAULT_NONE,
    FAULT_PARTIAL_HALF,
    FAULT_PROTECTION_OUTAGE,
    FAULT_REJECT_FIRST_2,
    FAULT_STALE_FEED,
    EvaluationCellSpec,
)


# ── Unit: profile construction & cell identity ────────────────────────────


class TestFaultProfiles:
    def test_none_profile_is_inactive(self):
        assert not FAULT_NONE.active
        assert FAULT_NONE.name == "none"

    def test_all_presets_are_active_and_named(self):
        names = {p.name for p in ALL_FAULT_PROFILES}
        assert {
            "none",
            "gap50",
            "stale",
            "reject2",
            "partial_half",
            "cancel_race",
            "protect_outage",
        } <= names
        for profile in ALL_FAULT_PROFILES:
            if profile is FAULT_NONE:
                continue
            assert profile.active

    def test_cell_id_suffix_only_for_active_faults(self):
        plain = EvaluationCellSpec("rsi", "BTC/USDT")
        faulty = EvaluationCellSpec("rsi", "BTC/USDT", fault=FAULT_PARTIAL_HALF)
        assert "__f" not in plain.cell_id
        assert faulty.cell_id.endswith("__fpartial_half")
        assert plain.cell_id != faulty.cell_id

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"drop_gap_bars_every": -1},
            {"stale_windows": -3},
            {"partial_fill_fraction": 0.0},
            {"partial_fill_fraction": 1.5},
            {"reject_first_n_orders": -1},
        ],
    )
    def test_invalid_faults_rejected(self, kwargs):
        with pytest.raises(ValueError):
            FaultProfile(**kwargs)

    def test_deterministic_identity(self):
        a = EvaluationCellSpec("bbands", "SOL/USDT", fault=FAULT_CANCEL_RACE)
        b = EvaluationCellSpec("bbands", "SOL/USDT", fault=FAULT_CANCEL_RACE)
        assert a.cell_id == b.cell_id


from trading_agent.backtest.tournament import FaultProfile  # noqa: E402


# ── Integration: each profile through the real execution path ─────────────


def _run(profile: FaultProfile):
    from trading_agent.backtest.tournament import run_cell

    spec = EvaluationCellSpec("rsi", "BTC/USDT", params={"period": 14}, fault=profile)
    return run_cell(
        spec,
        out_root=Path("data/backtests/tournament_fault_tests"),
        end=400,
    )


@pytest.mark.slow
class TestFaultScenarioCells:
    """Each scenario must either complete cleanly or fail CLOSED.

    Verified behaviour on real binance data (see tournament_index.json):
    - gap/stale cells survive with clean terminal state;
    - broker rejections escalate to UNKNOWN terminal state (deliberate P0
      decision — reconciliation is a runtime-ops concern, never silent);
    - a partially-filled EXIT leaves a naked remainder position;
    - cancel races and protection outages surface as manual intents.
    No fault ever completes with dirty state — that IS the contract.
    """

    def test_gap_every_50_completes(self):
        artifact = _run(FAULT_GAP_EVERY_50)
        # Data gaps are recorded; execution must still terminate clean.
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        assert artifact.metrics["total_trades"] >= 0

    def test_stale_feed_completes(self):
        artifact = _run(FAULT_STALE_FEED)
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        health = artifact.execution_health
        assert int(health.get("unknown_orders", 0)) == 0
        assert int(health.get("manual_interventions", 0)) == 0

    def test_rejected_order_fails_closed_unknown_state(self):
        artifact = _run(FAULT_REJECT_FIRST_2)
        assert artifact.status == "FAILED"
        reasons = " | ".join(artifact.failure_reasons)
        assert "unknown_orders" in reasons or "manual_interventions" in reasons, (
            artifact.failure_reasons
        )

    def test_partial_fill_exit_fails_closed_naked_remainder(self):
        artifact = _run(FAULT_PARTIAL_HALF)
        assert artifact.status == "FAILED"
        reasons = " | ".join(artifact.failure_reasons)
        assert "unprotected_positions" in reasons, artifact.failure_reasons

    def test_cancel_race_fails_closed_manual_intent(self):
        artifact = _run(FAULT_CANCEL_RACE)
        assert artifact.status == "FAILED"
        reasons = " | ".join(artifact.failure_reasons)
        assert "manual_interventions" in reasons or "unknown_orders" in reasons, (
            artifact.failure_reasons
        )

    def test_protection_outage_fails_closed(self):
        artifact = _run(FAULT_PROTECTION_OUTAGE)
        assert artifact.status == "FAILED"
        reasons = " | ".join(artifact.failure_reasons)
        assert (
            "manual_interventions" in reasons
            or "unknown_orders" in reasons
            or "unprotected_positions" in reasons
            or "execution_crash" in reasons
        ), artifact.failure_reasons
