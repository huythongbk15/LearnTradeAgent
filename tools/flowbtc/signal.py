"""The daily call of H-FLOW-BTC-02 from the local store: long or flat for the day.

What the demo book (src/trading_agent/execution/perp/flowbtc.py) asks:

- ``update(now_ms)``: append closed public data to the store.
- ``decide(day_ms, record=...)``: features of the bar opening at ``day_ms``
  (00:00 UTC), the model of that bar's quarter (fitted and, with ``record``,
  written with its hash when it is missing and the data reach the quarter
  start), and the call: long iff the prediction is above the threshold.

``decide`` refuses to call a day before the store has been brought past it:
the 00:00 bar, the metric stamped 00:55 and a funding rate settled in the 8 h
to the bar close must be on file (``missing`` names what is not). A gap the
update recorded is not missing: until a later update fills it (only after the
research end, and a 5 m hole only within the series' 29 days) its features
stay unknown and the model fills them with the train median, as in research.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from tools.flowbtc import features as F
from tools.flowbtc import model as M
from tools.flowbtc.store import ROOT, Store
from tools.newperp import fetch as fx

FUNDING_EVERY_MS = 8 * F.H_MS


class Signal:
    def __init__(self, root: Path = ROOT, client: Callable[[], fx.Client] = fx.Client):
        self.root = Path(root)
        self.client = client

    @property
    def store(self) -> Store:
        return Store(self.root)

    def update(self, now_ms: int) -> dict:
        return self.store.update(self.client(), now_ms)

    def missing(self, day_ms: int) -> list[str]:
        """What the store lacks for the call on the bar opening at ``day_ms``."""
        st = self.store
        if not st.ready():
            return ["store"]
        cov = st.coverage()
        close = day_ms + F.H_MS
        out = []
        if cov["bar"] < day_ms:
            out.append("bars")
        if cov["metric"] < close - 5 * 60_000:
            out.append("metrics")
        if cov["funding"] <= close - FUNDING_EVERY_MS:
            out.append("funding")
        return out

    def decide(self, day_ms: int, *, record: bool) -> dict:
        """The call for one day. Without ``record`` nothing is written (a missing model is fitted in memory)."""
        bar = pd.Timestamp(day_ms, unit="ms", tz="UTC")
        if bar.hour != M.DECISION_HOUR or bar.minute or bar.second or bar.microsecond:
            raise ValueError(f"not a decision bar: {bar}")
        missing = self.missing(day_ms)
        if missing:
            return {"ok": False, "missing": missing}
        b, m, fu = self.store.frames()
        f = F.features(b, m, fu)
        q = M.quarter_of(bar)
        tag = M.quarter_tag(q)
        try:
            model = M.load(self.root, tag)
        except M.ModelError:
            return {"ok": False, "missing": ["model_refused"]}
        if model is None:
            if b.index[-1] + pd.Timedelta(hours=1) < q:
                return {"ok": False, "missing": ["model"]}
            model = M.fit(f, F.targets(b, M.H), q)
            if record:
                M.record(self.root, model)
        row = f.loc[[bar], model["cols"]]
        pred = float(M.predict(model, row)[0])
        if not np.isfinite(pred):
            return {"ok": False, "missing": ["prediction"]}
        return {"ok": True, "long": M.is_long(model, pred), "pred": pred, "threshold": float(model["threshold"]),
                "model": tag, "nan": int(row.isna().sum(axis=1).iloc[0])}
