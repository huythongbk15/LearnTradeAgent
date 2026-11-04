"""The new perp pair book on Binance USD-M Demo Trading: one run.

Without --execute it connects to the demo venue's public market list, reads
mainnet listings and prints what is due; nothing is signed or sent. With
--execute it also needs TRADING_MODE=testnet, TRADING_EXECUTION_ENABLED=true,
no TRADING_KILL_SWITCH, the demo API key BINANCE_DEMO_FUTURES_API_KEY with
either BINANCE_DEMO_FUTURES_API_SECRET (system-generated) or
BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE (self-generated Ed25519, a PEM file
readable by its owner only), and LIVE_SAFETY_HMAC_KEY. Rules:
docs/operations/PERP_DEMO_RUNNER.md.
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
from trading_agent.execution.perp.book import Book, PairStore, run_cycle  # noqa: E402
from trading_agent.execution.perp.lowvol import LowVolBook  # noqa: E402
from trading_agent.execution.perp.venue import (  # noqa: E402
    PerpDemoVenue,
    demo_config,
    load_demo_credentials,
)


def mainnet_listings() -> dict:
    """Public mainnet exchangeInfo through the NL-P allowlisted client."""

    from tools.newperp import fetch as nf

    return nf.Client().get(nf.EXCHANGE_INFO, {})


def sealed_check():
    """The selection run's mask: no LV leg may be one of the operator's masked coins."""

    from tools.selection.config import is_sealed

    return is_sealed


def parse_names(text: str) -> tuple[list[str], list[str]]:
    """``"LTC,XLM:SUI,WLD"``: list coins to hold long, then short."""

    longs, _, shorts = text.partition(":")
    split = lambda s: [c.strip().upper() for c in s.split(",") if c.strip()]  # noqa: E731
    return split(longs), split(shorts)


def write_status(root: Path, report: dict, pairs: PairStore, lv_blocks: PairStore | None = None) -> None:
    status = {
        "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "execute": report.get("execute"),
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
    args = ap.parse_args(argv)

    load_dotenv(ROOT / ".env")
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    audit = root / "audit.jsonl"
    if (args.rehearse or args.lv_rehearse) and not args.execute:
        print("--rehearse and --lv-rehearse need --execute", file=sys.stderr)
        return 2
    if args.lv_rehearse and not args.lv:
        print("--lv-rehearse needs --lv", file=sys.stderr)
        return 2
    info: dict | None = None
    if args.shadow:
        # Paper on public mainnet data; runs even when execution is switched off.
        try:
            from tools.newperp import fetch as nf
            from tools.newperp import shadow

            client = nf.Client()
            info = client.get(nf.EXCHANGE_INFO, {})
            counts = shadow.step(client, info, int(time.time() * 1000), root)
            print(json.dumps({"shadow": counts}, sort_keys=True))
        except Exception as exc:
            append_live_audit_event(audit, "perp_shadow_failed", {"error": type(exc).__name__, "detail": str(exc)[:300]})
            print(f"perp shadow failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    try:
        require_execution_authorization(execute=args.execute, testnet=True, cli_confirmation=None)
        key, secret = load_demo_credentials(os.environ) if args.execute else ("", "")
        integrity = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
        with LiveExecutionLock(root / "writer.lock"):
            venue = PerpDemoVenue(CCXTAdapter(demo_config(key, secret)))
            venue.connect()
            if args.execute:
                venue.require_one_way_mode()
                orders = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
                pairs = PairStore.load(root / "pairs.json", integrity)
                blocks = PairStore.load(root / "lv_blocks.json", integrity) if args.lv else None
            else:
                orders = LiveRiskStateStore(root / "order_state.json")  # read only, never saved
                pairs = PairStore.peek(root / "pairs.json")
                blocks = PairStore.peek(root / "lv_blocks.json") if args.lv else None
            book = Book(venue, orders, pairs, audit, args.pair_usd)
            lv = None
            if blocks is not None:
                picks = Path(args.selection_root) / "lv_book" / "picks.jsonl"
                lv = LowVolBook(book, blocks, args.lv_usd, picks, sealed_check())
            now_ms = int(time.time() * 1000)
            entry_lock = configured_entry_lock_reason()
            if args.execute:
                append_live_audit_event(audit, "perp_run_started", {
                    "pair_usd": args.pair_usd, "rehearse": args.rehearse or "",
                    "lv_usd": args.lv_usd if lv is not None else 0, "lv_rehearse": args.lv_rehearse or ""})
            report = run_cycle(
                book,
                now_ms=now_ms,
                mainnet_info=info if info is not None else mainnet_listings(),
                execute=args.execute,
                entry_lock=entry_lock,
                rehearse=args.rehearse,
                rehearse_hold_h=args.rehearse_hold_h,
                lv=lv,
            )
            if lv is not None and args.lv_rehearse:
                if entry_lock:
                    raise LiveSafetyError(f"no LV rehearsal under the entry lock: {entry_lock}")
                longs, shorts = parse_names(args.lv_rehearse)
                report["lv_rehearsal"] = lv.rehearse(longs, shorts, now_ms, args.lv_rehearse_hold_h)
            if args.execute:
                write_status(root, report, pairs, blocks)
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
