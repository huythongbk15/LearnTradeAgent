#!/usr/bin/env python3
"""Audit every promoted policy for whether its expected return can survive costs.

Reads all data/*/policies stores and answers one question per policy:
could the recorded OOS return have paid for the round trip?

Break-even is not a flat number. A policy that trades often needs a much
larger per-unit return than one that trades rarely, so each policy is
judged against:

    break_even_return = round_trip_cost * round_trips
    round_trips       = median_oos_trades / 2   (a trade = one round trip)

and the recorded median_oos_return_pct is the promised OOS outcome.

Read-only: this script never writes to a policy store.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Same cost schedule as the AC14 evidence runs.
COMMISSION = 0.001
SLIPPAGE = 0.0005
SPREAD_BPS = 2.0
ROUND_TRIP = COMMISSION * 2 + SLIPPAGE * 2 + SPREAD_BPS / 10_000  # 0.0032

FLAT_THRESHOLD = 0.001  # the 0.1% the user asked about
OUT = Path("/tmp/policy_return_audit.json")


def main() -> None:
    stores = sorted(ROOT.glob("data/*/policies"))
    rows: list[dict] = []

    for store in stores:
        for f in sorted(store.glob("*.json")):
            if ".sig." in f.name:
                continue
            try:
                p = json.loads(f.read_text())
            except Exception:
                continue
            sc = p.get("scores") or {}
            inc = p.get("incumbent") or {}
            ret = sc.get("median_oos_return_pct")
            trades = sc.get("median_oos_trades")
            if ret is None:
                continue
            try:
                ret_f = float(ret)
                tr_f = float(trades) if trades is not None else None
            except (TypeError, ValueError):
                continue

            round_trips = (tr_f / 2.0) if tr_f else None
            be = round_trips * ROUND_TRIP * 100 if round_trips else None

            rows.append({
                "store": store.parent.name,
                "policy_id": p.get("policy_id", f.stem)[:12],
                "symbol": p.get("symbol"),
                "timeframe": p.get("timeframe"),
                "regime": p.get("regime"),
                "strategy": inc.get("strategy_id"),
                "status": p.get("status"),
                "stage": p.get("promotion_stage"),
                "selection_score": sc.get("selection_score"),
                "oos_return_pct": ret_f,
                "oos_trades": tr_f,
                "sharpe": sc.get("median_test_sharpe"),
                "max_dd_pct": sc.get("median_max_dd_pct"),
                "folds": f"{sc.get('n_passing_folds')}/{sc.get('total_folds')}",
                "round_trips": round_trips,
                "break_even_pct": round(be, 4) if be is not None else None,
                "below_flat": ret_f < FLAT_THRESHOLD,
                "can_pay_costs": (ret_f > be) if be is not None else None,
            })

    total = len(rows)
    print("=" * 78)
    print(f"PROMOTED POLICY RETURN AUDIT — {total} policies across {len(stores)} stores")
    print("=" * 78)

    if not rows:
        print("no scored policies found")
        return

    # ── headline counts ──
    below = [r for r in rows if r["below_flat"]]
    negative = [r for r in rows if r["oos_return_pct"] < 0]
    priced = [r for r in rows if r["can_pay_costs"] is not None]
    solvent = [r for r in priced if r["can_pay_costs"]]

    print(f"\nmedian_oos_return_pct < {FLAT_THRESHOLD:.3f} (0.1%) : "
          f"{len(below):>4} / {total}  ({len(below) / total * 100:.1f}%)")
    print(f"median_oos_return_pct < 0 (negative)          : "
          f"{len(negative):>4} / {total}  ({len(negative) / total * 100:.1f}%)")
    print(f"can pay its own round-trip cost                : "
          f"{len(solvent):>4} / {len(priced)}  ({len(solvent) / max(len(priced), 1) * 100:.1f}%)")
    print(f"CANNOT pay cost                                : "
          f"{len(priced) - len(solvent):>4} / {len(priced)}")

    # ── distribution ──
    print("\n--- distribution of median_oos_return_pct ---")
    buckets = [(-1e9, -0.01, "< -1%"), (-0.01, 0, "[-1%, 0%)"), (0, 0.001, "[0%, 0.1%)"),
               (0.001, 0.01, "[0.1%, 1%)"), (0.01, 0.05, "[1%, 5%)"),
               (0.05, 0.10, "[5%, 10%)"), (0.10, 1e9, ">= 10%")]
    for lo, hi, name in buckets:
        n = sum(1 for r in rows if lo <= r["oos_return_pct"] < hi)
        bar = "#" * max(0, round(n / max(total, 1) * 60))
        print(f"  {name:>12} : {n:>4}  {bar}")

    rets = sorted(r["oos_return_pct"] for r in rows)
    p50 = rets[len(rets) // 2]
    print(f"\n  min={rets[0]:.4f}  p25={rets[len(rets)//4]:.4f}  "
          f"median={p50:.4f}  p75={rets[3*len(rets)//4]:.4f}  max={rets[-1]:.4f}")

    # ── break-even view ──
    print("\n--- vs cost break-even (round_trip = {:.2f}%) ---".format(ROUND_TRIP * 100))
    be_sorted = sorted(
        (r for r in priced), key=lambda r: r["break_even_pct"] - r["oos_return_pct"]
    )
    print("worst 8 (furthest below break-even):")
    for r in be_sorted[:8]:
        print(f"  {r['strategy'][:22]:22s} {r['regime'][:14]:14s} "
              f"oos={r['oos_return_pct']:+.4f}%  breakeven={r['break_even_pct']:.4f}%  "
              f"trades={r['oos_trades']:.0f}  score={r['selection_score']}")

    best = [r for r in priced if r["can_pay_costs"]]
    print("\nbest 5 (can pay costs):")
    for r in sorted(best, key=lambda r: r["oos_return_pct"] - r["break_even_pct"],
                    reverse=True)[:5]:
        print(f"  {r['strategy'][:22]:22s} {r['regime'][:14]:14s} "
              f"oos={r['oos_return_pct']:+.4f}%  breakeven={r['break_even_pct']:.4f}%  "
              f"trades={r['oos_trades']:.0f}  score={r['selection_score']}")

    # ── selection score vs return ──
    print("\n--- selection_score vs realised return (is the score rewarding the right thing?) ---")
    by_score: dict[float, list[float]] = defaultdict(list)
    for r in rows:
        try:
            by_score[float(r["selection_score"])].append(r["oos_return_pct"])
        except (TypeError, ValueError):
            pass
    for score in sorted(by_score, reverse=True):
        v = by_score[score]
        neg = sum(1 for x in v if x < 0)
        print(f"  score={score:<5} n={len(v):>4}  mean_oos={sum(v)/len(v):+.4f}%  "
              f"negative={neg:>3} ({neg/len(v)*100:.0f}%)")

    # ── per strategy ──
    print("\n--- by strategy ---")
    per: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["strategy"]:
            per[r["strategy"]].append(r["oos_return_pct"])
    for s, v in sorted(per.items(), key=lambda kv: sum(kv[1]) / len(kv[1])):
        neg = sum(1 for x in v if x < 0)
        flag = "  <-- all negative" if neg == len(v) else ""
        print(f"  {s[:26]:26s} n={len(v):>3}  mean={sum(v)/len(v):+.4f}%  neg={neg:>3}{flag}")

    # ── status/stage of the insolvent ones ──
    insolvent = [r for r in priced if not r["can_pay_costs"]]
    print(f"\n--- of the {len(insolvent)} policies that cannot pay their own costs ---")
    print("  status :", dict(Counter(r["status"] for r in insolvent)))
    print("  stage  :", dict(Counter(r["stage"] for r in insolvent)))
    active = [r for r in insolvent if r["status"] == "active"]
    print(f"  ACTIVE (would be tradeable): {len(active)}")
    for r in active[:10]:
        print(f"    {r['store'][:26]:26s} {r['symbol']}/{r['timeframe']} "
              f"{r['regime']:14s} {r['strategy']}")

    json.dump({
        "total_policies": total,
        "stores": len(stores),
        "round_trip_pct": ROUND_TRIP * 100,
        "flat_threshold": FLAT_THRESHOLD,
        "below_flat": len(below),
        "negative": len(negative),
        "can_pay_costs": len(solvent),
        "priced": len(priced),
        "active_insolvent": len(active),
        "policies": rows,
    }, open(OUT, "w"), indent=2, default=str)
    print(f"\nevidence: {OUT}")


if __name__ == "__main__":
    main()
