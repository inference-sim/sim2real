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
| 1     | Extract   | `prompts/extract.md`  | `routing/best_program.py`, `routing/best_program_info.json` | `workspace/algorithm_summary.json` |
| 2     | Translate | `prompts/translate.md`| `workspace/algorithm_summary.json`, `docs/transfer/blis_to_llmd_mapping.md` | `workspace/signal_coverage.json` |
| 3     | Generate  | `prompts/generate.md` | `workspace/algorithm_summary.json`, `workspace/signal_coverage.json`, `docs/transfer/scorer_template.go.md` | scorer files + `workspace/stage3_output.json` |
| 4     | Test      | *Defined in PR4*      | `workspace/stage3_output.json`               | test results                       |
| 5     | Validate  | *Defined in PR5*      | test results, harness output                 | validation report                  |
| 6     | PR        | *Defined in PR6*      | all artifacts                                | PRs in target repos                |

## Prerequisites

Before starting the pipeline, verify all required artifacts and submodules exist.
**HALT if any check fails.**

```bash
# Verify required artifacts
test -f docs/transfer/blis_to_llmd_mapping.md || echo "HALT: missing mapping artifact"
test -f docs/transfer/scorer_template.go.md || echo "HALT: missing scorer template"
test -f routing/best_program.py || echo "HALT: missing routing input best_program.py"
test -f routing/best_program_info.json || echo "HALT: missing routing input best_program_info.json"

# Verify submodules initialized
test -d inference-sim/sim || echo "HALT: inference-sim submodule not initialized — run git submodule update --init inference-sim"
test -d llm-d-inference-scheduler/pkg || echo "HALT: llm-d-inference-scheduler submodule not initialized — run git submodule update --init llm-d-inference-scheduler"

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
test -f workspace/algorithm_summary.json || echo "HALT: Stage 1 output missing"

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json

# Semantic check: scope_validation_passed must be true
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"
```

**HALT if any validation fails.** Do not proceed to Stage 2.

---

### Stage 2: Translate

**Prompt:** `prompts/translate.md`

Follow the Stage 2 prompt to map simulation signals to production equivalents.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/signal_coverage.json || echo "HALT: Stage 2 output missing"

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json

# Semantic check: coverage must be complete
.venv/bin/python -c "import json,sys; d=json.load(open('workspace/signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)"
```

**HALT if any validation fails.** Do not proceed to Stage 3.

---

### Stage 3: Generate

**Prompt:** `prompts/generate.md`

Follow the Stage 3 prompt to generate the production scorer plugin.

**Between-stage validation:**

```bash
# Verify output exists
test -f workspace/stage3_output.json || echo "HALT: Stage 3 output missing"

# Schema validation
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json

# Semantic check: scorer file exists and has no PLACEHOLDER markers
.venv/bin/python -c "import json,sys,os; d=json.load(open('workspace/stage3_output.json')); scorer=d.get('scorer_file',''); sys.exit(0 if os.path.isfile(scorer) and 'PLACEHOLDER' not in open(scorer).read() else 1)"
```

**HALT if any validation fails.** Do not proceed to Stage 4.

---

### Stage 4: Test

*Defined in PR4.* Stage 4 drives build + test with retry logic.

---

### Stage 5: Validate

*Defined in PR5.* Stage 5 runs 3-suite equivalence validation + cluster benchmarks.

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
- `workspace/pipeline_commit.txt` — Pipeline commit record
