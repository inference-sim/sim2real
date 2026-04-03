"""Tests that the sim2real pipeline template compiles to a single-workload pipeline."""
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TEKTONC = str(REPO / "tektonc-data-collection/tektonc/tektonc.py")
TEMPLATE = str(REPO / "tektonc-data-collection/tektoncsample/sim2real/pipeline.yaml.j2")

MINIMAL_VALUES = """
phase: baseline
stack:
  model:
    modelName: test-model
    helmValues:
      decode:
        replicas: 1
  gateway:
    helmValues: {}
observe:
  image: test-image
  workloads:
    - name: overload_mixed_slo
      spec:
        aggregate_rate: 320
    - name: bursty_adversary
      spec:
        aggregate_rate: 320
gaie_config: {}
inference_objectives: []
"""


def _compile_template(values_text: str) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(values_text)
        values_path = f.name
    result = subprocess.run(
        [sys.executable, TEKTONC, "-t", TEMPLATE, "-f", values_path],
        capture_output=True, text=True,
    )
    Path(values_path).unlink(missing_ok=True)
    assert result.returncode == 0, f"Template compilation failed:\n{result.stderr}"
    return yaml.safe_load(result.stdout)


def test_template_has_required_params():
    """Compiled pipeline declares runName, workloadName, workloadSpec params."""
    pipeline = _compile_template(MINIMAL_VALUES)
    param_names = {p["name"] for p in pipeline["spec"]["params"]}
    assert "experimentId" in param_names
    assert "runName" in param_names
    assert "workloadName" in param_names
    assert "workloadSpec" in param_names


def test_template_has_single_run_workload_task():
    """Compiled pipeline has exactly one run-workload task (loop was removed)."""
    pipeline = _compile_template(MINIMAL_VALUES)
    task_names = [t["name"] for t in pipeline["spec"]["tasks"]]
    run_wl_tasks = [n for n in task_names if n.startswith("run-workload")]
    assert len(run_wl_tasks) == 1, (
        f"Expected 1 run-workload task, found {len(run_wl_tasks)}: {run_wl_tasks}\n"
        "The Jinja workload loop was not removed."
    )
    assert run_wl_tasks[0] == "run-workload"


def test_template_workload_spec_is_runtime_param():
    """workloadSpec in run-workload task uses Tekton param syntax, not Jinja value."""
    pipeline = _compile_template(MINIMAL_VALUES)
    tasks = {t["name"]: t for t in pipeline["spec"]["tasks"]}
    run_wl = tasks["run-workload"]
    params = {p["name"]: p["value"] for p in run_wl["params"]}
    assert params["workloadSpec"] == "$(params.workloadSpec)", (
        f"workloadSpec should be Tekton param, got: {params['workloadSpec']}"
    )


def test_template_results_dir_uses_run_name_param():
    """resultsDir uses $(params.runName) — not experimentId — for PVC path."""
    pipeline = _compile_template(MINIMAL_VALUES)
    tasks = {t["name"]: t for t in pipeline["spec"]["tasks"]}
    run_wl = tasks["run-workload"]
    params = {p["name"]: p["value"] for p in run_wl["params"]}
    assert "$(params.runName)" in params["resultsDir"]
    assert "$(params.workloadName)" in params["resultsDir"]


def test_template_observe_workloads_ignored():
    """Template compiles cleanly even with multiple workloads in values (they are ignored)."""
    pipeline = _compile_template(MINIMAL_VALUES)
    # Still only one run-workload task despite 2 workloads in values
    task_names = [t["name"] for t in pipeline["spec"]["tasks"]]
    assert task_names.count("run-workload") == 1
