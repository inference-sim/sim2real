# PR3: Prompt Templates (Stages 1-3) + Go Harness Skeleton — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the first three pipeline stage prompt templates and the Go test harness skeleton for equivalence testing.

**The problem today:** The pipeline has input artifacts (PR1: mapping, CLI extract, schemas) and a structural reference (PR2: scorer template), but no way to drive the transfer stages or test equivalence between simulation and production scoring.

**What this PR adds:**
1. Prompt templates for Stages 1-3 (extract, translate, generate) plus the top-level orchestrator
2. Go test harness skeleton that imports inference-sim and validates algorithm loading
3. `signal_coverage.schema.json` for Stage 2 output validation
4. `tools/check_scorer_template.sh` for automated scorer template staleness detection

**Why this matters:** Without prompt templates, the pipeline stages cannot be driven interactively. Without the Go harness, equivalence testing (PR5) has no foundation. This PR completes Milestone 2 — the pipeline can run through code generation + testing.

**Architecture:** Mixed-language PR. Prompt templates (Markdown) guide interactive Claude Code sessions. Go harness (`tools/harness/`) imports inference-sim as a dependency to run the evolved algorithm and compare scores. A Bash CI script validates scorer template freshness. Python schemas extend PR1's validation infrastructure.

**PR Category:** Pipeline Stage (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR3 in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Behavioral Contracts:** See Part 1, Section B below

---

## Part 1: Design Validation

### A) Executive Summary

PR3 builds the pipeline's interactive driving layer (prompt templates) and the testing foundation (Go harness). It sits between PR2 (scorer template) and PR4 (Stage 4 test retry logic) in the dependency chain.

As a Pipeline Stage PR, it gets 4 review perspectives: contracts, artifacts, prompts, plan. Single convergence round — re-run only if CRITICAL findings.

**Phase 0 Audit Results:**
- Submodules verified: inference-sim at `aa4bbb7`, llm-d-inference-scheduler at `091312c` (matches mapping artifact pin)
- PR1 artifacts confirmed: mapping artifact, CLI extract, algorithm_summary.schema.json, schema_validator.py
- PR2 artifacts confirmed: scorer_template.go.md, stage3_output.schema.json, escalation.schema.json
- DEVIATION D-1: Macro plan harness types use incorrect sim types (see Deviation Log)
- DEVIATION D-2: No `prompts/` directory exists yet (expected — PR3 creates it)
- R6 ACKNOWLEDGED: Shim approach confirmed in docs/transfer/README.md. `tools/harness/evolved_scorer.go` is a named PR3 deliverable per README § R6 (implements `scheduling.Scorer` interface). See D-5 for scope note.

### B) Behavioral Contracts

#### Positive Contracts

**BC-1: Go harness compiles against inference-sim**
- GIVEN the inference-sim submodule at commit `aa4bbb7`
- WHEN `go build ./tools/harness/...` is run from repo root
- THEN the build succeeds with exit code 0
- MECHANISM: `go.work` at repo root declares `use ./tools/harness`, enabling repo-root-relative commands. tools/harness/go.mod declares `require github.com/inference-sim/inference-sim` pointing at the submodule

**BC-2: Trivial test case passes**
- GIVEN the Go harness with a trivial scoring algorithm
- WHEN `go test ./tools/harness/... -v` is run
- THEN `TestEquivalenceTrivial` passes — RunTuples returns results without error, scores are non-zero for at least one endpoint
- MECHANISM: A single TestTuple with 2 endpoints and different queue depths is defined; test constructs `trivialAlgorithm{}` directly (not via LoadAlgorithm — LoadAlgorithm's happy path is tested separately in TestStaleHashAbortsParsing's matching-hash subcase)

**BC-3: signal_coverage.schema.json validates Stage 2 output**
- GIVEN a valid signal_coverage.json matching macro plan fields
- WHEN `python tools/transfer_cli.py validate-schema workspace/signal_coverage.json` is run
- THEN validation passes (exit 0)
- MECHANISM: Schema requires `signals[]` (sim_name, prod_name, prod_access_path, fidelity_rating, staleness_window_ms, mapped), `unmapped_signals[]`, `commit_hash`, `coverage_complete`

**BC-4: Scorer template compilation check works**
- GIVEN the scorer template at docs/transfer/scorer_template.go.md and the llm-d-inference-scheduler submodule
- WHEN `bash tools/check_scorer_template.sh` is run
- THEN the script extracts Go code blocks, compiles them against the submodule, and reports pass/fail
- MECHANISM: Shell script uses sed/awk to extract fenced Go blocks, writes to temp files, runs `go build`

#### Prompt Template Contracts

**BC-5: Each prompt template has 4 required sections**
- GIVEN any prompt template in `prompts/`
- WHEN reviewed for structural completeness
- THEN it MUST contain: (1) Prerequisites, (2) Validation steps, (3) Halt conditions, (4) Expected outputs
- MECHANISM: Structural review during PR review

**BC-6: Orchestrator prompt sequences stages correctly**
- GIVEN `prompts/transfer.md`
- WHEN the operator follows it
- THEN stages execute in order 1→2→3, each stage checks predecessor output exists
- MECHANISM: Prompt text specifies prerequisite artifacts per stage

**BC-7: Stage 1 prompt drives extract + scope validation**
- GIVEN `prompts/extract.md`
- WHEN followed in a Claude Code session
- THEN it runs `transfer_cli.py extract routing/`, validates the output, and halts on failure
- MECHANISM: Prompt text includes exact CLI commands, expected output format, and halt conditions

**BC-8: Stage 2 prompt drives signal mapping**
- GIVEN `prompts/translate.md` and `workspace/algorithm_summary.json`
- WHEN followed in a Claude Code session
- THEN it produces `workspace/signal_coverage.json` with all signals mapped, staleness check passes
- MECHANISM: Prompt reads algorithm_summary.json signals[], maps each using blis_to_llmd_mapping.md

**BC-9: Stage 3 prompt drives code generation**
- GIVEN `prompts/generate.md`, algorithm_summary.json, signal_coverage.json, and scorer_template.go.md
- WHEN followed in a Claude Code session
- THEN it generates scorer code in the submodule, writes stage3_output.json, and validates prerequisites
- MECHANISM: Prompt references scorer template, mapping artifact, and includes all halt conditions from scorer_template.go.md header

#### Negative Contracts

**BC-10: Prompts MUST NOT skip prerequisite checks**
- GIVEN any stage N prompt template
- WHEN prerequisite artifacts are missing or invalid
- THEN the prompt instructs HALT — it MUST NOT proceed with stale or absent artifacts
- MECHANISM: Each prompt starts with existence + schema validation checks

**BC-11: Stale EVOLVE-BLOCK hash detected**
- GIVEN algorithm_summary.json with a content hash and a modified EVOLVE-BLOCK source
- WHEN the harness attempts to load the algorithm
- THEN loading fails with an explicit "content hash mismatch" error
- MECHANISM: LoadAlgorithm recomputes SHA-256 over the EVOLVE-BLOCK line range and compares to stored hash

#### Error Handling Contracts

**BC-12: Go harness RunTuples captures per-tuple errors**
- GIVEN a TestTuple that causes a panic in the scoring function
- WHEN RunTuples executes it
- THEN the Result has `Error != nil` and other tuples still execute
- MECHANISM: RunTuples uses recover() per tuple

### C) Component Interaction

```
Orchestrator prompt (prompts/transfer.md)
    │
    ├─► Stage 1 prompt (prompts/extract.md)
    │   reads: routing/best_program.py, routing/best_program_info.json
    │   calls: transfer_cli.py extract routing/
    │   writes: workspace/algorithm_summary.json
    │
    ├─► Stage 2 prompt (prompts/translate.md)
    │   reads: workspace/algorithm_summary.json, docs/transfer/blis_to_llmd_mapping.md
    │   writes: workspace/signal_coverage.json
    │
    └─► Stage 3 prompt (prompts/generate.md)
        reads: workspace/algorithm_summary.json, workspace/signal_coverage.json,
               docs/transfer/scorer_template.go.md, docs/transfer/blis_to_llmd_mapping.md
        writes: llm-d-inference-scheduler/pkg/plugins/scorer/<scorer>.go (branch)
                workspace/stage3_output.json

Go test harness (tools/harness/)
    imports: github.com/inference-sim/inference-sim/sim
    reads: routing/best_program.py (EVOLVE-BLOCK), workspace/algorithm_summary.json
    tests: Algorithm loading, RunTuples, hash verification
    extended by: PR5 (Suite A/B/C logic)
```

**Workspace artifacts:**
- Consumed: `algorithm_summary.json` (writer: Stage 1/PR1 extract)
- Produced: `signal_coverage.json` (writer: Stage 2, readers: Stage 3, Stage 5)

**Dead artifact check:**
| File | Consumer |
|------|----------|
| prompts/transfer.md | Pipeline runtime (every transfer) |
| prompts/extract.md | Pipeline runtime (Stage 1) |
| prompts/translate.md | Pipeline runtime (Stage 2) |
| prompts/generate.md | Pipeline runtime (Stage 3) |
| tools/harness/*.go | Go tests (this PR), PR5 Suite logic |
| tools/schemas/signal_coverage.schema.json | validate-schema CLI (Stage 2) |
| tools/check_scorer_template.sh | CI (this PR onward) |

### D) Deviation Log

| # | Macro Plan Says | Micro Plan Does | Reason |
|---|-----------------|-----------------|--------|
| D-1 | TestTuple uses `Endpoints []sim.RouterState` and `Expected sim.RoutingSnapshot` | TestTuple uses `State sim.RouterState` (contains snapshots) and expected scores as `map[string]float64` | CORRECTION: RouterState already contains `Snapshots []RoutingSnapshot`. The macro plan's `Endpoints` field redundantly wraps them. RoutingDecision (not RoutingSnapshot) is the scoring output. |
| D-2 | Macro plan specifies `Algorithm interface` with `Route(req, endpoints) RoutingSnapshot` | Harness uses `Algorithm interface` with `Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision` | CORRECTION: Must match inference-sim's actual `RoutingPolicy.Route()` signature. Returns RoutingDecision (which contains Scores map), not RoutingSnapshot. |
| D-3 | `LoadAlgorithm(path string)` takes file path | `LoadAlgorithm(summaryPath, repoRoot string)` takes algorithm summary path + repo root | ADDITION: Need summary path to read content hash; need repo root to resolve relative EVOLVE-BLOCK source path. |
| D-4 | Macro plan lists prompt templates only | PR3 also fulfills cross-PR contracts from README.md (stale hash test, KVUtilization normalization instruction, etc.) | ADDITION: README § Cross-PR Contracts requires these; macro plan doesn't list them explicitly. |
| D-5 | README § R6 requires `evolved_scorer.go` implementing `scheduling.Scorer` (Score method) | Plan's harness uses `Algorithm` interface matching `RoutingPolicy.Route()` (sim-side signature). `evolved_scorer.go` is added as a PR3 deliverable implementing the production-side `scheduling.Scorer` interface as a structural shim wrapping the harness Algorithm. See Appendix K-9 for complete implementation and interface contract. | ADDITION: Two distinct interfaces needed — `Algorithm` for sim-side equivalence testing, `scheduling.Scorer` for production-side integration. PR3 delivers both interface implementations as structural shims. **R6 criteria deferred to PR5:** #2 (accept all signals from algorithm_summary.json), #3 (reproduce scoring/penalty logic), #5 (pass unit tests comparing shim output against simulation output). PR3's `Score()` returns uniform 0.5 placeholder scores; PR5 wires `Algorithm.Route()` into the production scorer path. |
| D-6 | Task ordering follows TDD (test-first) | Tasks 2-3 are ordered implementation-first (harness.go then tests) | DEVIATION: Go module initialization and type definitions must exist before tests can compile. Test file (Task 3) immediately follows. Implementing agent SHOULD write a minimal failing test stub in Task 2 Step 2 before writing full implementation to preserve red-green signal. |

### E) Review Guide

1. **THE TRICKY PART:** The Go harness's `LoadAlgorithm` must correctly parse a Python file containing embedded Go code, extract the EVOLVE-BLOCK, and verify the content hash. The hash computation must exactly match `transfer_cli.py`'s algorithm (join lines with `\n`, no trailing newline, SHA-256).

2. **WHAT TO SCRUTINIZE:** BC-11 (stale hash detection) — this is the cross-PR contract most likely to have subtle bugs. Verify the Go hash computation matches the Python implementation. Also scrutinize the `signal_coverage.schema.json` field definitions against the macro plan's workspace artifact table.

3. **WHAT'S SAFE TO SKIM:** Prompt templates for Stages 1-2 are relatively mechanical (they drive existing CLI commands). The `check_scorer_template.sh` is straightforward.

4. **KNOWN DEBT:** (a) Prompt templates can only be fully tested by running an actual transfer session — PR3 validates structure, not LLM behavior. (b) The Go harness trivial test uses a hardcoded simple scorer, not the actual evolved algorithm. Full equivalence testing is PR5. (c) `TestStaleHashAbortsParsing` only tests Go→Go hash consistency. A cross-language test (`TestCrossLanguageHashConsistency`, Task 3 Step 2b) verifies that Go's hash matches `transfer_cli.py extract`'s hash for the same input. This test is skipped if `.venv/bin/python` is not available.

---

## Part 2: Executable Implementation

### F) Implementation Overview

**Files to create:**
- `go.work` — Go workspace file enabling repo-root builds of nested `tools/harness` module
- `tools/schemas/signal_coverage.schema.json` — JSON Schema for Stage 2 output
- `tools/harness/go.mod` — Go module definition
- `tools/harness/go.sum` — Go dependency checksums
- `tools/harness/harness.go` — Types (TestTuple, Result, Algorithm) + LoadAlgorithm + RunTuples
- `tools/harness/evolved_scorer.go` — `scheduling.Scorer` shim (R6 deliverable, see D-5)
- `tools/harness/harness_test.go` — Trivial test case + stale hash test + KVUtilization normalization test
- `tools/check_scorer_template.sh` — CI compilation check script
- `prompts/transfer.md` — Top-level orchestrator
- `prompts/extract.md` — Stage 1
- `prompts/translate.md` — Stage 2
- `prompts/generate.md` — Stage 3

**Files to modify:**
- `docs/transfer/README.md` — Update pipeline status, add PR3 notes, amend R6 shim acceptance criteria (see D-5 deferrals)
- `CLAUDE.md` — Update pipeline status table
- `tools/schemas/escalation.schema.json` — Extend `halt_reason` enum with Stage 2 halt reasons (see K-7 step 12)
- `.github/workflows/test.yml` — Add `go test ./tools/harness/... -v` and `bash tools/check_scorer_template.sh` steps so Section C's CI consumer claims are satisfied

**Key decisions:**
- Go harness uses `sim.RoutingDecision.Scores` (not RoutingSnapshot) as the comparison target
- Two interfaces: `Algorithm` (sim-side, matches `RoutingPolicy.Route()`) for equivalence testing, and `scheduling.Scorer` (production-side, `Score()` method) via `evolved_scorer.go` shim per README § R6
- LoadAlgorithm parses Python-embedded Go code to verify content hash, then provides a trivial scorer for PR3 (full evolved logic scorer deferred to PR5)
- Prompt templates reference workspace artifacts by exact path and include schema validation commands
- check_scorer_template.sh extracts code blocks between ` ```go ` and ` ``` ` fences

**Confirmation:** No dead artifacts — all files have documented consumers.

### G) Task Breakdown

---

### Task 1: signal_coverage.schema.json

**Contracts Implemented:** BC-3
**Variant:** Artifact

**Files:**
- Create: `tools/schemas/signal_coverage.schema.json`

**Step 1: Author schema**

Context: This schema validates the Stage 2 output artifact (`workspace/signal_coverage.json`). Fields derived from macro plan workspace artifact table: signals[] (sim_name, prod_name, fidelity_rating, staleness_window_ms, mapped), unmapped_signals[], commit_hash, coverage_complete.

See Appendix K-1 for complete schema content.

**Step 2: Validate cross-references**

Run: `.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json` with a synthetic valid signal_coverage.json to verify the schema works.

Create a minimal test fixture:
```json
{
  "signals": [{"sim_name": "QueueDepth", "prod_name": "WaitingQueueSize", "prod_access_path": "endpoint.GetMetrics().WaitingQueueSize", "fidelity_rating": "high", "staleness_window_ms": 0, "mapped": true}],
  "unmapped_signals": [],
  "commit_hash": "091312c333a50e94f5e60a2ca2926e8442eeffa9",
  "coverage_complete": true
}
```
`mkdir -p workspace` (required — `workspace/` is gitignored and may not exist on a fresh checkout), then write to `workspace/signal_coverage.json`, validate, then remove.

**Step 3: Verify no dead artifacts**
Consumer: Stage 2 prompt (prompts/translate.md) instructs `validate-schema workspace/signal_coverage.json`. Also consumed by Stage 3 and Stage 5.

**Step 4: Commit**
```bash
git add tools/schemas/signal_coverage.schema.json
git commit -m "$(cat <<'EOF'
feat(schemas): add signal_coverage.schema.json (BC-3)

JSON Schema for Stage 2 output artifact validation.
Fields: signals[], unmapped_signals[], commit_hash, coverage_complete.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Go harness module + types

**Contracts Implemented:** BC-1, BC-11
**Language:** Go

**Files:**
- Create: `tools/harness/go.mod`
- Create: `tools/harness/harness.go`

**Step 1: Initialize Go module**

Context: The harness must import inference-sim from the local submodule. Use a `replace` directive to point at the local path.

```bash
cd tools/harness
go mod init github.com/kalantar/sim2real/tools/harness
```

Then edit go.mod to add:
```
require (
    github.com/inference-sim/inference-sim v0.0.0
    sigs.k8s.io/gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a
)

replace github.com/inference-sim/inference-sim => ../../inference-sim
```

The `sigs.k8s.io/gateway-api-inference-extension` dependency is required by `evolved_scorer.go` (K-9) for the `scheduling.Scorer` and `plugin.TypedName` interfaces. The pseudo-version matches llm-d-inference-scheduler's go.mod and will be fetched from the Go module proxy — no `replace` directive is needed for this dependency.

**Do NOT run `go mod tidy` yet** — no .go files exist, so tidy would prune all dependencies. Tidy is deferred to Step 2c (after harness.go and evolved_scorer.go are written).

Then create `go.work` at the repo root so that Go commands like `go build ./tools/harness/...` work from the repo root (without `go.work`, Go's module resolution cannot find the nested module):
```
go 1.25.7

use ./tools/harness
```

**Step 2: Write harness.go**

Context: Defines the core types and functions. See Appendix K-2 for complete implementation.

Key types:
- `Algorithm` interface: `Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision`
- `TestTuple`: `Request sim.Request`, `State sim.RouterState`
- `Result`: `SimScores map[string]float64`, `ProdScores map[string]float64`, `Passed bool`, `Error error`, `ScoreDiffs map[string]float64`
- `LoadAlgorithm(summaryPath, repoRoot string) (Algorithm, error)` — reads algorithm_summary.json, verifies EVOLVE-BLOCK content hash, returns Algorithm wrapper
- `RunTuples(alg Algorithm, tuples []TestTuple) []Result` — executes each tuple with panic recovery
- Note: `TestEquivalence(t *testing.T)` is the PR5 suite entry point — defined in `harness_test.go` (not harness.go). See K-2 note and K-3.

**Step 2b: Write evolved_scorer.go**

Context: Implements `scheduling.Scorer` shim per README § R6 / D-5. Wraps the harness `Algorithm` to present it as a production-side scorer. See Appendix K-9 for complete implementation.

**Step 2c: Resolve dependencies**

Now that both harness.go and evolved_scorer.go exist with their import statements, run `go mod tidy` to resolve all transitive dependencies:

```bash
cd tools/harness
go mod tidy
```

**Important:** `go mod tidy` may bump the go.mod go directive to match transitive dependencies like `sigs.k8s.io/gateway-api-inference-extension`. After running tidy, run `go work sync` from the repo root to ensure `go.work`'s go directive is >= all member modules' go directives. If `go mod tidy` bumps the harness go.mod above `1.25.7`, update `go.work` to match.

**Step 3: Verify build**

Run: `go build ./tools/harness/...`
Expected: Build succeeds (exit 0).

**Step 4: Commit**
```bash
git add go.work tools/harness/go.mod tools/harness/go.sum tools/harness/harness.go tools/harness/evolved_scorer.go
git commit -m "$(cat <<'EOF'
feat(harness): Go test harness skeleton with types, LoadAlgorithm, and Scorer shim (BC-1, BC-11)

- Define Algorithm interface, TestTuple, Result types
- Implement LoadAlgorithm with EVOLVE-BLOCK hash verification
- Implement RunTuples with per-tuple panic recovery
- Import inference-sim via local submodule replace directive
- Add evolved_scorer.go: scheduling.Scorer shim wrapping Algorithm (R6)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Go harness tests

**Contracts Implemented:** BC-2, BC-11, BC-12
**Language:** Go

**Files:**
- Create: `tools/harness/harness_test.go`

**Step 1: Write tests**

Context: Five tests — (1) trivial test case with 2 endpoints, (2) stale hash detection, (3) RunTuples panic recovery, (4) KVUtilization normalization, (5) unknown-type signal rejection (cross-PR contract #3). See Appendix K-3 for complete test code.

Tests:
- `TestEquivalenceTrivial` — creates 2 endpoints with different queue depths, runs through RunTuples, asserts non-zero scores and no errors. This is the "trivial test case" from macro plan.
- `TestStaleHashAbortsParsing` — creates temp files with algorithm_summary.json and a modified EVOLVE-BLOCK source, calls LoadAlgorithm with original summary, asserts error contains "hash mismatch". Named to match README's `test_stale_hash_aborts_parsing` gate name for CI audit traceability. **(Cross-PR contract #1)** Note: this is a Go-only hash consistency test; it does not invoke `transfer_cli.py` as the README's description implies. The cross-language integration gap is acknowledged in Section E item (c) and addressed by F-17's cross-language hash test.
- `TestRunTuplesPanicRecovery` — provides an Algorithm that panics, asserts Result.Error is non-nil and other tuples succeed.
- `TestKVUtilizationNormalization` — verifies production KVCacheUsagePercent (0-100) is divided by 100 to match sim's [0,1] range. Includes boundary cases. **(Cross-PR contract #2)**
- `TestUnknownSignalTypeRejection` — creates an algorithm_summary.json with a signal of type "unknown", verifies that LoadAlgorithm (or a signal validation helper) either rejects it or flags it explicitly. **(Cross-PR contract #3)**

**Step 2: Run tests**

Run: `go test ./tools/harness/... -v`
Expected: All tests pass (plus TestEquivalence placeholder).

**Step 2b: Write cross-language hash integration test**

Context: Section E item (c) identifies a gap — Go's hash must match `transfer_cli.py extract`'s hash for the same input. Add `TestCrossLanguageHashConsistency`:
- Create a temp directory with a minimal EVOLVE-BLOCK source file
- Run `.venv/bin/python tools/transfer_cli.py extract` against it (using `exec.Command`)
- Parse the output `algorithm_summary.json` to get `evolve_block_content_hash`
- Recompute the hash using Go's `LoadAlgorithm` path (or directly via `sha256`)
- Assert both hashes match
- Skip the test with `t.Skip("requires Python venv")` if `.venv/bin/python` is not available

This closes the cross-language hash divergence risk documented in Section E(c) and protects BC-11 against edge cases (bare `\r` line endings, non-UTF-8 bytes).

**Step 3: Run full suite**

Run: `go test ./tools/harness/... -v && go build ./tools/harness/...`
Expected: All pass.

**Step 4: Commit**
```bash
git add tools/harness/harness_test.go
git commit -m "$(cat <<'EOF'
test(harness): add equivalence, hash, panic, and normalization tests (BC-2, BC-11, BC-12)

- TestEquivalenceTrivial: 2 endpoints, non-zero scores, no errors
- TestStaleHashAbortsParsing: cross-PR contract #1 — content hash mismatch detected
- TestRunTuplesPanicRecovery: per-tuple panic captured in Result.Error
- TestKVUtilizationNormalization: cross-PR contract #2 — KV divide-by-100

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: check_scorer_template.sh

**Contracts Implemented:** BC-4
**Language:** Bash

**Files:**
- Create: `tools/check_scorer_template.sh`

**Step 1: Write script**

Context: Extracts Go code blocks from `docs/transfer/scorer_template.go.md`, writes them to a temp directory, attempts `go build`. See Appendix K-4 for complete script.

Key behavior:
- Extracts code between ` ```go ` and ` ``` ` markers
- Creates a temp Go package with extracted code
- Runs `go build` in the llm-d-inference-scheduler module context
- Reports pass/fail with clear error messages
- Exits 0 on success, 1 on compilation failure, 2 on infrastructure error

**Step 2: Run script**

Run: `bash tools/check_scorer_template.sh`
Expected: PASS (scorer template was verified in PR2)

**Step 3: Commit**
```bash
git add tools/check_scorer_template.sh
git commit -m "$(cat <<'EOF'
feat(tools): add check_scorer_template.sh for CI staleness detection (BC-4)

Extracts Go code blocks from scorer_template.go.md, compiles against
llm-d-inference-scheduler submodule HEAD. Catches template staleness
when the submodule API changes.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: prompts/transfer.md (Orchestrator)

**Contracts Implemented:** BC-5, BC-6
**Variant:** Prompt Template

**Files:**
- Create: `prompts/transfer.md`

**Step 1: Author prompt template**

Context: Top-level orchestrator that sequences Stages 1→2→3→4→5→6. For PR3, only Stages 1-3 are implemented; Stages 4-6 are marked as "defined in PR4/PR5/PR6". See Appendix K-5 for prompt content outline.

Key content:
- Pipeline overview and stage summary
- Prerequisites: list of required artifacts (mapping, scorer template, algorithm inputs)
- Stage sequence with prerequisite checks between each
- Records `pipeline_commit` at start for drift detection
- Halt conditions: any stage failure halts the pipeline

**Step 2: Verify structural completeness**
- [ ] Prerequisites: Required artifacts listed
- [ ] Validation steps: Per-stage validation specified
- [ ] Halt conditions: Stage failure → halt
- [ ] Expected outputs: Per-stage outputs listed

**Step 3: Verify predecessor artifact checks**
The orchestrator checks for mapping artifact, scorer template, and routing inputs before starting.

**Step 4: Commit**
```bash
git add prompts/transfer.md
git commit -m "$(cat <<'EOF'
docs(prompts): add transfer.md orchestrator prompt (BC-5, BC-6)

Top-level pipeline orchestrator sequencing Stages 1-6.
Stages 1-3 fully specified; 4-6 marked for PR4/PR5/PR6.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: prompts/extract.md (Stage 1)

**Contracts Implemented:** BC-5, BC-7
**Variant:** Prompt Template

**Files:**
- Create: `prompts/extract.md`

**Step 1: Author prompt template**

Context: Drives Stage 1 — extraction of algorithm metadata from routing artifacts. Wraps `transfer_cli.py extract` with validation. See Appendix K-6 for prompt content outline.

Key content:
- Prerequisites: routing/best_program.py exists, routing/best_program_info.json exists
- Steps: (1) run `python tools/transfer_cli.py extract routing/`, (2) check exit code, (3) validate schema, (4) review signals and metrics
- Halt conditions: exit code != 0, schema validation failure, scope_validation_passed == false
- Expected output: workspace/algorithm_summary.json
- Cross-PR contract: Include KVUtilization normalization note review

**Step 2: Verify structural completeness**
- [ ] Prerequisites
- [ ] Validation steps
- [ ] Halt conditions
- [ ] Expected outputs

**Step 3: Commit**
```bash
git add prompts/extract.md
git commit -m "$(cat <<'EOF'
docs(prompts): add extract.md Stage 1 prompt (BC-5, BC-7)

Drives algorithm extraction via transfer_cli.py extract.
Includes prerequisite checks, schema validation, halt conditions.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: prompts/translate.md (Stage 2)

**Contracts Implemented:** BC-5, BC-8
**Variant:** Prompt Template

**Files:**
- Create: `prompts/translate.md`
- Modify: `tools/schemas/escalation.schema.json` — Extend `halt_reason` enum with Stage 2 values (see K-7 step 12)

**Step 1a: Extend escalation.schema.json halt_reason enum**

Before authoring the prompt, extend the `halt_reason` enum in `tools/schemas/escalation.schema.json` to include the 5 Stage 2 halt reasons required by K-7 step 12. Add the following values to the existing enum array: `"stale_submodule_commit"`, `"unmappable_signal"`, `"coverage_incomplete"`, `"unknown_signal_unresolved"`, `"pre_write_validation_failure"`. The resulting enum should be:
```json
["unverified_fields", "cache_hit_rate_unavailable", "missing_algorithm_summary", "out_of_scope_patterns", "missing_mapping_artifact", "evolve_block_hash_mismatch", "stale_submodule_commit", "unmappable_signal", "coverage_incomplete", "unknown_signal_unresolved", "pre_write_validation_failure"]
```
Also update the schema's `description` field to document the new Stage 2 variants.

**Step 1b: Author prompt template**

Context: Drives Stage 2 — mapping simulation signals to production equivalents. Reads algorithm_summary.json and blis_to_llmd_mapping.md, produces signal_coverage.json. See Appendix K-7 for prompt content outline.

Key content:
- Prerequisites: workspace/algorithm_summary.json exists and passes schema validation
- Steps: (1) read algorithm_summary.json signals[], (2) for each signal, look up production equivalent in mapping artifact, (3) check submodule commit hash freshness, (4) write signal_coverage.json, (5) validate schema
- Halt conditions: missing prerequisite, stale commit hash, unmappable signal
- Expected output: workspace/signal_coverage.json
- Includes normalization notes propagation from algorithm_summary.json
- CacheHitRate access path investigation steps (cross-PR contract #5)
- F-10 double-counting detection (cross-PR contract #4)

**Step 2: Verify structural completeness**
- [ ] Prerequisites
- [ ] Validation steps
- [ ] Halt conditions
- [ ] Expected outputs

**Step 3: Commit**
```bash
git add prompts/translate.md tools/schemas/escalation.schema.json
git commit -m "$(cat <<'EOF'
docs(prompts): add translate.md Stage 2 prompt + extend escalation schema (BC-5, BC-8)

Drives signal mapping from sim to production equivalents.
Includes staleness check, normalization propagation, F-10 guard.
Extends escalation.schema.json halt_reason enum with 5 Stage 2 values
(K-7 step 12): stale_submodule_commit, unmappable_signal,
coverage_incomplete, unknown_signal_unresolved, pre_write_validation_failure.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: prompts/generate.md (Stage 3)

**Contracts Implemented:** BC-5, BC-9
**Variant:** Prompt Template

**Files:**
- Create: `prompts/generate.md`
- Modify: `tools/schemas/escalation.schema.json` — Extend `halt_reason` enum with Stage 3 values (see K-8 item 10)

**Step 1a: Extend escalation.schema.json halt_reason enum**

Before authoring the prompt, extend the `halt_reason` enum in `tools/schemas/escalation.schema.json` to include the 7 Stage 3 halt reasons required by K-8 item 10. Add the following values to the existing enum array: `"missing_signal_coverage"`, `"stale_signal_coverage"`, `"unverified_field_threshold_not_met"`, `"cache_hit_rate_unavailable_stage3"`, `"post_write_validation_failure_stage3"`, `"pre_write_validation_failure_stage3"`, `"evolve_block_hash_mismatch_stage3"`. The resulting enum should be:
```json
["unverified_fields", "cache_hit_rate_unavailable", "missing_algorithm_summary", "out_of_scope_patterns", "missing_mapping_artifact", "evolve_block_hash_mismatch", "stale_submodule_commit", "unmappable_signal", "coverage_incomplete", "unknown_signal_unresolved", "pre_write_validation_failure", "missing_signal_coverage", "stale_signal_coverage", "unverified_field_threshold_not_met", "cache_hit_rate_unavailable_stage3", "post_write_validation_failure_stage3", "pre_write_validation_failure_stage3", "evolve_block_hash_mismatch_stage3"]
```
Also update the schema's `description` field to document the new Stage 3 variants.

**Step 1b: Author prompt template**

Context: Drives Stage 3 — LLM code generation of the production scorer. This is the most complex prompt. It references the scorer template, mapping artifact, algorithm summary, and signal coverage. See Appendix K-8 for prompt content outline.

Key content:
- Prerequisites: algorithm_summary.json, signal_coverage.json, scorer_template.go.md, blis_to_llmd_mapping.md all exist and valid
- EVOLVE-BLOCK content hash verification (BC-11 mechanism at Stage 3 level)
- UNVERIFIED field halt condition (from scorer template header)
- Code generation steps: (1) parse EVOLVE-BLOCK for scoring logic, (2) map signals to production fields, (3) generate scorer using template structure, (4) generate tests, (5) add registration
- Stage 3 output validation: no PLACEHOLDER markers, structural invariants, stage3_output.json
- Halt conditions: missing prerequisites, hash mismatch, UNVERIFIED field resolution below scorer_template.go.md HALT CONDITION threshold, CacheHitRate unavailable
- Escalation procedures with workspace/escalation.json
- Expected output: generated scorer files + stage3_output.json

**Step 2: Verify structural completeness**
- [ ] Prerequisites
- [ ] Validation steps
- [ ] Halt conditions
- [ ] Expected outputs

**Step 3: Commit**
```bash
git add prompts/generate.md tools/schemas/escalation.schema.json
git commit -m "$(cat <<'EOF'
docs(prompts): add generate.md Stage 3 prompt + extend escalation schema (BC-5, BC-9)

Drives LLM code generation of production scorer plugin.
Includes EVOLVE-BLOCK hash verification, UNVERIFIED field resolution,
CacheHitRate investigation, escalation procedures.
Extends escalation.schema.json halt_reason enum with 7 Stage 3 values
(K-8 item 10): missing_signal_coverage, stale_signal_coverage,
unverified_field_threshold_not_met, cache_hit_rate_unavailable_stage3,
post_write_validation_failure_stage3, pre_write_validation_failure_stage3,
evolve_block_hash_mismatch_stage3.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Documentation updates

**Contracts Implemented:** (documentation maintenance)
**Variant:** Artifact

**Files:**
- Modify: `docs/transfer/README.md`
- Modify: `CLAUDE.md`

**Step 1: Update README.md**

Add PR3 implementation notes:
- Go harness location and purpose
- Prompt template locations
- CI script (check_scorer_template.sh)
- Update pipeline status
- **Amend R6 shim acceptance criteria section:** Add a note that criteria #2 (accept all signals), #3 (reproduce scoring/penalty logic), and #5 (pass unit tests comparing shim output) are deferred to PR5 per D-5. PR3's `Score()` returns uniform 0.5 placeholder scores. Without this amendment, PR5 authors will encounter incorrect criteria claiming PR3 delivered full signal acceptance and scoring logic.

**Step 2: Update CLAUDE.md**

Update pipeline status table:
- PR3: "Prompt templates (Stages 1-3) + Go harness" → "Complete"

**Step 3: Commit**
```bash
git add docs/transfer/README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: update README and CLAUDE.md for PR3 deliverables

- Add Go harness, prompt templates, CI script documentation
- Update pipeline status table

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 | Task 2 | Compilation (Go) | `go build ./tools/harness/...` |
| BC-2 | Task 3 | Unit (Go) | `go test -run TestEquivalenceTrivial` |
| BC-3 | Task 1 | Schema validation | `validate-schema` with synthetic fixture |
| BC-4 | Task 4 | Script execution | `bash tools/check_scorer_template.sh` |
| BC-5 | Tasks 5-8 | Structural check | Manual: each prompt has 4 sections |
| BC-6 | Task 5 | Structural check | Manual: stage ordering in orchestrator |
| BC-7 | Task 6 | Structural check | Manual: extract prompt drives CLI correctly |
| BC-8 | Task 7 | Structural check | Manual: translate prompt maps all signals |
| BC-9 | Task 8 | Structural check | Manual: generate prompt includes all halt conditions |
| BC-10 | Tasks 5-8 | Structural check | Manual: each prompt checks prerequisites first |
| BC-11 | Task 3 | Unit (Go) | `go test -run TestStaleHashAbortsParsing` |
| BC-12 | Task 3 | Unit (Go) | `go test -run TestRunTuplesPanicRecovery` |
| Cross-PR #3 | Task 3 | Unit (Go) | `go test -run TestUnknownSignalTypeRejection` |

**Verification gate (Pipeline Stage PR):**
```bash
source .venv/bin/activate
python -m pytest tools/ -v          # Existing Python tests still pass (per CLAUDE.md)
go build ./tools/harness/...        # Go harness compiles
go test ./tools/harness/... -v      # Go harness tests pass
bash tools/check_scorer_template.sh # Scorer template compiles
# Prompt template YAML front-matter check (manual: verify all required front-matter fields present)
# per docs/contributing/pr-workflow.md Pipeline Stage verification gate
for f in prompts/*.md; do echo "--- Checking $f ---"; head -20 "$f" | grep -q '^---' && echo "PASS: has front-matter" || echo "FAIL: missing YAML front-matter"; done
```

**YAML front-matter requirement:** Each prompt template in `prompts/` must include YAML front-matter (per docs/contributing/pr-workflow.md and docs/contributing/standards/principles.md). The implementing agent must add front-matter to each prompt template with at minimum: `stage` (integer), `version` (string), `pipeline_commit` (string, set at authoring time). See docs/contributing/templates/design-guidelines.md for the full field list.

---

## Part 3: Quality Assurance

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Go module replace directive breaks on different checkout layouts | Medium | High | Use relative path `../../inference-sim`; document requirement in README | Task 2 |
| EVOLVE-BLOCK hash computation differs between Python and Go | Medium | High | Unit test (BC-11) compares against known hash; document exact algorithm | Task 3 |
| Scorer template fails to compile after submodule update | Medium | Medium | check_scorer_template.sh in CI catches this | Task 4 |
| Prompt templates produce incorrect LLM behavior | High | Medium | Can only validate structure, not LLM output; PR6 dry-run validates | Tasks 5-8 |
| signal_coverage.json schema mismatch with macro plan | Low | High | Schema fields traced directly from macro plan table | Task 1 |

### J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [ ] inference-sim types (RoutingSnapshot, Request, RouterState) match Go source at aa4bbb7 — **BLOCKING**: also verify the following four symbols exist at aa4bbb7. **Implementing agent MUST verify before Task 2 Step 3:** run all four grep commands and record the results here. If any symbol is missing, HALT — K-2 code will not compile.
  1. `grep -n 'func.*EffectiveLoad' inference-sim/sim/*.go` — used in `trivialAlgorithm.Route()` as `snap.EffectiveLoad()`. **Record the formula** (e.g., `QueueDepth + BatchSize + InFlightRequests` or a subset) — TestEquivalenceTrivial's assertion that pod-b scores higher than pod-a depends on pod-a having non-zero EffectiveLoad(). The test fixture sets InFlightRequests:3 on pod-a to ensure this holds regardless of formula, but the formula should be recorded here for reference.
  2. `grep -n 'func NewRoutingDecisionWithScores' inference-sim/sim/*.go` — used in `trivialAlgorithm.Route()` return
  3. `grep -n 'Scores' inference-sim/sim/*.go` — verify `RoutingDecision.Scores` field exists; used in `runOneTuple` as `decision.Scores`
  4. `grep -n 'Reason' inference-sim/sim/*.go` — verify `RoutingDecision.Reason` field exists; used in `trivialAlgorithm.Route()` as `sim.RoutingDecision{Reason: "no-endpoints"}`
- [ ] llm-d-inference-scheduler scorer interface matches source at 091312c
- [ ] **Production-side field name verification** — **BLOCKING**: After running `cd tools/harness && go mod download` (which fetches `sigs.k8s.io/gateway-api-inference-extension`), verify that all UNVERIFIED production field names exist in the external Metrics type. Run: `grep -rn 'RunningQueueSize\|RunningRequestCount\|KVCacheUsagePercent\|WaitingQueueSize' $(go env GOPATH)/pkg/mod/sigs.k8s.io/gateway-api-inference-extension*/pkg/epp/framework/interface/scheduling/types.go` (adjust path glob as needed). Each of the 4 field names must appear. If any mapping artifact field is absent from the Metrics struct at the pinned version, HALT — Stage 3 generated code will not compile.
- [ ] **Production access path verification** — **BLOCKING**: For each `prod_access_path` in `blis_to_llmd_mapping.md` that references a specific scorer (e.g., "ActiveRequest scorer", "LoadAware scorer"), the implementing agent MUST verify that the claimed access mechanism matches the scorer's actual `Score()` method implementation. Specifically: (a) `grep -n 'GetMetrics' llm-d-inference-scheduler/pkg/plugins/scorer/active_request.go` — if ActiveRequest's `Score()` does NOT call `GetMetrics()`, the mapping artifact's `prod_access_path` claims for BatchSize and InFlightRequests are architecturally wrong (ActiveRequest uses an in-process TTL cache via `s.endpointCounts`, not the Metrics API). (b) If a contradiction is found, HALT and follow the recovery procedure below before proceeding to Stage 2 — otherwise Stage 3 will generate scorer code calling an API the target scorer does not use. This check supplements the UNVERIFIED annotations which only flag field name existence, not the access mechanism.
  **Recovery procedure when BLOCKING fires** (expected: ActiveRequest.Score() uses `s.endpointCounts` map, not `GetMetrics()`): (1) Read `active_request.go`'s `Score()` method and identify the actual data access mechanism (e.g., `s.endpointCounts[endpoint]`). (2) Update `docs/transfer/blis_to_llmd_mapping.md`'s `prod_access_path` for BatchSize and InFlightRequests to reflect the correct access mechanism (TTL cache read via `s.endpointCounts` map, not `GetMetrics()` API call). (3) Commit the mapping artifact update: `git add docs/transfer/blis_to_llmd_mapping.md && git commit -m "fix(mapping): correct prod_access_path for ActiveRequest signals — TTL cache, not GetMetrics()"`. (4) Resume the task sequence from Task 7 (Stage 2 prompt). This recovery is executed inline as part of the verification checklist — a separate named task is not required because the fix is mechanical (updating a documented path) and the implementing agent has all needed context from step (a).
- [ ] signal_coverage.schema.json fields match macro plan workspace artifact table
- [ ] Prompt templates reference correct CLI commands and artifact paths

**Dimension 2: Schema chain integrity**
- [ ] algorithm_summary.json → signal_coverage.json: signals[] fields chain correctly
- [ ] signal_coverage.json → Stage 3: fields consumed by generate.md match schema
- [ ] stage3_output.json schema (PR2) → Stage 4: not broken by PR3 changes

**Dimension 3: Prompt completeness**
- [ ] Each of 4 prompt templates has: prerequisites, validation steps, halt conditions, expected outputs
- [ ] Each prompt checks predecessor artifacts before reading

**Dimension 4: CLI contract**
- [ ] No new CLI commands in PR3 (existing validate-schema used for signal_coverage.json)
- [ ] Exit codes consistent: check_scorer_template.sh uses 0/1/2

**Dimension 5: Artifact consistency**
- [ ] Signal names match across: mapping artifact, algorithm_summary schema, signal_coverage schema, prompt templates
- [ ] File paths in prompts match actual locations

**Dimension 6: Dead artifact prevention**
- [ ] Every new file has a documented consumer (see Section C table)

**Additional checks:**
- [ ] PR category: Pipeline Stage ✓
- [ ] Verification gate matches: pytest + go build + go test + bash script ✓
- [ ] No feature creep beyond macro plan PR3 scope
- [ ] Deviation log reviewed: 6 deviations (D-1 through D-6), all justified
- [ ] All 12 contracts mapped to tasks
- [ ] Task dependencies ordered correctly (schema → harness → tests → prompts → docs)

---

## Appendix K: File-Level Implementation Details

### K-1: signal_coverage.schema.json

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Signal Coverage",
  "description": "Output of Stage 2 (Translate) — maps simulation signals to production equivalents. Consumed by Stage 3 (Generate) and Stage 5 (Validate).",
  "type": "object",
  "required": ["signals", "unmapped_signals", "commit_hash", "coverage_complete"],
  "additionalProperties": false,
  "properties": {
    "signals": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["sim_name", "prod_name", "prod_access_path", "fidelity_rating", "staleness_window_ms", "mapped"],
        "additionalProperties": false,
        "properties": {
          "sim_name": {
            "type": "string",
            "description": "Signal name from algorithm_summary.json signals[].name"
          },
          "prod_name": {
            "type": "string",
            "description": "Production equivalent field name (e.g., WaitingQueueSize)"
          },
          "prod_access_path": {
            "type": "string",
            "description": "Go code to access this signal (e.g., endpoint.GetMetrics().WaitingQueueSize)"
          },
          "fidelity_rating": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Signal fidelity from mapping artifact"
          },
          "staleness_window_ms": {
            "type": "integer",
            "minimum": 0,
            "description": "Staleness window in milliseconds (0 for v1 approximate-scorer signals)"
          },
          "mapped": {
            "type": "boolean",
            "description": "True if signal has a production equivalent"
          },
          "normalization": {
            "type": "string",
            "enum": ["divide_prod_by_100", "verify_and_normalize", "boolean_presence_check"],
            "description": "Normalization action to apply. Must be one of the three recognized action keys from algorithm_summary.json normalization_note pattern."
          },
          "notes": {
            "type": "string",
            "description": "Additional mapping notes (e.g., structural semantic gap, double-counting risk)"
          },
          "fidelity_provisional": {
            "type": "boolean",
            "description": "Copied from algorithm_summary.json signals[].fidelity_provisional when true. When true, PR5 empirical validation is required before the fidelity rating can be considered stable."
          }
        }
      }
    },
    "unmapped_signals": {
      "type": "array",
      "items": {"type": "string"},
      "description": "Signal names that could not be mapped to production equivalents"
    },
    "commit_hash": {
      "type": "string",
      "pattern": "^[0-9a-f]{7,40}$",
      "description": "llm-d-inference-scheduler submodule commit hash at mapping time"
    },
    "coverage_complete": {
      "type": "boolean",
      "description": "True if all signals are mapped (unmapped_signals is empty)"
    }
  },
  "allOf": [
    {
      "if": {
        "properties": { "coverage_complete": { "const": true } }
      },
      "then": {
        "properties": {
          "unmapped_signals": {
            "maxItems": 0,
            "description": "When coverage_complete is true, unmapped_signals must be empty"
          }
        }
      }
    },
    {
      "if": {
        "properties": { "unmapped_signals": { "maxItems": 0 } }
      },
      "then": {
        "properties": {
          "coverage_complete": {
            "const": true,
            "description": "When unmapped_signals is empty, coverage_complete must be true"
          }
        }
      }
    }
  ]
}
```

### K-2: Go harness — harness.go (complete implementation)

```go
package harness

import (
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "strconv"
    "strings"

    sim "github.com/inference-sim/inference-sim/sim"
)

// Algorithm is an opaque handle to a loaded evolved algorithm.
// Matches inference-sim's RoutingPolicy interface signature.
type Algorithm interface {
    Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision
}

// TestTuple is the input format for equivalence tests.
type TestTuple struct {
    Request sim.Request
    State   sim.RouterState
}

// Result captures per-tuple test output.
type Result struct {
    Tuple       TestTuple
    SimScores   map[string]float64 // scores from sim algorithm
    ProdScores  map[string]float64 // scores from production scorer (populated by PR5)
    Passed      bool
    NoEndpoints bool // true when algorithm returned empty Scores due to no available endpoints
    Error       error
    ScoreDiffs  map[string]float64
}

// algorithmSummary is a subset of algorithm_summary.json fields needed by the harness.
type algorithmSummary struct {
    EvolveBlockSource      string `json:"evolve_block_source"`
    EvolveBlockContentHash string `json:"evolve_block_content_hash"`
}

// LoadAlgorithm loads an evolved algorithm by verifying the EVOLVE-BLOCK content hash.
// summaryPath: path to workspace/algorithm_summary.json
// repoRoot: repository root for resolving relative source paths
// Returns: Algorithm interface wrapping a trivial scorer (PR3); PR5 extends with full evolved logic.
func LoadAlgorithm(summaryPath, repoRoot string) (Algorithm, error) {
    data, err := os.ReadFile(summaryPath)
    if err != nil {
        return nil, fmt.Errorf("read algorithm summary: %w", err)
    }
    var summary algorithmSummary
    if err := json.Unmarshal(data, &summary); err != nil {
        return nil, fmt.Errorf("parse algorithm summary: %w", err)
    }
    if summary.EvolveBlockContentHash == "" {
        return nil, fmt.Errorf("algorithm_summary.json missing required field 'evolve_block_content_hash'")
    }
    if summary.EvolveBlockSource == "" {
        return nil, fmt.Errorf("algorithm_summary.json missing required field 'evolve_block_source'")
    }

    // Parse source path and line range (format: "path/to/file.py:START-END")
    parts := strings.SplitN(summary.EvolveBlockSource, ":", 2)
    if len(parts) != 2 {
        return nil, fmt.Errorf("invalid evolve_block_source format: %q", summary.EvolveBlockSource)
    }
    sourcePath := filepath.Join(repoRoot, parts[0])
    // Guard against path traversal (e.g., evolve_block_source = "../../etc/passwd:1-1")
    absSource, err := filepath.Abs(sourcePath)
    if err != nil {
        return nil, fmt.Errorf("resolve absolute path for source %q: %w", sourcePath, err)
    }
    absRoot, err := filepath.Abs(repoRoot)
    if err != nil {
        return nil, fmt.Errorf("resolve absolute path for repo root %q: %w", repoRoot, err)
    }
    if !strings.HasPrefix(absSource, absRoot+string(filepath.Separator)) {
        return nil, fmt.Errorf("evolve_block_source path %q escapes repo root", parts[0])
    }
    rangeParts := strings.SplitN(parts[1], "-", 2)
    if len(rangeParts) != 2 {
        return nil, fmt.Errorf("invalid line range format: %q", parts[1])
    }
    startLine, err := strconv.Atoi(rangeParts[0])
    if err != nil {
        return nil, fmt.Errorf("invalid start line: %w", err)
    }
    endLine, err := strconv.Atoi(rangeParts[1])
    if err != nil {
        return nil, fmt.Errorf("invalid end line: %w", err)
    }

    // Read source file and extract EVOLVE-BLOCK lines
    sourceData, err := os.ReadFile(sourcePath)
    if err != nil {
        return nil, fmt.Errorf("read source file %s: %w", sourcePath, err)
    }
    // Normalize CRLF to LF before splitting — ensures hash matches transfer_cli.py
    // (which opens in text mode, normalizing \r\n to \n on all platforms)
    normalized := strings.ReplaceAll(string(sourceData), "\r\n", "\n")
    lines := strings.Split(normalized, "\n")
    if startLine < 1 || endLine > len(lines) || startLine > endLine {
        return nil, fmt.Errorf("line range %d-%d out of bounds (file has %d lines)",
            startLine, endLine, len(lines))
    }
    blockLines := lines[startLine-1 : endLine]
    block := strings.Join(blockLines, "\n")

    // Verify content hash (must match transfer_cli.py extract algorithm exactly)
    hash := sha256.Sum256([]byte(block))
    computedHash := hex.EncodeToString(hash[:])
    if computedHash != summary.EvolveBlockContentHash {
        return nil, fmt.Errorf(
            "EVOLVE-BLOCK content hash mismatch: expected %s, computed %s — "+
                "source has changed since extraction, re-run extract stage",
            summary.EvolveBlockContentHash, computedHash)
    }

    return &trivialAlgorithm{}, nil
}

// trivialAlgorithm is a placeholder that scores by inverse effective load.
// PR5 replaces this with the actual evolved algorithm scorer.
type trivialAlgorithm struct{}

func (a *trivialAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
    if len(state.Snapshots) == 0 {
        // Return zero-value decision directly — cannot use NewRoutingDecisionWithScores
        // because it panics on empty target. runOneTuple's recover() would catch the panic,
        // but that produces a misleading error. A zero-value RoutingDecision with empty
        // Scores signals "no endpoints available" without triggering error recovery.
        return sim.RoutingDecision{Reason: "no-endpoints"}
    }
    scores := make(map[string]float64, len(state.Snapshots))
    for _, snap := range state.Snapshots {
        scores[snap.ID] = 1.0 / (1.0 + float64(snap.EffectiveLoad()))
    }
    bestID := state.Snapshots[0].ID
    bestScore := scores[bestID]
    for id, score := range scores {
        if score > bestScore {
            bestScore = score
            bestID = id
        }
    }
    return sim.NewRoutingDecisionWithScores(bestID, "trivial-inverse-load", scores)
}

// RunTuples executes tuples against the algorithm and returns per-tuple results.
// Per-tuple errors (including panics) are captured in Result.Error.
// NOTE: No per-tuple timeout is implemented in PR3. PR5 SHOULD add a context.Context
// parameter or time.AfterFunc guard when extending with production scorer calls,
// as a hung scorer would block the entire test run indefinitely.
func RunTuples(alg Algorithm, tuples []TestTuple) []Result {
    results := make([]Result, len(tuples))
    for i, tuple := range tuples {
        results[i] = runOneTuple(alg, tuple)
    }
    return results
}

// NormalizeKVUtilization converts production KVCacheUsagePercent (0-100 scale)
// to simulation KVUtilization (0.0-1.0 scale) by dividing by 100.
// Cross-PR contract #2: Stage 3 generated code MUST apply this normalization.
func NormalizeKVUtilization(prodPercent float64) float64 {
    if prodPercent < 0 {
        prodPercent = 0
    } else if prodPercent > 100 {
        prodPercent = 100
    }
    return prodPercent / 100.0
}

func runOneTuple(alg Algorithm, tuple TestTuple) (result Result) {
    result.Tuple = tuple
    defer func() {
        if r := recover(); r != nil {
            result.Error = fmt.Errorf("panic during Route: %v", r)
        }
    }()
    decision := alg.Route(&tuple.Request, &tuple.State)
    result.SimScores = decision.Scores
    // Distinguish "no endpoints available" (valid) from "scoring bug" (failure).
    // When Scores is empty and Reason is "no-endpoints", it's a valid no-endpoint scenario.
    if len(result.SimScores) == 0 && decision.Reason == "no-endpoints" {
        result.NoEndpoints = true
        result.Passed = true // valid scenario, not a failure
    } else {
        result.Passed = len(result.SimScores) > 0
    }
    return result
}

```

**Note on ValidateSignalTypes:** The implementing agent must add a `ValidateSignalTypes(summaryData []byte) error` function to `harness.go` that parses `algorithm_summary.json`, iterates over `signals[].type`, and returns an error listing any signals with `type: "unknown"`. This satisfies cross-PR contract #3 at the Go harness level.

**Note:** `TestEquivalence` (the PR5 suite entry point) is defined in `harness_test.go`, not in `harness.go`. Go's test runner only discovers `Test*` functions in `*_test.go` files. Importing `"testing"` in a non-test file is non-idiomatic and would break linters.

### K-3: Go harness — harness_test.go (complete tests)

```go
package harness

import (
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "os"
    "path/filepath"
    "strings"
    "testing"

    sim "github.com/inference-sim/inference-sim/sim"
)

func TestEquivalenceTrivial(t *testing.T) {
    // BC-2: 2 endpoints with different load, non-zero scores.
    // pod-a has InFlightRequests:3, ensuring EffectiveLoad() > 0 regardless of whether
    // EffectiveLoad() sums all three fields (QueueDepth+BatchSize+InFlightRequests)
    // or uses only InFlightRequests. This avoids a hidden dependency on the exact
    // EffectiveLoad() formula. See Sanity Checklist Dimension 1 for formula verification.
    alg := &trivialAlgorithm{}
    tuples := []TestTuple{
        {
            Request: sim.Request{ID: "req-1"},
            State: sim.RouterState{
                Snapshots: []sim.RoutingSnapshot{
                    {ID: "pod-a", QueueDepth: 2, BatchSize: 1, InFlightRequests: 3},
                    {ID: "pod-b", QueueDepth: 0, BatchSize: 0, InFlightRequests: 0},
                },
            },
        },
    }

    results := RunTuples(alg, tuples)
    if len(results) != 1 {
        t.Fatalf("expected 1 result, got %d", len(results))
    }
    r := results[0]
    if r.Error != nil {
        t.Fatalf("unexpected error: %v", r.Error)
    }
    if len(r.SimScores) == 0 {
        t.Fatal("expected non-empty scores")
    }
    // pod-b (load 0) should score higher than pod-a (load 3)
    if r.SimScores["pod-a"] >= r.SimScores["pod-b"] {
        t.Errorf("expected pod-b > pod-a, got pod-a=%f pod-b=%f",
            r.SimScores["pod-a"], r.SimScores["pod-b"])
    }
    // All scores must be > 0
    for id, score := range r.SimScores {
        if score <= 0 {
            t.Errorf("expected positive score for %s, got %f", id, score)
        }
    }
}

func TestStaleHashAbortsParsing(t *testing.T) {
    // BC-11 + Cross-PR contract #1: content hash mismatch detected
    repoRoot := t.TempDir()
    workspaceDir := t.TempDir()

    // Create source file with EVOLVE-BLOCK
    sourceDir := filepath.Join(repoRoot, "routing")
    if err := os.MkdirAll(sourceDir, 0o755); err != nil {
        t.Fatal(err)
    }
    originalSource := "line1\n// EVOLVE-BLOCK-START\noriginal logic\n// EVOLVE-BLOCK-END\nline5"
    sourcePath := filepath.Join(sourceDir, "best_program.py")
    if err := os.WriteFile(sourcePath, []byte(originalSource), 0o644); err != nil {
        t.Fatal(err)
    }

    // Compute hash of original EVOLVE-BLOCK (lines 2-4, 1-based)
    originalBlock := "// EVOLVE-BLOCK-START\noriginal logic\n// EVOLVE-BLOCK-END"
    hash := sha256.Sum256([]byte(originalBlock))
    originalHash := hex.EncodeToString(hash[:])

    // Write algorithm_summary.json with the original hash
    summary := map[string]interface{}{
        "algorithm_name":             "test",
        "evolve_block_source":        "routing/best_program.py:2-4",
        "evolve_block_content_hash":  originalHash,
        "signals":                    []interface{}{},
        "composite_signals":          []interface{}{},
        "metrics":                    map[string]interface{}{"combined_score": 0},
        "scope_validation_passed":    true,
        "mapping_artifact_version":   "1.0",
        "fidelity_checked":           true,
    }
    summaryBytes, err := json.Marshal(summary)
    if err != nil {
        t.Fatal(err)
    }
    summaryPath := filepath.Join(workspaceDir, "algorithm_summary.json")
    if err := os.WriteFile(summaryPath, summaryBytes, 0o644); err != nil {
        t.Fatal(err)
    }

    // Verify loading works with matching hash
    _, err = LoadAlgorithm(summaryPath, repoRoot)
    if err != nil {
        t.Fatalf("expected successful load with matching hash, got: %v", err)
    }

    // Modify the source file (simulate drift)
    modifiedSource := "line1\n// EVOLVE-BLOCK-START\nMODIFIED logic\n// EVOLVE-BLOCK-END\nline5"
    if err := os.WriteFile(sourcePath, []byte(modifiedSource), 0o644); err != nil {
        t.Fatal(err)
    }

    // LoadAlgorithm should fail with hash mismatch
    _, err = LoadAlgorithm(summaryPath, repoRoot)
    if err == nil {
        t.Fatal("expected error for stale hash, got nil")
    }
    if !strings.Contains(err.Error(), "hash mismatch") {
        t.Errorf("expected 'hash mismatch' in error, got: %v", err)
    }
}

func TestRunTuplesPanicRecovery(t *testing.T) {
    // BC-12: panic in Algorithm.Route is captured, not propagated
    panickingAlg := &panicAlgorithm{}
    tuples := []TestTuple{
        {
            Request: sim.Request{ID: "req-panic"},
            State: sim.RouterState{
                Snapshots: []sim.RoutingSnapshot{{ID: "pod-a"}},
            },
        },
        {
            Request: sim.Request{ID: "req-ok"},
            State: sim.RouterState{
                Snapshots: []sim.RoutingSnapshot{{ID: "pod-b", QueueDepth: 1}},
            },
        },
    }

    results := RunTuples(panickingAlg, tuples)
    if len(results) != 2 {
        t.Fatalf("expected 2 results, got %d", len(results))
    }
    if results[0].Error == nil {
        t.Error("expected error for panicking tuple")
    }
    if !strings.Contains(results[0].Error.Error(), "panic") {
        t.Errorf("expected 'panic' in error message, got: %v", results[0].Error)
    }
    if results[1].Error != nil {
        t.Errorf("expected no error for second tuple, got: %v", results[1].Error)
    }
}

func TestKVUtilizationNormalization(t *testing.T) {
    // Cross-PR contract #2: KVCacheUsagePercent (0-100) must be divided by 100
    // before being passed to the scorer (producing 0.0-1.0 range).
    //
    // This test exercises NormalizeKVUtilization from harness.go — the canonical
    // normalization function that Stage 3 generated code must use.
    prodValue := 75.0 // production KVCacheUsagePercent (0-100 scale)
    normalized := NormalizeKVUtilization(prodValue)
    if normalized < 0.0 || normalized > 1.0 {
        t.Errorf("normalized KVUtilization out of [0,1] range: %f", normalized)
    }
    if normalized != 0.75 {
        t.Errorf("expected 0.75, got %f", normalized)
    }

    // Boundary cases (including out-of-range inputs that must be clamped to [0,1])
    for _, tc := range []struct{ prod, expected float64 }{
        {0.0, 0.0},
        {100.0, 1.0},
        {50.0, 0.5},
        {-5.0, 0.0},    // negative input clamped to 0
        {100.5, 1.0},   // >100 input clamped to 1.0 (float rounding in Metrics API)
        {200.0, 1.0},   // far out-of-range clamped to 1.0
    } {
        got := NormalizeKVUtilization(tc.prod)
        if got != tc.expected {
            t.Errorf("NormalizeKVUtilization(%f): expected %f, got %f",
                tc.prod, tc.expected, got)
        }
    }
}

func TestUnknownSignalTypeRejection(t *testing.T) {
    // Cross-PR contract #3: signals with type "unknown" must be rejected or handled explicitly.
    // This test verifies that an algorithm_summary.json containing an unknown-type signal
    // is flagged during validation (not silently passed through to the scorer).
    summaryJSON := map[string]interface{}{
        "algorithm_name":             "test",
        "evolve_block_source":        "routing/best_program.py:1-1",
        "evolve_block_content_hash":  "deadbeef",
        "signals": []interface{}{
            map[string]interface{}{
                "name": "UnknownSignal",
                "type": "unknown",
            },
        },
        "composite_signals":        []interface{}{},
        "metrics":                  map[string]interface{}{"combined_score": 0},
        "scope_validation_passed":  true,
        "mapping_artifact_version": "1.0",
        "fidelity_checked":         true,
    }
    data, err := json.Marshal(summaryJSON)
    if err != nil {
        t.Fatal(err)
    }
    // ValidateSignalTypes checks for unknown-type signals and returns an error listing them.
    // The implementing agent must add ValidateSignalTypes to harness.go (reads signals[].type,
    // returns error if any signal has type "unknown").
    err = ValidateSignalTypes(data)
    if err == nil {
        t.Fatal("expected error for signal with type 'unknown', got nil")
    }
    if !strings.Contains(err.Error(), "unknown") {
        t.Errorf("expected 'unknown' in error message, got: %v", err)
    }
}

type panicAlgorithm struct {
    callCount int
}

func (a *panicAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
    a.callCount++
    if a.callCount == 1 {
        panic("intentional test panic")
    }
    scores := map[string]float64{}
    for _, snap := range state.Snapshots {
        scores[snap.ID] = 0.5
    }
    return sim.NewRoutingDecisionWithScores(state.Snapshots[0].ID, "ok", scores)
}

// TestEquivalence is the entry point that PR5 suites call.
// PR3 provides a minimal placeholder; PR5 adds Suite A/B/C logic.
func TestEquivalence(t *testing.T) {
    t.Log("TestEquivalence: PR3 placeholder — PR5 adds Suite A/B/C logic")
}
```

### K-4: check_scorer_template.sh

```bash
#!/usr/bin/env bash
# Extracts Go code blocks from scorer_template.go.md and compiles against submodule HEAD.
# Exit codes: 0 = pass, 1 = compilation failure, 2 = infrastructure error
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/docs/transfer/scorer_template.go.md"
SUBMODULE="$REPO_ROOT/llm-d-inference-scheduler"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Scorer template not found: $TEMPLATE" >&2
    exit 2
fi

if [ ! -d "$SUBMODULE/pkg" ]; then
    echo "ERROR: llm-d-inference-scheduler submodule not initialized: $SUBMODULE" >&2
    echo "Run: git submodule update --init llm-d-inference-scheduler" >&2
    exit 2
fi

if ! command -v go >/dev/null 2>&1; then
    echo "ERROR: go binary not found in PATH — install Go toolchain" >&2
    exit 2
fi

# Create temp directory for extracted code
TMPDIR_WORK=$(mktemp -d) || { echo "ERROR: failed to create temp directory" >&2; exit 2; }
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# Extract Go code blocks (between ```go and ```)
# Concatenate all Go blocks into a single file
awk '/^```go$/{ found=1; next } /^```$/{ found=0; next } found{ print }' \
    "$TEMPLATE" > "$TMPDIR_WORK/template_check.go"

if [ ! -s "$TMPDIR_WORK/template_check.go" ]; then
    echo "ERROR: No Go code blocks found in $TEMPLATE" >&2
    exit 2
fi

# Verify extracted code contains exactly one package declaration.
# Missing = extraction error. Multiple = template has blocks with different packages
# (e.g., package scorer + package scorer_test) which cannot coexist in a single file.
PKG_COUNT=$(grep -c '^package ' "$TMPDIR_WORK/template_check.go" || true)
if [ "$PKG_COUNT" -eq 0 ]; then
    echo "ERROR: Extracted Go code has no 'package' declaration — extraction may be incomplete" >&2
    exit 2
fi
if [ "$PKG_COUNT" -gt 1 ]; then
    echo "ERROR: Extracted Go code has $PKG_COUNT package declarations — template contains blocks with different packages. Only blocks matching the scorer package will be extracted." >&2
    # Re-extract only blocks whose first line is 'package scorer'.
    # Uses a two-pass approach for POSIX awk compatibility (macOS BWK awk
    # does not support \n in regex patterns, unlike GNU awk).
    # pkg_printed ensures 'package scorer' is emitted only once across all blocks.
    # Two-phase approach: extract, then deduplicate imports.
    # Phase 1: Extract only 'package scorer' blocks (deduplicate package line).
    awk '
        /^```go$/  { found=1; is_scorer=0; next }
        /^```$/    { found=0; next }
        found && !is_scorer {
            # Skip blank lines, single-line comments, and build tags before package declaration
            if ($0 ~ /^[[:space:]]*$/ || $0 ~ /^\/\// || $0 ~ /^\/\*/ || $0 ~ /^\*/) { next }
            if ($0 ~ /^package scorer$/) { is_scorer=1; if (!pkg_printed) { print; pkg_printed=1 }; next }
            else { found=0; next }
        }
        found && is_scorer  { print }
    ' "$TEMPLATE" > "$TMPDIR_WORK/template_extracted.go"
    # Phase 2: Deduplicate import declarations. Multiple 'package scorer' blocks
    # may each have their own import(...) section, producing duplicate import paths
    # that cause a Go compile error. Merge all imports into a single block.
    awk '
        /^package / { pkg_line=$0; next }
        /^import \(/ { in_import=1; next }
        in_import && /^\)/ { in_import=0; next }
        in_import { if (!seen[$0]++) imports[nimports++]=$0; next }
        !in_import { body[nbody++]=$0 }
        END {
            if (pkg_line != "") print pkg_line
            if (nimports > 0) {
                print "import ("
                for (i=0; i<nimports; i++) print imports[i]
                print ")"
            }
            for (i=0; i<nbody; i++) print body[i]
        }
    ' "$TMPDIR_WORK/template_extracted.go" > "$TMPDIR_WORK/template_check.go"
    if [ ! -s "$TMPDIR_WORK/template_check.go" ]; then
        echo "ERROR: No 'package scorer' blocks found after filtering" >&2
        exit 2
    fi
fi

# Copy extracted code to scorer package for compilation check
# Use a unique filename to avoid symbol conflicts with existing scorers
DEST="$SUBMODULE/pkg/plugins/scorer/template_check_temp.go"
trap 'rm -f "$DEST"; rm -rf "$TMPDIR_WORK"' EXIT  # update trap BEFORE copy to ensure cleanup
cp "$TMPDIR_WORK/template_check.go" "$DEST" || { echo "ERROR: failed to copy extracted code to $DEST" >&2; exit 2; }

cd "$SUBMODULE"
if go build ./pkg/plugins/scorer/ 2>"$TMPDIR_WORK/build_errors.txt"; then
    echo "PASS: Scorer template code compiles against submodule HEAD"
    exit 0
else
    echo "FAIL: Scorer template code does not compile:" >&2
    cat "$TMPDIR_WORK/build_errors.txt" >&2
    exit 1
fi
```

**Note:** The script places extracted code as `template_check_temp.go` in the scorer package (no leading underscore — Go's build tool ignores files whose names begin with `_` or `.`, which would silently skip compilation). The trap ensures cleanup on exit. If the template contains Go blocks with different package declarations (e.g., `package scorer` and `package scorer_test`), the script detects this and re-extracts only `package scorer` blocks using a POSIX-compatible awk approach (no `\n` in regex — compatible with macOS BWK awk). The `pkg_printed` variable ensures only the first `package scorer` declaration is emitted — subsequent qualifying blocks contribute their body lines without re-emitting the package line. A second awk pass deduplicates `import` declarations across blocks and ensures correct output ordering: (1) package clause first, (2) single merged import block, (3) remaining body lines — without this ordering, Go's parser would reject the file. If symbol conflicts arise (duplicate type names), the implementing agent should adjust to compile only the type-assertion and import sections, or use `go vet` for syntax checking.

### K-5 through K-8: Prompt Template Content Outlines

> **Implementation note:** K-5 through K-8 are structured outlines, not complete prompt text. The implementing agent must compose full prompt templates from these outlines, ensuring: (1) exact CLI commands for every validation step, (2) explicit halt conditions with unambiguous triggers, (3) correct artifact paths matching Section C, (4) prerequisite existence checks before each stage reads an artifact, (5) **cross-prompt consistency** — use `prompts/extract.md` (K-6, authored first) as the reference template for Markdown structure, section ordering, heading levels, and tone, and (6) **YAML front-matter** — each prompt template must begin with YAML front-matter containing at minimum `stage` (integer), `version` (string), and `pipeline_commit` (string), per docs/contributing/pr-workflow.md and docs/contributing/standards/principles.md. All four prompts should follow the same structural pattern to reduce ambiguity. Use the behavioral contracts (Section B) as acceptance criteria for each composed prompt.

**K-5: prompts/transfer.md (Orchestrator)**

The orchestrator prompt should include:
1. Pipeline overview: 6 stages, purpose, architecture
2. Prerequisites checklist with machine-checkable verification commands:
   - `test -f docs/transfer/blis_to_llmd_mapping.md || echo "HALT: missing mapping artifact"`
   - `test -f docs/transfer/scorer_template.go.md || echo "HALT: missing scorer template"`
   - `test -f routing/best_program.py || echo "HALT: missing routing input best_program.py"`
   - `test -f routing/best_program_info.json || echo "HALT: missing routing input best_program_info.json"`
   - `test -d inference-sim/sim || echo "HALT: inference-sim submodule not initialized — run git submodule update --init inference-sim"`
   - `test -d llm-d-inference-scheduler/pkg || echo "HALT: llm-d-inference-scheduler submodule not initialized — run git submodule update --init llm-d-inference-scheduler"`
   - `git submodule status inference-sim llm-d-inference-scheduler` — verify both show a commit hash (no leading `-` indicating uninitialized)
3. Pipeline commit recording: run `git rev-parse HEAD` at start and write the result to `workspace/pipeline_commit.txt` (`mkdir -p workspace && git rev-parse HEAD > workspace/pipeline_commit.txt`). The `mkdir -p` is required because `workspace/` is gitignored and may not exist on a fresh checkout (it is normally created by Stage 1's `extract` command, which runs after this step). This file persists across separate Claude Code sessions (one per stage). Each stage's output artifact (e.g., `algorithm_summary.json`, `signal_coverage.json`, `stage3_output.json`) already includes a commit hash field for its relevant submodule; `pipeline_commit.txt` records the top-level repo commit for drift detection. At pipeline end, compare current HEAD against the stored value: `[ "$(git rev-parse HEAD)" = "$(cat workspace/pipeline_commit.txt)" ] || echo "HALT: repo HEAD has drifted since pipeline start"`. Shell variables do not persist across sessions, so file-based storage is required
4. Stage sequence: for each stage, (a) name, (b) prompt file path, (c) prerequisite artifacts, (d) output artifacts
5. Between-stage checks: output existence + schema validation + stage-specific semantic checks. Exact commands: `test -f <artifact_path> || echo "HALT: missing artifact"` followed by `.venv/bin/python tools/transfer_cli.py validate-schema <artifact_path>` for each stage output, **plus** the following semantic checks per stage:
   - After Stage 1: `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"` — HALT if scope validation failed
   - After Stage 2: `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)"` — HALT if coverage incomplete (schema validation alone is insufficient since the schema permits `coverage_complete: false`)
   - After Stage 3: `.venv/bin/python -c "import json,sys,os; d=json.load(open('workspace/stage3_output.json')); scorer=d.get('scorer_file',''); sys.exit(0 if os.path.isfile(scorer) and 'PLACEHOLDER' not in open(scorer).read() else 1)"` — HALT if generated scorer file does not exist or contains PLACEHOLDER markers (schema validation of stage3_output.json alone is insufficient — it does not verify the generated Go files)
6. Halt handling: any stage failure → halt pipeline, escalation procedure
7. Stages 4-6 marked as "defined in PR4/PR5/PR6" with placeholder sections

Key references: macro plan Section D (data flow), Section I PR3 scope

**K-6: prompts/extract.md (Stage 1)**

The extract prompt should include:
1. Prerequisites: verify all required input files exist before proceeding:
   - `test -f routing/best_program.py || echo "HALT: missing routing/best_program.py"`
   - `test -f routing/best_program_info.json || echo "HALT: missing routing/best_program_info.json"`
2. **Stale artifact guard** (must execute BEFORE the extract command): Delete any existing `workspace/algorithm_summary.json` (`rm -f workspace/algorithm_summary.json`). This prevents a stale artifact from a prior successful run from being consumed by a downstream stage if the current extract fails before writing. (Matches K-7 step 2 and K-8 step 1b guard pattern.)
3. Exact command: `.venv/bin/python tools/transfer_cli.py extract routing/`
4. Exit code handling: 0 → proceed, 1 → halt. Three distinct failure modes all return exit code 1: (a) fidelity failure — artifact NOT written, (b) strict-mode minimum-signal failure — artifact NOT written, (c) scope failure — artifact IS written (with `scope_validation_passed: false`). Use exit code, not file existence, as the success signal.
5. Schema validation: `.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json`
6. Signal review: display extracted signals, normalization notes, composite signals
7. Halt conditions: exit != 0, schema invalid, scope_validation_passed == false. **scope_validation_passed check** (schema validation alone is insufficient since the schema permits `scope_validation_passed: false`): `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"` — HALT if exit != 0
8. Expected output: workspace/algorithm_summary.json with all required fields

**K-7: prompts/translate.md (Stage 2)**

The translate prompt should include:
1. Prerequisites: verify all required input artifacts exist, are valid, and passed scope validation before proceeding:
   - `test -f workspace/algorithm_summary.json || echo "HALT: missing algorithm_summary.json"` then `.venv/bin/python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json`
   - **scope_validation_passed check** (schema validation alone is insufficient since the schema permits `scope_validation_passed: false`): `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"` — HALT if exit != 0 (matches K-6 step 6 guard pattern)
   - `test -f docs/transfer/blis_to_llmd_mapping.md || echo "HALT: missing mapping artifact"`
   - `test -d llm-d-inference-scheduler/pkg || echo "HALT: llm-d-inference-scheduler submodule not initialized — run git submodule update --init llm-d-inference-scheduler"`
   - Verify PrecisePrefixCache scorer file exists (consumed in step 6): `test -f llm-d-inference-scheduler/pkg/plugins/scorer/precise_prefix_cache.go || echo "HALT: PrecisePrefixCache scorer not found — verify submodule is initialized"`
2. **Stale artifact guard:** Before running translate, delete any existing `workspace/signal_coverage.json` (`rm -f workspace/signal_coverage.json`). This prevents a stale artifact from a prior successful run from being consumed by Stage 3 if the current translate halts before writing.
3. Submodule staleness check: run `git -C llm-d-inference-scheduler rev-parse HEAD` and **store the full 40-character result** (this becomes the `commit_hash` field in the output artifact). Compare against `commit_hash` field in `docs/transfer/blis_to_llmd_mapping.md` (currently `091312c`, a 7-character abbreviated hash). **Comparison method:** use prefix matching — the mapping artifact's abbreviated hash must be a prefix of the full hash from `rev-parse HEAD`. **Dynamic extraction command** (do NOT hardcode the hash — extract it from the mapping artifact at runtime; note: `grep -P` is not available on macOS BSD grep, so use `awk` instead): `MAPPING_HASH=$(awk '/Pinned commit hash:/ { match($0, /[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]/); if (RSTART>0) print substr($0, RSTART, 7) }' docs/transfer/blis_to_llmd_mapping.md) && [ -n "${MAPPING_HASH}" ] && git -C llm-d-inference-scheduler rev-parse HEAD | grep -q "^${MAPPING_HASH}"` (exit 0 = match, exit 1 = HALT; the `[ -n "${MAPPING_HASH}" ]` guard ensures extraction failure does not silently pass). The `match()` pattern uses 7 literal `[0-9a-f]` character classes instead of `{7,40}` interval expression for macOS BWK awk portability (BWK awk does not reliably support ERE interval expressions). Example with current value: `awk` extracts `091312c`, then `rev-parse HEAD | grep -q "^091312c"` verifies the prefix match. Do NOT use exact string equality (40-char vs 7-char will always mismatch). HALT if hashes differ — signals may have changed meaning between commits
4. Signal mapping procedure: for each signal in algorithm_summary.json, find production equivalent in blis_to_llmd_mapping.md
5. Normalization propagation: carry forward normalization_note action keys
5b. Fidelity provisional propagation: for each signal in algorithm_summary.json, if `fidelity_provisional` is `true`, copy it to the corresponding signal in signal_coverage.json. This flag indicates PR5 empirical validation is required before the fidelity rating can be considered stable.
6. F-10 double-counting detection: if two signals map to same production metric, flag and halt
7. CacheHitRate investigation: read PrecisePrefixCache scorer, determine access path
8. Unknown-type signal resolution procedure (cross-PR contract #3): for each signal with type "unknown": (a) look up the signal name in `docs/transfer/blis_to_llmd_mapping.md` — if a mapping exists, assign the documented type and proceed; (b) if not found in the mapping artifact, check `algorithm_summary.json` context (composite_signals, normalization_note) for type inference clues; (c) if type remains unresolvable, move the signal to `unmapped_signals[]` with a note explaining why resolution failed — then **apply the step 9 pre-write validation** (verify all required fields are present and correctly typed) before writing the artifact with `coverage_complete: false`, and then halt. This ensures even the early-write path produces a schema-valid artifact; (d) HALT if any unknown-type signal is neither resolved (via a/b) nor explicitly rejected (via c) — do not silently drop signals
9. Pre-write validation: write the JSON object to a temp file first, then validate it against the schema before moving to the final path. Concrete mechanism: `TMPFILE=$(mktemp) && cat > "$TMPFILE" <<'ARTIFACT_EOF'` (JSON content) `ARTIFACT_EOF` then `.venv/bin/python tools/transfer_cli.py validate-schema "$TMPFILE" --schema tools/schemas/signal_coverage.schema.json && mv "$TMPFILE" workspace/signal_coverage.json || { echo "HALT: pre-write validation failed"; rm -f "$TMPFILE"; exit 1; }`. This prevents an invalid artifact from being consumed by a downstream stage that bypasses the orchestrator halt.
10. Output: write workspace/signal_coverage.json with all required fields: `signals` (array of mapped signal objects), `unmapped_signals` (array of unmapped signal names), `commit_hash` (set to the llm-d-inference-scheduler HEAD hash recorded in step 3), `coverage_complete` (true iff unmapped_signals is empty)
11. Post-write validation: `.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json`
12. Escalation procedures: on any halt condition, write `workspace/escalation.json` (per PR2 escalation schema) with halt reason, `"stage": 2` (integer per schema), and relevant context (e.g., stale commit hashes, unmappable signal names). **Before writing escalation.json**, the implementing agent MUST extend `tools/schemas/escalation.schema.json`'s `halt_reason` enum to include the following Stage 2 values: `"stale_submodule_commit"`, `"unmappable_signal"`, `"coverage_incomplete"`, `"unknown_signal_unresolved"`, `"pre_write_validation_failure"`. This schema update should be committed as part of Task 7. The halt_reason field in each escalation.json must use one of these enum values (not free-form strings). This matches K-8's escalation procedure and ensures the operator has a defined recovery path.
13. Halt conditions (with corresponding `halt_reason` enum values): stale commit (`stale_submodule_commit`), unmappable signal (`unmappable_signal`, after pre-write validation and writing artifact with `coverage_complete: false` per step 8c), coverage_complete == false (`coverage_incomplete`), unknown-type signal unresolved (`unknown_signal_unresolved`, cross-PR contract #3), pre-write validation failure (`pre_write_validation_failure`, missing or mistyped required field detected in step 9 or in step 8c early-write path)

**K-8: prompts/generate.md (Stage 3)**

The generate prompt should include:
1. Prerequisites: algorithm_summary.json (exists + schema valid + **scope_validation_passed check**: `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/algorithm_summary.json')); sys.exit(0 if d.get('scope_validation_passed') is True else 1)"` — HALT if exit != 0; matches K-6 step 6 and K-7 step 1 guard pattern), signal_coverage.json (exists + schema valid via `.venv/bin/python tools/transfer_cli.py validate-schema workspace/signal_coverage.json` + verify `coverage_complete == true` AND `unmapped_signals` is empty via `.venv/bin/python -c "import json,sys; d=json.load(open('workspace/signal_coverage.json')); sys.exit(0 if d.get('coverage_complete') is True and len(d.get('unmapped_signals',[])) == 0 else 1)"` — schema validation alone is insufficient since the schema permits `coverage_complete: false`, and the if/then constraint catches the contradiction at schema level but a belt-and-suspenders Python check prevents Stage 3 from proceeding with unmapped signals), scorer_template.go.md, blis_to_llmd_mapping.md. **Submodule staleness check:** verify signal_coverage.json's `commit_hash` still matches llm-d-inference-scheduler HEAD: `.venv/bin/python -c "import json,subprocess,sys; d=json.load(open('workspace/signal_coverage.json')); head=subprocess.check_output(['git','-C','llm-d-inference-scheduler','rev-parse','HEAD']).decode().strip(); match=head.startswith(d['commit_hash']) or d['commit_hash'].startswith(head); sys.exit(0 if match else 1)"` — HALT if mismatch (submodule advanced since Stage 2; `prod_access_path` values may be stale)
1b. **Stale artifact guard:** Before running generate, delete any existing `workspace/stage3_output.json` (`rm -f workspace/stage3_output.json`). Also remove any previously generated scorer files in the submodule to prevent stale Go files from a prior run with a different `algorithm_name`: read `algorithm_name` from `workspace/algorithm_summary.json`, sanitize to snake_case per item 12 rules, then `rm -f llm-d-inference-scheduler/pkg/plugins/scorer/${SANITIZED_NAME}.go llm-d-inference-scheduler/pkg/plugins/scorer/${SANITIZED_NAME}_test.go`. If a prior `stage3_output.json` exists, also read its `scorer_file` path and delete that file if it differs from the current name. This prevents duplicate scorer registrations causing Go compile errors in Stage 4. (Matches K-6 and K-7 stale artifact guard pattern, extended to submodule-side generated files.)
2. EVOLVE-BLOCK content hash verification (from scorer template header step 6). **Procedure:** Read the `evolve_block_content_hash` from `workspace/algorithm_summary.json`, recompute SHA-256 over the EVOLVE-BLOCK source lines, and compare. HALT on mismatch with `halt_reason: "evolve_block_hash_mismatch_stage3"`. **Fallback if scorer_template.go.md step numbering differs:** Search for "content hash" or "EVOLVE-BLOCK" in the scorer template header to locate the equivalent instruction. If neither keyword is found, perform the hash recomputation directly: read `evolve_block_content_hash` and `evolve_block_source` from `workspace/algorithm_summary.json`, extract the EVOLVE-BLOCK lines from the source file, compute SHA-256, and compare. Stage 3 must re-verify independently — Stage 1 verification does not guarantee no drift occurred between stages.
3. Mapping artifact existence check (from scorer template header step 5). **Procedure:** `test -f docs/transfer/blis_to_llmd_mapping.md || echo "HALT: missing mapping artifact"`. **Fallback if scorer_template.go.md step numbering differs:** Search for "mapping artifact" or "blis_to_llmd" in the scorer template header to locate the equivalent instruction. If not found, the existence check command above is sufficient.
4. UNVERIFIED field resolution procedure (from scorer template header HALT CONDITION). **Procedure:** Read the scorer template header and count fields marked `UNVERIFIED`. If the count exceeds the threshold specified in the HALT CONDITION section, HALT with `halt_reason: "unverified_field_threshold_not_met"`. **Fallback if "HALT CONDITION" label is absent:** Search for "UNVERIFIED" (case-insensitive) in `docs/transfer/scorer_template.go.md` to locate annotated fields, then search for "halt" or "threshold" near those annotations to find the threshold value. If no threshold is specified, treat ANY unresolved UNVERIFIED field as a halt condition.
5. Code generation steps:
   a. Parse EVOLVE-BLOCK for scoring logic (weights, penalties, thresholds, composites)
   b. Map sim signals to production fields using signal_coverage.json
   c. Apply normalization (KVUtilization / 100, etc.). **Fallback when normalization field is absent:** If a signal in signal_coverage.json does not have a `normalization` field (it is optional), the agent MUST: (i) check the originating signal's `normalization_note` in `workspace/algorithm_summary.json` as a fallback source for the normalization action key, (ii) if neither source provides normalization information, treat normalization as identity (no scaling) and emit a `// WARNING: no normalization specified for <signal_name>` comment in the generated code so PR5 reviewers can verify this is correct.
   d. Generate scorer file using template structure (Sections 1-7)
   e. Generate test file (Section 6 patterns)
   f. Add registration to register.go
6. CacheHitRate handling (scorer template Section 3 note): The generated scorer MUST check whether a CacheHitRate production field was identified in Stage 2 (`signal_coverage.json`). **If the signal is mapped** (has a `prod_access_path`): use that path in the generated code. **If the signal is unmapped or has `prod_access_path: "UNVERIFIED"`**: the generated code MUST emit `// CacheHitRate: production access path unavailable — using zero fallback` and assign `cacheHitRate = 0.0` as the fallback value. **HALT condition**: if the EVOLVE-BLOCK scoring logic uses CacheHitRate as a multiplier (not additive), a zero fallback would zero out the entire score — in that case, HALT with `halt_reason: "cache_hit_rate_unavailable_stage3"` instead of using the fallback.
7. F-10 double-counting guard implementation: The generated scorer MUST check whether `InFlightRequests` and `RunningQueueSize` map to the same production metric. **Detection**: read `signal_coverage.json` and check if any two signals in the EffectiveLoad composite (`QueueDepth`, `BatchSize`, `InFlightRequests`) share the same `prod_access_path`. **If double-counting detected** (e.g., `InFlightRequests` falls back to `RunningQueueSize`, making `EffectiveLoad = WaitingQueueSize + 2*RunningQueueSize`): the generated code MUST either (a) use `RunningQueueSize` once with an adjusted coefficient, or (b) use a different proxy for `InFlightRequests` (e.g., derive from `RunningRequestCount` if available). **If neither alternative is available**: emit a `// WARNING: F-10 double-counting risk — InFlightRequests and RunningQueueSize share the same production metric` comment and use the single-count approach (option a). **HALT** only if the double-counting would affect >50% of the scoring weight (determined by inspecting EVOLVE-BLOCK weights).
8. Stage 3 output validation: (a) no PLACEHOLDER markers remaining, (b) structural invariants from scorer_template.go.md Section 7, step 3 — specifically: (i) import paths unchanged, (ii) type assertion present, (iii) factory function registered, (iv) UNVERIFIED fields remain commented-out, (v) ScoreEndpoints helper in non-test file. **Do NOT compile** (`go build`): compilation is deferred to Stage 4, which has the full Go module environment. Stage 3 should NOT attempt `go build` — failure in an environment without Go module setup would produce misleading errors (per scorer_template.go.md Section 7, step 2).
9. Pre-write validation: construct stage3_output.json in memory and verify all required fields before writing. Then write workspace/stage3_output.json + validate with `.venv/bin/python tools/transfer_cli.py validate-schema workspace/stage3_output.json`
10. Escalation procedures for all halt conditions. **Before writing any escalation.json**, the implementing agent MUST extend `tools/schemas/escalation.schema.json`'s `halt_reason` enum to include the following Stage 3 values: `"missing_signal_coverage"`, `"stale_signal_coverage"`, `"unverified_field_threshold_not_met"`, `"cache_hit_rate_unavailable_stage3"`, `"post_write_validation_failure_stage3"`, `"pre_write_validation_failure_stage3"`, `"evolve_block_hash_mismatch_stage3"`. This schema update should be committed as part of Task 8. The halt_reason field in each escalation.json must use one of these enum values (not free-form strings). This matches K-7 step 12's pattern for Stage 2.
11. Halt conditions (with corresponding `halt_reason` enum values): missing prerequisites (`missing_signal_coverage` if signal_coverage.json does not exist; `stale_signal_coverage` if signal_coverage.json exists but its commit_hash mismatches HEAD), hash mismatch (`evolve_block_hash_mismatch_stage3`), UNVERIFIED field resolution below threshold (`unverified_field_threshold_not_met`, read the HALT CONDITION from scorer_template.go.md header — do not hardcode the field count), CacheHitRate unavailable (`cache_hit_rate_unavailable_stage3`), pre-write validation failure (`pre_write_validation_failure_stage3`, missing or mistyped field in stage3_output.json), post-write schema validation failure (`post_write_validation_failure_stage3`, `validate-schema workspace/stage3_output.json` exit != 0)
12. Expected outputs: scorer file at `llm-d-inference-scheduler/pkg/plugins/scorer/<name>.go` (where `<name>` is `algorithm_name` from `algorithm_summary.json`, sanitized to snake_case), test file at `<name>_test.go`, register.go modification, stage3_output.json. **Snake_case sanitization rules:** (a) replace spaces, hyphens, and dots with underscores, (b) collapse consecutive underscores, (c) strip leading/trailing underscores, (d) lowercase all characters, (e) prepend `scorer_` if the result starts with a digit, (f) strip non-ASCII characters. Example: `"Weighted-Load.Balance v2"` → `weighted_load_balance_v2`

**Key principle for all prompts:** Each prompt is written for an LLM (Claude) operating in an interactive session. Instructions should be explicit, step-by-step, and include exact commands. Assume the LLM has access to shell tools, file reading, and file writing.

### K-9: evolved_scorer.go — scheduling.Scorer shim

`evolved_scorer.go` implements the production-side `scheduling.Scorer` interface as a shim wrapping the harness `Algorithm`. This is a PR3 deliverable per README § R6 / D-5.

**Interface contract** (verified from `llm-d-inference-scheduler/pkg/plugins/scorer/load_aware.go` which implements `var _ scheduling.Scorer = &LoadAware{}`):

The `scheduling.Scorer` interface (from `sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling`) requires three methods:

1. `TypedName() plugin.TypedName` — returns the plugin's type and name (import: `sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin`)
2. `Category() scheduling.ScorerCategory` — returns the scoring category (e.g., `scheduling.Distribution`)
3. `Score(ctx context.Context, cycleState *scheduling.CycleState, req *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64` — scores candidate endpoints

**Factory pattern** (from existing scorers): each scorer provides a factory function with signature `func(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)` and registers via `register.go`.

```go
package harness

import (
    "context"

    "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin"
    "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"
)

const EvolvedScorerType = "evolved-scorer"

// compile-time interface assertion
var _ scheduling.Scorer = &EvolvedScorer{}

// EvolvedScorer adapts the harness Algorithm to the production scheduling.Scorer interface.
// PR3 provides the structural shim; PR5 wires in the actual scoring logic.
type EvolvedScorer struct {
    typedName plugin.TypedName
    alg       Algorithm
}

// NewEvolvedScorer creates an EvolvedScorer wrapping the given Algorithm.
func NewEvolvedScorer(alg Algorithm) *EvolvedScorer {
    return &EvolvedScorer{
        typedName: plugin.TypedName{Type: EvolvedScorerType},
        alg:       alg,
    }
}

// WithName sets the scorer's name.
func (s *EvolvedScorer) WithName(name string) *EvolvedScorer {
    s.typedName.Name = name
    return s
}

// TypedName returns the typed name of the plugin.
func (s *EvolvedScorer) TypedName() plugin.TypedName {
    return s.typedName
}

// Category returns Distribution — the evolved scorer distributes load across endpoints.
func (s *EvolvedScorer) Category() scheduling.ScorerCategory {
    return scheduling.Distribution
}

// Score scores endpoints by delegating to the wrapped Algorithm.
// PR3: returns uniform 0.5 scores (placeholder). PR5 maps Algorithm.Route() scores
// to the production endpoint scoring contract.
func (s *EvolvedScorer) Score(_ context.Context, _ *scheduling.CycleState, _ *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
    scores := make(map[scheduling.Endpoint]float64, len(endpoints))
    for _, ep := range endpoints {
        scores[ep] = 0.5 // placeholder — PR5 wires Algorithm.Route() scores
    }
    return scores
}
```

**go.mod dependency:** Task 2 Step 1 includes `sigs.k8s.io/gateway-api-inference-extension` in go.mod at pseudo-version `v0.0.0-20260128235548-fd30cb97714a` (matching llm-d-inference-scheduler's go.mod). This is fetched from the Go module proxy — no `replace` directive is needed. `go mod tidy` runs in Step 2c (after both .go files are written) to resolve all transitive dependencies.

**Note:** This file lives in `tools/harness/` (not in the submodule). It demonstrates that the harness can produce a production-compatible scorer. PR5 wires the actual scoring logic; PR6 copies the generated scorer into the submodule for the real PR.
