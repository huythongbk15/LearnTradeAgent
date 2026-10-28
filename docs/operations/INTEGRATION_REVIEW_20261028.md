# Integration review — 2026-10-28

PR: https://github.com/huythongbk15/LearnTradeAgent/pull/64

Status: PUSHED; MERGE BLOCKED BY FULL CI. No strategy qualification or market-run authorization is granted by this integration.

## Integrated scope

The integration preserves the four previously local research commits and merges the remote master history. It adds closed-bar BTC-context research, explicit validation windows, synthetic first-fill expiry, protective-order reconciliation before emergency close, and optional research persistence settings. Production durability defaults remain unchanged. The fill-timed candidate remains unregistered and the driver requires explicit synthetic input.

The integration review also rejects invalid inventory rather than treating it as known flat, copies and freezes cycle attribution in portfolio snapshots, and records only positive fills in the synthetic driver. Local evidence links are explicitly identified as local references; old market packets are neither uploaded nor rewritten. `state/last_dispatch.json` remains a local session change outside the commits.

## Verification actually executed

- 99/99 targeted context, fill-cycle, emergency-close and storage regressions passed on the integrated source.
- 58/58 existing broker-boundary, trace, P0 and authority/paper-flow tests passed after CI portability repairs; an additional focused run passed 8/8.
- 21/21 preregistration tests passed using a checked-in metadata-only fixture, with hashes of the source receipts. This is a recorded metadata comparison, not fresh verification of market files.
- Integrated AC01–AC15 passed after replacing AC03's untracked market-price dependency with an hourly synthetic timestamp clock. Its scope is explicitly engineering verification. All holdout exclusion and integrity assertions remain present.
- Ruff, execution-boundary mypy and P0 smoke passed locally. A clean Git archive passed all local Markdown links in 156 files.

One earlier AC run was rejected because a documentation commit changed HEAD during execution. It is not accepted evidence; a subsequent run with stable identity passed.

## CI findings and repairs

The first full CI run on `82ed7f49` exposed issues beyond the targeted suite. Its original results were ci-p0: 591 passed / 6 failed / 3 collection errors; ci-fast: 2290 passed / 31 failed / 3 collection errors; slow: 48 passed / 5 failed / 3 collection errors. These counts describe that run, not the repaired revision.

Repairs preserve assertions: lock metadata now matches the existing NumPy requirement without changing locked package versions; tests use private engine state directories; trace assertions explicitly request INFO logging; optional installed QwenPaw tests report absence instead of crashing collection; the AST broker guard distinguishes the engine's exact internal close dispatch from a forbidden venue close. A regression keeps `self.exchange.close_position` forbidden. AC03 uses timestamps only and does not open market holdout prices.

## Remaining blockers: do not bypass or weaken CI

1. `tests/test_canonical_data.py`, parts of `tests/test_nested_wfo.py`, and `tests/test_ab_test_context.py` assume untracked market files. Replace unit-test dependencies with deterministic temporary fixtures; retain genuinely local-data checks as separately declared integration evidence. Do not download or publish market data to make CI pass.
2. `tests/test_r04_scope_lock_campaign.py` and `tests/strategies/test_canonical_wave_c.py` contain stale scope/catalog expectations. Compare against the approved current contract before changing assertions; candidate registration is not qualification.
3. `tests/test_campaign_publication.py` has publisher stubs missing `selection_evidence_root`; update fixture signatures and retain byte-preservation/refusal assertions.
4. Three short-side tests disagree with the current treatment of terminal open inventory. Establish the intended accounting contract before changing either totals or implementation; keep closed and open trades distinct.
5. `tests/test_campaign_runtime_input_binding.py` encounters an already-active frozen binding. Audit fixture cleanup and environment isolation; do not weaken the production binding refusal.
6. Five slow synthetic tests fail because measured sensitivity requires every planned outer fold. Determine whether fixtures fail to produce coverage or orchestration mishandles missing coverage. Preserve fail-closed sensitivity requirements and register any source change with fresh AC evidence.

Merge only after the repaired revision passes full CI. Do not use administrator override, force-push, relaxed assertions, or stale AC receipts to declare completion. Mainnet remains NO-GO.

## Follow-up on 44ce8f09

GitHub acceptance-evidence passed, matching the local final AC pass. CI-p0 improved to 596 passed / 2 failed / 3 skipped; the two remaining failures still shared the default event-store path despite isolated paper-state directories. The follow-up pins explicit temporary event-store paths; the four related tests passed under two xdist workers (8.97s). Lock freshness now passed, but export differed because CI installed a newer uv with different marker normalization. CI now pins uv 0.11.28 to match the verified exporter. These follow-up changes need fresh remote CI; the fast/slow blockers above remain open.
