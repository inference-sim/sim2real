# Per-Workload Isolated llm-d Stacks Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each workload its own independent llm-d stack (deploy → run → teardown) across all three benchmark phases, with two parallelism knobs: `--parallel` (phases) × `--parallel-workloads` (workloads within a phase).

**Architecture:** The Tekton pipeline template becomes a single-workload template with `runName` (shared per phase, used for PVC results path) and `experimentId` (unique per PipelineRun, used for k8s resource naming) as separate params. `deploy.py` reads the workload list from `values.yaml` and submits one PipelineRun per workload, with a `ThreadPoolExecutor` controlling concurrency. The `benchmark-state` CLI gains per-workload tracking via a new `--workload` flag.

**Tech Stack:** Python 3.10+ stdlib, Jinja2 (via tektonc), Tekton/kubectl, pytest

**Spec:** `docs/superpowers/specs/2026-04-02-per-workload-isolated-stacks-design.md`

---

## Chunk 1: Template and benchmark-state CLI

### Task 1: Rewrite pipeline template to single-workload

**Files:**
- Modify: `tektonc-data-collection/tektoncsample/sim2real/pipeline.yaml.j2`

This is the highest-leverage change. The template currently loops over all workloads with Jinja. We replace the loop with a single task that accepts workload params at runtime.

- [ ] **Step 1: Replace the Jinja workload loop with a single run-workload task and add the new params**

Open `tektonc-data-collection/tektoncsample/sim2real/pipeline.yaml.j2`.

Replace the entire `params` section at the top:

```yaml
params:
  - name: experimentId
    type: string
  - name: namespace
    type: string
  - name: sleepDuration
    type: string
    default: "30s"
  - name: runName
    type: string
  - name: workloadName
    type: string
  - name: workloadSpec
    type: string
```

Find the `__jinja__` block (the `{% for wl in observe.workloads %}` loop) and replace it entirely with a single static task:

```yaml
    - name: run-workload
      runAfter: ["pause-after-model-deploy", "deploy-httproute", "deploy-inference-objectives"]
      taskRef:
        name: run-workload-blis-observe
      workspaces:
        - name: data
          workspace: data-storage
      params:
        - name: endpoint
          value: "http://$(tasks.deploy-gateway.results.endpoint)/sim2real-$(params.experimentId)"
        - name: model
          value: "{{ stack.model.modelName }}"
        - name: workloadSpec
          value: "$(params.workloadSpec)"
        - name: blisImage
          value: "{{ observe.image }}"
        - name: resultsDir
          value: "{{ phase }}/$(params.runName)/$(params.workloadName)"
    - name: collect-results
      runAfter: ["run-workload"]
      taskRef:
        name: collect-results
      workspaces:
        - name: data
          workspace: data-storage
```

Note: `{{ phase }}`, `{{ stack.model.modelName }}`, `{{ observe.image }}` remain Jinja — resolved at compile time. `$(params.*)` are Tekton runtime expansions.

- [ ] **Step 2: Write a test that compiles the template and asserts the correct structure**

Create `tests/test_pipeline_template.py`:

```python
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
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd sim2real && python -m pytest tests/test_pipeline_template.py -v
```

Expected: FAIL — current template still has the Jinja loop; `test_template_has_single_run_workload_task` will find multiple tasks or fail.

- [ ] **Step 4: Apply the template changes from Step 1, then run tests again**

```bash
cd sim2real && python -m pytest tests/test_pipeline_template.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tektonc-data-collection/tektoncsample/sim2real/pipeline.yaml.j2 tests/test_pipeline_template.py
git commit -m "feat(pipeline): single-workload template with runName/workloadSpec params"
```

---

### Task 2: Extend benchmark-state CLI with per-workload tracking

**Files:**
- Modify: `tools/transfer_cli.py` (functions `_default_benchmark_state`, `cmd_benchmark_state`, and the `benchmark-state` subparser at line ~2937)
- Test: `tests/test_benchmark_state_workload.py` (new file)

The `benchmark-state` CLI currently tracks phase-level status only. We extend it to track per-workload status and `runName` within each phase. New flag: `--workload <name>` scopes `--set-phase` updates to a specific workload.

- [ ] **Step 1: Write failing tests**

Create `tests/test_benchmark_state_workload.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd sim2real && python -m pytest tests/test_benchmark_state_workload.py -v
```

Expected: FAIL — `workloads` key missing from state, `--workload` flag not recognized, migration not yet implemented.

- [ ] **Step 3: Update `_default_benchmark_state` in `tools/transfer_cli.py`**

Find `_default_benchmark_state` (line ~1202). Change `phase_template` to include `workloads` and `run_name`:

```python
def _default_benchmark_state(algorithm_name: str, namespace: str, context: str) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    phase_template = {
        "status": "pending", "pipelinerun_name": None,
        "submitted_at": None, "completed_at": None,
        "results_local_path": None, "failure_reason": None,
        "run_name": None,
        "workloads": {},
    }
    return {
        "schema_version": 1,
        "algorithm_name": algorithm_name,
        "created_at": now,
        "cluster_context": context,
        "namespace": namespace,
        "phases": {
            "noise":     {**phase_template, "results_pvc_path": "noise/"},
            "baseline":  {**phase_template, "results_pvc_path": "baseline/"},
            "treatment": {**phase_template, "results_pvc_path": "treatment/"},
        }
    }
```

- [ ] **Step 4: Add `--workload` and `--run-name` flags to the benchmark-state subparser**

Find the `p_bstate` block (line ~2937):

```python
p_bstate.add_argument("--workload", dest="workload", default=None,
                       help="Workload name for per-workload status update")
p_bstate.add_argument("--run-name", dest="run_name", default=None,
                       help="Shared run name for this phase invocation (PVC results path)")
```

- [ ] **Step 5: Update `cmd_benchmark_state` to handle `--workload` and `--run-name`**

In `cmd_benchmark_state`, find this block starting at line ~1353:

```python
    state["phases"][phase]["status"] = new_status
    if getattr(args, "pipelinerun", None):
        state["phases"][phase]["pipelinerun_name"] = args.pipelinerun
        state["phases"][phase]["submitted_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    if getattr(args, "results", None):
        state["phases"][phase]["results_local_path"] = args.results
        state["phases"][phase]["completed_at"] = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    if getattr(args, "failure_reason", None):
        state["phases"][phase]["failure_reason"] = args.failure_reason
```

Replace the entire block with:

```python
    # set run_name at phase level (no --workload required)
    if getattr(args, "run_name", None):
        state["phases"][phase]["run_name"] = args.run_name

    # per-workload update (--workload scopes the update)
    workload = getattr(args, "workload", None)
    if workload:
        wl_entry = state["phases"][phase].setdefault("workloads", {}).setdefault(workload, {
            "status": "pending", "pipelinerun_name": None,
            "submitted_at": None, "completed_at": None,
            "results_local_path": None, "failure_reason": None,
        })
        wl_entry["status"] = new_status
        if getattr(args, "pipelinerun", None):
            wl_entry["pipelinerun_name"] = args.pipelinerun
            wl_entry["submitted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if getattr(args, "results", None):
            wl_entry["results_local_path"] = args.results
            wl_entry["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if getattr(args, "failure_reason", None):
            wl_entry["failure_reason"] = args.failure_reason
    else:
        # phase-level update (existing logic)
        if getattr(args, "pipelinerun", None):
            state["phases"][phase]["pipelinerun_name"] = args.pipelinerun
            state["phases"][phase]["submitted_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        if getattr(args, "results", None):
            state["phases"][phase]["results_local_path"] = args.results
            state["phases"][phase]["completed_at"] = (
                datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        if getattr(args, "failure_reason", None):
            state["phases"][phase]["failure_reason"] = args.failure_reason
```

The key behavior: when `--workload` is provided, the `--pipelinerun` value records the workload's unique `experimentId` in `wl_entry["pipelinerun_name"]` — it does NOT touch `state["phases"][phase]["pipelinerun_name"]` (the phase-level field). This keeps the two namespaces cleanly separated.

- [ ] **Step 6: Ensure existing state files without `workloads` key are handled gracefully**

In `cmd_benchmark_state`, find the `for expected_phase in _PHASE_ORDER:` validation loop that ends with `return 2` (line ~1292-1299). Immediately after that entire loop, add:

```python
        # Migrate old state files: add workloads map and run_name if missing
        for ph in _PHASE_ORDER:
            state["phases"][ph].setdefault("workloads", {})
            state["phases"][ph].setdefault("run_name", None)
```

The surrounding context looks like:

```python
        for expected_phase in _PHASE_ORDER:
            if expected_phase not in state["phases"]:
                print(
                    f"ERROR: {state_path} 'phases' dict is missing phase '{expected_phase}' — "
                    "file may be corrupted. Delete it to start fresh.",
                    file=sys.stderr,
                )
                return 2
        # ← INSERT MIGRATION HERE, after the loop, inside the `else:` branch
        for ph in _PHASE_ORDER:
            state["phases"][ph].setdefault("workloads", {})
            state["phases"][ph].setdefault("run_name", None)
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
cd sim2real && python -m pytest tests/test_benchmark_state_workload.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 8: Run existing CLI tests to confirm no regression**

```bash
cd sim2real && python -m pytest tools/ tests/ -v -k "not test_benchmark_state_workload and not test_pipeline_template"
```

Expected: all existing tests PASS.

- [ ] **Step 9: Commit**

```bash
git add tools/transfer_cli.py tests/test_benchmark_state_workload.py
git commit -m "feat(cli): per-workload benchmark-state tracking with --workload and --run-name flags"
```

---

## Chunk 2: deploy.py helper functions

### Task 3: Pure data helpers for workload submission

**Files:**
- Modify: `scripts/deploy.py`
- Test: `tests/test_deploy_workload_isolation.py` (new file)

These are pure functions with no I/O: workload slug generation and `runName`/`experimentId` naming. Write and test them before touching the I/O-heavy parts.

- [ ] **Step 1: Write failing tests**

Create `tests/test_deploy_workload_isolation.py`:

```python
"""Tests for per-workload isolation helpers in deploy.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from deploy import _workload_slug, _make_run_name, _make_experiment_id


def test_workload_slug_basic():
    assert _workload_slug("overload_mixed_slo") == "overload-mixed-slo"


def test_workload_slug_already_slug():
    assert _workload_slug("bursty-adversary") == "bursty-adversary"


def test_workload_slug_truncated():
    long_name = "a" * 70
    slug = _workload_slug(long_name)
    assert len(slug) <= 40
    assert slug.isalnum() or all(c.isalnum() or c == "-" for c in slug)


def test_make_run_name():
    name = _make_run_name("baseline", ts=1743600000)
    assert name == "sim2real-baseline-1743600000"


def test_make_experiment_id():
    eid = _make_experiment_id("baseline", "overload_mixed_slo", ts=1743600000, idx=0)
    assert eid == "sim2real-baseline-overload-mixed-slo-1743600000-0"


def test_make_experiment_id_different_workloads_differ():
    eid_a = _make_experiment_id("baseline", "overload_mixed_slo", ts=1743600000, idx=0)
    eid_b = _make_experiment_id("baseline", "bursty_adversary", ts=1743600000, idx=1)
    assert eid_a != eid_b
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py -v
```

Expected: FAIL — functions not defined.

- [ ] **Step 3: Add pure helper functions to `scripts/deploy.py`**

Add after the `_merge_benchmark_into_validation` function (around line 168), before `_clear_phase_state`:

```python
# ── Per-workload isolation helpers ────────────────────────────────────────────

def _workload_slug(name: str) -> str:
    """Convert workload name to a DNS-safe slug (lowercase, hyphens, max 40 chars)."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40].rstrip("-")


def _make_run_name(phase: str, ts: int | None = None) -> str:
    """Shared run name for a phase invocation. Used as PVC results directory."""
    if ts is None:
        ts = int(time.time())
    return f"sim2real-{phase}-{ts}"


def _make_experiment_id(phase: str, workload_name: str, ts: int | None = None,
                         idx: int = 0) -> str:
    """Unique PipelineRun name for a workload. Used for k8s resource naming."""
    if ts is None:
        ts = int(time.time())
    slug = _workload_slug(workload_name)
    return f"sim2real-{phase}-{slug}-{ts}-{idx}"
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy.py tests/test_deploy_workload_isolation.py
git commit -m "feat(deploy): per-workload slug and run/experiment ID helpers"
```

---

### Task 4: Update `_run_pipeline_phase` signature

**Files:**
- Modify: `scripts/deploy.py`

Add `run_name`, `workload_name`, `workload_spec` parameters to `_run_pipeline_phase` and update the generated PipelineRun YAML to include them.

- [ ] **Step 1: Add a test for the new PipelineRun YAML shape**

Append to `tests/test_deploy_workload_isolation.py`:

```python
def test_pipelinerun_yaml_contains_new_params(tmp_path):
    """_run_pipeline_phase generates YAML with runName, workloadName, workloadSpec."""
    import yaml as _yaml
    # We test _build_pipelinerun_yaml (a new pure helper) rather than _run_pipeline_phase
    # which has side effects (kubectl).
    from deploy import _build_pipelinerun_yaml
    manifest_text = _build_pipelinerun_yaml(
        phase="baseline",
        experiment_id="sim2real-baseline-wl-overload-123",
        namespace="sim2real-test",
        run_name="sim2real-baseline-100",
        workload_name="overload_mixed_slo",
        workload_spec='{"aggregate_rate": 320}',
        run_index=0,
    )
    doc = _yaml.safe_load(manifest_text)
    params = {p["name"]: p["value"] for p in doc["spec"]["params"]}
    assert params["experimentId"] == "sim2real-baseline-wl-overload-123"
    assert params["runName"] == "sim2real-baseline-100"
    assert params["workloadName"] == "overload_mixed_slo"
    assert params["workloadSpec"] == '{"aggregate_rate": 320}'
    assert doc["metadata"]["name"] == "sim2real-baseline-wl-overload-123"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py::test_pipelinerun_yaml_contains_new_params -v
```

Expected: FAIL — `_build_pipelinerun_yaml` not defined.

- [ ] **Step 3: Extract PipelineRun YAML construction into `_build_pipelinerun_yaml`**

Add a new pure function after `_make_experiment_id` in `scripts/deploy.py`:

```python
def _build_pipelinerun_yaml(phase: str, experiment_id: str, namespace: str,
                             run_name: str, workload_name: str, workload_spec: str,
                             run_index: int = 0) -> str:
    """Build PipelineRun YAML string for a single workload. No I/O."""
    pipeline_ref_name = f"sim2real-{phase}"
    return f"""apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: {experiment_id}
  namespace: {namespace}
  labels:
    sim2real-phase: {phase}
spec:
  pipelineRef:
    name: {pipeline_ref_name}
  taskRunTemplate:
    serviceAccountName: helm-installer
    podTemplate:
      securityContext:
        runAsUser: 0
  params:
    - name: experimentId
      value: {experiment_id}
    - name: namespace
      value: {namespace}
    - name: runName
      value: {run_name}
    - name: workloadName
      value: {workload_name}
    - name: workloadSpec
      value: '{workload_spec}'
    - name: sleepDuration
      value: "30s"
  workspaces:
    - name: model-cache
      persistentVolumeClaim:
        claimName: model-pvc
    - name: hf-credentials
      secret:
        secretName: hf-secret
    - name: data-storage
      persistentVolumeClaim:
        claimName: data-pvc
"""
```

- [ ] **Step 4: Update `_run_pipeline_phase` signature and body**

Change the function signature (line ~418) from:

```python
def _run_pipeline_phase(phase: str, pipelinecurrent_run: str, namespace: str,
                        run_dir: Path, run_index: int = 0) -> None:
```

to:

```python
def _run_pipeline_phase(phase: str, experiment_id: str, namespace: str,
                        run_dir: Path, run_name: str, workload_name: str,
                        workload_spec: str, run_index: int = 0) -> None:
```

Replace the inline PipelineRun YAML f-string block (lines ~435-467) with a call to `_build_pipelinerun_yaml`:

```python
    pipelinerun_yaml = str(run_dir / f"pipelinerun-{phase}-{run_index}.yaml")
    Path(pipelinerun_yaml).write_text(
        _build_pipelinerun_yaml(phase, experiment_id, namespace,
                                run_name, workload_name, workload_spec, run_index)
    )
```

Also rename `pipelinecurrent_run` → `experiment_id` throughout the function body (it's used in `kubectl apply`, `tkn pr logs`, `tkn pr describe`, and `benchmark-state` calls). Update the `benchmark-state` call at line ~476 to use `experiment_id` for `--pipelinerun` and pass `--workload workload_name`:

```python
    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--workload", workload_name,
             "--status", "running",
             "--pipelinerun", experiment_id],
            check=False, cwd=REPO_ROOT)
```

Similarly update the failure and success state calls (lines ~500-524) to pass `--workload workload_name`.

- [ ] **Step 5: Run tests to confirm new test passes and no regression**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy.py tests/test_deploy_workload_isolation.py
git commit -m "feat(deploy): extract _build_pipelinerun_yaml, update _run_pipeline_phase for per-workload params"
```

---

### Task 5: Add `_run_workloads_for_phase` function

**Files:**
- Modify: `scripts/deploy.py`
- Test: `tests/test_deploy_workload_isolation.py`

This is the core new function: given a phase, workload list, and concurrency limit, submit all workload PipelineRuns and wait for them. Handles partial failures: completed workloads are preserved.

- [ ] **Step 1: Add tests for `_run_workloads_for_phase` behavior**

Append to `tests/test_deploy_workload_isolation.py`:

```python
def test_should_skip_workload_done(tmp_path):
    """_should_skip_workload returns True when workload is marked done in state."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {
            "baseline": {
                "status": "running", "run_name": "sim2real-baseline-100",
                "workloads": {
                    "overload_mixed_slo": {"status": "done"},
                },
            }
        }
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=False) is True


def test_should_skip_workload_force_rerun(tmp_path):
    """_should_skip_workload returns False when force_rerun=True even if done."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {
            "baseline": {
                "workloads": {"overload_mixed_slo": {"status": "done"}},
            }
        }
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=True) is False


def test_should_skip_workload_pending(tmp_path):
    """_should_skip_workload returns False for pending workload."""
    import json
    state_file = tmp_path / "benchmark_state.json"
    state_file.write_text(json.dumps({
        "phases": {"baseline": {"workloads": {"overload_mixed_slo": {"status": "pending"}}}}
    }))
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo", state_file,
                                  force_rerun=False) is False


def test_should_skip_workload_missing_state(tmp_path):
    """_should_skip_workload returns False when state file doesn't exist."""
    from deploy import _should_skip_workload
    assert _should_skip_workload("baseline", "overload_mixed_slo",
                                  tmp_path / "nonexistent.json",
                                  force_rerun=False) is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py::test_should_skip_workload_done -v
```

Expected: FAIL — `_should_skip_workload` not defined.

- [ ] **Step 3: Add `_should_skip_workload` to `scripts/deploy.py`**

Add after `_should_skip_phase`:

```python
def _should_skip_workload(phase: str, workload_name: str, bench_state_file: Path,
                           force_rerun: bool) -> bool:
    """Return True if this workload is already done and should be skipped."""
    if force_rerun:
        return False
    if not bench_state_file.exists():
        return False
    state = json.loads(bench_state_file.read_text())
    wl = state.get("phases", {}).get(phase, {}).get("workloads", {}).get(workload_name, {})
    return wl.get("status") == "done"
```

- [ ] **Step 4: Run workload skip tests to confirm they pass**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py -k "skip_workload" -v
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Add `_run_workloads_for_phase` to `scripts/deploy.py`**

Add after `_should_skip_workload`:

```python
def _run_workloads_for_phase(phase: str, workloads: list[dict], run_dir: Path,
                              namespace: str, bench_state_file: Path,
                              force_rerun: bool, parallel_workloads: int,
                              run_name: str, manifest: dict | None = None) -> None:
    """Submit one PipelineRun per workload and wait for all to complete.

    workloads: list of dicts with keys 'name' and 'spec' (from values.yaml observe.workloads).
    Raises PhaseError if any workload fails; completed workloads are preserved in state.
    """
    ts = int(time.time())

    # Record run_name in state
    with _bench_state_lock:
        run([VENV_PYTHON, CLI, "benchmark-state",
             "--workspace", str(run_dir.parent.parent),
             "--set-phase", phase, "--status", "running",
             "--run-name", run_name],
            check=False, cwd=REPO_ROOT)

    # Preflight once per phase (not per workload — phase-level validation)
    values_path = str(run_dir / "prepare_tekton" / "values.yaml")
    preflight_result = run(
        _preflight_cmd(phase, values_path, namespace, manifest),
        check=False, capture=True, cwd=REPO_ROOT,
    )
    if preflight_result.returncode != 0:
        raise PhaseError(f"Preflight failed for {phase}: {preflight_result.stderr}")

    def _submit_one(idx_wl: tuple[int, dict]) -> tuple[str, str]:
        idx, wl = idx_wl
        wl_name = wl["name"]
        wl_spec = json.dumps(wl.get("spec", {}), separators=(",", ":"))

        if _should_skip_workload(phase, wl_name, bench_state_file, force_rerun):
            info(f"[{phase}/{wl_name}] Already done — skipping")
            return wl_name, "skipped"

        experiment_id = _make_experiment_id(phase, wl_name, ts=ts, idx=idx)
        info(f"[{phase}/{wl_name}] Submitting PipelineRun: {experiment_id}")
        _run_pipeline_phase(phase, experiment_id, namespace, run_dir,
                            run_name=run_name, workload_name=wl_name,
                            workload_spec=wl_spec, run_index=idx)
        ok(f"[{phase}/{wl_name}] Complete")
        return wl_name, "done"

    failed: list[tuple[str, str]] = []

    if parallel_workloads <= 1:
        for idx, wl in enumerate(workloads):
            try:
                _submit_one((idx, wl))
            except PhaseError as exc:
                failed.append((wl["name"], str(exc)))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_workloads) as pool:
            futures = {pool.submit(_submit_one, (idx, wl)): wl["name"]
                       for idx, wl in enumerate(workloads)}
            for future in concurrent.futures.as_completed(futures):
                wl_name = futures[future]
                try:
                    future.result()
                except PhaseError as exc:
                    failed.append((wl_name, str(exc)))

    if failed:
        lines = "\n".join(f"  - {name}: {reason}" for name, reason in failed)
        done_names = [w["name"] for w in workloads
                      if _should_skip_workload(phase, w["name"], bench_state_file, False)]
        done_lines = "\n".join(f"  - {n} (preserved)" for n in done_names)
        msg = (
            f"{phase} phase failed — {len(failed)}/{len(workloads)} workload(s) failed:\n"
            f"{lines}"
        )
        if done_names:
            msg += f"\nCompleted workloads preserved:\n{done_lines}"
        msg += f"\nResume: re-run deploy.py (failed workloads will be retried)"
        raise PhaseError(msg)
```

- [ ] **Step 6: Run all workload isolation tests**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/deploy.py tests/test_deploy_workload_isolation.py
git commit -m "feat(deploy): add _should_skip_workload and _run_workloads_for_phase"
```

---

### Task 6: Update `_gpu_warning`

**Files:**
- Modify: `scripts/deploy.py`
- Test: `tests/test_deploy_workload_isolation.py`

- [ ] **Step 1: Add a test for the updated GPU warning formula**

Append to `tests/test_deploy_workload_isolation.py`:

```python
def test_gpu_warning_formula(capsys):
    """_gpu_warning with both knobs shows correct total GPU count."""
    import yaml, tempfile, os
    values = {
        "stack": {"model": {"helmValues": {"decode": {
            "replicas": 2,
            "resources": {"limits": {"nvidia.com/gpu": "4"}},
        }}}}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        import yaml as _yaml
        _yaml.dump(values, f)
        f.flush()
        values_path = Path(f.name)
    from deploy import _gpu_warning
    _gpu_warning(parallel=2, parallel_workloads=3, values_path=values_path)
    values_path.unlink()
    captured = capsys.readouterr()
    # 2 phases × 3 workloads × (2 replicas × 4 gpus) = 48 GPUs
    assert "48" in captured.err or "48" in captured.out
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py::test_gpu_warning_formula -v
```

Expected: FAIL — signature mismatch.

- [ ] **Step 3: Update `_gpu_warning` in `scripts/deploy.py`**

Find `_gpu_warning` (line ~715). Update its signature and body:

```python
def _gpu_warning(parallel: int, parallel_workloads: int, values_path: Path) -> None:
    """Warn about total GPU demand when running phases or workloads in parallel."""
    if parallel <= 1 and parallel_workloads <= 1:
        return
    try:
        import yaml
        values = yaml.safe_load(values_path.read_text())
        decode = values.get("stack", {}).get("model", {}).get("helmValues", {}).get("decode", {})
        replicas = decode.get("replicas", 1)
        gpu_per_pod = int(
            decode.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", "1")
        )
        gpus_per_stack = replicas * gpu_per_pod
        total = gpus_per_stack * parallel * parallel_workloads
        warn(
            f"parallel={parallel}, parallel_workloads={parallel_workloads} "
            f"→ {total} GPUs required simultaneously "
            f"({gpus_per_stack} per stack × {parallel} concurrent phase(s) "
            f"× {parallel_workloads} concurrent workload(s) per phase)."
        )
        warn(
            "If fewer GPUs are available, Kubernetes will queue pending pods — "
            "phases will complete but wall-clock savings will be reduced."
        )
    except Exception:
        warn(
            f"parallel={parallel}, parallel_workloads={parallel_workloads}: "
            "could not compute GPU demand from values.yaml."
        )
```

Update the call site in `stage_benchmarks` (line ~821) to pass `parallel_workloads`:

```python
_gpu_warning(parallel, parallel_workloads, run_dir / "prepare_tekton" / "values.yaml")
```

- [ ] **Step 4: Run GPU warning test to confirm it passes**

```bash
cd sim2real && python -m pytest tests/test_deploy_workload_isolation.py::test_gpu_warning_formula -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy.py tests/test_deploy_workload_isolation.py
git commit -m "feat(deploy): update _gpu_warning for parallel × parallel_workloads GPU demand"
```

---

## Chunk 3: Orchestration wiring

### Task 7: Update `_run_noise_phase` and `stage_benchmarks`

**Files:**
- Modify: `scripts/deploy.py` — `_run_noise_phase`, `stage_benchmarks`, `build_parser`, `_run_single_phase`

This is the final wiring step: connect `_run_workloads_for_phase` into the noise loop and into `stage_benchmarks`. Add the `--parallel-workloads` CLI argument.

- [ ] **Step 1: Add `--parallel-workloads` argument to `build_parser`**

Find `build_parser` (line ~81). Add after the `--parallel` argument:

```python
p.add_argument("--parallel-workloads", type=int, default=1, metavar="N",
               dest="parallel_workloads",
               help="Max workload stacks to run concurrently within a phase (default: 1)")
```

- [ ] **Step 2: Update `_run_noise_phase` to loop workloads per pass**

Replace the body of `_run_noise_phase` (line ~674):

```python
def _run_noise_phase(run_dir: Path, namespace: str, workspace_dir: Path,
                     parallel_workloads: int = 1,
                     force_rerun: bool = False,
                     manifest: dict | None = None) -> None:
    """Run sequential noise passes; within each pass, workloads run in parallel."""
    import yaml

    values_path = run_dir / "prepare_tekton" / "values.yaml"
    values = yaml.safe_load(values_path.read_text())
    noise_runs = values.get("observe", {}).get("noise_runs", 3)
    workloads = values.get("observe", {}).get("workloads", [])
    info(f"Running {noise_runs} noise pass(es) × {len(workloads)} workload(s)...")

    bench_state_file = workspace_dir / "benchmark_state.json"
    noise_experiment_ids: list[str] = []

    for i in range(noise_runs):
        pass_run_name = f"sim2real-noise-run{i}-{int(time.time())}"
        info(f"Noise pass {i}/{noise_runs - 1}: run_name={pass_run_name}")

        _run_workloads_for_phase(
            phase="noise",
            workloads=workloads,
            run_dir=run_dir,
            namespace=namespace,
            bench_state_file=bench_state_file,
            force_rerun=force_rerun,
            parallel_workloads=parallel_workloads,
            run_name=pass_run_name,
            manifest=manifest,
        )
        noise_experiment_ids.append(pass_run_name)

    _extract_phase_results("noise", namespace, run_dir,
                           experiment_ids=noise_experiment_ids)
    ok(f"Noise characterization complete: {run_dir / 'deploy_noise_results.json'}")
```

- [ ] **Step 3: Update `_run_single_phase` to thread `parallel_workloads` and `force_rerun`**

Find `_run_single_phase` (line ~741). Update the signature and body:

```python
def _run_single_phase(phase: str, run_dir: Path, namespace: str,
                      workspace_dir: Path, bench_state_file: Path,
                      force_rerun: bool, parallel_workloads: int = 1,
                      manifest: dict | None = None) -> tuple[str, str]:
```

In the non-noise branch, replace the existing single-PipelineRun block with a call to `_run_workloads_for_phase`:

```python
    if phase == "noise":
        _run_noise_phase(run_dir, namespace, workspace_dir,
                         parallel_workloads=parallel_workloads,
                         force_rerun=force_rerun, manifest=manifest)
    else:
        import yaml
        values = yaml.safe_load(
            (run_dir / "prepare_tekton" / "values.yaml").read_text()
        )
        workloads = values.get("observe", {}).get("workloads", [])
        run_name = _make_run_name(phase)
        _run_workloads_for_phase(
            phase=phase,
            workloads=workloads,
            run_dir=run_dir,
            namespace=namespace,
            bench_state_file=bench_state_file,
            force_rerun=force_rerun,
            parallel_workloads=parallel_workloads,
            run_name=run_name,
            manifest=manifest,
        )
        _extract_phase_results(phase, namespace, run_dir,
                               experiment_ids=[run_name])
```

- [ ] **Step 4: Update `stage_benchmarks` sequential path**

Find the sequential path in `stage_benchmarks` (line ~824). Replace the `if phase == "noise": ... else: ...` block with a call to `_run_single_phase` (passing `parallel_workloads`), or inline `_run_workloads_for_phase` similarly. Also update the `_run_single_phase` calls in the `ThreadPoolExecutor` path to pass `parallel_workloads`.

In `stage_benchmarks`, update the function signature to accept `parallel_workloads`:

```python
def stage_benchmarks(run_dir: Path, namespace: str, fast_iter: bool, manifest: dict,
                     force_rerun: bool = False, parallel: int = 1,
                     parallel_workloads: int = 1) -> str:
```

Sequential path (replacing the existing inner `if phase == "noise":` block):

```python
    if parallel <= 1:
        for phase in phases_to_run:
            skip, msg = _should_skip_phase(
                phase, bench_state_file,
                force_rerun=force_rerun,
                interactive=sys.stdin.isatty(),
                results_path=run_dir / f"deploy_{phase}_results.json",
            )
            if skip:
                info(msg)
                continue
            if msg:
                info(msg)
            _clear_phase_state(phase, bench_state_file)
            _run_single_phase(phase, run_dir, namespace, workspace_dir,
                              bench_state_file, force_rerun=force_rerun,
                              parallel_workloads=parallel_workloads,
                              manifest=manifest)
```

Parallel path (in the `ThreadPoolExecutor` block, update the `pool.submit` call — use keyword arguments to match the sequential path):

```python
            futures = {
                pool.submit(
                    _run_single_phase, phase, run_dir, namespace,
                    workspace_dir, bench_state_file,
                    force_rerun=force_rerun,
                    parallel_workloads=parallel_workloads,
                    manifest=manifest,
                ): phase
                for phase in phases_to_run
            }
```

- [ ] **Step 5: Wire `parallel_workloads` from `main` through to `stage_benchmarks`**

Find `main()` (or wherever `stage_benchmarks` is called). Pass `args.parallel_workloads`:

```python
verdict = stage_benchmarks(
    run_dir, namespace, fast_iter, manifest,
    force_rerun=args.force_rerun,
    parallel=args.parallel,
    parallel_workloads=args.parallel_workloads,
)
```

- [ ] **Step 6: Run the full test suite**

```bash
cd sim2real && python -m pytest tools/ tests/ -v
```

Expected: all tests PASS. If any existing deploy.py tests exist, they should still pass.

- [ ] **Step 7: Manual smoke test (if cluster available)**

If a cluster is accessible, do a dry-run check:
```bash
python scripts/deploy.py --skip-build-epp --parallel-workloads 1 --help
```
Expected: `--parallel-workloads` appears in usage.

- [ ] **Step 8: Commit**

```bash
git add scripts/deploy.py
git commit -m "feat(deploy): wire --parallel-workloads through noise phase and stage_benchmarks"
```

---

### Task 8: Final integration check and plan sign-off

**Files:**
- No new files

- [ ] **Step 1: Run full test suite one final time**

```bash
cd sim2real && python -m pytest tools/ tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 2: Verify help text shows both knobs with GPU warning context**

```bash
python scripts/deploy.py --help
```

Expected output includes both:
```
  --parallel N          Max pipeline phases to run concurrently (default: 1)
  --parallel-workloads N
                        Max workload stacks to run concurrently within a phase (default: 1)
```

- [ ] **Step 3: Verify template compiles for all three phases**

```bash
for phase in noise baseline treatment; do
  echo "=== $phase ===" && \
  python tektonc-data-collection/tektonc/tektonc.py \
    -t tektonc-data-collection/tektoncsample/sim2real/pipeline.yaml.j2 \
    -f /dev/stdin --explain <<EOF
phase: $phase
stack:
  model:
    modelName: test-model
    helmValues: {}
  gateway:
    helmValues: {}
observe:
  image: test-image
  workloads: []
gaie_config: {}
inference_objectives: []
EOF
done
```

Expected: task dependency table printed for each phase, no errors.

- [ ] **Step 4: Final commit**

```bash
git add -p  # review any unstaged changes
git commit -m "feat(deploy): per-workload isolated llm-d stacks with --parallel-workloads"
```
