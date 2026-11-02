"""The hourly store: append only, sorted, unique, no partial bar, and the loader against the seed matrices. Synthetic only."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from tests.test_selection_fixture import GONE_FROM, MASKED, market, write_seed
from tools.selection import config
from tools.selection import panel as pn
from tools.selection.config import hour_of

CUT = "2020-01-03"      # hour 24


def stored(root, coin: str = "AAA") -> pl.DataFrame:
    return pl.read_parquet(config.bars_dir(root) / f"{coin}.parquet")


def add(root, hours, cutoff=CUT, coin="AAA") -> int:
    x = np.arange(1, len(hours) + 1, dtype=float)
    return pn.append_bars(coin, hours, x, x + .5, x * 10, cutoff, root)


def test_a_bar_enters_only_if_it_closed_before_the_cutoff(tmp_path) -> None:
    assert add(tmp_path, [22, 23, 24, 25]) == 2
    assert stored(tmp_path)["hour"].to_list() == [22, 23]        # hour 23 closes just before hour 24, hour 24 is partial
    assert add(tmp_path, [24, 25], cutoff="2020-01-03T01:30:00") == 1
    assert stored(tmp_path)["hour"].to_list() == [22, 23, 24]


def test_the_store_is_append_only(tmp_path) -> None:
    add(tmp_path, [1, 2, 3])
    before = stored(tmp_path)
    x = np.array([9.0, 9.0])
    assert pn.append_bars("AAA", [2, 5], x, x, x, CUT, tmp_path) == 1
    after = stored(tmp_path)
    assert after.head(3).equals(before)
    assert after["hour"].to_list() == [1, 2, 3, 5]


def test_a_repeat_changes_no_byte(tmp_path) -> None:
    add(tmp_path, [1, 2, 3])
    first = (config.bars_dir(tmp_path) / "AAA.parquet").read_bytes()
    assert add(tmp_path, [1, 2, 3]) == 0
    assert (config.bars_dir(tmp_path) / "AAA.parquet").read_bytes() == first


@pytest.mark.parametrize("hours", ([3, 2], [2, 2], [-1, 0]))
def test_keys_must_be_sorted_unique_and_on_the_grid(tmp_path, hours) -> None:
    with pytest.raises(ValueError):
        add(tmp_path, hours)


def test_a_bar_with_a_missing_field_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        pn.append_bars("AAA", [1], [np.nan], [1.0], [1.0], CUT, tmp_path)


@pytest.mark.parametrize("o,c,q", [(0.0, 1.0, 1.0), (1.0, 0.0, 1.0), (-1.0, 1.0, 1.0), (np.inf, 1.0, 1.0), (1.0, np.inf, 1.0), (1.0, 1.0, np.inf), (1.0, 1.0, np.nan)])
def test_a_bar_with_no_real_price_never_enters_the_store(tmp_path, o: float, c: float, q: float) -> None:
    with pytest.raises(ValueError):
        pn.append_bars("AAA", [1, 2], [1.0, o], [1.0, c], [1.0, q], CUT, tmp_path)
    assert not list(tmp_path.rglob("*.parquet"))


def test_volume_may_be_zero(tmp_path) -> None:
    assert pn.append_bars("AAA", [1], [1.0], [1.0], [0.0], CUT, tmp_path) == 1


@pytest.mark.parametrize("close,volume", [(0.0, 1.0), (-1.0, 1.0), (np.nan, 1.0), (1.0, np.nan)])
def test_a_day_with_no_real_close_never_enters_the_store(tmp_path, close: float, volume: float) -> None:
    with pytest.raises(ValueError):
        pn.append_daily("AAA", [date(2020, 1, 2)], [close], [volume], "2020-01-03", tmp_path)
    assert not list(tmp_path.rglob("*.parquet"))


def test_open_times_must_sit_on_the_hourly_grid() -> None:
    t0 = config.ts(config.T0) * 1000
    assert pn.hours_from_open_ms([t0, t0 + 2 * config.HOUR_MS]).tolist() == [0, 2]
    with pytest.raises(ValueError):
        pn.hours_from_open_ms([t0 + 1])


def test_no_temporary_file_is_left_behind(tmp_path) -> None:
    add(tmp_path, [1, 2])
    assert [p.name for p in config.bars_dir(tmp_path).iterdir()] == ["AAA.parquet"]


def test_daily_store_keeps_only_closed_days(tmp_path) -> None:
    days = [date(2020, 3, 1) + timedelta(days=k) for k in range(4)]
    assert pn.append_daily("AAA", days, [1, 2, 3, 4], [5, 6, 7, 8], "2020-03-03", tmp_path) == 2
    assert pn.append_daily("AAA", days, [1, 2, 3, 4], [5, 6, 7, 8], "2020-03-04", tmp_path) == 1
    got = pn.load_daily(tmp_path)
    assert list(got) == ["AAA"]
    assert got["AAA"] == {days[0]: (1.0, 5.0), days[1]: (2.0, 6.0), days[2]: (3.0, 7.0)}


@pytest.fixture(scope="module")
def stored_market(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    seed = write_seed(d / "seed", market())
    return seed, d / "root", pn.seed_store(seed, d / "root")


def test_seed_step_drops_the_masked_columns(stored_market) -> None:
    _, root, summary = stored_market
    mk = market()
    assert summary["dropped"] == len(MASKED) and summary["coins"] == len(mk.coins) - len(MASKED)
    files = sorted(p.stem for p in config.bars_dir(root).glob("*.parquet"))
    assert files == sorted(c for c in mk.coins if c not in MASKED)
    assert summary["rows_added"] == int(sum((~np.isnan(mk.closes[:, mk.coins.index(c)])).sum() for c in files))


def test_store_is_sorted_unique_on_the_grid(stored_market) -> None:
    _, root, _ = stored_market
    for p in config.bars_dir(root).glob("*.parquet"):
        f = pl.read_parquet(p)
        h = f["hour"].to_numpy()
        assert (np.diff(h) > 0).all() and h[0] >= 0
        assert f.schema["hour"] == pl.Int32 and f.schema["q"] == pl.Float32
        assert not f.select(pl.any_horizontal(pl.col("o", "c", "q").is_nan())).to_series().any()


def test_the_seed_marks_itself_done_with_its_last_file(tmp_path, monkeypatch) -> None:
    seed = write_seed(tmp_path / "seed", market())
    root = tmp_path / "root"
    order = []
    real = config.write_text

    def note(path, text):
        order.append(path.name)
        real(path, text)

    monkeypatch.setattr(config, "write_text", note)
    pn.seed_store(seed, root)
    assert order == ["frozen.json", config.tier_path(root).name]


def test_a_second_seed_step_changes_no_byte(stored_market) -> None:
    seed, root, _ = stored_market
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert pn.seed_store(seed, root)["rows_added"] == 0
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_loader_equals_the_seed_columns(stored_market) -> None:
    seed, root, _ = stored_market
    end = "2020-09-07"
    a = pn.load_panel(end, root)
    b = pn.seed_panel(end, seed=seed, replay_cost=False)
    assert a.coins == b.coins and a.lists == b.lists and a.tier == b.tier and a.masked
    for x in ("opens", "closes", "quotes"):
        assert np.array_equal(getattr(a, x), getattr(b, x), equal_nan=True), x


def test_loader_keeps_the_grid_when_the_bars_stop_early(stored_market) -> None:
    _, root, _ = stored_market
    late = "2020-12-07"          # past the last stored bar
    p = pn.load_panel(late, root)
    T = hour_of(late)
    assert p.opens.shape[0] == T
    assert np.isnan(p.closes[hour_of("2020-10-26"):]).all()


def test_loader_reads_only_bars_before_the_end(stored_market) -> None:
    _, root, _ = stored_market
    assert pn.load_panel("2020-08-03", root).opens.shape[0] == hour_of("2020-08-03") < GONE_FROM


def test_loader_adds_a_column_for_a_listed_coin_with_no_bars(stored_market, tmp_path) -> None:
    seed, _, _ = stored_market
    root = tmp_path / "root"
    pn.seed_store(seed, root)
    (config.lists_dir(root) / "2026-08-10.json").write_text('["NEWC"]', encoding="utf8")
    p = pn.load_panel("2020-09-07", root)
    assert "NEWC" in p.coins and np.isnan(p.closes[:, p.coins.index("NEWC")]).all()


def test_seed_panel_cuts_at_the_end_and_can_replay_the_study_universe(stored_market) -> None:
    seed, _, _ = stored_market
    p = pn.seed_panel("2020-09-07", masked=False, seed=seed, replay_cost=False)
    assert not p.masked and set(MASKED) <= set(p.coins) and p.opens.shape[0] == hour_of("2020-09-07")


def test_a_changed_seed_is_refused(tmp_path) -> None:
    seed = write_seed(tmp_path / "seed", market())
    assert "O.npy" in pn.verify_seed(seed)
    with open(seed / "tier_cost.json", "ab") as f:
        f.write(b" ")
    with pytest.raises(ValueError, match="tier_cost.json"):
        pn.seed_store(seed, tmp_path / "root")
    assert not (tmp_path / "root").exists()
