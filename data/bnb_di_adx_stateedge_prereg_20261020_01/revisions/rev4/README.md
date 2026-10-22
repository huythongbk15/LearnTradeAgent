# Revision 4 — superseded, kept

rev4 froze source identity A `94bcc703…` and preregistration identity B
`117ea187…`, after the integration review fixed the five dropped token hops.

**What changed in rev5:** the `--max-retries` wiring (the preregistration pins
no automatic retry, and the protocol producer hard-coded 1) moved the source
identity again; the freeze record was updated to the final A. The preregistration
decisions (identity B) are unchanged.

rev4's manifest is kept so its evidence hashes stay verifiable.
