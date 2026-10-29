"""Unregistered entry-only thinning control; no market launcher admission."""
from datetime import UTC, datetime
from hashlib import sha256

from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy

PAIRS = frozenset({"ETH/USDT", "BNB/USDT"})
SEEDS = frozenset({42, 123, 2026, 777, 31415})


def canonical_timestamp(stamp: datetime) -> str:
    """Naive dataset timestamps follow the registered UTC convention."""
    if not isinstance(stamp, datetime):
        raise ValueError("timestamp must be datetime")
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    stamp = stamp.astimezone(UTC)
    if stamp.microsecond or stamp.minute or stamp.second:
        raise ValueError("expected exact hourly bar timestamp")
    return stamp.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def accepts_entry(seed: int, pair: str, stamp: datetime,
                  numerator: int, denominator: int) -> bool:
    if type(seed) is not int or seed not in SEEDS or pair not in PAIRS:
        raise ValueError("unregistered seed or pair")
    if (type(numerator) is not int or type(denominator) is not int
            or denominator <= 0 or not 0 <= numerator <= denominator):
        raise ValueError("invalid exact calibration counts")
    payload = f"{seed}|{pair}|{canonical_timestamp(stamp)}".encode("utf-8")
    h = int.from_bytes(sha256(payload).digest()[:8], "big")
    return h * denominator < numerator * (1 << 64)


class Own24ThinnedFillStrategy(Own24FillContextStrategy):
    """Synthetic-stage control; existing expiry and inventory rules inherited."""

    name = "own24_thinned_fill_context"

    def __init__(self, *, pair: str, seed: int, numerator: int, denominator: int):
        accepts_entry(seed, pair, datetime(2024, 1, 1, tzinfo=UTC),
                      numerator, denominator)
        super().__init__({"arm": "A0"})
        self.params.update(pair=pair, seed=seed, numerator=numerator,
                           denominator=denominator)

    def _allowed(self, df, index):
        if not super()._allowed(df, index):
            return False
        name = "time" if "time" in df.columns else "timestamp"
        return accepts_entry(self.params["seed"], self.params["pair"],
                             df[name][index], self.params["numerator"],
                             self.params["denominator"])

    def generate_signal_with_position(self, df, portfolio_state):
        if getattr(portfolio_state, "symbol", None) != self.params["pair"]:
            raise ValueError("thinning subject differs from position cycle")
        return super().generate_signal_with_position(df, portfolio_state)
