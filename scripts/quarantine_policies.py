#!/usr/bin/env python3
"""Quarantine policy stores whose scores were never measured.

POLICY_RETURN_AUDIT.md found every promoted policy built from three
hardcoded return constants. The measured-evidence gate (19ec13c) now
refuses those artifacts on load, so leaving them in place turns the next
process start into an unplanned outage: the runtime asks for a policy, the
registry raises, and trading stops for a reason nobody scheduled.

Quarantine moves the affected store directories out of the live path so
nothing reads them, keeping every file for audit and rollback. Nothing is
deleted. A MANIFEST records what moved and why.

Usage:
    .venv/bin/python scripts/quarantine_policies.py --dry-run
    .venv/bin/python scripts/quarantine_policies.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCRIPTS_DATA = ROOT / "scripts" / "data"

# Metrics the promotion gate requires. A store whose validated/active
# policies lack any of these predates the gate, or was written by a
# generator that fabricated its scores.
REQUIRED_METRICS = (
    "median_oos_return_pct",
    "median_oos_trades",
    "n_passing_folds",
    "total_folds",
)


def find_stores() -> list[Path]:
    """Every policies/ directory, including nested campaign output."""
    out: list[Path] = []
    for base in (DATA, SCRIPTS_DATA):
        if base.exists():
            out.extend(p for p in base.rglob("policies") if p.is_dir())
    return sorted(out)


def _real_code_shas() -> dict[str, str]:
    """Canonical source hash per strategy, computed from the module on disk."""
    import sys

    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from trading_agent.strategies.canonical import build_default_registry

    registry = build_default_registry()
    return {sid: registry.describe(sid).code_sha
            for sid in registry.list_ids()}


def inspect(store: Path, real_shas: dict[str, str]) -> dict:
    """Classify a store by why its policies cannot be trusted.

    Two independent reasons, both real:

    * ``missing_metrics`` — a promotable policy has no OOS metric family,
      so its score is a bare claim.
    * ``code_sha_mismatch`` — the policy claims a score produced by
      strategy X, but its code_sha does not match X's real source. The old
      generators wrote literal SHAs ("c"*64, "live-pipeline-001"), so the
      score is not attributable to anything that ran.

    Metric *presence* alone proves nothing: the generators wrote complete
    metric blocks full of constants, which is why a first dry-run reported
    38 of 44 stores as clean.
    """
    files = [f for f in store.glob("*.json") if ".sig." not in f.name]
    total = active = validated = 0
    missing = 0
    mismatched = 0
    reasons: list[str] = []
    samples: list[dict] = []

    for f in files:
        try:
            p = json.loads(f.read_text())
        except Exception:
            continue
        if p.get("symbol") is None or p.get("incumbent") is None:
            continue  # index.json and other bookkeeping
        total += 1
        status = str(p.get("status", ""))
        scores = p.get("scores") or {}
        absent = [m for m in REQUIRED_METRICS if m not in scores]
        promotable = status in {"validated", "active"}
        if status == "active":
            active += 1
        elif status == "validated":
            validated += 1
        if not promotable:
            continue

        inc = p.get("incumbent") or {}
        sid = inc.get("strategy_id", "")
        claimed = (inc.get("code_sha") or "").lower()
        expected = real_shas.get(sid, "").lower()
        bad_sha = not expected or not claimed.startswith(expected[:12])
        if absent:
            missing += 1
        if bad_sha:
            mismatched += 1
        if absent or bad_sha:
            why = []
            if absent:
                why.append(f"missing metrics {absent}")
            if bad_sha:
                why.append(
                    f"code_sha {claimed[:12]!r} != {sid} source {expected[:12]!r}"
                    if expected else
                    f"strategy {sid!r} not on canonical allowlist"
                )
            reasons.extend(why)
            if len(samples) < 3:
                samples.append({
                    "policy_id": p.get("policy_id", "")[:16],
                    "status": status,
                    "symbol": p.get("symbol"),
                    "strategy": sid,
                    "why": why,
                })

    return {
        "store": str(store.relative_to(ROOT)),
        "total_policies": total,
        "validated": validated,
        "active": active,
        "missing_metrics": missing,
        "code_sha_mismatch": mismatched,
        "unmeasured_promotable": missing + mismatched,
        "affected": bool(reasons),
        "reasons": sorted(set(reasons))[:4],
        "samples": samples,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the move")
    ap.add_argument(
        "--date", default=datetime.now(UTC).strftime("%Y%m%d"), help="quarantine date tag"
    )
    args = ap.parse_args()

    stores = find_stores()
    real_shas = _real_code_shas()
    reports = [inspect(s, real_shas) for s in stores]
    affected = [r for r in reports if r["affected"]]
    clean = [r for r in reports if not r["affected"]]

    print("=" * 74)
    print(f"POLICY STORE QUARANTINE — {len(reports)} stores found")
    print("=" * 74)
    print(f"affected (untrustworthy promotable policies): {len(affected)}")
    print(f"clean                                        : {len(clean)}")

    tot_active = sum(r["active"] for r in affected)
    tot_missing = sum(r["missing_metrics"] for r in affected)
    tot_sha = sum(r["code_sha_mismatch"] for r in affected)
    print(f"\nactive policies inside affected stores  : {tot_active}")
    print(f"promotable policies missing metrics     : {tot_missing}")
    print(f"promotable policies with unattributable code_sha: {tot_sha}")

    if clean:
        print("\nleft in place (no untrustworthy promotable policy):")
        for r in clean:
            print(f"  {r['store']:66s} active={r['active']:>3d}")

    if not args.apply:
        print("\n--dry-run: nothing moved. Re-run with --apply to quarantine.")
        if affected:
            print("\nwould quarantine:")
            for r in affected:
                print(f"  {r['store']:66s} active={r['active']:>3d} "
                      f"missing={r['missing_metrics']:>3d} "
                      f"sha_mismatch={r['code_sha_mismatch']:>3d}")
                for why in r["reasons"]:
                    print(f"        - {why}")
        return

    dest_root = DATA / f"policies_quarantine_{args.date}"
    dest_root.mkdir(parents=True, exist_ok=True)

    moved = []
    for r in affected:
        src = ROOT / r["store"]
        # Flatten the path so nested campaign stores stay distinguishable.
        rel = r["store"].replace("/", "__")
        dest = dest_root / rel
        if dest.exists():
            print(f"  skip (already quarantined): {rel}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved.append({"from": r["store"], "to": str(dest.relative_to(ROOT))})
        print(f"  moved  {r['store']}  ->  {dest.relative_to(ROOT)}")

    manifest = {
        "quarantined_at": datetime.now(UTC).isoformat(),
        "reason": (
            "Policy artifacts whose scores were never measured. "
            "See POLICY_RETURN_AUDIT.md: 2690 promoted policies were built "
            "from three hardcoded return constants (0.02/0.05/0.10) and two "
            "trade counts (30/40), with selection_score a pure function of "
            "trade count. The measured-evidence gate added in 19ec13c "
            "refuses these on load, so leaving them in the live path turns "
            "the next process start into an outage."
        ),
        "stores_scanned": len(reports),
        "stores_quarantined": len(moved),
        "stores_left_in_place": len(clean),
        "active_policies_quarantined": tot_active,
        "promotable_missing_metrics": tot_missing,
        "promotable_unattributable_code_sha": tot_sha,
        "moved": moved,
        "left_in_place": [r["store"] for r in clean],
        "rollback": (
            "Move each entry in 'moved' back to its 'from' path. No file was "
            "modified or deleted; this was a move only."
        ),
    }
    (dest_root / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nquarantined {len(moved)} stores -> {dest_root.relative_to(ROOT)}")
    print(f"manifest  : {(dest_root / 'MANIFEST.json').relative_to(ROOT)}")
    print("rollback  : move each entry in 'moved' back to its 'from' path")


if __name__ == "__main__":
    main()
