#!/usr/bin/env python3
"""Does the WFO pipeline produce the same result twice on the same input?

Every comparison in this investigation ran into the same wall. The
campaigns on disk disagree by up to 14x for one strategy on one data
manifest, which CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md attributed to three
pipeline bugs fixed at a65ed29000. That attribution was never checked.

`tests/test_nested_wfo.py` calls `run_nested_wfo` once in the whole file,
and no test runs a campaign twice to compare. The idempotency tests there
cover artifact reuse by identity, not a fresh run reproducing its result.
So the question the rest of the investigation depends on — is a number
this pipeline produces worth anything — has no test.

This runs the same minimal campaign twice into separate output roots and
compares the aggregate metrics, the per-fold returns, and the selected
params. Any divergence is a determinism defect that no amount of
downstream analysis can compensate for.

Run: .venv/bin/python scripts/check_wfo_determinism.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np

from trading_agent.backtest.nested_wfo import WFOSpec, run_nested_wfo

TOL = 1e-9


def spec_for(registry_path: str) -> WFOSpec:
    return WFOSpec(
        strategy_id="rsi",
        symbol="BTC/USDT",
        timeframe="1h",
        param_grid={"period": [10, 14]},
        train_months=6,
        val_months=2,
        test_months=2,
        step_months=2,
        min_trades_per_fold=1,
        registry_path=registry_path,
    )


def summarise(result) -> dict:
    agg = dict(result.aggregate_metrics)
    folds = []
    for r in result.outer_results:
        tm = r.test_metrics or {}
        folds.append({
            "fold": getattr(r, "fold_id", ""),
            "params": r.params,
            "trades": tm.get("total_trades"),
            "return_pct": tm.get("total_return_pct"),
            "sharpe": tm.get("sharpe_ratio"),
        })
    return {"aggregate": agg, "folds": folds}


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="wfo_determinism_"))
    print("=" * 72)
    print("WFO DETERMINISM CHECK — same spec, two fresh runs, compared")
    print("=" * 72)

    runs = []
    for i in (1, 2):
        out = base / f"run{i}"
        out.mkdir(parents=True, exist_ok=True)
        print(f"\nrun {i} ...", flush=True)
        result = run_nested_wfo(spec_for(str(out / "registry.sqlite3")), out_root=out)
        runs.append(summarise(result))
        print(f"  median_sharpe={result.aggregate_metrics.get('median_test_sharpe')}")

    a, b = runs
    diffs: list[str] = []

    # aggregate metrics
    keys = set(a["aggregate"]) | set(b["aggregate"])
    for k in sorted(keys):
        va, vb = a["aggregate"].get(k), b["aggregate"].get(k)
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if not np.isclose(float(va), float(vb), atol=TOL, equal_nan=True):
                diffs.append(f"aggregate[{k}]: {va!r} != {vb!r}")
        elif va != vb:
            diffs.append(f"aggregate[{k}]: {va!r} != {vb!r}")

    # per-fold
    if len(a["folds"]) != len(b["folds"]):
        diffs.append(f"fold count: {len(a['folds'])} != {len(b['folds'])}")
    else:
        for fa, fb in zip(a["folds"], b["folds"]):
            if fa["params"] != fb["params"]:
                diffs.append(f"{fa['fold']} params: {fa['params']} != {fb['params']}")
            for key in ("trades", "return_pct", "sharpe"):
                va, vb = fa[key], fb[key]
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                    if not np.isclose(float(va), float(vb), atol=TOL, equal_nan=True):
                        diffs.append(f"{fa['fold']} {key}: {va!r} != {vb!r}")
                elif va != vb:
                    diffs.append(f"{fa['fold']} {key}: {va!r} != {vb!r}")

    print("\n" + "=" * 72)
    if diffs:
        print(f"NON-DETERMINISTIC — {len(diffs)} difference(s):")
        for d in diffs[:20]:
            print(f"  {d}")
        if len(diffs) > 20:
            print(f"  ... {len(diffs) - 20} more")
    else:
        print("DETERMINISTIC — two fresh runs agree exactly")
        print("  aggregate metrics, per-fold params, trades, return and Sharpe")
        print("  all match within 1e-9")
    print("=" * 72)

    json.dump(
        {"diffs": diffs, "run1": a, "run2": b, "artifacts": str(base)},
        open("/tmp/wfo_determinism.json", "w"), indent=2, default=str,
    )
    print("\nevidence: /tmp/wfo_determinism.json")
    print(f"artifacts kept at: {base}")
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
