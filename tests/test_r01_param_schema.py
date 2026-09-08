"""
R01 Parameter Schema & Trial Identity Tests

Acceptance criteria from KE_HOACH_CUNG_CO_VA_BAN_GIAO_AGENT.md:

- Validate khóa, kiểu, miền giá trị, quan hệ fast/slow. Unknown key phải báo lỗi; alias nếu giữ phải có migration rõ ràng, không âm thầm fallback.
- Artifact ghi requested params, normalized/effective params, schema version và hash. Effective identity lấy từ cấu hình thực thi, không chỉ chuỗi JSON người dùng gửi.
- Deduplicate cấu hình thực tế trong cùng evaluation context; phân biệt tham số lặp với lần đánh giá hợp lệ khác fold/cost. Không tự đặt effective trial count bằng số cell.
- Test bắt buộc:
  - typo bị từ chối
  - default có chủ đích
  - alias round-trip (nếu có alias)
  - hai cấu hình MA hợp lệ tạo trạng thái constructor khác nhau
  - cùng effective params có cùng identity
  - grid enumerate đúng
- Không đòi mọi cấu hình phải tạo PnL khác nhau.
"""

import pytest
from trading_agent.strategies.canonical.candidates import (
    validate_params,
    compute_effective_params_hash,
    build_legacy_candidate,
    build_parameterized_adapter,
    enumerate_param_grid,
    ParamValidationError,
    FIRST_WAVE_DESCRIPTORS,
)
from trading_agent.strategies.enhanced_ma import (
    EnhancedMaCrossover,
    MaAdxCrossover,
    MaVolTargetCrossover,
)
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy


class TestR01ParamValidation:
    """R01: Parameter validation tests."""

    def test_unknown_key_rejected(self):
        """Typo/unknown key must be rejected (fail-closed)."""
        with pytest.raises(ParamValidationError, match="Unknown parameter"):
            validate_params("rsi", {"period": 14, "unknown_key": 123})

        with pytest.raises(ParamValidationError, match="Unknown parameter"):
            validate_params("enhanced_ma", {"fast": 10, "slow_period": 60})  # typo: fast vs fast_period

        with pytest.raises(ParamValidationError, match="Unknown parameter"):
            validate_params("ma_adx", {"fast_period": 10, "slow": 60})  # typo: slow vs slow_period

    def test_intentional_defaults_applied(self):
        """Missing optional keys get intentional defaults (not silent fallback)."""
        # RSI: all defaults applied
        result = validate_params("rsi", {})
        assert result == {"period": 14, "oversold": 30, "overbought": 70}

        # Enhanced MA: all defaults applied
        result = validate_params("enhanced_ma", {})
        expected = {
            "fast_period": 20,
            "slow_period": 80,
            "adx_period": 14,
            "adx_threshold": 25.0,
            "require_close_above_slow": False,
            "momentum_period": 0,
            "atr_period": 14,
            "atr_sl_mult": 2.0,
            "atr_tp_mult": 3.0,
            "max_dd_pct": 0.15,
            "dd_cooldown_bars": 0,
            "dd_recovery_pct": 0.03,
            "trailing_atr_mult": 0.0,
            "risk_per_trade": 0.02,
        }
        assert result == expected

    def test_alias_roundtrip_if_exists(self):
        """If alias migration exists, it must round-trip explicitly (not implicit fallback).
        
        Currently no aliases are defined, but this test documents the requirement.
        If aliases are added in future, they must be explicit in schema with migration path.
        """
        # No aliases currently defined - this is intentional
        # If added, test would be:
        # normalized = validate_params("rsi", {"rsi_period": 14})  # alias
        # assert normalized["period"] == 14
        pass

    def test_two_valid_ma_configs_different_constructor_state(self):
        """Two valid MA configs must create different constructor states."""
        # Config 1: fast=10, slow=30
        result1 = validate_params("enhanced_ma", {"fast_period": 10, "slow_period": 30})
        strat1 = EnhancedMaCrossover(result1)

        # Config 2: fast=20, slow=60
        result2 = validate_params("enhanced_ma", {"fast_period": 20, "slow_period": 60})
        strat2 = EnhancedMaCrossover(result2)

        # Constructor state differs
        assert strat1.fast == 10
        assert strat1.slow == 30
        assert strat2.fast == 20
        assert strat2.slow == 60

        # Same for ma_adx
        result3 = validate_params("ma_adx", {"fast_period": 10, "slow_period": 30, "adx_threshold": 20})
        strat3 = MaAdxCrossover(result3)
        result4 = validate_params("ma_adx", {"fast_period": 20, "slow_period": 60, "adx_threshold": 30})
        strat4 = MaAdxCrossover(result4)

        assert strat3.fast == 10
        assert strat3.slow == 30
        assert strat3.adx_threshold == 20.0
        assert strat4.fast == 20
        assert strat4.slow == 60
        assert strat4.adx_threshold == 30.0

    def test_same_effective_params_same_identity(self):
        """Same effective params must have same canonical hash (identity)."""
        # Explicit params
        hash1 = compute_effective_params_hash("rsi", {"period": 14, "oversold": 30, "overbought": 70})
        # Missing params (defaults applied)
        hash2 = compute_effective_params_hash("rsi", {})
        # Different order
        hash3 = compute_effective_params_hash("rsi", {"overbought": 70, "period": 14, "oversold": 30})

        assert hash1 == hash2 == hash3, "Same effective params must have same hash"

        # Different params = different hash
        hash4 = compute_effective_params_hash("rsi", {"period": 14, "oversold": 25, "overbought": 75})
        assert hash1 != hash4

    def test_grid_enumeration_correct(self):
        """Grid enumeration must produce correct normalized combinations."""
        grid = {
            "fast_period": [10, 20],
            "slow_period": [30, 60],
            "adx_threshold": [20, 30],
        }
        combos = enumerate_param_grid("ma_adx", grid)

        # Should produce 2 * 2 * 2 = 8 combinations
        assert len(combos) == 8

        # All combos must be valid and normalized
        for combo in combos:
            assert "fast_period" in combo
            assert "slow_period" in combo
            assert "adx_threshold" in combo
            assert "adx_period" in combo  # default applied
            assert combo["fast_period"] < combo["slow_period"]

        # Specific combos present
        expected_combos = [
            {"fast_period": 10, "slow_period": 30, "adx_threshold": 20},
            {"fast_period": 20, "slow_period": 60, "adx_threshold": 30},
        ]
        for expected in expected_combos:
            assert any(
                c["fast_period"] == expected["fast_period"]
                and c["slow_period"] == expected["slow_period"]
                and c["adx_threshold"] == expected["adx_threshold"]
                for c in combos
            )

    def test_grid_invalid_combos_rejected(self):
        """Invalid grid combos (e.g., fast >= slow) must be rejected."""
        grid = {
            "fast_period": [20, 30],
            "slow_period": [10, 20],  # fast >= slow for all combos
        }
        with pytest.raises(ParamValidationError, match="fast_period.*must be < slow_period"):
            enumerate_param_grid("ma_adx", grid)

    def test_type_coercion_works(self):
        """String numbers should be coerced to correct types."""
        result = validate_params("rsi", {"period": "14", "oversold": "30", "overbought": "70"})
        assert result == {"period": 14, "oversold": 30, "overbought": 70}
        assert all(isinstance(v, int) for v in result.values())

        result = validate_params("enhanced_ma", {"fast_period": "10", "slow_period": "60", "adx_threshold": "25.5"})
        assert result["fast_period"] == 10
        assert result["slow_period"] == 60
        assert result["adx_threshold"] == 25.5
        assert isinstance(result["fast_period"], int)
        assert isinstance(result["adx_threshold"], float)

    def test_type_coercion_rejects_invalid(self):
        """Invalid types that can't be coerced must be rejected."""
        with pytest.raises(ParamValidationError, match="expected integer"):
            validate_params("rsi", {"period": "invalid"})

        with pytest.raises(ParamValidationError, match="expected boolean"):
            validate_params("enhanced_ma", {"require_close_above_slow": "true"})

    def test_range_validation(self):
        """Values outside allowed ranges must be rejected."""
        # RSI oversold < 1 or > 49
        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("rsi", {"oversold": 0})

        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("rsi", {"oversold": 50})

        # RSI overbought < 51 or > 99
        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("rsi", {"overbought": 50})

        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("rsi", {"overbought": 100})

        # Enhanced MA fast_period < 2 or > 200
        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("enhanced_ma", {"fast_period": 1})

        with pytest.raises(ParamValidationError, match="minimum|maximum"):
            validate_params("enhanced_ma", {"fast_period": 201})

    def test_cross_param_validation(self):
        """Cross-parameter constraints must be validated."""
        # fast_period >= slow_period
        with pytest.raises(ParamValidationError, match="fast_period.*must be < slow_period"):
            validate_params("enhanced_ma", {"fast_period": 60, "slow_period": 30})

        with pytest.raises(ParamValidationError, match="fast_period.*must be < slow_period"):
            validate_params("ma_adx", {"fast_period": 60, "slow_period": 30})

        with pytest.raises(ParamValidationError, match="fast_period.*must be < slow_period"):
            validate_params("ma_vol_target", {"fast_period": 60, "slow_period": 30})

        # oversold >= overbought - this is caught by range validation first (oversold max 49, overbought min 51)
        with pytest.raises(ParamValidationError):
            validate_params("rsi", {"oversold": 50, "overbought": 50})

    def test_descriptor_parameters_schema_populated(self):
        """All FIRST_WAVE_DESCRIPTORS must have non-trivial parameters_schema."""
        for strategy_id, desc in FIRST_WAVE_DESCRIPTORS.items():
            schema = desc.parameters_schema
            assert schema is not None
            assert schema.get("type") == "object"
            assert schema.get("additionalProperties") is False
            assert "properties" in schema
            assert len(schema["properties"]) > 0

            # Check specific required properties exist
            if strategy_id in ("enhanced_ma", "ma_adx", "ma_vol_target"):
                assert "fast_period" in schema["properties"]
                assert "slow_period" in schema["properties"]
            elif strategy_id == "rsi":
                assert "period" in schema["properties"]
                assert "oversold" in schema["properties"]
                assert "overbought" in schema["properties"]
            elif strategy_id == "bbands":
                assert "period" in schema["properties"]
                assert "std_dev" in schema["properties"]


class TestR01TrialIdentity:
    """R01: Trial identity / effective params hash tests."""

    def test_build_legacy_candidate_validates_params(self):
        """build_legacy_candidate must validate params."""
        with pytest.raises(ParamValidationError):
            build_legacy_candidate("rsi", {"unknown": 123})

        desc, strat = build_legacy_candidate("rsi", {"period": 20})
        assert isinstance(strat, RsiStrategy)
        assert strat.period == 20

    def test_build_parameterized_adapter_hash_in_artifact_id(self):
        """Adapter model_artifact_id must contain effective params hash."""
        desc, adapter = build_parameterized_adapter("rsi", {"period": 14})
        assert "legacy.rsi." in adapter._model_artifact_id
        # Hash part after "legacy.rsi."
        hash_part = adapter._model_artifact_id.split("legacy.rsi.")[-1]
        assert len(hash_part) == 64  # sha256 hex

    def test_same_effective_params_same_artifact_id(self):
        """Same effective params must produce same artifact ID."""
        _, adapter1 = build_parameterized_adapter("rsi", {"period": 14, "oversold": 30, "overbought": 70})
        _, adapter2 = build_parameterized_adapter("rsi", {})  # defaults
        _, adapter3 = build_parameterized_adapter("rsi", {"overbought": 70, "period": 14, "oversold": 30})

        assert adapter1._model_artifact_id == adapter2._model_artifact_id == adapter3._model_artifact_id

    def test_different_effective_params_different_artifact_id(self):
        """Different effective params must produce different artifact ID."""
        _, adapter1 = build_parameterized_adapter("rsi", {"period": 14})
        _, adapter2 = build_parameterized_adapter("rsi", {"period": 20})

        assert adapter1._model_artifact_id != adapter2._model_artifact_id


class TestR01SchemaCompleteness:
    """R01: Schema completeness for all strategies."""

    def test_all_strategies_have_schemas(self):
        """All registered strategies must have parameter schemas."""
        from trading_agent.strategies.canonical.candidates import _PARAM_SCHEMAS

        for strategy_id in ("enhanced_ma", "ma_adx", "ma_vol_target", "rsi", "bbands"):
            assert strategy_id in _PARAM_SCHEMAS
            schema = _PARAM_SCHEMAS[strategy_id]
            assert schema.get("additionalProperties") is False

    def test_defaults_cover_all_schema_properties(self):
        """Defaults dict must cover all properties in schema."""
        from trading_agent.strategies.canonical.candidates import _PARAM_DEFAULTS, _PARAM_SCHEMAS

        for strategy_id, schema in _PARAM_SCHEMAS.items():
            defaults = _PARAM_DEFAULTS[strategy_id]
            for prop in schema["properties"]:
                assert prop in defaults, f"Missing default for {strategy_id}.{prop}"

    def test_all_properties_have_type_and_range(self):
        """All schema properties must have type and range constraints."""
        from trading_agent.strategies.canonical.candidates import _PARAM_SCHEMAS

        for strategy_id, schema in _PARAM_SCHEMAS.items():
            for prop, spec in schema["properties"].items():
                assert "type" in spec, f"{strategy_id}.{prop} missing type"
                assert spec["type"] in ("integer", "number", "boolean"), f"{strategy_id}.{prop} invalid type"
                if spec["type"] in ("integer", "number"):
                    assert "minimum" in spec, f"{strategy_id}.{prop} missing minimum"
                    assert "maximum" in spec, f"{strategy_id}.{prop} missing maximum"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])