"""PLUS wiring through the position-aware hook; synthetic frames only."""
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.execution.position_cycle import PositionCycleView
from trading_agent.strategies.own24_context import Own24ContextStrategy
from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy


def frame(btc_end=110.0):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return pl.DataFrame({
        "timestamp": [start + timedelta(hours=i) for i in range(27)],
        "close": [100.0] * 25 + [110.0, 110.0],
        "btc_close": [100.0] * 25 + [btc_end, btc_end],
    })


def view(*, quantity=0.0, hours=0, known=True):
    first = datetime(2024, 1, 2, tzinfo=UTC)
    return PositionCycleView(
        symbol="ETH/USDT", quantity=quantity,
        decision_at=first + timedelta(hours=hours), inventory_known=known,
        first_fill_at=first if quantity else None,
        entry_order_id="synthetic-entry" if quantity else None,
    )


def signal(arm, data, position):
    return Own24FillContextStrategy({"arm": arm}).generate_signal_with_position(
        data, position,
    ).item()


def test_plus_reaches_position_hook_and_filters_only_btc_direction():
    assert signal("PLUS", frame(110.0), view()) == 1
    assert signal("PLUS", frame(90.0), view()) == 0
    assert signal("A0", frame(110.0), view()) == 1
    assert signal("A0", frame(90.0), view()) == 1


def test_plus_missing_context_holds_and_base_still_enters():
    data = frame().drop("btc_close")
    assert signal("PLUS", data, view()) == 0
    assert signal("A0", data, view()) == 1


def test_plus_excludes_current_decision_row():
    data = frame(90.0).with_columns(
        pl.Series("btc_close", [100.0] * 25 + [90.0, 10000.0]),
    )
    assert signal("PLUS", data, view()) == 0


def test_plus_expiry_does_not_require_context():
    assert signal("PLUS", pl.DataFrame(), view(quantity=1.0, hours=23)) == 0
    assert signal("PLUS", pl.DataFrame(), view(quantity=1.0, hours=24)) == -1


def test_plus_unknown_inventory_never_enters():
    assert signal("PLUS", frame(), view(known=False)) == 0


def test_plus_does_not_expand_legacy_constructor_or_parameters():
    with pytest.raises(ValueError):
        Own24ContextStrategy({"arm": "PLUS"})
    with pytest.raises(ValueError):
        Own24FillContextStrategy({"arm": "PLUS", "unexpected": 1})
    with pytest.raises(ValueError):
        Own24FillContextStrategy({"arm": "UNKNOWN"})
