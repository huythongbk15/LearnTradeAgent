"""Debug: check param grid flow for vol_target."""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

from trading_agent.research.param_grids import get_param_grid, get_strategy_code_name

strategy_id = "vol_target"
code_name = get_strategy_code_name(strategy_id)
grid = get_param_grid(strategy_id)
print(f"strategy_id: {strategy_id}")
print(f"code_name: {code_name}")
print(f"param_grid: {grid}")

# Now simulate what WFOSpec does
from trading_agent.backtest.nested_wfo import _generate_param_combinations
combos = _generate_param_combinations(grid)
print(f"\nGenerated {len(combos)} param combos:")
for c in combos:
    print(f"  {c}")
