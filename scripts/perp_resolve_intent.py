"""Clear a perp intent parked in ``manual_intervention``: the operator's way out (PERP_LIVE_R0.md section 6).

When a perp run cannot find an intent at the venue by its client key, its reconcile parks the intent in
``manual_intervention`` and every later run of that root stops there, exits included. This command closes such an
intent as never submitted (``LiveRiskStateStore.resolve_unsubmitted_order``) only when the evidence rule holds:

- the ledger: status reserved, reconciling or manual_intervention; no venue order id, fill, cost or fee; reserved at
  least 10 minutes ago (``--min-age-minutes``, never less); not a stop (a key ending "-st" is settled by its book);
- the venue: no order with that client key, no open plain or conditional order with it, and no trade on the symbol
  since the reservation from an order the ledger does not know;
- the operator: ``--operator-checked-app`` states that the venue's app shows no order with that key either.

The default is a report. ``--apply`` closes the intent under the root's writer lock (no run can overlap) and writes
the evidence and the operator's statement to the root's audit log. It only reads the venue: the live venue is built
read-only, so this command never sends an order or a cancel. The report carries keys, statuses and counts only.

    .venv/bin/python scripts/perp_resolve_intent.py --venue demo --key krw-123-ABC-c0
    .venv/bin/python scripts/perp_resolve_intent.py --venue live --env-file ~/.perp_live.env \\
        --key krw-123-ABC-c0 --apply --operator-checked-app
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from trading_agent.execution.live_safety import (  # noqa: E402
    LiveExecutionLock,
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
)

MIN_AGE = timedelta(minutes=10)     # PERP_LIVE_R0.md section 6: "after at least 10 minutes"
LOOK_BACK_MS = 60_000               # trades are read from a minute before the reservation (clock skew)


class Venue(Protocol):
    def order_by_key(self, exchange_id: str, key: str) -> dict | None: ...
    def open_order_keys(self, exchange_id: str) -> set[str]: ...
    def open_stop_keys(self, exchange_id: str) -> set[str]: ...
    def trade_order_ids(self, exchange_id: str, since_ms: int, until_ms: int) -> list[str]: ...


def resolve(store: LiveRiskStateStore, venue: Venue, key: str, *, min_age: timedelta, operator_checked: bool,
            apply: bool, audit: Path, now: datetime | None = None) -> dict:
    """The report, and with ``apply`` the closed intent; refusals are listed, never raised."""

    current = now or datetime.now(UTC)
    rec = store.state.order_ledger.get(key)
    out: dict = {"key": key, "eligible": False, "applied": False, "already_resolved": False, "refusals": []}
    if not isinstance(rec, dict):
        out["refusals"].append("the key is not in this root's order ledger")
        return out
    out["status"] = str(rec.get("status") or "")
    if out["status"] == "rejected" and rec.get("resolution") == "operator_unsubmitted":
        out["already_resolved"] = True
        return out
    refusals: list[str] = list(store.unsubmitted_resolution_refusals(key))
    if key.endswith("-st"):
        refusals.append("a stop's intent is settled by its book's check, not here")
    symbol = str(rec.get("symbol") or "")
    out["symbol"] = symbol
    try:
        created = datetime.fromisoformat(str(rec.get("created_at")))
    except ValueError:
        created = None
        refusals.append("the ledger entry has no valid created_at")
    if min_age < MIN_AGE:
        refusals.append(f"--min-age-minutes is below the rule's {MIN_AGE.seconds // 60}")
    if created is not None and current - created < max(min_age, MIN_AGE):
        refusals.append(f"reserved {int((current - created).total_seconds() // 60)} min ago; the rule needs "
                        f"{int(max(min_age, MIN_AGE).total_seconds() // 60)}")
    evidence: dict = {}
    if symbol and created is not None:
        found = venue.order_by_key(symbol, key)
        with_key = int(key in venue.open_order_keys(symbol)) + int(key in venue.open_stop_keys(symbol))
        known = {str(r.get("exchange_order_id")) for r in store.state.order_ledger.values()
                 if isinstance(r, dict) and r.get("exchange_order_id")}
        since = int(created.timestamp() * 1000) - LOOK_BACK_MS
        until = int(current.timestamp() * 1000)
        unknown = sum(1 for oid in venue.trade_order_ids(symbol, since, until) if oid not in known)
        evidence = {
            "venue_lookup": "found" if found is not None else "not_found",
            "open_orders_with_client_id": with_key,
            "trades_since_reservation": unknown,
            "trades_counted": "trades of orders the ledger does not know",
            "checked_at": current.astimezone(UTC).isoformat(),
            "reserved_at": created.astimezone(UTC).isoformat(),
            "symbol": symbol,
            "operator_checked_app": bool(operator_checked),
        }
        if found is not None:
            refusals.append("the venue knows this client key: the next run's reconcile settles it")
        if with_key:
            refusals.append("an open order at the venue carries this client key")
        if unknown:
            refusals.append(f"{unknown} trade(s) on {symbol} since the reservation from orders the ledger does not "
                            "know")
        for r in store.unsubmitted_resolution_refusals(key, evidence):
            if r not in refusals:
                refusals.append(r)
    if apply and not operator_checked:
        refusals.append("--apply needs --operator-checked-app: the operator has looked for the key in the app")
    out.update(eligible=not refusals, refusals=refusals, evidence=evidence)
    if not apply or refusals:
        return out
    store.resolve_unsubmitted_order(key, evidence=evidence, now=current)
    append_live_audit_event(audit, "perp_intent_resolved", {"key": key, **evidence}, now=current)
    out["applied"] = True
    return out


def connect(kind: str, env_file: str | None):
    """The venue, read-only for live, and the root's integrity key. Nothing printed carries a key."""

    from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
    from trading_agent.execution.perp import venue as pv

    if kind == "live":
        from scripts.live_perp_r0 import read_live_env

        if not env_file:
            raise LiveSafetyError("--venue live needs --env-file")
        live = read_live_env(Path(env_file).expanduser())
        key, secret = pv.load_live_credentials(live)
        venue = pv.PerpLiveVenue(CCXTAdapter(pv.live_config(key, secret)), allow_orders=False)
        integrity = live.get("LIVE_SAFETY_HMAC_KEY", "")
    else:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        key, secret = pv.load_demo_credentials(os.environ)
        venue = pv.PerpDemoVenue(CCXTAdapter(pv.demo_config(key, secret)))
        integrity = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
    if not integrity:
        raise LiveSafetyError("LIVE_SAFETY_HMAC_KEY is missing")
    venue.connect()
    return venue, integrity


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--venue", choices=("demo", "live"), required=True)
    ap.add_argument("--key", required=True, help="the intent's client key, as the run's error names it")
    ap.add_argument("--root", default=None, help="the perp root (default: data/perp_demo or data/perp_live)")
    ap.add_argument("--env-file", default=None, help="live only: the live env file, mode 600")
    ap.add_argument("--min-age-minutes", type=float, default=MIN_AGE.seconds / 60)
    ap.add_argument("--operator-checked-app", action="store_true",
                    help="the operator states that the venue's app shows no order with this key")
    ap.add_argument("--apply", action="store_true", help="close the intent (default: report only)")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ROOT / "data" / ("perp_live" if args.venue == "live" else "perp_demo")
    try:
        if not (root / "order_state.json").is_file():
            raise LiveSafetyError(f"no order ledger under {root}")
        venue, integrity = connect(args.venue, args.env_file)
        with LiveExecutionLock(root / "writer.lock"):
            store = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
            report = resolve(store, venue, args.key, min_age=timedelta(minutes=args.min_age_minutes),
                             operator_checked=args.operator_checked_app, apply=args.apply,
                             audit=root / "audit.jsonl")
    except Exception as exc:
        print(f"perp resolve stopped: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=1, sort_keys=True))
    if report["applied"] or report["already_resolved"]:
        return 0
    return 0 if report["eligible"] and not args.apply else 2


if __name__ == "__main__":
    sys.exit(main())
