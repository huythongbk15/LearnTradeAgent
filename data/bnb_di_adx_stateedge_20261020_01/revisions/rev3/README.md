# Revision 3 — superseded, kept

rev3 froze source identity A `1c69541f…` and preregistration identity B
`f140a486a…`.

**What changed in rev4:** the integration review of the scoped path found and
fixed **five dropped hops** in the launcher → worker → cell chain, so the scoped
candidate is now executable through the intended entrypoint (proved with a
synthetic end-to-end integration test) and a swapped/wrong token is blocked
before any data read or simulation. Source identity A moved to `94bcc703…`.

rev3's manifest is kept so its evidence hashes stay verifiable.
