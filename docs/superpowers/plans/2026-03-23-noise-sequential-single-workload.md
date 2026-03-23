# Noise Sequential Single Workload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace parallel multi-workload noise runs with sequential single-workload runs, each on freshly deployed infrastructure, to eliminate KV cache contamination between noise samples.

**Architecture:** Remove glia-40qps from the workload set; add a `runIndex` Tekton param to the noise pipeline so each of the 5 noise runs is a separate PipelineRun writing to a distinct PVC path; update validate.md to drive a sequential submission loop for noise while leaving baseline/treatment unchanged.

**Tech Stack:** Jinja2 pipeline templates (tektonc), Tekton YAML, bash (validate.md prompt), Python values.yaml regeneration via Stage 3 Step 8.

**Spec:** `docs/superpowers/specs/2026-03-23-noise-sequential-single-workload-design.md`

---

### Task 1: Remove glia-40qps workload and regenerate values.yaml

**Files:**
- Delete: `blis_router/workloads/workload_glia_40qps.yaml`
- Regenerate: `workspace/tekton/values.yaml` (via Stage 3 Step 8)

- [ ] **Step 1: Delete the workload file**

```bash
git rm blis_router/workloads/workload_glia_40qps.yaml
```

- [ ] **Step 2: Verify only glia-prefix-heavy remains**

```bash
ls blis_router/workloads/
```

Expected: only `workload_glia_prefix_heavy.yaml`

- [ ] **Step 3: Rerun Stage 3 Step 8 to regenerate values.yaml**

Follow the procedure in `prompts/generate.md` Step 8. The key outputs are:
- `workspace/tekton/values.yaml` — will now have only glia-prefix-heavy in `observe.workloads`
- `workspace/tekton/pipelinerun-{noise,baseline,treatment}.yaml` — regenerated stubs
- `workspace/stage3_output.json` — updated and schema-validated

Rerunning Step 8 is required (not optional). Do not manually edit `workspace/tekton/values.yaml`
alone — that would leave `workspace/stage3_output.json` out of sync and fail schema validation.

- [ ] **Step 4: Confirm glia-40qps is gone from values.yaml**

```bash
grep -q 'glia-40qps' workspace/tekton/values.yaml \
  && echo "FAIL: glia-40qps still present" \
  || echo "OK: glia-40qps removed"
```

Expected: `OK: glia-40qps removed`

- [ ] **Step 5: Validate values.yaml still passes required-key check**

```bash
.venv/bin/python -c "
import yaml
v = yaml.safe_load(open('workspace/tekton/values.yaml'))
wl = v['observe'].get('workloads', [])
assert len(wl) == 1, f'expected 1 workload, got {len(wl)}'
assert wl[0]['name'] == 'glia-prefix-heavy', f'unexpected workload name: {wl[0][\"name\"]}'
gw = v['stack'].get('gateway', {}).get('helmValues', {}).get('gateway', {})
assert gw.get('provider'), 'missing gateway.provider'
for phase in ('baseline', 'treatment'):
    pcc = v['stack']['gaie'][phase]['helmValues']['inferenceExtension']['pluginsCustomConfig']
    assert pcc, f'missing pluginsCustomConfig for {phase}'
print('OK: values.yaml valid with 1 workload')
"
```

Expected: `OK: values.yaml valid with 1 workload`

- [ ] **Step 6: Commit**

```bash
git add blis_router/workloads/
git add workspace/tekton/values.yaml workspace/tekton/pipelinerun-noise.yaml \
        workspace/tekton/pipelinerun-baseline.yaml workspace/tekton/pipelinerun-treatment.yaml \
        workspace/stage3_output.json
git commit -m "feat: remove glia-40qps workload; single workload (glia-prefix-heavy)"
```

---

### Task 2: Modify noise-pipeline.yaml.j2

**Files:**
- Modify: `tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2`

This is the core template change. Replace the `loopName/foreach` loop with the same
`{% for workload in observe.workloads %}` pattern that baseline and treatment use.
Add `runIndex` as a Pipeline param. Update `collect-results` `runAfter`. The deploy
tasks and `finally` block are untouched.

- [ ] **Step 1: Replace the pipeline params block to add runIndex**

In `noise-pipeline.yaml.j2`, replace:

```yaml
  params:
    - name: experimentId
      type: string
    - name: namespace
      type: string
```

With:

```yaml
  params:
    - name: experimentId
      type: string
    - name: namespace
      type: string
    - name: runIndex
      type: string
```

- [ ] **Step 2: Replace the loopName/foreach block and collect-results runAfter**

Replace the entire block from `    - loopName: noise-runs` through the end of
`collect-results` (lines 81–120 in the current file):

```yaml
    - loopName: noise-runs
      foreach:
        domain:
          run_index: {{ range(observe.noise_runs) | list }}
          workload: {{ observe.workloads }}
      vars:
        taskId: "{{ workload.name | dns }}-run-{{ run_index }}"

      tasks:
        - name: run-workload-{{ taskId }}
          runAfter: ["deploy-model", "deploy-httproute"]
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
              value: "{{ workload.spec }}"
            - name: blisImage
              value: "{{ observe.image }}"
            - name: resultsDir
              value: "noise/{{ workload.name }}/run-{{ run_index }}"

    - name: collect-results
      runAfter:
        {% for run_index in range(observe.noise_runs) %}
        {% for workload in observe.workloads %}
        - run-workload-{{ workload.name | dns }}-run-{{ run_index }}
        {% endfor %}
        {% endfor %}
      taskRef:
        name: collect-results
      workspaces:
        - name: data
          workspace: data-storage
```

With:

```yaml
    {% for workload in observe.workloads %}
    - name: run-workload-{{ workload.name | dns }}
      runAfter: ["deploy-model", "deploy-httproute"]
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
          value: "{{ workload.spec }}"
        - name: blisImage
          value: "{{ observe.image }}"
        - name: resultsDir
          value: "noise/{{ workload.name }}/run-$(params.runIndex)"
    {% endfor %}

    - name: collect-results
      runAfter:
        {% for workload in observe.workloads %}
        - run-workload-{{ workload.name | dns }}
        {% endfor %}
      taskRef:
        name: collect-results
      workspaces:
        - name: data
          workspace: data-storage
```

- [ ] **Step 3: Compile the modified template and verify output**

```bash
.venv/bin/python tektonc-data-collection/tektonc/tektonc.py \
  -t tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2 \
  -f workspace/tekton/values.yaml \
  --explain
```

Expected output shows:
- `runIndex` in the params list
- A single `run-workload-glia-prefix-heavy` task (not 10 tasks)
- `collect-results` with `runAfter: [run-workload-glia-prefix-heavy]`
- No references to `run-0`, `run-1`, etc. in task names

- [ ] **Step 4: Compile to a file and inspect the critical sections**

```bash
.venv/bin/python tektonc-data-collection/tektonc/tektonc.py \
  -t tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2 \
  -f workspace/tekton/values.yaml \
  -o /tmp/noise-pipeline-check.yaml

# Check runIndex param exists
grep -A2 'name: runIndex' /tmp/noise-pipeline-check.yaml

# Check resultsDir uses runtime param syntax
grep 'resultsDir' /tmp/noise-pipeline-check.yaml

# Check collect-results runAfter references the correct task name
grep -A5 'collect-results' /tmp/noise-pipeline-check.yaml | grep runAfter -A3
```

Expected:
- `name: runIndex` appears in the Pipeline params section
- `resultsDir: noise/glia-prefix-heavy/run-$(params.runIndex)` (Tekton runtime param syntax, NOT Jinja2)
- `collect-results` `runAfter` contains `run-workload-glia-prefix-heavy` (not a run-N variant)

- [ ] **Step 5: Commit**

```bash
git add tektonc-data-collection/tektoncsample/sim2real/noise-pipeline.yaml.j2
git commit -m "feat: add runIndex param to noise pipeline; sequential single-workload runs"
```

---

### Task 3: Add runIndex param to pipelinerun-noise.yaml stub

**Files:**
- Modify: `workspace/tekton/pipelinerun-noise.yaml`

- [ ] **Step 1: Add runIndex param to the stub**

In `workspace/tekton/pipelinerun-noise.yaml`, replace the `params:` block:

```yaml
  params:
    - name: experimentId
      value: $PIPELINERUN_NAME
    - name: namespace
      value: $NAMESPACE
```

With:

```yaml
  params:
    - name: experimentId
      value: $PIPELINERUN_NAME
    - name: namespace
      value: $NAMESPACE
    - name: runIndex
      value: $RUN_INDEX
```

- [ ] **Step 2: Verify render-pipelinerun substitutes RUN_INDEX correctly**

```bash
.venv/bin/python tools/transfer_cli.py render-pipelinerun \
  --template workspace/tekton/pipelinerun-noise.yaml \
  --vars PIPELINERUN_NAME=sim2real-noise-run0-test \
         NAMESPACE=test-ns \
         PHASE=noise \
         RUN_INDEX=0 \
  --out /tmp/pipelinerun-noise-test.yaml \
  && cat /tmp/pipelinerun-noise-test.yaml
```

Expected: the rendered YAML contains `value: "0"` (or `value: 0`) under the `runIndex` param. No literal `$RUN_INDEX` remains.

- [ ] **Step 3: Verify substitution for a non-zero index**

```bash
.venv/bin/python tools/transfer_cli.py render-pipelinerun \
  --template workspace/tekton/pipelinerun-noise.yaml \
  --vars PIPELINERUN_NAME=sim2real-noise-run3-test \
         NAMESPACE=test-ns \
         PHASE=noise \
         RUN_INDEX=3 \
  --out /tmp/pipelinerun-noise-test3.yaml \
  && grep -A2 'runIndex' /tmp/pipelinerun-noise-test3.yaml
```

Expected: `value: "3"` (or `value: 3`) under runIndex.

- [ ] **Step 4: Commit**

```bash
git add workspace/tekton/pipelinerun-noise.yaml
git commit -m "feat: add runIndex param to pipelinerun-noise stub"
```

---

### Task 4: Update validate.md Step 5b — sequential noise loop

**Files:**
- Modify: `prompts/validate.md`

This is the orchestration change. The existing `for phase in noise baseline treatment`
loop becomes `for phase in baseline treatment`. Noise gets its own sequential loop
prepended, with compile+apply once before the loop, and a single extractor pod after
the loop.

- [ ] **Step 1: Replace the Step 5b header and noise section**

In `prompts/validate.md`, replace the heading and generic loop opening:

```markdown
### 5b. For each non-done phase in order: noise → baseline → treatment

Execute the procedure below **three times** — for `noise`, then `baseline`, then `treatment` — using this explicit loop (phases with status `done` are skipped automatically):

~~~bash
for phase in noise baseline treatment; do
  STATUS=$(.venv/bin/python -c \
    "import json; print(json.load(open('workspace/benchmark_state.json'))['phases']['$phase']['status'])" \
    2>/dev/null || echo "unknown")
  if [ "$STATUS" = "done" ]; then
    echo "Phase $phase already done — skipping."; continue
  fi
  echo "=== Processing phase: $phase ==="
~~~
```

With:

```markdown
### 5b. Run noise phase (sequential runs with fresh infra), then baseline and treatment

#### Noise phase — sequential runs

Each of the `noise_runs` iterations deploys fresh infrastructure, runs the workload
once, and tears down before the next iteration starts. This ensures each noise sample
starts with a cold KV cache and is statistically independent.

**Prerequisite:** verify Stage 3 Step 8 was rerun after removing glia-40qps:

~~~bash
grep -q 'glia-40qps' workspace/tekton/values.yaml \
  && { echo "HALT: values.yaml still contains glia-40qps — re-run Stage 3 Step 8 first"; exit 1; }
~~~

**Check noise phase status (skip if already done):**

~~~bash
NOISE_STATUS=$(.venv/bin/python -c \
  "import json; print(json.load(open('workspace/benchmark_state.json'))['phases']['noise']['status'])" \
  2>/dev/null || echo "unknown")
if [ "$NOISE_STATUS" = "done" ]; then
  echo "Noise phase already done — skipping noise loop."
else
~~~

**Compile and apply the noise pipeline once (reused for all runs):**

~~~bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase noise \
  --out workspace/tekton/compiled/
kubectl apply -f workspace/tekton/compiled/noise-pipeline.yaml \
  || { echo "HALT: kubectl apply pipeline failed for noise"; exit 1; }
~~~

**Sequential noise run loop:**

~~~bash
NOISE_RUNS=$(.venv/bin/python -c \
  "import yaml; v=yaml.safe_load(open('workspace/tekton/values.yaml')); \
   print(v['observe']['noise_runs'])")

for i in $(seq 0 $((NOISE_RUNS - 1))); do
  PIPELINERUN_NAME=sim2real-noise-run${i}-$(date +%s)
  echo "=== Noise run $i of $((NOISE_RUNS - 1)): $PIPELINERUN_NAME ==="
~~~

**Pre-flight:**
~~~bash
  .venv/bin/python tools/transfer_cli.py preflight \
    --phase noise --values workspace/tekton/values.yaml --namespace $NAMESPACE
~~~
**HALT if exit 1.** Note: if the prior run's `finally` teardown is still completing,
preflight may transiently fail. Wait 30 seconds and retry once before halting.

**Submit:**
~~~bash
  .venv/bin/python tools/transfer_cli.py render-pipelinerun \
    --template workspace/tekton/pipelinerun-noise.yaml \
    --vars PIPELINERUN_NAME=$PIPELINERUN_NAME NAMESPACE=$NAMESPACE \
           PHASE=noise RUN_INDEX=$i \
    --out /tmp/pipelinerun-noise-run${i}.yaml \
    || { echo "HALT: render-pipelinerun failed for noise run $i"; exit 1; }
  kubectl apply -f /tmp/pipelinerun-noise-run${i}.yaml \
    || { echo "HALT: kubectl apply pipelinerun failed for noise run $i"; exit 1; }
  .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
    --set-phase noise --status running --pipelinerun $PIPELINERUN_NAME
~~~

**Wait (4h timeout per run):**
~~~bash
  TIMEOUT_SECS=14400; ELAPSED=0
  while true; do
    REASON=$(tkn pr describe $PIPELINERUN_NAME \
      -o jsonpath='{.status.conditions[0].reason}' 2>/dev/null)
    echo "$REASON" | grep -qE 'Succeeded|Failed|PipelineRunCancelled|CouldntGetTask' && break
    sleep 30; ELAPSED=$((ELAPSED+30))
    if [ $ELAPSED -ge $TIMEOUT_SECS ]; then
      .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
        --set-phase noise --status failed \
        --failure-reason "Polling timeout after ${TIMEOUT_SECS}s on run $i"
      echo "HALT: noise run $i timed out."; exit 1
    fi
  done
~~~

**On Failed:**
~~~bash
  REASON=$(tkn pr describe $PIPELINERUN_NAME \
    -o jsonpath='{.status.conditions[0].reason}' 2>/dev/null)
  if echo "$REASON" | grep -qE 'Failed|PipelineRunCancelled|CouldntGetTask'; then
    FAIL_REASON=$(tkn pr describe $PIPELINERUN_NAME \
      -o jsonpath='{.status.conditions[0].message}' 2>/dev/null || echo "PipelineRun failed")
    .venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
      --set-phase noise --status failed \
      --failure-reason "$FAIL_REASON"
    echo "HALT: noise run $i failed — $FAIL_REASON"; exit 1
  fi

done  # end noise run loop
~~~

**After loop — extract all noise runs via single extractor pod:**
~~~bash
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
  || { echo "HALT: schema validation failed for workspace/noise_results.json — results file is malformed, do not mark phase done"; exit 1; }
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase noise --status done --results workspace/noise_results.json

fi  # end if NOISE_STATUS != done
~~~

#### Baseline and treatment phases

Execute the procedure below for `baseline`, then `treatment` (phases with status
`done` are skipped automatically):

~~~bash
for phase in baseline treatment; do
  STATUS=$(.venv/bin/python -c \
    "import json; print(json.load(open('workspace/benchmark_state.json'))['phases']['$phase']['status'])" \
    2>/dev/null || echo "unknown")
  if [ "$STATUS" = "done" ]; then
    echo "Phase $phase already done — skipping."; continue
  fi
  echo "=== Processing phase: $phase ==="
~~~
```

- [ ] **Step 2: Update the closing comment of the generic loop**

Find the closing line:

```bash
done  # end for phase in noise baseline treatment
```

Replace with:

```bash
done  # end for phase in baseline treatment
```

- [ ] **Step 3: Verify the validate.md diff looks correct**

```bash
git diff prompts/validate.md | head -300
```

Check:
- `for phase in noise baseline treatment` is gone
- `for phase in baseline treatment` appears for the generic loop
- The noise sequential loop section appears before the generic loop
- `done  # end noise run loop` closes the inner noise loop
- `done  # end for phase in baseline treatment` closes the outer generic loop

- [ ] **Step 4: Commit**

```bash
git add prompts/validate.md
git commit -m "feat: sequential noise runs in validate.md; baseline/treatment loop unchanged"
```

---

### Task 5: End-to-end dry-run verification

No cluster needed. Verify the full compile-to-render pipeline produces correct output
for the new noise pipeline.

- [ ] **Step 1: Compile the noise pipeline to a file**

```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase noise \
  --out workspace/tekton/compiled/
```

- [ ] **Step 2: Check the compiled pipeline structure**

```bash
# Should have runIndex param
grep -A2 'name: runIndex' workspace/tekton/compiled/noise-pipeline.yaml

# Should have exactly 1 run-workload task definition (not 10)
# Note: grep -c counts lines; the task name appears twice (definition + runAfter ref) → expect 2
grep -c 'run-workload-' workspace/tekton/compiled/noise-pipeline.yaml

# Confirm exactly 1 task *definition* (the - name: line)
grep -c '^ *- name: run-workload-' workspace/tekton/compiled/noise-pipeline.yaml

# resultsDir must use $(params.runIndex), not a hardcoded run-N
grep 'resultsDir' workspace/tekton/compiled/noise-pipeline.yaml

# collect-results runAfter must not reference run-0..run-4 task names
grep -A10 'collect-results' workspace/tekton/compiled/noise-pipeline.yaml
```

Expected:
- `runIndex` appears in params
- `grep -c 'run-workload-'` returns `2` (task definition + runAfter reference)
- `grep -c '^ *- name: run-workload-'` returns `1` (single task definition)
- `resultsDir: noise/glia-prefix-heavy/run-$(params.runIndex)`
- `collect-results` `runAfter` lists only `run-workload-glia-prefix-heavy`

- [ ] **Step 3: Render pipelinerun stubs for each noise run and verify**

```bash
for i in 0 1 2 3 4; do
  .venv/bin/python tools/transfer_cli.py render-pipelinerun \
    --template workspace/tekton/pipelinerun-noise.yaml \
    --vars PIPELINERUN_NAME=sim2real-noise-run${i}-dryrun \
           NAMESPACE=test-ns PHASE=noise RUN_INDEX=$i \
    --out /tmp/pr-noise-run${i}.yaml
  echo "Run $i:"
  grep -A2 'runIndex' /tmp/pr-noise-run${i}.yaml
done
```

Expected: each rendered file has `value: "$i"` for runIndex (0 through 4 respectively), with no literal `$RUN_INDEX` remaining.

- [ ] **Step 4: Verify baseline and treatment compile unchanged**

```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase baseline \
  --out workspace/tekton/compiled/

.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase treatment \
  --out workspace/tekton/compiled/

# Baseline should use load-aware-scorer, treatment blis-weighted-scorer
grep 'load-aware-scorer' workspace/tekton/compiled/baseline-pipeline.yaml
grep 'blis-weighted-scorer' workspace/tekton/compiled/treatment-pipeline.yaml

# Each should have exactly 1 run-workload task definition
grep -c '^ *- name: run-workload-' workspace/tekton/compiled/baseline-pipeline.yaml
grep -c '^ *- name: run-workload-' workspace/tekton/compiled/treatment-pipeline.yaml
```

Expected: each `grep -c` returns `1`.

- [ ] **Step 5: Validate stage3_output.json schema (sanity check after all changes)**

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json
```

Expected: `"status": "ok"`

- [ ] **Step 6: Final commit if any workspace artifacts were updated**

```bash
git add workspace/tekton/compiled/ workspace/tekton/values.yaml workspace/stage3_output.json
git commit -m "chore: regenerate compiled tekton artifacts for single-workload sequential noise"
```

If nothing changed in the compiled artifacts, skip this step.
