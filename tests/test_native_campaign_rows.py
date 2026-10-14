import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting
from trading_agent.backtest.campaign_writer import collect_wfo_campaign_rows


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "canonical_return",
        "conflicting_return",
        "missing_report",
        "ledger",
        "declaration",
        "open",
        "metric",
        "duplicate",
    ],
)
def test_native_rows_come_from_actual_report_and_wfo(tmp_path, mutation):
    trade = {
        "id": "trade",
        "side": "buy",
        "quantity": 1.0,
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_fee": 0.1,
        "exit_fee": 0.11,
        "pnl": 9.79,
        "metadata": {
            "measured_accounting": {
                "model": "paper_slippage_only_v1",
                "entry_reference_price": 100.0,
                "exit_reference_price": 110.0,
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
    accounting = export_paper_trade_accounting(
        [trade], equity_delta=1009.79 - 1000.0, open_inventory=False
    )
    report = {
        "trades": [trade],
        "measured_accounting": accounting,
        "open_positions": 0,
        "initial_capital": 1000.0,
        "equity_curve": [["end", 1009.79]],
    }
    path = tmp_path / "report.json"
    if mutation == "ledger":
        trade["pnl"] += 1
    elif mutation == "declaration":
        accounting["gross_pnl"] += 1
    elif mutation == "open":
        report["open_positions"] = 1
    path.write_text(json.dumps(report))
    subject = dict(strategy_id="ma", symbol="BTC/USDT", timeframe="1h")
    artifact = NS(**subject, status="COMPLETED", report_path=str(path))
    fold = NS(
        fold_id="f0",
        artifact=artifact,
        test_metrics={
            "total_trades": 1,
            "net_pnl": 9.79,
            "return_pct": 0.979,
            "sharpe": 1.0,
            "max_drawdown_pct": -1.0,
        },
    )
    result = NS(spec=NS(**subject), outer_results=[fold])
    if mutation == "canonical_return":
        fold.test_metrics["total_return_pct"] = fold.test_metrics.pop("return_pct")
    elif mutation == "conflicting_return":
        fold.test_metrics["total_return_pct"] = 99.0
    if mutation == "missing_report":
        artifact.report_path = str(tmp_path / "missing.json")
    elif mutation == "metric":
        fold.test_metrics["net_pnl"] += 1
    elif mutation == "duplicate":
        result.outer_results.append(fold)
    if mutation and mutation != "canonical_return":
        with pytest.raises(ValueError):
            collect_wfo_campaign_rows(result)
    else:
        rows = collect_wfo_campaign_rows(result)
        assert rows[0]["gross_pnl"] == pytest.approx(10.0)
        assert rows[0]["fees"] == pytest.approx(0.21)
        assert rows[0]["report_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
