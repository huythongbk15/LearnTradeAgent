"""Local store of BTCUSDT perp data for H-FLOW-BTC-02: hourly bars, 5 m metrics, funding.

Files under the root (default data/flowbtc, git-ignored):

- ``bars.parquet``: one row an hour by open_time (ms): open, close, vol, qv,
  cnt, tbv, the minute count ``n`` and ``src`` (0 = built from vision 1 m
  klines, 1 = a public 1 h kline, counted as 60 minutes).
- ``metrics.parquet``: one row a 5 m stamp ``t`` (ms, vision create_time) with
  the four series the features use.
- ``funding.parquet``: settled rates, ``t`` in seconds (the packet's form).
- ``meta.json``: how it was seeded, the last update, every gap the append
  found (``gaps``; a later fill may close one after the research end), every
  fill (``filled``) and, while one is open, the last fill failure.

``init_from`` seeds it once from the research packet and reports its holes:
hours missing or under 55 minutes, 5 m stamps missing or partly empty, and
funding settlements missing, before and after the research end (features.HOLDOUT).
``update`` appends closed data from the public endpoints through
tools/newperp/fetch.py, then fills holes from the research end on: hours with no
bar or under 55 minutes (public 1 h klines, src 1), 5 m stamps or cells with no
value inside the series' reach (29 days asked), and funding settlements missing
between two on file after the one at the research end. A row before the
research end is never changed, so parity holds. A fill asks again at every
update until the venue serves the hole; a failed one leaves the files as they
were. Each gap and fill is saved to meta.json before the rows it describes, so
a killed run keeps it. The /futures/data series keep about 30 days, so a longer
outage leaves a gap: it is recorded and its features stay unknown, as in research.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tools.flowbtc import features as F
from tools.newperp import fetch as fx
from tools.selection.fetch import FetchStop

ROOT = Path(__file__).resolve().parents[2] / "data" / "flowbtc"
SYMBOL = "BTCUSDT"
M5_MS = 300_000
# Each /futures/data series: (field in the answer, column of the vision metrics file, stamp offset in ms).
# The offset is vision create_time minus the API timestamp of the same value, measured by run.py
# --check-feed on 2026-11-05 over 2026-10-06 to 11-02 (about 8,050 stamps a series): open interest
# matched exactly at -5 min on 99% of stamps, the top-trader ratio to its 4 published decimals at -5 min,
# the all-account ratio at -5 min within about 1e-4 relative (median; more than rounding explains), the
# taker ratio at 0 within 1.4e-3. Every other offset was about 10 times worse. On those offsets all four
# series reach the same vision stamp.
SERIES = {
    fx.OPEN_INTEREST_HIST: ("sumOpenInterest", "sum_open_interest", -300_000),
    fx.TOP_POSITION_RATIO: ("longShortRatio", "sum_toptrader_long_short_ratio", -300_000),
    fx.GLOBAL_ACCOUNT_RATIO: ("longShortRatio", "count_long_short_ratio", -300_000),
    fx.TAKER_RATIO: ("buySellRatio", "sum_taker_long_short_vol_ratio", 0),
}
PUBLISH_LAG_MS = 3_600_000   # a 5 m stamp older than this that no series served is a gap, not a late row
FUNDING_LIMIT = 1000
K1M_COLS = ["open_time", "open", "close", "volume", "quote_volume", "count", "taker_buy_volume"]
HOLDOUT_MS = int(F.HOLDOUT.timestamp() * 1000)   # research rows end before this; a fill never changes one of them
KLINE_LIMIT = 1500
METRICS_REACH_MS = 29 * 24 * F.H_MS   # the /futures/data series keep about 30 days; a fill asks inside 29
FUNDING_EVERY_S = 8 * 3600            # BTCUSDT settles every 8 h; a wider step between two settlements is a hole


class StoreError(RuntimeError):
    pass


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%MZ")


def _write(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _ranges(missing: np.ndarray, step: int) -> list[tuple[int, int]]:
    """Runs of consecutive missing stamps as (first, last)."""
    out: list[tuple[int, int]] = []
    for t in sorted(int(x) for x in missing):
        if out and t == out[-1][1] + step:
            out[-1] = (out[-1][0], t)
        else:
            out.append((t, t))
    return out


def _windows(stamps: np.ndarray, step: int, limit: int) -> list[tuple[int, int]]:
    """Stamps cut into [first, end) windows of at most ``limit`` steps, each starting on a stamp: one call a window."""
    out: list[tuple[int, int]] = []
    for t in sorted(int(x) for x in stamps):
        if out and t + step - out[-1][0] <= limit * step:
            out[-1] = (out[-1][0], t + step)
        else:
            out.append((t, t + step))
    return out


def _filled(series: str, stamps, step: int, now_ms: int) -> list[dict]:
    """meta.json records of a fill: one a run of consecutive stamps, with its count."""
    return [{"series": series, "from": _iso(a), "to": _iso(b), "count": (b - a) // step + 1, "at": _iso(now_ms)}
            for a, b in _ranges(np.asarray(stamps), step)]


def bar_holes(b: pd.DataFrame) -> np.ndarray:
    """Open times from the research end to the last stored bar with no bar, or one of fewer than 55 minutes."""
    grid = np.arange(HOLDOUT_MS, int(b["open_time"].max()) + F.H_MS, F.H_MS, dtype="int64")
    return np.setdiff1d(grid, b.loc[b["n"] >= F.MIN_MINUTES, "open_time"].to_numpy())


def _funding_missing(t_s: np.ndarray) -> np.ndarray:
    """Seconds of the settlements missing between two on file, on the 8 h step."""
    t = np.sort(np.asarray(t_s, dtype="int64"))
    out = [np.arange(a + FUNDING_EVERY_S, b, FUNDING_EVERY_S) for a, b in zip(t[:-1], t[1:]) if b - a > FUNDING_EVERY_S]
    return np.concatenate(out) if out else np.array([], dtype="int64")


def hole_report(b: pd.DataFrame, m: pd.DataFrame, fu: pd.DataFrame) -> dict:
    """What the features would see as unknown, before the research end (left as research had it) and after (update fills).

    Bars: hours missing on the grid from the first bar to the last, and hours
    under 55 minutes. Metrics: 5 m stamps missing on the grid, and stamps with a
    series empty. Funding: settlements missing on the 8 h step; one at the
    research end counts as before it (the last research bar saw it). Counts and
    UTC times only.
    """
    hold = HOLDOUT_MS
    bt = b["open_time"].to_numpy(dtype="int64")
    miss = np.setdiff1d(np.arange(bt.min(), bt.max() + F.H_MS, F.H_MS, dtype="int64"), bt)
    short = bt[b["n"].to_numpy() < F.MIN_MINUTES]
    after = np.union1d(miss[miss >= hold], short[short >= hold])
    mt = m["t"].to_numpy(dtype="int64")
    mmiss = np.setdiff1d(np.arange(mt.min(), mt.max() + M5_MS, M5_MS, dtype="int64"), mt)
    part = mt[m[F.METRIC_COLS].isna().any(axis=1).to_numpy()]
    fmiss = _funding_missing(fu["t"].to_numpy()) * 1000
    return {
        "bars_missing_before_holdout": int((miss < hold).sum()),
        "bars_under_55_min_before_holdout": int((short < hold).sum()),
        "bars_missing_after_holdout": int((miss >= hold).sum()),
        "bars_under_55_min_after_holdout": int((short >= hold).sum()),
        "bars_holes_after_holdout": [{"from": _iso(a), "to": _iso(z), "hours": (z - a) // F.H_MS + 1}
                                     for a, z in _ranges(after, F.H_MS)],
        "metrics_missing_before_holdout": int((mmiss < hold).sum()),
        "metrics_missing_after_holdout": int((mmiss >= hold).sum()),
        "metrics_partial_after_holdout": int((part >= hold).sum()),
        "funding_missing_before_holdout": int((fmiss <= hold).sum()),
        "funding_missing_after_holdout": int((fmiss > hold).sum()),
    }


class Store:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)

    # ── files ──────────────────────────────────────────────────────────────

    @property
    def bars_path(self) -> Path:
        return self.root / "bars.parquet"

    @property
    def metrics_path(self) -> Path:
        return self.root / "metrics.parquet"

    @property
    def funding_path(self) -> Path:
        return self.root / "funding.parquet"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def exists(self) -> bool:
        return any(p.exists() for p in (self.bars_path, self.metrics_path, self.funding_path))

    def ready(self) -> bool:
        return all(p.exists() for p in (self.bars_path, self.metrics_path, self.funding_path))

    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

    def _save_meta(self, meta: dict) -> None:
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=1, sort_keys=True))
        os.replace(tmp, self.meta_path)

    def load_bars(self) -> pd.DataFrame:
        return pd.read_parquet(self.bars_path)

    def load_metrics(self) -> pd.DataFrame:
        return pd.read_parquet(self.metrics_path)

    def load_funding(self) -> pd.DataFrame:
        return pd.read_parquet(self.funding_path)

    def frames(self, end: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
        """Bars, metrics and funding in the form features.features takes; bars and metrics cut before ``end``."""
        return F.bars(self.load_bars(), end), F.metrics(self.load_metrics(), end), F.funding(self.load_funding())

    def coverage(self) -> dict[str, int]:
        """The last stored stamp of each file, in ms: bar open, metric stamp, funding settlement."""
        b = pd.read_parquet(self.bars_path, columns=["open_time", "n"])
        b = b[b["n"] >= F.MIN_MINUTES]
        return {"bar": int(b["open_time"].max()), "metric": int(pd.read_parquet(self.metrics_path, columns=["t"])["t"].max()),
                "funding": int(pd.read_parquet(self.funding_path, columns=["t"])["t"].max()) * 1000}

    # ── seed ───────────────────────────────────────────────────────────────

    def init_from(self, panel_dir: Path, funding_parquet: Path) -> dict:
        """Seed once from the packet: 1 m klines to hours (minute count kept), metrics and funding as they are.

        Every row is kept, the research holdout included: live features need
        the last 30 days. Parity cuts at the holdout as combo does. The counts
        include ``hole_report``: holes after the research end are filled by the
        next ``update``; those before it stay, as research had them.
        """
        if self.exists():
            raise StoreError(f"store exists under {self.root}; refusing to reseed")
        k = pd.read_parquet(Path(panel_dir) / "k1m.parquet", columns=K1M_COLS)
        b = F.hourly_from_1m(k)
        b["cnt"] = b["cnt"].astype("float64")
        b["src"] = np.int8(0)
        m = pd.read_parquet(Path(panel_dir) / "m5.parquet", columns=["t"] + F.METRIC_COLS)
        m = m.drop_duplicates("t").sort_values("t").reset_index(drop=True)
        fu = pd.read_parquet(funding_parquet, columns=["t", "rate"]).sort_values("t").reset_index(drop=True)
        self.root.mkdir(parents=True, exist_ok=True)
        _write(b, self.bars_path)
        _write(m, self.metrics_path)
        _write(fu, self.funding_path)
        counts = {"bars": len(b), "bars_under_55_min": int((b["n"] < F.MIN_MINUTES).sum()), "metrics": len(m),
                  "funding": len(fu), "bars_last": _iso(int(b["open_time"].max())), "metrics_last": _iso(int(m["t"].max())),
                  "funding_last": _iso(int(fu["t"].max()) * 1000), **hole_report(b, m, fu)}
        self._save_meta({"seeded": {"panel": str(panel_dir), "funding": str(funding_parquet), "at": _iso(_now_ms()), **counts},
                         "gaps": [], "updates": 0})
        return counts

    # ── update from the public endpoints ───────────────────────────────────

    def fetch_bars(self, client: fx.Client, start_ms: int, end_ms: int) -> pd.DataFrame:
        rows = fx.klines(client, SYMBOL, "1h", start_ms, end_ms)
        d = pd.DataFrame({
            "open_time": [int(r[0]) for r in rows], "open": [float(r[1]) for r in rows],
            "close": [float(r[4]) for r in rows], "vol": [float(r[5]) for r in rows],
            "qv": [float(r[7]) for r in rows], "cnt": [float(r[8]) for r in rows],
            "tbv": [float(r[9]) for r in rows]})
        d["n"] = np.int64(60)
        d["src"] = np.int8(1)
        return d.astype({"open_time": "int64"})

    def fetch_metrics(self, client: fx.Client, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, int]:
        """Rows stamped (vision time) in [start_ms, end_ms) and the stamp each series has reached.

        Returns the merged rows (one a stamp any series served, a series that
        lacks it is NaN there) and the last stamp every series has served.
        """
        cols: dict[str, dict[int, float]] = {}
        reached = []
        for path, (_, col, _) in SERIES.items():
            vals = self._series(client, path, start_ms, end_ms)
            cols[col] = vals
            reached.append(max(vals) if vals else -1)
        stamps = sorted(set().union(*(set(v) for v in cols.values())))
        d = pd.DataFrame({"t": np.asarray(stamps, dtype="int64")})
        for col in F.METRIC_COLS:
            d[col] = [cols[col].get(t, np.nan) for t in stamps]
        return d, min(reached)

    def _series(self, client: fx.Client, path: str, start_ms: int, end_ms: int) -> dict[int, float]:
        """One /futures/data series by vision stamp in [start_ms, end_ms)."""
        field, _, off = SERIES[path]
        return {int(r["timestamp"]) + off: float(r[field])
                for r in fx.stats(client, path, SYMBOL, "5m", start_ms - off, end_ms - off)}

    def fetch_funding(self, client: fx.Client, start_ms: int, end_ms: int) -> pd.DataFrame:
        out: list[tuple[int, float]] = []
        while start_ms < end_ms:
            rows = fx.funding(client, SYMBOL, start_ms, end_ms)
            out += rows
            if len(rows) < FUNDING_LIMIT:
                break
            start_ms = rows[-1][0] + 1
        d = pd.DataFrame({"t": [t // 1000 for t, _ in out], "rate": [r for _, r in out]}).astype({"t": "int64", "rate": "float64"})
        return d.drop_duplicates("t")

    # ── fill holes after the research end ───────────────────────────────────

    def _land(self, meta: dict, rec: list[dict], df: pd.DataFrame, path: Path) -> None:
        """Save a fill's record to meta.json, then write its rows, as the model's hash goes before its file.

        A run killed between the two keeps the record; the hole is still on
        file, so the next update asks for it and records it again. A record
        save or a write that fails takes the record back out of ``meta``, and
        update's last save of meta.json drops it from the file.
        """
        filled = meta.setdefault("filled", [])
        filled.extend(rec)
        try:
            self._save_meta(meta)
            _write(df, path)
        except Exception:
            del filled[len(filled) - len(rec):]
            raise

    def _fill_bars(self, client: fx.Client, now_ms: int, meta: dict) -> int:
        """Hours from the research end to the last bar with no bar or under 55 minutes, from public 1 h klines (src 1)."""
        old = self.load_bars()
        want = bar_holes(old)
        got = [g for g in (self.fetch_bars(client, a, z) for a, z in _windows(want, F.H_MS, KLINE_LIMIT)) if len(g)]
        if not got:
            return 0
        new = pd.concat(got, ignore_index=True)
        new = new[new["open_time"].isin(want)].drop_duplicates("open_time")
        if not len(new):
            return 0
        keep = old[~old["open_time"].isin(new["open_time"])]        # only short hours of ``want`` are replaced
        self._land(meta, _filled("bars", new["open_time"], F.H_MS, now_ms),
                   pd.concat([keep, new], ignore_index=True).sort_values("open_time"), self.bars_path)
        return len(new)

    def _fill_metrics(self, client: fx.Client, now_ms: int, meta: dict) -> int:
        """Empty cells and missing stamps from the research end to the last stamp, inside the series' reach.

        Each series is asked only for the stamps it lacks; a value on file is
        never replaced. Returns the stamps that gained a value.
        """
        old = self.load_metrics().set_index("t")
        lo = max(HOLDOUT_MS, -(-(now_ms - METRICS_REACH_MS) // M5_MS) * M5_MS)
        grid = np.arange(lo, int(old.index.max()) + M5_MS, M5_MS, dtype="int64")
        cur = old.reindex(grid)
        patch = pd.DataFrame(np.nan, index=grid, columns=F.METRIC_COLS)
        for path, (_, col, _) in SERIES.items():
            want = grid[cur[col].isna().to_numpy()]
            wanted = set(want.tolist())
            for a, z in _windows(want, M5_MS, fx.STAT_LIMIT):
                vals = self._series(client, path, a, z)
                ts = [t for t in sorted(vals) if t in wanted]
                if ts:
                    patch.loc[ts, col] = [vals[t] for t in ts]
        patch = patch.dropna(how="all")
        if not len(patch):
            return 0
        new = old.combine_first(patch).sort_index()[F.METRIC_COLS].rename_axis("t").reset_index()
        self._land(meta, _filled("metrics", patch.index, M5_MS, now_ms), new.astype({"t": "int64"}), self.metrics_path)
        return len(patch)

    def _fill_funding(self, client: fx.Client, now_ms: int, meta: dict) -> int:
        """Settlements missing between two on file after the research end. The endpoint keeps the whole history.

        The settlement at the research end is the last one research's last bar
        (closing then) saw, so it counts as research's and is not filled.
        """
        old = self.load_funding()
        t = np.sort(old["t"].to_numpy(dtype="int64"))
        hold = HOLDOUT_MS // 1000
        gaps = [(max(a, hold), b) for a, b in zip(t[:-1].tolist(), t[1:].tolist()) if max(a, hold) + FUNDING_EVERY_S < b]
        got = [self.fetch_funding(client, (a + 1) * 1000, b * 1000) for a, b in gaps]   # the later settlement's own row is left out
        got = [g for g in got if len(g)]
        if not got:
            return 0
        new = pd.concat(got, ignore_index=True)
        new = new[(new["t"] > hold) & ~new["t"].isin(t)].drop_duplicates("t")
        if not len(new):
            return 0
        self._land(meta, _filled("funding", new["t"] * 1000, FUNDING_EVERY_S * 1000, now_ms),
                   pd.concat([old, new], ignore_index=True).sort_values("t"), self.funding_path)
        return len(new)

    def update(self, client: fx.Client, now_ms: int) -> dict:
        """Append closed data after the last stored row, then fill holes after the research end.

        Returns counts (``filled_*``: hours, 5 m stamps and settlements filled).
        Gaps and fills go to meta.json before the rows they describe are
        written, so a killed run keeps them (a fill the kill cut off is asked
        for and recorded again). A gap found again unchanged after a kill is
        not recorded twice. One found changed, because the venue served more of
        its rows since, is recorded again as found, and the first record stays:
        it can name stamps that are now on file in full. A gap at the end of
        the data, with no row after it yet, is found again at each update with
        a later end, and each end is a new record, until rows arrive after it.

        Each fill runs on its own. One that fails leaves its file as it was,
        is listed in ``fill_failed`` and in meta.json's ``last_fill_failure``
        (dropped by the next update whose fills all succeed) and is asked again
        at the next update; the other fills still run. A 418 or 429 (FetchStop)
        stops all asking this run: in the append it is raised at once; in a
        fill, the fills after it are skipped and it is raised once meta.json is
        saved, so the caller sees a failed update.
        """
        if not self.ready():
            raise StoreError(f"no store under {self.root}; seed it with --init-from first")
        meta = self.meta()
        out = {"bars": 0, "metrics": 0, "funding": 0, "gaps": 0, "filled_bars": 0, "filled_metrics": 0, "filled_funding": 0}
        known = meta.setdefault("gaps", [])

        def note(found: list[dict]) -> None:
            """Record new gaps before the rows around them are written; only a gap equal to one on file is skipped.

            One at the end is found again, with a later end, until data arrive after it.
            """
            found = [g for g in found if g not in known]
            if found:
                known.extend(found)
                out["gaps"] += len(found)
                self._save_meta(meta)

        old = self.load_bars()
        start, end = int(old["open_time"].max()) + F.H_MS, now_ms // F.H_MS * F.H_MS   # bars opening before end are closed
        if start < end:
            new = self.fetch_bars(client, start, end)
            new = new[(new["open_time"] >= start) & (new["open_time"] < end)]
            missing = np.setdiff1d(np.arange(start, end, F.H_MS), new["open_time"].to_numpy())
            note([{"series": "bars", "from": _iso(a), "to": _iso(b)} for a, b in _ranges(missing, F.H_MS)])
            if len(new):
                _write(pd.concat([old, new], ignore_index=True).drop_duplicates("open_time").sort_values("open_time"),
                       self.bars_path)
            out["bars"] = len(new)

        oldm = self.load_metrics()
        start = int(oldm["t"].max()) + M5_MS
        end = now_ms // M5_MS * M5_MS + M5_MS                      # every stamp up to now
        if start < end:
            new, reached = self.fetch_metrics(client, start, end)
            cut = max(reached, end - M5_MS - PUBLISH_LAG_MS)       # wait for a series still behind, but not for long
            new = new[(new["t"] >= start) & (new["t"] <= cut)]
            if cut >= start:
                missing = np.setdiff1d(np.arange(start, cut + 1, M5_MS), new["t"].to_numpy())
                found = [{"series": "metrics", "from": _iso(a), "to": _iso(b)} for a, b in _ranges(missing, M5_MS)]
                part = new[new[F.METRIC_COLS].isna().any(axis=1)]
                if len(part):
                    found.append({"series": "metrics_partial", "rows": int(len(part)), "from": _iso(int(part["t"].min())),
                                  "to": _iso(int(part["t"].max()))})
                note(found)
            if len(new):
                _write(pd.concat([oldm, new], ignore_index=True).drop_duplicates("t").sort_values("t"), self.metrics_path)
            out["metrics"] = len(new)

        oldf = self.load_funding()
        start = (int(oldf["t"].max()) + 1) * 1000
        if start < now_ms:
            new = self.fetch_funding(client, start, now_ms + 1)
            new = new[new["t"] > int(oldf["t"].max())]
            if len(new):
                _write(pd.concat([oldf, new], ignore_index=True).drop_duplicates("t").sort_values("t"), self.funding_path)
            out["funding"] = len(new)

        failed: dict[str, str] = {}
        stop: FetchStop | None = None
        for name, fill in (("bars", self._fill_bars), ("metrics", self._fill_metrics), ("funding", self._fill_funding)):
            try:
                out[f"filled_{name}"] = fill(client, now_ms, meta)
            except FetchStop as exc:   # the venue said stop: nothing more is asked this run
                failed[name], stop = type(exc).__name__, exc
                break
            except Exception as exc:   # public data: the file stays as it was and the next update asks again
                failed[name] = type(exc).__name__
        if failed:
            out["fill_failed"] = list(failed)
            meta["last_fill_failure"] = {"at": _iso(now_ms), "errors": failed}
        else:
            meta.pop("last_fill_failure", None)
        meta["updates"] = int(meta.get("updates", 0)) + 1
        meta["last_update"] = _iso(now_ms)
        self._save_meta(meta)
        if stop is not None:
            raise stop
        return out


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
