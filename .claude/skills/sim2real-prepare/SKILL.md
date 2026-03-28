---
name: sim2real-prepare
description: |
  Phase 1 of the sim2real transfer pipeline. Extracts algorithm metadata,
  translates signals, generates scorer plugin, and runs multi-model AI review.
  Use after /sim2real-setup. Produces all artifacts needed for /sim2real-deploy.
argument-hint: "[--reviews N]"
user-invocable: true
allowed-tools:
  - Bash(**/review_translation.py *)
  - Bash(**/build_review_request.py *)
  - Bash(python3 *)
  - Bash(cd * && *)
  - Bash(cat *)
  - Bash(test *)
  - Bash(git *)
  - Glob
  - Read
  - Edit
  - Write
  - Grep
---

# sim2real-prepare

Phase 1 of the sim2real transfer pipeline: Extract, Translate, Generate, Review.

## CRITICAL: Working Directory

**All commands in this skill must run from the `sim2real/` root directory.**
The `llm-d-inference-scheduler/` submodule is a reference repo — if you need
to run commands there, always use a subshell: `(cd llm-d-inference-scheduler && ...)`
Never `cd` into it directly. If you find yourself in a subdirectory, run
`cd` back to the sim2real root before continuing.

Before each stage, verify you are in the right directory:
```bash
# Must show the sim2real root (contains blis_router/, workspace/, config/, etc.)
ls blis_router/best/best_program.go >/dev/null 2>&1 || { echo "ERROR: not in sim2real root — cd back"; exit 1; }
```

## Arguments

- `--reviews N` — Number of multi-model review rounds (default: 2)

Parse the argument: if the user invokes `/sim2real-prepare --reviews 3`, set
`REVIEW_ROUNDS=3`. Otherwise `REVIEW_ROUNDS=2`.

## Prerequisites

Before starting, verify all required artifacts and submodules. **HALT if any
check fails.**

```bash
# Required input artifacts
test -f blis_router/best/best_program.go || { echo "HALT: missing blis_router/best/best_program.go"; exit 1; }
test -f blis_router/best/best_program_info.json || { echo "HALT: missing best_program_info.json"; exit 1; }
test -f docs/transfer/blis_to_llmd_mapping.md || { echo "HALT: missing mapping artifact"; exit 1; }
test -f docs/transfer/scorer_template.go.md || { echo "HALT: missing scorer template"; exit 1; }

# Submodules initialized
test -d inference-sim/sim || { echo "HALT: inference-sim submodule not initialized"; exit 1; }
test -d llm-d-inference-scheduler/pkg || { echo "HALT: llm-d-inference-scheduler not initialized"; exit 1; }
```

Record the pipeline commit:
```bash
mkdir -p workspace
git rev-parse HEAD > workspace/pipeline_commit.txt
```

### Load Setup Config

Read `workspace/setup_config.json` to get namespace, registry, and run name:

```bash
if [ -f workspace/setup_config.json ]; then
  NAMESPACE=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json'))['namespace'])")
  REGISTRY=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json')).get('registry',''))")
  RUN_NAME=$(python3 -c "import json; print(json.load(open('workspace/setup_config.json'))['run_name'])")
  export NAMESPACE
  RUN_DIR="workspace/runs/${RUN_NAME}"
  echo "Loaded config: NAMESPACE=${NAMESPACE}, RUN_DIR=${RUN_DIR}"
else
  echo "HALT: workspace/setup_config.json not found — run /sim2real-setup first"
  exit 1
fi
```

Update `run_metadata.json` at the start:
```bash
python3 -c "
import json
m = json.load(open('${RUN_DIR}/run_metadata.json'))
m['stages']['prepare']['status'] = 'in_progress'
m['stages']['prepare']['started_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
"
```

## Stage 1: Extract

Follow the logic from `prompts/extract.md`. Key steps:

1. Delete stale artifact: `rm -f $RUN_DIR/prepare_algorithm_summary.json`
2. Run extraction:
   ```bash
   .venv/bin/python tools/transfer_cli.py extract --strict blis_router/best/
   ```
3. Validate output:
   ```bash
   test -f $RUN_DIR/prepare_algorithm_summary.json || { echo "HALT: extraction failed"; exit 1; }
   .venv/bin/python tools/transfer_cli.py validate-schema $RUN_DIR/prepare_algorithm_summary.json
   .venv/bin/python -c "import json,sys; d=json.load(open('$RUN_DIR/prepare_algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"
   ```

**HALT on any failure.** Do not proceed to Translate.

### Artifacts produced
- `$RUN_DIR/prepare_algorithm_summary.json`

## Stage 2: Translate

Follow the logic from `prompts/translate.md`. Key steps:

1. Delete stale artifact: `rm -f $RUN_DIR/prepare_signal_coverage.json`
2. Check submodule staleness — compare mapping artifact commit hash with
   `llm-d-inference-scheduler` submodule HEAD (7-char prefix match).
3. For each signal in `algorithm_summary.json`:
   - Look up in `docs/transfer/blis_to_llmd_mapping.md`
   - Record: prod_name, prod_access_path, fidelity, staleness_window_ms
   - Propagate normalization and fidelity_provisional flags
4. Handle unknown-type signals — move to `unmapped_signals[]` and HALT if any.
5. F-10 double-counting detection (skip if `composite_signals` is empty).
6. Write `signal_coverage.json` and validate:
   ```bash
   .venv/bin/python tools/transfer_cli.py validate-schema $RUN_DIR/prepare_signal_coverage.json
   .venv/bin/python -c "import json,sys; d=json.load(open('$RUN_DIR/prepare_signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)"
   ```

**HALT on any failure.** Do not proceed to Generate.

### Artifacts produced
- `$RUN_DIR/prepare_signal_coverage.json`

## Stage 3: Generate

Follow the logic from `prompts/generate.md`. Key steps:

1. Create directories and delete stale outputs:
   ```bash
   mkdir -p $RUN_DIR/prepare_tekton
   rm -f $RUN_DIR/prepare_stage3_output.json $RUN_DIR/prepare_tekton/algorithm_values.yaml $RUN_DIR/prepare_tekton/values.yaml
   ```
2. Re-verify EVOLVE-BLOCK content hash against `algorithm_summary.json`.
3. Generate scorer code:
   - Parse EVOLVE-BLOCK source
   - Map signals using `signal_coverage.json`
   - Apply normalizations from `algorithm_summary.json`
   - Generate scorer `.go`, test `.go`, and registration in `llm-d-inference-scheduler/pkg/plugins/scorer/`
   - Use template from `docs/transfer/scorer_template.go.md`
4. Validate generated code — no PLACEHOLDER markers, correct imports, type assertions.
5. Write `$RUN_DIR/prepare_stage3_output.json`.
6. Generate Tekton artifacts:
   - Resolve inference-sim image tag:
     ```bash
     BLIS_IMAGE_TAG=$(cd inference-sim && git describe --tags 2>/dev/null)
     ```
     The tag must be a clean release tag (e.g., `v0.6.14`), not a commit hash with `-g` suffix.
   - Read `config/env_defaults.yaml`
   - Generate `$RUN_DIR/prepare_tekton/algorithm_values.yaml`
   - **IMPORTANT:** The `observe.image` field must use the full registry path:
     ```yaml
     observe:
       image: "ghcr.io/inference-sim/blis:${BLIS_IMAGE_TAG}"
     ```
     Do NOT use bare `inference-sim:TAG` — it must be `ghcr.io/inference-sim/blis:TAG`.
   - **IMPORTANT:** When generating `algorithm_values.yaml`, the `decode.acceleratorTypes`
     field must use `labelValues` (not the GPU product name as a key). Correct format:
     ```yaml
     decode:
       acceleratorTypes:
         labelValues:
           - NVIDIA-H100-80GB-HBM3
     ```
     Do NOT use `nvidia.com/gpu.product:` as a key — the upstream Helm chart
     schema requires `labelValues` as the only allowed property besides `labelKey`.
   - **IMPORTANT:** Each container in `decode.containers` must include readiness
     and startup probes so Kubernetes knows when vLLM is actually ready to serve.
     The merge step replaces lists (not merges), so probes must be in `algorithm_values.yaml`:
     ```yaml
     decode:
       containers:
         - modelCommand: vllmServe
           mountModelVolume: true
           image: "ghcr.io/llm-d/llm-d-cuda:v0.5.1"
           readinessProbe:
             httpGet:
               path: /health
               port: 8000
             initialDelaySeconds: 10
             periodSeconds: 5
             failureThreshold: 3
           startupProbe:
             httpGet:
               path: /health
               port: 8000
             initialDelaySeconds: 30
             periodSeconds: 10
             failureThreshold: 60
           # ... resources, args, etc.
     ```
     Without these probes, pods report Ready before vLLM finishes loading,
     causing 503 errors from the workload harness.
   - **IMPORTANT:** Do NOT include `--port=8000` in the container `args`.
     The llm-d-cuda image and Helm chart already configure the port.
     Adding `--port` causes a duplicate key error and `Address already in use` crash.
   - Merge: `.venv/bin/python tools/transfer_cli.py merge-values --env config/env_defaults.yaml --algorithm $RUN_DIR/prepare_tekton/algorithm_values.yaml --out $RUN_DIR/prepare_tekton/values.yaml`
7. Validate all outputs:
   ```bash
   .venv/bin/python tools/transfer_cli.py validate-schema $RUN_DIR/prepare_stage3_output.json
   test -f $RUN_DIR/prepare_tekton/values.yaml || { echo "HALT: merge-values failed"; exit 1; }
   ```

**HALT on any failure.** Do not proceed to Review.

### Artifacts produced
- `$RUN_DIR/prepare_stage3_output.json`
- `llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go`
- `llm-d-inference-scheduler/pkg/plugins/scorer/<name>_test.go`
- `$RUN_DIR/prepare_tekton/algorithm_values.yaml`
- `$RUN_DIR/prepare_tekton/values.yaml`

## Stage 4: Build, Test & Equivalence Gate

Build and test the generated scorer, then run Suite A/B/C before AI review.
All commands run from the sim2real root — use subshells for llm-d-inference-scheduler.

### Build & Test

```bash
(cd llm-d-inference-scheduler && GOWORK=off go build ./...)
(cd llm-d-inference-scheduler && GOWORK=off go vet ./...)
(cd llm-d-inference-scheduler && GOWORK=off go test -timeout 10m ./pkg/plugins/scorer/... -v)
```

**HALT** on any failure. If build/test fails, fix the generated scorer and retry
(same retry logic as `prompts/test.md`: max 4 per class, 6 total).

### Equivalence Gate (Suite A/B/C)

```bash
# Suite A — Rank correlation (Kendall-tau >= 0.8)
(cd llm-d-inference-scheduler && GOWORK=off go test -tags suitea -json -v -timeout 10m ./pkg/plugins/scorer/...)

# Suite B — Staleness stability (informational, no halt on failure)
(cd llm-d-inference-scheduler && GOWORK=off go test -tags suiteb -json -v -timeout 10m ./pkg/plugins/scorer/...)

# Suite C — Concurrent safety
(cd llm-d-inference-scheduler && GOWORK=off go test -tags suitec -race -json -v -timeout 10m ./pkg/plugins/scorer/...)
```

**HALT** if Suite A or Suite C fails after retries.

Write `$RUN_DIR/prepare_equivalence_results.json` with suite metrics and validate:
```bash
.venv/bin/python tools/transfer_cli.py validate-schema $RUN_DIR/prepare_equivalence_results.json
python3 -c "
import json, sys
d = json.load(open('$RUN_DIR/prepare_equivalence_results.json'))
if not d.get('suite_a', {}).get('passed'):
    print('HALT: Suite A did not pass'); sys.exit(1)
if not d.get('suite_c', {}).get('passed'):
    print('HALT: Suite C did not pass'); sys.exit(1)
print('Equivalence gate: PASS (tau=' + str(d['suite_a']['kendall_tau']) + ')')
"
```

### Artifacts produced
- `$RUN_DIR/prepare_equivalence_results.json`

## Stage 5: Multi-Model AI Review

**IMPORTANT:** Verify you are in the sim2real root before proceeding.
If you `cd`'d into `llm-d-inference-scheduler/` during previous stages,
return now: `cd` to the directory containing `blis_router/`, `workspace/`, `config/`.

Use the existing `/review-plan` skill from sdlc-plugins for AI review.
This provides battle-tested LiteLLM integration with redaction and error handling.

### Prepare review content

Write a temporary review document combining the scorer code and context:

```bash
SCORER_FILE=$(python3 -c "import json; print(json.load(open('$RUN_DIR/prepare_stage3_output.json'))['scorer_file'])")
EVOLVE_BLOCK=$(python3 -c "import json; print(json.load(open('$RUN_DIR/prepare_algorithm_summary.json'))['evolve_block_file'])")

cat > $RUN_DIR/prepare_translation_review_input.md <<REVIEWEOF
# Translation Consistency Review

## Task
Verify that the generated scorer Go code faithfully implements the evolved
routing algorithm. For EACH signal, check:
- The scorer reads the correct production field
- Normalization matches the algorithm summary
- Weight/coefficient matches the EVOLVE-BLOCK
- Scoring logic (comparison, threshold, combination) is faithful

Respond with: verdict (consistent/inconsistent), per-signal analysis, issues, suggestions.

## Generated Scorer Code
\`\`\`go
$(cat "$SCORER_FILE")
\`\`\`

## Algorithm Summary
\`\`\`json
$(cat $RUN_DIR/prepare_algorithm_summary.json)
\`\`\`

## Signal Coverage
\`\`\`json
$(cat $RUN_DIR/prepare_signal_coverage.json)
\`\`\`

## EVOLVE-BLOCK Source
\`\`\`go
$(cat "$EVOLVE_BLOCK")
\`\`\`
REVIEWEOF
```

### Run reviews

Locate the review script:
```
Glob: **/skills/review-plan/scripts/review.sh
```
Store the result as `[REVIEW_SCRIPT]`.

For each round (1 to `REVIEW_ROUNDS`), run reviews with each model:

```bash
bash [REVIEW_SCRIPT] $RUN_DIR/prepare_translation_review_input.md Azure/gpt-4o
bash [REVIEW_SCRIPT] $RUN_DIR/prepare_translation_review_input.md GCP/gemini-2.5-flash
bash [REVIEW_SCRIPT] $RUN_DIR/prepare_translation_review_input.md aws/claude-opus-4-6
```

### Evaluate reviews

Read each model's output. If any reviewer identifies inconsistencies:
- Apply fixes to the scorer file
- Re-validate build: `cd llm-d-inference-scheduler && GOWORK=off go build ./...`
- Regenerate `$RUN_DIR/prepare_translation_review_input.md` with updated scorer
- Re-run reviews for the next round

After round N: if any reviewer still flags inconsistencies → **HALT**.

Save review results to `$RUN_DIR/prepare_translation_reviews.json`.

### Artifacts produced
- `$RUN_DIR/prepare_translation_reviews.json`
- `$RUN_DIR/prepare_translation_review_input.md` (intermediate, can be deleted)

### Update Run Metadata

```bash
python3 -c "
import json
m = json.load(open('${RUN_DIR}/run_metadata.json'))
m['stages']['prepare']['status'] = 'completed'
m['stages']['prepare']['completed_at'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
m['stages']['prepare']['summary'] = 'Extract, translate, generate, and AI review completed'
m['stages']['prepare']['artifacts'] = [
    'prepare_algorithm_summary.json',
    'prepare_signal_coverage.json',
    'prepare_stage3_output.json',
    'prepare_translation_reviews.json'
]
json.dump(m, open('${RUN_DIR}/run_metadata.json', 'w'), indent=2)
"
```

## Completion

Print artifact summary:

```
━━━ /sim2real-prepare complete ━━━

Artifacts produced:
  $RUN_DIR/prepare_algorithm_summary.json    ✓ Extract
  $RUN_DIR/prepare_signal_coverage.json      ✓ Translate
  $RUN_DIR/prepare_stage3_output.json        ✓ Generate
  $RUN_DIR/prepare_tekton/values.yaml        ✓ Generate (merged)
  $RUN_DIR/prepare_translation_reviews.json  ✓ AI Review (N rounds, consensus)
  <scorer_file>                       ✓ Generate

Next: run /sim2real-deploy
```
