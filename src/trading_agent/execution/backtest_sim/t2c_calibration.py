"""T2C: Per-asset slippage calibration loader.

Loads per-symbol base_slippage_bps and slippage_volatility_factor from
``data/slippage_calibration.json`` and provides factory helpers.

Calibration source: historical Binance 1h intrabar ranges (2020–2026).
- base_slippage_bps = P25 intrabar range (quiet-market slippage proxy)
- slippage_volatility_factor = regression slope of intrabar_bps vs realized vol
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_CALIBRATION_PATH = Path("data/slippage_calibration.json")


def load_slippage_calibration(
    path: str | Path | None = None,
) -> dict[str, dict[str, float]]:
    """Load per-symbol slippage calibration.

    Returns a dict like:
        {"BTCUSDT": {"base_slippage_bps": 9.5, "slippage_volatility_factor": 0.55}, ...}

    If the file is not found or malformed, returns an empty dict (falls back
    to global SimulatorConfig defaults).
    """
    calibration_path = Path(path) if path else _DEFAULT_CALIBRATION_PATH
    if not calibration_path.exists():
        logger.warning(
            "T2C calibration file not found at %s — using global defaults",
            calibration_path,
        )
        return {}

    try:
        data = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("T2C calibration parse failed: %s — using global defaults", exc)
        return {}

    assets = data.get("assets", {})
    calibration: dict[str, dict[str, float]] = {}
    for symbol, params in assets.items():
        calibration[symbol] = {
            "base_slippage_bps": float(params.get("base_slippage_bps", 5.0)),
            "slippage_volatility_factor": float(
                params.get("slippage_volatility_factor", 1.0)
            ),
        }
    return calibration


def build_simulator_config_overrides(
    symbol: str,
    path: str | Path | None = None,
) -> dict[str, float]:
    """Return per-symbol slippage overrides for ``create_execution_simulator``.

    Returns empty dict if no calibration exists for ``symbol``.
    """
    calibration = load_slippage_calibration(path)
    if symbol not in calibration:
        return {}
    params = calibration[symbol]
    return {
        "base_slippage_bps": params["base_slippage_bps"],
        "slippage_volatility_factor": params["slippage_volatility_factor"],
    }


def build_all_simulator_config_overrides(
    path: str | Path | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (symbol_slippage_bps, symbol_slippage_vol_factor) for all calibrated assets."""
    calibration = load_slippage_calibration(path)
    symbol_slippage_bps: dict[str, float] = {}
    symbol_slippage_vol_factor: dict[str, float] = {}
    for symbol, params in calibration.items():
        symbol_slippage_bps[symbol] = params["base_slippage_bps"]
        symbol_slippage_vol_factor[symbol] = params["slippage_volatility_factor"]
    return symbol_slippage_bps, symbol_slippage_vol_factor
