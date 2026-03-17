# PR4: Stage 4 Prompt Template + Test Retry Logic — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement Stage 4 (Test) — build and test the generated scorer plugin with error classification, retry logic, and loop detection.

**The problem today:** After Stage 3 generates scorer code, there is no pipeline stage to compile, test, and iteratively fix that code. The operator would have to manually run `go build`, interpret errors, and decide whether to retry — with no structured error classification or halt conditions.

**What this PR adds:**
1. `prompts/test.md` — Stage 4 prompt template guiding build → test → retry → escalate
2. `tools/transfer_cli.py test-status` command — parses `go build`/`go test` output, classifies errors, outputs structured JSON
3. Updated escalation schema with Stage 4 halt reasons
4. Updated orchestrator prompt (`prompts/transfer.md`) with Stage 4 section

**Why this matters:** Stage 4 is the feedback loop that makes the pipeline self-correcting. Without it, a single compilation error in generated code terminates the pipeline. With it, the LLM can iteratively fix errors up to defined retry limits, with structured escalation when it can't.

**Architecture:** Prompt template (Markdown) drives an interactive Claude Code session. The `test-status` CLI command (Python, stdlib only) parses Go toolchain output and classifies errors into categories. No Go code changes — PR4 only reads `go build`/`go test` output from the llm-d-inference-scheduler submodule.

**PR Category:** Pipeline Stage (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR4: "Prompt template (Stage 4) + test retry logic" in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Behavioral Contracts:** See Part 1, Section B below

---

## Part 1: Design Validation

### A) Executive Summary

PR4 implements Stage 4 of the transfer pipeline — the build-test-retry loop for generated scorer code. It sits between PR3 (Stages 1-3 + Go harness) and PR5 (validation pipeline) in the dependency chain.

As a Pipeline Stage PR, it gets 4 review perspectives: contracts, artifacts, prompts, plan. Single convergence round — re-run only if CRITICAL findings.

**Phase 0 Audit Results:**
- Submodules verified: inference-sim at `aa4bbb7`, llm-d-inference-scheduler at `091312c`
- PR3 artifacts confirmed: `prompts/transfer.md`, `prompts/extract.md`, `prompts/translate.md`, `prompts/generate.md`, `tools/harness/`, `tools/schemas/stage3_output.schema.json`, `tools/schemas/escalation.schema.json`
- `stage3_output.schema.json` confirmed: requires `scorer_file`, `test_file`, `register_file`, `scorer_type`
- `escalation.schema.json` confirmed: current `halt_reason` enum does not include Stage 4 values (must be extended)
- DEVIATION D-1: Macro plan says `register_file` but schema field is `register_file` with pattern `^llm-d-inference-scheduler/pkg/plugins/register\\.go$` — matches
- No other deviations found

### B) Behavioral Contracts

#### Positive Contracts

**BC-1: test-status command classifies Go build errors**
- GIVEN `go build` output containing compilation errors
- WHEN `python tools/transfer_cli.py test-status` is run with that output piped to stdin
- THEN it outputs JSON with `error_class: "compilation"`, `error_count` > 0, and `errors[]` with per-error details including file path and message
- MECHANISM: Regex parsing of Go compiler error format (`file.go:line:col: message`)

**BC-2: test-status command classifies Go test failures**
- GIVEN `go test` output containing test failures
- WHEN `python tools/transfer_cli.py test-status` is run with that output piped to stdin
- THEN it outputs JSON with `error_class: "test_failure"`, `error_count` > 0, and `errors[]` with per-error test names and failure messages
- MECHANISM: Regex parsing of Go test output format (`--- FAIL: TestName`)

**BC-3: test-status command classifies infrastructure errors**
- GIVEN `go build` or `go test` output containing module resolution or timeout errors
- WHEN `python tools/transfer_cli.py test-status` is run with that output piped to stdin
- THEN it outputs JSON with `error_class: "infrastructure"` and `errors[]` with diagnostic details
- MECHANISM: Pattern matching for `go: module`, `context deadline exceeded`, `cannot find module` patterns

**BC-4: test-status outputs valid JSON with exit codes**
- GIVEN any input (valid or empty)
- WHEN `python tools/transfer_cli.py test-status` is run
- THEN stdout is valid JSON matching the macro plan schema: `{"status": "ok"|"error", "error_class": string, "error_count": int, "errors": [...]}`
- MECHANISM: `_output()` helper from existing CLI pattern; exit 0 = no errors found, exit 1 = errors classified

#### Prompt Template Contracts

**BC-5: Stage 4 prompt has 4 required sections**
- GIVEN `prompts/test.md`
- WHEN reviewed for structural completeness
- THEN it MUST contain: (1) Prerequisites, (2) Validation steps, (3) Halt conditions, (4) Expected outputs
- MECHANISM: Same structural contract as BC-5 from PR3

**BC-6: Stage 4 prompt enforces retry limits**
- GIVEN the Stage 4 prompt is being followed and errors are encountered
- WHEN the same error class has been retried 3 times, OR total retries reach 5
- THEN the prompt instructs the operator to HALT and escalate
- MECHANISM: Prompt text specifies retry counters and halt thresholds

**BC-7: Stage 4 prompt detects identical consecutive errors**
- GIVEN two consecutive retry attempts produce the exact same error output
- WHEN the operator checks the error signature
- THEN the prompt instructs immediate HALT (no further retries)
- MECHANISM: Prompt text instructs comparing error signature (class + first error line) between attempts

**BC-8: Stage 4 prompt detects non-consecutive duplicate errors**
- GIVEN the same error signature appears 3 times across any retries (not necessarily consecutive)
- WHEN the operator checks the rolling error history
- THEN the prompt instructs HALT with oscillation detection message
- MECHANISM: Prompt text maintains a rolling list of error signatures and checks for 3x occurrence

#### Negative Contracts

**BC-9: Stage 4 never proceeds without Stage 3 output**
- GIVEN `workspace/stage3_output.json` does not exist or fails schema validation
- WHEN Stage 4 is started
- THEN it MUST HALT before any build/test commands
- MECHANISM: Prerequisite check in prompt; `validate-schema` call before any `go` commands

**BC-10: Stage 4 never retries infrastructure errors**
- GIVEN a `go build` or `go test` failure classified as `infrastructure`
- WHEN the error class is checked
- THEN the prompt instructs immediate HALT (infrastructure errors are not retried)
- MECHANISM: Prompt text explicitly excludes `infrastructure` class from retry logic

#### Error Handling Contracts

**BC-11: Escalation artifact written on Stage 4 halt**
- GIVEN Stage 4 encounters a blocking condition (retry limit, infrastructure error, etc.)
- WHEN the operator follows the halt procedure
- THEN `workspace/escalation.json` is written with `stage: 4` and the appropriate `halt_reason`
- MECHANISM: Prompt text includes escalation.json writing instructions for each halt condition

**BC-12: test-status exit code semantics**
- GIVEN any invocation of `test-status`
- WHEN it completes
- THEN exit code 0 means no errors found in input, exit code 1 means errors classified, exit code 2 means infrastructure error in the CLI itself
- MECHANISM: Same `_output()` pattern as other CLI commands

### C) Component Interaction

```
                    ┌──────────────────┐
                    │ workspace/       │
                    │ stage3_output.json│
                    └────────┬─────────┘
                             │ (read by Stage 4)
                             ▼
┌────────────────────────────────────────────────┐
│  prompts/test.md (Stage 4 Prompt)              │
│                                                │
│  1. Validate prerequisites                     │
│  2. Run go build → pipe to test-status         │
│  3. If errors: classify → retry or halt        │
│  4. Run go test → pipe to test-status          │
│  5. If errors: classify → retry or halt        │
│  6. Update orchestrator stage status            │
└────────────────┬───────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────┐     ┌──────────────────────────────┐
│ tools/transfer_cli.py       │     │ llm-d-inference-scheduler/   │
│   test-status command       │     │   go build ./...             │
│   (classifies errors)       │     │   go test ./pkg/plugins/...  │
└─────────────────────────────┘     └──────────────────────────────┘
```

**Cross-system data flow:**
- Stage 3 → `workspace/stage3_output.json` → Stage 4 reads scorer/test file paths
- Stage 4 → `go build`/`go test` in llm-d-inference-scheduler submodule → test-status classifies output
- Stage 4 → `workspace/escalation.json` on halt (consumed by operator)
- Stage 4 → orchestrator prompt updated to include Stage 4 between-stage validation

**Workspace artifacts:**
- Consumed: `workspace/stage3_output.json` (written by Stage 3)
- Produced: `workspace/escalation.json` (only on halt — written per escalation schema)

### D) Deviation Log

| Macro Plan Says | Micro Plan Does | Reason |
|-----------------|-----------------|--------|
| `test-status` parses `go test` and `go build` output | `test-status` reads from stdin (pipe-friendly) | SIMPLIFICATION: stdin is more flexible than file args; Go toolchain output goes to stderr for builds, stdout for tests — piping via `2>&1` unifies both |
| Error classes: compilation, test-unit, test-integration, lint | Error classes: compilation, test_failure, infrastructure | SIMPLIFICATION: Go test output doesn't distinguish unit vs integration; three classes suffice for retry logic |
| Lint as separate error class with own retry counter | Lint (`go vet`) failures classified as compilation | SIMPLIFICATION: `go vet` runs as Step 2 between build and test, but errors are classified as `compilation` since they indicate code issues. A dedicated lint class could be added in PR5 if needed. Known debt. |
| Stage 4 has 60-minute total timeout | Prompt mentions timeout but enforcement is operator-driven | DEFERRAL: Automated timeout enforcement requires a wrapper script; for v1 interactive sessions the operator manages wall-clock time. The prompt advises checking elapsed time |
| Non-consecutive duplicate detection via rolling hash | Prompt maintains list of `(error_class, first_error_line)` tuples | SIMPLIFICATION: A list of string tuples is simpler than a hash table and achieves the same detection with ≤5 retries |
| No workspace output artifact specified | No `stage4_output.json` — Stage 4 success is "build + test pass" | CORRECTION: The macro plan says "test results" for Stage 4 output but doesn't define a schema. Stage 5 reads generated scorer code directly via `stage3_output.json` paths, not Stage 4 results |

### E) Review Guide

1. **THE TRICKY PART:** The `test-status` error classification regex. Go compiler errors come in `file:line:col: message` format but can also include notes, warnings, and multi-line context. The regex must be robust enough to classify correctly without over-matching.

2. **WHAT TO SCRUTINIZE:** BC-6 through BC-8 (retry limits and loop detection). These are implemented entirely in prompt text — verify the instructions are unambiguous and the counters are correctly specified.

3. **WHAT'S SAFE TO SKIM:** The orchestrator update (adding Stage 4 section to `prompts/transfer.md`) follows the exact pattern of Stages 1-3. The escalation schema extension is mechanical.

4. **KNOWN DEBT:** The lint step (`go vet`) is included in the prompt but not in the error classification — `test-status` doesn't have a dedicated `lint` class. Lint errors are classified as `compilation` since they indicate code issues. A dedicated lint class could be added in PR5 if needed.

---

## Part 2: Executable Implementation

### F) Implementation Overview

**Files to create:**
- `prompts/test.md` — Stage 4 prompt template (~200 lines)
- `tools/test_test_status.py` — Tests for the test-status CLI command

**Files to modify:**
- `tools/transfer_cli.py` — Add `test-status` command (~80 lines)
- `tools/schemas/escalation.schema.json` — Add Stage 4 halt reasons to enum
- `prompts/transfer.md` — Add Stage 4 section between Stages 3 and 5
- `docs/transfer/README.md` — Add PR4 deliverables section
- `CLAUDE.md` — Update PR4 status to Complete

**Key decisions:**
- `test-status` reads from stdin (not file args) for pipe-friendly usage
- Three error classes: `compilation`, `test_failure`, `infrastructure`
- No `stage4_output.json` — Stage 4 success state is implicit (build + test pass)
- Retry logic is entirely in the prompt template (not in Python code)

**Dead artifact check:** All files have consumers:
- `prompts/test.md` → consumed by pipeline operator via `prompts/transfer.md`
- `test-status` command → consumed by `prompts/test.md`
- Escalation schema update → consumed by Stage 4 halt paths
- Tests → consumed by CI and verification gate

### G) Task Breakdown

---

### Task 1: Extend escalation schema with Stage 4 halt reasons

**Contracts Implemented:** BC-11

**Files:**
- Modify: `tools/schemas/escalation.schema.json`

**Step 1: Update escalation schema**

Context: The escalation schema's `halt_reason` enum needs Stage 4 values. These follow the same pattern as existing Stage 2/3 values.

Add these values to the `halt_reason` enum in `escalation.schema.json`:
- `"build_compilation_failure"` — go build failed after max retries
- `"test_failure_limit_exceeded"` — go test failed after max retries
- `"total_retry_limit_exceeded"` — combined retries across all classes hit 5
- `"identical_consecutive_errors"` — same error repeated consecutively
- `"oscillating_errors"` — same error signature appeared 3 times across retries
- `"infrastructure_error_stage4"` — module resolution, timeout, or similar
- `"missing_stage3_output"` — stage3_output.json absent
- `"stage3_schema_validation_failed"` — stage3_output.json present but fails schema validation
- `"scorer_file_missing"` — generated scorer file not found on disk
- `"test_file_missing"` — generated test file not found on disk
- `"register_file_missing"` — generated register file not found on disk

Also update the `description` field on `halt_reason` to document the Stage 4 variants.

**Step 2: Validate schema is still valid JSON**

Run: `python -c "import json; json.load(open('tools/schemas/escalation.schema.json'))"`
Expected: No error

**Step 3: Run existing schema tests**

Run: `.venv/bin/python -m pytest tools/test_schema_validator.py -v`
Expected: All existing tests PASS (schema extension is backward-compatible)

**Step 4: Commit**

```bash
git add tools/schemas/escalation.schema.json
git commit -m "$(cat <<'EOF'
feat(schemas): extend escalation schema with Stage 4 halt reasons (BC-11)

- Add 9 Stage 4 halt_reason enum values for build, test, retry, and
  infrastructure failure modes
- Update halt_reason description to document Stage 4 variants
- Backward-compatible: no existing enum values changed

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Implement test-status CLI command

**Contracts Implemented:** BC-1, BC-2, BC-3, BC-4, BC-12
**Language:** Python

**Files:**
- Modify: `tools/transfer_cli.py`
- Create: `tools/test_test_status.py`

**Step 1: Write failing tests**

Context: Tests use the same `run_cli` pattern from `test_transfer_cli.py`. The `test-status` command reads from stdin, so tests pipe sample Go output.

```python
# tools/test_test_status.py
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "tools" / "transfer_cli.py"


def run_test_status(stdin_text: str) -> tuple[int, dict]:
    """Run test-status command with given stdin, return (exit_code, parsed_json)."""
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    result = subprocess.run(
        [sys.executable, str(CLI), "test-status"],
        input=stdin_text, capture_output=True, text=True,
        cwd=str(REPO_ROOT), env=env,
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        output = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, output


class TestTestStatusCompilation:
    """BC-1: test-status classifies Go build errors."""

    def test_classifies_compilation_error(self):
        """BC-1: Go compiler error is classified as 'compilation'."""
        go_output = (
            "# github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\n"
            "pkg/plugins/scorer/evolved_scorer.go:42:15: undefined: scheduling.InvalidType\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1, f"Expected exit 1, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "compilation"
        assert output["error_count"] >= 1
        assert len(output["errors"]) >= 1
        assert "evolved_scorer.go" in output["errors"][0]["file"]

    def test_classifies_multiple_compilation_errors(self):
        """BC-1: Multiple errors counted correctly."""
        go_output = (
            "pkg/plugins/scorer/foo.go:10:5: undefined: bar\n"
            "pkg/plugins/scorer/foo.go:20:5: cannot use x (type int) as type string\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "compilation"
        assert output["error_count"] == 2

    def test_classifies_import_error(self):
        """BC-1: Import errors are compilation class."""
        go_output = (
            '# github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\n'
            'pkg/plugins/scorer/evolved.go:5:2: "context" imported and not used\n'
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "compilation"


class TestTestStatusTestFailure:
    """BC-2: test-status classifies Go test failures."""

    def test_classifies_test_failure(self):
        """BC-2: Failed Go test is classified as 'test_failure'."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.01s)\n"
            "    evolved_scorer_test.go:25: expected 0.5, got 0.0\n"
            "FAIL\n"
            "FAIL\tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\t0.015s\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"
        assert output["error_count"] >= 1

    def test_classifies_panic_in_test(self):
        """BC-2: Panic during test is classified as 'test_failure'."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.00s)\n"
            "panic: runtime error: index out of range [recovered]\n"
            "FAIL\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"


class TestTestStatusInfrastructure:
    """BC-3: test-status classifies infrastructure errors."""

    def test_classifies_module_error(self):
        """BC-3: Module resolution failure is 'infrastructure'."""
        go_output = (
            "go: github.com/example/missing@v1.0.0: reading "
            "https://proxy.golang.org/github.com/example/missing: 410 Gone\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_timeout(self):
        """BC-3: Context deadline exceeded is 'infrastructure'."""
        go_output = "context deadline exceeded\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_cannot_find_module(self):
        """BC-3: Cannot find module is 'infrastructure'."""
        go_output = "cannot find module providing package github.com/missing/pkg\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"


class TestTestStatusCleanOutput:
    """BC-4: test-status handles clean output and edge cases."""

    def test_clean_build_returns_zero(self):
        """BC-4, BC-12: No errors in input produces exit 0."""
        go_output = "ok  \tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\t0.015s\n"
        code, output = run_test_status(go_output)
        assert code == 0
        assert output["status"] == "ok"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        assert output["errors"] == []

    def test_empty_input_returns_zero(self):
        """BC-4: Empty stdin is not an error."""
        code, output = run_test_status("")
        assert code == 0
        assert output["status"] == "ok"
        assert output["error_count"] == 0

    def test_output_is_valid_json(self):
        """BC-4: Output is always valid JSON."""
        code, output = run_test_status("some random text\n")
        assert isinstance(output, dict)
        assert "status" in output
        assert "error_class" in output
        assert "error_count" in output
        assert "errors" in output


class TestTestStatusExitCode2:
    """BC-12: test-status exit code 2 for CLI infrastructure errors."""

    def test_oversized_input_returns_exit_2(self):
        """BC-4, BC-12: Input exceeding 10 MB limit produces exit code 2 with valid JSON."""
        # Generate input just over the 10 MB limit
        huge_input = "x" * (10 * 1024 * 1024 + 1)
        code, output = run_test_status(huge_input)
        assert code == 2, f"Expected exit 2, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        assert "errors" not in output or output.get("errors", []) == []
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tools/test_test_status.py -v`
Expected: FAIL (test-status command not yet implemented)

**Step 3: Implement test-status command**

Context: Add `cmd_test_status` function and register it in `main()`. The command reads stdin, classifies errors using regex patterns, and outputs structured JSON.

Add to `tools/transfer_cli.py` before `main()`:

```python
def cmd_test_status(args: argparse.Namespace) -> int:
    """Classify errors from go build/test output (reads from stdin).

    Error classes:
      - compilation: Go compiler errors (file:line:col: message)
      - test_failure: Go test failures (--- FAIL: TestName)
      - infrastructure: Module resolution, timeouts, missing packages
      - none: No errors detected

    Precedence: infrastructure > compilation > test_failure
    (infrastructure errors mask other errors since they indicate
    the build environment is broken, not the generated code).

    Exit codes: 0 = no errors found, 1 = errors classified, 2 = CLI infrastructure error
    """
    MAX_INPUT_SIZE = 10 * 1024 * 1024  # 10 MB, consistent with other CLI commands
    try:
        input_text = sys.stdin.read(MAX_INPUT_SIZE + 1)
        if len(input_text) > MAX_INPUT_SIZE:
            return _output("error", 2,
                           output_type="test_status",
                           error_class="none",
                           error_count=0,
                           message="stdin exceeds 10 MB limit")
    except Exception as exc:
        return _output("error", 2,
                       output_type="test_status",
                       error_class="none",
                       error_count=0,
                       message=f"Failed to read stdin: {exc}")

    errors_found: list[dict] = []
    classes_found: set[str] = set()

    # Infrastructure patterns (checked first — highest precedence)
    infra_patterns = [
        (r'go:\s+.*(?:reading|downloading).*(?:410 Gone|404 Not Found|connection refused)', 'module_fetch_failure'),
        (r'cannot find module providing package', 'missing_module'),
        (r'^go:.*context deadline exceeded', 'timeout'),
        (r'go: (?:finding|downloading|extracting)\s+\S+.*(?:error|failed)', 'module_error'),
        (r'no required module provides package', 'missing_module'),
    ]
    for pattern, sub_class in infra_patterns:
        for match in re.finditer(pattern, input_text, re.MULTILINE):
            classes_found.add("infrastructure")
            errors_found.append({
                "class": "infrastructure",
                "sub_class": sub_class,
                "message": match.group(0).strip(),
                "file": "",
            })

    # Compilation errors: file.go:line:col: message OR file.go:line: message
    # The column group is optional to support go vet output (many analyzers
    # emit file:line: without a column number).
    for match in re.finditer(
        r'^(?:#\s+\S+\n)?(\S+\.go):(\d+):(?:(\d+):)?\s+(.+)$',
        input_text, re.MULTILINE
    ):
        classes_found.add("compilation")
        errors_found.append({
            "class": "compilation",
            "message": match.group(4).strip(),
            "file": match.group(1),
            "line": int(match.group(2)),
            "column": int(match.group(3)) if match.group(3) else None,
        })

    # Test failures: --- FAIL: TestName
    for match in re.finditer(
        r'^--- FAIL:\s+(\S+)\s+\([\d.]+s\)',
        input_text, re.MULTILINE
    ):
        classes_found.add("test_failure")
        errors_found.append({
            "class": "test_failure",
            "message": f"Test failed: {match.group(1)}",
            "file": "",
            "test_name": match.group(1),
        })

    # Also detect panics in tests
    for match in re.finditer(
        r'^panic:\s+(.+)$',
        input_text, re.MULTILINE
    ):
        classes_found.add("test_failure")
        errors_found.append({
            "class": "test_failure",
            "message": f"Panic: {match.group(1).strip()}",
            "file": "",
        })

    if not errors_found:
        return _output("ok", 0,
                        output_type="test_status",
                        error_class="none",
                        error_count=0)

    # Precedence: infrastructure > compilation > test_failure
    if "infrastructure" in classes_found:
        primary_class = "infrastructure"
    elif "compilation" in classes_found:
        primary_class = "compilation"
    else:
        primary_class = "test_failure"

    # Filter to only primary-class errors so error_count and errors[]
    # accurately reflect the root cause, not incidental secondary errors
    primary_errors = [e for e in errors_found if e["class"] == primary_class]

    return _output("error", 1,
                    output_type="test_status",
                    error_class=primary_class,
                    error_count=len(primary_errors),
                    errors=primary_errors)
```

Also add the subparser registration in `main()`:

```python
    # test-status
    p_test_status = subparsers.add_parser("test-status",
        help="Classify errors from go build/test output (reads stdin)")
    p_test_status.set_defaults(func=cmd_test_status)
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tools/test_test_status.py -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tools/ -v`
Expected: All tests PASS (existing + new)

**Step 6: Commit**

```bash
git add tools/transfer_cli.py tools/test_test_status.py
git commit -m "$(cat <<'EOF'
feat(tools): add test-status CLI command for Stage 4 error classification (BC-1..4, BC-12)

- Parse go build/test output from stdin
- Classify errors: compilation, test_failure, infrastructure
- Infrastructure errors take precedence (broken environment)
- JSON output with error_class, error_count, errors[]
- Exit 0 = clean, exit 1 = errors classified

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Create Stage 4 prompt template

**Contracts Implemented:** BC-5, BC-6, BC-7, BC-8, BC-9, BC-10, BC-11
**Variant:** Prompt Template

**Files:**
- Create: `prompts/test.md`

**Step 1: Author prompt template**

Context: Stage 4 drives the build-test-retry loop. It reads `stage3_output.json` for file paths, runs `go build` and `go test` in the submodule, uses `test-status` to classify errors, and implements structured retry logic with loop detection.

Complete prompt content in Appendix (Section K).

**Step 2: Verify structural completeness**

Check that the prompt contains all 4 required sections:
- [x] Prerequisites: checks `workspace/stage3_output.json` exists and is schema-valid, checks generated files exist on disk
- [x] Validation steps: `go build`, `go test`, `go vet`
- [x] Halt conditions: table with 11 conditions mapped to halt reasons
- [x] Expected outputs: build/test pass confirmation, escalation.json on halt

**Step 3: Verify predecessor artifact checks**

The prompt must instruct the LLM to validate predecessor artifacts before reading them:
- `workspace/stage3_output.json` — file existence + schema validation + semantic check (scorer file exists on disk)

**Step 4: Commit**

```bash
git add prompts/test.md
git commit -m "$(cat <<'EOF'
docs(prompts): add Stage 4 test prompt template (BC-5..11)

- Build + test with structured retry logic (max 3/class, 5 total)
- Error classification via test-status CLI command
- Identical consecutive error detection → immediate halt
- Non-consecutive duplicate detection (3x any) → halt
- Infrastructure errors never retried
- Escalation artifact on halt with Stage 4 halt_reason

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update orchestrator prompt with Stage 4

**Contracts Implemented:** BC-9 (prerequisite enforcement via orchestrator)

**Files:**
- Modify: `prompts/transfer.md`

**Step 1: Add Stage 4 section**

Context: Replace the placeholder `*Defined in PR4.*` with the actual Stage 4 section, following the same pattern as Stages 1-3 (between-stage validation).

Replace the existing Stage 4 placeholder in `prompts/transfer.md` with:

```markdown
### Stage 4: Test

**Prompt:** `prompts/test.md`

Follow the Stage 4 prompt to build and test the generated scorer plugin.

**Between-stage validation:**

\```bash
# Verify Stage 3 output exists and generated files present
test -f workspace/stage3_output.json || { echo "HALT: Stage 3 output missing"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json || { echo "HALT: Stage 3 schema validation failed"; exit 1; }

# Verify generated scorer file exists
SCORER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['scorer_file'])")
test -f "$SCORER_FILE" || { echo "HALT: generated scorer file missing: $SCORER_FILE"; exit 1; }

# Final build + vet verification
cd llm-d-inference-scheduler && go build ./... && go vet ./... && cd .. || { echo "HALT: Stage 4 build/vet verification failed"; exit 1; }
\```

**HALT if any validation fails.** Do not proceed to Stage 5.
```

Also update the Pipeline Overview table to change Stage 4 from `*Defined in PR4*` to `prompts/test.md` with correct artifacts.

**Step 2: Verify orchestrator still follows sequential stage pattern**

Manual check: Stages 1 → 2 → 3 → 4 each have between-stage validation blocks that check predecessor output.

**Step 3: Commit**

```bash
git add prompts/transfer.md
git commit -m "$(cat <<'EOF'
docs(prompts): add Stage 4 section to orchestrator prompt (BC-9)

- Replace placeholder with full Stage 4 section
- Between-stage validation: stage3_output.json + scorer file exists + go build
- Update pipeline overview table with Stage 4 prompt reference

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Update documentation

**Contracts Implemented:** (documentation — no specific BC)

**Files:**
- Modify: `docs/transfer/README.md`
- Modify: `CLAUDE.md`

**Step 1: Update README.md**

Add PR4 deliverables section to `docs/transfer/README.md` after the existing PR3 section:

```markdown
### PR4 Deliverables

- **Stage 4 prompt:** `prompts/test.md` — build + test with retry logic, error classification, halt conditions
- **test-status CLI command:** `tools/transfer_cli.py test-status` — classifies `go build`/`go test` output into compilation, test_failure, infrastructure
- **Escalation schema update:** Stage 4 halt reasons added to `tools/schemas/escalation.schema.json`

**PR4 obligations for downstream PRs:**
1. **Stage 4 success state:** Stage 4 success means `go build ./...` and `go test ./...` pass in the llm-d-inference-scheduler submodule. There is no `stage4_output.json` — PR5 reads generated code paths from `stage3_output.json`.
```

**Step 2: Update CLAUDE.md pipeline status**

Change PR4 row from "Not started" to "Complete".

**Step 3: Commit**

```bash
git add docs/transfer/README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: update README and CLAUDE.md for PR4 deliverables

- Add PR4 deliverables section to docs/transfer/README.md
- Update pipeline status table: PR4 → Complete

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 (compilation classify) | Task 2 | Unit (Python) | `pytest tools/test_test_status.py::TestTestStatusCompilation` |
| BC-2 (test failure classify) | Task 2 | Unit (Python) | `pytest tools/test_test_status.py::TestTestStatusTestFailure` |
| BC-3 (infrastructure classify) | Task 2 | Unit (Python) | `pytest tools/test_test_status.py::TestTestStatusInfrastructure` |
| BC-4 (valid JSON output) | Task 2 | Unit (Python) | `pytest tools/test_test_status.py::TestTestStatusCleanOutput` |
| BC-5 (prompt sections) | Task 3 | Structural check | Manual review of prompts/test.md |
| BC-6 (retry limits) | Task 3 | Structural check | Verify prompt text specifies 3/class, 5 total |
| BC-7 (identical consecutive) | Task 3 | Structural check | Verify prompt text specifies immediate halt |
| BC-8 (non-consecutive 3x) | Task 3 | Structural check | Verify prompt text maintains error signature list |
| BC-9 (no proceed w/o S3) | Task 3, 4 | Structural check | Verify prerequisite block in prompt and orchestrator |
| BC-10 (no retry infra) | Task 3 | Structural check | Verify prompt excludes infrastructure from retry |
| BC-11 (escalation artifact) | Task 1, 3 | Schema + structural | Escalation schema has Stage 4 values; prompt writes it |
| BC-12 (exit codes) | Task 2 | Unit (Python) | `pytest tools/test_test_status.py::TestTestStatusCleanOutput::test_clean_build_returns_zero` (exit 0), `TestTestStatusCompilation::test_classifies_compilation_error` (exit 1), `TestTestStatusExitCode2::test_oversized_input_returns_exit_2` (exit 2) |

**Cross-system invariants verified:**
- Schema chain: `stage3_output.json` → Stage 4 reads `scorer_file`, `test_file` paths
- CLI exit codes: test-status follows 0/1/2 convention
- Signal name consistency: N/A (Stage 4 doesn't reference signal names)
- Prompt completeness: 4 required sections verified

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Go compiler error format varies across versions | Low | Medium | Regex tested against Go 1.21+ standard format; `file.go:line:col:` is stable | Task 2 |
| `go test` output format varies with `-v` flag | Medium | Low | Test-status handles both verbose and non-verbose; `--- FAIL:` pattern is consistent | Task 2 |
| Prompt retry logic ambiguous to operator | Medium | Medium | Explicit step numbering, counter table, worked examples in prompt | Task 3 |
| `stage3_output.json` schema changes in PR3 rework | Low | High | Schema validated at Stage 4 start; `register_file` pattern verified against actual schema | Task 3 |

---

## Part 3: Quality Assurance

### J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [x] `stage3_output.schema.json` pattern for `register_file` is `^llm-d-inference-scheduler/pkg/plugins/register\\.go$` — confirmed in schema
- [x] Submodule commits: inference-sim `aa4bbb7`, llm-d-inference-scheduler `091312c` — confirmed via `git submodule status`
- [x] No stale API references — PR4 does not reference submodule APIs directly (only runs `go build`/`go test`)

**Dimension 2: Schema chain integrity**
- [x] Stage 3 output → Stage 4 input: `scorer_file`, `test_file`, `register_file`, `scorer_type` all present in schema
- [x] Escalation schema extended with Stage 4 values
- [x] No new workspace artifacts produced (Stage 4 success is build+test pass)

**Dimension 3: Prompt completeness**
- [x] `prompts/test.md` has: Prerequisites, Validation steps, Halt conditions, Expected outputs
- [x] Predecessor artifact check included (stage3_output.json validated before use)

**Dimension 4: CLI contract**
- [x] `test-status` outputs JSON matching documented schema
- [x] Exit codes: 0 = no errors, 1 = errors classified, 2 = CLI infrastructure error (try/except + stdin size guard)
- [x] Error messages are actionable (include file paths, line numbers)

**Dimension 5: Artifact consistency**
- [x] Halt reason names consistent between escalation schema and prompt template
- [x] `stage3_output.json` field names match between schema and prompt usage
- [x] File paths in prompt match actual submodule structure

**Dimension 6: Dead artifact prevention**
- [x] `prompts/test.md` → consumed by pipeline operator via orchestrator
- [x] `test-status` command → consumed by prompt template
- [x] Escalation schema Stage 4 values → consumed by prompt halt conditions
- [x] `test_test_status.py` → consumed by CI/verification gate

**Additional checks:**
- [x] PR category: Pipeline Stage
- [x] Verification gate: `python -m pytest tools/ -v` + manual prompt review
- [x] No feature creep beyond macro plan scope
- [x] Deviation log reviewed — all deviations justified
- [x] Each task produces working, verifiable output
- [x] Task dependencies ordered correctly (schema → CLI → prompt → orchestrator → docs)
- [x] All contracts mapped to tasks

---

## Appendix: File-Level Implementation Details

### K.1: `prompts/test.md` — Stage 4 Prompt Template

```markdown
---
stage: 4
version: "1.0"
pipeline_commit: "set-at-runtime"
description: "Stage 4 — Build and test generated scorer plugin with retry logic"
---

# Stage 4: Test

Build and test the generated scorer plugin in the llm-d-inference-scheduler
submodule. This stage runs `go build`, `go vet`, and `go test` with structured
retry logic: errors are classified, retries are tracked per class, and the stage
halts when retry limits are exceeded or loop detection triggers.

## Prerequisites

Verify Stage 3 output exists and is valid. **HALT if any check fails.**

```bash
# Stage 3 output artifact: exists + schema valid
test -f workspace/stage3_output.json || { echo "HALT: missing stage3_output.json"; exit 1; }
.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json || { echo "HALT: stage3_output.json schema validation failed"; exit 1; }

# Read file paths from stage3_output.json
SCORER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['scorer_file'])")
TEST_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['test_file'])")
REGISTER_FILE=$(.venv/bin/python -c "import json; print(json.load(open('workspace/stage3_output.json'))['register_file'])")

# Verify generated files exist on disk
test -f "$SCORER_FILE" || { echo "HALT: scorer file missing: $SCORER_FILE"; exit 1; }
test -f "$TEST_FILE" || { echo "HALT: test file missing: $TEST_FILE"; exit 1; }
test -f "$REGISTER_FILE" || { echo "HALT: register file missing: $REGISTER_FILE"; exit 1; }
```

On HALT, write `workspace/escalation.json`:
- Missing stage3_output.json → `"missing_stage3_output"`
- Schema validation failed → `"stage3_schema_validation_failed"`
- Scorer file missing → `"scorer_file_missing"`
- Test file missing → `"test_file_missing"`
- Register file missing → `"register_file_missing"`

**Important:** Steps 1 and 2 run `go build ./...` and `go vet ./...` on the entire
submodule. If a pre-existing build or vet failure exists in an unrelated package,
Stage 4 will enter the retry loop even though the generated code is correct. Before
running Stage 4, verify the submodule builds cleanly by running
`cd llm-d-inference-scheduler && go build ./... && go vet ./... && cd ..` and confirming
exit code 0. If it fails, resolve pre-existing issues first. If Stage 4 halts with
`build_compilation_failure` and the errors reference files NOT listed in
`stage3_output.json`, this indicates a pre-existing submodule issue, not a generated
code problem.

## Stale Artifact Guard

Remove any prior escalation artifact from Stage 4 (but preserve Stage 3's if present).

```bash
# Only remove escalation.json if it was written by Stage 4
.venv/bin/python -c "
import json, os
esc = 'workspace/escalation.json'
if os.path.isfile(esc):
    try:
        d = json.load(open(esc))
        if d.get('stage') == 4:
            os.remove(esc)
            print('Removed stale Stage 4 escalation artifact')
    except (json.JSONDecodeError, KeyError):
        pass
"
```

## Retry State

Initialize these counters at the start of Stage 4. Track them across all retry
attempts.

| Counter | Initial | Halt threshold | Action on limit |
|---------|---------|----------------|-----------------|
| `retries_compilation` | 0 | >= 4 (3 retries done) | HALT: `build_compilation_failure` |
| `retries_test_failure` | 0 | >= 4 (3 retries done) | HALT: `test_failure_limit_exceeded` |
| `retries_total` | 0 | >= 6 (5 retries done) | HALT: `total_retry_limit_exceeded` |
| `error_signatures` | [] | 3 occurrences of same signature | HALT: `oscillating_errors` |
| `last_error_signature` | null | same as current → immediate halt | HALT: `identical_consecutive_errors` |

**Error signature** = `(error_class, first_error_message)` where `first_error_message`
is the `message` field of the first entry in `test-status` output `errors[]`.
If `errors[]` is empty (unrecognized output format), use the first non-empty line of
the raw Go output as `first_error_message`. If raw output is also empty, use the
sentinel string `"<unclassified>"`.

## Step 1: Run Go Build

```bash
cd llm-d-inference-scheduler
set -o pipefail
go build ./... 2>&1 | tee /tmp/stage4_build_output.txt
# Portable: bash uses PIPESTATUS (0-indexed), zsh uses pipestatus (1-indexed)
BUILD_EXIT=${PIPESTATUS[0]:-${pipestatus[1]}}
cd ..
```

If `BUILD_EXIT == 0`, proceed to Step 2.

If `BUILD_EXIT != 0`, classify the error:

```bash
cat /tmp/stage4_build_output.txt | .venv/bin/python tools/transfer_cli.py test-status | tee /tmp/stage4_build_status.json
```

Read the JSON output from `/tmp/stage4_build_status.json`:
- If `error_class == "infrastructure"`: **HALT immediately** (do not retry). Write escalation.json with `halt_reason: "infrastructure_error_stage4"`.
- If `error_class == "compilation"`: proceed to **Step 4: Retry**.
- Otherwise (including `error_class == "none"`): classify as compilation error and proceed to **Step 4: Retry**.

## Step 2: Run Go Vet

```bash
cd llm-d-inference-scheduler
set -o pipefail
go vet ./... 2>&1 | tee /tmp/stage4_vet_output.txt
# Portable: bash uses PIPESTATUS (0-indexed), zsh uses pipestatus (1-indexed)
VET_EXIT=${PIPESTATUS[0]:-${pipestatus[1]}}
cd ..
```

If `VET_EXIT == 0`, proceed to Step 3.

If `VET_EXIT != 0`, classify the error:

```bash
cat /tmp/stage4_vet_output.txt | .venv/bin/python tools/transfer_cli.py test-status | tee /tmp/stage4_vet_status.json
```

Read the JSON output from `/tmp/stage4_vet_status.json`:
- If `error_class == "infrastructure"`: **HALT immediately**. Write escalation.json with `halt_reason: "infrastructure_error_stage4"`.
- Otherwise: classify as compilation error and proceed to **Step 4: Retry**.

## Step 3: Run Go Test

Run only the scorer package tests (not the entire repo — to avoid unrelated failures):

```bash
cd llm-d-inference-scheduler
set -o pipefail
go test -timeout 10m ./pkg/plugins/scorer/... -v 2>&1 | tee /tmp/stage4_test_output.txt
# Portable: bash uses PIPESTATUS (0-indexed), zsh uses pipestatus (1-indexed)
TEST_EXIT=${PIPESTATUS[0]:-${pipestatus[1]}}
cd ..
```

If `TEST_EXIT == 0`: **Stage 4 PASSES.** Proceed to Step 5 (Completion).

If `TEST_EXIT != 0`, classify the error:

```bash
cat /tmp/stage4_test_output.txt | .venv/bin/python tools/transfer_cli.py test-status | tee /tmp/stage4_test_status.json
```

Read the JSON output from `/tmp/stage4_test_status.json`:
- If `error_class == "infrastructure"`: **HALT immediately**. Write escalation.json with `halt_reason: "infrastructure_error_stage4"`.
- If `error_class == "test_failure"` or `error_class == "compilation"`: proceed to **Step 4: Retry**.
- Otherwise (including `error_class == "none"`): classify as test failure and proceed to **Step 4: Retry**.

## Step 4: Retry

Before retrying, perform these checks IN ORDER:

### 4a: Check infrastructure class

If the error class is `infrastructure`, **HALT immediately**. Infrastructure errors
are never retried — they indicate environment problems (module resolution, timeouts),
not generated code issues.

### 4b: Check identical consecutive errors

Compute the error signature: `(error_class, first_error_message)` from the saved
`/tmp/stage4_*_status.json` file. If `errors[]` is empty, use the first non-empty line
of the raw Go output file (`/tmp/stage4_*_output.txt`) as `first_error_message`, or
`"<unclassified>"` if raw output is also empty.

If this signature is identical to `last_error_signature`, **HALT immediately** with
`halt_reason: "identical_consecutive_errors"`. The LLM is making the same mistake.

### 4c: Check non-consecutive duplicate (oscillation detection)

Add the current error signature to `error_signatures[]`.

Count how many times this exact signature appears in the list. If the count reaches 3,
**HALT** with `halt_reason: "oscillating_errors"`. The LLM is oscillating between
error states.

### 4d: Check per-class retry limit

Increment the appropriate class counter based on the **effective** error class
(i.e., after applying the fallback classification from Steps 1/2/3):
- Compilation error (or `error_class == "none"` from Step 1 or Step 2): `retries_compilation += 1`
- Test failure (or `error_class == "none"` from Step 3): `retries_test_failure += 1`

If the counter is now **4 or more** (i.e., 3 retries already attempted), **HALT**:
- `retries_compilation >= 4` → `build_compilation_failure`
- `retries_test_failure >= 4` → `test_failure_limit_exceeded`

(The limit is 3 retries per class. The counter starts at 0 and is incremented before
this check, so the 4th increment means 3 retries have already been attempted.)

### 4e: Check total retry limit

Increment `retries_total += 1`.

If `retries_total` is now **6 or more** (i.e., 5 retries already attempted), **HALT**
with `halt_reason: "total_retry_limit_exceeded"`.

### 4f: Apply fix and retry

If all checks pass:

1. Update `last_error_signature` with the current signature.
2. Read the full error output from `/tmp/stage4_build_output.txt`,
   `/tmp/stage4_vet_output.txt`, or `/tmp/stage4_test_output.txt`
   (whichever corresponds to the failing step).
3. Identify which file(s) need changes from the error output (file paths are
   included in compilation errors; test names map to test files).
4. Apply the fix to the generated code. **Only modify files listed in
   `stage3_output.json`** (`scorer_file`, `test_file`, `register_file`).
   Do NOT modify other submodule files.
5. Return to **Step 1** (full rebuild — do NOT skip ahead to Step 3 even if
   only test files were changed, since compilation must be re-verified).

## Step 5: Completion

Stage 4 passes when:
- `go build ./...` exits 0
- `go vet ./...` exits 0
- `go test -timeout 10m ./pkg/plugins/scorer/... -v` exits 0

No output artifact is written. Stage 4 success is verified by the orchestrator's
between-stage validation (re-runs `go build` after Stage 4).

## Halt Conditions

| Condition | halt_reason | Retryable? | Action |
|-----------|-------------|------------|--------|
| Missing stage3_output.json | `missing_stage3_output` | No | Write escalation.json, HALT |
| stage3_output.json schema validation failed | `stage3_schema_validation_failed` | No | Write escalation.json, HALT |
| Scorer file not found on disk | `scorer_file_missing` | No | Write escalation.json, HALT |
| Test file not found on disk | `test_file_missing` | No | Write escalation.json, HALT |
| Register file not found on disk | `register_file_missing` | No | Write escalation.json, HALT |
| Infrastructure error (module/timeout) | `infrastructure_error_stage4` | No | Write escalation.json, HALT |
| Compilation retries >= 4 (3 retries done) | `build_compilation_failure` | No | Write escalation.json, HALT |
| Test failure retries >= 4 (3 retries done) | `test_failure_limit_exceeded` | No | Write escalation.json, HALT |
| Total retries >= 6 (5 retries done) | `total_retry_limit_exceeded` | No | Write escalation.json, HALT |
| Identical consecutive errors | `identical_consecutive_errors` | No | Write escalation.json, HALT |
| Same error signature 3 times | `oscillating_errors` | No | Write escalation.json, HALT |

On any halt, write `workspace/escalation.json`:

```json
{
  "stage": 4,
  "halt_reason": "<halt_reason from table above>",
  "details": "<human-readable description including: last error output, retry counts, error class, and recommended next steps>"
}
```

**Recommended next steps by halt reason:**
- `build_compilation_failure`: The LLM could not fix compilation errors in 3 attempts. Manually review the generated code against the scorer template. Common causes: missing imports, incorrect type conversions, API mismatch with submodule HEAD.
- `test_failure_limit_exceeded`: Tests fail consistently. Manually inspect the test expectations against the evolved algorithm logic. Common causes: incorrect normalization, wrong scoring formula translation.
- `infrastructure_error_stage4`: The Go build environment is broken. Check: `go env`, `go mod download` in the submodule, network access to module proxy.
- `identical_consecutive_errors` / `oscillating_errors`: The LLM is stuck in a loop. The fix attempt is not addressing the root cause. Manually review the error and the attempted fix.
- `total_retry_limit_exceeded`: Too many errors of different types. The generated code likely has fundamental issues. Consider re-running Stage 3 (regenerate).

## Expected Outputs

**On success:**
- `go build`, `go vet`, `go test` all pass in llm-d-inference-scheduler
- No workspace artifacts written (success is implicit)

**On halt:**
- `workspace/escalation.json` with Stage 4 halt reason and details
```

### K.2: `tools/schemas/escalation.schema.json` changes

Add these values to the `halt_reason` enum array:

```json
"build_compilation_failure",
"test_failure_limit_exceeded",
"total_retry_limit_exceeded",
"identical_consecutive_errors",
"oscillating_errors",
"infrastructure_error_stage4",
"missing_stage3_output",
"stage3_schema_validation_failed",
"scorer_file_missing",
"test_file_missing",
"register_file_missing"
```

Update the `description` field on `halt_reason` to append:
```
Stage 4 halt reasons: 'build_compilation_failure' (go build failed after 3 retries), 'test_failure_limit_exceeded' (go test failed after 3 retries), 'total_retry_limit_exceeded' (5 total retries across all classes), 'identical_consecutive_errors' (same error in consecutive retries), 'oscillating_errors' (same error signature 3 times across retries), 'infrastructure_error_stage4' (module/timeout/environment errors), 'missing_stage3_output' (stage3_output.json absent), 'stage3_schema_validation_failed' (stage3_output.json present but fails schema validation), 'scorer_file_missing' (generated scorer file not on disk), 'test_file_missing' (generated test file not on disk), 'register_file_missing' (generated register file not on disk).
```

### K.3: `prompts/transfer.md` Stage 4 section

Replace the placeholder at line 127 (`*Defined in PR4.* Stage 4 drives build + test with retry logic.`) with:

```markdown
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
cd llm-d-inference-scheduler && go build ./... && go vet ./... && cd .. || { echo "HALT: Stage 4 build/vet verification failed"; exit 1; }
```

**HALT if any validation fails.** Do not proceed to Stage 5.
```

Also update the Pipeline Overview table row for Stage 4:

```markdown
| 4     | Test      | `prompts/test.md`     | `workspace/stage3_output.json`               | build + test pass (no artifact)    |
```

### K.4: `docs/transfer/README.md` PR4 section

Append after the existing PR3 deliverables section:

```markdown
### PR4 Deliverables

- **Stage 4 prompt:** `prompts/test.md` — build + test with structured retry logic, error classification, halt conditions
- **test-status CLI command:** `tools/transfer_cli.py test-status` — classifies `go build`/`go test` output into compilation, test_failure, infrastructure error classes
- **Escalation schema update:** Stage 4 halt reasons added to `tools/schemas/escalation.schema.json`

**PR4 obligations for downstream PRs:**
1. **Stage 4 success state:** Stage 4 success means `go build ./...`, `go vet ./...`, and `go test ./pkg/plugins/scorer/... -v` all pass in the llm-d-inference-scheduler submodule. There is no `stage4_output.json` — PR5 reads generated code paths from `stage3_output.json`.
2. **Retry state not persisted:** Retry counters live only in the interactive session. If Stage 4 halts and the operator restarts, counters reset to zero.
```
