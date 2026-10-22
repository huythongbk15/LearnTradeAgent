# Revision 6 — superseded, kept

rev6 froze source identity A `8d27b8f1…` and preregistration identity B
`763a478e…`.

**What changed in rev7:** the reviewer found that the payload identity B hashes
did **not** cover every decision — the scope-change request and its approval
state, the inherited execution contract, the exact registered command, the
final review round (cap 600 s, check list, prohibitions, verdicts), the
falsifiable hypothesis and the run-authority statements were prose only and
were not hash-bound. The payload now carries all six, so identity B binds them.
Source identity A is unchanged (only `data/` changed).

rev6's manifest is kept so its evidence hashes stay verifiable.
