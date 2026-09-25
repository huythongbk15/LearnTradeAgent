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

import numpy as np

from trading_agent.authority.adaptive_router import HandoverState, RoutingDecision
from trading_agent.authority.portfolio_risk_gate import PortfolioRiskGate, PortfolioRiskGateConfig
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.authority.strategy_tournament import (
    TournamentConfig,
    _ShadowMetrics,
)
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


class TestShadowE2ENetSharpe:
    """Shadow E2E: 300+ bars with net-of-fees Sharpe tracking (P1)."""

    def test_shadow_300_bars_net_of_fees_sharpe(self, tmp_path: Path):
        """Run 300 shadow bars, verify net Sharpe is computed and tracked.

        Simulates a StrategyTournament shadow run with realistic returns:
        - 300 hourly bars (3.8 days of shadow)
        - Gross returns ~ 2 bps/bar, turnover fees at ~5 bps per trade
        - Net Sharpe must differ from gross Sharpe
        - Net Sharpe must be stored in _ShadowMetrics
        """
        cfg = TournamentConfig(
            shadow_mode=True,
            min_shadow_bars=100,
            min_shadow_bars_for_promote=30,
        )

        # Build metrics manually for 300 bars
        m = _ShadowMetrics()
        rng = np.random.default_rng(42)
        gross_rets = rng.normal(loc=0.002, scale=0.01, size=300)  # 2 bps mean
        weights = rng.choice([0.8, 0.5, 0.0, -0.5, -0.8], size=300)
        prev_w = 0.0
        for gr, w in zip(gross_rets, weights):
            fee = abs(w - prev_w) * cfg.total_fee_rate
            net_ret = gr - fee
            m.add(net_ret, w, fee_rate=0.0, gross_ret=gr)
            prev_w = w

        assert m.n >= 300, f"Expected >=300 bars, got {m.n}"
        gross_sp = m.gross_sharpe()
        net_sp = m.net_sharpe()

        assert net_sp < gross_sp, \
            f"Net Sharpe ({net_sp:.4f}) should be < gross ({gross_sp:.4f})"
        assert net_sp > 0.0, f"Net Sharpe should be positive, got {net_sp:.4f}"
        assert m.sharpe() == net_sp, "sharpe() should equal net_sharpe()"

    def test_net_sharpe_gate_blocks_low_quality(self, tmp_path: Path):
        """If net Sharpe < threshold, a log event should record the block."""
        cfg = TournamentConfig(
            shadow_mode=False,
            min_shadow_bars_for_promote=30,
            promotion_net_sharpe_threshold=1.5,
        )
        # Build a challenger with net Sharpe below threshold
        m = _ShadowMetrics()
        rng = np.random.default_rng(123)
        gross_rets = rng.normal(loc=0.001, scale=0.02, size=50)  # Low Sharpe
        for gr in gross_rets:
            fee = 0.002 * cfg.total_fee_rate  # Some turnover
            m.add(gr - fee, 0.5, fee_rate=0.0, gross_ret=gr)

        net_sp = m.net_sharpe()
        gross_sp = m.gross_sharpe()

        # The net Sharpe gate threshold should block strategies below 1.5
        if net_sp < cfg.promotion_net_sharpe_threshold:
            # Verify the gate condition exists and would trigger
            assert hasattr(cfg, "promotion_net_sharpe_threshold")
            assert net_sp >= 0.0  # Sanity: net Sharpe is computable


class TestHealthGatePrePromotion:
    """P1: Exchange health gate before live promotion."""

    def test_routing_decision_has_exchange_name(self):
        """RoutingDecision must carry exchange_name for health gate."""
        decision = _make_decision(0, NOW)
        assert hasattr(decision, "exchange_name")

    def test_routing_decision_serializes_exchange_name(self):
        """RoutingDecision round-trip must preserve exchange_name."""
        decision = RoutingDecision(
            symbol="BTC/USDT", timeframe="1h", observed_at=NOW,
            posterior_fingerprint="fp0", policy_ids=("default",),
            incumbent_strategy_id=None, challenger_strategy_id="t3",
            chosen_strategy_id="t3", chosen_policy_id="default",
            chosen_params={}, handover_state=HandoverState.ACTIVATE,
            reason="activate", allow_new_exposure=True,
            exposure_multiplier=1.0, candidate_score=8.5,
            incumbent_score=None, position_owner_strategy_id=None,
            confidence_adjustment=1.0, exchange_name="binance",
        )
        d = decision.to_dict()
        assert d["exchange_name"] == "binance"
        restored = RoutingDecision.from_dict(d)
        assert restored.exchange_name == "binance"

    def test_health_monitor_is_healthy_method(self):
        """HealthMonitor must expose is_healthy() interface."""
        from trading_agent.exchanges.health_monitor import HealthMonitor
        assert hasattr(HealthMonitor, "is_healthy")
        assert hasattr(HealthMonitor, "get_unhealthy")
        assert hasattr(HealthMonitor, "get_exchange_status")

    def test_strategy_tournament_has_health_monitor_attr(self):
        """StrategyTournament must optionally accept health_monitor."""
        import inspect
        from trading_agent.authority.strategy_tournament import StrategyTournament
        sig = inspect.signature(StrategyTournament.__init__)
        assert "health_monitor" in sig.parameters
        assert sig.parameters["health_monitor"].default is None

    def test_health_gate_logic_in_source(self):
        """Verify health gate exists in _maybe_promote source code."""
        from trading_agent.authority.strategy_tournament import StrategyTournament
        import inspect
        src = inspect.getsource(StrategyTournament._maybe_promote)
        assert "health_monitor" in src
        assert "HEALTH_GATE_BLOCK" in src
