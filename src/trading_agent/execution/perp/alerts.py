"""Alerts that leave the PC for perp tier R0 (docs/operations/PERP_LIVE_R0.md, section 5 item 5).

After every run the live wrapper (``scripts/ops/run_perp_live.sh``) calls ``scripts/perp_live_alert.py`` with the
run's rc. ``decide`` takes that rc, the run's own outcome record (``last_run.json``, written by
``scripts/live_perp_r0.py`` through ``write_outcome``), ``status.json``, the order ledger and the alert state of
earlier runs, and returns the messages for the operator's Telegram and whether the dead-man check is pinged.

- **Dead-man ping:** after a run that ended rc 0, and only then. A run the wrapper's lock skipped never reaches the
  alert script, so it pings nothing; the outside check pages the phone when the pings stop.
- **Messages,** counts and statuses only, never an amount, a key, a client order id, an account id or an error's
  text (its text can hold quantities):
  - a run that failed (rc not 0), with the error's class and a ``kind``; sent again at most hourly while the same
    failure goes on, and one "ok again" when a run ends rc 0 after it;
  - a fired stop (K-pause), the book's halt and its end (K3), the D5 floor holding entries and its end, and orders
    the ledger has not settled;
  - a named skip of the notice list (``upbit_429``, ``upbit_403``, ``binance_429``) going on for
    ``SKIP_ALERT_MS``: the book does not see new listings while it lasts.
- A message Telegram did not take is kept (at most ``MAX_PENDING``) and sent again after the next run.

Nothing here reaches the network: the alert script sends what ``decide`` returns.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

OUTCOME = "last_run.json"              # the run's own outcome record, in the live root
STATE = "alerts.json"                  # the alert state of earlier runs, in the live root
REPEAT_MS = 60 * 60 * 1000             # the same failure or skip is sent again at most hourly
SKIP_ALERT_MS = 30 * 60 * 1000         # a named skip going on this long is sent
MAX_PENDING = 20                       # unsent messages kept for the next run
PREFIX = "R0 live"
SKIPS = ("upbit_429", "upbit_403", "binance_429")
# Ledger statuses that leave an order unsettled after a run has ended. An open exchange stop ("-st",
# acknowledged or open) is the book's normal state while an event is open and is not counted.
UNSETTLED = ("reserved", "submitted", "acknowledged", "open", "partial", "reconciling", "unknown",
             "manual_intervention")
STOP_UNSETTLED = ("submitted", "reconciling", "unknown", "manual_intervention")
# The kind of a failure, from its class and phrases of the perp book's own errors (checked in order). The text is
# read here only; it never leaves in a message.
KIND_PHRASES = (
    ("store", ("integrity check failed",)),
    ("position", ("do not match the book", "differs from", " is held by ", "negative", "short is gone",
                  "has no open stop", "more bought back than sold")),
    ("order", ("outcome unknown", "not at the venue", "operator review required", " still ", "not filled",
               " ended ", "filled without a price", "could not be cancelled", " refused ", " not_sent")),
    ("account", ("hedge mode", "multi-assets mode", "fees are paid", "futures wallet", "cross margin")),
    ("venue", ("host", "proxy", "read-only", "not connected", "is not Binance", "requires the binanceusdm")),
)


def write_outcome(root: Path, **fields: Any) -> None:
    """Write the run's outcome record, atomically, when the live root exists (a run before the first signed run
    has none). Counts, names and statuses only."""

    if not root.is_dir():
        return
    rec = {"at_ms": int(time.time() * 1000), **fields}
    tmp = root / f".{OUTCOME}.tmp"
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    os.replace(tmp, root / OUTCOME)


def read_json(path: Path) -> dict | None:
    """A JSON object from ``path``, or None when it is missing or not one."""

    try:
        out = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def kind_of(exc: BaseException) -> str:
    """A short name for what failed: gate, network, store, position, order, account, venue or other."""

    names = {c.__name__ for c in type(exc).__mro__}
    if type(exc).__name__ == "LiveSafetyError":     # the gates themselves; the perp book's errors subclass it
        return "gate"
    if "NetworkError" in names or isinstance(exc, (TimeoutError, ConnectionError)):
        return "network"
    text = str(exc)
    for kind, phrases in KIND_PHRASES:
        if any(p in text for p in phrases):
            return kind
    return "other"


def unsettled_orders(ledger: Mapping[str, Mapping]) -> int:
    """How many orders of the ledger a finished run has left unsettled (an open exchange stop aside)."""

    n = 0
    for key, rec in ledger.items():
        status = str((rec or {}).get("status") or "")
        if status in (STOP_UNSETTLED if str(key).endswith("-st") else UNSETTLED):
            n += 1
    return n


def _hm(ms: int) -> str:
    return time.strftime("%m-%d %H:%MZ", time.gmtime(int(ms) / 1000))


def decide(state: Mapping[str, Any], *, rc: int, outcome: Mapping[str, Any] | None,
           status: Mapping[str, Any] | None, unsettled: int, now_ms: int) -> tuple[list[str], bool, dict]:
    """(messages, ping, the new state). ``outcome`` is the run's own record, None when it wrote none."""

    st: dict[str, Any] = json.loads(json.dumps(dict(state)))     # a copy; the caller saves it
    out = outcome or {}
    msgs: list[str] = []

    if rc != 0:
        what = f"{out.get('kind') or 'unknown'} {out.get('error') or '(no outcome record)'}"
        fail = st.get("fail")
        if not fail or fail.get("what") != what or now_ms - int(fail.get("sent_ms") or 0) >= REPEAT_MS:
            since = int(fail["since_ms"]) if fail else now_ms
            tail = f", failing since {_hm(since)}" if fail else ""
            msgs.append(f"run failed rc={rc}: {what}{tail}")
            st["fail"] = {"what": what, "since_ms": since, "sent_ms": now_ms}
        ping = False
    else:
        fail = st.pop("fail", None)
        if fail:
            msgs.append(f"runs end rc 0 again (failing since {_hm(int(fail['since_ms']))})")
        ping = True

    skip = out.get("skipped") if rc == 0 else None
    if skip in SKIPS:
        sk = st.get("skip") or {"since_ms": now_ms, "sent_ms": None}
        sk["name"] = skip
        lasted = now_ms - int(sk["since_ms"])
        if lasted >= SKIP_ALERT_MS and (sk["sent_ms"] is None or now_ms - int(sk["sent_ms"]) >= REPEAT_MS):
            msgs.append(f"notice list skipped for {lasted // 60_000} min (now {skip}): new listings are not seen")
            sk["sent_ms"] = now_ms
        st["skip"] = sk
    elif rc == 0:
        sk = st.pop("skip", None)
        if sk and sk.get("sent_ms") is not None:
            msgs.append("notice list read again")

    if status:
        stops = int(status.get("krw_stops_fired") or 0)
        seen = int(st.get("stops") or 0)
        if stops > seen:
            msgs.append(f"stop fired: {stops - seen} new, {stops} in all. K-pause: no entry until you sign")
        st["stops"] = stops
        halted = bool(status.get("krw_halted"))
        if halted != bool(st.get("halted")):
            msgs.append("book KRW halted (K3): entries stop until a --resume run" if halted
                        else "book KRW no longer halted")
        st["halted"] = halted
        floor = status.get("floor")
        if floor in ("ok", "below_floor"):
            if floor == "below_floor" and st.get("floor") != "below_floor":
                msgs.append("equity below the D5 floor: entries held, exits run")
            elif floor == "ok" and st.get("floor") == "below_floor":
                msgs.append("equity back above the D5 floor")
            st["floor"] = floor

    before = int(st.get("unsettled") or 0)
    if unsettled > 0 and unsettled != before:
        msgs.append(f"orders not settled in the ledger: {unsettled}")
    elif unsettled == 0 and before > 0:
        msgs.append("every order in the ledger is settled")
    st["unsettled"] = unsettled

    return [f"{PREFIX}: {m}" for m in msgs], ping, st


def queue(state: dict[str, Any], msgs: list[str]) -> list[str]:
    """The messages to send now: those kept from earlier runs, then the new ones, at most ``MAX_PENDING`` (the
    newest kept)."""

    return (list(state.get("pending") or []) + msgs)[-MAX_PENDING:]
