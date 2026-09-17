"""Tests for the SelectionAudit analysis CLI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.audit_report import generate_report, _format_markdown
from trading_agent.authority.adaptive_router import (
    HandoverState, RoutingDecision,
)
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.ml.regime_detection import RegimePosterior

NOW = datetime(2024, 7, 15, 10, 0, tzinfo=UTC)


def _populate_audit(audit: SelectionAudit, count: int = 10) -> None:
    """Seed audit with varied routing decisions."""
    strategies = ["trend_following", "mean_reversion"]
    anomalies = ["cvd_price_divergence", "volume_anomaly"]

    for i in range(count):
        ts = NOW + timedelta(hours=i)
        posterior = RegimePosterior(
            p_trend=0.5, p_mean_reversion=0.3, p_high_vol=0.1, p_crisis=0.05, p_other=0.05,
            model_id="test", generated_at=ts,
        )
        sid = strategies[i % 2]
        decision = RoutingDecision(
            symbol="BTC/USDT", timeframe="1h", observed_at=ts,
            posterior_fingerprint=f"fp{i}",
            policy_ids=(f"{sid}_default_1h",),
            incumbent_strategy_id=strategies[(i - 1) % 2] if i > 0 else None,
            challenger_strategy_id=sid,
            chosen_strategy_id=sid,
            chosen_policy_id=f"{sid}_default_1h",
            chosen_params={},
            handover_state=HandoverState.ACTIVATE if i % 3 == 0 else HandoverState.STABLE,
            reason="ACTIVATE" if i % 3 == 0 else "STABLE",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=8.5,
            incumbent_score=7.0 if i > 0 else None,
            position_owner_strategy_id=None,
        )
        audit.append(
            decision, posterior,
            regime_tags={"trend": "bullish" if i % 2 == 0 else "bearish"},
            anomaly_flags=[anomalies[i % 2]],  # alternate cvd / volume
            confidence_adjustment=0.85 if i % 2 == 0 else 1.2,
            shadow_sharpe=0.5 if i % 2 == 0 else -0.3,
        )


class TestAuditReportCLI:
    def test_report_basic_structure(self, tmp_path: Path):
        """Report has all required top-level keys."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=10)
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")

        assert report["entry_count"] == 10
        assert "strategy_switches" in report
        assert "regime_coverage" in report
        assert "anomaly_analysis" in report
        assert "confidence_adjustment" in report
        assert "posterior_entropy" in report
        assert "shadow_sharpe" in report
        assert "date_range" in report
        assert "reasons" in report

    def test_strategy_switch_count(self, tmp_path: Path):
        """Switch count is correct for alternating strategies."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=10)
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")
        # Alternating trend_following / mean_reversion → 9 switches
        assert report["strategy_switches"]["total_switches"] == 9

    def test_confidence_stats(self, tmp_path: Path):
        """Confidence adjustment stats are computed correctly."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=10)
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")
        ca = report["confidence_adjustment"]
        assert ca["min"] == 0.85
        assert ca["max"] == 1.2
        assert ca["below_1_count"] == 5
        assert ca["above_1_count"] == 5

    def test_anomaly_counts(self, tmp_path: Path):
        """Anomaly flags are counted correctly."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=10)
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")
        ac = report["anomaly_analysis"]
        assert ac["flag_counts"]["cvd_price_divergence"] == 5
        assert ac["flag_counts"]["volume_anomaly"] == 5
        assert ac["anomaly_rate_pct"] == 100.0

    def test_empty_result(self, tmp_path: Path):
        """Empty DB returns error dict with 0 entries."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")
        assert report["entry_count"] == 0
        assert "error" in report

    def test_markdown_output(self, tmp_path: Path):
        """Markdown formatter produces valid output."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=10)
        audit.close()

        report = generate_report(audit._db_path, symbol="BTC/USDT")
        md = _format_markdown(report)

        assert "# SelectionAudit Report" in md
        assert "Entries analyzed" in md
        assert "10" in md
        assert "Strategy Switches" in md
        assert "Anomaly Flags" in md
        assert "Confidence Adjustment" in md

    def test_date_range_filter(self, tmp_path: Path):
        """Date range filter works correctly."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        _populate_audit(audit, count=20)
        audit.close()

        start = NOW + timedelta(hours=5)
        end = NOW + timedelta(hours=10)
        report = generate_report(
            audit._db_path, symbol="BTC/USDT", start=start, end=end,
        )
        assert report["entry_count"] == 6  # hours 5-10 inclusive
