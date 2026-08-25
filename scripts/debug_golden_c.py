"""Debug: golden two-pair numbers under batch pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
import tempfile


sys.path.insert(0, "tests")
from test_multi_pair_runtime import (  # noqa: E402
    _build_engine,
    _buy_at_last_bar_df,
    _instrument_rules,
    _make_artifact,
)
from trading_agent.authority.config import AuthorityConfig, Environment  # noqa: E402
from trading_agent.execution.multi_pair_runtime import MultiPairRuntime  # noqa: E402

tmp = Path(tempfile.mkdtemp())
cfg = AuthorityConfig.for_environment(Environment.PAPER)
promotion_store_path = tmp / "promotion.db"
artifact_store_path = tmp / "artifacts"

from trading_agent.authority.promotion_store import PromotionStateStore  # noqa: E402
from trading_agent.research.artifact import PersistentArtifactStore  # noqa: E402

ps = PromotionStateStore(promotion_store_path)
asx = PersistentArtifactStore(artifact_store_path)


def mk(symbol):
    return _make_artifact(asx, ps, symbol=symbol, timeframe="1h", fast=10, slow=30)


btc = mk("BTC/USDT")
eth = mk("ETH/USDT")

rules = {
    "BTC/USDT": _instrument_rules("BTC/USDT"),
    "ETH/USDT": _instrument_rules("ETH/USDT"),
}
eng = _build_engine(cfg, (ps, asx), tmp, rules)

prices = {"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}


def provider(symbol, timeframe):
    if timeframe != "1h" or symbol not in prices:
        return None
    return _buy_at_last_bar_df(prices[symbol])


# Instrument the planner + authorize to see numbers
orig_plan = eng.execution_service.plan


def plan_spy(**kwargs):
    res = orig_plan(**kwargs)
    tgt = kwargs["target"]
    pf = kwargs["portfolio"]
    px = kwargs["price"]
    if res.intent is not None:
        it = res.intent
        print(
            f"PLAN {tgt.symbol}: target_exp={tgt.exposure:.8f} "
            f"equity={pf.equity:.2f} cash={pf.available_cash:.2f} px={px.mid:.4f} "
            f"qty={it.quantity:.8f} resulting={it.resulting_exposure:.8f}"
        )
    else:
        print(f"PLAN {tgt.symbol}: NO INTENT status={res.status}")
    return res


eng.execution_service.plan = plan_spy

rt = MultiPairRuntime(eng)
report = rt.run_cycle(environment="paper", market_data_provider=provider)
print("\n== report ==")
for r in report.results:
    print(f"{r.symbol} {r.status} orders={r.orders_count} {r.detail}")
v = report.portfolio_target_vector
print("targets:", v.targets if v else None)
print("status:", report.status)
eng._graceful_shutdown()
