"""S6 shared-capital constraint tests independent of broker I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.portfolio import (
    AllocationRequest,
    PortfolioAllocator,
    PortfolioSnapshot,
    ReconciliationState,
)


NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _risk(ask: float):
    return SimpleNamespace(
        allowed_target_exposure=ask,
        max_new_exposure=ask,
        reduce_only=False,
    )


def _snapshot(*, exposures: dict[str, float] | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        equity=100_000.0,
        available_cash=100_000.0,
        positions={},
        symbol_exposures=exposures or {},
        gross_exposure=sum((exposures or {}).values()),
        untracked_valued=True,
        reconciliation_state=ReconciliationState.RECONCILED,
        observed_at=NOW,
    )


def _request(
    strategy: str,
    symbol: str,
    ask: float,
    **kwargs,
) -> AllocationRequest:
    return AllocationRequest(
        strategy_id=strategy,
        symbol=symbol,
        risk_decision=_risk(ask),
        current_exposure=0.0,
        equity=100_000.0,
        available_cash=100_000.0,
        portfolio_exposure=0.0,
        **kwargs,
    )


def _allocator() -> PortfolioAllocator:
    config = AuthorityConfig.for_environment(Environment.PAPER)
    config.exposure.max_portfolio_exposure = 0.80
    config.exposure.max_single_strategy_exposure = 0.60
    config.exposure.max_single_symbol_exposure = 0.50
    config.exposure.max_correlated_exposure = 0.60
    return PortfolioAllocator(config)


def test_shared_symbol_requests_are_aggregated_and_capped():
    allocator = _allocator()
    requests = [
        _request("strategy_a", "BTC/USDT", 0.40, correlation_cluster="MAJORS"),
        _request("strategy_b", "BTC/USDT", 0.40, correlation_cluster="MAJORS"),
    ]

    outcome = allocator.allocate_batch(requests, _snapshot())
    vector = allocator.build_target_vector(outcome, _snapshot(), "cycle-1")

    assert vector.targets["BTC/USDT"] == pytest.approx(0.50)
    assert outcome.approved_by_symbol["BTC/USDT"] == pytest.approx(0.50)
    assert outcome.approved_by_strategy["strategy_a"] == pytest.approx(0.25)
    assert outcome.approved_by_strategy["strategy_b"] == pytest.approx(0.25)
    assert "aggregate_constraint_scale" in outcome.entries[0].reason


def test_liquidity_participation_and_no_trade_band_are_enforced():
    allocator = _allocator()
    request = _request(
        "strategy_a",
        "ETH/USDT",
        0.25,
        average_daily_notional=1_000.0,
        max_order_participation=0.01,
    )
    outcome = allocator.allocate_batch([request], _snapshot())
    assert outcome.total_approved == pytest.approx(0.0001)

    band_request = _request(
        "strategy_b",
        "SOL/USDT",
        0.01,
        no_trade_band=0.02,
    )
    band_outcome = allocator.allocate_batch([band_request], _snapshot())
    assert band_outcome.total_approved == 0.0
    assert band_outcome.total_requested == 0.0


def test_opposing_forecasts_are_netted_before_long_only_target_emission():
    allocator = _allocator()
    long_request = _request(
        "strategy_a", "BTC/USDT", 0.30, desired_exposure=0.30
    )
    opposing_request = _request(
        "strategy_b", "BTC/USDT", 0.20, desired_exposure=-0.20
    )
    outcome = allocator.allocate_batch(
        [long_request, opposing_request], _snapshot()
    )
    vector = allocator.build_target_vector(outcome, _snapshot(), "cycle-net")

    assert outcome.approved_by_symbol["BTC/USDT"] == pytest.approx(0.50)
    assert outcome.net_by_symbol["BTC/USDT"] == pytest.approx(0.10)
    assert vector.targets["BTC/USDT"] == pytest.approx(0.10)


def test_duplicate_request_key_fails_closed_and_input_order_is_irrelevant():
    allocator = _allocator()
    duplicate = _request("strategy_a", "BTC/USDT", 0.1)
    with pytest.raises(ValueError, match="duplicate"):
        allocator.allocate_batch([duplicate, duplicate], _snapshot())

    first = _request("strategy_a", "BTC/USDT", 0.2)
    second = _request("strategy_b", "ETH/USDT", 0.2)
    left = allocator.allocate_batch([first, second], _snapshot())
    right = allocator.allocate_batch([second, first], _snapshot())
    assert left.approved_by_symbol == right.approved_by_symbol
    assert left.scale_factor == pytest.approx(right.scale_factor)
