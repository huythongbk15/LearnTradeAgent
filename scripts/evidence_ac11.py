
"""AC11 Evidence Script — Shared Capital (Multi-Pair Budget).

Independent oracle: constructs AllocationRequests for two pairs,
hand-computes global budget, verifies pro-rata scaling and no double-counting.
"""
import json
import sys
from pathlib import Path

from trading_agent.authority.config import get_authority_config
from trading_agent.authority.portfolio import (
    PortfolioAllocator, PortfolioSnapshot, AllocationRequest, ReconciliationState,
)
from trading_agent.authority.causation import new_chain
from trading_agent.execution.canonical.risk_decision import RiskLevel

results = []
def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    print(f"[{status}] {name}: {detail}")
    if not cond:
        raise SystemExit(f"AC11 FAIL: {name}")

def make_risk(allowed=0.50, max_new=0.50):
    return type("RD", (), {
        "allowed_target_exposure": allowed,
        "max_new_exposure": max_new,
        "reduce_only": False,
        "risk_level": RiskLevel.LOW,
        "requested_target_exposure": allowed,
    })()

config = get_authority_config()
config.exposure.max_portfolio_exposure = 0.65
config.exposure.max_single_symbol_exposure = 0.30
config.exposure.max_single_strategy_exposure = 0.40

allocator = PortfolioAllocator(config=config)

EQUITY = 100_000.0
AVAILABLE_CASH = 50_000.0
GROSS_EXPOSURE_ABS = 20_000.0
GROSS_EXPOSURE = GROSS_EXPOSURE_ABS / EQUITY  # 0.20 — ratio, not absolute
# high enough notional so liquidity_headroom is not the binding constraint;
# liquidity is tested separately in C9
AVG_DAILY_NOTIONAL = 10_000_000.0

snapshot = PortfolioSnapshot(
    equity=EQUITY, available_cash=AVAILABLE_CASH,
    positions={"ADA_USDT": 0.0, "ETH_USDT": 0.0},
    symbol_exposures={"ADA_USDT": 0.0, "ETH_USDT": 0.0},
    gross_exposure=GROSS_EXPOSURE,
    untracked_exposure=0.0,
    untracked_valued=True,
    reserved_cash=0.0,
    reserved_inventory=0.0,
    reconciliation_state=ReconciliationState.RECONCILED,
)

req1 = AllocationRequest(strategy_id="strat_1", symbol="ADA_USDT",
    risk_decision=make_risk(0.30, 0.30), current_exposure=0.0, equity=EQUITY,
    available_cash=AVAILABLE_CASH, portfolio_exposure=0.20,
    correlation_cluster=None, symbol_total_exposure=0.0,
    causation_chain=new_chain({}), desired_exposure=0.30,
    regime_risk_multiplier=1.0, average_daily_notional=AVG_DAILY_NOTIONAL,
    max_order_participation=0.01)

req2 = AllocationRequest(strategy_id="strat_1", symbol="ETH_USDT",
    risk_decision=make_risk(0.30, 0.30), current_exposure=0.0, equity=EQUITY,
    available_cash=AVAILABLE_CASH, portfolio_exposure=0.20,
    correlation_cluster=None, symbol_total_exposure=0.0,
    causation_chain=new_chain({}), desired_exposure=0.30,
    regime_risk_multiplier=1.0, average_daily_notional=AVG_DAILY_NOTIONAL,
    max_order_participation=0.01)

outcome = allocator.allocate_batch([req1, req2], snapshot)

# ── Independent oracle ────────────────────────────────────────────────
# Must match allocate_batch() contract without importing its internals.
# Stage 1: per-request individual cap
MAX_PORT = config.exposure.max_portfolio_exposure        # 0.65
MAX_SYM  = config.exposure.max_single_symbol_exposure     # 0.30
MAX_STRAT = config.exposure.max_single_strategy_exposure  # 0.40

asked = min(0.30, 0.30)  # min(allowed, max_new)
asked = min(asked, abs(0.30))  # min(asked, |desired_exposure|)
asked = max(0.0, asked) * 1.0  # regime_risk_multiplier
liquidity_headroom = AVG_DAILY_NOTIONAL * 0.01 / EQUITY
symbol_headroom = max(0.0, MAX_SYM - 0.0)  # symbol_total_exposure=0
strat_budget = max(0.0, MAX_STRAT - 0.0) * 1.0  # cluster_adjustment(None)=1.0
capped_ask = min(asked, strat_budget, symbol_headroom, liquidity_headroom)

# Stage 1b: group factors (strategy group is binding for two same-strategy requests)
strat_total = capped_ask * 2
strat_factor = min(1.0, strat_budget / strat_total) if strat_total > 0 else 1.0  # 0.40/0.60
# Symbol groups: single request per symbol → factor 1.0
symbol_factor = 1.0
capped_after_group = capped_ask * strat_factor * symbol_factor  # 0.20

# Stage 2: global increase budget
cash_reserve_pct = max(0.0, 1.0 - MAX_PORT)  # 0.35
budget_cash = max(0.0, (AVAILABLE_CASH / EQUITY) - cash_reserve_pct)  # 0.15
budget_exposure = max(0.0, MAX_PORT - GROSS_EXPOSURE)  # 0.65 - 0.20 = 0.45
global_budget = min(budget_cash, budget_exposure)  # 0.15

total_capped = capped_after_group * 2
if total_capped > global_budget and total_capped > 0:
    scale = global_budget / total_capped  # 0.375
else:
    scale = 1.0

expected_each = capped_after_group * scale
expected_total = expected_each * 2

approved_vals = [e.approved for e in outcome.entries]
check("C1_no_double_counting", outcome.total_approved <= global_budget + 1e-9,
      f"approved={outcome.total_approved:.4f}, oracle_budget={global_budget:.4f}")
check("C2_scale_factor", abs(outcome.scale_factor - scale) < 1e-9,
      f"system={outcome.scale_factor:.6f}, oracle={scale:.6f}")
check("C3_pro_rata_equal", all(abs(v - expected_each) < 1e-9 for v in approved_vals),
      f"approved=[{approved_vals[0]:.4f}, {approved_vals[1]:.4f}], oracle={expected_each:.4f}")
check("C4_total_matches", abs(outcome.total_approved - sum(approved_vals)) < 1e-9,
      f"total={outcome.total_approved:.4f}")
# C5: Order independence
outcome_rev = allocator.allocate_batch([req2, req1], snapshot)
check("C5_order_independent", abs(outcome.total_approved - outcome_rev.total_approved) < 1e-9,
      f"normal={outcome.total_approved:.4f}, reversed={outcome_rev.total_approved:.4f}")
check("C6_symbol_cap", all(v <= MAX_SYM + 1e-9 for v in approved_vals),
      f"max={max(approved_vals):.4f}, cap={MAX_SYM}")
check("C7_strategy_cap", sum(approved_vals) <= MAX_STRAT + 1e-9, f"total={sum(approved_vals):.4f}, cap={MAX_STRAT}")
check("C8_cash_avail", sum(approved_vals) * EQUITY <= AVAILABLE_CASH + 1e-6,
      f"cash_used={sum(approved_vals)*EQUITY:.2f}")
# C9: liquidity headroom binds when notional is tiny
req_low_liq = AllocationRequest(strategy_id="strat_2", symbol="SOL_USDT",
    risk_decision=make_risk(0.30, 0.30), current_exposure=0.0, equity=EQUITY,
    available_cash=AVAILABLE_CASH, portfolio_exposure=0.20,
    correlation_cluster=None, symbol_total_exposure=0.0,
    causation_chain=new_chain({}), desired_exposure=0.30,
    regime_risk_multiplier=1.0, average_daily_notional=1000.0,
    max_order_participation=0.01)
out_low = allocator.allocate_batch([req_low_liq], snapshot)
low_liquidity_headroom = 1000.0 * 0.01 / EQUITY  # 0.0001
check("C9_liquidity_caps", abs(out_low.entries[0].approved - low_liquidity_headroom) < 1e-9,
      f"approved={out_low.entries[0].approved:.6f}, oracle={low_liquidity_headroom:.6f}")

all_pass = all(r["status"] == "PASS" for r in results)
print(f"\n{'='*60}")
print(f"AC11 Evidence: {'ALL PASS' if all_pass else 'FAIL'} ({len(results)} checks)")
print(f"{'='*60}")
ev = {"ac_id":"AC11","oracle":"independent: per-request cap (asked, strat_budget, symbol_headroom, liquidity_headroom) then global pro-rata scale = budget / total_capped",
      "cases":results,"all_pass":all_pass,"total_checks":len(results),
      "passed":sum(1 for r in results if r["status"]=="PASS"),
      "global_budget":global_budget,
      "scale_factor":scale,
      "expected_each":expected_each}
Path("/tmp/ac11_evidence.json").write_text(json.dumps(ev, indent=2, default=str))
print("Evidence: /tmp/ac11_evidence.json")
sys.exit(0 if all_pass else 1)
