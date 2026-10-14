"""Campaign evidence must price a carried position, not omit it.

A fold that ends holding inventory used to fail campaign publication
outright. Refusing cost nothing, because it discarded the position instead
of pricing it, and the cost cleared on the closed half alone.
"""

from __future__ import annotations


import pytest

from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting
from trading_agent.backtest.campaign_writer import collect_wfo_campaign_rows


def paper_trade(**overrides) -> dict:
    """A paper trade whose components reconstruct its own fill drag."""
    quantity = overrides.pop("quantity", 1.0)
    entry_reference = overrides.pop("entry_reference", 99.0)
    exit_reference = overrides.pop("exit_reference", 109.0)
    entry_price = overrides.pop("entry_price", 100.0)
    exit_price = overrides.pop("exit_price", 110.0)
    entry_fee = overrides.pop("entry_fee", 0.05)
    exit_fee = overrides.pop("exit_fee", 0.05)
    trade = {
        "id": "t1",
        "trade_id": "t1",
        "side": "buy",
        "quantity": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "pnl": quantity * (exit_price - entry_price) - entry_fee - exit_fee,
        "metadata": {
            "measured_accounting": {
                "model": "paper_slippage_only_v1",
                "entry_reference_price": entry_reference,
                "exit_reference_price": exit_reference,
                "execution_components": {
                    "entry": {
                        "slippage": quantity * (entry_price - entry_reference),
                        "spread": 0.0,
                        "market_impact": 0.0,
                        "price_cap_credit": 0.0,
                    },
                    "exit": {
                        "slippage": quantity * (exit_reference - exit_price),
                        "spread": 0.0,
                        "market_impact": 0.0,
                        "price_cap_credit": 0.0,
                    },
                },
            }
        },
    }
    trade.update(overrides)
    return trade


def open_leg(**overrides) -> dict:
    quantity = overrides.pop("quantity", 1.0)
    entry_price = overrides.pop("entry_price", 100.0)
    entry_reference = overrides.pop("entry_reference", 99.0)
    valuation = overrides.pop("valuation_price", 110.0)
    entry_fee = overrides.pop("entry_fee", 0.05)
    leg = {
        "side": "buy",
        "quantity": quantity,
        "entry_price": entry_price,
        "entry_reference_price": entry_reference,
        "valuation_price": valuation,
        "entry_fee": entry_fee,
        "execution_components": {
            "slippage": quantity * (entry_price - entry_reference),
            "spread": 0.0,
            "market_impact": 0.0,
            "price_cap_credit": 0.0,
        },
    }
    leg.update(overrides)
    return leg


def test_flat_run_still_uses_the_closed_basis() -> None:
    evidence = export_paper_trade_accounting(
        [paper_trade()], equity_delta=9.9, open_inventory=False
    )
    assert evidence["accounting_basis"] == "closed_trades_only"
    assert evidence["execution_model"] == "paper_slippage_only_v1"


def test_zero_trade_window_requires_zero_unexplained_equity():
    evidence = export_paper_trade_accounting([], equity_delta=0.0, open_inventory=False)
    assert evidence["trades"] == 0 and evidence["net_pnl"] == 0.0
    assert evidence["strategy_qualified"] is False
    with pytest.raises(ValueError):
        export_paper_trade_accounting([], equity_delta=1.0, open_inventory=False)


def test_producer_accepts_measured_open_only_boundary_window(tmp_path):
    import json
    from types import SimpleNamespace as NS
    from trading_agent.backtest.accounting_evidence import mark_paper_open_at_boundary

    leg = mark_paper_open_at_boundary(
        open_leg(),
        boundary_price=105.0,
        entry_bar_index=2,
        measurement_start=10,
        measurement_end=20,
    )
    report = dict(
        initial_capital=100.0,
        equity_curve=[[0, 100.0], [1, 105.0]],
        trades=[],
        open_positions=1,
        measured_open_inventory=leg,
        measured_equity_basis="marked_equity",
    )
    report["measured_accounting"] = export_paper_trade_accounting(
        [], equity_delta=5.0, open_inventory=leg, equity_basis="marked_equity"
    )
    path = tmp_path / "open_only.json"
    path.write_text(json.dumps(report))
    subject = dict(strategy_id="s", symbol="BTC/USDT", timeframe="1h")
    fold = NS(
        fold_id="f0",
        artifact=NS(**subject, status="COMPLETED", report_path=str(path)),
        test_metrics=dict(
            total_trades=0,
            net_pnl=0.0,
            total_return_pct=5.0,
            sharpe=1.0,
            max_drawdown_pct=0.0,
        ),
    )
    row = collect_wfo_campaign_rows(NS(spec=NS(**subject), outer_results=[fold]))[0]
    assert row["trades"] == 0 and row["net_pnl"] == 0.0
    assert row["total_pnl"] == 5.0 and row["cost_cleared"] is True


def test_partial_exit_and_remaining_inventory_share_boundary_basis():
    from trading_agent.backtest.accounting_evidence import (
        mark_paper_open_at_boundary,
        mark_paper_trade_at_boundary,
    )

    original_trade = paper_trade(
        quantity=0.5, entry_fee=0.5, exit_fee=0.5, entry_bar_index=2, exit_bar_index=12
    )
    trade = mark_paper_trade_at_boundary(
        original_trade, boundary_price=105.0, measurement_start=10, measurement_end=20
    )
    leg = mark_paper_open_at_boundary(
        open_leg(quantity=0.5, entry_fee=0.5, valuation_price=112.0),
        boundary_price=105.0,
        entry_bar_index=2,
        measurement_start=10,
        measurement_end=20,
    )
    evidence = export_paper_trade_accounting(
        [trade], equity_delta=5.5, open_inventory=leg, equity_basis="marked_equity"
    )
    assert evidence["net_pnl"] == pytest.approx(2.0)
    assert evidence["open_unrealized"] == pytest.approx(3.5)
    assert evidence["total_pnl"] == pytest.approx(5.5)
    assert evidence["fees"] == pytest.approx(0.5)


@pytest.mark.parametrize("side,delta", [("buy", 5.0), ("sell", -5.0)])
def test_inventory_born_before_oos_is_marked_at_boundary(side, delta):
    from copy import deepcopy
    from trading_agent.backtest.accounting_evidence import mark_paper_open_at_boundary

    original = open_leg(side=side, entry_reference=100.0, valuation_price=110.0)
    leg = mark_paper_open_at_boundary(
        original,
        boundary_price=105.0,
        entry_bar_index=2,
        measurement_start=10,
        measurement_end=20,
    )
    evidence = export_paper_trade_accounting(
        [], equity_delta=delta, open_inventory=leg, equity_basis="marked_equity"
    )
    assert evidence["total_pnl"] == delta and evidence["open_entry_fee"] == 0.0
    assert leg["measurement_boundary"]["original_open_inventory"] == original
    forged = deepcopy(leg)
    forged["entry_price"] = 104.0
    with pytest.raises(ValueError):
        export_paper_trade_accounting(
            [], equity_delta=delta, open_inventory=forged, equity_basis="marked_equity"
        )
    with pytest.raises(ValueError):
        export_paper_trade_accounting(
            [], equity_delta=delta, open_inventory=leg, equity_basis="cash"
        )


def test_marked_equity_does_not_add_inventory_value_twice():
    evidence = export_paper_trade_accounting(
        [paper_trade()],
        equity_delta=19.85,
        open_inventory=open_leg(),
        equity_basis="marked_equity",
    )
    assert evidence["total_pnl"] == pytest.approx(19.85)
    assert evidence["equity_basis"] == "marked_equity"
    with pytest.raises(ValueError):
        export_paper_trade_accounting(
            [paper_trade()],
            equity_delta=19.85,
            open_inventory=open_leg(),
            equity_basis="cash",
        )
    with pytest.raises(ValueError):
        export_paper_trade_accounting(
            [paper_trade()],
            equity_delta=19.85,
            open_inventory=open_leg(),
            equity_basis="invented",
        )


def test_boundary_trade_preserves_original_fill_proof():
    from copy import deepcopy
    from trading_agent.backtest.accounting_evidence import mark_paper_trade_at_boundary

    original = paper_trade(entry_bar_index=2, exit_bar_index=12)
    trade = mark_paper_trade_at_boundary(
        original, boundary_price=105, measurement_start=10, measurement_end=20
    )
    evidence = export_paper_trade_accounting(
        [trade], equity_delta=4.95, open_inventory=False
    )
    assert evidence["net_pnl"] == pytest.approx(4.95)
    assert evidence["fees"] == pytest.approx(0.05)
    assert trade["measurement_boundary"]["original_trade"] == original
    forged = deepcopy(trade)
    forged["entry_fee"] = 0.01
    with pytest.raises(ValueError):
        export_paper_trade_accounting([forged], equity_delta=4.94, open_inventory=False)
    with pytest.raises(ValueError):
        mark_paper_trade_at_boundary(
            original, boundary_price=105, measurement_start=1, measurement_end=20
        )


def test_a_bare_flag_can_no_longer_stand_in_for_a_carried_position() -> None:
    # True used to be enough to say "open inventory exists", which the
    # exporter then refused -- it could not price what it could not see.
    with pytest.raises(ValueError, match="measured open leg"):
        export_paper_trade_accounting(
            [paper_trade()], equity_delta=9.9, open_inventory=True
        )


def test_carried_position_is_priced_and_reconciled() -> None:
    # Closed leg nets 9.9. The open unit cost 100 plus a 0.05 fee, so cash
    # moved 9.9 - 100.05. It is worth 110 at the window close.
    evidence = export_paper_trade_accounting(
        [paper_trade()],
        equity_delta=-90.15,
        open_inventory=open_leg(valuation_price=110.0),
    )
    assert evidence["accounting_basis"] == "closed_trades_plus_open_inventory"
    assert evidence["open_quantity"] == pytest.approx(1.0)
    assert evidence["open_mark_value"] == pytest.approx(110.0)
    assert evidence["open_unrealized"] == pytest.approx(110.0 - 100.0 - 0.05)
    assert evidence["total_pnl"] == pytest.approx(9.9 + 9.95)


def test_carried_loss_is_reported_rather_than_hidden_by_closed_gains() -> None:
    # Closed trades made money while the position sits deeply underwater.
    # Only total PnL shows that the fold lost.
    evidence = export_paper_trade_accounting(
        [paper_trade()],
        equity_delta=9.9 - 100.05,
        open_inventory=open_leg(valuation_price=20.0, entry_reference=99.0),
    )
    assert evidence["net_pnl"] > 0, "closed half was meant to be profitable"
    assert evidence["open_unrealized"] < 0
    assert evidence["total_pnl"] < 0, "the carried loss was dropped"


def test_open_leg_components_must_reconstruct_the_fill() -> None:
    bad = open_leg(valuation_price=110.0)
    bad["execution_components"]["slippage"] = 0.0
    with pytest.raises(ValueError, match="do not reconcile"):
        export_paper_trade_accounting(
            [paper_trade()], equity_delta=-90.15, open_inventory=bad
        )


def test_open_leg_without_measured_fees_is_refused() -> None:
    leg = open_leg()
    del leg["entry_fee"]
    with pytest.raises(ValueError, match="entry_fee"):
        export_paper_trade_accounting(
            [paper_trade()], equity_delta=-90.15, open_inventory=leg
        )


def test_short_carried_position_marks_on_the_other_side() -> None:
    # A short carried into a rising market loses; the mark is negative and
    # the reconciliation still has to hold.
    leg = open_leg(side="sell", valuation_price=120.0)
    leg["execution_components"]["slippage"] = -1.0
    # sell: drag = -1 * 1 * (100 - 99) = -1
    evidence = export_paper_trade_accounting(
        [paper_trade()],
        # Selling the unit credited 100 and paid its 0.05 fee, on top of the
        # closed leg's 9.9.
        equity_delta=9.9 + 100.0 - 0.05,
        open_inventory=leg,
    )
    assert evidence["open_side"] == "sell"
    assert evidence["open_mark_value"] == pytest.approx(-120.0)
    assert evidence["open_unrealized"] == pytest.approx(-(120.0 - 100.0) - 0.05)


def test_carried_fold_is_priced_rather_than_refused(tmp_path) -> None:
    # The producer must turn a real carried-position report into a measured
    # row: a closed half that hides an open loss is still a carried loss, so
    # cost_cleared is driven by total PnL, not the closed net alone.
    leg = open_leg(quantity=1.0, valuation_price=110.0)
    initial = 100.0
    final = 9.85
    # equity_delta is derived from the curve in production; mirror that so
    # the stored ledger and the producer's reconstruction compare equal.
    accounting = export_paper_trade_accounting(
        [paper_trade()],
        equity_delta=final - initial,
        open_inventory=leg,
    )
    report = {
        "trades": [paper_trade()],
        "initial_capital": initial,
        "equity_curve": [[0, initial], [1, final]],
        "open_positions": 1,
        "measured_open_inventory": leg,
        "measured_accounting": accounting,
    }
    report_path = tmp_path / "fold_carry.json"
    report_path.write_text(__import__("json").dumps(report))

    from types import SimpleNamespace

    fold = SimpleNamespace(
        fold_id="f_carry",
        params={},
        artifact=SimpleNamespace(
            status="COMPLETED",
            report_path=str(report_path),
            strategy_id="s",
            symbol="s",
            timeframe="1h",
        ),
        test_metrics={
            "total_trades": accounting["trades"],
            "net_pnl": accounting["net_pnl"],
            "total_return_pct": 12.0,
            "sharpe": 1.0,
            "max_drawdown_pct": -2.0,
        },
    )
    result = SimpleNamespace(
        spec=SimpleNamespace(strategy_id="s", symbol="s", timeframe="1h"),
        outer_results=[fold],
    )

    rows = collect_wfo_campaign_rows(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["fold_id"] == "f_carry"
    assert row["report_sha256"]
    # The carried long's PnL is the closed leg plus the still-open leg; the
    # row surfaces the closed ledger (net_pnl) while cost_cleared is driven
    # by total PnL of the full position, not the closed half alone.
    assert row["gross_pnl"] == pytest.approx(accounting["gross_pnl"])
    assert row["fees"] == pytest.approx(accounting["fees"])
    assert row["slippage"] == pytest.approx(accounting["slippage"])
    assert row["net_pnl"] == pytest.approx(accounting["net_pnl"])
    total_pnl = accounting["net_pnl"] + accounting["open_unrealized"]
    assert row["cost_cleared"] is (total_pnl > 0)


def test_flat_fold_reconstructs_without_open_inventory(tmp_path) -> None:
    accounting = export_paper_trade_accounting(
        [paper_trade()], equity_delta=109.9 - 100.0, open_inventory=False
    )
    report = {
        "trades": [paper_trade()],
        "initial_capital": 100.0,
        "equity_curve": [[0, 100.0], [1, 109.9]],
        "open_positions": 0,
        "measured_open_inventory": None,
        "measured_accounting": accounting,
    }
    report_path = tmp_path / "fold_flat.json"
    report_path.write_text(__import__("json").dumps(report))

    from types import SimpleNamespace

    fold = SimpleNamespace(
        fold_id="f_flat",
        params={},
        artifact=SimpleNamespace(
            status="COMPLETED",
            report_path=str(report_path),
            strategy_id="s",
            symbol="s",
            timeframe="1h",
        ),
        test_metrics={
            "total_trades": accounting["trades"],
            "net_pnl": accounting["net_pnl"],
            "total_return_pct": 9.9,
            "sharpe": 1.0,
            "max_drawdown_pct": -1.0,
        },
    )
    result = SimpleNamespace(
        spec=SimpleNamespace(strategy_id="s", symbol="s", timeframe="1h"),
        outer_results=[fold],
    )
    rows = collect_wfo_campaign_rows(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["fold_id"] == "f_flat"
    assert row["cost_cleared"] is (accounting["net_pnl"] > 0)
