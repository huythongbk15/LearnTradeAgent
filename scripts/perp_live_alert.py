"""Perp tier R0: the alerts that leave the PC after one run (docs/operations/PERP_LIVE_R0.md, section 5 item 5).

``scripts/ops/run_perp_live.sh`` runs this after every run it starts, with that run's rc and start time; a run its
lock skipped never gets here, so a lock skip pings nothing:

    perp_live_alert.py --env-file ~/.perp_live.env --rc 0 --since-ms 1762934400000

From the live env file (the runner's, owned by the live user, mode 600) it reads:

- PERP_LIVE_TELEGRAM_BOT_TOKEN and PERP_LIVE_TELEGRAM_CHAT_ID: the bot the operator made (D14);
- PERP_LIVE_DEADMAN_URL: the outside check's ping URL, https only, pinged after a run that ended rc 0.

What to send is decided by ``trading_agent.execution.perp.alerts`` from the rc, the run's outcome record
(``last_run.json``, used only when written after ``--since-ms``), ``status.json``, the order ledger and the alert
state of earlier runs (``alerts.json``). Messages carry counts and statuses only, and are also printed to the log.
A message Telegram did not take is kept for the next run. Without the Telegram names the messages go to the log only.

It asks nothing of Binance, prints no URL and no token, and never changes the run's rc: the wrapper exits with the
run's. Its own rc is 1 when the env file cannot be read or a send or the ping failed, else 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from scripts.live_perp_r0 import read_live_env  # noqa: E402
from trading_agent.execution.perp import alerts, krwfade  # noqa: E402

TIMEOUT_S = 10.0
TELEGRAM = "https://api.telegram.org/bot{token}/sendMessage"
TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

# (method, url, JSON body or None) -> HTTP status. Replaced in tests, so no test reaches the network.
Http = Callable[[str, str, dict | None], int]


def http(method: str, url: str, body: dict | None) -> int:
    """One request, no proxy; the status, or 0 when none came back."""

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=TIMEOUT_S) as r:
            return int(r.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:              # network or timeout; its text can hold the URL, so it is not printed
        return 0


def send_all(msgs: list[str], token: str, chat: str, send: Http) -> int:
    """Send in order; stop at the first that Telegram does not take. Returns how many were taken."""

    url = TELEGRAM.format(token=token)
    for i, text in enumerate(msgs):
        if send("POST", url, {"chat_id": chat, "text": text, "disable_web_page_preview": True}) != 200:
            return i
    return len(msgs)


def main(argv: list[str] | None = None, send: Http = http) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", required=True, help="the live env file, outside the repo, mode 600")
    ap.add_argument("--root", default=str(ROOT / "data" / "perp_live"))
    ap.add_argument("--rc", type=int, required=True, help="the run's rc")
    ap.add_argument("--since-ms", type=int, required=True, help="the run's start: an older outcome record is not it")
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        live = read_live_env(Path(args.env_file).expanduser())
    except Exception as exc:
        print(f"perp live alert: the env file cannot be read: {type(exc).__name__}", file=sys.stderr)
        return 1
    token = (live.get("PERP_LIVE_TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (live.get("PERP_LIVE_TELEGRAM_CHAT_ID") or "").strip()
    ping_url = (live.get("PERP_LIVE_DEADMAN_URL") or "").strip()

    outcome = alerts.read_json(root / alerts.OUTCOME)
    if outcome is not None and int(outcome.get("at_ms") or 0) < args.since_ms:
        outcome = None                       # an earlier run's: this run wrote none
    status = alerts.read_json(root / "status.json")
    try:
        unsettled = alerts.unsettled_orders(krwfade.peek_ledger(root / "order_state.json"))
    except Exception:                        # unreadable: the signed run's own check stops on it
        unsettled = 0
    state = alerts.read_json(root / alerts.STATE) or {}
    now = int(time.time() * 1000)
    msgs, ping, state = alerts.decide(state, rc=args.rc, outcome=outcome, status=status, unsettled=unsettled,
                                      now_ms=now)
    for m in msgs:
        print(f"alert: {m}")

    todo = alerts.queue(state, msgs)
    if token and chat:
        if TOKEN_RE.match(token):
            sent = send_all(todo, token, chat, send)
            telegram = "ok" if sent == len(todo) else "failed"
        else:
            sent, telegram = 0, "bad_token"
        state["pending"] = todo[sent:]
    else:
        sent, telegram = 0, "off"            # the log has them
        state["pending"] = []

    if not ping:
        pinged = "no"
    elif not ping_url:
        pinged = "off"
    elif not ping_url.startswith("https://"):
        pinged = "not_https"
    else:
        pinged = "ok" if 200 <= send("GET", ping_url, None) < 300 else "failed"

    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{alerts.STATE}.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    tmp.replace(root / alerts.STATE)
    print(json.dumps({"alerts": len(msgs), "sent": sent, "pending": len(state["pending"]), "telegram": telegram,
                      "ping": pinged}))
    return 1 if telegram in ("failed", "bad_token") or pinged in ("failed", "not_https") else 0


if __name__ == "__main__":
    sys.exit(main())
