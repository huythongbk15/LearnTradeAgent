# SEAL-DATE POOL: one pre-registered pooled read of the sealed forward books

**Status: FROZEN 2026-11-20.** The digests of this page, `pool.py`, `members_brief.md`, `selftest.out` and `power/*` are in `FROZEN.sha256`. Change nothing after freeze. A later wish to change a rule is a new decision by the operator, recorded as an amendment beside this page, never an edit of it.

Opened by the operator on 2026-11-20 with "Viết trang SEAL-DATE", after the project synthesis (`data/synthesis_20261120/REPORT.md`, candidate 3).

This page adds no order, no book and no data. It fixes, before any member's forward weeks are seen, one pooled statistic that is read once on **2028-11-13**. Each member keeps its own rules, its own record and its own read words. Nothing on this page changes them.

## Members

Six members. Each keeps its own page, record and words; the pool reads only what is listed here. Facts below are from each member's own page (citations in `members_brief.md`).

| Member | Own page | Record the pool uses | Week value x(m, w), credited to the week of | Fixed scale s(m), worn sd in bps |
|---|---|---|---|---|
| RANK (H-RANK-18) | `docs/operations/H_RANK_18_DEFERRED_READ.md` | the weekly series of the run whose number look 1 printed as its word (no ledger) | the book's net for the entry Monday w | 400.1 (panel `RANK18`, entries before 2026-07-27) |
| NLP (NL-P core; bybit book as stand-in, rule 6) | `docs/operations/NEWPERP_PAPER_RUN.md` | snapshot written by the core book's read at 60 (or 120), or the bybit book's read (rule 6); the pool never opens the ledger | stamp Monday w: short legs 0.5 at equal weight, BTC long 0.5, each leg net as its page books it (book cost, F2 stop fill, funding); 0 when the stamp has no leg | 596.9 (panel `NLP`) |
| KRW (H-KRW-FADE-16) | `data/krw_fade_20261108_01/KILL_RULES.md` | shadow rows (`n_bps`) in `data/perp_demo/krw.json`, not Demo fills | entry E: events with E in w, grouped by UTC notice day within w; 0.5 × mean over those clusters of the cluster-mean `n_bps`; exclusions as classified at the pool run | 607.7 (panel `KRW`) |
| FLOW02 (H-FLOW-BTC-02) | `docs/operations/PERP_DEMO_RUNNER.md` (FLOW02 section) | research nets from the store bars, not Demo fills | decision day: sum over the week of the 01:00 open-to-open 24 h long net of each recorded long call at 5.5 bps a side, no funding; flat or missed days 0 | 211.2 (panel `FLOW_RIDGE`) |
| LEARN (H-LEARN-11) | `docs/operations/LEARN_PAPER_RUN.md` | snapshot written by its Read 2 (or latest look), from the paper ledger, not the Demo book; the pool never opens the ledger | the 23:00 decision bar: sum of the week's daily pooled nets (mean over 20 coins, flat 0) | 729.8 (`power/learn_sd.out`, the 23:00 decision on worn predictions before 2026-10-26) |
| CRASH (FlowBid) | `docs/operations/FLOWBID_PAPER_RUN.md` | snapshot written by the FlowBid read | flag week: mean over the week's events of the event's sleeve return per bid placed (each placed bid gets 1/bids placed of its cell, unfilled bids earn 0, nine cells 1/9 each, 148 bps; FLOWBID_PAPER_RUN.md:53 prints this form); events with no bid placed and weeks with no event 0 | 156.3 (panel `CB9`, nine-cell average) |

Panel streams are in `data/synthesis_20261120/quant/panel.parquet` (built by `s2_panel.py`). Every one ends before its member's forward record starts; the RANK stream ends at entry 2026-07-20.

Not members: RS, LV (paper and Demo) and the pair book, which are the RANK bet or the masked list; FLOW01, whose primary failed; NL-P alpha, which is description only; H-COMBO-17, which has no forward book. Nothing may be added after freeze.


## Question

The sealed forward books each have little power alone (E[t] about 0.3 to 1.1 after two years at half their worn Sharpe). Their worn weekly streams are close to uncorrelated (|r| ≤ .17). Does the equal-risk pool of their forward weeks have a positive mean, read once by a rule fixed before any forward week was seen?

The pool word says whether combining the leads adds evidence. It does not say which member earns, and it does not replace any member's own word.

## Pool unit

- **Weeks.** Calendar weeks keyed by their Monday, 00:00 UTC to the next Monday 00:00 UTC. The pool covers the 105 weeks with Mondays 2026-11-09 through 2028-11-06. Weeks before 2026-11-09 are outside the pool, even for a member whose forward record starts earlier.
- **Member week value x(m, w).** Net bps a week of a 1x sleeve of member m, using the member's own recorded net (its own cost, funding and fill rules), built the same way as the worn stream named for it in the member table (`data/synthesis_20261120/quant/s2_panel.py`):
  - per-event members: a week's value is the mean over that week's event clusters of the cluster-mean event net, times the pair scale given in the table, credited to the week of entry;
  - per-decision members: the sum of that week's decision nets;
  - book members: the book's weekly net.
  - A week with no position is 0. A week inside a member's opened span where its record is missing (outage, host down) is 0 and is counted in the read's outage line.
  - Where this table and a member's own page differ on the unit, the table governs the pool and the member page governs the member.
- **Fixed scale s(m).** The member's worn all-week sd, fixed now from the worn panel (member table). It is not re-estimated at the read.
- **Pool value.** Each present member-week counts once: m̂ = Σ_w Σ_(m present in w) x(m, w) / s(m) ÷ Σ_w k(w), where k(w) is the number of members present in w.

Weights depend only on s(m) and on presence, and presence is fixed by rules 1 to 6 below, not by returns (a stop is a return-driven event, but its effect is fixed now by rule 4). A mismatch between s(m) and the forward unit changes power, not validity.

## Eligibility, opened weeks and kills

The pool never opens a member record that the member's own page has not already opened, and is not a look under any member's page.

1. **Opened span.** A member's opened span runs from pool week 2026-11-09 to the end named here, and nowhere else:
   - RANK: the last entry Monday settled by look 1 (2028-11-13).
   - CRASH: the cut of the FlowBid read (2028-11-13).
   - LEARN: the cut of its latest look on or before the pool read (expected Read 2, 2028-10-26).
   - NLP: the cut of the core read at 60 symbol-weeks (or the final read at 120), never of the futility look. Otherwise rule 6.
   - KRW: if K1 has been taken, every event settled by 2028-11-06 (its page blinds the shadow rows only until K1); otherwise absent.
   - FLOW02: if the 365-decision look has been taken, every decision settled by 2028-11-06 (its page seals the book only until then); otherwise absent.
   A week is in the span only if every unit credited to it settled by the span's end. Outside its span a member is **absent**: it adds nothing to the sum or the count. A pool week with no member present is dropped.
2. **Snapshots.** For RANK, CRASH, LEARN and NLP, the member's own look writes its per-unit nets up to that look's cut to `data/seal_pool_20261120/snap/<MEMBER>_<look>.csv` and prints its sha256 in the look's output. Writing this file is part of that look, not a new look. The pool reads only the latest snapshot on or before the pool read. A member with no snapshot is absent. For KRW and FLOW02, whose pages have no blind after K1 and the 365-decision look, the pool run builds the week file from the record itself.
3. **Order and deadline.** The pool runs after H-RANK-18 look 1 and the FlowBid read have printed their words under their own pages (download approval, parity check, blind rebuild). If either has not printed by 2028-12-31, that member is absent and the pool runs then.
4. **Stops.** A stop counts only if it ends the record the pool uses (a futility kill of the paper or shadow record, or an operator stop of it). That member is present with x = 0 in every pool week after the stop, through 2028-11-06; this needs no data. Demo-only stops (KRW K2 and K3, the LEARN Demo rules, the FLOW02 Demo halt if the research nets continue) do not stop the member. A resumed or fresh FLOW02 store is not part of the member.
5. **Never started.** A member with no forward record is absent.
6. **NLP stand-in.** The bybit book takes the NLP seat only if the core book is still recording and has fewer than 60 settled symbol-weeks at the pool read. A core book killed at futility keeps the seat under rule 4. Core and bybit are never both in the pool: they are the same bet.
7. **Too few.** If fewer than 52 pool weeks have at least 3 members present (a stopped member at 0 counts as present), the word is **"quá ít"** and no statistic is printed.
8. **Weeks before the pool.** RANK's W7 weeks (2026-07-27 to 2026-10-19), its 2026-11-02 week, and any member week before 2026-11-09 are outside the pool.

Expected spans (estimates from the members' pages, not rules): RANK and CRASH 105 weeks; LEARN about 103; KRW and FLOW02 about 105 if their first looks come (K1 about 2027-08, 365 decisions about 2027-11); NLP core only if its read at 60 comes by the pool read (estimated late 2028), else the bybit book from its read (2027) if rule 6 allows.

**What "unread" means here.** The pool is fixed before any member's forward week is seen. By the read day, most members' own looks will have opened part of the data (KRW, FLOW02, LEARN, NLP). That does not touch the test, because nothing on this page can change after freeze, but the pool is not a read of wholly unseen weeks. Only RANK and CRASH are first opened on the read day. (This corrects the synthesis line "about 104 unread weeks".)

## Statistic and words

- m̂ as defined under Pool unit, over the weeks kept by rule 1.
- se: the kept weeks in calendar order; a non-circular moving block bootstrap with blocks of 4 weeks draws ceil(n/4) start points per resample, `default_rng(20281113).integers(0, n-3, size=(10000, ceil(n/4)))`, truncates to n weeks, recomputes the same ratio, and takes the sd with ddof 1.
- The reader is `pool.py`, frozen with this page. `pool.py --selftest` runs it on synthetic data only (`selftest.out`: null 'giữ' rate .030 over 200 synthetic pools against a one-sided nominal .025).
- Words (one read, printed once):
  - **"giữ"** if m̂ ≥ 1.96 se;
  - **"không"** if m̂ + 1.645 se < 0;
  - **"chưa rõ"** otherwise;
  - **"quá ít"** as in rule 7.
- Printed beside the word, as description only: the iid t of the per-week mean, the number of members present, the pool without its best 10 weeks, and the outage weeks. Each member's mean of x / s, and the pool without its best member, are printed only when every present member's span ends at a look that has printed a mean, so the pool prints no member number its page still withholds.

## Named secondaries (description only, no words)

- **THREE-CLOCK**: KRW + crash (FlowBid) + FLOW02, same unit and scales.
- **LEARNED PAIR**: FLOW02 + LEARN, same unit and scales.

These two lines are printed at the same read with the same statistic, labelled "mô tả". Their worn blocks were already printed (THREE-CLOCK was the best of about 14 lines in the synthesis), so they carry no word. No other sub-pool may be read unless it is registered on this page before freeze.

## Power (inference on worn data, `power/power.out`)

Equal-risk pool of the six worn streams, 105 weeks, with each window's own sd as weights and pairwise correlations (not the fixed s(m) and presence rules). "×h" scales every member's worn weekly Sharpe by h.

| Worn window | ×1 E[t] / P(t ≥ 1.96) | ×.5 | ×.3 | 80% power at ×.5 |
|---|---|---|---|---|
| all weeks | 3.71 / .96 | 1.86 / .46 | 1.11 / .20 | 4.7 years |
| from 2022-03-14 | 3.41 / .93 | 1.70 / .40 | 1.02 / .17 | 5.6 years |
| from 2023-01-02 | 3.32 / .91 | 1.66 / .38 | 1.00 / .17 | 5.9 years |

- Singles at ×.5 after 105 weeks: E[t] 0.28 to 1.08.
- Dropping any one member at ×.5 (all weeks): E[t] 1.59 to 1.93.
- ×.5 is a central guess, not a conservative one: across the 29 worn streams in `data/synthesis_20261120/quant/halves.csv` the second half ran at a median .64 of the first (.64 also over the 25 with a positive first half; the synthesis quoted .59 over 24); for these six the ratio is .55 to 1.54 (`power/power.out`). ×.3 is the cautious case.
- The power assumes all six members in all 105 weeks. Rules 1 to 6 can leave fewer member-weeks (NLP may be absent; KRW and FLOW02 need their first looks), so real power can be lower than the table.
- LEARN in the table uses the 24-offset panel stream (sd 538.3); its fixed scale is the single 23:00 decision (sd 729.8), as recorded forward.
- It is a guide, not a promise.

## What this page does not do

- It does not open, list or compute on any sealed record now, and at the read it opens only what rules 1 and 2 allow.
- It does not change any member's rules, kill dates, words or record.
- It places no order and needs no download.
- A "giữ" here is not a method PASS and opens nothing by itself. Any use of it needs the operator's own words.

This is research description, not investment advice.
