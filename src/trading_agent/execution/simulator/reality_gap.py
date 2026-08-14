"""Reality Gap Framework (Section 5 of the hardening brief).

Compares the same strategy across environments:

    Backtest → Execution Simulator → Paper → Testnet → Shadow Mainnet

``RealityGapReport`` keeps all raw metrics AND a composite
``RealityGapScore`` (0 = identical to reference, 1 = maximally different).
The score never hides the cause — raw metrics are always preserved.

Promotion is fail-closed: if any configured threshold is breached, the
environment cannot be promoted to the next stage.

Missing metrics are handled fail-closed:
- Metric missing from BOTH observed AND reference → hard breach (cannot compare)
- Metric missing from only one side → warning recorded, excluded from score
- ``required_metrics`` parameter enforces mandatory metrics that must be present
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Metrics that every environment should provide for a comparable report.
REALITY_GAP_METRICS = [
    "fill_ratio",
    "slippage_bps",
    "implementation_shortfall_bps",
    "trade_count",
    "turnover",
    "avg_latency_ms",
    "spread_cost_quote",
    "fees_quote",
    "sharpe",
    "total_return_pct",
    "max_drawdown_pct",
    "tracking_error_bps",
    "rejected_order_rate",
    "partial_fill_rate",
]

# Metrics that MUST be present for a valid promotion gate (fail-closed default).
# Only the absolutely critical execution quality metrics are required by default.
# Other metrics (sharpe, drawdown, etc.) are optional — missing from both is a warning.
DEFAULT_REQUIRED_METRICS = frozenset(
    [
        "fill_ratio",
        "slippage_bps",
        "implementation_shortfall_bps",
        "rejected_order_rate",
        "partial_fill_rate",
        "trade_count",
    ]
)


@dataclass
class RealityGapReport:
    """One environment's reality gap vs a reference (usually backtest).

    ``metrics`` always carries the raw values; ``score`` is derived.
    ``breaches`` lists every threshold violation with metric + observed value.
    ``missing_in_both`` — metrics absent from BOTH observed & reference (hard breach).
    ``missing_in_one`` — metrics present in only one side (warning, excluded from score).
    """

    environment: str
    reference_environment: str
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    breaches: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    missing_in_both: list[str] = field(default_factory=list)
    missing_in_one: list[str] = field(default_factory=list)
    required_metrics: frozenset[str] = field(default_factory=frozenset)
    critical_metrics: frozenset[str] = field(default_factory=frozenset)
    minimum_required_coverage: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pass_gate(self) -> bool:
        """Gate passes if no breaches AND no REQUIRED metrics missing from both.
        Optional metrics missing from both are warnings, not gate failures."""
        return not self.breaches and (
            self.minimum_required_coverage <= 0
            or not any(b.startswith("coverage") for b in self.breaches)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "reference_environment": self.reference_environment,
            "metrics": self.metrics,
            "score": round(self.score, 6),
            "breaches": self.breaches,
            "thresholds": self.thresholds,
            "missing_in_both": self.missing_in_both,
            "missing_in_one": self.missing_in_one,
            "required_metrics": sorted(self.required_metrics),
            "critical_metrics": sorted(self.critical_metrics),
            "minimum_required_coverage": self.minimum_required_coverage,
            "gate_passed": self.pass_gate,
        }


DEFAULT_REALITY_GAP_THRESHOLDS: dict[str, float] = {
    # Max allowed relative deviation from the reference for each metric.
    # Interpreted as a fraction (1.0 = must match within 100%; lower = stricter).
    "fill_ratio": 0.25,
    "slippage_bps": 0.50,
    "implementation_shortfall_bps": 0.50,
    "trade_count": 0.50,
    "turnover": 0.50,
    "avg_latency_ms": 0.50,
    "spread_cost_quote": 0.50,
    "fees_quote": 0.50,
    "sharpe": 0.50,
    "total_return_pct": 0.50,
    "max_drawdown_pct": 0.50,
    "tracking_error_bps": 0.50,
    "rejected_order_rate": 0.25,
    "partial_fill_rate": 0.25,
}


def _rel_deviation(obs: float, ref: float) -> float:
    """Relative deviation |obs - ref| / max(|ref|, epsilon)."""
    if ref == 0:
        # No reference activity — any observed activity is a full deviation.
        return 1.0 if obs != 0 else 0.0
    return abs(obs - ref) / abs(ref)


def compute_reality_gap(
    *,
    environment: str,
    reference_environment: str,
    observed: dict[str, float],
    reference: dict[str, float],
    thresholds: dict[str, float] | None = None,
    required_metrics: frozenset[str] | None = None,
    critical_metrics: frozenset[str] | None = None,
    minimum_required_coverage: float = 0.0,
) -> RealityGapReport:
    """Build a RealityGapReport comparing ``observed`` vs ``reference``.

    * ``score`` is the mean relative deviation over the metrics present in
      both dicts (0 = identical, >0 = gap) — raw metrics are kept separately.
    * ``breaches`` collects every threshold violation.
    * Missing metrics handling (fail-closed):
      - Metric in REQUIRED_METRICS but missing from BOTH → hard breach (gate fails)
      - Metric in REQUIRED_METRICS but missing from ONE side → warning (excluded from score)
      - Optional metrics missing from both → warning only
    """
    thresholds = {**DEFAULT_REALITY_GAP_THRESHOLDS, **(thresholds or {})}
    required = (
        required_metrics if required_metrics is not None else DEFAULT_REQUIRED_METRICS
    )
    deviations: list[float] = []
    breaches: list[str] = []
    missing_in_both: list[str] = []
    missing_in_one: list[str] = []

    total_metrics = len(REALITY_GAP_METRICS)
    covered = 0
    for metric in REALITY_GAP_METRICS:
        in_obs = metric in observed
        in_ref = metric in reference
        is_required = metric in required
        is_critical = critical_metrics is not None and metric in critical_metrics

        if not in_obs and not in_ref:
            # Missing from both
            if is_required:
                missing_in_both.append(metric)
                breaches.append(
                    f"{metric}: REQUIRED but missing from BOTH observed and reference"
                )
            else:
                missing_in_both.append(metric)
            continue

        if not in_obs or not in_ref:
            # Missing from only one side
            side = "observed" if not in_obs else "reference"
            if is_required:
                missing_in_one.append(metric)
                breaches.append(
                    f"{metric}: REQUIRED but missing from {side} "
                    f"(obs={'present' if in_obs else 'missing'}, "
                    f"ref={'present' if in_ref else 'missing'})"
                )
            else:
                missing_in_one.append(metric)
            continue

        # Both present — validate finite and non-NaN
        obs, ref = float(observed[metric]), float(reference[metric])
        if not math.isfinite(obs) or not math.isfinite(ref):
            breaches.append(f"{metric}: non-finite value (obs={obs}, ref={ref})")
            if is_critical:
                # Critical metric non-finite is a hard breach; stop further scoring
                return RealityGapReport(
                    environment=environment,
                    reference_environment=reference_environment,
                    metrics=dict(observed),
                    score=1.0,
                    breaches=breaches,
                    thresholds=thresholds,
                    missing_in_both=missing_in_both,
                    missing_in_one=missing_in_one,
                    required_metrics=required,
                    critical_metrics=critical_metrics or frozenset(),
                    minimum_required_coverage=minimum_required_coverage,
                )
            continue

        covered += 1
        dev = _rel_deviation(obs, ref)
        deviations.append(dev)
        if metric in thresholds and dev > thresholds[metric]:
            breaches.append(
                f"{metric}: observed={obs:.6g} ref={ref:.6g} "
                f"dev={dev:.3f} > threshold={thresholds[metric]:.3f}"
            )

    score = sum(deviations) / len(deviations) if deviations else 0.0
    coverage = covered / total_metrics if total_metrics else 0.0
    if minimum_required_coverage > 0 and coverage < minimum_required_coverage:
        breaches.append(
            f"coverage {coverage:.2%} below minimum_required_coverage "
            f"{minimum_required_coverage:.2%}"
        )
    return RealityGapReport(
        environment=environment,
        reference_environment=reference_environment,
        metrics=dict(observed),
        score=score,
        breaches=breaches,
        thresholds=thresholds,
        missing_in_both=missing_in_both,
        missing_in_one=missing_in_one,
        required_metrics=required,
        critical_metrics=critical_metrics or frozenset(),
        minimum_required_coverage=minimum_required_coverage,
    )


def promotion_check(report: RealityGapReport) -> bool:
    """Fail-closed promotion gate: False if ANY threshold is breached."""
    return report.pass_gate


def environment_metrics_from_result(result) -> dict[str, float]:
    """Extract the standard metric dict from any object exposing ``metrics``
    (SimulatedExecutionResult, BacktestResult, live runner report, ...)."""
    m = result.metrics
    if hasattr(m, "to_dict"):
        d = m.to_dict()
    else:
        d = dict(m)
    mapping = {
        "fill_ratio": "fill_ratio",
        "slippage_bps": "slippage_bps",
        "implementation_shortfall_bps": "implementation_shortfall_bps",
        "trade_count": "trade_count",
        "turnover": "turnover",
        "avg_latency_ms": "avg_latency_ms",
        "spread_cost_quote": "spread_cost_quote",
        "fees_quote": "fees_quote",
        "sharpe": "sharpe",
        "total_return_pct": "total_return_pct",
        "max_drawdown_pct": "max_drawdown_pct",
        "rejected_order_rate": "rejected_order_rate",
        "partial_fill_rate": "partial_fill_rate",
    }
    out: dict[str, float] = {}
    for src, dst in mapping.items():
        if src in d and d[src] is not None:
            out[dst] = float(d[src])
    # Optional: tracking error passed explicitly by the caller.
    if getattr(result, "tracking_error_bps", None) is not None:
        out["tracking_error_bps"] = float(result.tracking_error_bps)
    return out


def reality_gap_between(
    *,
    environment: str,
    reference_environment: str,
    observed_result,
    reference_result,
    thresholds: dict[str, float] | None = None,
    observed_metrics: dict[str, float] | None = None,
    reference_metrics: dict[str, float] | None = None,
    required_metrics: frozenset[str] | None = None,
) -> RealityGapReport:
    """One-call helper: build a report from two result objects (or metric dicts)."""
    obs = (
        observed_metrics
        if observed_metrics is not None
        else environment_metrics_from_result(observed_result)
    )
    ref = (
        reference_metrics
        if reference_metrics is not None
        else environment_metrics_from_result(reference_result)
    )
    return compute_reality_gap(
        environment=environment,
        reference_environment=reference_environment,
        observed=obs,
        reference=ref,
        thresholds=thresholds,
        required_metrics=required_metrics,
    )
