"""Tests for PortfolioRiskGate — cross-asset risk management."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trading_agent.authority.adaptive_router import (
    HandoverState, RoutingDecision,
)
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.authority.portfolio_risk_gate import (
    PortfolioRiskGate, PortfolioRiskGateConfig,
)
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import RegimePosterior

NOW = datetime(2024, 7, 15, 10, 0, tzinfo=UTC)


def _make_posterior() -> RegimePosterior:
    return RegimePosterior(
        p_trend=0.5, p_mean_reversion=0.3, p_high_vol=0.1, p_crisis=0.05, p_other=0.05,
        model_id="test", generated_at=NOW,
    )


def _make_decision(symbol: str = "BTC/USDT", exposure: float = 0.5) -> RoutingDecision:
    return RoutingDecision(
        symbol=symbol, timeframe="1h", observed_at=NOW,
        posterior_fingerprint="fp0",
        policy_ids=("tf_default",),
        incumbent_strategy_id=None,
        challenger_strategy_id="tf",
        chosen_strategy_id="tf",
        chosen_policy_id="tf_default", chosen_params={},
        handover_state=HandoverState.ACTIVATE, reason="ACTIVATE",
        allow_new_exposure=True, exposure_multiplier=exposure,
        candidate_score=8.5, incumbent_score=None,
        position_owner_strategy_id=None,
    )


class TestPortfolioRiskGate:
    def test_no_gate_without_data(self, tmp_path: Path):
        """No returns tracked → decision passes through unchanged."""
        gate = PortfolioRiskGate()
        decision = _make_decision()
        posterior = _make_posterior()

        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
        )
        assert result.exposure_multiplier == 0.5
        assert result.reason == "ACTIVATE"

    def test_symbol_exposure_cap(self, tmp_path: Path):
        """Per-symbol exposure exceeds cap → scaled down."""
        config = PortfolioRiskGateConfig(max_symbol_exposure=0.3)
        gate = PortfolioRiskGate(config=config)

        # Pre-set state: symbol already has high exposure
        state = gate.get_state("1h")
        state.symbol_exposure["BTC/USDT"] = 0.8

        decision = _make_decision(symbol="BTC/USDT", exposure=0.5)
        posterior = _make_posterior()
        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
        )

        assert result.exposure_multiplier <= config.max_symbol_exposure + 0.001
        assert "PORTFOLIO" in result.reason

    def test_total_exposure_cap(self, tmp_path: Path):
        """Total portfolio exposure exceeds cap → scaled down."""
        config = PortfolioRiskGateConfig(max_total_exposure=0.6, max_symbol_exposure=1.0)
        gate = PortfolioRiskGate(config=config)

        state = gate.get_state("1h")
        # Pre-existing exposure in other symbol
        state.symbol_exposure["ETH/USDT"] = 0.5

        decision = _make_decision(symbol="BTC/USDT", exposure=0.5)
        posterior = _make_posterior()
        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
        )

        total = state.total_exposure
        assert total <= config.max_total_exposure + 0.001
        assert "PORTFOLIO: total exposure" in result.reason

    def test_circuit_breaker_demote(self, tmp_path: Path):
        """Portfolio Sharpe below threshold → circuit breaker triggers."""
        config = PortfolioRiskGateConfig(
            portfolio_sharpe_threshold=-0.50,
            min_shadow_bars=5,
            portfolio_sharpe_warmup=0,
        )
        gate = PortfolioRiskGate(config=config)

        state = gate.get_state("1h")
        # Feed varied negative returns to drive Sharpe below -0.50
        state.symbol_returns["BTC/USDT"] = [
            -0.03, -0.01, -0.02, -0.04, -0.01,
            -0.02, -0.03, -0.01, -0.02, -0.04,
        ]
        state.symbol_bar_count["BTC/USDT"] = len(state.symbol_returns["BTC/USDT"])

        assert state.portfolio_sharpe is not None
        assert state.portfolio_sharpe < -0.50

        decision = _make_decision(symbol="BTC/USDT", exposure=0.5)
        posterior = _make_posterior()
        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
        )

        assert result.exposure_multiplier < 0.5
        assert "circuit breaker" in result.reason

    def test_correlation_penalty(self, tmp_path: Path):
        """High cross-asset signal alignment → correlation penalty applied."""
        config = PortfolioRiskGateConfig()
        gate = PortfolioRiskGate(config=config)

        ctx = MarketContext(
            cross_asset_signals={
                "ETH/USDT": {"signal": "BUY", "confidence": 0.7},
                "SOL/USDT": {"signal": "BUY", "confidence": 0.6},
                "BNB/USDT": {"signal": "BUY", "confidence": 0.8},
            },
            details={"signal": "BUY"},
        )

        decision = _make_decision(exposure=0.5)
        posterior = _make_posterior()
        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
            market_context=ctx, symbol_bar_return=0.01,
        )

        # 3/3 aligned → high penalty
        assert result.exposure_multiplier < 0.5
        assert "correlation" in result.reason.lower() or "corr" in result.reason

    def test_no_correlation_penalty_when_unaligned(self, tmp_path: Path):
        """Mixed signals → no correlation penalty."""
        config = PortfolioRiskGateConfig()
        gate = PortfolioRiskGate(config=config)

        ctx = MarketContext(
            cross_asset_signals={
                "ETH/USDT": {"signal": "SELL", "confidence": 0.7},
                "SOL/USDT": {"signal": "BUY", "confidence": 0.6},
                "BNB/USDT": {"signal": "SELL", "confidence": 0.8},
            },
            details={"signal": "BUY"},
        )

        decision = _make_decision(exposure=0.5)
        posterior = _make_posterior()
        result = gate.evaluate(
            symbol="BTC/USDT", timeframe="1h",
            decision=decision, posterior=posterior,
            market_context=ctx, symbol_bar_return=0.01,
        )

        # 1/3 aligned (ETH, BNB sell vs BTC buy) → below 50% threshold → no penalty
        assert result.exposure_multiplier == 0.5
        assert "corr" not in result.reason.lower()

    def test_portfolio_state_tracking(self, tmp_path: Path):
        """PortfolioState tracks returns and exposure correctly."""
        gate = PortfolioRiskGate()

        for i in range(10):
            decision = _make_decision()
            posterior = _make_posterior()
            gate.evaluate(
                symbol="BTC/USDT", timeframe="1h",
                decision=decision, posterior=posterior,
                symbol_bar_return=0.001 * i,
            )

        state = gate.get_state("1h")
        assert state.symbol_bar_count["BTC/USDT"] == 10
        assert len(state.symbol_returns["BTC/USDT"]) == 10
        assert state.symbol_exposure["BTC/USDT"] > 0

    def test_reset(self, tmp_path: Path):
        """reset() clears all state."""
        gate = PortfolioRiskGate()
        gate.get_state("1h").symbol_exposure["BTC/USDT"] = 0.5
        gate.reset()
        assert len(gate._state) == 0

    def test_audit_store_integration(self, tmp_path: Path):
        """PortfolioRiskGate can be initialized with SelectionAudit."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        gate = PortfolioRiskGate(audit_store=audit)
        assert gate.audit_store is audit
        audit.close()
