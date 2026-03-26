---
stage: 4.5
version: "1.0"
pipeline_commit: "set-at-runtime"
description: "Stage 4.5 — Equivalence gate: validate scorer against simulation reference using Suite A/B/C"
---

# Stage 4.5: Equivalence Gate

Run the Go test harness suites (A, B, C) to validate the generated scorer plugin
against the simulation reference implementation. This is the go/no-go gate before
the EPP image build. Suite A failures detected here prevent building an image
with a buggy scorer.

## Prerequisites

Verify Stage 4 passed and required artifacts exist. **HALT if any check fails.**

```bash
# Stage 3 output valid
test -f workspace/stage3_output.json || { echo "HALT: missing stage3_output.json"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json \
  || { echo "HALT: stage3_output.json schema validation failed"; exit 1; }

# algorithm_summary.json exists (required by Suite A t.Skip guard)
test -f workspace/algorithm_summary.json || { echo "HALT: workspace/algorithm_summary.json missing (run Stage 1 first)"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json \
  || { echo "HALT: workspace/algorithm_summary.json schema validation failed"; exit 1; }

# Scorer builds (verifies Stage 4 completed)
SCORER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['scorer_file'])")
test -f "$SCORER_FILE" || { echo "HALT: scorer file missing: $SCORER_FILE"; exit 1; }
(cd llm-d-inference-scheduler && GOWORK=off go build ./pkg/plugins/scorer/... && GOWORK=off go vet ./pkg/plugins/scorer/...) \
  || { echo "HALT: scorer does not build — run Stage 4 first"; exit 1; }
```

## Stale Artifact Guard

Remove any prior equivalence results and Stage 4.5 escalation artifacts:

```bash
rm -f workspace/equivalence_results.json
.venv/bin/python -c "
import json, os
esc = 'workspace/escalation.json'
if os.path.isfile(esc):
    try:
        d = json.load(open(esc))
        if d.get('stage') == 4 and 'equivalence' in d.get('halt_reason', ''):
            os.remove(esc)
            print('Removed stale Stage 4.5 escalation artifact')
    except (json.JSONDecodeError, KeyError):
        pass
"
```

## Retry State

Initialize these counters at the start of Stage 4.5:

| Counter | Initial | Halt threshold | Action on limit |
|---------|---------|----------------|-----------------|
| `retries_suite_a` | 0 | >= 4 (3 retries done) | HALT: `equivalence_suite_a_failure` |
| `retries_suite_c` | 0 | >= 4 (3 retries done) | HALT: `equivalence_suite_c_failure` |
| `retries_total` | 0 | >= 6 (5 retries done) | HALT: `equivalence_total_retry_limit_exceeded` |
| `error_signatures` | [] | 3 occurrences of same signature | HALT: `equivalence_oscillating_errors` |
| `last_error_signature` | null | same as current → immediate halt | HALT: `equivalence_identical_consecutive_errors` |

**Error signature** = `(failing_suite, first_failure_message)` where `first_failure_message`
is the first `"Action":"fail"` test name from the JSON output, or `"<unclassified>"` if
not parseable.

## Step 1: Suite A — Rank Correlation Equivalence

Run Suite A with `-json -v` and the `suitea` build tag:

```bash
set -o pipefail
go test ./tools/harness/... -tags suitea -run TestSuiteA_KendallTau -v -timeout 60s -json 2>&1 \
  | tee /tmp/suite_a_output.json
SUITE_A_EXIT=${PIPESTATUS[0]}
```

If `SUITE_A_EXIT == 0`: extract numerical results and proceed to Step 2.

```bash
grep -oE 'mean_kendall_tau=[0-9]+\.[0-9]+|max_abs_error=[0-9]+\.[0-9]+|tuple_count=[0-9]+' /tmp/suite_a_output.json
```

Record: `mean_kendall_tau`, `max_abs_error`, `tuple_count`.

If `SUITE_A_EXIT != 0`: proceed to **Step 4: Retry** with `failing_suite = "suite_a"`.

## Step 2: Suite B — Staleness Rank Stability (Informational)

```bash
go test ./tools/harness/... -tags suiteb -run TestSuiteB_StalenessStability -v -timeout 30s -json 2>&1 \
  | tee /tmp/suite_b_output.json
```

Do NOT halt on Suite B results (informational only in v1). Extract:

```bash
grep -oE 'rank_stability_tau=[0-9]+\.[0-9]+|threshold_crossing_pct=[0-9]+\.[0-9]+%' /tmp/suite_b_output.json
```

Record: `rank_stability_tau`, `threshold_crossing_pct` (as a number, strip the `%` when writing to JSON).

## Step 3: Suite C — Concurrent Safety and Pile-On

```bash
set -o pipefail
go test ./tools/harness/... -tags suitec -run TestSuiteC -v -race -timeout 60s -json 2>&1 \
  | tee /tmp/suite_c_output.json
SUITE_C_EXIT=${PIPESTATUS[0]}
```

If `SUITE_C_EXIT == 0`: extract results and proceed to Step 5 (Completion).

```bash
grep -oE 'max_pile_on_ratio=[0-9]+\.[0-9]+' /tmp/suite_c_output.json
```

Record: `deterministic` (true if TestSuiteC_ConcurrentDeterminism passes), `max_pile_on_ratio`.

If `SUITE_C_EXIT != 0`: proceed to **Step 4: Retry** with `failing_suite = "suite_c"`.

## Step 4: Retry

Before retrying, perform these checks IN ORDER:

### 4a: Check identical consecutive errors

Compute error signature: `(failing_suite, first_failure_message)`.
If identical to `last_error_signature`, **HALT** with `halt_reason: "equivalence_identical_consecutive_errors"`.

### 4b: Check non-consecutive duplicate (oscillation detection)

Add signature to `error_signatures[]`. If count reaches 3, **HALT** with
`halt_reason: "equivalence_oscillating_errors"`.

### 4c: Check per-suite retry limit

Increment the appropriate counter:
- Suite A failure: `retries_suite_a += 1`
- Suite C failure: `retries_suite_c += 1`

If counter >= 4: **HALT** with `equivalence_suite_a_failure` or `equivalence_suite_c_failure`.

### 4d: Check total retry limit

Increment `retries_total += 1`. If >= 6: **HALT** with `equivalence_total_retry_limit_exceeded`.

### 4e: Apply fix, re-validate, and retry

If all checks pass:

1. Update `last_error_signature`.
2. Re-read `workspace/stage3_output.json` for current file paths.
3. Read the suite failure output (`/tmp/suite_a_output.json` or `/tmp/suite_c_output.json`).
4. Apply the fix to the scorer. **Only modify files listed in `workspace/stage3_output.json`.**
5. **Re-run Stage 3.5 mechanical checks** (translation validation):

```bash
SCORER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['scorer_file'])")
.venv/bin/python tools/transfer_cli.py validate-translation \
  --algorithm workspace/algorithm_summary.json \
  --signal-coverage workspace/signal_coverage.json \
  --scorer-file "$SCORER_FILE"
```

If `validate-translation` exits non-zero, the fix broke translation fidelity. Attempt to fix the
translation issue, then re-run `validate-translation`. If it fails again, **HALT** with
`halt_reason: "equivalence_translation_revalidation_failed"`.

6. **Re-run Stage 4 build/vet/test steps** (inline, not re-entering Stage 4 prompt):

```bash
(cd llm-d-inference-scheduler && GOWORK=off go build ./... && GOWORK=off go vet ./... && GOWORK=off go test -timeout 10m ./pkg/plugins/scorer/... -v)
```

If any command fails, the fix broke compilation or unit tests. Attempt to fix, then re-run.
If it fails again, **HALT** with `halt_reason: "equivalence_build_test_revalidation_failed"`.

7. Return to **Step 1** (re-run all suites from the beginning).

## Step 5: Completion

Write `workspace/equivalence_results.json`:

```json
{
  "suite_a": {
    "passed": <true|false>,
    "kendall_tau": <mean_tau>,
    "max_abs_error": <max_abs_err>,
    "tuple_count": <tuple_count>
  },
  "suite_b": {
    "passed": true,
    "rank_stability_tau": <tau>,
    "threshold_crossing_pct": <pct as number, not string — strip the % from grep output>,
    "informational_only": true
  },
  "suite_c": {
    "passed": <true|false>,
    "deterministic": <true if TestSuiteC_ConcurrentDeterminism passed>,
    "max_pile_on_ratio": <ratio>
  }
}
```

Validate the output:

```bash
.venv/bin/python tools/transfer_cli.py validate-schema workspace/equivalence_results.json \
  || { echo "HALT: equivalence_results.json failed schema validation"; exit 1; }
```

## Halt Conditions

| Condition | halt_reason | Retryable? | Action |
|-----------|-------------|------------|--------|
| Suite A retries >= 4 | `equivalence_suite_a_failure` | No | Write escalation.json, HALT |
| Suite C retries >= 4 | `equivalence_suite_c_failure` | No | Write escalation.json, HALT |
| Total retries >= 6 | `equivalence_total_retry_limit_exceeded` | No | Write escalation.json, HALT |
| Identical consecutive errors | `equivalence_identical_consecutive_errors` | No | Write escalation.json, HALT |
| Same error 3 times | `equivalence_oscillating_errors` | No | Write escalation.json, HALT |
| Translation re-validation failed twice | `equivalence_translation_revalidation_failed` | No | Write escalation.json, HALT |
| Build/test re-validation failed twice | `equivalence_build_test_revalidation_failed` | No | Write escalation.json, HALT |

On any halt, write `workspace/escalation.json`:

```json
{
  "stage": 4,
  "halt_reason": "<halt_reason from table above>",
  "details": "<human-readable description including: last suite output, retry counts, and recommended next steps>"
}
```

> Note: `"stage": 4` (not 4.5) because the escalation schema's `stage` field is an integer (1-6). Stage 4.5 uses `stage: 4` in the escalation artifact; the halt_reason prefix `equivalence_` disambiguates it from Stage 4 halt reasons.

**Recommended next steps:**
- `equivalence_suite_a_failure`: Scorer formula does not match simulation reference. Compare `tools/harness/evolved_algorithm.go` against the scorer. Check: normalization type, weight computation, signal mapping, EffectiveLoad formula.
- `equivalence_suite_c_failure`: Scorer is not concurrency-safe or distributes load unevenly. Check for shared mutable state or non-deterministic map iteration.

## Expected Outputs

**On success:**
- `workspace/equivalence_results.json` with all suites passed

**On halt:**
- `workspace/escalation.json` with Stage 4.5 halt reason
