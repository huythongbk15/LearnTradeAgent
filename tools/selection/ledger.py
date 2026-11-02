"""Paper ledger of the selection run, append only JSONL, one row a finished week. Nobody reads it before the read date: the page shows none of it.

A week is the 42 stamps from Sunday 20:00 to Sunday 16:00, named after the Monday that closes it. That is the block of the selector seven times over, and the last week the cutoff can close: the return of the Sunday 20:00 stamp ends at the open of Monday 00:00, a bar not closed before that cutoff. So a run on Monday closes the week ended the same morning, the row of the read date exists on the read date, and every stamp falls in exactly one week. The first week starts at the first selection and has 41 stamps: all four portfolios start flat there, as in the study.

A row is final. A week waits, and a later run writes it in order, until every coin held in it or listed for it has a bar at the last hour the week reads, unless the exchange says the coin stopped: a fetch that failed or a mirror that lags must not freeze a false zero into an append only file. The file is replaced whole on each write, so a kill or a full disk cannot leave half a line.

Fractions, not bps. Cost is |dw| times c over 2e4 with c in bps a round trip. Drift means cost on |w(s) - drifted w(s-1)|, the study's drift_cost. The late fill runs from the open at h+1 to the open at h+5, with the same weights.

Statistic of the page: the week's return minus k times EW's, k the portfolio's mean gross exposure over EW's in that week. The running z is the sum of those differences over 302 bps times the root of the number of weeks, on tier plus 10 bps, drift, open one hour later, counted from the first week of the cohort.
"""
from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.selection import config, universe
from tools.selection import library as lib
from tools.selection import selector as sel
from tools.selection.config import ts
from tools.selection.decide import Event

PORTFOLIOS = ("paper", "EW", "BEST_SO_FAR", "hold_btc")
DRIFT = {"no_drift": False, "drift": True}
WEEK_SECONDS = 7 * 86400
BLOCK_OFFSET = 14400        # a week ends with the selector's block, four hours before the Monday, and a stamp is as long


def ledger_path(root: Path) -> Path:
    return root / "ledger.jsonl"


def load_rows(root: Path) -> list[dict]:
    path = ledger_path(root)
    return [json.loads(x) for x in path.read_text(encoding="utf8").splitlines()] if path.exists() else []


def append_rows(root: Path, rows: list[dict]) -> None:
    """Add rows after the last one. A week already in the ledger is refused: a row is final. The old bytes stay as they are, under the new rows."""
    path = ledger_path(root)
    last = (load_rows(root)[-1:] or [{"week": ""}])[0]["week"]
    text = ""
    for r in rows:
        if r["week"] <= last:
            raise ValueError(f"week {r['week']} is not after {last}")
        last = r["week"]
        text += json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    if text:
        config.write_text(path, (path.read_text(encoding="utf8") if path.exists() else "") + text)


def week_span(monday: str) -> tuple[int, int]:
    """First stamp of the week a Monday closes, and the stamp just past its last: both are Sunday 20:00."""
    end = ts(monday) - BLOCK_OFFSET
    return end - WEEK_SECONDS, end


def last_hour_read(monday: str) -> int:
    """The last hour a week reads: its last stamp's late fill ends at the open of this hour, one past the end of the week. With no bar from here on a coin reads as stopped, so a lag this short would still move the row."""
    return config.hour_of(np.datetime64(week_span(monday)[1], "s")) + 1


def listed_in(monday: str, lists: Mapping[str, list[str]]) -> set[str]:
    """Names on the lists in force at the stamps of a week. There are two: the week starts on the Sunday before its Monday's list."""
    if not lists:
        return set()
    first, end = week_span(monday)
    keys, rows = universe.week_of_stamps(lists, np.array([first, end - BLOCK_OFFSET]))
    return {c for i in rows for c in lists[keys[i]]}


def open_weeks(rows: list[dict], events: list[Event], as_of: str) -> list[str]:
    """Mondays that close a week not yet in the ledger, oldest first. The ledger starts at the first selection: nothing was held before it."""
    if not events:
        return []
    monday = date.fromisoformat(rows[-1]["week"] if rows else events[0].monday) + timedelta(days=7)
    out = []
    while monday.isoformat() <= as_of:
        out.append(monday.isoformat())
        monday += timedelta(days=7)
    return out


def keep_from(t: np.ndarray, weeks: list[str]) -> int:
    """First stamp a build must keep: the one before the oldest open week, for the weight change into it, else the stamp at the cutoff alone."""
    if not weeks:
        return len(t) - 1
    return max(int(np.searchsorted(t, week_span(weeks[0])[0])) - 1, 0)


@dataclass(frozen=True)
class Window:
    """What the ledger and the target read of a build, from stamp keep_from on. t, W and c run to the stamp at the cutoff; R and R_late stop one stamp short of it, or more if nothing later can be read."""

    t: np.ndarray
    W: np.ndarray           # 32 by stamps by coins
    R: np.ndarray
    R_late: np.ndarray
    c: np.ndarray
    coins: list[str]


def window_of(s: lib.Series, t: np.ndarray, coins: list[str]) -> Window:
    a = s.keep_from
    return Window(t[a:].copy(), np.stack([s.recent[n] for n in lib.NAMES]), s.R[a:].copy(), s.R_late[a:].copy(), s.c[a:].copy(), coins)


def four_books(win: Window, events: list[Event], field: str) -> np.ndarray:
    """Mean of the four staggered books. Each is flat before its own first selection and holds its last selection after."""
    shell = SimpleNamespace(S=win.W.shape[1], N=win.W.shape[2], W=win.W)
    total = np.zeros(win.W.shape[1:])
    for phase in range(config.PRIMARY_CADENCE_WEEKS):
        own = [e for e in events if e.phase == phase]
        if own:
            ch = np.array([[lib.NAMES.index(x) for x in getattr(e, field)] for e in own])
            total += sel.path(shell, ch, np.searchsorted(win.t, [ts(e.monday) for e in own]))
    return total / config.PRIMARY_CADENCE_WEEKS


def weight_paths(win: Window, events: list[Event], start: str) -> dict[str, np.ndarray]:
    """Weights of the four portfolios on the window's stamps, flat before the first selection."""
    out = {
        "paper": four_books(win, events, "slots"),
        "EW": win.W.sum(0) / len(lib.NAMES),
        "BEST_SO_FAR": four_books(win, events, "best"),
        "hold_btc": win.W[lib.NAMES.index("hold_btc")].copy(),
    }
    for w in out.values():
        w[win.t < ts(start)] = 0.0
    return out


def drifted(w: np.ndarray, R: np.ndarray) -> np.ndarray:
    """w(s) carried to the end of stamp s."""
    return w * (1 + R) / (1 + (w * R).sum(1))[:, None]


def cost_path(w: np.ndarray, R: np.ndarray, c: np.ndarray, drift: bool) -> np.ndarray:
    """Cost per stamp as a fraction. The first row is a stamp whose start is not known here: the ledger never reads it, unless it is flat."""
    if not drift:
        return lib.cost_on_weights(w, c)
    before = np.zeros(w.shape)
    before[1:] = drifted(w, R)[:-1]
    return (np.abs(w - before) * c).sum(1) / 2e4


def basis_cost(c: np.ndarray, basis: str) -> np.ndarray:
    kind, x = config.COST_BASES[basis]
    return c + x if kind == "tier" else np.full(c.shape, x)


def stamp_series(w: np.ndarray, win: Window) -> tuple[dict, dict]:
    """Gross per clock and net per clock, basis and drift, one value a stamp that has a return."""
    n = len(win.R)
    gross, net = {}, {}
    for clock, R in zip(config.FILL_CLOCKS, (win.R, win.R_late)):
        gross[clock] = (w[:n] * R).sum(1)
        for basis in config.COST_BASES:
            c = basis_cost(win.c[:n], basis)
            for name, drift in DRIFT.items():
                net[clock, basis, name] = gross[clock] - cost_path(w[:n], R, c, drift)
    return gross, net


def _compound(x: np.ndarray) -> float:
    return float(np.prod(1 + x) - 1)


def _grid(fn) -> dict:
    return {clock: {basis: {d: fn(clock, basis, d) for d in DRIFT} for basis in config.COST_BASES} for clock in config.FILL_CLOCKS}


def _main(grid: dict) -> float:
    return grid[config.MAIN_FILL_CLOCK][config.MAIN_COST_BASIS]["drift"]


def cohort_week(monday: str, cohort: dict | None) -> int | None:
    """Number of the week a Monday closes, 1 for the week after the first run of the cohort. None before it."""
    if cohort is None or monday <= cohort["start"]:
        return None
    return (date.fromisoformat(monday) - date.fromisoformat(cohort["start"])).days // 7


def running_z(rows: list[dict], name: str, diff: float, week: int | None) -> float | None:
    if week is None:
        return None
    total = sum(_main(r["portfolios"][name]["same_exposure"]) for r in rows if r["cohort_week"]) + diff
    return float(total / (config.TRIPWIRE_WEEKLY_SD_BPS / 1e4 * np.sqrt(week)))


def week_row(monday: str, start: str, cohort: dict | None, win: Window, paths: dict, stamps: dict, rows: list[dict]) -> dict:
    first, end = week_span(monday)
    a, b = (int(x) for x in np.searchsorted(win.t, [max(first, ts(start)), end]))
    week = cohort_week(monday, cohort)
    block = {}
    for name in PORTFOLIOS:
        gross, net = stamps[name]
        block[name] = {
            "exposure": float(np.abs(paths[name][a:b]).sum(1).mean()),
            "gross": {clock: _compound(gross[clock][a:b]) for clock in config.FILL_CLOCKS},
            "net": _grid(lambda c, b_, d: _compound(net[c, b_, d][a:b])),
        }
    ew = block["EW"]
    for name in PORTFOLIOS:
        if name != "EW":
            q = block[name]
            k = q["exposure"] / ew["exposure"] if ew["exposure"] > 0 else 0.0
            q["k"] = k
            q["same_exposure"] = _grid(lambda c, b_, d: q["net"][c][b_][d] - k * ew["net"][c][b_][d])
            q["z"] = running_z(rows, name, _main(q["same_exposure"]), week)
    return {
        "week": monday,
        "first_stamp": str(np.datetime64(int(win.t[a]), "s")) + "Z",
        "last_stamp": str(np.datetime64(int(win.t[b - 1]), "s")) + "Z",
        "stamps": b - a,
        "cohort_week": week,
        "portfolios": block,
    }


def week_ready(monday: str, win: Window, paths: dict, last_bar: np.ndarray, stopped: Collection[str], listed: Collection[str] = ()) -> bool:
    """Every coin held in the week or listed for it has a bar at the last hour the week reads, or the exchange says it stopped: a stopped coin leaves at its last close, a late one must wait. A listed coin counts with no weight: with no bars it is stale, so the week would close on a universe without it."""
    a, b = np.searchsorted(win.t, week_span(monday))
    held = sum(np.abs(w[a:b]).sum(0) for w in paths.values()) > 0
    need = last_hour_read(monday)
    return all(last_bar[j] >= need or c in stopped for j, c in enumerate(win.coins) if held[j] or c in listed)


def update(root: Path, as_of: str, win: Window, events: list[Event], cohort: dict | None, last_bar: np.ndarray, stopped: Collection[str], lists: Mapping[str, list[str]] | None = None) -> list[dict]:
    """Write every open week whose bars are in, oldest first, and stop at the first that is not. Rows written."""
    rows = load_rows(root)
    weeks = open_weeks(rows, events, as_of)
    if not weeks:
        return []
    start = events[0].monday
    paths = weight_paths(win, events, start)
    stamps = {name: stamp_series(w, win) for name, w in paths.items()}
    new: list[dict] = []
    for monday in weeks:
        if not week_ready(monday, win, paths, last_bar, stopped, listed_in(monday, lists or {})):
            break
        new.append(week_row(monday, start, cohort, win, paths, stamps, rows + new))
    append_rows(root, new)
    return new


def target_weights(win: Window, events: list[Event], as_of: str) -> dict[str, float]:
    """The paper book at the stamp of the Monday, by coin, no zero weights."""
    if not events:
        return {}
    last = weight_paths(win, events, events[0].monday)["paper"][int(np.searchsorted(win.t, ts(as_of)))]
    return {c: float(x) for c, x in zip(win.coins, last) if x != 0.0}


def tripwire(rows: list[dict]) -> str:
    """NOT_ARMED before week 8 of the cohort, FIRED from the first week the z is under the limit, and it stays fired: the book keeps writing and the operator decides."""
    cohort = [r for r in rows if r["cohort_week"]]
    if len(cohort) < config.TRIPWIRE_FROM_WEEK:
        return "NOT_ARMED"
    fired = any(r["cohort_week"] >= config.TRIPWIRE_FROM_WEEK and r["portfolios"]["paper"]["z"] < config.TRIPWIRE_Z for r in cohort)
    return "FIRED" if fired else "OK"


def weeks_done(rows: list[dict]) -> int:
    return sum(1 for r in rows if r["cohort_week"])
