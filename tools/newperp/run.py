"""One weekly run of the NL-P paper log: settle the week that ended, then open this week's paper legs with a snapshot of the order book.

    python -m tools.newperp.run [--as-of 2026-11-09T04:07:00]

Started on Monday at 04:07 UTC by Windows Task Scheduler through scripts/ops/run_newperp_weekly.sh. --as-of replays a past instant for a rehearsal, with NEWPERP_ROOT outside the repo; a past order book cannot be read, so a replay records none.
The ledger is append only and holds prices, minutes, book costs and counts. The return is computed once, at the read; the status file shows counts and book costs only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.newperp import config as cf
from tools.newperp import fetch as fx
from tools.newperp import rules
from tools.selection import fetch as sf
from tools.selection.config import write_text

WEEK_MS = 7 * rules.DAY_MS
LIVE_BOOK_MINUTES = 60              # a book snapshot counts as the entry or exit book only this close to the bar it prices


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf8").splitlines()
    out = []
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            if i < len(lines) - 1:
                raise
    return out


def append(path: Path, recs: list[dict]) -> None:
    if not recs:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf8") as f:
        for r in recs:
            f.write(fx.dumps(r) + "\n")
        f.flush()
        os.fsync(f.fileno())


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%MZ")


def real_time(now_ms: int) -> bool:
    """A replay of a past instant must not record today's order book as that instant's book."""
    return abs(time.time() * 1000 - now_ms) < 10 * 60_000


def stamp_of(now_ms: int) -> int:
    """The Monday 00:00 UTC at or before now."""
    d = datetime.fromtimestamp(now_ms / 1000, UTC)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)


def book(client: fx.Client, symbol: str, side: str, usd_each: float, live: bool) -> dict | None:
    if not live:
        return None
    d = client.get(fx.DEPTH, {"symbol": symbol, "limit": cf.DEPTH_LIMIT})
    return {"paper": rules.book_cost(d, side, usd_each), "usd_1k": rules.book_cost(d, side, 1_000), "usd_10k": rules.book_cost(d, side, 10_000),
            "at": iso(int(time.time() * 1000))}


def settle(client: fx.Client, legs: list[dict], now_ms: int, log) -> list[dict]:
    """Close every leg whose exit bar has opened: exit open, stop fill from 1m bars, funding, and the exit book when the run is near the exit."""
    out = []
    for leg in legs:
        a, b = leg["entry_ms"], leg["exit_ms"]
        if b > now_ms - 60_000:
            continue
        try:
            h = fx.klines(client, leg["symbol"], "1h", b, b + rules.HOUR_MS)
            exit_open = rules.open_at(h, b)
            stop = None
            if leg["side"] == "short":
                stop = rules.stop_fill(fx.klines(client, leg["symbol"], "1m", a, b), leg["entry_open"])
            ev = fx.funding(client, leg["symbol"], a, b)
            end = stop["minute_ms"] if stop else b
            live = real_time(now_ms) and now_ms - b <= LIVE_BOOK_MINUTES * 60_000
            bk = book(client, leg["symbol"], "buy" if leg["side"] == "short" else "sell", leg["usd"], live) if exit_open is not None else None
        except sf.FetchStop:
            raise
        except sf.FetchError as e:
            log(f"settle {leg['id']}: {e}, left for the next run")
            continue
        out.append({"kind": "leg_close", "id": leg["id"], "exit_open": exit_open, "stop": stop, "funding": rules.funding_sum(ev, a, end),
                    "funding_events": sum(1 for t, _ in ev if a <= t < end), "exit_book": bk, "gone": exit_open is None})
    return out


def open_week(client: fx.Client, stamp: int, now_ms: int, log) -> list[dict]:
    """This week's legs. A new perp without an entry bar is skipped and counted."""
    entry = stamp + cf.ENTRY_HOURS * rules.HOUR_MS
    exit_ = entry + WEEK_MS
    info = client.get(fx.EXCHANGE_INFO)
    new = rules.new_perps(info, stamp)
    live = real_time(now_ms) and now_ms - entry <= LIVE_BOOK_MINUTES * 60_000
    skipped: list[str] = []
    week = {"kind": "week_open", "stamp": iso(stamp), "stamp_ms": stamp, "entry_ms": entry, "exit_ms": exit_, "found": [n["symbol"] for n in new],
            "skipped": skipped, "book_live": live, "delay_min": round((now_ms - entry) / 60_000, 1)}
    legs = []
    for book_name in cf.BOOKS:                       # two paper books, each .5 short against its own BTC .5
        group = [p for p in new if rules.book_of(p) == book_name]
        n = len(group)
        shorts = []
        for p in group:
            rows = fx.klines(client, p["symbol"], "1h", entry, entry + rules.HOUR_MS)
            e = rules.open_at(rows, entry)
            if e is None:
                skipped.append(p["symbol"])
                continue
            usd = cf.BOOK_USD * cf.SHORT_SIDE / n
            shorts.append({"kind": "leg_open", "id": f"{iso(stamp)[:10]}-{p['symbol']}", "book": book_name, "stamp_ms": stamp, "symbol": p["symbol"],
                           "side": "short", "weight": -cf.SHORT_SIDE / n, "usd": usd, "entry_ms": entry, "exit_ms": exit_, "entry_open": e,
                           "onboard": iso(p["onboard_ms"]), "weeks_since_listing": (stamp - p["onboard_ms"]) // WEEK_MS + 1, "sub": p["sub"],
                           "entry_book": book(client, p["symbol"], "sell", usd, live)})
        if shorts:
            rows = fx.klines(client, cf.BTC, "1h", entry, entry + rules.HOUR_MS)
            usd = cf.BOOK_USD * cf.SHORT_SIDE
            shorts.append({"kind": "leg_open", "id": f"{iso(stamp)[:10]}-{cf.BTC}-{book_name}", "book": book_name, "stamp_ms": stamp, "symbol": cf.BTC,
                           "side": "long", "weight": cf.SHORT_SIDE, "usd": usd, "entry_ms": entry, "exit_ms": exit_,
                           "entry_open": rules.open_at(rows, entry), "entry_book": book(client, cf.BTC, "buy", usd, live)})
        legs += shorts
    week["legs"] = len(legs)
    log(f"week {week['stamp']}: new perps {week['found']}, legs {len(legs)}, skipped {skipped}, book live {live}")
    return [week] + legs


def status(recs: list[dict], now_ms: int) -> dict:
    """Counts and book costs only, per book. No price change, no return."""
    weeks = [r for r in recs if r["kind"] == "week_open"]
    opens = {r["id"]: r for r in recs if r["kind"] == "leg_open"}
    closes = {r["id"]: r for r in recs if r["kind"] == "leg_close"}
    out: dict = {"as_of": iso(now_ms), "weeks": len(weeks), "weeks_with_legs": sum(1 for w in weeks if w["legs"]),
                 "open_legs": len(opens) - len(closes), "read_at_core_symbol_weeks": cf.READ_SYMBOL_WEEKS,
                 "first_stamp": cf.FIRST_STAMP, "last_week": weeks[-1]["stamp"] if weeks else None}
    for b in cf.BOOKS:
        shorts = [k for k, r in opens.items() if r["side"] == "short" and r.get("book", "core") == b]
        settled = [k for k in shorts if k in closes]
        costs = sorted(opens[k]["entry_book"]["paper"]["cost_bps"] for k in shorts
                       if opens[k].get("entry_book") and opens[k]["entry_book"]["paper"]["cost_bps"] is not None)
        out[b] = {"short_legs": len(shorts), "settled_symbol_weeks": len(settled), "stopped": sum(1 for k in settled if closes[k]["stop"]),
                  "entry_book_cost_bps_median": costs[len(costs) // 2] if costs else None}
    return out


def run(now_ms: int, client: fx.Client, r: Path, log=print) -> dict:
    path = cf.ledger_path(r)
    recs = load(path)
    opens = [x for x in recs if x["kind"] == "leg_open"]
    closed = {x["id"] for x in recs if x["kind"] == "leg_close"}
    new = settle(client, [x for x in opens if x["id"] not in closed], now_ms, log)
    stamp = stamp_of(now_ms)
    done = {x["stamp_ms"] for x in recs if x["kind"] == "week_open"}
    first = int(datetime.fromisoformat(cf.FIRST_STAMP).replace(tzinfo=UTC).timestamp() * 1000)
    if stamp >= first and stamp not in done and now_ms >= stamp + cf.ENTRY_HOURS * rules.HOUR_MS:
        new += open_week(client, stamp, now_ms, log)
    new.append({"kind": "run", "at": iso(now_ms), "calls": client.calls})
    append(path, new)
    s = status(recs + new, now_ms)
    write_text(cf.status_path(r), json.dumps(s, indent=1, sort_keys=True))
    return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--as-of", help="replay instant, UTC ISO, for a rehearsal outside the repo")
    a = p.parse_args(argv)
    r = cf.root()
    if a.as_of and r.is_relative_to(cf.REPO):
        print("--as-of writes a replay: set NEWPERP_ROOT outside the repo", file=sys.stderr)
        return 2
    now_ms = int(datetime.fromisoformat(a.as_of).replace(tzinfo=UTC).timestamp() * 1000) if a.as_of else int(time.time() * 1000)
    s = run(now_ms, fx.Client(), r, log=lambda x: print(x, flush=True))
    print(json.dumps(s, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
