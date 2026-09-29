# AC Evidence Tracker — 2026-09-25

## Latest integrated evidence and acceptance decision

- Run: `data/acceptance_runs/ac01_15_20260925_114252_utc/`
- Base revision: `e78ff4bb5d33aa903596ea3cf0b50e010a72426b`; tested state includes the exact uncommitted diff SHA-256 bound in `manifest.json`.
- AC01–AC15 runner result: all PASS; AC01 is 7/7. Supplemental suites: 69/69 and 318/318 pass without failures, skips, or warnings.
- Independent reviewer gave **bounded ACCEPT** for AC01–AC05 and AC07–AC10; **PARTIAL** for AC06 and AC11–AC13; **BLOCKED** for AC14–AC15.
- **Update 2026-09-28 (revision `f4e2a91`)**: investigating AC14 on real data surfaced a defect that invalidates part of the above. The promotion pipeline never required scores to come from a measurement: 4,516 of 4,520 promotable policies carried a `code_sha` that matches no source in the repository, and their `median_oos_return_pct` took one of three literal values (0.02 / 0.05 / 0.10) with `selection_score` a pure function of trade count. Every downstream result — router bindings, incumbent selection, abstain behaviour — was selected from those artifacts. 43 policy stores are quarantined; the live store is empty.
- Final acceptance: **NOT ACHIEVED**, and the gap is now larger than the 2026-09-25 review recorded. AC15 still needs operational soak/recovery/rollback evidence. AC14 is blocked upstream of that: an OOS campaign needs a policy to evaluate, and no promotable policy in the repository is attributable to code that exists. Local simulator tests do not substitute for these gates.
- Scope: no mainnet authorization, no claim of profitability, and no completed C01–C10 integrated execution acceptance.

| AC | Evidence | Result | Status |
|---|---|---|---|
| AC01 | Prefix contract; integrated manifest + independent review | 7/7; frozen-fit GMM future mutation and batch/stream parity included | ACCEPT — bounded tested strategy/path scope only |
| AC02 | Effective params/trial identity | Canonical trial identity/dedup, per-fold/per-cost accounting, serial/parallel parity; independently reviewed | ACCEPT — bounded WFO identity/cell/accounting scope |
| AC03 | Nested WFO/holdout lifecycle | Spawned process race, restart/reload, tamper and freeze tests | ACCEPT — bounded holdout non-reuse control |
| AC04 | Evidence completeness/resume | Missing/tampered artifacts, foreign-commit freeze, wrong-commit resume, promotion block | ACCEPT — bounded local WFO provenance/consumer gate |
| AC05 | Statistics/cost correctness | Cost oracle/scenarios, serial/parallel parity and zero-volatility CSCV regression; independently reviewed, no warnings | ACCEPT — bounded metric/statistical scope. **Superseded in practice 2026-09-28**: the cost model is correct, but no policy in the store carried an edge to apply it to (see AC14) |
| AC06 | Policy-to-runner enforcement | Public missing-policy paper cycle proves zero broker submit; other resolver negatives covered | PARTIAL — wrong-pair and bad-lineage no-submit cases remain |
| AC07 | Adaptive router | Runner + supplemental lifecycle tests | ACCEPT — router-contract scope only |
| AC08 | Instrument/permission rules | Runner + local spot/paper registry and shadow no-submit tests | ACCEPT — local spot/paper scope only |
| AC09 | Fill/ledger accounting | Runner + local paper/simulator reconciliation | ACCEPT — simulator scope only |
| AC10 | Cancel/recovery | Runner + local crash/reconcile/cancel race tests | ACCEPT — bounded local behavior; no exactly-once transport claim |
| AC11 | Shared capital across pairs | Runner PASS; pending→partial→cancel/correlation stress sequence missing | PARTIAL — not accepted |
| AC12 | Protection/telemetry | Runner PASS; stop rejection, stale fallback and telemetry-fault safety proofs incomplete | PARTIAL — not accepted |
| AC13 | Approval/promotion | Runner PASS; revoked/stale/future/replay signatures tied to actual promotion consumer/key identity missing | PARTIAL — **a worse defect was found 2026-09-28**: promotion never verified that scores came from a measurement. 4,516 of 4,520 promotable policies carried a `code_sha` matching no source on disk, and their scores were three literal constants. Gates added in `8870e03`; the prior 43 stores are quarantined and the live store is empty |
| AC14 | Locked out-of-sample research | 12/12 synthetic checks; negative synthetic comparison is not efficacy evidence | BLOCKED — **blocking condition changed 2026-09-28**. It is no longer "an OOS campaign is required": no promotable policy in the repository is attributable to code that exists, so there was nothing to run a campaign against. A real campaign on the best-scoring strategy has since been run (104 cells, `ENHANCED_MA_MEASUREMENTS.md §3`): 10 clear their own cost and half of those sit in one test window, so the apparent edge is a window artefact. See `HANDOVER_2026_09_28.md` |
| AC15 | Replay + operational readiness | Deterministic/local shadow checks pass; no operational soak/recovery/rollback | BLOCKED — operational evidence required |

## Evidence scripts (untracked)
- `scripts/evidence_ac02.py` — AC02: effective params + identity (independent oracle, 13 checks)
- `scripts/evidence_ac03.py` — AC03: freeze/holdout manifest integrity (13 checks)
- `scripts/evidence_ac04.py` — AC04: provenance completeness + resume guard (9 checks)
- `scripts/evidence_ac05.py` — AC05: statistics/cost correctness (15 checks)
- `scripts/evidence_ac06.py` — AC06: policy→runner binding (8 checks)
- `scripts/evidence_ac07.py` — AC07: router atomic claim (8 checks)
- `scripts/evidence_ac08.py` — AC08: permission/instrument control (12 checks)
- `scripts/evidence_ac09.py` — AC09: fill-ledger reconciliation (12 checks)
- `scripts/evidence_ac10.py` — AC10: order lifecycle cancel/recovery (6 checks)
- `scripts/evidence_ac11.py` — AC11: shared capital allocator (9 checks)
- `scripts/evidence_ac12.py` — AC12: protection/telemetry recovery (11 checks)
- `scripts/evidence_ac13.py` — AC13: approval consumer (8 checks)
- `scripts/evidence_ac15.py` — AC15: deterministic replay (11 checks)

## Evidence files
- `/tmp/ac01_evidence.json` — AC01: prefix contract (6 tests)
- `/tmp/ac02_evidence.json` — AC02: effective params + identity (13 checks)
- `/tmp/ac03_evidence.json` — AC03: manifest integrity + bar mapping (13 checks)
- `/tmp/ac04_evidence.json` — AC04: serialize/reload + tamper detection (9 checks)
- `/tmp/ac05_evidence.json` — AC05: statistics/cost correctness (15 checks)
- `/tmp/ac06_evidence.json` — AC06: policy→runner binding (8 checks)
- `/tmp/ac07_evidence.json` — AC07: router atomic claim (8 checks)
- `/tmp/ac08_evidence.json` — AC08: permission gate (12 checks)
- `/tmp/ac09_evidence.json` — AC09: fill-ledger reconciliation (12 checks)
- `/tmp/ac10_evidence.json` — AC10: cancel/recovery (6 checks)
- `/tmp/ac11_evidence.json` — AC11: shared capital (9 checks)
- `/tmp/ac12_evidence.json` — AC12: protection/telemetry (11 checks)
- `/tmp/ac13_evidence.json` — AC13: approval consumer (8 checks)
- `/tmp/ac15_evidence.json` — AC15: deterministic replay (11 checks)
- `/tmp/ac14_evidence.json` — AC14: adaptive comparison (12 checks)

## Status summary
- Hash-indexed artifacts were independently reviewed; the review did **not** approve the full AC set.
- The exact per-AC bounded verdicts and remaining criteria are in the integrated run's `manifest.json` and the table above.
- Do not mark the overall project or release as finally accepted until all PARTIAL and BLOCKED rows are closed on one integrated revision.
- **2026-09-28**: results measured on real BTC/USDT data through the promoted-policy pipeline are recorded in `HANDOVER_2026_09_28.md`. They are not a substitute for a locked OOS campaign, and the system currently has no promotable policy to run one against.

## Next priority (per contract §9.5 + §9.7)
1. ~~Establish whether the WFO pipeline is deterministic.~~ **Done** — `check_wfo_determinism.py` ran one spec twice; every measurement matched exactly and the four differences were provenance (`worktree_dirty`, and the manifest fields derived from it), not arithmetic. The 14x spread between on-disk campaigns came from the `a65ed29000` bugs and differing configurations, not non-determinism. Not verified on a run that actually trades — that spec produced zero trades — so the trading path is exercised but unconfirmed. See `HANDOVER_2026_09_28.md`.
2. **Re-measure one strategy on the fixed pipeline**, not the registry. `enhanced_ma` on SOL is the cheapest test that also produces the first citable post-fix number. Workstream B (S3 real nested WFO campaign) is subsumed by this; a full registry re-measurement would cost roughly 170 hours to learn the same thing and is explicitly not recommended.
3. **AC15** stays blocked until a cluster exists for operational soak/recovery/rollback evidence.
4. Re-run AC14 only after steps 1 and 2, on data that reproduces.

### Completed since 2026-09-25
- Promotion now requires measured OOS metrics and scores attributable to canonical source (`19ec13c`, `b872c5f`, `8870e03`).
- Edge must clear the cost floor and be spread across folds; selection score is net-of-cost, not Sharpe; the spread gate is a binomial test (`eeea331`, `98a0658`).
- Campaign coverage invariant: a run must measure what it claimed (`54906f6`).
- 43 policy stores quarantined — 2,690 promoted policies carried fabricated scores; only 4 of 4,520 promotable policies are attributable to code that exists (`f4e2a91`).
- E2E full flow 9/9, 86 assertions, with scenario 3 asserting fail-closed abstention (`9ba654d`).
- **Integrated AC01–AC15 run 15/15 PASS on the current pipeline** (`ac01_15_20260929_154822_utc`, revision `0022dc4`). Reaching that required fixing the three evidence scripts that were themselves writing fabricated policies: AC06, AC13 and AC14 all used placeholder `code_sha` values and score dicts with no OOS metrics, so they were refused by the gate added in `8870e03` and `eeea331`. A fixture that violates the rule it is meant to exercise tests nothing.
- Campaign irreproducibility traced to three WFO pipeline bugs fixed at `a65ed29000`; 4,527 cells predate the fix and the sign-off's only approval rests on them (`CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`, `EVIDENCE_BASE_AUDIT.md`).

Full account: `HANDOVER_2026_09_28.md`.
