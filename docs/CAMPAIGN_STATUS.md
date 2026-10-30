# CAMPAIGN STATUS — authoritative tracker

Single source of truth for the measured-accounting and campaign-publication
work. Supersedes the per-session `*_TODO.md` files, which are gitignored and
were fragmenting the state across ~60 files.

## Current — 2026-10-30 / THINNING_DEVELOPMENT_COMPLETE (descriptive only; market otherwise paused) / PLUS_MATRIX_NO_SUPPORTED_INCREMENT

### 2026-10-30 — thinning development execution complete, descriptive only

DEVELOPMENT_COMPLETE. `data/btc_thinning_execution_approved_20261030_02/` ran
04:03:50Z–05:17Z, 4370 s of its 23 400 s budget, under `controlled_exec`
(rc 0). All 36 cells COMPLETED and verified. Evidence class
EXPOSED_DEVELOPMENT_NOT_CONFIRMATION; statistical conclusion
DESCRIPTIVE_ONLY_NO_CI_OR_EQUIVALENCE_CLAIM; not qualified, not live. The first
approved packet `_01` never started: its source binding broke when unrelated
`.py` commits landed after it was prepared (see its APPROVAL.md and `_02`'s).

Primary metric: net return on entry notional. PLUS (own 24 h return > 0 and BTC
24 h return > 0) against five random-thinning controls of matched intensity.

| Pair | Cost | PLUS | Controls mean (min..max) | PLUS above n of 5 | PLUS marked return | PLUS max DD |
| --- | --- | --- | --- | --- | --- | --- |
| ETH/USDT | 1x | −0.497 % | −0.487 % (−0.525..−0.427) | 2 | −35.7 % | 36.3 % |
| ETH/USDT | 2x | −0.810 % | −0.777 % (−0.821..−0.727) | 1 | −51.1 % | 51.6 % |
| ETH/USDT | slip stress | −0.901 % | −0.857 % (−0.877..−0.805) | 0 | −54.8 % | 55.2 % |
| BNB/USDT | 1x | −0.324 % | −0.358 % (−0.390..−0.313) | 4 | −23.0 % | 24.0 % |
| BNB/USDT | 2x | −0.614 % | −0.660 % (−0.715..−0.627) | 5 | −39.2 % | 39.7 % |
| BNB/USDT | slip stress | −0.713 % | −0.750 % (−0.787..−0.700) | 4 | −43.9 % | 44.4 % |

Descriptive reading, no preregistered decision rule exists for this diagnostic:
every arm loses at every cost. On ETH the BTC-context filter does no better
than dropping the same share of entries at random. On BNB it loses less than
most random controls but still loses 23–44 % of the portfolio. Nothing here
supports an increment, a confirmation cohort or a holdout read. This result does
not define the next step.

### 2026-10-30 — thinning execution approved on the current source, not run

APPROVED_NOT_RUN. The operator (huythongbk15) approved the BTC thinning
development execution at 03:53:54Z. The 10-29 proposal `_02` no longer verified
(`preparation source mismatch`: source `5cdda1af`, 19 `src` files changed since)
and stays untouched, as its REVIEW.md asks. A fresh proposal
`data/btc_thinning_execution_20261030_03/` on source `bc5136fb` differs from `_02`
only in source and fingerprint: calibration ETH 3647/4356 and BNB 3267/4325 with
0 missing, the 36 cells, budget, design, windows, inputs and environment are
identical. The protocol is write-once, so the approval is a separate packet
prepared again with the same prepared fingerprint:
`data/btc_thinning_execution_approved_20261030_01/` (scope
`development-thinning:30e6437d…`, protocol fingerprint `d4bbd2b0…`;
`thinning_execution.verify(approved=True)` passes). Provenance and accepted
limits are in its APPROVAL.md: no independent release review or full remote CI,
market data reopened for this scope only, exposed development diagnostic,
never qualification or live evidence. Running is a separate operator decision.

### Full BASE–PLUS development matrix closed

12 logical cells: ten new cells plus two sealed ETH1x references. All six comparisons completed, zero IMPROVES. BNB return improves by3.88/4.55/6.03pp but remains negative; ETH1x worsens1.98pp, ETH stress improves1.49/1.90pp but remains negative. All returns and Sharpes are negative. PLUS gross PnL worsens in all six comparisons while modeled costs fall. No exposure-matched control was measured; this does not reject BTC information in general.

All12 raw report audits PASS:7976 signal exits exactly24h, no BUY during breaker intervals, no missing required return features, reconciled marked equity, zero terminal inventory/orders or unresolved health. QA counts are not independent observations. 131 tests and AC01–15 PASS on source d6afc7b26a809cb318d911c4d69dcfec8f4d142ba1c0135a07a24e26469840ce. ETH1x retains earlier sealed source; core equivalence is recorded, but no single-revision qualification is claimed. All14 known attempts including the defective pilot remain disclosed. Total market execution2737.42s; new batch1950.07s; zero retries.

Full report (local evidence `data/plus_context_matrix_20261029_01/RESULTS.md`), MATRIX_RESULTS.json, RAW_AUDIT.json and wave_protocol.json hold primary evidence. CLOSED_NO_SUPPORTED_INCREMENT / NOT_QUALIFIED. No further run, confirmation, paper/testnet/live authority; no independent review claimed. CI remains separate. Static draft authority functions are design snapshots; protocols and this tracker record actual progress.

### 2026-10-29 — validation binding bypass resolved on integrated source

P1 from data/revision_integration_review_20261029_01/ is fixed in local commit5cdda1af. CampaignCellRunner always performs scoped admission regardless of caller validation_only flag. QualificationCellRunner verifies its actual frozen protocol plus subject/token/native-cell membership, preserving legitimate validation instead of reconstructing a PLUS-specific envelope. No threshold/approval relaxation.

Fix was first tested in an isolated source snapshot while the other agent ran CI. After shared tests ended and baseline SHA matched, patch integrated. On MAIN:60/60 gate/cache/input/protocol regressions PASS,3/3 canonical launcher integration PASS including real synthetic worker/coverage. Fresh AC01–15 ALL PASS, source5b2c0ac3efb41ffef7b117029974ac9ffec540b0e668f114c5536733aac55d0d, identity stable. Lint clean. Broader isolated suite hit240s timeout; retained as incomplete, not reported as all-green CI.

Current operational proposal:data/btc_thinning_execution_pending_20261029_02/, freshly bound to fixed source, calibration unchanged ETH3647/4356 and BNB3267/4325. PENDING_OPERATOR_APPROVAL/null identity fields, zero market cells. Prior proposals/source-specific proofs stay immutable; no silent reseal. Closeout:data/binding_gate_fix_verified_20261029_01/RESULTS.md.

Our fix is committed; user analysis scripts/reports and session/Claude files remain untouched/uncommitted. Source capture remains dirty for those pre-existing analysis scripts; no claim of clean independently reviewed release, full remote CI green, qualification or live authority. No push/merge/market run.

### 2026-10-29 — scoped thinning execution engineering complete on synthetic

Worker/admission implemented in thinning_execution.py; original BASE/PLUS lane retained through typed-request dispatch. 128/128 regression tests PASS plus one full E2E:36/36 real subprocess workers on generated synthetic parquet,193.35s controlled, one worker/no retry. Harness injects fixture ROOT/source context only; validator, CLI main, supervisor, worker, native engine and metric exporter run for real. Approval in this harness is synthetic-fixture-only, not operator authorization.

Raw SQLite audit reproduces incremental BUY-fill denominator for all36 cells, including matching report/protocol/metric hashes, per-cost contracts, entry hash timing,24h exit and terminal flat. Separate bound-worker carry regression proves final marked equity−10000 and positive BUY notional even with no closed trade. Partial fills counted as deltas; duplicate event ids idempotent, conflicting duplicates rejected; zero denominator remains undefined. Summary keeps all six pair–cost groups and five seeds, descriptive only.

Code commit3f3a1512, source49c4e4a223256f320dc95c9866d25be1b5b75a19d22f45961291c8999d79f269. Actual pending packet:data/btc_thinning_execution_pending_20261029_01/. Approval fields remain null/PENDING_OPERATOR_APPROVAL. Actual --run negative test returned NOT_AUTHORIZED_TO_RUN before creating cells/execution journal; market cells0. Exact calibration remains ETH3647/4356,BNB3267/4325. No holdout/OOS/live, no push.

Evidence and limits:data/thinning_execution_e2e_20261029_01/RESULTS.md. Source snapshot still includes user-owned untracked analysis scripts; not a clean independently reviewed integrated release. Old preparation-only limitation is superseded by this worker proof; prior packets stay unchanged. Next gate is clean integration/review of exact source and explicit scope/budget authorization; no further engineering feature or run is scheduled automatically.

### 2026-10-29 — fixed calibration and36-cell preparation verified

PREPARED_NOT_AUTHORIZED. Calibration[216,8760), evaluation[8760,25198), ETH/BNB1h only. Exact p:ETH3647/4356 (83.7236%), BNB3267/4325 (75.5376%), missing0 of8544 calibration rows per target. Independent Arrow/direct-ratio implementation reproduces counts (same-author audit, not independent reviewer). All input hashes and full clocks rechecked; only timestamp+close decoded, full already-exposed25198rows per file, only prefix used to compute p. No strategy/simulator market evaluation.

Packet:data/btc_thinning_development_preparation_20261029_01/. Binds frozen design v5 hash, current source28d5537a…, runtime/config, exact calibration counts,36cell identities/seeds, per-cost native contracts, windows/flat10000initialization, metrics and proposed budget23400s. Execution entrypoint absent, authorization NOT_AUTHORIZED_TO_RUN; actual authority check raises PermissionError. Rehashed approval/field tamper rejected. 87/87 final targeted tests PASS; lint PASS. Local code commit e65af9da.

Source snapshot remains worktree_dirty=true due preserved user analysis scripts, not a clean reviewed integrated release. Preparation source differs from earlier synthetic proof; old acceptance evidence not reassigned. No market qualification or independent review claimed. Full market worker/launcher admission remains unimplemented; this preparation packet cannot be activated merely by editing an approval field. No further execution is scheduled.

### 2026-10-29 — thinning launcher synthetic proof closed

Control thinning and scoped synthetic launcher verified; 36/36 fixture cells completed in160.95s. Six PLUS cells plus30 controls (five seeds per pair/cost), calibration5/10 on synthetic[216,240), evaluation[240,280). These fixture windows/counts are NOT the frozen market study windows/p. 75/75 targeted tests and lint PASS; raw audit verifies per-cost contracts, hash entry timing,24h exits, marked equity accounting, flat inventory/orders and cross-cost deterministic entry vectors. Source19b1c257… stable.

Initial attempt01 stopped before simulation because synthetic manifest label lacked required prefix; retained INCOMPLETE. Fixed label; fresh attempt02 completed, no cell retries. No guard/gate thresholds changed. Evidence:data/btc_thinning_launcher_synthetic_20261029_02/RAW_AUDIT.json and RESULTS.md. SYNTHETIC_VERIFIED, NOT_AUTHORIZED_TO_RUN, no economic conclusion. Existing market runner unchanged; calibration on real bound input and complete market execution binding/admission remain outside this proof.

### Historical pilot status (before matrix completion)

### 2026-10-29 — bounded development pilot closed NO_INCREMENT

Two fixed ETH/USDT 1h cells (BASE A0 vs PLUS BTC-return24>0), cost1x,
window[216,25198), shared source/input/native policy completed. After correcting
risk admission, BASE:785 trades, return-44.48135%, Sharpe-2.07383, DD45.64071%;
PLUS:717 trades, return-46.45637%, Sharpe-2.33912, DD48.08630%. PLUS worsens return
by1.97502pp: model costs fall357.96405USDT but gross alpha PnL falls555.46643USDT.
Verdict NO_INCREMENT, not qualified. This is exposed development, not OOS or
full twelve-cell coverage. BNB/2x/slip remain unmeasured.

The first two-cell attempt is preserved as diagnostic: fill-cycle flat entry
could bypass breaker/cooldown, and emergency exits were mislabeled signal.
Two synthetic regressions failed before fix. Corrected source uses authoritative
risk state, blocks flat entries, keeps reducing expiry and stamps risk exits at
the risk phase. Actual corrected reports have all688/625 signal exits at24h,
no BUY in active breaker intervals,96 risk-entry halts per arm, no terminal
inventory/orders or unresolved execution health. No thresholds/params retuned.
119 integrated tests and fresh AC01–15 PASS on source0f77fa29… (commitfd9049c9).
Both market attempts total787.356s under the original1500s envelope; no cell retries.
Source stayed stable; no more market authority, no confirmation/paper/testnet/live.
Local evidence: data/plus_eth1x_development_20261029_02/RESULTS.md, raw_audit.json,
protocol/receipts/reports; original attempt01 and its findings retained. Full CI
and independent review are separate; no independent acceptance claimed.

### Historical 2026-10-28 — synthetic-stage status


### 2026-10-28 — PLUS fill-policy wiring verified on synthetic

The separate fill policy now accepts PLUS and applies only the pair/BTC 24h
positive-return predicate. The legacy own24_context constructor/source is
unchanged. The existing synthetic launcher produced real reports: A0 has one
24h trade for BTC up/down; PLUS has one for up and none for down/missing.
Terminal positions/orders are zero in all five cases. Net PnL per constructed
flat-price trade is -5.62485 USDT from cost/slippage; this is wiring evidence,
not predictive/economic value. Final regression: **142 PASS**, Ruff/diff clean,
source stable `d5561a10…`; no new AC/full-CI claim and no market cells.
PLUS market contract now explicitly says DEVELOPMENT_ENTRYPOINT_NOT_IMPLEMENTED;
the nonexistent pytest command and stale revision are removed. The simulator
still refuses market input for the fill-cycle prototype.
Local evidence: `data/plus_execution_readiness_audit_20261028_01/FINAL_RESULTS.md`
and five preserved raw reports in `final_verification_reports/`. Mainnet NO-GO.

### 2026-10-28 — fill-timer native runtime prototype verified

Opt-in immutable position-cycle input now carries actual first-fill time,
entry-order identity and remaining inventory into a separate, unregistered
`own24_fill_context` policy. It holds until24h from the first fill, keeps the
clock through partial fills/exits and restart, and prioritises expiry over
missing market context. Historical snapshots lacking cycle attribution are
refused; no live-state fallback. Legacy `own24_context` bytes are unchanged.
The broker Order wrapper also preserves PARTIALLY_FILLED and confirmed fill
quantity instead of reporting partial/ACK/OPEN as fully filled.

The 28/10 native receipt remains 118 PASS on source `69f2a648…`. A same-agent
review of that exact source found the driver skipped HOLD and historical
snapshots could not carry a fill cycle. Those two gaps are now closed on
synthetic data: every cadence bar asks the runtime, including HOLD, and the
order still passes through the existing authority and protection. A historical
snapshot without cycle fields is still refused and is not filled from live
state. Partial remainder and a restored position keep the first fill.

Launcher proof: `scripts/run_wfo_parallel.py --fill-cycle-synthetic` ran
launcher → worker → simulator → report → summary. One ETH synthetic round trip
held 24 hours. 36 targeted tests PASS in 20.64s. Precomputed signals and a
frozen market binding are rejected. This is not qualification, not a market
rerun, and not multi-pair parity. H-OWN24 stays closed. Mainnet NO-GO.
The design wave now has its window locked and its entry rule unset:
W-INFORMATION-GATE-01 — local evidence `data/w_information_gate_20261028_01/RESULTS.md`.
Twelve cells would be two sessions of six, 3600s each, because 12 × 600s
equals the 7200s cap. No cells run until a mechanism other than H-OWN24
is written and separately authorized.
Results, receipts and bounded next steps — local evidence `data/x2_fill_timer_synthetic_20261027_01/RESULTS.md`.

### 2026-10-27 — H-LIQ-REVERSAL-01 closed RESEARCH_ABSTAIN

H-LIQ-REVERSAL-01 completed one authorized validation-only packet, 3/3 cells,
coverage PASS, in data/liq_reversal_validation_run_20261027_01. Protocol
f65b1804…, input 45ba1b…, dirty source a9133770… on commit 3b1cfcda,
measurement 6510,15150). Trades 95/94/94. Sharpe −1.931 / −3.164 / −3.591.
Return −7.90 / −12.74 / −14.38%. Gross is already negative. Selection ABSTAIN.
Sample complete, all_folds_selected false. No open inventory. Registry
sidecar data/liq_reversal_registry_closeout_20261027_01 is
RECONCILED_READ_ONLY_3_OF_3: one INNER_VALIDATION row per cost and the
stored Sharpe matches the sealed report. The sealed ledgers still say
UNKNOWN_NOT_RECONCILED. provenance_eligible stays false. Supervisor 733.795s, sampled RSS 753.54 MiB,
one attempt per cell. Independent pre-run review accepted one run; post-run
review VERIFIED_RESEARCH_ABSTAIN_NOT_QUALIFIED. `no_further_run_authority` is
true. Not confirmation and not a ranking. Mainnet stays NO-GO.
[Result — local evidence `data/liq_reversal_validation_run_20261027_01/RESULTS.md`.
Post-run review — local evidence `data/liq_reversal_validation_run_20261027_01/POST_RUN_REVIEW.md`.
Registry sidecar — local evidence `data/liq_reversal_registry_closeout_20261027_01/RESULTS.md`.

Review follow-up27/10: **CHANGES_REQUIRED / MARKET_PAUSED**. Existing reports
show a holding-contract mismatch (742/890 ETH A0/1x signal closes are under
24h), persistent ETH A1/slip inventory (~0.0003 ETH) blocking later entries,
and cross-source A0 versus A1 comparisons without established equivalence.
The measurements remain negative diagnostics of the executed implementation;
they do not reject BTC context or the wider information pool. A nonfinite
metric label guard was fixed; 46 targeted tests PASS on the new code. Market
packets are preserved. Next: synthetic exit/residual contracts, exact source
comparability, then D1–D8 information-design discussion; no market rerun.
Review and actionable handoff — local evidence `data/x2_context_review_20261027_01/RESULTS.md`.

Execution follow-up27/10 identified the ETH residual's concrete source: an old
0.3511 ETH stop survived emergency close and later sold a new0.3514 ETH entry.
Engine emergency close now cancels/reconciles owned protection (including
retry stops), rereads inventory after cancel/fill races and marks gaps if exit
fails. **69 targeted tests PASS**, including7 new local-paper regressions.
Old market packets are unchanged; no market rerun. Holding-from-fill is still
OPEN: runtime currently has no fill-cycle view and precomputed signals cannot
implement that timer. The bounded integration contract is in the
new handoff — local evidence `data/x2_execution_followup_20261027_01/RESULTS.md`.
This fix changes source; prior campaign approval/evidence does not transfer.

Prior run summary (labels require the review caveats above):
H-OWN24-CONTEXT-01 has no IMPROVES cell.
A0 is negative on ETH at all three costs and on BNB at 1x and 2x. Where the
execution path is stable, A1 versus A0 and A2 versus A1 are NO_INCREMENT or
NO_CONFIRMATION. ETH slip contrasts are INCONCLUSIVE because A1 slip fell
to 311 trades. BNB A0 slip was not accepted. Not qualification. Mainnet
stays NO-GO.
Result — local evidence `data/x2_context_run_20261027_01/RESULTS.md`.
User27/10 authorised a plan for BTC/USDT context and ETH/USDT plus BNB/USDT
targets, with shared verification and possibly pair-specific policy/params.
[Plan §10](operations/MULTI_STRATEGY_RESEARCH_PLAN.md#10-kế-hoạch-btc-context-và-policy-riêng-theo-pair)
defines X0–X6, causal input/feature binding, A0/A1/A2 and separate parameter
personalisation evidence. This changes research design, not current runtime
or market authority. X0 metadata result:
ETH/BNB clock check — local evidence `data/x0_context_admission_20261027_01/RESULTS.md`.
The two pre-holdout 1h files match all 25198 frozen-BTC timestamps and both
passed `assess_ohlcv` with `gap_policy=reject`: 0 gaps, 0 nulls, 0 invalid
OHLC, every `is_closed` true. Prices of those two hashes are now read.
Status QUALITY_ACCEPTED_DEVELOPMENT_INPUT_NOT_CONFIRMATION. No implementation
or run approval here.
Do not rerun a closed packet/retune its rule or inherit its authority.
Runtime parity, paper, testnet and mainnet remain blocked.

Steering27/10 expands the candidate information pool: research broadly across
market/relative/liquidity/positioning/dominance/events/on-chain/macro/attention
groups, then retain what adds reproducible predictive/economic value per
target. The six-feature draft is a baseline, not a discovery cap. Plan §10.8
adds source/available-at profiles, group/interaction tests, nested time-aware
selection, trial burden and a per-pair evidence table. Feature importance or
ablation alone does not identify a causal market effect. New provider groups
are NOT_ADMITTED until checked; the declined news pilot stays declined.
X0 clock check is recorded and is not a price screen, a new collector, or a
market run. Market authority remains NONE.

27/10 desk-research follow-up is complete, documentation only. The
[decision memo](RESEARCH_EVIDENCE.md) distinguishes failed specific rules
from entire strategy families and checks primary literature against the
BTC spot1h long-only constraints. No new eligible mechanism was established
in the reviewed sources. Recommended action remains PAUSED on the current
profile; attention/new information or horizon/universe changes are separate
scope decisions, not chosen strategies or run authority. Six admission
questions must be answered before presenting any future proposal. No source,
tests, sealed packets, market prices or runtime were changed by this follow-up.

External-information feasibility is also complete on paper. Existing
sentiment/news modules inspected do not establish an eligible external
information archive: the agent uses technical inputs, the aggregator has
synthetic news and no implemented live fetch, and the LLM extractor consumes
caller-supplied articles. Google Trends official API access/lag/resolution
and GDELT availability/archive limits are documented in the same decision
memo. No provider data request or LLM call was made. A metadata-only pilot
envelope stays NOT_AUTHORIZED. Decision 27/10: do not spend the 20-request
pilot. A metadata sample cannot establish 2023 vintage, a confirmation cohort,
or an edge after costs. PAUSED and the current price-only scope stay.
Market authority NONE.

27/10 design note, not a hypothesis and not run authority: validate with one
method, while signals and parameters may differ by pair. BTC is both a
tradable asset and a lagged context. A shared policy may consume BTC
return, volatility, drawdown and a past-only beta residual. Relative strength
is not a positive USDT return, and the residual is not assumed to mean-revert.
BTC dominance is out of scope without a consistent market-cap source. A later
proposal would have to pass pair-only, then BTC context, then the residual,
each showing an after-cost out-of-sample gain on a pre-frozen window. The
17/10 ETH/BNB screen is not that evidence. Literature on minute-level
cross-crypto predictability is not evidence at 1h. PAUSED remains.
[Design note](RESEARCH_EVIDENCE.md).


Closeout update: registry attribution is now RECONCILED_READ_ONLY_9_OF_9
for H-PULLBACK-01, H-PULLBACK-TEMPORAL-01 and H-VOLBREAK-01. Each exact
execution context has one matching INNER_VALIDATION registry row and its
Sharpe matches the sealed raw report. This is an integrator read-only
sidecar, not independent review or qualification. Original UNKNOWN fields
and the sealed packet index remain historical and unchanged.
Registry closeout and remaining integration — local evidence `data/research_closeout_20261026_01/RESULTS.md`.
No further volatility-breakout confirmation is planned. The measured
liquidity-reversal source is on commit e7d7dd5c and the worktree is clean.
The H-LIQ packet itself remains the dirty-tree record a9133770. That commit
does not qualify any study.

H-VOLBREAK-01 completed 3/3 cells, coverage PASS, in
data/volatility_breakout_validation_run_20261026_01. Protocol ac1c79d5…,
input 45ba1b…, tree d2864272… dirty, measurement 6552,15192). Each cost has
53 closed trades. Selection SELECTS 1x only: Sharpe 0.321 / −1.266 / −1.767,
return +0.575 / −2.385 / −3.352%. 2x and slip stress fail the Sharpe floor.
No open inventory. Report accounting reconciles. Registry SQL remains
UNKNOWN_NOT_RECONCILED. Supervisor 726.946s, sampled RSS 757.45 MiB, no retry.
Verdict DIAGNOSTIC_POSITIVE_NOT_QUALIFIED. Separate-agent post-run review:
VERIFIED_DIAGNOSTIC_POSITIVE_NOT_QUALIFIED. Exposed window, dirty tree,
strategy_qualified false. Not confirmation and not a ranking against pullback.
`no_further_run_authority` is true. Mainnet stays NO-GO.
[Volbreak result — local evidence `data/volatility_breakout_validation_run_20261026_01/RESULTS.md`.
Post-run review — local evidence `data/volatility_breakout_validation_run_20261026_01/POST_RUN_REVIEW.md`.
Pre-run review — local evidence `data/volatility_breakout_review_20261026_01/REVIEW.md`.

The previously selected minimum spot runtime path is Binance Spot,
BTC/USDT, 1h, long-only, one pair. The new research design in §10 does not
activate ETH/BNB trading or qualification. The runner still pins
`expected_strategy_name="enhanced_ma"`. Futures, margin and funding carry stay
outside it.

Still required, in order, before any live order:

1. A mechanism that is not a retune of a closed rule. Research is paused
   while the closed measurements are read together. No new strategy is opened
   from this closeout.
2. H-LIQ-REVERSAL-01 already had its one bounded validation and is closed.
   A future hypothesis would need its own measurement. That measurement has
   not been defined.
3. Only if a future measurement selects a cost-robust candidate: a confirmation
   cohort that is not the exposed 25198-row file, and a DSR/PBO method locked
   before the outer result is read.
4. `QUALIFIED_RESEARCH` on a revision that matches the measured source.
5. Bind the exact qualified subject throughout `scripts/live_enhanced_ma_binance.py`
   (constructor, strategy labels and admission expectation, not just one string),
   then prove runtime parity including any required BTC context on the same input.
6. Operator account, capital and loss budget. Testnet evidence is still 30
   continuous days, 100 complete lifecycles, 0 unresolved orders and 0 safety
   breaches, then a separate canary approval.

Why the closed long-only BTC 1h measurements failed:

- Sample: H-PULLBACK-01 has 25 trades, under the 30-trade floor, so the
  fold abstains. Its 1x Sharpe is positive and is not a qualification.
- Price path: MA20/50 (64 trades, Sharpe −0.162 / −1.544 / −1.995) and
  H-PULLBACK-TEMPORAL-01 (40 trades, Sharpe −0.201 / −1.167 / −1.476) do
  not clear the floor. Temporal gross is positive and net is negative, so
  that continuation lost to costs. RSI fixed policy abstains at 39 trades.
  These windows are not ranked against each other.
- Cost-fragile gross: H-VOLBREAK-01 gross stays positive and only 1x clears
  the inner floor (Sharpe 0.321). 2x and slippage stress fail. Confirmation
  was declined. This does not reject every expansion rule.
- Gross loss: H-LIQ-REVERSAL-01 gross is negative on the fixed 1.5% drop and
  6-bar hold. At 1x the 91 signal exits lose as well as the 4 stops. BUY
  signals stay 95 at every cost. Stress costs record circuit_breakers 1 and
  funnel terminals 2 and 3, with 94 closed trades, so the gap from 1x is an
  execution-path change plus fees. This rejects that rule and native policy
  on 6510,15150) only.

The repeated failure is the absence of a cost-robust edge. It is not a
missing runner and it is not an unfinished audit. Mainnet stays NO-GO.
[Packet index — local evidence `data/research_packet_index_20261026_01/INDEX.md`.
E3 proposal — local evidence `data/volatility_breakout_e3_proposal_20261026_01/E3_PROPOSAL.md`.

H-PULLBACK-TEMPORAL-01 is closed. One authorized packet completed 3/3 cells,
coverage PASS, in data/trend_pullback_temporal_validation_run_20261026_01.
Protocol 6733c156…, input 45ba1b…, tree d2864272…, measurement 6672,15312),
600/2100s, 1 worker, 0 retry. Each cost has 40 closed trades. Selection ABSTAIN
because validation Sharpe is not above the floor: −0.201 / −1.167 / −1.476.
Return −0.479 / −2.696 / −3.424%. No open inventory. Accounting inside the
reports reconciles. Registry SQL remains UNKNOWN_NOT_RECONCILED. Supervisor
675.299s, sampled RSS 753.38 MiB, no retry. This window was already exposed.
It is not confirmation, not a ranking against H-PULLBACK-01, and not
qualification. `no_further_run_authority` is true. Mainnet stays NO-GO.
[Temporal result — local evidence `data/trend_pullback_temporal_validation_run_20261026_01/RESULTS.md`.
Separate-agent post-run review: VERIFIED_RESEARCH_ABSTAIN_NOT_QUALIFIED.
Protocol objects match; file bytes differ only by JSON key order.
Post-run review — local evidence `data/trend_pullback_temporal_validation_run_20261026_01/POST_RUN_REVIEW.md`.
Pre-run review — local evidence `data/trend_pullback_temporal_review_20261026_01/REVIEW.md`.

Temporal proposal — local evidence `data/trend_pullback_temporal_prereg_20261026_01/RESULTS.md`.
Inventory — local evidence `data/confirmation_design_20261026_01/RESULTS.md` and
design/tasks — local evidence `data/confirmation_design_20261026_01/CONFIRMATION_DESIGN.md`.

H-PULLBACK-01 one authorized packet completed3/3cells,coveragePASS in
data/trend_pullback_validation_run_20261025_01 (directory created25/10; actual
execution26/10). Separate-agent review accepted current source d2864272… and
protocol081fce81…; authorization hash-binds the verdict, proposal, assertions,
config and adapters. Exact fresh protocol matches preparation _02. One fold,
three cost cells,600s/cell2100stotal,1worker2048MiB0retry/fail-fast.
Eachcost25closedtrades<30,selectionABSTAIN,sample_complete=false. Sharpe
0.695/−0.633/−1.060;return+0.721/−0.688/−1.154%. No terminal inventory;
accounting reconciled. Supervisor245.659s,RSS719.61MiB,no retry/cache/timeout.
Results and next decision — local evidence `data/trend_pullback_validation_run_20261025_01/RESULTS.md`.
Review — local evidence `data/trend_pullback_validation_run_20261025_01/REVIEW.md`.
Earlier BLOCKED_REVIEW below concerns old preparation _01, superseded by this
new source-bound preparation/review; old packet is preserved.

Reporting follow-up: sample_complete now requires the frozen minimum trade count
for every candidate; empty/missing/invalid counts fail the sample check. 99 targeted
tests PASS plus lint; no strategy/gate edits or market run. Source now d2864272…;
correction evidence — local evidence `data/sample_completeness_fix_20261025_01/RESULTS.md`.
Pullback old preparation is historical for source8df9a071… and cannot activate on
this changed source. New preparation — local evidence `data/trend_pullback_prereg_20261025_02/RESULTS.md`
has one fold/three cells and unchanged scope/budget, protocol081fce81…;
Preparation remains historically PREPARED_NOT_AUTHORIZED; separate run authority
and accepted review are now recorded in the new execution packet. Prior admissions/AC are not
relabeled as fresh tests; old packets and their economic verdicts remain intact.

Current coordination: [E2E delivery playbook](operations/MULTI_STRATEGY_RESEARCH_PLAN.md).
One tracker, stage-specific evidence and bounded loops; no until-profitable search.
NOW-02 accepted on d2864272…; NOW-03 operator authority recorded; NOW-04 measured
and separately reviewed VERIFIED_INCOMPLETE_SAMPLE_ABSTAIN
(review — local evidence `data/trend_pullback_validation_run_20261025_01/POST_RUN_REVIEW.md`).
H-PULLBACK-TEMPORAL-01 is now also closed RESEARCH_ABSTAIN / NOT_QUALIFIED on
the later exposed window. No automatic run, retune, next catalog strategy, or
qualification. A new hypothesis needs a different mechanism and a new operator
decision.
The historical BLOCKED_REVIEW below refers to preparation _01 and source8df9a071….
Independent review — local evidence `data/trend_pullback_independent_review_20261025_01/REVIEW.md`.
Older research-plan snapshots are archived; current RSI/MA/BNB verdicts below
are unchanged.

MA crossover defaults20/50 BTC1h authorized run completed3/3 cells, coverage PASS,
64closed trades/cost, one attempt each, zero retry/cache/timeout. Sharpe
-0.162/-1.544/-1.995; marked return -0.481/-4.002/-5.149%. Selection ABSTAIN,
NOT_QUALIFIED_VALIDATION_ONLY. No promotion or automatic follow-up run.
Baseline results, inventory accounting and benchmark — local evidence `data/ma_simple_baseline_validation_run_20261024_01/RESULTS.md`.

1x closed net-363.76 plus open PnL315.51 gives marked delta-48.24; not a closed
profit. Passive25%context on same window+3.436%, not native/risk-matched or alpha.
Source8df9a071… stable; no src/scripts/gate edits. Original ledgers keep historical
UNKNOWN fields; new read-only sidecar reconciles3/3 attempts to exact registry
contexts/metrics without rewriting receipts. Study provenance_eligible=false
because worktree_dirty=true; this remains a release/qualification limitation,
not a cause of negative Sharpe. Integrator verification, no independent review.
Registry reconciliation — local evidence `data/ma_simple_baseline_validation_run_20261024_01/postrun_registry_review_20261025_01/RESULTS.md`.

RSI fixed-policy packet remains ABSTAIN (39trades/cost). BNB state-edge packet
remains INCOMPLETE/HOLD; two unknown cells not filled. No candidate qualified,
mainnet NO-GO. MA window822,5142) differs from RSI[920,5240), so no paired ranking.
[RSI results — local evidence `data/rsi_fixed_validation_run_20261024_01/RESULTS.md`.

H-BRANCH-01 closed:16reports compared,6synthetic cases PASS,12,960action records
matched; mixed-branch trades not shown to be the cause of loss. Strategy unchanged.
Branch attribution — local evidence `data/rsi_branch_attribution_20261024_01/RESULTS.md`.
Comparison — local evidence `data/economic_comparison_20261024_01/RESULTS.md`,
synthetic proof — local evidence `data/rsi_branch_synthetic_20261024_01/RESULTS.md`, and
historical baseline proposal/reference — local evidence `data/measurement_reference_20261024_01/RESULTS.md`
are supporting history, not current pending authorization or fresh run authority.

H-PULLBACK-01 preparation complete, PREPARED_NOT_AUTHORIZED:existing default
trend_pullback,SMA20/80,ADX25,volume1.5; admission4/4syntheticfunctions PASS.
Publicpreflight1fold/3cells,simulation0,5232),measurement[912,5232),warmup96.
Envelopeproposed600s/cell/2100stotal/1worker/2048MiBsampledRSS/0retry/failfast.
Source8df9a071…stable; no newstrategy/engineevaluation, no inherited approval.
Old discovery10trades/Sharpe-3.018 retained; no resetting trial burden. Data2023
alreadyexposed,diagnostic only; dirtysource cannot be promoted by positive result.
[Exact plan and run constraints — local evidence `data/trend_pullback_prereg_20261025_01/RESULTS.md`.
Admission — local evidence `data/trend_pullback_admission_20261025_01/RESULTS.md`.
Independent review on 2026-10-25 blocked activation: strategy bytes and the
frozen protocol still match, current src/scripts fingerprint does not.
Review — local evidence `data/trend_pullback_independent_review_20261025_01/REVIEW.md`.

### Historical preparation — 2026-10-24 (superseded by execution above)

New candidate data revision reuses the already-frozen20261008 pre-holdout input,
not current mixed raw files. Fresh byte hash45ba1b… matches historical receipt;
25198 contiguous hourly rows,0 reserved timestamps. New byte-identical copy has
fresh OHLCV quality PASS; previously exposed research, not untouched/OOS. Raw
parent provenance remains incomplete; old raw manifest mismatch not repaired.
6160-row deterministic prefix and one-fold/3-cost proposal prepared; no strategy
run. Proposal600s/cell/2100s total is unapproved, stop/probe enforcement still
needs review. No official protocol/run authority or independent data review.
Data layout, revision and proposal — local evidence `data/btc1h_preholdout_layout_20261024_01/RESULTS.md`.

### Historical update — 2026-10-23 (superseded where noted)

Latest: fixed-param launcher accepted by separate reviewer for synthetic scope
on source4930c28c…. Data admission is DATA_IDENTITY_BLOCKED: timestamp-only probe
found physical BTC1h SHA392712…/907723B differs from declared aedecd…/892340B.
This proves file-byte mismatch, not necessarily changed prices.27183 pre-holdout
rows have1 non-hourly step; extended/full53456 have15. No OHLCV decoded, but
whole-file bytes were hashed and holdout timestamps read. Manifest/raw files
unchanged; no market run. Need explicit data-revision/read scope decision.
Review and metadata preflight — local evidence `data/fixed_params_review_20261023_01/RESULTS.md`.

Fixed-policy launcher input implemented: --fixed-params scalar JSON resolves
defaults into frozen singleton params; existing catalog grids are unchanged.
102 distinct selected synthetic/regression cases verified plus lint; one native
integration preserved full packet, coverage complete but NOT_QUALIFIED and sample
incomplete. Source4930c28c… stable. Parameter-input blocker below is historical;
new launcher patch still needs independent review and data/windows/budget remain
unapproved. No market run, strategy/gate change or inherited run authority.
Fixed-param verification — local evidence `data/fixed_params_validation_20261023_01/RESULTS.md`.

RSI neutral-contract review: separate reviewer accepted SYNTHETIC_SCOPE on
source5a2a71ec…, rehashed482 source files and evidence with0 mismatch; inherited
context/no fresh tests. Review — local evidence `data/rsi_admission_review_20261023_01/REVIEW.md`.
Next economic admission is BLOCKED_PARAMETER_BINDING: shared CLI pins the old
15/35/65/20/50 configuration rather than proposed20/30/70/20/80 defaults.
Read-only preflight and smallest next slice — local evidence `data/rsi_admission_review_20261023_01/NEXT_MEASUREMENT.md`.
No source edit, market-data decode, test execution or market-run authorization.

Latest RSI/ADX update: operator-approved neutral edge contract is implemented:
flat RSI50/NO_TRADE, only-loss0, only-gain100; mixed rolling formula unchanged.
48/48 integrated synthetic cases and lint PASS on stable source5a2a71ec…;
source delta this turn is one strategy file. Prior flat-BUY characterization
below is historical, not current behavior. No independent review or market run.
Neutral RSI contract and receipts — local evidence `data/rsi_neutral_contract_20261023_01/RESULTS.md`.

RSI/ADX test-only follow-up:11/11 synthetic cases PASS, lint PASS. Native action
timing and real closed-trade entry fills match the two-bar chain on one fixture.
Flat RSI0 emits BUY continuously; this requires an explicit semantics decision
before economic admission. No production source change or market authorization.
Native contract and limitations — local evidence `data/rsi_native_contract_20261023_01/RESULTS.md`.

RSI/ADX proposed-subject admission follow-up: synthetic tests reproduced negative
directional movement on inside bars and non-finite zero-range indicators. Minimal
indicator correction is verified by39/39 final cases plus lint; no parameter,
native policy, gate or market-run change. This is new source, not inherited
economics or AC evidence. Adapter parity is not engine/fill parity; flat RSI
semantics and admission review remain open. No independent review of this patch.
Synthetic correction and all attempts — local evidence `data/rsi_dm_synthetic_fix_20261023_01/RESULTS.md`.

The authorized native state-edge market packet
`data/bnb_di_adx_stateedge_validation_run_20261023_01/` remains INCOMPLETE:
13/15 completed, 2 fold-004 timeouts at 300s, original controller WORKER_ERROR.
It has not been rewritten or rerun. Completed cells have 34–38 trades but
mixed economics; no stable edge or qualification is established.

The orchestration correction now preserves failure reasons, validates COMPLETED
reports even in mixed packets, avoids duplicate timing emission, and propagates
consistent INCOMPLETE to supervisor receipts. Regression: 95 tests PASS and lint
PASS on source `a50046b389bffd47fb5f19295ee34ccb139f7ba2c55175ae0b9e4eef28778b96`.
AC01–AC15 ALL PASS on the same stable source. Full evidence and receipts are in
the separate engineering evidence packet:
fix and verification — local evidence `data/bnb_validation_incomplete_fix_20261023_01/RESULTS.md`.
Old source approvals/manifests are historical, not authority for this changed
source. No strategy/gate/window change, timeout increase, retry, market profile
or market rerun. Further market work needs a new explicit decision; default is
to retain RESEARCH_HOLD and analyze the existing partial evidence.

Bounded validation refactor is now accepted on source
`fe166b1923280620e942824e9fec5353844cb75dc9039bf030760fc1a1076b38`:
final engineering handoff — local evidence `data/validation_refactor_final_20261023_01/RESULTS.md`.
This does not rewrite or reattribute the historical market packet.

Report-only economic review of all 13 completed cells confirms sufficient
observed inner trade counts but weak temporal robustness: F2 gross is positive
and below recorded cost; F3 gross is already negative; F0 closed net turns
negative in every cost scenario after subtracting its best trade. These are
post-hoc validation diagnostics, not OOS gates or a formal strategy rejection.
Recommendation: no rerun merely to fill two missing cells; candidate remains
HOLD / NOT_QUALIFIED, with archive as an operator decision. No raw OHLCV read,
market execution, strategy change or new run authority in this review.
report-only economic evidence — local evidence `data/bnb_stateedge_economic_review_20261023_01/RESULTS.md`.

Follow-up inventory reviewed 22 raw reports across selected screening/discovery
and RSI/ADX packets. Source declares18 broad bindings plus the scoped state-edge
candidate; older17-count snapshots are historical. No next-market candidate is
admitted by this bounded review. Three RSI/ADX reports currently exist in the
separate20261020_d packet and corroborate25/22/22 trades, but planning/data-scope
alignment and provenance remain unresolved; this does not restore the deleted
packet's original chain or establish qualification. See
inventory and conditional shortlist — local evidence `data/strategy_evidence_inventory_20261023_01/RESULTS.md`.
No new market run, source change, archive decision or approval was performed.

RSI/ADX metadata lineage audit is now CLOSED as LEGACY_DESCRIPTIVE_ONLY, not
qualification. All3 native `data_manifest_id=9471…` values reproduce exactly
from metadata; they are not direct parquet-file SHA256 values. Earlier
different-file-hash wording comparing9471… with frozen-file SHA aedec… is not a
valid file-identity conclusion; historical artifacts are preserved with this
erratum. Current source supports a legacy-planning vs canonical-native loader
split consistent with31783/58897rows, but original planning-frame binding is not
fully recoverable. Packet D protocol786a…/timeout900 differs from old recovered
protocol937a…/timeout300 despite identical scores. Keep descriptive evidence,
do not reseal/admit it or reopen an endless audit loop. No new run authority.
lineage closure and typed-hash proof — local evidence `data/rsi_lineage_closure_20261023_01/RESULTS.md`.

## Historical — 2026-10-22, rev24 / CHANGES_REQUIRED (round 3) / NO_LIVE

**Three independent review rounds have now run and every one of them returned
`CHANGES_REQUIRED`.** Round 3 (two fresh-context, read-only reviewers on rev23)
confirmed that no measurement, identity, gate or fail-closed behaviour is wrong —
both reviewers checked the seventeen native quantities themselves, and the second
one went further and showed the two re-execution outputs are **byte-identical** to
the stdout recorded in the earlier receipts — and that every remaining defect is
in the text/provenance layer. All ten findings are fixed in rev24; **acceptance
awaits a round-4 re-check**, and no market run is authorised.

**Identities (rev24).** A `d82bd081dfe999c3568268fd1b2a220db08ec4bdc87f24dc759582eb219a0825`
(source, committed at `7d5f3c9`, `git diff 7d5f3c9 HEAD -- src scripts` is empty,
so the worktree is clean and A is reproducible); B
`1e96538d2796e0975fd1e90a9ddf03be609abc40a69c4ebd09aa5c21020504b6`; C = the
`manifest.json` of the **five** sealed packets (evidence, preregistration, native
review, and the operator's two review packets), **220 sealed entries**, all
re-hashed from disk. The manifests' `code_commit` and `identity_A_dirty_diff_sha256`
are now **read at seal time** (they were copy-forwarded before — round-3 finding
N3), and `rev21` + `rev22` are archived under `revisions/`, so the archive policy
the preregistration declares is now true.

**What round 3 changed.** N1 the approval verifier compared 9 of the 14 sealed
assertion files and said "every" — it now iterates the sealed record, with the
old nine as a floor. N1b the fault-injection test still called a live-pid fixture
INTERRUPTED — its framing is now true. N2 the preregistration defined identity C
three different ways — it now defines it once. N5 the scope-token map was compared
with itself — it is now compared with the candidate the named preregistration
packet actually registers (fail closed, with a regression test). N6 the native
contract check was a membership test, so a 1x cell could have run a 2x body — it
is now an equality test against the approved body of that cell's own cost (with
a regression test). N7/N8 three stale hashes in the working set and a receipt
that still said "F9 OPEN" — refreshed. N9 the F9 output did not record the
source it ran under — the re-execution now carries a sidecar identity, and the
record names both runs. N10 the manifest could exclude a verdict file — the
exclusion is gone; everything the .gitignore does not exclude is sealed. D2 the
tracker carried a hash (`c92a9ff4…`) that existed in no artifact — replaced by
the manifest's own, with the correction visible. D3/D5/D6/D7/D8/D9 the tracker's
revision, counts, labels, commit and "the data/ tree is untracked" statement
were stale or wrong — corrected.

**Verification on this revision.** 211 tests PASS across the fourteen sealed
assertion files; AC01–AC15 ALL PASS (rc 0) re-captured into the packet on
identity A (`acceptance_run_rev24/`); F9 re-executed twice — 20.0 s each, 30 s
cap, no retry — with **all seventeen quantities reproduced exactly** and the two
runs **byte-identical**; the source fix is committed, so A is a clean,
reproducible content hash.

Remaining: the operator approves the data scope and separately authorises ONE
15-cell / 300 s-per-cell / 5400 s-total / 1-worker / 2048 MiB / 0-retry
validation-only run. Approval produces a new B and requires a preserved revision
plus binding verification; no market run is inferred from any engineering
verdict. No live, qualification, OOS or holdout claim.

## Historical — rev20 / independent review CHANGES_REQUIRED

**Update: independent fresh-context review of rev20 is complete and returned
CHANGES_REQUIRED.** Two HIGH findings: approved payload does not bind concrete
runtime risk/stop/sizing, and a noncanonical preregistration root can substitute
a copied approval for REAL_MARKET. See
[independent rev20 review](../data/bnb_di_adx_stateedge_review2_20261022_01/INDEPENDENT_REVIEW_REV20.md).
Existing test PASS and coherent manifests do not close these findings. No
approval or market execution is permitted on this revision. No source was
changed during the review.

Supersedes the rev19 status below. Source A is
`c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74`,
preregistration B is
`1338572d57628e9ba1c29b655856547d1c945f4695ee7cc9aef0545c8ce88145`.
Three manifests are now rev20. Seven mismatching pre-reseal entries and old
manifests were preserved, not erased. An exact rev19-source delta is unavailable
because no per-file source snapshot was sealed then; the current full source
map and diff versus HEAD are retained for subsequent reviews.

Verification on A: 30 fast regression tests + 31 control/plan tests + one real
launcher synthetic integration PASS; AC01–AC15 ALL PASS; targeted lint PASS.
F9 reruns reproduce the cited crossover/episode/per-cost funnel metrics.
Old acceptance evidence remains historical, explicitly distinguished from the
current hashed verification index in each packet. No market campaign was run.

[Current execution and freeze handoff](../data/bnb_di_adx_stateedge_review2_20261022_01/FINAL_HANDOFF.md)
and receipts are in the separate review2 packet. Current source was stable
before/after verification. Scope and run approval remain pending. A fresh
reviewer must review this revision: the current reviewer contributed an earlier
ledger correction and does not claim clean-context independence for it.

Remaining: independent final review, then explicit operator scope/run approval
on the final payload before ONE bounded validation-only run. No profitability,
qualification, OOS or live claim. Control remains FAILED/INCOMPLETE.

## Historical — 2026-10-20/21, rev19 / RESEARCH_HOLD / NO_LIVE

**Latest: rev21 — independent review #2 ALSO returned `CHANGES_REQUIRED` (2
findings: R20-1 and R20-2); those findings are fixed in code and the revision is
re-sealed; review #3, the F9 re-execution and the operator approval are still
PENDING — no market run.**
[Contract + synthetic](../data/bnb_di_adx_stateedge_20261020_01/RESULTS.md) ·
[the single preregistration](../data/bnb_di_adx_stateedge_prereg_20261020_01/PREREGISTRATION.md) ·
[review record #1](../data/bnb_di_adx_stateedge_20261020_01/INDEPENDENT_REVIEW_ROUND1.md) ·
[verification receipts](../data/bnb_di_adx_stateedge_20261020_01/VERIFICATION_RECEIPTS_20261021.md) ·
[test plan](../data/bnb_di_adx_stateedge_20261020_01/TEST_PLAN_FOR_APPROVAL.md).
**1. Two independent review rounds, both `CHANGES_REQUIRED`.** Round 1: two
reviewers with a fresh context, read-only, returned CHANGES_REQUIRED with 19
consolidated findings; the fixes were executed (supplementary envelope 420 s,
used 290.3 s: the 7-test control regression back to 29/29, 20/20 invariants
including a new "a non-scoped study is not blocked" regression, the launcher
plan-level geometry, AC01–AC15 re-captured **into the packet on the final
identity**, and the launcher integration end to end). Round 2, on the verified
revision, returned **CHANGES_REQUIRED again**. (Corrected 2026-10-22: the
count "12 new findings and 5 partials" in earlier versions of this line was
wrong — review #1 raised **19 findings (F1–F19)** and review #2 raised **2
findings (R20-1, R20-2)**; the list that follows is review #1's.) The most
serious were: the **payload still pinned a stale identity
A**, so the binding gate would have refused the registered run; the approval
verifier consulted a payload field that no longer exists; the "assertion files
unchanged" check was **vacuous** because no test file is covered by any
identity; the acceptance evidence in the packets was bound to a **different
source** than the frozen one; a redirected preregistration root could bind a
**self-approved payload**; the qualification path could bypass the binding; the
ledger's `extra` could override the accounting fields it promises to keep
UNKNOWN; the two scope-token maps were not bound to each other; the archive was
off by one; a stale rev10 verdict file carrying the literal string
`ACCEPTED_FOR_BOUNDED_VALIDATION` sat inside the live packet; and the receipts
headed a column "Approved" for a budget that was only a proposal.
**2. What is fixed in rev21.** The payload now pins the **current** identity A
and its recomputed B, so the gate's own identity check can pass; the redirected
root must now be **declared in the frozen protocol** (`preregistration_root_canonical`),
so a substituted packet is a recorded deviation rather than a silent swap; the
assertion files are hash-sealed in `evidence/assertion_files.json` (14 files,
covered by manifest C, because identity A does not cover `tests/`); the
acceptance evidence is re-sealed on the frozen identity and the receipt that
produced it is in the packet; the ledger's accounting fields can no longer be
fabricated; the two token maps share one source of truth; the stale verdict
files are archived; and the receipts, the procedure, the native RESULTS.md, the
§0 identity table and the archive disclosure are corrected.
**3. Not met, and why.** **F9 was open and it was needed before a run — it was
closed on 2026-10-21 by a budgeted re-execution** (30 s cap, 20.0 s spend, no
retry): the three native analysis scripts were re-run on the hash-verified
frozen pre-holdout input and **all seventeen quantities quoted in the native
`RESULTS.md` were reproduced exactly** (0 mismatches). Record:
`../data/bnb_native_review_20261020_01/F9_REEXECUTION_DIFF.json`. The reason it
was needed: the
preregistration's hypothesis cites the control's funnel (`7/6/8/8/6` gate
actions → 30 fills → 28 closed trades) and §12 says the control review
"derives from" the native `RESULTS.md`, whose measurements were produced under
source `1b165019…` while the manifest declares a later identity. The static
argument (the scripts import only the unchanged `enhanced_ma`) is plausible but
is not a re-execution. The **qualification path** for this candidate is also not
functional: on that path the selection attribution rebuild does not receive the
token — it is disclosed in the payload as out of scope for this validation
round. The DSR caveat and the four tokenless out-of-scope paths are **not**
blocking this validation round, because neither is executed nor used to claim
qualification. The revision archive remains **partial**: rev8, rev10, rev12, rev13
and rev15 itself are unrecoverable, and the current manifest cannot repair that.
**4. Identities and receipts.** A source
`b3aa44f026939863018a15de44a57c5b81e1bde9176b2c7483c7d3b7a65db427` (dirty
`de09328a5d10994ad5d2e0a00a2efa2cac1e8c52befcede5c1cc46a336851209`, see the
manifests. *Corrected 2026-10-22: this line previously carried the hash
`c92a9ff4…`, which existed in no manifest and in no artifact — it was an
erroneous value and has been replaced by the manifest's own.)*, B preregistration
`fe9cf95ee72b27036a52616b79a6f2e8f1edfb56481a4f0d97b547a713aa4373`, C evidence
manifest **rev21** at three packets, 0 mismatch, with the archived manifests
themselves sealed by C. Approval remains `PENDING_OPERATOR_APPROVAL`; I did not
write one.
**5. Budget used and deviation.** 640 s (used 621.3 s; tranche 2 over its 400 s
split by 81.6 s, my own defects plus one cell timeout) and a supplementary 420 s
(used 290.3 s). No market run, no market data, no holdout, no OOS, no
sensitivity, no live/paper, no broker, no download.
**6. Smallest next decision, and the permission needed.** Two things, in order:
(a) a **third independent review** of the rev21 revision (the fixes of round 2
are not yet reviewed), and (b) a **separate ~30 s budget for the F9
re-execution** of the three native analysis scripts on the frozen pre-holdout
input — fixed input hash, no raw/holdout read, new output, the old report kept
and the diff recorded, time-boxed, no retry — because the hypothesis and the
scope rest on those numbers. Only after both may the operator approve the
scope and one registered run (15 cells, 300 s/cell, 5400 s total, one worker,
2048 MiB, zero retries, no OOS/sensitivity/live).

**Latest: bottleneck review rev2 (corrected); candidate closed at this stage;
operator policy choice required.**
[Bottleneck review](../data/bnb_native_review_20261020_01/RESULTS.md) — **rev2
supersedes rev1 of the same day after reviewer-found defects in the analysis**.
Read-only analysis of the hash-bound pre-holdout input, restricted to the already
**read+decoded scope [0,6795) của loader đóng băng** (xem mục "Latest" ở trên: cả [5600,6795) cũng bị decode, chỉ không evaluate; 0 bar holdout trong file); holdout_rows_read=0; no market run; no strategy change.
Method validated against the **full per-bar action vector** of all 14 completed
cells: **15120/15120 bar-level assertions, 0 mismatches**, alignment derived by
scan (unique) and locked by a synthetic regression (3 passed, no market data).
Alignment: `action at execution_bar j == full_frame_signal[j-2]` (canonical
signal decides on the window ending strictly before the bar; the simulator
executes one bar later). rev1 had derived j-1 — its 10/10 COUNT match was a
false positive because the counts are identical under both alignments.
**Corrected decomposition (4-way partition, asserted to sum to raw):** on the
3240-bar union, up-crossovers 30 raw -> 21 pass / 5 fail-ADX / 1 fail-DI / 3
fail-both; down-crossovers 29 raw -> 20 pass / 5 fail-ADX / 2 fail-DI / 2
fail-both. The filter is a MINORITY and SYMMETRIC effect (~30% of crossovers in
both directions), not what makes the cells fail. Per-cell gate 30 is still
failed by every measured cell for SAMPLE-SIZE reasons; the ADX/DI filter is a
minority contributor.
**Withdrawn from rev1:** the "arithmetically incapable / impossible" claim (it
rested on a linear extrapolation, which cannot bound every feasible geometry);
the "70% of down-crossovers fail ADX / trend exhaustion" claim (an artefact of
an inverted SELL condition — down-crossovers pass the SELL gate at the same rate
as up-crossovers pass the BUY gate).
**Refuted:** "relax ADX/DI to get more trades"; "authority/fills drop trades";
"a campaign is still running"; "the candidate's source changed" (src/** intact;
the only whole-tree drift is scripts/run_wfo_validation.py, a research script).
**Still UNKNOWN:** the trade count of any longer, unrun window; profitability
of any state-entry/re-entry policy — only event availability was measured
(35-39 trend episodes per 1080-bar window vs 7-12 raw up-crossovers for this
candidate; measured, NOT projected).
Overlap duplication made explicit: 68 occurrences -> 41 unique signal bars.
Verification: AC01-AC15 rc0 ALL PASS, source identity identical before/after
(tree_fingerprint 1b165019..., commit 4078892d...). No gate file touched.
Terminal: RESEARCH_HOLD / NOT_QUALIFIED; operator must choose Path A (more
eligible observations - needs a data decision), Path B (separate contracted
state-entry candidate - new identity, cannot inherit this evidence) or Path C
(stop). Current source fingerprint for any NEW packet is 1b165019..., not the
stale 6f5bd4f4... quoted by the 2026-10-19/20 packets.

**Latest: sample feasibility audit complete; no new market run.**
Deduplicated events and geometry bounds — local evidence `data/native_sample_feasibility_20261020_01/RESULTS.md`.
2x/stress5overlapping windows each contain28reported occurrences but only17unique
closed events;1x4completed windows24occurrences→14unique. Never pool costs or sum
overlapping windows as independent trades. Per-cell gate30is unchanged.
With6795bars,train12/test3/step3/purge100/embargo100,keeping5folds permits at most
8validation months;24months does not fit even1fold. Unrun-window trade counts
remain UNKNOWN. Current crossover candidate stays RESEARCH_HOLD. Explicit operator
choice now needed:preserve crossover and secure adequate eligible observations,
or authorize a separately contracted state-entry/re-entry candidate (synthetic
first). No silent policy change,threshold relaxation or market retry.

**Latest: saved-evidence signal→authority→fill attribution completed, no market run.**
Audit and next policy decision — local evidence `data/native_signal_fill_review_20261020_01/RESULTS.md`.
All14completed sources reconcile hashes/contracts/bar counts and filled-entry /
carry-in/out / closed-trade identity. Gate BUY6–8 perwindow; all BUYs reaching
runtime show submitted+filled snapshots. Skips are already_long, not observed
authority blocks. Source and4synthetic cases confirm event-crossing+ADX/DI at the
crossing,not continuous trend-state entry or deferred ADX entry. Raw forecast
rejection rates remain UNKNOWN from saved reports. Increasing sizing cannot by
itself resolve sparse triggers. No strategy/gate change or market retry. Decide
intended event-vs-state semantics and sample feasibility before a new market budget;
do not silently introduce re-entry to meet the gate. Existing failed run and
holdout-access incident below remain unchanged.

**Latest actual run: FAILED / INCOMPLETE / NO_GO (no automatic rerun).**
Raw outcomes,holdout incident and engineering repair — local evidence `data/native_validation_activation_20261019_01/RESULTS.md`.
One15cell validation-only attempt finished18m39s,controllerrc1:14completed,
1timeout90s. Dispatcher ignored frozenresearchloader and omitted frozenfolds,
generated7folds vsplanned5; guard rejected an extra cell. No complete coverage or
official selection decision.14valid cell reports are bound to approved pre-cut
input,each4–7trades<30. Descriptive results do not authorize strategy/paper/live.

Important access incident:dispatcher/provenance decoded raw7946rows through
2026-08-17,including reserved holdout; do NOT claim holdout untouched. No extra
strategy/outer/holdout cell ran. Measured historical+current union1800,5600)
is3800unique bars; full-prefix native replay[0,5600) is a different exposure scope.
Failed/preflight packets preserved. Post-stop fix routes all3frozen WFO reads
through researchloader,compares schedule beforecells and passes frozenfolds.
115targeted tests/AC01–15PASS stable newsource6f5bd4f4…f35f6ca,ruffPASS.
Old preflight freeze is now stale; do not resume it. Next:review existing signal/
fill frequency and concentration before approving any new market scope/budget.
Independent reviewer ACCEPTED dispatcher fix on115completed tests and final AC
identity. Existing-funnel audit shows6–8BUY actions per1080bar window and98.5–99.1%
HOLD at simulator-gate stage,not raw-forecast attribution. Low measured trade count
is not resolved by simply raising allocation. Supplementary legacy nested-WFO
suite was interrupted on discovery of unisolated raw-market readers;NOT PASS,
receipt preserved. Isolate that suite before any broad rerun; no market retry.

## History — preflight and earlier engineering closure

**Latest: singleton ADX25 preflight packet ACCEPTED independently (NO EXECUTION).**
[Adapter,quality,freeze and handoff — local evidence `data/native_singleton_preflight_20261019_01/RESULTS.md`.
Packet `data/bnb_native_adx25_validation_preflight_20261019_01` freezes5folds,
15inner cells,3native contracts;1worker/90s-cell/1800s-total/2048MiB/0retries.
6795pre-holdout OHLCV rows pass quality checks with0gaps/nulls/invalid rows.
7adapter+63protocol tests PASS,AC01–15PASS stable finalsource97726b50…725c1ecb,
lintPASS. Reviewer recomputed protocol hash and verified exact schedule/contracts,
no reports or execution-start artifacts. Reused descriptive validation only,
not untouched OOS. P2 sensitivity still blocks FULL qualification. Next requires
explicit execution scope/budget approval and reviewed supervised activation of
this exact freeze; generic CLI default-grid/hidden-worker launch is not approved.

**P1 follow-up: frozen-map/raw/artifact comparison integrated.**
Independent reviewer subsequently ACCEPTED P1 after checking source hashes,
core145test receipts and final AC verification. No full qualification approval.
Fix and refreshed evidence — local evidence `data/native_binding_fix_20261019_01/RESULTS.md`.
Checks now run on qualification cache publication/read and full inner/outer or
validation-only final coverage, without changing planned cell IDs.156targeted
tests PASS,including6missing/self-consistent-substituted contract regressions;
AC01–15PASS identity stable,ruff/diff-check PASS. Earlier9fixture failures retained
and fixed by adding native contract to synthetic positive reports, not bypassing
guards. P2 sensitivity preregistration remains OPEN; full qualification blocked.
Exact singleton ADX25 adapter, data preflight and reviewed execution freeze/budget
remain before the proposed validation-only run. No market campaign launched.

**History: initial independent review REQUEST_CHANGES / execution blocked.**
Review, metadata eligibility and bounded next-packet draft — local evidence `data/native_evaluation_review_20261019_01/PLAN.md`.
Reviewer confirmed144tests/AC receipts and source hashes, but found frozen native
contract equality missing on cache/raw/final coverage paths; sensitivity expected
contracts also not preregistered. Prior LOCAL_VERIFIED is not independent approval.
Metadata-only audit confirms6795ordered4h pre-holdout timestamps and file identity;
dataset is reused descriptive validation, NOT attested untouched OOS. Proposed
next stage:fixedADX25,5validation folds×3costs=15cells,1worker,90s/cell,
1800s total,2048MiB sampled RSS,zero retries; DRAFT, not approved or executed.
Fix/review raw/cache binding and exact singleton entrypoint before launch.

Operator selected native authority/protective stops;25% is requested entry
allocation, not guaranteed approved sizing.18.75% is an observation, not a new
fixed parameter. Native cooldown24h belongs to circuit breaker, not every SELL.
Research V2 results remain screening only; native policy needs fresh evidence.
The target is no longer economic equality with V2.

Native engineering packet and next steps — local evidence `data/native_policy_adoption_20261019_01/RESULTS.md`:
pure declaration shares constructor config, new qualification protocol freezes
per-cell contracts, worker forwards expected ID to pre-first-bar verification.
Legacy missing maps/unknown cells/conflicting IDs rejected. No source/data reuse
relaxation.35native+54protocol+24validation+31cold-recovery tests PASS
(144 disjoint targeted tests, not the repository-wide full suite).
AC01–15PASS with stable source identity;ruff and scoped diff-check PASS.
Initial combined test timeouts retained, not counted as PASS.
Not all diagnostic CLIs require expected ID; sensitivity is current-config-bound,
not claimed separately preregistered per-neighbor. No market run or live approval.

Next: independent review, data eligibility/access-history resolution, then a fresh
native preregistration with exact costs/folds/gates/budget before market execution.

## History — previous local closure (before native operator decision)

**Latest local closure (supersedes bounded missing-screening finding):**
full audit and corrected verdict — local evidence `data/evaluation_closure_20261019_02/RESULTS.md`.
6108report/summary files indexed,114RSI/ADX matches;12screening cells corroborate
the operator's Sharpe/trade table. Three1x INNER_VALIDATION Sharpe values recovered
from readonly registry. Operator confirms original WFO packet deleted; trade counts
and complete acceptance packet cannot be certified from registry alone.52records
including failed attempts retained; no history reset. Report-calendar/holdout
identity concern remains unresolved; different source-file SHA does not prove
untouched overlapping prices. No raw market files opened or new campaign.
Parallel WFO wrapper expected-contract propagation gap fixed;128testsPASS,
AC01–15PASS identity stable,ruffPASS. Local engineering/audit loop closed with
RESEARCH_HOLD,not WFO/market qualification or independent review complete.

Evaluation audit + minimum execution-contract integration completed locally:
closure/handoff — local evidence `data/evaluation_closure_20261019_01/RESULTS.md`.
RSI/ADX claims remain UNVERIFIED: bounded audit finds6 retained reports but no
threshold15 raw report/complete3fold packet; current runner is validation-only1x,
minimum15(not proof of historical settings).6report calendars overlap reserved
holdout dates; exact frozen dataset identity/access history requires review.
No raw holdout prices read,manifest unchanged. Cannot claim untouched holdout.
Native active_config/cell evidence now carry contract identity; optional expected
ID passes inline/MP and rejects mismatch before first engine update. Not mandatory
all-CLI qualification guard or V2/native economic parity.85initial testsPASS,
32final affected testsPASS(overlap),AC01–15PASS identity stable,ruffPASS.
No trading behavior/gate changes,market run or qualification approval. Older
prototype-only/pending-integration entries below describe historical states.

Execution-contract scope advanced to isolated prototype:
contract declarations and integration boundary — local evidence `data/bnb_execution_contract_20261019_01/RESULTS.md`.
19tests PASS; semantic identity separated from evidence binding; current research
and native observed declarations differ in8 fields including sizing/stop/cooldown/
cost. Same JSON identity does NOT prove economic parity. NOT wired into production
preflight/reporting; new user WFO PID503284 remains active, no shared source edit.
Candidate unchanged,qualification NO_GO,no new market run or monitor resumed.

**Accounting caller integration CLOSED locally** after actual research processes
stopped. Final handoff — local evidence `data/bnb_accounting_integration_20261019_01/RESULTS.md`.
Native report now explicitly declares marked-equity basis; exporter default cash
semantics unchanged.57tests PASS including formerly red caller regression and
carried/flat cases.2×420bar real-engine synthetic rerun without wrapper confirms
both open-position reports reconcile; economics unchanged, signal parity840/840.
Economic parity remains NOT_EQUIVALENT (sizing/stop/cooldown/cost),qualification
NO_GO. No gate changes/market run. Earlier pending-source entries below are
historical and superseded by this integration. Monitor scope complete; next is
explicit shared execution-contract/identity work, not more parameter optimization.

Real-engine stop counterexample now measured:
stop fixture — local evidence `data/bnb_native_stop_probe_20261019_01/RESULTS.md`.
37.56s,2×420synthetic bars,840signal parity; native protective exits bar180/300
at bar_low, research exits bar181/301 at next open. Economic policy NOT equivalent.
No raw market data/gate/source changes. Earlier weak low_shock did not trigger
stops; this new severe-wick fixture does, not a claim of realistic intrabar fills.
Shared accounting fix still pending concurrent user inline/MP run_cell process;
monitor must inspect actual new processes rather than only old WFO PID492040.

Autonomous real-engine synthetic follow-up:
results/handoff — local evidence `data/bnb_native_parity_accounting_probe_20261019_01/RESULTS.md`.
840/840 canonical/runtime signal bars match; native first target18.75% vs
research25%, so economic parity FAIL. Found native report cash-vs-marked basis
caller defect: original run fails terminal open accounting, isolated process-local
fix reconciles both reports. Shared source NOT fixed yet because another WFO
validation process is active. Heartbeat resumes minimal integration after stop;
no stopping user process, no new market campaign, no operating paper/live.

Candidate ADX25/cooldown24h retained per operator instruction. Synthetic parity
findings — local evidence `data/bnb_policy_parity_20261019_01/RESULTS.md`:29 tests PASS,
8 scoped cases; verdict PARITY_NOT_ESTABLISHED, not qualification approval.
Native4h real-loop/spy-engine trace differs in stop sampling; native risk API
does not arm cooldown on ordinary record_trade unlike research after-any-SELL.
Real fill-ledger/cost/runtime parity remains untested. No market data/campaign,
broker/promotion setup, production source/gate changes. Next bounded scope is
explicit simulation policy contract/seam and full economic fixtures, not retuning.

Qualification readiness: **NO_GO new qualification run**.
[Bounded loop/protocol](operations/BNB_QUALIFICATION_PROTOCOL.md),
metadata and synthetic evidence — local evidence `data/bnb_qualification_readiness_20261019_01/inventory.json`.
Known measured exposure minimum3360/6795 bars; remaining3435 are NOT attested
untouched. Additional user multiwindow run used a different policy (1bar cooldown)
and synthetic fixture reproduced current-low → same-open stop lookahead; its
results cannot qualify the24h candidate, but still count toward research exposure.
V2 replay/native qualification execution parity remains unproved. Holdout docs
corrected to authoritative16:00UTC boundaries; manifest unchanged, no prices read.
Next: synthetic policy parity contract, not new market campaign or parameter search.

Fixed-fill cost follow-up(no new market run):
diagnostic — local evidence `data/bnb_cost_diagnostic_20261019_01/README.md`,4tests PASS.
Incremental fee+spread+impact doubling at frozen fills yields ADX16.1092%/3.1434%
versusbase16.8462%/3.5376%;NOT stress replay,NO stressedDD/Sharpe or sizing/fill
path recalculation. Do not label cost-stress qualification passed. Sourcepacket
unchanged. Next recommendation: freeze one research candidate and prepare honest
qualification protocol/data-exposure inventory,budget approval before new runs;
no further cooldown optimization/framework,paper/live or automatic campaign.

Latest cooldown ablation completed after user approval/independentAIpre-review:
results — local evidence `data/bnb_cooldown_ablation_20261019_01/RESULTS.md`,
independentAIpost-review — local evidence `data/bnb_cooldown_ablation_20261019_01/INDEPENDENT_AI_REVIEW.md`.
8cells×1/0retry,4baseline24h raw exactmatch before0h;elapsed2.65s.
w1MA24−3.5780%/DD5.8737%→MA0+13.3477%/DD11.0444%;ADXunchanged+16.8462%/DD8.3089%.
w1D24+20.4243pp,D0+3.4985pp,I+16.9257pp(82.8707%observedreturn-gap amplification,
notalphashare).w2D24=D0+1.0205pp,I0;allw2rawpathsunchanged.
ADX0/24raw/orders/decisionsidenticalbothwindows;filtergap persists without24h
in these seenwindows,butstrongw1interaction/concentration andw2opencarry remain.
37testsPASS,audit8×1081marksPASS,independentAIreviewPASSnoblocker;source/58historical
files unchanged.No productioncooldown/gate changes,NO_GOqualification/paper/live,
no further campaign authorized.Earlierproposal-only entries belowarehistory.

Cooldown mechanism ablation preparation only:
proposal — local evidence `data/bnb_cooldown_ablation_proposal_20261019_01/README.md`,hash3d725f48… .
MA/ADX×cooldown24h/0h×two seen windows=8cells,baseline24h reruns first and must
exact-match pinned raw. Only cooldown parameter changed;28fixture/AST tests+lint
PASS,signals1080/arm/window checked. Proposed1worker,0retry,120s/cell,1200s total.
No market/approval/claim; independent review/new operator budget approval required.
Policy production unchanged;diagnostic cannot auto-select winner/promote live.

Latest window2 run completed after user approval and independent AI pre-review:
results — local evidence `data/bnb_window2_ablation_20261019_01/RESULTS.md`.
4arms×1,0retry,elapsed5.70s within600s budget.14fixture/delta tests PASS,
audit4×1081marks/source-bound hashes PASS. ADX+3.5376%/DD2.6135%;
MA+2.5171%/DD4.5844%;BH+2.5304%/DD6.7321%;cash0%.
ADX−MA+1.0205ppreturn,−1.9709ppDD => frozen DESCRIPTIVE_FAVORABLE,
directional replication=true on already-seen validation. NOT OOS/qualification.
ADXclosednet−73.03/openmarked+426.78,total+353.76USDT;MAclosed−170.85/open+422.56.
Source differs window1; scoped core check finds only naive-UTC conditional in
tournament on explicitUTC input,engine/strategy/provider unchanged. No blanket
source equivalence; receipts retained. Independent AI post-result review tracked
separately; human signoff/live remain unapproved. No further run authorized.
Earlier window2-proposed/not-executed entries below are history.

Independent AI post-result review — local evidence `data/bnb_window2_ablation_20261019_01/INDEPENDENT_AI_REVIEW.md`
PASS scoped execution/accounting,no blocking findings; reviewer independently
reconciled fixed-decimal ledger and authored Parquet check(root-assisted),
max4×1081mark error2.402954e−12USDT. Human signoff/alpha qualification remain
pending/not earned. Approved window2 loop closed; next market study needs new
preregistration/budget/approval, not automatic tuning or paper/live promotion.

Window2 preparation only:
proposal — local evidence `data/bnb_window2_proposal_20261019_01/README.md`, hash83dda4c8… .
Existing BNB4h4520,5600),UTC2025-01-23T08→2025-07-22T08,1080bars;
ADX signal/report parity1080/1080,commonwarmup100. MA7BUY/6SELL vs ADX6BUY/4SELL
(signals, not fills).10delta/safety tests+lint/help PASS. Fixed policy, fresh flat
10000cash per segment, new window-specific approval scope. Budget proposed4arms×1,
1worker,0retry,120s/arm,600s total. No execution approval/claim or new market PnL.
Known validation, not OOS/blind replication; independent review for window2
changes pending. Next: review/approve exact proposal before execution.

Independent AI review completed with scoped acceptance:
[report — local evidence `data/bnb_independent_ai_review_20261019_01/REVIEW.md`.
Reviewer independently checked10bound/20raw/46protected hashes, Decimal ledger,
return/DD, timeline/cooldown and guard revision. Reviewer-authored read-only query,
root-assisted execution due WSL access limits, verified4×1081Parquet marks with
max error<8e-12USDT. No new blocking finding, trade-off verdict unchanged.
Scope excludes independent signal regeneration/full current-source fingerprint,
exchange calibration, alpha/OOS/paper/live. Reviewer did not rerun tests.
Independent AI review is not human/organizational signoff; human pending.
Bounded engineering/research review loop closed; no automatic next campaign.
Earlier independent-review-pending statements below are historical.

2026-10-19 follow-up: P2 approval-reference guard fixed in a
NEW runner revision — local evidence `data/bnb_approval_guard_revision_20261019_01/README.md`,
not in the immutable approved runner.42offline fixture/regression tests PASS;
only strict reference type/nonblank check changed. No new plan/approval/claim or
market run; original packet/economic verdict unchanged. Independent reviewer
signoff remains pending. Earlier next-action-to-fix statements below are history.

Further boundary verification:51tests PASS including9integration failure paths
for approval/source drift, duplicate claim, old output protection and path/symlink
escape. Receipt — local evidence `data/bnb_approval_guard_revision_20261019_01/boundary_tests_execution.json`.
No real-data reads/engine construction in boundary cases; frozen runner and
historical evidence unchanged. Bounded P2 engineering work complete; independent
reviewer signoff and economic qualification remain separate, not auto-approved.

### Latest — approved four-arm BNB ablation completed and arithmetically audited

Cross-method review follow-up:
review report — local evidence `data/bnb_ablation_review_20261018_01/REVIEW.md`.
Manually recomputed1080signals/strategy, Decimal ledger/1081marks per arm and
1080policy decisions per arm; conclusion unchanged.46packet/proposal files
unchanged. Same author agent, NOT independent human/organizational signoff.
P2 finding: approval guard coerces null operator reference to string "None";
actual packet approval valid, no metric impact. Frozen runner not edited.
Next engineering action: fix strict reference type/blank validation in a NEW
runner revision with negative tests; no additional market execution authorized.

Results — local evidence `data/bnb_ablation_20261018_01/RESULTS.md`,
audit — local evidence `data/bnb_ablation_20261018_01/audit.json`: user approved exact proposal
e0787c11…;4arms×1,1worker,0retry,600s cap, wrapper elapsed6.94s.
Cash0%; risk-constrained initial25%BH+22.9177%/DD14.8737% (open8.167BNB);
MA20/80−3.5780%/DD5.8737%,10closedtrips;
ADX25/DI+16.8462%/DD8.3089%,7closedtrips.
Return delta+20.4243pp but DD+2.4351pp => preregistered TRADE_OFF_OR_INCONCLUSIVE.
25tests PASS; fills reconstruct4×1081equitymarks; source/bound hashes stable.
Dominant ADX trade1863.33USDT exceeds totalnet1684.62; remainder−178.70.
Trace identifies entry/exit/cooldown interaction; not isolated ADX causal proof.
Economic alpha evidence insufficient; independent human review pending, no
holdout/OOS/paper/live/promotion. Next action: independent review this packet,
not automatic next campaign. Earlier preflight/proposal statuses below are history.

### Research operating procedure — documentation only, 2026-10-18

Added [one-run protocol](operations/RESEARCH_RUN_PROTOCOL.md),
[draft manifest](operations/BNB_ADX25_RESEARCH_MANIFEST.json) and
[worked BNB evidence card](operations/BNB_ADX25_EVIDENCE_CARD.md).
The procedure is TARGET, not a runtime schema or retroactive preregistration.
Existing V2 evidence supports scoped execution/accounting only; MA-only and
execution-equivalent benchmarks remain NOT_RUN. ADX25 is unchanged.
Next action: complete ablation preflight (score/cost lineage, parity, identity,
reuse compatibility, criteria and budget) before requesting execution approval.
No source/gate changes, new market run, holdout access or promotion in this update.

Preflight follow-up: signal/identity receipt — local evidence `data/bnb_adx_preflight_20261018_01/receipt.json`
passes 1080-bar parity for each arm, common warmup100 and four future-perturbation
checkpoints per arm. MA-only signals12BUY/12SELL vs ADX7BUY/9SELL, eight differing
bars (not fills). ADX includes DI alignment and filters exits as well as entries.
Score audit — local evidence `data/bnb_adx_preflight_20261018_01/score_cost_review.json` traces
already-net WFO return plus an additional0.32-per-trade hurdle; no gate changed.
Historical source differs, including current user changes in candidates.py and
run_nested_wfo.py; only scoped historical evidence reuse, not current-source
execution equivalence. Still BLOCKED_PREFLIGHT pending reviewed comparison runner,
logical trial snapshot, benchmark semantics and operator criteria/budget freeze.

Preparation follow-up: review proposal and exact execution command — local evidence `data/bnb_ablation_preparation_20261018_01/HANDOFF.md`.
Runner/fixture21tests PASS plus lint/help; full source/config/input/signal identity
and read-only logical registry snapshot bound in immutable plan.json.
Both strategy arms proposed as fresh runs, no cross-source metric reuse.
Cash and risk-constrained initial25%BH semantics explicit. Budget proposed4arms,
1worker,0retry,120s/arm,600s aggregate. Status PROPOSED_AWAITING_OPERATOR_APPROVAL,
not a market run, independent signoff or economic completion. Next: operator
review/approval of proposal hash/budget; no holdout/paper/live authority.

### Latest integrated engineering evidence (supersedes older snapshots below)

Operator approved recovery of the prior Git lock blocker on18/10. Rechecked
Git processes, file users and exact empty lock inode/mtime; recoverably renamed
the unused lock to index.lock.backup_20261018 in the review directory, not deleted.
lock_backup.json records recovery; integration_blocker.json/commit_execution.json
retain the earlier failure. Local commit identity and subsequent verification
are recorded separately in the same directory. No remote push/merge or paper/
live authority is implied by this engineering integration.

2026-10-18 integration self-review:126regressions PASS,42lifecycle/accounting
tests PASS, lint PASS, fresh AC01–AC15 PASS/identity_stable=true on the same
source/test snapshot. Explicit typed boundary now rejects historical preview,
permission result, dict or missing context at runtime permission/submit entry;
tests confirm even ALLOW preview cannot create intent/events/orders. No sizing,
signals, thresholds or admission qualifications relaxed.25%entry contract kept.
Review `data/bnb_integration_review_20261018_01/review.json` binds pre/post source,
all tests and53historical artifacts.9-file integration whitelist excludes user
wfo_wrapper.sh, MULTI_STRATEGY_RESEARCH_PLAN, tool guards and data/state changes.
Precommit AC: `data/acceptance_runs/ac01_15_20261018_004800_utc/verification.json`;
local integration identity/verification recorded separately under the review
directory. No remote push/merge or market campaign included. This is self-review,
not independent signoff or release acceptance.
Paper/live remain NO_GO:7closedtrades in latest case are not OOS qualification;
DSR/PBO/cost-stress/stability bundle not verified for this case; runtime
calibration/OOD/regime are unknown. No formal evidence was fabricated. The
requirements diagnostic is scoped to this case, not all global/historical
registries. Next authority decision concerns genuine qualification evidence,
not bypassing gates with a positive simulation or operator sizing approval.

2026-10-17 operator decision **ENTRY_ALLOCATION_25_PERCENT_CONFIRMED** for this
BNB4h MA ADX25 case: keep25%entry-reference notional allocation, allow price-driven
MTM exposure drift; no automatic25%rebalance or scale-in while long. Fees/spread/
impact remain separately accounted, not included in a hard all-in25%cost cap.
Existing signal exits, experiment stop/halt/cooldown and all admission/gates
unchanged. This is not global approval for other strategies or live/promotion.
Decision and9contract tests PASS:
`data/bnb_entry_allocation_decision_20261017_01/decision.json`, tests_execution.json.
No strategy/source or old packet edits, no new market simulation required.
The earlier request to choose entry-vs-MTM cap is now resolved; economic
qualification, tested-source integration and genuine runtime admission remain open.

2026-10-17 fixed-case V2 execution research completed:
**PASS_EXECUTION_RESEARCH_ONLY**, not runtime/paper/live acceptance.1080bars,
14orders all filled,7round trips, flat final; equity10000→11684.6222, fees40.7750,
net+16.8462%, DD magnitude8.3089%, Sharpe2.2135. Independent arithmetic program
verified persisted cash/fees/trades/1080marks and520protected hashes; identical
seed replay and fill-journal reconstruction PASS (not engine process resume).
Economic evidence remains insufficient: besttrade1863.33 exceeds totalnet1684.62;
remaining trades−178.70. Entry25% allocation drifted to peak close exposure40.13%.
V2 costs/closed-bar stops/sizing differ from old baseline; do not rank16.85%vs11.87%.
Evidence `data/bnb_execution_replay_20261017_01/{manifest,result,audit}.json`.
Important correction: prior admission/clock trace had32bar-action timing
mismatches despite same signal counts. New sandbox uses canonical research
signal generator and verifies1080exact actions/execution bars before fills.
Old receipts retained, not relabelled as signal-parity evidence.
96targeted tests PASS, lint PASS; fresh AC01–AC15 PASS/identity_stable=true:
`data/acceptance_runs/ac01_15_20261017_142332_utc/verification.json`.
Local dirty source tree9540f44ad83eed543e0244538a6a247faa7f85c7ea47684cb920959f8ba82ce7,
HEADb897c32; not committed/pushed or clean release signoff. No gate weakening,
holdout/data download, qualified policy or broker/account IO. Next: define
stop contract and review/integrate tested source; entry-vs-hard-MTM decision is
now resolved by the operator decision above. No new
strategy sweep or automatic promotion based on this positive result.

2026-10-17 historical clock diagnostic completed. Runtime still uses real clock;
new historical preview is diagnostic-only, not PermissionResult/authorization.
BNB1080bars:7BUY proposals; runtime stale BLOCK7; historical preview kill-switch
BLOCK7; conditional preview without kill-switch missing_calibration_evidence
BLOCK7. UNKNOWN calibration/OOD/regime retained.0orders/fills, no promotions.
Evidence `data/bnb_adx_clock_sandbox_20261017_01/result.json`, manifest.json,
trace.json;508source/original packet hashes stable during run, runtime98.47s.
82targeted tests PASS,42lifecycle/accounting regressions PASS, lint PASS.
Fresh AC01–AC15 PASS with identity_stable=true:
`data/acceptance_runs/ac01_15_20261017_140720_utc/verification.json`.
This is a local dirty source snapshot, not clean commit/release acceptance:
tree605839ae1d03134aeb628bd3df92dcc8cb1a7de482116270545bb0b46db4d634,
HEADb897c32. No holdout/OOS, publish, real broker/account or live permission.
Risk/evidence applicability for deterministic MA remains unresolved; accepted
research calibration protocol is not proof of predictive probability calibration.
See clock/evidence contract section in the observations report for next scope.

2026-10-17 operator-approved local sandbox completed:
**PASS_ADMISSION_BLOCKED_NOT_TRADING_ACCEPTANCE**. Fixed BNB4h MA20/80 ADX14/25,
1080previously observed validation bars; BUY7/SELL9/HOLD1064. Seven BUY proposals
were planned and blocked by stale historical market data. No orders/fills,
promotion or live IO; equity10000cash. Flat restart PASS, filled restart not
exercised.508source/original packet file hashes unchanged; runtime137.72s.
Evidence `data/bnb_adx_admission_sandbox_20261017_04/result.json`, manifest.json,
trace.json;47targeted tests PASS and lint PASS. Failed attempts01–03 preserved
(datetime schema and enum integration issues fixed in new runner/tests).
Runner never submits; it does not implement a new permission/promotion lane.
Economic verdict unchanged. Full trade sandbox remains incomplete: requires
reviewed simulation-clock and deterministic-strategy evidence/risk contracts,
not retimestamping data or inventing KNOWN evidence. Details and limitations in
the observations report. No gate edits or fresh AC01–AC15 claim at this step.

2026-10-17 admission review: **BLOCKED_BY_ADMISSION_CONTRACT** for continuous
paper of the unqualified BNB case.17new boundary cases + resolver regression:
38PASS, network denied,507source/original packet hashes unchanged. Evidence:
`data/bnb_adx_operational_20261017_01/admission_boundary_receipt.json`.
PAPER requires PAPER_ELIGIBLE; EXPLORATORY research permission is not paper
permission, even with cached runtime. Production gates unchanged.
Do not launch tournament_paper_trader: historical router-only loop, different
defaults, placeholder challenger scores, and destructive output cleanup;
not a continuous execution runner. Detailed findings and five-part proposed
local operational sandbox contract are in the observations report. New lane
needs an explicit operator scope decision; no paper session started here.

BNB/MA ADX25 operational follow-up: **OFFLINE_CASE_REPLAY_PASS**, not continuous
paper acceptance.393+627events replayed on isolated copies; startup restore==
full projection, duplicate IDs idempotent,20measured strategy order links/fills
match durable intent/risk/authorization and paper ledger. Net fills reconstruct
positions0/3.318BNB and equity11462.57/11800.07. Original packet33files unchanged.
42generic lifecycle/paper/accounting tests PASS with network denied and global
monitoring writes disabled; initial wrapperCWD40PASS/2relative-pathFAIL retained,
correctedCWD42PASS without editing source/tests. Evidence:
`data/bnb_adx_operational_20261017_01/offline_replay_receipt.json`, chain_receipt.json,
safety_tests_corrected_cwd.json. Case card updated in observations report.
No policy created/promoted, no real broker/account IO or case orders submitted.
Research remains insufficient; realtime paper NOT_STARTED because no eligible
policy activation path established. Do not bypass promotion or relabel replay
as continuous soak. No production code changes or new market campaign here.

Current narrow case: **BNB/USDT4h MA20/80 ADX14/25, step1 complete**.
Accounting/benchmark review verdict: ACCOUNTING_RECONCILED /
INSUFFICIENT_EVIDENCE_OF_ALPHA. No new simulator/campaign or code/gate changes.
Strategy return11.87%/2.44%, initial25%allocation buy/hold22.89%/2.53%; strategy
DD6.97%/2.15% vs14.86%/6.72%, but average exposure10.02%/9.29% vs36.34%/23.49%:
not equal-risk alpha comparison. Ex-post exposure-matched sensitivity is labelled
descriptive only. Fold0one trade1435.89USD exceeds totalnet1215.88; fold1closed
PnL−88.11 plusopen369.26 gives total281.15.7/4closed trades remain insufficient.
Exact evidence `data/bnb_adx_case_20261017_01/step1_results.json`;34protected
files/input unchanged. Case card is at top of
[observations report](operations/MULTIPAIR_OBSERVATIONS_20261017.md).
Scope now one complete lifecycle, not more pairs/strategies/AI frameworks.
No OOS/holdout/paper session or live approval created. Potential next step is
separately approved operational-only paper/shadow, with no qualification bypass.
Earlier broad research priorities below are historical and not current authority.

2026-10-17 multi-pair observations: [report](operations/MULTIPAIR_OBSERVATIONS_20261017.md).
Existing WFO validation-only runner used, no production code/gate changes.
Planned40cells/20packets;16cells/8packets accepted before BNB/RSI second cell
timeout180s stopped batch.11packets unexecuted,1INCOMPLETE, no retry.
ETH5strategies and BNB3MAvariants measured; no SOL/XRP results yet. No group
meets30trades and positiveSharpe inbothfolds. BNBMA/ETHvol-target positive
observations remain underpowered; RSIETH32tradefold still has negativeSharpe.
Raw SHA/accounting16checks each PASS, historical packets/source stable.
Exact evidence `data/multipair_screen_20261017_01/reviewed_summary.json`.
Cross-strategy windows differ due canonical history-dependent gaps; compare
pair effects within same strategy, not fair winner ranking or priorBTCpooling.
Next useful step is bounded coverage of missing pairs with explicit schedule/
budget, not automatic timeout retry or architecture expansion. No new run
authorized by this tracker update, no holdout/OOS/promotion/live.

R3 metadata follow-up complete: see section6 in
[multi-strategy plan](operations/MULTI_STRATEGY_RESEARCH_PLAN.md).
ExactBTC4h6795bars gives6/5/4/3folds for3/6/9/12month validation; p≤.10 requires
6/5/4/impossible cost-clearing folds. Long validations overlap. RSI9month density
scaling is near30 but not measured adequacy/profitability; no winner chosen.
Inventory10existingpre-holdout inputs across5pairs×1h/4h: strict timestamps,
zero abnormal intervals, last close respects cutoff. BTC1h25198bars has same
6/5/4/3calendar-fold geometry, not 4xindependent evidence. No price backtest.
Authority `data/research_r3_geometry_20261016_01/analysis.json`.
Recommended next: explicit1h calibration producer/verifier contract + synthetic
compatibility tests before any new pilot budget. Current market calibration is
hardcoded4h; synthetic1h is not market1h support. No implementation, approval,
freeze or new campaign performed during this metadata analysis.

### Latest result — POOL5 completed, feasibility ABSTAIN

User explicitly approved the displayed fingerprint/budget ("duyệt chạy đi").
Delegated approval provenance is recorded in
`data/pool5_freeze_20261016_01/operator_approval.json`, not reused from2cells.
Packet `data/calibration_feasibility_pool5_btc4h_20261016_01`:
**ACCEPTED_CALIBRATION**,10/10COMPLETED,10/10ABSTAIN; producer/shared CLI verifier
both PASS12checks (not independent algorithms). Supervisor336.56s,
execution327.52s, CPU194.60s; all approved budgets respected.
382protected files unchanged; operational registry append expected and logical
history/seal verified. Source/input/config stable on clean b897c32.
Receipt `data/pool5_freeze_20261016_01/execution_handoff.json`; raw summary
`data/pool5_freeze_20261016_01/results_summary.json`. All10raw SHA/bar partitions/
closed-trade attribution checks PASS; no packet mutation during summary.

| Strategy | 2024H1 trades / return / Sharpe | 2025H2 trades / return / Sharpe |
|---|---|---|
| bbands | 21 / -3.35% / -0.90 | 20 / -4.67% / -1.43 |
| enhanced_ma | 9 / +1.91% / +0.55 | 6 / -3.09% / -1.24 |
| ma_adx (ADX25) | 7 / +5.28% / +1.27 | 5 / -2.69% / -1.06 |
| ma_vol_target | 9 / +1.91% / +0.55 | 8 / -3.73% / -1.38 |
| rsi | 22 / -1.49% / -0.41 | 21 / -5.25% / -2.08 |

Every cell fails30trade minimum;7/10also fail Sharpe>0. The early ma_adx positive
result is NOT a winner or qualification. RSI/BB have denser actions, but many
already-long/flat exits and negative returns: more emitted signals alone does
not solve sample adequacy or economics. Gate reasons are distinct from normal
execution terminals. No OOS/sensitivity/holdout/promotion/live was run.

R2 fixed-default feasibility is complete for these5candidates/2windows, NOT
full strategy search. R3 next: metadata-only joint trade/fold feasibility and
preregister a new hypothesis if necessary. Preserve all trials; do not lower
thresholds, rerun a larger grid or switch TF/pair without a separate plan/budget.
Older FROZEN_NOT_EXECUTED statuses below are historical.

Latest authorized action: **POOL5 FROZEN, NOT EXECUTED** on reviewed clean
sourceb897c32. Packet:
`data/calibration_feasibility_pool5_btc4h_20261016_01`.
Fingerprint `0b5ff99f4752c512718e6727686597a5194b9a023f3c04b063ff9f93e3725f87`.
5strategies×2windows=10cells: enhanced_ma/ma_adx/ma_vol_target/rsi/bbands;
fixed defaults, ADX25 for the two ADX subjects. Gates remain30trades and
Sharpe>0. Budget180s wall/120s CPU per cell,1800s wall/1200s CPU total,
sampled RSS2GiB,1worker,0retries; caps are not ETA. No new approval created and
old2cell approval not reused. Source/registry/383protected files unchanged.
Authority: `data/pool5_freeze_20261016_01/freeze_receipt.json`.
Initial supervisor check assumed caller order rather than sorted canonical
strategy order; corrected via read-only inspection, no refreeze/packet edit.
Await exact fingerprint/budget approval before execute. No holdout/live authority.
Older next-freeze statements below are historical.

Compatibility integration handoff: selected strategy/history/funding fixes,
regression tests and linked roadmap are being committed separately. Exact commit
and post-commit verification are authoritative in
`data/retire_wfo_wrapper_20261016_01/compatibility_commit_receipt.json` and
`data/compatibility_post_commit_20261016_01/compatibility_verified.json`.
Check those receipts rather than treating the earlier dirty-source snapshots
below as current integration status. Review here is author self-review, not
independent reviewer sign-off. No new freeze/execution/push/merge is included.

Next-phase plan: [Nghiên cứu nhiều strategy](operations/MULTI_STRATEGY_RESEARCH_PLAN.md).
Latest scoped pool audit:108regression tests +Ruff PASS. Prefix-append causality
checked for17default candidates on one deterministic fixture;2named_ls runtime
tests confirm SELL/reduce_only, not short. Source unchanged fromb22da73a AC
snapshot; protected files unchanged except explicit test extension. Receipt:
`data/retire_wfo_wrapper_20261016_01/pool_audit_receipt.json`.
No full17candidate execution parity/qualification claimed. Next boundary is
review/integration of dirty source patch, then separately approved5candidate
calibration feasibility; old2cell approval does not authorize new source/matrix.
Engineering follow-up (uncommitted source patch).
Final local verification PASS:205regression tests, AC01–AC15 PASS, Ruff PASS,
git diff --check clean;383protected files/source identity stable before/after.
Source tree `b22da73aac8f82ef9a1094836b56c844516f88ecee6518c898f113c2e5f00a90`
on dirtycdf39ce; not yet committed/reviewed as an integrated release.
Receipt `data/retire_wfo_wrapper_20261016_01/compatibility_verified.json`.
Fixed regime-history resolver and grid max for ensemble_ma_adx/ma_adx_regime
(default96→268bars), removed funding
synthetic fallback and backward fill; missing funding now fails closed.
7failing synthetic regressions reproduced before fix;89targeted tests and Ruff
PASS after fix. Integrated verification authority:
`data/retire_wfo_wrapper_20261016_01/compatibility_verified.json` (check status
and source identity, not this progress text). Old diagnostic remains historical
on cleancdf39ce, not re-executed/resealed. Calibration allowlist/gates/ADX25 and
research_only unchanged. Source changed: no reuse/freeze under old approval.
Historical R0/R1 snapshot below predates these fixes; broader economics/parity,
true funding/portfolio/short semantics still need evidence, not a PASS relabel.
R0/R1 audit update:28scoped raw/accounting assertions PASS, packet unchanged;
17candidate defaults/warmup inventoried;51factory/contract/PIT tests PASS.
Evidence `data/retire_wfo_wrapper_20261016_01/r0_r1_audit.json` and
compatibility_tests.json. R0 SCOPED_RECONCILIATION_PASS, R1 INVENTORY_COMPLETE /
COMPATIBILITY_PARTIAL, not final acceptance. Blockers:252bar regime lookback vs
97bar bridge window for2candidates; funding fallback/PIT; single-asset stat/CS
approximations and unproven short execution. See plan for17-row matrix and exact
follow-ups. No new market campaign, strategy/gate changes or live authority.
R0 raw/accounting review → R1 compatibility inventory17candidates → R2 bounded
5strategy feasibility proposal → R3 sample/fold protocol → R4 qualification →
R5 operational paper/shadow. Plan only; no new campaign/budget approved.

### Diagnostic executed following explicit delegated approval

User subsequently authorized agent-created approval and execution in chat
("cứ làm đi tôi cho phép"); this is a one-run exception to the operator-created
file workflow, not permission to generate future approvals. Approval provenance
is recorded outside the packet in
`data/retire_wfo_wrapper_20261016_01/operator_approval_delegated.json` and copied
by the executor into the packet. It is not a human digital signature.
Packet `data/calibration_telemetry_btc4h_20261016_01` is now
**ACCEPTED_CALIBRATION**, producer and separate CLI verifier PASS all12checks.
Two cells completed, both ABSTAIN. Supervisor58.43s; execution49.61s/CPU25.89s.
Logs: `data/retire_wfo_wrapper_20261016_01/authorized_execution.json`.

| Validation window | Trades | Return | Sharpe | Max DD | Win rate | PF |
|---|---:|---:|---:|---:|---:|---:|
| 2024H1 | 9 | +1.91% | 0.555 | 6.48% | 33.33% | 1.253 |
| 2025H2 | 8 | -3.73% | -1.376 | 6.36% | 25.00% | 0.327 |

Both fail minimum30validation trades; late also fails Sharpe>=0. No OOS or
qualification claim. Telemetry: early1092bars=1074HOLD+9BUY+9SELL,12runtime
submissions/6flat exits; late1104bars=1088HOLD+8BUY+8SELL,14submissions/2flat
exits. Missing trade-bar attribution0both. Early9closes=6stop+3signal; late8=
2stop+6signal. Late carry-in/out true, so do not equate BUY count with closed
trades. No extra terminal gate rejection is observed in these two windows;
signal sparsity dominates this measured simulator-gate scope, not proof about
all raw forecasts or other strategies. Source/input/config stable per C10;
338historical market-packet files independently rehashed unchanged.

Next: review measurement/accounting and close attribution, then preregister a
sample-feasible multi-strategy qualification plan before requesting another
budget. No automatic campaign retries/grid expansion or lower thresholds.
ADX25 unchanged; this subject has no ADX parameter. No holdout/promotion/live.
All freeze-only/awaiting-approval statements below are historical snapshots.

Freeze follow-up authorized and completed (no execution):
`data/calibration_telemetry_btc4h_20261016_01/calibration_manifest.json`.
Fingerprint `60b7ac42f968d34363ed900530c4dd1ee68b1262f1879a5732cc1bf17be67878`.
Exactly2cells, ma_vol_target20/80,1x,1worker,0retries; per-cell wall180s/CPU120s,
aggregate wall1800s/CPU1200s, sampled RSS2GiB. Identity grid is trial-history
binding, not an instruction to execute all grid combinations. Receipt:
`data/retire_wfo_wrapper_20261016_01/freeze_receipt.json`. Protected files,
registry and source unchanged. Supervisor initially looked for manifest.json
instead of calibration_manifest.json after successful freeze; recovered by
read-only inspection, no retry or packet edit. Awaiting operator approval of
this exact fingerprint; do not auto-create approval or execute from a generic
continuation. Earlier no-freeze statements below describe pre-freeze snapshots.

Revision `cdf39ce132a22bac32e5daff8c95f009bf9ff567`: **139 regression tests PASS,
AC01–AC15 PASS, Ruff PASS**, with source and350protected file hashes unchanged
before/after. Execution source is clean; the whole workspace is not declared clean.
Receipt: `data/retire_wfo_wrapper_20261016_01/integrated_verified.json`.
Read-only preflight:12/12technical checks PASS; receipt
`data/retire_wfo_wrapper_20261016_01/preflight_current.json`. Existing input6795bars,
early/late measurement1092/1104bars, input/manifest hashes and logical registry
match. Older BLOCKED_REVIEW_INTEGRATION receipts below are historical, not the
current technical verdict. Separate automated reviews found no scoped blockers;
this does not replace operator consent, human acceptance or strategy qualification.

**Next: obtain explicit authorization to freeze a fresh two-cell telemetry
diagnostic**, then operator approval of its exact fingerprint before execution.
Use `--strategies ma_vol_target` for the bounded proposal, not the default five
strategies/ten cells. It measures crossover/filter/order/close attribution, not
the best strategy; defaults20/80 are a plumbing subject, not a selected winner.
Keep ADX25 unchanged. No new packet frozen, approval created, market campaign,
holdout access, policy activation, live orders, push or merge occurred here.
If source changes, invalidate this current-revision claim and reverify/refreeze.

### Earlier integration and preflight history

Selected integration update: separate automated reviewer
`/root/review_readiness_integration` reports no blocking findings in the scoped
Python/docs changes; did not rerun tests. Receipt:
`data/readiness_integration_20261016_01/automated_review.json`.
Selected implementation commit:2fc02acae9e02ee259718afa76312411ed354867 on
codex/readiness-integration-20261016;15reviewed files, no push or market execution.
Final commit/verification identity is recorded
in that local integration evidence directory, not inferred from older snapshots.
This is not human sign-off or whole-tree clearance. Following explicit user
authorization, scripts/run_wfo_b_phase.sh is now an inert retired stub (exit64),
with no data reads, deletion or campaign launch. The original is backed up in
data/retire_wfo_wrapper_20261016_01/. Five focused tests pass. Separate review,
commit and post-commit source checks belong to that evidence directory; do not
infer whole-workspace cleanliness. No freeze, approval, execution or push here.

Preflight update: `data/readiness_preflight_20261016_01/preflight.json` has11/11
technical checks true (NOT C1–C10 packet acceptance). Verified source04ba6a07,
all protected files, input bytes and logical registry remain unchanged. Exact
early/late windows contain1092/1104bars. Status BLOCKED_REVIEW_INTEGRATION:
independent review not supplied, clean integration not established, frozen
fingerprint approval absent. No packet frozen, approval created or measurement
started. Boundary draft is prepared separately, not approved. Next action is
review/integration authorization, NOT another market run or repeated test suite.

**Latest combined verification: LOCAL_VERIFIED_REVIEW_PENDING.**
`data/readiness_closure_20261016_01/verified.json`:134 regression tests PASS,
AC01–AC15:15PASS, Ruff PASS; source/config/tests/348protected files stable
before/after. Source tree04ba6a07cc3e66a8311191093f32b14761b33bf85150b9dbbaca7e21193eba64
at dirty master9b69462. AC packet: `data/acceptance_runs/ac01_15_20261016_081845_utc`.
This supersedes the narrower verification scope immediately below; not full
repository testing, independent sign-off, Git integration or live readiness.

Metadata-only geometry planner repaired; stale build_campaign_policy CLI retired
before data/registry/policy writes. Plans now use canonical p<=0.10, history and
pre-holdout timestamps, with trade adequacy UNKNOWN and no invented ETA/ranking.
For existing BTC4h metadata, validation3/6/9/12months gives6/5/4/3folds;
minimum binomial clearing counts are6/5/4/impossible respectively. These are
geometry checks, NOT qualification or strategy recommendations.

Next approval item: `data/readiness_closure_20261016_01/diagnostic_proposal.json`
(DRAFT,2 fixed ma_vol_target calibration cells, existing pre-holdout bytes,
1worker,0retries,180s/cell,1800s total upper bound,2GiB RSS contract).
No new market evaluation/holdout/activation/broker/live was performed.

Measurement telemetry IMPLEMENTED/LOCAL_TESTED: execution-bar scoped terminal
reasons, runtime/authority status, returned-order intent links, fill snapshots
and close attribution. Broad regression120PASS; Ruff PASS. Evidence:
`data/funnel_trace_20261016/telemetry_tests_final.json`. No mandatory-gate source
file edited; no new AC/full-repo/independent acceptance claim. Next is source
review/integration and explicit diagnostic budget approval, not automatic rerun.

Follow-up funnel trace complete: historical exporter deliberately removed
signals/authority traces; per-filter attribution is not recoverable from these
packets. Read-only lifecycle audit PASS;45 synthetic contract tests PASS.
Evidence: `data/funnel_trace_20261016/result.json`, `contract_tests.json` in the
same directory. Telemetry is now implemented as noted above; next is review and
separately approved market diagnostic. Vol Target sizing semantics need
clarification (traced bridge uses fixed25% target, not observed vol sizing).

Current integration branch: `codex/readiness-integration-20261016`.
The original checkout baseline was master9b69462. The implementation commit
above contains the selected changes; untracked/unrelated work remains preserved.
Post-commit QA is recorded in data/readiness_integration_20261016_01/verified.json.
The older integration-branch description below is historical.

| Work | Evidence | Verdict |
|---|---|---|
| MA ADX BTC4h v4 | `data/wfo_ma_adx_btc_4h_v4` | 18 inner cells complete; 2/2 folds ABSTAIN; no OOS |
| Vol Target BTC4h v1 | `data/wfo_vol_target_btc_4h_v1` | 8 inner cells complete; 2/2 folds ABSTAIN; no OOS |
| Input-contract patch | `data/calibration_closure_20261016_tests.json` | 70 module tests PASS; not full-repo or integrated acceptance |
| Packet diagnosis | `data/calibration_closure_20261016/review_result.json` | 26 raw hashes match; closed-trade attribution matches |
| Prior integrated AC | `data/acceptance_runs/ac01_15_20261015_091209_utc` | 15/15 PASS on c9a255d2; historical after source change |

Each validation window has540 x4h bars; candidates have0–6 closed trades versus
minimum30. Some also fail Sharpe. No winner qualified. Independent review remains
pending; verifier summary is not reviewer sign-off. Next: trace signal/filter
funnel and review fold feasibility before requesting new research budget.
No new market campaign/holdout/publish/live authorization.
See [closure and next decision](operations/VALIDATION_CLOSURE_20261016.md).

## Historical integration — 2026-10-14, COMMIT_PREPARATION_PENDING

Nhánh tích hợp: `codex/calibration-integration-20261014`, base `dbb9713`.
Operator đã duyệt đồng bộ code/tests/docs lên PR; không cấp quyền market/live.
Snapshot local mới nhất source `32bfdb619927bcc5ffcf22958db54e9168a0a7e846c417b6e4c8b07ed12178af`:
80/80 scoped regression PASS; AC01–AC15:15PASS có raw SHA và identity khớp.
Evidence local-only: `data/calibration_v2_recheck_20261014/review.json`,
AC packet `data/acceptance_runs/ac01_15_20261014_045012_utc`.
Protocol producer/worker/verifier đã thống nhất2.0; market freeze không synthetic;
fingerprint, resource/authority guards và backup inventory đã kiểm. Giữ ADX25.

## Phase B — Validation-Only WFO Campaigns (2026-10-15)

Two validation-only WFO campaigns completed successfully:

- **ma_adx** (`data/wfo_ma_adx_btc_4h_v4/`): 18 cells (9 param combos x 2 folds), protocol `791fe675...`, duration 18m37s, peak RSS 708.8 MB. Both folds ABSTAIN (insufficient validation trades: 0-4 each). Exact coverage: PASS. live_authorized: false.

- **vol_target** (`data/wfo_vol_target_btc_4h_v1/`): 8 cells (4 param combos x 2 folds), protocol `7f50d1ef...`, duration 8m41s, peak RSS 708.1 MB. Both folds ABSTAIN (insufficient validation trades: 4-6 each). Exact coverage: PASS. live_authorized: false.

ABSTAIN reason: All candidates have 0-6 validation trades (minimum required: 30) due to 3-month validation windows at 4h timeframe — insufficient observation, NOT strategy failure. Next step: analyze window geometry, signal frequency, and filter settings before next campaign.

## Historical drift finding — 2026-10-14, REVISION_REVIEW_REQUIRED

Operator chốt giữ ADX25 cho calibration cost/plumbing và preset validation.
Trong kiểm tra quyết định, actual source fingerprint đã khác snapshot431PASS:
`0edcf435e75f9301509a3828905153ea4b6e370dee852e8de65045fffab014ea`.
Evidence: `data/calibration_scope_decision_20261014/identity_drift.json`.
Environment/runtime config/test-source identity không khác trong comparison này.
Không sửa source trong lượt ghi nhận preset; giữ nguyên thay đổi hiện hữu.
431PASS/AC15 bên dưới chỉ chứng nhận source04141…, không phải revision hiện tại.
Cần review diff và kiểm chứng revision mới trước freeze/approval/execution.

## Historical 431-test snapshot — 2026-10-14, LOCAL_VERIFIED_REVIEW_PENDING

Review sau kiểm chứng đã tái hiện hai lỗi: worker CLI nội bộ có thể tới engine
ngoài supervisor/intent; sampler bỏ CPU của child đã reap (probe5children chỉ
ghi0.13s rồi wall-timeout). Đã bổ sung one-shot parent pipe +registered intent/
approval/replay guards và cumulative child CPU. Final **431PASS/22scoped modules**,
0FAIL/0ERROR/0SKIP; **AC01–AC15:15PASS**, Ruff/diff PASS. Focused56tests là
diagnostic, không cộng thêm vào431. Source/config/test identity trước-sau ổn định:
`04141b7e8170ee2a5e6a8da4ba7c5068d994d94d34befe8cd00ca63e330a56c4`.
Registry425rows/digest giữ nguyên; market packets và toàn bộ QA packet13/10
không đổi. Authority: `data/calibration_review_20261014/final_verification/verified.json`;
post-doc `data/calibration_review_20261014/final_handoff.json`. Không full repo suite.
Proposal10cells/BTC4h/pre-holdout6795bars đã tạo, DRAFT_NOT_FROZEN_NOT_APPROVED.
Operator đã chốt **giữ ADX25** cho enhanced_ma/ma_adx ngày2026-10-14,
chỉ cost/plumbing và preset validation; parent-grid30 giữ nguyên. Decision:
`data/calibration_scope_decision_20261014/operator_scope_decision.json`.
Preset choice đã CLOSED; execution approval/review/integration vẫn PENDING.
Không coi calibration là qualification/reuse/full-grid result.
Next: external independent review → clean integration/refreeze/reverify → explicit
operator approval cho fingerprint/scope/budget → mới được market calibration.
Review cùng agent không phải independent sign-off. 414PASS bên dưới là historical.
Không market campaign/holdout/publish/live mới; mainnet **NO-GO**.

Read-only qualification identity audit (cùng14/10):
`data/calibration_identity_20261014/identity_audit.json` PASS, source/config/tests
khớp đúng revision431PASS; hai registry và1437protected files không đổi.
RSI/BBands/MA Vol Target có đúng1default khớp raw params hash trong grid;
Enhanced MA/MA ADX không khớp vì25vs30. Không thấy lỗi raw/normalized representation
ở các điểm gần default đã kiểm; **không có quyền reuse tự động**, vì còn khác
execution context và train/validation geometry. Không chạy lại suite không đổi,
không thêm market measurement hoặc tự ký independent review. Scope preset đã chốt
giữ25, không cấp quyền chạy: [calibration contract](CALIBRATION_PROTOCOL.md#kiểm-chứng-đường-nối-sang-qualification--2026-10-14).

## Previous local verification — 2026-10-13, dirty `dbb9713`

**LOCAL_VERIFIED_REVIEW_PENDING** — calibration v2 và qualification restoration.
Final **414PASS/22scoped modules**, 0FAIL/0ERROR/0SKIP; integrated
**AC01–AC15:15PASS**, Ruff/diff PASS. Source/config/tests trước-sau ổn định:
`452170b22fdd9ad8a1cef165b40fb066b0ec970d4519e6c11e9d139dd151ac49`.
Registry425rows/digest `d641649ae2f2a2b833ac7e5024407679ad81900c21699b94b9eade03e6a05dc4`
và toàn bộ historical packet inventory không đổi. Không full repository suite.
Authority: `data/calibration_engineering_20261013/final_verification_02/verified.json`;
post-doc `data/calibration_engineering_20261013/final_handoff.json`.
Vòng QA đầu đỏ được giữ nguyên; không ghép snapshots hoặc cộng lại tests
diagnostic209PASS vào final414PASS. Các số liệu 2026-10-10/11 phía dưới là historical.
Synthetic simulator ×2cells: COMPLETED/ABSTAIN, 0freezes; không market proof.
Next: independent review → clean integration/refreeze/reverify → operator approval
cho calibration thị trường → evidence review trước qualification/pilot.
Không market calibration/pilot/holdout/publish/live mới; mainnet **NO-GO**.
[Calibration v2 contract](CALIBRATION_PROTOCOL.md).

### Latest handoffs — independent review pending

2026-10-11 Historical calibration draft (SUPERSEDED_BY_V2; không phải approval hiện hành):
Calibration protocol thiết kế và implement xong: 5 OHLC strategies (rsi, bbands, enhanced_ma, ma_adx, vol_target) × 2 validation windows (early/late) = 10 cells. Fixed preset params, cost 1x, 1 worker, 0 retries, 180s/cell, 30min total, 2GiB RSS. INNER_VALIDATION only (no outer/OOS/holdout/promotion/live). Acceptance criteria C1-C10 định nghĩa rõ. Operator approval gate + independent review bắt buộc trước khi chạy. Scripts: `scripts/run_calibration.py`, `scripts/verify_calibration.py`, `docs/CALIBRATION_PROTOCOL.md`. Validation-only workflow tests 24/24 PASS, AC01-AC15 15/15 PASS, Ruff/diff PASS. Source `139392686d1868b8cecc55efa50b8965020664a404d980f4603cf609881f90f3` at HEAD `dbb9713f95ffb6bf24ce65a1ee469afbbf335d9d`. Next: operator duyệt checklist → chạy calibration → independent review sign-off → freeze pilot budget cho vol_target/BTC/4h. Pilot KHÔNG được authorized bởi protocol này. Chi tiết: [calibration protocol](CALIBRATION_PROTOCOL.md).

2026-10-10 Canonical validation-only repair (LOCAL_VERIFIED_REVIEW_PENDING):
đã bỏ dedup mất windows/copy scores giữa folds, sai metric keys/default0,
bypass eligibility và coverage nonempty. Inner-only dùng canonical WFO/registry/
full protocol context; ≥30trades, Sharpe>0, deterministic tie và ABSTAIN giữ nguyên.
Không OOS/sensitivity/holdout/qualification, summary chỉ sau exact raw coverage.
Workers1/retries0, frozen cell timeout≤180s, worker wall≤1800s, sampled group RSS;
cleanup chờ process group không còn active. Source
`139392686d1868b8cecc55efa50b8965020664a404d980f4603cf609881f90f3`;
packet `data/validation_only_fix_20261010/`, final21scoped modules **375PASS**,
integrated **AC01–AC15:15PASS** (`ac01_15_20261010_095655_utc`). Identity stable,
Ruff/diff PASS, registry425rows và bảy old packets unchanged. Real synthetic
worker demo1cell:37.58s/698MiB,13trades/Sharpe−15.96 ⇒ ABSTAIN; không market proof.
Authority: `final_verification/verified.json` +post-doc `final_handoff.json`;
không full repository suite, chưa independent review/clean integration.
Next: reviewer → integration/refreeze/reverify → duyệt representative calibration
scope/budget → market pilot nếu đủ evidence. Validation-only hiện là full inner
protocol, **không phải**10cells early/late calibration; không cắt grid/folds để
gắn nhãn COMPLETE. Các validation packets cũ vẫn INCOMPLETE và lịch sử attempts
cần reconciliation riêng, không backfill scores/reset burden. Chưa market
campaign/pilot/holdout/publish/live mới trong lượt này.
Chi tiết: [validation-only handoff](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#canonical-validation-only-repair--2026-10-10-local_verified_review_pending).

2026-10-09 Bounded synthetic profiling (LOCAL_VERIFIED_REVIEW_PENDING):
tool `scripts/strategy_research/profile_synthetic.py`,5catalogOHLC presets,
BTC1h300bars,1worker/0retries,60s/cell,total330s,sampled RSS ceiling1024MiB.
5/5COMPLETED; wall6.94–19.75s,CPU5.00–7.51s,RSS343.93–348.23MiB.
3presets0closed trades: không đại diện order-intensive/full4hWFO,không p95/ETA.
Packet `data/synthetic_profile_completion_20261009/`;source
`8e539afd9cd5983129339ea2ced310eb2d516c96338424362ba1bbf4b6d31eb4`.
Final20scoped modules351PASS +AC01–AC15:15PASS (`ac01_15_20261009_154215_utc`),
identity stable,Ruff/diff checkPASS,registry425rows và old packets unchanged.
`verified.json` là local authority, không reviewer acceptance/market qualification.
Next: independent review/integration → approved representative validation-only
calibration → refreeze pilot budget trên revision tích hợp. Chưa chạy market
pilot/holdout/publish/live; không tắt durability theo micro-profile.
Chi tiết: [profiling handoff](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#bounded-profiling--2026-10-09-local_verified_review_pending).
Các source/verification bên dưới là historical snapshots, không đổi nhãn thành current.

2026-10-09 Reuse integration closure (LOCAL_VERIFIED_REVIEW_PENDING): maintainer's
per-subject dispatch giữ lại; strict source/protocol admission trước mọi job,
không silent fresh fallback. Generic plan semantic hash tách timestamps, giữ
source/dirty/data/config/gates; canonical research full protocol v2 không nới.
Cache miss reason/terminal receipts/global cell cap; generic --no-publish không
build/activate policy. AC lưu source/config/test identity trước-sau +fresh raw
hashes; drift/stale evidence FAIL. Packet `data/reuse_integration_completion_20261009/`;
source `96a103082d157bc744fc92153402f6dafe24bd9cde2060b0f0906247630f2997`.
Final19scoped modules PASS +AC01–AC15:15PASS (`ac01_15_20261009_151502_utc`);
source/config/test identity stable,fresh evidence hashes,Ruff/diff checkPASS.
Authority `final_verification/qa_final.json`; không full repository suite.
Catalog50jobs/310folds/12,195planned inner,0market evaluations;425rows unchanged.
Old smoke/cold vẫn INCOMPLETE,0cache hit,sensitivity2/3 và1/3; không đủ chứng cứ
nguyên nhân dừng. Proposed BTC4h5strategy pilot1,269inner +outer≤32 +sensitivity≤219;
chưa ETA/representative CPU-RAM profile/pilot approval. Independent review pending.
Chi tiết/latest limits: [reuse handoff](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#reuse-integration-closure--2026-10-09-local_verified_review_pending).

2026-10-09 P2 evaluation identity v2 (AWAITING_REVIEW):candidate ID giữ nguyên;
registry append context-scoped atomic, exact replay idempotent/metric conflict
reject, new protocol append immutable evaluation mới, legacy giữ không reuse.
Raw/effective burden tính candidate×context +legacy bucket; DSR trực tiếp/
summary cùng registry source. Không reset DB/search-family/evaluator/threshold.
Final catalog50job/310folds/12,195inner cells chỉ planned,0market evaluations:
48NO_MATCHING_HISTORY;2HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED (152legacyrows),
0current conflicts; Funding/cross-asset vẫn BLOCKED_DATA.425rows/digest giữ nguyên.
Packet `data/research_evaluation_identity_20261009/final_catalog/`; source
`96392d372fd5b520388ff84bfe9bf7970c702e3f29ccca36528766dce43368c6`.
Final251scoped testsPASS/14modules/347.53s; integrated AC01–AC15:15PASS
(`ac01_15_20261009_080845_utc`), Ruff/diff checkPASS; source/environment không đổi.
Không full repository suite/independent acceptance;91targeted cases không đếm trùng.
Orchestrator reuse dispatch/independent review/clean integration/representative
profile/full trace vẫn OPEN; không campaign mới/live. Trạng thái BLOCKED_HISTORY
của audit trước bên dưới là historical, không áp dụng vào fresh v2 execution.
[Current v2 contract](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p2-evaluation-identity-v2--2026-10-09-awaiting_review).

2026-10-09 P2 trial-history early guard (AWAITING_REVIEW):worker kiểm đúng
canonical candidate/fold/phase trong SQLite read-only trước evaluation, kiểm lại
ở runner startup; full metric/metadata append vẫn atomic. Catalog preflight đủ
50job OHLC:48 NO_MATCHING_HISTORY,2 BLOCKED_HISTORY_CONFLICT (Trend Pullback
BTC4h:74rows; Vol Target BTC4h:78rows),10 Funding Carry BLOCKED_DATA và cross-asset
chưa bound. Registry425rows/logical digest trước/sau không đổi;0evaluations mới.
152matched rows khác commit và thiếu context; không blanket-reject425rows.
230scoped testsPASS/13modules/290.42s; integrated AC01–AC15:15PASS,
`ac01_15_20261009_062704_utc`. Final result rc0, source/environment unchanged,
Ruff/diff checkPASS; không full repository suite hoặc independent acceptance.
Source `7b26764fb2eee94fc088a08e8b8b6e083f99b2b73e38369159b73e49029dcfad`.
Packet `data/research_history_preflight_20261009/`. Next:versioned evaluation
identity giữ historical burden, không reset/migrate legacy; orchestrator dispatch/
independent reviewer/clean integration/representative profile vẫn OPEN.
Research NOT_QUALIFIED/live NO-GO, không campaign thị trường nặng mới.
[Current history guard contract](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p2-trial-history-preflight--2026-10-09-awaiting_review).

2026-10-09 P2 qualification cold recovery author handoff (AWAITING_REVIEW):
worker CLI nối cache validation/selected OOS với fresh destination và exact
protocol; source outer decision/freeze bắt buộc khi reuse cache. Sensitivity
luôn rerun, selection/coverage/native gates không bỏ qua. Registry replay atomic
và identity/metric strict, bind protocol/source/runtime; incompatible legacy
history fail-closed, không reset. Per-cell OS lock và UUID imports xử lý concurrent
writers/interrupted copy; non-secret env/config SHA/loaded risk và active loader
binding được kiểm. Report measured capital rebased theo opening equity đúng
warm-up, không ép bằng simulator initial capital. Packet mới:
`data/research_qualification_resume_20261009/`. Main143testsPASS/69.69s,
AC01–AC15:15PASS, authority13testsPASS/36.03s; source fingerprint
`7b489f460c24da1012bfee5a1d6711c3ad49cb9d0d6c6b40f447ff062705713e`.
Final measured synthetic demo/raw parity và BTC4h preflight6folds/72cells/
0evaluationsPASS. Registry audit425rows/0protocol-context: legacy conflicts cần
review, không reset. Authority fixtures đã sửa shared-registry/pre-bound real
callback leak; ngoài ý định market-data diagnostic không dùng qualification.
Combined WFO suite TIMEOUT120s retained; unit-suite rerun64PASS/146.98s với
synthetic input thực sự bound trong child. Total220distinct scoped testsPASS
(143+13+64), chưa full-repository PASS. Source/config/environment QA khớp final
preflight; packet cũ không bị ghi đè. Next
catalog orchestrator reuse dispatch/independent review/clean integration và
representative performance/full trace parity vẫn OPEN. Full P2 chưa ACCEPTED,
research NOT_QUALIFIED/live NO-GO; không market campaign nặng/holdout/broker/push.
[Current qualification recovery contract](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p2-qualification-cold-recovery--2026-10-09-awaiting_review).

2026-10-09 P2 cache v2 author handoff (AWAITING_REVIEW): exact source/data/plan/
environment/stage/window/cost/fault cache binding; matching cache corrupt/legacy
fails closed, no silent rerun. New attempts have unique immutable directories;
atomic no-replace completed cache; cold import into disjoint fresh packet preserves
source packet. Genuine child-kill before/after publication recovery tests PASS.
Final scoped115testsPASS/9modules/26.27s; integrated AC01–AC15:15PASS. Demo real
simulator on300synthetic bars:1newcell/1warmhit/1coldhit; report bytes/metrics/health
parity/source-packet unchanged, NOT market qualification/full trace proof.
Packet `data/research_resume_p2_20261009/`; source fingerprint
`5a86038372d9bf6d9804c816a155c84bd5a771844dfc8ba56ab41a55d86d7050`.
Frozen BTC4h preflight6folds/72innercells/0evaluations, historical validation ABSTAIN.
Catalog qualification resume not enabled; full effective-config/history binding,
trial-count/frozen-selection idempotence, concurrent/partial-import recovery,
independent review/clean integration and broad authority timeout remain OPEN.
Full P2 INCOMPLETE/research NOT_QUALIFIED/live NO-GO. No market campaign heavy,
holdout/download/broker/commit/push. Previous entries retain their historical SHAs.
[Current cache contract/evidence/next owners](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p2-cache-v2--2026-10-09-awaiting_review).

2026-10-09 P2 author handoff (AWAITING_REVIEW): wrapper previously disabled
run_cell isolation, so Future timeout could leave cells running while shutdown
waited. Fixed per-attempt isolation/retry and callable outer dispatch (previously
fell back to different path); forward fault; fsynced exclusive started/result
receipts for inner/selected outer, transport failures retained, plan order stable.
Final scoped86testsPASS/10.80s, integrated AC01–AC15 rerun; broad authority suite
TIMEOUT120s remains recorded, not full suite PASS. Packet:
`data/research_runtime_p2_20261009/`; final source fingerprint
`813226afad26c334d7110fef8bbf7cfb929c0f4fc337597a606ef16bd870a3f6`.
Final frozen BTC4h preflight6folds/72inner cells/0evaluations, old validation still
ABSTAIN. Synthetic300bar profile at pre-callable source shows commit9.172s,
fsync1.938s/schema1.427s versus signal0.563s cumulative; only a small diagnostic,
no campaign ETA/speedup or live durability change. Qualification resume remains
disabled; receipts are not cache/qualification. Next integration/review,
isolated representative I/O profiling, exact-identity kill/restart resume then
bounded strategy feasibility. Full P2 INCOMPLETE, research NOT_QUALIFIED/live
NO-GO; no commit/push/holdout/broker or heavy market campaign.
[Latest scope/evidence/remaining work](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p2-executionprofile--2026-10-09-awaiting_review).

2026-10-09 P1 cadence/sensitivity author handoff (AWAITING_REVIEW): shared
timeframe cadence removes hardcoded1h from tournament quality/bridge; source
ordering audited before sort.10frozen subjects (5pairs×1h/4h) byte/quality checks
PASS,0new evaluations. BTC4h wrong1h negative control detects6794false gaps/
20382false missing bars; correct4h has0gaps. Stress starts flat at outer boundary
like primary OOS; freeze requests before rerun, axes>=2values use adjacent
neighbors, retain failed/missing/tampered receipts. Full verified scope now
planned_inner_selected_outer_and_sensitivity; no partial stress aggregation.
Native thresholds unchanged, old packets not relabeled current. Final12module
regression226PASS in191.49s; integrated AC01–AC15PASS. Packet:
`data/research_quality_sensitivity_20261009/`; source fingerprint
`f0718a321afcbc3e40204047d61a8d611559bba8624beb88b36eadf9dbf2d096`,
source_unchanged=true. Actual BTC4h preflight6folds/72inner cells/0evaluations;
historical12validation candidates remain ABSTAIN. No new alpha qualification,
commit/push/publish/holdout/broker or heavy campaign. Full P0/P1 INCOMPLETE,
research NOT_QUALIFIED/live NO-GO. Next integration/reviewer,environment/config
and legacy-scope binding,then P2 profiling/durable resume before bounded P3.
3measurement-window tests account for~94% test runtime; not measured campaign
bottleneck. [Current evidence/commands/limits](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p1-cadencesensitivity--2026-10-09-awaiting_review).

The 2026-10-08 entries below are historical; their cadence/supplemental gaps
are superseded by the author verification above, not independent acceptance.

2026-10-08 P1 engineering handoff (AWAITING_REVIEW): catalog/parallel entry point
now freezes and passes validation>=30/Sharpe>0 contract; input/source/spec/grid/
cost/history/folds/expected validation inventory recorded before evaluation.
Exact validation tuples and selected OOS reports checked before summary; worker
summary bound to raw WFO decision/hash and report inventory; campaign subject
coverage catches missing/duplicate pair/timeframe. Missing/scenario inputs and
legacy summaries fail-closed. Existing packet untouched; native thresholds
unchanged, noholdout/no publish/no broker/no heavy campaign. Final focused
regressions111PASS; final integrated AC01–AC15PASS. Evidence:
`data/research_protocol_verification_20261008/`; preflight_final BTC4h has6folds/
72inner cells/0evaluations; historical first-fold12validation candidates ABSTAIN.
Source fingerprint `f9867a438123207cdd5fa20d6c70a58cadba5f20dff8fc5abb111512f67b3fe1`
on dirty dbb9713, not clean integrated release. Full P0/P1 still INCOMPLETE;
independent review/dependency integration,1h–4h quality cadence and supplemental
sensitivity inventory remain. Research NOT_QUALIFIED/live NO-GO. Current handoff:
[P1 scope/evidence/next owners](operations/CAMPAIGN_EVIDENCE_HANDOFF.md#p1-contractcoverage--2026-10-08-awaiting_review).

2026-10-08 agent roadmap/sample: current P0–P5 work packages, owners/dependencies,
scope/claim/handoff/acceptance rules added to existing LIVE_READINESS_AGENT_PLAN;
existing CAMPAIGN_EVIDENCE_HANDOFF contains reproducible example (no new doc silo).
`data/live_readiness_example_20261008/` holds stdlib arithmetic auditor and NEW
results.6outer folds/input-byte linkage/ledger-return-DD/aggregate checksPASS;
in-memory +1pp return negative control rejected. Scope excludes historical dirty
source certification,Sharpe/DSR/PBO/full grid coverage/parquet re-quality checks.
Selected validation trades5/5/5/5/5/4 expose legacy/current protocol discrepancy;
do not relabel historical run as>=30validation-eligible. Research NOT_QUALIFIED,
full audit INCOMPLETE,live NO-GO. No broker/holdout/gate/source runtime changes.

Vol target rerun2 started2026-10-08 on operator request;old recovery packet kept.
Old recovery ended at3601.41s,rc1,TIMEOUT;coverage additionally misidentified
catalog vol_target versus canonical ma_vol_target. Wrapper now maps both
requested/result IDs through catalog code mapping without changing statuses;
2focused testsPASS,including preserving TIMEOUT. Per-job timeout now explicit
CLI --job-timeout-seconds;rerun7200s,controller7500s. No statistical thresholds,
input bytes,cost scenarios or holdout permission changed. Fresh output
`data/existing_research_vol_target_rerun2_20261008`;result
`data/existing_research_audit_20261008/vol_target_rerun2_execution.json`.
Detached controller log `/tmp/research_vol_target_rerun2_controller.log`.
This remains one existing-process subject,not full multi-strategy acceptance.

Recovery update2026-10-08:batch1 parent/child/controller disappeared without
result/summary;registry4699 still says running,last heartbeat stale. No retained
exit reason proves whether session loss,signal or another cause killed it.
Old packet retained,not resumed or represented as completed. Orchestrator now
writes worker stdout/stderr directly plus exclusive execution_started metadata,
so parent loss cannot erase buffered child output.1binding regressionPASS;RuffPASS.
Detached controlled recovery started for smallest catalog grid:vol_targetBTC4h,
same frozen input/cost all/holdout disabled;output
`data/existing_research_recovery_vol_target_20261008`,result
`data/existing_research_audit_20261008/recovery_vol_target_execution.json`.
Controller log `/tmp/research_recovery_vol_target_controller.log`;deadline3900s
with existing per-subject1h limit. This tests durable orchestration before full
batch,not portfolio completion or live alpha. Commit/merge remains pending.

2026-10-08 existing orchestrator batch1 STARTED (not completed):BTC/USDT4h,
trend_pullback/range_mean_reversion/volatility_breakout/vol_target/regime_ensemble.
Catalog grids validated against mapped canonical schemas:12/9/24/4/16parameter
sets respectively,zero invalid candidates. Execution uses audited frozen input,
one orchestration worker (child WFO uses2workers),cost all,holdout disabled.
Output `data/existing_research_batch1_20261008`;controlled result
`data/existing_research_audit_20261008/batch1_execution.json`;overall limit6h,
existing per-subject subprocess limit1h. Limits are not ETA or PASS criteria.
This is first bounded batch of existing process,not all50READY_INPUT subjects.
Remaining pairs/timeframes,blocked funding/universe,and final portfolio/policy
must be tracked separately; research completion never authorizes live.

Existing-process continuation2026-10-08: packet
`data/existing_research_audit_20261008/audit_manifest.json` freezes10OHLC inputs
(BTC/ETH/SOL/BNB/XRP x1h/4h) before exclusive cutoff2026-02-06T12:00UTC.
Timestamp gaps excluded by taking continuous tail, never imputed; byte hashes,
coverage and flags recorded. All10passed input audit. Baseline index now contains
156artifacts across5historical commit IDs,all report paths present; existence
is not current-source qualification or ledger reproduction.
Existing campaign now accepts --input-manifest and forwards per-subject verified
binding via child env without mutating global environment. Funding and missing
UNIVERSE bindings fail BLOCKED_DATA rather than loading raw/placeholder inputs.
--preflight-only completed:50single-asset combinations READY_INPUT and10funding
combinations BLOCKED_DATA; READY_INPUT means data binding only, not validated
parameter grid/history/strategy/runtime or native eligibility. Cross-asset still
needs actual universe binding.2focused testsPASS;Ruff/diffPASS.
No heavy full batch started. Next verify each catalog grid against actual CLI
grid/canonical schema,parameter-history and workload; audit funding/universe
input requirements,then run existing orchestrator in persisted bounded batches.

Existing-flow audit2026-10-08: `scripts/strategy_research/run_campaign.py` is
the8catalog-family orchestrator (6single-asset,2cross-asset), delegating to
`run_wfo_parallel.py`,then portfolio analysis. Separate tournament CLI defaults
to5strategy IDs x3cost scenarios; dry-run BTC found15requested cells all marked
completed in an index with155entries. These IDs/statuses alone do not establish
current source/data/parameter evidence validity. `research_pipeline_v2.py` is
a separate analysis orchestrator with audit/baseline/WFO/stats/cross-pair/
portfolio/report stages and explicit unimplemented report sections; it is not
interchangeable with the canonical WFO campaign.
Fixed old campaign's implicit `--run-holdout` removal and subprocess interpreter
to sys.executable. Fixed parallel WFO fold-planning loader to use the same
load_research_ohlcv frozen-input contract as canonical evaluator, not raw loader.
56data-binding/governance/stats testsPASS;Ruff/diff checksPASS. These do not
constitute a new full-market campaign or end-to-end acceptance of the wrappers.
Full rerun NOT STARTED: still needs per-subject pre-holdout data manifests,
existing baseline provenance audit,scenario-specific data (funding/multi-asset),
and strategy-specific budget/geometry verification. Reuse valid baseline first;
do not replace this existing process with another2MA-only experiment.

2026-10-08 root-cause follow-up: both campaign runners are stopped. Comparator
has6COMPLETED cached validation cells,11–23trades,Sharpe -3.1555..-1.2624,
return -4.3218%..-1.7031%; Enhanced MA6completed cells:3–16trades,Sharpe
-3.1516..-1.0154,return -3.4567%..-0.8861%. These are first-fold validation,
NOT outer OOS; neither provides qualified-policy evidence. Missing cells are
not measured losses. MA comparator final JSON is absent because controlled_exec
timeout returned before the result-write branch. That branch now persists
timeout status,logs,error,registry result path; reproduced with1s timeout and
stdout preservation. Old missing result is not fabricated/backfilled.
Tournament defaults300s/attempt plus2retries are consistent with902s FAILED
cells, but old failed artifacts were not persisted, so exact reasons cannot
be reconstructed from elapsed time alone. Campaign runner now persists failed
artifacts/identity separately (never resumable success cache) and includes
execution_control in timings;4runner testsPASS including failure persistence.

Research scope correction:17canonical strategy IDs exist, but this packet tests
only2MA-family candidates. It is NOT a library-wide tournament and cannot prove
the whole research system ineffective. Next contract must inventory each ID's
market/universe/history/parameter/runtime compatibility, partition single-asset
Spot candidates from funding/futures and multi-asset/long-short candidates,
then run bounded common-profile screening before nested qualification of a
predeclared shortlist. Never apply one MA grid to RSI/BBands/etc; never count
MA variants as independent strategy families. Keep trial burden and NO_TRADE.
Remaining engineering:strategy-scoped decision paths (current root filename
collides across subjects),explicit persisted failure verdicts/preflight runtime
budget,and measured profiling before any speed optimization or heavy rerun.

2026-10-08 monitoring update: Enhanced MA ended after2534.87s with rc1,
`NOT_QUALIFIED: fold_000 ABSTAIN; campaign incomplete`; no outer OOS completed.
6cached COMPLETED cells had3–16 validation trades and Sharpe -3.1516..-1.0154.
Cells1,2,4 were FAILED at902.08/902.13/902.04s; their metrics are invalid/missing,
not evidence of zero trades or a measured strategy loss. Investigate per-cell
execution timeout before any comparative ranking. Comparator was still running
at this check; final result must be read from comparator_execution.json.
Heartbeat automation `theo-d-i-research-btc-1h` monitors every10minutes and
continues evidence audit/engineering fixes after both processes stop; no source
edits during active campaign. Shared-root selection decision attribution/path
collision also needs audit before treating that file as strategy-specific proof.

2026-10-08 BTC1h research: extended input failed strict hourly continuity with
15 gaps before the unchanged cutoff2026-02-06T12:00UTC. Gap report retained in
`data/research_btc1h_20261008/extended_input_gap_report.json`; no synthetic fill.
The continuous tail after the last gap,2023-03-24T14:00UTC through2026-02-06T11:00,
passed closed/unique/sorted/hourly/finite/OHLC checks:25,198bars,byte SHA256
`45ba1b1bc6a91986eda445de8026cab9995fcabe011a735244a4c7cf0e0825ce`.
Selection of this span is data-quality based, not measured-return based.
Preflight PASS for Enhanced MA and MA crossover:6folds12/3/3months,9parameter
sets per fold,121warmup/purge/embargo,validation>=30trades/Sharpe>0,minOOS30.
Enhanced MA campaign STARTED with2workers,timeout3600s,no-publish; completion
must be established from `enhanced_execution.json`, not this STARTED line.
MA crossover comparator STARTED separately with1worker and the same profile,
timeout3600s,no-publish; result target `comparator_execution.json`.
Frozen Enhanced MA baseline costs verified:commission10bps,slippage5bps,
round-trip30bps,spread0 (not a separately simulated spread component).
Research results are not
live authority; holdout remained excluded and no broker/account IO occurred.

Research/runtime alignment update: Enhanced MA history now follows effective
slow/fast periods, nested ADX/ATR lookbacks and momentum rather than fixed100.
Focused history tests:15PASS; integrated AC01–AC15:15/15PASS (48.53s), no gate
threshold changes. These are engineering checks, not profitable-strategy evidence.
Campaign-plan/input-binding/admission/eligibility regression:81PASS (76.10s);
producer recheck after final fixture identity correction:18PASS (56.71s),
overlaps the81. Historical failed run had invalid parameter-name fixture and
outdated mock signature; fixtures corrected, production validation retained.
The live-readiness plan now specifies the validation→OOS→qualification→policy→
paper→live contract and explicit ABSTAIN/NO_TRADE outcomes.
Local timestamp-footer inventory found BTC1h_extended and1h_full:58,897bars,
2020-01-01 through2026-09-21; base1h:31,783bars from2023-01-01.
No price/holdout read in this inventory. Next research input must be explicitly
restricted to the existing pre-holdout cutoff before quality checks/freezing;
coverage metadata alone is not quality or qualification evidence.
Generic live routing and measured strategy selection remain OPEN; fixed
Enhanced MA runner has not been replaced or authorized to trade.

Operator reprioritized to **single-machine strategy/config readiness** with
existing crawled data; license/provider work deferred, no approval implied.
Next blocker is research/live alignment: MA crossover4h packet does not qualify
fixed enhanced_ma live runner; legacy WFO profile mintrades10/cost5+2bps also
differs from approved30/current10+5bps campaign. Live-readiness plan now tracks
enhanced_ma parameter-history audit, one-subject profile/parity, qualification,
paper execution and separately approved canary. No runtime config/evidence
threshold changed, no broker/account/execute activity. Do not persist a new
license code gate or declare live readiness from local run scope alone.

Production-license desk review completed2026-10-07: no source LICENSE_APPROVED.
CoinAPI is the leading conditional candidate for exact2018–2022 scope, pending
written Binance upstream rights, coverage and retention/model confirmation.
Tardis terms explicitly support internal quantitative trading, subject to
licensor restrictions, but published Binance history starts2019-03-30; it
requires an approved scope/pipeline change. Binance direct API rights remain
unresolved, not a bypass. Official references, provider questions and approval
exit criteria are in handoff. No signup/trial/key/purchase/contact/download.
Next requires owner provider/budget/use-case choice and, if desired, explicit
authorization to contact provider; public terms review is not license acquisition.

Backfill download approved in principle, but **PAUSED BEFORE ACQUISITION due
to newly discovered license-scope decision**. Binance Vision dataset terms
v1.0 (last updated2026-08-26), checked2026-10-07, distinguish personal
non-production research from production/live proprietary trading and require
separate enterprise licensing for commercial use. No ZIP/checksum downloaded,
no data license accepted on owner's behalf, no source changed. Owner must
choose research-only isolated usage or establish a suitable licensed source
before acquisition continues. See official terms URL in handoff; do not infer
that the repository MIT code license grants production dataset rights.

BTC4h source/scope proposal prepared: official Binance Spot monthly4h archive,
backfill2018–2022 (60 month ZIPs+checksums), merge only with preserved development
ending2026-02-06T12:00Z. No fetch or new campaign authorized/performed yet.
Raw local4h footer inspected without price-row load:7946 rows, starts2023,
ends2026-08-17; later raw portion must stay excluded. Evidence
`local_4h_metadata_result.json` in corrected-history packet. Handoff documents
allowlist/holdout denylist, units/checksum/gap/ZIP safety, immutable provenance,
coverage preregistration and separate execution approval. Exact monthly file
availability/terms still to check before acquisition. Awaiting download scope
approval; do not treat "ok to plan" as acquisition or live authority.

Owner agreed to **plan** coverage redesign while retaining30. Geometry matrix
has been measured without backtest/new data/holdout: validation3/6/12/18/30m
allows6/5/3/1/0 folds on existing6796 bars, train12/test3/step3, gaps121.
Evidence: corrected-history packet `coverage_feasibility_result.json`.
Preferred proposal: retain BTC4h hypothesis and request a permitted expanded
development history; alternative BTC1h is a new hypothesis requiring approved
real1h input and time-horizon conventions. No data download, new experiment,
contract change or holdout access authorized yet. Detailed C0–C5 protocol is
in handoff's coverage-redesign section; currently awaiting C1 choice/data scope.

Corrected-history qualification **STOPPED: INCOMPLETE / fold_000 ABSTAIN**,
423.16s. Nine inner cells completed; no OOS or final qualification bundle.
Disk audit PASS (`first_fold_verification_result.json`): current source, frozen
input bytes, complete grid coverage, report reconstruction, exact simulation
and saved decision agree. Validation trades3–5 per candidate, Sharpe1.2813–2.7967,
return+2.8677% to+7.5978%, maxDD4.3192%–6.5975%. All rejected solely for
insufficient trade count under the approved >=30 threshold. These are
validation metrics, NOT OOS performance; positive metrics do not imply alpha.
Warmup repair is exercised successfully; no persistent zero-signal defect in
this run. No policies/holdout/live activity. Historical RUNNING text below is
superseded by this result.

Next decision: retain approved trade threshold and redesign data/horizon or
strategy/timeframe with a preregistered new experiment; do not repeatedly run
this identical grid/geometry. Extrapolation from one fold (not a reliable
forecast): 30 trades at observed3–5 per3months needs roughly18–30 months of
validation. The present37.8-month input cannot supply the existing six-fold
12/18/3 geometry (roughly48 months plus gaps). No new data download or contract
relaxation has been authorized. A low-frequency statistical contract would
require separate approval and independent methodology review, not changing30
to3–5 merely because those are observed counts.

WFO regression completed: **196 PASS** (616.65s pytest, 617.68s wall),
`warmup_full_regression.json` preserved in MA history acceptance packet.
Final separately added span tests are covered by focused118 and CLI correction
by CLI19/independent19; do not sum overlapping groups or call this all-repo CI.
New corrected-history qualification **RUNNING** in
`data/campaign_qualification_ma_history_20261007/`, approved thresholds/grid
unchanged, warmup121/default purge121/embargo121, workers2, no-publish.
Source/data/history/cost/gates frozen before execution; no result yet.
Do not edit Python source or resume the older flawed packet.
Earlier RUNNING regression statements below are historical.

MA lookback repair is implemented and locally tested: effective params resolve
descriptor/adapter/signal history; WFO and CLI use max-grid history for simulation
and default gaps; selection plans freeze `history_contract`, consumer rejects
drift. Current focused118 PASS, CLI/history19 PASS, independent re-review19
PASS, AC01–AC15 15/15 PASS (52.26s), Ruff/diff-check PASS. Groups overlap.
Default and explicit-gap fold-span parity tests included. Full nested-WFO
regression RUNNING (`/tmp/warmup_full_regression.json`, controlled timeout900s).
Do not launch another qualification until this finishes and is inspected.
Logs preserved in `data/acceptance_runs/ma_history_20261007/`.
Only ma_crossover parameterized history is repaired; other strategies retain
their existing registered lookback and have not received a general history audit.
Old eligibility plans lacking history_contract now fail closed. Old campaign
fingerprints remain historical; do not rewrite them to match the new source.

Qualification attempt has **STOPPED: INCOMPLETE / fold_000 ABSTAIN**, 122.71s,
9 completed inner cells, no outer qualification. All candidates recorded
0 trades/Sharpe0. Root cause reproduced in packet `diagnose_warmup.py` and
`warmup_diagnosis_result.json`: ma_crossover descriptor warmup51 yields a
52-row signal window, insufficient for slow MA60/80/120. For fast10 with
each slow value, 138 validation bars produced zero signals at current window,
but two nonzero signals when given slow+1 warmup. This is an evaluator
lookback defect, not evidence of an intrinsically inactive strategy.

Next engineering work: parameter-aware registered lookback, shared resolution
across descriptor/adapter/signal generation/WFO simulation and consumer proofs;
regressions covering slow periods above defaults and prefix-causality. Freeze
the resulting resolved warmup in plan/artifact context, then run integrated
AC01–AC15 and independent review before a new experiment ID. Preserve this
failed attempt and trial burden; do not overwrite/resume it under changed
source or change eligibility thresholds. No live/publication authorization.
The RUNNING paragraph below describes the launch, not current status.

Owner approved the proposed thresholds via "tiếp tục" on 2026-10-07.
Development qualification **RUNNING**, packet `data/campaign_qualification_20261007/`:
six 12/3/3-month folds, registered 9-combination grid, validation >=30 trades
and Sharpe >0, min OOS trades30, workers2, no-publish. Actual plan and source/
data/cost/native contracts are persisted before cells. Controlled timeout7200s,
heartbeat30s, result-file `run_result.json`. No qualification result yet.
Do not edit Python source while this run is active. The earlier draft preflight
paragraph below is historical, not an outstanding approval request.

Next experiment remains **DRAFT awaiting owner approval**. Dry-run on
2026-10-07 passed in 22.92s: BTC/USDT 4h, six 12/3/3-month folds,
grid fast=[10,20,30], slow=[60,80,120], 54 inner + 6 outer cells plus
sensitivity. Proposed eligibility >=30 validation trades and Sharpe >0,
explicit minimum OOS trades 30, workers2, no-publish. No backtest executed
by dry-run and no persistent frozen plan written. This verifies geometrical
feasibility only, not trade sufficiency, statistical validity or qualification.
Protocol and approval boundaries are in the handoff's qualification section.
Preflight log preserved in the current diagnostic packet as
`qualification_preflight_20261007.json`. Do not reuse the printed temporary
dry-run fingerprint as an execution plan; freeze again after approval.

Current-source engineering rerun completed in **95.01s** under
`data/campaign_frozen_selection_diagnostic_20261007/`. Preserved pre-holdout
BTC/USDT 4h, ma_crossover 15/20, one fold, one inner cell, no-publish.
Disk-bound verifier passed: current source capture equals frozen source,
schema-v2, frozen costs, timestamp indices, exact warmup and rebuilt selection
agree. Validation: 8 trades, Sharpe 1.579139. OOS: 5 trades, return +0.288308%,
Sharpe 0.931486. Qualification **FAIL: 14 failed/invalid gates**; CLI exit 1
is expected NOT_QUALIFIED, not a simulation crash. No policy published.
Logs: packet `run_result.json` and `verification_result.json`; scope declared
before run in `SCOPE.md`. Reused known data is not fresh statistical evidence.

Next: approve a production eligibility contract and preregister a sufficiently
broad qualification experiment before any new data/holdout use. Do not enlarge
the grid opportunistically or relax native thresholds to rescue this FAIL.
Current result lacks trade/fold coverage, PBO/stability/neighbor evidence, and
fails doubled-cost and best-trade-removal robustness. Live remains NO-GO.

Independent review found two P1 provenance gaps and one P2 path gap in the
prior TESTED snapshot. Validation now binds train/validation/OOS indices to
timestamps in the frozen input, compares named cost scenarios and exact rates
against the frozen plan, and refuses escaped or path-like plan/decision paths.
The CLI freezes validation costs explicitly; older eligibility plans without
that binding fail closed. Synthetic fixtures corrected inclusive timestamp
span ends to match exclusive WFO end indices; qualification thresholds did
not change.

Re-review confirmed those original findings addressed (108 independent tests
PASS), then requested exact simulation warmup geometry. Consumer now derives
the expected simulation start from frozen train start, registered strategy
warmup and the WFO 100-bar buffer convention. Same-validation rehashed reports
with a substituted simulation start are rejected. Final regression: **136 PASS**
(18.21s); independent final re-review: **109 PASS** (4.62s), all four findings
closed in the inspected scope. Ruff and diff-check PASS. These overlapping
counts must not be summed. Logs preserved in the identity diagnostic packet:
`frozen_selection_final.json`, `independent_final_selection_pytest.json`,
`frozen_selection_quality.json`, `frozen_selection_diff.json`.
Trusted current source capture remains a caller obligation; this targeted
clearance does not authenticate arbitrary caller-provided source metadata.
AC01–AC15 passed **15/15 on the final code** (37.61s), log
`frozen_selection_ac_final.json`; it is not a
new campaign or proof of strategy qualification. Native qualification remains
FAIL and publish/live remain locked. No fresh source-frozen campaign was run.


### Latest: revision and data-quality identity pinning

Validation artifact revision must now equal the campaign source commit; its
data-quality fingerprint must equal the actual outer artifact's valid fingerprint.
Missing outer identity is refused, rather than disabling the check. Rehashed
commit/fingerprint substitutions are covered by negative tests. These are metadata
consistency checks, NOT a claim that the quality fingerprint hashes all price
bytes: tournament quality identity and frozen parquet file SHA256 are distinct.

Current focused checks: **117 tests PASS**, AC01–AC15 **15/15 PASS** (38.23s),
Ruff PASS. No new backtest campaign was executed in this pinning continuation.
`verify_provenance_compatibility.py` in the identity diagnostic packet revalidates
the historical report with the new checks, explicitly reporting
`historical_packet=true`, `current_source_matches_run=false`, `fresh_campaign=false`.
It cannot be substituted for a new source-frozen campaign or live approval.
Logs `provenance_final_regression.json`, `provenance_final_ac.json` and
`provenance_compatibility_result.json` are preserved in that packet.
Independent review/production-contract approval remains pending; publish/live
locks and all native qualification thresholds are unchanged.

### Earlier: validation artifact identity hardening

`selection_eligibility.py` now checks cell ID, exact cost scenario/commission/
slippage, normal fault profile, zero validation signal delay, and simulation
start/end confined to the declared validation boundary. Rehashed forged artifacts
are refused by semantic checks, not just by their content hash. Unavailable,
invalid-JSON or non-mapping report sources are refused with ValueError.

Current focused regression: **114 tests PASS**, AC01–AC15 **15/15 PASS** (45.96s),
Ruff PASS. These counts overlap earlier groups; do not sum them. The prior
127-test slow WFO run is historical, before this additional identity check.
Fresh engineering diagnostic uses the same explicitly synthetic-policy thresholds
and previously inspected data/candidate in
`data/campaign_eligibility_identity_diagnostic_20261006/`: completed **102.48s**.
Disk verification on current source rebuilt SELECT and confirmed source/plan/
schema binding; validation 8 trades/Sharpe 1.579139, OOS +0.288308%/Sharpe
0.931486/5 trades. Qualification **FAIL, 14 gates**, no policy published.
Controller, regression, AC and verifier logs are preserved in this packet.
Earlier SELECT/staging packet verifiers require their own historical source
snapshots and must not be presented as current-source acceptance.
Publish/live stays locked; independent review and production-contract approval
remain distinct from local TESTED evidence.

### Earlier local engineering evidence

Latest continuation: zero-closed-trade windows are measured explicitly and
must reconcile, not treated as absent evidence. Remaining inventory opened
before OOS is marked at the opening boundary with original measured basis
retained and revalidated; pre-OOS fees are not charged again. Covered cases
include long/short accounting identities, tampered proof rejection, open-only
producer rows, and a partial exit plus its remaining inventory. These are
scoped paper-accounting proofs, not certification of all live short/scale-in flows.

The frozen 3×3 grid (fast `[5,10,15]`, slow `[20,40,60]`) ran on the same
BTC/USDT 4h pre-holdout input with two folds and two workers, **no-publish**.
All 18 inner and 2 outer cells completed; sensitivity also executed.
Wall time **444.56s**; sampled peak process-tree RSS **1,097,363,456 bytes**
(about **1.02 GiB**, shared pages double-counted; not exact PSS).
Native contract was unchanged and written before cells. Bundle revalidated
from disk against preserved input, plan and native contract; **FAIL, 16 gates**.

| Measurement | One-candidate pilot | Frozen nine-candidate grid |
| --- | ---: | ---: |
| Median OOS return | −0.120844% | +0.144154% |
| Median OOS Sharpe | −1.249501 | 0.465743 |
| Closed trades | 13 | 5 |
| Failed gates | 18 | 16 |
| PBO | unavailable | 0.485714 (limit 0.20) |

Selected params came only from inner validation: fold 0 selected `15/20`;
fold 1 selected `5/60` with zero validation trades/Sharpe 0. Under the existing
score-ranking contract this is a research winner, NOT a demonstrated trading
edge; it remains blocked by native qualification. Do not silently reinterpret
it as an eligible live strategy or force selection of a losing active candidate.
Any eligibility/ABSTAIN rule change must be preregistered in a separate frozen
selection contract and tested, not tuned after seeing these OOS results.

Evidence: `data/campaign_grid9_20261006/verification/`, plus
`resource_profile.json`. Profiler uses `/proc`, needs no dependency installation.
Current regression: **145 passed**; AC01–AC15 **15/15 PASS**. No policy or exchange
order was published/sent. Real PASS publication and independent acceptance
remain pending; expanding the grid alone did not establish a robust strategy.

The historical table below is not a claim that the current dirty worktree has
been independently accepted. New local fixes are **TESTED**, not ACCEPTED:

- Simulator equity includes inventory already. Carry reconciliation now records
  `marked_equity` explicitly instead of adding the inventory mark twice as cash.
- Closed trades spanning the OOS boundary retain original measured-fill proof;
  attribution validates that proof before removing pre-OOS entry costs.
- Schema-v2 producer preserves the open leg, proves mark/unrealized/total PnL,
  and derives `cost_cleared` from total PnL. Closed profit cannot hide open loss.
- Publication with carried inventory is exercised through the real schema,
  ledger reconstruction, consumer and atomic writer in a **synthetic** fixture;
  this is not real strategy qualification.

Regression: **140 passed** across campaign/accounting/consumer/native contract.
AC01–AC15: **15/15 PASS** on this local source state. The real BTC/USDT 4h pilot
(6,796 pre-holdout bars, one candidate, two folds, 2m/1m/1m, two workers) completed
in **221.79s**. It produced a schema-valid, disk-revalidated bundle with its
pre-run plan and native contract intact. Median OOS return **−0.120844%**,
median Sharpe **−1.249501**, **13 trades**, **18 failed gates**; verdict **FAIL**,
no policy published. It no longer stops at the missing-carry producer error.

Durable local evidence: `data/campaign_pilot_carry_recheck_20261006/verification/`.
Input bytes are copied there so validation does not depend on `/tmp` surviving.
Run `PYTHONPATH=src .venv/bin/python
data/campaign_pilot_carry_recheck_20261006/verification/verify_bundle.py` from the
workspace (on one command line).

### What remains before acceptance/live

1. The preregistered nine-candidate grid is complete, but PBO 0.485714 and
   negative parameter-neighbor stress did not establish robustness. Do not
   repeat the same work or expand the grid solely to obtain a PASS.
2. Do not change gates to rescue negative OOS, low trade count or failed 2x-cost
   stress. A larger run is research, not guaranteed qualification.
3. Boundary/empty-ledger proofs now cover the scoped single-position paper
   cases above. Incomplete fill basis and unexplained equity remain refused.
   Complex scale-in/multi-position/live-short flows are not certified by this
   continuation; do not generalise these accounting tests to live execution.
4. Real PASS publication and independent review on an integrated revision are
   still pending. AC PASS and synthetic publication do not substitute for them.
5. Testnet/soak remains operator-dependent; no external orders were authorised
   or sent in this continuation. Mainnet remains NO-GO.

### Next work package: eligibility before more research

Current decision is **ABSTAIN / NOT_QUALIFIED**, not a broken campaign runner.
The saved verifier prints the bundle SHA256, failed gate observations/limits,
and a diagnostic decision; even a future PASS is only REVIEW_REQUIRED and
never live authorization. Validation errors stop the verifier before a decision.

Before changing selection, freeze a separate eligibility specification:

- Separate candidate activity from score ranking. Sharpe 0 alone does not prove
  zero trades; activity must come from the candidate's measured validation report.
- Define minimum observations, invalid/missing metric handling, tie handling and
  the no-eligible-candidate outcome using inner validation only, before new OOS.
- Preserve NO_TRADE/ABSTAIN when no candidate qualifies; never replace it with
  an active losing candidate merely to increase trade count.
- Test zero-trade, all-invalid, all-ineligible, ties, deterministic replay and
  validation/OOS isolation. A change to WFO or promotion requires AC01–AC15.
- Freeze source/data/selection contract/budget for a new campaign. The existing
  grid is diagnostic evidence, not an untouched confirmation sample for a
  rule inspired by its results. Keep the holdout unopened.

Completion evidence: contract fingerprint, fixtures and named test results,
per-candidate activity/rejection reasons, selected candidate or explicit ABSTAIN,
and independently reviewed evidence on one integrated revision. This work package
is not yet integrated or accepted; the current native gates are unchanged.

Local continuation: `selection_eligibility.py` implements a separate, fingerprinted
contract and deterministic candidate assessment. Thresholds are required inputs,
not defaults inferred from this grid. Zero/missing/invalid activity or insufficient
validation evidence rejects a candidate; no eligible candidate produces ABSTAIN.
Ranking uses validation Sharpe only, with canonical params/cost tie-breaking.
The module cannot authorize live execution. WFO and the real-campaign CLI now
accept the optional contract; legacy runs without it retain the old ranking.
Its tests use synthetic thresholds, not an approved production selection policy.
Local checks on 2026-10-06: **32 passed** across selection eligibility and native
contract suites, Ruff PASS, AC01–AC15 **15/15 PASS** (43.95s). Controller results:
`/tmp/eligibility_final_tests.json`, `/tmp/eligibility_final_quality.json`,
`/tmp/eligibility_ac01_15.json` (temporary local logs, not durable acceptance).

Pre-run plan binding and persisted per-fold candidate/rejection decisions are
implemented. Specify BOTH `--min-validation-trades N` and
`--min-validation-sharpe S` using preregistered thresholds and a fresh output root.
WFO persists `selection_contract.json` before cells; changed/omitted contracts on
resume are refused. A fold with no eligible candidate writes
`selection_decision_<fold>.json` and stops the entire campaign as NOT_QUALIFIED,
before that fold's outer OOS: no incomplete aggregate or publishable PASS is built.
This conservative fail-stop is not a simulated cash/ABSTAIN OOS return.
The CLI requires `--no-publish` when eligibility is enabled pending independent
acceptance and approval of a production contract; optional integration is research-only.
Focused integration verification: **50 tests PASS**, Ruff PASS; mandatory
AC01–AC15 **15/15 PASS** in 46.64s (`/tmp/eligibility_wiring_ac.json`).
The publication/paired-threshold CLI guards have **2 additional tests PASS**;
the existing CLI suite separately passed 9 tests. The broader five-file WFO
regression hit its 180s controller timeout after progress reached 62% plus
20 further dots. It is **incomplete, not PASS**; the 50-test focused run above
is the completed integration check, not a substitute for the full slow suite.

Consumer attribution is now implemented for the optional contract: bundle/actual
result/plan contracts must agree; all declared grid candidates and folds must be
present; report hashes/windows/subject/params must match and the tournament
artifact must reconstruct from each validation report. Selection is recomputed
and compared to the saved decision and the actual outer parameters. Missing or
failed source evidence refuses publication rather than silently removing trials.
Local verification: **81 tests PASS** across eligibility, consumer binding, plan,
real-schema synthetic publication and fail-stop ABSTAIN; AC01–AC15 **15/15 PASS**
in 42.77s. The artifact reconstruction branch unit test stubs the rebuilder;
the engineering SELECT campaign below must separately exercise the real rebuilder.

The frozen SELECT engineering diagnostic completed in **158.32s**. Disk verifier
ran the **real** report/artifact rebuilder for fold_000 and confirmed source,
plan and schema bindings. Verdict **FAIL, 14 gates**, no policy published.
Rerun `PYTHONPATH=src .venv/bin/python
data/campaign_eligibility_select_diagnostic_20261006/verify_selection.py` from the
workspace (one command line). The preserved input remains under the earlier
pilot verification directory; deleting that input invalidates verification.

The slow five-file WFO regression subsequently completed: **127 tests PASS**,
500.64s pytest time / 501.68s controlled wall time. This supersedes the historical
180s timeout above; it is not the entire repository's test suite. Durable result:
`data/campaign_eligibility_select_diagnostic_20261006/selection_full_wfo_regression.json`.
The 81-test focused group overlaps this group; do not add counts together.

Research-mode eligibility integration is locally **TESTED**, not independently
ACCEPTED. Atomic staging now accepts an explicit selection-evidence source root
under the declared publication root, rather than looking for the source reports
inside the temporary bundle directory. Five synthetic tests exercise the real
rebuilder/consumer/writer: valid + idempotent, report/decision tampering, missing
candidate coverage and an escaped source root. The source packet must remain
available; this is not a self-contained archival bundle or a signature service.
Current post-staging checks: **102 tests PASS**, AC01–AC15 **15/15 PASS** (37.31s),
Ruff PASS. The 127-test slow WFO run above preceded this staging-only change;
WFO selection code was unchanged, and counts from overlapping groups are not summed.
Fresh source-bound diagnostic is under
`data/campaign_eligibility_staging_diagnostic_20261006/`: **94.69s**, source/plan/
schema match; the real rebuilder revalidated SELECT (8 validation trades,
Sharpe 1.579139), outer OOS +0.288308% / Sharpe 0.931486 / 5 trades.
Qualification **FAIL, 14 gates**; no policy published. Its verifier and copied
AC/regression/controller logs are durable inside that directory. The earlier
SELECT packet is historical for the pre-staging source snapshot and its source
check intentionally no longer passes against current code. Timing differences
between these diagnostics are not a controlled performance benchmark.
Before changing deployment eligibility: independently review the integrated path
and approve a production selection contract. Publication remains locked;
native qualification remains FAIL. No further grid expansion is implied by these
engineering checks, and no missing approval is substituted with fixture thresholds.
Diagnostic scope lives in
`data/campaign_eligibility_select_diagnostic_20261006/SCOPE.md`: reused data and
candidate, one fold, fixture thresholds 1 validation trade/Sharpe > 0. These
thresholds are NOT a production selection-policy approval or fresh edge evidence.
If a future design continues across ABSTAIN folds, it must define explicit cash
returns/coverage and keep those folds in every gate denominator. Do not claim
the previous grid tests this new source.
The old grid verifier deliberately rejects a current-source mismatch after this
module was added. Its historical diagnostic results remain valid for its snapshot.

## How to read the status column

Three levels, deliberately not collapsed:

| Level | Means | Evidence required |
|---|---|---|
| **IMPLEMENTED** | Code exists and is committed | commit sha |
| **TESTED** | A named suite was run and passed, at a stated count | suite name + count + date |
| **ACCEPTED** | Independent evidence exists that the thing does what it claims | acceptance run, published bundle, or review sign-off |

A `- [x]` in an old TODO meant all three at once, which is how "implemented"
was previously reported as "verified". Nothing here is ACCEPTED unless it
says so.

## Status

| # | Item | Status | Commit | Test evidence | Acceptance evidence |
|---|---|---|---|---|---|
| 1 | Measured cost per fill (spread / slippage / impact / commission) | **TESTED** | `bb8c795` | `tests/test_fill_cost_components.py` 10 pass; 37 pass across simulator group | none |
| 2 | Partial fill + carry + open inventory accounting | **TESTED** | `70ecb51` | `tests/test_open_inventory_accounting.py` 13 pass; 50 pass across accounting group | none |
| 3 | Carried inventory priced in campaign evidence | **TESTED** | `f1173e6` | `tests/test_campaign_open_inventory.py` 7 pass; 523 pass across campaign/accounting/tournament/holdout group | none |
| 4 | Accounting regression snapshot + seed fix | **TESTED** | `31cee3e` | `tests/test_accounting_regression_snapshot.py` 6 pass; 63 pass across simulator group | none |
| 5 | Frozen campaign plan + native bundle producer | **TESTED** | `d357347` | `tests/test_campaign_plan_producer.py` + `tests/test_run_real_wfo_campaign_script.py` 20 pass; 27 pass | none |
| 6 | Real campaign executed through producer → validator → publication | **TESTED** | `3511b40` | BTC/USDT 4h `ma_crossover` pilot ran end-to-end (2 workers, 2 folds); gate refused carry as a valid pilot output; real publish left to full run | gate refusal accepted as valid; see carry fix below |
| 7 | AC01–AC15 acceptance suite | **ACCEPTED** | `d357347` | see below | `data/acceptance_runs/ac01_15_20261005_082339_utc/` |
| 8 | Independent review | not started | — | — | — |
| 9 | Testnet / soak | **BLOCKED on operator** | — | — | requires `LIVE_ACCOUNT_ID` |

## AC01–AC15 acceptance run

Run: `data/acceptance_runs/ac01_15_20261005_082339_utc/`
Revision under test: `d35734702a309d088995aa6e6b9258ecb1bcc6c0`

**15/15 PASS (rc=0)** — AC01 through AC15, each with its own log and evidence
JSON in `current/`, plus `previous/` retained for comparison and a
`SUMMARY.txt` naming the revision. AC01 is re-run last as a regression after
AC02–AC15, and passed there too.

This is the acceptance evidence for items 1–5: the run happened at the commit
that carries the fill-cost split, the open-inventory contract, the campaign
evidence changes, the regression snapshot and the new producer.

## Full-suite baseline

Last full run: **2083 pass, 3 skipped, 7 failed**.

All 7 failures also fail on a clean tree (`HEAD`, worktree stashed) and are
not caused by this work:

- `tests/test_r04_scope_lock_campaign.py` — 4 failures, strategy catalog scope
  (`allows_strategy('rsi')` is False). The other three in that file cascade
  from the same scope fixture.
- `tests/test_short_side_backtest.py` — 3 failures, short-side entry/exit.

Verified by stashing the working tree and re-running each file: identical
failure counts.

## Known defects not addressed

- **mypy, `execution/backtest_sim/models.py`** — 5 errors at `HEAD`, all in
  `_process_limit_order`, where `order.price` is `float | None` and is
  compared and multiplied without a guard. Pre-existing. Fixing it requires
  deciding what a limit order with no price means, which is a modelling
  decision, not a typing fix.
- **mypy, `backtest/campaign_evidence.py`** — 4 errors at `HEAD`
  (`finite(object)`, `dict | None` access). Pre-existing, count unchanged.
- **`LIVE_ACCOUNT_ID`** unset. `docs/operations/LIVE_READINESS_AGENT_PLAN.md`
  requires the operator-verified stable venue account UID, explicitly not the
  API key or a run id. The live runner refuses even a dry run without it.

## Definition of done for this line of work

1. A campaign runs end to end on local data through the new chain.
2. A schema-v2 bundle is published atomically and re-validates from disk.
3. AC01–AC15 pass with the run recorded.
4. An independent review reads the bundle against its frozen plan.
5. Only then: operator-authorised testnet/soak.

## Calibration v2 — COMPLETED — 2026-10-14
Push master: 08a9d3f -> 0951da3 -> dbb9713
Packets: 10-cell ACCEPTED + 2-cell pilot ACCEPTED
Verifier bundle: data/calibration_review_package_20261014.json (self-generated fingerprint/verifier summary, not an independent review — reviewer_id is "independent_review_package" with no actual independent reviewer sign-off)










