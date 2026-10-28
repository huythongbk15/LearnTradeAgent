"""Unregistered synthetic-stage policy: entry context, exit from actual fill.

This is a distinct policy, not a replacement for the measured own24_context.
It cannot be evaluated as a precomputed signal series.
"""
import hashlib
import json
from datetime import timedelta

import polars as pl

from trading_agent.execution.position_cycle import PositionCycleView
from trading_agent.research.btc_context_features import (
    FeatureValue, contract_fingerprint, own_simple_return_24,
)
from trading_agent.research.btc_context_rule import (
    PLUS_ARM, entry_allowed, rule_fingerprint, rule_fingerprint_plus,
)
from trading_agent.strategies.base import Strategy
from trading_agent.strategies.own24_context import Own24ContextStrategy

HOLDING_HOURS = 24


def fill_policy_fingerprint():
    payload = {
        "policy": "own24_fill_context_v1_synthetic_stage",
        "entry_rule": rule_fingerprint(),
        "features": contract_fingerprint(),
        "entry_window": "exclude_decision_bar_as_in_canonical_bridge",
        "holding_hours": HOLDING_HOURS,
        "clock": "first_positive_fill_of_current_cycle_no_reset_on_partial",
        "exit_priority": "expiry_before_context",
        "unknown_inventory": "hold_no_new_exposure",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def fill_policy_fingerprint_plus():
    payload = dict(
        policy="own24_fill_context_v1_synthetic_stage_plus",
        entry_rule=rule_fingerprint_plus(),
        features=contract_fingerprint(),
        entry_window="exclude_decision_bar_as_in_canonical_bridge",
        holding_hours=HOLDING_HOURS,
        clock="first_positive_fill_of_current_cycle_no_reset_on_partial",
        exit_priority="expiry_before_context",
        unknown_inventory="hold_no_new_exposure",
        plus_predicate="btc_simple_return_24_strictly_positive",
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Own24FillContextStrategy(Own24ContextStrategy):
    name = "own24_fill_context"
    requires_execution_cycle = True

    def __init__(self, params: dict | None = None) -> None:
        resolved = {"arm": "A0", **dict(params or {})}
        if resolved.get("arm") == PLUS_ARM:
            if set(resolved) != {"arm"}:
                raise ValueError("fill-context PLUS accepts only the arm parameter")
            Strategy.__init__(self, resolved)
        else:
            super().__init__(resolved)

    def _allowed(self, df: pl.DataFrame, index: int) -> bool:
        if self.params["arm"] != PLUS_ARM:
            return super()._allowed(df, index)
        view = df.head(index + 1)
        time_name = "time" if "time" in view.columns else "timestamp"
        if not {time_name, "close", "btc_close"}.issubset(view.columns):
            return False
        try:
            stamps = tuple(view[time_name].to_list())
            own = own_simple_return_24(tuple(view["close"].to_list()), stamps, index)
            btc = own_simple_return_24(tuple(view["btc_close"].to_list()), stamps, index)
        except (ValueError, TypeError):
            return False
        context = {"btc_simple_return_24": FeatureValue(
            "btc_simple_return_24", btc.value, btc.unavailable_reason,
        )}
        return entry_allowed(PLUS_ARM, own, context)

    def generate_signals(self, df):
        raise ValueError("fill-based holding requires a runtime position cycle, not precomputed signals")

    def generate_signal_with_position(self, df, portfolio_state):
        if not isinstance(portfolio_state, PositionCycleView):
            raise ValueError("fill-based strategy requires PositionCycleView")
        if portfolio_state.symbol not in {"ETH/USDT", "BNB/USDT"}:
            raise ValueError("unsupported target for fill-context prototype")
        value = 0
        if portfolio_state.inventory_known:
            if portfolio_state.quantity > 0:
                # A valid open view has a first-fill timestamp. Price/context
                # is deliberately not accessed before the expiry decision.
                elapsed = portfolio_state.decision_at - portfolio_state.first_fill_at
                value = -1 if elapsed >= timedelta(hours=HOLDING_HOURS) else 0
            elif df.height > 1:
                # Preserve native entry timing k -> canonical k+1 -> open k+2.
                closed_window = df.head(-1)
                value = int(self._allowed(closed_window, closed_window.height - 1))
        return pl.Series("signal", [value], dtype=pl.Int8)
