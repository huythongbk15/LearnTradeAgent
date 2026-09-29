# Investigation index — where to read what

Sixteen documents were written across one session on 2026-09-28. They are
ordered here by when they must be read, not by when they were written,
because later documents correct earlier ones.

**Start with `HANDOVER_2026_09_28.md`.** It is the current state. Everything
else is evidence or history behind it.

## 1. Current state

| document | what it answers |
|---|---|
| `HANDOVER_2026_09_28.md` | where the project is, what is sound, what to do next |
| `AC_TRACKER.md` | per-AC verdicts, blocking conditions, next priority |
| `E2E_SYSTEM_TEST_RESULT.md` | what the full flow does today, end to end |

## 2. What was broken, and what it changed

Read in order — each found the next.

| document | finding | change |
|---|---|---|
| `POLICY_RETURN_AUDIT.md` | 2,690 promoted policies carried fabricated scores; 4 of 4,520 promotable policies traceable to real code | promotion now requires measured metrics and source attribution; 43 stores quarantined |
| `CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md` | three WFO pipeline bugs at `a65ed29000`, one silently dropping strategies | campaign coverage invariant added |
| `COMMIT_FILTER_AND_SIGNOFF_RECHECK.md` | 4,527 cells predate the fix; the sign-off's only approval rests entirely on them | sign-off recommendation withdrawn |
| `MISSING_EVIDENCE_TRACE.md` | 5,760 cells survive only as summaries; 3 strategies have no artifact at all | sign-off figures uncheckable for 7 of 16 rows |

## 3. Measurements, and what they are worth

**All four rest on pre-`a65ed29000` data.** Each document carries a
provenance caveat. They are internally consistent — one campaign each, no
pooling — so within-document comparisons hold, but none can be cited as
current-pipeline evidence.

| document | measurement |
|---|---|
| `AC14_LOSS_DIAGNOSIS.md` | why a 1h strategy loses: cost drag, not signal |
| `ENHANCED_MA_FINDINGS.md` | no alpha in any of five windows; exposure 1.2–1.5% |
| `WFO_CAMPAIGN_RESULT.md` | 104 cells; edge concentrated in one fold |
| `RESEARCH_EVIDENCE_REVIEW.md` | the sign-off existed and was not read; symbol matters |
| `SPREAD_GATES_BY_SYMBOL.md` | gates applied per symbol; all fail on the same one |
| `TIMEFRAME_PROBE_RESULT.md` | higher timeframes reduce trade count, not increase it |

## 4. Method decisions

| document | decision |
|---|---|
| `GATE_SEMANTICS_AND_RERANK.md` | p ≤ 0.20, with n as the binding constraint; the pooled re-rank is invalid |
| `AC14_CHAIN_SUMMARY.md` | the six-round record, and the failure shape common to all of them |

## Reading paths

**New to this work:** handover → AC_TRACKER → E2E result. Three documents,
and the state is clear.

**Asked "does the registry have any edge":** handover, then
`GATE_SEMANTICS_AND_RERANK.md`. The answer is unknown, and the document
that says so most precisely is the one to read.

**Picking up the next task:** handover, then re-read the determinism
result. Nothing else should start before it.

**Auditing my own reasoning:** `AC14_CHAIN_SUMMARY.md`, then
`TIMEFRAME_PROBE_RESULT.md` and the two cost mistakes in
`HANDOVER_2026_09_28.md` under "Known-unknown". Three estimates in this
session were wrong by one to two orders of magnitude, and all three were
found by measuring rather than reasoning.

## Known-incomplete

`scripts/check_wfo_determinism.py` answers whether the WFO pipeline
reproduces its own result. No prior test does. It was still running when
`HANDOVER_2026_09_28.md` was written, and nothing downstream of it is
settled until it returns:

```bash
cat /tmp/det_result.txt     # watcher writes the verdict here
```

Two wrong turns while getting it to run fast enough to matter, both mine:
sensitivity analysis on every fold made a 4-minute spec take 4 hours, and
shorter folds produce *more* folds, so the "minimal" spec generated 40.
Both are recorded in the script docstring.
