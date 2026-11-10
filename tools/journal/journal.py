"""The operator's trade journal: real trades recorded by hand, with a market snapshot and later outcomes.

The operator trades real money by hand, outside the project. This journal measures that judgement
forward: each trade is recorded when it is entered (typed, or entered from a screenshot), with the
reason, the size and the stops. At recording it takes a snapshot of the coin and BTC from public
1m bars. Later `score` adds what happened: the return at fixed horizons, the worst and best move,
which of SL, TP or the liquidation price was touched first. `report` prints them. `status`
evaluates every open trade now: price, P&L, distance to its levels, the coin against BTC, and how
often the coin touched the TP before the SL or liquidation level in its last 365 days of 1h bars.

- **No order, no key:** it only reads public market data through tools.newperp.fetch (one host, an
  allowlist of GET paths, no credential). It never reads .env or any account.
- **Seal:** a coin masked by the operator's ruling (tools/selection/config.SEALED, the H-XSEC-01
  cohort) is recorded without market data and is never scored while the seal holds.
- **Private:** the journal file lives outside git (``JOURNAL_DIR``, default
  ~/trading-journal-data). It holds sizes; nothing here prints them except ``report --sizes``.
- **Append-only:** every record carries the hash of the one before it; ``verify`` checks the chain.
  A record is never edited. A correction is a new record.

Rules and the reading plan: docs/operations/TRADE_JOURNAL.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from tools.newperp import fetch as fx
from tools.selection import config as sel

MIN_MS = 60_000
HOUR_MS = 60 * MIN_MS
HORIZONS = {"15m": 15, "1h": 60, "4h": 240, "24h": 1440, "72h": 4320}
PATH_MIN = 4320                    # the worst and best move are read over the first 72 h, or to the close
SNAP_1M_MIN = 2880                 # 1m bars before the trade: z over 1,440 min needs 1,440 more for its scale
SNAP_1H_H = 168                    # 1h bars before the trade: 7 days of returns, highs and lows
SIDES = ("long", "short")
HOWS = ("manual", "sl", "tp", "liq", "all", "tool", "other")


def journal_path() -> Path:
    return Path(os.environ.get("JOURNAL_DIR", str(Path.home() / "trading-journal-data"))) / "journal.jsonl"


# ── storage ───────────────────────────────────────────────────────────────

def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "sha"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def append(path: Path, rec: dict) -> dict:
    rows = read(path)
    rec = dict(rec, prev=rows[-1]["sha"] if rows else None, recorded_ms=int(time.time() * 1000))
    rec["sha"] = _sha(rec)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        path.touch(mode=0o600)             # it holds sizes: the owner only
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return rec


def verify(path: Path) -> list[str]:
    """Breaks in the hash chain, or [] when it is whole."""

    bad, prev = [], None
    for i, r in enumerate(read(path)):
        if r.get("prev") != prev or r.get("sha") != _sha(r):
            bad.append(f"line {i + 1} ({r.get('id')})")
        prev = r.get("sha")
    return bad


def trades(rows: list[dict]) -> dict[str, dict]:
    """Each trade by id: its open record, its close record if any, and its latest score."""

    out: dict[str, dict] = {}
    for r in rows:
        if r["kind"] == "open":
            out[r["id"]] = {"open": r, "close": None, "score": None}
        elif r["id"] in out:
            out[r["id"]][r["kind"]] = r
    return out


# ── market data ───────────────────────────────────────────────────────────

def sealed(symbol: str) -> bool:
    return symbol != "BTCUSDT" and sel.is_sealed(symbol)


def _bars(client, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[tuple[int, float, float, float, float]]:
    rows = fx.klines(client, symbol, interval, start_ms, end_ms)
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows]


def _pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 3)


def snapshot(client, symbol: str, at_ms: int, entry: float) -> dict:
    """The coin and BTC just before ``at_ms``, from public bars that closed before it. Percent units."""

    if sealed(symbol):
        return {"sealed": True}
    start = at_ms // MIN_MS * MIN_MS - SNAP_1M_MIN * MIN_MS
    c1 = _bars(client, symbol, "1m", start, at_ms // MIN_MS * MIN_MS)
    b1 = _bars(client, "BTCUSDT", "1m", start, at_ms // MIN_MS * MIN_MS)
    hs = at_ms // HOUR_MS * HOUR_MS - SNAP_1H_H * HOUR_MS
    ch = _bars(client, symbol, "1h", hs, at_ms // HOUR_MS * HOUR_MS)
    bh = _bars(client, "BTCUSDT", "1h", hs, at_ms // HOUR_MS * HOUR_MS)
    bmap = {t: c for t, _, _, _, c in b1}
    pairs = [(c, bmap[t]) for t, _, _, _, c in c1 if t in bmap]
    if len(pairs) < SNAP_1M_MIN // 2 or len(ch) < SNAP_1H_H // 2:
        return {"error": "not_enough_bars", "bars_1m": len(pairs), "bars_1h": len(ch)}
    lc = [math.log(c) for c, _ in pairs]
    lb = [math.log(b) for _, b in pairs]
    rc = [lc[i] - lc[i - 1] for i in range(1, len(lc))]
    rb = [lb[i] - lb[i - 1] for i in range(1, len(lb))]
    w = rc[-1440:], rb[-1440:]
    mc, mb = sum(w[0]) / len(w[0]), sum(w[1]) / len(w[1])
    var_b = sum((x - mb) ** 2 for x in w[1]) / len(w[1])
    cov = sum((x - mc) * (y - mb) for x, y in zip(*w)) / len(w[0])
    beta = max(0.0, cov / var_b) if var_b > 0 else 0.0
    res = [x - beta * y for x, y in zip(rc, rb)]
    rw = res[-1440:]
    mr = sum(rw) / len(rw)
    sd = math.sqrt(sum((x - mr) ** 2 for x in rw) / len(rw))
    last = pairs[-1][0]
    snap = {
        "last": last, "entry_vs_last": _pct(entry, last), "beta_1d": round(beta, 3),
        "vol_1d": round(math.sqrt(sum((x - mc) ** 2 for x in w[0]) / len(w[0])) * math.sqrt(1440) * 100, 3),
    }
    for name, n in (("15m", 15), ("1h", 60), ("4h", 240), ("1d", 1440)):
        snap[f"ret_{name}"] = round((math.exp(lc[-1] - lc[-1 - n]) - 1) * 100, 3)
        snap[f"btc_{name}"] = round((math.exp(lb[-1] - lb[-1 - n]) - 1) * 100, 3)
    for name, n in (("4h", 240), ("1d", 1440)):
        snap[f"resid_{name}"] = round(sum(res[-n:]) * 100, 3)
        snap[f"z_{n}"] = round(sum(res[-n:]) / (sd * math.sqrt(n)), 2) if sd > 0 else None
    c7, b7 = ch[0][1], bh[0][1]
    snap["ret_7d"] = _pct(ch[-1][4], c7)
    snap["btc_7d"] = _pct(bh[-1][4], b7) if bh else None
    if snap["btc_7d"] is not None:
        snap["resid_7d"] = round(math.log(ch[-1][4] / c7) * 100 - beta * math.log(bh[-1][4] / b7) * 100, 3)
    snap["dist_high_7d"] = _pct(last, max(h for _, _, h, _, _ in ch))
    snap["dist_low_7d"] = _pct(last, min(lo for _, _, _, lo, _ in ch))
    snap["dist_high_1d"] = _pct(last, max(h for _, _, h, _, _ in ch[-24:]))
    try:
        fr = fx.funding(client, symbol, at_ms - 7 * 24 * HOUR_MS, at_ms)
        snap["funding_last"] = round(fr[-1][1] * 1e4, 3) if fr else None     # bps
        snap["funding_7d"] = round(sum(r for _, r in fr) * 1e4, 3) if fr else None
    except Exception:
        snap["funding_last"] = snap["funding_7d"] = None
    return snap


def outcome(client, o: dict, close: dict | None, now_ms: int) -> dict:
    """Returns at each horizon that has passed, the worst and best move, and the first level touched."""

    side = 1 if o["side"] == "long" else -1
    at, entry = o["at_ms"], o["entry"]
    end = min(at // MIN_MS * MIN_MS + (PATH_MIN + 1) * MIN_MS, now_ms // MIN_MS * MIN_MS)   # through the 72 h close
    if close is not None:
        end = min(end, close["at_ms"] // MIN_MS * MIN_MS + MIN_MS)
    start = at // MIN_MS * MIN_MS
    bars = _bars(client, o["symbol"], "1m", start, max(end, start + MIN_MS)) if end > start else []
    out: dict = {"bars": len(bars), "to_ms": bars[-1][0] + MIN_MS if bars else start}
    closes = {t: c for t, _, _, _, c in bars}
    for name, n in HORIZONS.items():
        t = start + n * MIN_MS
        if t + MIN_MS <= now_ms and t in closes:
            out[f"ret_{name}"] = round(side * (closes[t] / entry - 1) * 1e4, 1)      # bps, before costs
    if bars:
        worst = max((side * -1) * (lo / entry - 1) if side > 0 else (h / entry - 1) for _, _, h, lo, _ in bars)
        best = max((h / entry - 1) if side > 0 else -(lo / entry - 1) for _, _, h, lo, _ in bars)
        out["mae_bps"] = round(worst * 1e4, 1)
        out["mfe_bps"] = round(best * 1e4, 1)
        levels = {k: o.get(k) for k in ("sl", "tp", "liq") if o.get(k)}
        first = None
        for t, _, h, lo, _ in bars:
            hit = []
            for k, p in levels.items():
                adverse = k in ("sl", "liq")
                up = (side < 0) == adverse          # a short's SL and liq are above; its TP below
                if (up and h >= p) or (not up and lo <= p):
                    hit.append(k)
            if hit:
                first = {"level": "+".join(sorted(hit)), "after_min": (t - start) // MIN_MS}
                break
        out["first_touch"] = first
    if close is not None:
        out["realized_bps"] = round(side * (close["price"] / entry - 1) * 1e4, 1)
        out["held_min"] = (close["at_ms"] - at) // MIN_MS
    return out


# ── commands ──────────────────────────────────────────────────────────────

def parse_time(s: str | None) -> int:
    if not s:
        return int(time.time() * 1000)
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return int(t.timestamp() * 1000)


def norm_symbol(s: str) -> str:
    s = s.upper()
    return s if s.endswith("USDT") else s + "USDT"


def cmd_add(a, path: Path, client) -> dict:
    sym = norm_symbol(a.symbol)
    at = parse_time(a.time)
    rows = read(path)
    n = sum(1 for r in rows if r["kind"] == "open") + 1
    rec = {"kind": "open", "id": f"j{n:04d}", "at_ms": at, "symbol": sym, "side": a.side, "entry": a.entry,
           "notional": a.notional, "lev_setting": a.lev, "margin_balance": a.margin_balance,
           "sl": a.sl, "tp": a.tp, "liq": a.liq, "reason": a.reason or "", "tags": sorted(set(a.tags or [])),
           "book": a.book, "source": a.source}
    if a.entry <= 0 or (a.notional is not None and a.notional <= 0):
        raise SystemExit("entry and notional must be positive")
    lv = {}
    if a.notional and a.margin_balance:
        lv["eff_leverage"] = round(a.notional / a.margin_balance, 2)
    for k in ("sl", "tp", "liq"):
        if getattr(a, k):
            lv[f"{k}_dist_pct"] = _pct(getattr(a, k), a.entry)
    rec["levels"] = lv
    try:
        rec["snapshot"] = snapshot(client, sym, at, a.entry)
    except fx.sf.FetchStop as e:
        rec["snapshot"] = {"error": f"fetch_stop {e}"}
    except Exception as e:                 # the trade is recorded even when the market read fails
        rec["snapshot"] = {"error": type(e).__name__}
    return append(path, rec)


def cmd_close(a, path: Path, client) -> dict:
    tr = trades(read(path))
    if a.id not in tr:
        raise SystemExit(f"no trade {a.id}")
    if tr[a.id]["close"] is not None:
        raise SystemExit(f"{a.id} is already closed; record a correction with `note`")
    return append(path, {"kind": "close", "id": a.id, "at_ms": parse_time(a.time), "price": a.price, "how": a.how,
                         "note": a.note or ""})


def cmd_note(a, path: Path, client) -> dict:
    if a.id not in trades(read(path)):
        raise SystemExit(f"no trade {a.id}")
    return append(path, {"kind": "note", "id": a.id, "text": a.text})


def cmd_score(a, path: Path, client, now_ms: int | None = None) -> list[str]:
    now = now_ms or int(time.time() * 1000)
    done = []
    for tid, t in trades(read(path)).items():
        o = t["open"]
        if o.get("snapshot", {}).get("sealed"):
            continue
        prev = t["score"]
        final_at = o["at_ms"] + (PATH_MIN + 1) * MIN_MS
        if prev and prev.get("final"):
            continue
        out = outcome(client, o, t["close"], now)
        out["final"] = now >= final_at or (t["close"] is not None and now >= t["close"]["at_ms"] + MIN_MS
                                           and all(f"ret_{h}" in out or o["at_ms"] + n * MIN_MS > t["close"]["at_ms"]
                                                   for h, n in HORIZONS.items()))
        append(path, {"kind": "score", "id": tid, "as_of_ms": now, **out})
        done.append(tid)
    return done


RACE_H = (24, 72, 168)
RACE_DAYS = 365


def race(bars: list, up: float, down: float, hours: int) -> dict:
    """From every past hour close, which of +up% / -down% the coin touched first within ``hours`` (1h bars)."""

    n = {"up": 0, "down": 0, "both": 0, "none": 0}
    for i in range(len(bars) - hours):
        p = bars[i][4]
        hit = None
        for _, _, h, lo, _ in bars[i + 1:i + 1 + hours]:
            u, d = h >= p * (1 + up / 100), lo <= p * (1 - down / 100)
            if u or d:
                hit = "both" if u and d else ("up" if u else "down")
                break
        n[hit or "none"] += 1
    tot = sum(n.values())
    return {k: round(100 * v / tot, 1) for k, v in n.items()} | {"starts": tot} if tot else {}


def cmd_status(a, path: Path, client, now_ms: int | None = None) -> str:
    """Each open trade now: price, P&L, distance to its levels, the coin against BTC, and past odds of the levels."""

    now = now_ms or int(time.time() * 1000)
    lines = []
    for tid, t in trades(read(path)).items():
        o = t["open"]
        if t["close"] is not None:
            continue
        side = 1 if o["side"] == "long" else -1
        head = f"{tid} {o['symbol']} {o['side']} entry {o['entry']}"
        if sealed(o["symbol"]):
            lines.append(head + ": sealed coin, no market data")
            continue
        snap = snapshot(client, o["symbol"], now, o["entry"])
        if "error" in snap:
            lines.append(head + f": {snap['error']}")
            continue
        px = snap["last"]
        lines.append(head + f" | now {px} ({iso(now)}) | P&L {round(side * (px / o['entry'] - 1) * 1e4, 1)} bps before costs")
        dist = {k: _pct(o[k], px) for k in ("sl", "tp", "liq") if o.get(k)}
        if dist:
            lines.append("   distance from now: " + ", ".join(f"{k} {v:+}%" for k, v in dist.items()))
        lines.append(f"   coin vs BTC now: 1d {snap['ret_1d']}% vs {snap['btc_1d']}%, residual 1d {snap['resid_1d']}% "
                     f"z1440 {snap['z_1440']} z240 {snap['z_240']} | vol 1d {snap['vol_1d']}% | funding {snap['funding_last']} bps")
        adverse = [abs(v) for k, v in dist.items() if k in ("sl", "liq")]
        favour = abs(dist["tp"]) if "tp" in dist else None
        if adverse and favour:
            hb = _bars(client, o["symbol"], "1h", now // HOUR_MS * HOUR_MS - RACE_DAYS * 24 * HOUR_MS, now // HOUR_MS * HOUR_MS)
            near = min(adverse)
            for h in RACE_H:
                r = race(hb, near if side < 0 else favour, favour if side < 0 else near, h)
                if r:
                    tp_first = r["down"] if side < 0 else r["up"]
                    stop_first = r["up"] if side < 0 else r["down"]
                    lines.append(f"   past {RACE_DAYS} d, within {h} h: TP first {tp_first}%, SL/liq first {stop_first}%, "
                                 f"neither {r['none']}%, same hour {r['both']}% ({r['starts']} starts)")
        lines.append("   description only, not advice")
    return "\n".join(lines) or "no open trade"


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%MZ")


def cmd_report(a, path: Path, client) -> str:
    lines = []
    tr = trades(read(path))
    for tid, t in tr.items():
        o, s, c = t["open"], t["score"] or {}, t["close"]
        sn = o.get("snapshot", {})
        size = f" notional {o['notional']:.0f} lev_eff {o['levels'].get('eff_leverage')}" if a.sizes and o.get("notional") else \
            f" lev_eff {o['levels'].get('eff_leverage')}"
        lines.append(f"{tid} {iso(o['at_ms'])} {o['symbol']} {o['side']} entry {o['entry']}{size} tags {','.join(o['tags']) or '-'}")
        if sn.get("sealed"):
            lines.append("   sealed coin: no market data until the H-XSEC-01 seal ends")
        elif "error" in sn:
            lines.append(f"   snapshot failed: {sn['error']}")
        else:
            lines.append(f"   before: coin 1d {sn['ret_1d']}% 7d {sn['ret_7d']}% | BTC 1d {sn['btc_1d']}% 7d {sn['btc_7d']}% | "
                         f"vs BTC (beta {sn['beta_1d']}) 1d {sn['resid_1d']}% 7d {sn.get('resid_7d')}% z1440 {sn['z_1440']} | "
                         f"vol 1d {sn['vol_1d']}% | funding {sn['funding_last']} bps")
        lv = o.get("levels", {})
        if lv:
            lines.append("   levels from entry: " + ", ".join(f"{k} {v}%" for k, v in lv.items() if k != "eff_leverage"))
        if s:
            rets = " ".join(f"{h} {s[f'ret_{h}']}" for h in HORIZONS if f"ret_{h}" in s)
            lines.append(f"   after (bps, before costs): {rets or '-'} | MAE {s.get('mae_bps')} MFE {s.get('mfe_bps')} | "
                         f"first {s.get('first_touch')}" + (" | final" if s.get("final") else ""))
        if c:
            lines.append(f"   closed {iso(c['at_ms'])} at {c['price']} by {c['how']}, {s.get('realized_bps', '?')} bps")
        if o.get("reason"):
            lines.append(f"   reason: {o['reason']}")
    scored = [t for t in tr.values() if t["score"] and "ret_24h" in t["score"]]
    if scored:
        m = sum(t["score"]["ret_24h"] for t in scored) / len(scored)
        lines.append(f"\n{len(tr)} trades, {len(scored)} with a 24 h outcome, mean 24 h {m:.1f} bps before costs. "
                     "Description only; one reading is planned (docs/operations/TRADE_JOURNAL.md).")
    else:
        lines.append(f"\n{len(tr)} trades, none with a 24 h outcome yet.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="journal", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="record a trade at entry")
    p.add_argument("symbol")
    p.add_argument("side", choices=SIDES)
    p.add_argument("--entry", type=float, required=True, help="average entry price")
    p.add_argument("--time", help="entry time, ISO; UTC unless an offset is given (default now)")
    p.add_argument("--notional", type=float, help="position notional in USDT")
    p.add_argument("--lev", type=float, help="the leverage setting on the row")
    p.add_argument("--margin-balance", type=float, help="account margin balance, for effective leverage")
    p.add_argument("--sl", type=float)
    p.add_argument("--tp", type=float)
    p.add_argument("--liq", type=float, help="liquidation price shown by the venue")
    p.add_argument("--reason", help="why, in your words")
    p.add_argument("--tags", nargs="*", help="e.g. btc_down alt_lag news feel")
    p.add_argument("--book", help="an id shared by trades opened together")
    p.add_argument("--source", default="typed", choices=("typed", "screenshot", "csv"))
    p = sub.add_parser("close", help="record the exit")
    p.add_argument("id")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--time")
    p.add_argument("--how", choices=HOWS, default="manual")
    p.add_argument("--note")
    p = sub.add_parser("note", help="add a note to a trade")
    p.add_argument("id")
    p.add_argument("text")
    sub.add_parser("score", help="add outcomes for horizons that have passed")
    sub.add_parser("status", help="evaluate every open trade now from public bars")
    p = sub.add_parser("report", help="print every trade with its snapshot and outcomes")
    p.add_argument("--sizes", action="store_true", help="also print notional")
    sub.add_parser("verify", help="check the hash chain")
    return ap


def main(argv: list[str] | None = None, client=None) -> int:
    a = build_parser().parse_args(argv)
    path = journal_path()
    if a.cmd == "verify":
        bad = verify(path)
        print("ok" if not bad else "broken: " + ", ".join(bad))
        return 0 if not bad else 1
    client = client or fx.Client()
    if a.cmd == "add":
        r = cmd_add(a, path, client)
        print(f"recorded {r['id']}" + (" (sealed coin: no market data)" if r["snapshot"].get("sealed") else ""))
    elif a.cmd == "close":
        print(f"closed {cmd_close(a, path, client)['id']}")
    elif a.cmd == "note":
        print(f"noted {cmd_note(a, path, client)['id']}")
    elif a.cmd == "score":
        print("scored " + (", ".join(cmd_score(a, path, client)) or "nothing new"))
    elif a.cmd == "status":
        print(cmd_status(a, path, client))
    elif a.cmd == "report":
        print(cmd_report(a, path, client))
    return 0


if __name__ == "__main__":
    sys.exit(main())
