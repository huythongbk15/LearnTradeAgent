#!/usr/bin/env python
"""
A/B Test: LLM Context Enrichment vs Deterministic Baseline

Chay 3 modes trên historical data:
  Pass 1 (llm_on):  enable_backtest_mode() → ContextEnricher.enrich() → LLM
  Pass 2 (llm_off): USE_LLM=false → ContextEnricher.enrich() → deterministic fallback
  Pass 3 (replay):  ContextEnricher.replay() → read from ResearchMemory (no LLM)

So sánh:
  - confidence_adjustment (LLM vs deterministic)
  - anomaly_flags coverage
  - regime_tags consistency

Usage:
    python scripts/ab_test_context.py --symbol BTC/USDT --timeframe 1h \\
        --start 2024-01-01 --end 2024-06-01 --exchange binance

Output:
    JSON report + CSV comparison of per-bar contexts
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
import tempfile
import traceback
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.llm.context_enrichment import MarketContext

import click
import polars as pl

# ── Config ────────────────────────────────────────────────────────────────

DEFAULT_EXCHANGE = "binance"
MIN_BARS = 300


# ── AnalysisContext builder ───────────────────────────────────────────────


def _build_analysis_context(
    symbol: str,
    timeframe: str,
    row: dict[str, Any],
    series_dict: dict[str, list],
    idx: int,
) -> tuple[Any, dict[str, Any]]:
    """Build AnalysisContext + indicators dict from a DataFrame row."""
    from trading_agent.agents.base import AnalysisContext

    indicators: dict[str, Any] = {}
    for col, values in series_dict.items():
        if idx < len(values):
            val = values[idx]
            # Skip None/NaN indicator values (common in early bars with rolling windows)
            if val is not None and isinstance(val, float) and val != val:
                continue  # NaN check
            if val is not None:
                indicators[col] = val

    extra = {}
    for col in ("funding_rate", "open_interest", "buy_pressure", "sell_pressure",
                "cvd_short_window", "volatility_20", "volume_ratio_5_20"):
        if col in indicators:
            extra[col] = indicators.pop(col)

    indicators["_extra"] = extra

    ts = row.get("timestamp")
    if ts is None:
        ts = datetime.now(UTC)
    if hasattr(ts, "replace"):
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts

    close = float(row.get("close", row.get("mid", 0)) or 0)

    ctx = AnalysisContext(
        symbol=symbol,
        timeframe=timeframe,
        current_price=close,
        indicators=indicators,
    )

    return ctx, indicators


# ── Results dataclass ────────────────────────────────────────────────────


@dataclass
class ABTestResult:
    symbol: str
    timeframe: str
    start: str
    end: str
    num_bars: int
    llm_on_stats: dict[str, Any] = field(default_factory=dict)
    llm_off_stats: dict[str, Any] = field(default_factory=dict)
    replay_stats: dict[str, Any] = field(default_factory=dict)
    confidence_delta: list[float] = field(default_factory=list)
    anomaly_divergence: int = 0
    regime_divergence: int = 0
    avg_abs_confidence_delta: float = 0.0
    max_abs_confidence_delta: float = 0.0
    replay_matches_llm: int = 0
    replay_total: int = 0
    per_bar: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModeResult:
    contexts: list[dict[str, Any]] = field(default_factory=list)
    errors: int = 0


# ── Indicator computation ────────────────────────────────────────────────


def compute_strategy_indicators(df: pl.DataFrame, strategy_name: str) -> pl.DataFrame:
    """Compute indicators for a strategy using its compute_indicators method."""
    from trading_agent.strategies.base import get_strategy
    import trading_agent.strategies  # noqa: F401

    try:
        strategy_cls = get_strategy(strategy_name)
        strategy = strategy_cls({})
        if hasattr(strategy, "compute_indicators"):
            df = strategy.compute_indicators(df)
    except Exception:
        pass  # No strategy-specific indicators, use defaults

    # Ensure basic indicators exist
    if "rsi" not in df.columns:
        df = df.with_columns(pl.col("close").rolling_mean(14).alias("rsi"))
    if "ma_20" not in df.columns:
        df = df.with_columns(pl.col("close").rolling_mean(20).alias("ma_20"))
    if "ma_50" not in df.columns:
        df = df.with_columns(pl.col("close").rolling_mean(50).alias("ma_50"))

    return df


# ── A/B test runner ──────────────────────────────────────────────────────


def _run_mode(
    mode: str,
    df: pl.DataFrame,
    symbol: str,
    timeframe: str,
    enricher: Any,
    memory: Any,
) -> ModeResult:
    """Run ContextEnricher on all bars in one mode."""
    result = ModeResult()
    series_dict = {col: df[col].to_list() for col in df.columns}
    rows = df.to_dicts()
    batch_timestamps: list[datetime] = []

    # Pre-batch-retrieve for replay mode to avoid N SQLite round-trips
    replay_cache: list[Any] | None = None
    if mode == "replay" and memory is not None and hasattr(memory, "retrieve_batch"):
        all_ts: list[datetime] = []
        for row in rows:
            bar_ts = row.get("timestamp") or datetime.now(UTC)
            if hasattr(bar_ts, "replace"):
                bar_ts = bar_ts.replace(tzinfo=UTC) if bar_ts.tzinfo is None else bar_ts
            all_ts.append(bar_ts)
        replay_cache = memory.retrieve_batch(
            symbol, timeframe, all_ts, deterministic=False
        )

    for idx, row in enumerate(rows):
        try:
            ctx_obj, indicators = _build_analysis_context(
                symbol, timeframe, row, series_dict, idx
            )
            bar_ts = row.get("timestamp") or datetime.now(UTC)
            if hasattr(bar_ts, "replace"):
                bar_ts = bar_ts.replace(tzinfo=UTC) if bar_ts.tzinfo is None else bar_ts

            if mode == "replay" and memory is not None:
                if replay_cache is not None and replay_cache[idx] is not None:
                    ctx_result = replay_cache[idx]
                else:
                    ctx_result = enricher.replay(
                        ctx_obj, indicators=indicators,
                        bar_timestamp=bar_ts, symbol=symbol,
                        timeframe=timeframe, memory=memory,
                    )
            else:
                ctx_result = enricher.enrich(
                    ctx_obj, indicators=indicators,
                    symbol=symbol, timeframe=timeframe,
                )

            batch_timestamps.append(bar_ts)

            result.contexts.append({
                "bar_index": idx,
                "timestamp": bar_ts.isoformat(),
                "regime_tags": ctx_result.regime_tags,
                "anomaly_flags": list(ctx_result.anomaly_flags),
                "confidence_adjustment": ctx_result.confidence_adjustment,
                "reasoning": ctx_result.reasoning[:100],
            })
        except Exception:
            result.errors += 1
            if result.errors <= 3:
                print(f"  [{mode}] Error at bar {idx}: {sys.exc_info()[1]}", file=sys.stderr)

    if memory is not None and result.contexts and hasattr(memory, "store_batch"):
        contexts = [
            # Reconstruct MarketContext from the dict we stored
            _reconstruct_context(c) for c in result.contexts
        ]
        memory.store_batch(
            entries=contexts,
            symbol=symbol,
            timeframe=timeframe,
            timestamps=batch_timestamps,
            deterministic=(mode == "llm_off"),
        )

    return result


def _reconstruct_context(ctx_dict: dict[str, Any]) -> MarketContext:
    """Rebuild a MarketContext from per-bar dict for batch storage."""
    return MarketContext(
        regime_tags=ctx_dict["regime_tags"],
        anomaly_flags=ctx_dict["anomaly_flags"],
        cross_asset_signals={},
        confidence_adjustment=ctx_dict["confidence_adjustment"],
        reasoning=ctx_dict["reasoning"],
        details={},
    )


def _compute_stats(mode_result: ModeResult) -> dict[str, Any]:
    if not mode_result.contexts:
        return {"count": 0, "errors": mode_result.errors}

    adjustments = [c["confidence_adjustment"] for c in mode_result.contexts]
    anomalies = [a for c in mode_result.contexts for a in c["anomaly_flags"]]
    anomaly_counts = dict(Counter(anomalies))

    return {
        "count": len(mode_result.contexts),
        "errors": mode_result.errors,
        "avg_confidence_adjustment": sum(adjustments) / len(adjustments),
        "min_confidence_adjustment": min(adjustments),
        "max_confidence_adjustment": max(adjustments),
        "anomaly_counts": anomaly_counts,
        "total_anomalies": len(anomalies),
    }


def run_ab_test(
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    exchange: str = DEFAULT_EXCHANGE,
    start: str | None = None,
    end: str | None = None,
    strategy: str | None = None,
    tmp_dir: Path | None = None,
    skip_llm_on: bool = False,
    max_bars_llm: int = 50,
) -> ABTestResult:
    """Run A/B test comparing LLM enrichment vs deterministic baseline."""
    from trading_agent.data.storage import load_ohlcv
    from trading_agent.llm.context_enrichment import ContextEnricher
    from trading_agent.llm.research_memory import ResearchMemory

    tmp_dir = tmp_dir or Path(tempfile.mkdtemp(prefix="ab_test_"))
    memory = ResearchMemory(tmp_dir / "ab_test_memory.sqlite3")

    df = load_ohlcv(exchange, symbol, timeframe, start=start, end=end)

    if len(df) < MIN_BARS:
        raise ValueError(
            f"Not enough data: {len(df)} bars (need >= {MIN_BARS}). "
            "Try a longer date range or different symbol/timeframe."
        )

    if strategy:
        df = compute_strategy_indicators(df, strategy)
    else:
        df = df.with_columns([
            pl.col("close").rolling_mean(14).alias("rsi"),
            pl.col("close").rolling_mean(20).alias("ma_20"),
            pl.col("close").rolling_mean(50).alias("ma_50"),
            pl.col("close").rolling_mean(200).alias("ma_200"),
        ])

    print(f"Loaded {len(df)} bars for {symbol} {timeframe}")
    modes_str = "llm_off | replay" if skip_llm_on else "llm_on | llm_off | replay"
    print(f"Running modes: {modes_str}")
    print()

    # Pass 1: LLM ON (backtest mode)
    from trading_agent.agents.llm import enable_backtest_mode, disable_backtest_mode
    if skip_llm_on:
        llm_on = ModeResult(contexts=[])
    else:
        print("=== Pass 1: LLM ON (deterministic backtest mode) ===")
        df_llm = df.head(max_bars_llm)
        enable_backtest_mode(provider="openrouter", model="nvidia/nemotron-3-ultra-550b-a55b:free",
                             temperature=0.0, max_tokens=1000, seed=42, use_cache=False)
        # Override timeout to 60s for slower free-tier LLM responses
        import trading_agent.agents.llm as _llm_mod
        _llm_mod._BACKTEST_CONFIG["timeout"] = 60
        try:
            llm_on = _run_mode("llm_on", df_llm, symbol, timeframe, ContextEnricher(), memory)
        finally:
            disable_backtest_mode()
    llm_on_stats = _compute_stats(llm_on)
    if not skip_llm_on:
        print(f"  Completed: {len(llm_on.contexts)} bars, {llm_on.errors} errors")
        print(f"  Avg confidence adjustment: {llm_on_stats.get('avg_confidence_adjustment', 'N/A')}")

    # Pass 2: LLM OFF (deterministic fallback)
    print("=== Pass 2: LLM OFF (deterministic fallback) ===")
    os.environ["USE_LLM"] = "false"
    import importlib
    import trading_agent.agents.llm as llm_mod
    importlib.reload(llm_mod)
    from trading_agent.llm.context_enrichment import ContextEnricher as CE2
    llm_off = _run_mode("llm_off", df, symbol, timeframe, CE2(), memory)
    os.environ.pop("USE_LLM", None)
    importlib.reload(llm_mod)
    llm_off_stats = _compute_stats(llm_off)
    print(f"  Completed: {len(llm_off.contexts)} bars, {llm_off.errors} errors")
    print(f"  Avg confidence adjustment: {llm_off_stats.get('avg_confidence_adjustment', 'N/A')}")

    # Pass 3: Replay (read from memory, no LLM)
    print("=== Pass 3: Replay (read from ResearchMemory) ===")
    from trading_agent.llm.context_enrichment import ContextEnricher as CE3
    replay = _run_mode("replay", df, symbol, timeframe, CE3(), memory)
    replay_stats = _compute_stats(replay)
    print(f"  Completed: {len(replay.contexts)} bars, {replay.errors} errors")
    print(f"  Avg confidence adjustment: {replay_stats.get('avg_confidence_adjustment', 'N/A')}")

    # Comparison
    print()
    print("=== Comparison ===")

    result = ABTestResult(
        symbol=symbol,
        timeframe=timeframe,
        start=start or str(df["timestamp"].min()),
        end=end or str(df["timestamp"].max()),
        num_bars=len(df),
        llm_on_stats=llm_on_stats,
        llm_off_stats=llm_off_stats,
        replay_stats=replay_stats,
    )

    n_compare = min(len(llm_off.contexts), len(replay.contexts)) if skip_llm_on else min(len(llm_on.contexts), len(llm_off.contexts))
    confidence_deltas: list[float] = []
    anomaly_divergence = 0
    regime_divergence = 0
    replay_matches = 0
    replay_total = 0

    for i in range(n_compare):
        det_ctx = llm_off.contexts[i]
        rep_ctx = replay.contexts[i]

        if not skip_llm_on:
            llm_ctx = llm_on.contexts[i]
        else:
            llm_ctx = det_ctx  # When LLM skipped, llm_on == det

        delta = llm_ctx["confidence_adjustment"] - det_ctx["confidence_adjustment"]
        confidence_deltas.append(delta)

        if set(llm_ctx["anomaly_flags"]) != set(det_ctx["anomaly_flags"]):
            anomaly_divergence += 1
        if llm_ctx["regime_tags"] != det_ctx["regime_tags"]:
            regime_divergence += 1

        if (
            rep_ctx["confidence_adjustment"] == llm_ctx["confidence_adjustment"]
            and rep_ctx["regime_tags"] == llm_ctx["regime_tags"]
            and set(rep_ctx["anomaly_flags"]) == set(llm_ctx["anomaly_flags"])
        ):
            replay_matches += 1
        replay_total += 1

        per_bar_entry = {
            "bar_index": i,
            "timestamp": llm_ctx["timestamp"],
            "llm_confidence": llm_ctx["confidence_adjustment"],
            "det_confidence": det_ctx["confidence_adjustment"],
            "confidence_delta": delta,
            "llm_anomalies": llm_ctx["anomaly_flags"],
            "det_anomalies": det_ctx["anomaly_flags"],
            "llm_regime": llm_ctx["regime_tags"],
            "det_regime": det_ctx["regime_tags"],
            "replay_confidence": rep_ctx["confidence_adjustment"],
        }
        result.per_bar.append(per_bar_entry)

    result.confidence_delta = confidence_deltas
    result.anomaly_divergence = anomaly_divergence
    result.regime_divergence = regime_divergence
    result.avg_abs_confidence_delta = (
        sum(abs(d) for d in confidence_deltas) / len(confidence_deltas)
        if confidence_deltas else 0.0
    )
    result.max_abs_confidence_delta = max((abs(d) for d in confidence_deltas), default=0.0)
    result.replay_matches_llm = replay_matches
    result.replay_total = replay_total

    print(f"  Bars compared: {n_compare}")
    print(f"  Avg |confidence delta|: {result.avg_abs_confidence_delta:.4f}")
    print(f"  Max |confidence delta|: {result.max_abs_confidence_delta:.4f}")
    print(f"  Anomaly divergence: {anomaly_divergence}/{n_compare} bars")
    print(f"  Regime divergence: {regime_divergence}/{n_compare} bars")
    print(f"  Replay matches LLM: {replay_matches}/{replay_total}")

    if not skip_llm_on:
        llm_only_anomalies = set()
        for i in range(n_compare):
            llm_anoms = set(llm_on.contexts[i]["anomaly_flags"])
            det_anoms = set(llm_off.contexts[i]["anomaly_flags"])
            llm_only_anomalies |= (llm_anoms - det_anoms)

        if llm_only_anomalies:
            print(f"  LLM-only anomalies (missed by deterministic): {llm_only_anomalies}")

    return result


# ── CLI ───────────────────────────────────────────────────────────────────


@click.command()
@click.option("--symbol", "-s", default="BTC/USDT")
@click.option("--timeframe", "-t", default="1h")
@click.option("--exchange", "-e", default=DEFAULT_EXCHANGE)
@click.option("--start", default=None, help="Start date (YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYY-MM-DD)")
@click.option("--strategy", default=None, help="Strategy name for indicator computation")
@click.option("--output-dir", "-o", default=None, help="Output directory for results")
@click.option("--skip-llm-on", is_flag=True, help="Skip LLM pass (deterministic comparison only)")
@click.option("--max-bars-llm", default=50, type=int, help="Max bars for LLM pass (performance)")
@click.option("--quiet", "-q", is_flag=True, help="Suppress LLM provider error spam")
def main(symbol, timeframe, exchange, start, end, strategy, output_dir, skip_llm_on, max_bars_llm, quiet):
    """A/B test: LLM context enrichment vs deterministic baseline."""
    if quiet:
        for name in ("trading_agent", "trading_agent.agents.llm", "root"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="ab_test_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_ab_test(
            symbol=symbol, timeframe=timeframe, exchange=exchange,
            start=start, end=end, strategy=strategy, tmp_dir=out_dir,
            skip_llm_on=skip_llm_on, max_bars_llm=max_bars_llm,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    json_path = out_dir / "ab_test_report.json"
    csv_path = out_dir / "ab_test_per_bar.csv"

    with open(json_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "bar_index", "timestamp",
            "llm_confidence", "det_confidence", "confidence_delta",
            "llm_anomalies", "det_anomalies",
            "llm_regime", "det_regime", "replay_confidence",
        ])
        writer.writeheader()
        for row in result.per_bar:
            row_copy = dict(row)
            row_copy["llm_anomalies"] = ",".join(row_copy["llm_anomalies"])
            row_copy["det_anomalies"] = ",".join(row_copy["det_anomalies"])
            row_copy["llm_regime"] = json.dumps(row_copy["llm_regime"])
            row_copy["det_regime"] = json.dumps(row_copy["det_regime"])
            writer.writerow(row_copy)

    print(f"\nResults saved to: {out_dir}")
    print(f"  - {json_path.name}")
    print(f"  - {csv_path.name}")


if __name__ == "__main__":
    main()
