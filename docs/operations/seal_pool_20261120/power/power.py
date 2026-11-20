# SEAL-DATE POOL, power arithmetic. Inference on WORN weekly streams already on disk
# (data/synthesis_20261120/quant/panel.parquet: every stream ends by 2026-10-19; RANK18 ends 2026-07-20, before the H-RANK-18 seal).
# Reads no sealed ledger and no forward week. Description, not a test.
import sys; sys.dont_write_bytecode = True
import numpy as np, pandas as pd
from math import erf, sqrt
W = "/home/huythong/.qwenpaw/workspaces/trading/.claude/worktrees/doc-sau-phan-tich-du-an-cc1551"
P = pd.read_parquet(f"{W}/data/synthesis_20261120/quant/panel.parquet")
assert P["RANK18"].dropna().index.max() <= pd.Timestamp("2026-07-20")
assert P.index.max() <= pd.Timestamp("2026-10-19")
MEM = ["RANK18", "NLP", "KRW", "FLOW_RIDGE", "LEARN11", "CB9"]
WIN = {"all weeks": None, "from 2022-03-14": "2022-03-14", "from 2023-01-02": "2023-01-02"}
def pool(mem, X, hair, nweeks):
    sd = X[mem].std().values; C = X[mem].corr().values
    s = (X[mem].mean().values / sd) * hair
    w = 1 / sd; Sig = np.outer(sd, sd) * C
    sr = (w * s * sd).sum() / np.sqrt(w @ Sig @ w)
    t = sr * nweeks ** .5
    return sr, t, .5 * (1 + erf((t - 1.96) / sqrt(2))), (2.84 / sr) ** 2 / 52 if sr > 0 else float("inf")
print("member stats (worn): first, last, n, mean, sd, first-half sd")
for k in MEM:
    v = P[k].dropna(); print(f"  {k:10s} {v.index[0].date()} {v.index[-1].date()} n {len(v)} mean {v.mean():7.1f} sd {v.std():6.1f} h1sd {v.iloc[:len(v)//2].std():6.1f} h2/h1 mean {v.iloc[len(v)//2:].mean()/v.iloc[:len(v)//2].mean():.2f}")
NW = 105  # Mondays 2026-11-09 .. 2028-11-06 inclusive
for lab, start in WIN.items():
    X = P[MEM] if start is None else P[MEM].loc[start:]
    print(f"\n== window: {lab}; correlations and Sharpe from the same weeks (pairwise) ==")
    for hair in (1.0, .5, .3):
        sr, t, pw, yrs = pool(MEM, X, hair, NW)
        print(f"  six members x{hair}: weekly SR {sr:.3f}; {NW} weeks E[t] {t:.2f}, P(t>=1.96) {pw:.2f}; 80% power {yrs:.1f} years")
    for drop in MEM:
        m = [k for k in MEM if k != drop]; sr, t, pw, _ = pool(m, X, .5, NW)
        print(f"    without {drop:10s} x0.5: E[t] {t:.2f}, P {pw:.2f}")
    for k in MEM:
        v = X[k].dropna(); print(f"    single {k:10s} x0.5: E[t] {v.mean()/v.std()*.5*NW**.5:.2f}")
X = P[MEM]
print("\nall-week correlations (worn):"); print(X.corr().round(2).to_string())
