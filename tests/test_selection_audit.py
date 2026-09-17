"""Tests for the SelectionAudit module — immutable decision trail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_agent.authority.adaptive_router import (
    HandoverState, RoutingDecision,
)
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.ml.regime_detection import RegimePosterior

NOW = datetime(2024, 7, 15, 10, 0, tzinfo=UTC)


def _make_posterior() -> RegimePosterior:
    p = RegimePosterior(
        p_trend=0.7, p_mean_reversion=0.1, p_high_vol=0.1, p_crisis=0.05, p_other=0.05,
        model_id="test", generated_at=NOW,
    )
    return p


def _make_decision() -> RoutingDecision:
    return RoutingDecision(
        symbol="BTC/USDT",
        timeframe="1h",
        observed_at=NOW,
        posterior_fingerprint="abc123",
        policy_ids=("trend_following_default_1h",),
        incumbent_strategy_id=None,
        challenger_strategy_id="trend_following",
        chosen_strategy_id="trend_following",
        chosen_policy_id="trend_following_default_1h",
        chosen_params={},
        handover_state=HandoverState.ACTIVATE,
        reason="ACTIVATE",
        allow_new_exposure=True,
        exposure_multiplier=0.5,
        candidate_score=8.5,
        incumbent_score=None,
        position_owner_strategy_id=None,
    )


class TestSelectionAudit:
    def test_append_and_query(self, tmp_path: Path):
        """Append a decision and retrieve it via query."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()
        ctx = MarketContext(
            regime_tags={"trend": "up"},
            anomaly_flags=[],
            confidence_adjustment=0.9,
            reasoning="Clear uptrend.",
        )

        entry = audit.append(
            decision=decision,
            posterior=posterior,
            regime_tags=ctx.regime_tags,
            anomaly_flags=ctx.anomaly_flags,
            confidence_adjustment=ctx.confidence_adjustment,
        )

        audit.close()
        assert entry.entry_id is not None
        assert entry.chosen_strategy_id == "trend_following"
        assert entry.confidence_adjustment == 0.9

    def test_query_by_symbol(self, tmp_path: Path):
        """Query returns entries for a specific symbol."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()

        audit.append(decision, posterior)
        audit.close()

        audit2 = SelectionAudit(tmp_path / "audit.sqlite3")
        entries = audit2.query(symbol="BTC/USDT")
        audit2.close()

        assert len(entries) == 1
        assert entries[0].symbol == "BTC/USDT"

    def test_query_by_strategy(self, tmp_path: Path):
        """Query by chosen_strategy_id."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()

        d1 = _make_decision()
        audit.append(d1, posterior)

        d2 = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=NOW + timedelta(hours=1),
            posterior_fingerprint="def456",
            policy_ids=(),
            incumbent_strategy_id="trend_following",
            challenger_strategy_id="mean_reversion",
            chosen_strategy_id="mean_reversion",
            chosen_policy_id="mean_reversion_default_1h",
            chosen_params={},
            handover_state=HandoverState.STABLE,
            reason="SWITCH",
            allow_new_exposure=False,
            exposure_multiplier=0.3,
            candidate_score=7.0,
            incumbent_score=5.0,
            position_owner_strategy_id=None,
        )
        audit.append(d2, posterior)
        audit.close()

        audit3 = SelectionAudit(tmp_path / "audit.sqlite3")
        entries = audit3.query(chosen_strategy_id="mean_reversion")
        audit3.close()

        assert len(entries) == 1
        assert entries[0].chosen_strategy_id == "mean_reversion"

    def test_immutable_entry(self, tmp_path: Path):
        """AuditEntry is frozen — cannot modify after creation."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()

        entry = audit.append(decision, posterior)
        audit.close()

        with pytest.raises((AttributeError, Exception)):
            entry.chosen_strategy_id = "tampered"  # type: ignore[misc]

    def test_entry_id_deterministic(self, tmp_path: Path):
        """Same (symbol, timeframe, observed_at, decision_id) → same entry_id."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()

        entry1 = audit.append(decision, posterior)
        audit.close()

        audit2 = SelectionAudit(tmp_path / "audit.sqlite3")
        entry2 = audit2.append(decision, posterior)
        audit2.close()

        assert entry1.entry_id == entry2.entry_id

    def test_posterior_entropy_recorded(self, tmp_path: Path):
        """Posterior entropy is stored correctly."""
        audit = SelectionAudit(tmp_path / "audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()

        entry = audit.append(decision, posterior)
        audit.close()

        assert entry.posterior_entropy == pytest.approx(posterior.normalized_entropy)
        assert entry.posterior_fingerprint == "abc123"
