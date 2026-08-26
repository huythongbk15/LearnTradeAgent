#!/usr/bin/env python
"""Verify golden-run replay determinism between two full multi-pair backtests.

S0 exit gate: "Hai lần replay cùng input/config cho cùng quyết định, trade
ledger và metrics trong tolerance định trước."

Usage:
    python scripts/verify_golden_replay.py \
        --run-a data/backtests/multi_pair_1h/<RUN_A> \
        --run-b data/backtests/multi_pair_1h/<RUN_B> \
        [--emit-manifest artifacts/golden/<name>.json]

Compares every symbol's report.json after stripping volatile identity fields
(run_id, state_dir, report_path). Any remaining difference is a violation.
Optionally emits a content-addressed golden manifest binding commit SHA,
data manifests, configs and headline metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent

#: Report keys that legitimately differ between two replays of the same code.
VOLATILE_KEYS = ("run_id", "state_dir", "report_path")

#: Randomly-minted identity fields inside trade/order records. They are
#: allocation artifacts of the venue/event store, NOT trading decisions.
#: Every other field (side, quantity, prices, bar indices, simulated
#: timestamps, metrics) must match bit-exactly across replays.
ID_FIELDS = ("id", "order_id", "entry_order_id", "exit_order_id")

HEADLINE_FIELDS = (
    "final_equity",
    "total_return_pct",
    "sharpe",
    "max_drawdown_pct",
    "total_trades",
    "win_rate_pct",
    "profit_factor",
)


def load_report(run_dir: Path, symbol_file: str) -> dict[str, Any]:
    path = run_dir / symbol_file / "report.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing report: {path}")
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def strip_volatile(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k not in VOLATILE_KEYS}


def mask_identities(node: Any) -> Any:
    """Replace randomly-minted IDs with a stable placeholder."""
    if isinstance(node, dict):
        return {
            k: "<id>" if k in ID_FIELDS and isinstance(v, str) else mask_identities(v)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [mask_identities(x) for x in node]
    return node


def diff_paths(a: Any, b: Any, path: str = "$") -> list[str]:
    """Return dotted paths where normalized reports disagree."""
    if isinstance(a, dict) and isinstance(b, dict):
        diffs: list[str] = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                diffs.append(f"{path}.{key}: present={key in a}/{key in b}")
            else:
                diffs.extend(diff_paths(a[key], b[key], f"{path}.{key}"))
            if len(diffs) >= 20:
                return diffs
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        diffs = []
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(diff_paths(x, y, f"{path}[{i}]"))
            if len(diffs) >= 20:
                return diffs
        return diffs
    if isinstance(a, float) and isinstance(b, float):
        # Bit-exact equality expected from a deterministic engine; allow
        # exact-zero tolerance here because any drift is a real violation.
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def git_commit_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_symbol_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "data_manifest_id": report["data_manifest_id"],
        "feature_artifact_id": report["feature_artifact_id"],
        "config_id": report["active_config"]["config_id"],
        "status": report["status"],
        "schema_version": report["schema_version"],
    }
    for field in HEADLINE_FIELDS:
        if field in report:
            summary[field] = report[field]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-a", required=True, type=Path)
    parser.add_argument("--run-b", required=True, type=Path)
    parser.add_argument(
        "--emit-manifest",
        type=Path,
        default=None,
        help="write golden manifest JSON to this path when verification passes",
    )
    args = parser.parse_args()

    symbols = sorted(p.name for p in args.run_a.iterdir() if p.is_dir())
    if not symbols:
        print(f"no symbol directories under {args.run_a}")
        return 2

    all_ok = True
    summaries: dict[str, Any] = {}
    for sym in symbols:
        report_a = mask_identities(strip_volatile(load_report(args.run_a, sym)))
        report_b = mask_identities(strip_volatile(load_report(args.run_b, sym)))
        diffs = diff_paths(report_a, report_b)
        ok = not diffs
        all_ok &= ok
        status = "✅ IDENTICAL" if ok else f"❌ {len(diffs)}+ diffs"
        print(f"{sym:<12} {status}")
        for line in diffs[:5]:
            print(f"    {line}")
        if ok:
            summaries[sym] = build_symbol_summary(report_a)

    if not all_ok:
        print("\n❌ REPLAY DETERMINISM VIOLATION — reports diverge")
        return 1

    print(
        f"\n✅ REPLAY DETERMINISTIC — {len(summaries)}/{len(symbols)} symbol "
        "reports byte-identical (modulo volatile identity fields)"
    )

    if args.emit_manifest is not None:
        payload = {
            "manifest_type": "golden_replay",
            "schema_version": 1,
            "commit_sha": git_commit_sha(),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "runs": {
                "A": json.loads((args.run_a.parent / "_runid").read_text())
                if False
                else str(args.run_a.name),
                "B": str(args.run_b.name),
            },
            "replay_deterministic": True,
            "symbols": summaries,
        }
        manifest_id = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        )
        payload["golden_manifest_id"] = manifest_id
        out = args.emit_manifest
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")
        print(f"\n🥇 Golden manifest → {out}")
        print(f"   id = {manifest_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
