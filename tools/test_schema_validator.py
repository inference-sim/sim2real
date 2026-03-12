# tools/test_schema_validator.py
import json
import pytest
from pathlib import Path
from tools.schema_validator import validate_artifact

SCHEMA_DIR = Path(__file__).parent / "schemas"

@pytest.fixture
def summary_schema():
    with open(SCHEMA_DIR / "algorithm_summary.schema.json") as f:
        return json.load(f)

def _valid_summary():
    return {
        "algorithm_name": "blis_weighted_scoring",
        "evolve_block_source": "routing/best_program.py:171-242",
        "evolve_block_content_hash": "a" * 64,  # placeholder SHA-256 hex digest
        "signals": [
            {"name": "QueueDepth", "type": "int", "access_path": "snap.QueueDepth"}
        ],
        "composite_signals": [
            {"name": "EffectiveLoad", "constituents": ["QueueDepth", "BatchSize", "InFlightRequests"], "formula": "sum"}
        ],
        "metrics": {"combined_score": -3858.94},
        "scope_validation_passed": True,
        "mapping_artifact_version": "1.0",
        "fidelity_checked": True,
    }

class TestValidateArtifact:
    def test_valid_artifact_passes(self, summary_schema):
        errors = validate_artifact(_valid_summary(), summary_schema)
        assert errors == []

    def test_missing_required_field_fails(self, summary_schema):
        data = _valid_summary()
        del data["algorithm_name"]
        errors = validate_artifact(data, summary_schema)
        assert any("algorithm_name" in e for e in errors)

    def test_wrong_type_fails(self, summary_schema):
        data = _valid_summary()
        data["algorithm_name"] = 123  # should be string
        errors = validate_artifact(data, summary_schema)
        assert any("algorithm_name" in e for e in errors)

    def test_missing_nested_required_field_fails(self, summary_schema):
        data = _valid_summary()
        data["signals"] = [{"name": "QueueDepth"}]  # missing type, access_path
        errors = validate_artifact(data, summary_schema)
        assert len(errors) > 0

    def test_scope_validation_bool_required(self, summary_schema):
        data = _valid_summary()
        data["scope_validation_passed"] = "yes"  # should be bool
        errors = validate_artifact(data, summary_schema)
        assert any("scope_validation_passed" in e for e in errors)

    def test_empty_signals_array_fails_min_items(self, summary_schema):
        """F-13: minItems: 1 constraint rejects empty signals array."""
        data = _valid_summary()
        data["signals"] = []
        errors = validate_artifact(data, summary_schema)
        assert any("minimum" in e.lower() or "minItems" in e.lower() or "0 items" in e for e in errors)

    def test_excess_signals_array_fails_max_items(self, summary_schema):
        """F-10: maxItems: 20 constraint rejects arrays with >20 signals."""
        data = _valid_summary()
        data["signals"] = [
            {"name": f"Signal{i}", "type": "int", "access_path": f"snap.Signal{i}"}
            for i in range(21)
        ]
        errors = validate_artifact(data, summary_schema)
        assert any("maximum" in e.lower() or "21 items" in e for e in errors)

    def test_unexpected_top_level_field_rejected(self, summary_schema):
        """F-13: additionalProperties: false rejects unknown fields."""
        data = _valid_summary()
        data["unexpected_field"] = "should fail"
        errors = validate_artifact(data, summary_schema)
        assert any("unexpected_field" in e for e in errors)

    def test_invalid_hash_pattern_rejected(self, summary_schema):
        """F-18: evolve_block_content_hash must match ^[0-9a-f]{64}$ pattern."""
        data = _valid_summary()
        data["evolve_block_content_hash"] = "invalid_hash"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_hash_with_trailing_chars_rejected(self, summary_schema):
        """F-1: re.fullmatch rejects valid 64-hex-char prefix with trailing chars."""
        data = _valid_summary()
        data["evolve_block_content_hash"] = "a" * 64 + "INVALID"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_absolute_path_evolve_block_source_rejected(self, summary_schema):
        """F-9: evolve_block_source pattern rejects absolute paths."""
        data = _valid_summary()
        data["evolve_block_source"] = "/absolute/path/best_program.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_path_traversal_evolve_block_source_rejected(self, summary_schema):
        """F-16: evolve_block_source pattern rejects relative path traversal."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/../../../etc/passwd.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_dot_slash_evolve_block_source_rejected(self, summary_schema):
        """F-28 fix: evolve_block_source pattern rejects './' current directory reference."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/./best_program.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_invalid_line_range_rejected(self, summary_schema):
        """F-12 fix: semantic check rejects line ranges where start > end (e.g., 242-171)."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/best_program.py:242-171"
        errors = validate_artifact(data, summary_schema)
        assert any("line range" in e.lower() for e in errors), (
            f"Expected line range validation error for 242-171, got: {errors}"
        )

    def test_unsupported_schema_keyword_rejected(self, summary_schema):
        """R3-F-17 fix: Schema using unsupported keywords ($ref, allOf, etc.)
        is rejected with a clear error rather than silently ignored."""
        data = _valid_summary()
        bad_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ref_field": {"$ref": "#/definitions/Foo"},
            },
        }
        errors = validate_artifact(data, bad_schema)
        assert any("unsupported keyword" in e.lower() for e in errors), (
            f"Expected unsupported keyword error for $ref, got: {errors}"
        )

    def test_unsupported_allof_rejected(self, summary_schema):
        """R3-F-17 fix: allOf at top level is rejected."""
        data = _valid_summary()
        bad_schema = {"allOf": [{"type": "object"}, {"required": ["name"]}]}
        errors = validate_artifact(data, bad_schema)
        assert any("unsupported keyword" in e.lower() and "allOf" in e for e in errors), (
            f"Expected unsupported keyword error for allOf, got: {errors}"
        )


# --- Escalation schema tests ---

@pytest.fixture
def escalation_schema():
    with open(SCHEMA_DIR / "escalation.schema.json") as f:
        return json.load(f)


def _valid_escalation():
    return {
        "stage": 3,
        "halt_reason": "unverified_fields",
        "details": "Two metric fields could not be confirmed.",
        "resolved_fields": ["WaitingQueueSize"],
        "unresolved_fields": ["RunningQueueSize", "RunningRequestCount"],
    }


class TestEscalationSchema:
    def test_valid_escalation_passes(self, escalation_schema):
        errors = validate_artifact(_valid_escalation(), escalation_schema)
        assert errors == []

    def test_valid_prerequisite_halt_passes(self, escalation_schema):
        """Prerequisite halt reasons require only base fields."""
        data = {
            "stage": 3,
            "halt_reason": "missing_algorithm_summary",
            "details": "workspace/algorithm_summary.json not found.",
        }
        errors = validate_artifact(data, escalation_schema)
        assert errors == []

    def test_valid_cache_hit_rate_halt_passes(self, escalation_schema):
        data = {
            "stage": 3,
            "halt_reason": "cache_hit_rate_unavailable",
            "details": "CacheHitRate not accessible via GetMetrics().",
            "signal": "CacheHitRate",
            "evolve_block_references": ["penalty_term_3"],
        }
        errors = validate_artifact(data, escalation_schema)
        assert errors == []

    def test_missing_required_field_fails(self, escalation_schema):
        data = _valid_escalation()
        del data["halt_reason"]
        errors = validate_artifact(data, escalation_schema)
        assert any("halt_reason" in e for e in errors)

    def test_invalid_halt_reason_fails(self, escalation_schema):
        data = _valid_escalation()
        data["halt_reason"] = "invalid_reason"
        errors = validate_artifact(data, escalation_schema)
        assert len(errors) > 0

    def test_stage_below_minimum_fails(self, escalation_schema):
        data = _valid_escalation()
        data["stage"] = 0  # minimum is 1
        errors = validate_artifact(data, escalation_schema)
        assert len(errors) > 0

    def test_stage_above_maximum_fails(self, escalation_schema):
        data = _valid_escalation()
        data["stage"] = 99  # maximum is 6
        errors = validate_artifact(data, escalation_schema)
        assert len(errors) > 0

    def test_unverified_fields_halt_without_variant_fields_passes(self, escalation_schema):
        """Known pass-through: unverified_fields halt with only base fields passes schema
        validation. Variant-specific fields (resolved_fields, unresolved_fields) are enforced
        by the pipeline orchestrator, not the schema (see escalation.schema.json description
        and scorer_template.go.md HALT CONDITION notes)."""
        data = {
            "stage": 3,
            "halt_reason": "unverified_fields",
            "details": "Two metric fields could not be confirmed.",
        }
        errors = validate_artifact(data, escalation_schema)
        assert errors == [], f"Expected no errors for base-fields-only unverified_fields halt, got: {errors}"

    def test_cache_hit_rate_halt_without_variant_fields_passes(self, escalation_schema):
        """Known pass-through: cache_hit_rate_unavailable halt with only base fields passes
        schema validation. Variant-specific fields (signal, evolve_block_references) are
        enforced by the pipeline orchestrator, not the schema."""
        data = {
            "stage": 3,
            "halt_reason": "cache_hit_rate_unavailable",
            "details": "CacheHitRate not accessible via GetMetrics().",
        }
        errors = validate_artifact(data, escalation_schema)
        assert errors == [], f"Expected no errors for base-fields-only cache_hit_rate halt, got: {errors}"

    def test_additional_properties_rejected(self, escalation_schema):
        data = _valid_escalation()
        data["unexpected"] = "field"
        errors = validate_artifact(data, escalation_schema)
        assert any("unexpected" in e for e in errors)

    def test_stage_at_minimum_boundary_passes(self, escalation_schema):
        """Boundary test: stage=1 (exact minimum) should pass."""
        data = _valid_escalation()
        data["stage"] = 1
        errors = validate_artifact(data, escalation_schema)
        assert errors == []

    def test_stage_at_maximum_boundary_passes(self, escalation_schema):
        """Boundary test: stage=6 (exact maximum) should pass."""
        data = _valid_escalation()
        data["stage"] = 6
        errors = validate_artifact(data, escalation_schema)
        assert errors == []


# --- Synthetic min/max unit tests (decoupled from escalation schema) ---

class TestMinMaxValidation:
    """Unit tests for minimum/maximum validation using hand-crafted schemas."""

    _SCHEMA = {
        "type": "object",
        "required": ["value"],
        "properties": {
            "value": {"type": "integer", "minimum": 1, "maximum": 10}
        },
    }

    _FLOAT_SCHEMA = {
        "type": "object",
        "required": ["value"],
        "properties": {
            "value": {"type": "number", "minimum": 0.0, "maximum": 1.0}
        },
    }

    def test_value_at_minimum_passes(self):
        errors = validate_artifact({"value": 1}, self._SCHEMA)
        assert errors == []

    def test_value_at_maximum_passes(self):
        errors = validate_artifact({"value": 10}, self._SCHEMA)
        assert errors == []

    def test_value_below_minimum_fails(self):
        errors = validate_artifact({"value": 0}, self._SCHEMA)
        assert any("minimum" in e for e in errors)

    def test_value_above_maximum_fails(self):
        errors = validate_artifact({"value": 11}, self._SCHEMA)
        assert any("maximum" in e for e in errors)

    def test_nan_rejected(self):
        """NaN must not silently pass minimum/maximum validation."""
        errors = validate_artifact({"value": float('nan')}, self._FLOAT_SCHEMA)
        assert any("not a finite number" in e for e in errors)

    def test_positive_infinity_rejected(self):
        errors = validate_artifact({"value": float('inf')}, self._FLOAT_SCHEMA)
        assert any("not a finite number" in e for e in errors)

    def test_negative_infinity_rejected(self):
        errors = validate_artifact({"value": float('-inf')}, self._FLOAT_SCHEMA)
        assert any("not a finite number" in e for e in errors)

    def test_float_at_boundaries_passes(self):
        assert validate_artifact({"value": 0.0}, self._FLOAT_SCHEMA) == []
        assert validate_artifact({"value": 1.0}, self._FLOAT_SCHEMA) == []

    def test_float_outside_boundaries_fails(self):
        errors = validate_artifact({"value": 1.1}, self._FLOAT_SCHEMA)
        assert any("maximum" in e for e in errors)


# --- Stage 3 output schema tests ---

@pytest.fixture
def stage3_output_schema():
    with open(SCHEMA_DIR / "stage3_output.schema.json") as f:
        return json.load(f)


def _valid_stage3_output():
    return {
        "scorer_file": "llm-d-inference-scheduler/pkg/plugins/scorer/evolved_routing_scorer.go",
        "test_file": "llm-d-inference-scheduler/pkg/plugins/scorer/evolved_routing_scorer_test.go",
        "register_file": "llm-d-inference-scheduler/pkg/plugins/register.go",
        "scorer_type": "evolved-routing-scorer",
    }


class TestStage3OutputSchema:
    def test_valid_output_passes(self, stage3_output_schema):
        errors = validate_artifact(_valid_stage3_output(), stage3_output_schema)
        assert errors == []

    def test_missing_required_field_fails(self, stage3_output_schema):
        data = _valid_stage3_output()
        del data["scorer_file"]
        errors = validate_artifact(data, stage3_output_schema)
        assert any("scorer_file" in e for e in errors)

    def test_invalid_scorer_file_path_fails(self, stage3_output_schema):
        """scorer_file must match the scorer package path pattern."""
        data = _valid_stage3_output()
        data["scorer_file"] = "wrong/path/scorer.go"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("pattern" in e for e in errors)

    def test_test_file_as_scorer_file_fails(self, stage3_output_schema):
        """scorer_file must not accept _test.go filenames."""
        data = _valid_stage3_output()
        data["scorer_file"] = "llm-d-inference-scheduler/pkg/plugins/scorer/evolved_routing_scorer_test.go"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("pattern" in e for e in errors)

    def test_invalid_test_file_path_fails(self, stage3_output_schema):
        """test_file must end with _test.go in the scorer package."""
        data = _valid_stage3_output()
        data["test_file"] = "llm-d-inference-scheduler/pkg/plugins/scorer/scorer.go"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("pattern" in e for e in errors)

    def test_invalid_register_file_path_fails(self, stage3_output_schema):
        data = _valid_stage3_output()
        data["register_file"] = "llm-d-inference-scheduler/pkg/plugins/wrong.go"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("pattern" in e for e in errors)

    def test_invalid_scorer_type_fails(self, stage3_output_schema):
        """scorer_type must be lowercase-hyphenated ending with -scorer."""
        data = _valid_stage3_output()
        data["scorer_type"] = "InvalidScorer"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("pattern" in e for e in errors)

    def test_additional_properties_rejected(self, stage3_output_schema):
        data = _valid_stage3_output()
        data["extra"] = "field"
        errors = validate_artifact(data, stage3_output_schema)
        assert any("extra" in e for e in errors)
