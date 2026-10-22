#!/usr/bin/env python3
"""V2 — BNB native ADX25 bottleneck decomposition (corrected, 2026-10-20 rev2).

Supersedes decompose_crossover_bottleneck.py v1. Corrections:

  C1. SELL gate direction was inverted in v1. The candidate's SELL gate needs
      ``NOT trend_up``; v1 used ``trend_up`` for both directions, so it reported
      33 down-crossovers as "rejected by ADX" when they actually PASS the SELL
      gate. Corrected gates:
          BUY  = MA20 cross above MA80 AND adx > 25 AND plus_di >  minus_di
          SELL = MA20 cross below MA80 AND adx > 25 AND plus_di <= minus_di
  C2. v1's rejection buckets were mislabelled (DI failures called ADX failures
      and vice versa) and were not a partition: the "both conditions fail"
      case fell through uncounted, so BUY showed 35+2+6=43 against 48 raw.
      v2 uses an explicit 4-way partition and ASSERTS the buckets sum to the
      raw count for every window and direction.
  C3. v1 validated only per-window COUNTS. Counts cannot distinguish an
      off-by-one, and indeed gave a false 10/10: the counts are IDENTICAL under
      both candidate alignments. v2 validates the full PER-BAR action vector
      (execution_funnel.records of all 14 completed cells) and DERIVES the
      alignment by scanning candidate shifts, requiring exactly one shift with
      zero mismatches over all 15120 bar-level assertions.
  C4. v1 made an "arithmetically impossible" claim from a linear
      extrapolation. v2 makes NO such claim. Densities are reported only on
      measured windows; the count for any longer, unrun window is UNKNOWN.
  C5. v1 conflated layers. v2 keeps four layers distinct and labels them:
      raw crossover events (indicator) > gate-passing signals (strategy) >
      simulator gate actions (recorded per bar) > closed trades (recorded
      round trips). A signal bar is not a trade.

Alignment (derived, not assumed)
--------------------------------
action at execution_bar j  ==  full_frame_signal[j - 2]

because (1) canonical_signal_series decides on the window that ends strictly
BEFORE bar j, hence canonical_signal[j] == full_frame_signal[j-1], and (2) the
simulator executes a signal one bar later than its index. The recorded
``decision_bar`` is the canonical signal index (execution_bar - 1), NOT the bar
on which the MA crossover occurred (decision_bar - 1).

Scope: read-only. Hash-bound pre-holdout input; only the already-declared
exposed prefix [0, 5600); holdout rows read = 0. No market run, no strategy,
gate, sizing, authority or risk change.
"""
from __future__ import annotations

import glob
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
FUNNEL = Path("data/native_validation_activation_20261019_01/funnel_review.json")
RUN_PACKET = Path("data/bnb_native_adx25_validation_run_20261019_01")
EXPOSED_PREFIX_BARS = 5600
ADX_THRESHOLD = 25.0
PARAMS = {"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25.0}
ACTION = {1: "BUY", -1: "SELL", 0: "HOLD"}


def _expected_rows() -> list[dict]:
    raw = json.loads(FUNNEL.read_text())
    text = raw["stdout"] if isinstance(raw["stdout"], str) else "\n".join(raw["stdout"])
    return json.loads(text)["rows"]


def _num(value) -> bool:
    return value is not None and value == value


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text())
    binding = protocol["input_binding"]
    path = Path(binding["path"])
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != binding["sha256"]:
        print(json.dumps({"status": "FAIL", "reason": "input_binding_hash_mismatch"}))
        return 2

    df = pl.read_parquet(path).head(EXPOSED_PREFIX_BARS).rename({"timestamp": "time"})
    if "is_closed" in df.columns and df.filter(~pl.col("is_closed")).height:
        print(json.dumps({"status": "FAIL", "reason": "unclosed_bars_present"}))
        return 2

    strategy = MaAdxCrossover(PARAMS)
    ind = strategy.compute_indicators(df)
    signals = strategy.generate_signals(ind).to_list()
    ma_f, ma_s = ind["ma_20"].to_list(), ind["ma_80"].to_list()
    adx = ind["adx"].to_list()
    trend_up = ind["trend_up"].to_list()

    def _act(index: int) -> str:
        if not (0 <= index < len(signals)):
            return "HOLD"
        return ACTION[signals[index]]

    # ── raw crossover events (indicator layer) ───────────────────────────
    raw_up, raw_down = [], []
    for i in range(1, len(ma_f)):
        if not all(_num(v) for v in (ma_f[i], ma_s[i], ma_f[i - 1], ma_s[i - 1])):
            continue
        prev = 1 if ma_f[i - 1] > ma_s[i - 1] else (-1 if ma_f[i - 1] < ma_s[i - 1] else 0)
        curr = 1 if ma_f[i] > ma_s[i] else (-1 if ma_f[i] < ma_s[i] else 0)
        if prev == 0 or curr == 0 or prev == curr:
            continue
        (raw_up if curr == 1 else raw_down).append(i)

    # ── C3: derive the action alignment from the recorded action vector ──
    reports = sorted(glob.glob(str(RUN_PACKET / "cell_attempts/*/ma_adx__*/report.json")))
    records = []
    per_report = []
    for report_path in reports:
        report = json.loads(Path(report_path).read_text())
        funnel = report.get("execution_funnel") or {}
        rows = funnel.get("records") or []
        if not rows:
            continue
        records.extend(rows)
        per_report.append({
            "cell": Path(report_path).parent.name,
            "window": funnel.get("window"),
            "records": len(rows),
            "bar_count": funnel.get("bars"),
            "recorded_actions": funnel.get("actions"),
            "recorded_closed_trades": funnel.get("closed_trades"),
        })

    shift_scan = []
    for shift in range(0, 4):
        mismatches = sum(
            1 for rec in records if rec.get("action") != _act(rec["execution_bar"] - shift)
        )
        shift_scan.append({"shift": shift, "mismatches": mismatches})
    clean = [row["shift"] for row in shift_scan if row["mismatches"] == 0]
    if len(clean) != 1:
        print(json.dumps({"status": "FAIL", "reason": "alignment_not_unique",
                          "shift_scan": shift_scan}, indent=2))
        return 2
    shift = clean[0]

    # per-report mismatch detail under the derived alignment
    for report_path, entry in zip(reports, per_report):
        pass
    report_detail = []
    for report_path in reports:
        report = json.loads(Path(report_path).read_text())
        funnel = report.get("execution_funnel") or {}
        rows = funnel.get("records") or []
        if not rows:
            continue
        bad = [
            {"execution_bar": r.get("execution_bar"), "decision_bar": r.get("decision_bar"),
             "recorded": r.get("action"), "expected": _act(r["execution_bar"] - shift)}
            for r in rows if r.get("action") != _act(r["execution_bar"] - shift)
        ]
        report_detail.append({
            "cell": Path(report_path).parent.name,
            "window": funnel.get("window"),
            "records": len(rows),
            "action_mismatches": len(bad),
            "examples": bad[:3],
        })

    # cross-cost identity of the action vector
    window_vectors: dict[tuple, list] = {}
    for report_path in reports:
        report = json.loads(Path(report_path).read_text())
        funnel = report.get("execution_funnel") or {}
        rows = funnel.get("records") or []
        if not rows:
            continue
        window = tuple(funnel.get("window") or report.get("measurement_window"))
        window_vectors.setdefault(window, []).append(
            {r.get("execution_bar"): r.get("action") for r in rows}
        )
    cross_cost_identical = {
        str(list(window)): all(v == vectors[0] for v in vectors)
        for window, vectors in window_vectors.items()
    }

    # ── C1/C2: correct, complete 4-way partition per direction ──────────
    def _partition(indices: list[int], direction: str) -> dict:
        gate_pass = fail_adx_only = fail_di_only = fail_both = 0
        for i in indices:
            a = adx[i] if i < len(adx) else None
            t = bool(trend_up[i]) if i < len(trend_up) else False
            strong = _num(a) and a > ADX_THRESHOLD
            aligned = t if direction == "up" else (not t)
            if strong and aligned:
                gate_pass += 1
            elif not strong and aligned:
                fail_adx_only += 1
            elif strong and not aligned:
                fail_di_only += 1
            else:
                fail_both += 1
        total = len(indices)
        assert gate_pass + fail_adx_only + fail_di_only + fail_both == total, (
            f"{direction} partition does not sum to raw count")
        return {"raw_crossovers": total, "gate_pass": gate_pass,
                "fail_adx_only": fail_adx_only, "fail_di_only": fail_di_only,
                "fail_both": fail_both}

    expected = {}
    for row in _expected_rows():
        expected.setdefault(tuple(row["window"]), row)

    windows = []
    for (start, end) in sorted(expected):
        row = expected[(start, end)]
        # crossovers that can produce an action inside [start, end)
        aligned_lo, aligned_hi = start - shift, end - shift
        up_stats = _partition([i for i in raw_up if aligned_lo <= i < aligned_hi], "up")
        dn_stats = _partition([i for i in raw_down if aligned_lo <= i < aligned_hi], "down")
        # crossovers occurring strictly inside the window's own bar range
        own_up = _partition([i for i in raw_up if start <= i < end], "up")
        own_dn = _partition([i for i in raw_down if start <= i < end], "down")
        rec_buy = row["actions"]["BUY"]
        rec_sell = row["actions"]["SELL"]
        recomputed_buy = sum(1 for j in range(start, end) if _act(j - shift) == "BUY")
        recomputed_sell = sum(1 for j in range(start, end) if _act(j - shift) == "SELL")
        windows.append({
            "window": [start, end],
            "bars": end - start,
            "aligned_crossover_range": [aligned_lo, aligned_hi],
            "aligned_up": up_stats,
            "aligned_down": dn_stats,
            "window_own_range_up": own_up,
            "window_own_range_down": own_dn,
            "recomputed_gate_actions_buy": recomputed_buy,
            "recomputed_gate_actions_sell": recomputed_sell,
            "recorded_gate_actions_buy": rec_buy,
            "recorded_gate_actions_sell": rec_sell,
            "buy_count_match": recomputed_buy == rec_buy,
            "sell_count_match": recomputed_sell == rec_sell,
            "recorded_closed_trades": row.get("closed_trades"),
            "gate_pass_equals_recorded_actions": (
                up_stats["gate_pass"] == rec_buy and dn_stats["gate_pass"] == rec_sell
            ),
        })

    # ── deduplicated union view, layers kept separate ───────────────────
    union_lo = min(w[0] for w in expected) - shift
    union_hi = max(w[1] for w in expected) - shift
    unique_signal_bars = [j for j in range(union_lo, union_hi) if _act(j) != "HOLD"]
    occurrences_summed_over_windows = sum(
        sum(1 for j in range(start, end) if _act(j - shift) != "HOLD")
        for (start, end) in expected
    )
    up_union = _partition([i for i in raw_up if union_lo <= i < union_hi], "up")
    dn_union = _partition([i for i in raw_down if union_lo <= i < union_hi], "down")

    out = {
        "status": "PASS",
        "supersedes": "decompose_crossover_bottleneck.py v1",
        "scope": "read_only_analysis_of_already_exposed_prefix",
        "input_binding": {"path": str(path), "sha256": actual_sha, "hash_verified": True},
        "rows_used": df.height,
        "file_total_rows": 6795,
        "exposed_prefix_bars": EXPOSED_PREFIX_BARS,
        "holdout_rows_read": 0,
        "candidate": {"strategy_id": "ma_adx", "params": PARAMS, "warmup_bars": 100},
        "gate_definitions": {
            "BUY": "MA20 cross above MA80 AND adx > 25 AND plus_di > minus_di",
            "SELL": "MA20 cross below MA80 AND adx > 25 AND plus_di <= minus_di",
        },
        "alignment": {
            "derived_shift": shift,
            "rule": f"action at execution_bar j == full_frame_signal[j - {shift}]",
            "reason": ("canonical_signal_series decides on the window that ends strictly "
                       "before bar j (canonical_signal[j] == full_frame_signal[j-1]); "
                       "the simulator executes a signal one bar later than its index. "
                       "The recorded decision_bar is the canonical signal index, not "
                       "the crossover bar."),
            "shift_scan": shift_scan,
            "bar_level_assertions": len(records),
            "action_mismatches": sum(
                1 for rec in records if rec.get("action") != _act(rec["execution_bar"] - shift)),
            "cross_cost_action_vector_identical": cross_cost_identical,
            "per_report": report_detail,
        },
        "per_window": windows,
        "union_deduplicated": {
            "union_crossover_range": [union_lo, union_hi],
            "union_bars": union_hi - union_lo,
            "signal_occurrences_summed_over_5_windows": occurrences_summed_over_windows,
            "unique_signal_bars": len(unique_signal_bars),
            "overlap_duplicates": occurrences_summed_over_windows - len(unique_signal_bars),
            "unique_buy_signal_bars": len([i for i in unique_signal_bars if _act(i) == "BUY"]),
            "unique_sell_signal_bars": len([i for i in unique_signal_bars if _act(i) == "SELL"]),
            "up_partition_union": up_union,
            "down_partition_union": dn_union,
        },
        "layer_note": (
            "Four distinct layers: raw crossover events (indicator) > gate-passing "
            "signals (strategy) > simulator gate actions (recorded per bar) > closed "
            "trades (recorded round trips). A signal bar is not a trade; unique signal "
            "bars and unique closed trades are different quantities and neither "
            "replaces the per-cell 30-trade gate."
        ),
        "no_ceiling_claim": (
            "All densities below are measured on the measured windows only. The trade "
            "count of any longer, unrun window is UNKNOWN. No linear extrapolation "
            "and no arithmetic-impossibility claim is made."
        ),
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
