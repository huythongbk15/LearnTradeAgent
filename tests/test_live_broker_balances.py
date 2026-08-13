from __future__ import annotations

import pytest

from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.exchanges.models import AssetClass, Balance


class SpotBalanceAdapter:
    class config:
        id = "binance"

    def has_market(self, pair):
        return pair == "BTC/USDT"

    async def fetch_balance(self):
        return {
            AssetClass.CRYPTO: Balance(
                asset_class=AssetClass.CRYPTO,
                assets={
                    "BTC": {"free": 0.02, "used": 0.08, "total": 0.1},
                    "USDT": {"free": 100.0, "used": 5.0, "total": 105.0},
                },
            )
        }

    async def fetch_tickers(self, symbols):
        return {"BTC/USDT": 100.0}


def test_spot_position_snapshot_exposes_free_and_locked_quantity():
    positions = LiveBroker(
        "binance",
        SpotBalanceAdapter(),
        pricing_symbols=["BTC/USDT"],
        strict_pricing=True,
    ).get_positions()
    assert positions == [
        {
            "symbol": "BTC/USDT",
            "side": "long",
            "qty": 0.1,
            "free_qty": 0.02,
            "locked_qty": 0.08,
            "avg_entry_price": 100.0,
            "current_price": 100.0,
            "unrealized_pl": 0.0,
            "unrealized_plpc": 0.0,
            "market_value": 10.0,
        }
    ]


def test_spot_account_uses_free_quote_as_cash_but_total_quote_as_equity():
    account = LiveBroker(
        "binance",
        SpotBalanceAdapter(),
        pricing_symbols=["BTC/USDT"],
        strict_pricing=True,
    ).get_account()
    assert account["cash"] == pytest.approx(100.0)
    assert account["buying_power"] == pytest.approx(100.0)
    assert account["equity"] == pytest.approx(115.0)


def test_inconsistent_spot_balance_fails_closed():
    with pytest.raises(RuntimeError, match="inconsistent"):
        LiveBroker._spot_balance_quantities(
            {
                "free": 0.08,
                "used": 0.08,
                "total": 0.1,
            }
        )
