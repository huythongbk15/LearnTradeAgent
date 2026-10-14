"""Consumer binding tests, independent of schema validator tests.

The validator is stubbed only to isolate comparison against the actual result.
These fixtures are not backtest evidence or strategy qualification.
"""

from types import SimpleNamespace as NS
import json
import hashlib
from unittest.mock import patch

import pytest

from trading_agent.backtest.campaign_evidence import content_hash
from trading_agent.backtest.campaign_integrity import require_wfo_campaign_evidence


def test_consumer_refuses_removed_selection_contract(tmp_path):
    from trading_agent.backtest.selection_eligibility import freeze_selection_contract

    source, result, bundle = fixture()
    result.aggregate_metrics = {
        "selection_contract": freeze_selection_contract(
            min_validation_trades=3, min_validation_sharpe=0
        )
    }
    validated = NS(admissible=True, validated_bundle=bundle)
    with patch(
        "trading_agent.backtest.campaign_integrity.verify_evidence_bundle",
        return_value=validated,
    ):
        with pytest.raises(ValueError, match="selection contract differs"):
            require_wfo_campaign_evidence(
                result, tmp_path, data_root=tmp_path, source_binding=source
            )


def fixture():
    source = {"code_commit": "a" * 40, "tree_fingerprint": "b" * 64}
    subject = {"strategy_id": "ma", "symbol": "BTC/USDT", "timeframe": "1h"}
    params = {"fast": 10}
    subject["params_hash"] = content_hash({"f0": params})
    metrics = {
        "total_trades": 12,
        "return_pct": 2.0,
        "sharpe": 1.0,
        "max_drawdown_pct": -2.0,
        "net_pnl": 20.0,
    }
    artifact = NS(
        **{k: subject[k] for k in ("strategy_id", "symbol", "timeframe")},
        status="COMPLETED",
        commission=0.001,
        slippage=0.0005,
    )
    fold = NS(fold_id="f0", params=params, test_metrics=metrics, artifact=artifact)
    gate = NS(gate_id="sharpe", threshold=0.8, comparison=">=", observed_value=1.0)
    result = NS(
        spec=NS(**subject),
        passes_hard_gates=True,
        outer_results=[fold],
        gate_results=[gate],
    )
    bundle = {
        **source,
        "verdict": "PASS",
        "subject": subject,
        "results": [
            {
                "fold_id": "f0",
                "trades": 12,
                "return_pct": 2.0,
                "sharpe": 1.0,
                "max_dd_pct": -2.0,
                "net_pnl": 20.0,
            }
        ],
        "cost_schedule": {"commission_bps": 10.0, "slippage_bps": 5.0},
        "aggregate": {"median_sharpe": 1.0},
        "gate_set": [
            {
                "gate_id": "sharpe",
                "threshold": 0.8,
                "comparison": ">=",
                "metric": "aggregate.median_sharpe",
            }
        ],
    }
    return source, result, bundle


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "source",
        "subject",
        "params",
        "metric",
        "cost_nan",
        "cost",
        "artifact",
        "fold_duplicate",
        "gate_duplicate",
        "gate",
        "not_qualified",
        "report_missing",
        "report_tampered",
        "report_rehashed_wrong_ledger",
        "report_accounting_wrong",
        "report_open",
        "report_rowcost",
    ],
)
def test_consumer_binds_validated_payload_to_measured_result(tmp_path, mutation):
    source, result, bundle = fixture()
    fold = result.outer_results[0]
    from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting

    rows = [
        {
            "id": str(index),
            "side": "buy",
            "quantity": 1.0,
            "entry_price": 100.0,
            "exit_price": 102.0,
            "entry_fee": 1 / 6,
            "exit_fee": 1 / 6,
            "pnl": 2 - 1 / 3,
            "metadata": {
                "measured_accounting": {
                    "model": "paper_slippage_only_v1",
                    "entry_reference_price": 100.0,
                    "exit_reference_price": 102.0,
                    "execution_components": {
                        leg: {
                            "slippage": 0.0,
                            "spread": 0.0,
                            "market_impact": 0.0,
                            "price_cap_credit": 0.0,
                        }
                        for leg in ("entry", "exit")
                    },
                }
            },
        }
        for index in range(12)
    ]
    measured = export_paper_trade_accounting(
        rows, equity_delta=20.0, open_inventory=False
    )
    report = {
        "open_positions": 0,
        "initial_capital": 1000.0,
        "equity_curve": [["end", 1020.0]],
        "trades": rows,
        "measured_accounting": measured,
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report))
    fold.artifact.report_path = str(report_path)
    bundle["results"][0].update(
        {key: measured[key] for key in ("gross_pnl", "fees", "slippage", "spread_cost")}
    )
    bundle["results"][0]["report_sha256"] = hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    if mutation == "source":
        bundle["tree_fingerprint"] = "c" * 64
    elif mutation == "subject":
        result.spec.symbol = "ETH/USDT"
    elif mutation == "params":
        fold.params = {"fast": 11}
    elif mutation == "metric":
        fold.test_metrics["net_pnl"] = 21.0
    elif mutation == "cost_nan":
        fold.artifact.commission = float("nan")
    elif mutation == "cost":
        fold.artifact.slippage = 0.002
    elif mutation == "artifact":
        fold.artifact.symbol = "ETH/USDT"
    elif mutation == "fold_duplicate":
        result.outer_results.append(fold)
    elif mutation == "gate_duplicate":
        result.gate_results.append(result.gate_results[0])
    elif mutation == "gate":
        result.gate_results[0].threshold = 0.9
    elif mutation == "not_qualified":
        result.passes_hard_gates = False
    elif mutation == "report_missing":
        fold.artifact.report_path = str(tmp_path / "absent.json")
    elif mutation == "report_tampered":
        report["initial_capital"] += 1
        report_path.write_text(json.dumps(report))
    elif mutation in (
        "report_rehashed_wrong_ledger",
        "report_accounting_wrong",
        "report_open",
    ):
        if mutation == "report_rehashed_wrong_ledger":
            report["trades"][0]["pnl"] += 7
        elif mutation == "report_accounting_wrong":
            report["measured_accounting"]["fees"] += 7
        else:
            report["open_positions"] = 1
        report_path.write_text(json.dumps(report))
        bundle["results"][0]["report_sha256"] = hashlib.sha256(
            report_path.read_bytes()
        ).hexdigest()
    elif mutation == "report_rowcost":
        bundle["results"][0]["gross_pnl"] += 7
    report = NS(admissible=True, validated_bundle=bundle)
    with patch(
        "trading_agent.backtest.campaign_integrity.verify_evidence_bundle",
        return_value=report,
    ):
        if mutation is None:
            assert (
                require_wfo_campaign_evidence(
                    result, tmp_path, data_root=tmp_path, source_binding=source
                )
                is bundle
            )
        else:
            with pytest.raises(ValueError):
                require_wfo_campaign_evidence(
                    result, tmp_path, data_root=tmp_path, source_binding=source
                )
