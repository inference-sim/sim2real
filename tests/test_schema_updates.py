"""Tests that updated schemas accept new optional fields."""
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parent.parent
VENV_PYTHON = str(REPO / ".venv/bin/python")
CLI = str(REPO / "tools/transfer_cli.py")


def _validate(filename: str, data: dict) -> int:
    """Write data to a temp file named `filename` and validate via CLI."""
    tmp = REPO / "workspace" / filename
    tmp.write_text(json.dumps(data, indent=2))
    result = subprocess.run(
        [VENV_PYTHON, CLI, "validate-schema", str(tmp)],
        cwd=REPO, capture_output=True, text=True,
    )
    tmp.unlink(missing_ok=True)
    return result.returncode


def _base_algo_summary() -> dict:
    """Minimal valid algorithm_summary with all required fields."""
    return {
        "algorithm_name": "test_algo",
        "evolve_block_source": "blis_router/best/best_program.go:177-262",
        "evolve_block_content_hash": "a" * 64,
        "signals": [{"name": "Sig1", "type": "int", "access_path": "snap.Sig1"}],
        "composite_signals": [],
        "metrics": {"combined_score": 1.0},
        "scope_validation_passed": True,
        "mapping_artifact_version": "1.0",
        "fidelity_checked": True,
    }


def test_algo_summary_with_type_refs():
    data = _base_algo_summary()
    data["type_refs"] = [
        {"name": "WeightedScoring", "file_path": "blis_router/best/best_program.go",
         "fields": [{"name": "decayConstant", "type": "float64"}]}
    ]
    assert _validate("algorithm_summary.json", data) == 0


def test_algo_summary_with_helper_refs():
    data = _base_algo_summary()
    data["helper_refs"] = [
        {"name": "computeAffinity", "file_path": "blis_router/best/best_program.go",
         "signature": "func computeAffinity(snap Snapshot) float64", "returns": "float64"}
    ]
    assert _validate("algorithm_summary.json", data) == 0


def test_algo_summary_with_cross_file_deps():
    data = _base_algo_summary()
    data["cross_file_deps"] = [
        {"symbol": "types.Snapshot", "file_path": "blis_router/types.go",
         "usage_note": "Main snapshot type passed to scoring function"}
    ]
    assert _validate("algorithm_summary.json", data) == 0


def test_algo_summary_without_new_fields_still_valid():
    """Existing artifacts without new fields must still pass."""
    data = _base_algo_summary()
    assert _validate("algorithm_summary.json", data) == 0


def _base_signal_coverage() -> dict:
    """Minimal valid signal_coverage with all required fields."""
    return {
        "signals": [{
            "sim_name": "InFlightRequests", "prod_name": "RunningRequestsSize",
            "prod_access_path": "endpoint.GetMetrics().RunningRequestsSize",
            "fidelity_rating": "medium", "staleness_window_ms": 0, "mapped": True,
        }],
        "unmapped_signals": [],
        "commit_hash": "abcdef1",
        "coverage_complete": True,
    }


def test_signal_coverage_with_context_notes():
    data = _base_signal_coverage()
    data["signals"][0]["context_notes"] = "Used as tiebreaker with weight 0.01"
    assert _validate("signal_coverage.json", data) == 0


def test_signal_coverage_with_type_mappings():
    data = _base_signal_coverage()
    data["type_mappings"] = [
        {"sim_type": "WeightedScoring", "prod_type": "ScorerState",
         "notes": "Struct fields map 1:1 except decayConstant"}
    ]
    assert _validate("signal_coverage.json", data) == 0


def test_signal_coverage_with_helper_translations():
    data = _base_signal_coverage()
    data["helper_translations"] = [
        {"sim_function": "computeAffinity", "prod_pattern": "inline in Score()",
         "notes": "No direct function equivalent; logic inlined"}
    ]
    assert _validate("signal_coverage.json", data) == 0


def test_signal_coverage_without_new_fields_still_valid():
    data = _base_signal_coverage()
    assert _validate("signal_coverage.json", data) == 0
