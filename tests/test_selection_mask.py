"""The mask of option B, proven by mutation and by refusal, not by intent.

Mutate the masked coins' bars, list places and tier in a seed, and on the exchange of a whole weekly run: every byte the run produces stays equal. The same mutation on an open coin moves the output, so the proof can fail. Fetch, store, list and loader refuse a masked symbol in every spelling.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from tests.test_selection_exchange import FakeExchange, client_of, make_world, refuse_network, seed_market
from tests.test_selection_fixture import MASKED, Market, market, panel_of, write_seed
from tools.selection import config, fetch, universe
from tools.selection import library as lib
from tools.selection import panel as pn
from tools.selection import selector as sel
from tools.selection.config import SealedSymbolError

AS_OF = "2020-09-07"
SPELLINGS = ("ETH", "eth", "ETHUSDT", "ETH/USDT", "ETH_USDT", "eth-usdt", " SOL ", "BNBUSDT", "ETHBTC", "ETHUSDC", "1000XRP", "1MDOGE", "ETH:USDT")


def outputs(seed: Path, root: Path) -> dict[str, bytes]:
    """Every output of the path a weekly run takes, as bytes: the store files, then the panel, weights, series, scores, lists."""
    pn.seed_store(seed, root)
    out = {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    p = pn.load_panel(AS_OF, root)
    out["coins"] = json.dumps(p.coins).encode()
    out["lists"] = json.dumps(p.lists, sort_keys=True).encode()
    out["tier"] = json.dumps(p.tier, sort_keys=True).encode()
    for x in ("opens", "closes", "quotes"):
        out[f"panel_{x}"] = getattr(p, x).tobytes()
    d = lib.Data(p, live=True)
    out["mem"] = d.Mem.tobytes()
    out["cost"] = d.cost.tobytes()
    h = hashlib.sha256()

    def sink(name: str, w: np.ndarray) -> None:
        h.update(name.encode())
        h.update(w.tobytes())

    s = lib.build_series(d, on_weights=sink)
    out["weights"] = h.digest()
    for x in ("gross", "net", "cost", "turn", "expo", "nexp", "R", "R_late"):
        out[f"series_{x}"] = getattr(s, x).tobytes()
    bk = sel.Book(s.t, s.gross, s.cost, s.c)
    sb = sel.score_book(s.net, s.t)
    for phase in range(config.PRIMARY_CADENCE_WEEKS):
        days, rows, ch = sel.primary_lists(bk, sb, phase=phase)
        out[f"scores_{phase}"] = rows.tobytes()
        out[f"picks_{phase}"] = ch.tobytes()
    return out


def mutated(mk: Market, coins, seed: int = 5) -> Market:
    """The same market with these coins' bars, list places and tier changed."""
    rng = np.random.default_rng(seed)
    Op, C, Q = mk.opens.copy(), mk.closes.copy(), mk.quotes.copy()
    for c in coins:
        j = mk.coins.index(c)
        f = np.exp(rng.normal(0, .3, Op.shape[0]))
        Op[:, j] *= f
        C[:, j] *= f
        Q[:, j] = (Q[:, j] * rng.uniform(.01, 100, Op.shape[0])).astype(np.float32)
    weeks = {k: sorted(v, key=lambda x: x not in coins) for k, v in mk.weeks.items()}      # stable: the open names keep their order
    tier = dict(mk.tier)
    for c in coins:
        tier[c] = 99.0
    return replace(mk, opens=Op, closes=C, quotes=Q, weeks=weeks, tier=tier)


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> dict[str, bytes]:
    d = tmp_path_factory.mktemp("base")
    return outputs(write_seed(d / "seed", market()), d / "root")


def test_nothing_masked_reaches_a_stored_byte(base) -> None:
    names = {k for k in base if k.startswith(("bars/", "daily/"))}
    assert len(names) > 10
    assert not config.sealed_hits(Path(k).stem for k in names)
    frozen = json.loads(base["lists/frozen.json"])
    assert not config.sealed_hits(c for v in frozen.values() for c in v)
    assert not config.sealed_hits(json.loads(base["tier_cost.json"]))
    assert not config.sealed_hits(json.loads(base["coins"]))


def test_mutating_masked_coins_changes_no_output_byte(base, tmp_path) -> None:
    got = outputs(write_seed(tmp_path / "seed", mutated(market(), MASKED)), tmp_path / "root")
    assert sorted(got) == sorted(base)
    moved = [k for k in base if got[k] != base[k]]
    assert moved == []


def test_removing_masked_bars_changes_no_output_byte(base, tmp_path) -> None:
    mk = market()
    Op, C, Q = mk.opens.copy(), mk.closes.copy(), mk.quotes.copy()
    cols = [mk.coins.index(c) for c in MASKED]
    Op[:, cols] = C[:, cols] = Q[:, cols] = np.nan
    got = outputs(write_seed(tmp_path / "seed", replace(mk, opens=Op, closes=C, quotes=Q)), tmp_path / "root")
    assert [k for k in base if got[k] != base[k]] == []


def test_the_mask_proof_can_fail(base, tmp_path) -> None:
    got = outputs(write_seed(tmp_path / "seed", mutated(market(), ("DDD",))), tmp_path / "root")
    moved = {k for k in base if got[k] != base[k]}
    assert "weights" in moved and "series_net" in moved and "bars/DDD.parquet" in moved


@pytest.mark.parametrize("symbol", SPELLINGS)
def test_store_refuses_a_masked_symbol(symbol: str, tmp_path) -> None:
    with pytest.raises(SealedSymbolError):
        pn.append_bars(symbol, [0], [1.0], [1.0], [1.0], "2020-01-03", tmp_path)
    with pytest.raises(SealedSymbolError):
        pn.append_daily(symbol, [date(2020, 1, 2)], [1.0], [1.0], "2020-01-03", tmp_path)
    assert not list(tmp_path.rglob("*.parquet"))


@pytest.mark.parametrize("symbol", SPELLINGS)
def test_list_refuses_a_masked_symbol(symbol: str, tmp_path) -> None:
    with pytest.raises(SealedSymbolError):
        universe.store_list("2026-08-10", ["AAA", symbol], tmp_path)
    assert not list(tmp_path.rglob("*.json"))
    monday = date(2026, 8, 10)
    bars = {monday - timedelta(days=k): (1.0 + k % 3, 1.0) for k in range(1, 80)}
    with pytest.raises(SealedSymbolError):
        universe.rule_list({"AAA": bars, symbol: bars}, monday)


def test_loader_refuses_a_masked_file_that_got_into_the_store(tmp_path) -> None:
    root = tmp_path / "root"
    pn.seed_store(write_seed(tmp_path / "seed", market()), root)
    src = config.bars_dir(root) / "AAA.parquet"
    (config.bars_dir(root) / "ETH.parquet").write_bytes(src.read_bytes())
    with pytest.raises(SealedSymbolError):
        pn.load_panel(AS_OF, root)
    config.daily_dir(root).mkdir()
    (config.daily_dir(root) / "SOL.parquet").write_bytes(src.read_bytes())
    with pytest.raises(SealedSymbolError):
        pn.load_daily(root)


def test_loader_refuses_a_masked_name_in_a_stored_list_or_tier(tmp_path) -> None:
    root = tmp_path / "root"
    pn.seed_store(write_seed(tmp_path / "seed", market()), root)
    bad = config.lists_dir(root) / "2026-08-10.json"
    bad.write_text(json.dumps(["AAA", "ADA"]), encoding="utf8")
    with pytest.raises(SealedSymbolError):
        pn.load_panel(AS_OF, root)
    bad.unlink()
    tier = config.tier_path(root)
    tier.write_text(json.dumps({"ETH": 11.0}), encoding="utf8")
    with pytest.raises(SealedSymbolError):
        pn.load_panel(AS_OF, root)


def test_a_masked_panel_cannot_hold_a_masked_coin() -> None:
    p = panel_of(market())
    coins = list(p.coins)
    coins[0] = "ETH"
    with pytest.raises(SealedSymbolError):
        replace(p, coins=coins)
    with pytest.raises(SealedSymbolError):
        replace(p, lists={"2020-03-02": ["AAA", "XRP"]})
    with pytest.raises(SealedSymbolError):
        replace(p, tier={"ZEC": 20.0})


def test_masked_panel_is_masked_by_default_on_the_replay_path(tmp_path) -> None:
    seed = write_seed(tmp_path / "seed", market())
    p = pn.seed_panel("2020-09-07", seed=seed, replay_cost=False)
    assert p.masked and not config.sealed_hits(p.coins) and not config.sealed_hits(p.tier)
    assert not config.sealed_hits(c for v in p.lists.values() for c in v)


@pytest.mark.parametrize("symbol", SPELLINGS)
def test_fetch_refuses_a_masked_symbol_before_any_call(symbol: str, tmp_path, monkeypatch) -> None:
    refuse_network(monkeypatch)
    ex = FakeExchange(market())
    client = client_of(ex)
    with pytest.raises(SealedSymbolError):
        fetch.refresh_coin(client, symbol, "2020-02-01", tmp_path)
    with pytest.raises(SealedSymbolError):
        fetch.refresh_pair(client, symbol, "2020-02-01", tmp_path)
    assert ex.calls == [] and not list(tmp_path.rglob("*.parquet"))


@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT", "BNBUSDT", "eth-usdt", "ETH/USDT"])
def test_the_lowest_layer_refuses_a_masked_symbol_too(symbol: str, monkeypatch) -> None:
    refuse_network(monkeypatch)
    ex = FakeExchange(market())
    with pytest.raises(SealedSymbolError):
        client_of(ex).get(fetch.KLINES, {"symbol": symbol, "interval": "1h", "startTime": 0, "endTime": 1, "limit": 1})
    assert ex.calls == []


def test_a_masked_coin_among_the_names_stops_the_fetch_as_a_refusal_not_a_failure(tmp_path, monkeypatch) -> None:
    refuse_network(monkeypatch)
    ex = FakeExchange(market())
    report = fetch.Report(attempted=True)
    with pytest.raises(SealedSymbolError):
        fetch.refresh_hourly(client_of(ex), ["ETH"], "2020-01-04", tmp_path, report)
    assert ex.calls == [] and report.hourly_failed == []


@pytest.mark.parametrize("coin", sorted(config.SEALED))
def test_every_masked_coin_is_refused_at_fetch(coin: str, tmp_path, monkeypatch) -> None:
    refuse_network(monkeypatch)
    with pytest.raises(SealedSymbolError):
        fetch.refresh_coin(client_of(FakeExchange(market())), coin, "2020-01-04", tmp_path)


def run_chain(tmp_path, monkeypatch, name: str, mk: Market) -> dict[str, bytes]:
    """Every byte a chain of weekly runs leaves, on a market as the seed and the exchange both hold it. The manifest holds the wall clock and the seed's hash, so it is the one file left out."""
    base = make_world(tmp_path / name, monkeypatch, seed_mk=seed_market(mk), exchange_mk=mk)
    for monday in ("2020-08-31", "2020-09-07", "2020-09-14"):
        base.run(monday)
    assert base.exchange.masked_requests == []
    return base.files()


@pytest.fixture
def chain_base(tmp_path, monkeypatch) -> dict[str, bytes]:
    refuse_network(monkeypatch)
    (tmp_path / "base").mkdir()
    return run_chain(tmp_path, monkeypatch, "base", market())


def test_a_weekly_run_holds_no_masked_name_in_any_file(chain_base) -> None:
    names = [k for k in chain_base if k.startswith(("bars/", "daily/"))]
    assert len(names) > 20 and not config.sealed_hits(Path(k).stem for k in names)
    for k, data in chain_base.items():
        if k.endswith((".json", ".jsonl")):
            for coin in config.SEALED:
                assert f'"{coin}"'.encode() not in data, (k, coin)


def test_mutating_masked_coins_changes_no_byte_of_a_weekly_run(chain_base, tmp_path, monkeypatch) -> None:
    (tmp_path / "moved").mkdir()
    got = run_chain(tmp_path, monkeypatch, "moved", mutated(market(), MASKED))
    assert sorted(got) == sorted(chain_base) and [k for k in chain_base if got[k] != chain_base[k]] == []


def test_the_weekly_mask_proof_can_fail(chain_base, tmp_path, monkeypatch) -> None:
    (tmp_path / "open").mkdir()
    got = run_chain(tmp_path, monkeypatch, "open", mutated(market(), ("DDD",)))
    moved = {k for k in chain_base if got.get(k) != chain_base[k]}
    assert "ledger.jsonl" in moved and "bars/DDD.parquet" in moved


def as_proxy(mk: Market, coin: str, proxy: str) -> Market:
    """The same market with a coin under the proxy's name, on every list it was on."""
    coins = tuple(proxy if c == coin else c for c in mk.coins)
    return replace(mk, coins=coins, weeks={k: [proxy if c == coin else c for c in v] for k, v in mk.weeks.items()})


def test_a_proxy_the_operator_masks_changes_no_byte_of_a_weekly_run(tmp_path, monkeypatch) -> None:
    """The path the ruling on BNSOL opens. Its name goes through the same mask, and the same mutation proof holds."""
    refuse_network(monkeypatch)
    monkeypatch.setattr(config, "SEAL_PROXIES", frozenset({"BNSOL"}))
    mk = as_proxy(market(), "RRR", "BNSOL")
    (tmp_path / "base").mkdir()
    (tmp_path / "moved").mkdir()
    base = run_chain(tmp_path, monkeypatch, "base", mk)
    assert not [k for k in base if "BNSOL" in k] and not [k for k, v in base.items() if k.endswith((".json", ".jsonl")) and b"BNSOL" in v]
    got = run_chain(tmp_path, monkeypatch, "moved", mutated(mk, ("BNSOL",)))
    assert sorted(got) == sorted(base) and [k for k in base if got[k] != base[k]] == []


@pytest.mark.parametrize("write", [
    lambda root: pn.append_bars("BNSOL", [0], [1.0], [1.0], [1.0], "2020-01-03", root),
    lambda root: pn.append_daily("BNSOL", [date(2020, 1, 2)], [1.0], [1.0], "2020-01-03", root),
    lambda root: universe.store_list("2026-08-10", ["AAA", "BNSOL"], root),
])
def test_a_proxy_the_operator_masks_cannot_be_stored(tmp_path, monkeypatch, write) -> None:
    monkeypatch.setattr(config, "SEAL_PROXIES", frozenset({"BNSOL"}))
    with pytest.raises(SealedSymbolError):
        write(tmp_path)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_a_proxy_the_operator_masks_is_never_asked_for(monkeypatch) -> None:
    refuse_network(monkeypatch)
    monkeypatch.setattr(config, "SEAL_PROXIES", frozenset({"BNSOL"}))
    ex = FakeExchange(market())
    with pytest.raises(SealedSymbolError):
        client_of(ex).get(fetch.KLINES, {"symbol": "BNSOLUSDT", "interval": "1h", "startTime": 0, "endTime": 1, "limit": 1})
    info = {"symbols": [{"status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True, "baseAsset": c} for c in ("BNSOL", "AAA", "BTC")]}
    assert ex.calls == [] and universe.tradable_bases(info) == ["AAA"] and universe.trading_bases(info) == {"AAA", "BTC"}


def test_exchange_pairs_drop_masked_coins_before_any_request() -> None:
    info = {"symbols": [{"status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True, "baseAsset": c} for c in ("ETH", "SOL", "AAA", "BTC")]}
    assert universe.tradable_bases(info) == ["AAA"]


def _calls_with_masked_false(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf8"))
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) for k in n.keywords if k.arg == "masked" and isinstance(k.value, ast.Constant) and k.value.value is False]


def test_the_unmasked_path_is_for_acceptance_only() -> None:
    package = Path(config.__file__).parent
    assert _calls_with_masked_false(package / "acceptance.py")
    for path in sorted(package.glob("*.py")):
        if path.name != "acceptance.py":
            assert _calls_with_masked_false(path) == [], path.name
    for name in ("run_weekly.py", "decide.py", "ledger.py", "policy.py", "status.py", "fetch.py"):
        path = package / name
        if path.exists():
            assert "seed_panel" not in path.read_text(encoding="utf8"), name
            assert "acceptance" not in path.read_text(encoding="utf8"), name


def test_nothing_in_the_package_imports_a_seed_text_file_or_scripts() -> None:
    package = Path(config.__file__).parent
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                roots = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                roots = [(n.module or "").split(".")[0]]
            else:
                continue
            assert not {"scripts", "seed", "core", "stamps", "build"} & set(roots), path.name
