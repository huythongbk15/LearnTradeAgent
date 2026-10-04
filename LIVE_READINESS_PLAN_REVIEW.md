# Review of LIVE_READINESS_AGENT_PLAN and the live runner

Assessment requested against the two new documents and
`scripts/live_enhanced_ma_binance.py`. Baseline claims were verified rather
than read: B02, B05 and B06 reproduce; one test failure that the plan does
not yet list was found and is in L0.4's scope.

---

## The documents are a real improvement

Two things about `LIVE_READINESS_AGENT_PLAN.md` are worth saying plainly
before the criticism, because they are not common in plans:

**It refuses to widen its own conclusions.** The scope line says
*"Không mặc định Enhanced MA là winner"*, and later *"Không được dùng canary
phạm vi nhỏ để tuyên bố toàn bộ adaptive project đã nghiệm thu."* A
five-pair Binance Spot plan that could report "live" and have it read as
project sign-off is the failure mode this blocks.

**It pre-commits to conflict resolution.** *"Nếu các nguồn mâu thuẫn: giữ
NO-GO, ghi decision record và yêu cầu owner giải quyết; không chọn tài liệu
có ngưỡng dễ hơn."* That is the correct default when documents disagree,
and it is written down rather than left to whoever notices first.

The permission table (§3), decision-record requirement before L2/L3 (§3),
and the evidence-bundle contract (§7) are the parts most plans omit and
most regrets come from.

**L0 before L2 before L4 is the right order.** Building the execution path,
then the measurement contract, then proving one policy, then operations, then
canary — with each level gated on evidence rather than completion — is the
correct dependency graph for this problem.

---

## Verified baseline claims

| ID | claim | verified |
|---|---|---|
| B02 | legacy runner tests skipped | **confirmed** — 3 skipped in `test_binance_testnet_acceptance.py`, all `LIVE_TESTNET_ACCEPTANCE` opt-in |
| B05 | promotion store empty at baseline | **confirmed** — `data/promotion_store/` has 0 entries |
| B05 | no published `live_strategy_evidence.json` | **confirmed** — only `.rejected.json` exists |
| B06 | payoff script break-even constant wrong | **confirmed** — `BREAK_EVEN_RATIO = 0.71` is the hardcoded 1 − 0.707, using the per-trade hit rate that §L1.1 correctly identifies as wrong |
| B06 | payoff script lacks per-trade records | **confirmed** — campaign artifacts carry no trade rows; the script reports this honestly and falls back to fold-level asymmetry |

The rejected evidence file is itself informative: `annualized_sharpe
-1.0744`, `deflated_sharpe_ratio 0.0`, `excess_kurtosis 170.4`. Whatever
produced it, it was correctly rejected.

---

## One finding not yet in the baseline table

`tests/test_binance_live_runner.py` has one failure that B02 does not
mention, and it is not a skip:

```
FAILED test_canonical_recovery_requires_context_and_uses_client_key
  scripts/live_enhanced_ma_binance.py:1614
  LiveSafetyError: reconciliation broker identity mismatch for intent_...
```

```
40 passed, 1 failed, 3 skipped
```

`_record_reconciled_submission` compares `broker_order_id` against
`order.exchange_order_id` after a store restart. When the lifecycle
reconstructs an intent from the persisted ledger but has not rehydrated the
exchange identity onto it, the comparison fails and reconciliation raises —
a restart-recovery path that cannot recover.

This is squarely L0.4's *"restore/crash không duplicate submit hoặc reset
risk baseline"*, and it is the failure mode that matters most: the code
correctly refuses to double-count a fill, and in doing so refuses to
reconcile at all. The guard is right and the hydration behind it is
missing.

Worth noting the test is a good one. It asserts the guard fires on a
genuine identity mismatch; what is missing is the positive path — restart,
reconcile the same order, and get one cumulative fill rather than two.

---

## Where I would push back on the plan

### 1. L1.4 depends on something L1 does not produce

L1.4 is *"evidence bundle và revision tích hợp"* and B07 lists hourly data
cutoff and gaps. But the plan does not say which evidence bundle a campaign
must emit for L1.4 to consume, and §7's bundle spec covers *admission
evidence* (a policy's evidence) rather than *campaign evidence* (a run's
metrics, fold identities, cost schedule, and the code revision that produced
them).

That gap is what let the last three campaigns produce numbers nobody could
reconcile: `campaign_summary.json` exists, but it records a status string
and does not bind the result to a revision, a data manifest, or the fold
identities. Adding a campaign-evidence contract to L1 is cheap now and
expensive later.

### 2. L2 is one strategy, and the plan does not say which

L2 proves *"một policy đủ điều kiện"*. Given the current evidence:

```
enhanced_ma        32-fold, 5 trades,   median return  0.000%
stat_arbitrage_lo 32-fold, 45 trades,  median return -0.029%
                   20/28 folds trade, 6/28 profitable, payoff ratio 0.50
```

Neither has a demonstrated edge, and the plan's own scope line declines to
presume `enhanced_ma` is the winner. But the work package has no default
subject, so the first person to reach L2 will pick by preference and the
choice will be made on a 5-trade result.

L2 should name the selection rule, not the strategy: highest fold-clearing
rate among strategies with enough folds to satisfy the gate, decided before
the campaign and recorded in D01.

### 3. The gate question the plan defers is load-bearing

L1.1 says cost floor and selection score must be reconciled net/gross. That
is correct and it is unresolved. Two decisions in
`GATE_SEMANTICS_AND_RERANK.md` are still open and both change L2's outcome:

- p≤0.20, n≥14 — chosen, but as judgement rather than derivation
- fold independence versus fold count — the 270-bar window makes folds
  tradeable and overlapping, and the binomial test assumes independence

The plan's rule — *"không tự sửa ngưỡng trong plan để hợp thức hóa code"* —
is right, and it means these must be settled at L1 and recorded, not
discovered at L2 when a result sits on the boundary.

### 4. L3 has a calendar dependency the plan does not surface

AC15 requires 30 days of soak. That is wall-clock, not compute, and it is
the longest lead item in the plan. If it starts after L2 completes, the
schedule extends by a month regardless of how fast L0–L2 go.

Worth stating explicitly so the ordering decision is made knowingly: some
part of L3 preparation can run in parallel with L1/L2, and the parts that
can is a decision worth making now.

---

## What the plan gets right that is easy to lose

The §4 invariants and the L0 evidence rule — *"Mock chỉ exchange boundary;
không mock risk/planner/permission thành ALLOW"* and *"NO_TRADE chỉ chứng
minh abstention, không thay case positive BUY"* — encode a failure this
investigation hit repeatedly. A test that passes because construction was
refused is not a test, and the plan names that exact failure mode before
anyone can repeat it.

Likewise *"Testnet opt-in chưa chạy phải ghi NOT_RUN, không PASS"* — the
three skips in B02 are precisely what that rule exists to prevent being
counted as green.

---

## Recommendation

The plan is usable as written, with three additions, in this order:

1. **Add the failing reconciliation test to B02**, and add a positive
   restart-recovery case alongside it. This is L0.4 work that has already
   surfaced as a red test, and it is the kind of defect that would surface
   during a testnet soak rather than before one.

2. **Specify the campaign-evidence contract at L1** — revision, data
   manifest, fold identities, cost schedule, per-fold metrics — so L1.4 and
   L2 consume something they can verify. This is the highest-leverage
   addition, because it is the contract that would have caught the
   fabricated-policy incident and the unbindable campaign numbers.

3. **Name the L2 selection rule before L2 starts**, so the subject is
   chosen by rule and recorded, rather than by preference against a
   5-trade result.

The plan's own instruction is to confirm B01–B07 on the revision that
receives the work. Doing that now: B02 and B05 confirmed above, B06
confirmed, B01/B03/B04/B07 not independently re-checked in this pass, and
the one new finding is the reconciliation failure.

Everything in `DEEP_REVIEW_2026_10_03.md` remains consistent with this
plan — the gap the plan addresses is the same one the deep review found, and
L0/L1 are the right level to close it.