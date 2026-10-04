#!/usr/bin/env python3
"""Measure the payoff asymmetry behind stat_arbitrage_lo's losing median.

The campaign produced a 70.7% win rate and a -0.029% median fold return.
That combination has one explanation: losses exceed wins. This measures the
distribution directly from the trade records the campaign wrote, so the
0.71 break-even ratio is an observed number rather than an inference.

Three questions:

  * what is the realised average win and average loss
  * what expectancy does that imply per trade
  * does the protective stop account for the loss tail, or are the losers
    simply larger positions

Run: .venv/bin/python scripts/analyse_payoff_distribution.py
"""

from __future__ import annotations

import glob
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "data" / "campaign_evidence_sa" / "stat_arbitrage_lo"

BREAK_EVEN_RATIO = 0.71  # 1 - hit rate, for a zero-expectancy strategy


def load_trades() -> list[dict]:
    trades: list[dict] = []
    for path in glob.glob(str(CAMPAIGN / "**" / "*.json"), recursive=True):
        try:
            data = json.loads(Path(path).read_text())
        except Exception:
            continue
        found = None
        if isinstance(data, dict):
            found = data.get("trades")
        if isinstance(found, list):
            for t in found:
                if isinstance(t, dict) and "pnl_abs" in t:
                    trades.append(t)
    return trades


def load_fold_returns() -> list[float]:
    out: list[float] = []
    for path in glob.glob(str(CAMPAIGN / "outer_one_shot" / "*" / "stat_arbitrage_lo"
                              / "fold_*" / "*.json"), recursive=True):
        try:
            data = json.loads(Path(path).read_text())
        except Exception:
            continue
        m = data.get("metrics") or {}
        r = m.get("total_return_pct")
        if r is not None:
            out.append(float(r))
    return sorted(out)


def main() -> None:
    print("=" * 72)
    print("PAYOFF DISTRIBUTION — stat_arbitrage_lo, BTC/USDT 1d, 32-fold campaign")
    print("=" * 72)

    folds = load_fold_returns()
    trades = load_trades()
    print(f"fold returns found : {len(folds)}")
    print(f"trade records     : {len(trades)}")

    if not folds:
        print("\nNo fold returns on disk. Re-run the campaign:")
        print("  .venv/bin/python scripts/build_campaign_policy.py "
              "--strategy stat_arbitrage_lo")
        return

    pos_folds = [r for r in folds if r > 0]
    neg_folds = [r for r in folds if r < 0]
    zero_folds = [r for r in folds if r == 0]
    print()
    print("Fold return distribution")
    print("-" * 52)
    print(f"  positive : {len(pos_folds):>3}  ({len(pos_folds) / len(folds) * 100:.0f}%)"
          f"  median {st.median(pos_folds):+.3f}%" if pos_folds else "  positive :   0")
    print(f"  negative : {len(neg_folds):>3}  ({len(neg_folds) / len(folds) * 100:.0f}%)"
          f"  median {st.median(neg_folds):+.3f}%" if neg_folds else "  negative :   0")
    print(f"  flat     : {len(zero_folds):>3}")
    print(f"  worst    : {folds[0]:+.3f}%")
    print(f"  best     : {folds[-1]:+.3f}%")

    if pos_folds and neg_folds:
        avg_win_fold = st.mean(pos_folds)
        avg_loss_fold = st.mean(neg_folds)
        print()
        print("Fold-level asymmetry")
        print("-" * 52)
        print(f"  average winning fold : {avg_win_fold:+.3f}%")
        print(f"  average losing fold  : {avg_loss_fold:+.3f}%")
        print(f"  ratio win/loss       : "
              f"{abs(avg_win_fold / avg_loss_fold) if avg_loss_fold else float('inf'):.2f}")

    if trades:
        wins = [t for t in trades if t.get("pnl_abs", 0) > 0]
        losses = [t for t in trades if t.get("pnl_abs", 0) < 0]
        print()
        print("Trade-level distribution")
        print("-" * 52)
        total = len(trades)
        hit = len(wins) / total if total else 0
        print(f"  trades    : {total}")
        print(f"  hit rate  : {hit * 100:.1f}%  ({len(wins)}W / {len(losses)}L)")
        if wins and losses:
            aw = st.mean(t["pnl_abs"] for t in wins)
            al = st.mean(t["pnl_abs"] for t in losses)
            print(f"  avg win   : ${aw:,.2f}")
            print(f"  avg loss  : ${abs(al):,.2f}")
            print(f"  ratio     : {aw / abs(al):.2f}")
            expectancy = hit * aw - (1 - hit) * abs(al)
            print(f"  expectancy/trade : ${expectancy:,.2f}")

            wl = sorted(t.get("bars_held", 0) for t in wins)
            ll = sorted(t.get("bars_held", 0) for t in losses)
            if wl and ll:
                print()
                print("  hold time, median bars")
                print(f"    wins   : {st.median(wl):.0f}")
                print(f"    losses : {st.median(ll):.0f}")
                ratio = st.median(ll) / st.median(wl) if st.median(wl) else 0
                print(f"    losses held {ratio:.1f}x longer than wins" if ratio > 1
                      else f"    losses held {1 / ratio:.1f}x shorter than wins"
                      if ratio else "")
        print()
        print("  loss tail")
        print("-" * 52)
        if losses:
            losses.sort(key=lambda t: t["pnl_abs"])
            print("    worst 5: "
                  + ", ".join(f"${t['pnl_abs']:,.0f}" for t in losses[:5]))
            reasons = {}
            for t in losses:
                r = str(t.get("exit_reason", "unknown"))
                reasons[r] = reasons.get(r, 0) + 1
            print(f"    exit reasons: {reasons}")
    else:
        print()
        print("No per-trade records in the campaign artifacts; fold-level")
        print("asymmetry above is the whole of the available evidence.")

    print()
    print("=" * 72)
    if trades and wins and losses:
        ratio = (st.mean(t["pnl_abs"] for t in wins)
                 / abs(st.mean(t["pnl_abs"] for t in losses)))
        need = BREAK_EVEN_RATIO
        verdict = "VIABLE" if ratio >= need else "NOT VIABLE"
        print(f"VERDICT: win/loss ratio {ratio:.2f} vs break-even {need:.2f} "
              f"-> {verdict}")
        if ratio < need:
            print()
            print("The hit rate is genuine; the payoff is not. A strategy with")
            print("this win rate becomes profitable at ratio >= 0.71, which is a")
            print("sizing and stop question rather than a signal question.")
    print("=" * 72)


if __name__ == "__main__":
    main()