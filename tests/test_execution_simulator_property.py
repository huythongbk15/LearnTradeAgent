"""Property-based tests for the Execution Simulator V2 (Wave A).

Invariants under randomized (but seeded) inputs:

* fills never exceed the intended quantity;
* fill prices respect tick size and are positive;
* ledger cash/inventory never go negative;
* rounding never rounds an order quantity up;
* seeded replay is deterministic;
* fill fee accounting: total fees equals sum of per-fill fees.
"""

from __future__ import annotations

from hypothesis import given, strategies as st
import polars as pl
import pytest

from trading_agent.execution.simulator import (
    MarketReplayEngine,
    OrderIntent,
    SimOrderType,
    SimSide,
    SimulationConfig,
    quantize_price,
    quantize_qty,
)

st_seed = st.integers(min_value=0, max_value=10_000)
st_qty = st.floats(
    min_value=1e-4, max_value=10_000.0, allow_nan=False, allow_infinity=False
)
st_price = st.floats(
    min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False
)
st_tick = st.floats(
    min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False
)
st_step = st.floats(
    min_value=1e-8, max_value=1.0, allow_nan=False, allow_infinity=False
)


def make_df(n: int, base: float) -> pl.DataFrame:
    import datetime as dt

    rows = []
    for i in range(n):
        o = base + i * 0.1
        rows.append(
            {
                "timestamp": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
                + dt.timedelta(hours=i),
                "open": o,
                "high": o + 0.5,
                "low": o - 0.5,
                "close": o + 0.1,
                "volume": 20.0,
            }
        )
    return pl.DataFrame(rows)


class TestRoundingProperties:
    @given(st_price, st_tick)
    def test_quantize_price_on_grid(self, price, tick):
        q = quantize_price(price, tick)
        assert q > 0 or q == 0.0
        # q must be an integer multiple of tick (within float tolerance).
        nearest = round(q / tick) * tick
        assert abs(q - nearest) <= max(abs(q) * 1e-12, 1e-9)

    @given(st_qty, st_step)
    def test_quantize_qty_never_rounds_up(self, qty, step):
        q = quantize_qty(qty, step)
        assert q <= qty + 1e-12
        assert q >= 0.0


class TestLedgerInvariants:
    @given(
        st_seed,
        st.lists(
            st.tuples(
                st.sampled_from(["buy", "sell"]),
                st_qty,
            ),
            min_size=1,
            max_size=12,
        ),
    )
    def test_no_negative_cash_or_inventory(self, seed, orders):
        df = make_df(30, base=100.0)
        cfg = SimulationConfig(random_seed=seed, depth_volume_share=0.5, spread_bps=5.0)
        engine = MarketReplayEngine(
            df, config=cfg, symbol="T", initial_cash=1_000_000.0
        )

        def provider(i, eng):
            # Inject orders at a handful of bars.
            if i not in (5, 10, 15, 20):
                return []
            side = SimSide.BUY if orders[i % len(orders)][0] == "buy" else SimSide.SELL
            qty = orders[i % len(orders)][1]
            qty = quantize_qty(qty, cfg.step_size)
            if qty <= 0:
                return []
            return [
                OrderIntent(
                    order_id=f"o{i}",
                    side=side,
                    order_type=SimOrderType.MARKET,
                    quantity=qty,
                )
            ]

        result = engine.run(provider)
        ledger = result.ledger
        assert ledger.cash_quote >= -1e-6, f"cash went negative: {ledger.cash_quote}"
        assert ledger.inventory_base >= -1e-6, (
            f"inventory went negative: {ledger.inventory_base}"
        )
        # Total fees equal the sum of per-fill fees (float tolerance).
        assert ledger.total_fees() == pytest.approx(
            sum(f.fee for f in ledger.fills), abs=1e-9
        )
        # Fill quantities never exceed intended quantities.
        for o in result.order_results:
            assert o.filled_quantity <= o.intent.quantity + 1e-9

    @given(st_seed)
    def test_seeded_replay_deterministic(self, seed):
        df = make_df(25, base=50.0)
        cfg = SimulationConfig(random_seed=seed)

        def provider(i, e):
            if i in (4, 9, 14, 19):
                side = SimSide.BUY if i % 3 == 0 else SimSide.SELL
                return [
                    OrderIntent(
                        order_id=f"o{i}",
                        side=side,
                        order_type=SimOrderType.MARKET,
                        quantity=1.0,
                    )
                ]
            return []

        r1 = MarketReplayEngine(df, config=cfg, symbol="T").run(provider)
        r2 = MarketReplayEngine(df, config=cfg, symbol="T").run(provider)
        assert r1.to_dict() == r2.to_dict()

    @given(st_seed)
    def test_fill_price_positive_and_on_tick(self, seed):
        df = make_df(20, base=100.0)
        cfg = SimulationConfig(random_seed=seed)
        engine = MarketReplayEngine(df, config=cfg, symbol="T", initial_cash=10_000.0)
        result = engine.run(
            lambda i, e: (
                [
                    OrderIntent(
                        order_id=f"o{i}",
                        side=SimSide.BUY,
                        order_type=SimOrderType.MARKET,
                        quantity=5.0,
                    )
                ]
                if i == 5
                else []
            )
        )
        for f in result.fills:
            assert f.price > 0
            assert f.quantity > 0
            assert f.quantity <= 5.0 + 1e-9
            # Price lies on the tick grid.
            assert abs(f.price / cfg.tick_size - round(f.price / cfg.tick_size)) < 1e-6
