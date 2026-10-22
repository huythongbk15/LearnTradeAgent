# Revision 5 — superseded, kept

rev5 froze source identity A `8d27b8f1…` and preregistration identity B
`117ea187…`.

**What changed in rev6:** the two pre-run conditions were closed by fault
injection (timeout with a descendant that ignores SIGTERM; the ledger after a
killed attempt), the data boundary was checked directly with a timestamp-only
probe, and the preregistration now states the **read** scope separately from
the **evaluated** scope — the frozen loader decodes every row of the pre-holdout
file before slicing, and the file contains **0** reserved-holdout bars (last
bar 2026-02-06 08:00, holdout opens 16:00). A supplementary review budget is
registered separately from the market-run budget.

rev5's manifest is kept so its evidence hashes stay verifiable.
