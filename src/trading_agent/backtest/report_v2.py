"""BacktestReportV2 — canonical schema, semantic validator, and JSON Schema.

Phase S0 deliverable ("Baseline truth và report correctness"):

* ``validate_report_v2`` enforces the S0 exit-gate invariants on any child
  report produced by ``scripts/full_system_backtest.py``:
  - schema_version == 2 and required sections present;
  - no NaN/Infinity anywhere in the document;
  - manifest IDs are content-addressed (``sha256:<64 hex>``);
  - every simulated trade carries bar-time evidence
    (``time_source="simulated_bar"``, entry/exit reference prices, bar indices,
    holding bars, MAE/MFE) — wall-clock ledgers are rejected;
  - cost attribution reconciles (gross − costs = net within tolerance);
  - execution health is clean for a passing run;
  - the data-quality window gate accepted the data.
* ``report_json_schema``/``export_json_schema`` expose a structural JSON Schema
  (draft 2020-12). The committed artifact at
  ``schemas/backtest_report_v2.schema.json`` must stay byte-identical to the
  module export — guarded by tests.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 2

_SHA256_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: Absolute tolerance for |gross − costs − net| reconciliation.
_RECONCILIATION_ABS_TOL = 1e-6
#: Relative tolerance scaled by |gross alpha|.
_RECONCILIATION_REL_TOL = 1e-9

REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "schema_version",
    "report_type",
    "status",
    "symbol",
    "timeframe",
    "final_equity",
    "total_return_pct",
    "sharpe",
    "max_drawdown_pct",
    "total_trades",
    "win_rate_pct",
    "data_manifest_id",
    "feature_artifact_id",
    "active_config",
    "execution_health",
    "simulation_window",
    "data_quality",
    "cost_attribution",
    "benchmarks",
)

_ALLOWED_STATUS = {"passed", "failed"}


class ReportValidationError(ValueError):
    """Raised when a report violates the BacktestReportV2 contract."""


def _walk_nonfinite(node: Any, path: str, violations: list[str]) -> None:
    """Recursively flag NaN/Infinity values anywhere in the document."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            _walk_nonfinite(value, f"{path}.{key}", violations)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _walk_nonfinite(value, f"{path}[{index}]", violations)
    elif isinstance(node, float) and not math.isfinite(node):
        violations.append(f"non-finite number at {path}")


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_tz_aware_iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _check_trade_evidence(trade: Any, index: int, violations: list[str]) -> None:
    label = f"trades[{index}]"
    if not isinstance(trade, Mapping):
        violations.append(f"{label}: not an object")
        return
    metadata = trade.get("metadata")
    simulation = metadata.get("simulation") if isinstance(metadata, Mapping) else None
    if not isinstance(simulation, Mapping):
        violations.append(f"{label}: missing metadata.simulation evidence")
        return
    if simulation.get("time_source") != "simulated_bar":
        violations.append(
            f"{label}: time_source must be 'simulated_bar' "
            f"(got {simulation.get('time_source')!r})"
        )
    for key in ("entry_reference_price", "exit_reference_price"):
        value = simulation.get(key)
        if not _is_finite_number(value) or float(value) <= 0.0:
            violations.append(f"{label}: {key} must be a positive finite number")
    for key in ("entry_time", "exit_time"):
        if not _is_tz_aware_iso(simulation.get(key)):
            violations.append(f"{label}: {key} must be a tz-aware ISO timestamp")
    holding_bars = simulation.get("holding_bars")
    if not isinstance(holding_bars, int) or isinstance(holding_bars, bool) or holding_bars < 0:
        violations.append(f"{label}: holding_bars must be a non-negative integer")
    for key in ("mae_pct", "mfe_pct"):
        if not _is_finite_number(simulation.get(key)):
            violations.append(f"{label}: {key} must be a finite number")


def _check_cost_reconciliation(costs: Any, violations: list[str]) -> None:
    if not isinstance(costs, Mapping):
        violations.append("cost_attribution: not an object")
        return
    if costs.get("complete") is not True:
        violations.append("cost_attribution.complete must be true")
    error = costs.get("reconciliation_error")
    if not _is_finite_number(error):
        violations.append("cost_attribution.reconciliation_error must be finite")
        return
    gross = costs.get("gross_alpha")
    scale = abs(float(gross)) if _is_finite_number(gross) else 0.0
    tolerance = max(_RECONCILIATION_ABS_TOL, _RECONCILIATION_REL_TOL * scale)
    if abs(float(error)) > tolerance:
        violations.append(
            f"cost_attribution.reconciliation_error {float(error):.3e} exceeds "
            f"tolerance {tolerance:.3e}"
        )


def validate_report_v2(report: Mapping[str, Any]) -> list[str]:
    """Return all BacktestReportV2 violations (empty list means valid)."""
    violations: list[str] = []
    if not isinstance(report, Mapping):
        return ["report: not an object"]

    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in report]
    if missing:
        violations.append(f"missing required keys: {sorted(missing)}")
    if report.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            f"schema_version must be {SCHEMA_VERSION} (got {report.get('schema_version')!r})"
        )
    status = report.get("status")
    if status not in _ALLOWED_STATUS:
        violations.append(f"status must be one of {sorted(_ALLOWED_STATUS)}")

    for key in ("symbol", "timeframe"):
        if not isinstance(report.get(key), str) or not report[key]:
            violations.append(f"{key} must be a non-empty string")

    for key in ("data_manifest_id", "feature_artifact_id"):
        value = report.get(key)
        if not isinstance(value, str) or not _SHA256_ID_RE.match(value):
            violations.append(f"{key} must match 'sha256:<64 hex>'")
    commit_sha = report.get("commit_sha")
    if commit_sha is not None and (not isinstance(commit_sha, str) or not commit_sha):
        violations.append("commit_sha must be a non-empty string when present")

    active_config = report.get("active_config")
    if not isinstance(active_config, Mapping) or not active_config.get("config_id"):
        violations.append("active_config.config_id must be a non-empty string")

    data_quality = report.get("data_quality")
    window = data_quality.get("window") if isinstance(data_quality, Mapping) else None
    if not isinstance(window, Mapping) or window.get("accepted") is not True:
        violations.append("data_quality.window.accepted must be true")

    health = report.get("execution_health")
    if not isinstance(health, Mapping):
        violations.append("execution_health: not an object")
    elif report.get("status") == "passed":
        if health.get("unknown_orders") != 0:
            violations.append("execution_health.unknown_orders must be 0")
        if health.get("manual_interventions") != 0:
            violations.append("execution_health.manual_interventions must be 0")
        if health.get("unprotected_positions") != []:
            violations.append("execution_health.unprotected_positions must be empty")
        if health.get("trade_evidence_complete") is not True:
            violations.append("execution_health.trade_evidence_complete must be true")

    _check_cost_reconciliation(report.get("cost_attribution"), violations)

    benchmarks = report.get("benchmarks")
    if not isinstance(benchmarks, Mapping) or "fixed_allocation_buy_and_hold" not in benchmarks:
        violations.append("benchmarks.fixed_allocation_buy_and_hold is required")

    numeric_scalars = (
        "final_equity",
        "total_return_pct",
        "sharpe",
        "max_drawdown_pct",
    )
    for key in numeric_scalars:
        if key in report and not _is_finite_number(report[key]):
            violations.append(f"{key} must be a finite number")
    total_trades = report.get("total_trades")
    if total_trades is not None and (
        not isinstance(total_trades, int)
        or isinstance(total_trades, bool)
        or total_trades < 0
    ):
        violations.append("total_trades must be a non-negative integer")
    win_rate = report.get("win_rate_pct")
    if win_rate is not None and (
        not _is_finite_number(win_rate) or not 0.0 <= float(win_rate) <= 100.0
    ):
        violations.append("win_rate_pct must be within [0, 100]")

    trades = report.get("trades")
    if trades is not None:
        if not isinstance(trades, list):
            violations.append("trades must be a list when present")
        else:
            for index, trade in enumerate(trades):
                _check_trade_evidence(trade, index, violations)

    _walk_nonfinite(report, "report", violations)
    return violations


def ensure_valid_report_v2(report: Mapping[str, Any]) -> None:
    """Raise :class:`ReportValidationError` if the report violates the schema."""
    violations = validate_report_v2(report)
    if violations:
        summary = "; ".join(violations[:10])
        more = f" (+{len(violations) - 10} more)" if len(violations) > 10 else ""
        raise ReportValidationError(f"BacktestReportV2 violations: {summary}{more}")


def report_json_schema() -> dict[str, Any]:
    """Structural JSON Schema (draft 2020-12) for BacktestReportV2."""
    sha256_pattern = r"^sha256:[0-9a-f]{64}$"

    def number_field(description: str) -> dict[str, Any]:
        return {"type": "number", "description": description}

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://learntradeagent.local/schemas/backtest_report_v2.schema.json",
        "title": "BacktestReportV2",
        "description": (
            "Canonical machine-readable full-system backtest report "
            "(Phase S0: baseline truth & report correctness)."
        ),
        "type": "object",
        "required": list(REQUIRED_TOP_LEVEL_KEYS),
        "additionalProperties": True,
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "report_type": {
                "type": "string",
                "enum": ["full_system_backtest"],
            },
            "run_id": {"type": "string"},
            "status": {"type": "string", "enum": sorted(_ALLOWED_STATUS)},
            "symbol": {"type": "string", "minLength": 1},
            "timeframe": {"type": "string", "minLength": 1},
            "exchange": {"type": "string"},
            "final_equity": {"type": "number"},
            "total_return_pct": {"type": "number"},
            "sharpe": {"type": "number"},
            "max_drawdown_pct": {"type": "number"},
            "total_trades": {"type": "integer", "minimum": 0},
            "win_rate_pct": {"type": "number", "minimum": 0, "maximum": 100},
            "profit_factor": number_field("gross profit / gross loss"),
            "circuit_breakers": {"type": "integer", "minimum": 0},
            "signals_seen": {"type": "integer", "minimum": 0},
            "open_positions": {"type": "integer", "minimum": 0},
            "open_orders": {"type": "integer", "minimum": 0},
            "execution_timing": {"type": "string"},
            "commit_sha": {
                "type": "string",
                "minLength": 1,
                "description": "Exact source commit (git rev-parse HEAD or override).",
            },
            "data_manifest_id": {
                "type": "string",
                "pattern": sha256_pattern,
            },
            "feature_artifact_id": {
                "type": "string",
                "pattern": sha256_pattern,
            },
            "state_dir": {"type": "string"},
            "report_path": {"type": "string"},
            "active_config": {
                "type": "object",
                "required": ["config_id"],
                "properties": {
                    "config_id": {"type": "string", "minLength": 1},
                    "provenance": {
                        "type": "object",
                        "properties": {"commit_sha": {"type": "string"}},
                    },
                },
            },
            "execution_health": {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "unknown_orders": {"type": "integer"},
                    "manual_interventions": {"type": "integer"},
                    "unprotected_positions": {"type": "array"},
                    "trade_evidence_complete": {"type": "boolean"},
                },
            },
            "simulation_window": {"type": "object"},
            "data_quality": {
                "type": "object",
                "required": ["window"],
                "properties": {
                    "window": {
                        "type": "object",
                        "required": ["accepted"],
                        "properties": {"accepted": {"const": True}},
                    }
                },
            },
            "cost_attribution": {
                "type": "object",
                "required": ["complete", "reconciliation_error"],
                "properties": {
                    "complete": {"const": True},
                    "reconciliation_error": {"type": "number"},
                    "gross_alpha": {"type": "number"},
                },
            },
            "benchmarks": {
                "type": "object",
                "required": ["fixed_allocation_buy_and_hold"],
            },
            "metrics": {"type": "object"},
            "calendar_returns_pct": {},
            "equity_curve": {
                "type": "array",
                "items": {
                    "type": "array",
                    "prefixItems": [
                        {"type": "string"},
                        {"type": "number"},
                    ],
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
            "trades": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["metadata"],
                    "properties": {
                        "metadata": {
                            "type": "object",
                            "required": ["simulation"],
                            "properties": {
                                "simulation": {
                                    "type": "object",
                                    "required": [
                                        "time_source",
                                        "entry_reference_price",
                                        "exit_reference_price",
                                        "entry_bar_index",
                                        "exit_bar_index",
                                        "holding_bars",
                                        "mae_pct",
                                        "mfe_pct",
                                        "entry_time",
                                        "exit_time",
                                    ],
                                    "properties": {
                                        "time_source": {"const": "simulated_bar"},
                                        "entry_reference_price": {"type": "number"},
                                        "exit_reference_price": {"type": "number"},
                                        "entry_bar_index": {"type": "integer"},
                                        "exit_bar_index": {"type": "integer"},
                                        "holding_bars": {
                                            "type": "integer",
                                            "minimum": 0,
                                        },
                                        "mae_pct": {"type": "number"},
                                        "mfe_pct": {"type": "number"},
                                        "entry_time": {"type": "string", "format": "date-time"},
                                        "exit_time": {"type": "string", "format": "date-time"},
                                    },
                                }
                            },
                        }
                    },
                },
            },
            "signals": {"type": "array"},
        },
    }


def load_json_schema() -> dict[str, Any]:
    """Load the committed JSON Schema artifact shipped with the package."""
    path = Path(__file__).parent / "schemas" / "backtest_report_v2.schema.json"
    with path.open(encoding="utf-8") as handle:
        schema: dict[str, Any] = json.load(handle)
    return schema


def export_json_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Return the JSON Schema; optionally write it to ``path`` (stable ordering)."""
    schema = report_json_schema()
    if path is not None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(schema, handle, indent=2, sort_keys=False, allow_nan=False)
            handle.write("\n")
    return schema


def main() -> None:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(description="Export BacktestReportV2 JSON Schema")
    parser.add_argument("--out", required=True, help="output .schema.json path")
    args = parser.parse_args()
    export_json_schema(args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
