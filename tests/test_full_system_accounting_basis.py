"""The native report supplies marked equity, including terminal inventory."""
from datetime import timedelta
from types import SimpleNamespace

import polars as pl
import pytest


@pytest.mark.parametrize("carried", [True, False])
def test_native_report_declares_marked_equity(monkeypatch, carried):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest import accounting_evidence

    class Captured(RuntimeError):
        pass

    observed = {}

    def capture(trades, **kwargs):
        observed.update(kwargs)
        raise Captured

    monkeypatch.setattr(accounting_evidence, "export_paper_trade_accounting", capture)
    sim = FullSystemSimulator.__new__(FullSystemSimulator)
    sim.equity_curve = [("2024-01-01T04:00:00+00:00", 10000.),
        ("2024-01-01T08:00:00+00:00", 10010.)]
    sim.timeframe_delta = timedelta(hours=4)
    sim._run_end = 2
    sim.df = pl.DataFrame({"close": [500., 502.]})
    sim.engine = SimpleNamespace(exchange=SimpleNamespace(
        get_trade_history=lambda **kwargs: [],
        get_all_positions=lambda: [object()] if carried else [],
        get_open_inventory_measurement=lambda mark: {"synthetic_mark": mark}))
    with pytest.raises(Captured):
        sim._report()
    assert observed["equity_delta"] == 10
    assert observed["equity_basis"] == "marked_equity"
    assert observed["open_inventory"] == ({"synthetic_mark": 502.} if carried else False)
