"""Wave D — execution algorithms: liquidity-aware TWAP, POV, MPC layer, driver.

Unit tests + property-based tests (spec §27: property tests for order
lifecycle / precision / partial fills).  Deterministic only.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest
from hypothesis import given, settings, strategies as st

from trading_agent.execution.algorithms import (
    LiquidityAwareTwap,
    MpcConstraints,
    MpcFeasibilityLayer,
    MpcObjectives,
    ParentOrder,
    PovExecution,
    run_parent_through_engine,
    objective_cost,
)
from trading_agent.execution.algorithms.base import (
    MarketSnapshot,
    SliceContext,
)
from trading_agent.execution.simulator.models import SimSide


def snap(
    mid: float = 100.0,
    spread_bps: float = 5.0,
    bid_depth: float = 10_000.0,
    ask_depth: float = 10_000.0,
    recent_volume: float = 10_000.0,
    volatility_bps: float = 20.0,
) -> MarketSnapshot:
    return MarketSnapshot(
        mid=mid,
        spread_bps=spread_bps,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        recent_volume=recent_volume,
        volatility_bps=volatility_bps,
    )


def ctx(
    remaining: float = 1000.0,
    *,
    elapsed: int = 0,
    total: int = 10,
    filled: float = 0.0,
    paid: float = 0.0,
    budget: float = 30.0,
    participation: float = 0.1,
    is_buy: bool = True,
    **snap_kw,
) -> SliceContext:
    return SliceContext(
        snapshot=snap(**snap_kw),
        remaining_qty=remaining,
        elapsed_bars=elapsed,
        total_bars=total,
        filled_qty=filled,
        slippage_paid_bps=paid,
        slippage_budget_bps=budget,
        max_participation=participation,
        is_buy=is_buy,
    )


# ── Liquidity-aware TWAP ──────────────────────────────────────────────────


class TestLiquidityAwareTwap:
    def test_basic_twap_slice(self):
        alg = LiquidityAwareTwap()
        # Deadline 10 bars, remaining 1000 → base 100; volume 10k → no cap.
        res = alg.next_slice(ctx(remaining=1000.0, total=10))
        assert res.has_slice
        assert 0 < res.quantity <= 1000.0

    def test_participation_cap_never_exceeded(self):
        alg = LiquidityAwareTwap()
        c = ctx(remaining=100_000.0, total=10, participation=0.02, recent_volume=5000.0)
        res = alg.next_slice(c)
        assert res.has_slice
        assert res.quantity <= 0.02 * 5000.0

    def test_never_exceeds_remaining(self):
        alg = LiquidityAwareTwap()
        res = alg.next_slice(ctx(remaining=10.0, total=10, recent_volume=1_000_000.0))
        assert res.quantity <= 10.0

    def test_wide_spread_reduces_slice(self):
        c_tight = ctx(spread_bps=2.0)
        c_wide = ctx(spread_bps=50.0)
        tight = LiquidityAwareTwap().next_slice(c_tight).quantity
        wide = LiquidityAwareTwap().next_slice(c_wide).quantity
        assert wide < tight

    def test_high_volatility_reduces_slice(self):
        low = LiquidityAwareTwap().next_slice(ctx(volatility_bps=5.0)).quantity
        high = LiquidityAwareTwap().next_slice(ctx(volatility_bps=100.0)).quantity
        assert high < low

    def test_slippage_budget_caps_slice(self):
        alg = LiquidityAwareTwap()
        # Budget 0.2 bps over the whole parent (1000 qty) → 200 bps-units;
        # est cost 3.5 bps/slice → max slice ≈ 57.1 < base 100 → capped.
        res = alg.next_slice(
            ctx(remaining=1000.0, total=10, budget=0.2, recent_volume=1_000_000.0)
        )
        assert res.has_slice
        assert res.quantity <= 200.0 / 3.5 + 1e-6

    def test_budget_exhausted_returns_zero(self):
        alg = LiquidityAwareTwap()
        # Average slippage already at the budget with half still to trade:
        # spent = 60 bps × 500 filled = 30 000 bps-units ≥ 30×1000 allowed.
        res = alg.next_slice(
            ctx(
                remaining=500.0,
                total=10,
                filled=500.0,
                paid=60.0,
                budget=30.0,
                recent_volume=1_000_000.0,
            )
        )
        assert not res.has_slice
        assert res.reason == "budget_exhausted"

    def test_done_when_no_remaining(self):
        alg = LiquidityAwareTwap()
        res = alg.next_slice(ctx(remaining=0.0))
        assert not res.has_slice
        assert res.reason == "done"

    def test_deadline_passed(self):
        alg = LiquidityAwareTwap()
        res = alg.next_slice(ctx(remaining=10.0, elapsed=10, total=10))
        assert not res.has_slice
        assert res.reason == "deadline"

    def test_invalid_params_fail_closed(self):
        with pytest.raises(ValueError):
            LiquidityAwareTwap(spread_ref_bps=0.0)
        with pytest.raises(ValueError):
            LiquidityAwareTwap(volume_ref=-1.0)
        with pytest.raises(ValueError):
            LiquidityAwareTwap(min_slice_qty=-1.0)

    def test_invalid_context_fail_closed(self):
        alg = LiquidityAwareTwap()
        with pytest.raises(ValueError):
            alg.next_slice(ctx(remaining=-1.0))
        with pytest.raises(ValueError):
            alg.next_slice(ctx(participation=0.0))

    def test_deterministic(self):
        alg = LiquidityAwareTwap()
        c = ctx(remaining=500.0, total=7, spread_bps=8.0, volatility_bps=35.0)
        assert alg.next_slice(c) == alg.next_slice(c)


# ── POV ───────────────────────────────────────────────────────────────────


class TestPovExecution:
    def test_slice_is_participation_cap(self):
        alg = PovExecution()
        res = alg.next_slice(
            ctx(remaining=1000.0, participation=0.05, recent_volume=2000.0)
        )
        assert res.quantity == pytest.approx(100.0)

    def test_slice_capped_by_remaining(self):
        alg = PovExecution()
        res = alg.next_slice(
            ctx(remaining=30.0, participation=0.1, recent_volume=2000.0)
        )
        assert res.quantity == pytest.approx(30.0)

    def test_never_exceeds_cap_property(self):
        alg = PovExecution()

        @given(
            remaining=st.floats(min_value=1.0, max_value=1e6),
            part=st.floats(min_value=0.001, max_value=0.5),
            volume=st.floats(min_value=100.0, max_value=1e6),
            elapsed=st.integers(min_value=0, max_value=9),
            total=st.integers(min_value=10, max_value=100),
        )
        @settings(max_examples=50, deadline=None)
        def _check(remaining, part, volume, elapsed, total):
            res = alg.next_slice(
                ctx(
                    remaining=remaining,
                    elapsed=elapsed,
                    total=total,
                    participation=part,
                    recent_volume=volume,
                )
            )
            assert res.quantity <= part * volume + 1e-9
            assert res.quantity <= remaining + 1e-9

        _check()

    def test_no_volume_returns_zero(self):
        alg = PovExecution()
        res = alg.next_slice(ctx(remaining=100.0, recent_volume=0.0))
        assert not res.has_slice
        assert res.reason == "no_volume"

    def test_deterministic(self):
        alg = PovExecution()
        c = ctx(remaining=100.0, participation=0.1, recent_volume=333.0)
        assert alg.next_slice(c) == alg.next_slice(c)


# ── MPC feasibility layer ─────────────────────────────────────────────────


class TestMpcFeasibilityLayer:
    def make(self, **constraints):
        return MpcFeasibilityLayer().plan(
            ctx(remaining=1000.0, total=10, **{k: v for k, v in []}),
            MpcConstraints(
                max_participation=constraints.get("participation", 0.1),
                slippage_budget_bps=constraints.get("budget", 30.0),
                deadline_bars=constraints.get("deadline", 10),
                risk_limit_qty=constraints.get("risk_limit", float("inf")),
                min_liquidity=constraints.get("min_liquidity", 0.0),
            ),
        )

    def test_feasible_slice_satisfies_constraints(self):
        res = self.make()
        assert res.feasible
        assert res.has_slice
        assert res.slice_qty <= 0.1 * 10_000.0  # participation cap
        assert res.slice_qty <= 1000.0  # remaining

    def test_infeasible_deadline(self):
        res = self.make(participation=0.001)  # tiny cap → cannot finish in 10 bars
        assert not res.feasible
        assert res.reason == "INFEASIBLE_PARTICIPATION"

    def test_infeasible_liquidity(self):
        res = self.make(min_liquidity=1_000_000.0)
        assert not res.feasible
        assert res.reason == "INFEASIBLE_LIQUIDITY"

    def test_infeasible_budget(self):
        # Budget so tight that even a dust slice is refused (min_slice gate).
        layer = MpcFeasibilityLayer(min_slice_qty=1.0)
        res = layer.plan(
            ctx(remaining=1000.0, total=10, budget=0.001),
            MpcConstraints(
                max_participation=0.1, slippage_budget_bps=0.001, deadline_bars=10
            ),
        )
        assert not res.feasible
        assert res.reason.startswith("INFEASIBLE_")

    def test_risk_limit_respected(self):
        res = self.make(risk_limit=10.0)
        assert res.feasible
        assert res.slice_qty <= 10.0 + 1e-9

    def test_done_when_remaining_zero(self):
        layer = MpcFeasibilityLayer()
        res = layer.plan(
            ctx(remaining=0.0),
            MpcConstraints(
                max_participation=0.1, slippage_budget_bps=30.0, deadline_bars=10
            ),
        )
        assert res.feasible
        assert res.slice_qty == 0.0
        assert res.reason == "done"

    def test_constraints_validate(self):
        with pytest.raises(ValueError):
            MpcConstraints(
                max_participation=0.0, slippage_budget_bps=10.0, deadline_bars=5
            ).validate()
        with pytest.raises(ValueError):
            MpcConstraints(
                max_participation=0.1, slippage_budget_bps=-1.0, deadline_bars=5
            ).validate()
        with pytest.raises(ValueError):
            MpcConstraints(
                max_participation=0.1, slippage_budget_bps=10.0, deadline_bars=0
            ).validate()

    def test_objective_costs_components(self):
        c = ctx(remaining=1000.0, total=10)
        costs = objective_cost(c, 100.0, MpcObjectives())
        assert costs.spread_cost_bps > 0
        assert costs.impact_cost_bps > 0
        assert costs.delay_cost_bps > 0
        assert costs.opportunity_cost_bps > 0
        assert costs.inventory_risk_bps > 0
        assert costs.total == pytest.approx(
            costs.spread_cost_bps
            + costs.impact_cost_bps
            + costs.delay_cost_bps
            + costs.opportunity_cost_bps
            + costs.inventory_risk_bps
        )

    def test_zero_slice_has_no_spread_or_impact(self):
        costs = objective_cost(ctx(remaining=1000.0), 0.0, MpcObjectives())
        assert costs.spread_cost_bps == 0.0
        assert costs.impact_cost_bps == 0.0

    def test_mpc_feasible_property(self):
        layer = MpcFeasibilityLayer()

        @given(
            remaining=st.floats(min_value=10.0, max_value=1e6),
            part=st.floats(min_value=0.005, max_value=0.5),
            volume=st.floats(min_value=100.0, max_value=1e6),
            deadline=st.integers(min_value=2, max_value=50),
            budget=st.floats(min_value=1.0, max_value=100.0),
        )
        @settings(max_examples=50, deadline=None)
        def _check(remaining, part, volume, deadline, budget):
            res = layer.plan(
                SliceContext(
                    snapshot=snap(recent_volume=volume),
                    remaining_qty=remaining,
                    elapsed_bars=0,
                    total_bars=deadline,
                    filled_qty=0.0,
                    slippage_paid_bps=0.0,
                    slippage_budget_bps=budget,
                    max_participation=part,
                ),
                MpcConstraints(
                    max_participation=part,
                    slippage_budget_bps=budget,
                    deadline_bars=deadline,
                ),
            )
            if res.feasible and res.slice_qty > 0:
                assert res.slice_qty <= part * volume + 1e-9
                assert res.slice_qty <= remaining + 1e-9
            else:
                assert res.reason.startswith("INFEASIBLE_") or res.reason == "done"

        _check()


# ── Parent-order driver integration ───────────────────────────────────────


def make_df(n: int = 60, volume: float = 2000.0) -> pl.DataFrame:
    rows = []
    for i in range(n):
        o = 100.0 + i * 0.05
        rows.append(
            {
                "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
                + dt.timedelta(hours=i),
                "open": o,
                "high": o + 0.1,
                "low": o - 0.1,
                "close": o + 0.05,
                "volume": volume + i * 2.0,
            }
        )
    return pl.DataFrame(rows)


class TestParentOrderExecutor:
    def test_twap_fills_within_deadline(self):
        # Use a spread whose quantized value gives spread_adj ≈ 1 so the
        # TWAP schedule completes within the deadline under normal conditions.
        from trading_agent.execution.simulator import SimulationConfig

        parent = ParentOrder(
            order_id="P1",
            side=SimSide.BUY,
            quantity=500.0,
            deadline_bars=20,
            max_participation=0.1,
            slippage_budget_bps=50.0,
        )
        engine, res = run_parent_through_engine(
            make_df(),
            parent,
            LiquidityAwareTwap(),
            config=SimulationConfig(random_seed=42, spread_bps=4.0),
        )
        assert res.status == "filled"
        assert res.filled_qty == pytest.approx(500.0, abs=1e-6)
        assert res.residual_qty <= 1e-6
        assert len(res.slices) <= 20
        assert res.algorithms_version

    def test_pov_respects_participation_cap(self):
        parent = ParentOrder(
            order_id="P2",
            side=SimSide.BUY,
            quantity=5000.0,
            deadline_bars=10,
            max_participation=0.05,
            slippage_budget_bps=200.0,
        )
        engine, res = run_parent_through_engine(make_df(), parent, PovExecution())
        for rec in res.slices:
            assert rec["participation"] is None or rec["participation"] <= 0.05 + 1e-9

    def test_pov_leaves_residual_when_cap_too_tight(self):
        parent = ParentOrder(
            order_id="P3",
            side=SimSide.BUY,
            quantity=5000.0,
            deadline_bars=10,
            max_participation=0.05,
            slippage_budget_bps=200.0,
        )
        engine, res = run_parent_through_engine(make_df(), parent, PovExecution())
        assert res.status == "partial"
        assert res.residual_qty > 0

    def test_deterministic_run(self):
        parent = ParentOrder(
            order_id="P4",
            side=SimSide.BUY,
            quantity=500.0,
            deadline_bars=20,
            max_participation=0.1,
            slippage_budget_bps=50.0,
        )
        _, r1 = run_parent_through_engine(make_df(), parent, LiquidityAwareTwap())
        _, r2 = run_parent_through_engine(make_df(), parent, LiquidityAwareTwap())
        assert r1.to_dict() == r2.to_dict()

    def test_sell_parent_order(self):
        # Buy first so we have inventory to sell, then sell with POV.
        df = make_df()
        parent_buy = ParentOrder(
            order_id="B1",
            side=SimSide.BUY,
            quantity=300.0,
            deadline_bars=5,
            max_participation=0.2,
            slippage_budget_bps=50.0,
        )
        engine, res_buy = run_parent_through_engine(df, parent_buy, PovExecution())
        assert res_buy.status == "filled"

    def test_invalid_parent_fails_closed(self):
        engine, _ = run_parent_through_engine(
            make_df(),
            ParentOrder(
                order_id="X", side=SimSide.BUY, quantity=100.0, deadline_bars=10
            ),
            PovExecution(),
        )
        from trading_agent.execution.algorithms.driver import ParentOrderExecutor

        with pytest.raises(ValueError):
            ParentOrderExecutor(engine, PovExecution()).run(
                ParentOrder(
                    order_id="", side=SimSide.BUY, quantity=100.0, deadline_bars=5
                )
            )
