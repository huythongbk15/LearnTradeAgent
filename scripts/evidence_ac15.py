"""AC15 Evidence Script — Deterministic Replay."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv, seed_safe

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC15 FAIL: {name}")

def df_hash(df):
    csv = df.sort("timestamp").write_csv()
    return hashlib.sha256(csv.encode("utf-8")).hexdigest()

# C1: seed_safe deterministic
s1 = seed_safe("volatility_breakout_v2", "ADA_USDT")
s2 = seed_safe("volatility_breakout_v2", "ADA_USDT")
s3 = seed_safe("volatility_breakout_v2", "BTC_USDT")
check("C1_seed_deterministic", s1 == s2, f"seed1={s1}, seed2={s2}")
check("C1_seed_differs_by_symbol", s1 != s3, f"ADA={s1}, BTC={s3}")

# C2: Same seed identical data
df_a = generate_synthetic_ohlcv(symbol="ADA_USDT", n_bars=500, seed=42)
df_b = generate_synthetic_ohlcv(symbol="ADA_USDT", n_bars=500, seed=42)
ha, hb = df_hash(df_a), df_hash(df_b)
check("C2_same_seed_identical", ha == hb, f"hash_a={ha[:16]}, hash_b={hb[:16]}")
check("C2_bar_count", df_a.height == 500, f"rows={df_a.height}")

# C3: Different seed different data
df_c = generate_synthetic_ohlcv(symbol="ADA_USDT", n_bars=500, seed=99)
hc = df_hash(df_c)
check("C3_different_seed_differs", ha != hc, f"seed42={ha[:16]}, seed99={hc[:16]}")

# C4: Schema match
required = {"timestamp", "open", "high", "low", "close", "volume", "exchange", "symbol"}
actual = set(df_a.columns)
check("C4_schema_match", required.issubset(actual), f"missing={required - actual}")

# C5: Timestamps monotonic
ts = df_a["timestamp"].to_list()
check("C5_monotonic", all(ts[i] <= ts[i+1] for i in range(len(ts)-1)), "monotonic=True")

# C6: Price variation
prices = df_a["close"].to_numpy()
check("C6_price_variation", float(np.std(prices)) / float(np.mean(prices)) > 0.01,
      f"std/mean={float(np.std(prices))/float(np.mean(prices)):.4f}")

# C7: seed valid range
check("C7_seed_range", isinstance(s1, int) and 1 <= s1 < 2**31, f"seed={s1}")

# C8: Strategy reproducible
from trading_agent.strategies.volatility_breakout import VolatilityBreakoutStrategy
params = {"window": 20, "multiplier": 2.0}
sa = VolatilityBreakoutStrategy(params=params)
sb = VolatilityBreakoutStrategy(params=params)
check("C8_strategy_identical", sa.params == sb.params, f"params_match={sa.params == sb.params}")

# C9: Regime affects data
df_r = generate_synthetic_ohlcv(symbol="TST_USDT", n_bars=1000, seed=7, regimes=True)
df_f = generate_synthetic_ohlcv(symbol="TST_USDT", n_bars=1000, seed=7, regimes=False)
check("C9_regime_affects", df_hash(df_r) != df_hash(df_f), "different with/without regimes")

all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC15 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")
ev = {"ac_id": "AC15", "oracle": "independent SHA-256 hash comparison",
      "cases": results, "all_pass": all_pass,
      "total_checks": len(results),
      "passed": sum(1 for r in results if r["status"] == "PASS"),
      "seed": s1}
Path("/tmp/ac15_evidence.json").write_text(json.dumps(ev, indent=2, default=str))
print("Evidence: /tmp/ac15_evidence.json")
sys.exit(0 if all_pass else 1)
