#!/usr/bin/env python3
"""Read-only calibration v2 verification; legacy evidence is never resealed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.calibration.packet import verify_calibration_packet


def verify_calibration(output_root: Path) -> dict[str, bool]:
    """Compatibility checks API; delegates to the same verifier as the producer."""
    return verify_calibration_packet(output_root)["checks"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    result = verify_calibration_packet(args.output_root)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ACCEPTED_CALIBRATION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
