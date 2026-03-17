# PR5: Validation Pipeline (Stage 5) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement Stage 5 (Validate) — wire the actual evolved algorithm into the Go harness, implement 3-suite equivalence testing against the generated production scorer, add noise characterization and benchmark CLI commands, and create the Stage 5 prompt template.

**The problem today:** PR1–PR4 built the pipeline scaffolding: extraction, mapping, code generation, and test retry. But the harness still returns a trivial placeholder algorithm (PR3 stub), `EvolvedScorer.Score()` returns uniform 0.5, and there are no Suite A/B/C test implementations, no noise characterization command, and no Stage 5 prompt. The pipeline cannot validate equivalence.

**What this PR adds:**
1. `evolvedAlgorithm` struct that runs the EVOLVE-BLOCK penalty logic using inference-sim's WeightedScoring as the base — replaces `trivialAlgorithm` in `LoadAlgorithm`
2. Wired `EvolvedScorer.Score()` that translates production endpoint metrics to sim types and delegates to `alg.Route()`
3. Suite A/B/C equivalence tests; Suite A imports `BLISWeightedScorer` from llm-d submodule (go workspace)
4. `noise-characterize` and `benchmark` CLI commands (Python, stdlib only)
5. `prompts/validate.md` Stage 5 prompt template
6. `docs/transfer/calibration_log.md` and `docs/transfer/noise_characterization.md` templates
7. `tools/schemas/validation_results.schema.json`

**Why this matters:** Stage 5 is the go/no-go gate: the pipeline must not create PRs in target repos without demonstrating that the generated scorer reproduces the sim algorithm's routing decisions. This PR completes Milestone 3 — full validation operational.

**Architecture:** Mixed-language. The Go harness (`tools/harness/`) is extended with `evolvedAlgorithm`, Kendall-tau stats, and Suite A/B/C tests. Suite A imports `github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer` via the existing `go.work` workspace. Python CLI adds two new commands. The Stage 5 prompt orchestrates all three tools from an interactive Claude Code session, writing results to `workspace/validation_results.json`.

**PR Category:** Validation (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR5: "Validation pipeline (Stage 5) + noise characterization + benchmarks" in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Behavioral Contracts:** See Part 1, Section B below

---

## Part 1: Design Validation

### A) Executive Summary

PR5 wires the real evolved algorithm into the harness and tests it against the generated production scorer (`BLISWeightedScorer`). It sits at the end of the build phase: PR1–PR4 created the scaffolding, PR5 validates it, PR6 creates the final PRs.

As a Validation PR, it gets 5 review perspectives (per pr-workflow.md). Full convergence protocol: re-run until zero CRITICAL + zero IMPORTANT findings, maximum 3 rounds.

**Phase 0 Audit Results:**

- Submodules verified: inference-sim at `aa4bbb713c46e0`, llm-d-inference-scheduler at `091312c333a50e`, llm-d-benchmark at `111c6ff8e4995`
- PR3 artifacts confirmed: `tools/harness/harness.go` (Algorithm, TestTuple, Result, LoadAlgorithm, RunTuples, NormalizeKVUtilization), `tools/harness/evolved_scorer.go` (EvolvedScorer placeholder), `tools/harness/harness_test.go` (TestEquivalence placeholder)
- PR4 artifacts confirmed: `prompts/test.md`, `tools/transfer_cli.py` `test-status` command
- `go.work` confirmed: both `./llm-d-inference-scheduler` and `./tools/harness` in workspace — harness can import llm-d via workspace resolution. A `replace` directive is also added to `go.mod` (K.13) so standalone `go build` (outside the workspace) also works; with go.work active the replace is redundant but harmless.
- `BLISWeightedScorer` confirmed: `llm-d-inference-scheduler/pkg/plugins/scorer/blis_weighted_scoring.go` already exists (Stage 3 complete pre-PR5). Constructor: `NewBLISWeightedScorer(ctx context.Context, params BLISWeightedScorerParameters) *BLISWeightedScorer`. `BLISWeightedScorerParameters` struct confirmed (has `Enabled bool` field). `.WithName(string)` method chain confirmed. `ScoreEndpoints()` test helper returns `map[string]float64` keyed by `NamespacedName.String()` (i.e., `"/endpoint-name"` format).
- `fwkdl.Metrics` confirmed: fields are `WaitingQueueSize int`, `RunningRequestsSize int`, `KVCacheUsagePercent float64` — **no** `RunningQueueSize`, `RunningRequestCount`, or `CacheHitRate` fields.
- **DEVIATION D-1**: Mapping artifact documents `RunningQueueSize` and `RunningRequestCount` as production equivalents; actual field is `RunningRequestsSize`. See D-1 in Deviation Log.
- **DEVIATION D-2**: No `CacheHitRate` production equivalent; `BLISWeightedScorer` uses zero fallback. Cache affinity bonus never fires in production. See D-2.
- **DEVIATION D-3**: Macro plan `TestTuple{Request, Endpoints []sim.RouterState, Expected}` vs PR3 actual `TestTuple{Request sim.Request, State sim.RouterState}`. See D-3.
- **DEVIATION D-4**: Macro plan `LoadAlgorithm(path string)` vs PR3 actual `LoadAlgorithm(summaryPath, repoRoot string)`. See D-4.
- **DEVIATION D-5**: Prefix-affinity scorer (weight 3/7) has no production equivalent — `BLISWeightedScorer` sets it to 0.0. Base score gap means numerical fidelity (1e-6/1%) cannot be met; Suite A uses Kendall-tau rank correlation as the primary go/no-go criterion. See D-5.
- `validation_results.schema.json` absent (PR5 creates it) ✓
- Python venv: `.venv/bin/python` present (confirmed by TestCrossLanguageHashConsistency test)

### B) Behavioral Contracts

#### Positive Contracts

**BC-1: validation_results.schema.json validates correct workspace artifact**
- GIVEN `workspace/validation_results.json` with all required fields present and correctly typed
- WHEN `python tools/transfer_cli.py validate-schema workspace/validation_results.json` is run
- THEN it exits 0 and outputs `{"status": "ok", ...}`
- MECHANISM: `tools/schemas/validation_results.schema.json` mirrors the macro plan table; the CLI's lightweight custom validator checks required fields and types

**BC-2: LoadAlgorithm returns evolvedAlgorithm**
- GIVEN `workspace/algorithm_summary.json` with a correct EVOLVE-BLOCK content hash
- WHEN `LoadAlgorithm(summaryPath, repoRoot)` is called
- THEN the returned Algorithm, when given a RouterState where endpoint A has EffectiveLoad 5 and endpoint B has EffectiveLoad 1, selects endpoint B
- MECHANISM: `evolvedAlgorithm` applies cubic load penalty (1/(1+delta³×5)) that favors lower-load endpoints

**BC-3: evolvedAlgorithm applies KV pressure penalty**
- GIVEN a RouterState where endpoint A has KVUtilization=0.90 and endpoint B has KVUtilization=0.50 with equal loads
- WHEN `evolvedAlgorithm.Route()` is called
- THEN endpoint A's score is multiplied by ≤ 1.0 (KV penalty applied) and endpoint B's score is unchanged
- MECHANISM: Penalty fires when `snap.KVUtilization > 0.82`; factor = `max(0.3, 1 - (kv-0.82)*2)`

**BC-4: EvolvedScorer maps production metrics to sim types correctly**
- GIVEN mock endpoints with `WaitingQueueSize=3`, `RunningRequestsSize=2`, `KVCacheUsagePercent=85.0`
- WHEN `EvolvedScorer.Score()` is called
- THEN internal sim RoutingSnapshot has `QueueDepth=3`, `InFlightRequests=2`, `KVUtilization=0.85`
- MECHANISM: `QueueDepth←WaitingQueueSize`, `InFlightRequests←RunningRequestsSize` (BatchSize=0 in prod), `KVUtilization←KVCacheUsagePercent/100`

**BC-5: EvolvedScorer reads SessionID from request header**
- GIVEN an `LLMRequest` with `Headers["x-session-token"] = "sess-123"`
- WHEN `EvolvedScorer.Score()` is called
- THEN `Score()` completes without panic and returns scores for all endpoints (verifying the session header code path executes without error). The internal `sim.Request.SessionID` is set to `"sess-123"` by the header lookup, but this is not directly observable from the test because the algorithm's Route() method does not expose the SessionID it received — with CacheHitRate=0, SessionID has no effect on scores. The test verifies the no-panic contract and correct score count.
- MECHANISM: header lookup `request.Headers["x-session-token"]`
- NOTE: Direct SessionID assertion would require a test spy on the algorithm, which is not implemented. The code path is verified by inspection (K.3 line `simReq.SessionID = req.Headers[sessionTokenHeader]`) and by the no-panic test.

**BC-6: Suite A Kendall-tau > 0.8 for canonical test tuples**
- GIVEN 200 test tuples with BatchSize=0 and CacheHitRate=0 for all endpoints, varied queue depths and KV utilization
- WHEN Suite A runs `evolvedAlgorithm.Route()` (SimScores) and `BLISWeightedScorer.Score()` (ProdScores) on each tuple
- THEN the mean Kendall-tau rank correlation across tuples is > 0.8
- MECHANISM: With BatchSize=0, EffectiveLoad maps identically; with CacheHitRate=0, the EVOLVE-BLOCK cache-affinity bonus never fires; prefix-affinity scores are equal across all endpoints because `generateCanonicalTuples` sets `sim.Request.InputTokens` to nil (zero value), causing `ComputeBlockHashes(nil)` → empty hashes → `totalBlocks==0` → all instances score 0.0 from the prefix-affinity scorer

**BC-7: Suite A records max_abs_error (informational)**
- GIVEN any Suite A run
- WHEN Suite A completes
- THEN `suite_a.max_abs_error` is recorded in `validation_results.json` with the maximum per-endpoint absolute score difference
- MECHANISM: computed as `max(|SimScore[id] - ProdScore[id]|)` across all endpoints and tuples; not used for pass/fail decision

**BC-8: Suite B marks all v1 results informational_only=true**
- GIVEN any Suite B run on v1 signals (all have staleness_window_ms=0)
- WHEN Suite B completes
- THEN `suite_b.informational_only = true` and `suite_b.passed = true`
- MECHANISM: All signals have `staleness_window_ms=0`; zero perturbation means rank stability is trivially 1.0

**BC-9: Suite C verifies concurrent determinism**
- GIVEN a single TestTuple
- WHEN 20 goroutines each create their own `evolvedAlgorithm` instance and call `Route()` concurrently with identical inputs
- THEN all 20 RoutingDecision.Scores maps are byte-identical
- MECHANISM: Each goroutine uses an independent `evolvedAlgorithm` instance (via `newEvolvedAlgorithm()`). This is required because the underlying `WeightedScoring` includes the `prefix-affinity` scorer, which captures mutable closure variables (`cachedHashes`, `cachedReqID` in `routing_prefix_scorer.go:27-28`) with no synchronization — safe under inference-sim's single-threaded DES event loop, but causes data races under concurrent access. Per-goroutine instances eliminate shared mutable state. Production schedulers similarly create per-request or per-goroutine scorer instances.

**BC-10: Suite C pile-on ratio ≤ 2.0**
- GIVEN 100 test tuples with varied load assignments across 5 endpoints (each endpoint has lowest load ~20 times)
- WHEN `evolvedAlgorithm.Route()` is called for each tuple
- THEN no single endpoint is selected more than 2× its fair share (2 × 100/5 = 40 times max)
- MECHANISM: varied load assignments cause different endpoints to win for each tuple

**BC-11: noise-characterize halts when max CV > 15%**
- GIVEN a baseline runs JSON with high variance (CV = 0.20)
- WHEN `python tools/transfer_cli.py noise-characterize --runs baseline_runs.json` is run
- THEN it exits 1, outputs `{"status": "error", "halt": true, ...}`, and includes CV values in the output
- MECHANISM: `halt = max_cv > 0.15`; exit code 1 signals pipeline halt

**BC-12: noise-characterize computes T_eff correctly**
- GIVEN baseline runs with p99 values [0.40, 0.42, 0.41, 0.39, 0.41] (sample CV ≈ 0.028 using Bessel's correction)
- WHEN `noise-characterize` is run
- THEN T_eff = max(0.05, 2*0.028) = 0.056
- MECHANISM: `t_eff = max(0.05, 2 * max_cv_across_metrics)`; CV uses sample std (÷(n-1)) per K.8

**BC-13: benchmark outputs PASS when matched workload improvement ≥ T_eff**
- GIVEN workloads JSON with one matched workload (baseline_p99=0.45, transfer_p99=0.38), T_eff=0.10
- WHEN `python tools/transfer_cli.py benchmark --results benchmark_results.json --t-eff 0.10` is run
- THEN it exits 0, outputs `mechanism_check_verdict: "PASS"`, improvement ≈ 0.156

**BC-14: validate.md Stage 5 prompt checks predecessor artifacts**
- GIVEN that `workspace/signal_coverage.json` does not exist
- WHEN `prompts/validate.md` is loaded in a Claude Code session
- THEN the prompt instructs the operator to halt with "HALT: Stage 2 prerequisite missing: workspace/signal_coverage.json"
- MECHANISM: prompt prerequisites section lists required files and instructs `validate-schema` before each artifact is used

#### Negative Contracts

**BC-15: EvolvedScorer.Score() MUST NOT reference non-existent Metrics fields**
- GIVEN the production `fwkdl.Metrics` struct at commit `091312c`
- WHEN `EvolvedScorer.Score()` is compiled
- THEN it MUST NOT reference `RunningQueueSize`, `RunningRequestCount`, or `CacheHitRate` (none exist)
- MECHANISM: Compile-time verification; go build fails if non-existent fields are referenced

#### Error Handling Contracts

**BC-16: noise-characterize exits 2 on malformed input**
- GIVEN `--runs` pointing to a file with invalid JSON OR valid JSON with an empty runs list (`{"runs": []}`)
- WHEN noise-characterize runs
- THEN exit code 2 (infrastructure error), `{"status": "error", "errors": ["..."]}` on stdout
- MECHANISM: JSON parse failure or empty runs list raises infrastructure error (not validation failure). An empty runs list is treated as infrastructure error because CV computation requires ≥2 data points — an empty list indicates the operator has not yet collected baseline data, not that the data failed validation.

**BC-17: benchmark exits 1 when T_eff is not provided**
- GIVEN `benchmark` invoked without `--t-eff`
- WHEN benchmark runs
- THEN exit code 1, `{"status": "error", "errors": ["--t-eff required: run noise-characterize first"]}`

**BC-18: benchmark exits 2 on malformed input**
- GIVEN `--results` pointing to a file with invalid JSON OR valid JSON missing the `workloads` key
- WHEN benchmark runs
- THEN exit code 2 (infrastructure error), `{"status": "error", "errors": ["..."]}` on stdout
- MECHANISM: Parallel to BC-16 for noise-characterize. Invalid JSON or missing required structure indicates infrastructure/operator error, not a benchmark failure.

### C) Component Interaction

```
workspace/signal_coverage.json ──────────────────────→ prompts/validate.md (Stage 5)
workspace/stage3_output.json (→ BLISWeightedScorer) ──→ prompts/validate.md
                                                              │
                         ┌────────────────────────────────────┤
                         ↓                                    ↓
       Python CLI: noise-characterize            Go harness Suite A/B/C
       input: baseline_runs.json                 input: TestTuples (generated inline)
       output: stdout JSON (T_eff, CV)           sim: evolvedAlgorithm.Route()
                         │                       prod: BLISWeightedScorer.Score()
                         │                       output: Kendall-tau, errors, pile-on ratio
                         ↓                                    ↓
       Python CLI: benchmark                   ┌─────────────┘
       input: benchmark_results.json + T_eff   │
       output: stdout JSON (mechanism check)   │
                         │                     │
                         └────────┬────────────┘
                                  ↓
                   workspace/validation_results.json
                   (written by operator following validate.md prompt)
                                  ↓
                          Stage 6 (PR creation)
```

**Cross-system dependencies:**
- `inference-sim` at `aa4bbb7`: provides `sim.NewRoutingPolicy`, `sim.RouterState`, `sim.RoutingSnapshot`, `sim.DefaultScorerConfigs`
- `llm-d-inference-scheduler` at `091312c` (via go.work): provides `scorer.BLISWeightedScorer`, `scorer.NewBLISWeightedScorer`, `scorer.ScoreEndpoints`
- `gateway-api-inference-extension` (dep of both): provides `fwkdl.Metrics`, `scheduling.Endpoint`, `scheduling.LLMRequest`

**Workspace artifacts produced:** `workspace/validation_results.json` (written by operator following prompt instructions)

**Dead artifact check:** All files are consumed:
- `validation_results.schema.json` — consumed by `transfer_cli.py validate-schema` and Stage 6 prompt
- `evolved_algorithm.go` — called by `LoadAlgorithm` and Suite A/B/C
- `evolved_scorer.go` (wired) — consumed by `TestEvolvedScorerContract`
- `stats.go` — consumed by Suite A
- `suite_a/b/c_test.go` — executed by `go test ./tools/harness/...`
- `validate.md` — consumed by operator at Stage 5 runtime
- `noise_characterization.md` — consumed by operator as standalone procedure reference (not referenced by `validate.md`; `validate.md` embeds its own noise-characterize instructions inline)
- `calibration_log.md` — consumed by Stage 6 (PR6)
- CLI commands — consumed by `validate.md` prompt and Python tests

### D) Deviation Log

| Macro Plan Says | Micro Plan Does | Reason |
|-----------------|-----------------|--------|
| `fwkdl.Metrics.RunningQueueSize` (BatchSize mapping) | Uses `RunningRequestsSize`; `BatchSize=0` in test tuples | **CORRECTION**: `RunningQueueSize` does not exist in `fwkdl.Metrics` at the pinned commit. `RunningRequestsSize` is the only available running-request field. Mapping artifact `docs/transfer/blis_to_llmd_mapping.md` corrected in this PR (Signal Mapping Table, Composite Signals, and Metric access sections updated). (D-1) |
| `fwkdl.Metrics.RunningRequestCount` (InFlightRequests mapping) | Uses `RunningRequestsSize` (F-10 single-count) | **CORRECTION**: `RunningRequestCount` does not exist. Both BatchSize and InFlightRequests map to `RunningRequestsSize`. Single-count to avoid double-weighting. Mapping artifact corrected in this PR. (D-1, D-2 combined) |
| `fwkdl.Metrics.CacheHitRate` field | Zero fallback; cache affinity bonus always skipped | **CORRECTION**: No `CacheHitRate` field exists. `BLISWeightedScorer` already implements zero fallback. Suite A uses BatchSize=CacheHitRate=0 test tuples to eliminate the gap. (D-2) |
| `TestTuple{Request, Endpoints []sim.RouterState, Expected sim.RoutingSnapshot}` | `TestTuple{Request sim.Request, State sim.RouterState}` | **CORRECTION**: PR3 implemented the actual PR3 interface. `sim.RouterState.Snapshots` already holds all endpoint states. `Expected` is not stored in TestTuple — Suite A derives expected from SimScores at test time. (D-3) |
| `LoadAlgorithm(path string)` | `LoadAlgorithm(summaryPath, repoRoot string)` | **CORRECTION**: PR3 implementation requires repoRoot for path resolution and hash verification. (D-4) |
| Suite A: numerical fidelity (1e-6 abs OR 1% relative) as primary criterion | Suite A: Kendall-tau rank correlation > 0.8 as primary criterion; max_abs_error recorded informally | **SIMPLIFICATION**: Prefix-affinity scorer (weight 3/7) has no production equivalent (`BLISWeightedScorer` sets it to 0.0). This creates a constant base-score gap that prevents numerical fidelity for most tuples. Rank correlation is the correct metric for a routing algorithm (relative order matters, not absolute values). (D-5) |
| `validate.md` stage prompt invoked after Stage 4 success | Identical | No deviation. |
| Calibration log schema: `pipeline_commit` field | Included | No deviation. |
| Benchmark CLI outputs per-workload records under key `results` | Schema (`validation_results.schema.json`) uses `workload_classification`; operator must rename when assembling artifact | **NAMING**: The benchmark CLI (`_cmd_benchmark`) emits the key as `results` for internal clarity, but the schema field is `workload_classification` (with `additionalProperties: false`). K.10 Step 6 template annotates the rename. (D-6) |

### E) Review Guide

**The tricky part:** The equivalence comparison in Suite A has a known semantic gap (prefix-affinity). The go/no-go criterion is Kendall-tau > 0.8, not numerical fidelity. Reviewers should verify that the test tuple generator (BatchSize=0, CacheHitRate=0) correctly eliminates the EffectiveLoad mapping ambiguity while leaving the real scoring signals (queue depth, KV utilization) exercised.

**What to scrutinize:**
- `evolvedAlgorithm.Route()` in evolved_algorithm.go: verify the penalty coefficients exactly match `routing/best_program.py:171-241`. Any numeric deviation would cause the harness to test a different algorithm than what was evolved.
- `EvolvedScorer.Score()` metric translation: verify `QueueDepth←WaitingQueueSize`, `InFlightRequests←RunningRequestsSize`, `KVUtilization←KVCacheUsagePercent/100`. These are the key cross-system contracts.
- BC-15 (no non-existent fields): confirm the build passes and no `RunningQueueSize`/`RunningRequestCount`/`CacheHitRate` appear in the harness metric reads.

**What's safe to skim:** Suite B (trivially passes in v1), noise_characterization.md and calibration_log.md templates (documentation only), Python test fixtures.

**Known debt:** Suite B infrastructure is present but provides no v1 coverage (all signals have staleness_window_ms=0). This is intentional and documented via `informational_only: true` in validation_results.json. The benchmark command cannot be end-to-end tested without a live cluster; PR5 tests only the JSON input parsing and computation logic.

---

## Part 2: Executable Implementation

### F) Implementation Overview

**Files to create:**
- `tools/schemas/validation_results.schema.json` — schema for validation_results workspace artifact
- ~~`tools/schemas/stage4_output.schema.json`~~ — **REMOVED**: Stage 5 prerequisites now verify Stage 4 via direct `go build`/`go vet` instead of a JSON artifact (Stage 4 writes no workspace artifact on success)
- `tools/harness/evolved_algorithm.go` — `evolvedAlgorithm` struct implementing EVOLVE-BLOCK penalties
- `tools/harness/stats.go` — Kendall-tau computation utility
- `tools/harness/suite_a_test.go` — Suite A: rank correlation equivalence test
- `tools/harness/suite_b_test.go` — Suite B: staleness rank stability (v1: informational)
- `tools/harness/suite_c_test.go` — Suite C: concurrent safety + pile-on check
- `tools/harness/evolved_scorer_test.go` — test fixture (`testEndpointForScorer`) (see K.3)
- `prompts/validate.md` — Stage 5 prompt template
- `docs/transfer/noise_characterization.md` — noise characterization procedure template
- `docs/transfer/calibration_log.md` — calibration log template

**Files to modify:**
- `tools/harness/harness.go` — `LoadAlgorithm`: replace `trivialAlgorithm` return with `evolvedAlgorithm`
- `tools/harness/evolved_scorer.go` — wire `Score()` to use actual metric translation
- `tools/harness/harness_test.go` — update `TestEquivalence` placeholder; add `TestLoadAlgorithmReturnsEvolved`
- `tools/harness/go.mod` — add `github.com/llm-d/llm-d-inference-scheduler` require
- `go.work.sum` — updated by `go mod tidy` when workspace dependencies change (committed in Task 4 Step 6)
- `tools/transfer_cli.py` — add `noise-characterize` and `benchmark` commands
- `tools/test_transfer_cli.py` — add tests for new commands
- `docs/transfer/README.md` — PR5 deliverables section
- `docs/transfer/blis_to_llmd_mapping.md` — correct `RunningQueueSize`→`RunningRequestsSize` and `RunningRequestCount`→`RunningRequestsSize` field names (D-1 correction)

**Key decisions:**
1. `evolvedAlgorithm` calls `base.Route()` to get weighted base scores, then applies EVOLVE-BLOCK penalty logic verbatim (avoids reimplementing scorer functions which are unexported from sim package)
2. Suite A uses `scorer.ScoreEndpoints()` test helper from `BLISWeightedScorer` — keys by `NamespacedName.String()` which requires `testEndpoint.GetMetadata()` to return a NamespacedName using the snapshot ID as the `Name` field
3. `noise-characterize` reads a JSON file (not stdin) via `--runs` flag; `benchmark` reads a JSON file via `--results` flag and requires `--t-eff`
4. Suite A test tuples set `BatchSize=0` throughout to eliminate EffectiveLoad mapping ambiguity

**Confirmation: no dead artifacts** — all files are exercised by tests or consumed by the validate.md prompt.

### G) Task Breakdown

---

#### Task 1: Create validation_results.schema.json

**Contracts Implemented:** BC-1, BC-14 (prerequisite check)

**Files:**
- Create: `tools/schemas/validation_results.schema.json`

> **Note:** `stage4_output.schema.json` was originally planned here but has been removed. Stage 4 writes no workspace artifact on success (see `prompts/test.md` line 289). The Stage 5 prerequisite now verifies Stage 4 completion via direct `go build`/`go vet` in the target repo (see K.10 Step 0).

**Step 1: Author validation_results schema**

Create the JSON Schema for `workspace/validation_results.json`. Full content in Appendix K (Section K.1).

**Step 2: Validate cross-references**

```bash
# Verify schema loads without parse errors
python -c "
import json
with open('tools/schemas/validation_results.schema.json') as f:
    s = json.load(f)
print('validation_results schema OK, required fields:', s['required'])
"
```
Expected: `validation_results schema OK, required fields: ['suite_a', 'suite_b', 'suite_c', 'benchmark', 'overall_verdict', 'noise_cv']`

**Step 3: Verify no dead artifacts**

Consumer: `transfer_cli.py validate-schema` (existing command reads any schema in `tools/schemas/`) + Stage 5/6 prompts.

**Step 4: Commit**

```bash
git add tools/schemas/validation_results.schema.json
git commit -m "$(cat <<'EOF'
feat(schemas): add validation_results schema (BC-1, BC-14)

- validation_results.schema.json: Stage 5 output (suite_a/b/c, benchmark, verdict, noise_cv)
- Consumed by validate-schema CLI command and Stage 5/6 prompts

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 2: Implement evolvedAlgorithm

**Contracts Implemented:** BC-2, BC-3

**Language:** Go

**Files:**
- Create: `tools/harness/evolved_algorithm.go`
- Modify: `tools/harness/harness.go` (LoadAlgorithm return)
- Modify: `tools/harness/harness_test.go` (add TestLoadAlgorithmReturnsEvolved)

**Step 1: Write failing test**

Add to `harness_test.go`:

```go
func TestLoadAlgorithmReturnsEvolved(t *testing.T) {
    // BC-2: LoadAlgorithm should return evolvedAlgorithm (not trivialAlgorithm).
    // Under equal-load conditions, the evolved algorithm's output is determined by
    // KV utilization and cache hit rate (both 0 in this test) — scores are equal.
    // Under unequal-load, lower-load endpoint wins due to cubic penalty.
    repoRoot := findRepoRoot(t)
    summaryPath := filepath.Join(repoRoot, "workspace", "algorithm_summary.json")
    if _, err := os.Stat(summaryPath); err != nil {
        t.Skip("requires workspace/algorithm_summary.json (run extract first)")
    }

    alg, err := LoadAlgorithm(summaryPath, repoRoot)
    if err != nil {
        t.Fatalf("LoadAlgorithm: %v", err)
    }

    // High-load vs low-load: evolved algorithm should prefer lower load
    highLoad := sim.RouterState{
        Snapshots: []sim.RoutingSnapshot{
            {ID: "heavy", QueueDepth: 6, InFlightRequests: 2}, // load=8 → hard penalty
            {ID: "light", QueueDepth: 0, InFlightRequests: 1}, // load=1
        },
    }
    decision := alg.Route(&sim.Request{ID: "r1"}, &highLoad)
    if decision.TargetInstance != "light" {
        t.Errorf("expected 'light' (lower load), got %q", decision.TargetInstance)
    }

    // BC-3: KV pressure penalty fires when KVUtilization > 0.82
    kvState := sim.RouterState{
        Snapshots: []sim.RoutingSnapshot{
            {ID: "high-kv", QueueDepth: 0, KVUtilization: 0.90},
            {ID: "low-kv",  QueueDepth: 0, KVUtilization: 0.50},
        },
    }
    kvDecision := alg.Route(&sim.Request{ID: "r2"}, &kvState)
    if kvDecision.TargetInstance != "low-kv" {
        t.Errorf("expected 'low-kv' (lower KV), got %q", kvDecision.TargetInstance)
    }
    if kvDecision.Scores["high-kv"] >= kvDecision.Scores["low-kv"] {
        t.Errorf("expected high-kv score < low-kv score; got high=%f low=%f",
            kvDecision.Scores["high-kv"], kvDecision.Scores["low-kv"])
    }
}
```

**Step 2: Run test to verify it fails**

```bash
cd tools/harness && go test -run TestLoadAlgorithmReturnsEvolved -v
```
Expected: FAIL — `LoadAlgorithm` returns trivialAlgorithm; trivialAlgorithm uses `1/(1+EffectiveLoad)` which may select `light` by coincidence but `high-kv` won't lose on KV score.

**Step 3: Implement evolved_algorithm.go**

Full content in Appendix K (Section K.2). Key structure:

```go
type evolvedAlgorithm struct {
    base sim.RoutingPolicy // *WeightedScoring with DefaultScorerConfigs
}

func newEvolvedAlgorithm() *evolvedAlgorithm {
    return &evolvedAlgorithm{
        base: sim.NewRoutingPolicy("weighted", sim.DefaultScorerConfigs(), 64),
    }
}

func (a *evolvedAlgorithm) Route(req *sim.Request, state *sim.RouterState) sim.RoutingDecision {
    // 1. Get base weighted scores from WeightedScoring
    baseDecision := a.base.Route(req, state)
    scores := make(map[string]float64, len(state.Snapshots))
    for id, s := range baseDecision.Scores {
        scores[id] = s
    }
    // 2. Apply EVOLVE-BLOCK penalties (routing/best_program.py:200-230)
    // [load penalty, cache affinity, KV penalty, hard load penalties]
    // 3. Argmax (ties broken by first occurrence, matching EVOLVE-BLOCK:234-241)
    // 4. Return NewRoutingDecisionWithScores
}
```

Modify `LoadAlgorithm` in `harness.go` — replace the final `return &trivialAlgorithm{}, nil` with:
```go
return newEvolvedAlgorithm(), nil
```

**Step 4: Run test to verify it passes**

```bash
cd tools/harness && go test -run TestLoadAlgorithmReturnsEvolved -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
cd tools/harness && go test ./... -v
```
Expected: all existing tests pass (TestEquivalenceTrivial uses `trivialAlgorithm` directly, not via LoadAlgorithm)

**Step 6: Commit**

```bash
git add tools/harness/evolved_algorithm.go tools/harness/harness.go tools/harness/harness_test.go
git commit -m "$(cat <<'EOF'
feat(harness): implement evolvedAlgorithm with EVOLVE-BLOCK penalties (BC-2, BC-3)

- Add evolved_algorithm.go with EVOLVE-BLOCK penalty logic from routing/best_program.py:171-241
- Base scoring uses inference-sim WeightedScoring (DefaultScorerConfigs: prefix-affinity:3, queue-depth:2, kv-utilization:2)
- Penalties: cubic load, KV pressure, hard load thresholds
- LoadAlgorithm now returns evolvedAlgorithm instead of trivialAlgorithm

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 3: Wire EvolvedScorer.Score()

**Contracts Implemented:** BC-4, BC-5, BC-15

**Language:** Go

**Files:**
- Modify: `tools/harness/evolved_scorer.go`
- Create: `tools/harness/evolved_scorer_test.go` (test fixture: `testEndpointForScorer` type — see K.3; created in Step 1a before test is added)
- Modify: `tools/harness/harness_test.go` (add TestEvolvedScorerScoresCorrectly; update TestEvolvedScorerContract — see Step 3b below)

**Step 1a: Create test fixture file (evolved_scorer_test.go)**

Create `tools/harness/evolved_scorer_test.go` with the `testEndpointForScorer` test helper type (see K.3 NOTE). This file must exist before Step 1b so the test in `harness_test.go` can compile.

Content: copy the `testEndpointForScorer` type and its method implementations from K.3 (lines after the NOTE). This includes the type definition and all interface methods (`GetMetadata`, `GetMetrics`, `String`, `Get`, `Put`, `Keys`).

**Step 1b: Write failing test (TestEvolvedScorerScoresCorrectly)**

Add the new test for actual scoring behavior. Note: the existing `TestEvolvedScorerContract` will break after Step 3 wires Score() — Step 3b below fixes it by updating the assertion from `score != 0.5` to `score != 1.0`.

```go
func TestEvolvedScorerScoresCorrectly(t *testing.T) {
    // BC-4: metric translation; BC-5: session header.
    alg := newEvolvedAlgorithm()
    scorer := NewEvolvedScorer(alg).WithName("test")

    // Two endpoints: high-load should score lower.
    // Uses testEndpointForScorer defined in evolved_scorer_test.go (moved from K.3 production
    // file to a _test.go file — test fixtures must not live in production code).
    heavy := &testEndpointForScorer{
        id: "heavy",
        metrics: &fwkdl.Metrics{
            WaitingQueueSize:    5,
            RunningRequestsSize: 3, // EffectiveLoad=8 → hard penalty (load>7)
            KVCacheUsagePercent: 50.0,
        },
    }
    light := &testEndpointForScorer{
        id: "light",
        metrics: &fwkdl.Metrics{
            WaitingQueueSize:    0,
            RunningRequestsSize: 1, // EffectiveLoad=1
            KVCacheUsagePercent: 30.0,
        },
    }

    scores := scorer.Score(context.Background(), nil, nil, []scheduling.Endpoint{heavy, light})
    if len(scores) != 2 {
        t.Fatalf("expected 2 scores, got %d", len(scores))
    }
    if scores[heavy] >= scores[light] {
        t.Errorf("expected light > heavy; got heavy=%f light=%f", scores[heavy], scores[light])
    }

    // BC-5: session header extraction
    req := &scheduling.LLMRequest{
        RequestId: "req-sess",
        Headers:   map[string]string{"x-session-token": "sess-abc"},
    }
    // Session token should influence routing when CacheHitRate > 0 — but v1 uses zero fallback
    // so result is same as no-session. Verify no panic and session flag is correctly parsed.
    scoresWithSess := scorer.Score(context.Background(), nil, req, []scheduling.Endpoint{heavy, light})
    if len(scoresWithSess) != 2 {
        t.Fatalf("session request: expected 2 scores, got %d", len(scoresWithSess))
    }
}
```

Also add a compile-time check for BC-15 in evolved_scorer.go (ensure no non-existent fields):
The check is implicit: `go build` fails if we reference non-existent fields.

**Step 2: Run test to verify it fails**

```bash
cd tools/harness && go test -run TestEvolvedScorerScoresCorrectly -v
```
Expected: FAIL — Score() currently returns 0.5 uniform (PR3 placeholder), so `scores[heavy] >= scores[light]` (both 0.5) triggers the `t.Errorf`. The test fixture `testEndpointForScorer` was created in Step 1a, so the test compiles successfully but fails on the behavioral assertion.

**Step 3: Implement wired EvolvedScorer.Score()**

Full content in Appendix K (Section K.3). Key metric translation:

```go
func (s *EvolvedScorer) Score(_ context.Context, _ *scheduling.CycleState, req *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
    result := make(map[scheduling.Endpoint]float64, len(endpoints))
    if len(endpoints) == 0 {
        return result
    }

    snapshots := make([]sim.RoutingSnapshot, 0, len(endpoints))

    for _, ep := range endpoints {
        m := ep.GetMetrics()
        if m == nil {
            result[ep] = 0.0
            continue
        }
        id := ep.String()
        snap := sim.RoutingSnapshot{
            ID:               id,
            QueueDepth:       m.WaitingQueueSize,
            InFlightRequests: m.RunningRequestsSize, // F-10: single-count, BatchSize=0
            KVUtilization:    NormalizeKVUtilization(m.KVCacheUsagePercent),
            // CacheHitRate: implicitly 0.0 — no production field available.
        }
        snapshots = append(snapshots, snap)
    }

    simReq := sim.Request{ID: "prod-request"}
    if req != nil {
        if req.RequestId != "" {
            simReq.ID = req.RequestId
        }
        if req.Headers != nil {
            simReq.SessionID = req.Headers[sessionTokenHeader]
        }
    }

    state := sim.RouterState{Snapshots: snapshots}
    decision := s.alg.Route(&simReq, &state)

    for _, ep := range endpoints {
        if ep.GetMetrics() == nil {
            continue // already scored 0.0 above
        }
        id := ep.String()
        if score, ok := decision.Scores[id]; ok {
            result[ep] = score
        }
    }
    return result
}
```

**Step 3b: Update TestEvolvedScorerContract**

After wiring Score(), `TestEvolvedScorerContract` (harness_test.go:340-386) will fail.
The existing test uses `trivialAlgorithm{}` with `mockEndpoint` (all-zero metrics).
After wiring, Score() calls alg.Route() → trivialAlgorithm computes `1/(1+EffectiveLoad)`.
With all-zero metrics: EffectiveLoad=0 → score=1.0, not 0.5.

Update the assertion at line 373 from `score != 0.5` to `score != 1.0`:

```go
// PR3 placeholder returned 0.5; wired Score() delegates to trivialAlgorithm
// which computes 1/(1+0) = 1.0 for mockEndpoint (all-zero metrics).
if score != 1.0 {
    t.Errorf("score = %f, want 1.0 (trivialAlgorithm with zero-load endpoint)", score)
}
```

Also update the comment at line 358 from "Score returns 0.5 for each endpoint (PR3 placeholder contract)" to:
```go
// Score returns 1.0 for each endpoint (trivialAlgorithm with zero-load mockEndpoint)
```

**Step 4: Run test to verify it passes**

```bash
cd tools/harness && go test -run TestEvolvedScorerScoresCorrectly -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
cd tools/harness && go build ./... && go test ./... -v
```
Expected: all tests pass. Note: TestEvolvedScorerContract passes because Step 3b updated the assertion from 0.5 to 1.0.

**Step 6: Commit**

```bash
git add tools/harness/evolved_scorer.go tools/harness/harness_test.go tools/harness/evolved_scorer_test.go
git commit -m "$(cat <<'EOF'
feat(harness): wire EvolvedScorer.Score() with production metric translation (BC-4, BC-5, BC-15)

- Map WaitingQueueSize→QueueDepth, RunningRequestsSize→InFlightRequests (F-10 single-count)
- Normalize KVCacheUsagePercent/100 → KVUtilization via NormalizeKVUtilization
- Read SessionID from request.Headers["x-session-token"]
- CacheHitRate zero fallback (no production field available)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4: Kendall-tau utility and Suite A

**Contracts Implemented:** BC-6, BC-7

**Language:** Go

**Files:**
- Create: `tools/harness/stats.go`
- Create: `tools/harness/suite_a_test.go`
- Modify: `tools/harness/go.mod` (add llm-d-inference-scheduler require)

**Step 1: Write failing test**

Create `tools/harness/suite_a_test.go`. The full test is in Appendix K (Section K.5). Key test:

```go
func TestSuiteA_KendallTau(t *testing.T) {
    // BC-6: mean Kendall-tau rank correlation > 0.8 across 200 canonical tuples.
    // Canonical tuples: BatchSize=0, CacheHitRate=0 for all endpoints.
    // Full implementation in Appendix K (Section K.5).
    repoRoot := findRepoRoot(t)
    summaryPath := filepath.Join(repoRoot, "workspace", "algorithm_summary.json")
    if _, err := os.Stat(summaryPath); err != nil {
        t.Skip("requires workspace/algorithm_summary.json (run extract first)")
    }

    alg, err := LoadAlgorithm(summaryPath, repoRoot)
    if err != nil {
        t.Fatalf("LoadAlgorithm: %v", err)
    }

    params := blis.BLISWeightedScorerParameters{Enabled: true}
    prodScorer := blis.NewBLISWeightedScorer(context.Background(), params).WithName("suite-a")

    tuples := generateCanonicalTuples(200)
    tauSum := 0.0
    maxAbsErr := 0.0
    nonSkipped := 0

    for i, tuple := range tuples {
        simDecision := alg.Route(&tuple.Request, &tuple.State)
        if len(simDecision.Scores) == 0 {
            continue
        }
        nonSkipped++

        endpoints := make([]scheduling.Endpoint, len(tuple.State.Snapshots))
        for j, snap := range tuple.State.Snapshots {
            endpoints[j] = endpointFromSnap(snap)
        }
        rawProdScores := blis.ScoreEndpoints(context.Background(), prodScorer, nil, endpoints)

        // Align keys: rawProdScores keyed by "/<id>", simDecision.Scores by "<id>"
        prodScores := make(map[string]float64, len(rawProdScores))
        for _, snap := range tuple.State.Snapshots {
            prodScores[snap.ID] = rawProdScores["/"+snap.ID]
        }

        tau := KendallTau(simDecision.Scores, prodScores)
        tauSum += tau
        absErr := MaxAbsDiff(simDecision.Scores, prodScores)
        if absErr > maxAbsErr {
            maxAbsErr = absErr
        }
    }

    meanTau := tauSum / float64(nonSkipped)
    if meanTau <= 0.8 {
        t.Errorf("Suite A FAIL: mean Kendall-tau = %.3f, want > 0.8 (%d tuples)", meanTau, nonSkipped)
    }
    t.Logf("Suite A: mean Kendall-tau = %.3f, max_abs_error = %.6f (%d tuples)", meanTau, maxAbsErr, nonSkipped)
}
```

**Step 2: Run test to verify it fails**

```bash
cd tools/harness && go test -run TestSuiteA -v 2>&1 | head -30
```
Expected: compile error (stats.go with KendallTau, MaxAbsDiff not yet defined)

**Step 3: Implement stats.go and suite_a_test.go**

Full content in Appendix K (Sections K.4 and K.5).

Also update `tools/harness/go.mod` to add:
```
require (
    github.com/llm-d/llm-d-inference-scheduler v0.0.0
)

replace github.com/llm-d/llm-d-inference-scheduler => ../../llm-d-inference-scheduler
```

Run `go mod tidy` in the harness directory to update go.sum.

**Step 4: Run test to verify it passes**

```bash
cd tools/harness && go test -run TestSuiteA -v -timeout 60s
```
Expected: PASS with logged tau > 0.8

**Step 5: Run full test suite**

```bash
cd tools/harness && go build ./... && go test ./... -v -timeout 120s
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add tools/harness/stats.go tools/harness/suite_a_test.go tools/harness/go.mod tools/harness/go.sum go.work.sum
git commit -m "$(cat <<'EOF'
feat(harness): add Suite A equivalence test + Kendall-tau utility (BC-6, BC-7)

- stats.go: KendallTau rank correlation computation
- suite_a_test.go: 200-tuple equivalence test comparing evolvedAlgorithm vs BLISWeightedScorer
- go.mod: add llm-d-inference-scheduler dependency (go workspace resolution)
- Canonical tuples: BatchSize=0, CacheHitRate=0 (eliminates EffectiveLoad mapping ambiguity)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 5: Suite B (staleness rank stability, v1 informational)

**Contracts Implemented:** BC-8

**Language:** Go

**Files:**
- Create: `tools/harness/suite_b_test.go`

**Step 1: Write failing test**

```go
func TestSuiteB_StatenessStability(t *testing.T) {
    // BC-8: Suite B results must be marked informational_only=true.
    // All v1 signals have staleness_window_ms=0; zero perturbation means trivially passes.
    // Suite infrastructure runs and records; results excluded from overall_verdict.
    result := runSuiteB(t)
    if !result.InformationalOnly {
        t.Error("Suite B: expected informational_only=true for v1 approximate-scorer signals")
    }
    if !result.Passed {
        t.Errorf("Suite B: expected passed=true (trivially), got tau=%.3f", result.RankStabilityTau)
    }
    // tau should be 1.0 (zero perturbation = identical rankings)
    if result.RankStabilityTau < 0.99 {
        t.Errorf("Suite B: rank_stability_tau = %.3f, want ~1.0 (zero staleness)", result.RankStabilityTau)
    }
}
```

**Step 2: Run test to verify it fails**

```bash
cd tools/harness && go test -run TestSuiteB -v
```
Expected: compile error (runSuiteB not defined)

**Step 3: Implement suite_b_test.go**

Full content in Appendix K (Section K.6).

**Step 4: Run test to verify it passes**

```bash
cd tools/harness && go test -run TestSuiteB -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
cd tools/harness && go test ./... -v -timeout 120s
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add tools/harness/suite_b_test.go
git commit -m "$(cat <<'EOF'
feat(harness): add Suite B staleness rank stability (v1 informational) (BC-8)

- suite_b_test.go: zero-perturbation staleness test (trivially passes in v1)
- All signals have staleness_window_ms=0; informational_only=true
- Infrastructure present for future precise-scorer (ZMQ-based) transfers

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6: Suite C (concurrent safety + pile-on)

**Contracts Implemented:** BC-9, BC-10

**Language:** Go

**Files:**
- Create: `tools/harness/suite_c_test.go`

**Step 1: Write failing test**

```go
func TestSuiteC_ConcurrentDeterminism(t *testing.T) {
    // BC-9: 20 concurrent goroutines with identical inputs produce identical scores.
    // Each goroutine creates its own evolvedAlgorithm instance because the underlying
    // WeightedScoring includes prefix-affinity scorer with unsynchronized closure state
    // (cachedHashes, cachedReqID in routing_prefix_scorer.go:27-28). Sharing a single
    // instance would cause data races detectable by -race.
    tuple := TestTuple{
        Request: sim.Request{ID: "concurrent-req"},
        State: sim.RouterState{
            Snapshots: []sim.RoutingSnapshot{
                {ID: "ep-0", QueueDepth: 2, KVUtilization: 0.3},
                {ID: "ep-1", QueueDepth: 5, KVUtilization: 0.7},
                {ID: "ep-2", QueueDepth: 1, KVUtilization: 0.5},
            },
        },
    }

    results := make([]sim.RoutingDecision, 20)
    var wg sync.WaitGroup
    for i := range results {
        wg.Add(1)
        go func(idx int) {
            defer wg.Done()
            alg := newEvolvedAlgorithm() // per-goroutine instance (BC-9 mechanism)
            results[idx] = alg.Route(&tuple.Request, &tuple.State)
        }(i)
    }
    wg.Wait()

    // All results must be identical (same target, same scores)
    ref := results[0]
    for i, r := range results[1:] {
        if r.TargetInstance != ref.TargetInstance {
            t.Errorf("goroutine %d: target %q != ref %q", i+1, r.TargetInstance, ref.TargetInstance)
        }
        for id, s := range ref.Scores {
            if r.Scores[id] != s {
                t.Errorf("goroutine %d: score[%s] = %f, ref = %f", i+1, id, r.Scores[id], s)
            }
        }
    }
}

func TestSuiteC_PileOn(t *testing.T) {
    // BC-10: no endpoint selected > 2x fair share across 100 varied-load tuples.
    alg := newEvolvedAlgorithm()
    const N = 5    // endpoints
    const M = 100  // routing decisions

    counts := make(map[string]int, N)
    // Generate tuples where each endpoint has lowest load ~M/N times
    for i := 0; i < M; i++ {
        snaps := make([]sim.RoutingSnapshot, N)
        for j := 0; j < N; j++ {
            snaps[j] = sim.RoutingSnapshot{
                ID:         fmt.Sprintf("ep-%d", j),
                QueueDepth: 3, // base load
            }
        }
        // Make endpoint (i % N) the least loaded
        snaps[i%N].QueueDepth = 0
        state := sim.RouterState{Snapshots: snaps}
        dec := alg.Route(&sim.Request{ID: fmt.Sprintf("req-%d", i)}, &state)
        counts[dec.TargetInstance]++
    }

    fairShare := float64(M) / N
    maxAllowed := 2.0 * fairShare
    for id, cnt := range counts {
        if float64(cnt) > maxAllowed {
            t.Errorf("endpoint %s: selected %d times (> 2x fair share %.0f)", id, cnt, maxAllowed)
        }
    }
}
```

**Step 2: Run test to verify it fails**

```bash
cd tools/harness && go test -run TestSuiteC -v
```
Expected: compile error (sync import missing)

**Step 3: Implement suite_c_test.go**

Full content in Appendix K (Section K.7).

**Step 4: Run test to verify it passes**

```bash
cd tools/harness && go test -run TestSuiteC -v -race
```
Expected: PASS with `-race` flag (concurrent safety verified)

**Step 5: Run full test suite**

```bash
cd tools/harness && go build ./... && go test ./... -v -race -timeout 120s
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add tools/harness/suite_c_test.go
git commit -m "$(cat <<'EOF'
feat(harness): add Suite C concurrent safety and pile-on tests (BC-9, BC-10)

- TestSuiteC_ConcurrentDeterminism: 20 goroutines with per-goroutine algorithm instances, identical inputs → identical outputs
- TestSuiteC_PileOn: 100 varied-load decisions, no endpoint > 2x fair share
- Tests pass with -race flag (per-goroutine instances avoid prefix-affinity scorer closure races)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 7: noise-characterize CLI command

**Contracts Implemented:** BC-11, BC-12, BC-16

**Language:** Python

**Files:**
- Modify: `tools/transfer_cli.py` (add `noise-characterize` subcommand)
- Modify: `tools/test_transfer_cli.py` (add noise-characterize tests)

**Step 1: Write failing test**

Add to `test_transfer_cli.py`:

```python
import json, tempfile, os, sys

# All noise-characterize and benchmark tests use tmp_path (outside REPO_ROOT).
# Set _SIM2REAL_ALLOWED_ROOT=tmp_path so the path security guard accepts these paths.
# In production, _SIM2REAL_ALLOWED_ROOT is unset, so the guard defaults to REPO_ROOT.

def test_noise_characterize_halts_on_high_cv(tmp_path):
    """BC-11: CV > 15% causes halt=true and exit code 1."""
    runs = {"runs": [{"p99": v} for v in [0.40, 0.80, 0.20, 0.60, 0.30]]}  # CV ≈ 0.47
    runs_file = tmp_path / "baseline_runs.json"
    runs_file.write_text(json.dumps(runs))

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "noise-characterize", "--runs", str(runs_file)],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["halt"] is True
    assert output["status"] == "error"

def test_noise_characterize_malformed_input(tmp_path):
    """BC-16: malformed JSON input causes exit code 2 (infrastructure error)."""
    runs_file = tmp_path / "bad_runs.json"
    runs_file.write_text("{invalid json")

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "noise-characterize", "--runs", str(runs_file)],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["status"] == "error"

def test_noise_characterize_empty_runs(tmp_path):
    """BC-16: empty runs list is infrastructure error (no data to compute CV) — exit code 2."""
    runs_file = tmp_path / "empty_runs.json"
    runs_file.write_text('{"runs": []}')

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "noise-characterize", "--runs", str(runs_file)],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["status"] == "error"

def test_noise_characterize_t_eff_computation(tmp_path):
    """BC-12: T_eff = max(0.05, 2*max_cv) using sample std (Bessel's correction)."""
    # Low-variance runs: sample CV ≈ 0.028 (Bessel's correction, n-1=4)
    # → T_eff = max(0.05, 2*0.028) ≈ 0.056
    runs = {"runs": [{"p99": v} for v in [0.40, 0.42, 0.41, 0.39, 0.41]]}
    runs_file = tmp_path / "baseline_runs.json"
    runs_file.write_text(json.dumps(runs))

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "noise-characterize", "--runs", str(runs_file)],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stdout}"
    output = json.loads(result.stdout)
    assert output["halt"] is False
    assert output["status"] == "ok"
    # Verify per_metric_cv is present and contains the expected metric
    assert "per_metric_cv" in output, "missing per_metric_cv in output"
    assert "p99" in output["per_metric_cv"], "missing p99 in per_metric_cv"
    # Bessel's correction: mean=0.406, sample_std≈0.01140, CV≈0.02809
    expected_cv = 0.02809
    assert abs(output["per_metric_cv"]["p99"] - expected_cv) < 0.002, \
        f"Expected p99 CV ≈ {expected_cv}, got {output['per_metric_cv']['p99']}"
    # T_eff = max(0.05, 2*0.02809) ≈ 0.0562
    expected_t_eff = max(0.05, 2 * expected_cv)
    assert abs(output["t_eff"] - expected_t_eff) < 0.002, \
        f"Expected T_eff ≈ {expected_t_eff}, got {output['t_eff']}"
```

**Step 2: Run test to verify it fails**

```bash
cd /path/to/sim2real && source .venv/bin/activate
python -m pytest tools/test_transfer_cli.py::test_noise_characterize_halts_on_high_cv -v
```
Expected: FAIL (noise-characterize command not found)

**Step 3: Implement noise-characterize command**

Full implementation in Appendix K (Section K.8). Key logic:
- Read JSON file `{"runs": [{"p50": ..., "p95": ..., "p99": ...}]}`
- For each metric, compute CV = std/mean (sample variance, dividing by n-1)
- halt = max_cv > 0.15
- t_eff = max(0.05, 2 * max_cv)
- Output: `{"status": "ok"|"error", "per_metric_cv": {...}, "t_eff": float, "halt": bool, "errors": []}`

**IMPORTANT:** Both `noise-characterize` and `benchmark` (K.8, K.9) call `_output()` with keyword arguments (`per_metric_cv`, `t_eff`, `halt`, `mechanism_check_verdict`, etc.). Before implementing, verify the existing `_output()` function signature in `tools/transfer_cli.py` accepts `**kwargs` or update it to do so. If `_output` uses a fixed schema, add the new kwargs to its signature.

**IMPORTANT:** Add `import os` to the module-level imports in `tools/transfer_cli.py` before implementing K.8/K.9. Both commands use `os.environ["_SIM2REAL_ALLOWED_ROOT"]` for path security guards. The existing `os` import (`import os as _os`) is scoped inside `cmd_extract` and not visible at module level.

**Step 4: Run test to verify it passes**

```bash
python -m pytest tools/test_transfer_cli.py -k "noise_characterize" -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
python -m pytest tools/ -v
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add noise-characterize command (BC-11, BC-12, BC-16)

- Reads baseline_runs.json with p50/p95/p99 per run
- Computes per-metric CV; T_eff = max(0.05, 2*max_cv)
- halt=true and exit 1 when max CV > 15%
- Exit 2 on malformed input (infrastructure error)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 8: benchmark CLI command

**Contracts Implemented:** BC-13, BC-17

**Language:** Python

**Files:**
- Modify: `tools/transfer_cli.py` (add `benchmark` subcommand)
- Modify: `tools/test_transfer_cli.py` (add benchmark tests)

**Step 1: Write failing test**

```python
def test_benchmark_mechanism_check_pass(tmp_path):
    """BC-13: improvement >= T_eff for matched workload → PASS."""
    results = {
        "workloads": [
            {"name": "chatbot", "classification": "matched",
             "baseline_p99": 0.45, "transfer_p99": 0.38},  # improvement ≈ 15.6%
            {"name": "batch",   "classification": "unmatched",
             "baseline_p99": 0.30, "transfer_p99": 0.31},  # change ≈ -3.3% (within T_eff)
        ]
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(results))

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file), "--t-eff", "0.10"],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["mechanism_check_verdict"] == "PASS"
    assert output["status"] == "ok"

def test_benchmark_mechanism_check_inconclusive(tmp_path):
    """Improvement > 0 but < T_eff for all matched workloads → INCONCLUSIVE (exit 0)."""
    results = {
        "workloads": [
            {"name": "chatbot", "classification": "matched",
             "baseline_p99": 0.45, "transfer_p99": 0.44},  # improvement ≈ 2.2% < T_eff=0.10
        ]
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(results))

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file), "--t-eff", "0.10"],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, f"INCONCLUSIVE should exit 0, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["mechanism_check_verdict"] == "INCONCLUSIVE"
    assert output["status"] == "inconclusive", \
        f"INCONCLUSIVE should have status='inconclusive', got '{output['status']}'"

def test_benchmark_mechanism_check_fail(tmp_path):
    """All matched workload improvements <= 0 → FAIL (exit 1)."""
    results = {
        "workloads": [
            {"name": "chatbot", "classification": "matched",
             "baseline_p99": 0.45, "transfer_p99": 0.50},  # regression: improvement < 0
        ]
    }
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps(results))

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file), "--t-eff", "0.10"],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 1, f"FAIL should exit 1, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["mechanism_check_verdict"] == "FAIL"

def test_benchmark_requires_t_eff(tmp_path):
    """BC-17: missing --t-eff → exit 1."""
    results_file = tmp_path / "results.json"
    results_file.write_text('{"workloads": []}')

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file)],  # no --t-eff
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 1
    output = json.loads(result.stdout)
    assert "t-eff" in output["errors"][0].lower()

def test_benchmark_malformed_input(tmp_path):
    """BC-18: malformed JSON input causes exit code 2 (infrastructure error)."""
    results_file = tmp_path / "bad_results.json"
    results_file.write_text("{invalid json")

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file), "--t-eff", "0.10"],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["status"] == "error"

def test_benchmark_missing_workloads_key(tmp_path):
    """BC-18: valid JSON missing 'workloads' key causes exit code 2."""
    results_file = tmp_path / "no_workloads.json"
    results_file.write_text('{"other_key": 123}')

    env = {**os.environ, "_SIM2REAL_ALLOWED_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "tools/transfer_cli.py", "benchmark",
         "--results", str(results_file), "--t-eff", "0.10"],
        capture_output=True, text=True, env=env
    )
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}"
    output = json.loads(result.stdout)
    assert output["status"] == "error"
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tools/test_transfer_cli.py::test_benchmark_mechanism_check_pass -v
```
Expected: FAIL (benchmark command not found)

**Step 3: Implement benchmark command**

Full implementation in Appendix K (Section K.9). Key logic:
- Read JSON file with workloads list (classification, baseline_p99, transfer_p99)
- For matched workloads: improvement = (baseline - transfer) / baseline
- mechanism_check_verdict = "PASS" if any matched workload improvement >= t_eff
- mechanism_check_verdict = "INCONCLUSIVE" if improvement > 0 but < t_eff for all matched
- mechanism_check_verdict = "FAIL" if improvement <= 0 for all matched
- specificity_check: all unmatched workloads have |change| / baseline < t_eff

**Step 4: Run test to verify it passes**

```bash
python -m pytest tools/test_transfer_cli.py -k "benchmark" -v
```
Expected: PASS

**Step 5: Run full test suite**

```bash
python -m pytest tools/ -v
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): add benchmark command with mechanism check (BC-13, BC-17)

- Reads benchmark_results.json with matched/unmatched workload classifications
- Mechanism check: any matched workload improvement >= T_eff → PASS
- Specificity check: all unmatched workloads within T_eff of baseline
- INCONCLUSIVE if improvement > 0 but < T_eff; FAIL if no improvement
- --t-eff required; exit 1 if missing

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 9: Stage 5 prompt template

**Contracts Implemented:** BC-14, BC-15

**Files:**
- Create: `prompts/validate.md`

**Step 1: Author prompt template**

Full content in Appendix K (Section K.10). The prompt must contain all 4 required sections.

**Step 2: Verify structural completeness**

Check that the prompt contains all 4 required sections:
- [x] Prerequisites: `workspace/signal_coverage.json`, `workspace/stage3_output.json`, noise-characterize must have been run
- [x] Validation steps: run `go test ./tools/harness/... -run SuiteA`, `SuiteB`, `SuiteC`; run noise-characterize; run benchmark; write validation_results.json; run validate-schema
- [x] Halt conditions: Suite A FAIL, Suite C FAIL, noise CV > 15%, mechanism check INCONCLUSIVE
- [x] Expected outputs: `workspace/validation_results.json` with all required fields, calibration log entry

**Step 3: Verify predecessor artifact checks**

The prompt instructs:
1. `python tools/transfer_cli.py validate-schema workspace/signal_coverage.json` (halts if missing/invalid)
2. `python tools/transfer_cli.py validate-schema workspace/stage3_output.json` (halts if missing/invalid)
3. Both must exist before any harness commands run

**Step 4: Commit**

```bash
git add prompts/validate.md
git commit -m "$(cat <<'EOF'
docs(prompts): add validate.md Stage 5 prompt (BC-14, BC-15)

- Stage 5: noise characterization → Suite A/B/C → cluster benchmarks → verdict
- Prerequisites: signal_coverage.json, stage3_output.json
- Halt conditions: Suite A/C FAIL, noise CV > 15%, INCONCLUSIVE mechanism check
- Instructs operator to write workspace/validation_results.json and run validate-schema

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 10: Documentation artifacts and README update

**Contracts Implemented:** (support for all BCs via documentation)

**Files:**
- Create: `docs/transfer/noise_characterization.md`
- Create: `docs/transfer/calibration_log.md`
- Modify: `docs/transfer/README.md`
- Modify: `docs/transfer/blis_to_llmd_mapping.md` (D-1 field-name corrections)

**Step 1a: Author artifacts**

Full content for both templates in Appendix K (Sections K.11 and K.12).

**Step 1b: Correct D-1 field names in mapping artifact**

Apply the following corrections to `docs/transfer/blis_to_llmd_mapping.md` (Deviation D-1: `RunningQueueSize` and `RunningRequestCount` do not exist in `fwkdl.Metrics`; the actual field is `RunningRequestsSize`):

1. **Signal Mapping Table — BatchSize row (Prod Access Path):** Change `RunningQueueSize` to `RunningRequestsSize`. Add a `CORRECTED in PR5` annotation noting the old field name did not exist.
2. **Signal Mapping Table — InFlightRequests row (Prod Access Path):** Change `RunningRequestCount` to `RunningRequestsSize`. Add a `CORRECTED in PR5` annotation noting the old field name did not exist.
3. **Composite Signals table — EffectiveLoad() row:** Change any occurrences of `RunningQueueSize` or `RunningRequestCount` to `RunningRequestsSize`. Add a `CORRECTED in PR5` annotation.
4. **Metric access (VERIFIED) section (under Scorer Interface Reference):** Change `RunningQueueSize` and `RunningRequestCount` to `RunningRequestsSize`. Update the verification note to mark as `VERIFIED in PR5` with a `CORRECTED` annotation explaining the old field names.
5. **Signal Mapping Table header note:** Add a verification note after the table header: `> **VERIFIED (PR5):** Field names have been verified against fwkdl.Metrics at commit 091312c. Previous field names RunningQueueSize and RunningRequestCount were corrected to RunningRequestsSize in PR5.`

After edits, verify no stale field names remain:

```bash
grep -n "RunningQueueSize\|RunningRequestCount" docs/transfer/blis_to_llmd_mapping.md
```

**HALT if grep finds any occurrence that is not inside a `CORRECTED` annotation.** All surviving occurrences must be in "previously documented as" or "CORRECTED in PR5" context only.

Update `docs/transfer/README.md` to add a PR5 deliverables section. Add the following content after the existing PR4 section:

```markdown
## PR5: Validation Pipeline (Stage 5)

**Deliverables:**
- `tools/harness/evolved_algorithm.go` — EVOLVE-BLOCK penalty logic (replaces trivialAlgorithm in LoadAlgorithm)
- `tools/harness/evolved_scorer.go` — Wired EvolvedScorer.Score() with production metric translation
- `tools/harness/stats.go` — Kendall-tau rank correlation utility
- `tools/harness/suite_a_test.go` — Suite A: 200-tuple rank correlation equivalence (BC-6, BC-7)
- `tools/harness/suite_b_test.go` — Suite B: staleness rank stability, v1 informational (BC-8)
- `tools/harness/suite_c_test.go` — Suite C: concurrent safety + pile-on check (BC-9, BC-10)
- `tools/schemas/validation_results.schema.json` — Schema for Stage 5 output artifact
- `prompts/validate.md` — Stage 5 prompt template
- `docs/transfer/noise_characterization.md` — Noise characterization procedure
- `docs/transfer/calibration_log.md` — Per-transfer calibration log template
- CLI commands: `noise-characterize`, `benchmark` in `tools/transfer_cli.py`

**Key contracts:** Suite A Kendall-tau > 0.8, Suite C determinism + pile-on ≤ 2.0, noise CV ≤ 15%
```

**Step 2: Validate cross-references**

```bash
# Verify noise_characterization.md references correct CLI command
grep "noise-characterize" docs/transfer/noise_characterization.md
# Verify calibration_log.md has required schema fields
grep "transfer_date\|overall_verdict\|single_run_provisional" docs/transfer/calibration_log.md
# Verify README references PR5 deliverables
grep "PR5\|validate.md\|Suite A\|noise" docs/transfer/README.md
```

**Step 3: Verify no dead artifacts**

- `noise_characterization.md`: consumed by operator as standalone procedure reference (not referenced by `validate.md`; `validate.md` embeds its own noise-characterize instructions inline)
- `calibration_log.md`: consumed by Stage 6 prompt (PR6 appends entry)
- README: consumed by operators at runtime

**Step 4: Update TestEquivalence placeholder**

The PR3 placeholder `TestEquivalence` in `harness_test.go` now has suites A/B/C. Update it to call them:

```go
func TestEquivalence(t *testing.T) {
    // Convenience dispatcher — runs all three suites sequentially in one invocation.
    // NOTE: validate.md (K.10) runs suites independently via separate go test -run commands
    // (TestSuiteA_KendallTau, TestSuiteB_StatenessStability, TestSuiteC).
    // This function is for local development use only.
    t.Run("SuiteA", TestSuiteA_KendallTau)
    t.Run("SuiteB", TestSuiteB_StatenessStability)
    t.Run("SuiteC_Concurrent", TestSuiteC_ConcurrentDeterminism)
    t.Run("SuiteC_PileOn", TestSuiteC_PileOn)
}
```

**Step 5: Commit**

```bash
git add docs/transfer/noise_characterization.md docs/transfer/calibration_log.md docs/transfer/README.md docs/transfer/blis_to_llmd_mapping.md tools/harness/harness_test.go
git commit -m "$(cat <<'EOF'
docs(transfer): add noise characterization and calibration log templates; update README and mapping artifact

- docs/transfer/noise_characterization.md: noise characterization procedure
- docs/transfer/calibration_log.md: per-transfer calibration log template
- docs/transfer/README.md: PR5 deliverables section
- docs/transfer/blis_to_llmd_mapping.md: correct RunningQueueSize→RunningRequestsSize and RunningRequestCount→RunningRequestsSize (D-1)
- TestEquivalence now dispatches SuiteA/B/C subtests

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 | Task 1 | Schema validation | `python -c "json.load(open('tools/schemas/validation_results.schema.json'))"` |
| BC-2 | Task 2 | Unit (Go) | `TestLoadAlgorithmReturnsEvolved` — lower-load endpoint wins |
| BC-3 | Task 2 | Unit (Go) | `TestLoadAlgorithmReturnsEvolved` — KV penalty fires at KVUtil=0.90 |
| BC-4 | Task 3 | Unit (Go) | `TestEvolvedScorerScoresCorrectly` — heavy endpoint scores lower |
| BC-5 | Task 3 | Unit (Go) | `TestEvolvedScorerScoresCorrectly` — session header code path executes without panic, returns correct score count. SessionID value not directly asserted (not observable without test spy; CacheHitRate=0 makes SessionID effect-neutral). Code path verified by inspection (K.3). |
| BC-6 | Task 4 | Integration (Go) | `TestSuiteA_KendallTau` — mean tau > 0.8 across 200 tuples |
| BC-7 | Task 4 | Integration (Go) | `TestSuiteA_KendallTau` — max_abs_error logged |
| BC-8 | Task 5 | Unit (Go) | `TestSuiteB_StatenessStability` — informational_only=true, tau≈1.0 |
| BC-9 | Task 6 | Unit (Go) + race detector | `TestSuiteC_ConcurrentDeterminism` with `-race` |
| BC-10 | Task 6 | Unit (Go) | `TestSuiteC_PileOn` — max count ≤ 40 for 5 endpoints |
| BC-11 | Task 7 | Unit (Python) | `test_noise_characterize_halts_on_high_cv` — exit 1, halt=true |
| BC-12 | Task 7 | Unit (Python) | `test_noise_characterize_t_eff_computation` — T_eff ≈ 0.056 (Bessel's correction) |
| BC-13 | Task 8 | Unit (Python) | `test_benchmark_mechanism_check_pass` — verdict=PASS |
| — | Task 8 | Unit (Python) | `test_benchmark_mechanism_check_inconclusive` — verdict=INCONCLUSIVE, exit 0 |
| — | Task 8 | Unit (Python) | `test_benchmark_mechanism_check_fail` — verdict=FAIL, exit 1 |
| BC-14 | Task 9 | Structural | prompt has 4 required sections |
| BC-15 | Task 3 | Compilation | `go build ./tools/harness/...` — no non-existent field references |
| BC-16 | Task 7 | Unit (Python) | `test_noise_characterize_malformed_input` — exit 2; `test_noise_characterize_empty_runs` — exit 2 |
| BC-17 | Task 8 | Unit (Python) | `test_benchmark_requires_t_eff` — exit 1 |
| BC-18 | Task 8 | Unit (Python) | `test_benchmark_malformed_input` — exit 2 (invalid JSON); `test_benchmark_missing_workloads_key` — exit 2 |

**Cross-system invariants:**
- Schema chain: `validation_results.schema.json` required fields exactly match the `validate.md` prompt's output instructions
- Signal name consistency: `WaitingQueueSize`, `RunningRequestsSize`, `KVCacheUsagePercent` appear consistently in `evolved_scorer.go`, mapping artifact annotation, and `blis_weighted_scoring.go` comments
- Go harness compilation: `go build ./tools/harness/...` must succeed (scoped to harness only; `./...` would build all submodules and may fail for unrelated reasons)

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| `evolvedAlgorithm` penalty coefficients drift from EVOLVE-BLOCK | Low | High | Copy coefficients verbatim from routing/best_program.py:171-241; test catches divergence if coefficients are wrong | Task 2 |
| `WeightedScoring.Route()` API change in inference-sim | Low | High | Pinned to `aa4bbb7`; `go.mod replace` points to local submodule | Task 2 |
| `BLISWeightedScorer` import from llm-d fails (workspace resolution) | Medium | High | go.work includes both modules; go.mod replace directive added; `go build` test validates | Task 4 |
| Kendall-tau < 0.8 due to prefix-affinity gap | Medium | Medium | Test tuples use nil InputTokens (zero value from `generateCanonicalTuples`), causing prefix-affinity scorer to return equal scores for all endpoints (`ComputeBlockHashes(nil)` → empty hashes → `totalBlocks==0` → score 0.0). CacheHitRate=0 separately neutralizes the EVOLVE-BLOCK cache-affinity bonus. Together these eliminate the prefix-affinity gap from relative rankings. | Task 4 |
| Suite A skips when workspace/algorithm_summary.json absent (CI) | Medium | **High** | `validate.md` prerequisites now explicitly check `algorithm_summary.json` existence before running Suite A. Without this check, `t.Skip` causes exit 0 → false PASS → Stage 6 proceeds without equivalence validation. CI `go test` still skips gracefully (acceptable for CI — CI is not the go/no-go gate). | Task 4, Task 9 |
| noise-characterize CV > 0.15 on valid low-variance test input | Low | Low | Test uses controlled inputs with known CV | Task 7 |

---

## Part 3: Quality Assurance

### J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [x] inference-sim API refs: `sim.NewRoutingPolicy`, `sim.DefaultScorerConfigs`, `sim.RouterState`, `sim.RoutingSnapshot` — all confirmed from `inference-sim/sim/routing.go` at `aa4bbb7`
- [x] `fwkdl.Metrics` fields: `WaitingQueueSize`, `RunningRequestsSize`, `KVCacheUsagePercent` — confirmed from `metrics.go` at `gateway-api-inference-extension@v0.0.0-20260128235548-fd30cb97714a`
- [x] `BLISWeightedScorer` import path: `github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer` — confirmed from `blis_weighted_scoring.go`
- [x] `ScoreEndpoints` test helper signature confirmed
- [x] No stale references to `RunningQueueSize`, `RunningRequestCount`, `CacheHitRate` in new code

**Dimension 2: Schema chain integrity**
- [x] `validation_results.schema.json` required fields: `suite_a`, `suite_b`, `suite_c`, `benchmark`, `overall_verdict`, `noise_cv` — match macro plan workspace artifact table
- [x] `suite_a` required: `passed`, `kendall_tau`, `max_abs_error`, `tuple_count`
- [x] `suite_b` required: `passed`, `rank_stability_tau`, `threshold_crossing_pct`, `informational_only`
- [x] `suite_c` required: `passed`, `deterministic`, `max_pile_on_ratio`
- [x] `benchmark` required: `passed`, `mechanism_check_verdict`, `t_eff`

**Dimension 3: Prompt completeness**
- [x] `validate.md` has: prerequisites (signal_coverage.json, stage3_output.json), validation steps (go test suites, CLI commands, validate-schema), halt conditions (Suite A/C fail, CV > 15%), expected outputs (validation_results.json)
- [x] Predecessor artifact checks included

**Dimension 4: CLI contract**
- [x] `noise-characterize`: output JSON, exit 0 (success), 1 (validation failure/halt), 2 (infrastructure)
- [x] `benchmark`: output JSON, exit 0 (PASS with `status: "ok"` **or** INCONCLUSIVE with `status: "inconclusive"` — caller must check `mechanism_check_verdict` and HALT on INCONCLUSIVE), 1 (validation failure), 2 (infrastructure)
- [x] Error messages are actionable (reference specific missing arguments or failed checks)

**Dimension 5: Artifact consistency**
- [x] Signal names: `WaitingQueueSize`, `RunningRequestsSize`, `KVCacheUsagePercent` consistent across `evolved_scorer.go`, `suite_a_test.go`, mapping artifact (`blis_to_llmd_mapping.md` — corrected in this PR from `RunningQueueSize`/`RunningRequestCount`), and `blis_weighted_scoring.go`
- [x] File paths: all referenced files created in this PR or confirmed present from prior PRs

**Dimension 6: Dead artifact prevention**
- [x] Every file created has an identified consumer (verified in Task descriptions above)
- [x] No orphan schemas, no unreferenced prompts

**Additional checks:**
- [x] PR category: Validation — correct
- [x] Verification gate: `go test ./tools/harness/...` + `python -m pytest tools/` + `go build ./tools/harness/...` (scoped to harness, not `./...` which builds all submodules and may fail for unrelated reasons)
- [x] No feature creep: `evolved_scorer.go` wiring, Suite A/B/C, noise-characterize, benchmark, validate.md, calibration_log.md, noise_characterization.md all in macro plan PR5 scope
- [x] Deviation log reviewed — D-1 through D-5 all justified (CORRECTION or SIMPLIFICATION)
- [x] Each task produces working, verifiable output
- [x] Task dependency order: Task 1 (schema) → Task 2 (algorithm) → Task 3 (scorer) → Task 4 (Suite A needs both) → Tasks 5/6 (suites, parallel) → Task 7 (CLI) → Task 8 (CLI, after Task 7) → Task 9 (prompt) → Task 10 (docs)
- [x] All contracts mapped to tasks: BC-1→T1, BC-2/3→T2, BC-4/5/15→T3, BC-6/7→T4, BC-8→T5, BC-9/10→T6, BC-11/12/16→T7, BC-13/17/18→T8, BC-14→T9

---

## Appendix K: File-Level Implementation Details

> See `docs/plans/2026-03-16-pr5-validation-pipeline-appendix.md` for complete file contents:
> - K.1: `tools/schemas/validation_results.schema.json`
> - K.2: `tools/harness/evolved_algorithm.go` + `harness.go` diff
> - ~~K.1b: `tools/schemas/stage4_output.schema.json`~~ — REMOVED (Stage 5 prerequisites use direct `go build`/`go vet` instead)
> - K.3: `tools/harness/evolved_scorer.go` (wired Score method)
> - K.4: `tools/harness/stats.go` (KendallTau)
> - K.5: `tools/harness/suite_a_test.go`
> - K.6: `tools/harness/suite_b_test.go`
> - K.7: `tools/harness/suite_c_test.go`
> - K.8: `transfer_cli.py` noise-characterize implementation
> - K.9: `transfer_cli.py` benchmark implementation
> - K.10: `prompts/validate.md`
> - K.11: `docs/transfer/noise_characterization.md`
> - K.12: `docs/transfer/calibration_log.md`
> - K.13: `go.mod` changes for harness
