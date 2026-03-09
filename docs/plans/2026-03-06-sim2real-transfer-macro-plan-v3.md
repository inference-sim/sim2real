# Sim-to-Production Algorithm Transfer Pipeline — Macro Plan v3

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Date:** 2026-03-06
**Status:** Draft
**Design Doc:** `docs/plans/2026-03-06-sim2real-transfer-design-v3.md`
**Template:** Cross-system macro plan (`docs/contributing/templates/macro-plan-cross-system.md`)
**Supersedes:** `docs/plans/2026-03-02-blis-to-llmd-transfer-macro-plan-v2.md`

### Revision History

| Date | Change | Reason |
|------|--------|--------|
| 2026-03-06 | v3 rewrite | Re-home pipeline from OpenEvolve to sim2real. Prompt-driven architecture replaces Python modules. 8 stages → 6. All PRs target sim2real. Work locally in submodules; PRs to target repos created at runtime by pipeline. |

---

## A) Executive Summary

This plan decomposes the sim2real transfer pipeline into an ordered PR series. The pipeline takes a simulation-discovered routing algorithm (from inference-sim), translates it into a production scorer plugin for `llm-d-inference-scheduler`, validates equivalence through 3-suite testing + cluster benchmarks, and creates PRs as the final step.

**Architecture:** Prompt-driven — Markdown prompt templates (`prompts/`) guide an interactive Claude Code session through 6 stages. A thin Python CLI (`tools/transfer_cli.py`) handles mechanical tasks. A Go test harness (`tools/harness/`) runs equivalence tests against inference-sim.

**Systems:** sim2real (host — prompts, tools, artifacts), llm-d-inference-scheduler (target — scorer plugin, submodule), llm-d-benchmark (target — benchmark configs, submodule), inference-sim (source — simulation environment, submodule).

**PR count:** 6 PRs, all in sim2real.

**Milestones:** (1) After PR2: prerequisite artifacts exist, extraction works. (2) After PR4: pipeline runs Stages 1-4 end-to-end. (3) After PR5: full validation operational. (4) After PR6: pipeline self-verified and calibrated.

---

## B) Multi-System Recon Summary

### HOST: sim2real

- **Repository structure:** Bare repo with 3 submodules (`inference-sim/`, `llm-d-inference-scheduler/`, `llm-d-benchmark/`), input artifacts (`routing/`), design docs (`docs/plans/`). No pipeline code exists yet. [CONFIRMED: repo root listing]
- **Input artifacts:** `routing/best_program.py` (11KB, EVOLVE-BLOCK markers), `routing/best_program_info.json` (1.7KB, metrics + per-workload results), `routing/workload_v2_*.yaml` (3 files, traffic patterns). [CONFIRMED: routing/ listing]
- **Directories to create:** `prompts/`, `tools/`, `tools/harness/`, `docs/transfer/`, `workspace/`
- **.gitignore:** Does not yet exclude `workspace/`. [CONFIRMED: .gitignore]

### SOURCE: inference-sim (submodule)

- **Routing types:** `sim/routing.go` — `RoutingSnapshot`, `Request`, `RouterState`, `WeightedScoring` with `Route()` method. [CONFIRMED from earlier exploration]
- **5 routing signals (from evolved algorithm, per design doc):** (1) queue depth per endpoint, (2) in-flight request count, (3) estimated latency, (4) request token count, (5) KV cache utilization. All are approximate-scorer signals in v1 (staleness_window_ms = 0). **Note:** This is the design doc's initial signal list. The PR1 `extract` command produces the authoritative list from the actual EVOLVE-BLOCK. If the extracted list differs, this section and the mapping artifact are updated within PR1 (see R3 validation sequence in Risk Register).
- **LoadEvolvedBlock API:** UNVERIFIED — required for Stage 5 equivalence testing (Go harness must instantiate and run evolved algorithms). Must be verified or created before PR4.
- **Go module:** Importable as a Go dependency from `tools/harness/`.

### TARGET: llm-d-inference-scheduler (submodule)

- **Scorer interface:** `scheduling.Scorer` with `Score()`, `TypedName()`, `Category()` methods; factory registration pattern. [CONFIRMED from earlier exploration: `pkg/scheduling/scorer.go`]
- **LoadAware scorer:** Simplest template scorer — reads queue depth, in-flight requests. [CONFIRMED: `pkg/scheduling/plugins/`]
- **Build/test:** Go module with `go build ./...` and `go test ./...`. [CONFIRMED]
- **Config:** YAML-based scoring config with scorer weights. [INFERRED from scorer interface]

### TARGET: llm-d-benchmark (submodule)

- **Benchmark profiles:** Config files defining workload patterns. [CONFIRMED from earlier exploration]
- **Benchmark tool:** The llm-d-benchmark repository's CLI tool for running workloads against a deployed scheduler. **Identity:** UNVERIFIED — must be confirmed during PR5 implementation. The tool is expected to be a Go or Python CLI in the llm-d-benchmark submodule root (e.g., `cmd/benchmark/main.go` or `benchmark.py`). **Interface assumption:** accepts a benchmark profile config (YAML) and cluster endpoint, outputs per-workload latency metrics (P50, P95, P99) in JSON or structured text. **Resolution:** PR5 implementer must: (1) inspect the llm-d-benchmark submodule to identify the actual CLI tool, its location, and invocation syntax, (2) document the tool identity and interface in `docs/transfer/README.md`, (3) implement the `transfer_cli.py benchmark` wrapper to invoke it via `subprocess`. If no suitable CLI exists in llm-d-benchmark, PR5 must either implement a minimal benchmark runner or document the gap as a blocker. [INFERRED — requires PR5 verification]

### Open Uncertainties

1. inference-sim `LoadEvolvedBlock` API existence — UNVERIFIED (must resolve during PR1; PR1 cannot merge without resolution — see R6 in Risk Register)
2. Hot config reload support in llm-d-inference-scheduler — UNVERIFIED
3. llm-d-benchmark CLI interface for benchmark execution — UNVERIFIED (must be resolved during PR5 implementation; `transfer_cli.py benchmark` cannot be implemented without identifying the tool — see Section B llm-d-benchmark and R9)
4. Exact scorer registration and config file format — partially verified

---

## C) High-Level Objectives + Non-Goals + Scoping Table

### Analysis Questions (from design doc)

- **Q1: Does the benefit survive the abstraction gap?** — Validated by Suite A/B/C equivalence + cluster benchmarks
- **Q2: Which signals have production equivalents?** — Validated by signal coverage report + Suite A per-signal analysis
- **Q3: Minimum fidelity for transfer?** — Deferred to v2+ (calibration log accumulates data)

### Objectives

1. **Transfer one algorithm:** Take a simulation-discovered EVOLVE-BLOCK and produce a tested, PR-ready llm-d scorer plugin — answers Q1/Q2. **Pass criteria:** Generated scorer compiles, passes `go test ./...` in llm-d-inference-scheduler, and has a `gh pr create`-ready branch.
2. **Validate equivalence:** 3-suite testing (numeric fidelity + rank correlation + staleness + concurrency) — answers Q1. **v1 note:** For approximate-scorer signals (all v1 signals), Suite B passes trivially (`staleness_window_ms = 0`). Suite B **runs** in v1 (exercising the infrastructure and recording results in `validation_results.json`) but its results are **informational only** — v1 go/no-go criteria rely on Suite A + Suite C + mechanism check. Suite B infrastructure is built and tested in v1 to ensure readiness for future precise-scorer transfers.
3. **Validate production benefit:** Cluster benchmarks with mechanism check — answers Q1. **Pass criteria:** Matched workload improvement >= T_eff (aggregation: AT LEAST ONE matched workload must show improvement >= T_eff; if multiple matched workloads exist, the mechanism check passes if any one demonstrates the expected benefit), specificity check passes, mechanism check verdict is PASS. **Definitions:** *Matched workload* = benchmark workload whose traffic pattern exercises the routing signals targeted by the evolved algorithm (identified from `routing/workload_v2_*.yaml`). **Classification procedure:** Stage 1 extracts the signal list from the evolved algorithm (stored in `algorithm_summary.json` `signals[]`). Stage 2 maps each signal to its production equivalent (stored in `signal_coverage.json`). A benchmark workload is *matched* if it generates request patterns that cause variation in at least one mapped signal — determined by checking whether the workload's traffic pattern (from `routing/workload_v2_*.yaml`) includes request types that exercise the signal's access path. **Classification procedure:** For each benchmark workload, the operator checks each mapped signal from `signal_coverage.json` and answers: "Does this workload's traffic pattern vary this signal across requests?" A workload is *matched* if the answer is YES for at least one signal. **Objective criteria per signal:** `queue_depth` — workload sends concurrent requests (concurrency > 1); `in_flight_count` — workload has variable request arrival rates; `estimated_latency` — workload includes requests with different token counts; `request_token_count` — workload includes requests with different prompt lengths; `kv_cache_utilization` — workload includes requests with different context lengths or repeated prompts. **Recording:** The `validate.md` prompt instructs the operator to classify each benchmark workload as matched/unmatched before running benchmarks, recording the classification, the matched signal(s), and rationale in `validation_results.json` `benchmark.workload_classification[]`. *Improvement* = relative change in P99 latency (lower is better): `(baseline_p99 - transfer_p99) / baseline_p99`. *Specificity check* = for each unmatched workload in the benchmark suite, verify that `|baseline_p99 - transfer_p99| / baseline_p99 < T_eff` (i.e., performance change is within the noise threshold). The specificity check passes if all unmatched workloads satisfy this criterion.
4. **Enable rollback:** Generated scorer disableable via config toggle. **Pass criteria:** Config toggle named `scoring.plugins.<scorer_name>.enabled` exists; setting to `false` causes scheduler to skip the generated scorer; verified by unit test in generated code. **Implementation pattern:** The config toggle is a YAML field in the scheduler's scoring config file (same format as existing scorer configs). When `enabled: false`, the scorer's `Score()` method returns `nil` scores (all zeros), causing the scheduler to fall back to remaining active scorers — the generated scorer does not modify shared state or affect other scorers. The toggle is read at scorer initialization (not hot-reloadable in v1; requires scheduler restart). PR2 documents this pattern in the scorer template with an annotated example showing: (a) the config struct field, (b) the initialization check, (c) the `Score()` early-return, and (d) the unit test that verifies disabled behavior returns nil scores. Stage 3 prompt (PR3) instructs the LLM to generate both the toggle implementation and a unit test verifying disable behavior. Stage 4 (PR4) runs the toggle unit test as part of `go test ./...`.
5. **Establish calibration baseline:** First transfer validates provisional thresholds. **Pass criteria:** Calibration log entry recorded with all suite results, noise CV, and threshold data; provisional thresholds (Kendall-tau 0.8, rank stability 0.7) either confirmed or adjusted with rationale. **First-transfer behavior:** The first transfer CAN fail on provisional thresholds — a failure is a valid calibration data point. If it fails, the operator follows the R5 pre-calibration failure procedure (record, investigate, adjust if threshold-related, re-run). The "provisional tag" means that single-run results have lower statistical confidence than multi-run results; it does NOT mean the transfer automatically passes. The tag is recorded as `single_run_provisional: true` in the calibration log entry (see calibration log schema below).

### Non-Goals

- Multi-algorithm batch transfer
- Bidirectional transfer (production → simulation)
- CI auto-trigger
- P/D disaggregation-aware transfer
- Algorithms using Low-fidelity signals

### Scoping Table

| Capability | v1 | Deferred | Out of Scope | Justification |
|------------|:--:|:--------:|:------------:|---------------|
| Single algorithm transfer | X | | | Core use case |
| Prompt-driven pipeline (6 stages) | X | | | v3 architecture |
| Thin CLI tools | X | | | Mechanical support |
| Go test harness (inference-sim) | X | | | Equivalence testing |
| 3-suite equivalence testing | X | | | Answers Q1 |
| Cluster benchmarks + mechanism check | X | | | Answers Q1 at production level |
| Noise characterization | X | | | Required for meaningful benchmarks |
| Calibration transfer | X | | | Validates provisional thresholds |
| Multi-algorithm batch | | X | | Config merge complexity |
| Statistical significance (multi-run) | | X | | Single-run transfer validation with provisional tag for v1 (recorded as `single_run_provisional: true` in calibration log). Note: noise characterization uses 5 baseline runs to establish measurement variance, but the actual transfer validation (go/no-go) is single-run. |
| Suite D (staleness+concurrency) | | X | | Independent testing sufficient |
| Non-routing transfer types | | X | | Architecture supports; per-type work requires: (1) new mapping artifact for the transfer type's signals, (2) new/adapted prompt templates for extraction and generation, (3) harness extensions for type-specific equivalence testing, (4) new benchmark configs for the workload type |
| P/D disaggregation | | | X | Requires simulation extension |
| Bidirectional transfer | | | X | Different pipeline entirely |

---

## D) Component Model

### Components (7)

**1. Prompt Templates** (ARTIFACT — `prompts/`)
- LLM instructions for each pipeline stage + top-level orchestrator
- Inputs: Design doc, mapping artifact, scorer template references
- Outputs: Interactive Claude Code guidance through 6 stages
- **LLM error handling (cross-cutting):** Each prompt template includes a "Validation" section that specifies how to verify the LLM's output before proceeding. If the LLM produces invalid output (e.g., malformed JSON, incomplete code, refusal), the pipeline is interactive — the operator can re-prompt or manually correct. Stage 4 has formal retry logic (error classification + retry limits); Stages 1-3 and 5-6 rely on the operator's judgment since the pipeline is an interactive Claude Code session. Each prompt template instructs: (1) validate output format before writing to workspace artifacts, (2) if output is invalid, re-prompt with the specific validation error, (3) if re-prompt fails, halt and present the operator with the invalid output for manual intervention.

**2. CLI Tools** (INTEGRATION POINT — `tools/transfer_cli.py`)
- Mechanical support: artifact parsing, mapping validation, noise characterization, benchmark execution
- Commands: `extract`, `validate-mapping`, `noise-characterize`, `benchmark`
- Invariant: All commands output JSON; exit codes: 0 = success, 1 = validation failure (pipeline should halt), 2 = infrastructure error (retry or escalate). **Per-command JSON output schemas:** `extract` → `{"status": "ok"|"error", "artifact_path": string, "algorithm_name": string, "signal_count": int, "errors": string[]}`. `validate-mapping` → `{"status": "ok"|"error", "mapping_complete": bool, "missing_signals": string[], "stale_commit": bool, "errors": string[]}`. `validate-schema` → `{"status": "ok"|"error", "schema_path": string, "artifact_path": string, "violations": {"field": string, "expected": string, "actual": string}[]}`. `noise-characterize` → `{"status": "ok"|"error", "per_metric_cv": {string: float}, "t_eff": float, "halt": bool, "errors": string[]}`. `benchmark` → `{"status": "ok"|"error", "results": {"workload": string, "p99_latency": float, "classification": "matched"|"unmatched"}[], "errors": string[]}`. `test-status` → `{"status": "ok"|"error", "error_class": string, "error_count": int, "errors": {"class": string, "message": string, "file": string}[]}`
- Failure modes: validation failure → halt pipeline with diagnostic message; infrastructure error → prompt user for retry; malformed input → halt with parse error details

**3. Go Test Harness** (INTEGRATION POINT — `tools/harness/`)
- Equivalence testing: compiles original algorithm via inference-sim, feeds test tuples, captures scores
- External dependency: inference-sim submodule
- Failure modes: compilation failure → halt (inference-sim API mismatch, report versions); test failure → halt with per-tuple diff report; timeout → halt with partial results; infrastructure error (e.g., missing submodule) → halt with setup instructions

**4. Mapping Artifact** (ARTIFACT — `docs/transfer/blis_to_llmd_mapping.md`)
- Signal/interface mapping with types, metric paths, staleness windows, fidelity ratings
- **Fidelity rating scale:** Each signal is rated `high`, `medium`, or `low` based on sim-to-production equivalence: **high** = same computation, same data source, negligible staleness (e.g., queue depth from direct endpoint query). **Decision test:** Can you write `sim_value == prod_value` (within floating-point tolerance) for any given request? If yes → high. **medium** = equivalent computation but different data source or non-trivial staleness window (e.g., estimated latency from historical vs. real-time). **Decision test:** The signal measures the same concept but through a different mechanism — the correlation is expected to be strong but not exact. Staleness window > 0 ms automatically qualifies as medium (not high). **low** = approximate or proxy signal with known semantic gap (e.g., simulation-only signal with no production equivalent). **Decision test:** The signal requires interpretation or approximation to bridge the sim-to-production gap — a domain expert would caveat the mapping. **Ambiguity resolution:** If the rater is uncertain between two levels, choose the lower level (more conservative). The mapping artifact must include a one-sentence rationale for each signal's fidelity rating. **Pipeline behavior:** `low`-fidelity signals halt the pipeline at Stage 1 (`extract` command exits with code 1). `medium`-fidelity signals proceed with a warning logged in `algorithm_summary.json`. `high`-fidelity signals proceed silently. The threshold for halt is `low` only — `medium` signals are acceptable for v1 transfer.
- Pins target system commit hash
- Failure modes: stale commit hash (submodule HEAD differs from pinned) → Stage 2 warns and halts for user decision; unmappable signal → halt pipeline with signal details and fidelity assessment

**5. Scorer Template** (ARTIFACT — `docs/transfer/scorer_template.go.md`)
- Annotated example plugin showing target system conventions
- Must compile against target submodule HEAD

**6. Workspace** (ARTIFACT — `workspace/`)
- Inter-stage file storage (gitignored)
- Contains: algorithm_summary.json, signal_coverage.json, validation_results.json, etc.
- Failure modes: missing expected file → halt with "stage N prerequisite missing: <filename>" message (the prompt template for each stage lists its required predecessor artifacts; the check is a file-existence test followed by schema validation — if the file does not exist, the stage halts immediately without attempting to read or parse); schema validation failure → halt with field-level diff

**Workspace Artifact Schemas:**

| Artifact | Writer | Reader(s) | Required Fields | Introduced |
|----------|--------|-----------|-----------------|------------|
| `algorithm_summary.json` | Stage 1 (CLI `extract`) | Stage 2 (prompt) | `algorithm_name`, `evolve_block_source`, `signals[]` (name, type, access_path), `metrics{}`, `scope_validation_passed` | PR1 |
| `signal_coverage.json` | Stage 2 (prompt) | Stage 3 (prompt), Stage 5 | `signals[]` (sim_name, prod_name, fidelity_rating, staleness_window_ms, mapped: bool), `unmapped_signals[]`, `commit_hash`, `coverage_complete` | PR3 |
| `validation_results.json` | Stage 5 (harness + CLI) | Stage 6 (prompt) | `suite_a{}` (passed, kendall_tau, max_abs_error), `suite_b{}` (passed, rank_stability_tau, threshold_crossing_pct, `informational_only`: bool — true for v1 approximate-scorer signals; when true, `passed` is always true and Suite B results are excluded from `overall_verdict` computation), `suite_c{}` (passed, deterministic, max_pile_on_ratio), `benchmark{}` (passed, mechanism_check_verdict, t_eff), `overall_verdict` (PASS/FAIL/INCONCLUSIVE), `noise_cv` | PR5 |

- **Ownership rule:** Pipeline artifacts belong to one of two classes: **(a) Workspace artifacts** (`workspace/*.json`) — each is written by exactly one stage per pipeline run; no stage may mutate another stage's workspace artifact; consuming stages validate schema before reading. **(b) Append-only artifacts** (`docs/transfer/calibration_log.md`) — cross-run artifacts where a designated stage appends one immutable entry per pipeline run; entries are never read-then-modified; the file is created by its template PR and extended at runtime. **Classification:** All artifacts in `workspace/` are class (a). The Calibration Log is the only class (b) artifact in v1. New artifacts must be explicitly classified when introduced. **Partial failure recovery:** Workspace artifact writes are NOT atomic in v1. If a stage crashes mid-write, the artifact may be incomplete or malformed. **Detection:** The consuming stage's schema validation catches most cases (missing required fields, type mismatches). **Recovery:** If schema validation fails on a predecessor artifact, the consuming stage halts with a diagnostic message identifying the malformed artifact. The operator must delete the corrupted artifact and re-run the writing stage. The `workspace/` directory can be safely cleared (`rm workspace/*.json`) to restart from Stage 1. **Prevention (deferred):** Atomic write (write to temp file, then rename) may be added in a future version if mid-write crashes are observed in practice.

**7. Calibration Log** (ARTIFACT — `docs/transfer/calibration_log.md`)
- Per-transfer entry with suite results, benchmark verdicts, threshold data
- Drives threshold adjustment decisions
- **Schema per entry:** `transfer_date`, `algorithm_name`, `pipeline_commit`, `single_run_provisional` (bool — `true` for all v1 entries since v1 uses single-run validation; indicates lower statistical confidence than multi-run results; downstream consumers should note this when interpreting results), `suite_a_results{}` (kendall_tau, max_abs_error), `suite_b_results{}` (rank_stability_tau, threshold_crossing_pct), `suite_c_results{}` (deterministic, max_pile_on_ratio), `benchmark_results{}` (mechanism_check_verdict, t_eff, matched_improvement), `noise_cv`, `overall_verdict`, `threshold_adjustments[]` (metric, old_value, new_value, rationale)
- **Ownership:** PR5 creates the template. Stage 6 appends one immutable entry per transfer run. This is an append-only artifact (class (b) — see Ownership rule above). Threshold adjustments require manual review after 3 transfers (R5). **Concurrency:** v1 does not support concurrent pipeline runs. If multiple transfers run simultaneously, calibration log appends may interleave or corrupt. **Detection mechanism:** Before appending, Stage 6 counts the existing calibration log entries and records the expected count. After appending, it re-reads the file and verifies: (1) entry count incremented by exactly 1, (2) the last entry matches the just-written content (byte-exact comparison of the appended section). If either check fails, Stage 6 halts with a corruption warning and does not create PRs — the operator must inspect the log, repair if needed (restoring from `git diff` or git history), and re-run Stage 6. **Prevention:** The Stage 6 prompt warns the operator to ensure no other pipeline run is active before appending. Future versions may add file locking or move to a structured format (e.g., one file per entry) if concurrent transfers are needed.
- **Failure modes:** Corrupted or invalid calibration log → Stage 6 halts with parse error details (operator must manually repair or restore from git history); missing calibration log file → Stage 6 creates a new log from the template (first transfer); schema validation failure on existing entries → Stage 6 halts (log may have been manually edited incorrectly); file write failure during append → Stage 6 halts with I/O error (operator retries or checks filesystem permissions). In all halt cases, the pipeline-generated PR is not created — the operator must resolve the calibration log issue and re-run Stage 6.
- **Decision procedure:** After 3 transfers with `overall_verdict` of PASS or FAIL (INCONCLUSIVE entries do NOT count toward the 3-transfer threshold and are excluded from margin analysis — they indicate measurement problems, not threshold calibration data), review distribution of per-metric margins. If >50% of counted transfers have margin < 10% above threshold, tighten threshold. If >30% fail on a metric with margin < 5%, loosen threshold. Document rationale in `threshold_adjustments[]`. **Owner:** Pipeline operator (the person running transfers). **Trigger:** Automatic prompt from Stage 6 after the 3rd calibration log entry. **Governance:** Threshold changes require a reviewed PR to `docs/transfer/calibration_log.md` updating the `threshold_adjustments[]` section. **Review criteria:** The reviewer must verify: (1) the rationale references specific calibration log entries by `transfer_date`, (2) the margin analysis is arithmetically correct, (3) the new threshold is within a reasonable range (no more than 2x change from provisional values without explicit justification). **Reviewer:** Any team member with write access to sim2real (v1 has no dedicated reviewer role — this may be formalized if the team grows). **Conflict resolution:** If multiple transfers suggest conflicting adjustments (e.g., one suggests tightening, another loosening the same metric), the more conservative threshold is chosen (i.e., the threshold that would cause more transfers to pass) and the conflict is documented in the `threshold_adjustments[]` rationale. **Rollback:** If a tightened threshold causes the next transfer to fail, revert to the previous threshold and document the revert as a new `threshold_adjustments[]` entry. Transfers 1–2 do not require re-validation when thresholds change — they are historical records with provisional thresholds noted.

### Data Flow

```
Happy path:
[routing/*] → transfer_cli extract → workspace/algorithm_summary.json
                                              ↓
[mapping artifact] → Claude (Stage 2) → workspace/signal_coverage.json
                                              ↓
[scorer template] → Claude (Stage 3) → files on transfer/ branch in submodule
                                              ↓
                    Claude (Stage 4) ←→ go build + go test (retry loop)
                                              ↓
[inference-sim] → Noise characterization (5 baseline runs → T_eff) → Go harness Suites A/B/C (Stage 5) → Cluster benchmarks + mechanism check → workspace/validation_results.json
                                              ↓
                    Claude (Stage 6) → PR URLs + calibration log entry

Error/feedback paths:
  Stage 1: low-fidelity signal or scope validation failure → HALT (exit 1)
  Stage 2: unmappable signal or stale commit hash → HALT (user decision: for stale commit hash, either (a) update mapping artifact commit pin to current submodule HEAD and re-validate mappings, (b) update submodule to match mapping artifact's pinned commit, or (c) manually verify compatibility and override with documented rationale; for unmappable signal, either (a) add mapping entry with fidelity assessment, (b) mark signal as low-fidelity to halt pipeline, or (c) remove signal from algorithm scope if non-essential)
  Stage 3: (no automated validation — output reviewed in Stage 4)
  Stage 4: error → classify → retry (max 3/class, 5 total) → HALT + escalate
           identical consecutive errors → immediate HALT
  Stage 5: CV > 15% → HALT (noise too high)
           Suite A/B/C fail → HALT (equivalence not established)
           Mechanism check INCONCLUSIVE → HALT + diagnostic report
  Stage 6: (no error path — only reached on PASS)
```

### System Invariants

- **Pipeline-generated PRs** (runtime PRs to llm-d repos created by Stage 6): No PR created without `overall_verdict == PASS` in `workspace/validation_results.json` — enforced by Stage 6 prompt (FAIL and INCONCLUSIVE both block). **Infrastructure PRs** (PR1–PR6 in sim2real): Each PR must pass its own validation gate (defined per-PR in Section I) before merging. Infrastructure PRs do not require pipeline validation suites since the pipeline doesn't exist yet during the build phase
- Generated scorer disableable via config toggle — enforced by generated scorer unit tests (see Objective 4)
- Stages run strictly in order (1→2→3→4→5→6) — enforced by prompt template structure (each stage checks predecessor output exists)
- Low-fidelity signals halt pipeline (never silently proceed) — enforced by Stage 1 `extract` command (exit 1 on low-fidelity) and Stage 2 prompt (halts on unmappable signal)
- Workspace artifacts validated by consuming stage before processing — enforced by JSON Schema files (`tools/schemas/algorithm_summary.schema.json`, `signal_coverage.schema.json`, `validation_results.schema.json`) created in PR1 and extended in subsequent PRs. The CLI `transfer_cli.py` provides a `validate-schema` command that checks a workspace artifact against its schema. Prompt templates instruct each consuming stage to run schema validation before reading.

### External System Map

| Component | sim2real | inference-sim | llm-d-scheduler | llm-d-benchmark |
|-----------|:-------:|:-------------:|:---------------:|:---------------:|
| CLI extract | writes workspace | | | |
| Stage 2 (Translate) | reads mapping | | reads scorer source | |
| Stage 3 (Generate) | | | writes branch | |
| Stage 4 (Test) | | | runs build+test | |
| Stage 5 (Validate) | writes results | runs harness | reads plugin | runs benchmarks |
| Stage 6 (PR) | updates cal log | | creates PR | creates PR |

---

## E) Risk Register

| # | Decision | Assumption | Validation | Cost if Wrong | Gate |
|---|----------|------------|------------|---------------|------|
| R1 | LLM translates algorithms correctly | Claude can bridge sim↔production gap | 3-suite equivalence (Stage 5) | Re-run Stages 3→4→5 (0 PRs, but requires full regeneration-test-validation cycle). **False-positive risk:** If an incorrect translation passes all 3 suites, a bad PR is created and potentially merged to llm-d repos. Mitigation: (1) llm-d maintainer code review is the final gate before merge, (2) the config toggle (Objective 4) enables quick rollback if issues are discovered post-merge, (3) suite coverage is finite — the suites test specific equivalence properties but cannot guarantee semantic correctness for all inputs. | Before first transfer |
| R2 | Scorer template reflects llm-d conventions | Template compiles against submodule HEAD | Compilation check at template creation | 1 PR (PR2 rework) | Before PR3 |
| R3 | Mapping artifact captures all signals | Signal categories from design doc used as initial list; PR1 extraction is authoritative | Stage 2 halts on unmappable signal | 1 PR (PR1 rework) | Before PR3. **Validation sequence:** (1) PR1 `extract` command parses the actual EVOLVE-BLOCK and produces the authoritative signal list in `algorithm_summary.json`. (2) PR1 implementer compares extracted signals against the design doc's 5-signal list. (3) If signals differ (new signals found or design doc signals absent): update the mapping artifact within PR1 to match the extracted list, document the discrepancy in `docs/transfer/README.md`, and update Section B of this plan. (4) R3 is validated by confirming that every signal in `algorithm_summary.json` has a corresponding entry in the mapping artifact — this check runs at PR1 review time, not at Stage 2 runtime. Stage 2's unmappable-signal halt is a runtime safety net, not the primary R3 gate. |
| R4 | Single-run benchmarks meaningful | Noise CV < 15% | Noise characterization (5 runs) | Config change (0 PRs) | During PR5 (noise characterization runs before transfer validation). **Recovery if CV > 15%:** (1) Investigate noise source — check cluster load, time-of-day effects, resource contention. (2) Re-run noise characterization during a lower-variance window (e.g., off-peak hours). (3) If CV remains > 15% after 2 re-runs, increase baseline runs from 5 to 10 (reduces CV estimate variance). (4) If still > 15% after step (3): **terminal halt** — Stage 5 permanently halts for this cluster. The pipeline cannot proceed. **Decision procedure:** The operator must choose one of: (a) switch to a different cluster and restart noise characterization from step (1), or (b) abort the transfer and document the noise issue in the calibration log as a failed attempt with reason `noise_cv_exceeded`. The pipeline does NOT automatically retry — each step requires explicit operator action. **Maximum attempts:** 3 noise characterization runs total (initial + 2 re-runs at 5 baseline runs, then 1 run at 10 baseline runs). If all 3 fail, terminal halt applies. **Threshold rationale:** 15% is the maximum CV at which `T_eff = 2*CV = 30%` still produces a meaningful improvement threshold — above this, the noise floor exceeds plausible algorithm improvement. |
| R5 | Equivalence threshold (0.8 Kendall-tau) appropriate | Threshold separates good from bad | Calibrate after 3 transfers | Config change (0 PRs) | After PR6. **Pre-calibration failure handling:** If a transfer fails on provisional thresholds before 3 transfers have been completed, the operator should: (1) record the failure in the calibration log, (2) investigate whether the failure indicates a genuine translation problem vs. an overly strict threshold, (3) if threshold-related, adjust provisionally with documented rationale and re-run — this counts toward the 3-transfer calibration target. |
| R6 | inference-sim LoadEvolvedBlock API exists | API available in submodule | Check inference-sim source during PR1 | 1 PR (implement API in inference-sim or shim in tools/harness/) | **Resolved before PR1 merges** — if API missing: **Preferred: option (a)** implement shim in `tools/harness/` within PR1 (keeps all sim2real PRs independent). **Fallback: option (b)** submit API PR to inference-sim — only if the shim is infeasible (e.g., required internal state is unexportable). **Decision procedure:** PR1 implementer checks inference-sim source for `LoadEvolvedBlock` or equivalent. If absent, attempt shim implementation. If shim fails (document why), escalate to option (b) and update the dependency DAG (PR3 becomes blocked on the inference-sim PR merge). Decision and rationale recorded in `docs/transfer/README.md`. **Timeline:** Decision made during PR1 implementation; if option (b), the inference-sim PR must be submitted before PR1 merges. |
| R7 | Prompt templates produce reliable results | LLM follows instructions consistently | Dry-run with synthetic algorithm | Prompt iteration (0 PRs) | PR6 |
| R8 | Submodule commits remain available during pipeline run | Pinned commits not force-pushed or deleted | Stage 2 staleness check verifies commit exists | Re-pin submodule + update mapping artifact (0 PRs) | Every pipeline run (Stage 2) |
| R9 | Cluster available for benchmarks | Benchmark cluster accessible during Stage 5 | Pre-flight cluster health check in `transfer_cli.py benchmark` | Retry later (0 PRs); pipeline pauses at Stage 5 until cluster available | During PR5 (Stage 5 execution) |
| R10 | Submodule commits stable during pipeline run | No submodule updates between Stages 2 and 5 | Stage 2 records commit hash; Stage 5 re-validates | Silent API mismatch if submodule updated mid-run | Every pipeline run. **Mitigation:** Stage 5 (validate.md prompt) re-checks the submodule HEAD against the commit hash recorded in `signal_coverage.json`. If the hashes differ, Stage 5 halts with a warning: "Submodule updated since Stage 2 — re-run Stage 2 to re-validate mappings." This is a lightweight check (one `git rev-parse` per submodule) that prevents stale-validation bugs. **Operator responsibility:** Do not run `git pull` or update submodules during an active pipeline run. |

### Cross-System Risk Categories

| Risk Category | Status | Mitigation |
|---------------|--------|------------|
| **API drift** | Mitigated | Mapping artifact pins submodule commit; staleness check at Stage 2 |
| **Version mismatch** | Mitigated | Submodule pins exact commit; mapping artifact records "last verified against" |
| **Schema evolution** | Mitigated | Inter-stage JSON schemas validated by consuming stages |
| **External availability** | Partially mitigated | Interactive session — user resolves cluster issues |
| **Mock fidelity** | Acknowledged | v1 tests against real submodules; PR6 dry-run uses synthetic algorithm |

---

## F) Implementation Evolution

**Starting point:** sim2real has 3 submodules, input artifacts in `routing/`, and a v3 design doc. No pipeline infrastructure exists.

### Milestone 1: Foundation (after PR1 + PR2)

Prerequisites established. Mapping artifact and scorer template created. CLI can extract algorithms and validate mapping.

- **Testable:** `transfer_cli.py extract routing/` produces valid `algorithm_summary.json`. `transfer_cli.py validate-mapping` passes on the mapping artifact.

### Milestone 2: Pipeline Stages 1-4 (after PR3 + PR4)

Prompt templates for extraction, translation, generation, and testing. Go harness skeleton. Pipeline can run interactively through code generation and local testing.

- **Testable:** Run interactive Claude Code session: extract → translate → generate → test. Verify generated scorer compiles and passes unit tests in target submodule.

### Milestone 3: Full Validation (after PR5)

Equivalence testing (Suites A/B/C) and cluster benchmark tooling operational. Noise characterization workflow established. Prompt templates for validation and PR creation complete.

- **Testable:** Run Suite A/B/C against generated code. Run cluster benchmark. Full 6-stage pipeline operational.

### Milestone 4: Self-Verified + Calibrated (after PR6)

Pipeline smoke-tested with synthetic algorithm. Calibration transfer validates thresholds.

- **Testable:** End-to-end pipeline run. Calibration log entry recorded. Thresholds graduate from provisional.

---

## G) Stable API Reference

**Confirmed in llm-d-inference-scheduler:**

`pkg/scheduling/scorer.go` — `Scorer` interface with methods:
- `Score(ctx, request, endpoints) → scores` — compute per-endpoint scores
- `TypedName() → string` — unique scorer identity
- `Category() → string` — scorer classification

Factory registration pattern for adding new scorers. [CONFIRMED from submodule exploration]

`LoadAware` scorer in `pkg/scheduling/plugins/` — simplest template implementation. [CONFIRMED]

**Confirmed in inference-sim:**

`sim/routing.go` — `RoutingSnapshot`, `Request`, `RouterState`, `WeightedScoring` types. `WeightedScoring.Route()` method. [CONFIRMED]

---

## H) Cross-Cutting Infrastructure Plan

### 1. Shared Test Infrastructure

- **PR1:** Test fixtures for extraction (use existing `routing/` artifacts as test input)
- **PR3:** Go test harness skeleton with inference-sim integration
- **PR5:** Equivalence test templates, benchmark result parsers

**External system testing:** v1 tests against real submodules (go build/test in llm-d-inference-scheduler). No mocks for core pipeline. PR6 dry-run uses a synthetic algorithm.

### 2. Documentation Maintenance

- **PR1:** CLAUDE.md update with transfer pipeline section, `docs/transfer/README.md`
- **Each PR:** Update README.md with current pipeline status
- **PR5:** Complete prompt template documentation
- **Merge conflict strategy:** Since PRs are sequential (each merges before the next starts), documentation conflicts are unlikely during normal flow. If a PR is revised during review while a later PR is in progress, the later PR must rebase onto the updated base. Documentation updates should use append-only patterns where possible (e.g., adding new sections to CLAUDE.md rather than modifying existing ones) to minimize conflict surface.

### 3. CI Pipeline Changes

- **PR1:** Add `tools/transfer_cli.py` tests to CI
- **PR3:** Add Go test harness build check
- **PR6:** Add dry-run integration test
- No external access needed for CI (all CI tests use local fixtures or submodule builds). **Note:** Cluster benchmarks (PR5, Stage 5) are run manually during interactive pipeline execution, NOT in CI. CI validates harness compilation, suite logic with synthetic data, and benchmark result parsing — but does not execute actual cluster benchmarks. The pipeline-generated PR validation (Stage 6 checking `overall_verdict`) happens at runtime, not in CI.

### 4. Dependency Management

- **Python:** stdlib only (`json`, `re`, `pathlib`, `subprocess`) — no external dependencies. The CLI does not make direct API calls (LLM interaction is through the interactive Claude Code session, not the CLI). **Note:** The `benchmark` command invokes an external tool from the llm-d-benchmark submodule via `subprocess` — this is a runtime dependency, not a Python package dependency. The tool's identity and interface are confirmed during PR5 implementation (see Section B, llm-d-benchmark). **Python version:** >= 3.9 (for `pathlib` and type hint features). **Packaging:** No `pyproject.toml` or `requirements.txt` needed since stdlib-only; CLI is invoked directly as `python tools/transfer_cli.py`. **Schema validation approach:** The `validate-schema` CLI command uses a custom lightweight validator implemented with stdlib `json` only — NOT the `jsonschema` library. The schema files (`tools/schemas/*.schema.json`) use a restricted subset of JSON Schema (required fields, type checks, enum values) that can be validated with a ~100-line Python function using `json.load` + type introspection. The validator checks: (1) required field presence, (2) field type correctness (string, number, bool, array, object), (3) enum value membership. Full JSON Schema features (e.g., `$ref`, `allOf`, `patternProperties`) are not supported and not needed for workspace artifact validation.
- **Go:** inference-sim (submodule), standard library
- **llm-d repos:** Version-pinned via mapping artifact commit hash
- No new external dependencies beyond submodules

### 5. Cross-Repo Coordination

- **All sim2real PRs merge independently** (default, when R6 resolved via option (a)). **Exception:** If R6 is resolved via option (b), PR3 is blocked on the inference-sim API PR merge — see R6 in Risk Register and the conditional DAG in Section J.
- **Runtime PRs:** Pipeline creates PRs in llm-d repos at runtime (Stage 6). These are reviewed by llm-d maintainers. **Pipeline success = PR creation** (not PR merge). The pipeline's responsibility ends at creating well-formed, validated PRs. If llm-d maintainers request changes, the operator re-runs relevant pipeline stages (typically Stage 3→4) to address feedback, then updates the PR. If a PR is rejected entirely, the calibration log entry records the rejection with rationale.
- **Branch naming:** `transfer/<algorithm_name>` in target submodules
- **Submodule updates:** Submodule pins updated when mapping artifact is refreshed

### 6. Artifact Lifecycle

- **Prerequisites before first transfer:** Mapping artifact + scorer template + prompt templates (all created by PR5)
- **Staleness detection:** Stage 2 compares mapping artifact commit hash vs. submodule HEAD
- **Maintenance:** Manual update when target system APIs change. **Versioning:** Mapping artifact and scorer template each include a `version` field (semver) and a `last_verified_against` commit hash. Prompt templates are versioned by the sim2real `pipeline_commit` hash recorded at Stage 1 start — this is the git commit of the sim2real repo at pipeline invocation time, which pins all prompt template contents. If any prompt template changes between Stage 1 start and a later stage, the `pipeline_commit` mismatch is detected by the orchestrator prompt (`prompts/transfer.md`), which checks `git diff --name-only <pipeline_commit> HEAD -- prompts/` and warns the operator if any prompt files have changed. **Recommended response:** The operator should (a) review the diff to assess whether the changes affect the current or remaining stages, (b) if changes affect a completed stage, restart the pipeline from the affected stage (clear workspace artifacts from that stage onward), (c) if changes only affect future stages, proceed with the updated templates (the `pipeline_commit` is NOT updated — the warning persists as an audit trail), (d) if unsure, restart from Stage 1 (safest option). When a submodule is updated, Stage 2's staleness check detects the commit hash mismatch and halts. The operator then updates the artifact, bumps the version, and records the new commit hash. **Migration:** No automated migration — artifact updates are manual and reviewed via PR. The commit hash comparison is the primary staleness detection mechanism.

---

## I) PR Plan

### PR1: Mapping artifact + project scaffolding + CLI extract

**Target Repo:** sim2real
**Component Change:** Mapping Artifact, CLI Tools (extract + validate-mapping), Workspace
**PR Type:** infrastructure + artifact
**Motivation:** Establish the foundation: mapping artifact (signal/interface correspondences between inference-sim and llm-d), project directory structure, and the extraction CLI command.

**Scope:**
- In: `docs/transfer/blis_to_llmd_mapping.md` (complete signal mapping with types, metric paths, staleness windows, fidelity ratings, commit pin), `docs/transfer/README.md`, `tools/transfer_cli.py` (`extract`, `validate-mapping`, and `validate-schema` commands), `tools/schemas/algorithm_summary.schema.json` (JSON Schema for workspace artifact validation — extended by subsequent PRs for `signal_coverage.schema.json` in PR3 and `validation_results.schema.json` in PR5), `.gitignore` update (add `workspace/`), CLAUDE.md transfer section, tests for extraction, mapping validation, and schema validation, **R6 resolution (must be completed before PR1 merges):** verify `LoadEvolvedBlock` API exists in inference-sim. If the API does not exist, PR1 must include a shim implementation in `tools/harness/` that loads evolved blocks by directly instantiating `WeightedScoring` from parsed algorithm parameters, or alternatively submit the API implementation as a PR to inference-sim (which then blocks PR3, not PR1). The chosen approach and its rationale must be documented in `docs/transfer/README.md`.
- Out: No scorer template, no prompt templates, no Go harness

**Behavioral Guarantees:** `transfer_cli.py extract routing/` parses `best_program_info.json`, extracts EVOLVE-BLOCK, validates scope, produces `workspace/algorithm_summary.json` conforming to the workspace artifact schema (fields: `algorithm_name`, `evolve_block_source`, `signals[]`, `metrics{}`, `scope_validation_passed`). **Scope validation definition:** The `extract` command checks: (1) the EVOLVE-BLOCK markers are present and well-formed in `best_program.py`, (2) all signals referenced in the algorithm are routing signals (not scheduling, batching, or other categories — see Non-Goals), (3) no signal has `low` fidelity rating per the mapping artifact (if mapping artifact exists at extraction time; otherwise this check is deferred to Stage 2), (4) the algorithm does not reference P/D disaggregation constructs (out of scope). `scope_validation_passed` is `true` if all checks pass; `false` if any check fails (with diagnostic details in the `extract` command's JSON output `errors[]` field). `transfer_cli.py validate-mapping` checks structural completeness of mapping artifact. Mapping artifact contains all 5 signals from the routing algorithm with fidelity ratings.
**Risks:** R3 (mapping completeness), R6 (inference-sim API). Mitigation: Stage 2 halts on unmappable signals; R6 resolved during PR1 — PR1 cannot merge until `LoadEvolvedBlock` is either confirmed to exist or a shim/API implementation plan is completed (see R6 in Risk Register).
**Cross-Cutting:** Creates shared infrastructure consumed by all later PRs.
**Validation Gate:** R3 validated by mapping completeness.

**Implementation Guide:**
- Architectural impact: New `docs/transfer/`, `tools/`, `workspace/` directories
- Requires: Reading llm-d-inference-scheduler submodule to document scorer interface, signal access paths, config format
- Test categories: Unit tests (extraction, scope validation, mapping validation)
- Documentation: CLAUDE.md, docs/transfer/README.md
- Extension friction: New signal = 1 mapping row + 1 test case
- Independently reviewable: Self-contained infrastructure + documentation
- No dead code: CLI commands exercised by tests; mapping consumed by PR3

---

### PR2: Scorer template artifact

**Target Repo:** sim2real
**Component Change:** Scorer Template
**PR Type:** artifact
**Motivation:** Annotated example of a well-structured target-system plugin. Required for Stage 3 code generation — the LLM uses it as a reference for conventions, test structure, and config registration.

**Scope:**
- In: `docs/transfer/scorer_template.go.md` — annotated scorer showing: package structure, Scorer interface implementation, metric access pattern, config registration with feature flag, unit test structure (request parsing, no-op, overlap), `ScoreEndpoints` equivalence test function, hot-reload documentation
- Out: No code changes, no prompt templates

**Behavioral Guarantees:** Template code compiles and passes tests against pinned llm-d-inference-scheduler HEAD. All 8 required sections documented (per design doc). Version 1.0.
**Risks:** R2 (template accuracy). Mitigation: Compile-test at creation.
**Cross-Cutting:** Consumed by PR3 (prompt templates reference it for Stage 3).
**Validation Gate:** R2 validated before PR3. **Manual gate:** Extract code blocks from `scorer_template.go.md`, compile against pinned llm-d-inference-scheduler HEAD (`go build`), and verify tests pass (`go test`). This is a manual pre-merge check, not an automated CI gate, because the template is a markdown-embedded code artifact. **Staleness window:** Between PR2 merge and PR3 merge, there is no automated CI check for template staleness. If the llm-d-inference-scheduler submodule is updated during this window, the template could become stale without detection. **Mitigation:** (1) PR3 should be submitted promptly after PR2 merges to minimize the window. (2) PR3 adds a CI check (`tools/check_scorer_template.sh`) that extracts code blocks from `scorer_template.go.md`, compiles them against the current llm-d-inference-scheduler submodule HEAD, and fails if compilation fails. This provides automated staleness detection from PR3 onward. (3) The Stage 3 prompt (`prompts/generate.md`) instructs the operator to verify the scorer template compiles before using it as a generation reference — if compilation fails, Stage 3 halts with instructions to update the template.

**Implementation Guide:**
- Requires: Identifying a representative existing scorer in llm-d-inference-scheduler as basis
- Test categories: Manual (compile template against submodule HEAD)
- Extension friction: New target system = new template file
- Independently reviewable: Documentation artifact
- No dead code: Consumed by Stage 3 prompt

---

### PR3: Prompt templates (Stages 1-3) + Go harness skeleton

**Target Repo:** sim2real
**Component Change:** Prompt Templates (extract, translate, generate), Go Test Harness
**PR Type:** pipeline-stage + infrastructure
**Motivation:** Implements the first three pipeline stages as prompt templates plus the Go test harness skeleton for equivalence testing.

**Scope:**
- In: `prompts/transfer.md` (top-level orchestrator prompt), `prompts/extract.md` (Stage 1), `prompts/translate.md` (Stage 2), `prompts/generate.md` (Stage 3), `tools/harness/` (Go module skeleton using inference-sim, test tuple format, `TestEquivalence` entry point), `tools/check_scorer_template.sh` (CI script: extracts code blocks from scorer template, compiles against submodule HEAD — provides automated staleness detection for the scorer template), `tools/schemas/signal_coverage.schema.json` (extends PR1 schema infrastructure), Go harness tests
- Out: Stages 4-6 prompts, no validation, no benchmarks

**Behavioral Guarantees:** Stage 1 prompt guides extraction with scope validation and prerequisite checks. Stage 2 prompt guides signal mapping with staleness check and coverage report. Stage 3 prompt guides code generation using scorer template. Go harness compiles against inference-sim and can load/run a defined trivial test case: a single `TestTuple` with 2 endpoints and uniform queue depths, verifying that `RunTuples` returns scores without error and that the loaded algorithm produces non-zero scores.

**Harness Interface Contract (PR3 → PR5):** PR3 delivers the following interface that PR5 extends:

```go
// TestTuple is the input format for equivalence tests.
type TestTuple struct {
    Request      sim.Request        // request fields (token count, model, priority)
    Endpoints    []sim.RouterState  // per-endpoint state (queue depth, in-flight, latency, KV util)
    Expected     sim.RoutingSnapshot // expected routing output from evolved algorithm
}

// Result captures per-tuple test output.
type Result struct {
    Tuple       TestTuple
    Actual      sim.RoutingSnapshot // actual routing output
    Passed      bool                // whether Actual matches Expected within tolerance
    Error       error               // non-nil if execution failed (e.g., panic, timeout)
    ScoreDiffs  map[string]float64  // per-endpoint absolute score differences (empty if Passed)
}

// Algorithm is an opaque handle to a loaded evolved algorithm.
type Algorithm interface {
    Route(req sim.Request, endpoints []sim.RouterState) sim.RoutingSnapshot
}

// LoadAlgorithm loads an evolved algorithm from the inference-sim submodule.
// path is relative to the sim2real repo root (e.g., "routing/best_program.py").
// Returns error if: file not found, parse failure, or inference-sim API mismatch.
func LoadAlgorithm(path string) (Algorithm, error)

// RunTuples executes tuples against the algorithm and returns per-tuple results.
// Never returns a top-level error; per-tuple errors are captured in Result.Error.
// Error handling: Stage 5 (validate.md) counts per-tuple errors after RunTuples returns.
// If error rate > 5% of tuples (i.e., >5% of results have non-nil Error), the suite
// is marked FAIL with reason "excessive_tuple_errors" regardless of other metrics.
// Individual tuple errors are logged in validation_results.json for diagnosis.
func RunTuples(alg Algorithm, tuples []TestTuple) []Result

// TestEquivalence is the entry point that PR5 suites call with suite-specific tuples.
func TestEquivalence(t *testing.T)
```

- PR5 adds suite-specific tuple generators (Suite A: numeric fidelity tuples, Suite B: staleness-injected tuples, Suite C: concurrent tuples) that feed into `RunTuples`. **Suite B staleness injection mechanism:** Staleness is simulated by modifying `RouterState` field values in the test tuples before passing them to `RunTuples`. For each "source group" (set of signals from the same data source), the tuple generator applies a correlated perturbation: for v1 (staleness_window_ms = 0), the perturbation is zero (Suite B passes trivially). For future versions, the perturbation magnitude is proportional to `staleness_window_ms`. The `TestTuple` struct does not need a staleness field — staleness is injected by the tuple generator modifying the `Endpoints[]` values, not by metadata on the struct. The "3 repetitions" means each base tuple is tested 3 times with independently sampled perturbations to measure rank stability under staleness.
**Risks:** R1 (LLM translation quality — tested at Milestone 2), R6 (inference-sim API). Mitigation: Equivalence testing validates translation; R6 resolved in PR1 or as dependency.
**Cross-Cutting:** Consumes PR1 (mapping artifact, CLI extract) and PR2 (scorer template).
**Validation Gate:** Go harness builds and passes its trivial test case (`go test ./tools/harness/...`). Each prompt template has correct YAML front matter and references valid predecessor outputs. Full equivalence validation deferred to PR5. **Interface sufficiency check:** PR3 reviewer must verify that the `TestTuple` struct and `RunTuples` function can support PR5's needs: (1) `RouterState` fields are sufficient for staleness injection (Suite B — at minimum, staleness can be simulated by modifying state values before passing to `RunTuples`), (2) `RunTuples` can be called concurrently from multiple goroutines (Suite C — the function must be safe for concurrent use), (3) suite-specific tuple generators can be built from the `TestTuple` struct without schema changes. If any of these cannot be confirmed, PR3 must document the gap and the planned PR5 extension.

**Implementation Guide:**
- Architectural impact: New `prompts/` directory, new `tools/harness/` Go module
- Test categories: Go build/test for harness, manual dry-run for prompts
- Documentation: Prompt templates are self-documenting
- Extension friction: New transfer type = new prompt set + new mapping artifact
- Cross-repo impact: None at PR merge time. At pipeline runtime, Stage 3 writes generated code to a branch in the llm-d submodule working copy.
- Independently reviewable: Prompts + harness testable independently
- No dead code: All prompts exercised by Milestone 2 dry-run; harness exercised by tests

---

### PR4: Prompt template (Stage 4) + test retry logic

**Target Repo:** sim2real
**Component Change:** Prompt Templates (test), CLI Tools (test support)
**PR Type:** pipeline-stage
**Motivation:** Implements Stage 4 (Test) — build and test generated code with retry logic, error classification, and loop detection.

**Scope:**
- In: `prompts/test.md` (Stage 4 — test execution, error classification taxonomy, retry limits, loop detection, escalation), `tools/transfer_cli.py` `test-status` command (parses `go test` and `go build` output, classifies errors into compilation/test-unit/test-integration/lint/infrastructure categories, outputs structured JSON for prompt consumption)
- Out: Stages 5-6, no validation, no benchmarks

**Behavioral Guarantees:** Stage 4 prompt guides: run plugin unit tests, run full repo build/test/lint, retry on classified errors (max 3 per class, 5 total), detect identical consecutive errors (halt immediately), escalate with diagnosis when limits reached. Error classes: compilation, test-unit, test-integration, lint. Infrastructure errors not retried. **Timeouts:** Each `go build` or `go test` invocation has a 10-minute timeout (passed via `go test -timeout 10m`). If a single invocation exceeds the timeout, it is classified as an infrastructure error (not retried). Total Stage 4 timeout: 60 minutes (enforced by the prompt template — if Stage 4 has been running for >60 minutes, halt regardless of retry state). The operator can override these timeouts by modifying the prompt template for specific transfers. **State management:** Each retry modifies only the generated scorer source files in the submodule working copy; workspace artifacts from prior stages are read-only during Stage 4. No rollback of workspace artifacts occurs on retry — the retry loop only affects the generated code. **Between retries:** (1) The working copy is NOT reset — the LLM applies incremental fixes to the current state of the generated files. (2) The LLM receives the full error output from `go build`/`go test` (not summarized), plus the error class and count from `test-status`. (3) The LLM identifies which files to modify from the error output (e.g., compilation errors include file paths; test failures include test names traceable to source files). (4) **Non-consecutive duplicate detection:** The prompt maintains a rolling hash of error signatures (error class + first error message line). If the same signature appears 3 times across any retries (not just consecutive), the prompt escalates — this prevents oscillating between two error states. **Escalation:** When retry limits are reached, Stage 4 halts and presents the operator with: (a) the error class and count summary, (b) the last error output, (c) a diagnosis of the likely root cause (e.g., "compilation error suggests missing import — may indicate scorer template gap"). The operator decides whether to manually fix and resume, or abort the transfer.
**Risks:** R1 (LLM fix quality). Mitigation: Retry limits prevent infinite loops; escalation to user.
**Cross-Cutting:** Consumes PR3 (Stages 1-3 must complete first).
**Validation Gate:** None.

**Implementation Guide:**
- Architectural impact: New prompt file, minor CLI additions
- Test categories: Manual dry-run with intentional errors
- Independently reviewable: Stage 4 prompt is self-contained
- No dead code: Exercised in every pipeline run

---

### PR5: Validation pipeline (Stage 5) + noise characterization + benchmarks

**Target Repo:** sim2real
**Component Change:** Prompt Templates (validate), CLI Tools (noise-characterize, benchmark), Go Test Harness (Suite A/B/C), Calibration Log
**PR Type:** validation + infrastructure
**Motivation:** Implements Stage 5 (Validate) — 3-suite equivalence testing, cluster benchmarks with mechanism check, noise characterization workflow. This is the validation backbone.

**Scope:**
- In: `prompts/validate.md` (Stage 5 — Suite A/B/C execution, cluster benchmarks, go/no-go criteria, mechanism check), `tools/transfer_cli.py` (`noise-characterize` and `benchmark` commands), Go harness Suite A/B/C logic (extends PR3 harness interface: suite-specific tuple generators, staleness injection, concurrency tests), `docs/transfer/noise_characterization.md` (template), `docs/transfer/calibration_log.md` (template), tests for noise characterization and benchmark parsing
- Out: Stage 6 (PR creation), end-to-end orchestration

**Behavioral Guarantees:**
- Suite A: Numeric fidelity (1e-6 abs OR 1% relative) + rank correlation (Kendall-tau > 0.8). Tuples capped at 200, threshold boundaries prioritized.
- Suite B: Rank stability (tau > 0.7) + threshold-crossing stability (<20%). Correlated staleness per source group, 3 repetitions. **v1 note:** For approximate-scorer signals (all v1 signals), `staleness_window_ms` is 0, so Suite B passes trivially. The infrastructure is retained for future precise-scorer (ZMQ-based) transfers.
- Suite C: Parallel safety (20 concurrent, deterministic) + pile-on (no endpoint >2x fair share).
- Noise characterization: 5 baseline runs, per-metric CV, `T_eff = max(5%, 2*CV)`. CV > 15% halts. **Stage 5 tool invocation:** The `validate.md` prompt orchestrates both Python CLI and Go harness as separate tools within the interactive Claude Code session. The prompt instructs: (1) run `python tools/transfer_cli.py noise-characterize` (Python CLI, outputs JSON), (2) run `go test ./tools/harness/... -run SuiteA` / `SuiteB` / `SuiteC` (Go harness, invoked directly via shell), (3) run `python tools/transfer_cli.py benchmark` (Python CLI, which internally invokes the benchmark tool via `subprocess`). The Python CLI does NOT spawn the Go harness — they are independently invoked by the prompt. Results from both tools are written to `workspace/validation_results.json` by the prompt (not by the tools themselves).
- Mechanism check: matched workload improvement (relative P99 latency reduction) >= T_eff, specificity check (all unmatched workloads within T_eff of baseline — see Objective 3 definitions).
- **`overall_verdict` computation:** The verdict is computed as follows: (1) If any of Suite A, Suite C, or mechanism check has verdict FAIL → `overall_verdict = FAIL`. (2) If mechanism check verdict is INCONCLUSIVE → `overall_verdict = INCONCLUSIVE`. (3) If Suite A passed AND Suite C passed AND mechanism check verdict is PASS → `overall_verdict = PASS`. Suite B results are recorded but **excluded from the v1 verdict computation** (informational only — see Objective 2 v1 note). In future versions with precise-scorer signals, Suite B will be added to the AND clause. **Transition marker:** The `validate.md` prompt template must include a `# TODO(v2-precise-scorer): Include Suite B in verdict when staleness_window_ms > 0` comment at the verdict computation logic. Additionally, the `informational_only` field in `validation_results.json` serves as the programmatic marker — when a future version sets `informational_only: false` for Suite B, the verdict computation must include Suite B in the AND clause. The computation is deterministic: given the same suite results, the same verdict is always produced. The `validate.md` prompt template implements this logic and writes the result to `workspace/validation_results.json`.
- **INCONCLUSIVE handling:** A mechanism check is INCONCLUSIVE if improvement is positive but < T_eff, or if specificity check shows unexpected regression on unmatched workloads. INCONCLUSIVE blocks PR creation (same as FAIL) but produces a diagnostic report with recommended next steps: (a) If improvement > 0 but < T_eff: likely noise-related — re-run noise characterization to check if T_eff can be reduced (e.g., by running benchmarks during lower-variance time windows), then re-run the mechanism check. (b) If specificity check shows regression on unmatched workloads: investigate the generated scorer for unintended side effects — check whether the scorer modifies shared state or affects endpoints beyond the targeted routing signals. The diagnostic report includes the specific metric values to guide the operator's decision. The `overall_verdict` field supports three values: PASS, FAIL, INCONCLUSIVE — only PASS allows Stage 6 to proceed. **Re-execution after INCONCLUSIVE:** Stage 5 is NOT re-entrant — partial re-execution is not supported in v1. If the operator decides to re-run after INCONCLUSIVE, they restart Stage 5 from the beginning (all suites + noise characterization + benchmarks). Prior `validation_results.json` is overwritten. This is the safe default: it avoids stale-state bugs from mixing results across runs. The cost is re-running Suites A/B/C (~minutes) and benchmarks (~longer), but correctness outweighs efficiency for v1. Future versions may add selective re-execution with result caching.

**Risks:** R4 (single-run meaningfulness), R5 (threshold calibration). Mitigation: Noise characterization establishes measurement baseline; provisional thresholds tagged.
**Cross-Cutting:** Consumes all prior PRs. Creates calibration log template.
**Validation Gate:** R4 validated by noise characterization results.

**Implementation Guide:**
- Architectural impact: Major Go harness expansion (suite logic), new CLI commands, new prompt
- Test categories: Go unit tests (tuple generation, staleness injection, suite pass/fail logic), Python unit tests (CV computation, benchmark parsing, mechanism check logic)
- Documentation: Validate prompt, noise characterization procedure, calibration log format
- Extension friction: New validation suite = 1 Go test file + prompt section + CLI support
- Cross-repo impact: Runs Go tests in inference-sim; deploys to cluster for benchmarks
- Independently reviewable: Validation logic testable with synthetic data
- No dead code: All modules exercised by tests and consumed by Stage 5

---

### PR6: Stage 6 (PR creation) + pipeline self-verification + calibration

**Target Repo:** sim2real
**Component Change:** Prompt Templates (pr), Pipeline Self-Verification
**PR Type:** orchestration + validation
**Motivation:** Completes the pipeline with Stage 6 (PR creation) and validates the full pipeline with a synthetic algorithm smoke test and known-answer test. Establishes calibration workflow.

**Scope:**
- In: `prompts/pr.md` (Stage 6 — branch push, PR creation via `gh`, PR description template, calibration log entry), pipeline self-verification (smoke test with trivial algorithm, known-answer test), calibration transfer documentation
- Out: No new pipeline stages

**Behavioral Guarantees:** Stage 6 creates PRs via `gh` CLI with validation summary in description. **Error handling:** (1) `gh auth` failure → Stage 6 halts with instructions to run `gh auth login` (authentication is a prerequisite, not retried). (2) Branch already exists → Stage 6 appends a timestamp suffix (`transfer/<algorithm_name>-<YYYYMMDD-HHMMSS>`) and warns the operator (previous branch may indicate a prior transfer attempt). (3) `gh pr create` failure → Stage 6 halts with the `gh` error output; the operator can retry manually since the branch is already pushed. (4) Partial failure (branch pushed but PR creation fails) → Stage 6 reports the pushed branch name so the operator can create the PR manually or retry `gh pr create`. Smoke test: trivial algorithm runs through all 6 stages, each stage produces output in expected format. Known-answer test: synthetic algorithm with deterministic trivial logic (specifically: `score = queue_depth / max_queue_depth` for each endpoint, ignoring all other signals) is processed through the pipeline. **`max_queue_depth` definition:** `max_queue_depth` is a test-fixture constant defined in the known-answer test setup (not a field in `RouterState` or `TestTuple`). The test fixture specifies `max_queue_depth = 100` as a global constant for the synthetic algorithm. Each test tuple's endpoints have `queue_depth` values in `[0, 100]`, and the expected score is `queue_depth / 100`. This constant is part of the synthetic algorithm's logic (hardcoded in the trivial EVOLVE-BLOCK used for the known-answer test), not a runtime signal. The `RouterState.QueueDepth` field (confirmed in `sim/routing.go`) provides the per-endpoint `queue_depth` value. Ground truth Suite A scores are hand-computed by applying this formula to the test tuples' `queue_depth` values and the fixture constant. The expected scores are committed as a test fixture in `tools/harness/testdata/known_answer_expected.json`. The test verifies the pipeline-produced scores match within 1e-6 absolute tolerance **applied per-endpoint** (each endpoint's score must individually match within tolerance, not just the aggregate). If any single endpoint score exceeds tolerance, the test fails and reports: the endpoint index, expected score, actual score, and absolute difference. The PR6 implementer computes and commits the expected scores by applying `score = queue_depth / max_queue_depth` to each test tuple's endpoint data; the PR reviewer independently verifies by re-computing at least 3 representative tuples (minimum, maximum, and median queue depth ratios) and confirming they match the committed fixture. Calibration workflow documented.
**Risks:** R7 (prompt reliability). Mitigation: Dry-run with synthetic algorithm validates full flow.
**Cross-Cutting:** Consumes all prior PRs. Finalizes documentation.
**Validation Gate:** R5, R7 validated by calibration transfer.

**Implementation Guide:**
- Architectural impact: Final prompt file, self-verification tests
- Test categories: End-to-end (synthetic algorithm through full pipeline), known-answer (exact scores)
- Documentation: PR creation prompt, calibration workflow, final CLAUDE.md update
- Extension friction: N/A — PR creation is generic
- Independently reviewable: Stage 6 prompt + verification tests
- No dead code: All exercised by self-verification

---

## J) Dependency DAG

### PR Dependencies

```
Default DAG (R6 option (a) — preferred):
[PR1 sim2real] ──→ [PR2 sim2real] ──→ [PR3 sim2real] ──→ [PR4 sim2real] ──→ [PR5 sim2real] ──→ [PR6 sim2real]

Conditional DAG (R6 option (b) — fallback, only if shim infeasible):
[PR1 sim2real] ──→ [PR2 sim2real] ──→ [PR3 sim2real] ──→ [PR4 sim2real] ──→ [PR5 sim2real] ──→ [PR6 sim2real]
                                            ↑
                                   [inference-sim API PR] (cross-repo, blocks PR3 merge)
```

**Note:** PR2 depends on PR1 for mapping artifact commit pin references, but scorer template identification can begin in parallel with PR1 (partial parallelism). The DAG reflects the merge-order dependency. **Cross-repo dependency (conditional):** If R6 is resolved via option (b) (submit API PR to inference-sim), then PR3 is blocked on the inference-sim PR merge — this contradicts the default goal of independent sim2real PR merges, which is why option (a) is preferred. The decision is made during PR1 implementation (see R6 decision procedure in Risk Register). If option (b) is chosen, this DAG section must be updated to reflect the cross-repo block as the active dependency.

### Critical Path

PR1 → PR2 → PR3 → PR4 → PR5 → PR6

PR1 and PR2 have limited parallelism (PR2 depends on PR1 for mapping artifact references, but the scorer template work can begin in parallel with mapping artifact creation). PR3 requires both.

### Parallelism Opportunities

- **PR1 + PR2 (partial):** Scorer template identification can begin while mapping artifact is being written. Final template compilation requires the mapping artifact's commit pin.
- **After PR3:** PR4 and PR5 are sequential (Stage 4 must work before Stage 5 testing makes sense).

### Cross-Repo Visualization

```
sim2real PRs:  [PR1] → [PR2] → [PR3] → [PR4] → [PR5] → [PR6]
                                  ↓        ↓        ↓        ↓
llm-d repos:              (read submodules for template/mapping creation)
                                         (Stage 3-4: write/test in submodule locally)
                                                  (Stage 5: run benchmarks)
                                                           (Stage 6: create PRs at runtime)
```

No PRs are submitted to llm-d repos during the build phase. All llm-d interaction happens at runtime when a user runs the pipeline.

### Milestone Map

| Milestone | PRs | What's Testable |
|-----------|-----|-----------------|
| 1. Foundation | PR1 + PR2 | Extract algorithm, validate mapping, compile scorer template |
| 2. Stages 1-4 | + PR3 + PR4 | Interactive transfer through code generation + testing |
| 3. Full Validation | + PR5 | 3-suite equivalence + cluster benchmarks |
| 4. Self-Verified | + PR6 | End-to-end with synthetic algorithm, calibration |

---

## K) Design Bug Prevention Checklist

### Invariants

- Stages execute strictly in order (enforced by prompt template structure)
- No pipeline-generated PR created without validation pass (Stage 6 checks `overall_verdict`); infrastructure PRs pass their per-PR validation gates (Section I)
- Generated scorer disableable via config toggle — implemented in generated code (Stage 3 prompt instructs inclusion), verified by unit test in generated code (Stage 4 runs tests), documented in scorer template (PR2)
- Low-fidelity signals halt pipeline at Stage 1 (never silently proceed)

### Regression Surfaces

- inference-sim submodule: Go harness must compile against pinned commit
- llm-d-inference-scheduler submodule: Scorer template must compile against pinned commit
- Existing CI in target repos: Generated code must pass `go test ./...`

### Common Failure Modes and Prevention

**General:**
- **Scaffolding creep:** Every file, tool command, and prompt is exercised by its introducing PR's tests or by the self-verification in PR6.
- **Documentation drift:** Each PR updates CLAUDE.md and README.md in the same commit.
- **Test infrastructure duplication:** Go harness created once (PR3), consumed by PR5 and PR6.

**Cross-system-specific:**
- **API contract drift:** Mapping artifact pins submodule commit hash; Stage 2 staleness check warns on drift.
- **Mock divergence:** v1 tests against real submodules. No mocks for core pipeline.
- **Distributed partial failure:** Not applicable during build phase. At runtime, pipeline creates PRs as final step — no partial state across repos.
- **Artifact staleness:** Scorer template compiled against target HEAD. Mapping artifact validated by `transfer_cli.py validate-mapping`.
- **Prompt template drift:** Pipeline records `pipeline_commit` at Stage 1 start; warns if templates change mid-transfer.

### Abstraction Level Check

- Sections A-F and H-K contain zero implementation code
- Section G contains only confirmed API references from submodule exploration
- All component contracts described behaviorally
- All pre-implementation interfaces described behaviorally
