---
name: sim2real-deploy
description: |
  Phase 2 of the sim2real transfer pipeline. Builds, tests, runs equivalence
  gate, builds EPP image (in-cluster via BuildKit), runs cluster benchmarks,
  and creates PRs. Use after /sim2real-prepare completes successfully.
user-invocable: true
allowed-tools:
  - Bash(**/build-epp.sh *)
  - Bash(bash *)
  - Bash(kubectl *)
  - Bash(oc *)
  - Bash(tkn *)
  - Bash(python3 *)
  - Bash(cd * && *)
  - Bash(cat *)
  - Bash(test *)
  - Bash(git *)
  - Bash(gh *)
  - Glob
  - Read
  - Edit
  - Write
  - Grep
---

# sim2real-deploy

Phase 2 of the sim2real transfer pipeline: Build EPP, Cluster Benchmarks, PR.

## CRITICAL: Working Directory

**All commands in this skill must run from the `sim2real/` root directory.**
The `llm-d-inference-scheduler/` submodule is a reference repo — if you need
to run commands there, always use a subshell: `(cd llm-d-inference-scheduler && ...)`
Never `cd` into it directly. If you find yourself in a subdirectory, run
`cd` back to the sim2real root before continuing.

## Prerequisites

### Load Setup Config

```bash
if [ -f workspace/setup_config.json ]; then
  NAMESPACE=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json'))['namespace'])")
  REGISTRY=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json')).get('registry',''))")
  RUN_NAME=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json'))['run_name'])")
  export NAMESPACE
  RUN_DIR="workspace/runs/${RUN_NAME}"
  echo "Loaded config: NAMESPACE=${NAMESPACE}, RUN_DIR=${RUN_DIR}"
else
  echo "HALT: workspace/setup_config.json not found — run /sim2real-setup first"; exit 1
fi

# Update metadata
python3 -c "
import json
m = json.load(open('${RUN_DIR}/run_metadata.json'))
m['stages']['deploy']['status'] = 'in_progress'
m['stages']['deploy']['started_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
"
```

Before starting, verify all Phase 1 artifacts. **HALT if any check fails.**

```bash
# Phase 1 artifacts
for f in ${RUN_DIR}/prepare_algorithm_summary.json ${RUN_DIR}/prepare_signal_coverage.json \
         ${RUN_DIR}/prepare_stage3_output.json ${RUN_DIR}/prepare_tekton/values.yaml \
         ${RUN_DIR}/prepare_translation_reviews.json \
         ${RUN_DIR}/prepare_equivalence_results.json; do
  test -f "$f" || { echo "HALT: missing $f — run /sim2real-prepare first"; exit 1; }
done

# Schema validation
for f in ${RUN_DIR}/prepare_algorithm_summary.json ${RUN_DIR}/prepare_signal_coverage.json \
         ${RUN_DIR}/prepare_stage3_output.json; do
  .venv/bin/python tools/transfer_cli.py validate-schema "$f" || { echo "HALT: $f schema invalid"; exit 1; }
done

# AI review must have passed
python3 -c "
import json, sys
d = json.load(open('${RUN_DIR}/prepare_translation_reviews.json'))
if d.get('final_verdict') != 'consistent':
    print('HALT: AI review verdict is not consistent'); sys.exit(1)
" || exit 1

# Equivalence gate must have passed (Suite A + C)
python3 -c "
import json, sys
d = json.load(open('${RUN_DIR}/prepare_equivalence_results.json'))
if not d.get('suite_a', {}).get('passed'):
    print('HALT: Suite A did not pass'); sys.exit(1)
if not d.get('suite_c', {}).get('passed'):
    print('HALT: Suite C did not pass'); sys.exit(1)
" || exit 1

# Scorer file exists and builds
SCORER_FILE=$(python3 -c "import json; print(json.load(open('${RUN_DIR}/prepare_stage3_output.json'))['scorer_file'])")
test -f "$SCORER_FILE" || { echo "HALT: scorer file missing"; exit 1; }
(cd llm-d-inference-scheduler && GOWORK=off go build ./...) || { echo "HALT: scorer build failed"; exit 1; }

# Registry configuration
python3 -c "
import yaml, sys
d = yaml.safe_load(open('config/env_defaults.yaml'))
hub = d.get('stack',{}).get('gaie',{}).get('epp_image',{}).get('build',{}).get('hub','')
if not hub or 'REPLACE_ME' in hub:
    print('HALT: set epp_image.build.hub in config/env_defaults.yaml'); sys.exit(1)
" || exit 1

# Pipeline commit drift check
if [ -f ${RUN_DIR}/prepare_pipeline_commit.txt ]; then
  EXPECTED=$(cat ${RUN_DIR}/prepare_pipeline_commit.txt)
  ACTUAL=$(git rev-parse HEAD)
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "WARNING: repo HEAD has drifted since /sim2real-prepare"
  fi
fi
```

## Stage 1: Build EPP Image (In-Cluster via BuildKit)

Build the EPP image on the Kubernetes cluster using a single script.
The `registry-secret` created during `/sim2real-setup` provides push credentials.

Locate the build script:
```
Glob: **/skills/sim2real-deploy/scripts/build-epp.sh
```
Store the result as `[BUILD_SCRIPT]`.

Run the build as a single command:
```bash
bash [BUILD_SCRIPT] --run-dir ${RUN_DIR} --run-name ${RUN_NAME} --namespace ${NAMESPACE}
```

The script handles: copying source to cluster, submitting a BuildKit pod,
waiting for completion, and updating run metadata. The image tag uses the
run name (e.g., `quay.io/jchen/llm-d-inference-scheduler:evolved-router-2026-03-27`).

If the script fails, it prints logs and exits non-zero. **HALT** on failure.

4. Inject image reference into `${RUN_DIR}/prepare_tekton/algorithm_values.yaml`:
   Update `stack.gaie.treatment.helmValues.inferenceExtension.image.hub` and `.tag`.

5. Re-merge values:
   ```bash
   .venv/bin/python tools/transfer_cli.py merge-values \
     --env config/env_defaults.yaml \
     --algorithm ${RUN_DIR}/prepare_tekton/algorithm_values.yaml \
     --out ${RUN_DIR}/prepare_tekton/values.yaml
   ```

6. Compile pipeline YAMLs and apply to cluster:
   ```bash
   # Compile for each phase (noise, baseline, treatment)
   for phase in noise baseline treatment; do
     .venv/bin/python tools/transfer_cli.py compile-pipeline \
       --template-dir tektonc-data-collection/tektoncsample/sim2real \
       --values ${RUN_DIR}/prepare_tekton/values.yaml \
       --phase ${phase} \
       --out ${RUN_DIR}/prepare_tekton/pipelines
   done

   # Apply compiled pipelines to cluster
   for f in ${RUN_DIR}/prepare_tekton/pipelines/*.yaml; do
     kubectl apply -f "$f" -n ${NAMESPACE}
   done
   ```

7. Update run metadata:
   ```bash
   python3 -c "
   import json; m = json.load(open('${RUN_DIR}/run_metadata.json'))
   m['stages']['deploy']['last_completed_step'] = 'build_epp'
   json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
   "
   ```

### Artifacts produced
- EPP container image in registry
- Updated `${RUN_DIR}/prepare_tekton/algorithm_values.yaml`
- Updated `${RUN_DIR}/prepare_tekton/values.yaml`

## Stage 4: Cluster Benchmarks

Follow the logic from `prompts/validate.md`.

### Fast-iteration mode

Check `config/env_defaults.yaml` for `pipeline.fast_iteration: true`. If set:
- Skip noise characterization and mechanism check
- Write partial `validation_results.json` from equivalence results
- Run baseline and treatment pipelines only
- Generate comparison table
- Skip to Completion (no PR creation)

### Full validation flow

1. **Noise characterization** — run `observe.noise_runs` sequential pipeline
   submissions with fresh infrastructure per run. Extract results.
2. **Baseline pipeline** — submit and wait (4h timeout). Extract results.
3. **Treatment pipeline** — submit and wait (4h timeout). Extract results.
4. **Mechanism check:**
   ```bash
   .venv/bin/python tools/transfer_cli.py benchmark \
     --noise ${RUN_DIR}/deploy_noise_results.json \
     --baseline ${RUN_DIR}/deploy_baseline_results.json \
     --treatment ${RUN_DIR}/deploy_treatment_results.json \
     --signal-coverage ${RUN_DIR}/prepare_signal_coverage.json \
     --workloads-dir blis_router/workloads/ \
     --out ${RUN_DIR}/deploy_benchmark_output.json
   ```
   - Exit 0 = PASS or INCONCLUSIVE (check `mechanism_check_verdict`)
   - Exit 1 = FAIL — **HALT**
   - Exit 2 = ERROR — **HALT**
5. **Merge results** into `${RUN_DIR}/deploy_validation_results.json`.
6. **Comparison table:**
   ```bash
   .venv/bin/python tools/transfer_cli.py compare \
     --baseline ${RUN_DIR}/deploy_baseline_results.json \
     --treatment ${RUN_DIR}/deploy_treatment_results.json \
     --out ${RUN_DIR}/deploy_comparison_table.txt
   ```
7. **Update run metadata:**
   ```bash
   python3 -c "
   import json; m = json.load(open('${RUN_DIR}/run_metadata.json'))
   m['stages']['deploy']['last_completed_step'] = 'benchmarks'
   json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
   "
   ```

### Decision point: INCONCLUSIVE verdict

If `mechanism_check_verdict` is INCONCLUSIVE, **pause and ask the user:**

> Mechanism check verdict is INCONCLUSIVE. Options:
> 1. Re-run during lower-variance window
> 2. Inspect per-workload improvements and retry targeted workloads
> 3. Accept as soft-pass (requires operator_notes in validation_results.json)

If the user chooses option 3, they must provide `operator_notes` text.
Write it into `${RUN_DIR}/deploy_validation_results.json` and set
`overall_verdict: "INCONCLUSIVE"`.

### Artifacts produced
- `${RUN_DIR}/deploy_noise_results.json` (if not fast_iteration)
- `${RUN_DIR}/deploy_baseline_results.json`
- `${RUN_DIR}/deploy_treatment_results.json`
- `${RUN_DIR}/deploy_benchmark_output.json` (if not fast_iteration)
- `${RUN_DIR}/deploy_validation_results.json`
- `${RUN_DIR}/deploy_comparison_table.txt`
- `${RUN_DIR}/results_transfer_evidence.md`

## Stage 5: PR

Follow the logic from `prompts/pr.md`.

### Fast-iteration mode
If `pipeline.fast_iteration: true`, skip PR creation entirely. Print:
```
Fast-iteration mode: PR creation skipped.
Review comparison_table.txt and re-run with fast_iteration=false when ready.
```

### Full mode

1. Check verdict — **HALT** on FAIL. Verify `operator_notes` present if INCONCLUSIVE.
2. Verify `gh auth status`.
3. Push branch:
   ```bash
   ALG_NAME=$(python3 -c "import json; print(json.load(open('${RUN_DIR}/prepare_algorithm_summary.json'))['algorithm_name'])")
   (cd llm-d-inference-scheduler && \
     git checkout -b "transfer/${ALG_NAME}" && \
     git add -A && \
     git commit -m "feat: add ${ALG_NAME} scorer plugin (sim2real transfer)" && \
     git push -u origin "transfer/${ALG_NAME}")
   ```
4. Append calibration log entry.
5. Create PR via `gh pr create`.

### Artifacts produced
- `docs/transfer/calibration_log.md` (appended)
- PR in `llm-d-inference-scheduler` repo

## Completion

### Update Run Metadata

```bash
python3 -c "
import json
m = json.load(open('${RUN_DIR}/run_metadata.json'))
m['stages']['deploy']['status'] = 'completed'
m['stages']['deploy']['completed_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
m['stages']['deploy']['summary'] = 'Build, test, equivalence, EPP build, benchmarks completed'
m['stages']['deploy']['artifacts'] = [
    'deploy_equivalence_results.json',
    'deploy_validation_results.json',
    'deploy_comparison_table.txt'
]
json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
"
```

Print artifact summary and final status.

```
━━━ /sim2real-deploy complete ━━━

Verdict: <PASS|INCONCLUSIVE>
EPP Image: <full image reference>

Artifacts:
  ${RUN_DIR}/deploy_equivalence_results.json  ✓ Equivalence Gate
  ${RUN_DIR}/deploy_validation_results.json   ✓ Cluster Benchmarks
  ${RUN_DIR}/deploy_comparison_table.txt      ✓ Comparison

PR: <URL or "skipped (fast_iteration)">

Pipeline commit check: <OK or WARNING: drifted>
```
