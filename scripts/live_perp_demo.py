"""The books on Binance USD-M Demo Trading: one run.

The pair book always runs. Book LV (--lv), book LEARN (--learn, H-LEARN-11),
book FLOW02 (--flow02, H-FLOW-BTC-02), book FLOW01 (--flow01, H-FLOW-BTC-01)
and book KRW (--krw, H-KRW-FADE-16) run beside it on the same account, in that
order, through the same order path, ledger, reconcile and writer lock.

Without --execute it connects to the demo venue's public market list, reads
mainnet listings and prints what is due; nothing is signed or sent. With
--execute it also needs TRADING_MODE=testnet, TRADING_EXECUTION_ENABLED=true,
no TRADING_KILL_SWITCH, the demo API key BINANCE_DEMO_FUTURES_API_KEY with
either BINANCE_DEMO_FUTURES_API_SECRET (system-generated) or
BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE (self-generated Ed25519, a PEM file
readable by its owner only), and LIVE_SAFETY_HMAC_KEY.

--only flow01 is book FLOW01's 5-minute mode. It first asks public mainnet
data and FLOW01's record, without .env or any key, whether a signed run is
needed (an exit due, an order left unsettled, a fresh event). If not, it ends
there and signs nothing. If so, the signed run reconciles, checks every
book's legs and the venue's positions, then steps book FLOW01 alone. The
checks can still send orders as in every run (the hedge of a pair whose stop
fired, an LV roll that was cut off). It needs the same book flags as the
hourly run, so every book's legs are explained.

--only krw is book KRW's 5-minute mode, the same way. Its pre-check reads
book KRW's record and the order ledger (both unverified), Upbit's public
notice list and Binance's public exchangeInfo, without .env or any key, and
asks for a signed run only when an event is inside its entry window, an exit
is due, or an order is unsettled. The signed run then steps book KRW alone
after every check. --krw-resume, once and by hand (the hourly wrapper passes
it on), starts book KRW's new sleeve after its halt or a new --krw-usd, if
the book is flat.

Rules: docs/operations/PERP_DEMO_RUNNER.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from dotenv import load_dotenv  # noqa: E402

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter  # noqa: E402
from trading_agent.execution.live_safety import (  # noqa: E402
    LiveExecutionLock,
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
    configured_entry_lock_reason,
    require_execution_authorization,
)
from trading_agent.execution.perp import flowbtc  # noqa: E402
from trading_agent.execution.perp import flowflush  # noqa: E402
from trading_agent.execution.perp import krwfade  # noqa: E402
from trading_agent.execution.perp.book import Book, PairStore, run_cycle  # noqa: E402
from trading_agent.execution.perp.learnbook import LEARN_USD_CAP, LearnBook  # noqa: E402
from trading_agent.execution.perp.lowvol import LowVolBook  # noqa: E402
from trading_agent.execution.perp.venue import (  # noqa: E402
    PerpDemoVenue,
    demo_config,
    load_demo_credentials,
)

LEARN_STORE = "learn_book.json"
FLOW02_STORE = "flow02.json"
FLOW01_STORE = flowflush.STORE   # "flow01.json"
KRW_STORE = krwfade.STORE        # "krw.json"
ONLY = ("flow01", "krw")         # books that have a mode of their own


def now_ms() -> int:
    return int(time.time() * 1000)


def mainnet_listings() -> dict:
    """Public mainnet exchangeInfo through the NL-P allowlisted client."""

    from tools.newperp import fetch as nf

    return nf.Client().get(nf.EXCHANGE_INFO, {})


def public_client():
    """The NL-P allowlisted public client on fapi.binance.com (no key)."""

    from tools.newperp import fetch as nf

    return nf.Client()


def sealed_check():
    """The selection run's mask: no LV or LEARN leg may be one of the operator's masked coins."""

    from tools.selection.config import is_sealed

    return is_sealed


def flow01_feed():
    """Book FLOW01's public mainnet data: 1m klines and 5m open interest (tools/flowflush/feed.py)."""

    from tools.flowflush.feed import Feed

    return Feed()


def flow01_reason(root: Path, at_ms: int, feed) -> str | None:
    """Why FLOW01 needs a signed run now, or None. Reads flow01.json unverified and public data only."""

    return flowflush.signed_run_reason(at_ms, PairStore.peek(root / FLOW01_STORE), feed)


def krw_sources(box: dict) -> tuple:
    """Book KRW's public data: Upbit's notice list (page 1), mainnet exchangeInfo by symbol and the shadow row.

    No key. exchangeInfo is read once a run, and only when a notice needs it; ``box["info"]`` is the run's own
    exchangeInfo when the pair book has read it already.
    """

    from tools.krwfade import feed as kf
    from tools.krwfade import rules as kr
    from tools.krwfade import shadow as ks
    from tools.newperp import fetch as nf

    client = nf.Client()

    def symbols() -> dict:
        if box.get("info") is None:
            box["info"] = client.get(nf.EXCHANGE_INFO, {})
        return kr.symbol_map(box["info"])

    return kf.Feed().page, symbols, lambda perp, entry_ms, notice_ms: ks.fetch_row(client, perp, entry_ms, notice_ms)


def krw_reason(root: Path, at_ms: int, sources: tuple) -> str | None:
    """Why book KRW needs a signed run now, or None. Reads krw.json and the order ledger unverified, without a key,
    and public data only."""

    notices, symbols, _ = sources
    return krwfade.signed_run_reason(at_ms, PairStore.peek(root / KRW_STORE), notices, symbols,
                                     krwfade.peek_ledger(root / "order_state.json"))


def flow02_signal(root: Path):
    """H-FLOW-BTC-02's store and frozen model (tools/flowbtc/signal.py). Imported here: it loads sklearn."""

    from tools.flowbtc.signal import Signal

    return Signal(root)


def sized(cap: float):
    """An argparse type for a book's notional: a number in (0, cap]."""

    def parse(text: str) -> float:
        try:
            v = float(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from exc
        if not 0 < v <= cap:
            raise argparse.ArgumentTypeError(f"must be in (0, {cap:g}]")
        return v

    return parse


def parse_names(text: str) -> tuple[list[str], list[str]]:
    """``"LTC,XLM:SUI,WLD"``: list coins to hold long, then short."""

    longs, _, shorts = text.partition(":")
    split = lambda s: [c.strip().upper() for c in s.split(",") if c.strip()]  # noqa: E731
    return split(longs), split(shorts)


def book_counts(learn: LearnBook | None, flow02: flowbtc.FlowBtcBook | None,
                flow01: flowflush.FlowFlushBook | None, krw: krwfade.KrwFadeBook | None = None) -> dict:
    """Each new book's counts for status.json: counts and statuses only, never an amount."""

    out: dict = {}
    if learn is not None:
        out.update({f"learn_{k}": v for k, v in learn.counts().items()})
    if flow02 is not None:
        out.update(flowbtc.status(flow02.store))
    if flow01 is not None:
        out.update(flow01.counts())
    if krw is not None:
        out.update(krw.counts())
    return out


def write_status(root: Path, report: dict, pairs: PairStore, lv_blocks: PairStore | None = None,
                 books: dict | None = None) -> None:
    status = {
        "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "execute": report.get("execute"),
        "run": report.get("only") or "all",
        "open_pairs": len(pairs.open_pairs()),
        "closed_pairs": sum(1 for p in pairs.pairs.values() if p.get("status") == "closed"),
        "failed_pairs": sum(1 for p in pairs.pairs.values() if p.get("status") in ("failed", "opening")),
        "entered_this_run": len(report.get("entered", [])),
        "skipped_this_run": len(report.get("skipped", [])),
        "due_this_run": len(report.get("due", [])),
    }
    if lv_blocks is not None:   # counts only: the LV block records are closed until the LV read
        blocks = lv_blocks.pairs.values()
        live = [b for b in blocks if b.get("status") == "open"]
        status.update(
            lv_open_legs=sum(1 for b in live for leg in b["legs"].values() if leg["state"] == "held"),
            lv_stopped_legs=sum(1 for b in live for leg in b["legs"].values() if leg["state"] == "stopped"),
            lv_blocks={k: sum(1 for b in blocks if b.get("status") == k) for k in ("open", "closed", "missed", "failed")},
        )
    status.update(books or {})
    shadow = root / "shadow.json"
    if shadow.exists():
        sh = json.loads(shadow.read_text(encoding="utf-8"))
        status.update(shadow_open=len(sh.get("open", {})), shadow_closed=int(sh.get("closed", 0)))
    tmp = root / ".status.json.tmp"
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, root / "status.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true", help="sign and send orders on the demo venue")
    ap.add_argument("--root", default=str(ROOT / "data" / "perp_demo"))
    ap.add_argument("--pair-usd", type=float, default=100.0, help="notional of each leg in USDT")
    ap.add_argument("--rehearse", metavar="SYMBOL", help="open one pair on an existing demo symbol to test the mechanics")
    ap.add_argument("--rehearse-hold-h", type=int, default=2)
    ap.add_argument("--shadow", action="store_true", help="also record the rule on mainnet prices (paper, public data only)")
    ap.add_argument("--lv", action="store_true", help="also trade book LV's weekly pick (docs/operations/PERP_DEMO_RUNNER.md)")
    ap.add_argument("--lv-usd", type=float, default=1000.0, help="notional of book LV, both legs together, in USDT")
    ap.add_argument("--selection-root", default=str(ROOT / "data" / "selection"), help="where the weekly selection run writes lv_book/")
    ap.add_argument("--lv-rehearse", metavar="LONGS:SHORTS", help='roll book LV into a hand-given pick now, e.g. "LTC,XLM:SUI,WLD"')
    ap.add_argument("--lv-rehearse-hold-h", type=int, default=2)
    ap.add_argument("--learn", action="store_true", help="also trade H-LEARN-11's daily decisions (book LEARN)")
    ap.add_argument("--learn-usd", type=sized(LEARN_USD_CAP), default=2000.0,
                    help=f"notional of book LEARN in USDT, a twentieth a coin; at most {LEARN_USD_CAP:g}")
    ap.add_argument("--learn-root", default=str(ROOT / "data" / "learn_paper"),
                    help="the H-LEARN-11 paper book's bars/ and models/ (its ledger is never written here)")
    ap.add_argument("--flow02", action="store_true", help="also trade H-FLOW-BTC-02's daily BTCUSDT call (book FLOW02)")
    ap.add_argument("--flow02-usd", type=sized(flowbtc.USD_CAP), default=1000.0,
                    help=f"notional of book FLOW02 in USDT; at most {flowbtc.USD_CAP:g}")
    ap.add_argument("--flow02-root", default=str(ROOT / "data" / "flowbtc"), help="book FLOW02's data store and models")
    ap.add_argument("--flow01", action="store_true", help="also trade H-FLOW-BTC-01's flush events (book FLOW01)")
    ap.add_argument("--flow01-usd", type=sized(flowflush.USD_CAP), default=1000.0,
                    help=f"notional of book FLOW01 in USDT; at most {flowflush.USD_CAP:g}")
    ap.add_argument("--krw", action="store_true", help="also trade H-KRW-FADE-16's Upbit KRW listing events (book KRW)")
    ap.add_argument("--krw-usd", type=sized(krwfade.USD_CAP), default=500.0,
                    help=f"notional of each leg of a book KRW event in USDT; at most {krwfade.USD_CAP:g}")
    ap.add_argument("--krw-resume", action="store_true",
                    help="once, by hand: start book KRW's new sleeve after its halt or a new --krw-usd, if it is flat")
    ap.add_argument("--only", choices=ONLY,
                    help="run one book's own mode: a public pre-check, then, only when needed, every check and that book's step")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if (args.rehearse or args.lv_rehearse) and not args.execute:
        print("--rehearse and --lv-rehearse need --execute", file=sys.stderr)
        return 2
    if args.lv_rehearse and not args.lv:
        print("--lv-rehearse needs --lv", file=sys.stderr)
        return 2
    if args.only and not getattr(args, args.only):
        print(f"--only {args.only} needs --{args.only}", file=sys.stderr)
        return 2
    if args.only and (args.shadow or args.rehearse or args.lv_rehearse):
        print("--only runs no shadow ledger and no rehearsal", file=sys.stderr)
        return 2
    if args.krw_resume and (not args.krw or not args.execute or args.only):
        print("--krw-resume needs --krw and --execute, and no --only", file=sys.stderr)
        return 2
    feed = flow01_feed() if args.flow01 else None
    box: dict = {"info": None}           # the run's mainnet exchangeInfo, read once
    krw_src = krw_sources(box) if args.krw else None
    reason: str | None = None
    if args.only:
        # Before .env, the key, the writer lock or any write: a quiet moment ends here, unsigned.
        try:
            reason = flow01_reason(root, now_ms(), feed) if args.only == "flow01" else krw_reason(root, now_ms(), krw_src)
        except Exception as exc:
            print(f"{args.only} pre-check failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
            return 1
        if reason is None:
            print(json.dumps({"only": args.only, "signed_run": False}))
            return 0

    load_dotenv(ROOT / ".env")
    root.mkdir(parents=True, exist_ok=True)
    audit = root / "audit.jsonl"
    info: dict | None = None
    if args.shadow:
        # Paper on public mainnet data; runs even when execution is switched off.
        try:
            from tools.newperp import fetch as nf
            from tools.newperp import shadow

            client = nf.Client()
            info = client.get(nf.EXCHANGE_INFO, {})
            counts = shadow.step(client, info, now_ms(), root)
            print(json.dumps({"shadow": counts}, sort_keys=True))
        except Exception as exc:
            append_live_audit_event(audit, "perp_shadow_failed", {"error": type(exc).__name__, "detail": str(exc)[:300]})
            print(f"perp shadow failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        require_execution_authorization(execute=args.execute, testnet=True, cli_confirmation=None)
        key, secret = load_demo_credentials(os.environ) if args.execute else ("", "")
        integrity = os.getenv("LIVE_SAFETY_HMAC_KEY", "")

        def store(name: str) -> PairStore:
            """A book's record: signed and verified with --execute, an unverified read without."""

            return PairStore.load(root / name, integrity) if args.execute else PairStore.peek(root / name)

        with LiveExecutionLock(root / "writer.lock"):
            venue = PerpDemoVenue(CCXTAdapter(demo_config(key, secret)))
            venue.connect()
            if args.execute:
                venue.require_one_way_mode()
                orders = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
            else:
                orders = LiveRiskStateStore(root / "order_state.json")  # read only, never saved
            pairs = store("pairs.json")
            blocks = store("lv_blocks.json") if args.lv else None
            book = Book(venue, orders, pairs, audit, args.pair_usd)
            picks = Path(args.selection_root) / "lv_book" / "picks.jsonl"
            # The books beside the pair book, in run order (book.py): LV, LEARN, FLOW02, FLOW01, KRW.
            lv = LowVolBook(book, blocks, args.lv_usd, picks, sealed_check()) if blocks is not None else None
            learn = (LearnBook(book, store(LEARN_STORE), args.learn_usd, Path(args.learn_root), picks,
                               public_client, sealed_check()) if args.learn else None)
            flow02 = (flowbtc.FlowBtcBook(book, store(FLOW02_STORE), args.flow02_usd, flow02_signal(Path(args.flow02_root)))
                      if args.flow02 else None)
            flow01 = flowflush.FlowFlushBook(book, store(FLOW01_STORE), args.flow01_usd, feed) if args.flow01 else None
            krw = (krwfade.KrwFadeBook(book, store(KRW_STORE), args.krw_usd, *krw_src, resume=args.krw_resume)
                   if krw_src is not None else None)
            others = [o for o in (lv, learn, flow02, flow01, krw) if o is not None]
            at = now_ms()
            entry_lock = configured_entry_lock_reason()
            if args.execute:
                append_live_audit_event(audit, "perp_run_started", {
                    "pair_usd": args.pair_usd, "rehearse": args.rehearse or "",
                    "lv_usd": args.lv_usd if lv is not None else 0, "lv_rehearse": args.lv_rehearse or "",
                    "learn_usd": args.learn_usd if learn is not None else 0,
                    "flow02_usd": args.flow02_usd if flow02 is not None else 0,
                    "flow01_usd": args.flow01_usd if flow01 is not None else 0,
                    "krw_usd": args.krw_usd if krw is not None else 0,
                    "only": args.only or "", "only_reason": reason or ""})
            if info is None and not args.only:
                info = mainnet_listings()
            box["info"] = box["info"] or info      # book KRW reuses the pair book's exchangeInfo
            report = run_cycle(
                book,
                now_ms=at,
                mainnet_info=info,
                execute=args.execute,
                entry_lock=entry_lock,
                rehearse=args.rehearse,
                rehearse_hold_h=args.rehearse_hold_h,
                others=others,
                only=args.only,
            )
            if args.only:
                report["only_reason"] = reason
            if lv is not None and args.lv_rehearse:
                if entry_lock:
                    raise LiveSafetyError(f"no LV rehearsal under the entry lock: {entry_lock}")
                longs, shorts = parse_names(args.lv_rehearse)
                report["lv_rehearsal"] = lv.rehearse(longs, shorts, at, args.lv_rehearse_hold_h)
            if args.execute:
                write_status(root, report, pairs, blocks, book_counts(learn, flow02, flow01, krw))
                append_live_audit_event(audit, "perp_run_finished", {k: v for k, v in report.items() if k != "skipped"})
    except Exception as exc:  # every stop is audited; the next run reconciles first
        if args.execute:
            append_live_audit_event(audit, "perp_run_failed", {"error": type(exc).__name__, "detail": str(exc)[:300]})
        kind = "stopped" if isinstance(exc, LiveSafetyError) else "failed"
        print(f"perp demo run {kind}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
