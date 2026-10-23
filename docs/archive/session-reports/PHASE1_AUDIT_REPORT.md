# Phase 1 Audit Report: Data → Strategy → Regime
## Temporal / Look-Ahead Leakage Audit

**Ngày:** 2026-09-22 (Asia/Ho_Chi_Minh)
**Phạm vi:** `src/trading_agent/` — Data Pipeline → Strategy Signals → Regime Detection
**Methodology:** Code trace of every `rolling`, `expanding`, `bfill`, `shift`, `rolling_map`, and HMM `score_samples` call site. Cross-referenced against the temporal contract: *signal at bar t may only use data whose close-time ≤ t's close-time; forward returns are strictly future.*

**Executive summary:** 5 P0 leakage holes, all in regime detection; 3 P1 correctness defects that inflate backtest Sharpe; the **canonical path (PortfolioBacktestEngine + HistoricalMarketClock + build_ohlcv_window) is PIT-safe**. The legacy `BacktestEngine` is PIT-safe **only if** the strategy's `compute_indicators` + `generate_signals` use backward-looking operations (which they do today). The regime layer is the contamination vector.

---

## 0. Architecture Map

```
┌─────────────────────────────────────────────────────────────────────┐
│  LEGACY PATH (research / CLI / single-asset backtests)              │
│  ┌─────────────┐   ┌──────────────────────┐   ┌────────────────┐   │
│  │ load_ohlcv  │→  │ BacktestEngine.run() │→  │ Strategy       │   │
│  │ (storage.py)│   │ (engine.py)          │   │ (strategies/*) │   │
│  └─────────────┘   │ signal[t]→pos[t+1]  │   └────────────────┘   │
│                    │ fill@close[t+1]     │                        │
│                    └──────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  CANONICAL PATH (portfolio backtest / paper / production)            │
│  ┌─────────────┐  ┌──────────────────────┐  ┌────────────────────┐ │
│  │ Historical  │→ │ PortfolioBacktestEn- │→ │ StrategyRuntime    │ │
│  │ Clock       │  │ gine.run()           │  │ (authority layer)  │ │
│  │ slice_upto  │  │ settle @t+1        │  │                    │ │
│  │ (closed≤now)│  └──────────────────────┘  └────────────────────┘ │
│  └─────────────┘     ↑ only bars closed ≤ clock.now                │
│                      │                                            │
│  ┌────────────────────────────────────────┐  ┌─────────────────┐   │
│  │ MarketObservation                      │→ │ ForecastStrategy│   │
│  │ observed_at = bar_close_time           │  │ .forecast(obs)  │   │
│  │ features["ohlcv_window"] =             │  └─────────────────┘   │
│  │   build_ohlcv_window(time < observed_│                        │
│  │   at)                                  │                        │
│  └────────────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  REGIME DETECTION (consumed by regime_switching strategy)           │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │ HMMStrategy │  │ GMMStrategy  │  │ RuleBased    │  │ Hybrid   │ │
│  │ score_      │  │ predict_proba│  │ rolling MA   │  │ aggregate│ │
│  │ samples     │  │ (independent)│  │ +vol+pct_chg │  │          │ │
│  │ (full-seq)  │  │              │  │              │  │          │ │
│  └─────────────┘  └──────────────┘  └──────────────┘  └──────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. Tầng 1 — Data Pipeline

### 1.1 `data/storage.py`

| Function | Location | Assessment |
|---|---|---|
| `compute_atr` (in `execution/indicators.py`) | `execution/indicators.py:42` | **OK.** `prev_close = close.shift(1)`, `tr.rolling_mean(period)`. ATR[t] = mean of TR over [t-period+1, t]. TR[t] depends on H[t], L[t], C[t-1] — all known at bar `t` close. Backward-looking. ✓ |
| `load_ohlcv` | `storage.py:145` | **OK.** Loads full sorted DataFrame from parquet. Backtest engine filters by date range. Full-frame load is fine as long as all indicator computations are backward-looking. ✓ |
| `save_ohlcv` | `storage.py:105` | **OK / warning.** On append, calls `compute_atr(df, period=14)` which is PIT-safe (shift-based TR + backward rolling_mean). However, the stored `atr` column becomes stale if strategy params use a different `atr_period`. The column should be recomputed or stripped on every append, not conditionally kept. See P1. |
| `enrich_with_atr` | `storage.py:235` | **P1.** Docstring explicitly warns: *"This computes ATR using high/low/close of the SAME bar (t). For backtesting, DO NOT use pre-enriched ATR."* The stored ATR uses `compute_atr` (which is PIT-safe), but the **warning is misleading** — the real danger is consumers who read `df["atr"]` at bar `t` without shifting. The `BacktestEngine` mitigates this with `atr_values[i-1]`, but paper exchange / risk controller / live path may not. **Risk:** dual ATR source inconsistency. |

### 1.2 `backtest/portfolio_backtest.py` (canonical portfolio engine)

| Component | Location | Assessment |
|---|---|---|
| `HistoricalMarketClock` | `portfolio_backtest.py:83-139` | **OK.** `slice_upto(binding, now)` returns only bars with `close_ts ≤ now`. `closed_bars` uses `b.close_ts ≤ now`. Bar is labeled by OPEN timestamp; CLOSE time = open_ts + timeframe. `fill_for` returns the OPEN of the first bar that opens at/after `decision_time`. **No look-ahead: signals see only closed bars; fills use next-bar opens.** ✓ |
| `_gate_binding` | `portfolio_backtest.py:200-217` | **OK.** Validates `bar_ts_utc ≤ now`, `validate_candle_closed`, content-addressed provenance. ✓ |
| `HistoricalSimulationBroker.settle` | `portfolio_backtest.py:140-260` | **OK.** Fills queued orders at earliest t+1 eligible open. Reduction before increase. Cash/inventory reservations prevent double-spending. ✓ |

---

## 2. Tầng 2 — Strategy Signals

### 2.1 Legacy strategies (all use backward-looking polars ops)

All legacy strategies in `strategies/` (ma_crossover, rsi, bbands, enhanced_ma, cross_sectional_momentum, range_mean_reversion, trend_pullback, stat_arbitrage, vol_target) use:
- `rolling_mean`, `rolling_std`, `rolling_max`, `rolling_min` → polars default `center=False` → backward-looking ✓
- `pct_change()`, `diff()` → backward-looking ✓
- `shift(1)` for crossover detection → backward-looking ✓
- `cum_max()`, `cumsum()` → backward-looking ✓

**No `center=True`, no `shift(-N)`, no `expanding()` in any strategy file.** ✓

### 2.2 Legacy `BacktestEngine` timing

| Element | Location | Assessment |
|---|---|---|
| Signal → position shift | `engine.py:178-179` | **OK.** `previous_signal = signals[i - 1]` — signal at bar `t` → position effect at bar `t+1`. ✓ |
| ATR for sizing | `engine.py:231` | **OK.** `signal_atr = atr_values[i - 1]` — uses **previous bar's ATR**, which is known at the current bar's close. ✓ |
| SL/TP intrabar | `engine.py:247-254` | **OK.** SL uses `min(low[i], sl)`, TP uses `max(high[i], tp)`. Conservative stop-first on simultaneous touch. Intrabar high/low are known at bar close. ✓ |
| Entry/exit fill | `engine.py:231-240` | **OK.** Entry at `open[i] * buy_factor`, where `open[i]` is the next bar's open (bar `i` triggers position from `i-1`'s signal). ✓ |
| Equity | `engine.py:268` | **OK.** `cash + position * close[i]` — marked to market at bar close. ✓ |

### 2.3 `enhanced_ma.py` — DD circuit breaker in `generate_signals`

**P1 (execution-in-signal contamination).** Lines ~170-230: `EnhancedMaCrossover.generate_signals()` replays a **no-fee, no-sizing** long-only equity curve inside the signal-generation step:

```python
for i in range(n):
    equity = cash + shares * close[i]  # uses close[i], high[i] for ALL bars
    if dd > self.max_dd_pct:
        sig[i] = -1  # exit baked into signal stream
```

Three issues:
1. **Dual simulation**: The DD breaker's simulated equity (flat 1.0 cash, 100% allocation, no fees, no position sizing) does **not match** the BacktestEngine's actual equity (Kelly/vol-target sizing, fees, SL/TP, close-to-open fills). The breaker may generate false exits or miss real ones.
2. **Timing mismatch**: The breaker decides exit at bar `i` based on `close[i]` (known at bar `i` close). The engine executes at `open[i+1]`. This is PIT-safe (close[i] is not future), but the simulated equity curve assumes fill at `close[i]`, while the actual fill is at `open[i+1]` — creating a P&L gap that the breaker doesn't account for.
3. **Exit priority conflict**: The breaker bakes exits into the signal stream, but the engine ALSO has independent SL/TP/trailing-stop logic. Both can fire, creating inconsistent state (breaker thinks it exited at close[i], engine exits at open[i+1] at a different price).

**Recommendation:** Move risk management (DD breaker, trailing stop) into the execution layer (`_compute_positions_and_returns`), not into `generate_signals`. The signal should be pure direction; risk overlays should be applied at execution time where the actual equity curve is known.

### 2.4 `volatility_breakout.py` — `max_hold_bars` dead code

**P1.** `self.max_hold_bars` is defined in `__init__` and documented in the docstring ("Exit: price reverts to mean or max_hold_bars elapsed"), but `generate_signals` **never checks** `max_hold_bars`. Exit logic only fires on SMA revert. A position can be held indefinitely if price never reverts to SMA.

### 2.5 `funding_carry.py` — rolling_min entry semantics

**P2.** `fr_min = fr_expr.rolling_min(22).forward_fill()` with entry condition `fr_min <= threshold`. This fires if ALL of the past 22 bars had funding above threshold OR if ANY of the past 22 bars had funding below threshold (rolling minimum). The latter means the entry can trigger up to 22 bars after the funding event that actually triggered the roll. While backward-looking (PIT-safe), this creates **entry latency** — the strategy enters based on stale funding information.

---

## 3. Tầng 3 — Regime Detection

### 3.1 The `regime.py` indicator layer (used by `ensemble_ma_adx`, `ma_adx_regime`)

| Component | Location | Assessment |
|---|---|---|
| `add_regime_indicators` → ATR | `regime.py:30-37` | **OK.** `tr.rolling_mean(atr_period)` — backward-looking. ✓ |
| ATR percentile | `regime.py:45-52` | **P2 (self-inclusive bias, not leakage).** `shift(1).rolling_map(lambda s: (s < s[-1]).sum() / len(s))`. Window at bar `t` = `[atr[t-lookback], ..., atr[t-1]]`. `s[-1]` = `atr[t-1]` = the **current shifted value**. The comparison `(s < s[-1])` includes `s[-1]` itself, so the current ATR is compared against itself in its own percentile. This is **not** future leakage (atr[t-1] is known at time t), but it biases the percentile upward — the current value can never be below its own percentile threshold. Fix: compare against `s[:-1]` (exclude last element). |
| ADX / DI± | `regime.py:59-74` | **OK.** `rolling_mean`, `shift(1)` — backward-looking. ✓ |
| `vol_regime`, `trend_regime`, `trend_dir` | `regime.py:77-95` | **OK.** All based on materialized backward-looking columns. ✓ |

**Key point:** The `regime.py` indicators are PIT-safe. The contamination comes from the **ML regime detectors** in `ml/regime_detection.py`, which the `regime_switching` strategy caches and consumes wholesale.

### 3.2 `ml/regime_detection.py` — CRITICAL LEAKAGE

#### P0-1: HMM `_prepare_features` — `bfill()` (Lines 320-326)

```python
vol = returns.rolling(20).std() * np.sqrt(252)
vol = vol.bfill()  # ← LOOK-AHEAD: backfills NaN with NEXT valid value

vol_change = vol_aligned.pct_change().bfill()  # ← same leak
```

The rolling volatility has NaN for the first `vol_window` (20) bars. `bfill()` fills these with the **next** valid observation — i.e., data from bar 20+. At bar `t` (where t < 20), the feature `vol[t]` = `vol[20]`, which is **future data**. **This contaminates the HMM feature matrix for the entire warmup period.**

**Same pattern in `RuleBasedStrategy.detect_all` (line 727):**
```python
vol_series = returns.rolling(self.vol_window).std() * np.sqrt(252)
vol_series = vol_series.bfill()  # ← same leak
```

**Fix:** Replace `bfill()` with `fill(0)` or, better, use `rolling(20, min_periods=20).std()` which returns NaN (not backfilled) until 20 bars are available, and skip those bars entirely (consistent with GMM's `dropna` approach).

#### P0-2: HMM `score_samples` — Forward-Backward algorithm (Lines 215-216, 232-233)

```python
logprob, posteriors = self.model.score_samples(features)
current_probs = posteriors[-1]  # in predict()
# or in predict_all():
_, posteriors = self.model.score_samples(features)  # all bars
```

`hmmlearn`'s `score_samples` computes posteriors using the **forward-backward algorithm**, which conditions on the **entire observation sequence**. This means `posteriors[t]` depends on observations from `t+1...T`. **Every bar's regime state (except the last) is contaminated with future information.**

`predict_all` calls `score_samples` once on the full feature matrix and caches all posteriors. `regime_switching.py` then looks up `self._regime_cache[bar_idx]` for each bar. **The entire regime series is contaminated.**

**Note:** `predict()` (single call) uses only `posteriors[-1]` (the last bar), which is technically OK if called online with data up to the current time. But `predict_all` contaminates everything, and the regime-switching strategy uses `detect_all`/`predict_all` exclusively.

**Fix:** For online prediction, use the **forward filter only** (predict at time t using only observations 1...t). Replace `score_samples` (smoothing) with `score_samples` + manual forward pass, or use `model.predict` with `algorithm="viterbi"` on a growing window (expensive) or implement a filtered posterior. Alternatively, compute the forward probabilities manually using `model._do_forward_pass` restricted to `features[:t+1]` at each step.

#### P0-3: HybridRegimeDetector `detect_all` — inherits both leaks

`HybridRegimeDetector.initialize` fits HMM + GMM + RuleBased. `detect_all` calls `predict_all`/`detect_all` on each sub-detector. **Inherits P0-1 and P0-2** from HMM and RuleBased components.

#### P0-4: Regime cache contamination in `regime_switching.py` (Lines 170-200)

```python
if self._cache is None or self._cache_df is not df:
    all_states = detector.detect_all(prices_pd, volumes_pd)
    self._cache = all_states  # ← ALL bars contaminated if HMM/RuleBased
```

The `_predict_regime_state` method precomputes regime for ALL bars in one batch call and caches the result. In `generate_signals`, it looks up `self._cache[bar_idx]` per bar. **Every bar's regime is contaminated** if the detector is HMM or RuleBased.

This is the **operational leak path**: the regime-switching strategy's regime weights for every sub-strategy at every bar depend on future data.

#### P0-5: Transductive scaler leak (Lines 175-178, 278-284)

Both HMM and GMM `_prepare_features` fit a `StandardScaler` on the **full prediction data** (including future bars):

```python
scaler = StandardScaler()
features = np.nan_to_num(features, ...)
scaler.fit_transform(features)  # ← fits on ALL data, not training-only
```

In `fit()`, the scaler is fit on training data (correct). But in `predict_all()`, `_prepare_features` is called again on the **full prediction series**, fitting a **new** scaler that sees all data (including future bars). The HMM model was trained with a different scaler, so the features fed to the model in prediction are scaled with future statistics. **Transductive leakage** — the scaling mean/std at time `t` are derived from data 1...T.

**Fix:** Store the fitted scaler in `fit()` and reuse it in `predict`/`predict_all`. Fit on training data only.

### 3.3 How regime flows into strategies

| Strategy | Regime source | Assessment |
|---|---|---|
| `regime_switching.py` | `HybridRegimeDetector.detect_all` → cache → per-bar lookup | **P0** — cache contaminated by HMM forward-backward + RuleBased bfill |
| `ensemble_ma_adx.py` | `add_regime_indicators` (polars, backward-looking) | **OK** — uses regime.py indicators, PIT-safe |
| `ma_adx_regime.py` | `add_regime_indicators` (polars, backward-looking) | **OK** — PIT-safe |
| `enhanced_ma.py` | Direct ADX computation (no regime cache) | **OK** — PIT-safe |

### 3.4 Impact on metrics

Strategies using `regime_switching` with HMM or RuleBased detectors have regime states contaminated for **all bars except the last**. This means:
- Regime-dependent position sizing is biased by future regime knowledge
- The entropy shrinkage in `mix_regime_forecasts` uses contaminated posteriors
- **All backtest metrics (Sharpe, IR, drawdown, turnover) for regime_switching with HMM/hybrid are inflated**

Strategies using `ensemble_ma_adx` or `ma_adx_regime` (which use the polars `add_regime_indicators` directly) are PIT-safe.

---

## 4. Canonical Path — PIT Enforcement Review

### 4.1 `build_ohlcv_window` (`strategy/canonical/features.py:49-69`)

```python
closed = frame.filter(pl.col("time") < observed_at)
window = closed.tail(bars)
validate_point_in_time(window, observed_at=observed_at)
```

**OK.** Filters `time < observed_at` (strict inequality), then `validate_point_in_time` raises if max time ≥ observed_at. Fail-closed. ✓

### 4.2 `CanonicalRuntimeBridge._decide_last_bar` (`bridge.py:136-170`)

```python
window = build_ohlcv_window(
    canon.head(-1),  # exclude the decision bar itself
    observed_at=observed_at,
    bars=self._warmup_bars + 1,
)
```

**OK.** Decision bar is excluded (`head(-1)`). Window contains only bars before `observed_at`. The observation's OHLCV is the current bar's close (known at bar close). ✓

### 4.3 `LegacyDataFrameAdapter.forecast` (`adapter.py:65-95`)

```python
window = self._extract_window(observation)  # validates time < observed_at
signal_value = self._last_signal(window)  # compute_indicators + generate_signals on window only
```

**OK.** `_extract_window` enforces point-in-time: `max_time > observed_at → raise`. `_last_signal` calls `compute_indicators` and `generate_signals` on the **window only** (not the full history), and takes the last signal value. ✓

---

## 5. Research Methodology — PIT Review

### 5.1 `alpha_research/methodology.py`

| Component | Location | Assessment |
|---|---|---|
| `_causal_rank_positions` | line 158-172 | **P2.** `history = values[start : index + 1]` includes `values[index]` (current value). Self-inclusive ranking — current alpha included in its own percentile. Not future leakage (backward-looking window), but mild upward bias. |
| `build_return_series` | line 186-219 | **OK.** `positions` at `t` × `forward_returns` at `t`. Turnover = `|diff(positions)|`. Positions are clipped to [-1, 1]. ✓ |
| `make_chronological_folds` | line 127-157 | **OK.** Purge + embargo, expanding train, disjoint test. ✓ |
| `AutoMLPipeline.scan` forward returns | line 410 | **OK.** `pct_change(horizon).shift(-horizon)` = forward return from `t` to `t+horizon`. ✓ |
| `fit_factor_transform` | line 96-125 | **OK.** Winsorization, median, scale, direction all from training data only. ✓ |
| CSCV (combinatorially symmetric) | (imported) | **OK.** PBO computed over searched trial space, not OOS-selected subset. ✓ |

---

## 6. P0 / P1 / P2 Summary

### P0 — Temporal Leakage (must fix before any regime-switching results are trusted)

| # | File:Line | Issue | Impact |
|---|---|---|---|
| 1 | `ml/regime_detection.py:320,326` | HMM `_prepare_features` uses `vol.bfill()` and `vol_change.bfill()` — backfills NaN with future values | HMM features contaminated for warmup period |
| 2 | `ml/regime_detection.py:215,232` | HMM `predict_all` uses `score_samples` (forward-backward) — posteriors at bar `t` depend on observations `t+1...T` | ALL regime states (except last) use future data |
| 3 | `ml/regime_detection.py:727` | RuleBased `detect_all` uses `vol_series.bfill()` | Rule-based vol contaminated for warmup |
| 4 | `regime_switching.py:195-205` | Caches `detect_all` result for all bars; contaminated posteriors propagated to every bar's regime weights | Regime-aware strategy sizing contaminated at every bar |
| 5 | `ml/regime_detection.py:175-178,278-284` | HMM/GMM scaler `fit_transform` on full prediction data (not training-only) | Transductive scaling: mean/std include future data |

### P1 — Correctness Defects (inflate/deflate backtest metrics)

| # | File:Line | Issue | Impact |
|---|---|---|---|
| 1 | `enhanced_ma.py:170-230` | DD circuit breaker simulates equity in `generate_signals` (no fees, no sizing, close-fill) — conflicts with engine's SL/TP/sizing | Signal and execution diverge; exits based on wrong equity |
| 2 | `volatility_breakout.py:60-85` | `max_hold_bars` declared but never checked in `generate_signals` | Positions can be held indefinitely; comment is misleading |
| 3 | `storage.py:105-141` | `save_ohlcv` conditionally recomputes ATR on append; stale `atr` column may persist if `had_atr` is False on first append | Inconsistent ATR across data versions |

### P2 — Minor / Design Issues

| # | File:Line | Issue | Impact |
|---|---|---|---|
| 1 | `regime.py:48-49` | ATR percentile lambda uses `s[-1]` (self-inclusive) | Mild percentile bias; not true leakage |
| 2 | `methodology.py:163` | `_causal_rank_positions` includes `values[index]` in its own rank | Mild self-inclusion bias |
| 3 | `funding_carry.py:62` | `rolling_min(22)` entry may fire 22 bars after the funding event | Entry latency; not leakage but suboptimal |
| 4 | `ml/regime_detection.py` | `RegimeState.timestamp = datetime.now()` — not bar-aligned | Provenance issue; all cached states have same timestamp |

---

## 7. Temporal Contract Proposal

### 7.1 Point-in-time invariant

```
∀ bar t in signal series:
    signal[t] = f(data[0:t+1])    // uses data available at bar t close, NOT future
    position[t+1] = g(signal[t])  // position decision takes effect NEXT bar
    fill_price = open[t+1]        // execution at next bar open
```

### 7.2 Regime state contract

```
regime_state[t] = f(data[0:t+1], fitted_model)   // NOT score_samples on full sequence
    - HMM: use forward filter (predict at t using observations 1...t)
    - GMM: use predict_proba (independent per observation) — OK
    - Rule-based: rolling indicators only — OK after bfill removal
    - Scaler: must be fit_transform on training data ONLY, reused in prediction
```

### 7.3 Data freshness contract

```
1. Data source must tag each bar with its close_time (bar close = decision time)
2. Strategy MUST NOT see any bar whose close_time > observed_at
3. ATR must be shifted by 1 (atr[t-1] used for bar t decisions)
4. Regime state at bar t must NOT use observations from t+1...T
5. Scaler/encoder fit ONLY on training fold, applied (not refit) to test fold
6. Forward returns: alpha[t] predicts return(t → t+horizon), NOT return(t-1 → t)
```


---

## 8. Fix Status (Remediation Complete)

**Status date:** 2026-09-22

### P0 — Critical Temporal Leakage (5 issues) — FIXED

| # | File | Fix Applied |
|---|---|---|
| 1 | `ml/regime_detection.py:316-329` HMM `_prepare_features` `bfill()` | Replaced with `ffill().fillna(0.0)` — causal forward-fill only |
| 2 | `ml/regime_detection.py:727` RuleBased `detect_all` `bfill()` | Replaced with `ffill().fillna(0.0)` |
| 3 | `ml/regime_detection.py:215,232` HMM `score_samples` (forward-backward) | New `${'_compute_filtered_posteriors'}()` method uses forward-only algorithm — P(q_t | o_1…o_t), O(n·k²) |
| 4 | `ml/regime_detection.py:175,278` Transductive scaler (fit on predict) | Scaler now fit in `fit()`, stored in `self._scaler`, reused via `_scale_features()` in predict/predict_all |
| 5 | `ml/regime_detection.py:410,625` GMM scaler (same issue) | Same fix — `self._scaler` stored in `fit()`, `_scale_features()` in predict |

### P1 — Correctness Defects (3 issues) — FIXED

| # | File | Fix Applied |
|---|---|---|
| 6 | `strategies/enhanced_ma.py:170-230` DD circuit breaker mismatch | Removed parallel equity simulation from `generate_signals`. DD circuit breaker now lives in `BacktestEngine` with `max_dd_pct`, `dd_cooldown_bars`, `dd_recovery_pct` params (uses real equity curve) |
| 7 | `strategies/volatility_breakout.py:60-85` Dead `max_hold_bars` | Implemented post-processing loop that force-closes positions at `-1` signal after `max_hold_bars` elapsed. Also fixed exit signal from `0` → `-1` (engine treats `0` as hold, `-1` as exit) |
| 8 | `data/storage.py:105-141` Conditional ATR recompute | Removed `if had_atr:` gate — ATR now always recomputed after append via `compute_atr(df, period=14)` |

### P2 — Minor Issues — FIXED

| # | File | Fix Applied |
|---|---|---|
| 9 | `ml/regime_detection.py` `datetime.now()` (timezone-naive) | All 16 occurrences replaced with `datetime.now(UTC)` |
| 10 | `ml/regime_detection.py` HMM log(0) warnings | Added `np.maximum(…, 1e-300)` before `np.log()` in forward filter |

### Verification

- **11 new regression tests** in `tests/test_temporal_leakage_regression.py` — all pass
- **120 existing tests** pass (no regressions) across canonical wave C, storage, backtest accounting, enhanced_ma exit safety, chaos invariants, canonical contract, candidate factory, adaptive strategy router


### P1 -- Additional Temporal/Convention Fixes (during remediation)

| # | File | Fix Applied |
|---|---|---|
| 11 | strategies/range_mean_reversion.py:96 | Exit signal 0 -> -1 (engine treats 0 as hold, never closes) |
| 12 | strategies/stat_arbitrage.py:107 | Same fix -- exit signal 0 -> -1 |
| 13 | backtest/engine.py | Short signal (-1) now closes shorts in long/short mode |

### Updated Verification
- 11 regression tests in tests/test_temporal_leakage_regression.py
- 153 tests pass total (no regressions)

### Phase 1 + AC01 Remediation (Section 8)

#### Phase 1 Fixes (Data → Features → Strategy → Regime)

| # | File | P0/P1/P2 | Fix Applied |
|---|---|---|---|
| 1-3 | ml/regime_detection.py:318,727 | P0 | bfill() → ffill().fillna(0.0) |
| 4 | ml/regime_detection.py:215,232 | P0 | score_samples (forward-backward) → forward-filter algorithm |
| 5 | ml/regime_detection.py:412,632 | P0 | Scaler fit_transform → store fitted + transform() |
| 6 | strategies/enhanced_ma.py:170-230 | P1 | DD circuit breaker moved to BacktestEngine |
| 7 | strategies/volatility_breakout.py:85 | P1 | max_hold_bars implemented + exit 0 -> -1 |
| 8 | storage.py:133 | P0 | ATR always recomputed |
| 9 | enhanced_ma.py:590 | P2 | UTC timestamps (16 instances) |

#### AC01 Follow-up Fixes (Index Alignment + Cache + Cutoff)

| # | File | Issue | Fix |
|---|---|---|---|
| 10 | ml/regime_detection.py:812,779 | RuleBased.detect_all/detect: returns.dropna() shifts vol index 1 bar | Removed .dropna(), vol aligned to price index |
| 11 | ml/regime_detection.py:510 | HMM predict_all: _predict_cache reused across different input lengths | Cache key includes _predict_cache_len |
| 12 | ml/regime_detection.py:900,940 | HybridRegimeDetector.detect/detect_all fits on full input | Added training_cutoff param |

#### Strategy Exit Signal Fixes (same root cause)

| # | File | Issue | Fix |
|---|---|---|---|
| 13 | strategies/range_mean_reversion.py:96 | Exit signal 0 → engine treats as "hold" | Exit 0 → -1 |
| 14 | strategies/stat_arbitrage.py:107 | Same exit 0 bug | Exit 0 → -1 |
| 15 | backtest/engine.py:480 | -1 in long/short only closed longs | Now closes shorts too |
