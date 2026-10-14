"""Publish only WFO-bound evidence; never synthesize missing measurements."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from trading_agent.backtest.campaign_integrity import require_wfo_campaign_evidence


def selected_wfo_subject(plan: dict, result) -> dict:
    """Bind selected parameters to the frozen search space, not its grid hash."""
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.campaign_plan import verify_frozen_plan

    verify_frozen_plan(plan)
    selected = {}
    for fold in result.outer_results:
        if fold.fold_id in selected:
            raise ValueError("duplicate selected fold")
        params = fold.params
        grid = plan["param_grid"]
        if not isinstance(params, dict) or set(params) - set(grid) - {"cost_scenario"}:
            raise ValueError("selected parameters contain unfrozen keys")
        for key, values in grid.items():
            if key not in params or not any(
                content_hash(params[key]) == content_hash(value) for value in values
            ):
                raise ValueError("selected parameters are outside frozen search space")
        selected[fold.fold_id] = params
    if set(selected) != {fold["fold_id"] for fold in plan["folds"]}:
        raise ValueError("selected folds differ from frozen plan")
    return {**plan["subject"], "params_hash": content_hash(selected)}


def collect_wfo_campaign_rows(result) -> list[dict]:
    """Collect measured rows, without inventing fold plans or gate thresholds.

    This is the native accounting producer stage, not a complete schema-v2
    campaign. A pre-run frozen plan is still required for full publication.
    """
    import hashlib
    from trading_agent.backtest.accounting_evidence import export_paper_trade_accounting
    from trading_agent.backtest.campaign_evidence import finite

    rows = []
    seen = set()
    for fold in result.outer_results:
        if fold.fold_id in seen:
            raise ValueError("duplicate measured WFO fold")
        seen.add(fold.fold_id)
        artifact = fold.artifact
        if artifact is None or artifact.status != "COMPLETED":
            raise ValueError("missing completed WFO source artifact")
        if any(
            getattr(artifact, key) != getattr(result.spec, key)
            for key in ("strategy_id", "symbol", "timeframe")
        ):
            raise ValueError("WFO source artifact subject mismatch")
        try:
            raw = Path(artifact.report_path).read_bytes()
            report = json.loads(raw)
            if type(report.get("open_positions")) is not int:
                raise ValueError("campaign report lacks a measured position count")
            initial = report["initial_capital"]
            final = report["equity_curve"][-1][1]
            if not finite(initial) or not finite(final):
                raise ValueError("missing finite measured equity")
            # A carried position is measured and reconciled, not refused. It
            # used to fail this check outright, so a fold that ended holding
            # inventory could never be published at all -- and refusing cost
            # nothing, because it discarded the position instead of pricing
            # it, which is the direction that flatters.
            open_leg = report.get("measured_open_inventory")
            if report["open_positions"] == 0:
                if open_leg is not None:
                    raise ValueError("flat report must not declare open inventory")
                measured = export_paper_trade_accounting(
                    report["trades"],
                    equity_delta=final - initial,
                    open_inventory=False,
                )
            else:
                if not isinstance(open_leg, Mapping):
                    raise ValueError(
                        "open positions require a measured open inventory leg"
                    )
                measured = export_paper_trade_accounting(
                    report["trades"],
                    equity_delta=final - initial,
                    open_inventory=open_leg,
                    equity_basis=report.get("measured_equity_basis", "cash"),
                )
            if measured != report.get("measured_accounting"):
                raise ValueError("source accounting differs from reconstructed ledger")
            if measured["market_impact"] != 0 or measured["price_cap_credit"] != 0:
                raise ValueError(
                    "schema-v2 campaign does not yet represent impact/cap credit"
                )
        except (
            OSError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("missing or invalid measured WFO report") from exc
        row = {
            "fold_id": fold.fold_id,
            "report_sha256": hashlib.sha256(raw).hexdigest(),
            **{
                key: measured[key]
                for key in (
                    "trades",
                    "gross_pnl",
                    "fees",
                    "slippage",
                    "spread_cost",
                    "net_pnl",
                )
            },
            # Clearance follows total PnL once inventory is carried, not the
            # closed ledger. A fold that closed trades well while sitting on a
            # losing open position would otherwise clear on the closed half
            # alone.
            "cost_cleared": (
                measured["total_pnl"] if open_leg is not None else measured["net_pnl"]
            )
            > 0,
        }
        if open_leg is not None:
            row.update(
                {
                    key: measured[key]
                    for key in (
                        "open_quantity",
                        "open_side",
                        "open_entry_price",
                        "open_valuation_price",
                        "open_mark_value",
                        "open_entry_fee",
                        "open_unrealized",
                        "total_pnl",
                    )
                }
            )
        for key, source_key in (
            ("return_pct", "total_return_pct"),
            ("sharpe", "sharpe"),
            ("max_dd_pct", "max_drawdown_pct"),
        ):
            value = fold.test_metrics.get(source_key)
            if source_key == "total_return_pct":
                legacy = fold.test_metrics.get("return_pct")
                if value is None:
                    value = legacy
                elif legacy is not None and legacy != value:
                    raise ValueError("conflicting measured WFO return metrics")
            if not finite(value):
                raise ValueError(f"missing measured WFO metric {source_key}")
            row[key] = value
        if (
            type(fold.test_metrics.get("total_trades")) is not int
            or fold.test_metrics["total_trades"] != row["trades"]
            or not finite(fold.test_metrics.get("net_pnl"))
            or abs(fold.test_metrics["net_pnl"] - row["net_pnl"]) > 1e-8
        ):
            raise ValueError("WFO summary differs from measured ledger")
        rows.append(row)
    if not rows:
        raise ValueError("no measured WFO rows")
    return rows


def publish_campaign_bundle(
    result,
    bundle: dict,
    out_root: Path,
    *,
    data_root: Path,
    source_binding: dict,
    publication_root: Path | None = None,
    selection_evidence_root: Path | None = None,
) -> Path:
    """Validate the exact bytes in staging, then atomically create the artifact.

    Existing different evidence is never replaced. Idempotent re-publication
    still revalidates source reports. This API consumes measured schema-v2
    payloads; constructing native payloads is a separate producer operation.
    """
    encoded = json.dumps(bundle, sort_keys=True, indent=2, allow_nan=False).encode()
    out_root = Path(out_root).resolve()
    allowed_output = Path(publication_root or data_root).resolve()
    if not out_root.is_relative_to(allowed_output):
        raise ValueError("campaign output escapes declared publication root")
    if selection_evidence_root is not None:
        selection_evidence_root = Path(selection_evidence_root).resolve()
        if not selection_evidence_root.is_relative_to(allowed_output):
            raise ValueError("selection evidence escapes declared publication root")
    out_root.mkdir(parents=True, exist_ok=True)
    target = out_root / "campaign_evidence.json"
    with tempfile.TemporaryDirectory(prefix=".campaign-stage-", dir=out_root) as stage:
        staged = Path(stage) / target.name
        with staged.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        require_wfo_campaign_evidence(
            result,
            Path(stage),
            data_root=data_root,
            source_binding=source_binding,
            selection_evidence_root=selection_evidence_root,
        )
        try:
            # Same-filesystem link is atomic and refuses to overwrite, including
            # when concurrent publishers race. os.replace would lose evidence.
            os.link(staged, target)
        except FileExistsError:
            if target.is_symlink():
                raise ValueError(
                    "existing campaign artifact must not be a symlink"
                ) from None
            if target.read_bytes() != encoded:
                raise ValueError(
                    "existing campaign evidence differs; use a new run directory"
                ) from None
        if os.name == "posix":
            directory_fd = os.open(out_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    return target
