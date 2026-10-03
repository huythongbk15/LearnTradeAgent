#!/usr/bin/env python3
"""Is there a fold structure that gives both enough folds and enough trades?

The binding constraint found in probe_registry_window_density.py: the
spread gate needs 19 of 32 folds clearing cost, and a strategy cannot clear
a window it does not trade in. enhanced_ma trades in 62% of 61-bar windows
but in far more of the 90-bar and 180-bar ones, because longer windows
catch more of a sparse signal.

Two ways to widen a window without losing folds: longer test months, or a
shorter step so windows overlap. _get_fold_indices takes both, so this
enumerates the space and asks which structures clear both bars.

It also estimates expected trades per fold from measured entry density
rather than assuming, so a structure that passes the arithmetic is not
recommended if the trades still will not arrive.

Run: .venv/bin/python scripts/plan_window_structure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.backtest.nested_wfo import _binomial_upper_tail, _get_fold_indices

P_MAX = 0.20
MIN_FOLDS = 14
MIN_MEDIAN_TRADES = 2  # a fold needs at least this to be measurable

# Strategies worth a campaign, measured entry counts on BTC/USDT 1d.
CANDIDATES = {"enhanced_ma": 15, "ma_adx": 15, "ma_vol_target": 19,
              "ma_adx_regime": 18, "ma_crossover": 28}


def main() -> None:
    path = ROOT / "data/raw/binance/BTC_USDT/1d.parquet"
    df = pl.read_parquet(path)
    n = df.height
    print(f"BTC/USDT 1d: {n:,} bars")
    print(f"gate: p<={P_MAX}, n>={MIN_FOLDS}, fold needs >= {MIN_MEDIAN_TRADES} trades")
    print()

    # Entry positions for the densest approved candidate.
    from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

    s = EnhancedMaCrossover()
    sig = s.generate_signals(s.compute_indicators(df)).to_numpy()
    entries = [i for i in range(1, len(sig)) if sig[i] > 0 and sig[i - 1] <= 0]
    print(f"enhanced_ma entries on this file: {len(entries)}")
    print()

    candidates = []
    print(f"{'train/val/test/step':24s} {'purge':>6} {'folds':>6} "
          f"{'need':>6} {'win_bar':>8} {'med_trades':>10} {'empty%':>7}  verdict")
    print("-" * 88)

    for train in (12, 18, 24):
        for val in (2, 3, 6):
            for test in (3, 6, 9):
                for step in (1, 2, 3, test):
                    purge = 25
                    folds = _get_fold_indices(n, "1d", train, val, test, step,
                                              purge, purge)
                    nf = len(folds)
                    if nf < MIN_FOLDS:
                        continue
                    need = next((k for k in range(nf + 1)
                                 if _binomial_upper_tail(k, nf) <= P_MAX), None)
                    if need is None:
                        continue
                    win_bars = folds[0].outer_test_end - folds[0].outer_test_start

                    counts = []
                    for f in folds:
                        c = sum(1 for e in entries
                                if f.outer_test_start <= e < f.outer_test_end)
                        counts.append(c)
                    med = sorted(counts)[len(counts) // 2]
                    empty = sum(1 for c in counts if c == 0) / len(counts) * 100

                    label = f"{train}/{val}/{test}/{step}"
                    ok = med >= MIN_MEDIAN_TRADES
                    verdicts = []
                    if not ok:
                        verdicts.append("too few trades/fold")
                    if empty > 40:
                        verdicts.append(f"{empty:.0f}% folds empty")
                    verdict = "VIABLE" if ok and empty <= 40 else "; ".join(verdicts)
                    print(f"{label:24s} {purge:>6} {nf:>6} "
                          f"{need}/{nf:<4} {win_bars:>8} {med:>10} {empty:>6.0f}%  {verdict}")
                    if ok and empty <= 40:
                        candidates.append({
                            "label": label, "train": train, "val": val,
                            "test": test, "step": step, "folds": nf,
                            "need": need, "win_bars": win_bars,
                            "median_trades": med, "empty_pct": empty,
                        })

    print()
    print("=" * 88)
    if not candidates:
        print("NO VIABLE STRUCTURE on daily bars for this registry.")
        print("\nEvery configuration with enough folds still leaves most folds")
        print("without a trade. The file holds 15 entries; the gate needs a")
        print("strategy clearing 19+ of them, and a 61-91 bar window cannot")
        print("contain an entry often enough.")
    else:
        print("VIABLE — enough folds, and the folds actually contain trades")
        print("=" * 88)
        for c in sorted(candidates, key=lambda x: (-x["folds"], x["empty_pct"])):
            print(f"  {c['label']:24s} {c['folds']:>3} folds  need "
                  f"{c['need']}/{c['folds']}  window {c['win_bars']:>3} bars  "
                  f"median {c['median_trades']}/fold  {c['empty_pct']:.0f}% empty")

        best = max(candidates, key=lambda x: (x["folds"], -x["empty_pct"]))
        print(f"\nRecommended: train/val/test/step = {best['label']}")
        print(f"  {best['folds']} folds, need {best['need']} clearing, "
              f"window {best['win_bars']} bars,")
        print(f"  median {best['median_trades']} trades/fold, "
              f"{best['empty_pct']:.0f}% folds empty")
        print()
        print("  Sanity check: an overlapping step shorter than the test")
        print("  window means folds share bars, so fold results are not")
        print("  independent. The spread gate's binomial test assumes")
        print("  independence — a step < test inflates apparent confidence.")
        if best["step"] < best["test"]:
            print(f"  Here step={best['step']} < test={best['test']}, so the")
            print("  independence assumption does NOT hold and the gate's")
            print("  p-value is optimistic. Treat this as a screening result,")
            print("  not a promotion decision.")


if __name__ == "__main__":
    main()