"""One run of the forced selling paper log: judge every closed minute since the last run, record an event and its bids, settle what is due.

    python -m tools.flowbid.run [--now 2025-10-10T22:00:00]

Started every 5 minutes by Windows Task Scheduler through scripts/ops/run_flowbid.sh. --now replays a past instant on public bars for a rehearsal, with FLOWBID_ROOT outside the repo.
The ledger is append only. Every number in it is a price, a minute or a count: the return is computed once, at the read on READ_DATE, and the status file shows counts only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from tools.flowbid import config as cf
from tools.flowbid import rules
from tools.selection import fetch as fx
from tools.selection.config import write_text

STEP_MS = 60_000


def load(path: Path) -> list[dict]:
    """Ledger records. A last line cut by a kill is skipped; a bad line elsewhere is an error."""
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
            f.write(json.dumps(r, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


@dataclass
class State:
    """What the ledger says is done and what is open."""

    covered: int | None = None
    last_event: int | None = None
    dropped: set[str] = field(default_factory=set)
    open_bids: dict[str, dict] = field(default_factory=dict)       # bid id -> bid, no fill or cancel yet
    fills: dict[str, dict] = field(default_factory=dict)           # bid id -> fill
    exits: set[tuple[str, int]] = field(default_factory=set)       # (bid id, hold hours) done
    events: int = 0

    @classmethod
    def of(cls, recs: list[dict]) -> State:
        s = cls()
        for r in recs:
            k = r["kind"]
            if k == "run":
                if r.get("judged_to") is not None:
                    s.covered = max(s.covered or r["judged_to"], r["judged_to"])
                s.dropped |= set(r.get("gone", []))
            elif k == "event":
                s.last_event = r["flag_minute"]
                s.events += 1
            elif k == "bid":
                s.open_bids[r["id"]] = r
            elif k == "fill":
                s.open_bids.pop(r["id"], None)
                s.fills[r["id"]] = r
            elif k == "cancel":
                s.open_bids.pop(r["id"], None)
            elif k == "exit":
                s.exits.add((r["id"], r["hold_h"]))
        return s


def iso(m: int | None) -> str | None:
    return None if m is None else datetime.fromtimestamp(m * 60, UTC).strftime("%Y-%m-%dT%H:%MZ")


def fetch_bars(client: fx.Client, coin: str, g0: int, g1: int) -> rules.Bars:
    rows = [r for page in fx.pages(client, coin + fx.QUOTE, "1m", STEP_MS, g0 * STEP_MS, (g1 + 1) * STEP_MS) for r in page]
    return rules.dense(rows, g0, g1)


def fetch_many(client: fx.Client, coins: list[str], g0: int, g1: int) -> list[tuple[str, rules.Bars | fx.FetchError]]:
    """fetch_bars for many coins on FETCH_WORKERS threads, in the order given. Most of a call is waiting on the network.
    After the exchange says stop, coins not yet started are not asked and carry that stop."""
    stop: list[fx.FetchStop] = []

    def one(coin: str) -> rules.Bars | fx.FetchError:
        if stop:
            return stop[0]
        try:
            return fetch_bars(client, coin, g0, g1)
        except fx.FetchStop as e:
            stop.append(e)
            return e
        except fx.FetchError as e:
            return e

    with ThreadPoolExecutor(cf.FETCH_WORKERS) as ex:
        return list(zip(coins, ex.map(one, coins)))


def judge(client: fx.Client, st: State, m_now: int, live: list[str], log) -> tuple[list[dict], dict[str, rules.Bars]]:
    """Judge minutes since the last covered one. Returns the run record, any event and bid records, and the bars read."""
    m0 = m_now - cf.LOOKBACK_MIN + 1 if st.covered is None else max(st.covered + 1, m_now - cf.LOOKBACK_MIN + 1)
    run: dict = {"kind": "run", "m_now": m_now, "from": m0, "missed": None, "judged_to": None, "fetched": 0, "failed": [], "gone": [], "stop": None}
    if st.covered is not None and m0 > st.covered + 1:
        run["missed"] = [st.covered + 1, m0 - 1]
    if m0 > m_now:
        return [run], {}
    g0 = m0 - cf.LEAD_MIN
    bars: dict[str, rules.Bars] = {}
    for coin, got in fetch_many(client, live, g0, m_now):
        if isinstance(got, rules.Bars):
            bars[coin] = got
        elif isinstance(got, fx.UnknownSymbol):
            run["gone"].append(coin)
        elif isinstance(got, fx.FetchStop):
            run["stop"] = str(got)
        else:
            run["failed"].append(coin)
    run["fetched"] = len(bars)
    still = len(live) - len(run["gone"])
    if run["stop"] or len(bars) < cf.MIN_FETCHED_SHARE * still:
        log(f"not judged: fetched {len(bars)} of {still}, stop {run['stop']}")
        return [run], bars
    down, den = rules.counts(bars, m0, m_now)
    hit = rules.flagged(down, den)
    run["judged_to"] = m_now
    run["max_share"] = round(float(np.max(np.where(den > 0, down / np.maximum(den, 1), 0))), 4)
    run["min_den"] = int(den.min())
    out = [run]
    for m in rules.new_events(m0 + np.nonzero(hit)[0], st.last_event):
        i = m - m0
        out += place(client, m, m_now, int(down[i]), int(den[i]), bars, log)
        st.last_event = m
    return out, bars


def place(client: fx.Client, m: int, m_now: int, down: int, den: int, bars: dict[str, rules.Bars], log) -> list[dict]:
    """The event and its bids. Bids go live LATENCY_MIN after the later of the flag minute and the run's last closed minute."""
    d0 = rules.day_start(m)
    live_from = max(m, m_now) + cf.LATENCY_MIN
    ev: dict = {"kind": "event", "id": f"e{m}", "flag_minute": m, "flag_at": iso(m), "detected_minute": m_now, "down": down, "den": den,
          "live_from": live_from, "delay_min": live_from - m, "bids": 0, "already_through": 0, "no_reference": 0, "failed": 0}
    recs = []
    refs: dict[str, rules.Bars | fx.FetchError] = {c: b for c, b in bars.items() if b.last_traded(d0 - 1) is not None}
    refs.update(fetch_many(client, [c for c in bars if c not in refs], d0 - 1000, d0 - 1))
    for coin, b in bars.items():
        ref_bars = refs[coin]
        if not isinstance(ref_bars, rules.Bars):
            ev["failed"] += 1
            continue
        lt = b.last_traded(m_now)
        last = None if lt is None else b.close_at(lt)
        for k in cf.K_LEVELS:
            why, t = rules.bid_terms(ref_bars, m, last, k)
            if why != "bid":
                ev[why] += 1
                continue
            ev["bids"] += 1
            recs.append({"kind": "bid", "id": f"e{m}-{coin}-{round(k * 100)}", "event": ev["id"], "coin": coin, **t,
                         "live_from": live_from, "live_to": live_from + cf.REST_MIN})
    log(f"EVENT {ev['flag_at']}: {down} of {den} coins down, {ev['bids']} bids live from {iso(live_from)}")
    return [ev] + recs


def settle(client: fx.Client, st: State, m_now: int, log) -> list[dict]:
    """Fills, cancels and exits that are due, inside the run's call budget. What is left waits for the next run."""
    out: list[dict] = []
    start = client.calls

    def budget() -> bool:
        return client.calls - start < cf.SETTLE_CALLS

    due: dict[tuple, list[tuple[str, dict]]] = {}
    for bid_id, b in sorted(st.open_bids.items()):
        if b["live_to"] <= m_now:
            due.setdefault((b["coin"], b["live_from"], b["live_to"]), []).append((bid_id, b))
    for (coin, a, z), group in sorted(due.items()):     # one fetch serves every depth of a coin in an event
        if not budget():
            break
        try:
            bars = fetch_bars(client, coin, a, z - 1)
        except fx.FetchError:
            continue
        for bid_id, b in group:
            f = rules.fill_minute(bars, b["bid"], a, z)
            if f is None:
                out.append({"kind": "cancel", "id": bid_id})
            else:
                out.append({"kind": "fill", "id": bid_id, "coin": coin, "k": b["k"], "minute": f, "price": b["bid"], "low": float(bars.low[f - bars.g0])})
                st.fills[bid_id] = out[-1]
    for bid_id, fill in sorted(st.fills.items()):
        for h in cf.HOLDS_H:
            stamp = fill["minute"] + h * 60
            if (bid_id, h) in st.exits or stamp > m_now or not budget():
                continue
            try:
                bars = fetch_bars(client, fill["coin"], stamp - cf.GONE_MIN, stamp)
            except fx.FetchError:
                continue
            out.append({"kind": "exit", "id": bid_id, "hold_h": h, "stamp": stamp, **rules.exit_terms(bars, stamp)})
    if out:
        log(f"settled {len(out)} records")
    return out


def status(r: Path, recs: list[dict], now_s: int) -> dict:
    """Counts only. No price, no return."""
    st = State.of(recs)
    runs = [x for x in recs if x["kind"] == "run"]
    day = [x for x in runs if x["m_now"] >= now_s // 60 - 1440]
    events = [x for x in recs if x["kind"] == "event"]
    return {
        "as_of": iso(now_s // 60),
        "runs": len(runs),
        "runs_24h": len(day),
        "judged_24h": sum(1 for x in day if x.get("judged_to") is not None),
        "covered_to": iso(st.covered),
        "missed_spans": sum(1 for x in runs if x.get("missed")),
        "events": len(events),
        "last_event": events[-1]["flag_at"] if events else None,
        "last_event_delay_min": events[-1]["delay_min"] if events else None,
        "bids": sum(1 for x in recs if x["kind"] == "bid"),
        "open_bids": len(st.open_bids),
        "fills": len(st.fills),
        "exits_done": len(st.exits),
        "dropped": sorted(st.dropped),
        "read_date": cf.READ_DATE,
    }


def run(now_s: int, client: fx.Client, r: Path, log=print) -> dict:
    path = cf.ledger_path(r)
    recs = load(path)
    st = State.of(recs)
    m_now = now_s // 60 - 1
    live = [c for c in cf.UNIVERSE if c not in st.dropped]
    t0 = time.monotonic()
    new, _ = judge(client, st, m_now, live, log)
    if not new[0]["stop"]:                      # the exchange said stop: settle waits for the next run
        new += settle(client, st, m_now, log)
    new[0]["at"] = iso(now_s // 60)
    new[0]["calls"] = client.calls
    new[0]["seconds"] = round(time.monotonic() - t0, 1)
    append(path, new)
    s = status(r, recs + new, now_s)
    write_text(cf.status_path(r), json.dumps(s, indent=1, sort_keys=True))
    return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--now", help="replay instant, UTC ISO, for a rehearsal outside the repo")
    a = p.parse_args(argv)
    r = cf.root()
    if a.now and r.is_relative_to(cf.REPO):
        print("--now writes a replay: set FLOWBID_ROOT outside the repo", file=sys.stderr)
        return 2
    now_s = int(datetime.fromisoformat(a.now).replace(tzinfo=UTC).timestamp()) if a.now else int(time.time())
    s = run(now_s, fx.Client(), r, log=lambda x: print(x, flush=True))
    print(json.dumps({k: s[k] for k in ("as_of", "covered_to", "events", "open_bids", "fills")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
