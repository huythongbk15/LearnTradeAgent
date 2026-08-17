from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    source = Path(__file__).parents[1] / "scripts" / "benchmark_methodology.py"
    spec = importlib.util.spec_from_file_location("benchmark_methodology", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_methodology_benchmark_is_deterministic_and_honest() -> None:
    benchmark = _module()
    first = benchmark.run_benchmarks()
    second = benchmark.run_benchmarks()
    assert first == second
    assert first["evidence_class"] == "SYNTHETIC_DIAGNOSTIC"
    assert first["mpc_vs_twap_pov"]["status"] == "NOT_EMPIRICALLY_BENCHMARKABLE"
    for name, result in first.items():
        if isinstance(result, dict) and "production_claim" in result:
            assert result["production_claim"] is False


def test_calibration_benchmark_uses_independent_test_and_improves_fixture() -> None:
    result = _module().benchmark_calibration()
    assert result["status"] == "SYNTHETIC_INDEPENDENT_TEST"
    assert result["brier_improved"]
    assert result["ece_improved"]
