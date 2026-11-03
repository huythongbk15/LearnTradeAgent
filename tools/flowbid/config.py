"""Frozen constants of the forced selling paper log.

The rule is the frozen test of 2026-11-03 (data/design_review_20261103_01/forced-flow-sleeve/measure/PROTOCOL.md, PASS on replay, Sharpe .88 against BTC .89 at the same volatility). A value here changes only with an operator ruling in docs/operations/FLOWBID_PAPER_RUN.md. The root comes from FLOWBID_ROOT, read at call time so a test or a rehearsal can point it elsewhere.
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.selection import config as sel

REPO = Path(__file__).resolve().parents[2]

# Flag, past only: a coin is down when the lowest traded price of the trailing 60 minutes is at least 10% under its last traded
# close before that window, that close at most 24 h old. The market flags when at least 15% of at least 15 counted coins are down.
DOWN = 0.10
WINDOW_MIN = 60
REF_MAX_AGE_MIN = 1440
SHARE = 0.15
MIN_DENOMINATOR = 15
EVENT_MIN = 1440                    # an event stays active 24 h; flags inside it are ignored

# Bids: 15, 20 and 30% under the last traded close before 00:00 UTC of the flag date, that close at most 48 h old, placed only if the
# last price is above the bid, live 24 h, filled on the first traded minute whose low reaches the bid less .5%.
K_LEVELS = (0.15, 0.20, 0.30)
THROUGH = 0.005
BID_REF_MAX_AGE_MIN = 2880
REST_MIN = 1440
LATENCY_MIN = 2                     # the study's bids went live two minutes after the flag minute; this log adds its own delay

# Exit at the last traded close H hours after the fill minute, no stop. Unlevered spot. Cost per round trip.
HOLDS_H = (24, 72, 168)
# The read's primary is the mean of all nine depth and hold cells, not one cell: on a walk-forward of the replay the best depth
# and hold of past years did not stay best (data/flowbid_wf_20261103/wf.out, rank correlation -.55 to +.10). Operator, 2026-11-03.
PRIMARY_CELLS = tuple((k, h) for k in K_LEVELS for h in HOLDS_H)
COST_BPS = 148.0
GONE_MIN = 1440                     # no trade in the 24 h before the exit stamp: booked at the last close, marked gone

READ_DATE = "2028-11-13"            # read once, with books RS and LV

# A run covers every closed minute since the last covered one, at most this far back, so a missed run is filled in later.
LOOKBACK_MIN = 900
LEAD_MIN = WINDOW_MIN + 120         # bars fetched before the first minute a run judges
MIN_FETCHED_SHARE = 0.8             # fewer coins than this share of the live universe: the minutes are not judged and stay open
SETTLE_CALLS = 80                   # settlement calls a run may spend
FETCH_WORKERS = 4                   # a call is mostly network wait (about .85 s here on 2026-11-03): 4 at a time, near 600 of the 6,000 weight a minute

# Frozen universe: the 182 list coins of the study's spot 1m grid (data/small_bars_20261101/grid), less the coins masked by
# the operator's option B of 2026-11-01 and one name that is not ASCII. A coin the exchange no longer has drops out.
_GRID = (
    "1000SATS 1MBABYDOGE AAVE ACH ACT ADA AGIX AI AIGENSYN AKRO ALLO ALPINE APE API3 APT AR ARB ARK ASTER ATOM AUCTION AVAX "
    "AVNT AXS BAKE BANANAS31 BANK BARD BCH BERA BIO BLUR BLZ BNB BNT BNX BOME BOND BONK BROCCOLI714 BTC CAKE CELO CFX CKB "
    "COMP CRV CYBER DASH DEXE DOGE DOGS DOT DYDX EIGEN ENA ENJ ENS ENSO EOS ETC ETH ETHFI FET FIL FLM FLOKI FORM FRONT FTM "
    "FTT FUN GALA GAS GIGGLE HBAR HIFI HIGH HOME ICP INJ IO JASMY JTO KAITO KITE LAYER LDO LEVER LINK LOOM LPT LQTY LTC LUNC "
    "MASK MATIC MEGA MEME MINA MKR MOVR MUBARAK MULTI NEAR NEIRO NIGHT NMR NOM NOT OG OGN OM ONDO OP OPN ORDI PAXG PENDLE "
    "PENGU PEOPLE PEPE PERP PHA PIXEL PNUT POLYX PUMP PYTH RARE RAY RNDR RUNE S SAGA SAND SEI SENT SHIB SNX SOL SPK STMX STO "
    "STORJ STRAX STRK STX SUI SUN SYN TAO TIA TNSR TOMO TON TRB TROY TRUMP TRX TURBO UMA UNFI UNI USTC USUAL VIRTUAL WAVES "
    "WCT WIF WLD WLFI XAUT XLM XPL XRP YGG ZEC ZEN ZK ZRO"
).split()
UNIVERSE = tuple(c for c in _GRID if not sel.is_sealed(c))


def root() -> Path:
    """Root of everything the log writes. Inside the repo it is data/flowbid and nothing else; outside it a rehearsal may point it anywhere but a runner store."""
    home = REPO / "data" / "flowbid"
    r = Path(os.environ.get("FLOWBID_ROOT") or home).resolve()
    sel.refuse_runner_store(r)
    if r.is_relative_to(REPO) and r != home:
        raise ValueError(f"FLOWBID_ROOT {r} is inside the repo and is not data/flowbid")
    return r


def ledger_path(r: Path) -> Path:
    return r / "ledger.jsonl"


def status_path(r: Path) -> Path:
    return r / "status.json"
