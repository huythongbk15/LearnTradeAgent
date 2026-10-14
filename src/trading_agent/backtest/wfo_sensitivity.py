"""Preregistered, complete selected-fold stress execution and raw attribution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import median


def sensitivity_requests(spec, outers, folds):
    from trading_agent.backtest.tournament import (
        SCENARIO_BASE,
        SCENARIO_DOUBLE,
        SCENARIO_SLIPPAGE_STRESS,
    )

    if (
        not folds
        or len({f.fold_id for f in folds}) != len(folds)
        or [o.fold_id for o in outers] != [f.fold_id for f in folds]
    ):
        raise ValueError("sensitivity requires every distinct planned outer fold")
    requests = []
    for outer, fold in zip(outers, folds):
        params = {k: v for k, v in outer.params.items() if k != "cost_scenario"}
        if not params:
            raise ValueError("sensitivity requires frozen selected params")
        variants = [
            ("cost_2x", params, SCENARIO_DOUBLE, 0),
            ("slippage_stress", params, SCENARIO_SLIPPAGE_STRESS, 0),
            ("delay_1_bar", params, SCENARIO_BASE, 1),
        ]
        for name, choices in sorted(spec.param_grid.items()):
            if params.get(name) not in choices:
                continue
            index = choices.index(params[name])
            for neighbor in (index - 1, index + 1):
                if 0 <= neighbor < len(choices):
                    variants.append(
                        (
                            f"parameter_neighbor__{name}__{neighbor}",
                            params | {name: choices[neighbor]},
                            SCENARIO_BASE,
                            0,
                        )
                    )
        for kind, chosen, cost, delay in variants:
            requests.append(
                {
                    "kind": kind,
                    "fold_id": fold.fold_id,
                    "strategy_id": spec.strategy_id,
                    "symbol": spec.symbol,
                    "timeframe": spec.timeframe,
                    "params": chosen,
                    "cost": cost.to_dict(),
                    "simulation_window": [fold.outer_test_start, fold.outer_test_end],
                    "measurement_window": [fold.outer_test_start, fold.outer_test_end],
                    "signal_delay_bars": delay,
                }
            )
    return requests


def sensitivity_receipt(request, artifact, root):
    from trading_agent.backtest.tournament import (
        CostScenario,
        EvaluationCellSpec,
        _artifact_from_report,
        build_default_registry,
    )

    root = Path(root).resolve()
    receipt = {
        "request": request,
        "status": "INVALID_SOURCE",
        "artifact": artifact.to_dict()
        if artifact and hasattr(artifact, "to_dict")
        else None,
        "report_path": None,
        "report_sha256": None,
    }
    if artifact is None:
        return receipt
    if not hasattr(artifact, "to_dict"):
        return receipt
    if artifact.status != "COMPLETED":
        receipt["status"] = "FAILED"
        return receipt
    try:
        cost = CostScenario(**request["cost"])
        cell = EvaluationCellSpec(
            request["strategy_id"],
            request["symbol"],
            request["timeframe"],
            request["params"],
            cost,
        )
        path = Path(artifact.report_path).resolve()
        if not path.is_relative_to(root) or path.is_symlink():
            return receipt
        if (
            artifact.cell_id != cell.cell_id
            or artifact.params_hash != cell.params_hash
            or artifact.cost_scenario != cost.name
            or artifact.commission != cost.commission
            or artifact.slippage != cost.slippage
            or artifact.signal_delay_bars != request["signal_delay_bars"]
            or list(artifact.simulation_window) != request["simulation_window"]
            or list(artifact.measurement_window) != request["measurement_window"]
            or any(
                getattr(artifact, key) != request[key]
                for key in ("strategy_id", "symbol", "timeframe")
            )
        ):
            return receipt
        raw = path.read_bytes()
        report = json.loads(raw)
        if report["measurement_window"] != request["measurement_window"]:
            return receipt
        rebuilt = _artifact_from_report(
            cell,
            build_default_registry().describe(cell.strategy_id),
            path.parent,
            report,
            artifact.data_manifest_sha,
            measurement_window=artifact.measurement_window,
            simulation_window=artifact.simulation_window,
            signal_delay_bars=artifact.signal_delay_bars,
        )
        if (
            rebuilt.status != "COMPLETED"
            or rebuilt.metrics != artifact.metrics
            or rebuilt.execution_health != artifact.execution_health
        ):
            return receipt
        receipt.update(
            status="COMPLETED",
            report_path=path.relative_to(root).as_posix(),
            report_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return receipt


def compute_sensitivity(spec, outers, folds, out, descriptor):
    from trading_agent.backtest import nested_wfo as wfo
    from trading_agent.backtest.campaign_execution import _durable_json

    requests = sensitivity_requests(spec, outers, folds)
    root = Path(out) / "sensitivity"
    # A schedule cannot be replaced after observing any stress result.
    _durable_json(
        root / "sensitivity_plan.json",
        {"version": 1, "requests": requests, "startup": "fresh_at_outer_test_start"},
    )
    receipts, diagnostics = [], {}
    for request in requests:
        fold = next(f for f in folds if f.fold_id == request["fold_id"])
        from trading_agent.backtest.tournament import (
            SCENARIO_BASE,
            SCENARIO_DOUBLE,
            SCENARIO_SLIPPAGE_STRESS,
        )

        cost = next(
            c
            for c in (SCENARIO_BASE, SCENARIO_DOUBLE, SCENARIO_SLIPPAGE_STRESS)
            if c.to_dict() == request["cost"]
        )
        error = None
        try:
            artifact = wfo._run_outer_eval(
                spec,
                request["params"],
                cost,
                fold,
                root,
                descriptor,
                signal_delay_bars=request["signal_delay_bars"],
            )
        except Exception as exc:
            artifact, error = None, f"{type(exc).__name__}: {exc}"
        diagnostics[(request["kind"], request["fold_id"])] = dict(
            getattr(artifact, "metrics", {}) or {}
        )
        receipt = sensitivity_receipt(request, artifact, root)
        if error is not None:
            receipt.update(status="FAILED", error=error)
        receipts.append(receipt)
    complete = all(r["status"] == "COMPLETED" for r in receipts)
    result = {
        "baseline_cost": {},
        "cost_2x": {"folds": {}},
        "slippage_stress": {"folds": {}},
        "delay_1_bar": {},
        "drop_best_trade": {"folds": {}},
        "parameter_neighbors": [],
        "real_computed": [
            "cost_2x",
            "slippage_stress",
            "drop_best_trade",
            "delay_1_bar",
            "parameter_neighbors",
        ],
        "coverage": {
            "status": "PASS" if complete else "INCOMPLETE",
            "expected": requests,
            "observed": receipts,
            "source_root": "sensitivity",
            "live_authorized": False,
        },
    }
    for kind in ("cost_2x", "slippage_stress", "delay_1_bar"):
        rows = [r for r in receipts if r["request"]["kind"] == kind]
        measures = []
        for row in rows:
            result[kind].setdefault("folds", {})[row["request"]["fold_id"]] = (
                diagnostics[(kind, row["request"]["fold_id"])]
                | {"status": row["status"]}
            )
            if row["status"] == "COMPLETED":
                report = json.loads((root / row["report_path"]).read_bytes())
                metrics = report["metrics"]
                pnls = [
                    t["pnl"]
                    for t in report.get("trades", [])
                    if isinstance(t.get("pnl"), (int, float))
                ]
                measures.append(
                    {
                        "net_pnl": sum(pnls) if pnls else metrics.get("net_pnl"),
                        "profit_factor": metrics.get("profit_factor"),
                        "return_pct": metrics.get("total_return_pct"),
                    }
                )
        aggregate = {}
        for field in ("net_pnl", "profit_factor", "return_pct"):
            values = [m[field] for m in measures]
            usable = (
                complete
                and len(values) == len(folds)
                and all(isinstance(v, (int, float)) for v in values)
            )
            aggregate[f"median_{field}"] = median(values) if usable else None
        result[kind]["aggregate"] = aggregate
    drops = []
    for outer in outers:
        drop = wfo._compute_drop_best_trade(outer.artifact)
        result["drop_best_trade"]["folds"][outer.fold_id] = drop
        if drop is not None:
            drops.append(drop["net_pnl_after_drop"])
    result["drop_best_trade"]["aggregate"] = {
        "n_folds": len(drops),
        "total_net_pnl_after_drop": sum(drops)
        if complete and len(drops) == len(folds)
        else None,
    }
    for row in receipts:
        if row["request"]["kind"].startswith("parameter_neighbor"):
            metrics = row["artifact"]["metrics"] if row["artifact"] else {}
            result["parameter_neighbors"].append(
                {
                    "fold_id": row["request"]["fold_id"],
                    "neighbors": [
                        {
                            "params": row["request"]["params"],
                            "status": row["status"],
                            "net_pnl": metrics.get("net_pnl") if complete else None,
                            "profit_factor": metrics.get("profit_factor")
                            if complete
                            else None,
                        }
                    ],
                }
            )
    if complete:
        result["real_computed"] = [
            "cost_2x",
            "slippage_stress",
            "drop_best_trade",
            "delay_1_bar",
            "parameter_neighbors",
        ]
    return result
