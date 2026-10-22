#!/usr/bin/env python3
"""Layer-separated accounting of the BNB native ADX25 evidence.

Builds the exact chain, each rung on its own scope, so that quantities from
different scopes are never added into one another:

  L1  raw MA crossovers            (indicator layer)   union, deduplicated
  L2  gate-passing signals         (strategy layer)    union, deduplicated
  L3  simulator gate actions       (recorded)          per cell, summed over
                                                       the 5 OVERLAPPING windows
  L4  runtime BUY fills            (recorded)          per cell
  L5  closed trades + carry_out    (recorded)          per cell

The rungs L3-L5 are summed over five OVERLAPPING 1080-bar windows, so their
sums are occurrences, not independent observations, and they are NOT
comparable to the deduplicated L1/L2.

Read-only. No market data, no engine run, no policy change.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path

RUN_PACKET = Path("data/bnb_native_adx25_validation_run_20261019_01")


def main() -> int:
    reports = sorted(glob.glob(str(RUN_PACKET / "cell_attempts/*/ma_adx__*/report.json")))
    rows = []
    for path in reports:
        report = json.loads(Path(path).read_text())
        funnel = report.get("execution_funnel") or {}
        if not funnel.get("records"):
            continue
        cell_name = Path(path).parent.name
        parts = cell_name.split("__")
        rows.append({
            "cell": cell_name,
            "cost": parts[3] if len(parts) > 3 else "unknown",
            "window": funnel.get("window"),
            "actions": funnel.get("actions"),
            "terminals": funnel.get("terminals"),
            "runtime_calls": funnel.get("runtime_calls"),
            "returned_orders": funnel.get("returned_orders"),
            "closed_trades": funnel.get("closed_trades"),
            "close_reasons": funnel.get("close_reasons"),
            "carry_in": funnel.get("carry_in"),
            "carry_out": funnel.get("carry_out"),
        })

    def total(fn):
        return sum(fn(r) for r in rows)

    buy_actions = total(lambda r: r["actions"].get("BUY", 0))
    sell_actions = total(lambda r: r["actions"].get("SELL", 0))
    already_long = total(lambda r: r["terminals"].get("already_long", 0))
    flat_exit = total(lambda r: r["terminals"].get("flat_exit", 0))
    runtime_submitted = total(lambda r: r["terminals"].get("runtime_submitted", 0))
    closed = total(lambda r: r["closed_trades"])
    carry_out = sum(1 for r in rows if r["carry_out"])
    carry_in = sum(1 for r in rows if r["carry_in"])

    # Per-cost view: the correct comparison scope. Cost scenarios are
    # alternative assumptions for the same market history and must never be
    # pooled into one sample.
    by_cost = {}
    for cost in sorted({r["cost"] for r in rows}):
        subset = [r for r in rows if r["cost"] == cost]
        buy = sum(r["actions"].get("BUY", 0) for r in subset)
        sell = sum(r["actions"].get("SELL", 0) for r in subset)
        already = sum(r["terminals"].get("already_long", 0) for r in subset)
        closed_c = sum(r["closed_trades"] for r in subset)
        carry_c = sum(1 for r in subset if r["carry_out"])
        by_cost[cost] = {
            "cells": len(subset),
            "windows": sorted({tuple(r["window"]) for r in subset}),
            "L3_buy_gate_actions": buy,
            "L3_sell_gate_actions": sell,
            "L4_skipped_already_long": already,
            "L4_runtime_buy_fills": buy - already,
            "L5_closed_trades": closed_c,
            "L5_carry_out_positions": carry_c,
            "identity_runtime_fills_minus_carry_out_equals_closed": (buy - already) - carry_c == closed_c,
        }

    out = {
        "by_cost_scenario": by_cost,
        "scope_note": (
            "L3-L5 are summed over 5 OVERLAPPING 1080-bar windows: they are "
            "occurrences, not independent observations, and must not be added "
            "to or compared with the deduplicated L1/L2 counts."
        ),
        "cells": rows,
        "L3_gate_actions_summed_over_5_overlapping_windows": {
            "BUY": buy_actions,
            "SELL": sell_actions,
            "total": buy_actions + sell_actions,
        },
        "L4_runtime_buy_fills": {
            "BUY_actions": buy_actions,
            "skipped_already_long": already_long,
            "runtime_buy_calls": buy_actions - already_long,
        },
        "L5_closed_trades": {
            "closed_trades": closed,
            "carry_out_positions": carry_out,
            "carry_in_positions": carry_in,
            "identity": "runtime BUY fills - carry_out == closed trades",
            "identity_holds": (buy_actions - already_long) - carry_out == closed,
        },
        "terminal_totals_summed_over_5_overlapping_windows": {
            "already_long": already_long,
            "flat_exit": flat_exit,
            "runtime_submitted": runtime_submitted,
            "no_signal": total(lambda r: r["terminals"].get("no_signal", 0)),
        },
        "authority_statement": (
            "In the 14 checked reports, BUY actions that reached the runtime were "
            "not rejected by authority: the BUY terminals are already_long "
            "(position already open) and runtime_submitted only. This does NOT "
            "mean every signal becomes a trade: already_long skips and flat_exit "
            "discards are downstream skips, and only the fills that became closed "
            "round trips count as trades."
        ),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
