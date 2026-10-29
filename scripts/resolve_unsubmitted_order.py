#!/usr/bin/env python3
"""Operator recovery for a live intent that never reached the venue.

If the runner dies after reserving an intent but before sending it (crash,
kill, power loss), reconciliation finds nothing at the venue and parks the
ledger entry in ``manual_intervention``. That blocks every later batch, and
the ledger has no transition out of it, because the venue has nothing to
confirm. This tool is the supported way out.

It only closes an intent when every check passes:

* ledger: status reserved/reconciling/manual_intervention, no exchange order
  ID, no fills, no trades, older than ``--min-age-minutes``;
* lifecycle: no event from broker IO onwards (io_started, submitted, ack,
  fills, cancel, reconciled);
* venue: lookup by client order ID returns "does not exist", no open order
  carries that ID, and the symbol has no trades at all since the reservation.

The default is a report only. ``--apply`` holds the account session locks
(so no runner can run concurrently), rejects the canonical intent, which
releases its sell reservation, releases its submission claim, closes the
ledger entry with the evidence attached, and appends an audit event.
Re-running after success changes nothing.

    .venv/bin/python scripts/resolve_unsubmitted_order.py --testnet \\
        --state-file data/binance_testnet_risk_state_v2.json --order-key lta-...
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.execution.lifecycle.events import ExecutionEventType  # noqa: E402
from trading_agent.execution.lifecycle.lifecycle import (  # noqa: E402
    ExecutionLifecycle,
    IntentStatus,
)
from trading_agent.execution.lifecycle.store import ExecutionEventStore  # noqa: E402
from trading_agent.execution.live_safety import (  # noqa: E402
    UNSUBMITTED_RECOVERABLE_STATUSES,
    LiveAccountSession,
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
)

# Any of these means the order may have reached the venue.
SUBMISSION_EVIDENCE_EVENTS = frozenset(
    {
        ExecutionEventType.BROKER_IO_STARTED,
        ExecutionEventType.ORDER_SUBMITTED,
        ExecutionEventType.BROKER_ACKNOWLEDGED,
        ExecutionEventType.BROKER_STATE_RECONCILED,
        ExecutionEventType.PARTIAL_FILL_RECEIVED,
        ExecutionEventType.FILL_RECEIVED,
        ExecutionEventType.FEE_BOOKED,
        ExecutionEventType.CANCEL_REQUESTED,
        ExecutionEventType.CANCEL_CONFIRMED,
    }
)


class VenueProbe(Protocol):
    def client_order_exists(self, symbol: str, client_order_id: str) -> bool: ...

    def open_orders_with_client_id(self, symbol: str, client_order_id: str) -> int: ...

    def trades_since(self, symbol: str, since: datetime) -> int: ...


class CcxtVenueProbe:
    """Read-only venue checks through a ccxt Binance client."""

    def __init__(self, client) -> None:
        self.client = client

    def client_order_exists(self, symbol: str, client_order_id: str) -> bool:
        import ccxt

        try:
            self.client.private_get_order(
                {"symbol": symbol.replace("/", ""), "origClientOrderId": client_order_id}
            )
        except ccxt.OrderNotFound:
            return False
        return True

    def open_orders_with_client_id(self, symbol: str, client_order_id: str) -> int:
        return sum(
            1
            for order in self.client.fetch_open_orders(symbol)
            if order.get("clientOrderId") == client_order_id
        )

    def trades_since(self, symbol: str, since: datetime) -> int:
        return len(self.client.fetch_my_trades(symbol, since=int(since.timestamp() * 1000)))


@dataclass
class Report:
    order_key: str
    eligible: bool
    applied: bool = False
    already_resolved: bool = False
    reasons: tuple[str, ...] = ()
    evidence: dict | None = None

    def as_dict(self) -> dict:
        return {
            "order_key": self.order_key,
            "eligible": self.eligible,
            "applied": self.applied,
            "already_resolved": self.already_resolved,
            "refusals": list(self.reasons),
            "evidence": self.evidence or {},
        }


def resolve(
    *,
    store: LiveRiskStateStore,
    event_store_path: Path | None,
    order_key: str,
    venue: VenueProbe,
    min_age: timedelta,
    apply: bool,
    audit_log: str | None = None,
    now: datetime | None = None,
) -> Report:
    current = now or datetime.now(UTC)
    record = store.state.order_ledger.get(order_key)
    if not isinstance(record, dict):
        return Report(order_key, False, reasons=("order key is not in the ledger",))
    if (
        str(record.get("status")) == "rejected"
        and record.get("resolution") == "operator_unsubmitted"
    ):
        return Report(order_key, False, already_resolved=True)

    refusals: list[str] = []
    status = str(record.get("status") or "reserved").lower()
    if status not in UNSUBMITTED_RECOVERABLE_STATUSES:
        refusals.append(f"ledger status is {status}")
    if record.get("exchange_order_id") or record.get("trade_ids"):
        refusals.append("ledger carries an exchange order ID or trade IDs")
    if float(str(record.get("filled_quantity") or 0.0)) > 0:
        refusals.append("ledger records fills")
    symbol = str(record.get("symbol") or "")
    if not symbol:
        refusals.append("ledger entry has no symbol")
    try:
        created = datetime.fromisoformat(str(record.get("created_at")))
    except ValueError:
        created = None
        refusals.append("ledger entry has no valid created_at")
    if created is not None and current - created < min_age:
        refusals.append(f"reserved {current - created} ago; minimum age is {min_age}")

    intent_id = str(record.get("canonical_intent_id") or order_key)
    lifecycle_events: list[str] = []
    if event_store_path is not None and Path(event_store_path).is_file():
        events = ExecutionEventStore(str(event_store_path)).connect().read_events(intent_id)
        lifecycle_events = [event.event_type.value for event in events]
        seen = {event.event_type for event in events} & SUBMISSION_EVIDENCE_EVENTS
        if seen:
            refusals.append(
                "lifecycle shows submission activity: "
                + ", ".join(sorted(event.value for event in seen))
            )

    evidence: dict = {"lifecycle_events": lifecycle_events}
    if symbol and created is not None:
        exists = venue.client_order_exists(symbol, order_key)
        open_count = venue.open_orders_with_client_id(symbol, order_key)
        trades = venue.trades_since(symbol, created - timedelta(minutes=1))
        evidence.update(
            venue_lookup="found" if exists else "not_found",
            open_orders_with_client_id=open_count,
            trades_since_reservation=trades,
            checked_at=current.astimezone(UTC).isoformat(),
            symbol=symbol,
            reserved_at=created.isoformat(),
        )
        if exists:
            refusals.append("venue knows this client order ID")
        if open_count:
            refusals.append("an open venue order carries this client order ID")
        if trades:
            refusals.append(f"{trades} trade(s) on {symbol} since the reservation")

    report = Report(order_key, not refusals, reasons=tuple(refusals), evidence=evidence)
    if not apply or refusals:
        return report

    if event_store_path is not None and Path(event_store_path).is_file():
        event_store = ExecutionEventStore(str(event_store_path)).connect()
        lifecycle = ExecutionLifecycle(event_store)
        lifecycle.load()
        order = lifecycle.state.order(intent_id)
        if order is not None and order.status != IntentStatus.REJECTED:
            lifecycle.reject_order(
                intent_id, reason="operator recovery: intent never reached the venue"
            )
        if event_store.submission_claim(intent_id) is not None:
            event_store.release_submission_claim(intent_id)
    store.resolve_unsubmitted_order(order_key, evidence=evidence, now=current)
    if audit_log:
        append_live_audit_event(
            audit_log,
            "unsubmitted_intent_resolved",
            {"order_key": order_key, "canonical_intent_id": intent_id, **evidence},
            now=current,
        )
    report.applied = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--order-key", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--min-age-minutes", type=float, default=30.0)
    parser.add_argument("--audit-log", default="data/execution/binance_live_audit.jsonl")
    parser.add_argument("--apply", action="store_true", help="close the intent (default: report only)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    import ccxt

    load_dotenv(ROOT / ".env")
    prefix = "BINANCE_TESTNET_" if args.testnet else "BINANCE_"
    client = ccxt.binance(
        {"apiKey": os.getenv(prefix + "API_KEY", ""), "secret": os.getenv(prefix + "API_SECRET", "")}
    )
    if args.testnet:
        client.set_sandbox_mode(True)
    integrity_key = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
    try:
        with LiveAccountSession(
            account_id=os.getenv("LIVE_ACCOUNT_ID", ""),
            exchange="binance-testnet" if args.testnet else "binance-mainnet",
            state_path=args.state_file,
            integrity_key=integrity_key,
            root=ROOT / "data" / "execution" / "accounts",
        ) as session:
            report = resolve(
                store=LiveRiskStateStore(args.state_file, integrity_key=integrity_key),
                event_store_path=session.event_store_path,
                order_key=args.order_key,
                venue=CcxtVenueProbe(client),
                min_age=timedelta(minutes=args.min_age_minutes),
                apply=args.apply,
                audit_log=args.audit_log,
            )
    except LiveSafetyError as exc:
        print(f"FATAL: {exc}")
        return 1
    print(json.dumps(report.as_dict(), indent=2, default=str))
    if report.already_resolved or report.applied:
        return 0
    return 0 if (report.eligible and not args.apply) else 2


if __name__ == "__main__":
    raise SystemExit(main())
