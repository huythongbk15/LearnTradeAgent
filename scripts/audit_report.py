#!/usr/bin/env python
"""SelectionAudit Analysis CLI — summarize tournament routing decisions.

Reads the SQLite audit database produced by StrategyTournament and emits
a human-readable report:

  - Strategy switch frequency & champion tenure
  - Regime coverage (how often each regime tag appears)
  - Confidence adjustment distribution (mean, min, max)
  - Anomaly flag frequency
  - Shadow Sharpe statistics (challenger vs incumbent)
  - Posterior entropy summary (regime uncertainty)

Usage::

    python scripts/audit_report.py /path/to/tournament_audit.sqlite3 \\
        --symbol BTC/USDT --start 2024-07-01 --end 2024-08-15

Output: JSON + Markdown report to stdout (or --output-file).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_agent.authority.selection_audit import SelectionAudit


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def generate_report(
    audit_path: Path,
    *,
    symbol: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Generate a summary report from SelectionAudit entries."""
    audit = SelectionAudit(audit_path)
    entries = audit.query(
        symbol=symbol,
        start=start,
        end=end,
        limit=100_000,
    )
    audit.close()

    if not entries:
        return {"error": "No entries found for the given filters", "entry_count": 0}

    # ── Strategy switch analysis ──────────────────────────────────────────
    strategy_sequence = [
        e.chosen_strategy_id for e in entries
        if e.chosen_strategy_id is not None
    ]
    switches = sum(
        1 for i in range(1, len(strategy_sequence))
        if strategy_sequence[i] != strategy_sequence[i - 1]
    )

    # Champion tenure (bars per champion)
    tenure_counter: Counter[str] = Counter()
    current_champion = None
    current_tenure = 0
    for sid in strategy_sequence:
        if sid != current_champion:
            if current_champion is not None:
                tenure_counter[current_champion] = max(
                    tenure_counter[current_champion], current_tenure
                )
            current_champion = sid
            current_tenure = 1
        else:
            current_tenure += 1
    if current_champion is not None:
        tenure_counter[current_champion] = max(
            tenure_counter[current_champion], current_tenure
        )

    champion_counts: Counter[str] = Counter(strategy_sequence)

    # ── Regime coverage ───────────────────────────────────────────────────
    all_regime_tags: Counter[str] = Counter()
    for e in entries:
        for k, v in e.regime_tags.items():
            all_regime_tags[f"{k}:{v}"] += 1
    # Also count top-level tag keys
    tag_keys: Counter[str] = Counter()
    for e in entries:
        for k in e.regime_tags:
            tag_keys[k] += 1

    # ── Anomaly flags ─────────────────────────────────────────────────────
    anomaly_counter: Counter[str] = Counter()
    for e in entries:
        for flag in e.anomaly_flags:
            anomaly_counter[flag] += 1
    anomaly_rate = (
        sum(1 for e in entries if e.anomaly_flags) / len(entries) * 100
    )

    # ── Confidence adjustment stats ───────────────────────────────────────
    confidences = [e.confidence_adjustment for e in entries]
    conf_mean = sum(confidences) / len(confidences)
    conf_min = min(confidences)
    conf_max = max(confidences)
    conf_below_1 = sum(1 for c in confidences if c < 1.0)
    conf_above_1 = sum(1 for c in confidences if c > 1.0)

    # ── Posterior entropy stats ───────────────────────────────────────────
    entropies = [e.posterior_entropy for e in entries]
    entropy_mean = sum(entropies) / len(entropies)
    entropy_max = max(entropies)

    # ── Shadow Sharpe stats ───────────────────────────────────────────────
    sharpes = [s for s in [e.shadow_sharpe for e in entries] if s is not None]
    sharpe_mean = sum(sharpes) / len(sharpes) if sharpes else None
    sharpe_min = min(sharpes) if sharpes else None
    sharpe_max = max(sharpes) if sharpes else None

    deltas = [
        d for d in [e.shadow_sharpe_delta_vs_incumbent for e in entries]
        if d is not None
    ]
    delta_mean = sum(deltas) / len(deltas) if deltas else None

    return {
        "entry_count": len(entries),
        "date_range": {
            "start": min(e.observed_at for e in entries).isoformat(),
            "end": max(e.observed_at for e in entries).isoformat(),
        },
        "strategy_switches": {
            "total_switches": switches,
            "switch_rate_pct": round(switches / max(len(strategy_sequence) - 1, 1) * 100, 2),
            "champion_distribution": dict(champion_counts),
            "max_tenure_bars": dict(tenure_counter),
        },
        "regime_coverage": {
            "tag_combinations": dict(all_regime_tags),
            "tag_keys": dict(tag_keys),
        },
        "anomaly_analysis": {
            "flag_counts": dict(anomaly_counter),
            "anomaly_rate_pct": round(anomaly_rate, 2),
        },
        "confidence_adjustment": {
            "mean": round(conf_mean, 4),
            "min": round(conf_min, 4),
            "max": round(conf_max, 4),
            "below_1_count": conf_below_1,
            "above_1_count": conf_above_1,
        },
        "posterior_entropy": {
            "mean": round(entropy_mean, 4),
            "max": round(entropy_max, 4),
        },
        "shadow_sharpe": {
            "mean": round(sharpe_mean, 4) if sharpe_mean is not None else None,
            "min": round(sharpe_min, 4) if sharpe_min is not None else None,
            "max": round(sharpe_max, 4) if sharpe_max is not None else None,
            "challenger_vs_incumbent_mean_delta": (
                round(delta_mean, 4) if delta_mean is not None else None
            ),
        },
        "reasons": dict(
            Counter(e.reason for e in entries).most_common(10)
        ),
    }


def _format_markdown(report: dict[str, Any]) -> str:
    """Render report as markdown for terminal readability."""
    lines = [
        "# SelectionAudit Report",
        "",
        f"**Entries analyzed:** {report['entry_count']}",
        f"**Date range:** {report['date_range']['start']} → {report['date_range']['end']}",
        "",
        "## Strategy Switches",
        f"- Total switches: {report['strategy_switches']['total_switches']}",
        f"- Switch rate: {report['strategy_switches']['switch_rate_pct']}%",
    ]

    dist = report["strategy_switches"]["champion_distribution"]
    if dist:
        lines.append("- Champion distribution:")
        for sid, count in sorted(dist.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{sid}`: {count} bars")

    tenure = report["strategy_switches"]["max_tenure_bars"]
    if tenure:
        lines.append("- Max tenure:")
        for sid, tenure_bars in sorted(tenure.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{sid}`: {tenure_bars} bars")

    lines.extend([
        "",
        "## Anomaly Flags",
        f"- Anomaly rate: {report['anomaly_analysis']['anomaly_rate_pct']}%",
    ])
    for flag, count in report["anomaly_analysis"]["flag_counts"].items():
        lines.append(f"  - `{flag}`: {count} bars")

    ca = report["confidence_adjustment"]
    lines.extend([
        "",
        "## Confidence Adjustment",
        f"- Mean: {ca['mean']}  (min {ca['min']} / max {ca['max']})",
        f"- Below 1.0: {ca['below_1_count']} bars  |  Above 1.0: {ca['above_1_count']} bars",
    ])

    pe = report["posterior_entropy"]
    lines.extend([
        "",
        "## Regime Uncertainty (Posterior Entropy)",
        f"- Mean: {pe['mean']}  (max {pe['max']})",
    ])

    ss = report["shadow_sharpe"]
    lines.extend([
        "",
        "## Shadow Sharpe",
    ])
    if ss["mean"] is not None:
        lines.append(
            f"- Mean: {ss['mean']}  (min {ss['min']} / max {ss['max']})"
        )
    else:
        lines.append("- No shadow Sharpe data available")
    if ss["challenger_vs_incumbent_mean_delta"] is not None:
        lines.append(
            f"- Challenger Δ vs incumbent: {ss['challenger_vs_incumbent_mean_delta']}"
        )

    reasons = report["reasons"]
    if reasons:
        lines.extend(["", "## Top Routing Reasons"])
        for reason, count in list(reasons.items())[:5]:
            lines.append(f"  - `{reason}`: {count}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SelectionAudit analysis CLI"
    )
    parser.add_argument(
        "db_path", type=Path,
        help="Path to tournament_audit.sqlite3",
    )
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--start", type=_parse_dt, default=None)
    parser.add_argument("--end", type=_parse_dt, default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument("--format", choices=["json", "markdown", "both"],
                        default="both")
    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"ERROR: database not found: {args.db_path}", file=sys.stderr)
        return 1

    report = generate_report(
        args.db_path,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
    )

    if args.format in ("json", "both"):
        json_str = json.dumps(report, indent=2, default=str)
        if args.output:
            args.output.write_text(json_str)
            print(f"JSON report written to {args.output}")
        else:
            print(json_str)

    if args.format in ("markdown", "both"):
        md = _format_markdown(report)
        if args.output:
            (args.output.with_suffix(".md")).write_text(md)
            print(f"Markdown report written to {args.output.with_suffix('.md')}")
        else:
            if args.format == "both":
                print("\n" + "=" * 60 + "\n")
            print(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
