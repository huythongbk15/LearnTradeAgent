"""Debug: trace params flow in a single cell."""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from trading_agent.backtest.nested_wfo import _generate_param_combinations

# Check what param combos are generated
grid = {"fast_period": [20, 30], "slow_period": [60, 80]}
combos = _generate_param_combinations(grid)
print(f"Param combos: {combos}")

# Now check what params the EvaluationCellSpec would have
from trading_agent.backtest.tournament import EvaluationCellSpec
from trading_agent.backtest.tournament import SCENARIO_BASE

for params in combos:
    spec_val = EvaluationCellSpec(
        strategy_id="ma_vol_target",
        symbol="BTC/USDT",
        timeframe="4h",
        params=params,
        cost_scenario=SCENARIO_BASE,
    )
    print(f"  spec_val.params: {dict(spec_val.params)}")

# Now check what the cell_id looks like
for params in combos:
    spec_val = EvaluationCellSpec(
        strategy_id="ma_vol_target",
        symbol="BTC/USDT",
        timeframe="4h",
        params=params,
        cost_scenario=SCENARIO_BASE,
    )
    print(f"  cell_id: {spec_val.cell_id}")
    print(f"  params_hash: {spec_val.params_hash}")
