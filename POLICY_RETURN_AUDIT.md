# Policy Return Audit — the numbers are fixtures, not measurements

## Question asked
How many promoted policies have `median_oos_return_pct < 0.001`?

## Answer
**Zero — 0 of 2,690.** But that is not reassurance. It means the field was
never measured.

## What the audit found

```
policies audited        : 2690 across 34 stores
median_oos_return < 0.1% :    0  (0.0%)
median_oos_return < 0     :    0  (0.0%)
can pay round-trip cost   :    0  (0.0%)   <-- every single one
```

Distribution of `median_oos_return_pct`:

| bucket | count |
|---|---|
| [1%, 5%) | 2080 |
| [5%, 10%) | 560 |
| >= 10% | 50 |

min = p25 = median = p75 = **0.0200**, max = 0.1000.

## Why this is not data

Only **three** distinct return values exist across all 2,690 policies:
`0.02`, `0.05`, `0.10`. Only **two** distinct trade counts: `30`, `40`.
`selection_score` is a pure function of trade count (0.4 ↔ 30 trades,
0.5 ↔ 40 trades) — 2,380 and 310 policies, exactly matching.

Real WFO campaigns produce continuous, varied, and sometimes negative
results. Three quantised values cannot be measurements.

Source, confirmed in the generating scripts:

- `scripts/run_tournament_shadow.py:249,107` — `"median_oos_return_pct": 0.02` / `0.05`
- `scripts/live_data_pipeline.py:651,602` — `0.02` / `0.05`
- `scripts/t8a_cross_asset_stress.py:270,310` — `0.10` / `0.05`
- `scripts/t8a_tournament_validation.py:124` — `0.10`

The scripts write literal constants into `SelectionPolicyArtifact.scores`
with no measurement step. `code_sha` is likewise literal (`"f" * 64`,
`"live-pipeline-001"`), and `evidence_ids` are `sha256:shadow-chal-...`
strings that hash nothing.

## Break-even, which is the number that matters

Break-even is not a flat threshold — it scales with how often a policy trades:

```
break_even_return = 0.32% x (median_oos_trades / 2)
                  = 4.80% at 30 trades
                  = 6.40% at 40 trades
```

Best recorded return anywhere is 10%; median is 2%. The typical policy
promises **2%** while needing **4.8%** just to stand still. Every policy in
every store is insolvent by a factor of 2-3x.

## Status of the insolvent policies

```
status : validated 2530 | active 155 | expired 5
stage  : research_validated 2080 | shadow_eligible 260 | paper_eligible 350
ACTIVE (would be tradeable): 155
```

155 active policies would be eligible to trade, all insolvent, all with
fabricated scores. The active ones are predominantly `enhanced_ma` —
the strategy that ENHANCED_MA_FINDINGS.md showed loses on the long leg in
all five windows tested.

## What this means

The promotion pipeline has never gated on measured profitability:

1. `sweep_evidence_params.py:129` checks `median_return > policy.min_median_oos_return_pct`
   — that filter exists, but it is fed constants, so it always passes.
2. `selection_score` is derived from trade count, not from risk-adjusted return.
3. The router's `min_policy_coverage` and entropy gates were tuned against
   these fixtures, so gate behaviour observed in AC14 reflects fixture
   distributions rather than real ones.

Everything measured downstream of the policy store — router abstention in
the bull windows, `enhanced_ma` being "the" incumbent — is being selected
from fabricated scores.

## Recommendation

Do not tune the selection score on the existing data; there is no signal to
tune against. In order:

1. **Mark the store untrusted.** 155 active policies carry fabricated
   scores and should be demoted until re-measured. This is a safety
   action, not a scoring change.
2. **Make the generator measure.** The scripts listed above must either run
   a real WFO pass or stop writing `scores` at all. A policy with no
   measurement should have `scores=None` and be non-promotable.
3. **Add a cost-realistic gate** once real numbers exist:
   `median_oos_return_pct > 0.32% * (median_oos_trades / 2)`.
4. **Re-run AC14** after step 2. The current verdicts (EDGE_FOUND,
   router abstains in bull) describe a router fed fixtures and cannot
   support a go-live decision.

Reproduce: `.venv/bin/python scripts/audit_policy_returns.py`
Evidence: `/tmp/policy_return_audit.json`
