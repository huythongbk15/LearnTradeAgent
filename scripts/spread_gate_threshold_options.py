#!/usr/bin/env python3
"""What p-threshold makes the spread gate usable at the fold counts available?

The power analysis showed that at n=14 a strategy clearing cost 70% of the
time passes only 58% of the time — the gate is close to a coin flip on
whether a genuinely good strategy qualifies. The campaigns on disk produce
7 to 10 folds, so the question is not which p to prefer in the abstract but
which p makes a verdict possible with the history that exists.

For each (n, p) pair this reports what a real clearing rate has to be for
the gate to fire, and how often it would fire.
"""

from __future__ import annotations

import math

RATES = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90)


def upper_tail(k: int, n: int) -> float:
    if n <= 0:
        return 1.0
    k = max(0, min(k, n))
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)


def first_passing_k(n: int, p_max: float) -> int | None:
    for k in range(n + 1):
        if upper_tail(k, n) <= p_max:
            return k
    return None


def power_at(rate: float, n: int, p_max: float) -> float:
    return sum(
        math.comb(n, k) * rate ** k * (1 - rate) ** (n - k)
        for k in range(n + 1)
        if upper_tail(k, n) <= p_max
    )


def main() -> None:
    print("=" * 76)
    print("Spread gate: what true clearing rate is detectable at each (n, p)?")
    print("Rows are p thresholds, columns are fold counts on disk.")
    print("A cell is the real clearing rate the gate demands; '—' = unreachable")
    print("=" * 76)

    ns = (7, 10, 14)
    p_maxes = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)

    print(f"\n{'p_max':>6} " + " ".join(f"{'n=' + str(n):>18}" for n in ns))
    print("-" * 76)
    for p_max in p_maxes:
        cells = []
        for n in ns:
            k = first_passing_k(n, p_max)
            if k is None:
                cells.append(f"{'—':>18}")
                continue
            # smallest real rate that reaches k clearing folds
            need = k / n
            cells.append(f"{need:>13.0%} ({k}/{n})")
        print(f"{p_max:>6.2f} " + " ".join(cells))

    print("\n" + "=" * 76)
    print("Power: probability a genuinely good strategy passes the gate")
    print("=" * 76)
    print(f"{'n':>4} {'p_max':>6} " + " ".join(f"{r:>7.0%}" for r in RATES))
    print("-" * 66)
    for n in ns:
        for p_max in (0.10, 0.20, 0.25):
            row = " ".join(
                f"{power_at(r, n, p_max):>7.0%}" for r in RATES
            )
            print(f"{n:>4} {p_max:>6.2f} {row}")

    print("\n" + "=" * 76)
    print("Reading")
    print("=" * 76)
    print("  p is not the lever at n=14. 0.10, 0.15 and 0.20 all demand the")
    print("  same 10/14 (71%), because the binomial tail does not move until")
    print("  the threshold drops to 0.25 and 9/14 (64%).")
    print()
    print("  p=0.20 does help at n=10, where it asks 7/10 instead of 8/10 and")
    print("  lifts power for a genuine 70% edge from 38% to 65%.")
    print()
    print("  p<=0.25 is the only setting that moves n=14 (78% power at a 70%")
    print("  edge) but it also admits near-null strategies: at n=7 a strategy")
    print("  clearing 55% of the time passes 32% of the time, which is too")
    print("  loose for a test whose whole purpose is the chance comparison.")
    print()
    print("  Power stays the binding constraint, not p. No threshold available")
    print("  at n=14 reaches the 73-92% that n=30-50 would give for a 70% edge.")
    print("=" * 76)


if __name__ == "__main__":
    main()
