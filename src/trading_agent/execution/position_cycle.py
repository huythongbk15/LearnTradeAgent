"""Immutable fill-backed position view for opt-in runtime strategies."""
from dataclasses import dataclass
from datetime import datetime
import math


@dataclass(frozen=True)
class PositionCycleView:
    symbol: str
    quantity: float
    decision_at: datetime
    inventory_known: bool
    first_fill_at: datetime | None = None
    entry_order_id: str | None = None

    def __post_init__(self):
        if (not isinstance(self.symbol, str) or not self.symbol
                or isinstance(self.quantity, bool)
                or not math.isfinite(self.quantity) or self.quantity < 0):
            raise ValueError("position symbol/quantity invalid")
        if type(self.inventory_known) is not bool:
            raise ValueError("inventory_known must be explicit boolean")
        if (not isinstance(self.decision_at, datetime) or self.decision_at.tzinfo is None
                or self.decision_at.utcoffset() is None):
            raise ValueError("decision time must be timezone-aware")
        if self.inventory_known and self.quantity > 0:
            if (not isinstance(self.entry_order_id, str) or not self.entry_order_id
                    or not isinstance(self.first_fill_at, datetime)):
                raise ValueError("an open cycle requires first-fill time and order identity")
            if self.first_fill_at.tzinfo is None or self.first_fill_at.utcoffset() is None:
                raise ValueError("fill time must be timezone-aware")
            if self.first_fill_at > self.decision_at:
                raise ValueError("fill cannot come from after the decision cutoff")
        elif self.inventory_known and (self.first_fill_at is not None or self.entry_order_id):
            raise ValueError("a flat position has no active fill cycle")


@dataclass(frozen=True)
class PositionCycleRecord:
    """Cycle facts copied into a historical snapshot. No decision clock yet."""

    quantity: float
    inventory_known: bool
    first_fill_at: datetime | None = None
    entry_order_id: str | None = None

    def __post_init__(self):
        if (isinstance(self.quantity, bool) or not math.isfinite(self.quantity)
                or self.quantity < 0):
            raise ValueError("cycle quantity invalid")
        if type(self.inventory_known) is not bool:
            raise ValueError("inventory_known must be explicit boolean")
        if self.inventory_known and self.quantity > 0:
            if (not isinstance(self.entry_order_id, str) or not self.entry_order_id
                    or not isinstance(self.first_fill_at, datetime)):
                raise ValueError("an open cycle requires first-fill time and order identity")
            if self.first_fill_at.tzinfo is None or self.first_fill_at.utcoffset() is None:
                raise ValueError("fill time must be timezone-aware")
        elif self.inventory_known and (self.first_fill_at is not None or self.entry_order_id):
            raise ValueError("a flat position has no active fill cycle")

    def view(self, symbol: str, decision_at: datetime) -> PositionCycleView:
        return PositionCycleView(
            symbol=symbol, quantity=self.quantity, decision_at=decision_at,
            inventory_known=self.inventory_known, first_fill_at=self.first_fill_at,
            entry_order_id=self.entry_order_id,
        )


def cycle_record_from_position(position) -> PositionCycleRecord:
    """Copy one ledger position. The caller must not read that ledger again."""
    if position is None:
        return PositionCycleRecord(quantity=0.0, inventory_known=True)
    raw_quantity = position.quantity
    if isinstance(raw_quantity, bool):
        raise ValueError("cycle quantity invalid")
    quantity = float(raw_quantity)
    if not math.isfinite(quantity) or quantity < 0:
        raise ValueError("cycle quantity invalid")
    if quantity == 0:
        return PositionCycleRecord(quantity=0.0, inventory_known=True)
    if not bool(getattr(position, "is_active", False)):
        raise ValueError("positive inventory requires an active position")
    metadata = position.metadata if isinstance(getattr(position, "metadata", None), dict) else {}
    entry_ids = metadata.get("entry_order_ids", [])
    entry_id = entry_ids[0] if isinstance(entry_ids, (list, tuple)) and entry_ids else None
    return PositionCycleRecord(
        quantity=quantity,
        inventory_known=True,
        first_fill_at=getattr(position, "opened_at", None),
        entry_order_id=entry_id if isinstance(entry_id, str) and entry_id else None,
    )


def later_fill_must_keep_clock(position) -> bool:
    """A second entry fill belongs to the cycle already opened by the first."""
    metadata = getattr(position, "metadata", None)
    entry_ids = metadata.get("entry_order_ids") if isinstance(metadata, dict) else None
    return (
        isinstance(entry_ids, list)
        and len(entry_ids) > 1
        and getattr(position, "opened_at", None) is not None
    )

