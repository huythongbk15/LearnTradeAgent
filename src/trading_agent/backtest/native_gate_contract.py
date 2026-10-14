"""Pre-run native WFO gate definitions and strict measured-result binding."""

from statistics import median

from trading_agent.backtest.campaign_evidence import COMPARISONS, content_hash, finite

VERSION = "v1"
_STATIC = (
    ("outer_oos_net_return_positive", 0.0, ">", "percent"),
    ("outer_oos_sharpe_ge_080", 0.8, ">=", "ratio"),
    ("outer_oos_profit_factor_ge_120", 1.2, ">=", "ratio"),
    ("outer_oos_max_drawdown_le_10pct", 10.0, "<=", "percent"),
    ("outer_oos_calmar_ge_050", 0.5, ">=", "ratio"),
    ("dsr_ge_095", 0.95, ">=", "probability"),
    ("pbo_le_020", 0.2, "<=", "probability"),
    ("parameter_stability_ge_070", 0.7, ">=", "ratio"),
    ("positive_outer_folds_ge_60pct", 60.0, ">=", "percent"),
    ("zero_trade_fold_pct_le_50", 50.0, "<=", "percent"),
    ("cost_clearing_folds_above_chance_p_le_010", 0.1, "<=", "probability"),
    ("cost_clearing_single_window_le_60pct", 60.0, "<=", "percent"),
    ("drop_best_trade_net_pnl_positive", 0.0, ">", "quote_currency"),
    ("cost_2x_net_pnl_positive", 0.0, ">", "quote_currency"),
    ("cost_2x_profit_factor_gt_1", 1.0, ">", "ratio"),
    ("delay_1_bar_net_pnl_positive", 0.0, ">", "quote_currency"),
    ("delay_1_bar_profit_factor_gt_1", 1.0, ">", "ratio"),
    ("parameter_neighbors_net_pnl_positive", 0.0, ">", "quote_currency"),
)


def freeze_native_gate_contract(spec, *, run_holdout=False):
    gates = [
        dict(
            gate_id=name,
            threshold=value,
            comparison=comparison,
            unit=unit,
            source=f"WFOResult.gate_results.{name}",
            applicability="always",
        )
        for name, value, comparison, unit in _STATIC
    ]
    gates += [
        dict(
            gate_id="min_trades_per_pair_strategy_ge_30",
            threshold=float(spec.effective_min_oos_trades),
            comparison=">=",
            unit="trades",
            source="outer_results.total_trades",
            applicability="always",
        ),
        dict(
            gate_id="median_return_clears_cost_floor",
            threshold=None,
            comparison=">",
            unit="percent",
            threshold_formula="0.32 * median(positive_fold_trade_counts)",
            source="outer_results.test_returns",
            applicability="always",
        ),
    ]
    if run_holdout:
        gates += [
            dict(
                gate_id=name,
                threshold=1.0,
                comparison=">=",
                unit="boolean",
                source=f"final_holdout.{field}",
                applicability="run_holdout",
            )
            for name, field in (
                ("final_holdout_completed", "status"),
                ("final_holdout_frozen_window", "holdout_window"),
            )
        ]
    contract = dict(
        contract_version=1,
        policy_version=VERSION,
        run_holdout=run_holdout,
        min_oos_trades=spec.effective_min_oos_trades,
        gates=gates,
        requirements=["canonical_stats_verified", "sensitivity_verified"],
        cost_floor_pct=0.32,
    )
    contract["fingerprint"] = content_hash(contract)
    return contract


def verify_native_contract(contract):
    if not isinstance(contract, dict) or contract.get("fingerprint") != content_hash(
        {key: value for key, value in contract.items() if key != "fingerprint"}
    ):
        raise ValueError("native contract fingerprint mismatch")

    class Spec:
        effective_min_oos_trades = contract.get("min_oos_trades")

    if (
        type(Spec.effective_min_oos_trades) is not int
        or Spec.effective_min_oos_trades < 1
    ):
        raise ValueError("invalid native trade threshold")
    if type(contract.get("run_holdout")) is not bool:
        raise ValueError("invalid native holdout applicability")
    expected = freeze_native_gate_contract(Spec(), run_holdout=contract["run_holdout"])
    if contract != expected:
        raise ValueError("native contract differs from registered gate definitions")


def threshold_for(definition, trade_counts):
    if "threshold_formula" not in definition:
        return definition["threshold"]
    if any(type(value) is not int or value < 0 for value in trade_counts):
        raise ValueError("native cost threshold requires measured integer trade counts")
    positive = [value for value in trade_counts if value > 0]
    return 0.32 * median(positive) if positive else None


def validate_native_evidence(contract, evidence, trade_counts):
    """Return recomputed failures; INVALID stays fail-closed in diagnostics."""
    verify_native_contract(contract)
    if not isinstance(evidence, list) or len(evidence) != len(contract["gates"]):
        raise ValueError("missing/duplicate native gate evidence")
    measured = {row["gate_id"]: row for row in evidence}
    if len(measured) != len(evidence) or set(measured) != {
        g["gate_id"] for g in contract["gates"]
    }:
        raise ValueError("native gate set differs from frozen contract")
    failures = []
    for gate in contract["gates"]:
        row = measured[gate["gate_id"]]
        threshold = threshold_for(gate, trade_counts)
        if (
            row.get("threshold") != threshold
            or row.get("comparison") != gate["comparison"]
            or row.get("policy_version") != VERSION
        ):
            raise ValueError("native gate threshold/comparison/version drift")
        observed = row.get("observed")
        if observed is not None and not finite(observed):
            raise ValueError("native observed metric must be finite or explicit null")
        verdict = (
            "INVALID"
            if observed is None
            or threshold is None
            or (gate["gate_id"] == "final_holdout_frozen_window" and observed == 0)
            else "PASS"
            if COMPARISONS[gate["comparison"]](observed, threshold)
            else "FAIL"
        )
        if row.get("verdict") != verdict:
            raise ValueError("native verdict does not recompute")
        if verdict != "PASS":
            failures.append(
                dict(
                    gate_id=gate["gate_id"],
                    observed=observed,
                    threshold=threshold,
                    comparison=gate["comparison"],
                    unit=gate["unit"],
                    scope="native",
                    verdict=verdict,
                )
            )
    return failures


def native_evidence_from_result(contract, result):
    evidence = [
        dict(
            gate_id=gate.gate_id,
            observed=gate.observed_value if finite(gate.observed_value) else None,
            threshold=gate.threshold,
            comparison=gate.comparison,
            policy_version=gate.policy_version,
            verdict=gate.verdict,
        )
        for gate in result.gate_results
    ]
    # Native code historically marks some missing holdout observations INVALID.
    validate_native_evidence(
        contract,
        evidence,
        [fold.test_metrics["total_trades"] for fold in result.outer_results],
    )
    return evidence
