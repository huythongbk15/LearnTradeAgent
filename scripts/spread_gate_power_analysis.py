#!/usr/bin/env python3
"""How many folds does a spread claim actually require?

`cost_clearing_folds_above_chance_p_le_010` asks whether cost-clearing
folds appear more often than a coin flip. With 7 trading windows that
needs 6/7 (p=0.0625); with 14 it needs 10/14. Both numbers are forced by
the sample the campaign happened to produce, which is not a basis for a
promotion criterion.

This asks the two questions the gate needs answered before it can be used
to accept or reject anything:

  1. Minimum n — the smallest fold count at which the test can ever pass.
     Below it the gate is not strict, it is unreachable.
  2. Power — how large a true edge is needed to be detected at each n.
     If a strategy clears cost 80% of the time and the gate still cannot
     see it at n=10, the gate measures sample size, not edge.
"""

from __future__ import annotations

import math

P_MAX = 0.10


def upper_tail(k: int, n: int) -> float:
    if n <= 0:
        return 1.0
    k = max(0, min(k, n))
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)


def min_n_to_pass(p: float) -> int | None:
    """Smallest n at which k = ceil(n*p) clearing folds gives p <= 0.10."""
    for n in range(1, 121):
        k = math.ceil(n * p - 1e-12)
        if k <= n and upper_tail(k, n) <= P_MAX:
            return n
    return None


def power_at(true_rate: float, n: int, p_max: float = P_MAX) -> float:
    """Probability the gate passes when the true clearing rate is true_rate."""
    total = 0.0
    for k in range(n + 1):
        pk = math.comb(n, k) * (true_rate ** k) * ((1 - true_rate) ** (n - k))
        if pk > 0 and upper_tail(k, n) <= p_max:
            total += pk
    return total


def main() -> None:
    print("=" * 72)
    print(f"Spread gate: one-sided binomial, p <= {P_MAX} vs a coin-flip null")
    print("=" * 72)

    print("\n1. MINIMUM n — the smallest fold count at which the gate can pass")
    print(f"   {'true clearing rate':>20} | {'min n':>6} | {'k needed':>9}")
    print("   " + "-" * 42)
    for rate in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        n = min_n_to_pass(rate)
        if n is None:
            print(f"   {rate:>19.0%} | {'never':>6} |")
        else:
            k = math.ceil(n * rate - 1e-12)
            print(f"   {rate:>19.0%} | {n:>6} | {k}/{n:<7}")

    print("\n2. POWER — chance the gate detects an edge of that size")
    print(f"   {'n':>4} " + " ".join(f"{r:>7.0%}" for r in
                                     (0.60, 0.70, 0.75, 0.80, 0.90)))
    print("   " + "-" * 44)
    for n in (7, 10, 14, 20, 30, 50):
        row = " ".join(f"{power_at(r, n):>7.1%}" for r in
                       (0.60, 0.70, 0.75, 0.80, 0.90))
        print(f"   {n:>4} {row}")

    print("\n3. WHAT THIS MEANS FOR THE PROPOSED 14-FOLD CAMPAIGN")
    k14 = math.ceil(14 * 0.70 - 1e-12)
    print(f"   A strategy clearing cost 70% of folds needs "
          f"{min_n_to_pass(0.70)} folds.")
    print(f"   At n=14 the gate needs {k14}/14 and detects a 70% edge with "
          f"{power_at(0.70, 14):.0%} probability.")
    print(f"   At n=20 that rises to {power_at(0.70, 20):.0%}.")
    print()
    print("   The fold count a campaign produces is set by how much history")
    print("   exists, not by how much evidence the claim needs. Any criterion")
    print("   read off a particular campaign inherits that campaign's sample.")
    print("=" * 72)


if __name__ == "__main__":
    main()
