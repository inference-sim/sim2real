"""Tests for per-workload benchmark_state tracking."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PYTHON = str(REPO / ".venv/bin/python")
CLI = str(REPO / "tools/transfer_cli.py")


def _run_bstate(args: list[str], workspace: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [VENV_PYTHON, CLI, "benchmark-state", "--workspace", str(workspace)] + args,
        cwd=REPO, capture_output=True, text=True,
    )


def _init_state(workspace: Path, namespace: str = "sim2real-test") -> dict:
    """Create a minimal algorithm_summary.json and initialize state."""
    workspace.mkdir(parents=True, exist_ok=True)
    alg = {
        "algorithm_name": "test_algo",
        "evolve_block_source": "x:1-10",
        "evolve_block_content_hash": "a" * 64,
        "signals": [],
        "composite_signals": [],
        "metrics": {"combined_score": 1.0},
        "scope_validation_passed": True,
        "mapping_artifact_version": "1.0",
        "fidelity_checked": True,
    }
    (workspace / "algorithm_summary.json").write_text(json.dumps(alg))
    result = _run_bstate(["--namespace", namespace], workspace)
    assert result.returncode == 0, result.stderr
    return json.loads((workspace / "benchmark_state.json").read_text())


def test_default_state_has_workloads_map(tmp_path):
    """Newly created state has empty workloads map and null runName per phase."""
    state = _init_state(tmp_path)
    for phase in ["noise", "baseline", "treatment"]:
        assert "workloads" in state["phases"][phase]
        assert state["phases"][phase]["workloads"] == {}
        assert state["phases"][phase].get("run_name") is None


def test_set_workload_status(tmp_path):
    """--workload sets per-workload status within a phase."""
    _init_state(tmp_path)
    result = _run_bstate([
        "--set-phase", "baseline",
        "--workload", "overload_mixed_slo",
        "--status", "running",
        "--pipelinerun", "sim2real-baseline-wl-overload-123",
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "benchmark_state.json").read_text())
    wl = state["phases"]["baseline"]["workloads"]["overload_mixed_slo"]
    assert wl["status"] == "running"
    assert wl["pipelinerun_name"] == "sim2real-baseline-wl-overload-123"


def test_set_workload_done(tmp_path):
    """--workload done sets status and records results path."""
    _init_state(tmp_path)
    _run_bstate([
        "--set-phase", "baseline", "--workload", "overload_mixed_slo",
        "--status", "running",
    ], tmp_path)
    result = _run_bstate([
        "--set-phase", "baseline", "--workload", "overload_mixed_slo",
        "--status", "done", "--results", "/tmp/results.json",
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "benchmark_state.json").read_text())
    wl = state["phases"]["baseline"]["workloads"]["overload_mixed_slo"]
    assert wl["status"] == "done"
    assert wl["results_local_path"] == "/tmp/results.json"


def test_set_run_name(tmp_path):
    """--run-name stores the shared phase run name."""
    _init_state(tmp_path)
    # Mark noise as done so baseline can be set to running
    _run_bstate(["--set-phase", "noise", "--status", "done"], tmp_path)
    result = _run_bstate([
        "--set-phase", "baseline",
        "--run-name", "sim2real-baseline-1743600000",
        "--status", "running",
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "benchmark_state.json").read_text())
    assert state["phases"]["baseline"]["run_name"] == "sim2real-baseline-1743600000"


def test_phase_status_reflects_workload_completion(tmp_path):
    """Phase status update (no --workload) still works for phase-level marking."""
    _init_state(tmp_path)
    result = _run_bstate([
        "--set-phase", "baseline", "--status", "done",
        "--results", "/tmp/r.json",
    ], tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / "benchmark_state.json").read_text())
    assert state["phases"]["baseline"]["status"] == "done"


def test_workload_pipelinerun_name_is_experiment_id(tmp_path):
    """--pipelinerun with --workload records the per-workload experimentId, not a shared run name."""
    _init_state(tmp_path)
    # experimentId is unique per PipelineRun; runName is shared per phase
    experiment_id = "sim2real-baseline-wl-overload-1743600001-0"
    _run_bstate([
        "--set-phase", "baseline", "--workload", "overload_mixed_slo",
        "--status", "running", "--pipelinerun", experiment_id,
    ], tmp_path)
    state = json.loads((tmp_path / "benchmark_state.json").read_text())
    wl = state["phases"]["baseline"]["workloads"]["overload_mixed_slo"]
    assert wl["pipelinerun_name"] == experiment_id
    # Phase-level pipelinerun_name is NOT changed by a workload-scoped update
    assert state["phases"]["baseline"]["pipelinerun_name"] is None


def test_migration_adds_workloads_to_old_state(tmp_path):
    """Old state file without workloads/run_name is migrated on first read."""
    old_state = {
        "schema_version": 1,
        "algorithm_name": "test_algo",
        "created_at": "2026-01-01T00:00:00+00:00",
        "cluster_context": "",
        "namespace": "sim2real-test",
        "phases": {
            "noise":     {"status": "done", "results_pvc_path": "noise/"},
            "baseline":  {"status": "pending", "results_pvc_path": "baseline/"},
            "treatment": {"status": "pending", "results_pvc_path": "treatment/"},
        }
    }
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps(old_state))
    # Also need algorithm_summary.json for CLI initialization check
    alg = {
        "algorithm_name": "test_algo", "evolve_block_source": "x:1-10",
        "evolve_block_content_hash": "a" * 64, "signals": [], "composite_signals": [],
        "metrics": {"combined_score": 1.0}, "scope_validation_passed": True,
        "mapping_artifact_version": "1.0", "fidelity_checked": True,
    }
    (tmp_path / "algorithm_summary.json").write_text(json.dumps(alg))
    # Read-only call to trigger migration
    result = _run_bstate([], tmp_path)
    assert result.returncode == 0, result.stderr
    migrated = json.loads(state_file.read_text())
    for phase in ["noise", "baseline", "treatment"]:
        assert "workloads" in migrated["phases"][phase], \
            f"Phase {phase} missing 'workloads' after migration"
        assert "run_name" in migrated["phases"][phase], \
            f"Phase {phase} missing 'run_name' after migration"
    # Existing data preserved
    assert migrated["phases"]["noise"]["status"] == "done"
