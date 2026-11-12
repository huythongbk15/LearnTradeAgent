"""Perp tier R0: book KRW alone on Binance USD-M mainnet, one run.

Rules: docs/operations/PERP_LIVE_R0.md (the operator's ruling). This is the only entrypoint that builds
``PerpLiveVenue``. It runs book KRW (H-KRW-FADE-16) and nothing else, one event at a time, in its own root
(``data/perp_live/`` by default), with its own lock, env file, credentials and kill switches. It never reads the
repo's ``.env``.

Each run first asks, without any key, whether a signed run is needed: book KRW's own reasons (an event inside its
entry window, an exit due, an order left unsettled; ``krwfade.signed_run_reason``), else a signed check at least every
hour while the record may hold legs (a fired stop is seen by a signed run only) and at least once a day while flat (a
revoked key, a foreign position or a wrong account mode is found before an entry window). Otherwise it ends there,
unsigned.

A signed run (``--execute``) needs, in the live env file (``--env-file``, a file owned by the user running this and
readable by that user only): TRADING_MODE=live, TRADING_EXECUTION_ENABLED=true,
TRADING_LIVE_CONFIRMATION=LIVE_TRADING_WITH_REAL_MONEY, no TRADING_KILL_SWITCH, LIVE_SAFETY_HMAC_KEY,
BINANCE_LIVE_FUTURES_API_KEY, BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE (Ed25519) and PERP_LIVE_KRW_USD; and on the
command line ``--confirm-live LIVE_TRADING_WITH_REAL_MONEY``. A kill switch set in the process environment stops it
too. Orders are allowed only with PERP_LIVE_ALLOW_ORDERS=true in the env file; without it (stage B's read-only soak)
the venue refuses every write and the run holds entries under the entry lock.

--resume, by hand only (the live wrapper never passes it): a signed run whatever the pre-check says, which starts
book KRW's new sleeve after its K3 halt or a new size, if the book is flat. It is the operator's signature of K-pause.

--exit-now, by hand only (K-ops of PERP_LIVE_R0.md): a signed run whatever the pre-check says, in which book KRW
exits every open event the way a due exit does (``close_reason`` "operator") and enters nothing.

Floor (D5, section 3 and K-budget of section 7): every signed run reads the sub-account's equity and holds entries
when it is below 2.5 x PERP_LIVE_KRW_USD (the room one event needs) or below half the equity recorded when stage D
began. With PERP_LIVE_STAGE=D and orders allowed, the first signed run records that starting equity in its signed
record (``floor.json``). Exits always run. The run reports ``ok`` or ``below_floor``, never the amount.

Alerts (item 5): every run that finds the live root writes its outcome record (``last_run.json``: rc, signed or not,
the reason, a named skip, the error's class and kind); the wrapper then runs ``scripts/perp_live_alert.py``. A 429 or
418 on Upbit's notice list or Binance's public API, or a 403 from Upbit, is a named skip with rc 0 (``upbit_429``,
``upbit_403``, ``binance_429``), never a failure. With ``--notice-share`` (the wrapper passes it) the run writes the
Upbit page it read to that file for the Demo roots (``tools/krwfade/feed.py`` "The shared page"); it never reads one.

Nothing here logs an amount: the size, the equity and any transfer stay in the env file, the signed records and the
operator's notes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from dotenv import dotenv_values  # noqa: E402

from scripts.live_perp_demo import krw_reason, krw_sources, now_ms, skip_name  # noqa: E402
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter  # noqa: E402
from trading_agent.execution.live_safety import (  # noqa: E402
    LiveExecutionLock,
    LiveRiskStateStore,
    LiveSafetyError,
    append_live_audit_event,
    configured_entry_lock_reason,
    require_execution_authorization,
)
from trading_agent.execution.perp import alerts, krwfade  # noqa: E402
from trading_agent.execution.perp.book import Book, PairStore, run_cycle  # noqa: E402
from trading_agent.execution.perp.venue import (  # noqa: E402
    PerpLiveVenue,
    live_config,
    load_live_credentials,
)

KRW_STORE = krwfade.STORE
LIVE_MAX_OPEN = 1                       # one event at a time (PERP_LIVE_R0.md section 3)
CHECK_OPEN_MS = 60 * 60 * 1000          # a signed check at least hourly while the record may hold legs
CHECK_FLAT_MS = 24 * 60 * 60 * 1000     # and at least daily while flat
LAST_SIGNED = "last_signed.json"        # {"at_ms": ...}: the end of the last signed run that finished rc 0
KILL_NAMES = ("TRADING_KILL_SWITCH", "TRADING_ENTRY_KILL_SWITCH")
FLOOR_STORE = "floor.json"              # signed: the equity when stage D began
EVENT_ROOM = 2.5                        # x PERP_LIVE_KRW_USD: the equity one event needs (risk.py's alt-short cap)
K_BUDGET = 0.5                          # entries stop below half the equity at the start of stage D


def read_live_env(path: Path) -> dict[str, str]:
    """The live env file: a regular file owned by the user running this, readable by that user only."""

    if path.is_symlink() or not path.is_file():
        raise LiveSafetyError("the live env file is not a regular file")
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise LiveSafetyError("the live env file must be owned by the user running this and mode 600")
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def gate_env(live: Mapping[str, str]) -> dict[str, str]:
    """The env the gates read: the live file, with a kill switch set in the process environment winning."""

    env = dict(live)
    for name in KILL_NAMES:
        if (os.environ.get(name) or "").strip().lower() == "true":
            env[name] = "true"
    return env


def krw_usd(live: Mapping[str, str]) -> float:
    try:
        v = float(live.get("PERP_LIVE_KRW_USD", ""))
    except ValueError as exc:
        raise LiveSafetyError("PERP_LIVE_KRW_USD is not a number") from exc
    if not 0 < v <= krwfade.USD_CAP:
        raise LiveSafetyError(f"PERP_LIVE_KRW_USD must be in (0, {krwfade.USD_CAP:g}]")
    return v


def last_signed_ms(root: Path) -> int | None:
    try:
        return int(json.loads((root / LAST_SIGNED).read_text(encoding="utf-8"))["at_ms"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check_reason(root: Path, at_ms: int) -> str | None:
    """A scheduled signed check: hourly while the record may hold legs, daily while flat, and on a first run."""

    last = last_signed_ms(root)
    if last is None:
        return "first_check"
    store = PairStore.peek(root / KRW_STORE)
    if krwfade.holds_legs(store):
        return "hourly_check" if at_ms - last >= CHECK_OPEN_MS else None
    return "daily_check" if at_ms - last >= CHECK_FLAT_MS else None


def mark_signed(root: Path, at_ms: int) -> None:
    tmp = root / f".{LAST_SIGNED}.tmp"
    tmp.write_text(json.dumps({"at_ms": at_ms}), encoding="utf-8")
    os.replace(tmp, root / LAST_SIGNED)


def floor_check(venue, store: PairStore, usd: float, *, stage_d: bool, at_ms: int, audit: Path) -> str:
    """``ok`` or ``below_floor`` (D5). Records the starting equity once, at the first signed run of stage D."""

    equity = venue.equity_usdt()
    start = store.pairs.get("start")
    if start is None and stage_d:
        start = {"equity": equity, "at_ms": at_ms}
        store.pairs["start"] = start
        store.save()
        append_live_audit_event(audit, "perp_live_budget_started", {"at_ms": at_ms})
    floor = EVENT_ROOM * usd
    if start is not None:
        floor = max(floor, K_BUDGET * float(start["equity"]))
    return "ok" if equity >= floor else "below_floor"


def write_status(root: Path, report: dict, krw: krwfade.KrwFadeBook, allow_orders: bool) -> None:
    """Counts and statuses only, never an amount."""

    status = {"as_of": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()), "allow_orders": allow_orders,
              "reason": report.get("only_reason"), "entry_lock": bool(report.get("entry_lock")),
              "floor": report.get("floor"), **krw.counts()}
    tmp = root / ".status.json.tmp"
    tmp.write_text(json.dumps(status, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, root / "status.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true", help="a signed run on mainnet when one is needed")
    ap.add_argument("--confirm-live", default=None, help="the live confirmation phrase (PERP_LIVE_R0.md)")
    ap.add_argument("--env-file", required=True, help="the live env file, outside the repo, mode 600")
    ap.add_argument("--root", default=str(ROOT / "data" / "perp_live"))
    ap.add_argument("--resume", action="store_true",
                    help="by hand only: a signed run that starts book KRW's new sleeve after its halt, if it is flat")
    ap.add_argument("--notice-share", default=None,
                    help="write the Upbit page this run read here, for the Demo roots (a directory this user owns)")
    ap.add_argument("--exit-now", action="store_true",
                    help="by hand only: a signed run in which book KRW exits every open event now and enters nothing")
    args = ap.parse_args(argv)
    out: dict = {}
    rc = run(args, out)
    # The outcome record the alert script reads after the wrapper's run (PERP_LIVE_R0.md item 5): names only.
    alerts.write_outcome(Path(args.root), rc=rc, **out)
    return rc


def run(args: argparse.Namespace, out: dict) -> int:
    """One run. ``out`` gets the outcome record's fields: signed, reason, skipped, error (a class name), kind."""

    root = Path(args.root)
    out["signed"] = False
    if (args.resume or args.exit_now) and not args.execute:
        print("--resume and --exit-now need --execute", file=sys.stderr)
        return 2
    if args.resume and args.exit_now:
        print("--resume and --exit-now are separate runs", file=sys.stderr)
        return 2

    box: dict = {"info": None, "notices": None}
    sources = krw_sources(box, share_write=Path(args.notice_share) if args.notice_share else None)
    reason: str | None = "resume" if args.resume else "exit_now" if args.exit_now else None
    if reason is None:
        # Before the env file, the key, the lock or any write but the outcome record: a quiet moment ends here,
        # unsigned.
        at = now_ms()
        try:
            reason = krw_reason(root, at, sources)
        except Exception as exc:
            skip = skip_name(exc)
            if skip is None:
                out.update(error=type(exc).__name__, kind="precheck")
                print(f"krw pre-check failed: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
                return 1
            # Upbit or Binance said "not now": a named skip, rc 0. The record's and the ledger's reasons come first.
            reason = None
            skipped = f"{type(exc).__name__}: {str(exc)[:120]}"
            out["skipped"] = skip
        else:
            skipped = None
        reason = reason or check_reason(root, at)
        if reason is None:
            line = {"signed_run": False}
            if skipped:
                line.update(skipped=out["skipped"], error=skipped)
            print(json.dumps(line))
            return 0
        if not args.execute:
            print(json.dumps({"signed_run": False, "would_sign": reason}))
            return 0
    out.update(signed=True, reason=reason)

    audit = root / "audit.jsonl"
    try:
        live = read_live_env(Path(args.env_file).expanduser())
        env = gate_env(live)
        require_execution_authorization(execute=True, testnet=False, cli_confirmation=args.confirm_live, env=env)
        allow_orders = (live.get("PERP_LIVE_ALLOW_ORDERS") or "").strip().lower() == "true"
        usd = krw_usd(live)
        key, secret = load_live_credentials(live)
        integrity = live.get("LIVE_SAFETY_HMAC_KEY", "")
        if not integrity:
            raise LiveSafetyError("LIVE_SAFETY_HMAC_KEY is missing from the live env file")
        root.mkdir(parents=True, exist_ok=True)
        with LiveExecutionLock(root / "writer.lock"):
            venue = PerpLiveVenue(CCXTAdapter(live_config(key, secret)), allow_orders=allow_orders)
            venue.connect()
            venue.require_account_shape()
            orders = LiveRiskStateStore(root / "order_state.json", integrity_key=integrity)
            pairs = PairStore.load(root / "pairs.json", integrity)
            book = Book(venue, orders, pairs, audit, usd)
            krw = krwfade.KrwFadeBook(book, PairStore.load(root / KRW_STORE, integrity), usd, *sources,
                                      resume=args.resume, max_open=LIVE_MAX_OPEN, exit_now=args.exit_now)
            at = now_ms()
            stage_d = allow_orders and (live.get("PERP_LIVE_STAGE") or "").strip().upper() == "D"
            floor = floor_check(venue, PairStore.load(root / FLOOR_STORE, integrity), usd, stage_d=stage_d,
                                at_ms=at, audit=audit)
            entry_lock = configured_entry_lock_reason(env)
            if args.exit_now:
                entry_lock = entry_lock or "exit_now: the operator's exit, no entry in this run"
            if not allow_orders:
                entry_lock = entry_lock or "read-only: PERP_LIVE_ALLOW_ORDERS is not true"
            if floor != "ok":
                entry_lock = entry_lock or "below_floor: the sub-account's equity is under the D5 floor"
            append_live_audit_event(audit, "perp_live_run_started", {"reason": reason, "allow_orders": allow_orders,
                                                                    "entry_lock": bool(entry_lock), "floor": floor})
            report = run_cycle(book, now_ms=at, mainnet_info=None, execute=True, entry_lock=entry_lock,
                               others=[krw], only=krw.name)
            report.update(only_reason=reason, floor=floor)
            write_status(root, report, krw, allow_orders)
            append_live_audit_event(audit, "perp_live_run_finished", {k: v for k, v in report.items() if k != "skipped"})
            mark_signed(root, now_ms())
    except Exception as exc:  # every stop is audited when the root exists; the next run reconciles first
        if root.is_dir():
            append_live_audit_event(audit, "perp_live_run_failed", {"error": type(exc).__name__,
                                                                    "detail": str(exc)[:300]})
        out.update(error=type(exc).__name__, kind=alerts.kind_of(exc))
        kind = "stopped" if isinstance(exc, LiveSafetyError) else "failed"
        print(f"perp live run {kind}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
