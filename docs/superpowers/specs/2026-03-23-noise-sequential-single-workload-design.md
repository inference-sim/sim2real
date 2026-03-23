# Design: Sequential Noise Runs + Single Workload

**Date:** 2026-03-23
**Status:** Approved

## Problem

Two related issues with the current Stage 5 noise characterization pipeline:

1. **Two workloads running on shared infrastructure** — glia-40qps and glia-prefix-heavy
   run on the same 4 model pods. glia-40qps can warm the KV cache before
   glia-prefix-heavy runs, producing contaminated measurements.

2. **5 noise runs are parallel on shared infrastructure** — all 5 runs of glia-prefix-heavy
   fire simultaneously on the same pods. Later requests within a run may hit cache warmed
   by concurrent runs, so the 5 samples are not independent. This inflates or deflates
   noise_cv depending on cache hit rates, producing an unreliable T_eff threshold.

## Goals

- Each noise run starts with a cold, freshly deployed KV cache.
- Noise samples are statistically independent (no shared infra between runs).
- Baseline and treatment phases are unaffected (they already get fresh infra per phase
  and will run only one workload after Change 1).

## Non-Goals

- Changing noise_runs count (stays 5).
- Modifying baseline or treatment pipeline templates.
- Changing Suite A / B / C (no cluster involvement).

## Changes

### Change 1: Remove glia-40qps workload

**File:** `blis_router/workloads/workload_glia_40qps.yaml` — **delete**.

Stage 3 Step 8 reads `blis_router/workloads/workload_*.yaml` to populate
`observe.workloads` in `workspace/tekton/values.yaml`. After deletion, rerunning
Step 8 produces a values.yaml with only glia-prefix-heavy. No pipeline template
or validate.md changes required for this change.

**Effect on phases:**
- Noise: 5 runs × 1 workload = 5 tasks (was 5 × 2 = 10)
- Baseline: 1 run × 1 workload = 1 task (was 1 × 2 = 2)
- Treatment: 1 run × 1 workload = 1 task (was 1 × 2 = 2)

### Change 2: Sequential noise runs with fresh infra per run

#### 2a. `tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2`

Replace the `loopName: noise-runs / foreach` structure with the same
`{% for workload in observe.workloads %}` pattern used by baseline and treatment.
The pipeline runs exactly one workload execution per submission.

Add `runIndex` as a Pipeline-level param:

```yaml
params:
  - name: experimentId
    type: string
  - name: namespace
    type: string
  - name: runIndex
    type: string
```

Change `resultsDir` from compile-time `run-{{ run_index }}` to runtime
`run-$(params.runIndex)`:

```yaml
- name: resultsDir
  value: "noise/{{ workload.name }}/run-$(params.runIndex)"
```

Infrastructure deployed: gateway, model, GAIE (using `stack.gaie.baseline.helmValues`),
httproute — same as current noise pipeline.

The `collect-results` task `runAfter` block must be updated to reference the single
per-workload task name, matching the baseline/treatment pattern:

```yaml
- name: collect-results
  runAfter:
    {% for workload in observe.workloads %}
    - run-workload-{{ workload.name | dns }}
    {% endfor %}
```

The old `runAfter` referenced loop-derived names (`run-workload-...-run-N`) that no
longer exist after removing the foreach — leaving the old block would cause Tekton to
reject every PipelineRun submission with a reference error. The `finally` teardown
block is unchanged.

#### 2b. `workspace/tekton/pipelinerun-noise.yaml` (stub)

Add `runIndex` param alongside existing `experimentId` and `namespace`:

```yaml
params:
  - name: experimentId
    value: $PIPELINERUN_NAME
  - name: namespace
    value: $NAMESPACE
  - name: runIndex
    value: $RUN_INDEX
```

`render-pipelinerun` in validate.md substitutes `$RUN_INDEX` at submission time
via `--vars ... RUN_INDEX=$i`.

#### 2c. `prompts/validate.md` — Step 5b, noise phase only

Replace the single submit→wait block for the noise phase with a sequential loop.

The existing `for phase in noise baseline treatment` loop in Step 5b becomes
`for phase in baseline treatment` — noise is removed from the generic loop and
handled by the new dedicated loop below. Baseline and treatment phases are unchanged.

**Prerequisite check before the loop:**

Before entering the loop, verify that Stage 3 Step 8 has been rerun after deleting
`workload_glia_40qps.yaml`. If `values.yaml` still references glia-40qps the compiled
pipeline will include two workloads (contaminating results and doubling run time):

```bash
grep -q 'glia-40qps' workspace/tekton/values.yaml \
  && { echo "HALT: values.yaml still contains glia-40qps — re-run Stage 3 Step 8 first"; exit 1; }
```

**Compile and apply the pipeline once before the loop** (the compiled artifact is
identical for all iterations since `runIndex` is a runtime param; the Pipeline object
must exist in the cluster before any PipelineRun that references it is submitted):

```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase noise \
  --out workspace/tekton/compiled/
kubectl apply -f workspace/tekton/compiled/noise-pipeline.yaml \
  || { echo "HALT: kubectl apply pipeline failed for noise"; exit 1; }
```

**New noise submission loop:**

```bash
NOISE_RUNS=$(.venv/bin/python -c \
  "import yaml; v=yaml.safe_load(open('workspace/tekton/values.yaml')); \
   print(v['observe']['noise_runs'])")

for i in $(seq 0 $((NOISE_RUNS - 1))); do
  PIPELINERUN_NAME=sim2real-noise-run${i}-$(date +%s)

  # Pre-flight each run — validates live cluster state between deploy/teardown cycles.
  # Note: the prior run's finally-teardown may still be in progress when preflight
  # runs. If preflight false-fails due to resources in Terminating state, wait briefly
  # and retry once before halting.
  .venv/bin/python tools/transfer_cli.py preflight \
    --phase noise --values workspace/tekton/values.yaml --namespace $NAMESPACE
  # HALT if exit 1

  # Render and submit
  .venv/bin/python tools/transfer_cli.py render-pipelinerun \
    --template workspace/tekton/pipelinerun-noise.yaml \
    --vars PIPELINERUN_NAME=$PIPELINERUN_NAME NAMESPACE=$NAMESPACE \
           PHASE=noise RUN_INDEX=$i \
    --out /tmp/pipelinerun-noise-run${i}.yaml
  kubectl apply -f /tmp/pipelinerun-noise-run${i}.yaml

  # Record the current run's PipelineRun name in state. Each iteration overwrites
  # the previous name — this is intentional. Only the most recent name is persisted;
  # failed-run diagnosis uses the $PIPELINERUN_NAME shell variable still in scope
  # at halt time (via `tkn pr describe $PIPELINERUN_NAME`).
  .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
    --set-phase noise --status running --pipelinerun $PIPELINERUN_NAME

  # Wait (4h timeout per run)
  # ... same polling loop as current ...
  # HALT on failure

done  # end noise run loop
```

**After the loop — single extractor run:**

After all `noise_runs` PipelineRuns complete, run the extractor pod **once**.
At this point the PVC contains `noise/glia-prefix-heavy/run-0` through
`noise/glia-prefix-heavy/run-4`. The `kubectl cp` copies the entire `noise/`
subtree in one operation, collecting all runs.

Use the same `trap`-based cleanup pattern as the existing per-phase extractors
to ensure the pod is deleted on exit or error (handles REENTER re-entry where a
prior `sim2real-extract-noise` pod may not have been cleaned up):

```bash
trap "kubectl delete pod sim2real-extract-noise -n $NAMESPACE --ignore-not-found 2>/dev/null" EXIT ERR
kubectl delete pod sim2real-extract-noise -n $NAMESPACE --ignore-not-found 2>/dev/null || true
kubectl run sim2real-extract-noise --image=alpine:3.19 --restart=Never \
  --overrides='{"spec":{"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-pvc"}}],"containers":[{"name":"e","image":"alpine:3.19","command":["sleep","600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}]}}' \
  -n $NAMESPACE
kubectl wait pod/sim2real-extract-noise --for=condition=Ready --timeout=60s -n $NAMESPACE \
  || { echo "HALT: extractor pod not ready"; exit 1; }
kubectl cp $NAMESPACE/sim2real-extract-noise:/data/noise/ workspace/noise_raw/ --retries=3 \
  || { echo "HALT: kubectl cp failed"; exit 1; }
.venv/bin/python tools/transfer_cli.py convert-trace \
  --input-dir workspace/noise_raw/ --output workspace/noise_results.json \
  || { echo "HALT: convert-trace failed"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/noise_results.json \
  || { echo "HALT: schema validation failed for noise_results.json"; exit 1; }
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase noise --status done --results workspace/noise_results.json
```

#### 2d. `workspace/tekton/values.yaml`

`observe.noise_runs: 5` is unchanged. Its meaning shifts from "parallel tasks
per pipeline submission" to "sequential pipeline submissions." The field name
and value remain valid; validate.md reads it to control the loop count.

## Data Flow

```
values.yaml (noise_runs: 5)
        │
        ▼
validate.md loop (i = 0..4)
  ├─ PipelineRun noise run-0 → deploy → run → teardown → PVC: noise/glia-prefix-heavy/run-0
  ├─ PipelineRun noise run-1 → deploy → run → teardown → PVC: noise/glia-prefix-heavy/run-1
  ├─ PipelineRun noise run-2 → deploy → run → teardown → PVC: noise/glia-prefix-heavy/run-2
  ├─ PipelineRun noise run-3 → deploy → run → teardown → PVC: noise/glia-prefix-heavy/run-3
  └─ PipelineRun noise run-4 → deploy → run → teardown → PVC: noise/glia-prefix-heavy/run-4
        │
        ▼ (single extractor pod after loop)
workspace/noise_results.json
        │
        ▼
transfer_cli.py benchmark → noise_cv → T_eff
```

## Files Changed

| File | Change |
|------|--------|
| `blis_router/workloads/workload_glia_40qps.yaml` | Delete |
| `tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2` | Add `runIndex` param; replace foreach loop with per-workload loop; update `resultsDir` |
| `workspace/tekton/pipelinerun-noise.yaml` | Add `runIndex` param |
| `prompts/validate.md` | Replace single noise submission with sequential loop; extractor runs once after loop |
| `workspace/tekton/values.yaml` | Regenerate via Stage 3 Step 8 (auto-removes glia-40qps from workloads) |

## Future Extension: Multiple Workloads

When multiple workloads are reintroduced, cross-workload KV cache contamination
becomes the same problem as cross-run contamination: workload A running before
workload B on shared infrastructure pollutes the cache for B.

The consistent solution is **one PipelineRun per (workload, run_index)** across all
three phases. This requires finishing the shift of workload identity from compile-time
(Jinja2) to runtime (Tekton params) — the work started by adding `runIndex` in this
spec.

**Pipeline template changes (all three phases):**

Add `workloadName` and `workloadSpec` as Pipeline-level params and remove the
`{% for workload in observe.workloads %}` Jinja2 loop. The pipeline unconditionally
runs one workload:

```yaml
params:
  - name: workloadName   # used in resultsDir path
  - name: workloadSpec   # passed to run-workload-blis-observe task
  - name: runIndex       # noise only; omit or default to "0" for baseline/treatment
```

`resultsDir` becomes `{phase}/{$(params.workloadName)}/run-$(params.runIndex)`.

With the Jinja2 loop gone, all three pipeline templates become structurally identical
except for their GAIE config (`stack.gaie.baseline` vs `stack.gaie.treatment`) and
results path prefix. `compile-pipeline` still renders each template once before its
submission loop — the compiled artifact is reused for all (workload, run) submissions
of that phase.

**validate.md loop structure for noise:**

```bash
for workload in workloads:          # outer: each workload gets fresh infra
  for i in 0..noise_runs-1:        # inner: each repeat gets fresh infra
    submit PipelineRun(workloadName=$workload.name,
                       workloadSpec=$workload.spec,
                       runIndex=$i)
    wait
    # results accumulate: noise/{workloadName}/run-{i}
# single extractor pod after all loops
```

**validate.md loop structure for baseline and treatment:**

```bash
for workload in workloads:
  submit PipelineRun(workloadName=$workload.name, workloadSpec=$workload.spec)
  wait
  # results: {phase}/{workloadName}/run-0
# single extractor pod after loop
```

**No structural surprises:** with one workload the outer loop is a single iteration
and behaviour is identical to this spec. The multi-workload change is additive: add
two params to the pipeline templates, remove the Jinja2 loop, add the workload loop
to validate.md submission blocks.

## Not Changed

- `baseline-pipeline.yaml.j2`
- `treatment-pipeline.yaml.j2`
- `pipelinerun-baseline.yaml`, `pipelinerun-treatment.yaml`
- `workspace/tekton/values.yaml` — `noise_runs: 5` value
- Suite A / B / C test harness
- Stage 3 `generate.md`
- `transfer_cli.py` (no new subcommands needed)
