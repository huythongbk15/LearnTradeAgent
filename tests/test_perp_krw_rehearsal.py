"""Book KRW's marked rehearsal (PERP_LIVE_R0.md section 5 item 3, for stage C): one event of the book's shape on a
symbol the operator names, entered now, with its own stop multiple and hold, kept out of K3, the shadow and the
counts that page. On the fake venue, Demo and mainnet; no network."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tests import test_perp_krwfade as krwfade_tests
from tests import test_perp_live_runner as live_tests
from tests.test_perp_demo import orders_sent
from tests.test_perp_krwfade import DAY, E0, MIN, Public, cycle, krw, krw_events, stored
from trading_agent.execution.perp import krwfade as kx
from trading_agent.execution.perp.book import PerpBookError

world = krwfade_tests.world        # the fake venue, Demo and mainnet (a fixture)
r0 = live_tests.r0                 # the R0 runner on the fake venue (a fixture)
REPO = Path(__file__).resolve().parents[1]
T = E0 + 7 * MIN                   # the operator's run, at no notice's window


def rehearse(perp: str = "CCCUSDT", stop_x: float = 1.01, hold_min: int = 30) -> dict:
    return {"perp": perp, "stop_x": stop_x, "hold_ms": hold_min * 60_000}


def rehearsal_event(tmp) -> tuple[str, dict]:
    ((eid, ev),) = [(k, v) for k, v in stored(tmp).pairs.items() if k.startswith("e") and v.get("rehearsal")]
    return eid, ev


def test_a_rehearsal_is_one_event_of_the_books_shape_entered_now_with_its_own_stop(world):
    fake, book, tmp = world
    rep = cycle(book, krw(book, tmp, Public(), rehearse=rehearse()), T)["krw"]
    assert rep["rehearsal"] == "made" and rep["entered"] == 1
    eid, ev = rehearsal_event(tmp)
    assert eid == f"e{T // MIN}-CCC" and ev["status"] == "open" and ev["shadow"] == {"status": "rehearsal"}
    keys = [c[5]["clientOrderId"] for c in orders_sent(fake)]
    assert keys == [f"krw-{T // MIN}-CCC-o0", f"krw-{T // MIN}-CCC-st", f"krw-{T // MIN}-CCC-b0"]
    (algo,) = fake.algo.values()
    assert algo["triggerPrice"] == pytest.approx(1.01 * 5.0)                   # the rehearsal's tight multiple
    counts = krw(book, tmp, Public()).counts()
    assert counts["krw_events"] == {} and counts["krw_rehearsals"] == {"open": 1}
    assert [e["event"] for e in krw_events(tmp)][0] == "perp_krw_rehearsal"


def test_a_rehearsal_exits_after_its_own_hold(world):
    fake, book, tmp = world
    cycle(book, krw(book, tmp, Public(), rehearse=rehearse(hold_min=30)), T)
    store = kx.PairStore.peek(tmp / kx.STORE)
    assert kx.signed_run_reason(T + 20 * MIN, store, Public().notices, Public().symbols) is None
    assert kx.signed_run_reason(T + 28 * MIN, store, Public().notices, Public().symbols) == "exit_due"
    rep = cycle(book, krw(book, tmp, Public()), T + 28 * MIN)["krw"]
    _eid, ev = rehearsal_event(tmp)
    assert rep["closed"] == 1 and (ev["status"], ev["close_reason"]) == ("closed", "hold")


def test_a_tight_rehearsal_stop_that_fires_is_counted_apart_from_the_books_stops(world):
    fake, book, tmp = world
    cycle(book, krw(book, tmp, Public(), rehearse=rehearse(stop_x=1.002)), T)
    fake.fire_stop("CCCUSDT")
    cycle(book, krw(book, tmp, Public()), T + 5 * MIN)
    _eid, ev = rehearsal_event(tmp)
    assert (ev["status"], ev["close_reason"]) == ("closed", "stop")
    counts = krw(book, tmp, Public()).counts()
    assert counts["krw_stops_fired"] == 0 and counts["krw_rehearsal_stops"] == 1     # no K-pause page for it


def test_a_rehearsal_loss_never_reaches_the_halt(world):
    fake, book, tmp = world
    kb = krw(book, tmp, Public())
    kb._st(T)
    loss = {"kind": "event", "notice_id": 1, "token": "ZZZ", "perp": "ZZZUSDT", "entry_ms": T, "notice_ms": T,
            "status": "closed", "decided_ms": T,
            "fills": [{"key": "a", "side": "sell", "qty": 100.0, "avg": 5.0, "symbol": "ZZZUSDT", "fees": {"USDT": 0}},
                      {"key": "b", "side": "buy", "qty": 100.0, "avg": 20.0, "symbol": "ZZZUSDT", "fees": {"USDT": 0}}]}
    kb.store.pairs["e1-ZZZ"] = {**loss, "rehearsal": True}
    assert not kb._breached()
    kb.store.pairs["e1-ZZZ"] = loss
    assert kb._breached()


def test_a_rehearsal_not_entered_in_its_run_is_missed_and_never_signs_a_later_run(world):
    fake, book, tmp = world
    rep = cycle(book, krw(book, tmp, Public(), rehearse=rehearse()), T, entry_lock="read-only")["krw"]
    assert rep["rehearsal"] == "made" and orders_sent(fake) == []
    _eid, ev = rehearsal_event(tmp)
    assert (ev["status"], ev["reason"]) == ("missed", "entry_lock")
    store = kx.PairStore.peek(tmp / kx.STORE)
    assert kx.signed_run_reason(T + MIN, store, Public().notices, Public().symbols) is None


def test_one_rehearsal_at_a_time_and_bad_parameters_are_refused(world):
    fake, book, tmp = world
    cycle(book, krw(book, tmp, Public(), rehearse=rehearse()), T)
    assert cycle(book, krw(book, tmp, Public(), rehearse=rehearse()), T + MIN)["krw"]["rehearsal"] == "one_open"
    for bad in (rehearse(stop_x=1.0), rehearse(stop_x=1.6), rehearse(hold_min=1), rehearse(hold_min=2000),
                rehearse(perp="BTCUSDT"), rehearse(perp="CCC"), rehearse(perp="NOPEUSDT")):
        with pytest.raises(PerpBookError, match="rehearsal"):
            krw(book, tmp, Public(), rehearse=bad)._rehearsal(T + 2 * MIN)


# ── the runners ────────────────────────────────────────────────────────────


def test_the_live_runner_signs_a_rehearsal_by_hand_and_enters_it(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    live_tests.write_env(tmp, env)
    live_tests.run(mod, monkeypatch, capsys, E0 - DAY, live_tests.argv(tmp))          # the first check
    args = live_tests.argv(tmp, "--rehearse", "aaausdt", "--rehearse-stop-x", "1.01", "--rehearse-hold-min", "15")
    rep = live_tests.run(mod, monkeypatch, capsys, T, args)       # a quiet moment: signed all the same
    assert rep["only_reason"] == "rehearse" and rep["krw"]["rehearsal"] == "made" and rep["krw"]["entered"] == 1
    store = json.loads((tmp / "perp_live" / kx.STORE).read_text())["pairs"]
    (ev,) = [v for k, v in store.items() if k.startswith("e") and v.get("rehearsal")]
    assert (ev["perp"], ev["stop_x"], ev["hold_ms"], ev["status"]) == ("AAAUSDT", 1.01, 15 * 60_000, "open")
    live_tests.no_amount(tmp / "perp_live")


def test_the_live_runner_refuses_a_rehearsal_without_execute_or_with_another_by_hand_run(r0):
    mod, fake, pub, env, tmp = r0
    env_file = str(tmp / "perp_live.env")
    assert mod.main(["--rehearse", "AAAUSDT", "--env-file", env_file]) == 2
    for other in ("--resume", "--exit-now"):
        assert mod.main(["--execute", "--rehearse", "AAAUSDT", other, "--env-file", env_file]) == 2
    assert "--rehearse" not in (REPO / "scripts" / "ops" / "run_perp_live.sh").read_text()


def test_the_demo_runner_takes_krw_rehearse_with_krw_and_execute_alone(tmp_path):
    spec = importlib.util.spec_from_file_location("live_perp_demo_rh_test", REPO / "scripts" / "live_perp_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = ["--root", str(tmp_path / "d")]
    for bad in (["--krw-rehearse", "CCCUSDT", "--krw"], ["--execute", "--krw-rehearse", "CCCUSDT"],
                ["--execute", "--krw", "--krw-rehearse", "CCCUSDT", "--krw-exit-now"],
                ["--execute", "--krw", "--krw-rehearse", "CCCUSDT", "--krw-resume"]):
        assert mod.main(bad + base) == 2
    for name in ("run_perp_demo.sh", "run_flow_flush.sh"):
        assert "--krw-rehearse" not in (REPO / "scripts" / "ops" / name).read_text()
    ns = mod.argparse.Namespace(krw_rehearse="cccusdt", krw_rehearse_stop_x=1.5, krw_rehearse_hold_min=30)
    assert mod.krw_rehearsal(ns) == {"perp": "CCCUSDT", "stop_x": 1.5, "hold_ms": 30 * 60_000}
