# E2E system test — first full-flow run on the current tree

`scripts/e2e_system_test.py`, 300 bars, 3 symbols, output to
`/tmp/e2e_run2`. **84 assertions passed, 1 failed.**

## A bug this run found, in code written earlier today

Scenario 3 crashed before completing:

```
File "scripts/live_data_pipeline.py", line 617, in build_tournament
    service.activate(
File "src/trading_agent/research/selection_policy.py", line 933, in activate
ValueError: policy must pass canonical promotion lifecycle through
            paper_eligible before activation
```

`build_tournament` created a `DRAFT` policy — the change made in `19ec13c`
when the generators stopped fabricating scores — and then called
`service.activate()` on it. The script contradicted itself, and the E2E
caught it on the first full run.

Fixed by removing the activation. The bootstrap evaluates nothing, so its
policies have no measured scores and cannot be promoted; with no active
policy the router abstains and returns `NO_TRADE`, which is the correct
outcome for a system with no evidence. This is the E2E earning its keep:
the flow was only ever run before because fabricated scores let it through.

## Results

| # | scenario | result | assertions |
|---|---|---|---|
| 1 | Data Integrity (BinanceDataFeed) | **PASS** | 14/14 |
| 2 | Strategy Signal Generation | **PASS** | 10/10 |
| 3 | Tournament Routing (shadow) | **FAIL** | 5/6 |
| 4 | Risk Policy (deterministic) | **PASS** | 6/6 |
| 5 | Order Planning (LLM-free) | **PASS** | 4/4 |
| 6 | LLM Enrichment (deterministic) | **PASS** | 23/23 |
| 7 | Monitoring & Audit | **PASS** | 5/5 |
| 8 | T1B strategy hit rate | **PASS** | 13/13 |
| 9 | T1C correlation matrix | **PASS** | 4/4 |

Scenario 3's single failure:

```
✗ ≥1 strategy selected across bars — strategies=set()
```

**That failure is the correct result, not a regression.** The assertion
expects the router to select a strategy. There is no policy that can
legitimately be selected, so it selects nothing. An E2E that passed here
would mean a policy had become promotable without evidence.

## What the run establishes

**The execution and analysis core is sound, end to end.** Signal
generation works for the canonical strategies. Risk policy, order
planning, LLM enrichment determinism, monitoring, hit-rate analysis and
correlation all pass. None of these touch the promotion gate, which is
consistent with the position reached in
`CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`: the harness bugs were in the
campaign path, not the execution path.

**T1B is worth reading carefully.** All 12 strategies cleared a 0.40 hit
rate, and the mean cleared 0.45. A coin flip is 0.50 for a long signal
evaluated on forward returns, so a 0.40 floor is a weak bar and clearing
it twelve times is weaker evidence than the pass count suggests. It does
establish that signal generation is not systematically inverted, which is a
real check and had it been failing would have been worth finding.

**The routing failure localises the system's current state precisely.**
Data in, signals out, risk and order planning sound — and then the
promotion gate stops the flow. That is one gate standing where 2,690
fabricated policies used to stand.

## What the run does not establish

It runs 300 bars on 3 symbols with the strategies as configured. It does
not exercise the campaign path, so the coverage invariant added in
`54906f6` is not tested here, and it says nothing about whether any
strategy has edge. Scenario 8's hit rate is a direction-agreement check,
not a profitability check.

The E2E's own expectations in `scripts/e2e_test_scenarios.md` — T1A
asserting `shadow_sharpe[enhanced_ma] > 1.0` — were written before the
fabrication was found and are not enforced by the current scenarios, so
they are stale rather than failing.

## The one change this needs

Scenario 3 should assert the fail-closed behaviour rather than routing
success, since that is what a system with no promotable evidence should
do:

```
✓ router abstained with no promotable policy
✓ NO_TRADE returned for every bar
✓ abstention reason recorded in the audit trail
```

That turns the one failure into the assertion that the gate is doing its
job. Making it pass the other way — by re-adding a promoted policy — would
undo the finding this investigation was for.
