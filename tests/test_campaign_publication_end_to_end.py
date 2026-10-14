"""Synthetic contract fixtures; real validators/ledger/writer, no gate stubs.

These are not measured strategy qualification or release evidence.
"""

import json
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace as NS

import polars as pl
import pytest

from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting
from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_plan import (
    build_data_manifest,
    freeze_campaign_plan,
    build_campaign_bundle,
    semantic_fingerprint,
)
from trading_agent.backtest.campaign_writer import (
    collect_wfo_campaign_rows,
    publish_campaign_bundle,
    selected_wfo_subject,
)


@pytest.mark.parametrize(
    "mutation", [None, "report", "decision", "coverage", "source_root", "manifest", "decision_link", "plan_link", "strategy_path", "fold_path", "oos_validation", "cost_rate", "cost_added", "cost_removed", "simulation_start"]
)
def test_eligibility_publication_uses_declared_source_packet(tmp_path, mutation):
    """Synthetic qualification fixture, real rebuilder/consumer/atomic writer."""
    import hashlib
    from trading_agent.backtest.selection_eligibility import (
        freeze_selection_contract,
        assess_validation_candidates,
    )
    from trading_agent.backtest.tournament import (
        EvaluationCellSpec,
        SCENARIO_BASE,
        _artifact_from_report,
    )
    from trading_agent.strategies.canonical.candidates import build_default_registry

    result, _, data_root, source = fixture(tmp_path)
    contract = freeze_selection_contract(
        min_validation_trades=1, min_validation_sharpe=0
    )
    plan = result.frozen_plan
    plan["selection_contract"] = contract
    from trading_agent.strategies.canonical.candidates import campaign_history_contract
    plan["history_contract"] = campaign_history_contract(result.spec.strategy_id, plan["param_grid"])
    plan["validation_cost_scenarios"] = [
        {**SCENARIO_BASE.to_dict(), "commission_bps": 10.0, "slippage_bps": 5.0}
    ]
    plan["semantic_fingerprint"] = semantic_fingerprint(plan)
    plan["plan_fingerprint"] = content_hash(
        {key: value for key, value in plan.items() if key != "plan_fingerprint"}
    )
    result.spec.param_grid = plan["param_grid"]
    result.spec.cost_scenarios = (SCENARIO_BASE,)
    if mutation == "cost_rate":
        from trading_agent.backtest.tournament import CostScenario
        result.spec.cost_scenarios = (CostScenario("1x", fee_multiplier=2),)
    result.aggregate_metrics = {"selection_contract": contract}
    result.inner_results = []
    paths = []
    for index, outer in enumerate(result.outer_results):
        outer.test_start, outer.test_end = 20 * index + 12, 20 * index + 20
        params = dict(outer.params)
        outer.params["cost_scenario"] = "1x"
        cell = EvaluationCellSpec(
            strategy_id=result.spec.strategy_id,
            symbol=result.spec.symbol,
            timeframe="1h",
            params=params,
            cost_scenario=result.spec.cost_scenarios[0],
        )
        directory = tmp_path / f"validation-{index}"
        directory.mkdir()
        path = directory / "report.json"
        window = (20 * index + 6, 20 * index + 12)
        if mutation == "oos_validation":
            window = (outer.test_start, outer.test_end)
        report = {
            "measurement_window": list(window),
            "metrics": {**outer.test_metrics, "win_rate_pct": 50, "profit_factor": 1.2},
            "execution_health": {
                "unknown_orders": 0,
                "manual_interventions": 0,
                "unprotected_positions": [],
            },
        }
        path.write_text(json.dumps(report))
        artifact = _artifact_from_report(
            cell,
            build_default_registry().describe(cell.strategy_id),
            directory,
            report,
            plan["data_manifest"]["file_sha256"],
            measurement_window=window,
            simulation_window=(window[0] if mutation == "simulation_start" else 0, window[1]),
        )
        assert artifact.status == "COMPLETED"
        from dataclasses import replace

        artifact = replace(artifact, commit_sha=source["code_commit"])
        outer.artifact.data_manifest_sha = artifact.data_manifest_sha
        candidate = {
            "params": params,
            "cost_scenario": "1x",
            "val_metrics": dict(artifact.metrics),
            "validation_source": {
                "report_path": str(path.relative_to(tmp_path)),
                "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "artifact": artifact.to_dict(),
            },
        }
        decision = assess_validation_candidates(contract, [candidate])
        decision["fold_id"] = outer.fold_id
        decision_path = tmp_path / f"selection_decision_{outer.fold_id}.json"
        decision_path.write_text(json.dumps(decision))
        paths.append((path, decision_path))
        result.inner_results.append(
            NS(
                fold_id=outer.fold_id,
                train_start=20 * index,
                train_end=20 * index + 6,
                val_start=window[0],
                val_end=window[1],
                candidate_metrics=[candidate],
                best_params=dict(outer.params),
            )
        )
    plan_path = tmp_path / result.spec.strategy_id / "campaign_plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps(plan))
    bundle = build_campaign_bundle(
        plan=plan,
        rows=collect_wfo_campaign_rows(result),
        source=source,
        data_root=data_root,
        subject=selected_wfo_subject(plan, result),
    )
    if mutation == "report":
        paths[0][0].write_text("{}")
    elif mutation == "decision":
        paths[0][1].write_text("{}")
    elif mutation == "coverage":
        result.inner_results[0].candidate_metrics = []
    elif mutation == "manifest":
        result.outer_results[0].artifact.data_manifest_sha = None
    elif mutation in {"decision_link", "plan_link"}:
        target = paths[0][1] if mutation == "decision_link" else plan_path
        outside = tmp_path.parent / f"{tmp_path.name}_{mutation}.json"
        target.rename(outside)
        target.symlink_to(outside)
    elif mutation == "strategy_path":
        result.spec.strategy_id = "../enhanced_ma"
    elif mutation == "fold_path":
        result.outer_results[0].fold_id = "../f0"
        result.inner_results[0].fold_id = "../f0"
    elif mutation == "cost_added":
        from trading_agent.backtest.tournament import SCENARIO_DOUBLE
        result.spec.cost_scenarios += (SCENARIO_DOUBLE,)
    elif mutation == "cost_removed":
        result.spec.cost_scenarios = ()
    out = tmp_path / "published"

    def publish():
        return publish_campaign_bundle(
            result,
            bundle,
            out,
            data_root=data_root,
            source_binding=source,
            publication_root=tmp_path,
            selection_evidence_root=tmp_path.parent
            if mutation == "source_root"
            else tmp_path,
        )

    if mutation:
        with pytest.raises(ValueError):
            publish()
        assert not (out / "campaign_evidence.json").exists()
    else:
        target = publish()
        assert json.loads(target.read_text()) == bundle
        assert publish() == target


def fixture(tmp_path):
    data_root = tmp_path / "inputs"
    data_root.mkdir()
    start = datetime(2024, 1, 1, tzinfo=UTC)
    stamps = [start + timedelta(hours=i) for i in range(60)]
    path = data_root / "bars.parquet"
    pl.DataFrame(
        {
            "timestamp": stamps,
            "open": [100.0] * 60,
            "high": [111.0] * 60,
            "low": [90.0] * 60,
            "close": [100.0] * 60,
            "volume": [10.0] * 60,
        }
    ).write_parquet(path)
    subject = dict(
        strategy_id="enhanced_ma",
        symbol="BTC/USDT",
        timeframe="1h",
        market_type="spot",
        params_hash=content_hash({"fast_period": [10], "slow_period": [60]}),
    )
    manifest = build_data_manifest(
        path,
        data_root=data_root,
        subject=subject,
        window=dict(
            start=stamps[0].isoformat(),
            end=stamps[-1].isoformat(),
            timeframe="1h",
            market="spot",
        ),
    )
    folds = [
        dict(
            fold_id=f"f{i}",
            train_span=[stamps[20 * i].isoformat(), stamps[20 * i + 5].isoformat()],
            val_span=[stamps[20 * i + 6].isoformat(), stamps[20 * i + 11].isoformat()],
            test_span=[
                stamps[20 * i + 12].isoformat(),
                stamps[20 * i + 19].isoformat(),
            ],
            purge_bars=0,
            embargo_bars=0,
        )
        for i in range(3)
    ]
    plan = freeze_campaign_plan(
        subject=subject,
        folds=folds,
        data_manifest=manifest,
        cost_schedule=dict(
            commission_bps=10.0, slippage_bps=5.0, spread_bps=0.0, round_trip_bps=30.0
        ),
        gate_set=[
            dict(
                gate_id="sharpe",
                metric="aggregate.median_sharpe",
                threshold=0.5,
                comparison=">",
                unit="ratio",
            )
        ],
        param_grid={"fast_period": [10], "slow_period": [60]},
        frozen_by="synthetic-contract-test",
    )
    outer = []
    for i in range(3):
        trade = dict(
            id=f"trade{i}",
            side="buy",
            quantity=1.0,
            entry_price=100.0,
            exit_price=110.0,
            entry_fee=0.1,
            exit_fee=0.11,
            pnl=9.79,
            metadata={
                "measured_accounting": dict(
                    model="paper_slippage_only_v1",
                    entry_reference_price=100.0,
                    exit_reference_price=110.0,
                    execution_components={
                        leg: dict(
                            slippage=0.0,
                            spread=0.0,
                            market_impact=0.0,
                            price_cap_credit=0.0,
                        )
                        for leg in ("entry", "exit")
                    },
                )
            },
        )
        report = dict(
            trades=[trade],
            initial_capital=1000.0,
            equity_curve=[["end", 1009.79]],
            open_positions=0,
            measured_accounting=export_paper_trade_accounting(
                [trade], equity_delta=1009.79 - 1000.0, open_inventory=False
            ),
        )
        report_path = tmp_path / f"report{i}.json"
        report_path.write_text(json.dumps(report))
        artifact = NS(
            strategy_id=subject["strategy_id"],
            symbol=subject["symbol"],
            timeframe="1h",
            status="COMPLETED",
            report_path=str(report_path),
            commission=0.001,
            slippage=0.0005,
        )
        outer.append(
            NS(
                fold_id=f"f{i}",
                params=dict(fast_period=10, slow_period=60),
                artifact=artifact,
                test_metrics=dict(
                    total_return_pct=0.979,
                    sharpe=1.0,
                    max_drawdown_pct=0.0,
                    total_trades=1,
                    net_pnl=9.79,
                ),
            )
        )
    result = NS(spec=NS(**subject), outer_results=outer, passes_hard_gates=True)
    source = dict(
        code_commit="a" * 40,
        tree_fingerprint="b" * 64,
        worktree_dirty=False,
        dirty_diff_sha256=None,
    )
    selected = selected_wfo_subject(plan, result)
    bundle = build_campaign_bundle(
        plan=plan,
        rows=collect_wfo_campaign_rows(result),
        source=source,
        data_root=data_root,
        subject=selected,
    )
    result.gate_results = [
        NS(
            gate_id=gate["gate_id"],
            threshold=gate["threshold"],
            comparison=gate["comparison"],
            observed_value=bundle["aggregate"][
                gate["metric"].removeprefix("aggregate.")
            ],
        )
        for gate in plan["gate_set"]
    ]
    result.frozen_plan = plan
    return result, bundle, data_root, source


@pytest.mark.parametrize("mutation", ["outside_grid", "extra_key", "missing_fold"])
def test_selected_subject_cannot_escape_frozen_search(tmp_path, mutation):
    result, _, _, _ = fixture(tmp_path)
    if mutation == "outside_grid":
        result.outer_results[0].params["fast_period"] = 99
    elif mutation == "extra_key":
        result.outer_results[0].params["unfrozen"] = 1
    else:
        result.outer_results.pop()
    with pytest.raises(ValueError):
        selected_wfo_subject(result.frozen_plan, result)


@pytest.mark.parametrize(
    "mutation", [None, "params", "gates", "report", "cost", "source", "output"]
)
def test_publication_with_real_schema_and_ledger(tmp_path, mutation):
    result, bundle, data_root, source = fixture(tmp_path)
    allowed = tmp_path / "publication"
    out = allowed / "run"
    if mutation == "params":
        result.outer_results[0].params["fast_period"] = 99
    elif mutation == "gates":
        result.gate_results.pop()
    elif mutation == "report":
        result.outer_results[0].artifact.report_path = str(tmp_path / "missing")
    elif mutation == "cost":
        result.outer_results[0].artifact.commission = 0.002
    elif mutation == "source":
        source = {**source, "tree_fingerprint": "c" * 64}
    elif mutation == "output":
        out = tmp_path / "outside"

    def publish():
        return publish_campaign_bundle(
            result,
            bundle,
            out,
            data_root=data_root,
            source_binding=source,
            publication_root=allowed,
        )

    if mutation:
        with pytest.raises(ValueError):
            publish()
        assert not (out / "campaign_evidence.json").exists()
    else:
        target = publish()
        assert json.loads(target.read_text()) == bundle
        assert publish() == target


@pytest.mark.parametrize("mutation", [None, "omit", "observation"])
def test_native_publication_layer(tmp_path, mutation):
    from trading_agent.backtest.native_gate_contract import (
        freeze_native_gate_contract,
        threshold_for,
    )

    result, _, data_root, source = fixture(tmp_path)
    result.spec.effective_min_oos_trades = 1
    contract = freeze_native_gate_contract(result.spec)
    plan = result.frozen_plan
    plan["native_gate_contract"] = contract
    plan["semantic_fingerprint"] = semantic_fingerprint(plan)
    plan.pop("plan_fingerprint")
    plan["plan_fingerprint"] = content_hash(plan)
    evidence = []
    for gate in contract["gates"]:
        threshold = threshold_for(gate, [1, 1, 1])
        observed = threshold + 1 if gate["comparison"] in {">", ">="} else threshold / 2
        evidence.append(
            dict(
                gate_id=gate["gate_id"],
                threshold=threshold,
                observed=observed,
                comparison=gate["comparison"],
                policy_version="v1",
                verdict="PASS",
            )
        )
    result.gate_results = [
        NS(**{**g, "observed_value": g["observed"]}) for g in evidence
    ]
    result.aggregate_metrics = {"native_gate_contract": contract}
    bundle = build_campaign_bundle(
        plan=plan,
        rows=collect_wfo_campaign_rows(result),
        source=source,
        data_root=data_root,
        subject=selected_wfo_subject(plan, result),
        native_evidence=evidence,
    )
    if mutation == "omit":
        result.gate_results.pop()
    elif mutation == "observation":
        result.gate_results[0].observed_value = 99

    def publish():
        return publish_campaign_bundle(
            result,
            bundle,
            tmp_path / "publication" / "run",
            data_root=data_root,
            source_binding=source,
            publication_root=tmp_path / "publication",
        )

    if mutation:
        with pytest.raises(ValueError):
            publish()
    else:
        assert publish().is_file()


def test_carried_report_survives_full_publication_chain(tmp_path):
    result, _, data_root, source = fixture(tmp_path)
    leg = dict(
        side="buy",
        quantity=1.0,
        entry_price=100.1,
        entry_reference_price=100.0,
        valuation_price=100.0,
        entry_fee=0.1001,
        execution_components=dict(
            slippage=0.1, spread=0.0, market_impact=0.0, price_cap_credit=0.0
        ),
    )
    for fold in result.outer_results:
        path = __import__("pathlib").Path(fold.artifact.report_path)
        report = json.loads(path.read_text())
        delta = fold.test_metrics["net_pnl"] + (100.0 - 100.1) - 0.1001
        report.update(
            open_positions=1,
            measured_open_inventory=leg,
            measured_equity_basis="marked_equity",
        )
        report["equity_curve"][-1][1] = report["initial_capital"] + delta
        delta = report["equity_curve"][-1][1] - report["initial_capital"]
        report["measured_accounting"] = export_paper_trade_accounting(
            report["trades"],
            equity_delta=delta,
            open_inventory=leg,
            equity_basis="marked_equity",
        )
        fold.test_metrics["total_return_pct"] = (
            100.0 * delta / report["initial_capital"]
        )
        path.write_text(json.dumps(report))
    plan = result.frozen_plan
    bundle = build_campaign_bundle(
        plan=plan,
        rows=collect_wfo_campaign_rows(result),
        source=source,
        data_root=data_root,
        subject=selected_wfo_subject(plan, result),
    )
    for gate in result.gate_results:
        definition = next(g for g in plan["gate_set"] if g["gate_id"] == gate.gate_id)
        gate.observed_value = bundle["aggregate"][
            definition["metric"].removeprefix("aggregate.")
        ]
    target = publish_campaign_bundle(
        result,
        bundle,
        tmp_path / "publication" / "carry",
        data_root=data_root,
        source_binding=source,
        publication_root=tmp_path / "publication",
    )
    assert all(
        row["open_quantity"] == 1.0 for row in json.loads(target.read_text())["results"]
    )
