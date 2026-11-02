"""Weekly paper selection run. It writes records only: no order path, no key, no private endpoint.

Rules are frozen in docs/operations/SELECTION_PAPER_RUN.md. The thread limits below must be set before numpy or polars load, so importing this package first is enough for a long job.
"""
import os

for _name, _value in (("POLARS_MAX_THREADS", "2"), ("OMP_NUM_THREADS", "1"), ("OPENBLAS_NUM_THREADS", "1")):
    os.environ.setdefault(_name, _value)
