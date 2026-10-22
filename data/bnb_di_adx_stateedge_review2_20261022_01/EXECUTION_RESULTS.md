# Budgeted verification follow-up — 2026-10-22

The user explicitly authorized exec_command via controlled_exec for delta,
tests/AC and F9 only. No market campaign, operator scope approval or resealing
was performed. WSL access initially failed in the sandbox; elevated access
worked. All subsequent commands used controlled_exec with unique receipts.

## Actual production identities

Python capture_campaign_source, before and after, agrees:

- A: c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74
- Dirty diff: 1865dc28229abb82fd28eab83c2e29d33901067a3b8896937954ddf68d5685ee
- Commit: 4078892d738790872dc2d4f494e3973a62612347
- Current B, computed by production preregistration_fingerprint:
  1338572d57628e9ba1c29b655856547d1c945f4695ee7cc9aef0545c8ce88145

These differ from sealed rev19. Existing seven manifest mismatches remain an
evidence blocker; successful checks below do not reseal or approve the tree.

## Executed checks

| Check | Result | controlled child elapsed |
|---|---|---:|
| ledger snapshot + ledger/binding invariants + validation envelope | 30 passed, rc0 | 32.552 s |
| integrated AC01–AC15 | ALL PASS, rc0 | 43.390 s |
| F9 crossover decomposition | rc0 | 3.707 s |
| F9 episode availability | rc0 | 2.513 s |
| F9 layer accounting | rc0 | 0.280 s |

F9 total child execution: 6.500 s, below its proposed 30 s envelope. Test/AC
child execution: 75.942 s. Diagnostics and WSL/controller overhead are separate
from these figures, not claimed to be zero. No automatic retries were used.
The heavy launcher integration was not rerun while final identity is unsettled.

## F9 reproduction and comparison with native RESULTS.md

- Deduplicated union [2358,5598): raw 59 (30 up / 29 down), passing signals
  41 (21 BUY / 20 SELL), overlapping signal occurrences 68.
- ADX25 episode availability: min35, max39, mean36.8 per 1080-bar window.
- 1x (only four completed windows): 29 BUY actions -> 25 runtime fills ->
  24 closed + 1 carry-out.
- 2x and slip_stress (five completed windows each): 35 BUY actions ->
  30 runtime fills -> 28 closed + 2 carry-out.
- All per-cost inventory identities hold. These match the measured claims
  in the existing native RESULTS.md. This is a semantic metric comparison,
  not a byte-identical rerun of human-written Markdown.
- The layer script also emits all-cost diagnostic totals. They must not be
  interpreted as one market sample; only its by_cost_scenario sections above
  are used for comparison.

F9's numerical reproduction caveat is closed for these cited metrics on the
current source. It does not establish edge, qualification, independent sample
size or profitability. Old control remains FAILED/INCOMPLETE.

## Remaining terminal blocker

Review status remains BLOCKED until current source/payload/evidence delta is
accounted for and intentionally frozen as a new revision. Do not edit manifest
hashes merely to erase the mismatch. Scope/run approval remains pending.

Receipts are copied without replacement into this separate review directory.
No source, strategy, gate or old packet files were modified by this follow-up.
