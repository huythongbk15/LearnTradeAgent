# Preregistration rev1 — superseded, kept

`PREREGISTRATION.md` at the packet root is **rev2**. rev1 stated the
parameter-neighbour arm as three probes (`adx_threshold 20 / 30`,
`adx_period 21`) and asserted that the multiple-trial burden was unchanged.
Both statements were corrected in rev2 after checking the implementation:
the neighbour algorithm needs >=3 values per axis and silently skips a
2-value axis, and the sensitivity execution path is not registered, so it does
not enter the DSR trial count.

This directory keeps rev1's manifest so the earlier revision stays verifiable.
