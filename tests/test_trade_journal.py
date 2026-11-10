"""The operator's trade journal (tools/journal): records, snapshot, outcomes, seal, chain. No network."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.journal import journal as J
from tools.newperp import fetch as fx

T0 = 1_794_268_800_000            # 2026-11-10 00:00 UTC
MIN = 60_000


def flat(base):
    return lambda t: base


class Market:
    """Public klines and funding from price functions of time; every URL is checked by the real allowlist."""

    def __init__(self, prices: dict):
        self.prices = prices
        self.urls: list[str] = []

    def __call__(self, url, timeout):
        fx.check_url(url)
        self.urls.append(url)
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        sym = q["symbol"]
        if p.path == fx.FUNDING:
            return 200, json.dumps([{"fundingTime": T0 - 8 * 3_600_000, "fundingRate": "0.0001"}]).encode()
        step = {"1m": MIN, "1h": 60 * MIN}[q["interval"]]
        start, end, lim = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        t = -(-start // step) * step
        rows = []
        f = self.prices[sym]
        while t <= end and len(rows) < lim:
            o = f(t)
            c = f(t + step - MIN)
            hi = max(f(t + k * MIN) for k in range(0, step // MIN, max(1, step // MIN // 6)))
            lo = min(f(t + k * MIN) for k in range(0, step // MIN, max(1, step // MIN // 6)))
            rows.append([t, str(o), str(max(hi, o, c)), str(min(lo, o, c)), str(c), "1", t + step - 1])
            t += step
        return 200, json.dumps(rows).encode()


def client(m):
    return fx.Client(m, pace=0, sleep=lambda s: None)


@pytest.fixture
def jdir(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    return tmp_path / "journal.jsonl"


def run(argv, m, capsys):
    assert J.main(argv, client=client(m)) == 0
    return capsys.readouterr().out


def test_add_records_size_levels_and_a_snapshot_from_bars_closed_before_entry(jdir, capsys):
    def inj(t):                                           # INJ up 2% a day
        return 8.0 * math.exp(0.02 * (t - T0) / (24 * 3_600_000))
    m = Market({"INJUSDT": inj, "BTCUSDT": flat(80_000.0)})
    out = run(["add", "inj", "short", "--entry", "8.18", "--time", "2026-11-10T00:00", "--notional", "70000",
               "--margin-balance", "3370", "--sl", "8.284", "--tp", "7.836", "--liq", "8.3255",
               "--reason", "BTC sẽ giảm, INJ giảm ít hơn", "--tags", "feel", "alt_lag", "--book", "inj1109"], m, capsys)
    assert out.strip() == "recorded j0001"
    r = J.read(jdir)[0]
    assert r["symbol"] == "INJUSDT" and r["levels"]["eff_leverage"] == pytest.approx(20.77, abs=0.01)
    assert r["levels"]["sl_dist_pct"] == pytest.approx(1.271, abs=1e-3) and r["levels"]["tp_dist_pct"] < 0
    s = r["snapshot"]
    assert s["ret_1d"] == pytest.approx(2.0, abs=0.05) and s["btc_1d"] == 0 and s["resid_1d"] == pytest.approx(1.98, abs=0.05)
    assert s["funding_last"] == 1.0
    # nothing at or after the entry minute was asked for
    for u in m.urls:
        q = parse_qs(urlsplit(u).query)
        if "endTime" in q and "interval" in q:
            assert int(q["endTime"][0]) < T0


def test_score_adds_horizons_as_they_pass_and_finds_the_first_level_touched(jdir, capsys):
    # a short at 8.18 at T0; price rises to 8.30 at minute 30 (past the SL 8.284), then falls
    def inj(t):
        k = (t - T0) / MIN
        return 8.18 + 0.004 * k if k <= 30 else max(7.5, 8.30 - 0.002 * (k - 30))
    m = Market({"INJUSDT": inj, "BTCUSDT": flat(80_000.0)})
    run(["add", "INJUSDT", "short", "--entry", "8.18", "--time", "2026-11-10T00:00", "--sl", "8.284", "--tp", "7.9"], m, capsys)
    J.cmd_score(None, jdir, client(m), now_ms=T0 + 70 * MIN)
    s = J.trades(J.read(jdir))["j0001"]["score"]
    assert set(k for k in s if k.startswith("ret_")) == {"ret_15m", "ret_1h"}
    assert s["ret_15m"] == pytest.approx(-(8.18 + 0.004 * 15) / 8.18 * 1e4 + 1e4, abs=0.2)
    assert s["first_touch"]["level"] == "sl" and s["first_touch"]["after_min"] in (26, 27)   # 8.18 + .004 k >= 8.284
    assert s["mae_bps"] == pytest.approx((8.30 / 8.18 - 1) * 1e4, abs=1) and s["final"] is False
    J.cmd_score(None, jdir, client(m), now_ms=T0 + 5 * 24 * 60 * MIN)
    s = J.trades(J.read(jdir))["j0001"]["score"]
    assert "ret_72h" in s and s["final"] is True
    n = len(J.read(jdir))
    J.cmd_score(None, jdir, client(m), now_ms=T0 + 6 * 24 * 60 * MIN)
    assert len(J.read(jdir)) == n                          # a final score is not redone


def test_a_close_records_the_realized_return_and_ends_the_path(jdir, capsys):
    m = Market({"INJUSDT": lambda t: 8.18 - 0.001 * (t - T0) / MIN, "BTCUSDT": flat(80_000.0)})
    run(["add", "INJ", "short", "--entry", "8.18", "--time", "2026-11-10T00:00"], m, capsys)
    run(["close", "j0001", "--price", "8.10", "--time", "2026-11-10T01:20", "--how", "manual"], m, capsys)
    J.cmd_score(None, jdir, client(m), now_ms=T0 + 3 * 60 * MIN)
    s = J.trades(J.read(jdir))["j0001"]["score"]
    assert s["realized_bps"] == pytest.approx((1 - 8.10 / 8.18) * 1e4, abs=0.1) and s["held_min"] == 80
    assert s["bars"] == 81 and "ret_4h" not in s
    with pytest.raises(SystemExit):
        J.main(["close", "j0001", "--price", "8.0"], client=client(m))


def test_a_sealed_coin_is_recorded_without_market_data_and_never_scored(jdir, capsys):
    m = Market({"BTCUSDT": flat(80_000.0)})
    out = run(["add", "BNB", "short", "--entry", "689.278", "--time", "2026-11-09T06:00", "--notional", "9867"], m, capsys)
    assert "sealed coin" in out and m.urls == []
    J.cmd_score(None, jdir, client(m), now_ms=T0 + 10 * 24 * 60 * MIN)
    assert m.urls == [] and J.trades(J.read(jdir))["j0001"]["score"] is None


def test_the_chain_catches_an_edit_and_report_hides_sizes_by_default(jdir, capsys):
    m = Market({"INJUSDT": flat(8.0), "BTCUSDT": flat(80_000.0)})
    run(["add", "INJ", "short", "--entry", "8.18", "--time", "2026-11-10T00:00", "--notional", "70171.43"], m, capsys)
    run(["note", "j0001", "a correction comes as a new record"], m, capsys)
    assert run(["verify"], m, capsys).strip() == "ok"
    rep = run(["report"], m, capsys)
    assert "70171" not in rep and "j0001" in rep
    assert "70171" in run(["report", "--sizes"], m, capsys)
    rows = jdir.read_text().splitlines()
    rows[0] = rows[0].replace("8.18", "8.17")
    jdir.write_text("\n".join(rows) + "\n")
    assert J.main(["verify"]) == 1


def test_the_journal_only_reads_public_market_data():
    src = Path(J.__file__).read_text()
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"post", "POST", "urlopen", "Request", "environ_get_key", "ccxt", "load_dotenv"}
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 40}
    assert not any(".env" in x or "dotenv" in x or "api_key" in x.lower() for x in strings)
    assert set(fx.ALLOWED) >= {fx.KLINES, fx.FUNDING}


def test_status_evaluates_an_open_trade_now_with_distances_and_past_odds(jdir, capsys):
    m = Market({"INJUSDT": lambda t: 8.0 + 0.2 * math.sin(t / (6 * 3_600_000)), "BTCUSDT": flat(80_000.0)})
    run(["add", "INJ", "short", "--entry", "8.18", "--time", "2026-11-09T13:03", "--sl", "8.284", "--tp", "7.836",
         "--liq", "8.3255"], m, capsys)
    out = J.cmd_status(None, jdir, client(m), now_ms=T0 + 90 * MIN)
    assert "j0001 INJUSDT short" in out and "distance from now: sl" in out and "TP first" in out
    assert out.count("within") == 3 and "not advice" in out
    run(["close", "j0001", "--price", "8.05", "--time", "2026-11-10T01:00"], m, capsys)
    assert J.cmd_status(None, jdir, client(m), now_ms=T0 + 90 * MIN) == "no open trade"


def test_race_counts_which_level_comes_first():
    bars = [(i, 100, 100, 100, 100.0) for i in range(5)] + [(5, 100, 104, 100, 103.0)] + [(i, 100, 100, 100, 100.0) for i in range(6, 12)]
    r = J.race(bars, up=3, down=3, hours=24)
    assert r == {}                                         # fewer bars than the horizon: no start
    r = J.race(bars, up=3, down=3, hours=3)
    assert r["up"] > 0 and r["down"] == 0
