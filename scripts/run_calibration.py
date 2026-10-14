#!/usr/bin/env python3
"""Freeze/execute calibration v2. Explicit inputs, no market runs by default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--synthetic-demo", action="store_true")
    mode.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--key", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from trading_agent.calibration.execution import execute, freeze_plan, worker

    if args.worker:
        worker(args.out, args.key)
        return 0
    if args.synthetic_demo:
        if args.out.exists():
            parser.error("synthetic demo requires a new directory")
        from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv

        # Fixture input is kept adjacent, never a default market loader.
        args.out.parent.mkdir(parents=True, exist_ok=True)
        fixture = args.out.parent / (args.out.name + "_synthetic.parquet")
        if fixture.exists():
            parser.error("fixture input already exists")
        generate_synthetic_ohlcv(n_bars=800, seed=7).write_parquet(fixture)
        freeze_plan(args.out, fixture, synthetic=True)
        result = execute(args.out)
    elif args.freeze:
        if args.input is None or args.boundary is None:
            parser.error("--freeze requires --input and --boundary")
        result = freeze_plan(args.out, args.input, boundary_path=args.boundary, synthetic=False)
        print(
            json.dumps(
                {
                    "status": "FROZEN_NOT_EXECUTED",
                    "protocol_fingerprint": result["protocol_fingerprint"],
                }
            )
        )
        return 0
    else:
        result = execute(args.out, args.approval)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ACCEPTED_CALIBRATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
