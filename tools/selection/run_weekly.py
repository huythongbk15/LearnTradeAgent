"""The weekly paper selection run. It writes records only: no order, no key, no private call, no way to reach one.

    python -m tools.selection.run_weekly [--as-of YYYY-MM-DD] [--no-fetch] [--seed-only]

Steps: seed the store if it is new, refresh the pairs and their daily bars with cutoff as_of, build the lists, refresh the hourly bars, build the candidates, score, close the finished weeks in the ledger, decide, then write runs/<as_of>/decision.json, policy_draft.json, target.json, status.json and manifest.json. manifest.json is written last of all and marks a run as finished: a rerun of a finished Monday changes nothing, even if that run failed. The wall clock lives in the manifest only, so equal inputs give equal bytes in every other file.

A step that fails leaves a mark and the run goes on to its record. Whatever cannot be computed makes the due book hold. Exit code 0 means a record is on disk, 2 means the run was refused and wrote nothing.

The first run backfills the rule lists from 2026-08-10 in order. rule_list reads only bars closed before each Monday, so that does not leak, but exchangeInfo names only pairs trading today, so a pair delisted since is missing from those lists. A later run fills every Monday with no list, except a Monday that has a finished run of its own: that run's record said what it knew. So a first run that fails costs its own Monday, not the weeks before it.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import resource
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from tools.selection import config, decide, fetch, ledger, policy, status, universe
from tools.selection import library as lib
from tools.selection import panel as pn
from tools.selection import selector as sel
from tools.selection.config import BTC

REFRESH_WEEKS = 14              # lists a score can reach: 90 days, plus the list in force on the first of them


class Refused(Exception):
    """The run cannot start and wrote nothing."""


@dataclass
class Trail:
    """What went wrong on the way. A masked coin anywhere counts as a refusal, and the record names no masked coin."""

    errors: list[str] = field(default_factory=list)
    seal_refusals: int = 0

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        try:
            yield
        except config.SealedSymbolError:
            self.seal_refusals += 1
            self.errors.append(f"{name}: SealedSymbolError")
        except Exception as e:
            self.errors.append(f"{name}: {type(e).__name__}")
            traceback.print_exc()


@dataclass
class Built:
    """What the build reached. Each field stays at its default if the step before it failed."""

    health: decide.Health | None = None
    list_monday: str | None = None
    window: ledger.Window | None = None
    lists: dict[str, list[str]] = field(default_factory=dict)
    last_bar: np.ndarray | None = None
    slots: tuple[str, ...] | None = None
    best: tuple[str, ...] | None = None
    scores_sha256: str | None = None


@dataclass(frozen=True)
class Outcome:
    as_of: str
    rerun: bool
    decision: str
    reasons: tuple[str, ...]


def last_monday(now: datetime) -> str:
    day = now.astimezone(UTC).date()
    return (day - timedelta(days=day.weekday())).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


def _mondays(first: date, last: str) -> Iterator[date]:
    while first.isoformat() <= last:
        yield first
        first += timedelta(days=7)


def update_lists(as_of: str, root: Path, report: fetch.Report, history: list[dict]) -> int:
    """Rule lists of every Monday with no list and no finished run of its own, oldest first, on the pairs trading now. A list enters only if it has all its names and the daily bars behind it came in: without one, Mem keeps the last list. Lists added."""
    if as_of < config.RULE_FIRST_WEEK:
        return 0
    finished = {h["as_of"] for h in history}
    stored = universe.load_lists(root)
    daily = pn.load_daily(root)
    if report.tradable is not None:
        keep = set(report.tradable)
        daily = {k: v for k, v in daily.items() if k in keep}
    added = 0
    for monday in _mondays(date.fromisoformat(config.RULE_FIRST_WEEK), as_of):
        key = monday.isoformat()
        if key not in stored and key not in finished and report.daily_fail_share <= config.LIST_FETCH_FAIL_LIMIT:
            names = universe.rule_list(daily, monday)
            if len(names) == config.LIST_N:
                universe.store_list(monday, names, root)
                added += 1
    return added


def hourly_coins(lists: dict[str, list[str]], as_of: str, weeks: list[str]) -> list[str]:
    """BTC and every coin on a list a score or an open ledger week can reach. A coin off those lists stays where it is, and fills what it missed when it comes back."""
    since = date.fromisoformat(as_of) - timedelta(weeks=REFRESH_WEEKS)
    if weeks:
        since = min(since, date.fromisoformat(weeks[0]) - timedelta(weeks=2))
    names = {BTC}
    for k, v in lists.items():
        if since.isoformat() <= k <= as_of:
            names |= set(v)
    for week in weeks:                  # a week must not wait on a coin nobody asks for
        names |= ledger.listed_in(week, lists)
    return sorted(names)


def build(as_of: str, root: Path, weeks: list[str], out: Built) -> None:
    """Candidates one at a time, then scores and the lists of the Monday. Fields of out fill as far as the build gets."""
    panel = pn.load_panel(as_of, root)
    out.lists = panel.lists
    keys = [k for k in panel.lists if k <= as_of]
    out.list_monday = keys[-1] if keys else None
    d = lib.Data(panel, live=True)
    names = panel.lists[out.list_monday] if out.list_monday else []
    del panel
    out.health = decide.data_health(d.C, d.coins, names)
    out.last_bar = d.ff[-1].copy()
    t = d.t
    s = lib.build_series(d, keep_from=ledger.keep_from(t, weeks))
    out.window = ledger.window_of(s, t, d.coins)
    sb = sel.score_book(s.net, s.t)
    day = config.ts(as_of) // 86400
    row, ch = sel.select(sb, day)
    _, best = sel.select(sb, day, sb.best)
    out.slots, out.best = tuple(sel.slot_names(ch)), tuple(sel.slot_names(best))
    out.scores_sha256 = hashlib.sha256(row.tobytes()).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_tree(folders: list[Path]) -> str:
    h = hashlib.sha256()
    for folder in folders:
        for path in sorted(folder.glob("*")):
            h.update(f"{folder.name}/{path.name} {_sha_file(path)}\n".encode())
    return h.hexdigest()


def manifest(root: Path, seed: Path, folder: Path, as_of: str, report: fetch.Report, trail: Trail, lists_added: int, rows_added: int, started: datetime, t0: float) -> dict:
    """Hashes of what the run read and wrote. The only record with a wall clock."""
    code = sorted(Path(__file__).parent.glob("*.py"))
    seed_file = seed / "SEED.sha256"
    return {
        "as_of": as_of,
        "files": {p.name: _sha_file(p) for p in sorted(folder.glob("*.json"))},
        "ledger_sha256": _sha_file(ledger.ledger_path(root)) if ledger.ledger_path(root).exists() else None,
        "store_sha256": _sha_tree([config.bars_dir(root), config.daily_dir(root), config.lists_dir(root)]),
        "seed_sha256": _sha_file(seed_file) if seed_file.exists() else None,
        "code_sha256": hashlib.sha256("".join(_sha_file(p) for p in code).encode()).hexdigest(),
        "fetch": {"attempted": report.attempted, "calls": report.calls, "stop": report.stop, "errors": report.errors, "daily_failed": report.daily_failed, "hourly_failed": report.hourly_failed},
        "lists_added": lists_added,
        "rows_added": rows_added,
        "errors": trail.errors,
        "started_at": started.isoformat(),
        "finished_at": _now().isoformat(),
        "secs": round(time.monotonic() - t0, 1),
        "peak_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
    }


def _run(as_of: str, fetching: bool, client: fetch.Client | None, now: datetime, root: Path) -> Outcome:
    folder = config.runs_dir(root) / as_of
    if (folder / "manifest.json").exists():
        done = decide.read_json(folder / "decision.json")
        return Outcome(as_of, True, done["decision"], tuple(done["reasons"]))
    history = decide.load_history(root)
    if history and as_of <= history[-1]["as_of"]:
        raise Refused(f"{history[-1]['as_of']} is already finished, a run cannot go back to {as_of}")
    t0 = time.monotonic()
    trail = Trail()
    seed = config.seed_dir()
    with trail.step("seed"):
        if not config.tier_path(root).exists():
            pn.seed_store(seed, root)
    seeded = config.tier_path(root).exists()     # a fetch before the seed step would lose the seed's rows

    report = fetch.Report(attempted=fetching and seeded)
    client = client or fetch.Client()
    if report.attempted:
        with trail.step("fetch_pairs"):
            fetch.refresh_pairs(client, as_of, root, report)
    lists_added = 0
    with trail.step("lists"):
        lists_added = update_lists(as_of, root, report, history)

    events = decide.events_of(history)
    cohort = None
    cohort_read = False
    with trail.step("cohort_read"):
        cohort = decide.load_cohort(root)
        cohort_read = True
    rows: list[dict] | None = None
    weeks: list[str] = []
    with trail.step("ledger_read"):
        rows = ledger.load_rows(root)
        weeks = ledger.open_weeks(rows, events, as_of)
    if report.attempted:
        with trail.step("fetch_bars"):
            fetch.refresh_hourly(client, hourly_coins(universe.load_lists(root), as_of, weeks), as_of, root, report)
    trail.errors.extend(f"fetch: {x}" for x in report.errors)
    if report.stop:
        trail.errors.append(f"fetch: {report.stop}")

    built = Built()
    with trail.step("build"):
        build(as_of, root, weeks, built)
    written: list[dict] = []
    if built.window is not None and rows is not None and cohort_read:    # a week written without the cohort would lose its number for good
        with trail.step("ledger"):
            written = ledger.update(root, as_of, built.window, events, cohort, built.last_bar, report.stopped, built.lists)
            rows = rows + written
    tripwire, done = status.last_known(root) if rows is None else (ledger.tripwire(rows), ledger.weeks_done(rows))

    names: list[str] = []
    with trail.step("list_read"):
        names = universe.load_lists(root).get(as_of, [])
    facts = decide.Facts(
        health=built.health,
        list_names=tuple(names),
        daily_fail_share=report.daily_fail_share,
        late=decide.is_late(as_of, now),
        seal_refusals=trail.seal_refusals,
        slots=built.slots,
        policy_expired=decide.policy_expired(history, as_of),
        tripwire_fired=tripwire == "FIRED",
    )
    reasons = decide.reasons_for(facts)
    selected = decide.selects(reasons, built.slots)
    phase = config.phase_of(as_of)
    after = [*events, decide.Event(as_of, phase, built.slots, built.best)] if selected else events
    decision = decide.record(as_of, facts, reasons, selected, after, built.list_monday, built.scores_sha256, decide.health_counts(built.health, report, len(weeks) - len(written)), list(trail.errors))

    text = decide.dumps(decision)
    decide.write_text(folder / "decision.json", text)
    with trail.step("policy"):
        artifact = policy.draft(decision, hashlib.sha256(text.encode()).hexdigest())
        policy.publish(root, artifact)
        decide.write_text(folder / "policy_draft.json", artifact.to_json() + "\n")
    weights: dict[str, float] = {}
    if built.window is not None:
        with trail.step("target"):
            weights = ledger.target_weights(built.window, after, as_of)
    target = {"as_of": as_of, "decision": decision["decision"], "paper_only": True, "consumer": "none", "complete": built.window is not None, "weights": weights, "books": decision["books"]}
    decide.write_text(folder / "target.json", decide.dumps(target))
    if cohort_read:
        cohort = decide.start_cohort(root, as_of, reasons, selected)
    status.write(root, status.build(decision, tripwire, cohort, done, len(trail.errors)))
    decide.write_text(folder / "manifest.json", decide.dumps(manifest(root, seed, folder, as_of, report, trail, lists_added, len(written), now, t0)))
    return Outcome(as_of, False, decision["decision"], tuple(reasons))


def _root() -> Path:
    try:
        return config.selection_root()
    except ValueError as e:
        raise Refused(str(e)) from e


@contextmanager
def holding(root: Path) -> Iterator[None]:
    """The root, for this process alone."""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".lock", "w", encoding="utf8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise Refused(f"another run holds {root}") from e
        yield


def run(as_of: str, fetching: bool = True, client: fetch.Client | None = None, now: datetime | None = None) -> Outcome:
    """One Monday. Refused if the day is not a Monday of the grid, is still to come, or another run holds the root."""
    now = now or _now()
    try:
        as_of = date.fromisoformat(as_of).isoformat()
        weeks = config.weeks_since_first(as_of)
    except ValueError as e:
        raise Refused(str(e)) from e
    if weeks < 0:
        raise Refused(f"{as_of} is before the first grid day")
    if decide.stamp_of(as_of) > now:
        raise Refused(f"{as_of} is still to come")
    root = _root()
    with holding(root):
        return _run(as_of, fetching, client, now, root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", help="the Monday of the run, default the last Monday 00:00 UTC")
    ap.add_argument("--no-fetch", action="store_true", help="use the store as it is, no network")
    ap.add_argument("--seed-only", action="store_true", help="convert the seed to the store and stop")
    args = ap.parse_args(argv)
    try:
        if args.seed_only:
            root = _root()
            with holding(root):
                print(pn.seed_store(config.seed_dir(), root))
            return 0
        out = run(args.as_of or last_monday(_now()), fetching=not args.no_fetch)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    note = "already finished, nothing changed" if out.rerun else "record written"
    print(f"{out.as_of} {out.decision} {','.join(out.reasons)}: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
