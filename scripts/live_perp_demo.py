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
from trading_agent.execution.perp.venue import (  # noqa: E402
    PerpDemoVenue,
    demo_config,
    load_demo_credentials,
)


def mainnet_listings() -> dict:
    """Public mainnet exchangeInfo through the NL-P allowlisted client."""

    from tools.newperp import fetch as nf

    return nf.Client().get(nf.EXCHANGE_INFO, {})


def write_status(root: Path, report: dict, pairs: PairStore) -> None:
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
    args = ap.parse_args(argv)

    load_dotenv(ROOT / ".env")
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    audit = root / "audit.jsonl"
    if args.rehearse and not args.execute:
        print("--rehearse needs --execute", file=sys.stderr)
        return 2
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
            else:
                orders = LiveRiskStateStore(root / "order_state.json")  # read only, never saved
                pairs = PairStore.peek(root / "pairs.json")
            book = Book(venue, orders, pairs, audit, args.pair_usd)
            now_ms = int(time.time() * 1000)
            if args.execute:
                append_live_audit_event(audit, "perp_run_started", {"pair_usd": args.pair_usd, "rehearse": args.rehearse or ""})
            report = run_cycle(
                book,
                now_ms=now_ms,
                mainnet_info=mainnet_listings(),
                execute=args.execute,
                entry_lock=configured_entry_lock_reason(),
                rehearse=args.rehearse,
                rehearse_hold_h=args.rehearse_hold_h,
            )
            if args.execute:
                write_status(root, report, pairs)
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
