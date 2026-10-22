# Revision archive — **partial, and this is stated rather than hidden**

Held here: `rev1` … `rev7`, `rev11`, `rev15`, and the current revision's manifest
at the packet root.

**Missing**: `rev8`, `rev9`, `rev10`, `rev12`, `rev13`, `rev14`. Those
manifests were regenerated **in place** during the earlier rounds instead of being
archived first, so their file lists and hashes are not recoverable from this
directory. The gap is a bookkeeping defect in the archive procedure, not in the
evidence: every revision's **content** is recoverable from the working tree and
the git history of the changed files, and the preregistration and the tracker
carry the identity of each revision in prose. From `rev15` onward every manifest
is archived before it is superseded.

**One more defect, disclosed.** The archive is **off by one**: the manifest
filed under `revisions/rev15/` actually contains `"revision": "rev14"` and
rev14's identities, because rev15's manifest was overwritten in place rather
than archived first. **No archived manifest anywhere carries
`"revision": "rev15"`, so rev15's identities are not recoverable from this
directory** and the README filed beside it cannot be checked against a
manifest. The same mislabelling affects `revisions/rev7/manifest.json`, which
says `"revision": "rev9"`. This is a bookkeeping defect of the archive
procedure, not of the evidence; the content of every revision is recoverable
from the working tree and the git history.

The current revision is the one at the packet root; it supersedes `rev15`.
