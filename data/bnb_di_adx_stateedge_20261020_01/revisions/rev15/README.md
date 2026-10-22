# Revision 15 — superseded, kept

rev15 froze the source identity **after** the engineering verification of the
2026-10-21 ledger correction: the fast ledger/binding regressions, the
launcher plan-level geometry check, AC01–AC15 on the final source, and one
synthetic launcher integration that completed with terminal coverage. The
verification evidence is in `../bnb_di_adx_stateedge_20261020_01/VERIFICATION_RECEIPTS_20261021.md`.

rev15 also added the preregistration-root seam (`preregistration_root()` in
`scripts/run_wfo_parallel.py`, default = the repository root, resolved at call
time and recorded in the frozen protocol) so the synthetic integration could bind
to a synthetic approved packet without touching the registered one.
