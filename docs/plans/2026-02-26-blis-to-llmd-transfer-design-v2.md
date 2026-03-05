# BLIS-to-llm-d Algorithm Transfer Pipeline Design

**Date:** 2026-02-26 (updated 2026-03-02)
**Status:** Draft
**Author:** Claude Code + toslali

## Analysis Questions

This pipeline exists to answer:

1. **Does the BLIS-discovered algorithm's benefit survive the abstraction gap?** — Do simulation-predicted improvements appear in production benchmarks?
2. **Which BLIS signals have production equivalents that preserve the algorithm's mechanism?** — Can we faithfully map the signals the algorithm depends on?
3. **What is the minimum fidelity needed for the benefit to transfer?** — When a signal maps imperfectly, does the algorithm degrade gracefully or break?

Every validation stage (Translate, Integration + Equivalence Test, Cluster Benchmark) traces back to at least one of these questions. Scaffolding stages (Extract, Generate, Unit Test, Draft PR, Promote) support the pipeline infrastructure.

**How each question is answered:** Q1 is answered quantitatively by Stage 6 (equivalence tests) and Stage 7 (cluster benchmarks). Q2 is answered by Stage 2 (signal coverage report with fidelity ratings) and Stage 6 Suite A (per-signal divergence analysis). Q3 is answered **empirically, not computationally** — the pipeline tests each algorithm at its mapped fidelity level and records pass/fail. Over multiple transfers, the falsification protocol (see below) builds evidence for which fidelity levels are sufficient. The pipeline does not compute a minimum fidelity threshold in a single run; it accumulates empirical evidence across transfers via the calibration log.

## Problem Statement

We have a working discovery loop (OpenEvolve + BLIS) that evolves adaptive routing algorithms — conditional logic that adjusts how existing scorer outputs are combined based on request characteristics. These algorithms are validated in simulation but exist only as Go code within BLIS's simplified abstractions.

We need a **transfer pipeline** that takes a discovered algorithm and:

1. Translates it into a production-quality scorer plugin for `llm-d-inference-scheduler` (following the target system's scoring pipeline conventions)
2. Generates corresponding benchmark configs for `llm-d-benchmark` to validate the new scorer
3. Validates correctness through unit tests, integration tests, semantic equivalence checks, and cluster benchmarks
4. Produces tested, ready-to-review PRs against both repos — **no PR is acceptable unless it has been tested**

### Scope

**In scope (v1):**
- Single algorithm transfer (one evolved EVOLVE-BLOCK at a time)
- Algorithms expressible as conditional linear combinations of endpoint signals (e.g., "if input is long, weight prefix higher"). The generated scorer reads signals directly from the target system's metric interface — it does not depend on existing scorer outputs. This covers the class of algorithms the EVOLVE-BLOCK currently produces.
- Algorithms that use only High or Medium fidelity signals (see Signal Mapping)
- Primarily non-disaggregated clusters. If the target cluster uses P/D disaggregation, Stage 1 warns and the user may proceed — but the transferred scorer applies only to the non-disaggregated scheduling path (see P/D disaggregation detection).
- Manual trigger, interactive Claude Code session

**Out of scope (v1):**
- **P/D disaggregation**: BLIS does not model prefill/decode disaggregation. If the target cluster uses P/D, the transferred scorer applies only to the non-disaggregated scheduling path. P/D-aware transfer requires extending BLIS first.
- Algorithms requiring non-linear transformations or new signal computation beyond what existing llm-d scorers provide
- Algorithms that depend on Low-fidelity signals (signals rated Low in the mapping artifact) — the pipeline emits a signal coverage report and flags these for human review rather than proceeding automatically
- Bidirectional transfer (production insights back to BLIS)
- Multi-algorithm transfer or algorithm composition (v1 assumes a single transfer at a time — parallel transfers to the same repo may cause config merge conflicts on the scoring config file)
- Continuous integration trigger

**When the algorithm uses a Low-fidelity signal:** Stage 2 (Translate) emits a structured signal coverage report (JSON format) listing each BLIS signal used, its fidelity rating, which branches reference it, and the translation strategy (direct map, proxy, stub, or drop). If any signal in a critical branch is Low-fidelity, Stage 2 emits the signal coverage report and halts. The Claude Code session displays the flagged signal, branch classification, and fidelity rating interactively, and the user decides whether to proceed with a degraded mapping (accepting lower fidelity) or abort the transfer.

**Definition of "critical branch":** A branch in the EVOLVE-BLOCK is critical if it meets *any* of these criteria (evaluated statically from the code structure using only literal constants — computed values and symbolic constants are conservatively treated as critical):

1. **Default path**: The branch executes when no other condition matches (else/default clause), OR it is the only path (no condition). These affect the majority of requests.
2. **Score-dominant**: The branch directly sets or multiplies a weight by a literal constant ≥0.5 on a [0, 1] normalized scale. Only literal numeric constants are evaluated — if the weight is computed (e.g., `weight = max_val * factor`) or uses a variable, conservatively classify as critical. When multiple signals contribute to a single endpoint score, evaluate each signal's weight independently (not relative to the sum of all weights).
3. **All-request path**: The signal is read unconditionally (outside any if/else), meaning every routing decision depends on it.
4. **Nested conditions**: If a signal read is guarded by nested conditions (`if A: if B: read signal`), classify as critical if the outermost condition is a default path or all-request path. If both conditions are narrow, classify as non-critical only if both have documented fallback values.

A branch is **non-critical** if it is guarded by a narrow condition (e.g., a specific enum match) AND the signal it uses has a fallback value specified in the mapped algorithm spec AND the weight is a literal constant < 0.5.

**Tie-breaking rule:** When classification is ambiguous (e.g., weight is exactly 0.5, or condition narrowness is debatable), classify as critical. False criticals cause extra human review; false non-criticals risk silent fidelity loss.

Stage 2 must classify every branch as critical or non-critical in the signal coverage report. The classification is deterministic — given the same EVOLVE-BLOCK code, the same classification must result. The classification algorithm is documented in the transfer prompt template with worked examples.

### BLIS Experiment Artifacts (Prerequisites)

The transfer pipeline consumes artifacts produced by a normal BLIS experiment run. **No changes to the BLIS evaluator are required.**

**Expected artifacts from a BLIS experiment:**

1. **`best_program_info.json`**: Contains the best evolved algorithm — the EVOLVE-BLOCK code, hypothesis results, overall experiment metadata, and per-workload results under the `metrics.workload` entry. This is produced by OpenEvolve's standard best-program tracking. The `metrics.workload` entry contains the experiment results for each workload (metrics under the evolved algorithm, improvement data, etc.). TODO: document the concrete `metrics.workload` schema once an example is available.

2. **Per-workload configuration files (`<workload_name>.yaml`)**: Each workload used in the BLIS experiment has a YAML configuration file containing the BLIS workload generator parameters (traffic pattern, request size distribution, concurrency level, etc.) — sufficient to reproduce the workload via the BLIS workload generator in llm-d-benchmark. These files contain **configuration only**, not results. They are located in the experiment output directory alongside `best_program_info.json`. TODO: document concrete schema once an example is available.

**How the pipeline uses these artifacts:**
- Stage 1 reads `best_program_info.json` for the algorithm code, experiment metadata, and per-workload results (from `metrics.workload`). It reads the `<workload_name>.yaml` files for workload generator configuration.
- Stage 7 runs cluster benchmarks against the **same workloads** that BLIS used, reproduced via the BLIS workload generator in llm-d-benchmark (see Supporting Artifacts). The workload generator parameters come from the YAML config files; the predicted results for comparison come from `best_program_info.json`'s `metrics.workload`.
- The mechanism check (Stage 7) can compare **predicted vs. observed improvement magnitudes**: if BLIS predicted 15% improvement on the prefix-heavy workload, but the cluster shows only 2%, the algorithm technically passes the threshold but the abstraction gap is clearly degrading the benefit.
- The falsification protocol benefits from tracking prediction accuracy across transfers — systematic overestimation is an early signal that BLIS simulation fidelity needs improvement.

### Constraints

**Operational:**
- **Trigger:** Manual, post-experiment (user decides which algorithm to transfer)
- **Automation:** Fully automated from trigger to PR, with multi-tier validation as quality gate
- **Human involvement:** Interactive Claude Code session — user sees each step and can intervene. Failures at integration/cluster stages leave a draft PR for manual debugging.
- **Translation engine:** LLM-powered (Claude reads both codebases and bridges the abstraction gap)

**Quality:**
- Generated code must pass existing CI of `llm-d-inference-scheduler`
- Generated code must follow llm-d's plugin conventions (as documented in the scorer template artifact)

**Dependencies:**
- Pipeline depends on `llm-d-inference-scheduler` and `llm-d-benchmark` repos. The mapping artifact (`blis_to_llmd_mapping.md`) pins a "last verified against" commit hash for each.
- `llm-d-benchmark` must include the **BLIS workload generator** — a workload generator that produces synthetic traffic matching BLIS workload patterns (one-time prerequisite, see Supporting Artifacts).

---

## Architecture Overview

### Pipeline Stages

```
[1. Extract] → [2. Translate] → [3. Generate] → [4. Unit Test] → [5. Draft PR] → [6. Integration + Equivalence Test] → [7. Cluster Benchmark] → [8. Promote/Flag]
                                                        ↑
                                                 (retry on failure, up to 3x with error feedback)
```

**Why 8 stages (minimum viable pipeline justification):** A simpler pipeline (Extract → Generate → Test → Deploy) could answer Q1 via cluster benchmarks alone, but would be far more expensive to debug. Stages 2-3 (Translate + Generate) are separated so translation errors are caught before code generation compounds them. Stage 6 (equivalence tests) exists because it catches translation bugs in ~5 minutes vs. ~60 minutes for Stage 7 cluster benchmarks — running Stage 6 first saves cluster resources when the translation is wrong. Stage 7 cannot be skipped because equivalence tests use synthetic conditions (no real staleness, no real concurrency, no real request parsing), so Stage 7 is the only ground-truth validation. Stage 8 (Promote) is separated from Stage 7 because promotion criteria combine results from multiple prior stages.

**Stage ordering:** All stages are strictly sequential. Stage 6 must complete before Stage 7 begins — Stage 6 equivalence failures make cluster benchmarking pointless, so running them in parallel wastes cluster resources. Within Stage 6, the three suites (A, B, C) also run sequentially: Suite A must pass before Suite B runs (staleness testing assumes baseline equivalence), and Suite B must pass before Suite C (concurrency testing assumes staleness-robust behavior).

| Stage | Input | Output |
|---|---|---|
| **Extract** | Best program artifact (`best_program_info.json`) + per-workload YAML files (`<workload_name>.yaml`) | Algorithm summary: EVOLVE-BLOCK code, hypothesis results, per-workload results, signals used, **scope verdict** (pass/marginal/reject) |
| **Translate** | Algorithm summary + mapping artifact | Signal coverage report (JSON) + mapped algorithm spec |
| **Generate** | Mapped spec + scorer template + llm-d repo + workload parameters | New scorer plugin files on a new git branch: scorer source file, registration in plugin init, unit test file, scoring config update, **baseline and treatment deployment configs** (`transfer_workspace/baseline_deployment_config.yaml`, `transfer_workspace/treatment_deployment_config.yaml`), **benchmark workload configs** (one per BLIS workload, using the BLIS workload generator), **generator version pin** (`transfer_workspace/generator_version.txt` — exact version identifier of the BLIS workload generator used). All files are written to the llm-d repo working copy on a branch named `blis-transfer/<algorithm-name>`. No commit is made — files are staged for Stage 4 validation first. |
| **Unit Test** | Generated files in llm-d repo | Build + test results (pass/fail) |
| **Draft PR** | Passing unit tests + generated files | Draft PRs: scheduler + benchmark |
| **Integration + Equivalence Test** | Draft PR branch | Integration results + equivalence report (posted as PR comment) |
| **Cluster Benchmark** | Draft PR branch + K8s cluster | Benchmark metrics vs baseline (posted as PR comment) |
| **Promote/Flag** | Benchmark results + go/no-go criteria | PR promoted to ready-for-review, or flagged for debugging |

**Stage 1 scope validation:** Before proceeding to Stage 2, Stage 1 checks whether the discovered algorithm falls within the "conditional linear combinations" scope (line 32). The check verifies the EVOLVE-BLOCK uses only: arithmetic operators (`+`, `−`, `*`), scalar comparisons (`>`, `<`, `>=`, `<=`, `==`), and conditional branching (`if`/`else`). Operator classification:
- **In scope (pass):** `+`, `−`, `*`, `/` (scalar arithmetic), comparisons, `if`/`else`, ternary operators. These are linear or piecewise-linear.
- **Marginal:** `min`, `max`, `abs`, `clamp` — piecewise-linear, often translatable but require review. Emit **marginal** verdict; user confirms to proceed.
- **Reject:** `exp`, `log`, `pow`, `sqrt`, trigonometric functions, lookup tables, neural network layers — non-linear and outside the translation pattern's scope. Emit **reject** verdict; pipeline halts.
- **Helper functions:** If the EVOLVE-BLOCK calls a helper function, the helper's body is included in the scope check. Standard library math functions are classified per the categories above.
A **reject** halts the pipeline. A **marginal** requires user confirmation to proceed. When deciding on a marginal verdict, the user should consider: (a) whether the marginal operator appears in a critical branch (if only in non-critical branches with fallback values, proceed is usually safe), (b) whether the operator's piecewise-linear behavior is preserved by the translation pattern (e.g., `min(a,b)` translates directly, but `clamp(x, lo, hi)` requires both bounds to map correctly), and (c) whether the BLIS experiment showed sensitivity to the marginal operator's behavior (check hypothesis results for threshold effects near the operator). The transfer prompt template contains worked examples for common marginal operators.

**Stage 1 prerequisite checks:** Before extraction, Stage 1 also verifies: (a) all four supporting artifacts exist and are readable (including verifying the BLIS workload generator is present in llm-d-benchmark and its version is compatible — see below), (b) noise characterization data exists and is current (see Noise Characterization section), (c) the scorer template compiles against llm-d HEAD (see Staleness Prevention). If any prerequisite fails, Stage 1 halts with a diagnostic listing what's missing.

**Inter-stage artifact passing:** All stage artifacts (algorithm summary, signal coverage report, mapped spec, generated files) are written as files to a `transfer_workspace/` directory within the llm-d repo working copy. Each artifact is a JSON or text file named by convention (e.g., `transfer_workspace/algorithm_summary.json`). Stages read their inputs from this directory. If a required input file is missing or malformed, the stage halts with a diagnostic naming the missing file and the validation failure.

*Artifact validation rules:* Each consuming stage validates its input artifact before processing:
- **JSON artifacts** (algorithm summary, signal coverage report): Must parse as valid JSON. All required fields listed in the Stage Artifact Schemas section must be present and non-null (where "non-null" means the JSON value is not `null` — empty strings `""` are considered non-null but are invalid for enum fields and fields marked as requiring content). Enum fields must contain one of the allowed values. List fields must be non-empty (an algorithm with zero signals is invalid). String fields marked "required" must be non-empty (not `""`).
- **Structured text artifacts** (mapped algorithm spec): Must contain all required sections (`conditional_logic`, `weight_defaults`, `signal_requirements`).
- **Generated files** (Stage 3 output): All files listed in the Stage 3 output contract must exist on disk.
- On validation failure: halt with a diagnostic that names the artifact file, the specific field or section that failed validation, and the expected vs. actual value. Do not attempt to repair malformed artifacts — return to the producing stage.

**Retry behavior (Stage 4):** On unit test failure, the pipeline returns to Stage 3 with error context appended to the LLM prompt, so the LLM can diagnose and fix the specific failure. Up to 3 retries. On 3x failure: stop pipeline, report all accumulated errors — no PR created.

*Retry state ownership:*
- **Retry counter**: Owned by the orchestrator (Claude Code session state), not persisted to disk.
- **Error context per retry**: Include the first 500 and last 500 characters of build/test output. If output exceeds 1000 characters, the middle is omitted with a marker: `[... N characters omitted ...]` (deterministic truncation, not LLM-based summarization). Each retry's error context is appended (not replaced), so the LLM sees the progression of failures.
- **Identical error detection**: If two consecutive retries produce errors whose first 200 characters of output are identical (byte-for-byte comparison after whitespace normalization), stop retrying — the LLM cannot fix this error. If the error text changes between retries (even partially), continue retrying. The orchestrator records each retry's prompt and response as entries in `transfer_workspace/retry_log.json` (appended per retry, fields: `retry_number`, `prompt_hash`, `error_first_200_chars`, `timestamp`). This log serves as the audit trail for identical error detection and is also used for the error context inclusion check: the orchestrator verifies its own log shows the prior error context was included in the prompt before declaring the error identical.
- **Failure classification**: Three categories, checked in order:
  1. **Non-retryable (stale mapping):** The error references a type, interface, or method name that does not appear in the mapping artifact's interface list. The interface list is a section in the mapping artifact that enumerates the public types, interfaces, and method signatures the generated scorer may use — it is hand-curated when the mapping artifact is created or updated, and must be kept in sync with the scorer template. Action: stop immediately, emit diagnostic naming the unrecognized symbol and suggesting mapping artifact update.
  2. **Non-retryable (environment):** Missing system dependencies, authentication failures, network errors. Action: stop immediately, emit diagnostic.
  3. **Retryable (code generation):** Compilation errors, missing imports, test failures where the error references symbols that ARE in the mapping artifact. Action: retry with error context.
- **Error context storage**: Error context is held in the orchestrator's session memory (not persisted to disk). If the session crashes, retry state is lost and the pipeline must restart from Stage 1.
- **Idempotent re-entry (crash recovery):** If the pipeline restarts after a crash and the `blis-transfer/<algorithm-name>` branch already exists with generated files, Stage 3 must detect the existing branch and offer the user a choice: (a) delete the branch and regenerate from scratch, or (b) skip to Stage 4 and attempt to validate the existing files. This prevents duplicate file creation and lets users recover partial progress. The `transfer_workspace/` directory persists on the branch and serves as the indicator of prior execution.

**Stage failure modes:**
- Stages 1-3: Failure stops the pipeline with a diagnostic report. No PR created.
- Stage 4: Retry loop (up to 3x) back to Stage 3 with error context. On exhaustion, stop.
- Stage 5: Retry `gh pr create` once on transient failure. On persistent failure (auth, branch conflict), stop and report. **PR creation is atomic per repo**: the scheduler PR is created first; if the benchmark PR then fails, close the scheduler PR (delete the draft) and fail Stage 5 with diagnostic. The user can re-trigger Stage 5 alone (without re-running Stages 1-4) since generated files are already on the branch. **Idempotency**: if Stage 5 is re-triggered and a draft PR already exists on the branch, reuse it: update the PR description with the latest validation checklist and signal coverage data, but preserve existing PR comments and labels from prior runs.
- Stage 6: Post failure details as PR comment. Leave draft PR for manual debugging.
- Stage 7: Post failure details as PR comment. Leave draft PR for manual debugging.
- Stage 8: Automatic based on go/no-go criteria.

### Invocation

Interactive Claude Code session. The user triggers the transfer conversationally:

```
> Transfer the best discovered algorithm from examples/blis_router/openevolve_output/
  to llm-d-inference-scheduler at ~/repos/llm-d-inference-scheduler
  and llm-d-benchmark at ~/repos/llm-d-benchmark
```

---

## The Abstraction Gap

### Why Direct Copy Doesn't Work

BLIS and llm-d share the same *conceptual* architecture (weighted scoring across instances), but differ across several dimensions. The key categories of difference are:

- **Data model**: BLIS uses simple structs with numeric fields; llm-d uses production endpoints with asynchronously collected metrics and abstracted access patterns.
- **Request representation**: BLIS has typed fields for input tokens and request metadata; llm-d carries serialized request payloads requiring parsing and estimation (the concrete transport format — currently HTTP, but may change — is documented in the mapping artifact). This gap is bridged at code generation time (the scorer template shows how to parse request metadata) but is not explicitly validated in the equivalence tests — Suite A uses pre-parsed test tuples, so parsing fidelity is only tested in Stage 7 cluster benchmarks with real traffic. **Accepted risk with mandatory mitigation:** Stage 4 unit tests **must** include at least one test case that exercises request parsing (deserialize a sample request payload from the mapping artifact's documented format, verify extracted fields match expected values). This is mandatory because request representation is the highest-risk abstraction gap and is not covered by Stage 6 equivalence tests (which use pre-parsed tuples). If the request parsing test is missing from Stage 4 output, the pipeline halts with diagnostic: "Missing mandatory request parsing test — Stage 4 requires at least one test exercising request deserialization." If parsing fails in Stage 7 despite passing Stage 4, the diagnostic guide (Stage 7 failure, bullet 2) should check request parsing as a root cause.
- **Signal richness**: Some llm-d signals are strictly richer than BLIS equivalents (e.g., per-request prefix match vs. aggregate cache affinity). The translation must decide whether to use the richer signal or degrade to match BLIS semantics.
- **Metric staleness**: BLIS uses synchronous snapshots; llm-d metrics are collected asynchronously with variable staleness depending on collection interval. An algorithm conditioning on threshold values may behave differently with stale signals.
- **Concurrency**: BLIS evaluates routing decisions sequentially; llm-d processes requests concurrently. Pile-on effects tracked by pending request counts may differ.
- **Weight mechanism**: BLIS uses normalized weights summing to 1.0; llm-d uses per-scorer weights in its scheduling config. The concrete weight representation is documented in the mapping artifact.

**Gap handling strategy summary:**

| Gap | Strategy | Validated By | Risk If Wrong |
|---|---|---|---|
| Data model | Bridge faithfully (scorer template type conversions) | Stage 4 unit tests (compilation + type checks) | Build failure — caught early |
| Request representation | Bridge at code generation time (scorer template shows parsing) | Stage 7 cluster benchmarks only (not Stage 6) | Silent degradation with real traffic — highest risk gap |
| Signal richness | Per-signal decision: use richer signal (Upgrade) or degrade to match (direct map) | Stage 6 Suite A (upgrade signal validation) | Over/under-utilization of target signal — caught by equivalence tests |
| Metric staleness | Approximate (inject synthetic staleness in tests) | Stage 6 Suite B | Algorithm may be staleness-sensitive — caught by Suite B |
| Concurrency | Approximate (simulate sequential-but-rapid arrivals) | Stage 6 Suite C | Pile-on effects differ — partially caught; combined staleness+concurrency deferred to Future Extension #7 |
| Weight mechanism | Bridge faithfully (weight_convention field in mapped spec) | Stage 4 unit tests + Stage 6 Suite A numeric fidelity | Magnitude distortion — caught by numeric fidelity check |

The full concrete mapping (specific types, method names, metric paths) is maintained in `docs/transfer/blis_to_llmd_mapping.md` — the single source of truth for implementation-level details. That artifact is pinned to a specific llm-d commit and updated when llm-d APIs change.

### Translation Pattern: Composite Scorer

The discovered adaptive logic maps to a **new scorer plugin** that reads endpoint metrics directly and applies the BLIS-discovered conditional weighting logic. The scorer:

- Reads per-endpoint signals directly through the target system's standard metric access interface — it does not wrap or delegate to other scorer instances
- Applies conditional weight adjustments based on request characteristics — these conditions and weights are the core translation target from the BLIS EVOLVE-BLOCK
- Registers as the sole scorer (or replaces the scorers whose logic it internalizes) to avoid double-counting signals. The mechanism for disabling overlapping scorers is documented in the mapping artifact.
- Implements the full plugin interface following the target system's conventions (as documented in the scorer template artifact)

**Why direct metric reads instead of wrapping scorers:** Wrapping existing scorers creates coupling to their constructors and internal state, makes the composite invisible to per-plugin metrics/logging, and risks double-counting if the same scorer is also registered standalone. Reading metrics directly is simpler and consistent with how existing llm-d scorers work.

**Double-counting prevention:** The signal coverage report's `scorer_overlap` field lists which existing scorers share signals with the new scorer and recommends `disable` or `keep` for each. Stage 4 unit tests must include an **overlap assertion**: verify that the generated scoring config has all `disable`-recommended scorers set to weight zero (or equivalent disabled state). If a scorer marked `disable` is still active in the config, the test fails. This prevents accidental double-counting from config errors.

**Rollback/disable mechanism:** The generated configuration must include a toggle to disable the new scorer without removing code (using whatever disable convention the target system provides — documented in the mapping artifact). The PR description must document the minimal config change to disable the scorer.

**No-op default:** When the new scorer is disabled (or the pipeline has not been run), the target system must behave identically to its pre-transfer state. The scorer template artifact must include a test verifying that disabling the scorer produces identical routing behavior to the stock configuration. Stage 4 unit tests must include a "disabled scorer" test case.

**Scorer lifecycle requirements:** The generated scorer must satisfy these lifecycle properties (enforced via the scorer template and Stage 4 tests):
- **Idempotent registration**: Registering the scorer twice must not create duplicate entries or side-effects (e.g., duplicate metric collectors, background goroutines).
- **Clean disable/enable cycle**: Disabling and then re-enabling the scorer must return it to the same state as a fresh registration. No accumulated state across cycles.
- **No initialization side-effects beyond registration**: The scorer must not start background processes, open network connections, or modify global state during registration. All runtime behavior occurs during score evaluation calls.
The scorer template artifact documents the target system's plugin lifecycle contract, and the generated scorer must conform to it.

**Alternatives considered:**
- **Config-only transfer** (adjust scorer weight ratios without new code): Sufficient when the discovered algorithm is simply "use weights A:B instead of C:D", but cannot express conditional logic (if input > threshold, use different weights).
- **Wrapping existing scorers**: Architecturally problematic — see above. Rejected.
- **Filter-based approach** (route conditional logic to the eligibility/filter stage): The target system's filter stage is for endpoint eligibility, not score weighting. Wrong abstraction level.

This pattern is:
- **Additive at the code level** — introduces a new plugin file without modifying existing scorer source
- **Requires configuration changes** — the target system's scoring config must be updated to register the new scorer and disable overlapping scorers (using the target system's disable convention, preserving config entries for easy rollback)
- **Reviewable** — the adaptive logic is isolated and clearly attributable to BLIS discovery
- **Testable** — can unit test independently and verify semantic equivalence against BLIS

---

## Validation Pipeline

### Stage 2→3 Translation Validation

Stage 2 (Translate) and Stage 3 (Generate) are both LLM-powered. To catch translation errors before they compound:

1. **Mapped spec review (end of Stage 2):** After generating the mapped algorithm spec, the orchestrator asks the LLM to verify the spec against the original EVOLVE-BLOCK: "Does this pseudocode preserve all conditions and weight assignments from the original code?" If the LLM identifies discrepancies, it revises the spec before proceeding to Stage 3. This is a self-check, not a guarantee — it catches obvious omissions but not subtle semantic shifts.
2. **Branch-count consistency check:** The mapped algorithm spec must reference the same number of conditional branches as the EVOLVE-BLOCK. If they differ, halt with a diagnostic (a branch was dropped or invented during translation).
3. **Signal-set consistency check (bidirectional):** The signals referenced in the mapped spec must exactly match `signals_used` from Stage 1. Two checks: (a) If the spec references a signal not in the original algorithm, halt — the LLM hallucinated a signal mapping. (b) If a signal from Stage 1's `signals_used` is absent from the mapped spec, halt with diagnostic: "Signal X was identified as used in BLIS algorithm but does not appear in mapped spec — re-examine translation." A silently dropped signal means the translation ignored a dependency the algorithm relies on.
4. **Workload generator validation:** Stage 2 must verify that the BLIS workload generator is available in the llm-d-benchmark repo (see Supporting Artifacts). If the generator is not found, halt with diagnostic: "BLIS workload generator not found in llm-d-benchmark — this prerequisite must be installed before running the transfer pipeline." Stage 2 also validates that each BLIS workload's parameters are compatible with the generator's supported configuration schema.

**Limitation:** These checks reduce but do not eliminate LLM translation errors. The primary safety net remains Stage 6 equivalence testing, which validates actual behavioral output.

### Stage 4: Unit Test Gate

The minimum bar for creating a draft PR. Run the new scorer's tests in isolation first, then the full repo suite:

1. Run unit tests for the new scorer package only — isolate new code failures
2. Run the full repo build, test, and lint suite — catch integration breakage
3. If only the full suite fails (pre-existing issue), flag for human review instead of retrying code generation. **Pre-existing detection:** A failure is classified as pre-existing if (a) the new scorer's package-level tests pass (step 1), AND (b) the failing test is in a package that the generated code does not import or modify. The orchestrator compares the list of failing test packages against the list of files modified by Stage 3. If no overlap, the failure is pre-existing.
4. On new-code failure: regenerate code (back to Stage 3 with error context), up to 3 retries

### Stage 6: Integration + Equivalence Test

Runs after the draft PR exists. Two parts:

**Integration tests** — verify the scorer works in the llm-d pipeline:
- Scorer registers and loads correctly through the target system's scorer composition mechanism
- Produces valid score maps through the scoring pipeline with mocked endpoints
- No panics, no NaN, scores in expected range
- Edge cases: empty endpoints, very large/small requests

**Semantic equivalence tests** — verify the translation preserves the BLIS algorithm's behavior (answers Analysis Question 1). Three test suites, run in order:

**Suite A — Baseline equivalence (controlled):**
- Generate test tuples systematically: for each input dimension the algorithm conditions on (e.g., input size, queue depth, cache state), sample at min, median, max, and each threshold value ±1. Use Cartesian product across dimensions, capped at 200 tuples (provisional cap balancing ~5 min test time and coverage). When the cap is reached, prioritize: (1) all threshold boundary values first (these catch off-by-one translation errors), (2) then fill remaining slots with intermediate values sampled evenly across dimensions. Report the tuple count, dimension coverage, and which threshold values were sampled.
- Run each tuple through both the BLIS algorithm (via the evaluator) and the translated llm-d scorer with identical, synchronous signal values. The test harness serializes test tuples as JSON. The BLIS evaluator reads the JSON and produces a score vector; the llm-d scorer is tested via unit test reading the same JSON. The harness design is specified in the transfer prompt template.
- **Two independent pass criteria** (both must pass):
  1. **Numeric fidelity**: For each tuple, the score passes if EITHER: `abs(blis_score - llmd_score) <= 1e-6` (absolute epsilon, checked first — catches scores near zero), OR `abs(blis_score - llmd_score) / abs(blis_score) <= 0.01` (1% relative error, checked second). A tuple passes if either condition is satisfied (disjunction). This catches magnitude distortion (e.g., BLIS produces [0.1, 0.5, 0.9] but llm-d produces [0.1, 0.2, 0.3] — ranks match but magnitude is lost).
  2. **Rank correlation**: Kendall-tau rank correlation > `equivalence_threshold` (default: 0.8, configurable). This catches logical inversions and threshold translation errors.
- **Borderline** is triggered when EITHER condition holds (disjunction): Kendall-tau in [0.75, 0.85], OR numeric fidelity failures on <10% of tuples (but >0%). If borderline is triggered, halt for interactive user review (see borderline handling below). If the user overrides a borderline result (choosing "proceed"), the PR is promoted with a `borderline-override` label and the user's reasoning is recorded. Borderline overrides satisfy the Stage 8 "no borderline" requirement only when accompanied by documented reasoning — the override converts the borderline to a conditional pass.
- Report both metrics separately in the equivalence report.

*Borderline handling:* The pipeline runs in an interactive Claude Code session. On a borderline result, the orchestrator prints the borderline report (metric values, which tuples failed, per-signal breakdown) and asks the user to choose: (a) proceed with degraded confidence, (b) abort, or (c) re-run with adjusted thresholds. The user's decision and reasoning are recorded as a PR comment for audit trail and written to `transfer_workspace/overrides.json` with fields: `{"type": "borderline-override", "suite": string, "reasoning": string, "timestamp": ISO8601}`. Stage 8 reads this file to detect overrides (see Stage 8 promotion criteria). The borderline wait uses its own 30-minute timeout (wall-clock, checked by polling every 30 seconds). **This timeout is independent of the per-suite timeout** — when a borderline result halts the pipeline, the per-suite clock pauses until the user responds. The Stage 6 total timeout (30 minutes of active computation) does not include human think time. If the user does not respond within 30 minutes, the pipeline fails with "human review timeout."

*Threshold calibration:* The 0.8 default is provisional. For a typical 5-branch algorithm with 10 endpoints, Kendall-tau 0.8 permits approximately 10% rank inversions (1 in 10 endpoint pairs reordered). After the first 3 transfers, collect Kendall-tau distributions for algorithms that passed vs. failed cluster benchmarks. Recalibrate thresholds to minimize false positive/negative rates. Calibration data is stored in `docs/transfer/calibration_log.md` — one entry per completed transfer. Each entry contains: `algorithm_name`, `transfer_date`, `suite_a_kendall_tau`, `suite_a_numeric_fidelity_pass_rate`, `suite_b_kendall_tau`, `suite_b_threshold_crossing_pct`, `stage_7_verdict` (pass/fail/inconclusive), `stage_7_matched_workload_improvement`, `stage_7_mechanism_check_result`, `per_workload_improvement_ratios` (BLIS-predicted vs. cluster-observed), `overrides_used` (list), and `notes`. The calibration log is the input for threshold adjustment decisions; trend detection is manual (human reviews after every 3 transfers). Until calibrated, all equivalence verdicts are tagged as **provisional** in PR comments.

**Suite B — Staleness sensitivity:**
- Re-run Suite A tuples, but inject synthetic staleness into llm-d signal reads using **per-source-group staleness windows** drawn from the mapping artifact's documented collection intervals. Staleness is injected at the source-group level: signals collected by the same mechanism receive correlated staleness (same random offset within the window), while signals from different collection sources are independent. This reflects production reality where signals from the same collection source age together.
- Source groups and their staleness windows are documented in the mapping artifact. Example groups: load signals (queue depth, active requests) from one collection source, cache signals (utilization, prefix match) from another, per-request signals (input size, SLO class) computed fresh with zero staleness.
- Run 3 repetitions with different staleness seeds per repetition.
- **Two pass criteria for staleness** (both must pass):
  1. **Rank stability**: Kendall-tau remains > `staleness_threshold` (default: 0.7, configurable) across all repetitions.
  2. **Threshold-crossing stability**: For each condition in the algorithm that crosses a numeric threshold (e.g., `if queue_depth > 100`), count how many tuples change which side of the threshold they fall on under staleness. If >20% of tuples at a threshold boundary change classification, the algorithm is staleness-sensitive at that threshold — flag for human review.
- **Borderline for Suite B:** Kendall-tau in [0.65, 0.75] or threshold-crossing instability in [15%, 25%]. Outside these ranges, the result is a clear pass or fail. Borderline results halt for interactive user review (same mechanism as Suite A borderline).
- If either criterion fails, emit a per-signal degradation report showing which signals and which threshold crossings cause instability.
- This answers Analysis Question 3 (minimum fidelity for transfer).

**Suite C — Concurrency stress:**

Two sub-tests to validate concurrency behavior:

*C1 — Parallel safety:* Issue 20 concurrent routing decisions against the same endpoint snapshot, each with a different request. Verify: no panics, no NaN scores, no deadlocks, all scores within expected range, and score results are deterministic (same inputs produce same outputs regardless of concurrent execution ordering).

*C2 — Pile-on dynamics:* Simulate sequential-but-rapid arrivals where each routing decision updates the endpoint state (e.g., increments pending request count) before the next decision reads it. Issue 20 requests in rapid succession, with each request seeing a snapshot that reflects all prior routing decisions' effects. Verify: the scorer's pile-on avoidance logic (if any) distributes requests across endpoints rather than concentrating on one. Specifically, if the BLIS algorithm uses pending request counts to avoid pile-on, verify that no single endpoint receives more than 2× its fair share of the 20 requests (or 1.5× for clusters with fewer than 5 endpoints, where discretization effects dominate). The 2× threshold is provisional and should be calibrated against BLIS simulation measurements of pile-on avoidance performance after the first transfer.

**Borderline for Suite C:** Suite C uses binary pass/fail criteria (no panics, deterministic results, pile-on within threshold). There is no borderline zone — any failure is a clear fail. However, if the pile-on ratio is within 10% of the threshold (e.g., 1.8×–2.0× for the 2× threshold), the result is flagged as "marginal" in the report for informational purposes (does not halt).

This validates both parallel safety and that the BLIS algorithm's sequential pile-on assumptions produce reasonable behavior under llm-d's concurrent model.

**Note on gap interaction:** Suites B and C test staleness and concurrency independently. In production, both occur simultaneously. A combined staleness+concurrency test is deferred to Future Extension (see #7). The risk is acknowledged: an algorithm may pass both suites independently but degrade when stale signals meet concurrent requests. The falsification protocol's early warning indicators (Suite B sensitivity trending upward) may catch this indirectly.

**Signal upgrade validation:** For each signal marked as `"Upgrade"` fidelity in the signal coverage report (i.e., the target system's signal is strictly richer than the source), include test cases where the richer signal diverges from what the source-equivalent aggregate would predict. The set of Upgrade signals is determined dynamically from the signal coverage report — not hardcoded. **Tuple budget:** Allocate up to 5 tuples per Upgrade signal (separate from the 200-tuple Suite A cap — upgrade tuples are additional). For each Upgrade signal, the transfer prompt template instructs the LLM to generate tuples sampling the divergence range (e.g., for per-request prefix match: tuples where requests to the same endpoint have varying prefix overlap of 0%, 50%, 100%). Assert that the scorer handles per-request variation without degrading below the equivalence threshold. If multiple Upgrade signals exist, their tuples are independent (no combinatorial explosion).

Results posted as PR comment with per-suite pass/fail and Kendall-tau values.

**BLIS evaluator dependency:** Suite A requires the BLIS evaluator to produce ground-truth score vectors. If the BLIS evaluator's input format changes (breaking the JSON test harness), Stage 6 fails at Suite A with a harness error — not a translation error. The diagnostic should distinguish harness failures (JSON parse errors, missing fields) from equivalence failures (scores computed but divergent). The test harness version is pinned in the transfer prompt template.

### Stage 7: Cluster Benchmark

Deploys and benchmarks using the target system's standard benchmarking tool (concrete tool name documented in the mapping artifact).

**Deployment configurations:** Stage 7 requires two explicit deployment configurations, both generated as artifacts during Stage 3 and stored in `transfer_workspace/`:

- **`baseline_deployment_config.yaml`**: The stock scorer configuration — all existing scorers at their default weights, new scorer disabled. This is the "before" state. Generated by copying the target system's current scoring config and ensuring the new scorer entry (if present) is set to disabled/weight-zero.
- **`treatment_deployment_config.yaml`**: The new scorer enabled, overlapping scorers disabled per the signal coverage report's `scorer_overlap` recommendations. This is the "after" state.
- Both configs must be complete and deployable — not diffs or patches. Each is validated in Stage 4 (the baseline config must produce behavior identical to stock; the treatment config must enable exactly the new scorer).

**Workload selection:** The workloads to benchmark are drawn directly from the algorithm summary's `workload_results` list (populated from `best_program_info.json`'s `metrics.workload` entry and the per-workload YAML config files). For each BLIS workload in that list, Stage 3 generates a benchmark config that uses the BLIS workload generator (a prerequisite component in llm-d-benchmark — see Supporting Artifacts) with the workload's parameters. These generated configs are recorded in the signal coverage report's `matched_workload` and `benchmark_workloads` fields. Stage 7 runs **all workloads**, not just the single matched workload — this enables the mechanism-specificity check. Because the BLIS workload generator reproduces BLIS workloads directly, there is no archetype-matching step and no "unmapped" workloads — every BLIS workload in the algorithm summary can be benchmarked.

**Benchmark procedure:**

0. **Pre-flight re-validation:** Before deploying: (a) re-verify that all generated benchmark configs are valid and that the BLIS workload generator is still available in the llm-d-benchmark repo — if the generator has been removed or its interface has changed since Stage 3, halt with diagnostic: "BLIS workload generator no longer available or incompatible — regenerate benchmark configs"; (b) verify the BLIS workload generator's current version matches the version pinned at Stage 3 (stored in `transfer_workspace/generator_version.txt`) — if the version has changed (even a minor bump), halt with diagnostic: "BLIS workload generator version changed since Stage 3 (pinned: X, current: Y) — regenerate benchmark configs to ensure workload fidelity"; (c) re-check noise characterization currency — if the characterization date is now >30 days old or the cluster config fingerprint no longer matches (which can happen if days pass between Stage 1 and Stage 7), warn the user and require confirmation to proceed with stale noise data or halt to re-characterize; (d) re-validate deployment configs against the target system's current scoring config — diff `baseline_deployment_config.yaml` against the target system's current stock config and warn if they have diverged (indicating the target system's config changed since Stage 3). If diverged, the user must choose: regenerate configs (return to Stage 3) or proceed with stale configs (logged in `transfer_workspace/decisions.json`).
1. Deploy with `baseline_deployment_config.yaml` (stock scorer config)
2. Run all mapped workloads, collect per-workload metrics (cache baseline results keyed by cluster config fingerprint + llm-d HEAD commit hash; reuse only if <24h old, config unchanged, AND llm-d HEAD unchanged since baseline was run)
3. Deploy with `treatment_deployment_config.yaml` (new scorer enabled)
4. Run the same workloads, collect per-workload metrics
5. Collect metrics: latency (first-token and end-to-end, mean + P95) and throughput. The specific metric names used by the benchmark tool are documented in the mapping artifact and may vary across benchmark tooling versions.
6. Compare treatment vs baseline per workload, and compare cluster improvement against BLIS-predicted improvement (see improvement magnitude comparison below)

**Health check during benchmark:** If error rate exceeds 10% or any endpoint becomes unresponsive within the first 60 seconds, abort the run, post a diagnostic comment, and leave the PR as draft. **On abort or Stage 7 failure:** redeploy the stock scorer config (baseline) to the cluster before releasing it (timeout: 5 minutes for redeployment). This ensures the cluster is not left running a failed experimental scorer. If redeployment exceeds the 5-minute timeout or fails, post a warning comment on the PR naming the cluster and failed config, and notify the user interactively — the user must manually restore the cluster.

**Go/no-go criteria:**

All thresholds below are configurable defaults stored in the transfer prompt template (not hardcoded here). These initial values are provisional — they must be calibrated against measured cluster noise before production use.

*Prerequisite — noise characterization:* Before the first transfer, run the baseline benchmark 5 times on the target cluster with identical config. Compute the coefficient of variation (CV) for each metric. The improvement threshold must exceed 2× the observed CV to distinguish signal from noise. If the measured CV exceeds 3%, the default 5% improvement threshold is too tight and must be raised, or multiple runs with statistical significance testing (see Future Extension #6) must be used instead.

*Default thresholds (applied to mean E2E latency):*
- Improvement > `improvement_threshold` (default: 5%) on at least one workload
- No regression > `regression_threshold` (default: 2%) on any workload
- P95 latency regression < `p95_regression_threshold` (default: 2%) on any workload
- **Mechanism check** (see below)

*Mechanism check — decision tree:*

The mechanism check validates that the transfer preserved the algorithm's mechanism, not just a spurious outcome. Steps are evaluated **in order** — once a step produces a verdict, later steps are skipped:

1. Identify the **matched workload**: the BLIS workload where the algorithm was discovered (the primary mechanism workload). This is identified at Stage 2 and recorded in the signal coverage report. The corresponding benchmark config uses the BLIS workload generator with the original workload parameters.
2. **Primary criterion**: The matched workload must show improvement ≥ `improvement_threshold`. If it doesn't → **FAIL** (mechanism not preserved).
3. **Confounding factor check** (evaluated before specificity): If all workloads improve by similar amounts (max improvement − min improvement < `improvement_threshold / 2`), the benefit is likely from a non-mechanism source (e.g., reduced overhead from removing a scorer) → **INCONCLUSIVE**. Tag as such in the PR comment and require human review. Do not auto-promote. *(This step is checked before the specificity criterion because uniform improvement makes the rank-based check meaningless.)*
4. **Mechanism-specificity criterion**: The matched workload must rank first or tied-first in improvement across all workloads, AND its improvement must exceed the mean improvement across all workloads by at least `improvement_threshold / 2`. **Fallback for ties**: If the matched workload's improvement is within 1 percentage point of the top-ranked workload, treat as tied-first. If this criterion fails → **FAIL** (improvement present but not mechanism-specific).
5. If all above pass → **PASS**.
6. **Run-count qualifier**: If only one run is available (v1 default), a PASS verdict is tagged as **provisional** in the PR comment (provisional results do not satisfy promotion criteria — see Stage 8). With ≥3 runs, use the mean improvement and require the matched workload to rank first or second across runs.

*Improvement magnitude comparison (BLIS prediction vs. cluster observation):*

For each workload, the PR comment includes a comparison table:

| Workload | BLIS Predicted Improvement | Cluster Observed Improvement | Ratio (Observed/Predicted) |
|---|---|---|---|

This comparison is **informational, not a gate** — it does not block promotion. However, it feeds into the falsification protocol:
- **Ratio < 0.3** (cluster sees less than 30% of predicted improvement): Flag as "significant attenuation" — the abstraction gap is likely degrading the algorithm's mechanism for this workload type.
- **Ratio > 2.0** (cluster sees more than 2× predicted improvement): Flag as "unexpected amplification" — the cluster benefit may come from a mechanism BLIS doesn't model (e.g., reduced scorer overhead), making the mechanism check less trustworthy.
- Over multiple transfers, systematic attenuation or amplification for a workload type signals that BLIS simulation fidelity needs calibration for that workload. Track per-workload-type ratios in the calibration log.

Results posted as PR comment with verdict, noise characterization reference, and BLIS-vs-cluster comparison table.

**Stage timeouts:** Stage 6 must complete within 30 minutes total, with per-suite budgets: Suite A = 10 minutes, Suite B = 10 minutes, Suite C = 5 minutes, remaining 5 minutes for integration tests and overhead. If a per-suite budget is exceeded, the orchestrator aborts that suite and posts partial results. **Suite dependency on timeout:** Because Suite A is a prerequisite for Suite B, and Suite B for Suite C (see Stage ordering above), if a prerequisite suite times out or fails, dependent suites are skipped — their results would be meaningless without the prerequisite passing. Specifically: if Suite A times out or fails, skip Suites B and C; if Suite B times out or fails, skip Suite C. The integration tests (which are independent of the equivalence suites) still run regardless. Stage 7 must complete within 60 minutes total (including cluster provisioning), with sub-operation budgets: deployment = 10 minutes, baseline benchmark = 20 minutes, treatment benchmark = 20 minutes, remaining 10 minutes for metrics collection and overhead. If a sub-operation exceeds its budget, abort and post diagnostic. If a total stage timeout is exceeded, post a diagnostic comment with the timeout reason and leave the PR as draft with a "timeout" label.

### Stage 8: Promote or Flag

**Promotion requires ALL of:**
- Stage 4: Unit tests pass (both new scorer and full repo suite), including disabled-scorer no-op test
- Stage 6: All suites pass with no borderline results (Suite A: both numeric fidelity and rank correlation pass, no borderline per Suite A definition; Suite B: both rank stability and threshold-crossing stability pass, no borderline per Suite B definition; Suite C: no failures — Suite C uses binary pass/fail with no borderline zone). **Borderline override detection:** If a borderline result was overridden by the user, Stage 8 detects this by checking `transfer_workspace/overrides.json` for entries with `type: "borderline-override"` and non-empty `reasoning`. A borderline override with documented reasoning satisfies the "no borderline" requirement. A borderline without override (no matching entry in `overrides.json`) does not.
- Stage 7: All go/no-go criteria met including mechanism check. **Provisional or inconclusive results do not satisfy this requirement** — they require human override or additional runs. Override requires a PR comment documenting: (a) which result is being overridden (provisional single-run, inconclusive mechanism check, etc.), (b) why the override is justified (e.g., "improvement is 3× noise floor, single run is sufficient"), and (c) any follow-up actions planned (e.g., "will re-run with 3 runs next week"). Overrides are tracked: after 5 transfers, audit override frequency — if >50% of promotions use overrides, the pipeline's default thresholds or run requirements need adjustment.

**Failure matrix:**
- Stage 6 fails (below threshold): Leave draft PR (created at Stage 5) with "transfer-failed" label and failure details as PR comment. Return to Stage 2 with diagnostic.
- Stage 6 borderline: Halt for human review (see borderline handling in Suite A).
- Stage 7 fails: Leave PR as draft with "needs-debugging" label. Post failure details. Human picks up from there.
- Stage 7 times out or infrastructure failure: Leave PR as draft with "infra-retry" label. User can re-trigger Stage 7 without re-running earlier stages.

**PR cleanup on re-trigger:** If the pipeline is re-triggered for the same algorithm and a draft PR already exists on the branch, the orchestrator detects it (via `gh pr list --head blis-transfer/<algorithm-name>`) and asks the user: "Reuse existing PR and update its description, or close it and create a new one?" This prevents orphaned draft PRs from accumulating.

**On promotion:** Add "validated" label. If any Stage 7 results are from a single run, tag as **provisional** in the PR description.

---

## Draft PR Structure

### Scheduler PR (primary)

Created at Stage 5 as draft. Contains:

- A new scorer plugin file with corresponding tests
- Plugin registration update and scoring config enabling the new scorer (with overlapping scorers disabled per the target system's convention, preserving config entries for easy rollback)
- **PR description:**
  - Algorithm summary (from BLIS discovery — what it does, which signals it uses)
  - Signal coverage report (which signals mapped at what fidelity)
  - Performance in simulation (metric deltas from BLIS experiment)
  - Validation checklist (auto-checked as stages complete):
    - [ ] Unit tests pass
    - [ ] Integration + equivalence tests pass
    - [ ] Cluster benchmark pass
  - Link to benchmark PR if applicable

### Benchmark PR (secondary, always created)

Always created because each transfer generates benchmark configs for the BLIS workloads:
- Workload benchmark configs using the BLIS workload generator (one per BLIS workload from the experiment)
- Scoring config variants (baseline vs adaptive) for A/B comparison

### PR Comments as Validation Log

Each post-draft validation stage appends a comment to the scheduler PR:
1. Integration + equivalence test results (Stage 6)
2. Cluster benchmark results + go/no-go verdict (Stage 7)
3. Promotion status (Stage 8)

---

## Signal Mapping Summary

The canonical, implementation-level signal mapping lives in `docs/transfer/blis_to_llmd_mapping.md` (single source of truth for concrete types, method names, metric paths, and fidelity ratings). **Do not duplicate fidelity ratings here** — the mapping artifact is authoritative and this table is a high-level orientation only.

| Signal Category | Behavioral Description | Correspondence Quality | Notes |
|---|---|---|---|
| **Load signals** | Per-endpoint measures of current work (waiting, active, combined) | Direct | Both systems track similar load metrics |
| **Cache utilization** | Per-endpoint cache usage level | Direct | Both express as fraction/percentage |
| **Prefix cache affinity** | How well a request matches cached prefixes | Target is richer | Target provides per-request granularity vs. source's aggregate — see signal upgrade validation in Stage 6 |
| **Batch/concurrency** | Current processing volume per endpoint | Direct | May use different counting methods |
| **Available resources** | Free capacity per endpoint (e.g., cache blocks) | May require derivation | Target may not expose directly |
| **Request characteristics** | Properties of the incoming request (size, priority, session) | Convention-dependent | Requires agreement on how request metadata is conveyed |

**Fidelity enum ownership:** The fidelity enum values (`High`, `Medium`, `Low`, `Upgrade`) and their criteria are defined in this design doc (see Stage Artifact Schemas) and are immutable for a given pipeline major version. The mapping artifact assigns fidelity ratings to individual signals using these enum values but must not redefine the criteria. If a new fidelity level is needed, it requires a pipeline major version bump. Per-signal fidelity assignments and translation strategies (direct map, proxy, stub, drop) are maintained exclusively in the mapping artifact to prevent staleness between documents.

---

## Workload Porting

BLIS workloads are reproduced in the cluster benchmark using the **BLIS workload generator** — a workload generator added to llm-d-benchmark that knows how to produce synthetic traffic matching BLIS workload patterns (see Supporting Artifacts). This eliminates the need for archetype-matching between BLIS workloads and pre-existing llm-d benchmark profiles.

**Per-transfer flow:**
1. Stage 1 reads per-workload results from `best_program_info.json`'s `metrics.workload` entry, and reads `<workload_name>.yaml` files for workload generator configuration.
2. Stage 2 validates each workload's parameters against the BLIS workload generator's configuration schema.
3. Stage 3 generates a benchmark config file for each workload (using the BLIS workload generator with the workload's parameters). These configs are included in the benchmark PR.
4. Stage 7 runs all generated benchmark configs against the cluster.

**Why direct workload porting instead of archetype matching:** Matching BLIS workloads to pre-existing llm-d benchmark profiles by archetype (e.g., "prefix-heavy" → "shared-prefix synthetic") is fragile — the llm-d profiles may not exist, may test different traffic characteristics, or may drift over time. Using the BLIS workload generator ensures the cluster benchmark exercises the exact traffic patterns that BLIS used during evolution, making the mechanism check (Stage 7) a faithful comparison.

The BLIS workload generator's configuration schema and supported parameters are documented in `docs/transfer/blis_to_llmd_mapping.md`.

---

## Supporting Artifacts

These files live in `docs/transfer/` in this repo. The BLIS workload generator lives in the llm-d-benchmark repo. **All four are prerequisites — the pipeline cannot execute without them.** They must be created before the first transfer attempt.

| File | Purpose | Status | Maintained By |
|---|---|---|---|
| `blis_to_llmd_mapping.md` | **Canonical** signal/interface mapping with concrete types, method names, metric paths, signal source groups, and staleness windows. Also documents the BLIS workload generator's configuration schema. Includes "last verified against llm-d commit" field and artifact version number. | **TODO — create before first transfer** | Manual — update when llm-d APIs change |
| `scorer_template.go.md` | Example of a well-structured llm-d scorer showing the plugin conventions and test structure. Must compile and pass tests against current llm-d HEAD. | **TODO — create before first transfer** | Manual — based on existing llm-d scorers. Includes artifact version number. |
| `transfer_prompt.md` | Structured prompt template that guides Claude Code through the 8-stage pipeline. Includes test harness design, tuple generation strategy, configurable thresholds, branch classification worked examples, and diagnostic runbook. | **TODO — create before first transfer** | Manual — update as pipeline evolves. Includes artifact version number. |
| BLIS workload generator (in llm-d-benchmark) | A workload generator added to llm-d-benchmark that produces synthetic traffic matching BLIS workload patterns. Accepts workload configuration parameters (traffic pattern, request size distribution, concurrency level, etc.) and generates benchmark traffic accordingly. This is a **one-time addition** to llm-d-benchmark, not per-transfer work. Must expose a version identifier (e.g., CLI flag, version file, or API method). | **TODO — add to llm-d-benchmark before first transfer** | Manual — maintained alongside llm-d-benchmark. Must be kept compatible with the BLIS evaluator's workload parameter format. The mapping artifact documents the expected generator version. |

**Artifact acceptance criteria (all four must satisfy before first transfer):**
- Mapping artifact: contains at least one complete signal mapping entry with all fields (type, method, metric path, scraper group, staleness window, fidelity rating with justification), documents the BLIS workload generator's configuration schema, "last verified against" commit is current llm-d HEAD, and version is 1.0.
- Scorer template: compiles and passes its unit tests against current llm-d HEAD.
- Transfer prompt: contains all 8 stage instructions, test harness design, and at least one worked example of branch classification.
- BLIS workload generator: installed in llm-d-benchmark, exposes a version identifier, accepts at least one BLIS workload config, and produces a valid benchmark run against the target cluster. The mapping artifact documents the expected generator version; Stage 1 validates that the installed generator's version is compatible (same major version).

**Artifact version scheme:** Versions use `major.minor` format. **Major bump** (e.g., 1.0 → 2.0): breaking change — the pipeline must be updated to match (e.g., new required field, changed interface contract). **Minor bump** (e.g., 1.0 → 1.1): additive/compatible change (new signal added, threshold adjusted). The pipeline checks that its expected major version matches the artifact's major version; a mismatch halts Stage 1 with a diagnostic. Minor version mismatches are logged but allowed.

*Version mismatch recovery:* If Stage 1 halts on a major version mismatch, the diagnostic includes the expected and actual versions plus the action: "If artifact version is newer than pipeline expects → update the transfer prompt template to match the new artifact version. If pipeline expects a newer version than the artifact provides → update the mapping artifact (the artifact was not updated after a pipeline change)." The expected major version for each artifact is stored in the transfer prompt template's header section.

**Staleness prevention:** Before each transfer, Stage 1 performs three checks in order:
1. **Compilation check:** Test-build the scorer template against current llm-d HEAD (compile only). If the template no longer compiles, the mapping artifact is stale — halt with a diagnostic listing which interfaces have changed.
2. **Smoke test check:** Run the scorer template's unit tests (if any) against llm-d HEAD. A compile-passing but test-failing template indicates semantic drift (e.g., an interface method was renamed but the old name still compiles via a deprecated alias). If tests fail, halt with diagnostic.
3. **Commit distance check (secondary heuristic):** Verify the mapping artifact's "last verified against" commit hash is within 50 commits of the current llm-d HEAD. If exceeded, warn the user and require explicit confirmation to proceed. This is a supplementary heuristic — it does not catch semantic drift, only flags that more changes have accumulated than expected.

**Limitation:** These checks detect API breakage and some semantic drift, but cannot detect silent behavioral changes (e.g., a metric's meaning changed without renaming). The mapping artifact's "last verified" field must be manually updated by a human who has reviewed the relevant llm-d changes. **Mitigation:** The commit distance check (50-commit threshold) serves as a secondary signal that manual review may be overdue. If the mapping artifact's "last verified" date is >14 days old, Stage 1 emits an additional warning: "Mapping artifact manual review may be stale — consider reviewing recent llm-d changes before proceeding." This does not halt the pipeline but is logged in `transfer_workspace/decisions.json` for audit.

**P/D disaggregation detection:** Stage 1 must also check whether the target cluster uses prefill/decode disaggregation (by inspecting the target scheduler config for known P/D indicators — documented in the mapping artifact). If P/D is detected, the orchestrator prints a warning explaining that the transferred scorer applies only to the non-disaggregated scheduling path, and prompts the user to type `proceed` or `abort`. The user's decision is logged in `transfer_workspace/decisions.json` with fields: `{"decision": "pd_proceed"|"pd_abort", "timestamp": ISO8601, "cluster_config": string}`. Stage 2 reads this file and skips P/D-specific signal mappings if the user chose to proceed (the algorithm is transferred as non-disaggregated only). **P/D runtime behavior:** If the generated scorer is invoked in a disaggregated scheduling context, it must return a neutral score (equivalent to the default scorer's behavior for that context) rather than silently applying non-disaggregated logic. The scorer template must include a guard that detects the scheduling context and falls back to neutral behavior. Stage 4 unit tests must include a test case verifying this guard. If the user does not respond, Stage 1 blocks (no timeout — this is a deliberate human decision point, not a race condition).

The design doc describes *what* the pipeline does and *why*. The mapping artifact describes *how* at the implementation level. The prompt template orchestrates execution. The BLIS workload generator reproduces BLIS workloads in the cluster. These four artifacts are the single sources of truth for their respective concerns.

**`transfer_workspace/` lifecycle:** Created by Stage 1 on the `blis-transfer/<algorithm-name>` branch and added to `.gitignore`. All stages read and write artifacts to this directory. Lifecycle rules:
1. **Persistence:** `transfer_workspace/` persists across stage re-runs to enable crash recovery. Artifacts are authoritative once written — stages do not regenerate unless the user explicitly requests it.
2. **Crash recovery:** If the orchestrator restarts and `transfer_workspace/` exists, prompt the user: "Prior run detected. (a) Resume from the next incomplete stage, or (b) delete and restart from Stage 1." The presence of stage-specific artifacts (e.g., `algorithm_summary.json`, `signal_coverage_report.json`) indicates which stages completed.
3. **Post-promotion:** `transfer_workspace/` persists for audit trail. Not deleted on promotion.
4. **Git status:** Not committed to git. Discarded when the branch is deleted or merged.

---

## Stage Artifact Schemas

Each stage produces structured artifacts consumed by downstream stages. Schemas are specified here so stages can be implemented independently.

**Stage 1 output — Algorithm summary (JSON):**
- `evolve_block_code`: string — the raw EVOLVE-BLOCK source code
- `evolve_block_file`: string — path to the source file containing the EVOLVE-BLOCK
- `hypothesis_results`: object — hypothesis name, test results, verdict from the experiment
- `workload_results`: list of objects — per-workload configuration and results from the BLIS experiment. Stage 1 validates that at least one workload entry exists in `metrics.workload` and that a corresponding YAML config file exists. Each entry:
  - `workload_name`: string — BLIS workload identifier (e.g., `"prefix_heavy_high_throughput"`)
  - `workload_params`: object — BLIS workload generator configuration parameters (traffic pattern, request size distribution, concurrency level, etc.) needed to reproduce this workload via the BLIS workload generator in llm-d-benchmark. Extracted from the corresponding `<workload_name>.yaml` file.
  - `results`: object — experiment results for this workload, extracted from `best_program_info.json`'s `metrics.workload` entry (metrics under the evolved algorithm, improvement data, etc.). The concrete schema is determined by the BLIS evaluator's output format (**BLOCKING TODO: document once an example is available — Stages 1, 2, 3, and 7 cannot be implemented until this schema is defined**). The pipeline requires at minimum: (a) a latency or throughput metric comparable to Stage 7 cluster benchmark results, and (b) an improvement magnitude usable by the Stage 7 mechanism check's BLIS-vs-cluster comparison table.
  - `config_file`: string — path to the source `<workload_name>.yaml` configuration file for audit trail
- `signals_used`: list of strings — signal names referenced in the EVOLVE-BLOCK (e.g., `["queue_depth", "cache_utilization", "input_size"]`)
- `scope_verdict`: enum — `"pass"`, `"marginal"`, `"reject"`. Reflects the scope validation result (see Stage 1 scope validation). A `"reject"` halts the pipeline before this artifact is written. A `"marginal"` means the user confirmed proceeding despite non-linear operators.
- `source_experiment`: string — path to the experiment output directory for audit trail

**Stage 2 output — Signal coverage report (JSON):**
- `signals`: list of objects, each containing:
  - `blis_signal`: string — signal category name (from Signal Mapping Summary categories, not internal field names)
  - `fidelity`: enum — `"High"`, `"Medium"`, `"Low"`, `"Upgrade"` (ratings defined in the mapping artifact using these criteria: High = behavioral equivalence with <5% divergence in test comparisons; Medium = same concept but different granularity or access pattern; Low = requires approximation or proxy; Upgrade = target signal is strictly richer than source)
  - `fidelity_justification`: string — brief explanation of why this fidelity rating was assigned (e.g., "Direct numeric equivalent with same units" for High, or "BLIS uses aggregate cache hit ratio; llm-d provides per-request prefix match — richer granularity" for Upgrade). Required for Medium, Low, and Upgrade ratings; optional for High.
  - `branches`: list of objects — `{"line": int, "code_snippet": string, "classification": "critical"|"non-critical", "classification_reason": enum("default_path"|"score_dominant"|"all_request"|"nested_critical"|"narrow_with_fallback"|"conservative_tiebreak")}`
  - `translation_strategy`: enum — `"direct_map"`, `"proxy"`, `"stub"`, `"drop"`
- `matched_workload`: object — `{"blis_workload_name": string, "workload_params": object, "benchmark_config_path": string}` — the BLIS workload where the algorithm was discovered (primary mechanism workload), the workload's generator parameters extracted from the per-workload YAML file, and the path to the generated benchmark config file (populated by Stage 3)
- `benchmark_workloads`: list of objects — all BLIS workloads from the algorithm summary's `workload_results`, each with a generated benchmark config: `{"blis_workload_name": string, "workload_params": object, "benchmark_config_path": string, "is_matched": boolean}`. The `is_matched` field is true for the primary workload (same as `matched_workload`). The `benchmark_config_path` is populated by Stage 3 after generating the configs using the BLIS workload generator.
- `scorer_overlap`: list of objects — `{"scorer_name": string, "shared_signals": list of strings, "action": "disable"|"keep"}` — existing scorers that share signals, with recommended action
- `has_low_fidelity_critical`: boolean — true if any critical branch uses a Low-fidelity signal (triggers halt)
- `mapping_artifact_version`: string — the version number of `blis_to_llmd_mapping.md` used when assigning fidelity ratings (e.g., `"1.2"`). Enables downstream stages and audit to detect if the mapping artifact was updated after the signal coverage report was generated. If a later stage detects a version mismatch (mapping artifact version is newer than what the report records), it halts with diagnostic: "Signal coverage report was generated against mapping artifact vX but current mapping is vY — re-run Stage 2."

**Stage 2 output — Mapped algorithm spec (structured text):**
- `conditional_logic`: list of condition→weight-adjustment rules translated from the EVOLVE-BLOCK, expressed in pseudocode referencing llm-d signal categories (not BLIS field names)
- `weight_defaults`: object — default weight values when conditions don't match
- `weight_convention`: enum — `"normalized"` (weights sum to 1.0, BLIS convention) or `"raw"` (weights in target system's native scale). Stage 3 uses this to determine whether to apply normalization during code generation. The mapping artifact documents which convention the target system uses.
- `signal_requirements`: list — which endpoint metrics the scorer must read, referencing the mapping artifact for concrete access patterns

---

## Noise Characterization (Stage 0 prerequisite)

Before the first transfer on a target cluster, a one-time noise characterization must be performed:

1. Run the baseline benchmark 5 times on the target cluster with identical config
2. Compute the coefficient of variation (CV) for each metric (TTFT mean, TTFT P95, E2E mean, E2E P95, throughput)
3. Store results in `docs/transfer/noise_characterization.md` with: cluster config fingerprint, date, per-metric CV values, and the derived improvement threshold (2× max CV)
4. If the measured CV exceeds 3% for any metric, the default 5% improvement threshold is too tight — raise to 2× the measured CV, or plan for multi-run benchmarks (Future Extension #6)

**When to re-characterize:** After any cluster config change (hardware, Kubernetes version, scheduler version) or if >30 days have elapsed since the last characterization.

**Stage 1 noise data check:** Stage 1 reads `docs/transfer/noise_characterization.md` and parses the `date` field (ISO 8601 format) and the `cluster_config_fingerprint` field (a hash of scheduler version + Kubernetes version + hardware class). Stage 1 compares: (a) the characterization date must be within 30 days of today, and (b) the cluster config fingerprint must match the current target cluster's fingerprint (computed by the orchestrator from cluster metadata). If either check fails, Stage 1 halts with a diagnostic: "Noise characterization is stale — re-run baseline benchmarks on the target cluster (see Noise Characterization section)."

---

## Falsification Protocol

**Early warning indicators (before Stage 7):** Track these across transfers to detect problems before 3 full failures:
- Suite A Kendall-tau trending downward across successive transfers (even if still above threshold)
- Suite B staleness sensitivity increasing (more threshold-crossing instability)
- Stage 4 retry rate increasing (more code generation failures per transfer)
If any trend is observed across 2 consecutive transfers, flag for human review before starting the next transfer.

**Full falsification (post Stage 7):** If ≥3 independent algorithms — each with all High/Medium-fidelity signals, each passing Stage 6 equivalence tests — all fail Stage 7 cluster benchmarks (no improvement on matched workload), this constitutes evidence that the abstraction gap is too wide for the current BLIS simulation fidelity.

**Action on falsification:** Suspend pipeline. Investigate whether BLIS simulation must be improved (finer-grained signal modeling, staleness simulation, concurrency modeling) before further transfers. Document findings in the mapping artifact.

**Partial falsification:** If algorithms fail Stage 7 only on specific workload types, the gap may be workload-specific. In that case, restrict transfers to workload types where the pipeline has demonstrated success. Track per-workload-type pass/fail rates in the calibration log. If failure patterns suggest the BLIS workload generator is not faithfully reproducing certain traffic patterns, investigate generator fidelity for those workload types.

---

## Diagnostic Guide

When a transfer fails, use this decision tree to identify the root cause:

1. **Stage 4 failure (unit tests):** Check failure classification output. If "stale mapping" → update mapping artifact and re-run. If "code generation" after 3 retries → inspect the generated code manually; the algorithm may be too complex for LLM translation (see scope validation).
2. **Stage 6 Suite A failure (equivalence):** Check which tuples fail. If failures cluster at threshold boundaries → the translation has an off-by-one or inverted condition. If failures are widespread → the mapped spec is wrong; re-examine Stage 2 output. If numeric fidelity fails but rank correlation passes → magnitude scaling issue (weight normalization mismatch).
3. **Stage 6 Suite B failure (staleness):** Check per-signal degradation report. If a single signal causes most degradation → that signal's staleness window is larger than expected; consider a proxy strategy. If all signals degrade → the algorithm is fundamentally staleness-sensitive; consider whether BLIS can simulate staleness.
4. **Stage 7 failure (cluster benchmark):** Check mechanism check verdict. If "inconclusive" (all workloads improve equally) → benefit is likely from scorer consolidation, not the algorithm. If matched workload doesn't improve → either the BLIS workload generator is not faithfully reproducing the BLIS workload conditions, or the algorithm's benefit doesn't survive real-world conditions. If no workload improves → check noise characterization; the improvement threshold may be too tight for cluster noise.
5. **Repeated failures across algorithms:** Check falsification protocol early warning indicators. If trends are worsening, the abstraction gap may be widening (llm-d evolving away from BLIS assumptions).

The transfer prompt template contains a more detailed version of this guide with specific commands and file paths.

---

## Future Extensions

These are explicitly **out of scope** for the initial implementation but worth noting:

1. **P/D-aware transfer**: Extend BLIS to model prefill/decode disaggregation, then extend the pipeline to generate scorers for specific scheduling profiles (decode vs. prefill)
2. **Bidirectional transfer**: Insights from production llm-d benchmarks fed back to BLIS to improve simulation fidelity
3. **Claude Code skill**: Package the transfer as a first-class `/transfer-to-llmd` skill with argument parsing
4. **Multi-algorithm transfer**: Batch transfer of top-N algorithms from a single experiment, with comparative analysis
5. **Continuous integration**: Auto-trigger on new best program discovery above a threshold
6. **Statistical confidence**: Require multiple benchmark runs (≥3) with significance testing instead of single-run go/no-go.
7. **Combined staleness+concurrency test (Suite D)**: Inject staleness into signal reads while simultaneously issuing concurrent requests. This tests the interaction between staleness and concurrency, which v1 tests independently in Suites B and C. Note: v1 uses single-run benchmarks with results tagged as **provisional**. The noise characterization prerequisite (see Stage 7 go/no-go criteria) partially mitigates this by ensuring thresholds exceed measured noise, but multi-run significance testing is the proper solution.

---

## Summary

The transfer pipeline is an **interactive Claude Code session** guided by:
- A **mapping artifact** (`blis_to_llmd_mapping.md`) that captures the concrete abstraction correspondences (single source of truth for implementation details)
- A **scorer template** showing the target code structure and plugin conventions
- A **prompt template** that orchestrates the 8-stage pipeline
- A **BLIS workload generator** (in llm-d-benchmark) that reproduces BLIS workloads in the cluster benchmark

The core translation pattern is a **new scorer plugin** that reads endpoint metrics directly and applies the BLIS-discovered conditional logic. This avoids wrapping existing scorers (which creates coupling and double-counting risks) and is consistent with how existing llm-d scorers work.

The pipeline follows a **staged PR model**: draft PRs are created after unit tests pass (Stage 5), then integration tests, semantic equivalence checks, and cluster benchmarks run against the draft branch. Results are posted as PR comments, providing a complete audit trail. Only after cluster benchmarks meet go/no-go criteria is the PR promoted to ready-for-review. Failures leave the draft PR open for manual debugging.

The pipeline answers three core questions: does the benefit survive transfer, which signals preserve the mechanism, and what fidelity is needed. Semantic equivalence tests (Stage 6) verify the translation preserves behavior; cluster benchmarks (Stage 7) verify the benefit appears in production.
