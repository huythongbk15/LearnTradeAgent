"""The new perp pair, by the clock since listing: pure rules, no I/O.

Short a new USDT perp 4 hours after its listing and hold a long BTC perp of the
same notional; close both after 168 hours, or the hedge alone once the
exchange-side stop at 1.5x the short's fill has closed the short. Research:
data/deep_20261103_01 and data/fills_20261103 (git-ignored). A change of a
constant needs the operator's ruling in docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

from dataclasses import dataclass

HOUR_MS = 3_600_000
ENTRY_DELAY_H = 4      # short at listing + 4 h
ENTRY_WINDOW_H = 2     # a run that first sees the entry later than this skips it
HOLD_H = 168           # both legs close 168 h after entry
STOP_X = 1.5           # exchange-side stop on the short at 1.5x its average fill
MAX_OPEN = 10          # open pairs at most
HEDGE = "BTCUSDT"
UNDERLYING = "COIN"    # exchangeInfo underlyingType; equities, commodities and indices are out


@dataclass(frozen=True)
class Listing:
    symbol: str
    onboard_ms: int

    @property
    def entry_ms(self) -> int:
        return self.onboard_ms + ENTRY_DELAY_H * HOUR_MS


def is_candidate(s: dict) -> bool:
    """A trading USDT perpetual on a crypto underlying that Binance does not tag Alpha."""

    return (
        s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
        and s.get("underlyingType") == UNDERLYING
        and "Alpha" not in (s.get("underlyingSubType") or [])
        and str(s.get("symbol", "")).isascii()
        and s.get("symbol") != HEDGE
    )


def due_entries(
    mainnet_info: dict,
    demo_symbols: set[str],
    now_ms: int,
    seen: set[str],
) -> tuple[list[Listing], list[dict]]:
    """Listings to short now, and the reasons others in their window are skipped.

    ``mainnet_info`` is the public mainnet exchangeInfo, the source of listing
    times and tags. ``demo_symbols`` are the symbols trading on the demo venue.
    ``seen`` holds symbols this book already entered or skipped.
    """

    due: list[Listing] = []
    skipped: list[dict] = []
    for s in mainnet_info.get("symbols", []):
        if not is_candidate(s):
            continue
        listing = Listing(str(s["symbol"]), int(s["onboardDate"]))
        if listing.symbol in seen or now_ms < listing.entry_ms:
            continue
        if now_ms >= listing.entry_ms + ENTRY_WINDOW_H * HOUR_MS:
            # Past its window: recorded once so a late run never enters it.
            if now_ms < listing.entry_ms + HOLD_H * HOUR_MS:
                skipped.append({"symbol": listing.symbol, "reason": "missed_window"})
            continue
        if listing.symbol not in demo_symbols:
            skipped.append({"symbol": listing.symbol, "reason": "not_on_demo"})
            continue
        due.append(listing)
    due.sort(key=lambda x: (x.onboard_ms, x.symbol))
    return due, skipped


def stop_price(short_fill: float) -> float:
    return short_fill * STOP_X


def exit_due(entry_ms: int, now_ms: int, hold_h: int = HOLD_H) -> bool:
    return now_ms >= entry_ms + hold_h * HOUR_MS
