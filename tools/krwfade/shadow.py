"""The research trade of H-KRW-FADE-16 on one event, as a shadow row: exactly ``run/main.py``'s arithmetic.

``data/krw_fade_20261108_01/run/main.py`` (sha256 a655674daf2d2e79...), PROTOCOL.md "Trade, per event":

- **Check 0a** (``leg0a``): the alt perp and BTCUSDT each have a bar with nonzero base volume at the entry bar E and
  at the exit bar E + 72 h, and no hole between present bars longer than 2 h. A failing event gets no return: its
  row is ``pass_0a`` false with the reasons, as the research left such events out of every statistic.
- **Entry:** the 1h opens of the alt and of BTCUSDT at E.
- **Stop:** the first bar opening E .. E + 71 h whose high is at least 1.5x the alt entry (a missing bar cannot
  trigger it). The short closes at max(1.575 x entry, 1.005 x that bar's open); BTC closes at the close of its bar
  with the same open time (if that BTC bar is missing: the next BTC open, flagged ``btc_close_fallback``); the exit
  time X is that alt bar's close time.
- **No stop:** both legs close at the 1h open at E + 72 h, and X = E + 72 h.
- **Gross** g = ((alt entry - alt exit) / alt entry + (BTC exit - BTC entry) / BTC entry) x 1e4 bps.
- **Net** n = g - 74 - 11 + alt funding received - BTC funding paid, in bps of one unit a leg. Funding sums the
  settlement rates with E < t <= X, t rounded to the second; a leg without a settlement there gets 0 and a flag.
- **Check 0b**, recorded as a flag only (``0b_fail``): the alt's quote volume in the notice hour or the next is at
  least 3x the median of the 24 hours before the notice hour.

``row`` is pure: bars and funding in, the row out. ``fetch_row`` reads them through the NL-P allowlisted public
client (``tools/newperp/fetch.py``: /fapi/v1/klines and /fapi/v1/fundingRate on fapi.binance.com, no key; a masked
coin is refused before a byte leaves). ``parity.py`` gives ``row`` the research stores instead.

The row holds prices and returns: it is the one place they live. Nothing reads them back into a summary before the
book's K1 look (docs/operations/PERP_DEMO_RUNNER.md, "Book KRW"). Research description, not advice.
"""
from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

HOUR_MS = 3_600_000
HOLD_H = 72
STOP_X = 1.5
FILL_ENTRY_X, FILL_OPEN_X = 1.575, 1.005
COST_ALT_BPS, COST_BTC_BPS = 74.0, 11.0
MAX_GAP_H = 2
VOL_X, BASE_H = 3.0, 24
HEDGE = "BTCUSDT"

# One bar: (open, high, low, close, volume, close_time, quote_volume), keyed by its open time in ms.
Bar = tuple[float, float, float, float, float, int, float]
Bars = Mapping[int, Bar]


class NoData(Exception):
    """The public data needed for a row are not there (yet): the book retries in a later run."""


def bars_from_klines(rows: Sequence[Sequence]) -> dict[int, Bar]:
    """Klines rows ``[open_time, open, high, low, close, volume, close_time, quote_volume, ...]`` by open time."""

    out: dict[int, Bar] = {}
    for r in rows:
        t = int(r[0])
        if t % HOUR_MS:
            continue            # main.py drops bars whose open is not on the hour
        out[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), int(r[6]), float(r[7]))
    return out


def funding_rows(rows: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    """(settlement time rounded to the second, rate), sorted, one row a time (the first kept)."""

    out: dict[int, float] = {}
    for t, r in rows:
        out.setdefault(int(round(int(t) / 1000.0)) * 1000, float(r))
    return sorted(out.items())


def leg0a(b: Bars, E: int, nh: int = HOLD_H) -> tuple[bool, list[str], int, int]:
    """main.leg0a and reason0a for one leg: (ok, reasons, longest gap in hours, missing bars)."""

    pres = [E + j * HOUR_MS in b for j in range(nh + 1)]
    ent = pres[0] and b[E][4] > 0
    ext = pres[nh] and b[E + nh * HOUR_MS][4] > 0
    run = maxrun = 0
    for p in pres:
        run = 0 if p else run + 1
        maxrun = max(maxrun, run)
    gap = maxrun + 1
    reasons = []
    if not pres[0]:
        reasons.append("no bar at entry")
    elif not ent:
        reasons.append("zero volume at entry")
    if not pres[nh]:
        reasons.append("no bar at exit")
    elif not ext:
        reasons.append("zero volume at exit")
    if gap > MAX_GAP_H:
        reasons.append(f"gap {gap} h")
    return ent and ext and gap <= MAX_GAP_H, reasons, gap, pres.count(False)


def fund_sum(rows: Sequence[tuple[int, float]], lo: int, hi: int) -> tuple[float, int]:
    """main.fund_window: the sum of rates with lo < t <= hi, and the number of rows."""

    sel = [r for t, r in rows if lo < t <= hi]
    return float(sum(sel)), len(sel)


def check_0b(alt: Bars, notice_ms: int) -> tuple[bool, float | None]:
    """main.py check 0b: (pass, ratio). It fails when the 24 h base or both notice hours have no bar."""

    nh = notice_ms // HOUR_MS * HOUR_MS
    qv = [alt[t][6] for t in (nh, nh + HOUR_MS) if t in alt]
    base = [alt[t][6] for t in range(nh - BASE_H * HOUR_MS, nh, HOUR_MS) if t in alt]
    if not base or not qv:
        return False, None
    med = float(statistics.median(base))
    best = max(qv)
    return best >= VOL_X * med, (best / med if med > 0 else None)


def row(alt: Bars, btc: Bars, alt_funding: Sequence[tuple[int, float]], btc_funding: Sequence[tuple[int, float]],
        E: int, notice_ms: int | None = None) -> dict:
    """The shadow row of one event (``run/main.py``, the 72 h hold). Funding rows are ``funding_rows``'s."""

    a_ok, a_why, a_gap, a_mis = leg0a(alt, E)
    b_ok, b_why, b_gap, b_mis = leg0a(btc, E)
    out: dict = {"entry_ms": E, "pass_0a": a_ok and b_ok, "alt_max_gap_h": a_gap, "alt_missing_h": a_mis,
                 "btc_max_gap_h": b_gap, "btc_missing_h": b_mis}
    flags: list[str] = []
    if not out["pass_0a"]:            # no return, and 0b is not evaluated (main.py)
        out["reasons_0a"] = [f"alt: {x}" for x in a_why] + [f"btc: {x}" for x in b_why]
        out["flags"] = flags
        return out
    if notice_ms is not None:
        ok_b, ratio = check_0b(alt, notice_ms)
        out.update(pass_0b=ok_b, qv_ratio=None if ratio is None else round(ratio, 2))
        if not ok_b:
            flags.append("0b_fail")
    ae, be = alt[E][0], btc[E][0]
    k = next((j for j in range(HOLD_H) if E + j * HOUR_MS in alt and alt[E + j * HOUR_MS][1] >= STOP_X * ae), None)
    if k is not None:
        tk = E + k * HOUR_MS
        ax = max(FILL_ENTRY_X * ae, FILL_OPEN_X * alt[tk][0])
        if tk in btc:
            bx = btc[tk][3]
        else:                       # main.trade_core: the next BTC open, flagged
            bx = btc[E + min(k + 1, HOLD_H) * HOUR_MS][0]
            flags.append("btc_close_fallback")
        X = int(alt[tk][5])
    else:
        ax, bx, X = alt[E + HOLD_H * HOUR_MS][0], btc[E + HOLD_H * HOUR_MS][0], E + HOLD_H * HOUR_MS
    g = ((ae - ax) / ae + (bx - be) / be) * 1e4
    fa, na = fund_sum(alt_funding, E, X)
    fb, nb = fund_sum(btc_funding, E, X)
    if na == 0:
        flags.append("fund_alt_none")
    if nb == 0:
        flags.append("fund_btc_none")
    cost = COST_ALT_BPS + COST_BTC_BPS
    out.update(alt_entry=ae, btc_entry=be, stopped=k is not None, stop_bar_ms=None if k is None else E + k * HOUR_MS,
               exit_ms=X, alt_exit=ax, btc_exit=bx, g_bps=g, cost_bps=cost, fund_alt_recv_bps=fa * 1e4,
               fund_btc_paid_bps=fb * 1e4, fund_rows_alt=na, fund_rows_btc=nb,
               n_bps=g - cost + fa * 1e4 - fb * 1e4, flags=flags)
    return out


def fetch_row(client, perp: str, E: int, notice_ms: int) -> dict:
    """The row from public mainnet data: 1h klines of the alt and BTCUSDT and both legs' funding.

    Raises ``NoData`` when a leg returns no bar in the hold at all, so the book retries later; a fetch error
    (``tools.selection.fetch.FetchError``) also leaves the row to a later run.
    """

    from tools.newperp import fetch as nf

    lo = (notice_ms // HOUR_MS - BASE_H) * HOUR_MS          # the 0b base starts 24 h before the notice hour
    hi = E + (HOLD_H + 1) * HOUR_MS                        # bars opening up to E + 72 h
    alt = bars_from_klines(nf.klines(client, perp, "1h", min(lo, E), hi))
    btc = bars_from_klines(nf.klines(client, HEDGE, "1h", E, hi))
    for name, b in (("alt", alt), ("btc", btc)):
        if not any(E <= t < hi for t in b):
            raise NoData(f"no {name} bar in the hold")
    # Funding a little wider than (E, E + 73 h]: a stamp a few ms off its hour still rounds into the window.
    fa = funding_rows(nf.funding(client, perp, E - HOUR_MS, hi + HOUR_MS))
    fb = funding_rows(nf.funding(client, HEDGE, E - HOUR_MS, hi + HOUR_MS))
    return row(alt, btc, fa, fb, E, notice_ms)
