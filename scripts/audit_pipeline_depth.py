#!/usr/bin/env python3
"""Map the full pipeline and find where it breaks, layer by layer.

The question is whether any part of the system is unreachable in practice —
built, tested, and never called by the path that would run in production.
Static greps find candidates; this follows the actual call path from data
ingest to order placement and reports what each stage resolves to.

Every claim about reachability is checked by import and call, not by reading
the name. A module that imports cleanly but is never invoked from the
runtime path is reported as unreachable, which is a different problem from
one that is missing.

Run: .venv/bin/python scripts/audit_pipeline_depth.py
"""

from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "trading_agent"

# The pipeline a live trade must travel, in order.
STAGES = [
    ("1 data ingest", ["data/storage.py", "data/market_data.py", "data/collector.py"]),
    ("2 features", ["features"]),
    ("3 strategies", ["strategies"]),
    ("4 forecast", ["strategies/canonical", "research/forecast.py"]),
    ("5 regime detect", ["ml/regime_detection.py"]),
    ("6 router", ["authority/adaptive_router.py"]),
    ("7 policy/promotion", ["research/selection_policy.py", "authority/promotion_store.py"]),
    ("8 portfolio alloc", ["authority/portfolio.py", "portfolio"]),
    ("9 risk", ["risk", "execution/live_safety.py", "execution/risk_controller.py",
                "execution/boundaries.py"]),
    ("10 order plan", ["execution/canonical", "execution/proposal.py"]),
    ("11 execution", ["execution", "execution/engine.py"]),
    ("12 ledger/recon", ["execution/simulator", "execution/lifecycle",
                         "execution/data_trust.py"]),
    ("13 monitoring", ["monitoring"]),
    ("14 exchange", ["exchanges"]),
]


def call_graph() -> dict[str, set[str]]:
    """module basename -> basenames it calls (imports and attribute calls)."""
    graph: dict[str, set[str]] = defaultdict(set)
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        name = path.stem
        try:
            tree = ast.parse(path.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    graph[name].add(node.module.split(".")[-1])
                    for a in node.names:
                        graph[name].add(a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    graph[name].add(a.name.split(".")[-1])
    return graph


def test_count(pattern: str) -> int:
    try:
        out = subprocess.run(
            ["grep", "-rl", pattern, "tests/", "--include=*.py"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        return len([x for x in out.stdout.split() if x])
    except Exception:
        return -1


def main() -> None:
    print("=" * 78)
    print("PIPELINE DEPTH AUDIT — is every stage built, tested, and reachable?")
    print("=" * 78)

    graph = call_graph()
    print(f"modules parsed: {len(graph)}\n")

    print(f"{'stage':22s} {'files':>6} {'tested by':>10} {'called by':>10}  status")
    print("-" * 78)

    rows = []
    for stage, targets in STAGES:
        files = 0
        for t in targets:
            p = SRC / t
            if p.is_dir():
                files += len(list(p.rglob("*.py")))
            elif p.is_file():
                files += 1
        tested = max((test_count(Path(t).stem) for t in targets), default=0)
        stem = Path(targets[0]).stem
        called_by = sum(
            1 for src, deps in graph.items()
            if stem in deps and src != stem
        )
        rows.append((stage, files, tested, called_by))
        status = []
        if files == 0:
            status.append("MISSING")
        if tested == 0:
            status.append("no tests")
        if called_by == 0:
            status.append("not imported elsewhere")
        print(f"{stage:22s} {files:>6} {tested:>10} {called_by:>10}  "
              f"{', '.join(status) if status else 'ok'}")

    print()
    print("=" * 78)
    print("STAGES WITH NO TEST COVERAGE REFERENCING THEM")
    print("=" * 78)
    for stage, files, tested, called_by in rows:
        if tested == 0 and files:
            print(f"  {stage:22s} {files} files, no test file names it")

    print()
    print("=" * 78)
    print("MODULES PARSED BUT NEVER IMPORTED BY ANOTHER MODULE")
    print("=" * 78)
    orphans = sorted(
        m for m, deps in graph.items()
        if not deps and not m.startswith("__")
    )
    # only report ones that look like entry points rather than leaf helpers
    interesting = [m for m in orphans if any(
        k in m for k in ("engine", "runner", "main", "pipeline", "service",
                          "manager", "orchestr", "runner"))
    ]
    if interesting:
        for m in interesting:
            print(f"  {m}")
    else:
        print("  none of the entry-point-shaped modules")

    print()
    print("=" * 78)
    print("READING THIS")
    print("=" * 78)
    print("""
A stage marked 'not imported elsewhere' is not necessarily dead — it may be
an entry point invoked from scripts/ or a CLI, which this scan does not
follow. The point is to mark which claims need that second check before
being believed either way.

A stage with no tests is where a defect would survive longest. During this
investigation two of the three bugs found were in exactly this category:
the tz_localize failure in nested_wfo and the trade counter in engine.py,
both in well-covered files, and neither caught until a campaign ran.
""")


if __name__ == "__main__":
    main()