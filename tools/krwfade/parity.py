"""Parity of book KRW's rule and shadow with the H-KRW-FADE-16 packet: a script on local data, not a CI test.

    PYTHONPATH=src:. OMP_NUM_THREADS=4 nice -n 19 .venv/bin/python -m tools.krwfade.parity [--data data]

Reads only files already on disk (git-ignored ``data/``); no network.

1. **Rules.** Every notice of ``notice_feasibility_20261108/raw/upbit_trade/*.json`` goes through ``rules.krw_tokens``,
   the 7-day merge (``rules.first_within``) and ``rules.verdict`` against ``tradfi_20261103/dl/exinfo.json``, over the
   research window (notice from 2022-01-01, E + 72 h before the 2026-11-02 seal). The result is compared with the 109
   events of ``krw_fade_20261108_01/events.json`` on (token, perp, notice_ms, E), and every difference is listed
   with its reason.
   - The snapshot is of 2026-11-03, after the notices. A perp settled since then shows SETTLING, and the live rule
     (TRADING) would drop it although it traded at the notice. The replay therefore reads a perp as trading at the
     notice when it is TRADING, or SETTLING with its delivery after the notice (``trading_at``). The literal
     snapshot's extra losses are listed apart (``literal_snapshot_drops``).
2. **Shadow.** ``shadow.row`` runs on the stores ``run/main.py`` reads, merged as it merges them (nonlist, then
   stopfill, then perp_1h; funding from the nonlist fundingRate store, then perp_funding_20261102), with the same
   seal: no file dated from 2026-11 on is opened and no row at or after 2026-11-02 00:00 UTC is kept. For every event
   of events.json it compares 0a with ``run/events_out.csv``, and for every event passing 0a it compares ``g_bps``
   and ``n_bps`` to 1e-6 (the CSV prints 10 significant digits: at most 5e-7 off below 10,000 bps).
3. **Output:** counts and keys only, no mean and no return.
4. **Exit status** (``exit_code``): 0 only when both halves agree as recorded on 2026-11-08. The rule half: 109
   research events, 109 replayed, 106 equal, and exactly the four known differences (``EXPECTED_DIFFS``). The shadow
   half: every event agrees on 0a, and every event passing it agrees on g_bps and n_bps to 1e-6, on the stop, on the
   funding row counts and on the flags. ``--rules-only`` judges the rule half alone. Anything else returns 1.

The loaders below are copied from ``run/main.py`` (sha256 a655674daf2d2e79...), not imported: importing it would
append to its log. Research description, not advice.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import io
import json
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UTC = dt.timezone.utc
H = 3_600_000
SEAL = int(dt.datetime(2026, 11, 2, tzinfo=UTC).timestamp() * 1000)
START = int(dt.datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)
TOL_BPS = 1e-6
RESEARCH_EVENTS, SAME_EVENTS = 109, 106
# The rule's known differences from the research events (the doc lists their reasons): (kind, token, day, rule perp).
EXPECTED_DIFFS = frozenset({("extra", "BIGTIME", "2024-04-23", "BIGTIMEUSDT"), ("missing", "GAL", "2024-07-11", None),
                            ("perp_differs", "RAY", "2025-06-19", "RAYSOLUSDT"),
                            ("perp_differs", "AI", "2026-06-30", "AIGENSYNUSDT")})


def day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


# ── 1. rules ────────────────────────────────────────────────────────────────────

def trading_at(notice_ms: int):
    """A perp traded at the notice: TRADING in the snapshot, or SETTLING with its delivery after the notice."""

    def ok(s) -> bool:
        if s.get("status") == "TRADING":
            return True
        return s.get("status") == "SETTLING" and int(s.get("deliveryDate") or 0) > notice_ms

    return ok


def notices(data: Path):
    from tools.krwfade import feed as F

    out = {}
    for p in sorted(glob.glob(str(data / "notice_feasibility_20261108" / "raw" / "upbit_trade" / "*.json"))):
        for n in F.parse(Path(p).read_bytes()):
            out[n.id] = n
    return sorted(out.values(), key=lambda n: (n.first_listed_at_ms, n.id))


def replay(data: Path, literal: bool = False) -> tuple[list[dict], dict[str, int]]:
    """The events the rule finds in the research window, and the count of each reason a token was not one."""

    from tools.krwfade import rules as R

    symbols = R.symbol_map(json.loads((data / "tradfi_20261103" / "dl" / "exinfo.json").read_text(encoding="utf8")))
    earlier: dict[str, list[dict]] = collections.defaultdict(list)
    later: dict[str, list[int]] = collections.defaultdict(list)
    ns = notices(data)
    for n in ns:
        for tk in R.krw_tokens(n.title):
            later[tk.token].append(n.first_listed_at_ms)
    found, why = [], collections.Counter()
    for n in ns:
        t = n.first_listed_at_ms
        for tk in [x for x in R.classify(n.title) or [] if x.krw]:     # as krwfade._read_feed reads a notice
            first = R.first_within(t, R.chain(t, tk.ambiguous, earlier[tk.token]))
            earlier[tk.token].append({"t": t, "amb": tk.ambiguous, "cancelled": tk.cancelled})
            if tk.cancelled:
                continue
            if not first:
                why["merged_within_7d"] += t >= START
                continue
            E = R.entry_ms(t)
            if t < START or E + 72 * H >= SEAL:
                continue
            v = R.verdict(tk.token, t, symbols, R.trading_now if literal else trading_at(t))
            if not v.ok:
                why[v.reason] += 1
                continue
            found.append({"token": tk.token, "perp": v.perp, "notice_ms": t, "entry_ms": E, "notice_id": n.id,
                          "ambiguous": tk.ambiguous,
                          "own_krw_notice_later": any(x > t + R.DAY_MS for x in later[tk.token])})
    return found, dict(why)


def rules_parity(data: Path) -> dict:
    from tools.krwfade import rules as R

    ev = json.loads((data / "krw_fade_20261108_01" / "events.json").read_text(encoding="utf8"))
    want = {(e["token"], int(e["notice_ms"])): e for e in ev}
    found, why = replay(data)
    got = {(e["token"], e["notice_ms"]): e for e in found}
    symbols = R.symbol_map(json.loads((data / "tradfi_20261103" / "dl" / "exinfo.json").read_text(encoding="utf8")))
    same, diffs = 0, []
    for k in sorted(set(want) | set(got), key=lambda k: (k[1], k[0])):
        w, g = want.get(k), got.get(k)
        if w and g and (w["perp"], int(w["entry_ms"])) == (g["perp"], g["entry_ms"]):
            same += 1
            continue
        row = {"token": k[0], "notice_day": day(k[1]), "research_perp": w["perp"] if w else None,
               "rule_perp": g["perp"] if g else None}
        if w and g and int(w["entry_ms"]) != g["entry_ms"]:
            row.update(kind="entry_differs", reason="E differs")
        elif w and g:
            row["kind"] = "perp_differs"
            row["reason"] = (f"alias table: Upbit {k[0]} is {R.ALIAS[k[0]]}; research took {w['perp']}, a perp "
                             f"{symbols.get(w['perp'], {}).get('status', 'absent')} in the snapshot, which failed its 0a"
                             if k[0] in R.ALIAS else "perp resolution")
        elif w:
            v = R.verdict(k[0], k[1], symbols, trading_at(k[1]))
            row.update(kind="missing", rule_reason=v.reason)
            row["reason"] = ("the perp is absent from the 2026-11-03 exchangeInfo (delisted since); research took its "
                             "age from a store's first 1h bar" if w.get("age_source") != "exinfo" and v.reason == "no_perp"
                             else f"rule says {v.reason}")
        else:
            row.update(kind="extra", ambiguous=g["ambiguous"])
            row["reason"] = ("a shared market list names KRW; research ruled the token not KRW because it got a KRW "
                             "notice of its own later (reads the future; the rule cannot)"
                             if g["ambiguous"] and g["own_krw_notice_later"] else "rule finds an event research does not")
        diffs.append(row)
    lit, _ = replay(data, literal=True)
    lit_keys = {(e["token"], e["notice_ms"]) for e in lit}
    drops = sorted({(e["token"], day(e["notice_ms"]), e["perp"]) for e in found if (e["token"], e["notice_ms"]) not in lit_keys})
    return {"research_events": len(ev), "replay_events": len(found), "same_token_perp_notice_E": same,
            "differences": diffs,
            "by_kind": dict(collections.Counter(d["kind"] for d in diffs)),
            "replay_not_events_by_reason": why,
            "ambiguous_market_events": sum(1 for e in found if e["ambiguous"]),
            "literal_snapshot_drops": [{"token": t, "notice_day": d, "perp": p,
                                        "reason": "settled after the notice: SETTLING in the 2026-11-03 snapshot"}
                                       for t, d, p in drops]}


# ── 2. shadow on the research stores (loaders copied from run/main.py) ──────────

def _np():
    import numpy as np
    import pandas as pd

    return np, pd


def file_before_seal(path: str) -> bool:
    b = os.path.basename(path)
    if b.startswith("m-"):
        return b[2:9] < "2026-11"
    if b.startswith("d-"):
        return b[2:12] < "2026-11-02"
    return False


def parse_zip_csv(path: str, ncols: int):
    np, pd = _np()
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        raw = z.read(names[0])
    if not raw.strip():
        return np.empty((0, ncols))
    skip = 0 if raw[:1].isdigit() else 1
    try:
        a = pd.read_csv(io.BytesIO(raw), header=None, skiprows=skip, usecols=range(ncols), dtype=np.float64).to_numpy()
    except (ValueError, TypeError):
        x = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(ncols), dtype=str)
        x = x.apply(pd.to_numeric, errors="coerce")
        a = x[x[0].notna()].to_numpy(np.float64)
    return a


def us_to_ms(a, cols):
    np, _ = _np()
    for j in cols:
        big = a[:, j] > 1e14
        a[big, j] = np.floor(a[big, j] / 1000.0)
    return a


def store_map(root: Path, listing: Path) -> dict[str, str]:
    sid2sym: dict[int, str] = {}
    for e in json.loads(listing.read_text(encoding="utf8")):
        sid2sym.setdefault(int(e["sid"]), e["symbol"])
    out = {}
    for d in sorted(glob.glob(f"{root}/s*")):
        sym = sid2sym.get(int(os.path.basename(d)[1:]))
        if sym is None:
            continue
        st = f"{d}/symbol.txt"
        if os.path.exists(st) and open(st, encoding="utf8").read().strip() != sym:
            raise SystemExit(f"store map mismatch: {d} {sym}")
        out[sym] = d
    return out


def p1_map(p1: Path) -> dict[str, str]:
    out = {}
    for d in sorted(glob.glob(f"{p1}/s*")):
        out[open(f"{d}/symbol.txt", encoding="utf8").read().strip()] = d
    return out


class Stores:
    def __init__(self, data: Path):
        nl, sf = data / "nonlist_20261103_01", data / "stopfill_20261103_01"
        self.k1h = (store_map(nl / "dl" / "um" / "1h", nl / "dl" / "listing_1h.json"),
                    store_map(sf / "dl" / "um" / "1h", sf / "dl" / "listing_1h.json"),
                    p1_map(data / "perp_1h_20261031"))
        self.fr = store_map(nl / "dl" / "um" / "fundingRate", nl / "dl" / "listing_fundingRate.json")
        self.fq = data / "perp_funding_20261102"
        m = json.loads((self.fq / "symbols.json").read_text(encoding="utf8"))["map"]
        self.p2c = {perp: coin for coin, lst in m.items() for perp, _mult in lst}

    def bars(self, sym: str) -> dict:
        """main.load_bars: the three stores merged, nonlist first; open times on the hour; rows before the seal."""

        np, _ = _np()
        parts = []
        for rank, fmap in enumerate(self.k1h):
            d = fmap.get(sym)
            if not d:
                continue
            for f in sorted(glob.glob(f"{d}/m-*.zip")) + sorted(glob.glob(f"{d}/d-*.zip")):
                if not file_before_seal(f):
                    continue
                a = us_to_ms(parse_zip_csv(f, 8), (0, 6))
                a = a[a[:, 0] < SEAL]
                if len(a):
                    parts.append(np.c_[a, np.full(len(a), float(rank))])
        if not parts:
            return {}
        A = np.vstack(parts)
        A = A[np.lexsort((A[:, 8], A[:, 0]))]
        t = A[:, 0].astype(np.int64)
        first = np.r_[True, t[1:] != t[:-1]]
        A, t = A[first], t[first]
        keep = (t % H == 0) & np.isfinite(A[:, 1])          # main.Bars: a NaN open is a missing bar
        # main.py's X falls back to open + 1 h - 1 ms when a close time is missing
        return {int(tt): (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                          int(r[6]) if np.isfinite(r[6]) else int(tt) + H - 1, float(r[7]))
                for tt, r in zip(t[keep], A[keep])}

    def funding(self, sym: str) -> list[tuple[int, float]]:
        """main.load_funding: (time rounded to the second, rate), nonlist first, then the parquet; before the seal."""

        np, pd = _np()
        rows: list[tuple[int, float]] = []
        d = self.fr.get(sym)
        if d:
            for f in sorted(glob.glob(f"{d}/m-*.zip")):
                if not file_before_seal(f):
                    continue
                a = us_to_ms(parse_zip_csv(f, 3), (0,))
                a = a[a[:, 0] < SEAL]
                rows += [(int(np.round(x / 1000.0) * 1000.0), float(r)) for x, r in zip(a[:, 0], a[:, 2])]
        coin = self.p2c.get(sym, sym[:-4] if sym.endswith("USDT") else sym)
        q = self.fq / "parsed" / f"{coin}.parquet"
        if q.exists():
            x = pd.read_parquet(q)
            tt = x["t"].to_numpy(np.float64) * 1000.0
            keep = tt < SEAL
            rows += [(int(np.round(a / 1000.0) * 1000.0), float(r))
                     for a, r in zip(tt[keep], x["rate"].to_numpy(np.float64)[keep])]
        return rows


def shadow_parity(data: Path) -> dict:
    from tools.krwfade import shadow as S

    pkt = data / "krw_fade_20261108_01"
    ev = json.loads((pkt / "events.json").read_text(encoding="utf8"))
    with open(pkt / "run" / "events_out.csv", encoding="utf8", newline="") as h:
        out = {(r["token"], r["notice"]): r for r in csv.DictReader(h)}
    st = Stores(data)
    btc = st.bars(S.HEDGE)
    fbtc = S.funding_rows(st.funding(S.HEDGE))
    c = collections.Counter()
    bad: list[dict] = []
    for e in ev:
        r = out[(e["token"], e["notice"])]
        alt = st.bars(e["perp"])
        row = S.row(alt, btc, S.funding_rows(st.funding(e["perp"])), fbtc, int(e["entry_ms"]), int(e["notice_ms"]))
        want_0a = r["pass_0a"] == "True"
        c["events"] += 1
        c["pass_0a_agree" if row["pass_0a"] == want_0a else "pass_0a_disagree"] += 1
        if row["pass_0a"] != want_0a:
            bad.append({"token": e["token"], "notice_day": e["cluster_day"], "field": "pass_0a"})
            continue
        if not want_0a:
            continue
        c["compared"] += 1
        for f in ("g_bps", "n_bps"):
            ok = abs(row[f] - float(r[f])) <= TOL_BPS
            c[f"{f}_within_1e-6"] += ok
            c[f"{f}_same_10g"] += f"{row[f]:.10g}" == r[f]
            if not ok:
                bad.append({"token": e["token"], "notice_day": e["cluster_day"], "field": f})
        agree = {"stopped": row["stopped"] == (r["stopped"] == "True"),
                 "fund_rows": (row["fund_rows_alt"], row["fund_rows_btc"]) == (int(r["fund_rows_alt"]),
                                                                               int(r["fund_rows_btc"])),
                 "flags": sorted(row["flags"]) == sorted(x for x in r["flags"].split(";")
                                                         if x and x != "store_disagree_in_window")}
        for f, ok in agree.items():
            c[f"{f}_agree"] += ok
            if not ok:
                bad.append({"token": e["token"], "notice_day": e["cluster_day"], "field": f})
    return {"counts": dict(c), "mismatches": bad}


def diff_keys(rules: dict) -> set[tuple]:
    """The rule half's differences as (kind, token, day, rule perp)."""

    return {(d["kind"], d["token"], d["notice_day"], d.get("rule_perp")) for d in rules.get("differences") or []}


def exit_code(report: dict, rules_only: bool = False) -> int:
    """0 when the report shows the parity recorded on 2026-11-08, else 1 (module docstring, point 4)."""

    r = report.get("rules") or {}
    ok = (r.get("research_events") == RESEARCH_EVENTS and r.get("replay_events") == RESEARCH_EVENTS
          and r.get("same_token_perp_notice_E") == SAME_EVENTS and diff_keys(r) == set(EXPECTED_DIFFS))
    if rules_only:
        return 0 if ok else 1
    sh = report.get("shadow") or {}
    c = sh.get("counts") or {}
    ok = (ok and not sh.get("mismatches") and c.get("events") == RESEARCH_EVENTS and not c.get("pass_0a_disagree")
          and int(c.get("compared") or 0) > 0
          and all(c.get(k) == c["compared"] for k in ("g_bps_within_1e-6", "n_bps_within_1e-6", "stopped_agree",
                                                       "fund_rows_agree", "flags_agree")))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--rules-only", action="store_true")
    args = ap.parse_args(argv)
    data = Path(args.data)
    report = {"rules": rules_parity(data)}
    if not args.rules_only:
        report["shadow"] = shadow_parity(data)
    print(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True))
    return exit_code(report, args.rules_only)


if __name__ == "__main__":
    sys.exit(main())
