# Design: Per-Workload Isolated llm-d Stacks

**Date:** 2026-04-02
**Status:** Approved

## Problem

The current Tekton pipeline deploys one shared llm-d stack (gateway + GAIE + model) per phase
and runs all workloads sequentially against it. This means workloads within a phase share the
same infrastructure — residual state from one workload (in-flight requests, KV cache, scheduler
state) can contaminate the next.

The goal is to give each workload its own independent, clean llm-d stack: full deploy → run →
teardown per workload, with no shared infrastructure between workloads. Applies to all three
benchmark phases: noise, baseline, and treatment.

## Approach: Lift Workload to Tekton Pipeline Parameter (Approach A)

`pipeline.yaml.j2` becomes a single-workload template. `workloadName` and `workloadSpec` are
promoted from Jinja compile-time variables to Tekton pipeline-level runtime parameters.
`compile-pipeline` is called once per phase (unchanged) and produces one pipeline definition.
`deploy.py` submits N PipelineRuns per phase — one per workload — each with its own
independent stack.

## Pipeline Template Changes (`pipeline.yaml.j2`)

### New Tekton pipeline params

```yaml
params:
  - name: experimentId    # unique per PipelineRun — k8s resource naming
  - name: namespace
  - name: sleepDuration
  - name: runName         # NEW: shared across workloads in a phase — PVC results path
  - name: workloadName    # NEW: name of this workload
  - name: workloadSpec    # NEW: JSON string of the workload spec
```

### Two-param separation

**`experimentId`** is unique per PipelineRun (e.g. `sim2real-baseline-wl-overload-1743600001`).
Used for all Kubernetes resource names: gateway, GAIE, model label, HTTPRoute, delete tasks.
Ensures no k8s resource conflicts between concurrent workload stacks.

**`runName`** is shared across all workload PipelineRuns within a phase invocation
(e.g. `sim2real-baseline-1743600000`). Used exclusively for the PVC results path. Keeps the
results directory layout identical to today.

### Template changes

Remove the `{% for wl in observe.workloads %}` Jinja loop. Replace with a single
`run-workload-blis-observe` task:

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
      value: "$(params.workloadSpec)"
    - name: blisImage
      value: "{{ observe.image }}"
    - name: resultsDir
      value: "{{ phase }}/$(params.runName)/$(params.workloadName)"
```

`collect-results` runs after this single task. The `finally` teardown block is unchanged —
it already uses `$(params.experimentId)` so each PipelineRun tears down its own stack.

### What stays Jinja (compile-time)

`gaie_config`, `stack.*`, `observe.image`, `inference_objectives`, `phase` — all phase-level,
resolved at compile time. The workload list in `values.yaml` is no longer consumed by the
template (used by `deploy.py` at submission time).

### `compile-pipeline` interface

Unchanged — still called once per phase, still produces `{phase}-pipeline.yaml`. The workload
list in `values.yaml` is ignored by the template but remains in the file for `deploy.py` to
read.

## `deploy.py` Changes

### New CLI argument

```
--parallel-workloads N   Max workload stacks to run concurrently within a phase (default: 1)
```

### GPU warning

Updated to account for both parallelism dimensions:

```
parallel={P}, parallel_workloads={W} → {P × W × gpus_per_stack} GPUs total
({gpus_per_stack} per stack × {P} concurrent phases × {W} concurrent workloads per phase)
```

Where `gpus_per_stack = replicas × gpu_per_pod`. Shown whenever either `parallel > 1` or
`parallel_workloads > 1`.

### Phase execution model (baseline and treatment)

For each phase invocation:

1. Read workload list from `values.yaml`
2. Generate `runName` once: `sim2real-{phase}-{timestamp}` (shared across workloads)
3. For each workload, generate unique `experimentId`: `sim2real-{phase}-{wl_slug}-{timestamp+i}`
4. Submit up to `--parallel-workloads N` PipelineRuns concurrently via `ThreadPoolExecutor`
5. Each thread: preflight → submit PipelineRun (passing `runName`, `experimentId`,
   `workloadName`, `workloadSpec`) → poll until complete
6. All workload PipelineRuns must complete before the phase is marked done
7. Extract results once — reads `{phase}/{runName}/` as today

### Noise phase

Passes remain sequential. Within each pass, workloads run with `--parallel-workloads`
concurrency. Each pass generates its own per-pass run name
(e.g. `sim2real-noise-run0-1743600000`) used as `runName` for all workload PipelineRuns in
that pass. This preserves the existing noise results directory structure and keeps
`_reorganize_noise_results` and `_extract_phase_results` unchanged.

Pass-i submits M PipelineRuns (throttled to `--parallel-workloads` concurrent), waits for all
M to complete, then pass-(i+1) begins.

### Skip/resume

`benchmark_state.json` tracking is extended to per-workload granularity. If a workload's
results already exist under `{runName}/{wl_name}/` on the PVC, it is skipped individually.
`--force-rerun` clears all workload state for the phase.

## Result Extraction

### Baseline and treatment

PVC layout is **identical to today**: `{phase}/{runName}/{wl_name}/`. `_extract_phase_results`
is called once per phase with `experiment_ids=[runName]` — unchanged.

### Noise

`_extract_phase_results` is called with `experiment_ids=[passRunName_0, passRunName_1, ...]`
(N per-pass run names) — same as today's list of per-pass experimentIds. `_reorganize_noise_results`
reads `noise/{passRunName}/{wl_name}/` for each pass — unchanged.

### Change surface summary

| Component | Changes? |
|---|---|
| `pipeline.yaml.j2` | Yes — single workload, `runName` + `experimentId` params |
| `compile-pipeline` | No |
| `_run_pipeline_phase` | Yes — accepts workload name/spec/runName, passes all three params |
| `_extract_phase_results` | No |
| `_reorganize_noise_results` | No |
| `benchmark_state.json` tracking | Yes — per-workload granularity |
| `_gpu_warning` | Yes — accounts for both `--parallel` and `--parallel-workloads` |
| `stage_benchmarks` | Yes — loops over workloads, manages ThreadPoolExecutor per phase |

## PVC Layout Example

```
data-pvc/
  baseline/
    sim2real-baseline-1743600000/       ← runName (shared)
      overload_mixed_slo/               ← workload A results
      bursty_adversary/                 ← workload B results
  treatment/
    sim2real-treatment-1743600100/
      overload_mixed_slo/
      bursty_adversary/
  noise/
    sim2real-noise-run0-1743600200/     ← pass 0 runName
      overload_mixed_slo/
      bursty_adversary/
    sim2real-noise-run1-1743600400/     ← pass 1 runName
      overload_mixed_slo/
      bursty_adversary/
```

## Concurrent Stack Example

With `--parallel 1 --parallel-workloads 2` and 2 workloads:

```
baseline phase:
  t=0   PipelineRun A (wl=overload_mixed_slo)   → deploys stack A
  t=0   PipelineRun B (wl=bursty_adversary)     → deploys stack B
  t=N   Both complete, stacks torn down
  t=N   extract baseline results
```

With `--parallel 2 --parallel-workloads 2` and 2 workloads (fast mode, 4 stacks total):

```
  t=0   baseline/wl-overload   baseline/wl-bursty
  t=0   treatment/wl-overload  treatment/wl-bursty
  (requires 4 × gpus_per_stack GPUs)
```
