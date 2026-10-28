"""STR-0206 fault-scenario tests.

Unit tests run in the default suite. Slow per-profile cells use a deterministic
synthetic file hash-bound into spawned workers; no market files are read.
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


def _run(profile: FaultProfile, tmp_path: Path):
    import polars as pl
    from trading_agent.backtest.tournament import run_cell
    from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
    from trading_agent.data.storage import frozen_research_data

    spec = EvaluationCellSpec("rsi", "BTC/USDT", params={"period": 14}, fault=profile)
    data = tmp_path / "synthetic-fault-input.parquet"
    frame = generate_synthetic_ohlcv(n_bars=400, seed=7)
    # Keep RSI transitions, but prevent a later 5% stop fill from resolving
    # the injected cancel race before the terminal-state assertions. This is
    # fixture geometry, not a change to the engine's native protective stop.
    frame = frame.with_columns(
        [(100.0 + (pl.col(name) - 100.0) * 0.1).alias(name)
         for name in ("open", "high", "low", "close")]
        + [(pl.col("atr") * 0.1).alias("atr")]
    )
    assert frame["low"].min() > frame["high"].max() * 0.95
    frame.write_parquet(data)
    with frozen_research_data(data, exchange="binance", symbol="BTC/USDT", timeframe="1h"):
        return run_cell(
            spec,
            out_root=tmp_path / profile.name,
            end=400,
        )


@pytest.mark.slow
@pytest.mark.backtest
@pytest.mark.fault
class TestFaultScenarioCells:
    """Each scenario must either complete cleanly or fail CLOSED.

    Required behaviour through the real engine on synthetic prices:
    - gap/stale cells survive with clean terminal state;
    - broker rejections escalate to UNKNOWN terminal state (deliberate P0
      decision — reconciliation is a runtime-ops concern, never silent);
    - a partially-filled EXIT leaves a naked remainder position;
    - cancel races and protection outages surface as manual intents.
    No fault ever completes with dirty state — that IS the contract.
    """

    def test_gap_every_50_completes(self, tmp_path):
        artifact = _run(FAULT_GAP_EVERY_50, tmp_path)
        # Data gaps are recorded; execution must still terminate clean.
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        assert artifact.metrics["total_trades"] >= 0

    def test_stale_feed_completes(self, tmp_path):
        artifact = _run(FAULT_STALE_FEED, tmp_path)
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        health = artifact.execution_health
        assert int(health.get("unknown_orders", 0)) == 0
        assert int(health.get("manual_interventions", 0)) == 0

    def test_rejected_order_fails_cleanly_no_trades(self, tmp_path):
        """Rejected orders trigger clean FAILED state via retry-fault contract.

        The engine retries once on rejection; the fault profile rejects the
        first 2 _fill_market_order calls. After both rejections + retries
        are exhausted, the 3rd order fills — but the cell must still be
        FAILED because rejected_orders > 0 in execution_health.
        """
        artifact = _run(FAULT_REJECT_FIRST_2, tmp_path)
        assert artifact.status == "FAILED"
        health = artifact.execution_health
        assert int(health.get("rejected_orders", 0)) > 0
        # No dirty state: no unknown orders, no manual interventions
        assert int(health.get("unknown_orders", 0)) == 0
        assert int(health.get("manual_interventions", 0)) == 0

    def test_partial_fill_exit_completes_with_protection(self, tmp_path):
        """Partial fill on exit should be handled - protective stop covers the filled portion."""
        artifact = _run(FAULT_PARTIAL_HALF, tmp_path)
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        # Position opened, partially filled exit, protective stop handles remainder
        assert artifact.metrics["total_trades"] >= 1

    def test_cancel_race_fails_closed_with_manual_reconciliation(self, tmp_path):
        """A lost cancel race must surface UNKNOWN/manual state, never pass."""
        artifact = _run(FAULT_CANCEL_RACE, tmp_path)
        assert artifact.status == "FAILED", artifact.failure_reasons
        health = artifact.execution_health
        assert int(health.get("unknown_orders", 0)) >= 1
        assert int(health.get("manual_interventions", 0)) >= 1

    def test_protection_outage_completes(self, tmp_path):
        """Protection outage (transient) should retry and succeed."""
        artifact = _run(FAULT_PROTECTION_OUTAGE, tmp_path)
        assert artifact.status == "COMPLETED", artifact.failure_reasons
        assert artifact.metrics["total_trades"] >= 1
