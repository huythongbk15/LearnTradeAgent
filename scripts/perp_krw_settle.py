"""Settle a book KRW event closed outside the book: the operator's way out (PERP_LIVE_R0.md section 5 item 4).

When a signed run finds less of an event's alt short at the venue than its record holds, and the stop's algo order
shows no fill, the close was not the book's (a liquidation, an ADL or a hand close in the app). The event becomes
``outside``: no order of the book moves it and new entries are held. This command records what closed it from the
venue's trades (``KrwFadeBook.settle_outside``): each order the ledger does not know that traded on the event's legs
since the run that entered it becomes a fill of the event, with its price, time and fee from the trades.

The default is a report. ``--apply`` writes the fills under the root's writer lock (no run can overlap), and needs
``--operator-checked-app``: the operator states that the app's order and trade history shows those closes. Then the
event goes back to its earlier status when a short is left, else it is ``closing``: the next signed run sells the
event's BTC left, cancels a stop still listed and closes it. It only reads the venue: the live venue is built
read-only, so this command never sends an order or a cancel. The report carries statuses, counts, quantities and keys.

    .venv/bin/python scripts/perp_krw_settle.py --venue demo --event e123-ABC
    .venv/bin/python scripts/perp_krw_settle.py --venue live --env-file ~/.perp_live.env \\
        --event e123-ABC --apply --operator-checked-app
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from trading_agent.execution.live_safety import (  # noqa: E402
    LiveExecutionLock,
    LiveRiskStateStore,
    LiveSafetyError,
)


def _unused(*_args, **_kwargs):
    raise AssertionError("the settle reads no notice, exchangeInfo or shadow row")


def settle(root: Path, venue, integrity: str, event: str, *, apply: bool, operator_checked: bool,
           now_ms: int | None = None) -> dict:
    """The report, and with ``apply`` the settled event; refusals are listed, never raised."""

    from trading_agent.execution.perp import krwfade
    from trading_agent.execution.perp.book import Book, PairStore

    orders = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
    krw_store = PairStore.load(root / krwfade.STORE, integrity)
    usd = float((krw_store.pairs.get(krwfade.STATE) or {}).get("usd") or 0.0)
    book = Book(venue, orders, PairStore.load(root / "pairs.json", integrity), root / "audit.jsonl", usd)
    kb = krwfade.KrwFadeBook(book, krw_store, usd, _unused, _unused, _unused)
    at = int(time.time() * 1000) if now_ms is None else now_ms
    report = kb.settle_outside(event, at, apply=False)
    if apply and not operator_checked:
        report["refusals"].append("--apply needs --operator-checked-app: the operator has looked at the app's "
                                  "order and trade history")
    if apply and not report["refusals"]:
        report = kb.settle_outside(event, at, apply=True)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--venue", choices=("demo", "live"), required=True)
    ap.add_argument("--event", required=True, help="the event id, as the run's audit names it (e<notice>-<TOKEN>)")
    ap.add_argument("--root", default=None, help="the perp root (default: data/perp_demo or data/perp_live)")
    ap.add_argument("--env-file", default=None, help="live only: the live env file, mode 600")
    ap.add_argument("--operator-checked-app", action="store_true",
                    help="the operator states that the app's order and trade history shows these closes")
    ap.add_argument("--apply", action="store_true", help="record the closes (default: report only)")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ROOT / "data" / ("perp_live" if args.venue == "live" else "perp_demo")
    try:
        from scripts.perp_resolve_intent import connect

        if not (root / "order_state.json").is_file():
            raise LiveSafetyError(f"no order ledger under {root}")
        venue, integrity = connect(args.venue, args.env_file)
        with LiveExecutionLock(root / "writer.lock"):
            report = settle(root, venue, integrity, args.event, apply=args.apply,
                            operator_checked=args.operator_checked_app)
    except Exception as exc:
        print(f"perp KRW settle stopped: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=1, sort_keys=True))
    if report["applied"]:
        return 0
    return 0 if not report["refusals"] and not args.apply else 2


if __name__ == "__main__":
    sys.exit(main())
