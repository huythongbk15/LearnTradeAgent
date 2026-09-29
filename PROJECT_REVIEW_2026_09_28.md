# Project review — 2026-09-28

Written after the AC14 investigation, before committing to another long
campaign. The question asked was: where is the project against its own
plan, and what should happen next.

## What the plan said

`docs/ADAPTIVE_ROADMAP_STATUS.md` (reviewed 2026-08-31) covers S4–S7:

| stage | scope | status then |
|---|---|---|
| S4 | selection policy, provenance, lifecycle | complete, code + tests |
| S5 | regime router, safe switching | complete, code + tests |
| S6 | shared-capital allocator | complete, code + tests |
| S7 | shadow / testnet / canary | capability code ready, **evidence missing** |

Release decision recorded there:

- research / replay / paper — usable once policies are signed and evidence checked
- testnet / shadow — allowed under the promotion gate
- canary / production — **NO-GO** until S7 evidence closes

That decision was correct then and is still correct now. Nothing found
today argues for loosening it.

## What the investigation changed

The roadmap treats S4 as complete, and at the level of mechanism it is:
content-addressed policies, HMAC envelopes, append-only registry, CAS
activation, rollback lineage, fail-closed router. Those are real and tested.

What the investigation showed is that the **evidence flowing into S4 was
never measured**, and nothing in S4's design required it to be:

- 2,690 promoted policies carried `code_sha` values matching no source on
  disk, with `median_oos_return_pct` drawn from three literal constants
  (POLICY_RETURN_AUDIT.md). Promotion checked provenance fields existed and
  were non-empty; it never checked they were true.
- The best-scoring strategy behind those policies, `enhanced_ma`, was run
  over 852 real cells across nine symbols and clears its own round-trip cost
  in 24% of them, concentrated in a minority of windows
  (RESEARCH_EVIDENCE_REVIEW.md).
- Its 9/9 WFO fold pass, which is what made it an incumbent, is
  satisfiable by a strategy that barely trades: 49% of its cells never
  opened a position (WFO_CAMPAIGN_RESULT.md).

So the accurate statement is not "S4 is broken" — it is **"S4 is complete as
a mechanism and has never been exercised by real evidence."** The gates
added today (attribution, net-edge-per-fold, spread) close that gap, and
the promotion store is now empty as a result.

## AC position today

| verdict | count | rows |
|---|---|---|
| ACCEPT (bounded) | 8 | AC01–05, AC07–10 |
| PARTIAL | 4 | AC06, AC11, AC12, AC13 |
| BLOCKED | 2 | AC14, AC15 |

AC13 is worse than the reviewer recorded: promotion never verified that
scores came from a measurement at all. AC14's blocking condition moved
upstream — a campaign needs a policy, and there is no promotable policy
attributable to code that exists. AC15 is unchanged and still needs a
cluster.

Final acceptance remains **NOT ACHIEVED**, by a wider margin than on
2026-09-25.

## The state of knowledge, honestly

**Known to work**
- Execution safety: chaos invariants, fill/ledger accounting, cancel and
  recovery, short-side backtest, latency and fallback. 35/35, 21/21, 5/5
  and others green, all against real code paths.
- Evidence integrity: the gate chain now refuses unattributable scores,
  cost-underwater folds, and edges that appear in too few windows.

**Known not to work**
- Anything that depends on a promoted policy. The store is empty.
- Claiming efficacy. No configuration measured beats buy-and-hold on a
  like-for-like basis. `enhanced_ma` loses on the long leg in all five
  windows tested, and its short leg is worse everywhere.

**Not yet known**
- Whether any strategy in the registry has edge. The sign-off rejected 5
  of 16 outright, marked 2 as NO_TRADE, and left several unranked. The one
  strategy measured end-to-end does not qualify under the gates now in
  place. That is not evidence the registry is empty, and not evidence it
  is not.

## The decision that is actually open

The next long campaign was going to be 14+ folds on SOL to give the spread
gate enough resolution. That is ~20 hours for one strategy, and it answers
one question: does `enhanced_ma` show spread above chance when given a
sample large enough to tell?

Three things argue against running it first:

1. **A 14-fold campaign still leaves 15 strategies unmeasured.** Spending
   20 hours to test the one strategy already shown to be weakest is a poor
   use of the single most expensive resource here.
2. **The binding constraint is a research decision, not a measurement.**
   With 7 windows a strategy needs 6/7 to pass; with 14 it needs 10/14. The
   campaign length needed to resolve spread is set by how many folds the
   data supports, which is fixed before the run starts. Making the gate
   passable is a judgement about what counts as evidence.
3. **A more informative measurement exists and is cheap.** Entry
   frequency by timeframe was 30 seconds of compute and falsified the
   proposal that preceded it (TIMEFRAME_PROBE_RESULT.md). The
   symbol-dimension check took minutes and reversed the "no edge"
   conclusion. Both were data questions, and both were answerable without
   a campaign.

## Options

**A. Re-rank the registry on existing data, then measure the survivors.**
The sign-off left strategies unranked and 2 as NO_TRADE. Re-derive a
ranking from the on-disk cells across all symbols — the same treatment
`enhanced_ma` just received — and see whether anything else clears the
gates. Cost: hours, not days. Risk: the remaining cells are thinner than
`enhanced_ma`'s 852.

**B. Settle the gate semantics first, then measure to them.**
The three readings of the trade-count floor are still open, and the
fold-count question behind the spread gate has not been decided. Deciding
these on the evidence already in hand costs nothing and makes any campaign
that follows answer a question someone has actually posed.

**C. Run the 14-fold campaign on SOL.**
~20 hours. Answers whether `enhanced_ma` has spread above chance. Leaves
the other 15 untested and the gate semantics unsettled.

**D. Stop and treat the registry as unproven.**
Accept that the system has a sound execution core and no validated
strategy, and spend effort on the execution side or on a different
research direction entirely.

## Recommendation

B, then A.

The reasoning is that the most expensive resource has repeatedly been
spent on questions that cheaper checks could answer first — that is the
through-line of this investigation, from the interpreter mismatch to the
fabricated policy store to the fold-concentrated edge. Settling what the
gates mean, and re-ranking what is already measured, costs a fraction of
one campaign and either produces a shortlist worth spending 20 hours on, or
establishes that no strategy qualifies and the question moves elsewhere.

C is defensible if the intent is specifically to characterise
`enhanced_ma`. It should not be run as the next step in general, because
the strategy has already failed the gates that do not depend on sample
size, and the gate that does depend on it is unsettled.

D is honest but premature: the execution core is a substantial asset and
the strategy question is not yet closed.
