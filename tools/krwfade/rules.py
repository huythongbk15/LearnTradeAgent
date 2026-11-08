"""The frozen H-KRW-FADE-16 event rule: which Upbit notice is an event, on which Binance perp, and when the trade enters.

Pure functions: titles, times and an exchangeInfo symbol map in; no clock, no network.

- **Classifier** (``krw_tokens``): the KRW listing family of the corrected classifier
  ``data/notice_feasibility_20261108/verify/classification/classify.py`` (sha256 469dbe1927e1a779...), ported
  line for line: ``strip_trailing_parens``, ``u_tokens``, ``listing_markets``, ``LISTING_RE`` and the branches of
  ``u_classify`` that come before a listing (a delisting, a pair removal or a warning release is never a listing).
  It handles a follow-up appended to the original title ("(... 안내)", a date, a time: the notice time stays
  ``first_listed_at``), several tickers in one bracket, single-letter tickers, a market list per token ("A(KRW, BTC
  마켓), B(BTC, USDT 마켓)"), BTC- or USDT-only listings (not KRW) and a KRW-market addition for a coin Upbit already
  lists (KRW). A ticker cancelled in a follow-up ("거래지원 취소") is dropped, and so is the ticker of a swap warning
  inside a listing notice.
- **One deviation, forced:** the research classifier decided a token of a multi-token title with one shared market
  list that names KRW ("KRW, BTC 마켓 디지털 자산 추가 (SHIB, GAL)") as not KRW when that token got a non-ambiguous
  KRW-market notice of its own more than a day later (classify.py lines 352-362). That reads the future. Here every
  such token is KRW when its notice comes, as the title says, and is flagged ``ambiguous``. Of the 15 ambiguous KRW
  rows from 2022-01 to 2026-10, research ruled 6 not KRW: IMX 2022-02-08, GAL 2023-01-18, ALT 2024-02-27, POKT
  2024-03-27, BIGTIME 2024-04-23 and OMNI 2024-05-03. Only BIGTIME passes the rest of the rule (an event here, not
  in research; see parity.py); IMX, ALT (32.5 d) and OMNI fail the perp age, GAL and POKT have no perp in the
  2026-11-03 exchangeInfo. Research kept the other 9 (AVAX, SHIB, ACS, MASK, PYTH, MNT, AKT, ZETA and SKY).
  - Once the later notice comes, research's reading is known: ``chain`` leaves such a notice out of the token's 7-day
    chain, so it never merges away that later notice, and the book marks its event excluded from the read.
  - A listing a follow-up cancels is not in research's family: ``chain`` leaves it out of the 7-day chain too.
- **Perp** (``resolve``): ``ALIAS`` first and alone (Upbit BEAM is BEAMXUSDT, MET2 is METUSDT, AI is Gensyn's
  AIGENSYNUSDT, RAY is RAYSOLUSDT), else TOKENUSDT, 1000TOKENUSDT, 1000000TOKENUSDT, 1MTOKENUSDT: the first USDT
  perpetual in exchangeInfo that trades. ``DENY`` names Upbit tickers whose Binance namesake is another coin (PROS:
  Upbit's is Pharos).
- **Exclusions:** the H-XSEC-01 coins, BNSOL and BTC (``SEALED``), and pegged tokens (``PEGGED``, copied from
  ``data/krw_fade_20261108_01/scripts/freeze_events.py``, sha256 ccd982090d3e04b2...).
- **Eligible:** the perp is TRADING in mainnet exchangeInfo and onboarded at least 35 days before the notice; and the
  notice is the token's first KRW-listing notice within 7 days (a notice within 7 days of the token's previous one
  is merged into it, as the classifier's 7-day event window chains them; ``chain`` gives the previous ones).
- **Entry** ``E = entry_ms(notice)``: the first 1h open at or after notice + 10 minutes. The research trade holds 72 h
  from E with a stop at 1.5x (PROTOCOL.md, sha256 2639470ed22de5c2...; ``tools/krwfade/shadow.py``).

A change of a constant needs the operator's ruling in docs/operations/PERP_DEMO_RUNNER.md. Research description, not
advice.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
ENTRY_DELAY_MS = 10 * MIN_MS      # E = the first 1h open at or after notice + 10 min
MIN_AGE_MS = 35 * DAY_MS          # the perp onboarded at least 35 days before the notice
DEDUPE_MS = 7 * DAY_MS            # the token's first KRW notice within 7 days
HEDGE = "BTCUSDT"

SEALED = frozenset({"ADA", "AVAX", "BNB", "DOGE", "DOT", "ETH", "LINK", "NEAR", "SOL", "TRX", "XRP", "ZEC", "BNSOL", "BTC"})
PEGGED = frozenset({"USDC", "USDT", "USD1", "USDE", "RLUSD", "USDG", "EURC", "PYUSD", "JPYC", "USDS", "DAI", "FDUSD", "TUSD",
                    "USDP", "XAUT", "PAXG", "BUSD", "HBD", "AEUR", "IDRT", "SUSD"})
ALIAS = {"BEAM": "BEAMXUSDT", "MET2": "METUSDT", "AI": "AIGENSYNUSDT", "RAY": "RAYSOLUSDT"}
DENY = {"PROS": "Upbit PROS is Pharos; Binance PROSUSDT is another coin"}
PREFIXES = ("", "1000", "1000000", "1M")


def entry_ms(notice_ms: int) -> int:
    """Open time of the first 1h bar whose open is at or after notice + 10 min (freeze_events.entry_ms)."""

    t = int(notice_ms) + ENTRY_DELAY_MS
    return -(-t // HOUR_MS) * HOUR_MS


# ── classifier: classify.py's Upbit listing branch ─────────────────────────────

def strip_trailing_parens(t: str) -> tuple[str, list[str]]:
    """Split a title into (main, [trailing parenthetical groups]) keeping market specs '(... 마켓)' in main."""

    s = t.rstrip()
    tails: list[str] = []
    while s.endswith(")"):
        depth, i = 0, len(s) - 1
        while i >= 0:
            if s[i] == ")":
                depth += 1
            elif s[i] == "(":
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        if i < 0:
            break
        inner = s[i:][1:-1]
        # a trailing group is follow-up text only when it reads like a note, a date or a time; token lists such as
        # '(WEMIX)', '(AVAX, IMX)', '(바운스토큰 AUCTION, 던프로토콜 DAWN)' and market specs '(KRW, BTC 마켓)' stay
        if not re.search(r"안내|완료|업데이트|종료|~|\d+/\d+|\d+\.\d+\.\d+|\d{1,2}:\d{2}", inner) or inner.endswith("마켓"):
            break
        tails.insert(0, inner)
        s = s[:i].rstrip()
    return s, tails


def u_tokens(main: str) -> list[tuple[str, int, int]]:
    """Token occurrences in the main part of an Upbit title: (token, start, end), in title order."""

    occ = []
    for m in re.finditer(r"\(([A-Z0-9]{1,15}(?:\s*,\s*[A-Z0-9]{1,15})*)\)", main):
        inner, base = m.group(1), m.start(1)
        for mm in re.finditer(r"[A-Z0-9]{1,15}", inner):
            if mm.group(0) in ("KRW",):
                continue
            occ.append((mm.group(0), base + mm.start(), base + mm.end()))
    for m in re.finditer(r"\b([A-Z0-9]{2,15})\([가-힣]", main):          # 'PUFFER(퍼퍼)'
        occ.append((m.group(1), m.start(1), m.end(1)))
    for m in re.finditer(r"\(([^()]*[가-힣][^()]*)\)", main):             # old '(컴파운드 COMP, 스와이프 SXP)'
        for mm in re.finditer(r"[가-힣]+\s+([A-Z][A-Z0-9]{1,14})\b", m.group(1)):
            occ.append((mm.group(1), m.start(1) + mm.start(1), m.start(1) + mm.end(1)))
    return sorted(set(occ), key=lambda x: x[1])


MKT = re.compile(r"KRW|원화|BTC|USDT|ETH")


def listing_markets(main: str, occ: list[tuple[str, int, int]]) -> dict[str, tuple[bool, bool, str]]:
    """Per token: (is_krw, ambiguous, the market words of its spec).

    A token's spec is the text between the last token before the next '마켓' and that '마켓'; a token after the last
    '마켓' takes the text before the first. ``ambiguous``: several tokens share that spec.
    """

    mk = [m.start() for m in re.finditer(r"마켓", main)]
    lead = main[:mk[0]] if mk else ""
    used: dict[object, list[str]] = {}
    res: dict[str, tuple[str, object]] = {}
    for tok, _s, e in occ:
        nxt = next((p for p in mk if p >= e), None)
        if nxt is not None:
            last_end = max([e2 for _t2, s2, e2 in occ if s2 >= e and e2 <= nxt] + [e])
            used.setdefault(nxt, []).append(tok)
            res[tok] = (main[last_end:nxt], nxt)
        else:
            used.setdefault("lead", []).append(tok)
            res[tok] = (lead, "lead")
    return {tok: (bool(re.search(r"KRW|원화", spec)), len(used[key]) > 1, " ".join(MKT.findall(spec)))
            for tok, (spec, key) in res.items()}


LISTING_RE = re.compile(r"신규 거래지원|신규 거래 지원|디지털 자산 추가|마켓 추가 안내|신규 상장|코인 추가|상장 안내|상장$|"
                        r"마켓 오픈|원화마켓 상장|원화마켓 오픈|상장 예고|오픈 예정")


@dataclass(frozen=True)
class Token:
    token: str
    krw: bool              # the title lists it on the KRW market
    ambiguous: bool        # its market list is shared with other tokens of the title
    markets: str           # the market words of its spec, for the record
    cancelled: bool        # a follow-up cancelled it ("거래지원 취소")


def classify(title: str) -> list[Token] | None:
    """The listing tokens of an Upbit trade notice title, or None when the title is not a listing notice."""

    t = title
    if any(w in t for w in ("거래지원 종료", "거래 지원 종료", "페어 제거")):
        return None                                   # delisting family and market-only removals
    if re.search(r"유의 ?종목 (?:일시 )?(?:지정 )?해제", t):
        return None                                   # warning releases
    main, tails = strip_trailing_parens(t)
    if not (LISTING_RE.search(main) or LISTING_RE.search(t.split(" (")[0])):
        return None
    occ = u_tokens(main)
    warn = re.search(r"이에 따른 ([A-Z0-9]{2,15}) 유의 종목 지정", t)
    if warn:                                          # a swap warning inside a listing notice: that ticker is no listing
        occ = [o for o in occ if o[0] != warn.group(1)]
    mk = listing_markets(main, occ)
    tailtxt = " ".join(tails)
    cancelled = set(re.findall(r"\(([A-Z0-9]{1,15})\)", tailtxt)) if "거래지원 취소" in tailtxt else set()
    out = []
    for tok in dict.fromkeys(o[0] for o in occ):
        is_krw, amb, spec = mk[tok]
        out.append(Token(tok, is_krw, amb, spec, tok in cancelled))
    return out


def krw_tokens(title: str) -> list[Token]:
    """The tokens a title lists on the KRW market, not cancelled: the KRW listing family."""

    return [x for x in classify(title) or [] if x.krw and not x.cancelled]


# ── perp and eligibility ────────────────────────────────────────────────────────

def excluded(token: str) -> str | None:
    """Why a token is never an event: a sealed coin or a pegged token; None otherwise."""

    from tools.selection.config import is_sealed       # the selection mask: 1000- and 1M- spellings included

    if token.upper() in SEALED or is_sealed(token):
        return "sealed_coin"
    if token.upper() in PEGGED:
        return "pegged"
    if token in DENY:
        return "deny_collision"
    return None


def candidates(token: str) -> list[str]:
    """The Binance USDT perp names of an Upbit ticker, in order; an alias stands alone."""

    if token in ALIAS:
        return [ALIAS[token]]
    return [f"{p}{token}USDT" for p in PREFIXES]


def is_usdt_perp(s: Mapping) -> bool:
    return s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"


def trading_now(s: Mapping) -> bool:
    """The live rule: exchangeInfo says TRADING."""

    return s.get("status") == "TRADING"


@dataclass(frozen=True)
class Verdict:
    token: str
    perp: str | None
    ok: bool
    reason: str | None          # None when ok
    age_d: float | None = None


def resolve(token: str, symbols: Mapping[str, Mapping], trading: Callable[[Mapping], bool] = trading_now
            ) -> tuple[str | None, str | None]:
    """(perp, None) for the first candidate that trades; (first listed candidate or None, reason) otherwise."""

    if token in DENY:
        return None, "deny_collision"
    listed = [c for c in candidates(token) if c in symbols and is_usdt_perp(symbols[c])]
    for c in listed:
        if trading(symbols[c]):
            return c, None
    if listed:
        return listed[0], "not_trading"
    return None, "no_perp"


def verdict(token: str, notice_ms: int, symbols: Mapping[str, Mapping],
            trading: Callable[[Mapping], bool] = trading_now) -> Verdict:
    """Is a KRW-listing token of a notice an event on a perp? The 7-day merge is ``first_within``'s."""

    why = excluded(token)
    if why:
        return Verdict(token, None, False, why)
    perp, why = resolve(token, symbols, trading)
    if why:
        return Verdict(token, perp, False, why)
    onboard = symbols[perp].get("onboardDate")
    if onboard is None:
        return Verdict(token, perp, False, "no_onboard_date")
    age = int(notice_ms) - int(onboard)
    if age < MIN_AGE_MS:
        return Verdict(token, perp, False, "perp_younger_than_35d", round(age / DAY_MS, 1))
    return Verdict(token, perp, True, None, round(age / DAY_MS, 1))


def first_within(notice_ms: int, earlier: Iterable[int]) -> bool:
    """True when no earlier KRW notice of the token falls within 7 days before this one."""

    return not any(0 < int(notice_ms) - int(t) <= DEDUPE_MS for t in earlier)


def resolves(later_ms: int, ambiguous_ms: int) -> bool:
    """classify.py line 359: a non-ambiguous KRW notice of a token rules an ambiguous one not KRW when it comes more
    than a day after it."""

    return int(later_ms) > int(ambiguous_ms) + DAY_MS


def chain(notice_ms: int, ambiguous: bool, earlier: Iterable[Mapping | int]) -> list[int]:
    """The times of a token's earlier KRW notices that the 7-day merge chains this notice with (``first_within``).

    Research rules first and merges after (classify.py lines 352-362, then 480-497): an ambiguous notice is not KRW
    once the token has a non-ambiguous KRW notice more than a day after it, cancelled or not, and a cancelled listing
    is not in the family. Neither is in the chain. ``earlier``: the token's earlier KRW notices as ``{"t", "amb",
    "cancelled"}`` (a bare time is a non-ambiguous notice). A non-ambiguous notice is itself such a later notice for
    the ones before it.
    """

    rows = [{"t": int(e)} if isinstance(e, (int, float)) else e for e in earlier]
    later = [int(r["t"]) for r in rows if not r.get("amb")] + ([] if ambiguous else [int(notice_ms)])
    return [int(r["t"]) for r in rows
            if not r.get("cancelled") and not (r.get("amb") and any(resolves(x, r["t"]) for x in later))]


def symbol_map(exchange_info: Mapping) -> dict[str, Mapping]:
    """exchangeInfo's symbols by name."""

    return {str(s.get("symbol")): s for s in exchange_info.get("symbols") or [] if s.get("symbol")}
