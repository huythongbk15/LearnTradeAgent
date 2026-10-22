# Revision 1 — superseded, kept

This is the **rev1** evidence manifest of the `di_adx_state_edge` packet.

**What changed in rev2** (the current revision, at the packet root):

- **Source identity changed.** The controlled execution path was implemented:
  `candidates.py` (scoped descriptor + scope-token map + `build_default_registry(scope_token=...)`),
  `tournament.py` (`run_cell(scope_token=...)` → `_run_cell_impl`),
  `scripts/full_system_backtest.py` (`FullSystemSimulator(scope_token=...)`),
  `campaign_execution.py` (the token travels in the execution context).
  The candidate is still absent from the legacy `@register_strategy` registry
  and from the broad default registry; it is reachable only with the exact
  scope token.
- **Preregistration** rewritten: the three identities (source / experiment /
  evidence manifest) separated; the evaluation ledger added; the trial-count
  method restated as *my reading of the code, pending reviewer confirmation*;
  the DSR limitation stated; the budget restated as an expected envelope; the
  reviewer checklist added.
- **Synthetic suite** extended: the "stop while the state is still ON" case is
  now proven directly (S5b) plus an isolated state-machine check (S5c), and
  the four anti-vacuous assertions are explicit.
- **Envelope regression** added (`tests/test_wfo_validation_envelope.py`).

rev1's manifest is kept so the earlier revision's evidence hashes remain
verifiable. It does **not** describe the current packet state.
