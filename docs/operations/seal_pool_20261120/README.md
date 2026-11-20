# SEAL-DATE POOL (frozen 2026-11-20)

This directory holds the frozen SEAL-DATE POOL: one pre-registered pooled read of six sealed forward books, read once on 2028-11-13 (deadline 2028-12-31). The operator opened it on 2026-11-20 with "Viết trang SEAL-DATE".

- [PROTOCOL.md](PROTOCOL.md) is the page: members, unit, opened spans, statistic, words, power.
- [pool.py](pool.py) is the reader, frozen with the page. `--selftest` runs it on synthetic data only.
- [FROZEN.sha256](FROZEN.sha256) lists the digests of every file here except this README. Its own sha256 is `869f89e49a7186acc756790496b8d8196f0d3b1025c92db1252c47026da4de92`, recorded in the trial ledger at 2026-11-20T10:23:40Z.

The files are byte copies of the research packet `data/seal_pool_20261120/` (git-ignored). Check them with `sha256sum -c FROZEN.sha256` in this directory. Do not edit them. A rule change is an operator decision recorded as an amendment beside these files.

**Duty for member looks (PROTOCOL rule 2).** When the H-RANK-18 look 1, the FlowBid read, a LEARN look or an NL-P read runs, it also writes its per-unit nets up to its cut to `data/seal_pool_20261120/snap/<MEMBER>_<look>.csv` and prints the sha256. That file is part of the look, not a new look. Nothing computes the pool before 2028-11-13.

This is research description, not investment advice.
