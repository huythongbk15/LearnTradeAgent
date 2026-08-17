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

import numpy as np
from scipy import stats as sp_stats

from trading_agent.execution.simulator.calibration_provenance import (
    CalibrationObservation,
    CalibrationProfile,
    CalibrationSource,
)

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


@dataclass(frozen=True)
class DistributionSummary:
    sample_count: int
    mean: float
    p50: float
    p90: float
    p95: float
    p99: float
    cvar95: float

    @classmethod
    def from_values(cls, values: tuple[float, ...]) -> "DistributionSummary":
        sample = np.asarray(values, dtype=float)
        sample = sample[np.isfinite(sample)]
        if sample.size == 0:
            raise ValueError("distribution sample must contain finite values")
        p50, p90, p95, p99 = np.quantile(sample, [0.50, 0.90, 0.95, 0.99])
        tail = sample[sample >= p95]
        return cls(
            sample_count=int(sample.size),
            mean=float(np.mean(sample)),
            p50=float(p50),
            p90=float(p90),
            p95=float(p95),
            p99=float(p99),
            cvar95=float(np.mean(tail)),
        )


@dataclass(frozen=True)
class ExecutionDistributionEvidence:
    profile_id: str
    source: CalibrationSource
    samples: dict[str, tuple[float, ...]]
    summaries: dict[str, DistributionSummary]

    @classmethod
    def from_observations(
        cls,
        profile: CalibrationProfile,
        observations: tuple[CalibrationObservation, ...],
    ) -> "ExecutionDistributionEvidence":
        if not observations:
            raise ValueError("execution distribution evidence must not be empty")
        if any(observation.source != profile.source for observation in observations):
            raise ValueError("observation source does not match calibration profile")
        samples: dict[str, tuple[float, ...]] = {
            "latency_ms": tuple(obs.fill_latency_ms for obs in observations),
            "slippage_bps": tuple(obs.slippage_bps for obs in observations),
            "fill_ratio": tuple(
                obs.filled_qty / obs.requested_qty for obs in observations
            ),
            "partial_fill_probability": tuple(
                float(obs.partial_fills > 0 or obs.filled_qty < obs.requested_qty)
                for obs in observations
            ),
            "time_to_fill_ms": tuple(obs.fill_latency_ms for obs in observations),
            "adverse_selection_100ms_bps": tuple(
                obs.adverse_selection_100ms_bps for obs in observations
            ),
            "adverse_selection_1s_bps": tuple(
                obs.adverse_selection_1s_bps for obs in observations
            ),
            "adverse_selection_5s_bps": tuple(
                obs.adverse_selection_5s_bps for obs in observations
            ),
            "adverse_selection_30s_bps": tuple(
                obs.adverse_selection_30s_bps for obs in observations
            ),
        }
        return cls(
            profile_id=profile.profile_id,
            source=profile.source,
            samples=samples,
            summaries={
                metric: DistributionSummary.from_values(values)
                for metric, values in samples.items()
            },
        )


@dataclass(frozen=True)
class DistributionGapResult:
    metric: str
    statistic: float
    threshold: float
    wasserstein: float
    quantile_gap: float
    simulator: DistributionSummary
    observed_exchange: DistributionSummary

    @property
    def passed(self) -> bool:
        return math.isfinite(self.statistic) and self.statistic <= self.threshold


@dataclass(frozen=True)
class DistributionRealityGapReport:
    stage: str
    simulator_profile_id: str | None
    observed_profile_id: str | None
    results: tuple[DistributionGapResult, ...]
    missing_required: tuple[str, ...]
    breaches: tuple[str, ...]
    observed_source: CalibrationSource | None

    @property
    def pass_gate(self) -> bool:
        return (
            not self.missing_required
            and not self.breaches
            and self.observed_source is not None
            and self.observed_source != CalibrationSource.SYNTHETIC
        )


DEFAULT_DISTRIBUTION_THRESHOLDS_BY_STAGE: dict[str, dict[str, float]] = {
    "PAPER": {"default": 1.00},
    "TESTNET": {"default": 0.75},
    "SHADOW": {"default": 0.50},
    "LIVE": {"default": 0.35},
}

DEFAULT_REQUIRED_DISTRIBUTIONS = frozenset(
    {
        "latency_ms",
        "slippage_bps",
        "fill_ratio",
        "partial_fill_probability",
        "time_to_fill_ms",
        "adverse_selection_100ms_bps",
        "adverse_selection_1s_bps",
        "adverse_selection_5s_bps",
        "adverse_selection_30s_bps",
    }
)


def _distribution_distance(
    simulator: tuple[float, ...],
    observed: tuple[float, ...],
) -> tuple[float, float, float]:
    sim = np.asarray(simulator, dtype=float)
    obs = np.asarray(observed, dtype=float)
    sim_summary = DistributionSummary.from_values(simulator)
    obs_summary = DistributionSummary.from_values(observed)
    reference_scale = max(
        abs(obs_summary.p50),
        abs(obs_summary.p95 - obs_summary.p50),
        float(np.std(obs)),
        1e-9,
    )
    wasserstein = float(sp_stats.wasserstein_distance(sim, obs) / reference_scale)
    quantile_gap = (
        max(
            abs(sim_summary.p50 - obs_summary.p50),
            abs(sim_summary.p90 - obs_summary.p90),
            abs(sim_summary.p95 - obs_summary.p95),
            abs(sim_summary.p99 - obs_summary.p99),
            abs(sim_summary.cvar95 - obs_summary.cvar95),
        )
        / reference_scale
    )
    return max(wasserstein, quantile_gap), wasserstein, float(quantile_gap)


def compute_distributional_reality_gap(
    *,
    stage: str,
    simulator: ExecutionDistributionEvidence | None,
    observed_exchange: ExecutionDistributionEvidence | None,
    required_metrics: frozenset[str] = DEFAULT_REQUIRED_DISTRIBUTIONS,
    thresholds: dict[str, float] | None = None,
) -> DistributionRealityGapReport:
    """Compare simulator vs exchange distributions; missing critical data fails closed."""

    stage_name = stage.upper()
    if stage_name not in DEFAULT_DISTRIBUTION_THRESHOLDS_BY_STAGE:
        raise ValueError(f"unsupported promotion stage: {stage}")
    stage_thresholds = {
        **DEFAULT_DISTRIBUTION_THRESHOLDS_BY_STAGE[stage_name],
        **(thresholds or {}),
    }
    missing: list[str] = []
    breaches: list[str] = []
    results: list[DistributionGapResult] = []
    if simulator is None or observed_exchange is None:
        missing.extend(sorted(required_metrics))
    else:
        if observed_exchange.source == CalibrationSource.SYNTHETIC:
            breaches.append("observed exchange distribution source is SYNTHETIC")
        for metric in sorted(required_metrics):
            if (
                metric not in simulator.samples
                or metric not in observed_exchange.samples
            ):
                missing.append(metric)
                continue
            statistic, wasserstein, quantile_gap = _distribution_distance(
                simulator.samples[metric], observed_exchange.samples[metric]
            )
            threshold = float(stage_thresholds.get(metric, stage_thresholds["default"]))
            result = DistributionGapResult(
                metric=metric,
                statistic=statistic,
                threshold=threshold,
                wasserstein=wasserstein,
                quantile_gap=quantile_gap,
                simulator=simulator.summaries[metric],
                observed_exchange=observed_exchange.summaries[metric],
            )
            results.append(result)
            if not result.passed:
                breaches.append(
                    f"{metric}: distribution gap {statistic:.6f} > {threshold:.6f}"
                )
    if missing:
        breaches.append("missing required distribution evidence: " + ", ".join(missing))
    return DistributionRealityGapReport(
        stage=stage_name,
        simulator_profile_id=simulator.profile_id if simulator else None,
        observed_profile_id=observed_exchange.profile_id if observed_exchange else None,
        results=tuple(results),
        missing_required=tuple(missing),
        breaches=tuple(breaches),
        observed_source=observed_exchange.source if observed_exchange else None,
    )


def promotion_check(
    report: RealityGapReport,
    distribution_report: DistributionRealityGapReport | None = None,
) -> bool:
    """Fail closed on scalar breaches and any required distribution evidence."""

    return report.pass_gate and (
        distribution_report is None or distribution_report.pass_gate
    )


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
