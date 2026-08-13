#!/usr/bin/env python3
"""
Monthly Walk-Forward Optimization Pipeline

Automated monthly re-optimization workflow:
1. Fetch latest data
2. Run parameter sweep on rolling 2-year window
3. Validate out-of-sample on last 3 months
4. Select best params if OOS Sharpe > threshold
5. Update strategy config
6. Send notification
7. Log results

Run via cron: 0 2 1 * * (1st of month, 2 AM)
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trading_agent.data.storage import load_ohlcv
from trading_agent.strategies.enhanced_ma import MaAdxCrossover
from trading_agent.backtest.engine import BacktestEngine


# Default configuration
DEFAULT_CONFIG = {
    "pairs": ["BTC/USDT", "SOL/USDT", "ETH/USDT"],
    "timeframes": ["1h"],
    "lookback_years": 2,  # Training window
    "oos_months": 3,  # Out-of-sample window
    "min_oos_sharpe": 1.0,  # Minimum OOS Sharpe to deploy
    "min_oos_trades": 10,  # Minimum trades in OOS
    "param_grid": {
        "fast_period": [10, 15, 20, 25, 30],
        "slow_period": [40, 50, 60, 80, 100],
        "adx_threshold": [20, 25, 30, 35, 40],
    },
    "fixed_params": {
        "adx_period": 14,
    },
    "backtest_config": {
        "initial_capital": 10000,
        "commission": 0.0005,
        "slippage": 0.0002,
    },
    "data_config": {
        "exchange": "binance",
        "start_date": "2023-01-01",
    },
    "output_dir": "data/wfo_results",
    "config_file": "data/optimal_strategy_config.json",
}


def load_config(config_path: str) -> dict:
    """Load configuration from JSON file, merge with defaults."""
    config = DEFAULT_CONFIG.copy()
    if Path(config_path).exists():
        with open(config_path) as f:
            user_config = json.load(f)
        # Deep merge
        for k, v in user_config.items():
            if isinstance(v, dict) and k in config:
                config[k].update(v)
            else:
                config[k] = v
    return config


def save_config(config: dict, config_path: str):
    """Save configuration to JSON file."""
    Path(config_path).parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def generate_param_combinations(param_grid: dict, fixed_params: dict) -> list:
    """Generate all parameter combinations from grid."""
    import itertools

    keys = list(param_grid.keys())
    values = list(param_grid.values())

    combos = []
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        params.update(fixed_params)
        # Filter invalid: fast < slow
        if params.get("fast_period", 0) >= params.get("slow_period", 1):
            continue
        combos.append(params)

    return combos


def run_backtest(df: pl.DataFrame, params: dict, config: dict) -> dict:
    """Run backtest and return key metrics."""
    try:
        strat = MaAdxCrossover(params=params)
        engine = BacktestEngine(
            strat,
            initial_capital=config["backtest_config"]["initial_capital"],
            commission=config["backtest_config"]["commission"],
            slippage=config["backtest_config"]["slippage"],
        )
        result = engine.run(df, symbol="", timeframe="")

        return {
            "total_return_pct": result.total_return_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
            "profit_factor": result.profit_factor,
            "success": True,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_wfo_for_pair(pair: str, timeframe: str, config: dict) -> dict:
    """Run complete WFO for a single pair/timeframe."""
    print(f"\n{'=' * 60}")
    print(f"WFO: {pair} {timeframe}")
    print(f"{'=' * 60}")

    # Load data
    df = load_ohlcv(config["data_config"]["exchange"], pair, timeframe)
    start_dt = datetime.fromisoformat(config["data_config"]["start_date"])
    df = df.filter(pl.col("timestamp") >= start_dt)

    if len(df) < 500:
        return {"pair": pair, "timeframe": timeframe, "error": "Insufficient data"}

    print(
        f"Data: {len(df)} candles from {df['timestamp'].min()} to {df['timestamp'].max()}"
    )

    # Generate param combinations
    param_combos = generate_param_combinations(
        config["param_grid"], config["fixed_params"]
    )
    print(f"Testing {len(param_combos)} parameter combinations...")

    # Define train/test split
    min_date = df["timestamp"].min()
    max_date = df["timestamp"].max()

    train_end = max_date - timedelta(days=config["oos_months"] * 30)
    train_start = train_end - timedelta(days=config["lookback_years"] * 365)

    train_df = df.filter(
        (pl.col("timestamp") >= train_start) & (pl.col("timestamp") < train_end)
    )
    test_df = df.filter(pl.col("timestamp") >= train_end)

    print(
        f"Train: {len(train_df)} candles ({train_start.date()} to {train_end.date()})"
    )
    print(
        f"Test (OOS): {len(test_df)} candles ({train_end.date()} to {max_date.date()})"
    )

    if len(train_df) < 200 or len(test_df) < 50:
        return {
            "pair": pair,
            "timeframe": timeframe,
            "error": "Insufficient train/test data",
        }

    # In-sample optimization
    print("\nIn-sample optimization...")
    best_is_sharpe = -999
    best_params = None
    best_is_result = None

    for i, params in enumerate(param_combos):
        result = run_backtest(train_df, params, config)
        if not result["success"] or result["total_trades"] < 5:
            continue

        if result["sharpe_ratio"] > best_is_sharpe:
            best_is_sharpe = result["sharpe_ratio"]
            best_params = params.copy()
            best_is_result = result

        if (i + 1) % 20 == 0:
            print(f"  Tested {i + 1}/{len(param_combos)}...")

    if best_params is None:
        return {
            "pair": pair,
            "timeframe": timeframe,
            "error": "No valid IS params found",
        }

    print(
        f"\nBest IS: Sharpe={best_is_sharpe:.3f}, Return={best_is_result['total_return_pct']:.1f}%, "
        f"DD={best_is_result['max_drawdown_pct']:.1f}%, Trades={best_is_result['total_trades']}"
    )
    print(f"Best params: {best_params}")

    # Out-of-sample validation
    print("\nOut-of-sample validation...")
    oos_result = run_backtest(test_df, best_params, config)

    if not oos_result["success"]:
        return {"pair": pair, "timeframe": timeframe, "error": "OOS backtest failed"}

    print(
        f"OOS: Sharpe={oos_result['sharpe_ratio']:.3f}, Return={oos_result['total_return_pct']:.1f}%, "
        f"DD={oos_result['max_drawdown_pct']:.1f}%, Trades={oos_result['total_trades']}"
    )

    # Decision
    deploy = (
        oos_result["sharpe_ratio"] >= config["min_oos_sharpe"]
        and oos_result["total_trades"] >= config["min_oos_trades"]
    )

    result = {
        "pair": pair,
        "timeframe": timeframe,
        "train_period": f"{train_start.date()} to {train_end.date()}",
        "test_period": f"{train_end.date()} to {max_date.date()}",
        "best_params": best_params,
        "is_metrics": best_is_result,
        "oos_metrics": oos_result,
        "deploy": deploy,
        "timestamp": datetime.now().isoformat(),
    }

    if deploy:
        print(
            f"\n✅ DEPLOY: OOS Sharpe {oos_result['sharpe_ratio']:.3f} >= {config['min_oos_sharpe']}"
        )
    else:
        print(
            f"\n❌ REJECT: OOS Sharpe {oos_result['sharpe_ratio']:.3f} < {config['min_oos_sharpe']} "
            f"or trades {oos_result['total_trades']} < {config['min_oos_trades']}"
        )

    return result


def update_production_config(results: list, config_path: str):
    """Update production config with best deployed params."""
    deployed = [r for r in results if r.get("deploy", False)]

    if not deployed:
        print("\n⚠️  No configurations passed OOS validation. Keeping current config.")
        return

    # Select best by OOS Sharpe
    best = max(deployed, key=lambda x: x["oos_metrics"]["sharpe_ratio"])

    production_config = {
        "strategy": "ma_adx",
        "pair": best["pair"],
        "timeframe": best["timeframe"],
        "params": best["best_params"],
        "oos_sharpe": best["oos_metrics"]["sharpe_ratio"],
        "oos_return": best["oos_metrics"]["total_return_pct"],
        "oos_dd": best["oos_metrics"]["max_drawdown_pct"],
        "updated_at": datetime.now().isoformat(),
        "wfo_period": f"{best['train_period']} -> {best['test_period']}",
    }

    save_config(production_config, config_path)
    print(f"\n✅ Updated production config: {config_path}")
    print(f"   Strategy: {production_config['strategy']}")
    print(f"   Pair: {production_config['pair']} {production_config['timeframe']}")
    print(f"   Params: {production_config['params']}")
    print(f"   OOS Sharpe: {production_config['oos_sharpe']:.3f}")


def save_results(results: list, output_dir: str):
    """Save detailed results to JSON."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = Path(output_dir) / f"wfo_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n📁 Results saved to: {filename}")

    # Also save latest symlink
    latest = Path(output_dir) / "wfo_latest.json"
    if latest.exists():
        latest.unlink()
    latest.symlink_to(filename.name)


def send_notification(results: list, config: dict):
    """Send notification (Telegram, email, etc.) - placeholder."""
    # TODO: Implement actual notification
    deployed = [r for r in results if r.get("deploy", False)]
    rejected = [r for r in results if not r.get("deploy", False)]

    msg = f"""
📊 Monthly WFO Complete - {datetime.now().strftime("%Y-%m-%d")}

✅ Deployed: {len(deployed)}
❌ Rejected: {len(rejected)}

"""
    for r in deployed:
        msg += f"  • {r['pair']} {r['timeframe']}: OOS Sharpe={r['oos_metrics']['sharpe_ratio']:.3f}, "
        msg += f"Return={r['oos_metrics']['total_return_pct']:.1f}%\n"

    for r in rejected:
        reason = r.get("error", f"Sharpe={r['oos_metrics']['sharpe_ratio']:.3f}")
        msg += f"  • {r['pair']} {r['timeframe']}: {reason}\n"

    print("\n" + "=" * 60)
    print("NOTIFICATION:")
    print(msg)
    print("=" * 60)


async def fetch_latest_data(config: dict):
    """Fetch latest data from exchange."""
    from trading_agent.data.ccxt_client import fetch_ohlcv

    end_date = datetime.now().strftime("%Y-%m-%d")
    for pair in config["pairs"]:
        for tf in config["timeframes"]:
            try:
                await fetch_ohlcv(
                    config["data_config"]["exchange"],
                    pair,
                    tf,
                    config["data_config"]["start_date"],
                    end_date,
                )
                print(f"  ✅ {pair} {tf}")
            except Exception as e:
                print(f"  ❌ {pair} {tf}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Monthly Walk-Forward Optimization")
    parser.add_argument(
        "--config", default="config/wfo_config.json", help="Config file path"
    )
    parser.add_argument("--pairs", nargs="+", help="Override pairs")
    parser.add_argument("--timeframes", nargs="+", help="Override timeframes")
    parser.add_argument(
        "--dry-run", action="store_true", help="Don't update production config"
    )
    parser.add_argument(
        "--fetch-data", action="store_true", help="Fetch latest data before WFO"
    )
    args = parser.parse_args()

    print(f"🚀 Monthly WFO Pipeline - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load config
    config = load_config(args.config)

    if args.pairs:
        config["pairs"] = args.pairs
    if args.timeframes:
        config["timeframes"] = args.timeframes

    # Fetch latest data if requested
    if args.fetch_data:
        print("\n📥 Fetching latest data...")
        asyncio.run(fetch_latest_data(config))

    # Run WFO for all pairs/timeframes
    all_results = []
    for pair in config["pairs"]:
        for tf in config["timeframes"]:
            result = run_wfo_for_pair(pair, tf, config)
            all_results.append(result)

    # Save results
    save_results(all_results, config["output_dir"])

    # Update production config
    if not args.dry_run:
        update_production_config(all_results, config["config_file"])
    else:
        print("\n🔍 Dry run - production config not updated")

    # Send notification
    send_notification(all_results, config)

    print(
        f"\n✅ WFO Pipeline Complete - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Exit code: 0 if at least one deployed, 1 otherwise
    deployed = any(r.get("deploy", False) for r in all_results)
    sys.exit(0 if deployed else 1)


if __name__ == "__main__":
    main()
