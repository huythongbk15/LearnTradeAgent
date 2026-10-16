"""Observation-only telemetry; never used as a trading or promotion input."""
from collections import Counter
from copy import deepcopy


def export_funnel(records, trades, *, start, end):
    """Isolate execution bars [start,end); never include warm-up records."""
    rows = [deepcopy(r) for r in records if start <= r["execution_bar"] < end]
    closes, missing = [], 0
    for trade in trades:
        meta = trade.get("metadata", {}).get("simulation", {})
        bar = meta.get("exit_bar_index")
        if type(bar) is not int:
            missing += 1
            continue
        if start <= bar < end:
            entry = meta.get("entry_bar_index")
            closes.append({"trade_id": trade.get("id"), "execution_bar": bar,
                           "entry_order_id": trade.get("entry_order_id"),
                           "exit_order_id": trade.get("exit_order_id"),
                           "reason": trade.get("reason", "unknown"),
                           "carry_in": type(entry) is int and entry < start})
    for row in rows:
        row["decision_precedes_scope"] = row["decision_bar"] < start
    return {
        "version": 1, "scope": "execution_bar_window", "window": [start, end],
        "signal_semantics": "simulator_gate_signal_not_raw_forecast_posterior",
        "order_semantics": "strategy_returned_orders_excludes_protective_orders",
        "fill_semantics": "returned_order_cumulative_snapshot_not_lifecycle_fill_events",
        "bars": len(rows), "actions": dict(Counter(r["action"] for r in rows)),
        "terminals": dict(Counter(r["terminal"] for r in rows)),
        "runtime_calls": sum(r["runtime_called"] for r in rows),
        "returned_orders": sum(len(r["orders"]) for r in rows),
        "closed_trades": len(closes), "close_reasons": dict(Counter(t["reason"] for t in closes)),
        "missing_trade_bar_attribution": missing,
        "carry_in": rows[0]["position_before"] if rows else None,
        "carry_out": rows[-1]["position_after"] if rows else None,
        "records": rows, "closes": closes,
    }


def isolate_funnel(funnel, trades, *, start, end):
    """Legacy reports stay unavailable rather than fabricating zero counts."""
    if funnel is None:
        return None
    return export_funnel(funnel["records"], trades, start=start, end=end)
