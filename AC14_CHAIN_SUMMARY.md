# AC14 Chain Summary — what the investigation found

Revisions `e78ff4b` .. `f4e2a91`. Six rounds, each answering a question the
previous round raised. Written 2026-09-28.

## The question AC14 asks

Does the adaptive router, selecting a strategy per regime from a promoted
policy store, beat a fixed incumbent on out-of-sample data?

## The answer

**Not established, and the reason is upstream of the experiment.** No
promotable policy in the repository is attributable to code that exists.
4,516 of 4,520 carry a `code_sha` matching no source on disk, and their
scores were three literal constants. Running an OOS campaign now would
evaluate fabricated evidence.

---

## Round 1 — synthetic baseline

`scripts/evidence_ac14.py`, 12/12 checks PASS, conclusion PASS.

**What it actually measured:** methodology, on simulated data. Both legs
lost money (adaptive -6.78%, incumbent -3.61%). The contract is explicit
that adaptive need not win — the verdict is about method. The reviewer's
objection was already recorded: *"negative synthetic comparison is not
efficacy evidence."*

## Round 2 — same, on real data

`scripts/evidence_ac14_real.py`, BTC/USDT 1h, 8,200 bars
(2022-04-14 .. 2023-03-22, chosen by `scripts/_probe_regimes.py` for
regime variety).

Result: adaptive -44.42% vs incumbent -43.78% vs buy&hold -40.15%.

**Three defects found, each of which had produced a flat 0.00% run that
looked like a legitimate result:** naive timestamps, a stale
`posterior.generated_at`, and policy validity not bracketing the window.
The router fails closed on all three — correct, and silent. Guards added
so the next occurrence aborts instead of reporting a plausible zero.

## Round 3 — why did everything lose?

`AC14_LOSS_DIAGNOSIS.md`. ma_crossover(10/20) is +11.19% frictionless and
-45.00% at real costs, against -30.25% buy&hold: the signal carries about
41pp of alpha and 32bps of round-trip cost erases it. Break-even cost is
4.8bps; median hold is 1 hour.

A timeframe sweep then disproved the tidy conclusion: only 1h has positive
frictionless edge. 4h is -17.05% and 1d -13.74% before costs — higher
timeframes hold through more of the bear. Two independent causes, not one.

## Round 4 — promoted policies, no hand-wiring

`scripts/evidence_ac14_promoted.py`. The round-2 run hand-assigned the
regime→strategy map, the policy scores and the posterior probabilities —
it measured a simulation, not the system. Replaced with an auto-scanned
policy store, real `HybridRegimeDetector` regimes (fitted pre-window), and
the canonical `Forecast` contract.

Result: adaptive -44.42% vs incumbent -42.37% vs buy&hold -40.15%.

**A second silent-pass was caught here:** `Forecast` carries
`expected_excess_return`, not an `action`. The first pass mapped a field
that does not exist, produced an all-zero signal series, and still
reported 6/6 checks. `P3` now asserts non-empty signals and the run raises
if every strategy is flat.

## Round 5 — is the strategy weak or is the window bad?

`scripts/evidence_ac14_windows.py` and `scripts/_test_enhanced_ma_windows.py`.

Four windows. The router traded on one: bear_2022, where it beat buy&hold
by +32.54pp. On both bull windows it abstained (`POSTERIOR_HIGH_ENTROPY`)
while buy&hold gained +84.05% and +105.54%; on the sideways window it
abstained for missing signed policy coverage.

The first version of that script reported `EDGE_FOUND` from two windows
where the router did not trade, by comparing a flat 0.00% adaptive leg
against a negative buy&hold. Windows where `router_traded` is false are now
marked `NOT MEASURED` and excluded from the verdict.

`enhanced_ma` — 9/9 WFO folds — then turned out to lose on the long leg in
all five windows tested, with the short leg worse everywhere (-11.08% even
in the -40% bear) and exposure of 1.2–1.5%, meaning it is flat ~98% of the
time. Its folds certify a low-drawdown profile, not profitability.

## Round 6 — the audit

`scripts/audit_policy_returns.py` and `POLICY_RETURN_AUDIT.md`, asked
"how many policies have `median_oos_return_pct < 0.001?"

**Zero of 2,690** — which is not reassurance, it means the field was never
measured. Across the whole store: three distinct return values
(0.02 / 0.05 / 0.10), two trade counts (30 / 40), and `selection_score`
a pure function of trade count (2,380 policies at 0.4/30 trades, 310 at
0.5/40 — exact). The values are literals in `run_tournament_shadow.py`,
`live_data_pipeline.py`, `t8a_cross_asset_stress.py` and
`t8a_tournament_validation.py`, written straight into
`SelectionPolicyArtifact.scores` with no measurement step.

Break-even is 4.8% at 30 trades; the median policy promised 2%.

**Provenance was fabricated too**, which is what actually settles it:
`code_sha` is `"c" * 64` / `"t" * 64` / `"challenger-002"` /
`"live-pipeline-001"`, `policy_commit_sha` is a repeated character, and
`evidence_ids` are labels rather than hashes. Comparing `code_sha` against
the hash the canonical registry computes from the source on disk leaves
**4 attributable promotable policies out of 4,520**.

---

## What changed in the system

| Revision | Change |
|---|---|
| `19ec13c` | `_require_measured_scores()` — a score without its OOS metric family cannot reach VALIDATED/ACTIVE. The four generators write `scores={}` and DRAFT. `StrategyEvidencePolicy.required_return_pct()` replaces the flat 0.0% floor with `0.32% × trades`. |
| `b872c5f` | `require_trusted()` as an explicit boundary; `activate()` routes through it. |
| `8870e03` | `_require_attributable_code()` — the presence check was insufficient, because the generators wrote complete metric blocks full of constants. A score must now be traceable to code that exists. |
| `f4e2a91` | 1,215 tracked policy artifacts removed from the live tree; 43 stores quarantined under `data/policies_quarantine_20260928/` with a rollback manifest. |

Tests: 84 pass across the gate, lifecycle, router, R06 and live-safety
suites; 17 in `tests/test_policy_evidence_gate.py`.

---

## Why this is the finding and not an implementation detail

Rounds 1–5 each produced a conclusion that was wrong, and in each case the
error was the same shape: **a check that could pass without the underlying
thing being true.**

- Round 2: three fail-closed gates returned a flat 0.00% that read as
  "no edge" rather than "nothing ran".
- Round 4: `Forecast.action` does not exist; mapping it gave 6/6 checks on
  an all-zero signal series.
- Round 5: comparing an abstained run against a negative baseline
  manufactured +32pp.
- Round 6: a metric-presence gate passed 38 of 44 stores, because the
  fabricators supplied every metric the gate asked for.

Every one was caught by running the thing rather than reasoning about it.
That is the pattern worth carrying forward, and it is the reason the
evidence scripts assert `router_traded`, non-empty signals, and attributable
code rather than reporting whatever number comes out.

## What the system looks like now

The live policy store is empty. That is the correct state — the gate would
refuse the old artifacts on load — but it is not a running system. The next
step is not analysis: it is a real WFO campaign that writes measured
metrics and a `code_sha` matching the canonical source. AC14 stays BLOCKED
until that exists.
