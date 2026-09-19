#!/usr/bin/env python3
"""Live go-live monitoring dashboard for the tournament engine.

Reads two data sources:
  1. JSONL audit log (e.g. logs/audit.jsonl or data/execution/binance_live_audit.jsonl)
     — tailed for real-time promotion / circuit-breaker / gate events.
  2. SQLite SelectionAudit store (e.g. data/tournament_audit.sqlite3)
     — queried for aggregate metrics and current incumbent state.

Usage::
    # Follow live events (tail + refresh every 5s)
    python scripts/monitoring_dashboard.py --follow

    # One-shot snapshot
    python scripts/monitoring_dashboard.py --snapshot
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ALERT_SHARPE_THRESHOLD = -0.50
ALERT_EXPOSURE_CHANGE = 0.10  # flag if exposure multiplier changes by >10%

# ── Data source paths ────────────────────────────────────────────────────

DEFAULT_JSONL = [
    "data/tournament_paper/BTC_USDT/router_audit.jsonl",
    "data/tournament_paper/BTC_USDT_ETH_USDT_SOL_USDT/router_audit.jsonl",
    "data/execution/binance_live_audit.jsonl",
    "logs/audit.jsonl",
]
DEFAULT_SQLITE = [
    "data/tournament_paper/BTC_USDT/audit/tournament_audit.sqlite3",
    "data/tournament_paper/BTC_USDT_ETH_USDT_SOL_USDT/audit/tournament_audit.sqlite3",
    "data/tournament_audit.sqlite3",
    "data/tournament/audit.db",
]

# Event prefixes we care about from the JSONL log
PROMOTION_EVENTS = {"TOURNAMENT_PROMOTION"}
CIRCUIT_EVENTS = {"TOURNAMENT_CIRCUIT_BREAKER_DEMOTE"}
GATE_PASS_EVENTS = {"TOURNAMENT_SIGNIFICANCE_GATE_PASS"}
GATE_BLOCK_EVENTS = {"TOURNAMENT_SIGNIFICANCE_GATE_BLOCK"}


def find_file(candidates: list[str]) -> Path | None:
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p
    return None


def tail_file(path: Path, offset: float):
    """Generator that yields new lines appended to *path* since *offset*."""
    with open(path, "r", encoding="utf-8") as fh:
        fh.seek(0, 2)  # end
        size = fh.tell()
        if offset > size:
            offset = 0  # file was truncated/rotated
        fh.seek(int(offset))
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line, fh.tell()


def classify_jsonl_event(raw: str) -> dict | None:
    """Parse a JSONL audit line; return enriched dict or None."""
    try:
        evt = json.loads(raw)
    except json.JSONDecodeError:
        return None
    name = evt.get("event", "")
    if name.startswith("TOURNAMENT_"):
        evt["event_class"] = (
            "Promotion" if name in PROMOTION_EVENTS
            else "CircuitBreaker" if name in CIRCUIT_EVENTS
            else "GatePass" if name in GATE_PASS_EVENTS
            else "GateBlock" if name in GATE_BLOCK_EVENTS
            else "Other"
        )
        return evt
    return None


# ── SQLite queries ──────────────────────────────────────────────────────

def query_current_state(db_path: Path) -> list[dict]:
    """Return the latest audit entry per symbol/timeframe."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol, timeframe
                       ORDER BY observed_at DESC
                   ) AS rn
            FROM entries
        ) WHERE rn = 1
        ORDER BY symbol, timeframe
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_recent_events(db_path: Path, limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM entries ORDER BY observed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_sharpe_history(db_path: Path, symbol: str, limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT observed_at, chosen_strategy_id, exposure_multiplier,
               shadow_sharpe, shadow_sharpe_delta
        FROM entries
        WHERE symbol = ? AND shadow_sharpe IS NOT NULL
        ORDER BY observed_at DESC LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Dashboard rendering ──────────────────────────────────────────────────

def render_snapshot(db_path: Path | None, jsonl_path: Path | None):
    print(f"\n{'='*70}")
    print(f"  Tournament Go-Live Dashboard  —  {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*70}")

    # ── Section 1: Current Incumbents (from SQLite) ─────────────────────
    print("\n## Current Incumbents (SQLite SelectionAudit)\n")
    if db_path:
        state = query_current_state(db_path)
        if not state:
            print("  (no audit entries yet)")
        else:
            print(f"  {'Symbol':12} {'TF':6} {'Incumbent':24} {'Sharpe':>8} {'Δ_vs_Inc':>9} "
                  f"{'ExpMult':>8} {'Anomalies':>10}")
            print(f"  {'-'*12} {'-'*6} {'-'*24} {'-'*8} {'-'*9} {'-'*8} {'-'*10}")
            for s in state:
                sharpe = s.get("shadow_sharpe")
                delta = s.get("shadow_sharpe_delta")
                mult = s.get("exposure_multiplier", 0.0)
                anomalies = json.loads(s.get("anomaly_flags") or "[]")
                sharpe_str = f"{sharpe:.3f}" if sharpe is not None else "—"
                delta_str = f"{delta:+.3f}" if delta is not None else "—"
                anom_str = ",".join(anomalies) if anomalies else "—"

                # Alert: Sharpe below threshold
                alert = ""
                if sharpe is not None and sharpe < ALERT_SHARPE_THRESHOLD:
                    alert = f" ⚠️ Sharpe < {ALERT_SHARPE_THRESHOLD}"

                print(f"  {s['symbol']:12} {s['timeframe']:6} {s.get('chosen_strategy_id') or '—':24} "
                      f"{sharpe_str:>8} {delta_str:>9} {mult:>8.2f} {anom_str:>10}{alert}")

        # ── Section 2: Recent Events from SQLite ──────────────────────────
        print("\n## Recent Routing Decisions (last 20)\n")
        events = query_recent_events(db_path, limit=20)
        for e in reversed(events):
            ts = e.get("observed_at", "?")
            strat = e.get("chosen_strategy_id") or "—"
            mult = e.get("exposure_multiplier", 0.0)
            inc = e.get("incumbent_strategy_id") or "—"
            reason = (e.get("reason") or "")[:40]
            print(f"  {ts}  {e['symbol']:10}  strat={strat:20}  inc={inc:20}  "
                  f"mult={mult:.2f}  {reason}")
    else:
        print("  (no SQLite audit db found)")

    # ── Section 3: JSONL Audit Log (recent events) ──────────────────────
    print("\n## Recent JSONL Audit Events\n")
    if jsonl_path and jsonl_path.exists():
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        recent = []
        for line in lines[-50:]:
            evt = classify_jsonl_event(line)
            if evt:
                recent.append(evt)
        if not recent:
            print("  (no tournament events in JSONL log)")
        else:
            for evt in reversed(recent[-20:]):
                cls = evt.get("event_class", "?")
                ts = evt.get("timestamp", "?")
                print(f"  [{cls:14}] {ts}  {evt.get('symbol', '?'):10}  "
                      f"{evt.get('event', '?'):40}  "
                      f"inc={evt.get('old_incumbent', evt.get('incumbent', '—'))} "
                      f"→ new={evt.get('new_incumbent', evt.get('challenger', '—'))}")
                # Print alert details for circuit breakers
                if evt.get("event_class") == "CircuitBreaker":
                    print(f"                    reason={evt.get('reason', '')}")
                if evt.get("event_class") == "GateBlock":
                    print(f"                    p_value={evt.get('p_value', '?')} "
                          f"alpha={evt.get('alpha', '?')}")
    else:
        print(f"  (no JSONL audit log found at {jsonl_path or 'default paths'})")

    print(f"\n{'='*70}\n")


def follow_mode(db_candidates: list[str], jsonl_candidates: list[str],
                refresh_interval: int = 5):
    """Follow JSONL log and refresh SQLite snapshot at intervals."""
    db_path = find_file(db_candidates)
    jsonl_path = find_file(jsonl_candidates)

    if not jsonl_path:
        print(f"ERROR: No JSONL audit log found at {jsonl_candidates}", file=sys.stderr)
        sys.exit(1)

    print(f"Following: {jsonl_path}")
    if db_path:
        print(f"SQLite:    {db_path}")
    print(f"Refresh:   {refresh_interval}s")
    print(f"Alerts:    Sharpe < {ALERT_SHARPE_THRESHOLD}, "
          f"circuit breaker, failed promotions\n")

    offset = 0
    last_refresh = 0

    for line, offset in tail_file(jsonl_path, 0.0):
        evt = classify_jsonl_event(line.strip())
        if evt is None:
            continue
        ts = evt.get("timestamp", "?")
        cls = evt.get("event_class", "?")
        symbol = evt.get("symbol", "?")

        marker = ""
        if evt["event"] in PROMOTION_EVENTS:
            marker = " 🟢 PROMOTION"
        elif evt["event"] in CIRCUIT_EVENTS:
            marker = " 🔴 CIRCUIT BREAKER"
        elif evt["event"] in GATE_BLOCK_EVENTS:
            marker = " 🔴 GATE BLOCK"
        elif evt["event"] in GATE_PASS_EVENTS:
            marker = " 🟡 GATE PASS"

        print(f"[{ts}] [{cls:14}] {symbol:10} {evt['event']}{marker}")
        if evt["event"] in CIRCUIT_EVENTS:
            print(f"  → incumbent={evt.get('incumbent')} challenger={evt.get('challenger')} "
                  f"inc_sharpe={evt.get('inc_sharpe'):.3f}")
        if evt["event"] in PROMOTION_EVENTS:
            print(f"  → {evt.get('old_incumbent', '—')} → {evt.get('new_incumbent')} "
                  f"({evt.get('regime', '?')})")
        if evt["event"] in GATE_BLOCK_EVENTS:
            print(f"  → p={evt.get('p_value', '?'):.4f} alpha={evt.get('alpha_adj', evt.get('alpha', '?'))}")

        # Refresh SQLite snapshot periodically
        now = time.time()
        if db_path and now - last_refresh >= refresh_interval:
            render_snapshot(db_path, jsonl_path)
            last_refresh = now


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tournament go-live monitoring dashboard")
    parser.add_argument("--follow", action="store_true", help="Tail JSONL log + refresh SQLite")
    parser.add_argument("--snapshot", action="store_true", help="One-shot snapshot from SQLite + JSONL")
    parser.add_argument("--jsonl", default=None, help="Path to JSONL audit log")
    parser.add_argument("--db", default=None, help="Path to SQLite audit db")
    parser.add_argument("--refresh", type=int, default=5, help="Refresh interval (seconds) in follow mode")
    args = parser.parse_args()

    jsonl_candidates = [args.jsonl] if args.jsonl else DEFAULT_JSONL
    db_candidates = [args.db] if args.db else DEFAULT_SQLITE

    if args.follow:
        follow_mode(db_candidates, jsonl_candidates, args.refresh)
    else:
        db_path = find_file(db_candidates)
        jsonl_path = find_file(jsonl_candidates)
        render_snapshot(db_path, jsonl_path)
