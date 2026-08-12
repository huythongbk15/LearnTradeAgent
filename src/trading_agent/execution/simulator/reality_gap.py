"""Reality Gap Framework (Section 5 of the hardening brief).

Compares the same strategy across environments:

    Backtest → Execution Simulator → Paper → Testnet → Shadow Mainnet

``RealityGapReport`` keeps all raw metrics AND a composite
``RealityGapScore`` (0 = identical to reference, 1 = maximally different).
The score never hides the cause — raw metrics are always preserved.

Promotion is fail-closed: if any configured threshold is breached, the
environment cannot be promoted to the next stage.
"""

from __future__ import annotations

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


@dataclass
class RealityGapReport:
    """One environment's reality gap vs a reference (usually backtest).

    ``metrics`` always carries the raw values; ``score`` is derived.
    ``breaches`` lists every threshold violation with metric + observed value.
    """

    environment: str
    reference_environment: str
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    breaches: list[str] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pass_gate(self) -> bool:
        return not self.breaches

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "reference_environment": self.reference_environment,
            "metrics": self.metrics,
            "score": round(self.score, 6),
            "breaches": self.breaches,
            "thresholds": self.thresholds,
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
) -> RealityGapReport:
    """Build a RealityGapReport comparing ``observed`` vs ``reference``.

    * ``score`` is the mean relative deviation over the metrics present in
      both dicts (0 = identical, >0 = gap) — raw metrics are kept separately.
    * ``breaches`` collects every threshold violation.
    """
    thresholds = {**DEFAULT_REALITY_GAP_THRESHOLDS, **(thresholds or {})}
    deviations: list[float] = []
    breaches: list[str] = []
    for metric in REALITY_GAP_METRICS:
        # Only metrics present in both environments are comparable.
        if metric not in observed or metric not in reference:
            continue
        obs, ref = float(observed[metric]), float(reference[metric])
        dev = _rel_deviation(obs, ref)
        deviations.append(dev)
        if metric in thresholds and dev > thresholds[metric]:
            breaches.append(
                f"{metric}: observed={obs:.6g} ref={ref:.6g} "
                f"dev={dev:.3f} > threshold={thresholds[metric]:.3f}"
            )
    score = sum(deviations) / len(deviations) if deviations else 0.0
    return RealityGapReport(
        environment=environment,
        reference_environment=reference_environment,
        metrics=dict(observed),
        score=score,
        breaches=breaches,
        thresholds=thresholds,
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
) -> RealityGapReport:
    """One-call helper: build a report from two result objects (or metric dicts)."""
    obs = observed_metrics if observed_metrics is not None else environment_metrics_from_result(observed_result)
    ref = reference_metrics if reference_metrics is not None else environment_metrics_from_result(reference_result)
    return compute_reality_gap(
        environment=environment,
        reference_environment=reference_environment,
        observed=obs,
        reference=ref,
        thresholds=thresholds,
    )