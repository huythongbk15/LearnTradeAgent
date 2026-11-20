# SEAL-DATE POOL reader (PROTOCOL.md). Frozen with the page. Run ONCE on the read day, never before.
# Input: a directory of member week files <MEMBER>.csv with columns week (Monday, YYYY-MM-DD) and x (net bps a week),
# one row per opened pool week (rules 1-6 of PROTOCOL.md; stopped weeks already written as 0). Absent weeks have no row.
# Usage: python -I -B pool.py <dir>          (the read)
#        python -I -B pool.py --selftest     (synthetic data only; touches no record)
import sys
sys.dont_write_bytecode = True
import math
import numpy as np
import pandas as pd

SCALE = {"RANK": 400.1, "NLP": 596.9, "KRW": 607.7, "FLOW02": 211.2, "LEARN": 729.8, "CRASH": 156.3}
WEEKS = pd.date_range("2026-11-09", "2028-11-06", freq="7D")          # 105 pool weeks
SECONDARY = {"THREE-CLOCK": ["KRW", "CRASH", "FLOW02"], "LEARNED PAIR": ["FLOW02", "LEARN"]}
SEED, BLOCK, REPS = 20281113, 4, 10_000


def load(d):
    Z = pd.DataFrame(index=WEEKS, columns=list(SCALE), dtype=float)
    for m in SCALE:
        try:
            f = pd.read_csv(f"{d}/{m}.csv", parse_dates=["week"])
        except FileNotFoundError:
            continue
        assert f.week.is_unique and f.week.isin(WEEKS).all(), f"{m}: weeks must be unique pool Mondays"
        assert np.isfinite(f.x).all(), f"{m}: non-finite x"
        Z.loc[f.week.values, m] = f.x.values / SCALE[m]
    return Z


def ratio(Z):
    v = Z.to_numpy(float)
    return np.nansum(v) / np.isfinite(v).sum()


def stat(Z):
    Z = Z[Z.notna().any(axis=1)]                       # drop pool weeks with no member present
    n = len(Z)
    mhat = ratio(Z)
    nb = math.ceil(n / BLOCK)
    starts = np.random.default_rng(SEED).integers(0, n - BLOCK + 1, size=(REPS, nb))
    v = Z.to_numpy(float)
    bs = np.empty(REPS)
    for r in range(REPS):
        idx = (starts[r][:, None] + np.arange(BLOCK)).ravel()[:n]
        s = v[idx]; bs[r] = np.nansum(s) / np.isfinite(s).sum()
    se = bs.std(ddof=1)
    zw = np.nanmean(v, axis=1)                          # per-week mean over present members
    t_iid = zw.mean() / (zw.std(ddof=1) / math.sqrt(n))
    return n, mhat, se, t_iid, Z


def word(Z):
    k = Z.notna().sum(axis=1)
    if (k >= 3).sum() < 52:
        return "quá ít", None
    n, mhat, se, t_iid, Zk = stat(Z)
    w = "giữ" if mhat >= 1.96 * se else ("không" if mhat + 1.645 * se < 0 else "chưa rõ")
    return w, dict(weeks=n, mean_z=mhat, se=se, t_iid=t_iid, members=int(Z.notna().any().sum()))


def read(d, printed_mean_members=()):
    Z = load(d)
    w, s = word(Z)
    print(f"POOL word: {w}")
    if s is None:
        return w
    print(f"  weeks {s['weeks']}, members present {s['members']}, mean x/s {s['mean_z']:+.4f}, se {s['se']:.4f}, "
          f"ratio {s['mean_z']/s['se']:+.2f}, iid t {s['t_iid']:+.2f}  (description beside the word)")
    zw = Z.mean(axis=1).dropna()
    keep = Z.loc[~Z.index.isin(zw.nlargest(10).index)]
    print(f"  without best 10 weeks: mean x/s {ratio(keep):+.4f}")
    if set(printed_mean_members) >= set(Z.columns[Z.notna().any()]):
        for m in Z.columns[Z.notna().any()]:
            print(f"  {m}: opened weeks {int(Z[m].notna().sum())}, mean x/s {Z[m].mean():+.4f}")
        best = max(Z.columns[Z.notna().any()], key=lambda m: Z[m].mean())
        print(f"  without best member ({best}): mean x/s {ratio(Z.drop(columns=best)):+.4f}")
    for nm, mem in SECONDARY.items():
        Zs = Z[mem]
        if Zs.notna().any(axis=1).sum() >= 2 * BLOCK:
            n, mh, se, t, _ = stat(Zs)
            print(f"  mô tả {nm}: weeks {n}, mean x/s {mh:+.4f}, se {se:.4f}, ratio {mh/se:+.2f}")
    return w


def selftest():
    import tempfile, os
    rng = np.random.default_rng(1)
    for label, mu in (("null", 0.0), ("edge .18", 0.181), ("negative", -0.181)):
        with tempfile.TemporaryDirectory() as d:
            for m, s in SCALE.items():
                n = {"FLOW02": 52, "KRW": 80, "LEARN": 102}.get(m, 105)
                pd.DataFrame({"week": WEEKS[:n].strftime("%Y-%m-%d"), "x": rng.normal(mu * s, s, n)}).to_csv(os.path.join(d, f"{m}.csv"), index=False)
            print(f"[selftest {label}]"); read(d, printed_mean_members=tuple(SCALE))
    with tempfile.TemporaryDirectory() as d:
        for m in ("RANK", "CRASH"):
            pd.DataFrame({"week": WEEKS.strftime("%Y-%m-%d"), "x": 0.0}).to_csv(os.path.join(d, f"{m}.csv"), index=False)
        print("[selftest two members]"); assert read(d) == "quá ít"
    hits = []
    for i in range(200):                                  # size check under the null, all six present
        Z = pd.DataFrame(np.random.default_rng(100 + i).normal(0, 1, (105, 6)), index=WEEKS, columns=list(SCALE))
        hits.append(word(Z)[0] == "giữ")
    print(f"[selftest] null 'giữ' rate over 200 synthetic pools: {np.mean(hits):.3f} (one-sided nominal .025)")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["--selftest"] else read(sys.argv[1])
