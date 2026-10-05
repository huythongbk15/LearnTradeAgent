"""Pinned accounting numbers.

The invariant tests elsewhere prove the ledger is internally consistent.
They would all still pass if the cost model itself drifted -- a slippage
constant nudged, an impact exponent changed, a fee bps moved -- because a
consistent ledger with the wrong numbers is still consistent.

These assertions pin the measured values for two fixed, seeded scenarios so
that drift fails loudly. The numbers are not recorded because the code
produced them; each block below states the identity that makes it correct,
and a reviewer can check the arithmetic without running anything.

Seeded at 7 with partial_fill_prob 0 and 1. Any change to the simulator's
RNG consumption will move these, which is itself worth knowing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.execution.backtest_sim.backtest_integration import (
    SimulatorBacktestEngine,
)
from trading_agent.execution.backtest_sim.models import create_execution_simulator
from trading_agent.strategies.base import Strategy


class StaticSignals(Strategy):
    name = "static_signals"

    def __init__(self, signals: list[int]) -> None:
        super().__init__()
        self.signals = signals

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return pl.Series("signal", self.signals)


def run(signals, closes, *, partial=0.0, seed=7):
    simulator = create_execution_simulator(partial_fill_prob=partial, seed=seed)
    engine = SimulatorBacktestEngine(
        StaticSignals(signals),
        initial_capital=10_000.0,
        simulator=simulator,
        use_simulator=True,
    )
    frame = pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i)
                for i in range(len(closes))
            ],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )
    return engine.run(symbol="BTC/USDT", timeframe="1d", df=frame)


def test_closed_run_cost_model_is_unchanged() -> None:
    result = run([1, 0, -1, 0], [100.0, 101.0, 102.0, 103.0])
    evidence = result.accounting_evidence

    # Pinned fill economics. 95 units in at 102.0587641309 and back out at
    # 101.9259992057; the mid moved 102 -> 104 across the round trip.
    assert [(f.side.value, f.quantity) for f in result.fills] == [
        ("buy", 95.0),
        ("sell", 95.0),
    ]
    assert result.fills[0].price == pytest.approx(102.0587641309, abs=1e-9)
    assert result.fills[1].price == pytest.approx(101.9259992057, abs=1e-9)
    assert result.total_fees == pytest.approx(9.6892762585, abs=1e-9)
    assert result.total_execution_drag == pytest.approx(202.6126678949, abs=1e-8)

    assert evidence["gross_pnl"] == pytest.approx(190.0, abs=1e-9)
    assert evidence["fees"] == pytest.approx(9.6892762585, abs=1e-9)
    assert evidence["slippage"] == pytest.approx(103.7797982550, abs=1e-8)
    assert evidence["spread_cost"] == pytest.approx(1.9380000000, abs=1e-9)
    assert evidence["market_impact"] == pytest.approx(96.8948696399, abs=1e-8)
    assert evidence["net_pnl"] == pytest.approx(-22.3019441534, abs=1e-8)


def test_closed_run_cost_components_still_reconstruct_the_fill() -> None:
    # The pinned numbers above are only worth pinning if they still mean
    # something. Gross PnL is measured against the reference mids (2.00 apart,
    # 95 units -> 190), every execution component is drag against those same
    # references, and the remainder must equal what the fills actually paid.
    result = run([1, 0, -1, 0], [100.0, 101.0, 102.0, 103.0])
    evidence = result.accounting_evidence

    components = (
        evidence["fees"]
        + evidence["slippage"]
        + evidence["spread_cost"]
        + evidence["market_impact"]
        + evidence["price_cap_credit"]
    )
    assert evidence["gross_pnl"] - components == pytest.approx(
        evidence["net_pnl"], abs=1e-9
    )

    filled = result.fills[0]
    assert filled.spread_cost + filled.slippage_cost + filled.impact_cost == (
        pytest.approx(filled.execution_cost, abs=1e-9)
    )
    # and the fill-level PnL agrees with the ledger
    buy, sell = result.fills
    assert 95.0 * (sell.price - buy.price) - buy.fee - sell.fee == pytest.approx(
        evidence["net_pnl"], abs=1e-8
    )


def test_carry_run_numbers_are_unchanged() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)
    evidence = result.accounting_evidence

    # Three fills, none for the full order size. What survives is
    # 68.4521124586 - 54.6824741922 - 10.1572499924.
    assert [(f.side.value, f.quantity) for f in result.fills] == [
        ("buy", pytest.approx(68.4521124586, abs=1e-9)),
        ("sell", pytest.approx(54.6824741922, abs=1e-9)),
        ("sell", pytest.approx(10.1572499924, abs=1e-9)),
    ]
    assert evidence["open_quantity"] == pytest.approx(3.6123882741, abs=1e-9)

    assert evidence["open_entry_price"] == pytest.approx(102.0587641309, abs=1e-9)
    assert evidence["open_valuation_price"] == pytest.approx(104.0, abs=1e-12)
    assert evidence["open_entry_fee"] == pytest.approx(0.1843379414, abs=1e-9)
    assert evidence["open_slippage"] == pytest.approx(1.9539736378, abs=1e-8)
    assert evidence["open_spread_cost"] == pytest.approx(0.0364851216, abs=1e-9)
    assert evidence["open_market_impact"] == pytest.approx(1.8342083722, abs=1e-8)
    assert evidence["open_unrealized"] == pytest.approx(6.8281597492, abs=1e-8)
    assert evidence["open_mark_value"] == pytest.approx(375.6883805041, abs=1e-7)

    assert evidence["trades"] == 2
    assert evidence["net_pnl"] == pytest.approx(6.7595499634, abs=1e-8)
    assert evidence["total_pnl"] == pytest.approx(13.5877097127, abs=1e-7)
    assert evidence["equity_delta"] == pytest.approx(-362.1006707914, abs=1e-7)
    assert evidence["strategy_qualified"] is False


def test_carry_run_still_reconciles_cash_against_mark() -> None:
    # The reconciliation is checked here as well as pinned, because a
    # snapshot that only checked numbers would accept a ledger that no
    # longer agrees with itself.
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)
    evidence = result.accounting_evidence

    cash = 10_000.0
    position = 0.0
    for fill in result.fills:
        direction = 1.0 if fill.side.value == "buy" else -1.0
        cash -= direction * fill.quantity * fill.price + fill.fee
        position += direction * fill.quantity

    assert position == pytest.approx(evidence["open_quantity"], abs=1e-9)
    assert evidence["equity_delta"] == pytest.approx(cash - 10_000.0, abs=1e-8)
    assert evidence["open_mark_value"] == pytest.approx(
        position * evidence["open_valuation_price"], abs=1e-7
    )
    assert evidence["total_pnl"] == pytest.approx(
        evidence["net_pnl"] + evidence["open_unrealized"], abs=1e-9
    )
    assert evidence["total_pnl"] == pytest.approx(
        evidence["equity_delta"] + evidence["open_mark_value"], abs=1e-7
    )

def test_a_seeded_simulator_is_actually_reproducible() -> None:
    # The snapshot above is worthless if a seeded run can drift, so this
    # asserts determinism directly rather than trusting the pinned numbers.
    # BacktestEngine.run() used to swap in a fresh SimulatorState, which
    # discarded the seed and left every partial fill drawing from an
    # unseeded generator.
    first = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)
    second = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)

    assert [f.quantity for f in first.fills] == [f.quantity for f in second.fills]
    assert [f.price for f in first.fills] == [f.price for f in second.fills]
    assert first.accounting_evidence == second.accounting_evidence


def test_a_different_seed_still_produces_a_different_draw() -> None:
    # The guard above would also pass if reset() pinned the generator to a
    # constant and the seed became meaningless.
    a = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0, seed=7)
    b = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0, seed=99)
    assert [f.quantity for f in a.fills] != [f.quantity for f in b.fills]
