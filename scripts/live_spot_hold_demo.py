"""Book HOLD on Binance spot Demo Trading: one run.

Buys BTC once with --hold-usd USDT on the demo spot wallet and holds it.
Without --execute it connects to the demo venue's public markets and prints
what a run would do; nothing is signed or sent. With --execute it needs the
same switches and demo key as scripts/live_perp_demo.py (TRADING_MODE=testnet,
TRADING_EXECUTION_ENABLED=true, no TRADING_KILL_SWITCH, the Demo Trading key,
LIVE_SAFETY_HMAC_KEY). Rules: docs/operations/PERP_DEMO_RUNNER.md, "Book HOLD".
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
from trading_agent.execution.demo_spot.hold import HoldBook  # noqa: E402
from trading_agent.execution.demo_spot.venue import SpotDemoVenue, spot_demo_config  # noqa: E402
from trading_agent.execution.live_safety import (  # noqa: E402
    LiveExecutionLock,
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
    configured_entry_lock_reason,
    require_execution_authorization,
)
from trading_agent.execution.perp.book import PairStore  # noqa: E402
from trading_agent.execution.perp.venue import load_demo_credentials  # noqa: E402


def write_status(root: Path, report: dict, store: PairStore) -> None:
    h = store.pairs.get("btc") or {}
    status = {
        "as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
        "status": h.get("status", "none"),
        "held_since": h.get("held_since"),
        "bought_this_run": bool(report.get("bought")),
    }
    tmp = root / ".status.json.tmp"
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, root / "status.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true", help="sign and send orders on the demo venue")
    ap.add_argument("--root", default=str(ROOT / "data" / "spot_hold_demo"))
    ap.add_argument("--hold-usd", type=float, default=2500.0, help="USDT spent on BTC, once")
    args = ap.parse_args(argv)

    load_dotenv(ROOT / ".env")
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    audit = root / "audit.jsonl"
    try:
        require_execution_authorization(execute=args.execute, testnet=True, cli_confirmation=None)
        key, secret = load_demo_credentials(os.environ) if args.execute else ("", "")
        integrity = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
        with LiveExecutionLock(root / "writer.lock"):
            venue = SpotDemoVenue(CCXTAdapter(spot_demo_config(key, secret)))
            venue.connect()
            if args.execute:
                orders = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
                store = PairStore.load(root / "hold.json", integrity)
                append_live_audit_event(audit, "spot_hold_run_started", {"hold_usd": args.hold_usd})
            else:
                orders = LiveRiskStateStore(root / "order_state.json")  # read only, never saved
                store = PairStore.peek(root / "hold.json")
            book = HoldBook(venue, orders, store, audit, args.hold_usd)
            report = book.step(execute=args.execute, entry_lock=configured_entry_lock_reason())
            report["execute"] = args.execute
            if args.execute:
                write_status(root, report, store)
                append_live_audit_event(audit, "spot_hold_run_finished", report)
    except Exception as exc:  # every stop is audited; the next run reconciles first
        if args.execute:
            append_live_audit_event(audit, "spot_hold_run_failed", {"error": type(exc).__name__, "detail": str(exc)[:300]})
        kind = "stopped" if isinstance(exc, LiveSafetyError) else "failed"
        print(f"spot hold demo run {kind}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
