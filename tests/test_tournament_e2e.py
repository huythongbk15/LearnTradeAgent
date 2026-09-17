"""End-to-end integration test for the tournament audit + portfolio pipeline.

Exercises:
  1. SelectionAudit entries populated with varied routing decisions
  2. audit_report CLI generates reports from the audit DB
  3. PortfolioRiskGate reduces exposure when portfolio caps are breached
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_agent.authority.adaptive_router import HandoverState, RoutingDecision
from trading_agent.authority.portfolio_risk_gate import PortfolioRiskGate, PortfolioRiskGateConfig
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import RegimePosterior

NOW = datetime(2024, 7, 15, 10, 0, tzinfo=UTC)


def _make_decision(i: int, ts: datetime) -> RoutingDecision:
    return RoutingDecision(
        symbol="BTC/USDT", timeframe="1h", observed_at=ts,
        posterior_fingerprint=f"fp{i}",
        policy_ids=("tf_default",),
        incumbent_strategy_id="mean_reversion" if i > 0 else None,
        challenger_strategy_id="trend_following",
        chosen_strategy_id="trend_following" if i % 2 == 0 else "mean_reversion",
        chosen_policy_id="tf_default",
        chosen_params={},
        handover_state=HandoverState.ACTIVATE if i % 5 == 0 else HandoverState.STABLE,
        reason="ACTIVATE" if i % 5 == 0 else "STABLE",
        allow_new_exposure=True,
        exposure_multiplier=0.5,
        candidate_score=8.5,
        incumbent_score=7.0 if i > 0 else None,
        position_owner_strategy_id=None,
    )


class TestTournamentE2E:
    def test_audit_report_after_routing(self, tmp_path: Path):
        """SelectionAudit entries → audit_report CLI produces valid JSON."""
        audit = SelectionAudit(tmp_path / "tournament_audit.sqlite3")

        for i in range(50):
            ts = NOW + timedelta(hours=i)
            posterior = RegimePosterior(
                p_trend=0.7 if i % 2 == 0 else 0.4,
                p_mean_reversion=0.1 if i % 2 == 0 else 0.3,
                p_high_vol=0.1, p_crisis=0.05 if i % 2 == 0 else 0.1, p_other=0.05 if i % 2 == 0 else 0.1,
                model_id="test", generated_at=ts,
            )
            decision = _make_decision(i, ts)
            audit.append(
                decision, posterior,
                regime_tags={"trend": "bullish" if i % 2 == 0 else "bearish"},
                anomaly_flags=["volume_anomaly"] if i % 3 == 0 else [],
                confidence_adjustment=0.95 if i % 2 == 0 else 1.1,
                shadow_sharpe=0.5 if i % 2 == 0 else -0.2,
            )
        audit.close()

        # Run audit_report CLI
        result = subprocess.run(
            [sys.executable, "scripts/audit_report.py",
             str(tmp_path / "tournament_audit.sqlite3"),
             "--format", "json"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        report = json.loads(result.stdout)

        assert report["entry_count"] == 50
        assert report["strategy_switches"]["total_switches"] > 0
        assert report["confidence_adjustment"]["mean"] != 0.0
        assert report["anomaly_analysis"]["anomaly_rate_pct"] > 0.0
        assert "STABLE" in report["reasons"]
        assert "ACTIVATE" in report["reasons"]

    def test_portfolio_gate_modifies_decision(self, tmp_path: Path):
        """PortfolioRiskGate reduces exposure when caps are breached."""
        config = PortfolioRiskGateConfig(
            max_total_exposure=0.5,
            max_symbol_exposure=0.4,
        )
        gate = PortfolioRiskGate(config=config)

        # Pre-fill state to simulate existing portfolio exposure
        state = gate.get_state("1h")
        state.symbol_exposure["ETH/USDT"] = 0.4

        ts = NOW
        posterior = RegimePosterior(
            p_trend=0.7, p_mean_reversion=0.15, p_high_vol=0.1,
            p_crisis=0.03, p_other=0.02,
            model_id="test", generated_at=ts,
        )

        decision = RoutingDecision(
            symbol="BTC/USDT", timeframe="1h", observed_at=ts,
            posterior_fingerprint="fp0",
            policy_ids=("tf_default",),
            incumbent_strategy_id=None, challenger_strategy_id="tf",
            chosen_strategy_id="tf", chosen_policy_id="tf_default",
            chosen_params={}, handover_state=HandoverState.ACTIVATE,
            reason="ACTIVATE", allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=8.5, incumbent_score=None,
            position_owner_strategy_id=None,
        )

        ctx = MarketContext(
            regime_tags={"trend": "up"},
            anomaly_flags=[],
            confidence_adjustment=1.0,
        )

        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
            market_context=ctx, symbol_bar_return=0.01,
        )

        # With 0.4 ETH + 0.5 BTC = 0.9, cap at 0.5 → BTC gets 0.1
        assert result.exposure_multiplier <= 0.1 + 0.001
        assert "PORTFOLIO" in result.reason
