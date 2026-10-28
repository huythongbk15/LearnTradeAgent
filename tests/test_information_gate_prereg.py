"""The next wave locks its window and refuses a run while the rule is unset."""
from trading_agent.research.btc_context_prereg import (
    CLOCK_ROWS,
    HOLDOUT_START_UTC,
    MEASUREMENT_START,
    MEASUREMENT_STOP,
)
from trading_agent.research.btc_context_rule import ENTRY_RULE_ID
from trading_agent.research.information_gate_prereg import (
    ARMS,
    CLOSED_RULES,
    COSTS,
    ENTRY_RULE_ID as GATE_RULE,
    ENTRY_RULE_STATUS,
    TARGETS,
    cell_count,
    cells_per_session,
    execution_authority,
    session_timeout_seconds,
)


def test_rule_stays_unset_and_own24_is_closed():
    assert GATE_RULE is None
    assert ENTRY_RULE_STATUS == "UNSET"
    assert ENTRY_RULE_ID == "H-OWN24-CONTEXT-01"
    assert ENTRY_RULE_ID in CLOSED_RULES
    assert ARMS == ("BASE", "PLUS")
    assert "A0" not in ARMS


def test_window_is_the_preholdout_clock_and_the_run_is_refused():
    authority = execution_authority()
    assert authority["market_run"] is False
    assert authority["live_authorized"] is False
    assert authority["reason"] == "RULE_UNSET"
    assert authority["measurement"] == (MEASUREMENT_START, MEASUREMENT_STOP)
    assert MEASUREMENT_START == 216
    assert MEASUREMENT_STOP == CLOCK_ROWS == 25198
    assert authority["holdout_end_utc"] > HOLDOUT_START_UTC


def test_twelve_cells_split_into_two_sessions_under_the_cap():
    assert (TARGETS, COSTS) == (
        ("ETH/USDT", "BNB/USDT"),
        ("1x", "2x", "slip_stress"),
    )
    assert cell_count() == 12
    assert cells_per_session() == 6
    assert session_timeout_seconds() == 3600
    assert cell_count() * 600 == 7200
