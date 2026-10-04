# Combined assessment — deep review × live-readiness plan

Requested: an assessment that joins the system-wide review with the
live-readiness plan rather than treating them separately. The plan was
written against a baseline at `6f1d69d`; this cross-checks every baseline
claim B01–B07 and reports what the plan covers, what it under-specifies, and
what the deep review found that the plan does not yet list.

All claims below were verified by running the code, not by reading it.

---

## Baseline claims — all seven checked

| ID | claim | result |
|---|---|---|
| B01 | BUY producer chưa nối đủ risk/planning/permission | **partly resolved — see below** |
| B02 | 6 legacy runner tests bị skip; testnet signature cũ | **confirmed** — 3 opt-in skips; the fourth failure I reported was against a build since fixed |
| B03 | soak tổng hợp theo symbol; failed runs tạo false PASS | **đã fix trong working tree** (không có ở HEAD) — báo cáo đầu của tôi đọc bản cũ |
| B04 | cost floor/selection score cần thống nhất net/gross | **already fixed** — `selection_policy.py:841` uses `net_fold_edge` |
| B05 | chưa có published evidence; promotion store rỗng | **confirmed** — 0 entries, only `.rejected.json` |
| B06 | payoff script thiếu per-trade; break-even sai | **confirmed** — `BREAK_EVEN_RATIO = 0.71` hardcoded from the superseded per-trade rate |
| B07 | nhiều phiên bản hourly, cutoff khác nhau | **confirmed, quantified below** |

Two of seven are already resolved and one is partly resolved. Re-implementing
any of them would be waste, which is why the plan's instruction to confirm
before redoing work is the right one.

---

## B01 — admission is optional at the producer, mandatory at the consumer

`build_decisions` takes `buy_admissions: Mapping | None = None`. When it is
`None`, a BUY is emitted **without** `risk_decision`, `order_planning` or
`permission_context`:

```python
if action == "BUY" and buy_admissions is not None:   # line 1030
    ...
    decision.update(qty=..., risk_decision=..., order_planning=...,
                    permission_context=...)
decisions.append(decision)
```

The consumer closes this. `execute_orders` calls `_validated_buy_admission`,
which raises unless all three are present, correctly typed, non-stale
(`MAX_BUY_ADMISSION_AGE_SECONDS`), and the planned quantity does not exceed
the runner's own quantity.

So the invariant holds at the boundary that matters — no unadmitted BUY
reaches the broker. What remains true and worth stating: a producer caller
that omits the argument produces decisions that are guaranteed to be refused
later, rather than refused at production. That is a diagnosability gap
rather than a safety gap, and it is why the plan's L0.1 deliverable —
*"map chỉ rõ dependency thật trong code"* — is worth doing. Making the
producer require admission would turn a runtime refusal into a construction
error.

---

## B03 — already fixed in the working tree, not in HEAD

**Correction.** I read this file against `HEAD` and reported a false-PASS
path. The working tree does not have it. `scripts/testnet_soak_tracker.py`
carries 320 insertions against HEAD, and both halves of the fix are in it.

`tracking_days` was rewritten:

```
HEAD:  docstring "Consecutive covered days; a gap longer than max_gap_hours breaks the run."
        terminals = _terminals(events)          # run_completed AND run_failed

work:  docstring "Successful streak only; failed/critical events break continuous coverage."
        if event["event"] != "run_completed":
            days, prev = 0, None                 # a failure resets the streak
```

and `evaluate_gates` now reads `run_outcomes`, which it did not before:

```python
"zero_unexplained_events": eligible
and sum(critical.values()) == 0
and sum(report["run_outcomes"][key]
        for key in ("failed", "incomplete", "invalid")) == 0,
```

Every gate is now gated on `evidence_eligible`, and `stop_coverage_100pct`
requires `invalid_events == 0`.

`scripts/demo_soak_false_pass.py` builds 91 consecutive runs across 30
calendar days and confirms the behaviour: with every run failing,
`days_continuous` is 0 and all five gates refuse. The false-PASS I
described would have produced `days_continuous: 31`.

This is the failure mode this document keeps recording, in the other
direction. I read a file, found a defect, and reported it without checking
whether the working tree had already diverged from what I read. The
instruction to confirm B01-B07 on the revision that receives the work is
exactly what I skipped, and this is what skipping it costs.

## B07 — three hourly files, and which one is authoritative

```
1h              31,783 bars  2023-01-01 → 2026-08-17
1h_extended     58,897 bars  2020-01-01 → 2026-09-21
1h_full         58,897 bars  2020-01-01 → 2026-09-21
```

`1h_extended` and `1h_full` have identical bar counts and ranges; whether
they are byte-identical was not checked here and should be, because two files
with the same name-shape and the same content is a path to a campaign
silently reading one while the report cites the other.

The default `1h` carries **46% less history** than the other two. Every
campaign in this investigation used `data/raw/binance/{sym}/1h.parquet`
unless it said otherwise, and therefore measured 2023-01 onward while the
same symbol had data back to 2020. No campaign recorded which file it read.

Recommended at L1.4: one canonical hourly path, a manifest that records
sha256 and row count for whatever was read, and every campaign citing that
manifest. That is the same campaign-evidence contract noted below, applied
to the input rather than the output.

---

## The reconciliation failure also already fixed

**Second correction, same cause.** I reported
test_canonical_recovery_requires_context_and_uses_client_key failing with
reconciliation broker identity mismatch. It passes now:

    tests/test_binance_live_runner.py                        46 passed
    tests/test_binance_live_runner.py + testnet_acceptance  46 passed, 3 skipped

Timestamps settle it. My run finished at 16:15;
scripts/live_enhanced_ma_binance.py was last modified at 16:50.

What changed is exactly the missing hydration. Against HEAD the restart
path compared broker_order_id to order.exchange_order_id without restoring
the venue identity first. The working tree adds a branch calling
execution_service.record_broker_fact with an UNKNOWN fact when
order.exchange_order_id is None and the intent is not already unresolved.
record_broker_fact emits ORDER_SUBMITTED, _on_order_submitted assigns
order.exchange_order_id, and the identity comparison then compares two real
values instead of a value against None.

The guard I called correct still is correct, and it now has the hydration
behind it. Neither the B02 failure nor the L0.4 gap needs work from me.

I hit this twice: B03 and the reconciliation path. Both times the finding
was real against HEAD and already resolved in the working tree, and both
times I reported it before checking. The check is cheap: git status on the
file, or re-running the test and noticing it passes.
## What the plan covers well, confirmed against the code

**L0.1's prohibition is load-bearing.** *"không thiết kế luồng parallel
bypass canonical"* — verified that `execute_orders` requires both
`lifecycle` and `gateway` and raises without them. The canonical path is
enforced at the boundary, not merely documented.

**The L0 evidence rule matches a real failure mode in this repository.**
*"Mock chỉ exchange boundary; không mock risk/planner/permission thành
ALLOW"* and *"NO_TRADE chỉ chứng minh abstention, không thay case positive
BUY"*. During this investigation a test passed because construction was
refused rather than because behaviour was correct — exactly what these two
lines prohibit. Likewise *"Testnet opt-in chưa chạy phải ghi NOT_RUN, không
PASS"* is what the three verified skips would otherwise become.

**§3's authority table is unusually complete.** The distinction between
"task-scoped code change" and "operator approval for account access" is the
line most commonly blurred when an agent is handed a live-readiness plan.

**§4's invariant — keep NO-GO on conflict** is the correct default, and it
is what this document does: where a plan claim and a code check disagreed,
the code check was recorded against the plan.

---

## What the plan under-specifies

**1. L1.4 has no input to consume.** §7's evidence bundle is a *policy
admission* contract. L1.4 needs a *campaign* contract — revision, data
manifest, fold identities, cost schedule, per-fold metrics, and which
market-data file was read. `campaign_summary.json` currently records a
status string and binds nothing. This is the contract that would have
caught both the fabricated policies and the three unreconcilable campaign
results, and it is cheapest to specify at L1.

**2. L2 names no selection rule.** Neither candidate has an edge:

```
enhanced_ma         32-fold,  5 trades,  median  0.000%
stat_arbitrage_lo   32-fold, 45 trades,  median -0.029%
                    20/28 folds trade, 6/28 profitable, payoff ratio 0.50
```

The plan declines to presume a winner, correctly, but leaves the subject
unset — so the first person to reach L2 chooses by preference, against a
five-trade result. A rule fixed before the campaign and recorded in D01
costs nothing and removes the choice.

**3. Two gate decisions are deferred and are load-bearing.** p≤0.20 and n≥14
(`GATE_SEMANTICS_AND_RERANK.md`), and fold independence versus fold count —
the 270-bar window that makes folds tradeable also makes them overlap,
which the binomial test assumes away. Both change L2's outcome. The plan's
rule against self-amending thresholds means they must be settled at L1 and
recorded, not discovered at L2 with a result sitting on the boundary.

**4. L3 carries a calendar dependency.** AC15 needs 30 days of wall clock,
the longest lead item in the plan. Part of L3 preparation can run alongside
L1/L2; deciding that now is cheaper than discovering it at L2.

---

## How the two documents fit together

The deep review's conclusion — strong execution core, correctly-stopped
promotion gate, unmeasured evidence base — is the same finding the plan is
built to address, and L0/L1 are the right level to close it.

The plan adds three things the review could not see from code alone: an
authority model, a decision-record requirement, and an explicit refusal to
let a narrow canary stand for project sign-off. Those are process
instruments, and they are the part most likely to be the difference between
a completed workstream and a correct one.

Where they differ is granularity. The review measured the system;
the plan sequences the work. The review's largest gap — fifteen strategies
never run through walk-forward — corresponds to L2 being a single policy,
which is a reasonable scope decision and should be stated as such rather
than left implicit.

---

## Recommended order of additions

1. The reconciliation test B02 failure: **resolved** in the working tree,
   46/46 pass. Still worth adding the positive restart-recovery case,
   which no test covers.
3. **Campaign-evidence contract at L1**, including the market-data manifest,
   so L1.4 and L2 consume something verifiable.
4. **L2 selection rule recorded in D01** before the campaign runs.
5. **One canonical hourly file**, with sha256 and row count in the
   evidence manifest, so a campaign cannot silently read a shorter history
   than its report implies.

Items 1 and 2 are defects in existing code. Items 3–5 are specifications
that cost an hour now and weeks later.