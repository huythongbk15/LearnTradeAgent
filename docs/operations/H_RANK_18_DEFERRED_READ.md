# H-RANK-18: the coin-standing rank book, one deferred read (frozen 2026-11-16)

The operator chose this on 2026-11-16 ("Đóng băng, đọc sau"). Research description, not advice. **Frozen:** change nothing on this page after its merge. A later page may add to it, but never replaces it.

## Why a deferred read
- **The lead:** the rank family that survived H-COMBO-17 (`data/combo_20261116_01/RESULTS.md`). Long the coins near their 30-day high and with low volatility; short the far and volatile ones, on the point-in-time top-30 list.
- **One bet:** dhi30, low volatility, age and tenure correlate .5 to .8, and coins 1 to 3 weeks on the list carry most of the short leg. So it is one bet, "old coins against new joiners", the same effect that NL-P and KRW fade trade.
- **Best estimate from worn data (autopsy of 2026-11-02):**
  - about +40 to +47 bps a week net, 90% range -15 to +92;
  - weekly standard deviation 355 to 450, so a weekly Sharpe of about .12.
- **Power:** 80% at +45 needs about 390 to 620 weeks of unread data. On disk, and from cheap downloads, nothing has that power:
  - 12 weeks of August to October 2026 give power .11 and are partly worn;
  - coins off the list carry a different effect;
  - other venues hold the same coins in the same weeks.

  Scouting packet: `data/rank_scout_20261116/` and the workflow of 2026-11-16.
- **So:** one frozen read, unmasked, on the weeks that become readable, read once, late.

## Question
Does the 50/50 book of the two families "rank +dhi all windows" and "rank +lowvol all windows" earn a net mean above 0 on the weeks below?
- The universe is the unmasked top-30 list.
- The basis is perp settlement, twice tier cost and funding paid.

## Weeks
- **W7:** 12 entry Mondays, 2026-07-27 to 2026-10-19. The last exit is 2026-10-26 04:00 UTC. The 2026-10-26 entry is left out, because it exits on a bar behind the H-XSEC-01 seal.
  - The dhi half of these weeks is partly worn. The masked list (18 of 30 names) was read once for dhi30 (state_round Q9, +79, t .45), and NL-1 read the same weeks with another rule.
- **Forward:** every entry Monday from 2026-11-02.
  - These bars hold the 12 sealed H-XSEC-01 coins, so nothing of them is read before the seal ends (2027-12-27).

## Frozen rule (code hashes below)
- **Source:** `data/catalog_20261103/catalog.py` lines 37 to 45 (the families) over `data/replay_20261102_01/discovery/rlib.py` (features, books, `chain`).
- **Keys:** dhi{7,14,30,60,90} and lowvol{7,14,30,60,90} with sign +. Each family is the mean of its 5 windows, and the book is the mean of the two families.
  - The window average is used because the 30-day window ranks 998th of 1,000 choices; one window would carry the most hindsight.
- **Features:** on 4 h log closes, the rolling window ending before the stamp, `min_periods = int(.89 n)`.
- **Universe:** list members with a funding event in the 2 days before entry. MIN_NAMES 10, so a week with fewer names holds nothing.
- **Book:** each leg holds round(n/5) names, .5 a side, equal weight.
  - Hold 42 stamps (168 h). Enter at stamp + 4 h, which is Monday 04:00 UTC.
  - The short leg has a stop at +50%, filled as F2 (`plib.set_fills(1)`).
  - Cost `chain(cx=2.0)`; funding paid on both legs.
- **List:** the xsec02 rule, top 30 Binance spot USDT pairs by 28-day quote volume.
  - It is built at read time from spot 1d of every USDT pair, delisted ones included, from data.binance.vision.
  - That download needs the operator's go-ahead at read time.
- **The one change allowed:** features may come from perp closes, because the seed's spot closes end on 2026-08-05.
  - Before any read, the new code must reproduce the window S streams in `catalog_20261103/plans.pkl`: within a few bps a week, correlation >= .95.
  - Then a blind rebuild of the read, as usual.
- **Hashes of the frozen sources, sha256:**
  - `data/replay_20261102_01/discovery/rlib.py`: `53fbdcfe333140811a7f479a3bdf60b33b69cde5095cb23e47bb2e439a5989ca`
  - `data/catalog_20261103/catalog.py`: `281073c5c4d2ec74e3ee5ac7a3e38050d4d88d9e8160aeec3e4d994b4c7d68fb`
  - `data/stopfill_20261103_01/scripts/plib.py`: `725940b95af644b2483d0b5838989cc5eec554f8db515711d297c2318cc824a9`
  - `data/stopfill_20261103_01/scripts/book.py`: `fdfa7db467583b7fa8839aa9e4c682f29be039a2681b736b82f17c395b3a7f40`
  - These files are git-ignored. If they are lost, the rule above is the specification.

## Reads: two looks, no others
| Look | Date | Weeks | Power at +30 / +45 / +60 | Smallest effect at 80% |
|---|---|---|---|---|
| 1 | 2028-11-13 | about 117 | .15 / .28 / .44 (one-sided 2.5%) | about +92 |
| 2, final | 2030-12-30 | about 228 | .24 / .48 / .72 (one-sided 2.5%) | about +66 |

- **Statistic:** net mean bps a week of the 50/50 book. se = 358 / sqrt(n), using the window S standard deviation, frozen here.
- **Words at each look, z = 1.96** (two looks, so about 5% overall, one-sided):
  - "giữ" if mean >= 1.96 se;
  - "loại" if mean + 1.96 se < +45, which rejects the size the estimate centres on;
  - otherwise "chưa ngã ngũ". At look 1 that means wait for look 2; at look 2 it is final.
- **Also printed, not deciding:** each half alone, each leg alone, by year, without the best 10 weeks, and the W7 weeks apart from the forward weeks.

## Rules until then
- No one computes this book, or its two families, on any week from 2026-07-27, for any list, before look 1. That covers agents and scripts, the shadow and the Demo runners.
- The masked RS and LV forward books and the Demo LV book are separate records with their own rules. Their reads (2028-11-13) do not open this one.
- Nothing here lifts the H-XSEC-01 seal or opens a closed ledger.
- A wish to look earlier is a new decision by the operator. If one is made, it is written as its own page and named a third look.
