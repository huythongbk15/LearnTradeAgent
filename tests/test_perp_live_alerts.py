"""Perp tier R0's alerts that leave the PC (docs/operations/PERP_LIVE_R0.md, section 5 item 5): what
``trading_agent.execution.perp.alerts`` decides and what ``scripts/perp_live_alert.py`` sends. No network: the
script's HTTP call is replaced."""

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path

import ccxt
import pytest

from trading_agent.execution.live_safety import LiveSafetyError
from trading_agent.execution.perp import alerts
from trading_agent.execution.perp.book import PerpBookError

REPO = Path(__file__).resolve().parents[1]
H = 60 * 60 * 1000
MIN = 60 * 1000
T0 = 1_762_934_400_000
TOKEN = "123456:abc-DEF_ghi"
CHAT = "-100777"
PING = "https://hc.example.test/ping/abc"


def decide(state, rc=0, outcome=None, status=None, unsettled=0, at=T0):
    return alerts.decide(state, rc=rc, outcome=outcome, status=status, unsettled=unsettled, now_ms=at)


# ── decide ──────────────────────────────────────────────────────────────────


def test_a_clean_run_pings_and_says_nothing():
    msgs, ping, st = decide({})
    assert (msgs, ping) == ([], True) and st == {"unsettled": 0}


def test_a_failure_is_sent_at_once_then_hourly_while_it_lasts_then_ok_again():
    out = {"rc": 1, "error": "PerpBookError", "kind": "order"}
    msgs, ping, st = decide({}, rc=1, outcome=out)
    assert msgs == ["R0 live: run failed rc=1: order PerpBookError"] and ping is False
    msgs, ping, st = decide(st, rc=1, outcome=out, at=T0 + 5 * MIN)
    assert msgs == [] and ping is False                                   # the same failure, inside the hour
    msgs, _, st = decide(st, rc=1, outcome={**out, "kind": "position"}, at=T0 + 10 * MIN)
    assert msgs == ["R0 live: run failed rc=1: position PerpBookError, failing since 11-12 08:00Z"]
    msgs, _, st = decide(st, rc=1, outcome={**out, "kind": "position"}, at=T0 + 70 * MIN)
    assert len(msgs) == 1 and "failing since 11-12 08:00Z" in msgs[0]     # an hour after the last message
    msgs, ping, st = decide(st, at=T0 + 75 * MIN)
    assert msgs == ["R0 live: runs end rc 0 again (failing since 11-12 08:00Z)"] and ping and "fail" not in st


def test_a_failure_without_an_outcome_record_is_still_sent():
    msgs, ping, _ = decide({}, rc=137)
    assert msgs == ["R0 live: run failed rc=137: unknown (no outcome record)"] and not ping


def test_a_named_skip_is_sent_once_it_lasts_30_minutes_then_hourly_and_its_end_once():
    st: dict = {}
    for i in range(6):                                                    # 0..25 min: quiet, still pinged
        msgs, ping, st = decide(st, outcome={"rc": 0, "skipped": "upbit_403"}, at=T0 + 5 * i * MIN)
        assert msgs == [] and ping
    msgs, _, st = decide(st, outcome={"rc": 0, "skipped": "upbit_429"}, at=T0 + 30 * MIN)
    assert msgs == ["R0 live: notice list skipped for 30 min (now upbit_429): new listings are not seen"]
    msgs, _, st = decide(st, outcome={"rc": 0, "skipped": "upbit_403"}, at=T0 + 35 * MIN)
    assert msgs == []
    msgs, _, st = decide(st, outcome={"rc": 0, "skipped": "upbit_403"}, at=T0 + 90 * MIN)
    assert msgs == ["R0 live: notice list skipped for 90 min (now upbit_403): new listings are not seen"]
    msgs, _, st = decide(st, outcome={"rc": 0, "signed": False}, at=T0 + 95 * MIN)
    assert msgs == ["R0 live: notice list read again"] and "skip" not in st
    _, _, st = decide(st, outcome={"rc": 0, "skipped": "binance_429"}, at=T0 + 100 * MIN)
    msgs, _, st = decide(st, outcome={"rc": 0}, at=T0 + 105 * MIN)
    assert msgs == []                                                     # a short skip ends quietly


def test_a_fired_stop_a_halt_and_the_floor_are_sent_on_change_only():
    status = {"krw_stops_fired": 0, "krw_halted": False, "floor": "ok"}
    msgs, _, st = decide({}, status=status)
    assert msgs == []
    status = {"krw_stops_fired": 1, "krw_halted": True, "floor": "below_floor"}
    msgs, _, st = decide(st, status=status, at=T0 + 5 * MIN)
    assert msgs == ["R0 live: stop fired: 1 new, 1 in all. K-pause: no entry until you sign",
                    "R0 live: book KRW halted (K3): entries stop until a --resume run",
                    "R0 live: equity below the D5 floor: entries held, exits run"]
    msgs, _, st = decide(st, status=status, at=T0 + 10 * MIN)
    assert msgs == []                                                     # status.json unchanged since
    msgs, _, st = decide(st, status={"krw_stops_fired": 1, "krw_halted": False, "floor": "ok"}, at=T0 + 15 * MIN)
    assert msgs == ["R0 live: book KRW no longer halted", "R0 live: equity back above the D5 floor"]


def test_unsettled_orders_are_sent_when_their_count_changes():
    msgs, _, st = decide({}, unsettled=1)
    assert msgs == ["R0 live: orders not settled in the ledger: 1"]
    assert decide(st, unsettled=1, at=T0 + 5 * MIN)[0] == []
    msgs, _, st = decide(st, unsettled=2, at=T0 + 10 * MIN)
    assert msgs == ["R0 live: orders not settled in the ledger: 2"]
    msgs, _, st = decide(st, unsettled=0, at=T0 + 15 * MIN)
    assert msgs == ["R0 live: every order in the ledger is settled"]


def test_the_ledger_count_leaves_an_open_stop_out():
    ledger = {"krw-1-AAA-st": {"status": "acknowledged"}, "krw-1-AAA-o0": {"status": "filled"},
              "krw-1-AAA-c0": {"status": "reconciling"}, "krw-2-BBB-st": {"status": "manual_intervention"},
              "krw-3-CCC-o0": {"status": "submitted"}, "krw-4-DDD-st": {"status": "cancelled"}}
    assert alerts.unsettled_orders(ledger) == 3


def test_kinds_come_from_the_class_and_the_books_own_phrases():
    assert alerts.kind_of(LiveSafetyError("kill switch")) == "gate"
    assert alerts.kind_of(ccxt.RequestTimeout("x")) == "network"
    assert alerts.kind_of(TimeoutError()) == "network"
    cases = {
        "pair store integrity check failed: /x": "store",
        "venue positions do not match the book; operator review required": "position",
        "KRW AAAUSDT: venue 5.0 differs from book 4.0": "position",
        "KRW AAAUSDT: short has no open stop; operator review required": "position",
        "KRW order krw-1-AAA-c0 still reconciling; operator review required": "order",
        "order krw-1-AAA-o0 outcome unknown; reconcile before the next entry": "order",
        "intent krw-1-AAA-o0 is not at the venue; operator review required": "order",
        "order krw-1-AAA-c0 refused -2022: InvalidOrder": "order",
        "account is in hedge mode or its mode is unreadable; R0 needs one-way mode": "account",
        "live venue: a request to a host other than fapi.binance.com was refused": "venue",
        "something new": "other",
    }
    for text, kind in cases.items():
        assert alerts.kind_of(PerpBookError(text)) == kind, text


def test_every_phrase_a_kind_reads_is_still_in_the_perp_code():
    """A reworded error must not fall to "other" unnoticed."""

    src = "".join((REPO / "src" / "trading_agent" / "execution" / "perp" / n).read_text()
                  for n in ("book.py", "krwfade.py", "venue.py"))
    loose = {" still ", " ended ", " refused ", " not_sent", "host", "proxy", "negative"}   # built from f-strings
    for _kind, phrases in alerts.KIND_PHRASES:
        for p in phrases:
            assert p in loose or p in src, p


def test_messages_carry_no_amount():
    """Each message is made of fixed words, counts and names: no decimal number, so no price, size or equity."""

    st: dict = {}
    every = []
    for at, kw in enumerate([dict(rc=1, outcome={"error": "PerpBookError", "kind": "position"}), dict(rc=0),
                             dict(status={"krw_stops_fired": 2, "krw_halted": True, "floor": "below_floor"},
                                  unsettled=3)]):
        msgs, _, st = decide(st, at=T0 + at * MIN, **kw)
        every += msgs
    assert every and not any(re.search(r"\d\.\d", m) for m in every)


def test_the_outcome_record_is_written_only_into_an_existing_root(tmp_path):
    alerts.write_outcome(tmp_path / "none", rc=0)
    assert not (tmp_path / "none").exists()
    alerts.write_outcome(tmp_path, rc=1, error="X", kind="other")
    rec = alerts.read_json(tmp_path / alerts.OUTCOME)
    assert rec["rc"] == 1 and abs(rec["at_ms"] - time.time() * 1000) < 60_000
    assert [p.name for p in tmp_path.iterdir()] == [alerts.OUTCOME]


def test_pending_messages_go_first_and_at_most_twenty_are_kept():
    assert alerts.queue({"pending": ["a", "b"]}, ["c"]) == ["a", "b", "c"]
    assert alerts.queue({"pending": [str(i) for i in range(30)]}, ["new"])[-1] == "new"
    assert len(alerts.queue({"pending": [str(i) for i in range(30)]}, ["new"])) == alerts.MAX_PENDING


# ── the script ──────────────────────────────────────────────────────────────


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("perp_live_alert_test", REPO / "scripts" / "perp_live_alert.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Http:
    def __init__(self, status: int = 200):
        self.calls: list[tuple] = []
        self.status = status

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        return self.status


def live(tmp: Path, **extra: str) -> tuple[Path, Path]:
    env = {"PERP_LIVE_TELEGRAM_BOT_TOKEN": TOKEN, "PERP_LIVE_TELEGRAM_CHAT_ID": CHAT, "PERP_LIVE_DEADMAN_URL": PING,
           **extra}
    p = tmp / "perp_live.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in env.items() if v is not None), encoding="utf-8")
    p.chmod(0o600)
    root = tmp / "perp_live"
    root.mkdir(exist_ok=True)
    return p, root


def call(script, env: Path, root: Path, rc: int, http: Http, since: int = 0) -> int:
    return script.main(["--env-file", str(env), "--root", str(root), "--rc", str(rc), "--since-ms", str(since)],
                       send=http)


def test_a_run_that_ended_rc_0_pings_the_dead_man_check_only(script, tmp_path, capsys):
    env, root = live(tmp_path)
    http = Http()
    assert call(script, env, root, 0, http) == 0
    assert http.calls == [("GET", PING, None)]
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[-1]) == {"alerts": 0, "sent": 0, "pending": 0, "telegram": "ok", "ping": "ok"}
    assert TOKEN not in out and PING not in out


def test_a_failed_run_sends_telegram_its_kind_and_does_not_ping(script, tmp_path, capsys):
    env, root = live(tmp_path)
    alerts.write_outcome(root, rc=1, signed=True, error="PerpBookError", kind="position")
    http = Http()
    assert call(script, env, root, 1, http, since=int(time.time() * 1000) - 60_000) == 0
    ((method, url, body),) = http.calls
    assert (method, url) == ("POST", f"https://api.telegram.org/bot{TOKEN}/sendMessage")
    assert body["chat_id"] == CHAT and body["text"] == "R0 live: run failed rc=1: position PerpBookError"
    out = capsys.readouterr().out
    assert "alert: R0 live: run failed rc=1: position PerpBookError" in out and TOKEN not in out


def test_an_outcome_record_older_than_the_run_is_not_its_own(script, tmp_path):
    env, root = live(tmp_path)
    alerts.write_outcome(root, rc=1, error="PerpBookError", kind="position")
    http = Http()
    call(script, env, root, 1, http, since=int(time.time() * 1000) + 60_000)
    assert http.calls[0][2]["text"] == "R0 live: run failed rc=1: unknown (no outcome record)"


def test_a_message_telegram_did_not_take_is_sent_after_the_next_run(script, tmp_path, capsys):
    env, root = live(tmp_path)
    down = Http(status=502)
    assert call(script, env, root, 1, down) == 1
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["pending"] == 1
    up = Http()
    assert call(script, env, root, 0, up) == 0
    texts = [b["text"] for m, _u, b in up.calls if m == "POST"]
    assert texts == ["R0 live: run failed rc=1: unknown (no outcome record)",
                     "R0 live: runs end rc 0 again (failing since " + texts[1].split("since ")[1]]
    assert up.calls[-1] == ("GET", PING, None)
    assert alerts.read_json(root / alerts.STATE)["pending"] == []


def test_without_telegram_names_the_messages_go_to_the_log_only(script, tmp_path, capsys):
    env, root = live(tmp_path, PERP_LIVE_TELEGRAM_BOT_TOKEN=None, PERP_LIVE_DEADMAN_URL=None)
    http = Http()
    assert call(script, env, root, 1, http) == 0
    assert http.calls == []
    out = capsys.readouterr().out
    assert "alert: R0 live: run failed rc=1" in out
    assert json.loads(out.splitlines()[-1]) == {"alerts": 1, "sent": 0, "pending": 0, "telegram": "off", "ping": "no"}


def test_a_ping_url_that_is_not_https_is_refused(script, tmp_path, capsys):
    env, root = live(tmp_path, PERP_LIVE_DEADMAN_URL="http://hc.example.test/ping/abc")
    http = Http()
    assert call(script, env, root, 0, http) == 1
    assert http.calls == [] and json.loads(capsys.readouterr().out.splitlines()[-1])["ping"] == "not_https"


def test_an_env_file_others_can_read_is_refused(script, tmp_path, capsys):
    env, root = live(tmp_path)
    env.chmod(0o644)
    http = Http()
    assert call(script, env, root, 0, http) == 1 and http.calls == []
    assert "cannot be read" in capsys.readouterr().err


def test_the_live_wrapper_alerts_after_each_run_it_starts_and_keeps_the_runs_rc():
    text = (REPO / "scripts" / "ops" / "run_perp_live.sh").read_text()
    lock, run_at, alert_at, exit_at = (text.index(s) for s in ("flock -n 9 || exit 0", "-u scripts/live_perp_r0.py",
                                                               "-u scripts/perp_live_alert.py", 'exit "$rc"'))
    assert lock < run_at < alert_at < exit_at                   # a lock skip exits before; the run's rc is kept
    assert '--rc "$rc" --since-ms "$start_ms"' in text and "timeout 120" in text[run_at:exit_at]
