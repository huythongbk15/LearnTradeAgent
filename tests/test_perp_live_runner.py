"""scripts/live_perp_r0.py, perp tier R0's entrypoint (docs/operations/PERP_LIVE_R0.md), on the fake venue: no
network, no repo .env."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from tests.test_perp_demo import KEY, FakeUsdm, events, orders_sent
from tests.test_perp_krwfade import DAY, E0, MIN, Public, note
from tests.test_perp_live_venue import ED_PEM

REPO = Path(__file__).resolve().parents[1]
SIZE = "123.25"                     # an amount that must never reach a log, the audit or status.json


@pytest.fixture
def r0(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("live_perp_r0_test", REPO / "scripts" / "live_perp_r0.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("TRADING_KILL_SWITCH", "TRADING_ENTRY_KILL_SWITCH", "TRADING_MODE", "TRADING_EXECUTION_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(os, "fsync", lambda fd: None)
    monkeypatch.setattr(ca.RateLimitManager, "REFILL_PER_S", 1e9)
    fake = FakeUsdm({"BTCUSDT": 80_000.0, "AAAUSDT": 2.0, "BBBUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    pub = Public()
    monkeypatch.setattr(mod, "krw_sources", lambda box: (pub.notices, pub.symbols, pub.shadow_row))
    pem = tmp_path / "live.pem"
    pem.write_text(ED_PEM, encoding="ascii")
    pem.chmod(0o600)
    env = {"TRADING_MODE": "live", "TRADING_EXECUTION_ENABLED": "true",
           "TRADING_LIVE_CONFIRMATION": "LIVE_TRADING_WITH_REAL_MONEY", "LIVE_SAFETY_HMAC_KEY": KEY,
           "BINANCE_LIVE_FUTURES_API_KEY": "live-key", "BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE": str(pem),
           "PERP_LIVE_KRW_USD": SIZE, "PERP_LIVE_ALLOW_ORDERS": "true"}
    return mod, fake, pub, env, tmp_path


def write_env(tmp: Path, env: dict, mode: int = 0o600) -> Path:
    p = tmp / "perp_live.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
    p.chmod(mode)
    return p


def argv(tmp: Path, *extra: str, confirm: str | None = "LIVE_TRADING_WITH_REAL_MONEY") -> list[str]:
    out = ["--execute", "--env-file", str(tmp / "perp_live.env"), "--root", str(tmp / "perp_live"), *extra]
    return out + (["--confirm-live", confirm] if confirm is not None else [])


def run(mod, monkeypatch, capsys, at: int, args: list[str], rc: int = 0) -> dict | str:
    monkeypatch.setattr(mod, "now_ms", lambda: at)
    assert mod.main(args) == rc
    got = capsys.readouterr()
    return json.loads(got.out.splitlines()[-1]) if rc == 0 else got.err


def no_amount(root: Path) -> None:
    """The audit and status.json carry no amount (the signed records keep the size the halt needs)."""

    for name in ("audit.jsonl", "status.json"):
        assert SIZE not in (root / name).read_text(encoding="utf-8"), name


def test_a_first_run_signs_once_checks_the_account_and_holds_no_amount(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, env)
    monkeypatch.setattr(mod, "now_ms", lambda: E0 - DAY)
    assert mod.main(argv(tmp)) == 0
    out = capsys.readouterr().out
    rep = json.loads(out.splitlines()[-1])
    assert rep["only"] == "krw" and rep["only_reason"] == "first_check" and orders_sent(fake) == []
    assert SIZE not in out
    root = tmp / "perp_live"
    status = json.loads((root / "status.json").read_text())
    assert status["allow_orders"] is True and status["krw_open"] == 0
    assert [e["event"] for e in events(root)] == ["perp_live_run_started", "perp_live_run_finished"]
    no_amount(root)
    # a quiet moment within the day: unsigned, nothing written
    audit = (root / "audit.jsonl").read_bytes()
    monkeypatch.setattr(mod, "read_live_env", lambda p: (_ for _ in ()).throw(AssertionError("signed")))
    assert run(mod, monkeypatch, capsys, E0 - DAY + 6 * 60 * MIN, argv(tmp)) == {"signed_run": False}
    assert (root / "audit.jsonl").read_bytes() == audit


def test_the_daily_check_while_flat_and_the_hourly_check_while_holding(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, env)
    run(mod, monkeypatch, capsys, E0 - 2 * DAY, argv(tmp))
    assert run(mod, monkeypatch, capsys, E0 - DAY, argv(tmp))["only_reason"] == "daily_check"
    pub.notes.append(note(101, "AAA"))
    rep = run(mod, monkeypatch, capsys, E0 + 2 * MIN, argv(tmp))
    assert rep["only_reason"] == "entry_window" and rep["krw"]["entered"] == 1
    assert run(mod, monkeypatch, capsys, E0 + 30 * MIN, argv(tmp)) == {"signed_run": False}
    assert run(mod, monkeypatch, capsys, E0 + 63 * MIN, argv(tmp))["only_reason"] == "hourly_check"


def test_one_event_at_a_time(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, env)
    pub.notes.extend([note(101, "AAA"), note(102, "BBB")])
    rep = run(mod, monkeypatch, capsys, E0 + 2 * MIN, argv(tmp))
    assert rep["krw"]["entered"] == 1 and rep["krw"]["shadow_only"] == 1
    store = json.loads((tmp / "perp_live" / "krw.json").read_text())
    reasons = [v.get("reason") for v in store["pairs"].values() if v.get("status") == "shadow_only"]
    assert reasons == ["max_open"]


def test_read_only_refuses_every_entry(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, {k: v for k, v in env.items() if k != "PERP_LIVE_ALLOW_ORDERS"})
    pub.notes.append(note(101, "AAA"))
    rep = run(mod, monkeypatch, capsys, E0 + 2 * MIN, argv(tmp))
    assert rep["entry_lock"].startswith("read-only") and rep["krw"]["entered"] == 0 and orders_sent(fake) == []
    assert json.loads((tmp / "perp_live" / "status.json").read_text())["allow_orders"] is False


@pytest.mark.parametrize("case, match", [
    ("no_confirm", "--confirm-live"),
    ("testnet_mode", "TRADING_MODE"),
    ("env_confirm", "TRADING_LIVE_CONFIRMATION"),
    ("file_kill", "KILL_SWITCH"),
    ("process_kill", "KILL_SWITCH"),
    ("env_mode_644", "mode 600"),
    ("no_size", "PERP_LIVE_KRW_USD"),
    ("hmac_secret", "Ed25519"),
])
def test_every_gate_stops_the_run_before_the_venue(r0, monkeypatch, capsys, case, match):
    mod, fake, pub, env, tmp = r0
    confirm = "LIVE_TRADING_WITH_REAL_MONEY"
    mode = 0o600
    if case == "no_confirm":
        confirm = None
    elif case == "testnet_mode":
        env["TRADING_MODE"] = "testnet"
    elif case == "env_confirm":
        env["TRADING_LIVE_CONFIRMATION"] = "yes"
    elif case == "file_kill":
        env["TRADING_KILL_SWITCH"] = "true"
    elif case == "process_kill":
        monkeypatch.setenv("TRADING_KILL_SWITCH", "true")
    elif case == "env_mode_644":
        mode = 0o644
    elif case == "no_size":
        env.pop("PERP_LIVE_KRW_USD")
    elif case == "hmac_secret":
        env["BINANCE_LIVE_FUTURES_API_SECRET"] = "s"
    write_env(tmp, env, mode)
    monkeypatch.setattr(mod, "PerpLiveVenue", lambda *a, **k: (_ for _ in ()).throw(AssertionError("venue built")))
    err = run(mod, monkeypatch, capsys, E0 - DAY, argv(tmp, confirm=confirm), rc=1)
    assert match in err and "live-key" not in err and "BEGIN" not in err


def test_resume_is_a_signed_run_by_hand_only(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, env)
    run(mod, monkeypatch, capsys, E0 - DAY, argv(tmp))
    rep = run(mod, monkeypatch, capsys, E0 - DAY + MIN, argv(tmp, "--resume"))   # quiet moment, still signed
    assert rep["only_reason"] == "resume" and rep["krw"]["resume"] == "nothing_to_resume"
    assert mod.main(["--resume", "--env-file", str(tmp / "perp_live.env")]) == 2
    wrapper = (REPO / "scripts" / "ops" / "run_perp_live.sh").read_text()
    assert "--resume" not in wrapper.split("set -uo pipefail", 1)[1] and '"$@"' not in wrapper


def test_without_execute_nothing_is_signed(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    monkeypatch.setattr(mod, "read_live_env", lambda p: (_ for _ in ()).throw(AssertionError("signed")))
    out = run(mod, monkeypatch, capsys, E0, ["--env-file", str(tmp / "x"), "--root", str(tmp / "perp_live")])
    assert out == {"signed_run": False, "would_sign": "first_check"}


def test_a_wrong_account_shape_stops_the_signed_run_before_any_order(r0, monkeypatch, capsys):
    mod, fake, pub, env, tmp = r0
    write_env(tmp, env)
    fake.hedged = True
    pub.notes.append(note(101, "AAA"))
    err = run(mod, monkeypatch, capsys, E0 + 2 * MIN, argv(tmp), rc=1)
    assert "hedge mode" in err and orders_sent(fake) == []
    assert [e["event"] for e in events(tmp / "perp_live")] == ["perp_live_run_failed"]
