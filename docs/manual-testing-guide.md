# Manual Testing Guide — sim2real Transfer Pipeline

**Date:** 2026-03-17
**Purpose:** What to manually test after each PR lands, and how.

All commands assume you are in the repo root (`sim2real/`).

---

## PR1 — Transfer Infrastructure + Mapping Artifact + CLI Extract

### 1.1 Extract command — happy path

```bash
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
```

**Verify:**
- Exit code 0
- Stdout JSON (operational report) includes `status: "ok"`, `signal_count > 0`, `errors: []`
- `workspace/algorithm_summary.json` exists, is valid JSON, and contains `evolve_block_content_hash`, a non-empty `signals` array, and `scope_validation_passed: true`

### 1.2 Extract — schema validation round-trip

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```

**Verify:** Exit code 0. All required fields present per `tools/schemas/algorithm_summary.schema.json`.

### 1.3 Extract — strict mode

```bash
.venv/bin/python tools/transfer_cli.py extract --strict blis_router/best/
```

**Verify:** Same as 1.1, plus stricter fidelity checks pass.

### 1.4 Extract — tampered input (negative test)

```bash
# Copy routing dir, corrupt best_program.go by removing EVOLVE-BLOCK markers
cp -r blis_router/best/ /tmp/routing_broken/
sed -i '' 's/EVOLVE-BLOCK-START/BROKEN/' /tmp/routing_broken/best_program.go
.venv/bin/python tools/transfer_cli.py extract /tmp/routing_broken/
```

**Verify:** Exit code non-zero. Clear error message about missing markers.

### 1.5 Validate mapping artifact

Requires `workspace/algorithm_summary.json` from 1.1.

```bash
.venv/bin/python tools/transfer_cli.py validate-mapping
```

**Verify:**
- Exit code 0
- `mapping_complete: true`
- `missing_signals: []` (all signals from extract are mapped)
- `extra_signals: []`
- `stale_commit: false` (commit hash present in mapping artifact)

### 1.6 Mapping artifact — spot-check against submodule

```bash
# Check that the pinned commit matches what's in the submodule
cd llm-d-inference-scheduler && git log --oneline -1 && cd ..
```

**Verify:** Compare the commit hash with the one in `docs/transfer/blis_to_llmd_mapping.md` header. Note any drift.

### 1.7 Content hash stability

```bash
# Run extract twice, compare hashes from the file artifact (not stdout)
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
jq .evolve_block_content_hash workspace/algorithm_summary.json > /tmp/hash1.txt
.venv/bin/python tools/transfer_cli.py extract blis_router/best/
jq .evolve_block_content_hash workspace/algorithm_summary.json > /tmp/hash2.txt
diff /tmp/hash1.txt /tmp/hash2.txt
```

**Verify:** No diff output — hashes are identical (deterministic extraction).

### 1.8 Automated tests

```bash
.venv/bin/python -m pytest tools/ -v
```

**Verify:** All tests pass.

---

## PR2 — Scorer Template Artifact

### 2.1 Template exists and has required sections

```bash
grep -c '^## Section' docs/transfer/scorer_template.go.md
```

**Verify:** Output is `9` — the 9 sections are: Package Structure, Scorer Interface Implementation, Metric Access Pattern, Config Registration, Plugin Initialization Lifecycle, Feature Flag, Unit Test Structure, ScoreEndpoints Equivalence Test Helper, Hot-Reload Documentation.

### 2.2 Template references correct interface methods

Open `docs/transfer/scorer_template.go.md` and confirm these methods are documented:
- `Score(ctx, cycleState, request, endpoints)`
- `TypedName()`
- `Category()`
- Factory function with `Register()` call

### 2.3 Template compiles against submodule HEAD

```bash
# Extract Go code blocks from template, attempt build
cd llm-d-inference-scheduler
GOWORK=off go build ./...
cd ..
```

**Verify:** The submodule builds. The template's imports and types are consistent with the current submodule API. (Full compile-from-template is a PR3 deliverable via `check_scorer_template.sh`.)

### 2.4 Cross-reference with mapping artifact

Open both files side by side:
- `docs/transfer/blis_to_llmd_mapping.md`
- `docs/transfer/scorer_template.go.md`

**Verify:**
- Metric access pattern in template (`endpoint.GetMetrics()`) matches mapping artifact's `access_path` column
- KVUtilization normalization note (divide by 100) is reflected in template comments
- Signal field names in template match mapping artifact's `prod_field` column

### 2.5 Automated tests

```bash
.venv/bin/python -m pytest tools/ -v
```

**Verify:** All tests still pass (no regressions from PR2 changes).

---

## PR3 — Prompt Templates (Stages 1–3) + Go Harness

### 3.1 Prompt templates exist

```bash
ls prompts/
```

**Verify:** At minimum: `transfer.md`, `extract.md`, `translate.md`, `generate.md`

### 3.2 Prompt templates reference correct artifacts

For each prompt file, check:
- `extract.md` references `blis_router/best/best_program.go` and produces `workspace/algorithm_summary.json`
- `translate.md` reads `algorithm_summary.json` + mapping artifact, produces `signal_coverage.json`
- `generate.md` reads `signal_coverage.json` + scorer template, produces files in target submodule

### 3.3 New/extended schemas validate

```bash
# Verify schema files parse as valid JSON
.venv/bin/python -c "import json; json.load(open('tools/schemas/signal_coverage.schema.json'))"
.venv/bin/python -c "import json; json.load(open('tools/schemas/escalation.schema.json'))"
```

**Verify:** No parse errors. `signal_coverage.schema.json` is new in PR3. `escalation.schema.json` (PR2) was extended with Stage 2 and Stage 3 halt reasons.

### 3.4 Go harness builds

```bash
# go.work at repo root enables building from here
go build ./tools/harness/...
```

**Verify:** Clean build with no errors.

### 3.5 Go harness tests pass

```bash
go test ./tools/harness/... -v
```

**Verify:** All tests pass, including:
- `TestEquivalenceTrivial` — basic routing sanity
- `TestStaleHashAbortsParsing` — drift detection (cross-PR contract #1)
- `TestRunTuplesPanicRecovery` — error handling with stack traces
- `TestKVUtilizationNormalization` — normalization + clamping (cross-PR contract #2)
- `TestUnknownSignalTypeRejection` — unknown-type signal rejection (cross-PR contract #3)
- `TestCrossLanguageHashConsistency` — Go hash matches Python extract hash
- `TestEvolvedScorerContract` — scorer interface contract verified (TypedName, Category, correct type)
- `TestNewEvolvedScorerNilPanics` — nil Algorithm rejected at construction
- `TestLoadAlgorithmErrorPaths` — table-driven error path coverage (path traversal, missing fields, etc.)
- `TestEquivalence` — dispatches Suite B + Suite C
- `TestEvolvedAlgorithmSingleEndpoint` — single endpoint score < 0.3 (KV penalty fires at KVUtil=0.95)
- `TestEvolvedAlgorithmKVPenaltyBoundary` — equal scores at exactly KV=0.9 (boundary, penalty does not fire)
- `TestEvolvedAlgorithmInflightTiebreaker` — idle (InFlight=0) scores higher than busy (InFlight=5)
- `TestEvolvedScorerScoresCorrectly` — metric translation correct, evolved algorithm scores returned
- `TestKendallTau` — rank correlation utility verified
- `TestMaxAbsDiff` — max absolute difference utility verified

### 3.6 Cross-language content hash contract

This is now automated by `TestCrossLanguageHashConsistency` (see 3.5). The test runs
`transfer_cli.py extract`, reads the Python-computed hash from `algorithm_summary.json`,
recomputes in Go, and asserts they match. To verify manually:

```bash
go test ./tools/harness/... -v -run TestCrossLanguageHashConsistency
```

**Verify:** Test passes — Python and Go compute the same SHA-256 hash for the EVOLVE-BLOCK content.

### 3.7 Scorer template staleness check script

```bash
bash tools/check_scorer_template.sh
echo "Exit code: $?"
```

**Verify:**
- Exit 0 = template compiles against current submodule HEAD (fresh)
- Exit 1 = template is stale (submodule has breaking changes)
- Exit 2 = infrastructure error

### 3.8 End-to-end dry run: Stages 1–3

This is the key integration test. Run Claude Code interactively:

```
> Read prompts/transfer.md and execute Stages 1–3 using blis_router/best/ as input
```

**Verify at each stage:**

| Stage | Check | Expected |
|-------|-------|----------|
| 1 (Extract) | `workspace/algorithm_summary.json` created | Valid JSON, all required fields |
| 2 (Translate) | `workspace/signal_coverage.json` created | All signals mapped, `unmapped_signals` empty |
| 3 (Generate) | Branch `transfer/<name>` in submodule | Plugin, tests, config files committed |

### 3.9 Automated tests (full suite)

```bash
.venv/bin/python -m pytest tools/ -v
```

**Verify:** All Python tests pass. No regressions.

---

## PR4 — Stage 4 (Test + Retry Logic)

### 4.1 Stage 4 prompt exists

```bash
ls prompts/test.md
```

### 4.2 Generated plugin builds in submodule

After running Stages 1–3 (prerequisite):

```bash
cd llm-d-inference-scheduler
git checkout transfer/<algorithm_name>
GOWORK=off go build ./...
cd ..
```

**Verify:** Clean compilation.

### 4.3 Generated plugin tests pass

```bash
cd llm-d-inference-scheduler
go test ./pkg/plugins/scorer/<algorithm_name>_test.go -v
cd ..
```

**Verify:** Unit tests pass including:
- Request parsing test
- Disabled-scorer no-op test
- Overlap assertion (disabled scorers have weight zero)

### 4.4 Full repo test suite

```bash
cd llm-d-inference-scheduler
go test ./... -v
cd ..
```

**DO NOT REMOVE NOTES**

Several tests did not work out of the box. I had to:
- modify `test/config/prefix_cache_mode_test.go` to SKIP when 
- ensure these images were already present: 
    - `llm-d-inference-scheduler:dev` (created by `make image-build`)
    - `llm-d-routing-sidecar:dev` (created by `make image-build`)
    - `llm-d-uds-tokenizer:dev` (created by `make image-build-uds-tokenizer`)
    - `ghcr.io/llm-d/llm-d-inference-sim:latest` (pulled by `podmad pull`)
- set `CONTAINER_RUNTIME=podman`
- exclude `tests/sidecar/e2e` which expect live cluster
  ```shell
  CONTAINER_RUNTIME=podman go test $(go list ./... | grep -v 'test/sidecar/e2e') -v
  ```

**END NOTES**

**Verify:** No regressions in existing tests.

### 4.5 Lint passes

```bash
cd llm-d-inference-scheduler
GOWORK=off golangci-lint run ./...
cd ..
```

**Verify:** No new lint violations from generated code.

### 4.6 Retry logic — simulate a failure

Introduce a deliberate compilation error in the generated plugin (e.g., typo in a type name). Then run Stage 4 via Claude Code.

**DO NOT REMOVE NOTES**

- Use prompt: `Read prompts/test.md and execute Stage 4`
- _claude_: there is no evidence that the retry counter is incremented except in the output; the `prompts/test.md` explicityly says not to any success artifacts.

**END NOTES**

**Verify:**
- LLM detects the error
- LLM fixes and retries
- Fix is committed as a separate commit: `[transfer] Fix: compilation — <description>`
- Retry counter increments

### 4.7 Retry limit — loop detection

Introduce an error the LLM cannot fix (e.g., reference a non-existent API). Run Stage 4.

**DO NOT REMOVE NOTES**

- Claude suggests a change to an unrelated files such as `active_request.go`.

**END NOTES**

**Verify:**
- Halts after 3 retries for same error class (or 2 identical consecutive errors)
- Reports escalation with diagnosis
- `workspace/escalation.json` written with `stage: 4` and `halt_reason`

### 4.8 Stage 4 artifacts

Stage 4 writes **no output artifact on success** (`prompts/test.md` Step 5 is explicit about this). Success is verified by re-running the build commands directly.

**On success — verify by re-running:**

```bash
cd llm-d-inference-scheduler
go build ./...
go vet ./...
go test -timeout 10m ./pkg/plugins/scorer/... -v
cd ..
```

All three commands must exit 0.

**On halt — verify escalation artifact:**

```bash
cat workspace/escalation.json | jq .
```

**Verify:**
- `stage` is `4`
- `halt_reason` is one of the documented halt conditions (e.g., `build_compilation_failure`, `identical_consecutive_errors`)
- `details` contains human-readable diagnosis and recommended next steps

**Stage 3 artifact (for reference):**

```bash
cat workspace/stage3_output.json | jq .
```

Contains `scorer_file`, `test_file`, `register_file`, `scorer_type`. This file is not updated by Stage 4.

---

## PR5 — Validation Pipeline (Stage 5)

### 5.1 Stage 5 prompt and schema exist

```bash
ls prompts/validate.md
ls tools/schemas/validation_results.schema.json
```

Also verify Stage 3 produced Tekton artifacts (new in PR5 via generate.md Step 8):

```bash
.venv/bin/python -c "import json; d=json.load(open('workspace/stage3_output.json')); assert 'tekton_artifacts' in d, 'tekton_artifacts missing from stage3_output.json'"
ls workspace/tekton/values.yaml
ls workspace/tekton/pipelinerun-noise.yaml workspace/tekton/pipelinerun-baseline.yaml workspace/tekton/pipelinerun-treatment.yaml
```

**Verify:** All files exist. If `tekton_artifacts` is missing from `workspace/stage3_output.json`, Stage 3 was run before PR5 landed — re-run Stage 3 to regenerate.

### 5.2 Go harness tests pass (Suites B, C, unit tests)

Prerequisites: submodules checked out (`inference-sim`, `llm-d-inference-scheduler`).
No pipeline artifacts required.

```bash
go test ./tools/harness/... -race -timeout 120s -v
```

**Verify:**
- `TestSuiteB_StalenessStability` — passes (tau=1.0, informational_only=true for v1)
- `TestSuiteC_ConcurrentDeterminism` — 20 goroutines produce identical score vectors
- `TestSuiteC_PileOn` — no endpoint receives > 2× fair share across 100 decisions
- `TestEvolvedAlgorithmSingleEndpoint` — single endpoint score < 0.3 (KV penalty fires at KVUtil=0.95)
- `TestEvolvedAlgorithmKVPenaltyBoundary` — equal scores at exactly KV=0.9 (boundary, penalty does not fire)
- `TestEvolvedAlgorithmInflightTiebreaker` — idle (InFlight=0) scores higher than busy (InFlight=5)
- `TestEvolvedScorerScoresCorrectly` — metric translation correct, heavy < light
- All other unit tests pass

### 5.3 Suite A — rank correlation equivalence (requires pipeline artifacts)

Prerequisites: Stages 1–3 must have run (need `workspace/algorithm_summary.json`
and `blis_weighted_scoring.go` generated into `llm-d-inference-scheduler`).
Suite A is behind the `suitea` build tag because it imports the Stage 3 output.

```bash
go test -tags suitea ./tools/harness/... -run TestSuiteA_KendallTau -v -timeout 60s
```

**Verify:**
- Test does not skip (confirms `workspace/algorithm_summary.json` exists and is valid)
- `Suite A: mean_kendall_tau=X.XXXX` logged — value must be > 0.8
- `max_abs_error` logged (informational)
- `tuple_count=200`

### 5.4 Stage 5 overview — two-pass structure

Stage 5 runs as two passes separated by the noise Tekton pipeline:

- **Pass 1:** Run `validate.md` from Step 1. The Step 1 noise gate checks `benchmark-state`. If noise hasn't been collected yet, Stage 5 exits 3 (REENTER) — a planned pause. Jump to Step 5 (5a + 5b for noise phase only).
- **Pass 2:** After the noise pipeline finishes and `workspace/noise_results.json` exists, re-run `validate.md` from Step 1. The noise gate clears, and validation runs through Suites A/B/C → partial `validation_results.json` → baseline/treatment pipelines → mechanism check → 5c-merge → `generate-evidence` → final schema validation.

Exit code semantics for automated harnesses: `0` = complete, `1` = error/halt, `2` = infrastructure error, `3` = REENTER (planned pause after noise — not an error).

Before both passes, set the cluster namespace:

```bash
export NAMESPACE=<your-cluster-namespace>
```

### 5.4a Noise phase — Tekton pipeline (Pass 1)

After `validate.md` exits 3 (REENTER), execute Steps 5a and 5b for the noise phase only.

**Prerequisites:** `workspace/tekton/values.yaml` and `workspace/tekton/pipelinerun-noise.yaml` exist (generated by Stage 3 Step 8), `kubectl` context points to the target cluster, Tekton is installed.

**Initialize state:**

```bash
.venv/bin/python tools/transfer_cli.py benchmark-state \
  --workspace workspace/ --namespace $NAMESPACE
```

**Verify:** Exit 0. Exit 1 = cluster context mismatch; exit 2 = missing `workspace/algorithm_summary.json`.

**Pre-flight:**

```bash
.venv/bin/python tools/transfer_cli.py preflight \
  --phase noise --values workspace/tekton/values.yaml --namespace $NAMESPACE
```

**Verify:** Exit 0. Exit 1 means cluster isn't ready (check stdout for which check failed).

**Compile and submit:**

```bash
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase noise \
  --out workspace/tekton/compiled/
PIPELINERUN_NAME=sim2real-noise-$(date +%s)
.venv/bin/python tools/transfer_cli.py render-pipelinerun \
  --template workspace/tekton/pipelinerun-noise.yaml \
  --vars PIPELINERUN_NAME=$PIPELINERUN_NAME NAMESPACE=$NAMESPACE PHASE=noise \
  --out /tmp/pipelinerun-noise.yaml
kubectl apply -f workspace/tekton/compiled/noise-pipeline.yaml
kubectl apply -f /tmp/pipelinerun-noise.yaml
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase noise --status running --pipelinerun $PIPELINERUN_NAME
```

**Wait for completion, then extract results:**

```bash
# Wait (poll with tkn pr describe $PIPELINERUN_NAME until Succeeded/Failed)
# On Succeeded — extract via extractor pod, then:
.venv/bin/python tools/transfer_cli.py convert-trace \
  --input-dir workspace/noise_raw/ --output workspace/noise_results.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/noise_results.json
.venv/bin/python tools/transfer_cli.py benchmark-state --workspace workspace/ \
  --set-phase noise --status done --results workspace/noise_results.json
```

**Verify:**
- `workspace/noise_results.json` exists and passes schema validation
- `workspace/benchmark_state.json` shows `noise.status = "done"`
- T_eff is computed internally by `benchmark` from `noise_results.json` — no separate `noise-characterize` step needed

### 5.5 Baseline and treatment phases — Tekton pipelines (Pass 2)

After noise is done (Pass 1 complete), re-enter `validate.md` from Step 1. The noise gate passes, Suites A/B/C run, a partial `validation_results.json` is written (Step 4b), then Step 5b runs the baseline and treatment pipelines.

For each of `baseline` and `treatment`, the flow mirrors 5.4a above (replace `noise` with the appropriate phase):

```bash
.venv/bin/python tools/transfer_cli.py preflight \
  --phase baseline --values workspace/tekton/values.yaml --namespace $NAMESPACE
.venv/bin/python tools/transfer_cli.py compile-pipeline \
  --template-dir tektonc-data-collection/tektoncsample/sim2real \
  --values workspace/tekton/values.yaml --phase baseline \
  --out workspace/tekton/compiled/
# (submit, wait, extract, convert-trace, validate-schema, benchmark-state --status done)
# Repeat for treatment phase
```

**Verify after each phase:**
- `workspace/baseline_results.json` and `workspace/treatment_results.json` both exist
- Both pass schema validation
- `workspace/benchmark_state.json` shows each phase `status = "done"`

### 5.5a Mechanism check

After all three phases are done, run the mechanism check:

```bash
.venv/bin/python tools/transfer_cli.py benchmark \
  --noise    workspace/noise_results.json \
  --baseline workspace/baseline_results.json \
  --treatment workspace/treatment_results.json \
  --signal-coverage workspace/signal_coverage.json \
  --workloads-dir blis_router/workloads/ \
  --out workspace/benchmark_output.json
.venv/bin/python tools/transfer_cli.py validate-schema workspace/benchmark_output.json
```

**Verify:**
- Exit code 0 for PASS or INCONCLUSIVE; exit 1 = FAIL; exit 2 = infrastructure error
- `mechanism_check_verdict` in `workspace/benchmark_output.json` is `PASS`, `FAIL`, or `INCONCLUSIVE`
- PASS criterion: at least one matched workload improvement ≥ T_eff (computed from `noise_results.json`)
- `workload_classification` array shows per-workload improvements
- INCONCLUSIVE requires operator review before proceeding (see validate.md Step 5c for remediation options)

### 5.6 Validation results — merge and validate schema

`validate.md` writes results in two steps:

1. **Step 4b** (after Suites A/B/C): writes a partial `workspace/validation_results.json` containing only `suite_a`, `suite_b`, `suite_c`. Do **not** run `validate-schema` on this partial file — it will fail because `benchmark`, `noise_cv`, and `overall_verdict` are absent.

2. **Step 5c-merge** (after benchmark): merges `workspace/benchmark_output.json` into the partial file, adds `noise_cv` at top level, and computes `overall_verdict`. Only after this step is schema validation expected to pass.

Verify the final merged file:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/validation_results.json
```

**Verify:**
- Exit code 0
- `overall_verdict` is `PASS`, `FAIL`, or `INCONCLUSIVE`
- PASS requires: Suite A passed AND Suite C passed AND `mechanism_check_verdict == PASS`

```bash
cat workspace/validation_results.json | jq '{overall_verdict, suite_a_tau: .suite_a.kendall_tau, suite_c_passed: .suite_c.passed, noise_cv}'
```

### 5.7 Evidence document

```bash
.venv/bin/python tools/transfer_cli.py generate-evidence \
  --workspace workspace/ --out workspace/transfer_evidence.md
cat workspace/transfer_evidence.md
```

**Verify:** Exit 0. Output is a human-readable summary with algorithm name, suite results, noise CV, mechanism check verdict, and per-workload table.

---

## PR6 — Stage 6 (PR Creation + Self-Verification + Calibration)

### 6.1 Stage 6 prompt exists

```bash
ls prompts/pr.md
```

### 6.2 Pre-flight: gh auth

```bash
gh auth status
```

**Verify:** Authenticated. Stage 6 should check this before attempting PR creation.

### 6.3 PR creation (dry run)

Run Stage 6 via Claude Code with `overall_verdict = PASS`.

**Verify:**
- Branch pushed to remote
- PR created with title: `[sim2real] Add <algorithm_name> adaptive routing plugin`
- PR body includes: algorithm summary, sim improvement %, suite results, mechanism check, rollback instructions
- `workspace/pr_result.json` created with PR URL

### 6.4 PR blocks on non-PASS verdict

Manually set `overall_verdict` to `FAIL` in `workspace/validation_results.json`. Run Stage 6.

**Verify:** Stage 6 halts with clear message. No PR created.

### 6.5 PR blocks on bare INCONCLUSIVE

Set `overall_verdict` to `INCONCLUSIVE` without override. Run Stage 6.

**Verify:** Halts with: "Cannot create PR: mechanism check is INCONCLUSIVE."

### 6.6 Calibration log entry

After successful PR creation:

```bash
cat docs/transfer/calibration_log.md
```

**Verify:**
- New entry appended with correct template
- Numeric values match `workspace/validation_results.json`
- Per-workload table populated
- Observed/predicted ratio computed

### 6.7 Pipeline self-verification — smoke test

Create a trivial algorithm (e.g., `weight = 1.0` for all endpoints) and run all 6 stages.

**Verify:**
- Every stage produces output in expected format
- Pipeline completes end-to-end
- No stage errors

### 6.8 Pipeline self-verification — known-answer test

Create a synthetic algorithm with known behavior (e.g., `if load > 5, weight = 0`).

**Verify:**
- Suite A produces exact expected scores
- Equivalence testing infrastructure is correct
- Rank correlation matches manual calculation

---

## Cross-PR Regression Checks

Run after **every** PR merge:

```bash
# Python tests
.venv/bin/python -m pytest tools/ -v

# Extract still works
.venv/bin/python tools/transfer_cli.py extract blis_router/best/

# Mapping still valid
.venv/bin/python tools/transfer_cli.py validate-mapping

# Schema validation still works
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```

After PR3+, also:

```bash
# Go harness builds and tests (go.work enables repo-root invocation)
go build ./tools/harness/...
go test ./tools/harness/... -race -timeout 120s -v

# Scorer template freshness
bash tools/check_scorer_template.sh
```

After PR5+, Suite A (requires pipeline artifacts and Stage 3 output):

```bash
# Only after running Stages 1–3 and confirming blis_weighted_scoring.go exists
go test -tags suitea ./tools/harness/... -run TestSuiteA_KendallTau -v -timeout 60s
```
