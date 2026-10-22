#!/usr/bin/env python3
"""Vòng 3 — Feasibility bound for the operator's policy choice.

This is NOT a candidate evaluation. No returns, no Sharpe, no trades, no
backtest, no strategy code change. It measures only how many *entry events
are available* on the already-exposed prefix, to answer one question:

  "Is a state-entry / re-entry policy arithmetically capable of producing
   the 30-trade eligibility gate inside a feasible validation window,
   given the available pre-holdout bars?"

Event definitions (all derived from the candidate's own indicator columns, so
no new policy is created and nothing is implemented):
  * crossover_up          : MA20 crossing above MA80  (current candidate's BUY event)
  * crossover_down        : MA20 crossing below MA80
  * trend_episode_adx25   : maximal contiguous run of (plus_di > minus_di & adx > 25)
                            -> upper bound on entries for a "enter on up-trend
                            state" policy (one entry per episode)
  * trend_episode_adxX    : same with a weaker ADX threshold (sensitivity of
                            the bound, not a proposal)

Method equivalence to the native path is inherited from round 2 (validated:
10/10 recorded action counts reproduced).

Scope: read-only, hash-bound pre-holdout input, exposed prefix only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import polars as pl

from trading_agent.strategies.enhanced_ma import MaAdxCrossover

PROTOCOL = Path(
    "data/bnb_native_adx25_validation_run_20261019_01/research_protocol.json"
)
EXPOSED_PREFIX_BARS = 5600
WINDOWS = [(2360, 3440), (2900, 3980), (3440, 4520), (3980, 5060), (4520, 5600)]
MONTH_BARS = 180  # month = 180 bars at 4h, per the feasibility audit convention


def _episodes(flags: list[bool]) -> list[int]:
    """Return the length of each maximal contiguous True run."""
    runs, current = [], 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text())
    binding = protocol["input_binding"]
    path = Path(binding["path"])
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != binding["sha256"]:
        print(json.dumps({"status": "FAIL", "reason": "input_binding_hash_mismatch"}))
        return 2

    df = pl.read_parquet(path).head(EXPOSED_PREFIX_BARS)
    df = df.rename({"timestamp": "time"})
    strategy = MaAdxCrossover({"fast_period": 20, "slow_period": 80,
                                "adx_period": 14, "adx_threshold": 25.0})
    ind = strategy.compute_indicators(df)
    adx = ind["adx"].to_list()
    trend_up = ind["trend_up"].to_list()

    def _ok(value):
        return value is not None and value == value

    rows = []
    for (start, end) in WINDOWS:
        # Event availability is a property of the window's OWN bar range. The
        # simulator alignment (action bar = crossover bar + 2) shifts the
        # mapping by two bars out of 1080 and does not change the availability
        # measurement; it is documented in the decomposition packet.
        lo, hi = start, end
        for adx_thr in (25.0, 20.0, 15.0):
            flags = [
                bool(trend_up[i]) and _ok(adx[i]) and adx[i] > adx_thr
                for i in range(lo, hi)
            ]
            runs = _episodes(flags)
            rows.append({
                "window": [start, end],
                "bars": end - start,
                "adx_threshold": adx_thr,
                "trend_state_bars": sum(flags),
                "trend_state_bar_pct": round(100 * sum(flags) / len(flags), 2),
                "trend_episodes": len(runs),
                "mean_episode_bars": round(sum(runs) / len(runs), 2) if runs else 0.0,
                "max_episode_bars": max(runs) if runs else 0,
            })

    per_window_ep25 = [r["trend_episodes"] for r in rows if r["adx_threshold"] == 25.0]
    mean_ep25 = sum(per_window_ep25) / len(per_window_ep25)
    min_ep25, max_ep25 = min(per_window_ep25), max(per_window_ep25)

    out = {
        "status": "PASS",
        "scope": "event_availability_feasibility_bound_only_not_a_candidate_evaluation",
        "input_binding": {"path": str(path), "sha256": actual_sha, "hash_verified": True},
        "rows_used": df.height,
        "holdout_rows_read": 0,
        "definitions": {
            "trend_episode": "maximal contiguous run of (plus_di > minus_di AND adx > threshold)",
            "entry_bound": "one entry per episode is the maximum for a state-entry policy",
        },
        "per_window": rows,
        "measured_trend_episodes_per_1080bar_window_adx25": {
            "min": min_ep25,
            "max": max_ep25,
            "mean": round(mean_ep25, 2),
        },
        "no_projection": (
            "Measured on the five measured 1080-bar windows only. The episode "
            "count of any longer, unrun window is UNKNOWN. No linear "
            "extrapolation, no projected trade count, and no claim that a "
            "30-trade gate would be met is made here; whether a state-entry "
            "policy would clear the gate is a question for a preregistered "
            "candidate, not for this probe."
        ),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
