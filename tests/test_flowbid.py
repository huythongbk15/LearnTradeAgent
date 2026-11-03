"""Forced selling paper log: flag, bids, fills, exits, a crash replayed on a fake venue, and the wrapper. No network."""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from tools.flowbid import config as cf
from tools.flowbid import rules
from tools.flowbid import run as rn
from tools.selection import fetch as fx

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "ops" / "run_flowbid.sh"
TASKS = REPO / "scripts" / "ops" / "windows" / "register_tasks.ps1"
D0 = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp()) // 60       # 00:00 UTC of the crash day, in minutes
COINS = cf.UNIVERSE[:20]
CRASHED = set(COINS[:10])


def price(coin: str, m: int) -> tuple[float, float] | None:
    """(low, close) of a minute. Half the coins fall 25% at 10:00 UTC, deepen to 40% at 10:20 and are back at 10:40. No trade at 03:00."""
    if m == D0 + 180:
        return None
    if coin in CRASHED and D0 + 600 <= m < D0 + 640:
        return (75.0, 76.0) if m < D0 + 620 else (60.0, 61.0)
    return 100.0, 100.0


class Venue:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, timeout: float) -> tuple[int, bytes]:
        fx.check_url(url)
        self.calls += 1
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        coin = q["symbol"][: -len(fx.QUOTE)]
        a, b = int(q["startTime"]) // 60_000, int(q["endTime"]) // 60_000
        rows = []
        for m in range(a, min(b + 1, a + int(q["limit"]))):
            p = price(coin, m)
            if p is not None:
                lo, c = p
                rows.append([m * 60_000, str(c), str(c), str(lo), str(c), "1", m * 60_000 + 59_999, str(c)])
        return 200, json.dumps(rows).encode()


def bars(lows, closes=None, g0=0) -> rules.Bars:
    lows = np.asarray(lows, float)
    closes = lows if closes is None else np.asarray(closes, float)
    traded = np.isfinite(lows)
    return rules.Bars(g0, np.where(traded, lows, np.inf), np.where(traded, closes, np.nan), traded)


def test_the_rolling_minimum_is_the_plain_one() -> None:
    x = np.random.default_rng(1).normal(size=500)
    x[17] = np.inf
    out = rules.rolling_min(x, cf.WINDOW_MIN)
    assert np.all(np.isinf(out[: cf.WINDOW_MIN - 1]))
    assert np.array_equal(out[cf.WINDOW_MIN - 1:], [x[i - cf.WINDOW_MIN + 1: i + 1].min() for i in range(cf.WINDOW_MIN - 1, 500)])


def test_a_flag_needs_a_share_of_enough_coins_and_one_event_lasts_a_day() -> None:
    down = np.array([2, 3, 3, 30])
    den = np.array([14, 15, 21, 200])
    assert rules.flagged(down, den).tolist() == [False, True, False, True]
    assert rules.new_events([100, 101, 100 + cf.EVENT_MIN - 1, 100 + cf.EVENT_MIN], None) == [100, 100 + cf.EVENT_MIN]
    assert rules.new_events([100 + cf.EVENT_MIN], 101) == []


def test_a_bid_needs_a_fresh_reference_and_a_price_above_it() -> None:
    d0 = 10 * 1440
    b = bars([100.0] * 10, g0=d0 - 10)
    assert rules.bid_terms(b, d0 + 600, 95.0, 0.20) == ("bid", {"k": 0.20, "ref": 100.0, "ref_minute": d0 - 1, "bid": 80.0, "last": 95.0})
    assert rules.bid_terms(b, d0 + 600, 79.0, 0.20)[0] == "already_through"
    assert rules.bid_terms(b, d0 + 600, None, 0.20)[0] == "already_through"
    old = bars([100.0] * 10, g0=d0 - 10 - cf.BID_REF_MAX_AGE_MIN - 1)
    assert rules.bid_terms(old, d0 + 600, 95.0, 0.20)[0] == "no_reference"


def test_a_fill_needs_a_traded_minute_through_the_bid() -> None:
    b = bars([90.0, np.nan, 79.7, 79.5, 70.0])
    assert rules.fill_minute(b, 80.0, 0, 5) == 3          # 79.7 is not .5% through 80
    assert rules.fill_minute(b, 60.0, 0, 5) is None
    with pytest.raises(ValueError):
        rules.fill_minute(b, 80.0, 0, 6)


def test_an_exit_with_no_trade_for_a_day_is_marked_gone() -> None:
    b = bars([100.0] + [np.nan] * (cf.GONE_MIN + 5))
    assert rules.exit_terms(b, 10) == {"minute": 0, "close": 100.0, "gone": False}
    assert rules.exit_terms(b, cf.GONE_MIN + 5)["gone"] is True


def test_the_read_is_the_mean_of_nine_cells() -> None:
    assert len(cf.PRIMARY_CELLS) == 9 and set(cf.PRIMARY_CELLS) == {(k, h) for k in cf.K_LEVELS for h in cf.HOLDS_H}
    assert not hasattr(cf, "PRIMARY_K") and not hasattr(cf, "PRIMARY_HOLD_H")


def test_the_root_stays_in_data_flowbid_inside_the_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOWBID_ROOT", str(REPO / "data" / "elsewhere"))
    with pytest.raises(ValueError):
        cf.root()
    monkeypatch.setenv("FLOWBID_ROOT", str(tmp_path))
    assert cf.root() == tmp_path.resolve()
    assert not {"ETH", "SOL", "BNB"} & set(cf.UNIVERSE) and "ETHFI" in cf.UNIVERSE       # option B masks these coins, not their prefixes


def test_a_crash_is_flagged_bid_filled_and_exited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cf, "UNIVERSE", COINS)
    venue = Venue()

    def client() -> fx.Client:
        return fx.Client(venue, pace=0.0, sleep=lambda s: None)

    quiet = rn.run((D0 + 500) * 60, client(), tmp_path, log=lambda x: None)
    assert quiet["events"] == 0 and quiet["covered_to"] == rn.iso(D0 + 499)

    s = rn.run((D0 + 605) * 60, client(), tmp_path, log=lambda x: None)
    recs = rn.load(cf.ledger_path(tmp_path))
    ev = [r for r in recs if r["kind"] == "event"]
    assert s["events"] == 1 and ev[0]["flag_minute"] == D0 + 600 and ev[0]["down"] == 10 and ev[0]["den"] == 20
    assert ev[0]["live_from"] == D0 + 604 + cf.LATENCY_MIN
    bids = [r for r in recs if r["kind"] == "bid"]
    # crashed coins last traded at 76: only the 30% bid (70) is above nothing yet; the others get all three
    assert len(bids) == 10 * 1 + 10 * 3 and ev[0]["already_through"] == 20
    assert {round(b["bid"], 6) for b in bids if b["coin"] in CRASHED} == {70.0}

    again = rn.run((D0 + 700) * 60, client(), tmp_path, log=lambda x: None)
    assert again["events"] == 1                               # the next run sees no new event inside the day

    after = rn.run((D0 + 2200) * 60, client(), tmp_path, log=lambda x: None)
    recs = rn.load(cf.ledger_path(tmp_path))
    fills = [r for r in recs if r["kind"] == "fill"]
    assert after["fills"] == 10 and {f["minute"] for f in fills} == {D0 + 620} and after["open_bids"] == 0
    assert sum(1 for r in recs if r["kind"] == "cancel") == 30
    assert [r["missed"] for r in recs if r["kind"] == "run"][-1] == [D0 + 700, D0 + 2199 - cf.LOOKBACK_MIN]

    end = rn.run((D0 + 620 + 168 * 60 + 5) * 60, client(), tmp_path, log=lambda x: None)
    exits = [r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "exit"]
    assert end["exits_done"] == 30 and all(x["close"] == 100.0 and not x["gone"] for x in exits)
    status = json.loads(cf.status_path(tmp_path).read_text(encoding="utf8"))
    assert not any(k in status for k in ("price", "return", "pnl", "bps"))


def test_a_run_with_too_few_coins_judges_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cf, "UNIVERSE", COINS)

    def broken(url: str, timeout: float) -> tuple[int, bytes]:
        return 503, b""

    s = rn.run((D0 + 605) * 60, fx.Client(broken, pace=0.0, sleep=lambda x: None), tmp_path, log=lambda x: None)
    assert s["covered_to"] is None and s["events"] == 0


def test_when_the_exchange_says_stop_the_run_judges_nothing_and_asks_no_more(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cf, "UNIVERSE", COINS)
    asked = []

    def limited(url: str, timeout: float) -> tuple[int, bytes]:
        asked.append(url)
        return 429, b""

    s = rn.run((D0 + 605) * 60, fx.Client(limited, pace=0.0, sleep=lambda x: None), tmp_path, log=lambda x: None)
    run = rn.load(cf.ledger_path(tmp_path))[0]
    assert s["covered_to"] is None and run["stop"] == "status 429" and len(asked) <= cf.FETCH_WORKERS


def wrapper_at(tmp_path: Path, hhmm: str, job: str = "exit 0\n") -> subprocess.CompletedProcess:
    """The wrapper in a copy of the repo, with date and python stand-ins."""
    (tmp_path / "scripts" / "ops").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "scripts" / "ops" / "run_flowbid.sh"
    script.write_text(WRAPPER.read_text(encoding="utf8"), encoding="utf8")
    script.chmod(0o755)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("#!/usr/bin/env bash\n" + job, encoding="utf8")
    py.chmod(0o755)
    stand = tmp_path / "bin"
    stand.mkdir(exist_ok=True)
    (stand / "date").write_text(f'#!/usr/bin/env bash\ncase "$*" in *%H*) echo {hhmm[:2]};; *%M*) echo {hhmm[2:]};; *) /bin/date "$@";; esac\n', encoding="utf8")
    (stand / "date").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "FLOWBID_ROOT"}
    return subprocess.run([str(script)], env={**env, "PATH": f"{stand}:{os.environ['PATH']}"}, timeout=60, check=False)


@pytest.mark.parametrize("hhmm", ["1050", "1059", "1100", "1105"])
def test_the_wrapper_keeps_out_of_the_testnet_window(tmp_path: Path, hhmm: str) -> None:
    assert wrapper_at(tmp_path, hhmm, "touch ran\n").returncode == 0
    assert not (tmp_path / "ran").exists() and not (tmp_path / "logs" / "flowbid.log").exists()


@pytest.mark.parametrize("hhmm", ["1049", "1106", "1150", "1205"])
def test_the_wrapper_runs_outside_the_window_and_logs_how_it_ended(tmp_path: Path, hhmm: str) -> None:
    assert wrapper_at(tmp_path, hhmm, "echo the run spoke; exit 3\n").returncode == 3
    lines = (tmp_path / "logs" / "flowbid.log").read_text(encoding="utf8").splitlines()
    assert re.fullmatch(r"=== \S+Z flowbid start pid=\d+", lines[0]) and lines[1] == "the run spoke"
    assert re.fullmatch(r"=== \S+Z flowbid end rc=3", lines[2])


def test_the_wrapper_reaches_nothing_but_the_paper_log() -> None:
    text = WRAPPER.read_text(encoding="utf8")
    assert "-m tools.flowbid.run" in text
    for other in ("live_enhanced", "--execute", "TRADING_MODE", ".env", "API_KEY"):
        assert other not in text
    limit = int(re.search(r"timeout -k (\d+) (\d+) nice -n 19", text)[2])
    every = int(re.search(r'flowbidTrigger = .*?-RepetitionInterval \(New-TimeSpan -Minutes (\d+)\)', TASKS.read_text(encoding="utf8"), re.S)[1])
    assert limit + 20 < every * 60


def test_the_task_runs_the_wrapper_headless() -> None:
    text = TASKS.read_text(encoding="utf8")
    assert '-TaskName "TradingAgent-FlowBidPaper" -Action $flowbid' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_flowbid.sh" in text
