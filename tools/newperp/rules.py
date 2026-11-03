"""NL-P rules on public futures data. Pure: dicts and lists in, numbers out, no clock, no network."""
from __future__ import annotations

from tools.newperp import config as cf

DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def new_perps(info: dict, stamp_ms: int) -> list[dict]:
    """USDT perpetuals on a crypto underlying, trading, listed in the NEW_DAYS before the stamp. Oldest listing first."""
    out = []
    for s in info.get("symbols", []):
        if (s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
                and s.get("underlyingType") == cf.UNDERLYING and stamp_ms - cf.NEW_DAYS * DAY_MS <= int(s["onboardDate"]) < stamp_ms):
            out.append({"symbol": s["symbol"], "base": s.get("baseAsset"), "onboard_ms": int(s["onboardDate"]), "sub": s.get("underlyingSubType") or []})
    return sorted(out, key=lambda x: (x["onboard_ms"], x["symbol"]))


def book_of(p: dict) -> str:
    """"alpha" when the exchange tags the underlying Alpha (Binance Alpha tokens), else "core"."""
    return "alpha" if "Alpha" in p.get("sub", []) else "core"


def book_cost(depth: dict, side: str, usd: float) -> dict:
    """Walk one side of the book for a market order of `usd` notional. Cost in bps against the mid, how much was filled, the spread."""
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    if not bids or not asks:
        return {"mid": None, "spread_bps": None, "cost_bps": None, "filled_usd": 0.0}
    mid = (bids[0][0] + asks[0][0]) / 2
    levels = bids if side == "sell" else asks
    left = usd
    got_q = 0.0
    spent = 0.0
    for p, q in levels:
        take = min(q, left / p)
        got_q += take
        spent += take * p
        left -= take * p
        if left <= 1e-9:
            break
    vwap = spent / got_q if got_q else None
    cost = None if vwap is None else (mid - vwap) / mid * 1e4 if side == "sell" else (vwap - mid) / mid * 1e4
    return {"mid": mid, "spread_bps": (asks[0][0] - bids[0][0]) / mid * 1e4, "cost_bps": cost, "filled_usd": round(spent, 2)}


def open_at(rows: list, t_ms: int) -> float | None:
    """Open of the bar that opens at t_ms."""
    for r in rows:
        if int(r[0]) == t_ms:
            return float(r[1])
    return None


def stop_fill(m1: list, entry: float, stop: float = cf.STOP) -> dict | None:
    """First 1m bar with trades whose high reaches stop x entry. F1 max(trigger, open), F2 max(F1, minute VWAP), F3 the minute's high, as ratios to entry."""
    trig = stop * entry
    for r in m1:
        o, h, v, qv = float(r[1]), float(r[2]), float(r[5]), float(r[7])
        if v > 0 and h >= trig:
            f1 = max(trig, o)
            return {"minute_ms": int(r[0]), "f1": f1 / entry, "f2": max(f1, qv / v) / entry, "f3": h / entry}
    return None


def funding_sum(events: list[tuple[int, float]], a_ms: int, b_ms: int) -> float:
    return float(sum(r for t, r in events if a_ms <= t < b_ms))
