# Preregistration rev3 — superseded, kept

rev3 froze source identity A `1c69541f…` and preregistration identity B
`f140a486a…`, and recorded the scope token's binding to the execution
identity.

**What changed in rev4:** the *integration* review of the scoped path found
**five hops where the token was dropped** between the launcher and the cell
execution (`build_canonical_cells`, `prepare_parallel_protocol` →
`audit_parallel_trial_history`, `verify_parallel_result_coverage`, the
`QualificationCellRunner` cell-runner context, and `_run_cell_worker`). All
five now carry the token, and a new integration test drives the real entrypoint
chain with synthetic data. Source identity A therefore moved to `94bcc703…`.

rev3's manifest is kept so its evidence hashes stay verifiable.
