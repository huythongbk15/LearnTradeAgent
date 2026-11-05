"""The keyless poll of book FLOW01's 5-minute mode: does this moment need a signed run?

    PYTHONPATH=src:. python -m tools.flowflush.poll [--root data/perp_demo]

Reads the book's store without its key (``flow01.json``, unverified: the signed run verifies it) and public
mainnet data through the NL-P allowlisted client. Signs nothing, sends nothing, writes nothing. Prints one
JSON line ``{"signed_run": bool, "reason": ...}`` with no price or return. Exit status: 0 when a signed run
is needed (an exit due, an order left unsettled, a fresh event), 1 when not, 2 when the poll failed (the
hourly run still handles exits and records what a failed poll missed).

The endpoint publishes the row of a 5-minute stamp some seconds to over a minute after it (10, 18, 22 and
84 s on four stamps, 2026-11-05). With ``--wait-s`` a quiet poll whose look does not yet hold the current stamp asks again every
``--every-s`` seconds until it does or the wait ends, so an event is not left to the next poll 5 minutes later.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    from trading_agent.execution.perp.book import PairStore
    from trading_agent.execution.perp.flowflush import STORE, signed_run_reason
    from tools.flowflush.feed import Feed, published_now

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT / "data" / "perp_demo"))
    ap.add_argument("--wait-s", type=float, default=0.0, help="wait this long for the current stamp's row")
    ap.add_argument("--every-s", type=float, default=10.0)
    args = ap.parse_args(argv)
    feed = Feed()
    end = time.time() + args.wait_s
    tries = 0
    while True:
        tries += 1
        now_ms = int(time.time() * 1000)
        try:
            reason = signed_run_reason(now_ms, PairStore.peek(Path(args.root) / STORE), feed)
        except Exception as exc:
            print(json.dumps({"signed_run": False, "error": type(exc).__name__, "tries": tries}))
            return 2
        last = getattr(feed, "last", None)
        if reason is not None or last is None or published_now(last) or time.time() + args.every_s > end:
            break
        time.sleep(args.every_s)
    print(json.dumps({"signed_run": reason is not None, "reason": reason, "tries": tries}))
    return 0 if reason is not None else 1


if __name__ == "__main__":
    sys.exit(main())
