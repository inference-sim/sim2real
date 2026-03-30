---
name: sim2real-results
description: |
  Extract, analyze, and display results from sim2real transfer pipeline runs.
  Shows verdict, latency comparisons, mechanism check, and per-workload
  classification. Works with current run or historical runs.
argument-hint: "[--run NAME] [--extract] [--list]"
user-invocable: true
allowed-tools:
  - Bash(kubectl *)
  - Bash(python3 *)
  - Bash(cd * && *)
  - Bash(cat *)
  - Bash(test *)
  - Bash(ls *)
  - Glob
  - Read
  - Write
  - Grep
---

# sim2real-results

Analyze and display results from sim2real transfer pipeline runs.

## Arguments

- `--run NAME` — Analyze a specific run (default: current run from setup_config.json)
- `--extract` — Pull results from cluster before analyzing
- `--list` — List all saved runs with their status and verdict

Parse arguments from the user's invocation. Examples:
- `/sim2real-results` → analyze current run
- `/sim2real-results --list` → list runs
- `/sim2real-results --run evolved-router-2026-03-25` → analyze historical run
- `/sim2real-results --extract` → pull from cluster, then analyze

## List Mode (`--list`)

```bash
ls -1d workspace/runs/*/
```

For each run directory, read `run_metadata.json` and print a summary table:

```
━━━ sim2real Runs ━━━

Run Name                        Created     Setup  Prepare  Deploy  Results  Verdict
evolved-router-2026-03-27       2026-03-27  done   done     done    done     PASS
load-balancer-v2-2026-03-25     2026-03-25  done   done     failed  -        -
quick-test-2026-03-20           2026-03-20  done   done     done    pending  -
```

Exit after printing.

## Analysis Mode (default)

### Step 1: Resolve Run Directory

```bash
if [ -f workspace/setup_config.json ]; then
  RUN_NAME=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json'))['run_name'])")
fi
```

If `--run <name>` was provided, override `RUN_NAME` with that value.

```bash
RUN_DIR="workspace/runs/${RUN_NAME}"
test -d "${RUN_DIR}" || { echo "HALT: run directory not found: ${RUN_DIR}"; exit 1; }
```

Read `run_metadata.json` for context:
```bash
cat ${RUN_DIR}/run_metadata.json
```

### Step 2: Extract from Cluster (if `--extract`)

Only if `--extract` was passed. Read NAMESPACE from metadata:

```bash
NAMESPACE=$(python3 -c "import json; print(json.load(open('${RUN_DIR}/run_metadata.json'))['namespace'])")
```

For each phase in `noise`, `baseline`, `treatment`:

1. Skip if `$RUN_DIR/deploy_${phase}_results.json` already exists
2. Create extractor pod:
   ```bash
   kubectl run sim2real-extract-${phase} --image=alpine:3.19 --restart=Never \
     -n ${NAMESPACE} \
     --overrides='{
       "spec":{
         "volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"data-pvc"}}],
         "containers":[{"name":"e","image":"alpine:3.19","command":["sleep","600"],
           "volumeMounts":[{"name":"data","mountPath":"/data"}]}]
       }
     }'
   kubectl wait pod/sim2real-extract-${phase} --for=condition=Ready -n ${NAMESPACE} --timeout=60s
   ```
3. Copy data:
   ```bash
   kubectl cp ${NAMESPACE}/sim2real-extract-${phase}:/data/${phase}/ ${RUN_DIR}/${phase}_log/ --retries=3
   ```
4. Convert TraceV2:
   ```bash
   .venv/bin/python tools/transfer_cli.py convert-trace \
     --input-dir ${RUN_DIR}/${phase}_log/ \
     --output ${RUN_DIR}/deploy_${phase}_results.json
   ```
5. Clean up:
   ```bash
   kubectl delete pod sim2real-extract-${phase} -n ${NAMESPACE} --force --grace-period=0
   ```

### Step 3: Check Available Data

Determine which analysis to run based on available files:

```bash
HAS_NOISE=false
HAS_BASELINE=false
HAS_TREATMENT=false
HAS_VALIDATION=false

test -f ${RUN_DIR}/deploy_noise_results.json && HAS_NOISE=true
test -f ${RUN_DIR}/deploy_baseline_results.json && HAS_BASELINE=true
test -f ${RUN_DIR}/deploy_treatment_results.json && HAS_TREATMENT=true
test -f ${RUN_DIR}/deploy_validation_results.json && HAS_VALIDATION=true
```

If neither baseline nor treatment exists, **HALT**: "No results found. Run /sim2real-deploy first or use --extract."

### Step 4: Run Analysis

**Comparison table** (if baseline + treatment):
```bash
.venv/bin/python tools/transfer_cli.py compare \
  --baseline ${RUN_DIR}/deploy_baseline_results.json \
  --treatment ${RUN_DIR}/deploy_treatment_results.json \
  --out ${RUN_DIR}/deploy_comparison_table.txt
```

**Mechanism check** (if noise + baseline + treatment):
```bash
.venv/bin/python tools/transfer_cli.py benchmark \
  --noise ${RUN_DIR}/deploy_noise_results.json \
  --baseline ${RUN_DIR}/deploy_baseline_results.json \
  --treatment ${RUN_DIR}/deploy_treatment_results.json \
  --signal-coverage ${RUN_DIR}/prepare_signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out ${RUN_DIR}/deploy_benchmark_output.json
```

**Evidence document:**

The `generate-evidence` CLI expects files with their original names (no stage
prefixes). Create symlinks before calling it:
```bash
# Create symlinks with expected names for generate-evidence
for f in prepare_algorithm_summary.json prepare_signal_coverage.json \
         deploy_baseline_results.json deploy_treatment_results.json \
         deploy_noise_results.json deploy_benchmark_output.json \
         deploy_validation_results.json deploy_equivalence_results.json; do
  src="${RUN_DIR}/${f}"
  # Strip the stage prefix (prepare_ or deploy_) to get the expected name
  target="${RUN_DIR}/$(echo ${f} | sed 's/^prepare_//;s/^deploy_//')"
  [ -f "${src}" ] && [ ! -e "${target}" ] && ln -s "$(basename ${src})" "${target}"
done
```

Then run generate-evidence. **Note:** If `validation_results.json` does not contain
a `benchmark` key (e.g., in fast-iteration mode where noise/mechanism check was
skipped), skip this step — generate-evidence requires full benchmark data.
```bash
# Only run if validation_results has benchmark data
if python3 -c "import json; d=json.load(open('${RUN_DIR}/validation_results.json')); assert 'benchmark' in d" 2>/dev/null; then
  .venv/bin/python tools/transfer_cli.py generate-evidence \
    --workspace ${RUN_DIR}/ \
    --out ${RUN_DIR}/results_transfer_evidence.md
else
  echo "Skipping generate-evidence: no benchmark data in validation_results.json (fast-iteration mode?)"
fi
```

### Step 5: Print Terminal Summary

Read the analysis outputs and print a formatted summary:

```
━━━ sim2real Results: <RUN_NAME> ━━━

Verdict: <PASS|FAIL|INCONCLUSIVE>    Noise CV: <value>    T_eff: <value>

┌─────────────┬──────────┬──────────┬───────────┬─────────┐
│ Workload    │ Metric   │ Baseline │ Treatment │ Delta   │
├─────────────┼──────────┼──────────┼───────────┼─────────┤
│ <workload>  │ TTFT p50 │  XX.X ms │   XX.X ms │ -XX.X%  │
│             │ TTFT p99 │  XX.X ms │   XX.X ms │ -XX.X%  │
│             │ TPOT p50 │  XX.X ms │   XX.X ms │ -XX.X%  │
│             │ TPOT p99 │  XX.X ms │   XX.X ms │ -XX.X%  │
└─────────────┴──────────┴──────────┴───────────┴─────────┘

Classification:
  <workload> = matched|unmatched  (improvement: XX.X%)

Mechanism check: max improvement XX.X% ≥|< T_eff XX.X% → PASS|FAIL

Reports saved:
  ${RUN_DIR}/deploy_comparison_table.txt
  ${RUN_DIR}/deploy_benchmark_output.json
  ${RUN_DIR}/results_analysis_report.md
```

To build this table, read:
- `deploy_baseline_results.json` and `deploy_treatment_results.json` for per-workload metrics
- `deploy_benchmark_output.json` for verdict, t_eff, noise_cv, workload_classification
- Or `deploy_validation_results.json` if it exists (has all of the above merged)

### Step 6: Save Analysis Report

Write `${RUN_DIR}/results_analysis_report.md` — a markdown version of the
terminal summary with full detail:
- Run metadata (name, namespace, date, pipeline commit)
- Verdict and mechanism check details
- Full metrics table for all workloads and all metrics (p50, p99, mean)
- Per-workload classification with matched signals
- Links to source artifacts (relative paths)

### Step 7: Update Run Metadata

```bash
python3 -c "
import json
m = json.load(open('${RUN_DIR}/run_metadata.json'))
m['stages']['results']['status'] = 'completed'
m['stages']['results']['completed_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
m['stages']['results']['summary'] = 'Analysis complete, report generated'
m['stages']['results']['artifacts'] = [
    'results_analysis_report.md',
    'results_transfer_evidence.md'
]
json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
"
```
