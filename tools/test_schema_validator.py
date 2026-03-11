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
