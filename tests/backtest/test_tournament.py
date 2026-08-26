"""Phase S2 tournament core tests — cell specs, canonical signal series,
fail-closed artifact contract (STR-0202/0203/0207/0209 slices)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from trading_agent.backtest.tournament import (
    BASE_COMMISSION,
    BASE_SLIPPAGE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    CostScenario,
    EvaluationCellSpec,
    canonical_signal_series,
)
from trading_agent.strategies.canonical import (
    LegacyDataFrameAdapter,
)
from trading_agent.strategies.rsi import RsiStrategy

_OBS_AT = datetime(2026, 1, 20, tzinfo=UTC)


def _synthetic_frame() -> pl.DataFrame:
    seg_flat = [100.0] * 60
    seg_down = [100.0 - 2 * i for i in range(1, 31)]
    seg_up = [40.0 + 3 * i for i in range(1, 61)]
    closes = seg_flat + seg_down + seg_up + [220.0] * 60
    frame = (
        pl.DataFrame({"close": closes})
        .with_columns(open=pl.col("close").shift(1).fill_null(pl.col("close")))
        .with_columns(
            high=pl.max_horizontal("open", "close") * 1.002,
            low=pl.min_horizontal("open", "close") * 0.998,
            volume=pl.lit(10.0),
        )
    )
    start = _OBS_AT - timedelta(hours=len(closes))
    return frame.with_columns(
        (pl.lit(start) + pl.duration(hours=pl.int_range(len(closes)))).alias("time")
    ).select("time", "open", "high", "low", "close", "volume")


# ── EvaluationCellSpec ─────────────────────────────────────────────────────


class TestEvaluationCellSpec:
    def test_cell_id_deterministic_and_discriminating(self):
        a = EvaluationCellSpec("rsi", "BTC/USDT", params={"period": 14})
        b = EvaluationCellSpec("rsi", "BTC/USDT", params={"period": 14})
        c = EvaluationCellSpec("rsi", "BTC/USDT", params={"period": 21})
        d = EvaluationCellSpec(
            "rsi", "BTC/USDT", params={"period": 14}, cost_scenario=SCENARIO_DOUBLE
        )
        assert a.cell_id == b.cell_id
        assert a.cell_id != c.cell_id
        assert a.cell_id != d.cell_id

    def test_cost_scenario_multipliers(self):
        assert SCENARIO_DOUBLE.commission == pytest.approx(BASE_COMMISSION * 2)
        assert SCENARIO_SLIPPAGE_STRESS.slippage == pytest.approx(BASE_SLIPPAGE * 5)
        assert SCENARIO_SLIPPAGE_STRESS.commission == pytest.approx(BASE_COMMISSION)

    def test_invalid_cost_multiplier_rejected(self):
        with pytest.raises(ValueError):
            CostScenario("bad", fee_multiplier=0.0)


# ── STR-0203 canonical signal series ──────────────────────────────────────


class TestCanonicalSignalSeries:
    def test_signals_match_parity_expectations(self):
        registry_adapter = LegacyDataFrameAdapter(
            RsiStrategy({"period": 8, "oversold": 35, "overbought": 65}),
            model_artifact_id="m",
            warmup_bars=10,
            strategy_id="rsi_test",
        )
        frame = _synthetic_frame()
        signals = canonical_signal_series(
            registry_adapter, frame, warmup_bars=10, symbol="BTC/USDT"
        )
        assert len(signals) == frame.height
        assert all(value == 0 for value in signals[:11])  # warm-up stays flat

        # Down-leg must print BUYs, up-leg must print SELLs.
        down_zone = signals[70:95]
        up_zone = signals[100:130]
        assert any(v == 1 for v in down_zone)
        assert any(v == -1 for v in up_zone)

        # Determinism: identical input → identical series.
        again = canonical_signal_series(
            LegacyDataFrameAdapter(
                RsiStrategy({"period": 8, "oversold": 35, "overbought": 65}),
                model_artifact_id="m",
                warmup_bars=10,
                strategy_id="rsi_test",
            ),
            frame,
            warmup_bars=10,
            symbol="BTC/USDT",
        )
        assert signals == again


# ── STR-0209 fail-closed artifact contract ────────────────────────────────


def _descriptor_stub():
    from trading_agent.strategies.canonical import StrategyDescriptor

    return StrategyDescriptor(
        strategy_id="rsi",
        semantic_version="1.0.0",
        code_sha="a" * 64,
        horizon_bars=1,
        warmup_bars=16,
    )


def _report(metrics_overrides=None, health_overrides=None):
    metrics = {
        "total_return_pct": 5.0,
        "sharpe": 1.2,
        "max_drawdown_pct": -4.0,
        "total_trades": 12,
        "win_rate_pct": 58.3,
        "profit_factor": 1.7,
    }
    metrics.update(metrics_overrides or {})
    health = {
        "unknown_orders": 0,
        "manual_interventions": 0,
        "unprotected_positions": [],
    }
    health.update(health_overrides or {})
    return {
        "metrics": metrics,
        "execution_health": health,
        "commit_sha": "deadbeef",
    }


class TestFailClosedArtifacts:
    def _artifact(self, report, spec=None):
        from trading_agent.backtest.tournament import _artifact_from_report

        spec = spec or EvaluationCellSpec("rsi", "BTC/USDT")
        return _artifact_from_report(
            spec, _descriptor_stub(), Path("/tmp/cell"), report, "datasha"
        )

    def test_clean_report_completes(self):
        artifact = self._artifact(_report())
        assert artifact.status == "COMPLETED"
        assert artifact.failure_reasons == ()

    def test_missing_metric_fails_not_zeroed(self):
        report = _report({"profit_factor": None})
        artifact = self._artifact(report)
        assert artifact.status == "FAILED"
        assert "missing_metric:profit_factor" in artifact.failure_reasons
        assert "profit_factor" not in artifact.metrics  # never defaulted to 0

    def test_infinite_pf_with_no_losses_is_evidence_not_failure(self):
        # 12 trades, zero average loss → PF is literally +inf.
        report = _report({"profit_factor": None, "average_loss": 0.0})
        artifact = self._artifact(report)
        assert artifact.status == "COMPLETED"
        assert artifact.metrics["profit_factor"] == "inf"

    def test_zero_trades_still_fails(self):
        report = _report(
            {
                "profit_factor": None,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "sharpe": None,
                "average_loss": 0.0,
            }
        )
        artifact = self._artifact(report)
        assert artifact.status == "FAILED"

    def test_nonfinite_metric_fails(self):
        report = _report({"sharpe": float("nan")})
        artifact = self._artifact(report)
        assert artifact.status == "FAILED"
        assert any(
            r.startswith("nonfinite_metric:sharpe") for r in artifact.failure_reasons
        )

    @pytest.mark.parametrize(
        "health_overrides",
        [
            {"unknown_orders": 1},
            {"manual_interventions": 2},
            {"unprotected_positions": ["SOLUSDT"]},
        ],
    )
    def test_dirty_terminal_state_fails(self, health_overrides):
        artifact = self._artifact(_report(health_overrides=health_overrides))
        assert artifact.status == "FAILED"

    def test_empty_report_fails(self):
        artifact = self._artifact({})
        assert artifact.status == "FAILED"
        assert len(artifact.failure_reasons) >= 6

    def test_artifact_id_content_addressed(self):
        a = self._artifact(_report())
        b = self._artifact(_report())
        c = self._artifact(_report({"sharpe": 9.9}))
        assert a.artifact_id == b.artifact_id
        assert a.artifact_id != c.artifact_id
        assert a.artifact_id.startswith("sha256:")

    def test_unknown_strategy_fails_closed(self):
        from trading_agent.backtest.tournament import run_cell

        artifact = run_cell(
            EvaluationCellSpec("ghost_strategy", "BTC/USDT"),
            out_root=Path("/tmp/tourney_test"),
        )
        assert artifact.status == "FAILED"
        assert "not allowlisted" in artifact.failure_reasons[0]
