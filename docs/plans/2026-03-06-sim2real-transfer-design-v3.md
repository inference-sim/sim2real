# Sim-to-Production Algorithm Transfer Pipeline Design (v3)

**Date:** 2026-03-06
**Status:** Draft
**Author:** Claude Code + kalantar
**Supersedes:** `docs/plans/2026-02-26-blis-to-llmd-transfer-design-v2.md`

### Revision History

| Date | Change | Reason |
|------|--------|--------|
| 2026-03-06 | v3 rewrite | Re-home pipeline from OpenEvolve to sim2real. Remove OpenEvolve dependency. Prompt-driven architecture with thin CLI tools. Work locally in submodules, PR as final step. Drop operational scaffolding, keep validation rigor. Design for generality (routing first). 8 stages → 6. |

---

## Analysis Questions

This pipeline exists to answer:

1. **Does the sim-discovered algorithm's benefit survive the abstraction gap?** — Do simulation-predicted improvements appear in production benchmarks?
2. **Which simulation signals have production equivalents that preserve the algorithm's mechanism?** — Can we faithfully map the signals the algorithm depends on? *"Preserve the algorithm's mechanism" means: when the translated plugin receives the same logical input state as the simulation, it produces the same ranking of endpoints (measured by Kendall-tau in Suite A) and the same directional responses to signal changes (verified by threshold-boundary tuples). This is an indirect measure — it confirms behavioral equivalence at the individual-signal level but does not capture emergent effects from signal interactions. Suite A is the primary test for this question.*
3. **[Future — v2+] What is the minimum fidelity needed for the benefit to transfer?** — When a signal maps imperfectly, does the algorithm degrade gracefully or break? *v1 does not compute fidelity-benefit correlation. However, the calibration log accumulates fidelity ratings and benchmark results per transfer, building the dataset needed to answer this question after multiple transfers. This analysis is deferred to v2 or later, when sufficient data exists (estimated: after 5+ transfers).*

Every validation step (Translate, Equivalence Testing, Cluster Benchmark) traces back to at least one of these questions. The mapping from questions to pipeline verdicts:

| Analysis Question | Answered By | **Yes** criterion | **No** criterion |
|---|---|---|---|
| Q1: Does the benefit survive? | `overall_verdict` in `validation_results.json` | `overall_verdict` = PASS (all suites pass AND cluster benchmark mechanism check = PASS) | `overall_verdict` = FAIL at any stage |
| Q2: Which signals preserve mechanism? | `signal_coverage.json` fidelity ratings + Suite A results | All mapped signals have fidelity High/Medium AND Suite A passes (rank + numeric fidelity) | Any signal rated Low (halts at Stage 1) OR Suite A fails (signal mapping doesn't preserve behavior) |
| Q3: Minimum fidelity needed? | Calibration log (v2+, deferred) | Sufficient data from 5+ transfers to compute fidelity-benefit correlation | Insufficient transfers completed |

A **successful transfer** is defined as: `overall_verdict` = PASS, which requires all of: Suite A pass, Suite B pass, Suite C pass, and cluster benchmark mechanism check PASS (or INCONCLUSIVE with recorded user override). This answers Q1 = Yes and Q2 = Yes (for the signals used by this specific algorithm).

## Problem Statement

We have a simulation environment (inference-sim) that discovers adaptive algorithms — routing policies, admission policies, priority policies — validated in simulation but existing only as Go code within the simulator's abstractions.

We need a **transfer pipeline** that takes a discovered algorithm and its evaluation artifacts, translates it into production-quality code for a target system (e.g., `llm-d-inference-scheduler`), validates the translation preserves the algorithm's behavior and benefit, and produces tested PRs against the target repos.

### Scope (v1 — routing transfer)

**In scope:**
- Single algorithm transfer (one evolved code block at a time)
- Algorithms expressible as conditional linear combinations of endpoint signals (e.g., "if input is long, weight prefix higher")
- Algorithms that use only High or Medium fidelity signals (see Signal Mapping). *Fidelity decision rule: ALL signals used by the algorithm must have fidelity rated High or Medium in the mapping artifact. Stage 1 reads the mapping artifact to check signal fidelity before proceeding; the mapping artifact must exist before Stage 1 runs. Error paths:*
  - *If a signal used by the algorithm is **not listed** in the mapping artifact → halt with: "Signal `<name>` used by algorithm but not found in mapping artifact. Add this signal to the mapping artifact before proceeding." This is distinct from a Low-fidelity rating.*
  - *If any signal is rated **Low** → halt with: "Signal `<name>` rated Low fidelity — algorithm is out of scope. Low-fidelity signals: `<list>`."*
  - *If any signal is rated **Upgrade** → treated as High for scope purposes (the richer production signal is acceptable).*
- Primarily non-disaggregated clusters
- Manual trigger, interactive Claude Code session

**Out of scope:**
- P/D disaggregation-aware transfer (requires simulation extension)
- Algorithms requiring non-linear transformations beyond what the target system provides
- Algorithms that depend on Low-fidelity signals (pipeline halts for human review)
- Bidirectional transfer (production insights back to simulation)
- Multi-algorithm transfer or algorithm composition
- Continuous integration trigger

### Input Artifacts

The pipeline consumes opaque artifacts from a completed experiment. It does not depend on how these artifacts were produced.

- **`best_program.py`** — Contains the evolved algorithm as embedded source code with `EVOLVE-BLOCK-START`/`EVOLVE-BLOCK-END` markers
- **`best_program_info.json`** — Experiment metadata: metrics, per-workload results (under `metrics.artifacts.workload_results`), baseline comparisons
- **`<workload_name>.yaml` files** — Workload configurations sufficient to reproduce the experiment's traffic patterns

Input artifacts live in per-type directories (e.g., `routing/` for the first transfer type).

### Constraints

**Operational:**
- **Trigger:** Manual, post-experiment (user decides which algorithm to transfer)
- **Automation:** LLM-driven with supporting CLI tools — Claude Code reads prompt templates and executes each stage interactively
- **Human involvement:** Interactive Claude Code session — user sees each step and can intervene
- **Translation engine:** LLM-powered (Claude reads both codebases and bridges the abstraction gap)

**Quality:**
- Generated code must pass existing CI of target repos
- Generated code must follow target system plugin conventions (as documented in the scorer template artifact)

**Dependencies:**
- Pipeline depends on `llm-d-inference-scheduler` and `llm-d-benchmark` submodules
- The mapping artifact pins a "last verified against" commit hash for each
- **Staleness guard:** At the start of Stage 2, the pipeline compares the mapping artifact's pinned commit hash against the current submodule HEAD. If they differ, the pipeline warns: "Mapping artifact was verified against `<pinned>` but submodule is at `<current>`. Review mapping for changes before proceeding." The user must choose one of:
  - **Acknowledge** — proceed with the current mapping. The pipeline records `staleness_acknowledged: true`, `acknowledged_drift: "<pinned>..<current>"`, and `staleness_review_summary: "<user-provided description of which files changed and why they don't affect the mapping>"` in the signal coverage artifact. This is appropriate when the user has reviewed the diff and confirmed no API-breaking changes. **Downstream enforcement:** When `staleness_acknowledged` is true, the following stages adjust behavior:
    - **Stage 5 (Validate):** Suite A, B, and C results are annotated with `staleness_context: "mapping drift acknowledged (<pinned>..<current>)"`. If any suite *fails*, the failure diagnostic prominently notes that the mapping was not re-verified against HEAD, and recommends returning to Stage 2 with the **Update** option before debugging further.
    - **Stage 5 (Cluster benchmarks):** The `validation_results.json` field `staleness_acknowledged` is set to `true`, and the PR description (Stage 6) includes a warning: "Note: mapping artifact was verified against `<pinned>`, not current HEAD `<current>`. Staleness was acknowledged by the operator."
    - **Stage 6 (PR):** The PR description includes the staleness context and the user's review summary, ensuring reviewers are aware of the drift.
  - **Update** — update the mapping artifact's pinned commit hash to the current HEAD and review/update any affected signal mappings. Specifically: run `git diff <pinned>..<current> -- <paths_to_metric_types>` in the target submodule to identify changed types or APIs, then update affected entries in the mapping artifact. The updated mapping artifact is committed to the sim2real repo immediately (not deferred) so that crash recovery sees the updated version. If the diff reveals breaking API changes (removed fields, changed types, renamed packages), the pipeline halts Stage 2 and reports: "Breaking changes detected in target submodule: `<summary>`. Update the mapping artifact to reflect these changes before proceeding." The transfer does not proceed until the mapping artifact is updated and re-validated with `transfer_cli.py validate-mapping`.
  The staleness check runs once per Stage 2 entry (including re-entry after returning from a later stage). The check compares commit hashes only — it does not detect whether specific metric APIs changed. The user is responsible for reviewing the diff to assess impact. This is a known limitation; automated API-level validation is deferred to a future version.

---

## Architecture Overview

### Pipeline Stages

```
[1. Extract] → [2. Translate] → [3. Generate] → [4. Test] → [5. Validate] → [6. PR]
```

| Stage | What | Input | Output |
|---|---|---|---|
| **Extract** | Parse input artifacts, extract evolved code block, validate scope | `best_program.py`, `best_program_info.json`, workload YAMLs | Algorithm summary (code, signals used, scope verdict, per-workload results) |
| **Translate** | Map simulation signals to target system equivalents, classify branches, produce coverage report | Algorithm summary + mapping artifact | Signal coverage report + mapped algorithm spec |
| **Generate** | LLM generates target-system code (plugin, tests, configs) in the target submodule. **Conflict handling:** If the mapped algorithm spec conflicts with the scorer template conventions (e.g., different weight normalization, incompatible type expectations), the LLM halts and reports the conflict: "Conflict between mapped spec and scorer template: `<description>`. The scorer template defines `<convention>` but the algorithm requires `<requirement>`." The user decides: adapt the algorithm to match the template, update the template, or abort. The LLM does not silently resolve conflicts. | Mapped spec + scorer template + target submodule | Files on a local branch in the target submodule |
| **Test** | Build and test generated code locally in the target submodule | Generated files | Pass/fail + error context (LLM fixes and retries interactively) |
| **Validate** | Equivalence testing (Suites A/B/C via Go harness using inference-sim) + cluster benchmarks with mechanism check | Passing code + inference-sim + cluster access | Per-suite results + benchmark verdict |
| **PR** | Push branches, create PRs against upstream repos with validation summary | Validated code + validation results | PR URLs |

**Inter-stage artifact contracts:**

Each stage produces a structured output that the next stage consumes. Artifacts are written to `workspace/` as JSON or Markdown files. **Schema validation:** Each consuming stage validates its input artifact before processing: (a) JSON parses without error, (b) all required fields are present and non-null, (c) field types match expectations (strings are non-empty, arrays have expected element structure, enums are within allowed values). If validation fails, the stage halts with: "Invalid input artifact `<file>`: `<specific field and error>`." All field types below use JSON types: `string`, `number`, `boolean`, `array`, `object`. Array element schemas are specified inline.

| Stage | Output File | Format | Required Fields |
|---|---|---|---|
| **1 → 2** | `workspace/algorithm_summary.json` | JSON | `code_block` (string: extracted source), `signals_used` (array of `{name, sim_field, usage_description, fidelity}` where fidelity is looked up from the mapping artifact), `scope_verdict` ("pass" \| "marginal" \| "reject"), `marginal_ops` (array of operator strings, empty if scope=pass), `per_workload_results` (array of `{workload_name, metric_name, baseline_value, evolved_value, improvement_pct}`), `matched_workload` (string: workload name with highest improvement_pct, designated by user if tied), `branch_count` (integer: number of conditional branches) |
| **2 → 3** | `workspace/signal_coverage.json` | JSON | `mappings` (array of `{signal_name, sim_field, prod_field, prod_type, access_path, fidelity, staleness_window_ms, mapping_notes}`), `scorer_overlap` (array of `{scorer_name, shared_signals, recommendation: "disable"\|"keep"}`), `unmapped_signals` (array of `{signal_name, sim_field, usage_in_algorithm}`, must be empty to proceed — see unmapped signal resolution below), `mapped_algorithm_spec` (string: pseudocode with production signal names substituted), `branch_count` (integer: must match Stage 1 output) |
| **5 → 6** | `workspace/validation_results.json` | JSON | `suite_a` (`{passed, kendall_tau, numeric_fidelity_failures, total_tuples}`), `suite_b` (`{passed, rank_stability_tau, threshold_crossing_change_pct}`), `suite_c` (`{passed, parallel_safety, pile_on_max_share}`), `cluster_benchmark` (`{verdict: "PASS"\|"FAIL"\|"INCONCLUSIVE", per_workload: [{name, baseline, treatment, improvement_pct, is_matched}], mechanism_check_result, noise_cv}`), `overall_verdict` ("PASS" \| "FAIL" \| "INCONCLUSIVE") |

**Unmapped signal resolution:** If Stage 2 finds signals used by the algorithm that have no entry in the mapping artifact, it collects *all* unmapped signals (does not halt on the first one) and reports them together: "The following signals are used by the algorithm but not in the mapping artifact: `<list with sim_field and usage context>`. Resolution options: (a) Add the signal(s) to the mapping artifact and re-run Stage 2, or (b) Determine if the algorithm can be modified to not use the signal (return to experiment). The pipeline cannot proceed with unmapped signals." The `unmapped_signals` array in `signal_coverage.json` is populated with all unmapped signals for diagnostic purposes, but Stage 3 will not start until the array is empty.

Stages 3→4 and 4→5 operate on generated files in the target submodule rather than JSON artifacts. Their contracts are defined by file conventions:

**3 → 4 (Generate → Test):** Stage 3 creates a new branch in the target submodule named `transfer/<algorithm_name>` (e.g., `transfer/blis-routing-v1`). **Pre-flight checks:** Before creating the branch, Stage 3 verifies: (a) the target submodule is on a detached HEAD or a known branch (not in a rebase/merge state), (b) there are no uncommitted changes (`git status --porcelain` is empty — if not, halt with: "Target submodule has uncommitted changes. Commit or stash before proceeding."), (c) the branch name `transfer/<algorithm_name>` does not already exist (if it does, halt with: "Branch `transfer/<algorithm_name>` already exists. Delete it or choose a different algorithm name."). All generated files are committed to this branch with a conventional commit message: `"[transfer] Add <algorithm_name> plugin (generated by sim2real pipeline)"`. Stage 3 writes a manifest file `workspace/generated_files.json` listing all created/modified files: `{"branch": "<branch_name>", "submodule": "<submodule_path>", "files": [{"path": "<relative_path>", "action": "create"|"modify", "role": "<role>"}]}`. Stage 4 reads this manifest to identify which files to test.

**Required file roles and structure:** Stage 3 must produce files with the following roles (the `role` field in the manifest):

| Role | Expected Path Pattern | Description | Required |
|---|---|---|---|
| `plugin` | `pkg/plugins/<algorithm_name>/scorer.go` | Main plugin implementing the scorer interface | Yes |
| `plugin_test` | `pkg/plugins/<algorithm_name>/scorer_test.go` | Unit tests including request parsing, no-op, and overlap tests | Yes |
| `config` | `config/<algorithm_name>.yaml` | Scoring config with feature flag and scorer weights | Yes |
| `config_baseline` | `config/<algorithm_name>_disabled.yaml` | Baseline config (plugin disabled, overlapping scorers restored) | Yes |
| `equivalence_test` | `pkg/plugins/<algorithm_name>/equivalence_test.go` | `ScoreEndpoints` function for Suite A/B/C harness | Yes |
| `doc` | `pkg/plugins/<algorithm_name>/README.md` | Plugin documentation (auto-generated summary) | No |

Path patterns follow the scorer template conventions. The `<algorithm_name>` directory is the plugin package. The LLM instantiates the scorer template by substituting: (a) package name from `algorithm_name`, (b) signal access paths from `signal_coverage.json` mappings, (c) conditional logic from `mapped_algorithm_spec`, (d) config values from the mapping artifact. Stage 4 validates that all required-role files exist in the manifest before running tests.

**4 → 5 (Test → Validate):** Stage 4 amends the branch with any fixes applied during retries. Each retry is a separate commit: `"[transfer] Fix: <error_class> — <brief_description>"`. When Stage 4 passes, it updates `workspace/generated_files.json` with a `test_passed_at` timestamp and `final_commit` SHA. Stage 5 reads the manifest to identify the branch and files under test. Stage 5 checks out the branch at the `final_commit` SHA to ensure it tests exactly the code that passed Stage 4.

**Stage 1 scope validation:** Before proceeding to Stage 2, Stage 1 checks whether the discovered algorithm falls within the "conditional linear combinations" scope:
- **In scope (pass):** `+`, `-`, `*`, `/` (scalar arithmetic), comparisons, `if`/`else`, ternary operators
- **Marginal:** `min`, `max`, `abs`, `clamp` — piecewise-linear, often translatable but require review. The pipeline halts and presents the user with each marginal operator, its context in the algorithm, and asks: proceed (operator is supported by the target system) or reject (abort transfer). The `scope_verdict` is set to `"marginal"`, `marginal_ops` lists the operators found, and `marginal_decision` records the user's choice (`"proceed"` or `"reject"`) with a timestamp. This decision is persisted in `algorithm_summary.json` for crash recovery and audit.
- **Reject:** `exp`, `log`, `pow`, `sqrt`, trigonometric functions, lookup tables, neural network layers

### What Drives Each Stage

- **Prompt templates** (`prompts/`) — Markdown files containing LLM instructions for each stage. These are the primary pipeline artifact. Claude Code reads a prompt template and executes the stage interactively. **Template stability:** At the start of Stage 1, the pipeline records the git commit hash of the sim2real repo in `workspace/algorithm_summary.json` as `pipeline_commit`. On entering each subsequent stage, the pipeline checks that the sim2real repo HEAD still matches `pipeline_commit`. If templates or the scorer template have changed mid-transfer (commit hash differs), the pipeline warns: "Pipeline artifacts have changed since Stage 1. Templates at `<old_commit>` may differ from current `<new_commit>`. Review changes before proceeding or restart from Stage 1." This prevents inconsistent behavior when templates are updated during an in-progress transfer.
- **CLI tools** (`tools/transfer_cli.py`) — A thin Python CLI that the LLM invokes for mechanical tasks. Key commands:
  - `transfer_cli.py extract <input_dir>` → parses `best_program_info.json`, extracts EVOLVE-BLOCK, outputs `workspace/algorithm_summary.json`. Exit 0 on success, exit 1 with JSON error on failure.
  - `transfer_cli.py validate-mapping <mapping_path>` → checks structural completeness of mapping artifact. Exit 0 if valid, exit 1 with missing fields listed.
  - `transfer_cli.py noise-characterize --cluster <id> --runs <N>` → runs baseline benchmarks N times, computes CV, writes `docs/transfer/noise_characterization.md`. Exit 0 on success.
  - `transfer_cli.py benchmark --baseline <config> --treatment <config> --workloads <dir>` → runs cluster benchmarks, outputs JSON with per-workload metrics. Exit 0 on success.
  - All commands output JSON to stdout on success and JSON error objects to stderr on failure. Exit codes: 0 = success, 1 = recoverable error, 2 = unrecoverable error.
- **Go test harness** (`tools/harness/`) — Uses inference-sim (submodule) to compile and run the original algorithm against test tuples for equivalence testing. Invoked as: `go test ./tools/harness/ -run TestEquivalence -tuples <path_to_tuples.json>`. Outputs per-tuple score vectors as JSON to stdout.

### Invocation

Interactive Claude Code session. The user points at an input directory:

```
> Transfer the algorithm from routing/ to llm-d-inference-scheduler
```

A top-level prompt template (`prompts/transfer.md`) guides Claude Code through all six stages, calling stage-specific prompts and CLI tools as needed. **Session timeout:** The top-level prompt tracks elapsed wall-clock time. If the session exceeds 4 hours, the pipeline logs a warning every 30 minutes: "Transfer session has been running for `<hours>` hours. Consider saving progress and resuming later." This is advisory — the pipeline does not auto-halt — but ensures the user is aware of resource consumption. Individual cluster operations have a hard 30-minute timeout.

### Generality

The architecture generalizes beyond routing:
- Each **transfer type** (routing, admission, priority) has its own mapping artifact, prompt templates, and plugin template
- The CLI tools, equivalence harness pattern, and pipeline stages are shared
- `routing/` is the first instance; future types would add e.g. `admission/` with their own input artifacts and mapping artifacts

---

## The Abstraction Gap

### Why Direct Copy Doesn't Work

The simulation and production systems share the same conceptual architecture (weighted scoring across instances) but differ in:

| Gap | Description | How Bridged | Validated By |
|---|---|---|---|
| **Data model** | Sim uses simple structs; production uses endpoints with async metrics | Scorer template type conversions | Test (compilation) |
| **Request representation** | Sim has typed fields; production carries serialized payloads requiring parsing | Generated scorer includes parsing logic | Test (request parsing unit test) |
| **Signal richness** | Some production signals are richer (e.g., per-request prefix match vs. aggregate) | Per-signal decision: upgrade (use richer signal) or degrade (aggregate to match sim granularity). Degrade is acceptable if Suite A fidelity passes; if it fails, the signal's fidelity rating drops and may trigger scope re-evaluation. | Validate Suite A (upgrade signal tests) |
| **Metric staleness** | Sim uses synchronous snapshots; production metrics are async with variable staleness. **v1 note:** All v1 signals come from the approximate (router-side) scorer, which has zero collection latency — staleness is 0 for these signals. The precise (ZMQ-based) scorer introduces real staleness but is not yet modeled in inference-sim. | Synthetic staleness injection in tests (infrastructure for future precise-scorer transfers) | Validate Suite B (trivially passes for v1 approximate-scorer signals) |
| **Concurrency** | Sim evaluates sequentially; production processes concurrently | Sequential-but-rapid test pattern | Validate Suite C |
| **Weight mechanism** | Sim uses normalized weights summing to 1.0; production may use different convention | Documented in mapping artifact | Test (unit tests) |

### Translation Pattern: Composite Plugin

The discovered adaptive logic maps to a **new plugin** that reads endpoint metrics directly and applies the sim-discovered conditional weighting logic. The plugin:

- Reads per-endpoint signals through the target system's standard metric access interface — it does not wrap or delegate to other plugin instances
- Applies conditional weight adjustments based on request characteristics — these conditions and weights are the core translation target from the evolved code block
- Registers as the sole scorer (or replaces the scorers whose logic it internalizes) to avoid double-counting signals
- Implements the full plugin interface following the target system's conventions (as documented in the scorer template artifact)
- Is disableable via config toggle without code removal

**Double-counting prevention:** The signal coverage report's `scorer_overlap` field lists which existing scorers share signals with the new plugin and recommends `disable` or `keep` for each. Overlap detection works as follows:
- During Stage 2, the LLM reads each existing scorer's source code in the target submodule and lists the signals (metric fields) it accesses.
- For each signal in the new plugin's `mappings`, if an existing scorer accesses the same metric field, it is flagged as overlapping.
- The recommendation is `disable` if the existing scorer's entire logic is subsumed by the new plugin, `keep` if it provides independent functionality.
- **Overlap verification:** The LLM must list all scorers discovered in the target submodule (by scanning for interface implementations) and explicitly mark each as `overlapping` or `independent` in the `scorer_overlap` array. Any scorer not listed is flagged as a warning: "Scorer `<name>` not assessed for overlap." This ensures completeness of the overlap analysis even though the LLM's semantic judgment of overlap/independence is not automatically verifiable. **Automated partial check:** The generated unit tests include a "double-counting smoke test" that verifies: with the new plugin enabled and overlapping scorers disabled, the total weight assigned to any single metric field is <= 1.0. This catches the most common double-counting scenario (two scorers both reading the same field with positive weights) even though it cannot catch all semantic overlaps.
- Unit tests must verify overlapping scorers with `disable` recommendation have weight zero in the generated config.
- The generated config's feature-flag mechanism (see "No-op default and atomic rollback" below) ensures that disabled scorers are automatically re-enabled when the plugin is toggled off, preventing silent degradation.

**No-op default and atomic rollback:** When the new plugin is disabled, the target system must behave identically to its pre-transfer state. This requires that the plugin toggle and scorer re-enablement are atomic — implemented as follows:
- The generated config uses a single feature flag (e.g., `enable_<algorithm_name>_plugin: true/false`).
- When the flag is `true`: the new plugin is active, and overlapping scorers listed in `scorer_overlap` with recommendation `disable` are disabled.
- When the flag is `false`: the new plugin is inactive, and all previously-disabled overlapping scorers are re-enabled to their pre-transfer configuration.
- The config template must encode both states (enabled and disabled scorer lists) so that toggling the flag is the only operation required. No manual scorer re-enablement is needed.
- Unit tests must verify both directions: (a) flag=true → new plugin active, overlapping scorers disabled; (b) flag=false → new plugin inactive, overlapping scorers restored to pre-transfer state.
- **Deployment assumption:** The target system supports config reload without restart (hot config reload). The toggle takes effect on the next scheduling decision after the config is loaded — in-flight requests that have already read scorer weights are not affected. This is consistent with the target system's existing config-reload behavior. The scorer template artifact must explicitly document whether hot config reload is supported (field: `hot_reload_supported: true|false`). Stage 3 reads this field; if `false`, the generated config includes a comment noting restart is required, and the PR description includes a rollback procedure with a restart step. If the field is missing from the scorer template, Stage 3 halts and asks the user to confirm.

This pattern is:
- **Additive at the code level** — introduces a new plugin file without modifying existing source
- **Requires configuration changes** — scoring config updated to register the new plugin and disable overlapping ones
- **Reviewable** — the adaptive logic is isolated and clearly attributable to simulation discovery
- **Testable** — can unit test independently and verify semantic equivalence against simulation

---

## Validation Pipeline

### Stage 2→3 Translation Validation

Stage 2 (Translate) and Stage 3 (Generate) are both LLM-powered. To catch translation errors before they compound:

1. **Mapped spec review (end of Stage 2):** The LLM verifies the spec against the original code block: "Does this pseudocode preserve all conditions and weight assignments?" Self-check, not a guarantee — catches obvious omissions.
2. **Branch-count consistency check:** The mapped spec must reference the same number of conditional branches as the original code block.
3. **Signal-set consistency check (bidirectional):** Signals in the mapped spec must exactly match `signals_used` from Stage 1. Extra signals → LLM hallucinated a mapping. Missing signals → translation dropped a dependency.

### Stage 4: Test Gate

The minimum bar for proceeding to validation. Local only:

1. Run unit tests for the new plugin package only — isolate new code failures
2. Run the full repo build, test, and lint suite — catch integration breakage. The lint suite is expected to enforce plugin conventions (package naming, interface compliance, config registration). If the target repo's lint configuration does not cover plugin conventions, the scorer template artifact must document the conventions that require manual review during PR.
3. Generated unit tests must include: (a) request parsing test, (b) disabled-scorer no-op test, (c) overlap assertion (disabled scorers have weight zero in config)
4. On failure: the LLM reads the error, fixes the code, and retries interactively, subject to the following limits:
   - **Retry limit:** Maximum 3 retries per distinct error class, maximum 5 total retries across all classes. Error classes are defined by a fixed taxonomy (the LLM does not invent new classes):
     - **compilation** — `go build` fails (syntax errors, type errors, missing imports, undefined symbols). Classified by: non-zero exit from `go build`.
     - **test-unit** — Unit tests for the new plugin package fail. Classified by: failure in `go test ./<plugin_package>/...` only.
     - **test-integration** — Full repo test suite fails on tests outside the new plugin package. Classified by: failure in `go test ./...` on a test file not in the plugin package.
     - **lint** — Linter violations. Classified by: non-zero exit from the repo's lint command (e.g., `golangci-lint`).
     Errors that don't fit these classes (e.g., infrastructure failures, OOM) are not retried — Stage 4 halts immediately and reports the error for user intervention. **Unrecoverable error state:** The target submodule branch is left as-is (not rolled back) so the user can inspect the state. The `workspace/generated_files.json` manifest is updated with `halted_at: "<timestamp>"`, `halt_reason: "<error_class>: <message>"`, and `halt_commit: "<current_branch_HEAD>"`. On resume after the user fixes the infrastructure issue, the pipeline re-enters Stage 4 from the beginning (re-runs all tests) rather than attempting to continue mid-retry, since the branch state may have been modified externally. The LLM distinguishes infrastructure failures from code errors by: (a) non-zero exit codes from `go` toolchain with messages containing "cannot connect", "out of memory", "disk full", "permission denied" → infrastructure; (b) compilation/test/lint errors with specific file:line references → code error.
   - **Loop detection (takes precedence over per-class limit):** If two consecutive fix attempts produce the same error (identical error message or same failing test with same assertion), halt and escalate immediately — even if the per-class retry limit has not been reached. Rationale: two identical consecutive errors indicate the LLM's fix strategy is not working; additional retries of the same approach waste resources. The per-class limit (3 retries) applies to *distinct* errors within the same class (e.g., three different compilation errors), while loop detection catches *identical* errors recurring back-to-back. In summary: loop detection is an early-exit rule that fires first; the per-class and total limits are upper bounds that apply when errors vary between attempts.
   - **Escalation:** When retry limits are reached (3 per class or 5 total), Stage 4 halts. The LLM reports: (a) the original error, (b) each fix attempted and its result, (c) a diagnosis of whether the failure is likely a translation error (→ return to Stage 3) or a scope issue (→ return to Stage 1). The user decides whether to retry with guidance, return to an earlier stage, or abort the transfer.
   - **Unrecoverable errors:** Errors indicating scope violations (e.g., algorithm requires a type or API not available in the target system) are not retried — Stage 4 halts immediately and recommends returning to Stage 1 for re-scoping.

### Stage 5: Validate

#### Equivalence Testing (Suites A/B/C)

All suites run locally using a Go test harness built against inference-sim.

**How the harness executes the original algorithm:** The original algorithm in `best_program.py` is Python code that runs within inference-sim's Go simulation environment. The Go test harness does not execute Python directly. Instead:
1. The harness instantiates inference-sim's routing evaluator (Go code) and injects the evolved code block by calling inference-sim's `LoadEvolvedBlock(code string)` API, which compiles the Python-like expressions into inference-sim's internal representation (the same mechanism used during simulation). **Prerequisite:** This API must exist in the inference-sim submodule. If it does not exist at the pinned commit, it must be implemented before Stage 5 can run. The pipeline's missing-prerequisite check (at Stage 1) should verify this API is available by checking for the symbol in the inference-sim source. If absent, halt with: "inference-sim `LoadEvolvedBlock` API not found. This API is required for equivalence testing (Stage 5). Implement it in inference-sim before proceeding."
2. The harness feeds test tuples as `EndpointState` structs to the evaluator and captures the resulting score vectors.
3. The translated production plugin is tested separately via its own Go unit test framework, reading the same tuples from a shared JSON file (`workspace/test_tuples.json`).
4. The harness compares score vectors from both sides. The plugin's test must expose scores via a standardized interface: `func ScoreEndpoints(tuples []TestTuple) []ScoreVector`, implemented in the plugin's test file following the scorer template conventions.

Suites run in order: A → B → C. Dependencies are linear: Suite B depends on Suite A passing (staleness testing is meaningless if baseline equivalence fails), and Suite C depends on Suite B passing (concurrency testing is meaningless if the algorithm is staleness-sensitive beyond thresholds). If a suite fails, all subsequent suites are skipped by default. **Diagnostic mode:** The user may request `--diagnostic` to run all suites regardless of earlier failures. In diagnostic mode, Suite B/C results after a Suite A failure are annotated `diagnostic_only: true` in `validation_results.json` and do not count toward the overall verdict. This provides additional debugging information (e.g., whether the algorithm is also staleness-sensitive) without weakening the dependency rationale.

**Suite A — Baseline equivalence (controlled):**
- Generate test tuples systematically: for each input dimension the algorithm conditions on (e.g., input size, queue depth, cache state), sample at min, median, max, and each threshold value +/- epsilon (where epsilon is 1 for integer dimensions, or 1% of the threshold value for floating-point dimensions, minimum 1e-6). For multi-dimensional thresholds, epsilon is computed per-dimension independently. Cartesian product across dimensions, capped at 200 tuples. When the cap is reached, prioritize by: (1) threshold boundary tuples first (values at threshold +/- epsilon), (2) extreme values (min, max), (3) median values last. The ordering within each priority tier is lexicographic by dimension index. The LLM extracts threshold values from the algorithm's conditional expressions (e.g., `if load > 4.5` → threshold = 4.5). **Dynamic thresholds:** If a threshold is a dynamic expression (e.g., `if load > baseline * 1.5`), the LLM evaluates it at the min, median, and max values of the dependent variable(s) to produce concrete threshold values for tuple generation. **Nested conditionals:** For interdependent conditions (e.g., `if load > X && cache > Y`), generate tuples at the boundary of each condition independently, plus tuples at the intersection (both conditions at boundary simultaneously).
- Run each tuple through both the original algorithm (via Go harness + inference-sim) and the translated plugin.
- **Two pass criteria (both must pass):**
  1. **Numeric fidelity:** Per tuple, `abs(sim_score - prod_score) <= 1e-6` (absolute) OR `abs(sim_score - prod_score) <= 0.01 * max(abs(sim_score), abs(prod_score))` (1% relative, computed against the larger magnitude). The OR semantics mean the absolute threshold governs near-zero scores while the relative threshold governs larger scores — this is standard practice for floating-point comparison.
  2. **Rank correlation:** Kendall-tau > 0.8 (configurable, provisional until calibrated)
- For signals marked as `"Upgrade"` in the coverage report, include additional tuples (up to 5 per Upgrade signal) sampling the **divergence range** — the range of values where the production signal provides information that the simulation signal does not. For example, if the sim signal is an aggregate cache hit rate (0–1) but the production signal is a per-request prefix match ratio (also 0–1 but with finer granularity), the divergence range is the set of per-request values that map to the same aggregate value. The mapping artifact should document the divergence range for each Upgrade signal. **Risk note:** A richer production signal could cause the algorithm to encounter input patterns it was never trained on (e.g., per-request prefix match = 0.99 when the aggregate is 0.5). Suite A Upgrade tuples specifically test these divergence scenarios; if Suite A fails on Upgrade tuples, consider whether the signal should be downgraded to Medium fidelity with an aggregation wrapper.

**Suite B — Staleness sensitivity:**
- **v1 status:** For v1 transfers, all signals come from the approximate (router-side) scorer, which computes scores instantaneously from router-local state. Every signal has `staleness_window_ms: 0`, so staleness injection produces zero offset and Suite B passes trivially (it degenerates to a re-run of Suite A). Suite B infrastructure is retained because future transfers targeting the precise (ZMQ-based) scorer will introduce real async staleness, at which point this suite becomes the primary safety net. The mapping artifact should document which scorer each signal originates from.
- Re-run Suite A tuples with synthetic staleness injected into production signal reads. Staleness injection mechanism:
  - The mapping artifact defines `staleness_window_ms` per signal and groups signals by `collection_source` (e.g., "metrics-collector", "request-parser").
  - For each repetition, generate a random delay per source group (uniform within the group's max staleness window). All signals in the same group receive the same delay, modeling correlated collection latency.
  - **Staleness model:** Tuples are ordered by index (0..N-1). A delay of `delay_ms` is converted to a tuple offset: `offset = floor(delay_ms / staleness_step_ms)` where `staleness_step_ms` is defined in the mapping artifact (default: 100ms, representing the assumed inter-decision interval). The stale value for signal S at tuple index I is `S[max(0, I - offset)]` — i.e., the signal's value from `offset` tuples earlier. For the first `offset` tuples (where `I - offset < 0`), the signal uses its own value (no staleness effect). If `offset >= N` (delay exceeds sequence length), the signal is held at its value from tuple 0 for all tuples. **Warning:** This makes the signal effectively constant, which could artificially inflate rank correlation (a constant signal doesn't affect ranking). When this occurs, the pipeline logs: "Signal `<name>` is constant under staleness offset `<offset>` (exceeds sequence length `<N>`). Rank correlation may be inflated." Suite B results should note which signals, if any, were held constant, and the user should consider whether the staleness window is realistic.
  - **Seed strategy:** Each of the 3 repetitions uses a deterministic seed: `seed = hash(algorithm_name + repetition_index)` where `hash` is SHA-256 truncated to 64-bit integer. This ensures reproducibility across runs while varying staleness patterns between repetitions.
  - **Source group fallback:** If the mapping artifact does not define `collection_source` groups, fall back to independent per-signal staleness where each signal gets its own random delay drawn from its `staleness_window_ms`. If `staleness_window_ms` is also undefined for a signal, use 500ms (chosen as a conservative upper bound based on typical Kubernetes metrics-server collection intervals of 100–500ms; this value should be refined in the mapping artifact with production observability data before the first transfer).
  - **Estimated parameter validation:** Before running Suite B, the pipeline checks whether any staleness parameters are estimated (not measured from production data). Specifically: (a) if any signal's `staleness_window_ms` is missing or marked `(estimated)` in the mapping artifact, or (b) if `staleness_step_ms` uses the default 100ms rather than a measured value. If estimated parameters are found, the pipeline logs a prominent warning: "Suite B running with estimated staleness parameters: `<list of signals with estimated windows>`. Results may not reflect production staleness patterns. Refine these values with production observability data for higher confidence." Suite B results with estimated parameters are annotated in `validation_results.json` with `staleness_params_estimated: true` and listed in the PR description. The pipeline does not halt (estimated parameters are acceptable for early transfers) but the annotation ensures results are interpreted with appropriate caution.
  - **Decision interval justification:** The default `staleness_step_ms` of 100ms assumes decisions are spaced ~100ms apart. This is appropriate for routing decisions at moderate throughput (10 decisions/sec). For higher-throughput scenarios, this value should be reduced; for lower throughput, increased. The mapping artifact should document the expected decision rate and derive `staleness_step_ms` accordingly. If production decisions are bursty rather than uniformly spaced, the tuple-offset model provides a conservative approximation (uniform spacing overestimates the staleness effect during bursts).
- 3 repetitions with different staleness seeds.
- **Pass criteria (both must pass):**
  1. **Rank stability:** Kendall-tau > 0.7 across all repetitions
  2. **Threshold-crossing stability:** <20% of threshold-boundary tuples change classification under staleness

**Suite C — Concurrency stress:**
- C1 (parallel safety): 20 concurrent routing decisions against the same endpoint snapshot. **Pass criteria:** (a) no panics or runtime errors, (b) no NaN or Inf in any score vector, (c) deterministic results — all 20 concurrent decisions must produce identical score vectors (bitwise equal, since they read the same snapshot). If any score vectors differ, report the differing indices and values. Deterministic here means identical output given identical input — not "close enough."
- C2 (pile-on dynamics): 20 sequential-but-rapid requests with state updates between decisions. **Pass criteria:** No endpoint receives more than 2× its fair share of requests, where fair share = `total_requests / num_endpoints`. The 2× threshold is a conservative bound — it allows some concentration (which adaptive algorithms intentionally produce) while catching pathological pile-on where a single endpoint receives all traffic. If the algorithm intentionally concentrates traffic (e.g., session affinity), the user may adjust this threshold with justification.

#### Cluster Benchmarks

**Operational model:** The user triggers cluster deployments using existing benchmark tooling (e.g., `llm-d-benchmark` scripts). The LLM generates the benchmark configs (baseline and treatment) and invokes `tools/transfer_cli.py benchmark` to collect and parse results. The LLM does not directly manage cluster credentials or deployments — the user's environment must have cluster access pre-configured.

**Cluster access pre-check:** Before generating benchmark configs, the pipeline runs a lightweight connectivity check (`tools/transfer_cli.py benchmark --preflight --cluster <id>`) that verifies: (a) cluster credentials are valid, (b) the target namespace exists, (c) the benchmark tooling is available. If the pre-check fails, the pipeline halts immediately with a diagnostic before any benchmark setup work begins. Timeout for cluster operations: 30 minutes per benchmark run (configurable). If a benchmark run exceeds the timeout, the pipeline halts and reports the timeout with the last known status.

Two deployments against the same workloads (reproduced from the input workload YAMLs):

1. **Baseline run** — stock scorer config, new plugin disabled
2. **Treatment run** — new plugin enabled, overlapping scorers disabled

Metrics collected: latency (TTFT + E2E, mean + P95) and throughput.

**Go/no-go criteria (all thresholds provisional, configurable):**

The **effective improvement threshold** (`T_eff`) is defined as: `T_eff = max(5%, 2 × CV)` where CV is the coefficient of variation from noise characterization. This resolves the relationship between the absolute floor (5%) and the noise-relative requirement: the 5% floor applies when noise is low; the noise-relative rule dominates when noise is high.

- Improvement > `T_eff` on at least one workload
- No regression > 2% on any workload
- P95 latency regression < 2% on any workload
- **Mechanism check** (evaluated in order, using `T_eff` as the threshold throughout):

  **Matched workload definition:** The matched workload is the workload where the algorithm was originally discovered and optimized. It is identified as follows: Stage 1 reads `best_program_info.json` and extracts the `per_workload_results` array. The workload with the highest `improvement_pct` is designated the matched workload and recorded in `algorithm_summary.json` as `matched_workload: "<workload_name>"`. This field propagates through all downstream artifacts. If multiple workloads are tied for highest improvement (within 1%), Stage 1 halts and asks the user to designate the matched workload. The `is_matched` field in the cluster benchmark `per_workload` results (in `validation_results.json`) is set to `true` for this workload.

  0. **Precondition (validated early):** The matched workload (as identified by the `matched_workload` field in `algorithm_summary.json`) must be included in the benchmark set. This precondition is checked twice: (a) **early check** — when generating benchmark configs (before deploying to the cluster), the pipeline verifies the matched workload is in the config and halts with a clear error if not, preventing wasted cluster time; (b) **runtime check** — after benchmark results are collected, verify the matched workload appears in `per_workload_results`. If missing at either check → **FAIL** with diagnostic: "Matched workload `<name>` not in benchmark set — cannot verify mechanism. Add it to the benchmark config and re-run."
  1. Matched workload must show improvement >= `T_eff`. If not → **FAIL**.
  2. If all workloads improve by similar amounts (max - min < `T_eff / 2`), benefit is likely from overhead reduction, not mechanism → **INCONCLUSIVE**, requires human review. See "INCONCLUSIVE handling" below.
  3. **Mechanism specificity check:** Matched workload must rank first or tied-first in improvement, AND exceed mean improvement by at least `T_eff / 2`. If not → **SOFT FAIL** (see below).
     - **Rationale:** This criterion tests whether the algorithm's benefit is *specific* to the workload it was optimized for, as expected if the evolved mechanism is genuinely transferring. If all workloads benefit equally, the improvement may be from overhead reduction (caught by criterion 2). If unrelated workloads benefit more, the mechanism may not be what we think it is.
     - **Tie semantics:** "Tied-first" means the matched workload's improvement is within `T_eff / 4` of the highest-improving workload. When comparing improvements, use the raw improvement percentages (not rounded). If all workloads are within `T_eff / 4` of each other, criterion 2 (uniform improvement) should have already triggered INCONCLUSIVE — if it didn't (because the spread exceeds `T_eff / 2`), this indicates a borderline case that warrants human review.
     - **SOFT FAIL handling:** Unlike a hard FAIL, a SOFT FAIL on criterion 3 triggers user review rather than automatic rejection. The pipeline presents: (a) the matched workload's rank and improvement, (b) the top-improving workload's improvement, (c) the mean improvement, (d) the margin by which the criterion was missed. The user may: **Override to PASS** (algorithm has genuinely generalized — record justification), **Accept FAIL** (algorithm's mechanism didn't transfer as expected), or **Re-run with different matched workload** (if the user believes the matched workload was mis-identified). Overrides are recorded in the calibration log with `mechanism_override: true`.
     - *Known limitation: this criterion may reject algorithms that generalize well. The SOFT FAIL mechanism mitigates false positives by requiring human judgment rather than automatic rejection. After calibration (3+ transfers), the false-positive rate of this criterion should be reviewed.*
  4. If all above pass → **PASS**.

**INCONCLUSIVE handling:** When the mechanism check returns INCONCLUSIVE, the pipeline halts and presents the user with the benchmark data and the reason for the INCONCLUSIVE verdict. The user has three options:
- **Override to PASS** — proceed to Stage 6 with the override recorded in `validation_results.json` (`mechanism_check_override: "PASS"`, `override_reason: "<user-provided justification>"`). The PR description will note that the mechanism check was overridden.
- **Override to FAIL** — abort the transfer. Recorded in the calibration log as a mechanism check failure.
- **Re-run benchmarks** — add more workloads or adjust config and re-run the cluster benchmark phase. The pipeline returns to the start of the cluster benchmark phase (not the start of Stage 5).

Stage 6 accepts `overall_verdict` values of PASS or INCONCLUSIVE-with-override. It rejects FAIL and bare INCONCLUSIVE (no override). If `overall_verdict` is INCONCLUSIVE without a recorded override, Stage 6 halts with: "Cannot create PR: mechanism check is INCONCLUSIVE. Re-run Stage 5 cluster benchmarks or override the verdict."

**Improvement magnitude comparison (informational, not a gate):**

| Workload | Sim Predicted Improvement | Cluster Observed Improvement | Ratio (Observed/Predicted) |
|---|---|---|---|

- Ratio < 0.3 → "significant attenuation" flag
- Ratio > 2.0 → "unexpected amplification" flag
- Tracked per-workload-type in calibration log across transfers.

**Noise characterization prerequisite:**
Before the first cluster benchmark on a target cluster, run the baseline 5 times with identical config. **Bootstrapping:** The baseline config is the target system's stock scorer configuration (new plugin disabled). If the target system has never been deployed to this cluster, deploy the stock config first and verify it runs successfully before starting noise characterization. The `noise-characterize` CLI command handles deployment if given `--deploy-baseline` flag. Compute CV independently for each metric: latency-TTFT-mean, latency-TTFT-P95, latency-E2E-mean, latency-E2E-P95, and throughput. Each metric gets its own `T_eff`: `T_eff(metric) = max(5%, 2 × CV(metric))`. The `2×` multiplier provides a ~95% confidence that observed differences exceed noise (approximating 2-sigma for normally distributed measurements). The go/no-go criteria apply per-metric using that metric's `T_eff`.

**CV upper bound:** If any metric has CV > 15%, noise characterization fails — the cluster is too noisy for reliable benchmarking. The pipeline halts with: "Cluster noise too high: `<metric>` CV=`<value>`%. Investigate cluster stability before benchmarking." This prevents `T_eff` from exceeding 30%, which would make the threshold meaninglessly loose.

**CV = 0 handling:** If CV = 0 for a metric (identical values across all runs), `T_eff` defaults to the 5% floor. This is expected for metrics like throughput on deterministic workloads.

Store results in `docs/transfer/noise_characterization.md` with cluster ID, date, per-metric CV values, and config hash. Re-characterize after cluster config changes (detected by config hash mismatch) or after 30 days. **Config change detection:** The noise characterization file stores a hash of the baseline config and cluster node configuration (node count, GPU type, memory). On each Stage 5 entry, the pipeline recomputes these hashes and compares. If either hash differs, the pipeline treats the characterization as stale regardless of age.

**Integration with pipeline flow:** At the start of Stage 5's cluster benchmark phase, the pipeline checks for a valid noise characterization file:
1. Read `docs/transfer/noise_characterization.md` and verify: (a) it exists, (b) the cluster ID matches the target cluster, (c) the date is within 30 days, (d) the config hash matches the current baseline config.
2. If valid → extract CV values and compute `T_eff`.
3. If missing or stale → halt and prompt the user: "Noise characterization required. Run `tools/transfer_cli.py noise-characterize --cluster <id> --runs 5` to generate it." The pipeline does not proceed to cluster benchmarks without valid noise data.

### Stage 6: PR Creation

Stage 6 creates pull requests against the upstream target repos. Specification:

- **Branch:** Uses the `transfer/<algorithm_name>` branch created in Stage 3 (with Stage 4 fix commits).
- **Target branch:** The default branch of the target submodule (typically `main`). The pipeline reads this from the submodule's git config.
- **PR title:** `[sim2real] Add <algorithm_name> adaptive routing plugin`
- **PR description:** Generated from a template that includes: (a) one-line summary of the algorithm's behavior, (b) simulation improvement percentages (from `algorithm_summary.json`), (c) validation summary — Suite A/B/C pass/fail and Kendall-tau values, cluster benchmark verdict and per-workload improvements, (d) mechanism check result (including override reason if INCONCLUSIVE was overridden), (e) link to the sim2real design document, (f) rollback instructions (toggle the feature flag to disable).
- **Authentication:** Uses the user's existing `gh` CLI authentication (the pipeline runs `gh auth status` as a pre-check and halts if not authenticated).
- **Error handling:** If PR creation fails (e.g., branch already has an open PR, authentication failure, network error), the pipeline reports the error and the user resolves it interactively. The pipeline does not retry PR creation automatically.
- **Calibration log:** After successful PR creation, the LLM appends an entry to `docs/transfer/calibration_log.md` (see Calibration Log section below).
- **Output:** The PR URL is displayed to the user and recorded in `workspace/pr_result.json` (`{pr_url, created_at, target_repo, target_branch}`).

---

## Signal Mapping Summary

The canonical mapping lives in `docs/transfer/blis_to_llmd_mapping.md`. This table is a high-level orientation only — do not duplicate fidelity ratings here.

**Scorer types in llm-d:** The production system has two scorer implementations: (1) an **approximate scorer** that runs entirely router-side and returns scores instantaneously, and (2) a **precise scorer** that communicates with model servers via ZMQ and reflects actual engine state. For v1 transfers, all signals listed below come from the approximate scorer — they are computed locally from router-visible state with zero collection latency. The precise scorer's ZMQ-based signals are not yet modeled in inference-sim (BLIS) and are out of scope for v1. This distinction has implications for Suite B (see below).

**Signals used by the current routing algorithm** (from `best_program.py` EVOLVE-BLOCK):

| Signal | Sim Field | How Used in Algorithm | Correspondence |
|---|---|---|---|
| **Effective load** | `QueueDepth + BatchSize + InFlightRequests` | Cubic load penalty when delta > 0.2; hard penalty at load > 4.5 and > 7.0 | Direct — production tracks similar load metrics |
| **KV utilization** | `KVUtilization` (float64, 0-1) | Memory pressure penalty when > 0.82 | Direct — both express as fraction |
| **Cache hit rate** | `CacheHitRate` (float64, 0-1) | Session affinity boost when > 0.35 and request has SessionID | Target may be richer (per-request prefix match vs. aggregate) |
| **Session ID** | `req.SessionID` (string) | Presence check gates cache affinity logic | Convention-dependent — how session identity is conveyed |
| **Scorer weights** | `ws.weights[]` (normalized) | Base scorer pipeline scores combined before penalties | Weight mechanism may differ in production |

**Fidelity Enum**

- **High** — Behavioral equivalence with <5% divergence in test comparisons
- **Medium** — Same concept but different granularity or access pattern
- **Low** — Requires approximation or proxy
- **Upgrade** — Target signal is strictly richer than simulation equivalent

Per-signal fidelity assignments are maintained exclusively in the mapping artifact.

---

## Supporting Artifacts & Repository Structure

### Repository Layout

```
sim2real/
├── inference-sim/              # submodule — simulation environment
├── llm-d-inference-scheduler/  # submodule — target: scorer plugins
├── llm-d-benchmark/            # submodule — target: benchmark configs
├── routing/                    # input artifacts (first transfer type)
│   ├── best_program.py
│   ├── best_program_info.json
│   └── workload_v2_*.yaml
├── prompts/                    # prompt templates (the primary pipeline artifact)
│   ├── transfer.md             # top-level orchestration prompt
│   ├── extract.md              # Stage 1
│   ├── translate.md            # Stage 2
│   ├── generate.md             # Stage 3
│   ├── test.md                 # Stage 4
│   ├── validate.md             # Stage 5
│   └── pr.md                   # Stage 6
├── tools/                      # CLI utilities invoked by the LLM
│   ├── harness/                # Go equivalence test harness (uses inference-sim)
│   └── transfer_cli.py         # parsing, metrics collection, workload config generation
├── docs/
│   ├── plans/                  # design docs, macro plans
│   └── transfer/               # mapping artifacts, calibration log, noise data
│       ├── blis_to_llmd_mapping.md
│       ├── scorer_template.go.md
│       ├── calibration_log.md
│       └── noise_characterization.md
└── workspace/                  # per-transfer working directory (gitignored)
```

### Supporting Artifacts (3)

| Artifact | Location | Purpose | Owner | Status | Blocking Gate |
|---|---|---|---|---|---|
| **Mapping artifact** | `docs/transfer/blis_to_llmd_mapping.md` | Signal/interface mapping with concrete types, metric paths, staleness windows, fidelity ratings. Pins target system commit. Single source of truth for implementation-level details. | Pipeline author (kalantar) | TODO — create before first transfer | Blocks Stage 1 (scope validation reads fidelity ratings) and Stage 2 (signal mapping) |
| **Scorer template** | `docs/transfer/scorer_template.go.md` | Annotated example of a well-structured target-system plugin showing conventions, test structure, config registration. Must compile against target submodule HEAD. **Required sections:** (1) package declaration and imports, (2) scorer interface implementation (with all required methods), (3) metric access pattern (how to read endpoint signals via the standard interface), (4) config registration (feature flag, weight declaration), (5) plugin initialization lifecycle (construction, startup, shutdown hooks if any), (6) unit test structure (request parsing, no-op, overlap assertion), (7) `ScoreEndpoints` equivalence test function signature, (8) deployment assumption: whether hot config reload is supported or restart is required. | Pipeline author (kalantar) | TODO — create before first transfer | Blocks Stage 3 (code generation uses template as reference) |
| **Prompt templates** | `prompts/` | LLM instructions for each pipeline stage. Includes tuple generation strategy, diagnostic guidance, threshold configs. | Pipeline author (kalantar) | TODO — create before first transfer | Blocks all stages (each stage reads its prompt template) |

All three are prerequisites — the pipeline cannot execute without them.

**Prerequisite creation timeline:** All three artifacts must be created and validated before the first transfer attempt. The recommended order is: (1) mapping artifact first (requires target-system knowledge, estimated 2–4 hours), (2) scorer template second (requires mapping artifact for signal references, estimated 1–2 hours), (3) prompt templates last (reference both other artifacts, estimated 4–8 hours including dry-run validation).

**Missing prerequisite behavior:** At the start of Stage 1, the pipeline checks for the existence of all three artifacts. If any artifact is missing, the pipeline halts immediately with: "Missing prerequisite: `<artifact_name>` at `<expected_path>`. Create this artifact before running the pipeline. See 'Creation procedure' below." The pipeline does not attempt partial execution.

**Creation procedure for each artifact:**

1. **Mapping artifact** — Created by a human who understands both codebases. Procedure:
   1. Check out target submodule at a known commit. Record commit hash in the artifact header.
   2. For each signal in the Signal Mapping Summary above, document: concrete production type, metric access path, staleness window, and fidelity rating. Staleness windows should be measured from production observability data (e.g., Prometheus metric scrape intervals, metrics-server collection frequency). If production data is unavailable, document the value as an estimate with `(estimated)` suffix and create a follow-up task to measure and update after the first transfer. Suite B results with estimated windows should be interpreted cautiously.
   3. Validation: the artifact must list every signal from the Signal Mapping Summary with a non-empty production type, access path, and fidelity rating. Run `tools/transfer_cli.py validate-mapping <path>` to check structural completeness.
   4. Review: a second person with target-system knowledge reviews for correctness.

2. **Scorer template** — Created by extracting and annotating an existing well-structured plugin from the target submodule. Procedure:
   1. Identify a representative plugin in `llm-d-inference-scheduler` that follows current conventions.
   2. Copy and annotate with `// TEMPLATE:` comments marking: package declaration, interface implementation, config registration, test structure, and metric access patterns.
   3. Validation: the annotated template must compile against the current target submodule HEAD (`go build ./...` in the submodule). Must include at least one `// TEMPLATE:` annotation per section (package, interface, config, test).
   4. Review: target-system maintainer confirms conventions are current.

3. **Prompt templates** — Created iteratively. Procedure:
   1. Draft each stage prompt using this design document as the specification.
   2. Validation: each prompt must reference the stage's defined inputs and outputs (per the inter-stage contract below). Run a dry-run with a synthetic algorithm to verify the LLM can follow the instructions end-to-end.
   3. Review: run a single transfer with a known-good algorithm and verify each stage produces the expected output format.

### Workspace

`workspace/` is the per-transfer working directory (gitignored). Holds intermediate artifacts: algorithm summary, signal coverage report, mapped spec, generated configs. Persists across stages for continuity within a transfer session.

**Stage ownership:** Each stage writes only its own output files (per the inter-stage artifact contracts) and reads from previous stages' outputs. No stage modifies another stage's artifacts.

| Stage | Writes | Reads |
|---|---|---|
| 1 (Extract) | `workspace/algorithm_summary.json` | Input artifacts only |
| 2 (Translate) | `workspace/signal_coverage.json` | `workspace/algorithm_summary.json` |
| 3 (Generate) | Files in target submodule (on a branch) | `workspace/signal_coverage.json` |
| 4 (Test) | Modifies generated files in target submodule | Generated files |
| 5 (Validate) | `workspace/validation_results.json` | Generated files + inference-sim |
| 6 (PR) | None (pushes existing branch) | `workspace/validation_results.json` |

**Crash recovery:** Each workspace JSON artifact includes a `stage` field and `completed_at` timestamp. On session restart, the LLM checks which workspace artifacts exist and are complete:
- If `algorithm_summary.json` exists with a valid `completed_at` → resume from Stage 2.
- If `signal_coverage.json` exists → resume from Stage 3.
- If a branch exists in the target submodule with generated files: check `workspace/generated_files.json` for a `test_passed_at` timestamp and `final_commit` SHA. If both are present and the branch HEAD matches `final_commit` → resume from Stage 5. If the branch exists but `test_passed_at` is absent (crash during Stage 4 before tests passed), resume from Stage 4 — the LLM re-runs the test suite on the existing branch to determine current state before attempting fixes.
- If `validation_results.json` exists → resume from Stage 6.
- Otherwise → restart from Stage 1 (workspace artifacts are cheap to regenerate).

The user can also force restart from any stage by deleting the corresponding workspace artifact.

**Crash recovery limitations:** The `completed_at` timestamp provides a basic completeness signal, but does not guarantee content integrity — a partially-written JSON file could have a timestamp but corrupted content (e.g., if the session crashed mid-write). On resume, the LLM should validate that each artifact parses as valid JSON and contains all required fields (per the inter-stage contracts) before treating it as complete. If parsing fails, the artifact is treated as absent and the stage is re-run. Concurrent sessions on the same workspace are not supported — the pipeline assumes exclusive access to the workspace directory.

### Generality Points

- Input artifacts live in per-type directories (`routing/`, future `admission/`, etc.)
- Mapping artifacts and scorer templates are per-transfer-type
- Prompt templates can be shared or specialized per type
- The Go test harness pattern generalizes (any sim policy can be compiled and scored against test tuples)

---

## Falsification Protocol

**Early warning indicators (tracked across transfers):**
- Suite A Kendall-tau trending downward (even if still above threshold)
- Suite B staleness sensitivity increasing
- Stage 4 test failure rate increasing
- If any trend appears across 2 consecutive transfers, flag for human review.

**Full falsification:** If >=3 independent algorithms — each with High/Medium-fidelity signals, each passing equivalence tests — all fail cluster benchmarks, the abstraction gap is too wide for current simulation fidelity. Definitions:
- **Independent:** Algorithms from separate experiment runs that do not share the same evolved code lineage (i.e., not mutations of the same parent). Operationally determined by: different `experiment_id` values in `best_program_info.json` (each experiment run produces a unique ID). The `experiment_id` must be a non-empty string; Stage 1 validates its presence and logs it in `algorithm_summary.json`. The calibration log records `experiment_id` for each transfer, enabling the falsification protocol to query independence across transfers. If `experiment_id` is not available (legacy artifacts), the pipeline logs a warning: "No experiment_id found — independence cannot be verified programmatically. Using input directory as fallback." In this case, algorithms are considered independent if they come from different input directories (e.g., `routing/experiment_1/` vs. `routing/experiment_2/`), but this is a weaker guarantee. Two algorithms from the same experiment run with different random seeds are NOT independent — they share evolutionary lineage.
- **Fail:** Cluster benchmark verdict is FAIL (not INCONCLUSIVE) — the matched workload does not show improvement >= `T_eff`.
- **Decision authority:** The pipeline author (human) decides whether to suspend. The pipeline surfaces the falsification signal; it does not auto-suspend.
- **Suspension scope:** No new transfers are initiated. In-progress transfers may complete. Resumption requires documented simulation improvements and at least one successful end-to-end transfer on the improved simulation.

**Partial falsification:** If failures cluster on specific workload types, restrict transfers to workload types with demonstrated success. Track per-workload-type pass/fail in the calibration log.

**Calibration Log**

`docs/transfer/calibration_log.md` — one entry per completed transfer. Each entry follows this template:

```markdown
### Transfer: <algorithm_name> (<date>)
- **Algorithm:** <algorithm_name>
- **Date:** <YYYY-MM-DD>
- **Input directory:** <path>
- **Matched workload:** <workload_name>
- **Suite A:** Kendall-tau=<value>, numeric fidelity failures=<count>/<total>
- **Suite B:** Rank stability tau=<value>, threshold crossing change=<value>%
- **Suite C:** Parallel safety=<pass/fail>, pile-on max share=<value>
- **Cluster benchmark verdict:** <PASS/FAIL/INCONCLUSIVE>
- **Per-workload results:**
  | Workload | Sim Predicted | Observed | Ratio |
  |----------|--------------|----------|-------|
  | <name>   | <value>%     | <value>% | <value> |
- **Mechanism check:** <PASS/FAIL/INCONCLUSIVE> <override reason if applicable>
- **Noise CV:** <per-metric CV values>
- **Overrides:** <any threshold overrides or INCONCLUSIVE overrides used>
- **Notes:** <free-form observations>
```

The LLM must validate the entry against `workspace/validation_results.json` before appending — all numeric values must match the validation results file. Maintenance:
- **Owner:** The LLM appends a new entry at the end of Stage 6 (after PR creation), using data from `workspace/validation_results.json`. The user reviews the entry as part of the PR.
- **Trigger:** Entry creation is part of Stage 6 — the PR prompt template includes the instruction to append to the calibration log.
- **Threshold adjustment:** After every 3 completed entries, the pipeline author (human) reviews the log and decides whether to adjust thresholds. Threshold changes are committed as a separate PR to the sim2real repo, not bundled with a transfer PR.

**Initial calibration procedure (before first transfer):**

All validation thresholds are provisional for the first 3 transfers. The initial values and their justification:

| Threshold | Initial Value | Basis |
|---|---|---|
| Suite A numeric fidelity | `abs <= 1e-6` or `<= 1%` relative | Standard floating-point tolerance for arithmetic translations |
| Suite A Kendall-tau | 0.8 | Conservative — allows minor rank swaps while requiring strong correlation |
| Suite B rank stability | 0.7 | Relaxed relative to Suite A to account for staleness-induced noise |
| Suite B threshold-crossing stability | <20% | Allows 1-in-5 boundary tuples to shift under staleness |
| Cluster improvement (`T_eff` floor) | 5% | Industry-standard minimum meaningful improvement; adjusted upward by noise CV |
| Cluster regression limit | 2% | Tight regression bound to protect production quality |

For the first 3 transfers, the pipeline applies these defaults but records all raw metric values in the calibration log. **Bootstrapping safeguard:** During the uncalibrated period (transfers 1–3), a stage failure does not automatically reject the algorithm. Instead:
- The pipeline records the failure and all raw metric values.
- The pipeline presents the user with the raw data and the margin by which the threshold was missed (e.g., "Kendall-tau = 0.76, threshold = 0.8, margin = -0.04").
- The user decides: (a) **Accept failure** — the threshold is likely correct and the algorithm genuinely fails, (b) **Override to pass** — the threshold may be too strict; record the override with justification in the calibration log, or (c) **Adjust threshold** — change the threshold for this and future transfers, recording the old and new values.
- All overrides and adjustments are tagged `uncalibrated_period: true` in the calibration log for later review.

This prevents the chicken-and-egg problem: algorithms are not silently rejected by unjustified thresholds, and the calibration data accumulates regardless of pass/fail decisions.

**Normality assumption:** The `2×` multiplier in `T_eff = max(5%, 2 × CV)` approximates 2-sigma confidence for normally distributed measurements. If the noise characterization data (5 baseline runs) shows non-normal distribution (e.g., Shapiro-Wilk p < 0.05 on any metric), the pipeline logs a warning: "Metric `<name>` may not be normally distributed (Shapiro-Wilk p=`<value>`). The 2× CV multiplier may underestimate noise. Consider increasing to 3× CV or adding more baseline runs." The `noise-characterize` CLI command includes a normality check and reports the result.

After 3 completed transfers, the pipeline author reviews the log and adjusts thresholds based on observed distributions:
1. If Suite A Kendall-tau is consistently >0.95, consider tightening to 0.9.
2. If Suite B stability failures correlate with specific signal types, consider per-signal staleness window adjustments rather than threshold changes.
3. If cluster improvement ratios (observed/predicted) cluster around a consistent attenuation factor, consider adjusting the floor accordingly.

Until calibration review is complete, the pipeline logs a warning: "Using uncalibrated provisional thresholds (transfer N/3)."

---

## Pipeline Self-Verification

Before the first production transfer, validate the pipeline itself using a synthetic algorithm:
1. **Smoke test:** Create a trivial algorithm (e.g., "weight = 1.0 for all endpoints") and run it through all 6 stages. Verify each stage produces output in the expected format and the pipeline completes end-to-end. This validates prompt templates, CLI tools, and the Go test harness work together.
2. **Known-answer test:** Create a synthetic algorithm with known behavior (e.g., "if load > 5, weight = 0") and verify Suite A produces exact expected scores. This validates the equivalence testing infrastructure.
3. Re-run these tests after any change to prompt templates, CLI tools, or the Go test harness.

---

## Diagnostic Guide

| Failure | Check | Likely Cause |
|---|---|---|
| Stage 4 (test) | Error references symbol not in mapping artifact | Stale mapping — update artifact |
| Stage 4 (test) | 3 retries exhausted | Algorithm too complex for LLM translation |
| Suite A (equivalence) | Failures at threshold boundaries | Off-by-one or inverted condition in translation |
| Suite A (equivalence) | Numeric fidelity fails, rank passes | Weight normalization mismatch |
| Suite A (equivalence) | Rank fails, numeric fidelity passes | Threshold boundary translation error — scores are close but ranking flips at decision boundaries |
| Suite B (staleness) | Single signal causes most degradation | That signal's staleness window larger than expected |
| Suite B (staleness) | All signals degrade | Algorithm fundamentally staleness-sensitive |
| Cluster benchmark | All workloads improve equally | Benefit from overhead reduction, not mechanism |
| Cluster benchmark | Matched workload doesn't improve | Workload reproduction issue or benefit doesn't survive production conditions |
| Cluster benchmark | No workload improves | Check noise characterization — threshold may be too tight |

---

## Future Extensions

Explicitly **out of scope** for v1:

1. **P/D-aware transfer** — Extend simulation to model prefill/decode disaggregation, then extend the pipeline to generate plugins for specific scheduling profiles
2. **Bidirectional transfer** — Production benchmark insights fed back to simulation to improve fidelity
3. **Claude Code skill** — Package the transfer as a `/transfer` skill with argument parsing
4. **Multi-algorithm transfer** — Batch transfer of top-N algorithms from a single experiment
5. **CI auto-trigger** — Auto-trigger on new best program discovery above a threshold
6. **Statistical confidence** — Require >=3 benchmark runs with significance testing instead of single-run go/no-go
7. **Combined staleness+concurrency test (Suite D)** — Test interaction of stale signals with concurrent requests
8. **Non-routing transfer types** — Admission policies, priority policies, etc. (the architecture supports this; implementation is per-type work)

---

## Summary

The transfer pipeline is an **interactive Claude Code session** guided by:
- **Prompt templates** (`prompts/`) — LLM instructions for each of the 6 pipeline stages
- **A mapping artifact** (`docs/transfer/blis_to_llmd_mapping.md`) — concrete signal/interface correspondences
- **A scorer template** (`docs/transfer/scorer_template.go.md`) — target system plugin conventions
- **CLI tools** (`tools/`) — mechanical support for parsing, equivalence testing, and metrics collection

The pipeline works locally in the target submodules, validates through equivalence testing (using inference-sim) and cluster benchmarks, and creates PRs as the final step. Validation rigor (3-suite equivalence, mechanism checks, noise characterization, falsification protocol) is preserved from v2. Operational scaffolding (retry state machines, crash recovery, artifact version schemes) is removed in favor of interactive session handling.

### Key Changes from v2

| Aspect | v2 | v3 |
|---|---|---|
| Host repo | OpenEvolve | sim2real |
| Simulation dependency | OpenEvolve + BLIS evaluator | inference-sim (submodule) |
| Pipeline driver | Python modules (`openevolve/transfer/`) | Prompt templates + thin CLI tools |
| Stages | 8 | 6 |
| PR creation | Mid-pipeline (draft PRs, comments as validation log) | Final step (work locally, PR when validated) |
| Operational scaffolding | Heavy (retry state machines, crash recovery, artifact version schemes, borderline timeouts) | Light (interactive session handles errors and human decisions) |
| Validation rigor | 3-suite equivalence + cluster benchmarks + mechanism check | Same |
| Generality | Routing-specific | Designed for multiple transfer types (routing first) |
| Supporting artifacts | 4 (mapping, template, prompt, workload generator) | 3 (mapping, template, prompts) |
