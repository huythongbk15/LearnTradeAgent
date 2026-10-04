# Deep review — trading agent

Independent assessment requested before any further optimisation or rule
changes. This is not a status update: it walks the system layer by layer,
states what is strong and what is weak in each, and answers whether the
whole is a coherent chain or a set of parts with gaps between them.

Scope: 88,000 lines across 197 modules, plus data and evidence artifacts.

---

## The short answer

**The execution chain is genuinely strong and genuinely coherent. The
research chain above it is not — and the two are separated by a promotion
gate that currently passes nothing.**

The system is not a pile of unfinished pieces. It is roughly two systems
stacked: a working trading machine with no validated reason to trade, and
a research apparatus whose output never satisfied the criteria its
consumer required.

That is a different situation from "incomplete", and it changes what is
worth doing.

---

## Layer-by-layer

### 1. Data — sound

```
BTC/ETH/SOL 1h   31,783 bars  2023-01-01 → 2026-08-17   max gap 2h
BTC/ETH 1d        2,456 bars  2020-01-01 → 2026-09-21
SOL 1d            1,324 bars  2023-01-01 → 2026-08-16
```

Checked directly: zero OHLC violations, zero nulls in price columns, 3,600s
spacing on hourly. Across the 1,325 days the two timeframes share, the
hourly close and the daily close agree exactly (0.00% difference on every
day sampled) — so the daily file was derived from the hourly one
correctly.

One real gap: the hourly files stop at **2026-08-17** while daily runs to
**2026-09-21**. 35 days of daily data have no hourly equivalent. Any
work in hourly bars is measuring a window that ends six weeks early, and
none of the campaigns run in this investigation noticed.

The three symbols share identical timestamps with different prices, which
is correct — they were downloaded on the same schedule.

**Assessment: trustworthy, with a freshness shortfall that is small but
unnoticed.**

### 2. Features, strategies, forecast — sound, and unusually well organised

34 strategy files behind a 17-entry canonical registry that resolves each
id to a source-hashed adapter. The contract is narrow — one `forecast`
method, no execution capability — and every strategy implements it. The
`code_sha` mechanism is the reason the fabricated-policy problem was
eventually detectable at all.

**Assessment: the cleanest layer in the system.**

### 3. Regime detection — sound, underused

`HybridRegimeDetector` with HMM, GMM and rule-based methods, PIT-safe via
`training_cutoff`. Twelve test files reference it.

It is invoked, but the router's behaviour in every campaign run reduced to
"abstain" rather than "select by regime" — the regime dimension has never
been demonstrated to change a routing decision on real measured data.

**Assessment: correct implementation, unproven contribution.**

### 4. Router — sound and conservative by design

Full-posterior weighting rather than argmax, stale/OOD/entropy fail-closed,
dwell and cooldown against flip-flop, position-owner pinning, content-
addressed decisions with audit.

The caution is the point, and it has held: every abstention observed during
this investigation was a correct abstention.

**Assessment: the strongest single component, and it is doing exactly what
it was designed to do — refusing to route without evidence.**

### 5. Promotion and policy — the break point

2,690 policies were promoted carrying three literal score constants. The
promotion path verified that provenance fields *existed*; it never checked
they were true, and never checked the score came from a measurement. Only
4 of 4,520 promotable policies are attributable to code that exists.

Since the gate work: zero policies promotable, promotion store empty,
router abstains.

**Assessment: was the weakest link in the system, is now the strongest
control in it, and in converting from a silent failure into a hard stop
it changed from a liability into the most valuable part of the codebase.**

### 6. Portfolio allocation — sound, untested against real promotion

Aggregate caps by strategy, symbol and correlation cluster; deterministic
netting of opposing forecasts; regime multipliers; pro-rata scaling.

34 test files reference it. It has never allocated against a policy that
passed a real gate, because none has.

**Assessment: implemented and tested in isolation; its integration point
has never been exercised.**

### 7. Risk — the best-proportioned layer

Six files, 48 referencing tests. Permission boundaries, circuit breakers,
live-safety policy, risk controller, emergency close and protection. The
drawdown breaker firing repeatedly during the last campaign is evidence it
works, not that it misbehaved.

**Assessment: strongest coverage-to-complexity ratio in the system.**

### 8. Order planning — sound

11 modules under `execution/canonical`, 41 referencing tests, plus
`proposal.py` at the top level.

**Assessment: correct, and properly separated from execution.**

### 9. Execution — deep and, unusually, genuinely exercised

60 files, 25,846 lines — six sub-packages I initially miscounted as five
files, which is the kind of shallow read that produces confident wrong
answers. Lifecycle with `InnerSelectionFreeze`, content-addressed intents,
compare-and-swap activation, rollback lineage, chaos invariants, forced
audits. A full paper-flow E2E suite runs 24 tests in 52s, all passing.

**Assessment: the system has real depth here, and it is depth that is used
rather than accumulated.**

### 10. Ledger and reconciliation — sound

13 modules under `execution/simulator` plus a 4-module lifecycle. Trusted
price, fill history, position accounting.

**Assessment: sound.**

### 11. Monitoring and exchanges — built, used, correctly out of scope for the import graph

Both showed "not imported elsewhere" in the first pass, which was wrong:
they are reached through package `__init__` and from the CLI, which the
static scan does not follow. Worth stating plainly because the first
reading of this same table reported two stages as unreachable and the
claim was an artifact of the method.

**Assessment: both in active use.**

---

## Is the chain coherent?

| link | status |
|---|---|
| data → features → strategies → forecast | coherent, verified |
| forecast → regime → router | coherent, never observed making a decision |
| router → promotion | **broken by design, correctly** — nothing to route to |
| promotion → portfolio → risk → order → execution | coherent, tested with synthetic and paper inputs |
| execution → ledger → monitoring | coherent, exercised |
| ledger → exchange (live) | coherent, **never run against a real venue** |

**One break, and it is the right one.** Everything downstream of promotion
is intact; everything upstream produces nothing the break will accept. The
system is not fragmented — it is gated, at exactly the point where a
commercial system should be gated.

The second structural observation: the research chain's output never
reached the execution chain *even before* the gate was tightened. The only
integration path exercised in this investigation was built during it. The
27 scripts that create policies all target research paths; the consumers
target runtime paths. Nothing joined them until `build_campaign_policy.py`,
and that script has run three times.

---

## What has been left out

Three categories, in descending order of significance.

**1. Fifteen of seventeen strategies have never run through a walk-forward
campaign.** Sixteen were measured by probe — a single pass over full
history, which is precisely what walk-forward exists to replace. Two went
through campaigns. A sign-off document ranks all sixteen, but its basis is
pre-fix artifacts (see `COMMIT_FILTER_AND_SIGNOFF_RECHECK.md`) and seven
of its rows quote figures with no artifacts on disk at all.

`funding_carry` has never been measured by anything: the daily parquet
has no `funding_rate` column, and the strategy emits no signal without it.

**2. The live venue has never been touched.** No order has reached an
exchange. Every execution result in this repository comes from a simulator
or a paper exchange. AC15 records this as blocked, and it is blocked
correctly.

**3. Drift and calibration modules are unconnected.** `online_learning`,
`ml/meta_learning` and the drift/calibration validators exist and are
tested, and nothing on the runtime path calls them. They would matter
after a strategy is live, which is the wrong time to discover the gap.

---

## Why this happened

The fabricated-policy incident has a specific cause worth naming, because
it explains the shape of everything above.

Promotion verified *provenance*: that fields existed, were non-empty, and
were well-formed. It never verified *truth*: that the score came from
running the code the `code_sha` names. A system can pass every structural
check while carrying invented numbers, and this one did, for a long time,
at scale.

The same shape recurs. Every wrong conclusion in the investigation was a
check that passed without the underlying thing being true — a `Forecast`
field that does not exist, an edge computed from an abstained run, two
engines compared at different position sizes, a fixture that passed
because construction was refused.

Three defects were found only after expensive compute, each of which a
cheap probe would have caught:

- `nested_wfo.tz_localize` broke every daily-bar run — the first WFO on
  daily data had ever been attempted, so it failed on first attempt
- `total_trades` excluded open positions, making a +44% run report zero
  trades
- the entry counter missed short entries entirely, inverting the density
  ranking

---

## What this means for what to do next

The system does not need more optimisation and does not need new rules.
Both of the proposals on the table — tightening an objective, loosening a
gate — act on a research chain whose output is not trusted downstream, and
the second would make the trust problem worse.

What the assessment supports, in order:

1. **Finish the evidence base.** One strategy measured end to end, plus
   `funding_carry`'s data prepared, is a smaller and more valuable step
   than any change to scoring. Fifteen unmeasured strategies is the largest
   genuine gap in the system.

2. **Wire the integration path and keep it wired.** Three campaigns ran
   through a script written during the investigation. That script is the
   only thing that has ever connected a measurement to a routed decision,
   and it has no test.

3. **Close the data freshness gap** — 35 days of daily data with no
   hourly equivalent — before any campaign whose window matters.

4. **Treat the drift and calibration modules as unproven**, not as
   existing capability, until something on the runtime path calls them.

5. **Fix the hourly/daily boundary before trusting any hourly result.**
   The 35-day shortfall is small enough to have gone unnoticed by every
   campaign run, which is the point: it is exactly the class of defect
   that passes without being checked.

The honest summary is that this system has a strong execution core, a
correctly-stopped research gate, and an evidence base that has never been
measured on the strategies it is meant to promote. The work ahead is
measurement, not machinery.