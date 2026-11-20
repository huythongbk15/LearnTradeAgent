# Worn scale for the LEARN member in its forward unit (one decision a day at 23:00 UTC, as LEARN_PAPER_RUN.md).
# Uses only predictions before 2026-10-26 (the paper record's first decision). Description, not a test.
import sys; sys.dont_write_bytecode = True
import numpy as np, pandas as pd
W = "/home/huythong/.qwenpaw/workspaces/trading/.claude/worktrees/doc-sau-phan-tich-du-an-cc1551"
pr = pd.read_parquet(f"{W}/data/learn_candle_20261104_01/pred_h24.parquet")
pr = pr[pr.index < pd.Timestamp("2026-10-26", tz="UTC")]
hour_no = ((pr.index - pd.Timestamp("2020-01-01", tz="UTC")) / pd.Timedelta(hours=1)).astype(int)
def mon(ts): ts = ts.tz_localize(None) if ts.tz is not None else ts; return ts.normalize() - pd.to_timedelta(ts.weekday, unit="D")
for o in (23, 0):
    s_ = pr[(hour_no % 24) == o]; nets = {}
    for c, g in s_.groupby("coin"):
        g = g.sort_index(); p = g["pred"].to_numpy()
        pos = np.where(np.abs(p) > 2e-3, np.sign(p), 0.0); prev = np.concatenate([[0.0], pos[:-1]])
        nets[c] = pd.Series((pos * g["fwd"].to_numpy() - 1e-3 * np.abs(pos - prev)) * 1e4, index=g.index)
    pool = pd.concat(nets, axis=1).fillna(0).mean(axis=1)
    w = pool.groupby(mon(pool.index)).agg(["sum", "size"]); w = w[w["size"] == 7]["sum"]
    print(f"offset {o:02d}:00 weeks {len(w)} {w.index[0].date()}..{w.index[-1].date()} mean {w.mean():.1f} sd {w.std():.1f}")
