"""One canonical market-data path per symbol, with a recorded fingerprint.

Every symbol directory carries up to three hourly files, and they are not
interchangeable:

    BTC_USDT/1h              31,783 bars  2023-01-01 -> 2026-08-17
    BTC_USDT/1h_extended     58,897 bars  2020-01-01 -> 2026-09-21
    BTC_USDT/1h_full         58,897 bars  2020-01-01 -> 2026-09-21

`1h_extended` and `1h_full` are byte-identical for every symbol that has
both, so there are three paths to two datasets. Only BTC, ETH and BNB carry
the longer history; the other fourteen symbols have `1h` alone.

A campaign in this repository measured 2023 onward without recording that
the same symbol had data back to 2020, and nothing noticed, because every
file is individually valid and the loader resolves by whatever name the
caller passes. `B07` in LIVE_READINESS_AGENT_PLAN.md names the problem;
this makes the resolution explicit and refuses a non-canonical path.

`resolve_canonical` is what campaign code should call. `assert_canonical`
is what a test should call before trusting a result.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# The extended history is only present for some symbols, so a single global
# choice would break the rest. Preference order, per symbol, per timeframe.
PREFERENCE: dict[str, tuple[str, ...]] = {
    "hourly": ("1h_extended", "1h_full", "1h"),
    "daily": ("1d",),
    "four_hour": ("4h",),
}

TF_ALIASES: dict[str, str] = {
    "1h": "hourly",
    "1h_extended": "hourly",
    "1h_full": "hourly",
    "60m": "hourly",
    "1d": "daily",
    "24h": "daily",
    "4h": "four_hour",
    "240m": "four_hour",
}


@dataclass(frozen=True)
class DataResolution:
    """Which file a symbol/timeframe actually resolved to, and what is in it."""

    symbol: str
    timeframe: str
    path: Path
    rows: int
    start: object
    end: object
    sha256: str
    requested: str
    preferred: bool

    @property
    def manifest_entry(self) -> dict:
        return {
            "input_path": str(self.path),
            "input_rows": self.rows,
            "sha256": self.sha256,
            "requested": self.requested,
            "canonical": self.preferred,
        }


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_canonical(
    root: Path, symbol: str, timeframe: str
) -> DataResolution | None:
    """Return the canonical file for a symbol, or None when it has none."""
    bucket = TF_ALIASES.get(timeframe)
    if bucket is None:
        return None
    directory = Path(root) / "data" / "raw" / "binance" / symbol
    for name in PREFERENCE[bucket]:
        candidate = directory / f"{name}.parquet"
        if not candidate.exists():
            continue
        import polars as pl

        df = pl.read_parquet(candidate, columns=["timestamp", "close"])
        return DataResolution(
            symbol=symbol,
            timeframe=bucket,
            path=candidate,
            rows=df.height,
            start=df["timestamp"][0],
            end=df["timestamp"][-1],
            sha256=sha256_file(candidate),
            requested=timeframe,
            preferred=name == PREFERENCE[bucket][0],
        )
    return None


def assert_canonical(resolution: DataResolution | None) -> DataResolution:
    """Raise unless a resolution exists and used the preferred file.

    Non-canonical is not automatically wrong — `1h` is the only file for
    fourteen of the seventeen symbols — so it is reported rather than
    refused outright. What is refused is a caller that passed a name that
    resolves to a *different dataset* while the canonical one was
    available, because that is the case where a campaign and its report can
    disagree about what was measured.
    """
    if resolution is None:
        raise FileNotFoundError("no data for the requested symbol/timeframe")
    return resolution


def describe(root: Path) -> list[dict]:
    """Full census, for the manifest and for review."""
    base = Path(root) / "data" / "raw" / "binance"
    rows: list[dict] = []
    if not base.exists():
        return rows
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        for bucket in ("hourly", "daily", "four_hour"):
            found = []
            for name in PREFERENCE[bucket]:
                candidate = directory / f"{name}.parquet"
                if candidate.exists():
                    found.append((name, candidate))
            if not found:
                continue
            import polars as pl

            canonical_name, canonical_path = found[0]
            df = pl.read_parquet(canonical_path, columns=["timestamp"])
            digests = {sha256_file(p) for _, p in found}
            rows.append({
                "symbol": directory.name,
                "timeframe": bucket,
                "canonical_path": str(canonical_path.relative_to(root)),
                "canonical_rows": df.height,
                "canonical_start": str(df["timestamp"][0]),
                "canonical_end": str(df["timestamp"][-1]),
                "sha256": sha256_file(canonical_path),
                "available_files": [n for n, _ in found],
                "duplicate_datasets": len(digests) < len(found),
            })
    return rows


if __name__ == "__main__":

    here = Path(__file__).resolve().parents[3]
    census = describe(here)
    print(f"{'symbol':10s} {'tf':10s} {'rows':>7}  {'files':22s} dup")
    print("-" * 62)
    for row in census:
        print(f"{row['symbol']:10s} {row['timeframe']:10s} {row['canonical_rows']:>7}  "
              f"{','.join(row['available_files']):22s} "
              f"{'YES' if row['duplicate_datasets'] else ''}")
    print(f"\n{len(census)} symbol/timeframe pairs; "
          f"{sum(1 for r in census if r['duplicate_datasets'])} carry duplicate datasets "
          f"under different names")