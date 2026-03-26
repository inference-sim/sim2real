---
stage: 0
version: "1.0"
pipeline_commit: "set-at-runtime"
description: "Top-level orchestrator — sequences Stages 1-6 of the sim2real transfer pipeline"
---

# Transfer Pipeline Orchestrator

This prompt drives the end-to-end sim2real transfer pipeline. It sequences
Stages 1 through 6, verifying prerequisite artifacts between each stage.

## Pipeline Overview

| Stage | Name      | Prompt File           | Input Artifacts                              | Output Artifacts                   |
|-------|-----------|-----------------------|----------------------------------------------|------------------------------------|
| 1     | Extract   | `prompts/extract.md`  | `blis_router/best/best_program.go`, `blis_router/best/best_program_info.json` | `workspace/algorithm_summary.json` |
| 2     | Translate | `prompts/translate.md`| `workspace/algorithm_summary.json`, `docs/transfer/blis_to_llmd_mapping.md` | `workspace/signal_coverage.json` |
| 3     | Generate  | `prompts/generate.md` | `workspace/algorithm_summary.json`, `workspace/signal_coverage.json`, `docs/transfer/scorer_template.go.md` | scorer files + `workspace/stage3_output.json` |
| 3.5   | Validate Translation | `prompts/validate-translation.md` | `workspace/stage3_output.json`, `workspace/algorithm_summary.json`, `workspace/signal_coverage.json` | `workspace/translation_validation.json` |
| 4     | Test      | `prompts/test.md`       | `workspace/stage3_output.json`               | build + test pass (no artifact)    |
| 4.5   | Equivalence Gate | `prompts/equivalence-gate.md` | `workspace/stage3_output.json`, `workspace/algorithm_summary.json` | `workspace/equivalence_results.json` |
| 4.75  | Build & Push EPP | `prompts/build-push.md` | `workspace/tekton/algorithm_values.yaml`, `config/env_defaults.yaml`, `workspace/equivalence_results.json` | treatment EPP image in registry; updated `workspace/tekton/values.yaml` |
| 5     | Validate  | `prompts/validate.md`   | `workspace/equivalence_results.json`, `workspace/stage3_output.json` | `workspace/validation_results.json` |
| 6     | PR        | *Defined in PR6*      | all artifacts                                | PRs in target repos                |

## Prerequisites

Before starting the pipeline, verify all required artifacts and submodules exist.
**HALT if any check fails.**

```bash
# Verify required artifacts
test -f docs/transfer/blis_to_llmd_mapping.md || { echo "HALT: missing mapping artifact"; exit 1; }
test -f docs/transfer/scorer_template.go.md || { echo "HALT: missing scorer template"; exit 1; }
test -f blis_router/best/best_program.go || { echo "HALT: missing blis_router/best/best_program.go"; exit 1; }
test -f blis_router/best/best_program_info.json || { echo "HALT: missing blis_router/best/best_program_info.json"; exit 1; }

# Verify submodules initialized
test -d inference-sim/sim || { echo "HALT: inference-sim submodule not initialized — run git submodule update --init inference-sim"; exit 1; }
test -d llm-d-inference-scheduler/pkg || { echo "HALT: llm-d-inference-scheduler submodule not initialized — run git submodule update --init llm-d-inference-scheduler"; exit 1; }

# Verify submodule status (no leading '-' indicating uninitialized)
git submodule status inference-sim llm-d-inference-scheduler
```

## Record Pipeline Commit

Record the repo HEAD at pipeline start for drift detection. This file persists
across separate Claude Code sessions (one per stage).

```bash
mkdir -p workspace
git rev-parse HEAD > workspace/pipeline_commit.txt
```

## Stage Execution

### Stage 1: Extract

**Prompt:** `prompts/extract.md`

Follow the Stage 1 prompt to extract algorithm metadata from routing artifacts.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/algorithm_summary.json || { echo "HALT: Stage 1 output missing"; exit 1; }

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json || { echo "HALT: Stage 1 schema validation failed"; exit 1; }

# Semantic check: scope_validation_passed must be true
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)" || { echo "HALT: scope_validation_passed is false"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 2.

---

### Stage 2: Translate

**Prompt:** `prompts/translate.md`

Follow the Stage 2 prompt to map simulation signals to production equivalents.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/signal_coverage.json || { echo "HALT: Stage 2 output missing"; exit 1; }

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json || { echo "HALT: Stage 2 schema validation failed"; exit 1; }

# Semantic check: coverage must be complete
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)" || { echo "HALT: coverage incomplete"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 3.

---

### Stage 3: Generate

**Prompt:** `prompts/generate.md`

Follow the Stage 3 prompt to generate the production scorer plugin.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/stage3_output.json || { echo "HALT: Stage 3 output missing"; exit 1; }

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json || { echo "HALT: Stage 3 schema validation failed"; exit 1; }

# Semantic check: scorer file exists and has no PLACEHOLDER markers
.venv/bin/python -c "import json,sys,os; d=json.load(open('workspace/stage3_output.json')); scorer=d.get('scorer_file',''); sys.exit(0 if os.path.isfile(scorer) and 'PLACEHOLDER' not in open(scorer).read() else 1)" || { echo "HALT: scorer file missing or contains PLACEHOLDER"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 4.

---

### Stage 4: Test

**Prompt:** `prompts/test.md`

Follow the Stage 4 prompt to build and test the generated scorer plugin with retry logic.

**Between-stage validation:**

```bash
# Verify Stage 3 output is still present and schema-valid
test -f workspace/stage3_output.json || { echo "HALT: Stage 3 output missing"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json || { echo "HALT: Stage 3 schema validation failed"; exit 1; }

# Verify generated scorer file exists and builds
SCORER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['scorer_file'])")
test -f "$SCORER_FILE" || { echo "HALT: generated scorer file missing: $SCORER_FILE"; exit 1; }

# Final build + vet verification
cd llm-d-inference-scheduler && go build ./... && go vet ./... && go test -timeout 10m ./pkg/plugins/scorer/... -v && cd .. || { echo "HALT: Stage 4 build/vet/test verification failed"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 4.5 (Equivalence Gate).

---

### Stage 4.5: Equivalence Gate

**Prompt:** `prompts/equivalence-gate.md`

Run Suite A/B/C to validate the generated scorer against the simulation reference.
This gate prevents building an image with a scorer that doesn't match the evolved algorithm.

**Between-stage validation:**

```bash
# Verify equivalence results exist and are valid
test -f workspace/equivalence_results.json || { echo "HALT: Stage 4.5 output missing"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/equivalence_results.json || { echo "HALT: Stage 4.5 schema validation failed"; exit 1; }

# Verify suites passed
.venv/bin/python -c "
import json, sys
d = json.load(open('workspace/equivalence_results.json'))
if not d.get('suite_a', {}).get('passed'):
    print('HALT: Suite A did not pass'); sys.exit(1)
if not d.get('suite_c', {}).get('passed'):
    print('HALT: Suite C did not pass'); sys.exit(1)
print('Equivalence gate: PASS (tau=' + str(d['suite_a']['kendall_tau']) + ')')
" || { echo "HALT: Stage 4.5 validation failed"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 4.75.

---

### Stage 4.75: Build & Push EPP Image

**Prompt:** `prompts/build-push.md`

Follow the Stage 4.75 prompt to build the treatment EPP image from the
`llm-d-inference-scheduler` submodule and push it to the developer's registry.

**Between-stage validation:**

```bash
# Verify treatment image reference is set in algorithm_values.yaml
.venv/bin/python -c "
import yaml, sys
d = yaml.safe_load(open('workspace/tekton/algorithm_values.yaml'))
img = (d.get('stack',{}).get('gaie',{}).get('treatment',{})
        .get('helmValues',{}).get('inferenceExtension',{}).get('image',{}))
if not img.get('hub') or not img.get('tag'):
    print('HALT: treatment EPP image not set'); sys.exit(1)
print(f\"EPP image: {img['hub']}/{img['name']}:{img['tag']}\")
" || { echo "HALT: Stage 4.75 validation failed"; exit 1; }

# Verify values.yaml was regenerated
test -f workspace/tekton/values.yaml || { echo "HALT: workspace/tekton/values.yaml missing"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 5.

---

### Stage 5: Validate

**Prompt:** `prompts/validate.md`

Follow the Stage 5 prompt to run 3-suite equivalence validation + cluster benchmarks.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/validation_results.json || { echo "HALT: Stage 5 output missing"; exit 1; }

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json || { echo "HALT: Stage 5 schema validation failed"; exit 1; }

# Semantic check: overall_verdict must be PASS (or INCONCLUSIVE with operator sign-off)
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/validation_results.json')); v=d.get('overall_verdict',''); sys.exit(0 if v in ('PASS','INCONCLUSIVE') else 1)" || { echo "HALT: Stage 5 overall_verdict is not PASS or INCONCLUSIVE"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 6.

---

### Stage 6: PR

*Defined in PR6.* Stage 6 creates PRs in target repositories.

---

## Pipeline Completion

After all stages complete successfully, verify repo HEAD has not drifted:

```bash
[ "$(git rev-parse HEAD)" = "$(cat workspace/pipeline_commit.txt)" ] || echo "WARNING: repo HEAD has drifted since pipeline start"
```

## Halt Conditions

- **Any stage failure** halts the pipeline. Do not skip failed stages.
- **Missing prerequisite artifacts** halt before the dependent stage starts.
- **Schema validation failure** on any inter-stage artifact halts the pipeline.
- **Semantic check failure** (e.g., `scope_validation_passed == false`, `coverage_complete == false`) halts the pipeline.

## Expected Outputs

After a complete pipeline run:
- `workspace/algorithm_summary.json` — Stage 1 output
- `workspace/signal_coverage.json` — Stage 2 output
- `workspace/stage3_output.json` — Stage 3 output
- Generated scorer files in `llm-d-inference-scheduler/pkg/plugins/scorer/`
- `workspace/equivalence_results.json` — Stage 4.5 output
- `workspace/pipeline_commit.txt` — Pipeline commit record
