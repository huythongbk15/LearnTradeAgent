"""Accounting when fills are partial or inventory survives the window.

The oracle here deliberately recomputes cash and position from the raw fill
records instead of reading the engine's own bookkeeping. An engine that
records the same wrong number twice would otherwise agree with itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.accounting_evidence import (
    export_closed_trade_accounting,
    export_open_inventory_accounting,
)
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


def candles(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
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


def run(
    signals: list[int],
    closes: list[float],
    *,
    partial: float = 0.0,
    seed: int = 7,
    capital: float = 10_000.0,
    long_only: bool = True,
):
    simulator = create_execution_simulator(partial_fill_prob=partial, seed=seed)
    engine = SimulatorBacktestEngine(
        StaticSignals(signals),
        initial_capital=capital,
        simulator=simulator,
        use_simulator=True,
        long_only=long_only,
    )
    return engine.run(symbol="BTC/USDT", timeframe="1d", df=candles(closes))


def cash_from_fills(fills, capital: float) -> tuple[float, float]:
    """Rebuild cash and position from fills alone.

    This is the whole ledger: every fill pays for its quantity and its fee.
    Nothing the engine remembers is used.
    """
    cash = capital
    position = 0.0
    for fill in fills:
        direction = 1.0 if fill.side.value == "buy" else -1.0
        cash -= direction * fill.quantity * fill.price + fill.fee
        position += direction * fill.quantity
    return cash, position


def test_partial_fill_records_the_filled_size_not_the_order_size() -> None:
    partial = run([1, 0, 0, 0], [100.0, 101.0, 102.0, 103.0], partial=1.0)
    full = run([1, 0, 0, 0], [100.0, 101.0, 102.0, 103.0], partial=0.0)

    bought_partial = sum(f.quantity for f in partial.fills if f.side.value == "buy")
    bought_full = sum(f.quantity for f in full.fills if f.side.value == "buy")
    assert bought_partial < bought_full, "test did not actually partially fill"

    # The ledger follows the fills, so the two runs must not converge on the
    # same inventory. An order-size assumption would book the full order on
    # the partial run and the two positions would agree.
    _, position_partial = cash_from_fills(partial.fills, 10_000.0)
    _, position_full = cash_from_fills(full.fills, 10_000.0)
    assert abs(position_partial) < bought_full
    assert partial.accounting_evidence["open_quantity"] == pytest.approx(
        abs(position_partial), rel=1e-12
    )
    assert position_full == pytest.approx(0.0, abs=1e-12), (
        "the full-fill run was expected to end flat"
    )


def closed_trade_fixture(**overrides) -> dict:
    """A trade whose components genuinely reconstruct its fill drag."""
    trade = {
        "trade_id": "t1",
        "side": "buy",
        "quantity": 1.0,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_fee": 0.05,
        "exit_fee": 0.05,
        "pnl": 9.9,
        "metadata": {
            "simulation": {
                "entry_reference_price": 99.0,
                "exit_reference_price": 109.0,
                "execution_components": {
                    "entry": {
                        "slippage": 0.0,
                        "spread": 1.0,
                        "market_impact": 0.0,
                        "price_cap_credit": 0.0,
                    },
                    "exit": {
                        "slippage": 0.0,
                        "spread": -1.0,
                        "market_impact": 0.0,
                        "price_cap_credit": 0.0,
                    },
                },
            }
        },
    }
    trade.update(overrides)
    return trade


def open_leg_fixture(**overrides) -> dict:
    leg = {
        "side": "buy",
        "quantity": 1.0,
        "entry_price": 100.0,
        "entry_reference_price": 100.0,
        "valuation_price": 110.0,
        "entry_fee": 0.05,
        "execution_components": {
            "slippage": 0.0,
            "spread": 0.0,
            "market_impact": 0.0,
            "price_cap_credit": 0.0,
        },
    }
    leg.update(overrides)
    return leg


def test_partial_exit_leaves_the_remainder_open_and_priced() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)

    buy_qty = sum(f.quantity for f in result.fills if f.side.value == "buy")
    sell_qty = sum(f.quantity for f in result.fills if f.side.value == "sell")
    _, position = cash_from_fills(result.fills, 10_000.0)
    # What survives is the difference between the two sides' quantities, and
    # it must match both the fill ledger and the engine's own position.
    assert position == pytest.approx(buy_qty - sell_qty, abs=1e-12)
    assert position != 0.0, "test did not produce carried inventory"
    assert result.equity_curve["position"][-1] == pytest.approx(position, abs=1e-12)
    # The closed portion cannot exceed what was actually acquired.
    assert sum(t["quantity"] for t in result.trades.to_dicts()) <= buy_qty + 1e-12


def test_engine_cash_equals_the_fill_ledger() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)

    cash, position = cash_from_fills(result.fills, 10_000.0)
    assert result.equity_curve["cash"][-1] == pytest.approx(cash, abs=1e-8)
    assert result.equity_curve["position"][-1] == pytest.approx(position, abs=1e-12)


def test_carry_across_the_window_is_exported_not_skipped() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)

    assert result.accounting_status == "OPEN_INVENTORY_RECONCILED"
    evidence = result.accounting_evidence
    assert evidence is not None
    assert evidence["accounting_basis"] == "closed_trades_plus_open_inventory"
    assert evidence["open_quantity"] > 0
    assert evidence["open_side"] in ("buy", "sell")


def test_open_inventory_reconciles_against_cash_plus_mark() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)
    evidence = result.accounting_evidence

    cash, _ = cash_from_fills(result.fills, 10_000.0)
    mark = float(result.equity_curve["equity"][-1]) - cash
    assert evidence["equity_delta"] == pytest.approx(cash - 10_000.0, abs=1e-8)
    assert evidence["open_mark_value"] == pytest.approx(mark, abs=1e-8)
    assert evidence["total_pnl"] == pytest.approx(
        evidence["net_pnl"] + evidence["open_unrealized"], abs=1e-9
    )
    assert evidence["total_pnl"] == pytest.approx(
        float(result.equity_curve["equity"][-1]) - 10_000.0, abs=1e-6
    )


def test_open_inventory_marks_at_the_final_price_not_the_entry() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 130.0], partial=1.0)
    evidence = result.accounting_evidence

    assert evidence["open_valuation_price"] == pytest.approx(130.0)
    assert evidence["open_mark_value"] != pytest.approx(
        evidence["open_quantity"] * evidence["open_entry_price"]
    )


def test_carry_never_double_counts_the_open_entry_fee() -> None:
    result = run([1, 0, -1, 0, 0], [100.0, 101.0, 102.0, 103.0, 104.0], partial=1.0)
    evidence = result.accounting_evidence

    charged = sum(f.fee for f in result.fills)
    assert charged == pytest.approx(result.total_fees, rel=1e-9)
    # Closed legs carry their own share and the open leg carries the rest.
    # Between them they are the total, never more.
    assert evidence["open_entry_fee"] <= charged + 1e-12
    closed_fees = sum(t["fees"] for t in result.trades.to_dicts())
    assert closed_fees + evidence["open_entry_fee"] == pytest.approx(
        charged, abs=1e-8
    )


def test_flat_result_still_uses_the_closed_ledger() -> None:
    result = run([1, 0, -1, 0], [100.0, 101.0, 102.0, 103.0])

    assert result.accounting_status == "CLOSED_LEDGER_RECONCILED"
    assert result.accounting_evidence["accounting_basis"] == "closed_trades_only"


def test_valid_trade_fixture_is_accepted_so_the_negative_cases_are_real() -> None:
    evidence = export_closed_trade_accounting(
        [closed_trade_fixture()], equity_delta=9.9, open_inventory=False
    )
    assert evidence["net_pnl"] == pytest.approx(9.9)
    assert evidence["accounting_basis"] == "closed_trades_only"


def test_exporter_rejects_components_that_do_not_reconstruct_the_fill() -> None:
    trade = closed_trade_fixture()
    # Break the exit leg. A long's exit drag is negative, so a component set
    # that ignores it would silently overstate cost.
    trade["metadata"]["simulation"]["execution_components"]["exit"] = {
        "slippage": 0.0,
        "spread": 1.0,
        "market_impact": 0.0,
        "price_cap_credit": 0.0,
    }
    with pytest.raises(ValueError, match="do not reconcile"):
        export_closed_trade_accounting([trade], equity_delta=9.9, open_inventory=False)


def test_open_exporter_rejects_a_ledger_that_does_not_reconcile() -> None:
    # Closed ledger says 9.9 net; cash moved 0.0 and the open leg is worth
    # 10.0. The two stories disagree, so neither may be reported.
    with pytest.raises(ValueError, match="reconcile"):
        export_open_inventory_accounting(
            [closed_trade_fixture()],
            open_inventory=open_leg_fixture(),
            equity_delta=0.0,
        )


def test_open_exporter_rejects_open_components_that_do_not_reconstruct() -> None:
    with pytest.raises(ValueError, match="do not reconcile"):
        export_open_inventory_accounting(
            [closed_trade_fixture()],
            open_inventory=open_leg_fixture(entry_reference_price=99.0),
            equity_delta=0.0,
        )


def test_open_exporter_accepts_a_ledger_that_does_reconcile() -> None:
    # Closed leg nets 9.9. Buying the open unit cost 100 plus its 0.05 fee, so
    # cash moved 9.9 - 100.05. The position is worth 109.9 at the end.
    evidence = export_open_inventory_accounting(
        [closed_trade_fixture()],
        open_inventory=open_leg_fixture(valuation_price=109.9),
        equity_delta=-90.15,
    )
    assert evidence["open_unrealized"] == pytest.approx(9.85)
    assert evidence["open_mark_value"] == pytest.approx(109.9)
    assert evidence["total_pnl"] == pytest.approx(19.75)
