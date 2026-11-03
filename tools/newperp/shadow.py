"""Shadow ledger of the new perp pair book on mainnet prices: paper only.

The demo venue lists almost none of the new mainnet perps, so the frozen rule
of ``trading_agent.execution.perp.rules`` is also recorded here for every due
listing, from public mainnet 1m bars: entry at the first minute a run sees it
inside its window, the stop from 1m highs, the exit at the hold end. Only the
allowlisted public futures host is called (``tools.newperp.fetch``). It
records prices, never a return: do not compute returns before the read in
docs/operations/PERP_DEMO_RUNNER.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.newperp import fetch as fx
from tools.newperp import rules as nr
from tools.selection import fetch as sf
from tools.selection.config import write_text
from trading_agent.execution.perp import rules as pr

MIN_MS = 60_000


def state_path(root: Path) -> Path:
    return root / "shadow.json"


def ledger_path(root: Path) -> Path:
    return root / "shadow_ledger.jsonl"


def load(root: Path) -> dict:
    p = state_path(root)
    return json.loads(p.read_text(encoding="utf8")) if p.exists() else {"open": {}, "seen": [], "closed": 0}


def _append(root: Path, rec: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(ledger_path(root), "a", encoding="utf8") as h:
        h.write(json.dumps(rec, sort_keys=True) + "\n")
        h.flush()
        os.fsync(h.fileno())


def _open_at(client: fx.Client, symbol: str, t_ms: int) -> float | None:
    rows = fx.klines(client, symbol, "1m", t_ms, t_ms + MIN_MS)
    return nr.open_at(rows, t_ms)


def step(client: fx.Client, info: dict, now_ms: int, root: Path) -> dict:
    """One shadow run: open due listings, settle stops and hold ends. Returns counts."""

    st = load(root)
    now_min = now_ms - now_ms % MIN_MS
    every = {str(s.get("symbol")) for s in info.get("symbols", [])}
    seen = set(st["seen"]) | set(st["open"])
    due, skipped = pr.due_entries(info, every, now_ms, seen)
    out = {"opened": 0, "closed": 0, "skipped": 0}
    for s in skipped:
        st["seen"].append(s["symbol"])
        _append(root, {"event": "shadow_skip", "t": now_min, **s})
        out["skipped"] += 1
    for listing in due:
        try:
            e = _open_at(client, listing.symbol, now_min)
        except ValueError:            # refused before any call: a masked symbol
            st["seen"].append(listing.symbol)
            _append(root, {"event": "shadow_skip", "t": now_min, "symbol": listing.symbol, "reason": "masked"})
            out["skipped"] += 1
            continue
        b = _open_at(client, pr.HEDGE, now_min)
        if e is None or b is None:
            continue                  # no bar yet; the next run inside the window tries again
        rec = {"symbol": listing.symbol, "onboard_ms": listing.onboard_ms, "entry_ms": now_min,
               "delay_min": (now_min - listing.entry_ms) // MIN_MS, "entry": e, "btc_entry": b,
               "exit_ms": now_min + pr.HOLD_H * pr.HOUR_MS, "checked_to": now_min}
        st["open"][listing.symbol] = rec
        _append(root, {"event": "shadow_open", **rec})
        out["opened"] += 1
    for sym, rec in list(st["open"].items()):
        end = min(now_min, rec["exit_ms"])
        if end > rec["checked_to"]:
            m1 = fx.klines(client, sym, "1m", rec["checked_to"], end)
            hit = nr.stop_fill(m1, rec["entry"], pr.STOP_X)
            if hit is not None:
                _close(client, root, st, sym, rec, hit["minute_ms"], "stop", hit)
                out["closed"] += 1
                continue
            rec["checked_to"] = end
        if now_min >= rec["exit_ms"]:
            _close(client, root, st, sym, rec, rec["exit_ms"], "hold", None)
            out["closed"] += 1
    write_text(state_path(root), json.dumps(st, sort_keys=True, indent=1))
    return out


def _close(client: fx.Client, root: Path, st: dict, sym: str, rec: dict, t_ms: int, reason: str, hit: dict | None) -> None:
    px = None if hit is not None else _open_at(client, sym, t_ms)
    b = _open_at(client, pr.HEDGE, t_ms)
    if (hit is None and px is None) or b is None:
        raise sf.FetchError(f"no 1m bar to close {sym} at {t_ms}")
    fund = fx.funding(client, sym, rec["entry_ms"], t_ms)
    bfund = fx.funding(client, pr.HEDGE, rec["entry_ms"], t_ms)
    _append(root, {
        "event": "shadow_close", "symbol": sym, "reason": reason, "t": t_ms, "exit": px, "btc_exit": b,
        "stop": hit, "funding": nr.funding_sum(fund, rec["entry_ms"], t_ms),
        "btc_funding": nr.funding_sum(bfund, rec["entry_ms"], t_ms),
    })
    del st["open"][sym]
    st["seen"].append(sym)
    st["closed"] = int(st.get("closed", 0)) + 1
