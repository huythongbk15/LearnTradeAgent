"""Operational testing must not silently grant research promotion authority."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.promotion_store import (
    PromotionRecord,
    PromotionStateStore,
    is_stage_compatible,
)
from trading_agent.authority.resolver import RuntimeStrategyResolver
from trading_agent.research.promotion import ResearchStage


@pytest.mark.parametrize(
    "stage", [ResearchStage.EXPLORATORY, ResearchStage.RESEARCH_VALIDATED]
)
@pytest.mark.parametrize(
    "environment",
    [
        Environment.PAPER,
        Environment.TESTNET,
        Environment.SHADOW,
        Environment.CANARY,
        Environment.PRODUCTION,
    ],
)
def test_unqualified_case_cannot_resolve_even_with_cached_runtime(
    tmp_path, stage, environment
):
    # Fixture-only record, never the real research registry or case packet.
    store = PromotionStateStore(tmp_path / "promotions.db")
    store.upsert(PromotionRecord(artifact_id="boundary-fixture", stage=stage))
    resolver = RuntimeStrategyResolver(
        AuthorityConfig.for_environment(environment), store
    )
    promoted = SimpleNamespace(artifact_id="boundary-fixture")
    resolver._cache[resolver._make_key("BNB/USDT", "4h", environment)] = promoted
    resolver._bind_strategy = Mock(
        side_effect=AssertionError("must reject before binding")
    )

    assert resolver.resolve(promoted, "BNB/USDT", "4h", environment) is None
    resolver._bind_strategy.assert_not_called()
    assert store.get_stage("boundary-fixture") == stage


@pytest.mark.parametrize("environment", list(Environment))
def test_missing_promotion_fails_closed(tmp_path, environment):
    store = PromotionStateStore(tmp_path / "empty.db")
    resolver = RuntimeStrategyResolver(
        AuthorityConfig.for_environment(environment), store
    )
    assert (
        resolver.resolve(
            SimpleNamespace(artifact_id="missing"), "BNB/USDT", "4h", environment
        )
        is None
    )


def test_research_permission_is_not_paper_or_production_permission():
    assert is_stage_compatible(ResearchStage.EXPLORATORY, "research")
    assert not is_stage_compatible(ResearchStage.EXPLORATORY, "paper")
    assert is_stage_compatible(ResearchStage.PAPER_ELIGIBLE, "paper")
    assert not is_stage_compatible(ResearchStage.PAPER_ELIGIBLE, "testnet")
    assert not is_stage_compatible(ResearchStage.PRODUCTION, "production")
