"""Integration: ResearchMemory (LLM enrichment) → Tournament routing → SelectionAudit.

Verifies that MarketContext enrichment from the LLM layer flows correctly
into tournament routing decisions, and that those decisions are auditable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_agent.authority.adaptive_router import (
    HandoverState, RoutingDecision,
)
from trading_agent.authority.selection_audit import SelectionAudit
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.llm.research_memory import ResearchMemory
from trading_agent.ml.regime_detection import RegimePosterior

NOW = datetime(2024, 7, 15, 10, 0, tzinfo=UTC)


def _make_posterior() -> RegimePosterior:
    return RegimePosterior(
        p_trend=0.7, p_mean_reversion=0.1, p_high_vol=0.1, p_crisis=0.05, p_other=0.05,
        model_id="test", generated_at=NOW,
    )


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


class TestResearchMemoryTournamentIntegration:
    def test_context_flows_from_memory_to_audit(self, tmp_path: Path):
        """MarketContext stored in ResearchMemory is retrievable and its
        enrichment metadata appears in SelectionAudit entries."""
        # 1. Store MarketContext in ResearchMemory
        memory = ResearchMemory(tmp_path / "research_memory.sqlite3")
        original_ctx = MarketContext(
            regime_tags={"trend": "bullish"},
            anomaly_flags=["volume_anomaly"],
            confidence_adjustment=0.85,
            reasoning="Volume spike during uptrend.",
        )
        memory.store("BTC/USDT", "1h", NOW, original_ctx)

        # 2. Retrieve it (simulates replay path)
        retrieved = memory.retrieve("BTC/USDT", "1h", NOW, deterministic=False)
        assert retrieved is not None
        assert retrieved.regime_tags == {"trend": "bullish"}
        assert retrieved.confidence_adjustment == 0.85

        # 3. Pass to tournament routing → SelectionAudit
        audit = SelectionAudit(tmp_path / "selection_audit.sqlite3")
        posterior = _make_posterior()
        decision = _make_decision()

        entry = audit.append(
            decision=decision,
            posterior=posterior,
            regime_tags=retrieved.regime_tags,
            anomaly_flags=retrieved.anomaly_flags,
            confidence_adjustment=retrieved.confidence_adjustment,
        )
        audit.close()

        # 4. Query SelectionAudit and verify enrichment metadata preserved
        audit2 = SelectionAudit(tmp_path / "selection_audit.sqlite3")
        entries = audit2.query(symbol="BTC/USDT")
        audit2.close()

        assert len(entries) == 1
        e = entries[0]
        assert e.regime_tags == {"trend": "bullish"}
        assert e.anomaly_flags == ["volume_anomaly"]
        assert e.confidence_adjustment == 0.85
        assert e.chosen_strategy_id == "trend_following"

    def test_deterministic_vs_llm_contexts_separate(self, tmp_path: Path):
        """ResearchMemory stores deterministic and LLM contexts separately."""
        memory = ResearchMemory(tmp_path / "rm.sqlite3")

        llm_ctx = MarketContext(
            regime_tags={"momentum": "diverging"},
            anomaly_flags=["cvd_price_divergence"],
            confidence_adjustment=0.75,
            reasoning="CVD diverges from price.",
        )
        det_ctx = MarketContext(
            regime_tags={"momentum": "neutral"},
            anomaly_flags=[],
            confidence_adjustment=1.0,
            reasoning="Fallback.",
        )

        memory.store("BTC/USDT", "1h", NOW, llm_ctx, deterministic=False)
        memory.store("BTC/USDT", "1h", NOW + timedelta(hours=1), det_ctx, deterministic=True)

        llm_retrieved = memory.retrieve("BTC/USDT", "1h", NOW, deterministic=False)
        det_retrieved = memory.retrieve("BTC/USDT", "1h", NOW + timedelta(hours=1), deterministic=True)

        assert llm_retrieved is not None
        assert det_retrieved is not None
        assert llm_retrieved.confidence_adjustment == 0.75
        assert det_retrieved.confidence_adjustment == 1.0

    def test_audit_query_by_entropy(self, tmp_path: Path):
        """SelectionAudit can be queried by posterior entropy (uncertain regimes)."""
        memory = ResearchMemory(tmp_path / "rm.sqlite3")
        audit = SelectionAudit(tmp_path / "sa.sqlite3")

        # High entropy posterior (uncertain regime)
        uncertain = RegimePosterior(
            p_trend=0.4, p_mean_reversion=0.2, p_high_vol=0.2, p_crisis=0.1, p_other=0.1,
            model_id="test", generated_at=NOW,
        )

        decision = _make_decision()
        audit.append(decision, uncertain)
        audit.close()

        audit2 = SelectionAudit(tmp_path / "sa.sqlite3")
        entries = audit2.query(min_posterior_entropy=0.1)
        audit2.close()

        assert len(entries) == 1
        assert entries[0].posterior_entropy > 0.0
