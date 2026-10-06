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


def test_a_bare_flag_can_no_longer_stand_in_for_a_carried_position() -> None:
    # True used to be enough to say "open inventory exists", which the
    # exporter then refused -- it could not price what it could not see.
    with pytest.raises(ValueError, match="measured open leg"):
        export_paper_trade_accounting([paper_trade()], equity_delta=9.9, open_inventory=True)


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
        [paper_trade()], equity_delta=final - initial, open_inventory=leg,
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
            status="COMPLETED", report_path=str(report_path),
            strategy_id="s", symbol="s", timeframe="1h",
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