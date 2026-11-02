"""Acceptance itself must be able to fail. A synthetic study stands in for the real one: its files are written by a plain build, reference.json is hashed from them, and one changed cell has to fail the proof. It reads no real data."""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from tests.test_selection_fixture import END, market, panel_of, write_seed
from tools.selection import acceptance as acc
from tools.selection import config
from tools.selection import library as lib
from tools.selection import selector as sel

CUTS = (0, 2)


def stamps_of(mk) -> dict:
    """stamps_seed.npz of the fixture, from the first stamp the study kept."""
    p = panel_of(mk, masked=False)
    d = lib.Data(p, start="2020-03-02")
    return {"t": d.t, "hours": d.h, "Mem": d.Mem, "cost": lib.tier_cost(d, p.tier)}


def write_study(folder, mk) -> None:
    """The layout of the study's outputs for this market, built without the acceptance module."""
    z = stamps_of(mk)
    p = panel_of(mk, masked=False)
    p = replace(p, replay_cost={"t": z["t"], "cost": z["cost"]})
    d = lib.Data(p)
    W = np.zeros((32, d.S, d.N), np.float32)

    def sink(name, w):
        W[lib.NAMES.index(name)] = w.astype(np.float32)

    s = lib.build_series(d, on_weights=sink)
    (folder / "library").mkdir(parents=True)
    (folder / "selector").mkdir()
    np.save(folder / "library" / "weights_disc.npy", W)
    np.savez(folder / "library" / "series_disc.npz", t=s.t, h=s.h, names=np.array(lib.NAMES), gross=s.gross, net=s.net, cost=s.cost, turn=s.turn, expo=s.expo, nexp=s.nexp, gross_open=s.gross_open)
    np.savez(folder / "library" / "panel_disc.npz", t=s.t, coins=np.array(d.coins), R=s.R.astype(np.float32), c=s.c.astype(np.float32), Mem=d.Mem)
    bk = sel.Book(s.t, s.gross, s.cost, s.c, W)
    sb = sel.score_book(s.net, s.t)
    real = {"names": np.array([acc.PRIMARY, acc.BEST, "EW"])}
    gross, cost = np.zeros((bk.S, 3)), np.zeros((bk.S, 3))
    for i, (name, table) in enumerate(((acc.PRIMARY, sb.primary), (acc.BEST, sb.best))):
        days, rows, ch = sel.primary_lists(bk, sb, table=table)
        real[f"days_{name}"], real[f"ch_{name}"], real[f"sc_{name}"] = days, ch, rows
        starts = bk.stamp_of_day(days)
        gross[:, i] = sel.gross_path(bk, ch, starts)
        cost[:, i] = lib.cost_on_weights(sel.path(bk, ch, starts), bk.c[:bk.S])
    gross[:, 2], cost[:, 2], _ = sel.ew_series(bk)
    np.savez(folder / "selector" / "real_disc.npz", net=gross - cost, gross=gross, cost=cost, **real)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("acc")
    mk = market()
    seed = write_seed(d / "seed", mk, stamps_of(mk))
    write_study(d / "study", mk)
    acc.write_reference(d / "study", seed)
    return seed, d / "study"


def test_reference_comes_from_the_study_files(world) -> None:
    seed, _ = world
    ref = json.loads((seed / "reference.json").read_text(encoding="utf8"))
    assert list(ref["candidates"]) == lib.NAMES and ref["end"] == config.DISCOVERY_END
    assert len(ref["portfolios"][acc.PRIMARY]["lists"]) == 15 // 4 + 1
    assert all(len(row) == 3 for row in ref["portfolios"][acc.PRIMARY]["lists"])


def test_the_port_passes_with_the_study_files(world, tmp_path) -> None:
    seed, study = world
    r = acc.run(seed, study, end=END, tmp=tmp_path, cuts=CUTS)
    q = r["replay"]
    assert r["passed"], json.dumps(q, default=str)
    assert q["weights_vs_study"]["diff"] == 0 and q["weights_vs_study"]["cells"] == 32 * q["stamps"] * q["coins"]
    assert q["candidates_hash_equal"] == 32 and q["portfolios"][acc.PRIMARY]["lists_diff"] == 0
    assert [c["list_equal"] for c in q["cuts"]] == [True, True]


def test_acceptance_works_with_the_scratch_folder_gone(world, tmp_path) -> None:
    seed, _ = world
    r = acc.run(seed, None, end=END, tmp=tmp_path, cuts=CUTS)
    assert r["passed"] and "weights_vs_study" not in r["replay"]


def test_the_report_goes_beside_the_seed_and_never_under_the_selection_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SELECTION_ROOT", str(tmp_path / "root"))
    seed = tmp_path / "study" / "seed"
    assert acc.report_path(None, seed) == tmp_path / "study" / "acceptance.json"
    assert acc.report_path(str(tmp_path / "x.json"), seed) == tmp_path / "x.json"
    with pytest.raises(ValueError, match="selection root"):
        acc.report_path(str(tmp_path / "root" / "acceptance" / "acceptance.json"), seed)
    with pytest.raises(ValueError, match="runner store"):
        acc.report_path(str(tmp_path / "data" / "execution" / "acceptance.json"), seed)


def test_masked_measure_runs_on_the_store_path_and_names_no_masked_coin(world, tmp_path) -> None:
    seed, _ = world
    q = acc.masked_measure(seed, END, tmp_path)
    assert q["store_equals_seed_columns"] and q["rule_cost_vs_seed_cost"]["diff"] == 0
    assert q["store_files"] == q["seed_step"]["coins"]
    assert not config.sealed_hits(p for p in json.dumps(q).replace('"', " ").split() if p.isalpha())
    assert {"EW", "four_books", "phase_0_book", "best_so_far_four_books", "hold_btc"} == set(q["windows"]["ALL"])
    assert q["windows"]["ALL"]["four_books"]["gross_exposure"] > 0


def tampered(study, tmp_path, name: str, key: str | None, fn):
    """A copy of the study's folder with one array changed."""
    import shutil

    copy = tmp_path / "study"
    shutil.copytree(study, copy)
    if name.endswith(".npy"):
        a = np.load(copy / name)
        fn(a)
        np.save(copy / name, a)
    else:
        z = np.load(copy / name, allow_pickle=False)
        arrays = {k: z[k] for k in z.files}
        fn(arrays[key])
        np.savez(copy / name, **arrays)
    return copy


def flip_weight(a: np.ndarray) -> None:
    a[3, 700, 2] += .001


def flip_net(a: np.ndarray) -> None:
    a[500, 4] += 1e-9


def flip_list(a: np.ndarray) -> None:
    a[1, 0] = (a[1, 0] + 1) % 31


@pytest.mark.parametrize("name,key,fn", [
    ("library/weights_disc.npy", None, flip_weight),
    ("library/series_disc.npz", "net", flip_net),
    ("selector/real_disc.npz", f"ch_{acc.PRIMARY}", flip_list),
])
def test_one_changed_study_cell_fails_the_reference(world, tmp_path, name, key, fn) -> None:
    seed, study = world
    copy = tampered(study, tmp_path, name, key, fn)
    folder = tmp_path / "seed"
    import shutil

    shutil.copytree(seed, folder)
    acc.write_reference(copy, folder)
    r = acc.run(folder, None, end=END, tmp=tmp_path, masked=False, cuts=CUTS)
    assert not r["passed"]


def test_one_changed_study_cell_fails_the_cell_comparison(world, tmp_path) -> None:
    seed, study = world
    copy = tampered(study, tmp_path, "library/weights_disc.npy", None, flip_weight)
    r = acc.run(seed, copy, end=END, tmp=tmp_path, masked=False, cuts=CUTS)
    assert not r["passed"]
    assert r["replay"]["weights_vs_study"]["diff"] == 1
    assert r["replay"]["weights_vs_study"]["candidates_with_a_diff"] == [lib.NAMES[3]]


def test_a_cut_list_that_moved_fails(world, tmp_path) -> None:
    seed, _ = world
    r = acc.run(seed, None, end=END, tmp=tmp_path, masked=False, cuts=CUTS)
    r["replay"]["cuts"][0]["list_equal"] = False
    assert not acc.passed(r)
