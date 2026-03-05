# BLIS-to-llm-d Algorithm Transfer Pipeline — Macro Plan v2

**Date:** 2026-03-04
**Status:** Draft
**Design Doc:** `docs/plans/2026-02-26-blis-to-llmd-transfer-design-v2.md`
**Template:** Cross-system macro plan (`docs/contributing/templates/macro-plan-cross-system.md`)
**Supersedes:** `docs/plans/2026-03-02-blis-to-llmd-transfer-macro-plan.md` (v1)

### Revision History

| Date | Change | Reason |
|------|--------|--------|
| 2026-03-04 | v2 rewrite | Incorporate design review round 1 convergence (0C/0I), validation strategy review (3C/6I), PR guide feedback. Add BLIS workload generator as 4th prerequisite. Add calibration transfer milestone. Address request parsing fidelity gap. Tighten noise characterization provisioning. |
| 2026-03-04 | v2.1 update | Align with design doc update: BLIS evaluator already produces per-workload results under `metrics.workload` — no evaluator changes needed. Remove PR0. Add per-workload YAML config files as experiment artifacts. Update artifact schemas, dependencies, milestones. PR count: 8→7. |

---

## A) Executive Summary

This plan decomposes the BLIS-to-llm-d transfer pipeline into an ordered PR series. The pipeline takes an evolved routing algorithm from OpenEvolve/BLIS (adaptive routing logic in Go), translates it into a production scorer plugin for `llm-d-inference-scheduler`, generates benchmark configs for `llm-d-benchmark`, validates equivalence through multi-tier testing (3-suite equivalence + cluster benchmarks), and produces tested, ready-to-review PRs.

**Systems:** OpenEvolve (host — pipeline code and artifacts), llm-d-inference-scheduler (target — scorer plugin), llm-d-benchmark (target — BLIS workload generator + benchmark configs).

**PR count:** 7 PRs in openevolve (PR1–PR7). The BLIS workload generator in llm-d-benchmark is a prerequisite delivered outside this PR series.

**Key change from v1:** Separated noise characterization into its own PR, added calibration transfer milestone.

**Key change from v2:** Removed PR0 (BLIS evaluator changes) — the evaluator already produces per-workload results under `metrics.workload` in `best_program_info.json`, and per-workload YAML config files provide workload generator parameters. No evaluator modifications needed.

**Milestones:** (1) After PR2: prerequisite artifacts exist. (2) After PR4: pipeline runs Stages 1-5 (extract→generate→PR). (3) After PR6: validation pipeline operational. (4) After PR7: end-to-end orchestration. Calibration transfer validates thresholds before production use.

---

## B) Multi-System Recon Summary

### HOST: OpenEvolve

- **EVOLVE-BLOCK extraction**: `openevolve/utils/code_utils.py:9-37` — `parse_evolve_blocks(code)` returns `List[Tuple[int, int, str]]`. Line-based regex, handles multiple blocks, no nesting. Markers: `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` (Python) or `// EVOLVE-BLOCK-START` / `// EVOLVE-BLOCK-END` (Go). [CONFIRMED]
- **BLIS evaluator**: `examples/blis_router/evaluator.py` — cascade evaluation, subprocess-based Go build+run. Returns `EvaluationResult` with `combined_score` and per-workload metrics. Artifacts include `workload_results`, `hypothesis_results`, `hypothesis_knowledge_base`. [CONFIRMED: evaluator.py:323-720]
- **BLIS evaluator output**: `best_program_info.json` has fields: `id`, `generation`, `iteration`, `timestamp`, `parent_id`, `metrics` (dict), `language`, `saved_at`. Per-workload results available under `metrics.workload` entry — produced by OpenEvolve's standard best-program tracking. No evaluator changes required. [CONFIRMED by design doc; concrete `metrics.workload` schema TBD — see design doc BLOCKING TODO]
- **Per-workload YAML config files**: Each BLIS workload has a `<workload_name>.yaml` file in the experiment output directory containing workload generator parameters (traffic pattern, request size distribution, concurrency level). These provide the config needed to reproduce workloads via the BLIS workload generator in llm-d-benchmark. [INFERRED from design doc Section "BLIS Experiment Artifacts"]
- **BLIS hypothesis framework**: `examples/blis_router/hypothesis.py` — parses `// HYPOTHESIS-N`, `// MECHANISM-N`, `// EXPECT-N` comments, tests predictions, maintains ledger. [CONFIRMED: hypothesis.py:35-100]
- **BLIS workloads**: 3 YAML files defining traffic patterns — `workload_v2_cache_warmup.yaml`, `workload_v2_load_spikes.yaml`, `workload_v2_multiturn.yaml`. These serve as workload generator configuration for the BLIS workload generator. [CONFIRMED: examples/blis_router/]
- **BLIS EVOLVE-BLOCK boundary enforcement**: `evaluator.py:103-143` — extracts evolved block, preserves template elsewhere. [CONFIRMED]
- **Test framework**: unittest, `python -m unittest discover tests`. 41 test files including `test_blis_hypothesis.py`. [CONFIRMED]
- **Config**: YAML-based, `openevolve/config.py`. [CONFIRMED]
- **Formatting**: Black. [CONFIRMED: CLAUDE.md]
- **Existing skills**: `.claude/skills/hypothesis-experiment/` (10-step workflow with convergence review). [CONFIRMED]
- **`docs/transfer/` directory**: Does NOT exist yet. [CONFIRMED]

### TARGET: llm-d-inference-scheduler

- **Plugin interface**: Scorers implement a plugin interface with identity, category, and score methods. [UNVERIFIED — sourced from design doc]
- **Registration**: Scorers register via the target system's plugin composition mechanism. [UNVERIFIED]
- **Scoring config**: YAML/JSON config enabling/disabling individual scorers with weights. [UNVERIFIED]
- **Conventions**: New scorers are additive (new files, config registration). No modification of existing scorer source. [UNVERIFIED]
- **Build/test**: Go build + test suite. [INFERRED from Go ecosystem]

### TARGET: llm-d-benchmark

- **Benchmark profiles**: Config files defining workload patterns. [UNVERIFIED]
- **Benchmark tool**: Deploys scheduler + runs workloads, collects latency/throughput metrics. [UNVERIFIED]
- **BLIS workload generator**: Does NOT exist yet — must be added as a prerequisite (see design doc Section "Supporting Artifacts"). [CONFIRMED absent]

### Supporting Artifacts (to be created)

| Artifact | Location | Purpose | Status |
|----------|----------|---------|--------|
| `blis_to_llmd_mapping.md` | `docs/transfer/` | Signal/interface mapping, concrete types, version pin, workload generator schema | TODO — PR1 |
| `scorer_template.go.md` | `docs/transfer/` | Example scorer showing plugin conventions | TODO — PR2 |
| `transfer_prompt.md` | `docs/transfer/` | 8-stage orchestration instructions | TODO — PR3/PR4 |
| BLIS workload generator | llm-d-benchmark repo | Reproduces BLIS workloads in cluster benchmarks | TODO — external prerequisite |

### Open Uncertainties

1. llm-d scorer plugin interface concrete Go types — UNVERIFIED (no repo access)
2. Benchmark tool name and CLI interface — UNVERIFIED
3. Scoring config format (YAML vs JSON, field names) — UNVERIFIED
4. P/D disaggregation detection indicators — UNVERIFIED
5. BLIS workload generator feasibility in llm-d-benchmark — UNVERIFIED (requires llm-d-benchmark maintainer input)

---

## C) High-Level Objectives + Non-Goals + Scoping Table

### Analysis Questions (from design doc)

- **Q1: Does the benefit survive the abstraction gap?** — Validated by Stage 6 equivalence tests and Stage 7 cluster benchmarks
- **Q2: Which signals have production equivalents?** — Validated by Stage 2 signal coverage report and Stage 6 Suite A per-signal analysis
- **Q3: What is the minimum fidelity for transfer?** — Validated empirically across transfers via calibration log (not computed in a single run)

### Objectives

1. **Transfer one algorithm**: Take a single BLIS-discovered EVOLVE-BLOCK and produce a tested, ready-to-review llm-d scorer plugin PR — answers Q1/Q2/Q3
2. **Validate equivalence**: Confirm the translated scorer preserves the algorithm's behavior (rank correlation > 0.8, numeric fidelity within 1%) — answers Q1
3. **Validate production benefit**: Confirm improvement survives the abstraction gap via cluster benchmarks with mechanism check — answers Q1
4. **Produce audit trail**: Every validation stage posts results as PR comments — supports Q1/Q2/Q3 traceability
5. **Enable rollback**: Generated scorer disableable via config toggle without code removal
6. **Establish calibration baseline**: First transfer serves as calibration run to validate provisional thresholds. Calibration is complete when: (a) at least 1 known-good algorithm transferred with all suites passing, (b) observed Kendall-tau within ±10% of provisional threshold (0.8), and (c) improvement magnitude consistent with BLIS evaluator results. Pipeline maintainer runs calibration within 7 days of PR7 merge. If thresholds require adjustment, update config before production use.

### Non-Goals

- Multi-algorithm batch transfer (config merge conflicts — deferred)
- Bidirectional transfer (production insights back to BLIS)
- Continuous integration trigger (requires pipeline maturity)
- P/D disaggregation-aware transfer (requires BLIS extension)
- Algorithms using Low-fidelity signals (pipeline halts, human decision)

### Version Constraints

- OpenEvolve: current HEAD on `michael-blis` branch
- llm-d-inference-scheduler: pinned via mapping artifact's "last verified against" commit hash
- llm-d-benchmark: pinned via mapping artifact (same mechanism)
- BLIS workload generator: major version must match mapping artifact's expected version

### Rollback/Disable Guarantees

- **llm-d-inference-scheduler**: Generated scorer disableable via config toggle. Disabled scorer produces identical routing to pre-transfer state. Stage 4 unit tests verify this.
- **llm-d-benchmark**: Benchmark configs are additive (new files only). Removal = delete files.
- **OpenEvolve**: Pipeline artifacts in `docs/transfer/` are informational. No runtime impact.

### Pipeline Scoping Table

| Capability | v1 | Deferred | Out of Scope | Justification |
|------------|:--:|:--------:|:------------:|---------------|
| Single algorithm transfer | X | | | Core use case |
| Conditional linear combination algorithms | X | | | Covers EVOLVE-BLOCK output class |
| High/Medium fidelity signals only | X | | | Low-fidelity requires human review |
| Interactive Claude Code session | X | | | Manual trigger, user oversight |
| Signal coverage report with fidelity ratings | X | | | Answers Q2 |
| 3-suite equivalence testing (A/B/C) | X | | | Answers Q1 |
| Cluster benchmarks with mechanism check | X | | | Answers Q1 at production level |
| Noise characterization prerequisite | X | | | Required for meaningful benchmarks |
| BLIS workload generator (in llm-d-benchmark) | X | | | Required for faithful workload porting |
| Calibration transfer (threshold validation) | X | | | Addresses CRITICAL validation gap |
| Request parsing unit tests | X | | | Addresses CRITICAL parsing fidelity gap |
| Multi-algorithm batch transfer | | X | | Config merge conflicts |
| Statistical significance (multi-run) | | X | | Single-run with provisional tag for v1 |
| Combined staleness+concurrency (Suite D) | | X | | Independent testing sufficient for v1 |
| P/D disaggregation-aware transfer | | | X | Requires BLIS extension |
| Bidirectional transfer | | | X | Different pipeline entirely |
| CI auto-trigger | | | X | Requires pipeline maturity |

---

## D) Component Model

### Components (9)

**1. Orchestrator** (ORCHESTRATOR)
- Controls stage sequencing, retries, user interaction, crash recovery
- Inputs: User trigger (experiment path, llm-d repo paths), user decisions at checkpoints
- Outputs: Stage dispatch, retry decisions, final status
- Side effects: Reads/writes `transfer_workspace/`, user prompts at 4 checkpoints
- Invariants: Retry counter ≤ 3; identical consecutive errors stop retries; stages run strictly in order
- Failure modes: Session crash → restart from Stage 1 (crash recovery via branch detection)

**2. Extract** (PIPELINE STAGE — Stage 1)
- Parse best program, extract EVOLVE-BLOCK, validate scope, check all 4 prerequisites
- Inputs: Best program artifact (`best_program_info.json` with `metrics.workload`) + per-workload YAML config files (`<workload_name>.yaml`)
- Outputs: `transfer_workspace/algorithm_summary.json`
- Invariants: Scope verdict deterministic; prerequisites checked before extraction
- Failure modes: Missing markers → halt. Scope reject → halt. Stale artifacts → halt.

**3. Translate** (PIPELINE STAGE — Stage 2)
- Map BLIS signals to llm-d equivalents, classify branches (critical/non-critical), produce coverage report
- Inputs: Algorithm summary + mapping artifact
- Outputs: `transfer_workspace/signal_coverage.json` + `transfer_workspace/mapped_spec.md`
- Invariants: Branch classification deterministic; Low-fidelity critical → halt
- Failure modes: Unmappable signal → halt. Branch count mismatch → halt.

**4. Generate** (PIPELINE STAGE — Stage 3)
- LLM generates scorer plugin code, deployment configs, benchmark configs on llm-d branch
- Inputs: Mapped spec + scorer template + llm-d repo + workload parameters
- Outputs: Files on `blis-transfer/<name>` branch (scorer, tests, configs, benchmark configs)
- Side effects: Creates git branch, writes files
- Failure modes: LLM generates invalid code → retry via Unit Test feedback

**5. Unit Test** (PIPELINE STAGE — Stage 4)
- Build and test generated code; classify failures; feed errors back for retry
- Inputs: Generated files on branch
- Outputs: Pass/fail + error context
- Invariants: Pre-existing failures detected (package overlap check); stale mapping errors not retried
- Failure modes: New-code failure → retry (max 3x). Stale mapping → halt. Pre-existing → flag.

**6. Draft PR** (PIPELINE STAGE — Stage 5)
- Create draft PRs in scheduler and benchmark repos
- Inputs: Passing tests + generated files + signal coverage data
- Outputs: Draft PR URLs
- Invariants: Atomic per repo (benchmark failure → close scheduler PR)
- Failure modes: Auth/branch conflict → halt. Transient → retry once.

**7. Validation** (PIPELINE STAGE — Stages 6-7)
- Integration tests, 3-suite equivalence (A→B→C strict order), cluster benchmarks
- Inputs: Draft PR branch + cluster access
- Outputs: Per-suite results as PR comments + go/no-go verdict
- Side effects: Posts PR comments, deploys to cluster, restores baseline on failure
- Invariants: Suite A before B before C; borderline → halt for user; baseline restored on abort

**8. Promote** (PIPELINE STAGE — Stage 8)
- Evaluate go/no-go, promote or flag PR
- Inputs: All validation results + overrides.json
- Outputs: PR label changes
- Invariants: Provisional/inconclusive cannot auto-promote; overrides require documented reasoning

**9. Transfer Workspace** (ARTIFACT)
- Inter-stage file storage on `blis-transfer/<name>` branch
- Contains: `algorithm_summary.json`, `signal_coverage.json`, `mapped_spec.md`, `retry_log.json`, `decisions.json`, `overrides.json`, deployment configs, benchmark configs
- In `.gitignore`; persists for crash recovery and audit trail

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

- No PR created without passing unit tests (Stage 4 gates Stage 5)
- No PR promoted without passing equivalence + benchmark (Stage 8 gates on 6+7)
- Cluster baseline restored after any Stage 7 failure or abort
- Generated scorer disableable via config toggle without code removal
- Inter-stage artifacts validated by consuming stage before processing
- Low-fidelity critical signals halt pipeline (never silently proceed)

### Extension Points

| Extension | Cost | Files Changed | Notes |
|-----------|------|---------------|-------|
| New target system | High | ~3 new files in `docs/transfer/` + PR creator path | New mapping + template + prompt section |
| New validation suite | Medium | ~1 prompt section + 1 module method + timeout budget | Suite D (staleness+concurrency) is planned |
| New signal type | Low | 1 mapping artifact row + 1 test case | Lowest friction |

### External System Map

| Component | OpenEvolve | llm-d-scheduler | llm-d-benchmark | GitHub |
|-----------|:----------:|:----------------:|:---------------:|:------:|
| Extract | reads experiment | | | |
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
| R1 | LLM translates algorithms correctly | Claude can bridge BLIS↔llm-d gap | 3-suite equivalence (Stage 6) | 1 PR (re-run Stage 3) | Before PR5 |
| R2 | Scorer template reflects llm-d conventions | Template compiles against llm-d HEAD | Compilation + smoke test at Stage 1 | 2 PRs (PR1+PR2 recreate) | Before PR3 |
| R3 | Mapping artifact captures all needed signals | Signal categories from design doc complete | Stage 2 halt on unmappable signal | 2 PRs (PR1 rework) | Before PR3 |
| R4 | Single-run benchmarks meaningful | Noise CV < 3% | Noise characterization (5 baseline runs) | 1 PR (add multi-run) | Before PR6 |
| R5 | Equivalence threshold (0.8 Kendall-tau) appropriate | Threshold separates good from bad | Calibrate after first 3 transfers | 0 PRs (config change) | After PR7 |
| R6 | EVOLVE-BLOCK extraction handles all shapes | `parse_evolve_blocks` handles single-file blocks | Test with 3+ real experiment outputs | 1 PR (extend extraction) | Before PR3 |
| R7 | Provisional thresholds adequate for first transfer | Default values (5% improvement, 0.8 Kendall-tau) are reasonable starting points | Calibration transfer on known-good algorithm | 0 PRs (config change) | Before production use |
| R8 | BLIS workload generator faithfully reproduces BLIS workloads | Generator's synthetic traffic matches BLIS patterns | Compare cluster metrics under generator vs. BLIS simulation | 1 PR (fix generator) | Before PR6 |

### Cross-System Risk Categories

| Risk Category | Status | Mitigation |
|---------------|--------|------------|
| **API drift** | Mitigated | Mapping artifact pins llm-d commit; Stage 1 compiles template + runs smoke tests; commit distance check at 50 commits |
| **Version mismatch** | Mitigated | Mapping artifact "last verified against" commit hash; Stage 1 validates major version match for all 4 artifacts |
| **Schema evolution** | Mitigated | Inter-stage JSON schemas validated by consuming stages; artifact major version mismatch halts |
| **External availability** | Partially mitigated | Stage 7 failure leaves draft PR with "infra-retry" label; baseline restored before release |
| **Distributed partial failure** | Mitigated | PR creation atomic per repo; Stage 7 restores baseline; transfer workspace persists for recovery |
| **Mock fidelity** | Acknowledged | v1 tests against real repos for Stages 3-7; PR7 dry-run mocks derived from schemas |

---

## F) Implementation Evolution

**Starting point:** OpenEvolve has EVOLVE-BLOCK extraction (`code_utils.py:parse_evolve_blocks`), best program output (JSON with `metrics.workload`), per-workload YAML config files, experiment directories, and hypothesis framework. No transfer infrastructure exists. llm-d repos available as local clones. No `docs/transfer/` directory.

### Milestone 1: Foundation (after PR1 + PR2)

Prerequisites established. `docs/transfer/` populated with mapping artifact and scorer template. Pipeline can attempt extraction (Stage 1) against existing experiment output.

- **Testable:** Stage 1 reads experiment output (`best_program_info.json` with `metrics.workload` + per-workload YAML configs), extracts EVOLVE-BLOCK, validates scope, produces `algorithm_summary.json`.

### Milestone 2: Translation + Code Generation (after PR3 + PR4)

Pipeline runs Stages 1-5: extraction, translation, code generation, unit tests, PR creation. Draft PRs can be created.

- **Testable:** Run pipeline on real BLIS experiment. Verify: algorithm extracted, signals mapped, scorer generated, unit tests pass (including request parsing tests and disabled-scorer no-op test), draft PR created.
- **First real cross-repo interaction:** Stage 3 writes to llm-d repo, Stage 4 runs `go build` + `go test`.

### Milestone 3: Validation Pipeline (after PR5 + PR6)

Noise characterization established. Validation stages operational: integration tests, 3-suite equivalence, cluster benchmarks, promotion logic.

- **Testable:** Run equivalence tests (Suite A/B/C) against draft PR. Run cluster benchmark. Verify PR comments posted, go/no-go verdict produced.

### Milestone 4: End-to-End + Calibration (after PR7)

Full orchestration with crash recovery. **Calibration transfer** validates provisional thresholds on a known-good algorithm before the pipeline is used in production.

- **Testable:** Complete end-to-end transfer. Calibration transfer records threshold data in calibration log. All PR comments posted, audit trail complete.

---

## G) Stable API Reference

**Omitted for llm-d.** All llm-d behavioral references are UNVERIFIED (no repo access). Concrete Go interfaces documented in mapping artifact (PR1) after inspection.

**Confirmed stable in OpenEvolve:**

`openevolve/utils/code_utils.py:9-37` — `parse_evolve_blocks(code: str) → List[Tuple[int, int, str]]`
- Markers: `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` (or `//` for Go)
- Returns: `(start_line, end_line, block_content)` tuples
- Behavior: Line-based regex, multiple blocks, no nesting

`examples/blis_router/evaluator.py:168-192` — `_parse_cluster_metrics(output)` — extracts JSON metrics from Go simulator stdout/stderr. [CONFIRMED]

`examples/blis_router/hypothesis.py:35-100` — `parse_hypotheses(code)` — parses `HYPOTHESIS-N`, `MECHANISM-N`, `EXPECT-N` comments from EVOLVE-BLOCK. [CONFIRMED]

---

## H) Cross-Cutting Infrastructure Plan

### 1. Shared Test Infrastructure

- **PR1**: Creates `openevolve/transfer/` package stub (enables test imports for later PRs), `docs/transfer/schemas/` with JSON Schema files for `algorithm_summary` and `signal_coverage`, and `openevolve/transfer/schema_validator.py`
- **PR3**: Creates extraction test fixtures (3+ real experiment outputs with `metrics.workload` entries + per-workload YAML configs)
- **PR6**: Creates equivalence test harness template (JSON tuple format, BLIS evaluator adapter)

**External system mocking strategy:** v1 tests against real llm-d repos for Stages 3-7 (no mocks for core pipeline). PR7 dry-run test uses mocks derived from real artifact schemas for orchestration flow validation.

### 2. Documentation Maintenance

- **PR1**: Update CLAUDE.md with transfer pipeline section. Document `metrics.workload` and per-workload YAML config expectations.
- **Each PR**: Update `docs/transfer/README.md` with current pipeline status
- **PR1, PR2**: Artifact schema docs inline in artifact files
- **PR7**: CLAUDE.md final update with end-to-end usage and orchestrator invocation

### 3. CI Pipeline Changes

- **PR1**: Add `tests/test_transfer_schemas.py` — validates schema files parse correctly
- **PR3**: Add `tests/test_extraction.py`, `tests/test_translation.py`
- **PR7**: Add `tests/test_transfer_dry_run.py` — dry-run integration test
- No external access needed for CI (all tests use local fixtures or mocks)

### 4. Dependency Management

- No new Python package dependencies (stdlib `json`, `re`, `pathlib`)
- llm-d repos: version-pinned via mapping artifact commit hash
- Artifact version compatibility: major.minor scheme, pipeline checks major version at Stage 1
- BLIS workload generator: version pinned in mapping artifact; Stage 1 validates major version match

### 5. Cross-Repo Coordination

- **Ordering:** All OpenEvolve PRs merge independently. No cross-repo merge ordering constraints (llm-d PRs are created at runtime by the pipeline).
- **External prerequisite:** BLIS workload generator must be added to llm-d-benchmark before PR6 (validation). This is coordinated with llm-d-benchmark maintainers outside this PR series.
- **Branch naming:** Transfer branches in llm-d: `blis-transfer/<algorithm-name>`
- **Review ownership:** OpenEvolve PRs → OpenEvolve maintainers. Generated llm-d PRs → llm-d maintainers.
- **Cross-repo pre-merge checks for PR1/PR2:** Before merging, manually verify mapping artifact and scorer template against pinned llm-d HEAD. Documented in PR review checklists.

### 6. Artifact Lifecycle

- **Prerequisites before first transfer:** All 4 artifacts must exist (mapping, scorer template, transfer prompt, BLIS workload generator)
- **Created by:** PR1 (mapping + schemas), PR2 (scorer template), PR3/PR4 (transfer prompt stages), external (BLIS workload generator)
- **Maintained by:** Manual update when llm-d APIs change
- **Staleness detection:** Stage 1 performs compilation check + smoke test + commit distance check (automated per transfer)
- **Version scheme:** major.minor per artifact; pipeline checks major version match; mismatch halts Stage 1

---

## I) PR Plan

### PR1: Transfer infrastructure + mapping artifact

**Target Repo:** openevolve
**Component Change:** Transfer Workspace, Translate (enables signal mapping)
**PR Type:** infrastructure + artifact
**Motivation:** Establish `docs/transfer/` directory, JSON schemas, validation utilities, the `openevolve/transfer/` package stub, and the mapping artifact — the single source of truth for BLIS↔llm-d signal correspondences. Also documents the per-workload YAML config schema used by the BLIS workload generator.

**Scope:**
- In: `docs/transfer/` directory, `docs/transfer/schemas/` with JSON Schema files for `algorithm_summary` (referencing `metrics.workload` and per-workload YAML config structure) and `signal_coverage`, `openevolve/transfer/__init__.py` + `schema_validator.py`, `docs/transfer/blis_to_llmd_mapping.md` (complete signal mapping, interface list, staleness windows, version pin, BLIS workload generator config schema, per-workload YAML config schema), CLAUDE.md transfer section, `tests/test_transfer_schemas.py`
- Out: No pipeline logic, no scorer template, no prompt template

**Behavioral Guarantees:** Schema files parse correctly. Validator accepts valid JSON, rejects invalid. Mapping artifact contains at least one complete signal entry with all fields. "Last verified against" commit is current llm-d HEAD. Version 1.0. Algorithm summary schema references `metrics.workload` structure and per-workload YAML config files.
**Risks:** R3 (mapping completeness). Mitigation: Stage 2 halts on unmappable signals; iterate after first transfer.
**Cross-Cutting:** Creates shared infra consumed by all later PRs (including `openevolve/transfer/` package stub for test imports). Mapping artifact consumed by PR3.
**Validation Gate:** R2 and R3 require mapping accuracy.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/` package, new `docs/transfer/` directory tree
- Requires: Reading llm-d-inference-scheduler repo to document scorer interface, signal access, config format
- Test categories: Unit tests (schema validation, mapping artifact format)
- Documentation: CLAUDE.md transfer section, docs/transfer/README.md
- Extension friction: Adding new artifact schema = 1 JSON Schema file + 1 test
- Cross-repo impact: References llm-d types/APIs (described behaviorally)
- Independently reviewable: Self-contained infrastructure + documentation
- No dead code: Schema validator exercised by tests; mapping consumed by PR3; transfer package stub consumed by later PRs

---

### PR2: Scorer template artifact

**Target Repo:** openevolve
**Component Change:** Generate (enables code generation)
**PR Type:** artifact
**Motivation:** Shows Claude Code the target code structure and plugin conventions. Required for Stage 3 code generation.

**Scope:**
- In: `docs/transfer/scorer_template.go.md` — annotated scorer with plugin lifecycle, test structure, config registration, disabled-scorer no-op test, P/D guard, request parsing example. Version 1.0.
- Out: No code changes

**Behavioral Guarantees:** Template compiles and passes unit tests against pinned llm-d HEAD. Includes: disabled-scorer no-op test, P/D scheduling context guard returning neutral score, request parsing from documented transport format. Version 1.0.
**Risks:** R2 (template accuracy). Mitigation: Stage 1 compiles template before each transfer.
**Cross-Cutting:** Consumed by PR4 (Stage 3 code generation).
**Validation Gate:** R2 validated before PR3.

**Implementation Guide:**
- Requires: Reading llm-d repo to identify well-structured existing scorer as basis
- Test categories: Manual verification (compile template against llm-d HEAD)
- Extension friction: New target system = new template file
- Cross-repo impact: References llm-d code patterns (described behaviorally)
- Independently reviewable: Documentation artifact
- No dead code: Consumed by Stage 3 in PR4

---

### PR3: Transfer prompt — extraction + translation (Stages 1-2)

**Target Repo:** openevolve
**Component Change:** Extract, Translate
**PR Type:** pipeline-stage
**Motivation:** Implements the first two pipeline stages: reading an experiment (including `metrics.workload` and per-workload YAML configs), extracting the EVOLVE-BLOCK, mapping signals, classifying branches, producing signal coverage report.

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 1-2), `openevolve/transfer/extract.py` (extraction utilities extending `parse_evolve_blocks`, scope validation, prerequisite checks including BLIS workload generator version validation, reading `metrics.workload` from `best_program_info.json` + per-workload YAML configs), `openevolve/transfer/translate.py` (signal mapping, branch classification, workload generator parameter validation), test fixtures (3+ real experiment outputs with `metrics.workload` entries + per-workload YAML configs), `tests/test_extraction.py`, `tests/test_translation.py`
- Out: Stages 3-8, no PR creation, no validation

**Behavioral Guarantees:** Stage 1 reads `best_program_info.json` (including `metrics.workload`) and per-workload YAML config files, extracts EVOLVE-BLOCK, validates scope (pass/marginal/reject), checks all 4 prerequisites (artifacts exist, noise data current, template compiles, BLIS workload generator version compatible). Stage 1 validates that at least one workload entry exists in `metrics.workload` and that a corresponding YAML config file exists (per design doc). Stage 2 produces valid `signal_coverage.json` and `mapped_spec.md`. Branch classification deterministic. Low-fidelity critical → halt. Workload generator parameters validated against schema.
**Risks:** R6 (extraction handles all shapes). Mitigation: Test with 3+ real experiments.
**Cross-Cutting:** Consumes PR1 (schemas + mapping artifact). Creates extraction fixtures.
**Validation Gate:** R6 validated by extraction tests.

**Implementation Guide:**
- Architectural impact: New `openevolve/transfer/extract.py`, `translate.py`
- API surface: `extract_algorithm(experiment_path) → AlgorithmSummary`, `translate_signals(summary, mapping) → SignalCoverage` (described behaviorally). Extraction reads `metrics.workload` and per-workload YAML configs.
- Test categories: Unit tests (extraction, scope validation, prerequisite checks, branch classification, workload parameter validation, `metrics.workload` parsing, YAML config reading), integration tests (real experiment outputs)
- Documentation: Transfer prompt Stages 1-2 with worked examples of branch classification
- Extension friction: New signal type = 1 mapping row + 1 test case
- Cross-repo impact: None (reads local experiment output + mapping artifact)
- Independently reviewable: Stages 1-2 produce verifiable artifacts without llm-d access
- No dead code: Both extraction and translation exercised by tests and consumed by PR4

---

### PR4: Transfer prompt — generation + testing + PR creation (Stages 3-5)

**Target Repo:** openevolve
**Component Change:** Generate, Unit Test, Draft PR
**PR Type:** pipeline-stage
**Motivation:** Implements code generation from mapped spec, unit test execution with retry logic, and draft PR creation. Includes request parsing unit tests (addressing validation strategy CRITICAL finding).

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 3-5), `openevolve/transfer/generate.py` (LLM code generation, deployment config generation, benchmark config generation using BLIS workload generator), `openevolve/transfer/test_runner.py` (go build+test, error classification — stale-mapping/environment/retryable, retry management, identical error detection), `openevolve/transfer/pr_creator.py` (draft PRs via `gh` CLI, atomic per-repo creation), tests
- Out: Stages 6-8, no validation, no benchmarks

**Behavioral Guarantees:** Stage 3 generates scorer source, tests (including request parsing test and disabled-scorer no-op test), scoring config, baseline/treatment deployment configs, benchmark configs (one per BLIS workload using BLIS workload generator). Stage 4 retry loop stops on identical consecutive errors (byte-for-byte first 200 chars after whitespace normalization). Stage 5 PR creation atomic (benchmark failure → close scheduler PR). Crash recovery detects existing branch. Retry log written to `transfer_workspace/retry_log.json`.
**Risks:** R1 (LLM translation quality). Mitigation: Retry loop with error feedback; equivalence testing in PR6.
**Cross-Cutting:** Consumes PR1 (schemas), PR2 (scorer template), PR3 (Stage 1-2 outputs).
**Validation Gate:** None (validation in PR6).

**Implementation Guide:**
- Architectural impact: New `generate.py`, `test_runner.py`, `pr_creator.py`
- Test categories: Unit tests (error classification, retry logic with 3-identical-failure exhaustion, retry log format), mock-based tests (mocked `gh` CLI, mocked `go test`, atomic PR rollback)
- Documentation: Transfer prompt Stages 3-5
- Extension friction: New target repo = new PR creation path
- Cross-repo impact: Writes to llm-d repo (branch, files, PRs)
- Independently reviewable: Testable with mocked external dependencies
- No dead code: All modules exercised by tests and consumed by PR6

---

### PR5: Noise characterization workflow

**Target Repo:** openevolve
**Component Change:** Validation (prerequisite for Stage 7)
**PR Type:** infrastructure
**Motivation:** Noise characterization is a Stage 7 prerequisite without which benchmark thresholds are meaningless (addresses validation strategy CRITICAL #3). Must be established before any cluster benchmark runs.

**Scope:**
- In: `docs/transfer/noise_characterization.md` (template with cluster config fingerprint, date, per-metric CV, derived thresholds), `openevolve/transfer/noise.py` (utilities to run baseline benchmarks, compute CV, validate currency — date <30 days + fingerprint match), `tests/test_noise.py`
- Out: No pipeline stages, no equivalence tests

**Behavioral Guarantees:** Noise characterization produces per-metric CV values (TTFT mean/P95, E2E mean/P95, throughput). Derived improvement threshold = 2× max CV. Currency validation checks date (<30 days) and cluster config fingerprint. If CV > 3%, pipeline halts Stage 7 with diagnostic: "Noise CV {value}% exceeds 3% — default 5% improvement threshold is not statistically meaningful. Either re-run noise characterization on a quieter cluster window or raise improvement threshold to 2× max CV ({computed}%)." User may override with documented justification in overrides.json.
**Risks:** R4 (single-run meaningfulness). Mitigation: This PR establishes the measurement baseline.
**Cross-Cutting:** Consumed by PR6 (Stage 7 pre-flight) and PR3 (Stage 1 noise data check).
**Validation Gate:** R4 validated by noise characterization results.

**Implementation Guide:**
- Architectural impact: New `noise.py`, new template in `docs/transfer/`
- Test categories: Unit tests (CV computation, currency validation, fingerprint comparison)
- Documentation: Noise characterization procedure in `docs/transfer/README.md`
- Extension friction: N/A — one-time setup per cluster config
- Cross-repo impact: Runs benchmarks on llm-d-benchmark cluster (read-only measurement)
- Independently reviewable: Self-contained measurement infrastructure
- No dead code: Consumed by Stage 1 and Stage 7

---

### PR6: Validation — equivalence testing + benchmarks + promotion (Stages 6-8)

**Target Repo:** openevolve
**Component Change:** Validation, Promote
**PR Type:** validation + orchestration
**Motivation:** Implements multi-tier validation: integration tests, 3-suite equivalence (A/B/C), cluster benchmarks with mechanism check, and promotion logic. Includes calibration log template.

**Scope:**
- In: `docs/transfer/transfer_prompt.md` (Stages 6-8), `openevolve/transfer/equivalence.py` (test harness, tuple generation with 200-cap + threshold priority, Suite A/B/C logic, borderline detection, signal upgrade validation), `openevolve/transfer/benchmark.py` (deployment config management, workload execution via BLIS workload generator configs, go/no-go criteria, mechanism check decision tree, improvement magnitude comparison, pre-flight re-validation, baseline restoration on abort), `openevolve/transfer/promote.py` (promotion logic, label management, override detection from overrides.json), `docs/transfer/calibration_log.md` (template), tests
- Out: End-to-end orchestration (PR7)

**Behavioral Guarantees:**
- Suite ordering: A→B→C strictly enforced. Borderline results halt for user with 30-min human timeout.
- Suite A: Both numeric fidelity (epsilon 1e-6 absolute OR 1% relative) AND rank correlation (Kendall-tau > 0.8) must pass. Tuples: Cartesian product capped at 200, threshold boundaries prioritized.
- Suite B: Rank stability (Kendall-tau > 0.7) AND threshold-crossing stability (<20% boundary changes). Per-source-group correlated staleness injection, 3 repetitions.
- Suite C: Binary pass/fail — no panics, deterministic, pile-on ≤ 2× fair share.
- Stage 7: Mechanism check decision tree (primary criterion → confounding check → specificity). Baseline restored on abort within 5 min. Health check aborts on >10% error rate in first 60s.
- Stage 8: Promotion requires all stages pass with no unresolved borderlines. Provisional results tagged. Override audit trail in overrides.json.
- All thresholds tagged **provisional** in PR comments until calibrated.

**Risks:** R5 (threshold calibration), R7 (provisional thresholds), R8 (workload generator fidelity).
**Cross-Cutting:** Consumes all prior PRs. Creates calibration log template.
**Validation Gate:** R4 (noise characterization from PR5) validated before first real transfer.

**Implementation Guide:**
- Architectural impact: New `equivalence.py`, `benchmark.py`, `promote.py`
- Test categories: Unit tests (tuple generation, go/no-go logic, mechanism check, suite ordering enforcement, borderline detection, promotion criteria), mock-based integration tests (mock cluster responses, timeout, infra-retry path, baseline restoration)
- Documentation: Transfer prompt Stages 6-8, diagnostic guide
- Extension friction: New validation suite = 1 module + prompt section + timeout budget
- Cross-repo impact: Runs tests against llm-d, deploys to cluster, posts PR comments
- Independently reviewable: Validation logic testable with mock data
- No dead code: All modules exercised by tests and consumed by PR7

---

### PR7: End-to-end orchestration + calibration transfer

**Target Repo:** openevolve
**Component Change:** Orchestrator
**PR Type:** orchestration
**Motivation:** Ties all stages together. Includes crash recovery, user interaction checkpoints, and a dry-run integration test. Establishes calibration transfer workflow to validate provisional thresholds.

**Scope:**
- In: `openevolve/transfer/orchestrator.py` (stage sequencing, retry dispatch, 4 user interaction checkpoints, crash recovery via branch+workspace detection, P/D disaggregation detection+prompt, timeout management), `tests/test_transfer_dry_run.py` (end-to-end with mocked externals — validates orchestration flow, crash recovery, user checkpoint prompts), `docs/transfer/README.md` finalization, CLAUDE.md final update
- Out: No new pipeline stages

**Behavioral Guarantees:** Orchestrator runs all 8 stages strictly in sequence. Crash recovery: detects existing `blis-transfer/<name>` branch + `transfer_workspace/`, offers resume or restart. User checkpoints at: (1) Stage 2 signal coverage review, (2) Stage 5 generated code review, (3) Stage 7 borderline results, (4) Stage 8 promotion verdict. P/D disaggregation detected and prompted before Stage 2. Stage timeouts enforced (Stage 6: 30 min active, Stage 7: 60 min total). Dry-run test validates full flow.

**Calibration transfer workflow:** Pipeline maintainer runs calibration within 7 days of PR7 merge on a known-good algorithm (one where BLIS improvement is well-established). **Success criteria:** (a) all 3 equivalence suites pass, (b) observed Kendall-tau within ±10% of provisional 0.8 threshold, (c) cluster benchmark improvement magnitude consistent with BLIS evaluator results (within 2× noise CV). Record threshold data in calibration log. If any criterion fails, adjust thresholds (config change, 0 PRs) and re-run calibration before production use. Thresholds graduate from "provisional" to "production" upon successful calibration.

**Risks:** Mock fidelity for dry-run. Mitigation: Mocks derived from real artifact schemas.
**Cross-Cutting:** Consumes all prior PRs. Finalizes documentation.
**Validation Gate:** R7 (calibration transfer validates thresholds).

**Implementation Guide:**
- Architectural impact: New `orchestrator.py`
- Test categories: Integration test (dry-run with mocked externals, all 8 stages, crash recovery path, checkpoint prompts)
- Documentation: `docs/transfer/README.md`, CLAUDE.md, diagnostic guide
- Extension friction: N/A — orchestrator calls stages via defined interfaces
- Cross-repo impact: Orchestrates all cross-repo operations via prior PR modules
- Independently reviewable: Orchestrator + dry-run test
- No dead code: Orchestrator exercised by dry-run test

---

## J) Dependency DAG

### PR Dependencies

```
[PR1 openevolve] ─→ [PR3 openevolve] ─→ [PR4 openevolve] ─→ [PR6 openevolve] ─→ [PR7 openevolve]
       │                                       ↑                    ↑
       └──→ [PR2 openevolve] ─────────────────┘                    │
                                                                    │
                                              [PR5 openevolve] ────┘
```

### Cross-Repo Visualization

llm-d PRs are created **at runtime** by the pipeline (Stage 5), not as part of this PR series:

```
[PR1-PR3 openevolve]  →  [PR4 openevolve]  →  [PR6 openevolve]  →  [PR7 openevolve]
  (merged)                (code gen ready)     (validation ready)    (orchestration)
                               │                      │
                               ↓ (runtime)             ↓ (runtime)
                          [Draft PR: llm-d-scheduler]  [Validation runs against
                          [Draft PR: llm-d-benchmark]   llm-d draft PRs]

External prerequisite (not in this PR series):
[BLIS workload generator → llm-d-benchmark]  ← must exist before PR6 validation
```

### Ordering Constraints

- PR1 and PR2 can be developed **in parallel** (no dependency on each other)
- PR3 requires PR1 (mapping artifact for translation)
- PR4 requires PR2 and PR3 (scorer template + extraction outputs)
- PR5 can be developed **in parallel** with PR1/PR2/PR3/PR4 (noise characterization is independent)
- PR6 requires PR4 and PR5 (needs code generation + noise characterization)
- PR7 requires PR6 (all stages needed for orchestration)
- External: BLIS workload generator must exist in llm-d-benchmark before PR6 validation runs

### Critical Path

PR1 → PR3 → PR4 → PR6 → PR7

### Parallelizable Workstreams

- **Workstream A:** PR1 (mapping artifact) → PR3 (extraction + translation)
- **Workstream B:** PR2 (scorer template) — joins at PR4
- **Workstream C:** PR5 (noise characterization) — joins at PR6
- **Workstream D:** BLIS workload generator (external) — must complete before PR6

### Validation Gate Placement

- R2 + R3: Validated before PR3 begins (mapping + template accuracy)
- R4: Validated by PR5 (noise characterization) before PR6
- R6: Validated by PR3 extraction tests
- R7: Validated after PR7 (calibration transfer)
- R5: Validated post-launch after 3 transfers (calibration log)

### Integration Risk Notes

- **PR4 highest risk:** First cross-repo interaction at runtime (creates branches in llm-d)
- **PR6 second highest:** Deploys to cluster (production-adjacent)
- **BLIS workload generator:** External dependency — if delayed, PR6 validation cannot run

---

## K) Design Bug Prevention Checklist

### General

| Failure Mode | Prevention | Verified In |
|-------------|------------|-------------|
| **Scaffolding creep** | Every file, function, config exercised by tests in introducing PR | Each PR's test suite |
| **Documentation drift** | CLAUDE.md and README updated in the PR that causes the change | PR1 (initial), PR7 (final) |
| **Test infrastructure duplication** | Schema validation utility in PR1, consumed by all later PRs | PR1 |

### Cross-System-Specific

| Failure Mode | Prevention | Verified In |
|-------------|------------|-------------|
| **API contract drift** | Mapping artifact pins llm-d commit; Stage 1 compiles template + runs smoke tests | PR3 (Stage 1 prerequisite checks) |
| **Mock divergence** | v1 tests against real llm-d repos for Stages 3-7; PR7 dry-run mocks derived from schemas | PR7 |
| **Distributed partial failure** | PR creation atomic; Stage 7 restores baseline; transfer workspace persists for recovery | PR4 (atomic PR), PR6 (baseline restore) |
| **Artifact staleness** | Stage 1: (1) compile template, (2) run template tests, (3) commit distance <50, (4) BLIS workload generator version match. All must pass. | PR3 (Stage 1 checks) |
| **Experiment artifact availability** | Stage 1 validates `metrics.workload` exists in `best_program_info.json` and corresponding per-workload YAML configs are present | PR3 (Stage 1 validation) |
| **Cross-repo merge ordering** | All OpenEvolve PRs merge independently; llm-d PRs created per-transfer at runtime | Dependency DAG |
| **External system evolution** | major.minor version scheme; pipeline checks major version for all 4 artifacts | PR3 (version check) |
| **Request parsing fidelity gap** | Stage 4 unit tests include request parsing test case; diagnostic guide checks parsing as Stage 7 failure root cause | PR4 (parsing tests) |
| **Unvalidated thresholds** | All thresholds tagged provisional until calibration transfer (PR7); calibration log tracks empirical results | PR7 (calibration) |
| **BLIS workload generator drift** | Generator version pinned in mapping artifact; Stage 1 validates major version; Stage 7 pre-flight re-validates availability | PR3 (version check), PR6 (pre-flight) |

### Invariants (must never break)

1. No PR created without passing unit tests (Stage 4 gates Stage 5)
2. No PR promoted without passing equivalence + benchmark (Stage 8 gates on 6+7)
3. Cluster baseline restored after any Stage 7 failure (within 5 min or manual alert)
4. Generated scorer disableable via config toggle without code removal
5. Inter-stage JSON artifacts validated by consuming stage
6. Retry loop stops on identical consecutive errors (3x max)
7. Low-fidelity critical signals halt pipeline (never silently proceed)
8. All equivalence verdicts tagged **provisional** until calibrated

### Regression Surfaces

- Existing OpenEvolve tests (`python -m unittest discover tests`) must continue passing
- `openevolve/utils/code_utils.py:parse_evolve_blocks` behavior unchanged (PR3 extends, not modifies)
- No changes to `openevolve/evaluator.py` or `openevolve/controller.py` (core framework untouched)
- No changes to BLIS evaluator (`examples/blis_router/evaluator.py`) — pipeline reads existing `metrics.workload` output
