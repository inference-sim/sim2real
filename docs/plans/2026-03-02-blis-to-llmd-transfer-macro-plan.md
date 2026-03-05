# BLIS-to-llm-d Algorithm Transfer Pipeline — Macro Plan

**Date:** 2026-03-02 (rewritten 2026-03-03)
**Status:** Draft
**Design Doc:** `docs/plans/2026-02-26-blis-to-llmd-transfer-design.md`
**Template:** Cross-system macro plan (`docs/contributing/templates/macro-plan-cross-system.md`)

---

## A) Executive Summary

This plan decomposes the BLIS-to-llm-d transfer pipeline into an ordered PR series across two repositories. The pipeline takes an evolved algorithm from OpenEvolve/BLIS (adaptive routing logic in Go), translates it into a production scorer plugin for `llm-d-inference-scheduler`, validates equivalence through multi-tier testing, and produces tested PRs.

**Systems:** OpenEvolve (host — pipeline code), llm-d-inference-scheduler (target — scorer plugin), llm-d-benchmark (target — benchmark configs).

**PR count:** 7 PRs, all in openevolve (2 of which require llm-d repo access to create their artifact content).

**Milestones:** (1) After PR2: prerequisite artifacts exist, pipeline can attempt extraction. (2) After PR4: full pipeline can run through code generation + unit testing. (3) After PR6: end-to-end pipeline including validation and promotion.

---

## B) Multi-System Recon Summary

### HOST: OpenEvolve

- **EVOLVE-BLOCK extraction**: Already implemented in `openevolve/utils/code_utils.py:9-37`. Uses `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` markers, line-based regex scanning. Returns `(start_line, end_line, block_content)` tuples. Handles multiple blocks but not nested markers. [CONFIRMED: code_utils.py:9-37]
- **Best program artifacts**: JSON format with fields: `id`, `generation`, `iteration`, `timestamp`, `parent_id`, `metrics` (dict), `language`, `saved_at`. Metrics structure varies by evaluator. [CONFIRMED: examples/arc_benchmark/outputs/evaluation_task_0/best/best_program_info.json]
- **Evaluator pattern**: `openevolve/evaluator.py` — cascade evaluation (stage1/stage2/stage3), subprocess-based, returns `EvaluationResult` with metrics + artifacts dicts. [CONFIRMED: evaluator.py:32-728]
- **Config**: YAML-based, `openevolve/config.py`. [CONFIRMED]
- **Test framework**: unittest, `python -m unittest discover tests`. [CONFIRMED: CLAUDE.md]
- **Formatting**: Black. [CONFIRMED: CLAUDE.md]

### TARGET: llm-d-inference-scheduler

- **Plugin interface**: Scorers implement a plugin interface with identity, category, and score methods. [UNVERIFIED — sourced from design doc Section "Translation Pattern: Composite Scorer"]
- **Registration**: Scorers register via the target system's plugin composition mechanism. [UNVERIFIED — design doc]
- **Scoring config**: YAML/JSON config enabling/disabling individual scorers with weights. [UNVERIFIED — design doc]
- **Conventions**: New scorers are additive (new files, config registration). No modification of existing scorer source files. [UNVERIFIED — design doc]
- **Build/test**: Go build + test suite. [INFERRED from Go ecosystem + design doc references]

### TARGET: llm-d-benchmark

- **Benchmark profiles**: Config files defining workload patterns. [UNVERIFIED — design doc Section "Workload Mapping Summary"]
- **Benchmark tool**: Deploys scheduler + runs workloads, collects latency/throughput metrics. [UNVERIFIED — design doc]

### Supporting Artifacts (to be created)

| Artifact | Location | Purpose |
|----------|----------|---------|
| `blis_to_llmd_mapping.md` | `docs/transfer/` | Signal/interface mapping, concrete types, version pin |
| `scorer_template.go.md` | `docs/transfer/` | Example scorer showing plugin conventions |
| `transfer_prompt.md` | `docs/transfer/` | 8-stage orchestration instructions for Claude Code |

### Open Uncertainties

1. llm-d scorer plugin interface is described behaviorally in the design doc but concrete Go types are UNVERIFIED (no repo access)
2. Benchmark tool name and CLI interface are UNVERIFIED
3. Scoring config format (YAML vs JSON, field names) is UNVERIFIED
4. P/D disaggregation detection indicators are UNVERIFIED

---

## C) High-Level Objectives + Non-Goals + Scoping Table

### Analysis Questions (from design doc)

These objectives trace directly to the three analysis questions in the design document (`docs/plans/2026-02-26-blis-to-llmd-transfer-design.md`):

- **Q1: Does the benefit survive the abstraction gap?** BLIS operates on a simulation with synthetic signals. Does the evolved routing improvement transfer to production llm-d with real signals?
- **Q2: Which signals have equivalents?** For each BLIS signal (e.g., queue depth, KV-cache utilization, request priority), is there a production llm-d equivalent with sufficient fidelity?
- **Q3: What is the minimum fidelity threshold?** Below what signal fidelity does a transferred algorithm cease to outperform the production baseline?

### Objectives

1. **Transfer one algorithm**: Take a single BLIS-discovered EVOLVE-BLOCK and produce a tested, ready-to-review llm-d scorer plugin PR — answers Q1/Q2/Q3
2. **Validate equivalence**: Confirm the translated scorer preserves the original algorithm's behavior (rank correlation > 0.8, numeric fidelity within 1%) — answers Q1
3. **Validate production benefit**: Confirm the algorithm's improvement survives the abstraction gap via cluster benchmarks — answers Q1
4. **Produce audit trail**: Every validation stage posts results as PR comments, enabling reviewers to trace the algorithm's journey — supports Q1/Q2/Q3 traceability
5. **Enable rollback**: Generated scorer can be disabled via config toggle without code removal

### Non-Goals

- Multi-algorithm batch transfer (config merge conflicts — deferred to v2)
- Bidirectional transfer (production insights back to BLIS)
- Continuous integration trigger (requires pipeline maturity first)
- P/D disaggregation-aware transfer (requires BLIS extension)
- Algorithms using Low-fidelity signals (pipeline halts, requires human decision)

### Version Constraints

- OpenEvolve: current HEAD on `michael-blis` branch
- llm-d-inference-scheduler: pinned via mapping artifact's "last verified against" commit hash
- llm-d-benchmark: pinned via mapping artifact (same mechanism)

### Rollback/Disable Guarantees

- **llm-d-inference-scheduler**: Generated scorer can be disabled via config toggle (target system's disable convention). Disabled scorer produces identical routing to pre-transfer state. Stage 4 unit tests verify this.
- **llm-d-benchmark**: Benchmark configs are additive (new files only). Removal = delete files.
- **OpenEvolve**: Pipeline artifacts in `docs/transfer/` are informational. No runtime impact on OpenEvolve itself.

### Pipeline Scoping Table

| Capability | v1 | Deferred | Out of Scope | Justification |
|------------|:--:|:--------:|:------------:|---------------|
| Single algorithm transfer | X | | | Core use case |
| Conditional linear combination algorithms | X | | | Covers EVOLVE-BLOCK output class |
| High/Medium fidelity signals only | X | | | Low-fidelity requires human review |
| Interactive Claude Code session | X | | | Manual trigger, user oversight |
| Signal coverage report with fidelity ratings | X | | | Answers Q2 (which signals map) |
| 3-suite equivalence testing | X | | | Answers Q1 (benefit survives gap) |
| Cluster benchmarks with mechanism check | X | | | Answers Q1 at production level |
| Noise characterization prerequisite | X | | | Required for meaningful benchmarks |
| Multi-algorithm batch transfer | | X | | Config merge conflicts |
| Statistical significance (multi-run) | | X | | Single-run with provisional tag for v1 |
| Combined staleness+concurrency (Suite D) | | X | | Tests independence sufficient for v1 |
| P/D disaggregation-aware transfer | | | X | Requires BLIS extension |
| Bidirectional transfer | | | X | Different pipeline entirely |
| CI auto-trigger | | | X | Requires pipeline maturity |

---

## D) Component Model

### Components (9)

**1. Orchestrator** (ORCHESTRATOR)
- Responsibility: Controls stage sequencing, retries, user interaction, crash recovery
- Inputs: User trigger (experiment path, llm-d repo paths), user decisions at interactive checkpoints
- Outputs: Stage dispatch, retry decisions, final status
- Side effects: Reads/writes `transfer_workspace/` directory, user prompts
- Invariants: Retry counter never exceeds 3; identical consecutive errors stop retries
- Failure modes: Session crash → restart from Stage 1 (crash recovery via branch detection). Retry exhaustion (3 identical failures) → halt with diagnostic, user decides whether to debug or abort.
- External deps: Claude Code session state
- User interaction checkpoints: (1) After Stage 2 signal coverage report — user reviews unmapped/low-fidelity signals. (2) After Stage 5 draft PR creation — user reviews generated code before validation. (3) After Stage 7 borderline benchmark results — user decides promote/flag/debug. (4) After Stage 8 promotion verdict — user confirms final PR label.

**2. Extract** (PIPELINE STAGE)
- Responsibility: Parse best program, extract EVOLVE-BLOCK, validate scope, check prerequisites
- Inputs: Best program artifact (JSON), experiment metadata
- Outputs: `transfer_workspace/algorithm_summary.json`
- Side effects: None (read-only from experiment, writes to workspace)
- Invariants: Scope verdict is deterministic; prerequisite check runs before extraction; mapping artifact schema validated before processing
- Failure modes: Missing EVOLVE-BLOCK markers → halt. Scope reject → halt. Stale artifacts → halt with diagnostic. Invalid mapping artifact schema → halt with validation errors.
- External deps: OpenEvolve experiment output (`openevolve/utils/code_utils.py:parse_evolve_blocks`)

**3. Translate** (PIPELINE STAGE)
- Responsibility: Map BLIS signals to llm-d equivalents, classify branches, produce signal coverage report
- Inputs: Algorithm summary + mapping artifact
- Outputs: `transfer_workspace/signal_coverage.json` + `transfer_workspace/mapped_spec.md`
- Side effects: None
- Invariants: Branch classification is deterministic; Low-fidelity critical signals halt pipeline
- Failure modes: Unmappable signal → halt. Branch count mismatch → halt. Workload profile missing → halt.
- External deps: Mapping artifact (`docs/transfer/blis_to_llmd_mapping.md`)

**4. Generate** (PIPELINE STAGE)
- Responsibility: LLM generates scorer plugin code on a new branch in the llm-d repo
- Inputs: Mapped spec + scorer template + llm-d repo working copy
- Outputs: Generated files on `blis-transfer/<name>` branch (scorer source, tests, config update)
- Side effects: Creates git branch, writes files to llm-d repo
- Invariants: All files listed in output contract exist on disk before proceeding
- Failure modes: LLM generates invalid code → retry via Stage 4 feedback loop
- External deps: llm-d-inference-scheduler repo, scorer template artifact

**5. Unit Test** (PIPELINE STAGE)
- Responsibility: Build and test generated code; classify failures; feed errors back for retry
- Inputs: Generated files on branch
- Outputs: Pass/fail, error context for retry
- Side effects: Runs go build + go test on llm-d repo
- Invariants: Pre-existing failures detected (package overlap check); stale mapping errors not retried
- Failure modes: New-code failure → retry (back to Generate, up to 3x). Stale mapping → halt. Pre-existing → flag.
- External deps: llm-d-inference-scheduler repo (build toolchain)

**6. Draft PR** (PIPELINE STAGE)
- Responsibility: Create draft PRs in scheduler and benchmark repos
- Inputs: Passing unit tests, generated files, signal coverage data
- Outputs: Draft PR URLs, PR numbers
- Side effects: Creates PRs via GitHub API
- Invariants: PR creation is atomic per repo (benchmark PR failure → close scheduler PR)
- Failure modes: Auth failure → halt. Branch conflict → halt. Transient → retry once.
- External deps: GitHub API (`gh pr create`)

**7. Validation** (PIPELINE STAGE)
- Responsibility: Run integration tests, 3-suite equivalence tests, cluster benchmarks
- Inputs: Draft PR branch
- Outputs: Per-suite results posted as PR comments, go/no-go verdict
- Side effects: Posts PR comments, deploys to test cluster (Stage 7), restores baseline on failure
- Invariants: Suite ordering enforced (A→B→C); borderline results halt for user; baseline restored on abort
- Failure modes: Suite failure → post results, leave draft PR. Timeout → post diagnostic. Cluster failure → restore baseline, warn user.
- External deps: llm-d test infrastructure, benchmark cluster, BLIS evaluator (for Suite A ground truth)

**8. Promote** (PIPELINE STAGE)
- Responsibility: Evaluate go/no-go criteria, promote or flag the PR
- Inputs: All validation results
- Outputs: PR label changes, status update
- Side effects: Updates PR labels and status
- Invariants: Provisional results cannot auto-promote; borderline overrides require documented reasoning
- Failure modes: None — always produces a verdict (promote, flag, or manual-debug)
- External deps: GitHub API

**9. Transfer Workspace** (ARTIFACT)
- Responsibility: Inter-stage file storage and crash recovery indicator
- Inputs/outputs: All stage artifacts (algorithm_summary.json, signal_coverage.json, mapped_spec.md, retry_log.json, decisions.json)
- Location: `transfer_workspace/` in the llm-d repo working copy, on the transfer branch
- Invariants: Persists on branch for crash recovery; each file has a defined JSON schema

### Data Flow

```
[Experiment Output] → Extract → algorithm_summary.json
                                       ↓
[Mapping Artifact] → Translate → signal_coverage.json + mapped_spec.md
                                       ↓
[Scorer Template] → Generate → files on blis-transfer/<name> branch
                                       ↓
                    Unit Test ←→ Generate (retry loop, max 3x)
                        ↓
                    Draft PR → scheduler PR + benchmark PR (draft)
                        ↓
                    Validation → PR comments (integration, equivalence, benchmark)
                        ↓
                    Promote → PR labels (validated / needs-debugging / infra-retry)
```

### System Invariants

- No PR created without passing unit tests
- No PR promoted without passing equivalence + benchmark
- Cluster baseline restored after any Stage 7 failure or abort
- Generated scorer disableable via config toggle without code removal
- Inter-stage artifacts validated by consuming stage before processing

### Extension Points

| Extension | Cost | Files Changed | Tests to Add | Notes |
|-----------|------|---------------|-------------|-------|
| New target system | New mapping artifact + scorer template + prompt section | ~3 new files in `docs/transfer/` + PR creator path | ~5 tests (mapping validation, template compile, PR creation) | Highest cost; requires target repo familiarity |
| New validation suite | Add suite to Stage 6 prompt section + timeout budget | ~1 prompt section + 1 equivalence module method | ~3 tests (suite logic, ordering, timeout) | Medium cost |
| New signal type | Add entry to mapping artifact | 1 artifact row + 1 translation test case | ~1 test | Lowest cost |

### External System Map

| Component | OpenEvolve | llm-d-scheduler | llm-d-benchmark | GitHub |
|-----------|:----------:|:----------------:|:---------------:|:------:|
| Extract | reads experiment output | | | |
| Translate | reads mapping artifact | | | |
| Generate | | writes branch | | |
| Unit Test | | runs build+test | | |
| Draft PR | | | | creates PRs |
| Validation | BLIS evaluator (Suite A) | integration tests | cluster benchmark | posts comments |
| Promote | | | | updates labels |

---

## E) Risk Register

| # | Decision | Assumption | Validation | Cost if Wrong | Gate |
|---|----------|------------|------------|---------------|------|
| R1 | LLM translates algorithms correctly | Claude can bridge the BLIS↔llm-d abstraction gap | 3-suite equivalence testing (Stage 6) catches translation errors | 1 PR (re-run Stage 3 with different prompt) | Before PR5 |
| R2 | Scorer template accurately reflects llm-d conventions | Template compiles and passes tests against llm-d HEAD | Compilation + smoke test at Stage 1 prerequisite check | 2 PRs (PR1 + PR2 must be recreated) | Before PR3 |
| R3 | Mapping artifact captures all needed signals | Signal categories from design doc are complete | Stage 2 halt on unmappable signal; calibration log after 3 transfers | 2 PRs (PR1 rework + downstream) | Before PR3 |
| R4 | Single-run benchmarks are meaningful | Noise characterization shows CV < 3% | Noise characterization prerequisite (5 baseline runs) | 1 PR (PR5 add multi-run support — deferred feature) | Before PR5 |
| R5 | Equivalence threshold (0.8 Kendall-tau) is appropriate | Threshold distinguishes good from bad translations | Calibrate after first 3 transfers using calibration log | 0 PRs (config change in prompt template) | After PR6 (post-launch) |
| R6 | EVOLVE-BLOCK extraction handles all algorithm shapes | Existing `parse_evolve_blocks` handles single-file, non-nested blocks | Test with 3+ real experiment outputs | 1 PR (extend extraction in PR3) | Before PR3 |

### Cross-System Risk Categories

| Risk Category | Status | Mitigation |
|---------------|--------|------------|
| **API drift** | Mitigated | Mapping artifact pins llm-d commit; Stage 1 compilation + smoke test detect breakage; commit distance check halts at >50 commits (requires explicit user override to proceed) |
| **Version mismatch** | Mitigated | Mapping artifact's "last verified against" commit hash; Stage 1 validates before proceeding |
| **Schema evolution** | Mitigated | Inter-stage artifacts have JSON schemas validated by consuming stages; artifact major version mismatch halts pipeline |
| **External availability** | Partially mitigated | Stage 7 cluster benchmark depends on cluster availability; failure leaves draft PR for manual retry. PR5 includes an "infra-retry" label path. Cluster retry procedure: (1) restore baseline, (2) label PR "infra-retry", (3) post comment with diagnostic, (4) user re-runs Stage 7 when cluster available. |
| **Distributed partial failure** | Mitigated | PR creation is atomic per repo (benchmark failure → close scheduler PR). Stage 7 restores baseline on abort. Transfer workspace persists for crash recovery. |
| **Mock fidelity** | Acknowledged risk | v1 has no mock-based testing of the pipeline itself. Equivalence tests (Stage 6) run against real llm-d code, not mocks. Mock divergence risk is low for v1 (direct testing against repos). Future: add mock-based dry-run test in PR6. |

---

## F) Implementation Evolution

**Starting point:** OpenEvolve has EVOLVE-BLOCK extraction (`code_utils.py:parse_evolve_blocks`), best program artifact output (JSON), and experiment output directories. No transfer infrastructure exists. llm-d repos are available as local clones.

### Milestone 1: Foundation (after PR0 + PR1 + PR2)

Prerequisite artifacts exist. `docs/transfer/` directory populated. Pipeline can be manually invoked for extraction (Stage 1) against real experiments.

- **Testable:** Stage 1 can read an experiment output, extract the EVOLVE-BLOCK, validate scope, and produce `algorithm_summary.json`. Verify manually.

### Milestone 2: Translation + Code Generation (after PR3 + PR4)

Full pipeline runs through Stages 1-5 (extraction, translation, code generation, unit tests, PR creation). Draft PRs can be created against llm-d repos.

- **Testable:** Run pipeline on a real BLIS experiment. Verify: algorithm extracted, signals mapped, scorer code generated, unit tests pass, draft PR created.

### Milestone 3: End-to-End (after PR5 + PR6)

Full pipeline including validation (equivalence tests, cluster benchmarks) and promotion logic. Pipeline is production-ready for v1.

- **Testable:** Complete end-to-end transfer: extract → translate → generate → test → PR → equivalence → benchmark → promote. Verify all PR comments posted.

---

## G) Stable API Reference

**Omitted.** No llm-d APIs have been independently verified as stable. All llm-d behavioral references are sourced from the design document and marked UNVERIFIED in Section B. Concrete Go interfaces will be documented in the mapping artifact (PR1) after llm-d repo inspection.

The only confirmed stable API is OpenEvolve's `parse_evolve_blocks` function (`openevolve/utils/code_utils.py:9-37`):
- Input: `code: str` — source code with EVOLVE-BLOCK markers
- Output: `List[Tuple[int, int, str]]` — `(start_line, end_line, block_content)`
- Marker format: `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END`
- Behavior: Line-based regex scan, handles multiple blocks, does not handle nesting

---

## H) Cross-Cutting Infrastructure Plan

### 1. Shared Test Infrastructure
- **PR0** creates: `docs/transfer/schemas/` directory with JSON Schema files for all inter-stage artifacts
- **PR0** creates: `openevolve/transfer/` package with schema validation utility (validates JSON artifacts against schemas)
- **PR3** creates: extraction test fixtures (3+ real experiment outputs for testing Stage 1)
- **PR5** creates: equivalence test harness template (JSON tuple format, BLIS evaluator adapter)

### 2. Documentation Maintenance
- **PR0**: Update CLAUDE.md with transfer pipeline section (new CLI command, artifact locations, test commands)
- **Each PR**: Update `docs/transfer/README.md` with current pipeline status and usage instructions
- **PR1, PR2**: Include artifact schema docs inline in the artifact files themselves
- **PR6**: Update CLAUDE.md with end-to-end usage examples and orchestrator invocation instructions

### 3. CI Pipeline Changes
- **PR0**: Add `tests/test_transfer_schemas.py` — validates that schema files parse correctly
- **PR3**: Add `tests/test_extraction.py` — tests EVOLVE-BLOCK extraction against fixtures
- **PR6**: Add `tests/test_transfer_dry_run.py` — dry-run integration test (mocked llm-d, validates orchestration flow)
- No external access needed for CI (all tests use local fixtures or mocks)

### 4. Dependency Management
- No new Python package dependencies (uses stdlib `json`, `re`, `pathlib`)
- llm-d repos: version-pinned via mapping artifact commit hash (not Python dependency management)
- Artifact version compatibility: major.minor scheme, pipeline checks major version at Stage 1

### 5. Cross-Repo Coordination
- **Ordering:** All OpenEvolve PRs merge first. Mapping artifact (PR1) requires reading llm-d repo but lands in OpenEvolve.
- **Branch naming:** Transfer branches in llm-d: `blis-transfer/<algorithm-name>`
- **Review ownership:** OpenEvolve PRs reviewed by OpenEvolve maintainers. Generated llm-d PRs reviewed by llm-d maintainers (pipeline produces the PR, humans review).
- **Cross-repo pre-merge testing for PR1/PR2:** Before merging PR1, verify mapping artifact accuracy by manually compiling a scorer stub against pinned llm-d HEAD. Before merging PR2, verify scorer template compiles and passes tests against pinned llm-d HEAD. These verifications are manual (no CI cross-repo access) and documented in PR review checklists.

### 6. Artifact Lifecycle
- **Prerequisites before first transfer:** All three artifacts in `docs/transfer/` must exist (mapping, scorer template, transfer prompt)
- **Created by:** PR1 (mapping artifact), PR2 (scorer template), PR3 (transfer prompt)
- **Maintained by:** Manual update when llm-d APIs change
- **Staleness detection:** Stage 1 compilation + smoke test + commit distance check (automated per transfer)
- **Version scheme:** major.minor per artifact, pipeline checks major version match

---

## I) PR Plan

### PR0: Transfer infrastructure foundation
**Target Repo:** openevolve
**Component Change:** Transfer Workspace
**PR Type:** infrastructure
**Motivation:** Establish directory structure, JSON schemas, and validation utilities that all subsequent PRs depend on.

**Scope:**
- In: `docs/transfer/` directory, `docs/transfer/schemas/` with JSON Schema files for algorithm_summary and signal_coverage, `openevolve/transfer/__init__.py` + `schema_validator.py`, CLAUDE.md update, `tests/test_transfer_schemas.py`
- Out: No pipeline logic, no artifact content

**Behavioral Guarantees:** Schema files parse correctly. Validator accepts valid JSON and rejects invalid JSON. CLAUDE.md documents transfer pipeline.
**Risks:** Schema design may need revision after implementing Stage 2. Mitigation: schemas are additive (add fields, don't remove).
**Cross-Cutting:** Creates shared infra consumed by all later PRs.
**Validation Gate:** None.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/` package, new `docs/transfer/` directory
- Test categories: Unit tests for schema validation
- Documentation: CLAUDE.md transfer section, docs/transfer/README.md
- Extension friction: Adding a new artifact schema = 1 JSON Schema file + 1 test case
- Cross-repo impact: None
- Independently reviewable: Self-contained infrastructure
- No dead code: Schema validator exercised by tests; schemas consumed by PR3+

---

### PR1: Mapping artifact
**Target Repo:** openevolve
**Component Change:** Translate (enables signal mapping)
**PR Type:** artifact
**Motivation:** The mapping artifact is the single source of truth for BLIS↔llm-d signal correspondences. Required before any translation can occur.

**Scope:**
- In: `docs/transfer/blis_to_llmd_mapping.md` with complete signal mapping table, interface list, staleness windows, version pin
- Out: No code changes

**Behavioral Guarantees:** Contains at least one complete signal mapping entry. "Last verified against" commit is current llm-d HEAD. Version is 1.0.
**Risks:** R3 (mapping completeness). Mitigation: Stage 2 halts on unmappable signals; iterate after first transfer.
**Cross-Cutting:** Consumed by PR3 (Stage 2 translation).
**Validation Gate:** R2 and R3 require mapping accuracy.

**Implementation Guide:**
- Requires: Reading llm-d-inference-scheduler repo to document scorer interface, signal access patterns, config format
- Test categories: Manual review (artifact is documentation, not code)
- Documentation: Self-documenting artifact
- Extension friction: Adding a new signal = 1 table row
- Cross-repo impact: References llm-d types/APIs (described behaviorally per abstraction rule)
- Independently reviewable: Documentation artifact
- No dead code: Consumed by Stage 2 in PR3

---

### PR2: Scorer template artifact
**Target Repo:** openevolve
**Component Change:** Generate (enables code generation)
**PR Type:** artifact
**Motivation:** The scorer template shows Claude Code the target code structure and plugin conventions. Required for Stage 3 code generation.

**Scope:**
- In: `docs/transfer/scorer_template.go.md` — annotated example scorer with plugin lifecycle, test structure, config registration
- Out: No code changes

**Behavioral Guarantees:** Template compiles and passes unit tests against pinned llm-d HEAD. Includes disabled-scorer no-op test. Version is 1.0.
**Risks:** R2 (template accuracy). Mitigation: Stage 1 compiles template before each transfer.
**Cross-Cutting:** Consumed by PR4 (Stage 3 code generation).
**Validation Gate:** R2 must be validated before PR3.

**Implementation Guide:**
- Requires: Reading llm-d repo to identify a well-structured existing scorer as basis
- Test categories: Manual verification (compile template against llm-d HEAD)
- Extension friction: Adding a new target system = new template file
- Cross-repo impact: References llm-d code patterns (described behaviorally)
- Independently reviewable: Documentation artifact
- No dead code: Consumed by Stage 3 in PR4

---

### PR3: Transfer prompt — extraction + translation (Stages 1-2)
**Target Repo:** openevolve
**Component Change:** Extract, Translate
**PR Type:** pipeline-stage
**Motivation:** Implements the first two pipeline stages: reading an experiment, extracting the EVOLVE-BLOCK, mapping signals, and producing the signal coverage report.

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 1-2 sections), `openevolve/transfer/extract.py` (extraction utilities extending `parse_evolve_blocks`), `openevolve/transfer/translate.py` (signal mapping + branch classification logic), test fixtures, `tests/test_extraction.py`, `tests/test_translation.py`
- Out: Stages 3-8, no PR creation, no validation

**Behavioral Guarantees:** Stage 1 extracts EVOLVE-BLOCK from real experiment output and produces valid `algorithm_summary.json`. Stage 2 produces valid `signal_coverage.json` (passes schema validation). Branch classification is deterministic. Low-fidelity critical signals halt.
**Risks:** R6 (extraction handles all shapes). Mitigation: Test against 3+ real experiments.
**Cross-Cutting:** Consumes PR0 (schemas), PR1 (mapping artifact). Creates extraction test fixtures.
**Validation Gate:** R6 validated by extraction tests.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/extract.py` and `translate.py`
- API surface: `extract_algorithm(experiment_path) → AlgorithmSummary`, `translate_signals(summary, mapping) → SignalCoverage`
- Test categories: Unit tests (extraction, classification), integration tests (real experiment outputs)
- Documentation: Transfer prompt Stages 1-2
- Extension friction: New signal type = 1 mapping artifact row + 1 test case
- Cross-repo impact: None (reads local experiment output + mapping artifact)
- Independently reviewable: Stages 1-2 produce verifiable artifacts without needing llm-d access
- No dead code: Both extraction and translation exercised by tests and by PR4

---

### PR4: Transfer prompt — generation + testing + PR creation (Stages 3-5)
**Target Repo:** openevolve
**Component Change:** Generate, Unit Test, Draft PR
**PR Type:** pipeline-stage
**Motivation:** Implements code generation from the mapped spec, unit test execution with retry logic, and draft PR creation.

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 3-5 sections), `openevolve/transfer/generate.py` (orchestrates LLM code generation), `openevolve/transfer/test_runner.py` (runs go build+test, classifies errors, manages retries), `openevolve/transfer/pr_creator.py` (creates draft PRs via gh CLI), tests
- Out: Stages 6-8, no validation, no benchmarks

**Behavioral Guarantees:** Stage 3 generates files on a named branch. Stage 4 retry loop stops on identical errors. Stage 5 PR creation is atomic (benchmark failure → close scheduler PR). Crash recovery detects existing branch.
**Risks:** R1 (LLM translation quality). Mitigation: Retry loop with error feedback; equivalence testing in PR5.
**Cross-Cutting:** Consumes PR0 (schemas), PR2 (scorer template), PR3 (Stage 1-2 outputs).
**Validation Gate:** None (validation is in PR5).

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/generate.py`, `test_runner.py`, `pr_creator.py`
- Test categories: Unit tests (error classification, retry logic, retry exhaustion after 3 identical failures), mock-based tests (mocked `gh` CLI, mocked `go test`, atomic PR rollback on partial failure)
- Documentation: Transfer prompt Stages 3-5
- Extension friction: New target repo = new PR creation path
- Cross-repo impact: Writes to llm-d repo (branch creation, file generation, PR creation)
- Independently reviewable: Testable with mocked external dependencies
- No dead code: All modules exercised by tests and by PR5

---

### PR5: Validation — equivalence testing + benchmarks + promotion (Stages 6-8)
**Target Repo:** openevolve
**Component Change:** Validation, Promote
**PR Type:** validation + orchestration
**Motivation:** Implements the multi-tier validation pipeline: integration tests, 3-suite equivalence testing, cluster benchmarks, and promotion logic (includes orchestration of suite ordering and promotion decision tree).

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 6-8 sections), `openevolve/transfer/equivalence.py` (test harness, tuple generation, Suite A/B/C logic), `openevolve/transfer/benchmark.py` (cluster benchmark orchestration, go/no-go criteria, mechanism check), `openevolve/transfer/promote.py` (promotion logic, label management), `docs/transfer/noise_characterization.md` (template), `docs/transfer/calibration_log.md` (template), tests
- Out: End-to-end orchestration (PR6)

**Behavioral Guarantees:** Suite ordering enforced (A→B→C). Borderline results halt for user. Cluster baseline restored on abort/failure. Mechanism check follows decision tree. Provisional results tagged.
**Risks:** R4 (single-run benchmarks), R5 (threshold calibration). Mitigation: Noise characterization prerequisite; calibration after 3 transfers.
**Cross-Cutting:** Consumes all prior PRs. Creates noise characterization and calibration log templates.
**Validation Gate:** R4 (noise characterization) validated before first transfer.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/equivalence.py`, `benchmark.py`, `promote.py`
- Test categories: Unit tests (tuple generation, go/no-go logic, mechanism check, suite ordering enforcement — verify Suite B cannot run before Suite A), integration test with mock cluster responses (including timeout, partial failure, infra-retry label path)
- Documentation: Transfer prompt Stages 6-8, diagnostic guide
- Extension friction: New validation suite = 1 module + prompt section + timeout budget update
- Cross-repo impact: Runs tests against llm-d repo, deploys to cluster, posts PR comments
- Independently reviewable: Validation logic testable with mock data
- No dead code: All modules exercised by tests and by PR6

---

### PR6: End-to-end orchestration + dry-run test
**Target Repo:** openevolve
**Component Change:** Orchestrator
**PR Type:** orchestration
**Motivation:** Ties all stages together with the full orchestration flow, crash recovery, and a dry-run integration test.

**Scope:**
- In: `openevolve/transfer/orchestrator.py` (stage sequencing, user interaction, crash recovery), `tests/test_transfer_dry_run.py` (end-to-end test with mocked externals), docs/transfer/README.md finalization
- Out: No new pipeline stages

**Behavioral Guarantees:** Orchestrator runs all 8 stages in sequence. Crash recovery detects existing branch and offers resume. Dry-run test validates full flow with mocked llm-d responses.
**Risks:** Mock fidelity for dry-run test. Mitigation: Mocks derived from real artifact schemas (not hand-written).
**Cross-Cutting:** Consumes all prior PRs. Finalizes documentation.
**Validation Gate:** None.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/orchestrator.py`
- Test categories: Integration test (dry-run with mocked externals covering all 8 stages)
- Documentation: docs/transfer/README.md, CLAUDE.md final update
- Extension friction: N/A — orchestrator calls stages via defined interfaces
- Cross-repo impact: Orchestrates cross-repo operations (all via prior PR modules)
- Independently reviewable: Orchestrator logic + dry-run test
- No dead code: Orchestrator exercised by dry-run test

---

## J) Dependency DAG

### OpenEvolve PR Dependencies

```
[PR0 openevolve] ─→ [PR1 openevolve] ─→ [PR3 openevolve] ─→ [PR4 openevolve] ─→ [PR5 openevolve] ─→ [PR6 openevolve]
        │                                       ↑
        └──────→ [PR2 openevolve] ──────────────┘
```

### Cross-Repo Visualization

llm-d PRs are created **at runtime** by the pipeline (Stage 5), not as part of this PR series. The cross-repo relationship is:

```
[PR0-PR3 openevolve]  →  [PR4 openevolve]  →  [PR5 openevolve]  →  [PR6 openevolve]
  (merged)                (pipeline ready)      (validation ready)   (orchestration)
                               │                      │
                               ↓ (runtime)             ↓ (runtime)
                          [Draft PR: llm-d-scheduler]  [Validation runs against
                          [Draft PR: llm-d-benchmark]   llm-d draft PRs]
```

**Note:** llm-d PRs are output artifacts, not part of the merge sequence. They are created per-transfer by the pipeline and reviewed by llm-d maintainers. No llm-d PR needs to be merged for the OpenEvolve PR series to proceed.

**Ordering constraints:**
- PR0 must merge before all others (infrastructure foundation)
- PR1 and PR2 can be developed **in parallel** after PR0
- PR3 requires PR1 (mapping artifact needed for translation)
- PR4 requires PR2 and PR3 (scorer template + extraction outputs needed for generation)
- PR5 requires PR4 (generated code needed for validation)
- PR6 requires PR5 (all stages needed for orchestration)

**Critical path:** PR0 → PR1 → PR3 → PR4 → PR5 → PR6

**Parallelizable workstreams:**
- Workstream A: PR1 (mapping artifact)
- Workstream B: PR2 (scorer template)
- Both can proceed simultaneously after PR0.

**Validation gate placement:**
- R2 + R3 validated before PR3 begins (mapping + template must be accurate)
- R4 validated before first real transfer (noise characterization)
- R5 validated after PR6 (calibration after 3 transfers)

**Integration risk notes:**
- PR4 is highest-risk merge (first cross-repo interaction at runtime — creates branches in llm-d)
- PR5 is second highest (deploys to cluster — production-adjacent activity)

---

## K) Design Bug Prevention Checklist

### General

| Failure Mode | Prevention | Verified In |
|-------------|------------|-------------|
| **Scaffolding creep** | Every file, function, and config exercised by tests in the introducing PR | Each PR's test suite |
| **Documentation drift** | CLAUDE.md and README updated in the PR that causes the change | PR0 (initial), PR6 (final) |
| **Test infrastructure duplication** | Schema validation utility created in PR0, consumed by all later PRs | PR0 |

### Cross-System-Specific

| Failure Mode | Prevention | Verified In |
|-------------|------------|-------------|
| **API contract drift** | Mapping artifact pins llm-d commit hash; Stage 1 compiles scorer template against llm-d HEAD before each transfer | PR3 (Stage 1 prerequisite checks) |
| **Mock divergence** | v1 tests against real llm-d repos (not mocks) for Stages 3-7; PR6 dry-run mocks derived from real artifact schemas | PR6 |
| **Distributed partial failure** | PR creation atomic (benchmark failure → close scheduler PR); Stage 7 restores baseline on abort; transfer workspace persists for crash recovery | PR4 (atomic PR), PR5 (baseline restore) |
| **Artifact staleness** | Stage 1: (1) validate mapping artifact schema, (2) compile scorer template, (3) run template tests, (4) check commit distance (<50, halt with override). All four must pass. | PR3 (Stage 1 implementation) |
| **Cross-repo merge ordering** | All OpenEvolve PRs merge first; generated llm-d PRs created per-transfer after pipeline runs | PR4 (PR creation), dependency DAG |
| **External system evolution** | Mapping artifact's major.minor version scheme; pipeline checks major version match at Stage 1; mismatch halts with diagnostic | PR3 (version check) |

### Invariants (must never break)

1. No PR created without passing unit tests (Stage 4 gates Stage 5)
2. No PR promoted without passing equivalence + benchmark (Stage 8 gates on Stages 6+7)
3. Cluster baseline restored after any Stage 7 failure
4. Generated scorer disableable via config toggle
5. Inter-stage JSON artifacts validated by consuming stage
6. Retry loop stops on identical consecutive errors
7. Low-fidelity critical signals halt pipeline (never silently proceed)

### Regression Surfaces

- Existing OpenEvolve tests (`python -m unittest discover tests`) must continue passing
- `openevolve/utils/code_utils.py:parse_evolve_blocks` behavior unchanged (PR3 extends, not modifies)
- No changes to `openevolve/evaluator.py` or `openevolve/controller.py`
