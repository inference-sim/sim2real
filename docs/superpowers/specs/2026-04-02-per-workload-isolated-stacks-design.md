# Design: Per-Workload Isolated llm-d Stacks

**Date:** 2026-04-02
**Status:** Approved

## Problem

The current Tekton pipeline deploys one shared llm-d stack (gateway + GAIE + model) per phase
and runs all workloads sequentially against it. Residual state from one workload (in-flight
requests, KV cache, scheduler state) can contaminate the next.

The goal is to give each workload its own independent, clean llm-d stack: full deploy → run →
teardown per workload, with no shared infrastructure between workloads. Applies to all three
benchmark phases: noise, baseline, and treatment.

## Approach: Lift Workload to Tekton Pipeline Parameter

`pipeline.yaml.j2` becomes a single-workload template. `workloadName` and `workloadSpec` are
promoted from Jinja compile-time variables to Tekton pipeline-level runtime parameters.
`compile-pipeline` CLI flags are unchanged — still called once per phase, produces one pipeline
definition per phase. `deploy.py` submits N PipelineRuns per phase — one per workload — each
with its own independent stack.

## Pipeline Template Changes (`pipeline.yaml.j2`)

### Two-Param Separation

**`experimentId`** (unique per PipelineRun): used exclusively for Kubernetes resource names —
gateway, GAIE deployment, model label, HTTPRoute, teardown tasks. Ensures no k8s resource
conflicts between concurrent workload stacks. Format: `sim2real-{phase}-{wl_slug}-{timestamp+i}`.

**`runName`** (shared across all workload PipelineRuns within a phase invocation): used
exclusively for the PVC results path. Keeps results directory layout identical to today.
Format: `sim2real-{phase}-{timestamp}`.

### New Tekton Pipeline Params

```yaml
params:
  - name: experimentId    # unique per PipelineRun — k8s resource naming only
  - name: namespace
  - name: sleepDuration
    default: "30s"
  - name: runName         # shared per phase — PVC results path only
  - name: workloadName    # name of this workload (e.g. overload_mixed_slo)
  - name: workloadSpec    # JSON string of the workload spec dict
```

### Template Body Changes

Remove the `{% for wl in observe.workloads %}` Jinja loop. Replace with a single
`run-workload-blis-observe` task. `$(params.workloadName)` and `$(params.workloadSpec)` are
Tekton runtime variable expansions (not Jinja). `{{ phase }}` and `{{ observe.image }}` remain
Jinja compile-time expansions.

```yaml
- name: run-workload
  runAfter: ["pause-after-model-deploy", "deploy-httproute", "deploy-inference-objectives"]
  taskRef:
    name: run-workload-blis-observe
  params:
    - name: endpoint
      value: "http://$(tasks.deploy-gateway.results.endpoint)/sim2real-$(params.experimentId)"
    - name: model
      value: "{{ stack.model.modelName }}"
    - name: workloadSpec
      value: "$(params.workloadSpec)"        # Tekton param — runtime
    - name: blisImage
      value: "{{ observe.image }}"
    - name: resultsDir
      value: "{{ phase }}/$(params.runName)/$(params.workloadName)"  # runName = shared
```

`collect-results` runs after this single task. The `finally` teardown block is unchanged —
it already scopes all k8s resource names on `$(params.experimentId)`.

### `compile-pipeline` Interface

CLI flags unchanged. The template no longer iterates `observe.workloads` — that key remains
in `values.yaml` (it is read by `deploy.py` at submission time) but is now a no-op during
template compilation. Old templates in other sample directories (`blis/`, `blis-inference-perf/`)
are unaffected — they remain in their own subdirectories.

### Workload Spec Extraction

`deploy.py` reads `observe.workloads` from `values.yaml`. Each entry already contains an
embedded `spec` dict (placed there by `merge-values`). At submission time, `deploy.py`
serializes `wl['spec']` to a compact JSON string and passes it as the `workloadSpec` param:

```python
workload_spec_json = json.dumps(wl["spec"], separators=(",", ":"))
```

No file path resolution needed — the spec is already materialized in `values.yaml`.

## `deploy.py` Changes

### New CLI Argument

```
--parallel-workloads N   Max workload stacks to run concurrently within a phase (default: 1)
```

### GPU Warning

Updated to account for both parallelism dimensions. Warning fires when either
`--parallel > 1` or `--parallel-workloads > 1`:

```
parallel={P}, parallel_workloads={W}
→ {P × W × gpus_per_stack} GPUs required simultaneously
  ({gpus_per_stack} per stack × {P} concurrent phases × {W} concurrent workloads per phase)
```

Where `gpus_per_stack = replicas × gpu_per_pod` (same formula as before, renamed from
`gpus_per_phase`). Total reflects worst-case simultaneous demand across all running phases.

### `_run_pipeline_phase` Signature

New signature (breaking change to internal function):

```python
def _run_pipeline_phase(
    phase: str,
    experiment_id: str,    # unique per PipelineRun — k8s resource naming
    namespace: str,
    run_dir: Path,
    run_name: str,         # NEW: shared per phase — PVC results path
    workload_name: str,    # NEW
    workload_spec: str,    # NEW: JSON string
    run_index: int = 0,
) -> None:
```

All three call sites must be updated:
- Sequential baseline/treatment path in `stage_benchmarks`
- Noise pass loop in `_run_noise_phase`
- `_run_single_phase` (used by parallel phase `ThreadPoolExecutor`)

### PipelineRun YAML (emitted by `_run_pipeline_phase`)

```yaml
params:
  - name: experimentId
    value: {experiment_id}
  - name: namespace
    value: {namespace}
  - name: runName           # NEW
    value: {run_name}
  - name: workloadName      # NEW
    value: {workload_name}
  - name: workloadSpec      # NEW
    value: '{workload_spec_json}'
  - name: sleepDuration
    value: "30s"
```

### Phase Execution Model (Baseline and Treatment)

For each phase invocation:

1. Read `observe.workloads` from `values.yaml` to get workload list
2. Generate `runName` once: `sim2real-{phase}-{timestamp}` (shared across workloads)
3. For each workload, generate unique `experimentId`:
   `sim2real-{phase}-{wl_slug}-{timestamp+i}` (where `i` is the workload index)
4. Create one `ThreadPoolExecutor(max_workers=parallel_workloads)` per phase invocation
5. Submit all workloads to the pool; each thread runs:
   - preflight (phase-level, validates cluster readiness for this phase — unchanged interface)
   - submit PipelineRun via `kubectl apply` (passing all params above)
   - poll `tkn pr describe` until terminal state
6. Collect thread results; if **any workload fails**, the phase is marked failed (see error
   handling below)
7. After all threads complete, call `_extract_phase_results(phase, namespace, run_dir,
   experiment_ids=[run_name])` — single-element list, same as today

The `ThreadPoolExecutor` is created and destroyed per phase invocation. Phases themselves are
still orchestrated by the outer `--parallel` mechanism (unchanged).

### Noise Phase

Passes remain sequential. Each pass:

1. Generate per-pass `run_name`: `sim2real-noise-run{i}-{timestamp}` (same format as today's
   per-pass `pipelinecurrent_run`, but now used as `runName` param, not `experimentId`)
2. For each workload in that pass, generate unique `experimentId`:
   `sim2real-noise-run{i}-{wl_slug}-{timestamp+j}`
3. Submit up to `--parallel-workloads N` workload PipelineRuns concurrently within the pass
4. Wait for all workloads in the pass to complete before starting pass i+1
5. Append `run_name` to `noise_experiment_ids` (not individual workload experimentIds)

After all passes, `_extract_phase_results("noise", ..., experiment_ids=noise_experiment_ids)`
is called with the list of per-pass `runName` values — same as today's list of per-pass
experimentIds. `_reorganize_noise_results` and `_extract_phase_results` are unchanged.

### Error Handling for Partial Workload Failures

Within a `ThreadPoolExecutor`, each workload thread raises `PhaseError` on failure. The
calling code collects all futures before surfacing errors (uses `as_completed` + exception
check). If **any workload fails**:

- All other workloads in the phase that have completed are left in `benchmark_state.json`
  with status `done` (their results are preserved on the PVC)
- The phase is marked `failed` in `benchmark_state.json`
- The script exits with an error message listing which workloads failed

On resume (without `--force-rerun`): workloads already marked `done` in `benchmark_state.json`
are skipped. Only failed or pending workloads are re-run.

`--force-rerun`: clears all per-workload state for the phase, re-runs everything.

### Skip/Resume: `benchmark_state.json` Schema

The per-phase entry is extended with a `workloads` map and `runName`:

```json
{
  "phases": {
    "baseline": {
      "status": "running|done|failed",
      "runName": "sim2real-baseline-1743600000",
      "workloads": {
        "overload_mixed_slo": {
          "status": "done|running|pending|failed",
          "experimentId": "sim2real-baseline-wl-overload-1743600001"
        },
        "bursty_adversary": {
          "status": "pending",
          "experimentId": null
        }
      }
    }
  }
}
```

The `benchmark-state` CLI subcommand gains a `--workload` flag to set individual workload
status. Existing phase-level flags (`--set-phase`, `--status`) continue to work for
backward-compatible tooling.

Skip logic for a workload: status is `done` in `benchmark_state.json`. Fallback: results
directory `{phase}/{runName}/{wl_name}/` exists on the PVC (checked via the extractor).

### Preflight

Preflight is called once per workload PipelineRun, before submission. It remains phase-level
(validates that the cluster is ready to run this phase — e.g., the model image is available,
RBAC is configured). No per-workload validation is added. The preflight interface is unchanged.

## Result Extraction

### Baseline and Treatment

PVC layout is **identical to today**: `{phase}/{runName}/{wl_name}/`. All workloads in a phase
write under the same `runName` directory, so the extractor finds all workload subdirectories
in one pass.

`_extract_phase_results` is called once per phase with `experiment_ids=[run_name]` —
single-element list, unchanged behavior.

### Noise

`_extract_phase_results` is called with `experiment_ids=[passRunName_0, passRunName_1, ...]`
(N per-pass `runName` values). Each pass's `runName` directory contains all workload
subdirectories for that pass. `_reorganize_noise_results` reads
`noise/{passRunName}/{wl_name}/` for each pass and reorganizes into `wl_name/run-{i}/` —
unchanged logic.

## Change Surface Summary

| Component | Changes? | Notes |
|---|---|---|
| `pipeline.yaml.j2` | Yes | Remove workload loop; add `runName`, `workloadName`, `workloadSpec` params |
| `compile-pipeline` CLI | No | Flags unchanged; `observe.workloads` ignored by template |
| `_run_pipeline_phase` | Yes | New signature: `run_name`, `workload_name`, `workload_spec` args; all 3 call sites updated |
| `stage_benchmarks` | Yes | Loops over workloads; manages per-phase `ThreadPoolExecutor` |
| `_run_noise_phase` | Yes | Inner loop over workloads per pass; per-pass `run_name` generation |
| `_run_single_phase` | Yes | Updated to pass workload params through to `_run_pipeline_phase` |
| `_gpu_warning` | Yes | Accounts for `--parallel` × `--parallel-workloads`; new formula |
| `_extract_phase_results` | No | Receives `[run_name]` as before |
| `_reorganize_noise_results` | No | Reads same directory structure |
| `benchmark_state.json` schema | Yes | Per-workload `workloads` map + `runName` field per phase |
| `benchmark-state` CLI | Yes | Gains `--workload` flag for per-workload status updates |
| `build_parser` | Yes | New `--parallel-workloads` argument |

## PVC Layout Example

```
data-pvc/
  baseline/
    sim2real-baseline-1743600000/         ← runName (shared across workloads)
      overload_mixed_slo/                 ← workload A results
      bursty_adversary/                   ← workload B results
  treatment/
    sim2real-treatment-1743600100/        ← runName
      overload_mixed_slo/
      bursty_adversary/
  noise/
    sim2real-noise-run0-1743600200/       ← pass 0 runName
      overload_mixed_slo/
      bursty_adversary/
    sim2real-noise-run1-1743600300/       ← pass 1 runName
      overload_mixed_slo/
      bursty_adversary/
```

## Concurrent Stack Example

With `--parallel 1 --parallel-workloads 2` and 2 workloads (requires 2 × gpus_per_stack):

```
baseline phase:
  t=0   PipelineRun: sim2real-baseline-wl-overload-T0  → own stack A
  t=0   PipelineRun: sim2real-baseline-wl-bursty-T1   → own stack B
  t=N   Both complete, both stacks torn down
  t=N   Extract baseline results from baseline/sim2real-baseline-T/
```

With `--parallel 2 --parallel-workloads 2` (fast mode, 4 concurrent stacks,
requires 4 × gpus_per_stack):

```
  baseline/wl-overload   baseline/wl-bursty
  treatment/wl-overload  treatment/wl-bursty
```
