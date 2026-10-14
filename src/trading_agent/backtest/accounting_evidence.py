"""Measured closed-trade accounting export, not a promotion certificate.

No component is inferred or defaulted to zero. Producers must record actual
per-leg decomposition. Open/carry inventory requires a different contract.
"""

from __future__ import annotations

import math
from copy import deepcopy
from collections.abc import Mapping, Sequence


def _number(value, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite measured number")
    return float(value)


def export_paper_trade_accounting(
    trades: Sequence[Mapping],
    *,
    equity_delta: float,
    open_inventory: bool | Mapping,
    equity_basis: str = "cash",
) -> dict:
    """Use fill-time paper accounting, not later candle proxy references.

    ``open_inventory`` is either a flag confirming the run ended flat, or the
    measured open leg for a position that survived the window. A flag cannot
    certify what the flag says is true, so the legacy boolean form is refused
    for a carried position rather than trusted.
    """
    if equity_basis not in {"cash", "marked_equity"}:
        raise ValueError("unknown measured equity basis")
    normalized = []
    for trade in trades:
        if trade.get("measurement_attribution") is not None:
            proof = trade.get("measurement_boundary")
            if not isinstance(proof, Mapping):
                raise ValueError(
                    "boundary-carried trades require explicit boundary accounting"
                )
            expected = mark_paper_trade_at_boundary(
                proof.get("original_trade"),
                boundary_price=proof.get("boundary_price"),
                measurement_start=proof.get("measurement_start"),
                measurement_end=proof.get("measurement_end"),
            )
            if trade != expected:
                raise ValueError(
                    "boundary attribution differs from original measured fill"
                )
        metadata = trade.get("metadata", {})
        measured = (
            metadata.get("measured_accounting")
            if isinstance(metadata, Mapping)
            else None
        )
        if (
            not isinstance(measured, Mapping)
            or measured.get("model") != "paper_slippage_only_v1"
        ):
            raise ValueError(
                "missing measured paper cost basis; legacy/venue/carry is not certified"
            )
        record = {
            **trade,
            "trade_id": trade.get("id"),
            "metadata": {"simulation": measured},
        }
        record.pop(
            "measurement_attribution", None
        )  # verified above, not silently discarded
        normalized.append(record)
    carried = None
    if isinstance(open_inventory, Mapping):
        carried = open_inventory
    elif open_inventory is not False:
        raise ValueError("open inventory requires the measured open leg, not a flag")
    exporter = (
        export_open_inventory_accounting
        if carried is not None
        else export_closed_trade_accounting
    )
    kwargs = (
        {"open_inventory": carried, "equity_basis": equity_basis}
        if carried is not None
        else {"open_inventory": False}
    )
    return {
        **exporter(normalized, equity_delta=equity_delta, **kwargs),
        "execution_model": "paper_slippage_only_v1",
    }


def mark_paper_trade_at_boundary(
    original_trade, *, boundary_price, measurement_start, measurement_end
):
    """Attribute only post-boundary PnL, retaining validated original fill proof."""
    if (
        not isinstance(original_trade, Mapping)
        or original_trade.get("measurement_attribution") is not None
    ):
        raise ValueError("boundary requires an original measured trade")
    if any(type(v) is not int for v in (measurement_start, measurement_end)):
        raise ValueError("boundary requires integer measurement indices")
    original = deepcopy(dict(original_trade))
    timeline = original.get("metadata", {}).get("simulation", {})
    entry = original.get("entry_bar_index", timeline.get("entry_bar_index"))
    exit_bar = original.get("exit_bar_index", timeline.get("exit_bar_index"))
    if (
        type(entry) is not int
        or type(exit_bar) is not int
        or not 0 <= entry < measurement_start <= exit_bar < measurement_end
    ):
        raise ValueError("original trade does not straddle measured boundary")
    boundary_price = _number(boundary_price, "boundary_price")
    if boundary_price <= 0:
        raise ValueError("boundary price must be positive")
    # Prove full-trade fees/components first, before removing pre-OOS costs.
    export_paper_trade_accounting(
        [original], equity_delta=original.get("pnl"), open_inventory=False
    )
    trade = deepcopy(original)
    measured = trade["metadata"]["measured_accounting"]
    measured["entry_reference_price"] = boundary_price
    measured["execution_components"]["entry"] = dict(
        slippage=0.0, spread=0.0, market_impact=0.0, price_cap_credit=0.0
    )
    direction = 1 if trade["side"] == "buy" else -1
    trade.update(
        entry_price=boundary_price,
        entry_fee=0.0,
        pnl=direction * trade["quantity"] * (trade["exit_price"] - boundary_price)
        - trade["exit_fee"],
        measurement_attribution="marked_at_window_open",
        measurement_boundary=dict(
            original_trade=original,
            boundary_price=boundary_price,
            measurement_start=measurement_start,
            measurement_end=measurement_end,
        ),
    )
    return trade


def export_closed_trade_accounting(
    trades: Sequence[Mapping], *, equity_delta: float, open_inventory: bool
) -> dict:
    """Return accounting only when fills, net ledger and equity agree.

    ``execution_components`` contains entry/exit mappings with slippage,
    spread and market_impact in quote currency (not rates). Explicit zero is
    allowed, missing values are not. This does not attest their provenance.
    """
    if type(open_inventory) is not bool or open_inventory:
        raise ValueError("closed-trade export requires confirmed zero open inventory")
    equity_delta = _number(equity_delta, "equity_delta")
    totals = _closed_ledger(trades)
    if not math.isclose(totals["net_pnl"], equity_delta, abs_tol=1e-8, rel_tol=1e-9):
        raise ValueError(
            "closed trade ledger does not reconcile to measured equity delta"
        )
    return {
        "schema_version": 1,
        "accounting_basis": "closed_trades_only",
        "trades": len(trades),
        "equity_delta": equity_delta,
        **totals,
        "strategy_qualified": False,
    }


def _closed_ledger(trades: Sequence[Mapping]) -> dict:
    """Validate and total closed trades without reconciling to equity.

    Reconciliation depends on what happened to inventory that was still open
    at the window boundary, so it cannot live here.
    """
    # Empty is a measured zero, not missing evidence. Equity reconciliation
    # below still rejects unexplained movement; minimum-trade gates are separate.
    totals = dict(
        gross_pnl=0.0,
        fees=0.0,
        slippage=0.0,
        spread_cost=0.0,
        market_impact=0.0,
        price_cap_credit=0.0,
        net_pnl=0.0,
    )
    ids = set()
    for trade in trades:
        if not isinstance(trade, Mapping):
            raise ValueError("trade must be a measured mapping")
        identity = trade.get("trade_id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("closed trades require unique nonempty trade_id")
        ids.add(identity)
        if trade.get("measurement_attribution") is not None:
            raise ValueError(
                "boundary-carried trades require explicit boundary accounting"
            )
        side = trade.get("side")
        if side not in ("buy", "sell"):
            raise ValueError("missing measured trade side")
        direction = 1 if side == "buy" else -1
        quantity = _number(trade.get("quantity"), "quantity")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        metadata = trade.get("metadata")
        if not isinstance(metadata, Mapping) or not isinstance(
            metadata.get("simulation"), Mapping
        ):
            raise ValueError("missing simulation accounting metadata")
        simulation = metadata["simulation"]
        components = simulation.get("execution_components", {})
        if not isinstance(components, Mapping):
            raise ValueError("missing execution component mapping")
        values = {}
        for leg in ("entry", "exit"):
            price = _number(trade.get(f"{leg}_price"), f"{leg}_price")
            reference = _number(
                simulation.get(f"{leg}_reference_price"), f"{leg}_reference_price"
            )
            fee = _number(trade.get(f"{leg}_fee"), f"{leg}_fee")
            if price <= 0 or reference <= 0 or fee < 0:
                raise ValueError("invalid measured price/reference/fee")
            leg_components = components.get(leg, {})
            if not isinstance(leg_components, Mapping):
                raise ValueError("missing per-leg component mapping")
            measured = {
                key: _number(leg_components.get(key), f"{leg}.{key}")
                for key in ("slippage", "spread", "market_impact", "price_cap_credit")
            }
            # Maker spread and impact can be credits. Preserve signed measured
            # components, and prove them against the actual per-leg fill drag.
            drag = (
                direction
                * quantity
                * (price - reference)
                * (1 if leg == "entry" else -1)
            )
            _number(drag, "measured fill drag")
            if not math.isclose(
                sum(measured.values()), drag, abs_tol=1e-8, rel_tol=1e-9
            ):
                raise ValueError(
                    "per-leg components do not reconcile to actual fill drag"
                )
            totals["fees"] += fee
            totals["slippage"] += measured["slippage"]
            totals["spread_cost"] += measured["spread"]
            totals["market_impact"] += measured["market_impact"]
            totals["price_cap_credit"] += measured["price_cap_credit"]
            values[leg] = reference
        gross = direction * quantity * (values["exit"] - values["entry"])
        totals["gross_pnl"] += gross
        net = _number(trade.get("pnl"), "pnl")
        fill_net = (
            direction * quantity * (trade["exit_price"] - trade["entry_price"])
            - trade["entry_fee"]
            - trade["exit_fee"]
        )
        if not math.isclose(net, fill_net, abs_tol=1e-8, rel_tol=1e-9):
            raise ValueError("trade net does not reconcile to fills and fees")
        totals["net_pnl"] += net
    for key, value in totals.items():
        _number(value, key)
    return totals


def _open_leg(open_inventory: Mapping) -> dict:
    if open_inventory.get("measurement_attribution") is not None:
        proof = open_inventory.get("measurement_boundary")
        if not isinstance(proof, Mapping):
            raise ValueError("open boundary requires original inventory proof")
        expected = mark_paper_open_at_boundary(
            proof.get("original_open_inventory"),
            boundary_price=proof.get("boundary_price"),
            entry_bar_index=proof.get("entry_bar_index"),
            measurement_start=proof.get("measurement_start"),
            measurement_end=proof.get("measurement_end"),
        )
        if open_inventory != expected:
            raise ValueError(
                "open boundary attribution differs from original inventory"
            )
    """Validate the still-open position and return its measured amounts.

    The open leg is an entry that was never traded back, so it carries the
    same component contract as a closed trade's entry, marked at the final
    price rather than at an exit.
    """
    side = open_inventory.get("side")
    if side not in ("buy", "sell"):
        raise ValueError("open inventory requires a measured side")
    direction = 1 if side == "buy" else -1
    quantity = _number(open_inventory.get("quantity"), "open quantity")
    entry_price = _number(open_inventory.get("entry_price"), "open entry_price")
    reference = _number(
        open_inventory.get("entry_reference_price"), "open entry_reference_price"
    )
    valuation = _number(open_inventory.get("valuation_price"), "open valuation_price")
    entry_fee = _number(open_inventory.get("entry_fee"), "open entry_fee")
    if quantity <= 0:
        raise ValueError("open quantity must be positive")
    if entry_price <= 0 or reference <= 0 or valuation <= 0 or entry_fee < 0:
        raise ValueError("invalid measured open price/reference/fee")
    components = open_inventory.get("execution_components")
    if not isinstance(components, Mapping):
        raise ValueError("missing open execution component mapping")
    measured = {
        key: _number(components.get(key), f"open.{key}")
        for key in ("slippage", "spread", "market_impact", "price_cap_credit")
    }
    drag = direction * quantity * (entry_price - reference)
    _number(drag, "measured open fill drag")
    if not math.isclose(sum(measured.values()), drag, abs_tol=1e-8, rel_tol=1e-9):
        raise ValueError("open components do not reconcile to actual fill drag")
    return {
        "direction": direction,
        "quantity": quantity,
        "valuation_price": valuation,
        "mark_value": direction * quantity * valuation,
        "unrealized": direction * quantity * (valuation - entry_price) - entry_fee,
        "entry_fee": entry_fee,
        "components": measured,
    }


def export_open_inventory_accounting(
    trades: Sequence[Mapping],
    *,
    open_inventory: Mapping,
    equity_delta: float,
    equity_basis: str = "cash",
) -> dict:
    """Accounting when a position survives the window boundary (carry).

    The window ended holding inventory, so cash alone is not the result:
    it omits a position the run still owns, and reporting it as the outcome
    would show a loss for a position that was never closed out. Cash alone
    also overstates cost, because the entry fee for the open quantity has
    already been paid and would be counted twice if the closed ledger were
    the whole story.

    The reconciliation this enforces:

        closed net + open unrealized == equity_delta + open mark value

    ``equity_delta`` is measured cash movement. The open position's quantity
    leg was never traded back, so its value does not appear in cash at all
    and is added as the mark term. The unrealized term subtracts the open
    quantity's entry fee, which cash already paid.
    """
    equity_delta = _number(equity_delta, "equity_delta")
    if equity_basis not in {"cash", "marked_equity"}:
        raise ValueError("unknown measured equity basis")
    if (
        open_inventory.get("measurement_attribution") is not None
        and equity_basis != "marked_equity"
    ):
        raise ValueError("boundary inventory requires marked-equity measurement")
    totals = _closed_ledger(trades)
    leg = _open_leg(open_inventory)
    total_pnl = totals["net_pnl"] + leg["unrealized"]
    if not math.isclose(
        total_pnl,
        equity_delta + (leg["mark_value"] if equity_basis == "cash" else 0.0),
        abs_tol=1e-8,
        rel_tol=1e-9,
    ):
        raise ValueError(
            "open inventory ledger does not reconcile to cash plus marked position"
        )
    return {
        "schema_version": 1,
        "accounting_basis": "closed_trades_plus_open_inventory",
        "trades": len(trades),
        "equity_delta": equity_delta,
        "equity_basis": equity_basis,
        **totals,
        "open_quantity": leg["quantity"],
        "open_side": "buy" if leg["direction"] > 0 else "sell",
        "open_entry_price": _number(open_inventory.get("entry_price"), "entry_price"),
        "open_valuation_price": leg["valuation_price"],
        "open_mark_value": leg["mark_value"],
        "open_entry_fee": leg["entry_fee"],
        "open_unrealized": leg["unrealized"],
        "open_slippage": leg["components"]["slippage"],
        "open_spread_cost": leg["components"]["spread"],
        "open_market_impact": leg["components"]["market_impact"],
        "open_price_cap_credit": leg["components"]["price_cap_credit"],
        "total_pnl": total_pnl,
        "strategy_qualified": False,
    }


def mark_paper_open_at_boundary(
    original_open_inventory,
    *,
    boundary_price,
    entry_bar_index,
    measurement_start,
    measurement_end,
):
    """Price only the OOS movement of remaining inventory born before OOS."""
    if (
        not isinstance(original_open_inventory, Mapping)
        or original_open_inventory.get("measurement_attribution") is not None
    ):
        raise ValueError("open boundary requires original measured inventory")
    if (
        any(
            type(v) is not int
            for v in (entry_bar_index, measurement_start, measurement_end)
        )
        or not 0 <= entry_bar_index < measurement_start < measurement_end
    ):
        raise ValueError("open inventory does not straddle measurement boundary")
    _open_leg(original_open_inventory)  # Original fill drag and fee must be complete.
    boundary_price = _number(boundary_price, "boundary_price")
    if boundary_price <= 0:
        raise ValueError("boundary price must be positive")
    original = deepcopy(dict(original_open_inventory))
    leg = deepcopy(original)
    leg.update(
        entry_price=boundary_price,
        entry_reference_price=boundary_price,
        entry_fee=0.0,
        execution_components=dict(
            slippage=0.0, spread=0.0, market_impact=0.0, price_cap_credit=0.0
        ),
        measurement_attribution="marked_at_window_open",
        measurement_boundary=dict(
            original_open_inventory=original,
            boundary_price=boundary_price,
            entry_bar_index=entry_bar_index,
            measurement_start=measurement_start,
            measurement_end=measurement_end,
        ),
    )
    return leg
