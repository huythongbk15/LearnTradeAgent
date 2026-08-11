#!/usr/bin/env python3
"""Freeze the final holdout window and publish an immutable research manifest (P2).

The final holdout is the last 6-12 months of market data that must NEVER be
used for parameter selection, feature engineering, or any research decision.
Only after the holdout period elapses and the evidence is scored once may the
manifest be superseded.

The manifest records:
  * the exact holdout window per symbol (aligned to the common end timestamp),
  * a SHA-256 fingerprint of every dataset file it covers,
  * the freeze date, generator commit and rules that bind downstream tools.

Usage:
  python scripts/generate_holdout_manifest.py --months 6
  python scripts/generate_holdout_manifest.py --months 12 --data-dir data/raw/binance
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

DEFAULT_DATA_DIR = Path("data/raw/binance")
MANIFEST_PATH = Path("data/research_manifest.json")
HOLDOUT_RULES = {
    "holdout_purpose": (
        "The final holdout is scored exactly once, after the freeze date, to "
        "produce the release-gate evidence. It must not influence any earlier "
        "research decision."
    ),
    "forbidden_uses": [
        "parameter selection / optimization",
        "feature engineering",
        "model training or validation",
        "strategy ranking or ensemble weighting",
        "any decision that changes a config shipped to live",
    ],
    "immutability": (
        "Do not edit data/research_manifest.json. To extend the holdout, "
        "publish a new manifest with a later freeze date; the old manifest "
        "remains the record for its window."
    ),
}


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_datasets(data_dir: Path) -> dict[str, dict]:
    """Map ``symbol/timeframe -> parquet path``."""
    datasets: dict[str, dict] = {}
    for symbol_dir in sorted(data_dir.iterdir()):
        if not symbol_dir.is_dir():
            continue
        for parquet in sorted(symbol_dir.glob("*.parquet")):
            key = f"{symbol_dir.name}/{parquet.stem}"
            datasets[key] = {
                "path": str(parquet),
                "size_bytes": parquet.stat().st_size,
                "sha256": _sha256(parquet),
            }
    return datasets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=6, choices=(6, 12))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    datasets = collect_datasets(data_dir)
    if not datasets:
        print(f"no datasets under {data_dir}", file=sys.stderr)
        return 1

    # Align the holdout end to the earliest per-symbol end across the chosen
    # timeframe so no symbol leaks future data into the window.
    ends: list = []
    for key, meta in datasets.items():
        if not key.endswith(f"/{args.timeframe}"):
            continue
        df = pl.read_parquet(meta["path"])
        ts_col = next(
            (c for c in df.columns if "time" in c.lower() or "timestamp" in c.lower()),
            None,
        )
        if ts_col is None:
            continue
        ends.append(df[ts_col].max())
    if not ends:
        print(f"no {args.timeframe} datasets found", file=sys.stderr)
        return 1
    end = min(ends)
    start = end - timedelta(days=30 * args.months)

    now = datetime.now(UTC)
    manifest = {
        "schema_version": 1,
        "freeze_date": now.isoformat(),
        "generator_commit": _git_commit(),
        "holdout_months": args.months,
        "timeframe": args.timeframe,
        "window": {
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
        },
        "rules": HOLDOUT_RULES,
        "datasets": datasets,
        "integrity": "",
    }
    # Bind the manifest with a SHA-256 of its own serialized content.
    body = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    manifest["integrity"] = hashlib.sha256(body.encode("utf-8")).hexdigest()

    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        print(f"manifest written: {MANIFEST_PATH}")
    print(
        f"holdout window ({args.months}m): {start.date()} -> {end.date()} "
        f"({(end - start).days} days)"
    )
    print(f"datasets fingerprinted: {len(datasets)}")
    print(f"integrity: {manifest['integrity'][:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
