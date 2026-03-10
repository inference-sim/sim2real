# PR1: Mapping Artifact + Project Scaffolding + CLI Extract — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Establish the foundation for the sim-to-production transfer pipeline: a signal mapping document, directory structure, JSON schemas, and a Python CLI that extracts algorithm metadata from simulation artifacts.

**The problem today:** The sim2real repository has three submodules (inference-sim, llm-d-inference-scheduler, llm-d-benchmark) and routing artifacts from evolutionary optimization (`routing/best_program.py`, `routing/best_program_info.json`), but no infrastructure to transfer discovered algorithms to production. There are no `tools/`, `docs/transfer/`, or `workspace/` directories, no CLI commands, no signal mappings, and no schema definitions. Without this foundation, no pipeline stage can be implemented.

**What this PR adds:**
1. **Mapping artifact** — `docs/transfer/blis_to_llmd_mapping.md` documenting every routing signal's simulation-to-production correspondence: types, metric paths, fidelity ratings, staleness windows, and the pinned llm-d-inference-scheduler commit hash.
2. **CLI extraction tool** — `tools/transfer_cli.py` with three commands: `extract` (parses EVOLVE-BLOCK from `routing/best_program.py`, produces `workspace/algorithm_summary.json`), `validate-mapping` (checks mapping artifact completeness), and `validate-schema` (validates workspace JSON against schemas).
3. **JSON schemas** — `tools/schemas/algorithm_summary.schema.json` defining the workspace artifact contract that all downstream stages consume.
4. **R6 resolution** — Documents that `LoadEvolvedBlock` API does not exist in inference-sim and records the decision to use a shim approach in `tools/harness/` (deferred to PR3 implementation).

**Why this matters:** This is the first PR in a 6-PR series. Every subsequent PR imports the schemas, references the mapping artifact, or extends the CLI. Without this foundation, Stages 1-6 of the pipeline cannot be built.

**Architecture:** Python CLI (`tools/transfer_cli.py`, stdlib-only, >= 3.9) with subcommands. JSON Schema files in `tools/schemas/` using a restricted subset validated by a custom ~100-line stdlib validator. Mapping artifact as structured Markdown in `docs/transfer/`. Workspace directory (`workspace/`, gitignored) for inter-stage JSON artifacts.

**PR Category:** Artifact (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR1: Mapping Artifact + Project Scaffolding + CLI Extract in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Closes:** N/A — source is macro plan, no linked issues

**Behavioral Contracts:** See Part 1, Section B below

---

## Phase 0: Cross-System Dependency Audit

### 1) Submodule API Verification

For each submodule referenced by this PR:

- **inference-sim:** `git submodule status` records commit hash. PR1 depends on `RoutingSnapshot` struct fields in `inference-sim/sim/routing.go` and the `EffectiveLoad()` method signature. Verified: `EffectiveLoad()` returns `QueueDepth + BatchSize + InFlightRequests` (see Task 3 Step 0 pre-flight and `TestSourceSyncVerification::test_method_expansion_matches_source`). `ROUTING_SNAPSHOT_FIELDS` dict derived from `type RoutingSnapshot struct` at pinned commit. **No DEVIATION in submodule API** (struct fields, method signatures). **Note:** The macro plan's signal enumeration (5 candidate signals) differs from the actual EVOLVE-BLOCK signals (6 signals) — this is a signal list deviation documented in Deviation Log (Section D), not a submodule API deviation.
- **llm-d-inference-scheduler:** PR1 references the `scheduling.Scorer` interface for the mapping artifact's Scorer Interface Reference section. The commit hash is pinned in `blis_to_llmd_mapping.md`. PR1 does NOT call any llm-d-inference-scheduler API at runtime — the reference is documentation-only for PR2/PR3 consumption. **F-6 fix: UNVERIFIED — the Scorer Interface Reference section (Score method signature, factory pattern, existing scorers) was documented from design knowledge, not verified against the actual source at the pinned commit.** PR3 MUST derive the full interface specification directly from the `llm-d-inference-scheduler` codebase (see the "Do not rely solely on this summary" note in the Scorer Interface Reference). No runtime dependency in PR1.
- **llm-d-benchmark:** Not referenced by PR1. **No DEVIATION.**
- **LoadEvolvedBlock API:** Macro plan R6 requires verifying this API. `grep -r 'LoadEvolvedBlock' inference-sim/` confirms it does NOT exist. Decision: shim approach in PR3. Documented in `docs/transfer/README.md`. **DEVIATION: R6 shim deferred to PR3** (see Deviation Log, Section D).

### 2) Workspace Artifact Chain Verification

PR1 produces one workspace artifact:

| Artifact | Writer | Reader(s) | Required Fields |
|----------|--------|-----------|-----------------|
| `workspace/algorithm_summary.json` | `transfer_cli.py extract` (PR1) | `validate-schema` (PR1), Stage 1-3 prompt templates (PR3), Go harness (PR3) | `algorithm_name`, `evolve_block_source`, `evolve_block_content_hash`, `signals[]`, `composite_signals[]`, `metrics{}`, `scope_validation_passed`, `mapping_artifact_version`, `fidelity_checked` (F-7/F-22 fix: 9 required fields) |

Schema file `tools/schemas/algorithm_summary.schema.json` defines all required fields. `validate-schema` round-trip test (`TestRoundTrip::test_extract_then_validate_schema_round_trip`) verifies writer output matches reader input. **No DEVIATION.**

### 3) Predecessor Artifact Verification

PR1 is the first PR — no predecessor artifacts required. PR1 reads from:

- `routing/best_program.py` — exists, contains EVOLVE-BLOCK markers (verified in Task 3 Step 0)
- `routing/best_program_info.json` — exists, contains `metrics` key (verified in Task 3 Step 0)

These are input artifacts from the evolutionary optimization pipeline, not predecessor PR artifacts.

**F-17 fix: Referenced documents (not predecessor artifacts):** The plan references:
- `docs/contributing/pr-workflow.md` — referenced in PR Category (Part 1) and Sanity Checklist. This file is assumed to exist in the repository; it is NOT created by PR1. If it does not exist, the PR Category reference is advisory only and does not block implementation.
- `docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md` — referenced in README.md (Task 1). This file must exist for the README link to be valid. Verify with: `ls docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md`.

**No DEVIATION.**

### 4) Commit Pin Verification

- **inference-sim:** Current submodule HEAD recorded via `git submodule status`. `ROUTING_SNAPSHOT_FIELDS` and `METHOD_EXPANSIONS` dicts pinned to this commit. `TestSourceSyncVerification` tests verify dicts match source at test time. CI enforces via `test_ci_must_not_skip_sync_tests`. **F-5 fix: Recording mechanism:** The inference-sim commit hash is NOT recorded as a static value in the plan or mapping artifact. Instead, it is verified dynamically: `TestSourceSyncVerification` tests compare the hardcoded dicts against the inference-sim source at the currently checked-out commit. The hash is implicit in `git submodule status` output. To obtain the current hash: `cd inference-sim && git rev-parse HEAD`. The plan executor MUST run this command during Task 3 Step 0 and verify the output matches the submodule pointer in `.gitmodules`. **No DEVIATION in submodule API.**
- **llm-d-inference-scheduler:** Commit hash pinned in `blis_to_llmd_mapping.md` (Task 5). The hash is `PLACEHOLDER_REQUIRES_STEP_2` until Task 5 Step 2 replaces it with the actual hex hash from `cd llm-d-inference-scheduler && git rev-parse HEAD`. No automated CI verification of this pin in PR1 (acceptable — see F-6 note in convergence review). PR3 will verify at implementation time. **No DEVIATION.**

**Summary:** No unresolved DEVIATION flags from Phase 0. One known deferral (R6 shim → PR3) documented in Deviation Log.

---

## Part 1: Design Validation

### A) Executive Summary

This PR creates the transfer pipeline's foundation layer: (1) a signal mapping artifact documenting how 6 simulation routing signals (5 RoutingSnapshot fields + 1 request-level field) correspond to production llm-d-inference-scheduler concepts, (2) a Python CLI with extract/validate commands, (3) JSON Schema infrastructure for workspace artifact validation, and (4) project scaffolding (directories, .gitignore, CLAUDE.md, README).

**Where it fits:** First PR in a 6-PR series. PR2 (scorer template) and PR3 (prompt templates + harness) both depend on the mapping artifact and CLI created here. No predecessor PRs.

**Adjacent blocks:** Reads from `routing/` input artifacts (EVOLVE-BLOCK, metrics). Reads from `llm-d-inference-scheduler` submodule for scorer interface documentation. Produces artifacts consumed by PR2 (mapping) and PR3+ (CLI, schemas).

**DEVIATION flags:** The v3 macro plan lists 5 signals as "(1) queue depth per endpoint, (2) in-flight request count, (3) estimated latency, (4) request token count, (5) KV cache utilization." However, the actual EVOLVE-BLOCK in `routing/best_program.py:171-242` accesses: QueueDepth, BatchSize, InFlightRequests, KVUtilization, CacheHitRate (plus SessionID as a boolean check). The extract command will produce the authoritative list. See Deviation Log (Section D).

### B) Behavioral Contracts

**Positive Contracts:**

BC-1: Extract Produces Valid Summary
- GIVEN `routing/best_program.py` with EVOLVE-BLOCK markers and `routing/best_program_info.json` with metrics
- WHEN `python tools/transfer_cli.py extract routing/` is run
- THEN `workspace/algorithm_summary.json` is created with all required fields (`algorithm_name`, `evolve_block_source`, `signals[]`, `metrics{}`, `scope_validation_passed`) and the output JSON reports `status: "ok"`
- MECHANISM: CLI parses `best_program.py` for EVOLVE-BLOCK markers, extracts signal access patterns via regex, reads metrics from `best_program_info.json`

BC-2: Extract Identifies All Signals
- GIVEN the EVOLVE-BLOCK references RoutingSnapshot fields and/or request-level fields
- WHEN extract runs
- THEN `algorithm_summary.json` `signals[]` contains an entry for each RoutingSnapshot field and request-level field accessed in the EVOLVE-BLOCK, with `name`, `type`, and `access_path` fields
- MECHANISM: Regex patterns match `snap.FieldName`, `snapshots[i].FieldName` patterns for RoutingSnapshot fields, and `req.FieldName` patterns for request-level fields within the EVOLVE-BLOCK region

BC-3: Validate-Mapping Checks Completeness (Bidirectional)
- GIVEN a mapping artifact at `docs/transfer/blis_to_llmd_mapping.md`
- WHEN `python tools/transfer_cli.py validate-mapping` is run
- THEN the output reports whether (a) all signals from `algorithm_summary.json` have corresponding entries in the mapping (missing signals), (b) all signal rows in the mapping have corresponding signals in the algorithm summary (extra signals), and (c) the commit hash is present
- MECHANISM: CLI parses Markdown table rows, cross-references bidirectionally against algorithm summary signals. Missing signals indicate incomplete mapping; extra signals indicate stale/spurious mapping rows that may mislead downstream consumers.

BC-4: Validate-Schema Enforces Structure
- GIVEN a workspace JSON artifact and a schema file in `tools/schemas/`
- WHEN `python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json`
- THEN the output reports validation status: required field presence, field type correctness, enum membership
- MECHANISM: Custom lightweight validator using stdlib `json` + type introspection (~100 lines)

BC-5: Scope Validation Rejects Out-of-Scope Algorithms
- GIVEN an algorithm that references P/D disaggregation constructs or non-routing signals
- WHEN extract runs with scope validation
- THEN `scope_validation_passed` is `false` and `errors[]` contains a diagnostic message
- MECHANISM: Extract checks signal categories against a routing-only allowlist

**Negative Contracts:**

BC-6: Low-Fidelity Signal Halts (Conditional)
- GIVEN a signal in the EVOLVE-BLOCK that is rated `low` fidelity in the mapping artifact
- WHEN extract runs and the mapping artifact exists at `docs/transfer/blis_to_llmd_mapping.md`
- THEN the CLI exits with code 1 and the output `status` is `"error"` with a diagnostic in `errors[]`
- MECHANISM: Extract cross-references signals against mapping artifact fidelity ratings when the mapping artifact is present
- **Known limitation:** If the mapping artifact does not exist, the fidelity check is skipped with a stderr warning and extract proceeds as if all signals are acceptable. This means BC-6 is only enforced after Task 5 (mapping artifact creation). This is intentional — extract must be runnable before the mapping artifact exists (e.g., during initial signal discovery). **Non-determinism boundary:** The same EVOLVE-BLOCK produces different results depending on mapping artifact presence: without it, all signals pass; with it, low-fidelity signals halt. This non-determinism is bounded — once the mapping artifact exists (after Task 5), fidelity checks are always enforced. The only scenario where checks revert to skipped is if the mapping artifact is accidentally deleted, which is a low-probability operational concern detectable by `validate-mapping`. **Mitigation:** CI pipelines MUST use `--strict` (which fails if the mapping is absent), and `validate-mapping` independently verifies the mapping exists. Together, these ensure accidental deletion is caught before merge.
- **CI determinism: `--strict` flag (REQUIRED for CI).** To enforce deterministic behavior in CI pipelines, the `extract` command accepts a `--strict` flag. When `--strict` is passed: (1) if the mapping artifact does not exist, extract exits with code 1 (validation failure — the fidelity check cannot pass without the mapping, which is a validation outcome, not an infrastructure error) instead of skipping the fidelity check; (2) all fidelity checks are mandatory, not optional. **F-16 fix:** Previously documented as exit code 2 (infrastructure error), corrected to exit code 1 to match the implementation in `_check_fidelity` which returns validation errors processed by `_output("error", 1, ...)`. This eliminates the state-dependent behavior for automated environments. **Default behavior (no flag) is unchanged** — interactive/exploratory use still allows extract to run without the mapping artifact. **CI REQUIREMENT:** CI pipelines MUST use `python tools/transfer_cli.py extract --strict routing/` to ensure reproducible results. **Hard enforcement:** When the `CI` environment variable is set, extract **fails with exit code 1** if `--strict` is not passed. This is a hard gate, not a warning — a warning to stderr is insufficient because CI systems do not fail on stderr output. This is enforced by `TestCIStrictEnforcement::test_ci_env_requires_strict_flag` which asserts exit code 1 when `CI` is set and `--strict` is absent.
- **BOOTSTRAP WORKFLOW (F-23 fix):** The extract/strict/mapping dependency has a clear bootstrap sequence: (1) **Tasks 1-4 (before mapping exists):** Run `extract` without `--strict` locally to discover signals — fidelity checks are skipped, exit 0. CI does not run extract yet (no CI workflow until Task 7). (2) **Task 5 (mapping created):** The mapping artifact is written using extract's signal output. After committing the mapping, `extract` (with or without `--strict`) now performs fidelity checks. (3) **Task 7 (CI workflow added):** CI runs `extract --strict routing/` — `--strict` requires the mapping to exist (it does, committed in Task 5). The chicken-and-egg is resolved by task ordering: extract runs first without strict to produce signal data, the mapping is created from that data, then strict mode is enabled for CI. **Key invariant:** `--strict` is never required until after the mapping artifact exists.
- **LOCAL-VS-CI DIVERGENCE RISK:** Without `--strict`, local developers experience state-dependent behavior: the same EVOLVE-BLOCK produces exit 0 (with mapping artifact) or exit 0 with skipped fidelity checks (without mapping artifact). With `--strict` in CI, the same missing-mapping scenario produces exit 1 (validation failure). **This means local and CI results can diverge.** The `--strict` flag converts non-determinism into a hard failure rather than eliminating the underlying state-dependency. **Recommendation for local development:** Developers SHOULD use `--strict` locally after Task 5 (mapping artifact creation) to match CI behavior. Add `alias transfer-extract='python tools/transfer_cli.py extract --strict routing/'` to development setup instructions. **F-5 fix — Enforcement strengthening:** Document this alias in CLAUDE.md's Development section and in `docs/transfer/README.md` so it is discoverable. While enforcing `--strict` locally is not mandatory (developers may need non-strict mode for signal discovery), the alias makes the recommended workflow the path of least resistance. **R2-F-3 fix — Non-strict mode visibility:** When running without `--strict` and the mapping artifact exists, the extract command MUST emit a stderr notice: `NOTICE: Running without --strict. Fidelity checks are active (mapping artifact found), but CI uses --strict for deterministic enforcement. Use --strict locally to match CI behavior.` When running without `--strict` and the mapping artifact is absent (bootstrap phase), the existing WARNING is sufficient. This notice makes the local/CI divergence visible without blocking the developer, turning the advisory recommendation into an active reminder. **Residual risk:** Before Task 5 completes, local extract without `--strict` skips fidelity checks silently. This is an intentional bootstrap compromise — extract must be runnable before the mapping exists for initial signal discovery. After the mapping artifact is committed, the non-determinism window closes for normal development workflows. The only re-opening scenario is accidental mapping deletion, which `validate-mapping` detects.

BC-7: No External Dependencies
- GIVEN the CLI source code
- WHEN inspecting imports
- THEN only Python stdlib modules are imported (json, re, pathlib, subprocess, argparse, sys, hashlib)
- MECHANISM: No `pip install`, no `requirements.txt`, no third-party imports

**Error Handling Contracts:**

BC-8: Missing Input Files or Missing EVOLVE-BLOCK Markers
- GIVEN `routing/best_program.py` does not exist, OR the file exists but contains no `EVOLVE-BLOCK-START`/`EVOLVE-BLOCK-END` markers
- WHEN extract is run
- THEN the CLI exits with code 2 (infrastructure error) and `errors[]` contains a diagnostic
- MECHANISM: File existence and marker presence checked before parsing
- **Distinction from exit 1:** If the file exists AND markers are found but no recognizable signal patterns are found within the EVOLVE-BLOCK, that is exit 1 (validation failure, not infrastructure error). See exit code boundary clarification in Section F.

BC-9: Malformed Mapping Artifact
- GIVEN a mapping artifact with missing or malformed table rows
- WHEN validate-mapping is run
- THEN the CLI exits with code 1 and reports which rows are missing or malformed
- MECHANISM: Markdown table parser validates expected column count and non-empty cells

BC-10: Invalid Schema File
- GIVEN a schema file that is not valid JSON or references unsupported features
- WHEN validate-schema is run
- THEN the CLI exits with code 2 with a diagnostic about the schema parse error
- MECHANISM: Schema file loaded and validated for supported subset before artifact validation

**Cross-PR Contracts:**

BC-11: Content Hash Drift Detection (PR1 provides, PR3 MUST consume)
- GIVEN `workspace/algorithm_summary.json` produced by extract, containing `evolve_block_content_hash`
- WHEN a downstream stage (PR3) reads the algorithm summary to parse EVOLVE-BLOCK logic
- THEN the downstream stage MUST recompute SHA-256 of the EVOLVE-BLOCK at the location in `evolve_block_source`, compare it to `evolve_block_content_hash`, and abort if they differ
- MECHANISM (PR1 side): Extract computes SHA-256 of EVOLVE-BLOCK content and stores it in `evolve_block_content_hash`. The hash is validated by `TestExtract::test_extract_content_hash_matches_evolve_block` (independent recomputation) and by `TestHashDriftDetection::test_hash_detects_source_modification` (modified source produces different hash).
- MECHANISM (PR3 side — REQUIRED): PR3 MUST include a test `test_stale_hash_aborts_parsing` that: (1) runs extract to produce a summary, (2) modifies the EVOLVE-BLOCK source, (3) attempts to parse the EVOLVE-BLOCK using the now-stale summary, (4) asserts the parsing aborts with a drift detection error. **This test skeleton is documented in `tools/test_transfer_cli.py` as `TestHashDriftDetection` to establish the PR1-side mechanism; PR3 must implement the consumer-side equivalent.**
- **Enforcement:** PR1 cannot enforce PR3 behavior at runtime, but the contract is testable: PR3's CI MUST include a test verifying hash comparison. The PR3 plan review MUST check for this test. If PR3 omits hash verification, it violates BC-11. **PR3 plan convergence-review gate:** The PR3 convergence review MUST include a finding if `test_stale_hash_aborts_parsing` is absent. This is a CRITICAL-severity gate for PR3, not PR1. **F-4 fix — Asymmetric enforcement acknowledgment:** Cross-PR contract enforcement is inherently asymmetric — PR1 provides the mechanism and tests proving it works, but cannot programmatically enforce that PR3 consumes the hash. The mitigation layers are: (1) PR1 test `test_hash_detects_source_modification` proves the hash mechanism works, (2) PR3 convergence-review gate is documented as CRITICAL-severity, (3) this contract is prominently documented in BC-11, Review Guide, and CLAUDE.md. If automated enforcement is desired in the future, a shared test in `tools/test_cross_pr_contracts.py` could verify that PR3's test file contains `test_stale_hash_aborts_parsing`. **R2-F-4 fix — Cross-PR discoverability:** To ensure PR3 implementers encounter these requirements, PR1 MUST add a `## Cross-PR Contracts (PR3 Obligations)` section to `docs/transfer/README.md` listing all PR3 gates with severity levels: (1) `test_stale_hash_aborts_parsing` — CRITICAL, (2) normalization_note application test — CRITICAL, (3) unknown-type signal rejection — IMPORTANT. Additionally, CLAUDE.md's transfer pipeline section MUST include a note: `When implementing PR3, read docs/transfer/README.md § Cross-PR Contracts first.` This makes the contracts discoverable from two entry points PR3 implementers will naturally consult.
- **Comment sensitivity (F-16 note):** The content hash is computed on the entire EVOLVE-BLOCK content, including comments. This means non-semantic changes (reformatting, comment edits) within the EVOLVE-BLOCK will trigger hash drift detection in PR3, causing a pipeline halt. This is intentionally conservative: distinguishing semantic from non-semantic changes within Go code embedded in a Python string is fragile and error-prone. The recovery is straightforward — re-run `extract` to produce a new hash reflecting the current EVOLVE-BLOCK state. The golden-file test (`TestGoldenSignalList`) will catch any actual signal changes.

### C) Component Interaction

```
[routing/best_program.py]     [routing/best_program_info.json]
         |                              |
         v                              v
  tools/transfer_cli.py extract ────────┘
         |
         v
  workspace/algorithm_summary.json
         |
         v
  tools/transfer_cli.py validate-schema
         |                     |
         v                     v
  tools/schemas/*.schema.json  (validation result)

  [docs/transfer/blis_to_llmd_mapping.md]
         |
         v
  tools/transfer_cli.py validate-mapping
         |
         v
  (validation result: complete/incomplete)
```

**API Contracts:**

CLI interface (all commands):
- Input: command-line arguments
- Output: JSON to stdout, exit code 0/1/2
- `extract <routing_dir>` → produces `workspace/algorithm_summary.json` + stdout JSON status
- `validate-mapping [--summary <path>]` → stdout JSON with `status`, `errors[]`, `output_type`, `mapping_complete`, `missing_signals[]`, `extra_signals[]`, `stale_commit` (R5-F-10 fix: added common fields `status`, `errors[]`, `output_type` produced by `_output()` helper)
- `validate-schema <artifact_path>` → stdout JSON with `status`, `errors[]`, `output_type`, `violations[]` (R5-F-10 fix: added common fields)

**Two-output design (extract command):** The `extract` command produces two distinct JSON outputs that serve different purposes:
1. **File artifact** (`workspace/algorithm_summary.json`): Contains the 6 schema-required keys (`algorithm_name`, `evolve_block_source`, `evolve_block_content_hash`, `signals`, `metrics`, `scope_validation_passed`). This is the durable artifact consumed by downstream pipeline stages and validated by `validate-schema`.
2. **Stdout JSON**: Contains operational metadata (`status`, `errors`, `artifact_path`, `algorithm_name`, `signal_count`). This is for CLI user feedback and CI integration — it is NOT the artifact and is NOT validated by the schema.

The schema (`algorithm_summary.schema.json`) validates only the file artifact, not stdout. These are intentionally different structures: the file is a pipeline contract, stdout is an operational report.

**Non-strict mode caveat (F-12):** In non-strict (exploratory) mode, extract may produce a file artifact that fails `validate-schema` — specifically when `metrics.combined_score` is missing from `best_program_info.json`. This is intentional: non-strict mode prioritizes signal discovery over schema compliance. The artifact is useful for inspecting extraction results but is NOT pipeline-ready. Always use `--strict` for pipeline-ready artifacts. The warning message on stderr explicitly notes schema validation will fail.

**WARNING for downstream PR implementers:** PR2/PR3 stages MUST consume the file artifact (`workspace/algorithm_summary.json`), NOT the stdout JSON. The stdout JSON is for human/CI feedback only and is not schema-validated. This distinction should also be documented in CLAUDE.md.

**Enforcement mechanism:** The schema's `additionalProperties: false` provides structural enforcement. The stdout JSON contains `output_type` and `status` fields that are NOT in the schema, so running `validate-schema` on stdout JSON would fail with "unexpected additional property" errors. The test `test_extract_stdout_differs_from_file_artifact` explicitly verifies the structures differ. This means accidental consumption of stdout instead of the file artifact would be caught by schema validation. **F-23 fix — Suggested test (optional, for defense-in-depth):** `test_validate_schema_rejects_stdout_json` — capture extract's stdout JSON, write it to a temp file, run `validate-schema` on it, and verify it fails with an "additional property" violation for `output_type`. This would directly verify the documented enforcement mechanism rather than relying on structural difference assertions. Deferred because the existing four defense layers (different structures, `additionalProperties:false`, `test_extract_stdout_differs_from_file_artifact`, CLAUDE.md warning) provide adequate coverage for a developer tool. **PR3 defense-in-depth:** PR3 should load the artifact via `json.load(open("workspace/algorithm_summary.json"))` and immediately run `validate_artifact()` on it. If the loaded data contains an `output_type` key, it's the wrong output — abort with a clear error message.

**State Changes:**
- `extract` creates/overwrites `workspace/algorithm_summary.json` — **F-20 note: overwrite is intentional.** The `workspace/` directory is gitignored and designed for inter-stage artifacts. Each extract run produces the current state; previous artifacts are replaced without backup or confirmation. This is standard pipeline tool behavior. If a user needs to preserve a prior extract result, they should copy it before re-running. Running extract with a different routing directory silently replaces the prior result.
- `validate-mapping` and `validate-schema` are read-only (no side effects)

**Extension Friction:**
- New signal = 1 mapping row in `blis_to_llmd_mapping.md` + 1 test case (2 files)
- New CLI command = 1 function + 1 argparse subparser + tests (1-2 files)

### D) Deviation Log

| Source Says | Micro Plan Does | Reason |
|-------------|-----------------|--------|
| 5 signals: queue depth, in-flight count, estimated latency, request token count, KV cache utilization | Extract discovers 6 signals from EVOLVE-BLOCK: QueueDepth, BatchSize, InFlightRequests, KVUtilization, CacheHitRate (RoutingSnapshot fields) + SessionID (request-level boolean check). Actual list may differ from design doc. | CORRECTION (F-21 fix: macro plan update DEFERRED — out of scope for PR1): The macro plan delegates signal enumeration to PR1 by specifying that PR1 must extract signals from the actual EVOLVE-BLOCK code. The extract command is therefore the authoritative source for the signal list. The EVOLVE-BLOCK accesses `EffectiveLoad()` (= QueueDepth + BatchSize + InFlightRequests), `CacheHitRate`, `KVUtilization`, and checks `req.SessionID`. **Why macro plan signals are absent:** "Estimated latency" and "request token count" were listed in the macro plan's initial design analysis as *candidate* signals but are NOT directly accessed in the actual EVOLVE-BLOCK code. The evolutionary optimizer discovered a solution that uses different signals (BatchSize, CacheHitRate, SessionID) instead. Since PR1 extracts from the actual evolved code rather than design-time assumptions, these signals correctly do not appear. The extract command matches both RoutingSnapshot field patterns (`snap.Field`) and request-level field patterns (`req.Field`) to capture all 6 signals. **F-1 fix — Macro plan update recommendation (DEFERRED to post-PR1):** The macro plan's signal enumeration should be updated to reflect the actual 6-signal list discovered by PR1 extract, replacing the 5-signal candidate list. This update is out of scope for PR1 because PR1 does not modify the macro plan document. Until the macro plan is updated, this Deviation Log entry is the authoritative reference for the actual signal list. |
| R6: LoadEvolvedBlock API verified or shim created in PR1 | PR1 documents that LoadEvolvedBlock does NOT exist. Records decision for PR3 to implement the shim in `tools/harness/`. PR1 provides `evolve_block_source`, `evolve_block_content_hash`, and `signals[]` as the contract for PR3. PR3 must produce `tools/harness/evolved_scorer.go` implementing `scheduling.Scorer`. | DEFERRAL: The macro plan says "PR1 must include a shim" OR "confirm API exists." Since the shim is Go code in `tools/harness/` and PR1 is Python-only, the shim implementation belongs in PR3. PR1 documents the gap, decision, and PR3 contract in `docs/transfer/README.md`. |
| `transfer_cli.py` has `extract`, `validate-mapping`, `validate-schema` | Same | No deviation |
| `tools/schemas/algorithm_summary.schema.json` created in PR1 | Same | No deviation |
| CLAUDE.md updated | CLAUDE.md created (does not exist yet) | ADDITION: File doesn't exist; we create it. |
| Macro plan required fields: 8 fields (no `fidelity_checked`) | Schema required fields: 9 fields (includes `fidelity_checked`) | ADDITION (F-22 fix, R2-F-11 clarification): `fidelity_checked` was added as a required field in the schema (R2-F-11 fix) to track whether fidelity checks were performed during extraction. The macro plan's workspace artifact specification lists 8 required fields (`algorithm_name`, `evolve_block_source`, `evolve_block_content_hash`, `signals`, `composite_signals`, `metrics`, `scope_validation_passed`, `mapping_artifact_version`); the schema adds `fidelity_checked` as the 9th. All 8 macro plan fields are preserved; `composite_signals` and `mapping_artifact_version` were part of the macro plan's original 8-field specification, not PR1 additions. |

### E) Review Guide

1. **THE TRICKY PART:** The EVOLVE-BLOCK signal extraction logic (BC-2). The regex must handle both direct field access (`snap.QueueDepth`) and method calls (`snap.EffectiveLoad()`), and must correctly attribute composite signals (EffectiveLoad = QueueDepth + BatchSize + InFlightRequests) to their constituent fields. Getting this wrong means the mapping artifact is incomplete. **Automated safeguard:** `TestGoldenSignalList::test_extracted_signals_match_golden_list` compares the extract output against a manually-verified expected signal set (`EXPECTED_SIGNALS`). This test catches both missed signals (regex gaps) and spurious signals (false positives). If the EVOLVE-BLOCK changes, the golden list must be independently re-verified by manual inspection — do NOT auto-derive it from extract output.

2. **WHAT TO SCRUTINIZE:** The mapping artifact signal list (does it match the extracted signals?), the fidelity ratings (are they defensible?), and the schema validation logic (does the restricted-subset validator cover all workspace artifact needs?).

3. **WHAT'S SAFE TO SKIM:** Directory creation, .gitignore updates, CLAUDE.md boilerplate, README prose.

4. **KNOWN DEBT:** The R6 shim is deferred to PR3. The signal list may need updating after extraction confirms the actual EVOLVE-BLOCK signals — the mapping artifact should be reviewed again at PR3 time.

5. **KNOWN GAP: Algorithm logic not captured in PR1.** The `algorithm_summary.json` schema contains signal metadata and EVOLVE-BLOCK source location but no representation of the algorithm's behavioral logic (scoring formulas, penalty functions, thresholds). PR3 must re-parse the EVOLVE-BLOCK at the location specified by `evolve_block_source` to extract this logic. **Drift detection (BC-11):** PR1 includes `evolve_block_content_hash` (SHA-256 of the EVOLVE-BLOCK content at extraction time). PR3 MUST recompute this hash before parsing and abort if it differs — this indicates the source changed since extraction and the signal list may be stale. **Enforcement:** BC-11 (Cross-PR Contract) formalizes this requirement. PR1 provides: (a) the hash mechanism, (b) `TestHashDriftDetection::test_hash_detects_source_modification` proving the mechanism works, (c) `TestExtract::test_extract_content_hash_matches_evolve_block` proving independent recomputation matches. PR3 MUST provide: a test `test_stale_hash_aborts_parsing` that verifies the consumer-side abort behavior. The PR3 plan review MUST verify this test exists. **Hash normalization (F-18):** The hash is computed on the raw EVOLVE-BLOCK content as read from disk. Git normalizes line endings on checkout, so the hash is consistent within a checkout. Cross-platform concerns (CRLF vs LF) are mitigated by Git's `core.autocrlf` setting. PR3 MUST use the same normalization: read the file, split by `\n`, join the EVOLVE-BLOCK lines with `\n`, and hash. Do NOT strip trailing whitespace — hash the content as-is. **F-27 fix — Marker line inclusion:** The hash range **includes** the `# EVOLVE-BLOCK-START` and `# EVOLVE-BLOCK-END` marker lines themselves. Specifically: `block = "\n".join(lines[start_idx:end_idx + 1])` where `start_idx` is the line containing `EVOLVE-BLOCK-START` and `end_idx` is the line containing `EVOLVE-BLOCK-END`. PR3 MUST use the same inclusive range when recomputing the hash — excluding markers will produce a different hash and trigger a false drift detection. **Cross-platform safeguard (F-17):** If the repository is used across platforms with different `core.autocrlf` settings, the hash may differ. To detect this: `TestExtract::test_extract_is_deterministic` verifies consistent hashing within a single checkout. If cross-platform hashing becomes an issue, add explicit `\r\n` → `\n` normalization before hashing in both extract and PR3's consumer.

---

## Part 2: Executable Implementation

### F) Implementation Overview

**Files to create:**
- `tools/transfer_cli.py` — Main CLI with extract, validate-mapping, validate-schema commands
- `tools/schemas/algorithm_summary.schema.json` — JSON Schema for algorithm_summary workspace artifact
- `tools/__init__.py` — Empty (makes tools importable for tests)
- `tools/test_transfer_cli.py` — pytest test suite for all CLI commands
- `docs/transfer/README.md` — Transfer pipeline overview + R6 decision
- `docs/transfer/blis_to_llmd_mapping.md` — Signal mapping artifact
- `CLAUDE.md` — Project-level Claude instructions with transfer pipeline section
- `.github/workflows/test.yml` — CI configuration (F-2: submodule checkout for TestSourceSyncVerification, F-1: --strict enforcement)

**Files to modify:**
- `.gitignore` — Add `workspace/` exclusion

**Key decisions:**
- Python stdlib-only (no jsonschema library — custom validator)
- Regex-based EVOLVE-BLOCK parsing (not AST — the code is Go embedded in Python string)
- Exit codes: 0 = success, 1 = validation failure, 2 = infrastructure error
- `--strict` flag on extract: requires mapping artifact to exist, enforces deterministic fidelity checks (for CI)
- All commands output JSON to stdout
- **Algorithm logic extraction is out of scope for PR1.** The extract command captures signal metadata (names, types, access paths) and the EVOLVE-BLOCK source location, but does NOT extract the algorithm's behavioral logic (scoring weights, penalty functions, decision thresholds, EffectiveLoad computation). PR3 must independently parse the EVOLVE-BLOCK to extract algorithm logic for the shim. The contract between PR1 and PR3 is:
  - **PR1 provides to PR3:** `evolve_block_source` (file path + line range), `evolve_block_content_hash` (SHA-256 for drift detection), and `signals[]` (names, types, access paths of production metrics to wire up).
  - **PR3 is responsible for:** (1) Verifying the content hash matches before parsing, (2) parsing the scoring/penalty logic from the EVOLVE-BLOCK, (3) producing a Go shim in `tools/harness/` that instantiates `WeightedScoring` with the evolved logic. PR3's output format for the extracted algorithm logic is: a Go source file in `tools/harness/evolved_scorer.go` implementing the `scheduling.Scorer` interface, with the scoring formula, penalty functions, and EffectiveLoad computation translated from the EVOLVE-BLOCK's Python/Go hybrid into pure Go. The PR3 plan must define the exact shim struct and method signatures.
  - **Precise boundary definition:** PR1 captures *what signals the algorithm reads* (signal metadata: names, types, access paths, normalization). PR3 captures *what the algorithm does with those signals* (behavioral logic: scoring weights, penalty functions, decision thresholds, composite computations like EffectiveLoad). Concretely, "algorithm logic" means: (a) scoring weights applied to each signal, (b) penalty functions (e.g., low-KV penalty), (c) decision thresholds (e.g., score cutoffs), (d) composite signal computations (e.g., EffectiveLoad formula), (e) conditional branching logic (e.g., SessionID affinity check). PR1 provides the signal wiring; PR3 provides the behavioral implementation. **R2-F-14 fix — Boundary verification test:** The boundary is testable via the content hash mechanism: PR1's `algorithm_summary.json` contains the full signal list and the `evolve_block_content_hash`. PR3's shim MUST produce output that depends on all signals in the summary (coverage test) and MUST abort if the content hash drifts (contract test). The 5-category enumeration is documented in `docs/transfer/README.md` § Algorithm Logic Boundary so PR3 implementers have a single reference point. PR3's plan MUST define the intermediate representation for each category — this is where the natural-language definition becomes a concrete schema.

**Exit code boundary clarification:** Exit code 2 (infrastructure error) means the CLI cannot produce a meaningful result due to missing/malformed inputs (file not found, unparseable JSON, no EVOLVE-BLOCK markers). Exit code 1 (validation failure) means the CLI parsed the inputs successfully but the content fails a validation check (no recognizable signals, low-fidelity signal, scope violation, schema mismatch). Specifically: "No routing signals found in EVOLVE-BLOCK" is exit 1 because the EVOLVE-BLOCK was successfully located and parsed, but its content failed signal recognition — this is a validation outcome, not an infrastructure failure.

**Confirmation:** No dead code — all CLI commands exercised by tests, mapping consumed by PR3, schemas consumed by validate-schema.

### G) Task Breakdown

---

#### Task 1: Project Scaffolding + .gitignore

**Contracts Implemented:** (none — infrastructure only)

**Files:**
- Create: `docs/transfer/README.md`
- Create: `tools/__init__.py`
- Modify: `.gitignore`

**Step 1: Create directory structure and .gitignore update**

Context: We need the directory tree before any other task can write files.

Create `docs/transfer/README.md`:
```markdown
# Sim-to-Production Transfer Pipeline

Pipeline for transferring simulation-discovered routing algorithms to production
llm-d-inference-scheduler scorer plugins.

## Status

Under construction. See `docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md`.

## Directory Layout

- `docs/transfer/` — Mapping artifacts, scorer template, calibration log
- `tools/` — Python CLI + Go test harness (PR3)
- `tools/schemas/` — JSON Schema files for workspace artifact validation
- `prompts/` — Pipeline stage prompt templates (PR3)
- `workspace/` — Inter-stage JSON artifacts (gitignored)

## R6 Resolution: LoadEvolvedBlock API

**Status:** UNVERIFIED — `LoadEvolvedBlock` does not exist in inference-sim.

**Decision:** Use shim approach (option a). The Go test harness (`tools/harness/`, PR3)
will directly instantiate `WeightedScoring` from parsed algorithm parameters rather than
relying on a dedicated `LoadEvolvedBlock` API. This keeps all sim2real PRs independent
of inference-sim changes.

**Rationale:** The evolved algorithm's EVOLVE-BLOCK modifies the `Route()` method body
of `WeightedScoring`. A shim can reconstruct this by:
1. Parsing the evolved scoring/penalty logic from the EVOLVE-BLOCK
2. Creating a `WeightedScoring` instance with standard scorers
3. Wrapping it with the evolved penalty logic as a post-scoring step

This avoids requiring an inference-sim API PR (option b) which would block PR3.

**Shim acceptance criteria (PR3):** The shim in `tools/harness/evolved_scorer.go` MUST:
1. Implement `scheduling.Scorer` interface (Score method with correct signature)
2. Accept all signals from `algorithm_summary.json` `signals[]` as input
3. Reproduce the scoring/penalty logic from the EVOLVE-BLOCK
4. Verify `evolve_block_content_hash` before parsing (BC-11)
5. Pass unit tests comparing shim output against simulation output for reference inputs

## Cross-PR Contracts (PR3 Obligations)

R5-F-6 fix: Machine-readable list of PR3 gates with severity levels for cross-PR discoverability.

PR3 MUST implement and pass these gates before merging:

1. **`test_stale_hash_aborts_parsing`** — CRITICAL. PR3 must include a test that runs extract, modifies the EVOLVE-BLOCK source, attempts to parse using the stale summary, and asserts parsing aborts with a drift detection error. (See BC-11.)
2. **KVUtilization normalization test** — CRITICAL. PR3 must include a unit test verifying that production `KVCacheUsagePercent` values (0-100) are divided by 100 before being passed to the scorer.
3. **Unknown-type signal rejection** — IMPORTANT. PR3 must verify that signals with type `"unknown"` are rejected or handled explicitly, not silently passed through to the scorer.

## Prompt Template Contract (PR3)

PR3 prompt templates MUST consume these PR1 artifacts:
- **Input:** `workspace/algorithm_summary.json` (signal metadata, EVOLVE-BLOCK location, content hash)
- **Input:** `docs/transfer/blis_to_llmd_mapping.md` (signal mappings, fidelity ratings, normalization notes)
- **Output:** Generated Go source implementing `scheduling.Scorer`

**Prompt template requirements:**
1. Each prompt MUST include the signal list from `algorithm_summary.json` `signals[]`
2. Each prompt MUST include normalization notes from the mapping artifact for any signal where sim/prod units differ (e.g., KVUtilization: divide prod value by 100)
3. Each prompt MUST reference `evolve_block_source` to locate the algorithm logic
4. Generated code MUST be validated by `validate-schema` before downstream consumption
```

Create `tools/__init__.py` (empty file).

Append to `.gitignore`:
```
# Transfer pipeline workspace (inter-stage artifacts)
workspace/
```

**Step 2: Verify structure and R6 claim**

Run: `ls -la docs/transfer/ tools/ && grep workspace .gitignore`
Expected: directories exist, `workspace/` line present in .gitignore

Run: `test -d inference-sim/ && grep -r 'LoadEvolvedBlock' inference-sim/ 2>/dev/null || { test -d inference-sim/ && echo "CONFIRMED: LoadEvolvedBlock not found in inference-sim" || echo "ERROR: inference-sim/ directory not found — ensure submodule is checked out before verifying R6 claim"; }`
Expected: "CONFIRMED: LoadEvolvedBlock not found" — this verifies the R6 claim documented in README.md. **Pre-requisite:** `inference-sim/` must exist (submodule checked out). If the directory is missing, the command reports an error instead of a false positive. **Contingency if LoadEvolvedBlock IS found:** (1) Update README.md status from "UNVERIFIED" to "FOUND: LoadEvolvedBlock exists at [location]", (2) assess whether the API provides the functionality needed (loading evolved algorithms into WeightedScoring), (3) if the API is suitable, switch from shim approach to API approach — this changes the PR3 design, (4) update the macro plan with the new approach. This check is a one-time verification during plan execution.

**Step 3: Commit**

```bash
git add docs/transfer/README.md tools/__init__.py .gitignore
git commit -m "$(cat <<'EOF'
feat(transfer): project scaffolding for transfer pipeline

- Create docs/transfer/ with README documenting R6 resolution
- Create tools/ directory
- Add workspace/ to .gitignore
- Document LoadEvolvedBlock API decision (shim in PR3)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 2: JSON Schema + Lightweight Validator

**Contracts Implemented:** BC-4, BC-10

**Files:**
- Create: `tools/schemas/algorithm_summary.schema.json`
- Create: `tools/schema_validator.py`
- Create: `tools/test_schema_validator.py`

**Step 1: Write failing tests for schema validation**

Context: We need a lightweight JSON Schema validator that checks required fields, types, and enums using only stdlib.

```python
# tools/test_schema_validator.py
import json
import pytest
from pathlib import Path
from tools.schema_validator import validate_artifact

SCHEMA_DIR = Path(__file__).parent / "schemas"

@pytest.fixture
def summary_schema():
    with open(SCHEMA_DIR / "algorithm_summary.schema.json") as f:
        return json.load(f)

def _valid_summary():
    return {
        "algorithm_name": "blis_weighted_scoring",
        "evolve_block_source": "routing/best_program.py:171-242",
        "evolve_block_content_hash": "a" * 64,  # placeholder SHA-256 hex digest
        "signals": [
            {"name": "QueueDepth", "type": "int", "access_path": "snap.QueueDepth"}
        ],
        "composite_signals": [
            {"name": "EffectiveLoad", "constituents": ["QueueDepth", "BatchSize", "InFlightRequests"], "formula": "sum"}
        ],
        "metrics": {"combined_score": -3858.94},
        "scope_validation_passed": True,
        "mapping_artifact_version": "1.0",
        "fidelity_checked": True,  # R3-F-3 fix: 9th required field added to match schema
    }

class TestValidateArtifact:
    def test_valid_artifact_passes(self, summary_schema):
        errors = validate_artifact(_valid_summary(), summary_schema)
        assert errors == []

    def test_missing_required_field_fails(self, summary_schema):
        data = _valid_summary()
        del data["algorithm_name"]
        errors = validate_artifact(data, summary_schema)
        assert any("algorithm_name" in e for e in errors)

    def test_wrong_type_fails(self, summary_schema):
        data = _valid_summary()
        data["algorithm_name"] = 123  # should be string
        errors = validate_artifact(data, summary_schema)
        assert any("algorithm_name" in e for e in errors)

    def test_missing_nested_required_field_fails(self, summary_schema):
        data = _valid_summary()
        data["signals"] = [{"name": "QueueDepth"}]  # missing type, access_path
        errors = validate_artifact(data, summary_schema)
        assert len(errors) > 0

    def test_scope_validation_bool_required(self, summary_schema):
        data = _valid_summary()
        data["scope_validation_passed"] = "yes"  # should be bool
        errors = validate_artifact(data, summary_schema)
        assert any("scope_validation_passed" in e for e in errors)

    def test_empty_signals_array_fails_min_items(self, summary_schema):
        """F-13: minItems: 1 constraint rejects empty signals array."""
        data = _valid_summary()
        data["signals"] = []
        errors = validate_artifact(data, summary_schema)
        assert any("minimum" in e.lower() or "minItems" in e.lower() or "0 items" in e for e in errors)

    def test_excess_signals_array_fails_max_items(self, summary_schema):
        """F-10: maxItems: 20 constraint rejects arrays with >20 signals."""
        data = _valid_summary()
        data["signals"] = [
            {"name": f"Signal{i}", "type": "int", "access_path": f"snap.Signal{i}"}
            for i in range(21)
        ]
        errors = validate_artifact(data, summary_schema)
        assert any("maximum" in e.lower() or "21 items" in e for e in errors)

    def test_unexpected_top_level_field_rejected(self, summary_schema):
        """F-13: additionalProperties: false rejects unknown fields."""
        data = _valid_summary()
        data["unexpected_field"] = "should fail"
        errors = validate_artifact(data, summary_schema)
        assert any("unexpected_field" in e for e in errors)

    def test_invalid_hash_pattern_rejected(self, summary_schema):
        """F-18: evolve_block_content_hash must match ^[0-9a-f]{64}$ pattern."""
        data = _valid_summary()
        data["evolve_block_content_hash"] = "invalid_hash"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_hash_with_trailing_chars_rejected(self, summary_schema):
        """F-1: re.fullmatch rejects valid 64-hex-char prefix with trailing chars."""
        data = _valid_summary()
        data["evolve_block_content_hash"] = "a" * 64 + "INVALID"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_absolute_path_evolve_block_source_rejected(self, summary_schema):
        """F-9: evolve_block_source pattern rejects absolute paths."""
        data = _valid_summary()
        data["evolve_block_source"] = "/absolute/path/best_program.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_path_traversal_evolve_block_source_rejected(self, summary_schema):
        """F-16: evolve_block_source pattern rejects relative path traversal."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/../../../etc/passwd.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_dot_slash_evolve_block_source_rejected(self, summary_schema):
        """F-28 fix: evolve_block_source pattern rejects './' current directory reference."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/./best_program.py:1-10"
        errors = validate_artifact(data, summary_schema)
        assert any("pattern" in e for e in errors)

    def test_invalid_line_range_rejected(self, summary_schema):
        """F-12 fix: semantic check rejects line ranges where start > end (e.g., 242-171)."""
        data = _valid_summary()
        data["evolve_block_source"] = "routing/best_program.py:242-171"
        errors = validate_artifact(data, summary_schema)
        assert any("line range" in e.lower() for e in errors), (
            f"Expected line range validation error for 242-171, got: {errors}"
        )

    def test_unsupported_schema_keyword_rejected(self, summary_schema):
        """R3-F-17 fix: Schema using unsupported keywords ($ref, allOf, etc.)
        is rejected with a clear error rather than silently ignored."""
        data = _valid_summary()
        bad_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "ref_field": {"$ref": "#/definitions/Foo"},
            },
        }
        errors = validate_artifact(data, bad_schema)
        assert any("unsupported keyword" in e.lower() for e in errors), (
            f"Expected unsupported keyword error for $ref, got: {errors}"
        )

    def test_unsupported_allof_rejected(self, summary_schema):
        """R3-F-17 fix: allOf at top level is rejected."""
        data = _valid_summary()
        bad_schema = {"allOf": [{"type": "object"}, {"required": ["name"]}]}
        errors = validate_artifact(data, bad_schema)
        assert any("unsupported keyword" in e.lower() and "allOf" in e for e in errors), (
            f"Expected unsupported keyword error for allOf, got: {errors}"
        )
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_schema_validator.py -v`
Expected: FAIL (ModuleNotFoundError or ImportError)

**Step 3: Create schema file and implement validator**

`tools/schemas/algorithm_summary.schema.json`:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Algorithm Summary",
  "description": "Output of transfer_cli.py extract — metadata about an evolved routing algorithm. NOTE: This schema captures signal metadata and source location, not the algorithm's behavioral logic (scoring weights, penalty functions). PR3 must independently parse the EVOLVE-BLOCK at the location in evolve_block_source to extract algorithm logic. TEMPORAL ASSUMPTION: All signals are assumed to be point-in-time snapshots. No temporal metadata (rolling average window, sampling rate) is included in v1. PR5 must verify production metrics match this assumption — see blis_to_llmd_mapping.md Notes.",
  "type": "object",
  "required": ["algorithm_name", "evolve_block_source", "evolve_block_content_hash", "signals", "composite_signals", "metrics", "scope_validation_passed", "mapping_artifact_version", "fidelity_checked"],
  "additionalProperties": false,
  "properties": {
    "algorithm_name": {"type": "string"},
    "evolve_block_source": {
      "type": "string",
      "pattern": "^(?!.*(?:^|/)\\.\\./)(?!.*(?:^|/)\\./)[^/].+\\.py:\\d+-\\d+$",
      "description": "Source file path (relative to repository root) and line range, e.g., 'routing/best_program.py:171-242'. PATH FORMAT (F-25 fix, F-9 fix): The path MUST be relative to the repository root, not absolute. The pattern rejects absolute paths by requiring the first character is not '/'. The pattern also rejects both '../' and './' path components. R5-F-7 fix: Changed from (?:\\.\\.|\\.)/  to (?:^|/)\\.\\./ and (?:^|/)\\./  to anchor path-traversal detection to path component boundaries. The previous pattern rejected ANY dot followed by '/' anywhere in the string, which would incorrectly reject valid paths containing dotted directory names (e.g., 'routing/sub.dir/file.py:1-10'). The new pattern uses (?:^|/) to ensure '../' and './' are only rejected when they appear as complete path components (at start of string or after '/'). This correctly allows dotted directory names while still blocking path traversal. PR3 consumers should resolve this path relative to the repo root to locate the EVOLVE-BLOCK source file. NOTE (F-17): The regex pattern does not validate start_line <= end_line. Extract always produces valid ranges, but manual edits could create malformed ranges like '242-171'. The schema validator performs an additional semantic check: it parses the line numbers and verifies start <= end. IMPLICIT CONTRACT (F-13 fix): The _validate_node function in schema_validator.py enforces start <= end on line-range values as a defense-in-depth semantic check beyond the regex pattern. Consumers implementing their own validators MUST replicate this check: parse the digits after the colon, split on '-', and verify the first number is <= the second."
    },
    "evolve_block_content_hash": {
      "type": "string",
      "description": "SHA-256 hex digest of the EVOLVE-BLOCK content at extraction time. PR3 MUST recompute this hash and compare before parsing — if it differs, the source changed since extraction and the signal list may be stale.",
      "pattern": "^[0-9a-f]{64}$"
    },
    "signals": {
      "type": "array",
      "minItems": 1,
      "maxItems": 20,
      "description": "maxItems is a defense-in-depth guard against spurious regex matches. The golden-file test (TestGoldenSignalList) is the primary safeguard for signal count correctness.",
      "items": {
        "type": "object",
        "required": ["name", "type", "access_path"],
        "properties": {
          "name": {"type": "string"},
          "type": {
            "type": "string",
            "enum": ["int", "int64", "float64", "string", "bool", "unknown"],
            "description": "Go type of the signal. 'unknown' indicates the signal was found in the EVOLVE-BLOCK but is not in ROUTING_SNAPSHOT_FIELDS or REQUEST_LEVEL_FIELDS. Downstream stages MUST treat 'unknown' as an error and resolve the type before proceeding. DETECTION: extract emits a stderr WARNING for each unknown signal. TestGoldenSignalList will fail if an unknown signal appears (since EXPECTED_SIGNALS uses known names). PR3 MUST NOT generate code for unknown-type signals — it should abort with an error indicating the signal needs resolution in transfer_cli.py first. ENFORCEMENT (F-22 note): The 'unknown' value is allowed in the schema to avoid silently dropping unrecognized signals — rejecting at schema level would hide extraction gaps. The constraint is enforced by three layers: (1) stderr WARNING from extract, (2) TestGoldenSignalList golden-file failure, (3) PR3 convergence-review MUST include a finding if PR3 code does not check for and reject unknown-type signals before code generation."
          },
          "access_path": {"type": "string"},
          "normalization_note": {
            "type": "string",
            "description": "Machine-readable normalization requirement for production values. Example: 'divide_by_100' for KVUtilization (sim 0.0-1.0 vs prod 0-100). PR3 MUST apply this normalization before passing values to the scorer. F-15: This field is optional in the schema because not all signals require normalization, but when present, PR3 MUST NOT ignore it. PR3 MUST iterate all signals and check for this field. CONSEQUENCE OF IGNORING: Without divide_by_100 normalization, KVUtilization values are 100x larger than the algorithm was trained on, causing severely degraded scoring — this is a silent correctness bug, not a crash. PR3 convergence-review MUST verify a test exists for normalization application. R2-F-9 fix: This requirement is also listed in docs/transfer/README.md § Cross-PR Contracts as a CRITICAL gate for PR3. PR3 MUST include a test `test_normalization_applied_to_kvutilization` that verifies the divide_by_100 normalization produces values in [0.0, 1.0] range when given percentage inputs."
          },
          "fidelity_provisional": {
            "type": "boolean",
            "description": "F-14 fix: Set to true when the signal's fidelity rating in the mapping artifact is annotated as *(provisional)*. Allows downstream stages to programmatically distinguish provisional from confirmed fidelity ratings. When true, PR5 empirical validation is required before the rating can be considered stable. R4-F-11 fix: If absent AND fidelity_checked is true, the fidelity rating is confirmed (not provisional). If absent AND fidelity_checked is false, fidelity status is unknown (no check was performed because the mapping artifact was not present). Consumers MUST check fidelity_checked before interpreting the absence of this field."
          }
        }
      }
    },
    "metrics": {
      "type": "object",
      "description": "Metrics from best_program_info.json. combined_score is required.",
      "required": ["combined_score"],
      "properties": {
        "combined_score": {"type": "number", "description": "F-11 fix: Combined fitness score from the evolutionary optimizer (best_program_info.json). Represents the aggregate performance metric of the evolved algorithm. May be negative (e.g., -3858.94). No valid range constraint — the value's scale and sign depend on the optimizer's fitness function. Downstream consumers should treat this as an opaque metric for tracking which algorithm version was extracted."}
      }
    },
    "scope_validation_passed": {"type": "boolean"},
    "composite_signals": {
      "type": "array",
      "description": "F-16 fix: Composite signals (method calls that expand to multiple fields). Documents how constituent signals combine, so PR3 can reconstruct the composite without consulting the mapping artifact separately. EMPTY ARRAY SEMANTICS (F-8 fix): An empty array is schema-valid and means no composite method calls (e.g., EffectiveLoad()) were detected. This is NOT necessarily an error — algorithms that only use direct field access (snap.QueueDepth) with no composite methods will correctly have an empty array. However, if the EVOLVE-BLOCK is known to call EffectiveLoad() and composite_signals is empty, this indicates an extraction failure. The golden-file test (TestGoldenSignalList) catches missing constituent signals, providing defense-in-depth: if EffectiveLoad()'s constituents are extracted, the composite is populated; if they are missed, the golden-file test fails.",
      "items": {
        "type": "object",
        "required": ["name", "constituents"],
        "properties": {
          "name": {"type": "string", "description": "Method name, e.g., 'EffectiveLoad'"},
          "constituents": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of signal names that compose this method"
          },
          "formula": {"type": "string", "enum": ["sum"], "description": "How constituents combine. F-24 fix: enum-constrained to known formulas. Extend this enum when adding new composite formulas (e.g., weighted_sum, max, product)."}
        }
      }
    },
    "mapping_artifact_version": {
      "type": "string",
      "pattern": "^(unknown|\\d+\\.\\d+)$",
      "description": "Version of blis_to_llmd_mapping.md used at extraction time. Format: 'MAJOR.MINOR' (e.g., '1.0') or 'unknown' (fallback when version cannot be parsed). F-7 fix: pattern constraint ensures only valid version strings or the explicit 'unknown' fallback are accepted — rejects empty strings, arbitrary text, and malformed versions. Enables staleness detection: if the mapping artifact version changes (e.g., fidelity ratings updated in PR5), existing algorithm_summary.json artifacts are flagged as potentially stale. STALENESS POLICY (for PR3+): When consuming an algorithm_summary.json, compare its mapping_artifact_version against the current mapping artifact version. If they differ, re-run extract to produce an updated summary. A MAJOR version bump (e.g., 1.0 → 2.0) indicates signal or fidelity changes that require re-extraction; a MINOR bump (e.g., 1.0 → 1.1) indicates documentation-only changes where re-extraction is optional. ENFORCEMENT NOTE (F-4 fix): PR1 provides this field as a mechanism; enforcement is a PR3+ responsibility. PR3 MUST include a test `test_stale_mapping_version_detected` that verifies the consumer aborts when mapping_artifact_version differs from the current mapping. The PR3 convergence-review MUST check for this test."
    },
    "fidelity_checked": {
      "type": "boolean",
      "description": "R2-F-11 fix: Whether fidelity checks were performed during extraction. True if the mapping artifact existed at extraction time (fidelity ratings were verified). False if the mapping artifact was absent (bootstrap phase — fidelity checks skipped). Downstream stages SHOULD treat fidelity_checked=false artifacts as NOT pipeline-ready. CI with --strict always produces fidelity_checked=true (--strict fails if mapping is absent)."
    }
  }
}
```

`tools/schema_validator.py`:
```python
"""Lightweight JSON Schema validator using stdlib only.

Supports a restricted subset: required fields, type checks, enum values,
nested objects, arrays with item schemas, minItems, maxItems, additionalProperties: false,
and string pattern validation.
Does NOT support $ref, allOf, patternProperties, or other advanced JSON Schema features.
"""
import json
import re
from pathlib import Path

_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
    "null": type(None),
}


_UNSUPPORTED_KEYWORDS = {"$ref", "allOf", "anyOf", "oneOf", "patternProperties", "if", "then", "else"}


def validate_artifact(data: dict, schema: dict) -> list[str]:
    """Validate data against a restricted JSON Schema. Returns list of error strings.

    R3-F-17 fix: Rejects schemas that use unsupported JSON Schema features ($ref,
    allOf, etc.) with a clear error message, preventing silent incorrect validation.
    """
    errors: list[str] = []
    _check_unsupported_keywords(schema, "", errors)
    if errors:
        return errors  # Abort early — schema itself is invalid for this validator
    _validate_node(data, schema, "", errors)
    return errors


def _check_unsupported_keywords(schema: dict, path: str, errors: list[str]) -> None:
    """R3-F-17: Recursively check schema for unsupported keywords."""
    for keyword in _UNSUPPORTED_KEYWORDS:
        if keyword in schema:
            errors.append(
                f"Schema{' at ' + path if path else ''}: unsupported keyword '{keyword}'. "
                f"This validator only supports a restricted JSON Schema subset."
            )
    # Recurse into nested schemas
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            _check_unsupported_keywords(prop_schema, f"{path}.{prop_name}", errors)
    if "items" in schema and isinstance(schema["items"], dict):
        _check_unsupported_keywords(schema["items"], f"{path}[]", errors)


def _validate_node(data, schema: dict, path: str, errors: list[str]) -> None:
    # Type check
    expected_type = schema.get("type")
    if expected_type:
        py_types = _TYPE_MAP.get(expected_type)
        if py_types and not isinstance(data, py_types):
            errors.append(f"{path or '/'}: expected type '{expected_type}', got '{type(data).__name__}'")
            return  # Skip deeper checks if type is wrong

    # Enum check
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path or '/'}: value '{data}' not in enum {schema['enum']}")

    # Pattern check (F-18: validates string format, e.g., SHA-256 hex digest)
    # F-1 fix: use re.fullmatch() instead of re.match() to anchor at both ends,
    # ensuring patterns without explicit '$' still reject trailing characters.
    if "pattern" in schema and isinstance(data, str):
        if not re.fullmatch(schema["pattern"], data):
            errors.append(f"{path or '/'}: value does not match pattern '{schema['pattern']}'")
        # F-17 fix: semantic check for line range fields (start <= end).
        # Detect line-range patterns structurally (colon + digits-dash-digits)
        # rather than comparing the exact pattern string, so the check works
        # even if the regex pattern is modified.
        # F-13 note: This regex (r'.+:\d+-\d+') is intentionally broader than
        # the schema pattern (r'^.+\.py:\d+-\d+$') — it does not require .py.
        # This is a design choice: the semantic check triggers on ANY line-range
        # value, not just .py files. If the schema is extended to support
        # non-.py files, the semantic check already works. The schema pattern
        # is the gatekeeping constraint; this check is defense-in-depth.
        # F-6 note: Latent risk for schema evolution — if future schemas add
        # string fields matching '.+:\d+-\d+' that are NOT line ranges (e.g.,
        # version ranges), this check would produce false positives. For v1
        # with a single schema and one matching field, this is acceptable.
        elif isinstance(data, str) and re.fullmatch(r'.+:\d+-\d+', data):
            line_part = data.rsplit(":", 1)[-1]
            parts = line_part.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                if int(parts[0]) > int(parts[1]):
                    errors.append(f"{path or '/'}: line range start ({parts[0]}) > end ({parts[1]})")

    # Object: check required fields, property schemas, and additionalProperties
    if expected_type == "object" and isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}/{req}: required field missing")
        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key in data:
                _validate_node(data[key], prop_schema, f"{path}/{key}", errors)
        if schema.get("additionalProperties") is False:
            allowed = set(props.keys())
            for key in data:
                if key not in allowed:
                    errors.append(f"{path}/{key}: unexpected additional property")

    # Array: check minItems, maxItems, and item schemas
    if expected_type == "array" and isinstance(data, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(data) < min_items:
            errors.append(f"{path or '/'}: array has {len(data)} items, minimum is {min_items}")
        max_items = schema.get("maxItems")
        if max_items is not None and len(data) > max_items:
            errors.append(f"{path or '/'}: array has {len(data)} items, maximum is {max_items}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(data):
                _validate_node(item, item_schema, f"{path}[{i}]", errors)


def load_schema(schema_path: Path) -> dict:
    """Load a JSON Schema file. Raises on parse error."""
    with open(schema_path) as f:
        return json.load(f)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_schema_validator.py -v`
Expected: PASS (all 5 tests)

**Step 5: Lint**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m ruff check tools/schema_validator.py tools/test_schema_validator.py 2>/dev/null || echo "ruff not installed — skip lint"`
Expected: No issues (or ruff not installed)

**Step 6: Commit**

```bash
git add tools/schemas/algorithm_summary.schema.json tools/schema_validator.py tools/test_schema_validator.py
git commit -m "$(cat <<'EOF'
feat(transfer): JSON Schema + lightweight stdlib validator (BC-4, BC-10)

- Add algorithm_summary.schema.json with required fields, types, enums
- Implement schema_validator.py using stdlib json only (~100 lines)
- Tests: valid artifact, missing fields, wrong types, nested validation

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 3: CLI Framework + Extract Command

**Contracts Implemented:** BC-1, BC-2, BC-5, BC-7, BC-8

**Files:**
- Create: `tools/transfer_cli.py`
- Create: `tools/test_transfer_cli.py`

**Step 0: Pre-flight verification of routing artifacts**

Before writing any tests or implementation, verify the input artifacts exist and have the expected structure:

**F-24 fix: Prerequisite — ensure inference-sim submodule is checked out.** The last verification step below requires `inference-sim/sim/routing.go`. If the submodule is not checked out, run:
```bash
git submodule update --init inference-sim
```

```bash
# Verify routing artifacts exist
ls -la routing/best_program.py routing/best_program_info.json

# Verify EVOLVE-BLOCK markers exist
grep -n "EVOLVE-BLOCK-START\|EVOLVE-BLOCK-END" routing/best_program.py

# Verify RoutingSnapshot field access patterns exist in the EVOLVE-BLOCK
sed -n '/EVOLVE-BLOCK-START/,/EVOLVE-BLOCK-END/p' routing/best_program.py | grep -oE '(snap|target|req)\.\w+'

# Verify EffectiveLoad() expansion assumption against inference-sim source
# (requires inference-sim submodule to be checked out — see prerequisite above)
test -d inference-sim/ || { echo "ERROR: inference-sim submodule not checked out. Run: git submodule update --init inference-sim"; exit 1; }
grep -A 10 'func.*EffectiveLoad' inference-sim/sim/routing.go
```

Expected: `best_program.py` exists with `EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END` markers, and field access patterns like `snap.QueueDepth`, `snap.KVUtilization`, etc. are visible. `best_program_info.json` exists and contains a `metrics` key. EffectiveLoad() in inference-sim returns QueueDepth + BatchSize + InFlightRequests.

**Blocking prerequisite check:** If the files don't exist or markers are missing, STOP and update this plan — the CLI implementation depends on these artifacts existing with the assumed structure. If EffectiveLoad() has a different expansion, update `METHOD_EXPANSIONS` accordingly. This is NOT optional — do not proceed to Step 1 until all verifications above pass.

**Relationship to automated tests:** This pre-flight step is a manual implementation aid for the plan executor, not a runtime gate. The automated equivalent is the test suite: BC-8 tests verify missing files/markers produce correct exit codes, `TestSourceSyncVerification` verifies EffectiveLoad expansion, and `TestGoldenSignalList` verifies the expected signal set. If pre-flight is skipped, the tests serve as the automated safety net.

**Step 1: Write failing tests for extract**

Context: The extract command must parse the EVOLVE-BLOCK from `routing/best_program.py`, identify accessed RoutingSnapshot fields, read metrics from `best_program_info.json`, and produce `workspace/algorithm_summary.json`.

```python
# tools/test_transfer_cli.py
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "tools" / "transfer_cli.py"
ROUTING_DIR = REPO_ROOT / "routing"
WORKSPACE = REPO_ROOT / "workspace"


def run_cli(*args) -> tuple[int, dict]:
    """Run CLI command, return (exit_code, parsed_json_output)."""
    # R4-F-8 fix: Strip CI env var from subprocess environment. In GitHub Actions,
    # CI=true is set at the job level and propagates to all subprocesses. Without
    # stripping, every extract call without --strict fails with 'CI environment
    # detected but --strict flag not set'. Tests that need CI=true (e.g.,
    # test_ci_env_requires_strict_flag) set it explicitly via env= parameter
    # on subprocess.run. This ensures tests control the CI env var explicitly
    # rather than inheriting it from the CI runner.
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    result = subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        output = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, output


class TestExtract:
    def setup_method(self):
        """Ensure workspace dir exists and clean up prior artifacts."""
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_extract_produces_valid_summary(self):
        """BC-1: extract produces workspace/algorithm_summary.json with required fields."""
        code, output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0, f"Expected exit 0, got {code}: {output}"
        assert output["status"] == "ok"
        summary_path = WORKSPACE / "algorithm_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert "algorithm_name" in summary
        assert "evolve_block_source" in summary
        assert "evolve_block_content_hash" in summary
        assert len(summary["evolve_block_content_hash"]) == 64  # SHA-256 hex
        assert "signals" in summary
        assert isinstance(summary["signals"], list)
        assert len(summary["signals"]) > 0
        assert "metrics" in summary
        assert "scope_validation_passed" in summary

    def test_extract_identifies_signals(self):
        """BC-2: extract finds RoutingSnapshot fields from EVOLVE-BLOCK."""
        code, output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        signal_names = {s["name"] for s in summary["signals"]}
        # Direct field access
        assert "KVUtilization" in signal_names
        assert "CacheHitRate" in signal_names
        # EffectiveLoad() expansion must produce all three constituent fields
        assert "QueueDepth" in signal_names, "EffectiveLoad() expansion missing QueueDepth"
        assert "BatchSize" in signal_names, "EffectiveLoad() expansion missing BatchSize"
        assert "InFlightRequests" in signal_names, "EffectiveLoad() expansion missing InFlightRequests"

    def test_extract_signals_have_required_fields(self):
        """BC-2: each signal has name, type, access_path."""
        code, _ = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        for signal in summary["signals"]:
            assert "name" in signal, f"Signal missing 'name': {signal}"
            assert "type" in signal, f"Signal missing 'type': {signal}"
            assert "access_path" in signal, f"Signal missing 'access_path': {signal}"

    def test_extract_includes_metrics(self):
        """BC-1: metrics from best_program_info.json are included."""
        code, _ = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        assert "combined_score" in summary["metrics"]

    def test_extract_content_hash_matches_evolve_block(self):
        """F-18: evolve_block_content_hash is SHA-256 of actual EVOLVE-BLOCK content.

        F-20 fix: This test independently recomputes the hash using the same
        slicing convention as _extract_evolve_block: lines[start_idx:end_idx + 1]
        (inclusive of both EVOLVE-BLOCK-START and EVOLVE-BLOCK-END marker lines).
        See F-27 fix documentation for the canonical slicing specification.
        If _extract_evolve_block's slicing changes, this test MUST be updated
        to match, otherwise it would pass while production hashes diverge.
        """
        import hashlib
        code, _ = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        # Independently compute the hash from the source file
        # IMPORTANT: Slicing must match _extract_evolve_block exactly:
        # lines[start_idx:end_idx + 1] — inclusive of marker lines (F-27).
        source = (ROUTING_DIR / "best_program.py").read_text()
        lines = source.split("\n")
        start_idx = end_idx = None
        for i, line in enumerate(lines):
            if "EVOLVE-BLOCK-START" in line:
                start_idx = i
            if "EVOLVE-BLOCK-END" in line:
                end_idx = i
                break
        assert start_idx is not None and end_idx is not None
        block = "\n".join(lines[start_idx:end_idx + 1])
        expected_hash = hashlib.sha256(block.encode()).hexdigest()
        assert summary["evolve_block_content_hash"] == expected_hash, (
            f"Content hash mismatch: extract produced {summary['evolve_block_content_hash']}, "
            f"but independent SHA-256 of EVOLVE-BLOCK is {expected_hash}"
        )

    def test_extract_scope_validation_passes_for_routing(self):
        """BC-5: routing-only algorithm passes scope validation."""
        code, _ = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        assert summary["scope_validation_passed"] is True

    def test_extract_scope_validation_fails_for_out_of_scope(self):
        """BC-5 negative: out-of-scope patterns cause scope_validation_passed=false."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Copy real routing artifacts
            shutil.copy2(str(ROUTING_DIR / "best_program.py"), str(tmpdir / "best_program.py"))
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            # Inject P/D disaggregation pattern into the EVOLVE-BLOCK
            src = (tmpdir / "best_program.py").read_text()
            src = src.replace(
                "# EVOLVE-BLOCK-START",
                "# EVOLVE-BLOCK-START\n    # PrefillInstance disaggregation check",
            )
            (tmpdir / "best_program.py").write_text(src)
            code, output = run_cli("extract", str(tmpdir))
            # F-26 fix: Assert exit code as part of the behavioral contract (BC-5).
            # Scope failure produces exit 1 (validation failure) with artifact written.
            assert code == 1, f"Scope validation failure should exit 1, got {code}: {output}"
            # Should still produce artifact but with scope_validation_passed=false
            summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
            assert summary["scope_validation_passed"] is False

    def test_extract_missing_directory_exits_2(self):
        """BC-8: missing input directory exits with code 2."""
        code, output = run_cli("extract", "/nonexistent/path")
        assert code == 2
        assert output["status"] == "error"
        assert len(output["errors"]) > 0

    def test_extract_no_signals_exits_1(self):
        """F-15: EVOLVE-BLOCK found but no recognizable signals → exit 1 (validation), not 2 (infra)."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            # Create a best_program.py with EVOLVE-BLOCK but no recognizable signal patterns
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'def route():\n'
                '    return 42  # no signal access\n'
                '# EVOLVE-BLOCK-END\n'
            )
            code, output = run_cli("extract", str(tmpdir))
            assert code == 1, f"No signals should be exit 1 (validation), got {code}: {output}"
            assert output["status"] == "error"

    def test_extract_empty_evolve_block_exits_1(self):
        """F-15 edge case: EVOLVE-BLOCK markers present but empty content between them."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                '# EVOLVE-BLOCK-END\n'
            )
            code, output = run_cli("extract", str(tmpdir))
            assert code == 1, f"Empty EVOLVE-BLOCK should be exit 1, got {code}: {output}"

    def test_extract_multiple_evolve_blocks_warns(self):
        """F-27: Multiple EVOLVE-BLOCK pairs should emit a stderr warning."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'snap.QueueDepth\n'
                '# EVOLVE-BLOCK-END\n'
                '# EVOLVE-BLOCK-START\n'
                'snap.BatchSize\n'
                '# EVOLVE-BLOCK-END\n'
            )
            # R5-F-8 fix: Strip CI env var to prevent CI=true from triggering
            # --strict enforcement (exit 1 without --strict flag).
            env = {k: v for k, v in os.environ.items() if k != "CI"}
            result = subprocess.run(
                [sys.executable, str(CLI), "extract", str(tmpdir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
                env=env,
            )
            # F-22 fix: Verify extraction succeeds (exit 0) and produces correct output
            assert result.returncode == 0, (
                f"Multiple EVOLVE-BLOCK should still succeed (extract first block), got exit {result.returncode}: {result.stderr}"
            )
            stdout = json.loads(result.stdout)
            assert stdout["status"] == "ok", f"Expected status 'ok', got: {stdout}"
            # Verify the first block was extracted (contains QueueDepth, not BatchSize from second block)
            summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
            signal_names = {s["name"] for s in summary["signals"]}
            assert "QueueDepth" in signal_names, (
                f"First EVOLVE-BLOCK contains snap.QueueDepth — it should be extracted. Got: {signal_names}"
            )
            # Verify warning is present and mentions count
            assert "WARNING" in result.stderr, (
                f"Multiple EVOLVE-BLOCK-START markers should produce a stderr warning, got: {result.stderr}"
            )
            assert "2" in result.stderr, "Warning should mention the count of blocks found"

    def test_extract_few_signals_strict_exits_1(self):
        """F-9 fix: 1-2 signals in --strict mode should exit 1 (below MINIMUM_EXPECTED_SIGNALS=3).
        F-25 fix: Also verify the error message specifically mentions signal count."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            # Create a best_program.py with EVOLVE-BLOCK containing only 1 signal
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'func route(snap RoutingSnapshot) {\n'
                '    x := snap.QueueDepth\n'
                '}\n'
                '# EVOLVE-BLOCK-END\n'
            )
            code, output = run_cli("extract", "--strict", str(tmpdir))
            assert code == 1, (
                f"--strict with {output.get('signal_count', '?')} signals "
                f"(< MINIMUM_EXPECTED_SIGNALS=3) should exit 1, got {code}: {output}"
            )
            assert output["status"] == "error"
            # F-25 fix: Verify error message mentions signal count or minimum threshold
            error_text = " ".join(output.get("errors", []))
            assert "signal" in error_text.lower() and ("expected" in error_text.lower() or "minimum" in error_text.lower()), (
                f"Error message should mention signal count or minimum threshold. Got: {error_text}"
            )

    def test_extract_few_signals_boundary_2_fails(self):
        """R3-F-15 fix: Boundary test — exactly 2 signals (< MINIMUM_EXPECTED_SIGNALS=3)
        should exit 1 in --strict mode."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'func route(snap RoutingSnapshot) {\n'
                '    x := snap.QueueDepth\n'
                '    y := snap.BatchSize\n'
                '}\n'
                '# EVOLVE-BLOCK-END\n'
            )
            code, output = run_cli("extract", "--strict", str(tmpdir))
            assert code == 1, (
                f"--strict with 2 signals (< MINIMUM_EXPECTED_SIGNALS=3) "
                f"should exit 1, got {code}: {output}"
            )

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="Mapping artifact not present (pre-Task 5) — --strict always fails without mapping"
    )
    def test_extract_few_signals_boundary_3_passes_threshold(self):
        """R3-F-15 fix: Boundary test — exactly 3 signals (= MINIMUM_EXPECTED_SIGNALS=3)
        should pass the signal count threshold in --strict mode.
        R3-F-9 fix: Changed from non-strict to --strict mode. In non-strict mode, the
        MINIMUM_EXPECTED_SIGNALS check only emits a WARNING to stderr and never produces
        exit 1 for signal count, making the test vacuous. With --strict, signal count
        violations produce exit 1, so this test meaningfully verifies that 3 signals
        meets the threshold. The companion test (boundary_2_fails) already uses --strict.
        R4-F-4 fix: Added skipif guard for pre-Task 5 case. Without the mapping artifact,
        --strict mode causes _check_fidelity to fail with 'Mapping artifact not found',
        and the OR assertion ('signal' not in error OR 'expected' not in error) passes
        vacuously because the mapping error contains neither keyword. The test never
        actually verifies the signal count threshold. With the skipif guard, the test
        only runs when the mapping exists, ensuring the signal count check is reached.
        Also changed OR to AND in the assertion to prevent vacuous passes."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'func route(snap RoutingSnapshot) {\n'
                '    x := snap.QueueDepth\n'
                '    y := snap.BatchSize\n'
                '    z := snap.InFlightRequests\n'
                '}\n'
                '# EVOLVE-BLOCK-END\n'
            )
            # R3-F-9 fix: Run with --strict to verify signal count threshold is
            # actually enforced. In non-strict mode, signal count only warns on stderr
            # and never exits 1, making the assertion vacuous.
            code, output = run_cli("extract", "--strict", str(tmpdir))
            # R5-F-5 fix: Positively assert success instead of only checking errors
            # on failure. The previous code had a vacuous assertion: when code == 0
            # (the expected case), no assertion was evaluated, so the test passed
            # trivially without verifying that 3 signals actually meets the threshold.
            assert code == 0, (
                f"3 signals should pass the MINIMUM_EXPECTED_SIGNALS threshold, "
                f"but got exit code {code}: {output.get('errors', [])}"
                )

    def test_extract_missing_info_json_exits_2(self):
        """F-9 fix: best_program_info.json not existing at all should exit 2 (infra error)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            # Only create best_program.py, no best_program_info.json
            (tmpdir / "best_program.py").write_text(
                '# EVOLVE-BLOCK-START\n'
                'func route(snap RoutingSnapshot) {\n'
                '    x := snap.QueueDepth\n'
                '}\n'
                '# EVOLVE-BLOCK-END\n'
            )
            code, output = run_cli("extract", str(tmpdir))
            assert code == 2, (
                f"Missing best_program_info.json should exit 2 (infra error), got {code}: {output}"
            )
            assert output["status"] == "error"
            assert any("best_program_info.json" in e for e in output.get("errors", []))

    def test_extract_missing_metrics_key_warns(self):
        """F-15 edge case: best_program_info.json exists but has no 'metrics' key.
        F-26 fix: Also verify the artifact is still written so users can inspect it."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program.py"), str(tmpdir / "best_program.py"))
            (tmpdir / "best_program_info.json").write_text('{"generation": 100}')
            # R5-F-8 fix: Strip CI env var to prevent CI=true from triggering
            # --strict enforcement (exit 1 without --strict flag).
            env = {k: v for k, v in os.environ.items() if k != "CI"}
            result = subprocess.run(
                [sys.executable, str(CLI), "extract", str(tmpdir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
                env=env,
            )
            # Should succeed (metrics are optional for extraction) but warn
            assert result.returncode == 0, f"Missing metrics key should not abort: {result.stderr}"
            assert "metrics" in result.stderr.lower() or "warning" in result.stderr.lower()
            # F-26 fix: Verify the artifact was still written so users can inspect it
            summary_path = WORKSPACE / "algorithm_summary.json"
            assert summary_path.exists(), (
                "algorithm_summary.json should be written even without metrics "
                "(non-strict mode is for exploratory use)"
            )
            summary = json.loads(summary_path.read_text())
            assert "signals" in summary, "Artifact should contain signals even without metrics"

    def test_extract_missing_metrics_key_strict_fails(self):
        """F-26: In --strict mode, missing combined_score should fail with exit 1."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program.py"), str(tmpdir / "best_program.py"))
            (tmpdir / "best_program_info.json").write_text('{"generation": 100}')
            result = subprocess.run(
                [sys.executable, str(CLI), "extract", "--strict", str(tmpdir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )
            assert result.returncode == 1, (
                f"--strict with missing combined_score should exit 1, got {result.returncode}: {result.stderr}"
            )
            stdout = json.loads(result.stdout)
            assert stdout["status"] == "error"

    def test_extract_output_is_json(self):
        """BC-7 related: CLI outputs valid JSON to stdout."""
        result = subprocess.run(
            [sys.executable, str(CLI), "extract", str(ROUTING_DIR)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        parsed = json.loads(result.stdout)  # Should not raise
        assert "status" in parsed

    def test_extract_stdout_differs_from_file_artifact(self):
        """F-7: stdout JSON is an operational report, NOT the file artifact.
        Verifies they have different structures to prevent accidental misuse."""
        code, stdout_output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        file_artifact = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        # stdout has output_type marker; file artifact does not
        assert stdout_output.get("output_type") == "operational_report"
        assert "output_type" not in file_artifact
        # stdout has status; file artifact has signals[]
        assert "status" in stdout_output
        assert "signals" in file_artifact
        assert "signals" not in stdout_output

    # R4-F-14 fix: Added skipif guard for pre-Task 5 case. Without the mapping
    # artifact, the 'with mapping present' run (code_with) and the 'without mapping'
    # run both execute without mapping, so the test passes vacuously — it never
    # demonstrates the asymmetry between with/without mapping scenarios.
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="Mapping artifact not present (pre-Task 5) — cannot demonstrate with/without asymmetry"
    )
    def test_extract_non_determinism_boundary_documented(self):
        """F-2/F-19: Verify extract produces different fidelity outcomes with/without mapping.
        This test documents AND VERIFIES the known non-determinism boundary (BC-6).
        With mapping: fidelity checks enforced. Without: fidelity checks skipped.
        With --strict and no mapping: hard failure (exit 2) — proves the asymmetry."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        # Run with mapping present — should succeed with fidelity checks
        code_with, _ = run_cli("extract", str(ROUTING_DIR))
        assert code_with == 0
        # Temporarily remove mapping
        if mapping.exists():
            shutil.move(str(mapping), str(backup))
        try:
            # Without mapping, default mode: succeeds (fidelity checks skipped)
            code_without, _ = run_cli("extract", str(ROUTING_DIR))
            assert code_without == 0, "Extract should succeed without mapping in default mode"
            # With --strict and no mapping: FAILS — this proves the asymmetry
            code_strict, output_strict = run_cli("extract", "--strict", str(ROUTING_DIR))
            assert code_strict != 0, (
                "With --strict and no mapping, extract must fail. "
                "This verifies the non-determinism boundary: same EVOLVE-BLOCK, "
                "different exit codes depending on mapping presence + --strict flag."
            )
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    def test_extract_without_mapping_graceful_degradation(self):
        """BC-6 graceful degradation: extract succeeds when mapping artifact absent.
        R3-F-22 fix: Also verify fidelity_checked is false in the output artifact.
        F-20 NOTE: This test uses shutil.move to temporarily remove the mapping artifact.
        The finally block restores it, but SIGKILL would leave the artifact missing.
        Multiple tests share this pattern — do NOT run with pytest-xdist (parallel)
        as concurrent moves on the same file would corrupt test state. For v1 with
        sequential test execution, this is acceptable."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.move(str(mapping), str(backup))
        try:
            code, output = run_cli("extract", str(ROUTING_DIR))
            assert code == 0, f"Extract should succeed without mapping: {output}"
            assert output["status"] == "ok"
            # R3-F-22 fix: Verify the artifact has fidelity_checked=false
            summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
            assert summary.get("fidelity_checked") is False, (
                f"Without mapping artifact, fidelity_checked must be false. "
                f"Got: {summary.get('fidelity_checked')}"
            )
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    def test_extract_strict_fails_without_mapping(self):
        """F-1/F-16: --strict mode exits 1 when mapping artifact absent (CI determinism)."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.move(str(mapping), str(backup))
        try:
            code, output = run_cli("extract", "--strict", str(ROUTING_DIR))
            assert code == 1, f"--strict should fail without mapping: {output}"
            assert output["status"] == "error"
            assert any("strict" in e.lower() or "mapping" in e.lower() for e in output["errors"])
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    def test_extract_strict_succeeds_with_mapping(self):
        """F-1: --strict mode succeeds when mapping artifact exists."""
        code, output = run_cli("extract", "--strict", str(ROUTING_DIR))
        assert code == 0, f"--strict should succeed with mapping: {output}"

    def test_extract_mapping_version_parsed(self):
        """F-18: mapping_artifact_version is parsed from mapping artifact.
        F-22 fix: Independently read the mapping artifact version and compare,
        rather than only checking against a hardcoded '1.0' value.
        F-29 NOTE: The independent verification below uses the same regex as cmd_extract
        (r'\\*\\*Version:\\*\\*\\s*(\\S+)'). A truly independent check would use a different
        method (e.g., string search for 'Version:' then extract the next token). However,
        the primary value of this test is ensuring the version is not 'unknown' and
        matches the mapping artifact — the regex-vs-regex validation still catches
        regressions where cmd_extract's parsing breaks."""
        code, _ = run_cli("extract", str(ROUTING_DIR))
        assert code == 0
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        version = summary.get("mapping_artifact_version", "")
        assert version != "unknown", (
            "mapping_artifact_version should be parsed from mapping artifact, got 'unknown'. "
            "Ensure mapping has a '**Version:** X.Y' line."
        )
        # F-29 fix: Use a simpler, different method for independent verification.
        # Instead of the same regex, check that the version string appears in the
        # mapping artifact's Version line using basic string operations.
        mapping_content = (REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md").read_text()
        assert f"**Version:** {version}" in mapping_content, (
            f"Parsed version '{version}' not found as '**Version:** {version}' in mapping artifact. "
            f"Extract may have failed to parse or a different code path produced the value."
        )


class TestGoldenSignalList:
    """F-2: Golden-file test verifying extracted signals match manually-verified ground truth.

    This is the primary safeguard against regex-based extraction missing signals.
    The EXPECTED_SIGNALS set below was derived from manual inspection of the
    EVOLVE-BLOCK in routing/best_program.py (lines 171-242). If this test fails,
    either (a) the EVOLVE-BLOCK changed and EXPECTED_SIGNALS needs updating, or
    (b) the regex missed a signal pattern and _extract_signals needs fixing.

    HOW TO UPDATE: Manually read the EVOLVE-BLOCK, identify all RoutingSnapshot
    field accesses and request-level field accesses, and update EXPECTED_SIGNALS.
    Do NOT simply copy the extract output — the point is independent verification.

    FORMAL VERIFICATION PROCEDURE (F-2 fix):
    When updating EXPECTED_SIGNALS, follow these steps to ensure independence:
    1. Open routing/best_program.py and locate the EVOLVE-BLOCK region.
    2. For each line between START and END markers, identify field accesses matching:
       - snap.FieldName or snapshots[i].FieldName → RoutingSnapshot fields
       - snap.MethodName() → expand via METHOD_EXPANSIONS (verify in routing.go)
       - req.FieldName → request-level fields
    3. Cross-reference each found field against inference-sim/sim/routing.go to
       confirm it exists in the RoutingSnapshot struct (at the pinned commit).
    4. Record the complete set BEFORE looking at extract output.
    5. Only then run extract and compare — differences indicate either a regex
       gap (fix _extract_signals) or a manual inspection miss (update this set).
    This procedure ensures the golden list is derived from source reading, not
    from the regex output that the test is designed to verify.

    ANTI-GAMING SAFEGUARD (R2-F-5 fix):
    The git commit message for any EXPECTED_SIGNALS update MUST include:
    "Signals verified from EVOLVE-BLOCK lines [N-M] of routing/best_program.py"
    with the actual line range. Code reviewers MUST verify this claim by:
    (a) checking that the listed line range matches the EVOLVE-BLOCK markers,
    (b) confirming the signal set is derivable from those lines via the 5-step
    procedure above. If the commit message does not include this attestation,
    the PR review MUST request it before approval.
    """

    # F-17 fix: MAPPING UPDATE WORKFLOW when EVOLVE-BLOCK changes:
    # If this test fails because the EVOLVE-BLOCK changed, follow this workflow:
    # 1. Update EXPECTED_SIGNALS below using the Formal Verification Procedure above.
    # 2. Update EXPECTED_COMPOSITES if composite method calls changed.
    # 3. Run extract to produce updated algorithm_summary.json.
    # 4. Run validate-mapping to check if the mapping artifact needs updating.
    # 5. If validate-mapping reports missing/extra signals, update the mapping artifact:
    #    - Add rows for new signals (with fidelity ratings and production equivalents).
    #    - Remove rows for signals no longer accessed.
    #    - Bump the mapping artifact version (MAJOR bump for signal changes).
    # 6. Re-run extract --strict and validate-mapping to confirm consistency.
    # 7. If fidelity ratings need re-evaluation, document in the Deviation Log.
    #
    # Manually verified from EVOLVE-BLOCK inspection:
    #   snap.EffectiveLoad() -> QueueDepth, BatchSize, InFlightRequests
    #   snap.KVUtilization (direct access)
    #   snap.CacheHitRate (direct access)
    #   req.SessionID (boolean check)
    EXPECTED_SIGNALS = {
        "QueueDepth", "BatchSize", "InFlightRequests",
        "KVUtilization", "CacheHitRate", "SessionID",
    }

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    # F-25 fix: Expected composite signals, manually verified from EVOLVE-BLOCK.
    # EffectiveLoad() expands to QueueDepth + BatchSize + InFlightRequests.
    EXPECTED_COMPOSITES = {
        "EffectiveLoad": {"QueueDepth", "BatchSize", "InFlightRequests"},
    }

    def test_extracted_signals_match_golden_list(self):
        """Extract must produce exactly the manually-verified signal set."""
        code, output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0, f"Extract failed: {output}"
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        extracted = {s["name"] for s in summary["signals"]}
        missing = self.EXPECTED_SIGNALS - extracted
        extra = extracted - self.EXPECTED_SIGNALS
        assert not missing, (
            f"Signals in golden list but NOT extracted (regex may have missed them): {missing}. "
            f"If the EVOLVE-BLOCK changed, update EXPECTED_SIGNALS after manual verification."
        )
        assert not extra, (
            f"Signals extracted but NOT in golden list: {extra}. "
            f"If these are real signals, add them to EXPECTED_SIGNALS after manual verification."
        )

    def test_composite_signals_match_golden_list(self):
        """F-25 fix: Verify composite_signals array content, not just constituents."""
        code, output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0, f"Extract failed: {output}"
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        composites = {c["name"]: set(c["constituents"]) for c in summary["composite_signals"]}
        assert composites == self.EXPECTED_COMPOSITES, (
            f"Composite signals mismatch. Expected: {self.EXPECTED_COMPOSITES}, got: {composites}. "
            f"If EffectiveLoad() expansion changed, update EXPECTED_COMPOSITES after manual verification."
        )


class TestSourceSyncVerification:
    """Automated verification that hardcoded dicts match inference-sim source.

    NOTE ON CI (F-5): These tests are skipped when the inference-sim submodule is not
    checked out. For full verification coverage, CI MUST check out submodules
    (e.g., `git submodule update --init inference-sim`). If CI cannot check out
    submodules, the hardcoded dicts (ROUTING_SNAPSHOT_FIELDS, METHOD_EXPANSIONS)
    are pinned to a specific commit — drift only matters when the submodule is
    updated, at which point these tests MUST be run locally before committing.

    NOTE ON llm-d-inference-scheduler (F-6, R2-F-4 fix): CI checks out both inference-sim
    and llm-d-inference-scheduler (see .github/workflows/test.yml). The llm-d-inference-scheduler
    checkout is for commit hash verification in Task 5 Step 2. The mapping artifact pins the
    llm-d-inference-scheduler commit hash but no CI test currently verifies the scorer interface
    still matches. This is acceptable for PR1 because: (1) the pinned hash provides a stable
    reference point, (2) submodule updates are infrequent and deliberate, and (3) PR3 will
    verify the interface at implementation time.

    CI CONFIGURATION REQUIREMENT: Add `git submodule update --init inference-sim`
    to the CI setup step. Without this, TestSourceSyncVerification tests are
    silently skipped, and drift between hardcoded dicts and inference-sim source
    goes undetected. Example GitHub Actions step:
        - run: git submodule update --init inference-sim

    F-8 ENFORCEMENT: To prevent silent skipping in CI, the test_ci_must_not_skip
    test below verifies that when running in CI (CI env var set), the submodule
    MUST be checked out. This turns a silent skip into a loud failure in CI.

    CI SETUP CHECKLIST (F-8):
    1. Add `git submodule update --init inference-sim` to CI setup
    2. Verify CI sets the `CI` environment variable (most CI systems do this automatically)
    3. If CI cannot checkout submodules (e.g., private repo access), the test_ci_must_not_skip
       test will fail loudly — do NOT suppress this failure, fix the CI configuration instead.
    """

    def test_ci_must_not_skip_sync_tests(self):
        """F-8: In CI, submodule MUST be checked out — fail loudly instead of skipping.
        F-24 fix: Also verify the file is non-empty to catch corrupt/incomplete checkouts.
        """
        import os
        routing_go = REPO_ROOT / "inference-sim" / "sim" / "routing.go"
        if os.environ.get("CI"):
            assert routing_go.exists(), (
                "CI environment detected but inference-sim submodule not checked out. "
                "Add 'git submodule update --init inference-sim' to CI setup. "
                "Without this, TestSourceSyncVerification tests are silently skipped."
            )
            # F-24 fix: Verify file is non-empty (catches empty/corrupt checkout)
            assert routing_go.stat().st_size > 0, (
                "CI environment detected but routing.go is empty (0 bytes). "
                "The submodule checkout may be corrupt. Re-run: "
                "'git submodule update --init --force inference-sim'"
            )

    def test_method_expansion_matches_source(self):
        """F-1: Verify EffectiveLoad() expansion matches inference-sim implementation.
        F-8 fix: Known limitation — the regex r'func.*EffectiveLoad\\b.*?\\{(.*?)\\}'
        uses non-greedy .*? which terminates at the first closing brace. This works
        for simple accessor methods like EffectiveLoad() but would fail if the function
        body contains nested braces (if/for blocks). If EffectiveLoad() becomes more
        complex, replace the regex with a proper brace-depth counter.
        Also note: this test verifies field PRESENCE in the body, not the operation
        (addition). The 'sum' formula is manually verified — see F-21 note in cmd_extract."""
        routing_go = REPO_ROOT / "inference-sim" / "sim" / "routing.go"
        if not routing_go.exists():
            pytest.skip("inference-sim submodule not checked out — see class docstring for CI requirements")
        source = routing_go.read_text()
        # Find the EffectiveLoad function body
        # F-8 note: non-greedy match terminates at first '}' — see docstring.
        import re
        match = re.search(r'func.*EffectiveLoad\b.*?\{(.*?)\}', source, re.DOTALL)
        assert match is not None, "EffectiveLoad() function not found in routing.go"
        body = match.group(1)
        # Verify all constituent fields are referenced
        for field in ["QueueDepth", "BatchSize", "InFlightRequests"]:
            assert field in body, (
                f"METHOD_EXPANSIONS says EffectiveLoad includes {field}, "
                f"but {field} not found in EffectiveLoad() body"
            )

    def test_routing_snapshot_fields_match_source(self):
        """F-3: Verify ROUTING_SNAPSHOT_FIELDS matches RoutingSnapshot struct."""
        routing_go = REPO_ROOT / "inference-sim" / "sim" / "routing.go"
        if not routing_go.exists():
            pytest.skip("inference-sim submodule not checked out — see class docstring for CI requirements")
        source = routing_go.read_text()
        import re
        # Extract RoutingSnapshot struct fields
        match = re.search(
            r'type\s+RoutingSnapshot\s+struct\s*\{(.*?)\}', source, re.DOTALL
        )
        assert match is not None, "RoutingSnapshot struct not found in routing.go"
        struct_body = match.group(1)
        # Extract field names from struct
        # R3-F-15 fix: Broadened type regex to match any Go type, not just 5 primitives.
        # Previous regex only matched int|int64|float64|string|bool, silently missing
        # fields with types like time.Time, []byte, *int, or custom types. The new
        # pattern matches any non-whitespace sequence after the field name, capturing
        # all struct fields regardless of type.
        struct_fields = set(re.findall(r'(\w+)\s+\S+', struct_body))
        # Verify our hardcoded dict matches
        from tools.transfer_cli import ROUTING_SNAPSHOT_FIELDS
        hardcoded = set(ROUTING_SNAPSHOT_FIELDS.keys())
        missing = struct_fields - hardcoded
        extra = hardcoded - struct_fields
        assert not missing, f"Fields in source but not in ROUTING_SNAPSHOT_FIELDS: {missing}"
        assert not extra, f"Fields in ROUTING_SNAPSHOT_FIELDS but not in source: {extra}"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_transfer_cli.py::TestExtract -v`
Expected: FAIL (transfer_cli.py does not exist yet)

**Step 3: Implement CLI with extract command**

`tools/transfer_cli.py`:
```python
#!/usr/bin/env python3
"""Transfer pipeline CLI — mechanical support for sim-to-production transfer.

Commands:
    extract <routing_dir>    Parse EVOLVE-BLOCK, produce algorithm_summary.json
    validate-mapping         Check mapping artifact completeness
    validate-schema <path>   Validate workspace artifact against JSON Schema

Exit codes: 0 = success, 1 = validation failure, 2 = infrastructure error
All commands output JSON to stdout.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = REPO_ROOT / "workspace"
SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
MAPPING_PATH = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"

# RoutingSnapshot fields and their Go types (from inference-sim/sim/routing.go)
# SYNC NOTE: This dict is derived from the struct at the pinned submodule commit.
# If inference-sim evolves, re-derive via:
#   grep -A 20 'type RoutingSnapshot struct' inference-sim/sim/routing.go
# F-1 RECONCILIATION: This dict contains ALL 7 struct fields (mirrors the Go struct).
# Of these, only 5 are accessed in the EVOLVE-BLOCK (QueueDepth, BatchSize,
# KVUtilization, CacheHitRate, InFlightRequests). ID and FreeKVBlocks are struct
# fields but NOT accessed by the evolved algorithm. They appear here because
# test_routing_snapshot_fields_match_source verifies this dict matches the Go struct
# exactly. They do NOT appear in:
#   - EXPECTED_SIGNALS (6 entries: the 5 accessed struct fields + SessionID)
#   - The mapping table (5 RoutingSnapshot rows + 1 SessionID row = 6 mapped signals)
# This is correct: unmapped struct fields are intentionally excluded from the mapping
# because the evolved algorithm does not use them. validate-mapping's bidirectional
# check compares extracted signals (from the EVOLVE-BLOCK) against the mapping table,
# so ID and FreeKVBlocks are never flagged as extra — they are not extracted.
ROUTING_SNAPSHOT_FIELDS = {
    "ID": "string",              # Struct field — NOT accessed in EVOLVE-BLOCK
    "QueueDepth": "int",
    "BatchSize": "int",
    "KVUtilization": "float64",
    "FreeKVBlocks": "int64",     # Struct field — NOT accessed in EVOLVE-BLOCK
    "CacheHitRate": "float64",
    "InFlightRequests": "int",
}

# Method calls that expand to multiple fields.
# IMPORTANT: These expansions are hardcoded assumptions about the inference-sim
# implementation. They MUST be verified against the actual EffectiveLoad()
# implementation in inference-sim/sim/routing.go. If the implementation changes,
# update this dict. See Task 3 Step 0 pre-flight verification.
# Verified against: inference-sim EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests
# AUTOMATED CHECK: See test_method_expansion_matches_source in test_transfer_cli.py
METHOD_EXPANSIONS = {
    "EffectiveLoad": ["QueueDepth", "BatchSize", "InFlightRequests"],
}

# Request-level fields accessed outside RoutingSnapshot (e.g., req.SessionID)
REQUEST_LEVEL_FIELDS = {
    "SessionID": "string",
}

# Fields that are routing-scope (non-routing would be scheduling, batching, etc.)
ROUTING_SCOPE_FIELDS = set(ROUTING_SNAPSHOT_FIELDS.keys()) | set(REQUEST_LEVEL_FIELDS.keys())

# Out-of-scope patterns (P/D disaggregation, scheduling internals)
# KNOWN LIMITATION (F-16): This is a blacklist — new out-of-scope constructs
# (e.g., ModelInstance, SchedulingDecision) won't be detected unless added here.
# A whitelist approach (only allow known routing constructs) would be more robust
# but harder to maintain. For v1, this blacklist covers the known non-routing
# patterns. Extend this list if new out-of-scope patterns are discovered.
OUT_OF_SCOPE_PATTERNS = [
    r"PrefillInstance|DecodeInstance",  # P/D disaggregation
    r"BatchFormation|SchedulingPolicy",  # Scheduling internals
]


def _output(status: str, exit_code: int, **kwargs) -> int:
    """Print JSON result and return exit code."""
    result = {"status": status, **kwargs}
    if "errors" not in result:
        result["errors"] = []
    print(json.dumps(result, indent=2))
    return exit_code


def _extract_evolve_block(source: str) -> tuple[str | None, str | None]:
    """Extract EVOLVE-BLOCK region and line range from Go source embedded in Python.

    F-29 fix: Detects multiple EVOLVE-BLOCK pairs and emits a warning if more than
    one is found. Only the first pair is extracted. If the source evolves to use
    multiple blocks, this function must be extended to handle them.
    """
    lines = source.split("\n")
    start_idx = None
    end_idx = None
    block_count = 0
    for i, line in enumerate(lines):
        if "EVOLVE-BLOCK-START" in line:
            if start_idx is None:
                start_idx = i
            block_count += 1
        if "EVOLVE-BLOCK-END" in line:
            if end_idx is None:
                end_idx = i
    # F-18 fix: Differentiate error conditions for better diagnostics.
    if start_idx is None and end_idx is None:
        return None, None  # No markers at all — caller reports "markers not found"
    if start_idx is None:
        import sys as _sys
        print("WARNING: EVOLVE-BLOCK-END found without EVOLVE-BLOCK-START", file=_sys.stderr)
        return None, None
    if end_idx is None:
        import sys as _sys
        print("WARNING: EVOLVE-BLOCK-START found at line "
              f"{start_idx + 1} but no EVOLVE-BLOCK-END", file=_sys.stderr)
        return None, None
    if block_count > 1:
        import sys as _sys
        # R2-F-18 note: This warning covers both sequential (START,END,START,END) and
        # nested (START,START,END,END) cases. In the nested case, start_idx is the first
        # START and end_idx is the first END, so the extracted block may include a second
        # START marker but not its corresponding END — producing a malformed block. The
        # EVOLVE-BLOCK is expected to have exactly one pair; multiple pairs indicate a
        # source file issue that requires manual investigation.
        print(f"WARNING: Found {block_count} EVOLVE-BLOCK-START markers but only "
              f"extracting the first block (lines {start_idx + 1}-{end_idx + 1}). "
              f"Additional blocks are silently ignored. If multiple blocks are "
              f"intentional, extend _extract_evolve_block to handle them.",
              file=_sys.stderr)
    block = "\n".join(lines[start_idx:end_idx + 1])
    line_range = f"{start_idx + 1}-{end_idx + 1}"
    return block, line_range


def _extract_signals(block: str) -> list[dict]:
    """Identify RoutingSnapshot fields accessed in the EVOLVE-BLOCK.

    NOTE: Regex-based extraction — may miss signals accessed through aliased
    variables or chained method calls not matching the patterns below.
    The extracted signal list should be manually verified against the
    EVOLVE-BLOCK source. See Review Guide (Part 1 Section E).

    FALSE POSITIVE MITIGATION: The `[a-z]\.` pattern in the regex is
    intentionally broad (matches any single-letter variable followed by a dot).
    However, false positives are filtered out because only matches whose field
    name appears in ROUTING_SNAPSHOT_FIELDS are kept. So `x.Println` would
    match the regex but be discarded since `Println` is not a known field.
    The net false positive risk is: a single-letter variable accessing a field
    with the SAME NAME as a RoutingSnapshot field but on a different struct.
    This is unlikely in the current EVOLVE-BLOCK but not impossible.

    GO SYNTAX VALIDATION (F-5 note): This function does NOT validate that the
    EVOLVE-BLOCK content is syntactically valid Go code. If the EVOLVE-BLOCK
    contained non-Go text, extraction would find zero signals and exit 1 with
    "No routing signals found in EVOLVE-BLOCK" — the error message does not
    indicate the root cause. This is acceptable for v1: the EVOLVE-BLOCK is Go
    code embedded in routing/best_program.py and is not user-supplied input.
    The golden-file test (TestGoldenSignalList) catches any case where the
    EVOLVE-BLOCK doesn't contain expected signals.

    FALSE NEGATIVE RISK (genuine gap): Signals accessed through patterns not
    covered by the regex will be missed silently. Known blind spots:
    - Intermediate variables: `s := snap; s.QueueDepth` (only `snap.` is matched)
    - Method receivers: `r.snap.QueueDepth` (chained access not matched)
    - Struct embedding/composition (field promoted from embedded struct)
    The TestGoldenSignalList golden-file test is the primary safety net:
    if any signal is missed, the golden list comparison will fail. However,
    this requires the golden list to be independently maintained by manual
    inspection — do NOT derive it from extract output.

    VERIFICATION: After running extract, compare the signal list in
    workspace/algorithm_summary.json against a manual reading of the
    EVOLVE-BLOCK. If any signals are missing, add them to
    ROUTING_SNAPSHOT_FIELDS and/or the regex patterns above.
    """
    found: dict[str, str] = {}  # name -> access_path

    # Match direct field access on known receiver names:
    #   snap.FieldName, snapshots[i].FieldName, target.FieldName
    # Also match any single-letter alias (e.g., s.QueueDepth)
    # F-18 note (ReDoS assessment): This pattern is NOT ReDoS-vulnerable.
    # Catastrophic backtracking requires nested quantifiers (e.g., (a+)+).
    # This pattern uses alternation with fixed-length alternatives and a
    # single \w+ capture group — re.finditer processes matches sequentially
    # without backtracking across the full input. No mitigation needed.
    for match in re.finditer(
        r'(?:snap(?:shots?\[\w+\])?|target|[a-z])\.(\w+)', block
    ):
        field = match.group(1)
        if field in ROUTING_SNAPSHOT_FIELDS:
            found[field] = f"snap.{field}"

    # Match method calls that expand to multiple fields
    # F-12 fix: Unrecognized methods (not in METHOD_EXPANSIONS and not in _IGNORE_METHODS)
    # emit a stderr WARNING so new RoutingSnapshot methods are not silently dropped.
    # The golden-file test (TestGoldenSignalList) is the primary safety net, but the
    # warning provides immediate feedback during development.
    _IGNORE_METHODS = {"String", "Error", "Format", "GoString", "Reset", "ProtoMessage"}
    for match in re.finditer(r'\.\b(\w+)\(\)', block):
        method = match.group(1)
        if method in METHOD_EXPANSIONS:
            for field in METHOD_EXPANSIONS[method]:
                if field not in found:
                    found[field] = f"snap.{method}() -> {field}"
        elif method not in _IGNORE_METHODS:
            import sys as _sys
            print(f"WARNING: Unrecognized method call '{method}()' in EVOLVE-BLOCK — "
                  f"not in METHOD_EXPANSIONS. If this is a new RoutingSnapshot method, "
                  f"add it to METHOD_EXPANSIONS with its constituent fields.",
                  file=_sys.stderr)

    # Match request-level field access: req.FieldName, request.FieldName
    for match in re.finditer(r'(?:req(?:uest)?)\.\b(\w+)', block):
        field = match.group(1)
        if field in REQUEST_LEVEL_FIELDS:
            found[field] = f"req.{field}"

    # F-17 fix: Detect unrecognized field accesses and include them with type 'unknown'
    # instead of silently dropping them. This ensures the schema's 'unknown' enum value
    # is exercised and downstream stages can detect and resolve unrecognized signals.
    all_known = set(ROUTING_SNAPSHOT_FIELDS.keys()) | set(REQUEST_LEVEL_FIELDS.keys()) | set(METHOD_EXPANSIONS.keys())
    # Common Go method names and properties to ignore (not signal accesses)
    _IGNORE_FIELDS = {"String", "Error", "Len", "Less", "Swap", "Format"}
    # F-28 fix: Added [a-z] alternative to match the first extraction loop's regex,
    # ensuring single-letter alias field accesses trigger the unrecognized field warning.
    # R3-F-14 NOTE — FALSE POSITIVE RISK: The [a-z] alternative matches ANY single
    # lowercase letter as a receiver (e.g., 'v.Field' in 'for i, v := range items').
    # Unlike the first extraction loop which filters by ROUTING_SNAPSHOT_FIELDS (discarding
    # false positives), THIS loop emits 'unknown' type signals for ANY unrecognized field.
    # This could produce spurious unknown-type signals from loop variables or other
    # single-letter identifiers accessing non-RoutingSnapshot fields. MITIGATION: The
    # golden-file test (TestGoldenSignalList) catches unexpected signals in the output,
    # providing a safety net. For v1 with a known EVOLVE-BLOCK, this is acceptable.
    # If false positives become an issue, restrict [a-z] to known receiver aliases.
    for match in re.finditer(r'(?:snap(?:shots?\[\w+\])?|target|req(?:uest)?|[a-z])\.\b(\w+)', block):
        field = match.group(1)
        if field not in all_known and field not in _IGNORE_FIELDS and field not in found:
            import sys as _sys
            print(f"WARNING: Unrecognized field access '{field}' in EVOLVE-BLOCK — "
                  f"not in ROUTING_SNAPSHOT_FIELDS or REQUEST_LEVEL_FIELDS. "
                  f"Included with type 'unknown'. Downstream stages MUST resolve this.",
                  file=_sys.stderr)
            receiver = match.group(0).split(".")[0]
            found[field] = f"{receiver}.{field}"

    # Normalization notes for signals with known unit mismatches between sim and prod.
    # These are machine-readable so PR3 can programmatically discover required normalizations.
    # F-11 FORMAT CONVENTION: Values use the format 'operation: human_explanation'.
    # PR3 should extract the operation by splitting on the first colon and stripping
    # whitespace (e.g., 'divide_by_100: ...' → operation='divide_by_100').
    # For v1 with only two normalization notes, this convention is sufficient.
    # If more notes are added, consider a structured format (e.g., JSON or named fields).
    # F-4 fix: SessionID normalization note documents the semantic type gap.
    # The Go field type is 'string', but the EVOLVE-BLOCK uses it as a boolean
    # presence check (req.SessionID != ""). PR3 must generate a boolean check
    # (compare against empty string), not pass the raw string value to the scorer.
    NORMALIZATION_NOTES = {
        "KVUtilization": "divide_prod_by_100: production value (0-100 percentage) must be divided by 100 to match sim's 0.0-1.0 ratio (i.e., normalized = prod_kv / 100.0)",
        "SessionID": "boolean_presence_check: compare against empty string (req.SessionID != empty)",
    }

    signals = []
    all_fields = {**ROUTING_SNAPSHOT_FIELDS, **REQUEST_LEVEL_FIELDS}
    for name, access_path in sorted(found.items()):
        sig = {
            "name": name,
            "type": all_fields.get(name, "unknown"),
            "access_path": access_path,
        }
        if name in NORMALIZATION_NOTES:
            sig["normalization_note"] = NORMALIZATION_NOTES[name]
        signals.append(sig)
    return signals


def _check_scope(block: str, signals: list[dict]) -> tuple[bool, list[str]]:
    """Check that the algorithm is routing-scope only.

    R3-F-11 note: re.search(pattern, block) matches anywhere in the block
    text, including Go comments (// ...). This means a comment like
    '// TODO: integrate PrefillInstance later' would trigger a false positive.
    This is acceptable for v1 because the EVOLVE-BLOCK is Go code where
    comments are unlikely to reference out-of-scope struct names. If false
    positives occur in practice, switch to a comment-stripping pre-pass or
    restrict matching to non-comment lines.
    """
    errors = []

    # Check for out-of-scope patterns
    for pattern in OUT_OF_SCOPE_PATTERNS:
        match = re.search(pattern, block)
        if match:
            errors.append(f"Out-of-scope pattern found: '{match.group()}' (matched by /{pattern}/)")

    # Check that all signals are routing-scope
    for sig in signals:
        if sig["name"] not in ROUTING_SCOPE_FIELDS:
            errors.append(f"Non-routing signal: {sig['name']}")

    return len(errors) == 0, errors


def _check_fidelity(signals: list[dict], *, strict: bool = False) -> tuple[bool, list[str]]:
    """Check signal fidelity if mapping artifact exists. Returns (ok, errors).

    F-18 NOTE: This function has a deliberate side effect — it mutates signal dicts
    in-place by adding sig['fidelity_provisional'] = True for signals with provisional
    ratings. This is intentional: the caller (cmd_extract) needs these annotations in
    the signals list to include them in the output artifact. The schema defines
    fidelity_provisional as an optional boolean property.

    Args:
        strict: If True (CI mode), require mapping artifact to exist.
                If False (default), skip fidelity check when mapping is absent.
    """
    if not MAPPING_PATH.exists():
        if strict:
            return False, [
                "Mapping artifact not found and --strict mode is enabled. "
                "Cannot perform fidelity check without mapping artifact. "
                "Ensure docs/transfer/blis_to_llmd_mapping.md exists."
            ]
        import sys as _sys
        print("WARNING: Mapping artifact not found — fidelity check skipped. "
              "Run validate-mapping after creating the mapping artifact. "
              "Use --strict to enforce mapping artifact presence (recommended for CI).",
              file=_sys.stderr)
        return True, []  # Defer check to Stage 2

    # R2-F-12 fix: Size guard on mapping artifact for consistency with
    # best_program.py and best_program_info.json guards in cmd_extract.
    MAX_MAPPING_SIZE = 10 * 1024 * 1024  # 10 MB — same as other file guards
    if MAPPING_PATH.stat().st_size > MAX_MAPPING_SIZE:
        return False, [
            f"Mapping artifact exceeds {MAX_MAPPING_SIZE} bytes — refusing to read. "
            f"This is a safety guard; the mapping artifact should be small."
        ]
    # R2-F-3 fix: Emit a notice when running without --strict but mapping exists,
    # so developers are aware of the local/CI divergence.
    if not strict:
        import sys as _sys
        print("NOTICE: Running without --strict. Fidelity checks are active (mapping artifact "
              "found), but CI uses --strict for deterministic enforcement. Use --strict locally "
              "to match CI behavior.",
              file=_sys.stderr)
    # F-15 fix: Wrap read_text() in try/except for PermissionError/OSError.
    try:
        content = MAPPING_PATH.read_text()
    except OSError as e:
        return False, [f"Failed to read mapping artifact: {e}"]
    errors = []
    warnings = []
    # R5-F-12 fix: Named constants for column skip counts to prevent regressions.
    # These numbers represent how many pipe-delimited columns to skip between the
    # signal name (col 1) and the Fidelity column. They have been a recurring source
    # of bugs (R2-F-1 changed to {5}, R3-F-1 reverted to {4}).
    # Main table: | Sim Signal | Go Type | Sim Access Path | Prod Equivalent | Prod Access Path | Fidelity | ...
    #                col 1       col 2     col 3             col 4              col 5               col 6
    #              matched     ←———————————— skip 4 columns ————————————→     captured
    MAIN_TABLE_FIDELITY_COL_OFFSET = 4
    # Additional Signals table: | Signal | Context | Production Mapping | Fidelity | Notes |
    #                            col 1     col 2     col 3               col 4
    #                           matched  ←——— skip 2 columns ————→     captured
    ADDITIONAL_TABLE_FIDELITY_COL_OFFSET = 2

    for sig in signals:
        # Look for the signal in the mapping table and extract its fidelity rating.
        # F-5 fix: Also detect *(provisional)* annotation after the fidelity rating
        pattern = rf'\|\s*{re.escape(sig["name"])}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{{MAIN_TABLE_FIDELITY_COL_OFFSET}}}\s*(low|medium|high)\s*(?:\*\(provisional\)\*)?\s*\|'
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            # Fallback: try matching in the Additional Signals table (different column count)
            # Format: | Signal | Context | Production Mapping | Fidelity | Notes |
            pattern_alt = rf'\|\s*{re.escape(sig["name"])}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{{ADDITIONAL_TABLE_FIDELITY_COL_OFFSET}}}\s*(low|medium|high)\s*(?:\*\(provisional\)\*)?\s*\|'
            match = re.search(pattern_alt, content, re.IGNORECASE)
        if match:
            rating = match.group(1).lower()
            # R2-F-13 note: The Composite Signals table uses 'medium (composite)' as the
            # fidelity rating, which would NOT match the `(low|medium|high)` regex.
            # Currently this is harmless because composite signals (e.g., EffectiveLoad)
            # are not in the signals[] array — they are in composite_signals[]. If a future
            # change adds composite signals to signals[], the regex must be updated to
            # handle parenthetical qualifiers (e.g., `(low|medium|high)(?:\s*\([^)]*\))?`).
            # F-5 fix: Detect provisional ratings and emit a warning
            is_provisional = "*(provisional)*" in match.group(0)
            if is_provisional:
                import sys as _sys
                print(f"WARNING: Signal '{sig['name']}' has provisional {rating} fidelity rating. "
                      f"No empirical data supports this rating — PR5 must validate. "
                      f"If PR5 downgrades to low, previously-transferred algorithms using "
                      f"this signal must be re-evaluated.",
                      file=_sys.stderr)
                warnings.append(f"Signal '{sig['name']}' has provisional {rating} rating")
                # F-7 fix: Tag the signal with provisional status so downstream
                # stages can programmatically distinguish provisional from confirmed.
                sig["fidelity_provisional"] = True
            if rating == "low":
                errors.append(
                    f"Signal '{sig['name']}' has low fidelity rating — "
                    f"pipeline halted (low-fidelity signals not supported in v1)"
                )
        else:
            # Signal not found in mapping table — treat as unknown fidelity (unsafe)
            errors.append(
                f"Signal '{sig['name']}' not found in mapping artifact — "
                f"unknown fidelity treated as unsafe (add signal to mapping table)"
            )
    return len(errors) == 0, errors


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract algorithm metadata from routing artifacts."""
    # R3-F-6 fix: Validate routing_dir FIRST, before CI env var check.
    # If routing_dir is invalid, the user needs to know about that infrastructure
    # error regardless of CI mode. Previously, the CI check preceded routing_dir
    # validation, causing a misleading --strict error when the real problem was
    # a missing/invalid directory.
    routing_dir = Path(args.routing_dir).resolve()

    # R3-F-6 fix: Infrastructure validation FIRST — routing_dir must exist and be
    # within REPO_ROOT before any mode-specific checks. This ensures the user gets
    # an actionable error about the actual problem (missing/invalid directory)
    # rather than a misleading CI/--strict error.
    # F-23 + F-20 fix: Validate routing_dir is within REPO_ROOT using
    # Path.is_relative_to() (Python 3.9+) instead of string prefix matching,
    # which is robust against symlink-based bypasses on some systems.
    if not routing_dir.is_relative_to(REPO_ROOT):
        return _output("error", 2, errors=[
            f"routing_dir '{routing_dir}' is outside repository root '{REPO_ROOT}'. "
            f"Path must be within the project directory."])
    if not routing_dir.is_dir():
        return _output("error", 2, errors=[f"Routing directory not found: {routing_dir}"])

    # F-1 fix: CI auto-detection — FAIL if --strict not used in CI environment.
    # A warning is insufficient because CI systems do not fail on stderr output.
    # Hard failure ensures non-deterministic results cannot pass CI silently.
    import os as _os
    # F-9 fix: Check for truthy CI values only. CI='false' or CI='0' should NOT
    # trigger CI enforcement, matching developer expectations.
    _ci_val = _os.environ.get("CI", "").lower()
    if _ci_val in ("true", "1", "yes") and not getattr(args, 'strict', False):
        # F-20 fix: Clear error message explaining why this fails and how to resolve.
        # Note: CI=true may be set by developers locally for other reasons (e.g.,
        # testing CI-specific behavior of other tools). The error message should be
        # clear about how to resolve: either pass --strict or unset CI.
        return _output("error", 1, errors=[
            "CI environment detected (CI env var is set) but --strict flag not set. "
            "CI pipelines MUST use --strict to ensure deterministic fidelity checks. "
            "Without --strict, extract skips fidelity checks when the mapping artifact "
            "is absent, producing non-deterministic results. "
            "Fix: either pass --strict (recommended), set CI=false, or unset the CI "
            "environment variable if you are running locally and do not want CI-mode enforcement. "
            "Usage: python tools/transfer_cli.py extract --strict routing/"
        ])

    program_py = routing_dir / "best_program.py"
    if not program_py.exists():
        return _output("error", 2, errors=[f"best_program.py not found in {routing_dir}"])

    info_json = routing_dir / "best_program_info.json"
    if not info_json.exists():
        return _output("error", 2, errors=[f"best_program_info.json not found in {routing_dir}"])

    # F-21 defense-in-depth: Size guard on routing artifacts.
    # These are repository-local files (not user-uploaded), so multi-MB files are
    # unlikely. Guard set at 10 MB — generous for any reasonable EVOLVE-BLOCK program.
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
    if program_py.stat().st_size > MAX_FILE_SIZE:
        return _output("error", 2, errors=[
            f"best_program.py exceeds {MAX_FILE_SIZE} bytes — refusing to read. "
            f"This is a safety guard; routing artifacts should be small."])
    if info_json.stat().st_size > MAX_FILE_SIZE:
        return _output("error", 2, errors=[
            f"best_program_info.json exceeds {MAX_FILE_SIZE} bytes — refusing to read. "
            f"This is a safety guard; routing artifacts should be small."])

    # Read and parse
    source = program_py.read_text()
    block, line_range = _extract_evolve_block(source)
    if block is None:
        # F-18 fix: _extract_evolve_block emits specific warnings to stderr for
        # mismatched markers (START without END, END without START). The error
        # message here covers the general case; check stderr for diagnostics.
        return _output("error", 2, errors=[
            "EVOLVE-BLOCK markers not found or malformed in best_program.py. "
            "Check stderr for specific diagnostic (e.g., START without END)."])

    # Extract signals
    signals = _extract_signals(block)
    if not signals:
        return _output("error", 1, errors=["No routing signals found in EVOLVE-BLOCK"])

    # Sanity check: warn if signal count is suspiciously low.
    # The current EVOLVE-BLOCK accesses 5 RoutingSnapshot fields + 1 request field = 6.
    # Threshold rationale (F-31): 3 is ~50% of the expected 6 signals, chosen as the
    # midpoint where regex failure is more likely than a legitimately sparse algorithm.
    # At 1-2 signals, the regex almost certainly missed patterns; at 4+, the algorithm
    # may simply use fewer signals. Update this threshold if the EVOLVE-BLOCK changes
    # significantly (e.g., if a future algorithm uses only 2-3 signals by design,
    # lower the threshold accordingly).
    # F-19 fix: In --strict mode, this is a hard failure (exit 1) instead of a warning.
    # R2-F-15 fix: CONFIGURABILITY PATH — If multi-algorithm support is added (future PR),
    # move this threshold to algorithm_summary.schema.json as a per-algorithm field
    # (e.g., "minimum_expected_signals": 3 in the algorithm config). For v1 with a
    # single algorithm, the hardcoded constant is appropriate and avoids unnecessary
    # configuration complexity. The golden-file test (EXPECTED_SIGNALS) provides the
    # authoritative signal count check; this threshold is a secondary heuristic guard.
    MINIMUM_EXPECTED_SIGNALS = 3
    if len(signals) < MINIMUM_EXPECTED_SIGNALS:
        msg = (f"Only {len(signals)} signals found (expected >= {MINIMUM_EXPECTED_SIGNALS}). "
               f"Regex may have missed field access patterns. Manually verify against EVOLVE-BLOCK.")
        if getattr(args, 'strict', False):
            return _output("error", 1, errors=[msg])
        import sys as _sys
        print(f"WARNING: {msg}", file=_sys.stderr)

    # Scope validation
    scope_ok, scope_errors = _check_scope(block, signals)

    # Fidelity check (if mapping exists; mandatory in --strict mode)
    fidelity_ok, fidelity_errors = _check_fidelity(signals, strict=getattr(args, 'strict', False))
    if not fidelity_ok:
        return _output("error", 1, errors=fidelity_errors)

    # Read metrics
    try:
        info = json.loads(info_json.read_text())
    except json.JSONDecodeError as e:
        return _output("error", 2, errors=[f"Malformed JSON in {info_json}: {e}"])
    metrics = info.get("metrics", {})
    if not metrics or "combined_score" not in metrics:
        import sys as _sys
        msg = ("WARNING: No 'metrics' key or missing 'combined_score' in "
               "best_program_info.json — algorithm_summary.json will have "
               "empty/incomplete metrics and WILL FAIL schema validation "
               "(schema requires metrics.combined_score). "
               "F-12 note: This is intentional for non-strict (exploratory) mode — "
               "the artifact is written so you can inspect signal extraction results, "
               "but it is NOT pipeline-ready. Use --strict to enforce schema validity, "
               "or fix the metrics and re-run.")
        print(msg, file=_sys.stderr)
        # F-12 fix: In --strict mode, fail instead of producing an artifact
        # that cannot pass schema validation. In non-strict mode, proceed
        # for exploratory use (user can inspect partial results).
        if getattr(args, 'strict', False):
            return _output("error", 1,
                           errors=["metrics.combined_score missing in "
                                   "best_program_info.json (required by schema). "
                                   "Cannot produce schema-valid artifact in --strict mode."])

    # Build summary — this is the FILE ARTIFACT written to workspace/.
    # It contains the 6 schema-required keys. This is distinct from the
    # stdout JSON (produced by _output()) which contains operational metadata.
    # See "Two-output design" in Part 1, Section C.
    # NOTE: algorithm_name is hardcoded for PR1 (single algorithm).
    # For multi-algorithm support, derive from input artifact metadata.
    # R3-F-16 note: block.encode() is safe here — block is a Python str (from
    # read_text() → split → join), and str.encode() always succeeds on a valid
    # Python str (Unicode → UTF-8). If the source file contained non-UTF-8 bytes,
    # read_text() would have already raised UnicodeDecodeError (exit 2 path).
    block_hash = hashlib.sha256(block.encode()).hexdigest()

    # Read mapping artifact version for staleness tracking (F-13)
    # Versioning policy: mapping artifact uses semantic versioning (MAJOR.MINOR).
    # MAJOR bump = signal added/removed or fidelity rating changed.
    # MINOR bump = documentation-only changes.
    # If version cannot be parsed, falls back to "unknown" with a warning.
    # FORMAT COUPLING (F-13): The regex below expects Markdown bold format '**Version:** X.Y'.
    # If the mapping artifact format changes, update this regex and the test
    # test_extract_mapping_version_parsed. The mapping artifact and this parser
    # are co-created in PR1, so the format is consistent within this PR.
    # F-9 fix: SILENT FALLBACK RISK — if the format changes (e.g., 'Version: 1.0'
    # without bold), the regex fails silently and version becomes 'unknown'. This
    # could mask staleness issues since 'unknown' passes schema validation. The
    # test_extract_mapping_version_parsed test catches this regression by asserting
    # version != 'unknown'. In --strict mode, consider failing on 'unknown' version
    # (not implemented in v1 — acceptable since mapping and parser are co-created).
    mapping_version = "unknown"
    if MAPPING_PATH.exists():
        import re as _re
        # R2-F-7 fix: Wrap read_text() in try/except for consistency with _check_fidelity.
        # TOCTOU gap: file could be deleted between exists() check and read_text().
        try:
            _mapping_content = MAPPING_PATH.read_text()
        except OSError:
            _mapping_content = ""  # Fall through to "unknown" version
        ver_match = _re.search(r'\*\*Version:\*\*\s*(\S+)', _mapping_content)
        if ver_match:
            raw_version = ver_match.group(1)
            # F-26 fix: Validate captured version against schema pattern (MAJOR.MINOR only).
            # The regex captures any non-whitespace, but the schema requires ^(unknown|\d+\.\d+)$.
            # If the mapping artifact has e.g. '1.0.0' (three components), reject it with a warning.
            if _re.fullmatch(r'\d+\.\d+', raw_version):
                mapping_version = raw_version
            else:
                import sys as _sys
                print(f"WARNING: Mapping artifact version '{raw_version}' does not match "
                      f"expected MAJOR.MINOR format (e.g., '1.0'). "
                      f"mapping_artifact_version will be 'unknown'. "
                      f"Fix the mapping artifact '**Version:**' line.",
                      file=_sys.stderr)
        else:
            import sys as _sys
            print("WARNING: Could not parse mapping artifact version — "
                  "mapping_artifact_version will be 'unknown'. "
                  "Ensure mapping has a '**Version:** X.Y' line.",
                  file=_sys.stderr)

    # F-16 fix: Include composite signals so PR3 can reconstruct without
    # consulting the mapping artifact separately
    composites = []
    for method, fields in METHOD_EXPANSIONS.items():
        # Only include composites whose constituents were actually found
        found_constituents = [f for f in fields if any(s["name"] == f for s in signals)]
        if found_constituents:
            # F-21 note: formula is hardcoded to "sum" based on manual verification
            # of EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests.
            # TestSourceSyncVerification::test_method_expansion_matches_source verifies
            # constituent fields are referenced in EffectiveLoad() body but does not
            # verify the operation is addition. The "sum" formula was confirmed by
            # manual inspection of the `return r.QueueDepth + r.BatchSize + r.InFlightRequests`
            # line in routing.go. If EffectiveLoad() changes to use a different
            # operation (e.g., weighted sum, max), update this formula AND the
            # schema's formula enum.
            composites.append({
                "name": method,
                "constituents": found_constituents,
                "formula": "sum",
            })

    # R2-F-11 fix: Track whether fidelity checks were performed. When the mapping
    # artifact is absent and --strict is not set, fidelity checks are skipped.
    # Downstream stages consuming this artifact can use this field to determine
    # whether the artifact has been validated against the mapping.
    fidelity_checked = MAPPING_PATH.exists()  # True if mapping was present during extraction

    summary = {
        "algorithm_name": "blis_weighted_scoring",
        "evolve_block_source": f"routing/best_program.py:{line_range}",
        "evolve_block_content_hash": block_hash,
        "signals": signals,
        "composite_signals": composites,
        "metrics": metrics,
        "scope_validation_passed": scope_ok,
        "mapping_artifact_version": mapping_version,
        "fidelity_checked": fidelity_checked,
    }

    # Write to workspace
    WORKSPACE.mkdir(exist_ok=True)
    summary_path = WORKSPACE / "algorithm_summary.json"
    # F-18 fix: Wrap file write in try-except to produce a clean JSON error
    # (exit 2) for infrastructure failures like permission denied or disk full,
    # instead of an unhandled Python traceback.
    try:
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    except OSError as e:
        return _output("error", 2, errors=[
            f"Failed to write {summary_path}: {e}. "
            f"Check file permissions and available disk space."])

    all_errors = scope_errors + fidelity_errors
    # F-13 fix: scope failure writes the artifact (above) then returns exit 1.
    # Fidelity failure returns exit 1 BEFORE writing (see _check_fidelity path above).
    # This asymmetry is intentional: scope failure is a validation result (the artifact
    # records scope_validation_passed=false for inspection), while fidelity failure is
    # a safety halt (no artifact should exist with unverified fidelity).
    # R3-F-10 fix: DUAL FAILURE BEHAVIOR — when both scope AND fidelity fail,
    # fidelity takes precedence (checked first in the code flow above). The fidelity
    # check returns exit 1 BEFORE the artifact write, so NO artifact is produced.
    # This means the documented behavior "scope failure always writes the artifact"
    # is only true when fidelity passes. Combined failure: no artifact, exit 1 with
    # fidelity errors only (scope errors are not reported since we exit early).
    # F-13 fix: Include output_type on error paths for consistent JSON structure.
    if not scope_ok:
        return _output("error", 1,
                        output_type="operational_report",
                        artifact_path=str(summary_path),
                        algorithm_name=summary["algorithm_name"],
                        signal_count=len(signals),
                        errors=all_errors)

    return _output("ok", 0,
                    output_type="operational_report",
                    artifact_path=str(summary_path),
                    algorithm_name=summary["algorithm_name"],
                    signal_count=len(signals),
                    errors=all_errors)


def cmd_validate_mapping(args: argparse.Namespace) -> int:
    """Validate mapping artifact completeness against algorithm summary."""
    if not MAPPING_PATH.exists():
        # F-4 fix: Missing file is infrastructure error (exit 2), per Section F convention.
        # "File not found" = infrastructure error, not validation failure.
        # F-27 fix: Include extra_signals=[] on all exit paths for consistent JSON structure.
        return _output("error", 2, errors=[f"Mapping artifact not found: {MAPPING_PATH}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # Load algorithm summary if available
    summary_path = args.summary if hasattr(args, "summary") and args.summary else (
        WORKSPACE / "algorithm_summary.json"
    )
    summary_path = Path(summary_path).resolve()

    # R3-F-8 fix: Defense-in-depth path bounding on --summary argument, consistent
    # with cmd_extract (routing_dir) and cmd_validate_schema (artifact_path).
    if not summary_path.is_relative_to(REPO_ROOT):
        return _output("error", 2, errors=[
            f"Summary path '{summary_path}' is outside repository root '{REPO_ROOT}'."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    if not summary_path.exists():
        # F-27 fix: Include extra_signals=[] for consistent JSON structure.
        return _output("error", 2,
                        errors=[f"Algorithm summary not found: {summary_path}. Run 'extract' first."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # R4-F-12 fix: Size guard on algorithm_summary.json, consistent with
    # MAX_MAPPING_SIZE guard on the mapping artifact and MAX_FILE_SIZE guards
    # in cmd_extract. Prevents unbounded memory allocation from corrupt files.
    MAX_SUMMARY_SIZE = 10 * 1024 * 1024  # 10 MB — same as other file guards
    try:
        if summary_path.stat().st_size > MAX_SUMMARY_SIZE:
            return _output("error", 2,
                            errors=[f"Algorithm summary exceeds {MAX_SUMMARY_SIZE} bytes — refusing to read."],
                            mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to stat algorithm summary: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # F-15 fix: Wrap JSON parsing in try/except for corrupt/unreadable files.
    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # F-27 fix: Include extra_signals=[] for consistent JSON structure.
        return _output("error", 2,
                        errors=[f"Failed to read/parse algorithm summary: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # R5-F-4 fix: Validate that summary['signals'] is a list before iterating.
    # A corrupt or manually-edited summary file could have 'signals': 'not_a_list',
    # which would cause TypeError in the set comprehension below.
    try:
        extracted_names = {sig['name'] for sig in summary.get('signals', [])}
    except (TypeError, KeyError) as e:
        return _output("error", 2,
                        errors=[f"Malformed algorithm summary — 'signals' is not a valid list of dicts: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # R2-F-12 fix: Size guard on mapping artifact (consistent with _check_fidelity).
    # R4-F-13 fix: Wrapped stat() in try/except for TOCTOU safety. If the mapping
    # file is deleted between the exists() check above and this stat() call,
    # FileNotFoundError is raised. Without the try/except, this produces an
    # unhandled traceback instead of a clean JSON error response.
    MAX_MAPPING_SIZE = 10 * 1024 * 1024  # 10 MB
    try:
        _mapping_size = MAPPING_PATH.stat().st_size
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to stat mapping artifact: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)
    if _mapping_size > MAX_MAPPING_SIZE:
        # F-27 fix: Include extra_signals=[] for consistent JSON structure.
        return _output("error", 2,
                        errors=[f"Mapping artifact exceeds {MAX_MAPPING_SIZE} bytes — refusing to read."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # R2-F-6 fix: Wrap read_text() in try/except for consistency with _check_fidelity.
    # Race condition (file deleted between stat() size check and read_text()) or
    # permission error would otherwise produce an unhandled traceback.
    try:
        content = MAPPING_PATH.read_text()
    except OSError as e:
        return _output("error", 2,
                        errors=[f"Failed to read mapping artifact: {e}"],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=False)

    # F-30 fix: Detect malformed mapping artifact before regex parsing.
    # If the Markdown table structure is missing entirely, report "malformed"
    # instead of misleading "missing signals" from empty regex matches.
    if '|' not in content or not re.search(r'^\|.*\|.*\|', content, re.MULTILINE):
        # F-27 fix: Include extra_signals=[] for consistent JSON structure.
        # R2-F-23 fix: stale_commit=True for malformed artifacts (commit hash status
        # is unknown when the table is malformed, so reporting "not stale" is misleading).
        return _output("error", 1,
                        errors=["Malformed mapping artifact: no Markdown table found. "
                                "Expected pipe-delimited table rows."],
                        mapping_complete=False, missing_signals=[], extra_signals=[], stale_commit=True)

    # Check each extracted signal has a mapping entry (extract→mapping direction)
    # F-19 NOTE: This regex matches the signal name between ANY two pipes in the
    # document, not just the first column. A signal name appearing in a Rationale or
    # Notes column of a different row would be falsely detected as mapped. For v1
    # with known signal names (QueueDepth, BatchSize, etc.), this is unlikely to
    # cause issues because these names are not used as common words in prose. The
    # bidirectional check (extra signals detection below) uses first-column-anchored
    # parsing, which provides a cross-check.
    # R5-F-4: extracted_names computed above with try/except guard
    missing = [name for name in sorted(extracted_names)
               if not re.search(rf'\|\s*{re.escape(name)}\s*\|', content)]

    # Check for extra signals in mapping not present in extract (mapping→extract direction)
    # F-3 fix: Detect spurious mapping rows that don't correspond to extracted signals.
    # Parse signal names from the mapping table's first column (Sim Signal).
    mapping_signals = set()
    # F-22 fix: Track duplicate signal rows so conflicting fidelity ratings are reported.
    mapping_signal_counts: dict[str, int] = {}
    # F-12 note: \w+ matches [a-zA-Z0-9_] only — signal names with hyphens
    # (e.g., 'cache-hit-rate') would not be matched. Current signal names
    # (QueueDepth, BatchSize, etc.) are all word characters, so this is
    # acceptable for v1. If hyphenated signal names are introduced in future,
    # update this regex to r'^\|\s*([\w-]+)(?:\s*\([^)]*\))?\s*\|'.
    # R4-F-2 fix: Added (?:\s*\([^)]*\))? to handle parenthetical qualifiers
    # like 'SessionID (boolean check)' in the Additional Signals table.
    # Without this, SessionID is never added to mapping_signals and always
    # appears as an 'extra signal' in extracted_names - mapping_signals.
    # This matches the pattern used in _check_fidelity's signal-matching regex.
    for row_match in re.finditer(r'^\|\s*(\w+)(?:\s*\([^)]*\))?\s*\|', content, re.MULTILINE):
        candidate = row_match.group(1)
        # Skip table headers and non-signal rows.
        # F-14 note: This is a hardcoded allowlist of header words. If the mapping
        # artifact table format changes or new section headers are added, update
        # this list. The mapping artifact and this parser are co-created in PR1,
        # so the format is consistent. If extending the mapping table in future PRs,
        # add new header words here and add a test case for the new format.
        if candidate in ("Sim", "Signal", "Composite"):
            continue
        mapping_signal_counts[candidate] = mapping_signal_counts.get(candidate, 0) + 1
        mapping_signals.add(candidate)
    extra = sorted(mapping_signals - extracted_names)
    # F-22 + F-19 fix: Detect duplicate signal rows (ambiguous fidelity ratings).
    # Duplicates cause validation failure because conflicting fidelity ratings
    # make the mapping ambiguous — the CLI cannot determine which rating to use.
    duplicates = sorted(k for k, v in mapping_signal_counts.items() if v > 1)

    # Check commit hash presence — must be an actual hex hash, not just the label.
    # F-3 fix: The regex matches the Markdown format '**Pinned commit hash:** <hex>'.
    # Match path: 'commit[_ ]hash' branch finds 'commit hash' as a substring within
    # 'Pinned commit hash:', then [:\s*]* matches ':** ', then captures the hex hash.
    # Also matches alternative formats: 'Pinned commit: <hex>', 'commit_hash: <hex>'.
    # The regex is intentionally flexible to accommodate minor format variations.
    has_commit = bool(re.search(
        r'(?:commit[_ ]hash|pinned[_ ]commit[_ ]hash)[:\s*]*([0-9a-f]{7,40})',
        content, re.IGNORECASE,
    ))

    mapping_complete = (len(missing) == 0 and len(extra) == 0
                        and len(duplicates) == 0 and has_commit)
    errors = []
    if missing:
        errors.append(f"Missing signal mappings: {', '.join(missing)}")
    if extra:
        # F-10 fix: Clarify resolution direction. Extra signals in mapping not
        # in extract means either: (a) stale/spurious mapping rows that should
        # be removed from the mapping, or (b) extraction gaps where the regex
        # missed a signal that is legitimately in the EVOLVE-BLOCK. Investigate
        # (a) first — check if the signal is actually accessed in the EVOLVE-BLOCK.
        errors.append(
            f"Extra signals in mapping not found in extract: {', '.join(extra)}. "
            f"Resolution: First check if these signals are accessed in the EVOLVE-BLOCK. "
            f"If not, remove the stale rows from the mapping artifact. "
            f"If they are accessed, the extract regex has a gap — fix _extract_signals."
        )
    if duplicates:
        # R2-F-16 fix: Include row counts to help identify which rows to inspect.
        # R3-F-14 fix: Clarified wording — duplicates are a data quality error
        # regardless of whether fidelity ratings conflict. The validator does not
        # compare fidelity values; it rejects all duplicates because each signal
        # MUST appear exactly once in the mapping table.
        dup_details = [f"{name} ({mapping_signal_counts[name]} rows)" for name in duplicates]
        errors.append(
            f"Duplicate signal rows found: {', '.join(dup_details)}. "
            f"Each signal MUST appear exactly once in the mapping table (duplicates are "
            f"rejected regardless of whether fidelity ratings match or conflict). "
            f"Resolve by removing duplicate rows and keeping the row with the correct fidelity rating."
        )
    if not has_commit:
        errors.append("No commit hash found in mapping artifact")

    # R5-F-9 fix: Detect production metric overlap (double-counting risk).
    # Parse "Production Equivalent" column for each signal and warn if multiple
    # sim signals map to the same production metric (e.g., BatchSize and
    # InFlightRequests both falling back to RunningQueueSize).
    prod_metric_signals: dict[str, list[str]] = {}
    for sig_name in extracted_names:
        prod_match = re.search(
            rf'\|\s*{re.escape(sig_name)}(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){{2}}([^|]+)\|',
            content, re.IGNORECASE,
        )
        if prod_match:
            prod_metric = prod_match.group(1).strip()
            prod_metric_signals.setdefault(prod_metric, []).append(sig_name)
    for prod_metric, sigs in prod_metric_signals.items():
        if len(sigs) > 1:
            import sys as _sys
            print(
                f"WARNING: Double-counting risk — signals {', '.join(sigs)} "
                f"both map to production metric '{prod_metric}'. "
                f"PR3 MUST ensure the composite computation accounts for this overlap.",
                file=_sys.stderr,
            )

    status = "ok" if mapping_complete else "error"
    exit_code = 0 if mapping_complete else 1
    # R2-F-16 fix: Include output_type for consistent JSON structure across all commands.
    return _output(status, exit_code,
                    output_type="mapping_validation",
                    mapping_complete=mapping_complete,
                    missing_signals=missing,
                    extra_signals=extra,
                    stale_commit=not has_commit,
                    errors=errors)


def cmd_validate_schema(args: argparse.Namespace) -> int:
    """Validate a workspace artifact against its JSON Schema."""
    # R3-F-2 fix: Use relative import 'from schema_validator' instead of
    # 'from tools.schema_validator'. When transfer_cli.py is invoked as a script
    # ('python tools/transfer_cli.py validate-schema ...'), Python adds the script's
    # directory (tools/) to sys.path, not REPO_ROOT. 'from tools.schema_validator'
    # requires REPO_ROOT on sys.path, which is absent in script mode, causing
    # ModuleNotFoundError. 'from schema_validator' works because tools/ is on sys.path.
    # Tests pass either way because pytest adds REPO_ROOT to sys.path, masking the issue.
    from schema_validator import validate_artifact, load_schema

    artifact_path = Path(args.artifact_path).resolve()
    # F-14 fix: Defense-in-depth path bounding (consistent with cmd_extract).
    # These are read-only commands on a developer tool, so the risk is low,
    # but bounding prevents accidental reads from unrelated directories.
    if not artifact_path.is_relative_to(REPO_ROOT):
        return _output("error", 2, errors=[
            f"Artifact path '{artifact_path}' is outside repository root '{REPO_ROOT}'."])
    if not artifact_path.exists():
        return _output("error", 2, errors=[f"Artifact not found: {artifact_path}"])

    # F-23 fix: Schema naming convention — schema filename is derived from artifact stem.
    # Convention: artifact 'foo.json' → schema 'foo.schema.json' in SCHEMAS_DIR.
    # PR3+ artifacts MUST have a matching schema file in tools/schemas/ to be validatable.
    # Example: workspace/algorithm_summary.json → tools/schemas/algorithm_summary.schema.json
    stem = artifact_path.stem  # e.g., "algorithm_summary"
    schema_path = (SCHEMAS_DIR / f"{stem}.schema.json").resolve()
    # R2-F-8 fix: Defense-in-depth bounds check on derived schema_path.
    # Although artifact_path is validated with is_relative_to(REPO_ROOT) above,
    # and Path.stem extracts only the final component's stem, adding a bounds
    # check on schema_path prevents any edge case where the derived path
    # resolves outside SCHEMAS_DIR.
    if not schema_path.is_relative_to(SCHEMAS_DIR):
        return _output("error", 2, errors=[
            f"Schema path '{schema_path}' resolves outside schemas directory '{SCHEMAS_DIR}'."])
    if not schema_path.exists():
        return _output("error", 2, errors=[f"Schema not found: {schema_path}"])

    try:
        schema = load_schema(schema_path)
    except (json.JSONDecodeError, OSError) as e:
        return _output("error", 2, errors=[f"Failed to load schema: {e}"])

    try:
        data = json.loads(artifact_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # F-18 fix: Unparseable JSON is an infrastructure error (exit 2), not a
        # validation failure (exit 1), per Section F exit code boundary definition.
        return _output("error", 2, errors=[f"Failed to load artifact: {e}"],
                        schema_path=str(schema_path), artifact_path=str(artifact_path),
                        violations=[])

    errors = validate_artifact(data, schema)
    violations = [{"field": e.split(":")[0].strip(), "message": e} for e in errors]

    # R2-F-16 fix: Include output_type for consistent JSON structure across all commands.
    if errors:
        return _output("error", 1,
                        output_type="schema_validation",
                        schema_path=str(schema_path),
                        artifact_path=str(artifact_path),
                        violations=violations,
                        errors=errors)

    return _output("ok", 0,
                    output_type="schema_validation",
                    schema_path=str(schema_path),
                    artifact_path=str(artifact_path),
                    violations=[])


def main():
    # F-14 fix: Runtime version check — Path.is_relative_to() requires Python 3.9+.
    # The error from AttributeError would be confusing; this provides a clear message.
    if sys.version_info < (3, 9):
        print("ERROR: transfer_cli.py requires Python >= 3.9 "
              f"(running {sys.version_info.major}.{sys.version_info.minor})",
              file=sys.stderr)
        sys.exit(2)
    parser = argparse.ArgumentParser(
        description="Transfer pipeline CLI — mechanical support for sim-to-production transfer",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # extract
    p_extract = subparsers.add_parser("extract",
        help="Parse EVOLVE-BLOCK, produce algorithm_summary.json. "
             "NOTE: Downstream stages must consume workspace/algorithm_summary.json (file artifact), "
             "not stdout (operational report).")
    p_extract.add_argument("routing_dir", help="Path to routing/ directory")
    p_extract.add_argument("--strict", action="store_true",
        help="CI mode: (1) require mapping artifact, fail if absent; "
             "(2) enforce fidelity checks (low-fidelity signals halt); "
             "(3) fail if signal count < minimum threshold; "
             "(4) fail if metrics.combined_score missing. "
             "Required for deterministic CI results.")
    p_extract.set_defaults(func=cmd_extract)

    # validate-mapping
    p_mapping = subparsers.add_parser("validate-mapping", help="Check mapping artifact completeness")
    p_mapping.add_argument("--summary", help="Path to algorithm_summary.json (default: workspace/)")
    p_mapping.set_defaults(func=cmd_validate_mapping)

    # validate-schema
    p_schema = subparsers.add_parser("validate-schema", help="Validate workspace artifact against schema")
    p_schema.add_argument("artifact_path", help="Path to workspace JSON artifact")
    p_schema.set_defaults(func=cmd_validate_schema)

    args = parser.parse_args()
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_transfer_cli.py::TestExtract -v`
Expected: PASS (all 7 tests)

**Step 5: Lint**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m ruff check tools/transfer_cli.py 2>/dev/null || echo "ruff not installed — skip lint"`

**Step 6: Commit**

```bash
git add tools/transfer_cli.py tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
feat(transfer): CLI extract command (BC-1, BC-2, BC-5, BC-7, BC-8)

- Parse EVOLVE-BLOCK from routing/best_program.py
- Extract RoutingSnapshot field access patterns via regex
- Scope validation (routing-only, no P/D disaggregation)
- Fidelity check against mapping artifact when present
- All output as JSON, exit codes 0/1/2

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 4: Validate-Mapping Command

**Contracts Implemented:** BC-3, BC-9

**Files:**
- Modify: `tools/transfer_cli.py` (already has validate-mapping, but tests drive verification)
- Modify: `tools/test_transfer_cli.py`

**Step 1: Write failing tests for validate-mapping**

```python
# Append to tools/test_transfer_cli.py

class TestValidateMapping:
    def setup_method(self):
        """Ensure extract has run to produce algorithm_summary.json."""
        WORKSPACE.mkdir(exist_ok=True)
        run_cli("extract", str(ROUTING_DIR))

    @pytest.mark.skipif(
        not (REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="F-16 fix: Mapping artifact not yet created (expected during Task 4, "
               "created in Task 5). This test passes after Task 5 completes."
    )
    def test_validate_mapping_passes_with_complete_mapping(self):
        """BC-3: all signals mapped, commit hash present."""
        code, output = run_cli("validate-mapping")
        assert code == 0, f"Expected pass, got: {output}"
        assert output["mapping_complete"] is True
        assert output["missing_signals"] == []

    def test_validate_mapping_reports_missing_artifact(self):
        """BC-9: missing mapping artifact exits with code 2 (infrastructure error — file not found)."""
        # Temporarily rename mapping to test missing case
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.move(str(mapping), str(backup))
        try:
            code, output = run_cli("validate-mapping")
            assert code == 2, f"Missing mapping artifact should be exit 2 (infrastructure error), got {code}"
            assert output["status"] == "error"
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    def test_validate_mapping_without_summary_exits_2(self):
        """BC-9: missing algorithm summary exits with code 2."""
        summary = WORKSPACE / "algorithm_summary.json"
        backup = summary.with_suffix(".json.bak")
        if summary.exists():
            summary.rename(backup)
        try:
            code, output = run_cli("validate-mapping")
            assert code == 2
        finally:
            if backup.exists():
                backup.rename(summary)

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="Mapping artifact not present (pre-Task 5)"
    )
    def test_validate_mapping_rejects_placeholder_hash(self):
        """F-2 fix: validate-mapping MUST reject the placeholder commit hash.
        This is the automated gate preventing PLACEHOLDER_REQUIRES_STEP_2 from
        being committed. The placeholder is not a valid hex hash (7-40 chars),
        so the existing hex pattern check catches it.
        R2-F-20 fix: Added skipif guard for pre-Task 5 case (mapping artifact
        doesn't exist yet). Without this, the test raises FileNotFoundError
        instead of cleanly skipping."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.copy2(str(mapping), str(backup))
        try:
            content = mapping.read_text()
            # Replace actual hash with placeholder
            # R2-F-2 fix: The mapping artifact uses Markdown bold format:
            # '**Pinned commit hash:** <hash>'. The previous lookbehind
            # '(?<=Pinned commit hash:\s)' did not account for the '**' bold
            # markers and ':** ' separator, so re.sub silently returned the
            # original content unchanged, making the test vacuous.
            # Fixed by using a non-lookbehind approach that matches the full
            # Markdown bold format and replaces just the hash portion.
            import re
            content = re.sub(
                r'(\*\*Pinned commit hash:\*\*\s*)[0-9a-f]{7,40}',
                r'\1PLACEHOLDER_REQUIRES_STEP_2',
                content,
            )
            mapping.write_text(content)
            code, output = run_cli("validate-mapping")
            assert code == 1, (
                f"validate-mapping should reject placeholder hash, got exit {code}: {output}. "
                "The hex pattern check should catch 'PLACEHOLDER_REQUIRES_STEP_2'."
            )
            # R3-F-13 fix: Replaced weak OR assertion with direct check.
            # The previous OR clause meant the test passed even if stale_commit was
            # incorrectly False, as long as any error message contained 'commit' or 'hash'.
            # This could mask a bug where stale_commit is not correctly set.
            assert output.get("stale_commit") is True, (
                f"validate-mapping should set stale_commit=True for placeholder hash, "
                f"got stale_commit={output.get('stale_commit')}: {output}"
            )
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    # R4-F-5 fix: Added skipif guard consistent with test_validate_mapping_rejects_placeholder_hash.
    # Without this guard, mapping.read_text() raises FileNotFoundError when run before Task 5.
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="Mapping artifact not present (pre-Task 5)"
    )
    def test_validate_mapping_detects_extra_signals(self):
        """F-3 fix: validate-mapping detects signals in mapping that aren't in extract output.
        Extra rows in the mapping indicate stale/spurious entries that may mislead
        downstream consumers into expecting signals that the algorithm doesn't use."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.copy2(str(mapping), str(backup))
        try:
            content = mapping.read_text()
            # Inject a spurious signal row into the mapping table
            content = content.replace(
                "| QueueDepth |",
                "| FakeSignal | int | `snap.FakeSignal` | N/A | N/A | low | 0 | Spurious test row |\n| QueueDepth |",
            )
            mapping.write_text(content)
            code, output = run_cli("validate-mapping")
            assert code == 1, f"Expected failure for extra signal, got: {output}"
            assert "FakeSignal" in str(output.get("extra_signals", []))
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    # R4-F-5 fix: Added skipif guard consistent with test_validate_mapping_rejects_placeholder_hash.
    # Without this guard, mapping.read_text() raises FileNotFoundError when run before Task 5.
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "docs" / "transfer" / "blis_to_llmd_mapping.md").exists(),
        reason="Mapping artifact not present (pre-Task 5)"
    )
    def test_validate_mapping_detects_duplicate_signals(self):
        """F-19 fix: validate-mapping detects duplicate signal rows.
        R3-F-14 fix: Duplicates are rejected as a data quality error regardless
        of whether fidelity ratings conflict — each signal must appear exactly once."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.copy2(str(mapping), str(backup))
        try:
            content = mapping.read_text()
            # Inject a duplicate QueueDepth row with a different fidelity rating
            content = content.replace(
                "| QueueDepth |",
                "| QueueDepth | int | `snap.QueueDepth` | duplicate | N/A | medium | 0 | Duplicate test row |\n| QueueDepth |",
            )
            mapping.write_text(content)
            code, output = run_cli("validate-mapping")
            assert code == 1, f"Expected failure for duplicate signal, got: {output}"
            assert any("duplicate" in e.lower() for e in output.get("errors", [])), (
                f"Expected 'duplicate' in error message. Got: {output.get('errors', [])}"
            )
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))
```

**Step 2: Run tests**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_transfer_cli.py::TestValidateMapping -v`

R5-F-13 fix: Clarified expected test outcomes. Tests with `@pytest.mark.skipif` guards will be **skipped** (not failed) when the mapping artifact doesn't exist:
- **SKIP:** `test_validate_mapping_passes_with_complete_mapping` (skipif: mapping not present)
- **SKIP:** `test_validate_mapping_rejects_placeholder_hash` (skipif: mapping not present)
- **SKIP:** `test_validate_mapping_detects_stale_hash` (skipif: mapping not present)
- **PASS:** `test_validate_mapping_reports_missing_artifact` (tests missing-file error path)
- **PASS:** `test_validate_mapping_without_summary_exits_2` (tests missing-summary error path)
- **PASS:** `test_validate_mapping_detects_extra_signals` (uses inline mapping, no dependency)
- **PASS:** `test_validate_mapping_detects_duplicate_signals` (uses inline mapping, no dependency)

If any test that should PASS instead FAILs, investigate the failure — it indicates a real bug, not a Task 5 ordering issue.

**Task ordering note (F-19):** Tests with skipif guards depend on the mapping artifact created in Task 5. They will be skipped here and will run after Task 5 completes. The error-path tests work regardless of Task 5 ordering. Tasks execute sequentially (1→2→3→4→5→6→7), so this dependency is satisfied at integration time.

**Step 3: The mapping artifact is created in Task 5. For now, verify the command logic works with the missing-artifact and missing-summary test cases.**

**Step 4: Commit test additions**

```bash
git add tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
test(transfer): validate-mapping tests (BC-3, BC-9)

- Test complete mapping passes
- Test missing mapping artifact
- Test missing algorithm summary
- Test extra signals detection (F-3 bidirectional check)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 5: Mapping Artifact

**Contracts Implemented:** BC-3 (completes), BC-6

**Files:**
- Create: `docs/transfer/blis_to_llmd_mapping.md`

**Step 1: Write the mapping artifact**

Context: This documents the correspondence between inference-sim routing signals and llm-d-inference-scheduler production equivalents. The signal list comes from EVOLVE-BLOCK analysis.

```markdown
# BLIS-to-llm-d Signal Mapping Artifact

**Version:** 1.0
**Last verified against:** llm-d-inference-scheduler submodule HEAD
**Pinned commit hash:** PLACEHOLDER_REQUIRES_STEP_2 <!-- BLOCKER: Step 2 below MUST replace this BEFORE any commit. validate-mapping and the test suite WILL FAIL until this is a valid hex hash (7-40 chars). AUTOMATED SAFETY NETS: (1) validate-mapping's hex pattern check rejects this string, (2) TestValidateMapping::test_validate_mapping_rejects_placeholder_hash catches it in pytest, (3) Step 5 commit procedure includes an explicit pre-commit verification command. DO NOT proceed to Step 5 (commit) without completing Step 2. -->

## Signal Mapping Table

| Sim Signal | Go Type | Sim Access Path | Production Equivalent | Prod Access Path | Fidelity | Staleness Window (ms) | Rationale |
|------------|---------|-----------------|----------------------|------------------|----------|-----------------------|-----------|
| QueueDepth | int | `snap.QueueDepth` | `endpoint.GetMetrics().WaitingQueueSize` | LoadAware scorer | high | 0 | Same computation: count of waiting requests. Direct endpoint query in both systems. |
| BatchSize | int | `snap.BatchSize` | `endpoint.GetMetrics().RunningQueueSize` (approximate) | ActiveRequest scorer | medium | 0 | Sim tracks exact batch size; production uses running request count as proxy. **Structural semantic gap:** batch size (number of items in a batch) differs from queue size (number of items waiting). Correlation expected to be strong but not exact. **PR5 MUST measure:** Compare sim BatchSize distribution against prod RunningQueueSize to quantify the gap. If R² < 0.80, downgrade to low. |
| InFlightRequests | int | `snap.InFlightRequests` | `endpoint.GetMetrics().RunningRequestCount` (approximate) | ActiveRequest scorer | medium | 0 | Sim tracks in-flight requests at router level; production tracks running requests at endpoint. **Structural semantic gap:** Router-level counting includes requests in transit (not yet received by endpoint); endpoint-level counting only includes received requests. Same unit (request count) but different counting points. **PR5 MUST measure:** Compare sim InFlightRequests against prod RunningRequestCount under load to quantify the router-vs-endpoint gap. If R² < 0.80, downgrade to low. **Note:** Earlier drafts mapped to `ActiveModels` which is incorrect — ActiveModels counts model instances, not requests. `RunningRequestCount` is the correct production equivalent. If `RunningRequestCount` is unavailable, fall back to `RunningQueueSize` as a proxy. **F-10 WARNING: Double-counting risk** — `RunningQueueSize` is already the production mapping for BatchSize. If InFlightRequests also falls back to `RunningQueueSize`, the EffectiveLoad composite becomes `WaitingQueueSize + 2*RunningQueueSize`, double-counting that metric. PR3 MUST detect this case and either: (a) use a different proxy, or (b) adjust the composite computation to avoid double-counting. |
| KVUtilization | float64 | `snap.KVUtilization` | `endpoint.GetMetrics().KVCacheUsagePercent` | Custom scorer needed | high | 0 | Same computation: ratio of used KV cache to total. Both query endpoint metrics directly. **Units:** Sim = 0.0–1.0 ratio; Prod = 0–100 percentage. **Normalization (PR3):** Divide production value by 100 to match sim's 0.0–1.0 range (i.e., `prod_kv / 100.0`). The evolved algorithm expects the sim-scale range. **REQUIRED PR3 TEST:** PR3 MUST include a unit test verifying that production KVCacheUsagePercent values (0-100) are divided by 100 before being passed to the scorer. Without this normalization, the evolved algorithm receives values 100x larger than trained on. |
| CacheHitRate | float64 | `snap.CacheHitRate` | Prefix cache hit ratio from engine metrics | PrecisePrefixCache scorer | medium *(provisional)* | 0 | Sim uses router-side approximate cache index; production uses engine-reported precise cache metrics. Different data sources for same concept. **Provisional rating:** No empirical data supports the medium threshold (R² ≥ 0.80); the different data sources (approximate vs precise) could produce a larger gap than assumed. PR5 must validate empirically; if R² < 0.80, downgrade to low. **Known limitation:** The fidelity check regex matches only `low|medium|high` — it does not detect or handle the `*(provisional)*` annotation. Provisional medium is treated as medium for BC-6 enforcement. This is conservative: the signal passes the fidelity gate now, and PR5 can downgrade if empirical data warrants it. **If PR5 downgrades CacheHitRate to low:** (1) update this row's fidelity to `low`, (2) re-run extract — it will halt on the low-fidelity signal per BC-6, (3) any previously-transferred algorithms using CacheHitRate must be re-evaluated per the rollback procedure in the Notes section below. |

## Composite Signals

| Composite | Expansion | Production Equivalent | Fidelity | Notes |
|-----------|-----------|----------------------|----------|-------|
| EffectiveLoad() | QueueDepth + BatchSize + InFlightRequests | WaitingQueueSize + RunningQueueSize + RunningRequestCount | medium (composite) | No single production metric equivalent. PR3 scorer must compute inline. Semantic gaps in constituent signals (see individual rows above) propagate to composite. See Notes section for details. **Composite fidelity computation (F-10):** The composite rating is the minimum of constituent ratings: min(high, medium, medium) = medium. Rationale: a composite is only as reliable as its least reliable constituent. If any constituent is downgraded in PR5, the composite rating must also be recomputed. |

## Additional Signals (Non-RoutingSnapshot)

| Signal | Context | Production Mapping | Fidelity | Notes |
|--------|---------|-------------------|----------|-------|
| SessionID (boolean check) | `req.SessionID != ""` | Request header `x-session-id` | high | Boolean presence check — identical semantics. |

## Fidelity Rating Scale

- **high**: Same computation, same data source, negligible staleness. Decision test: `sim_value == prod_value` within floating-point tolerance. Quantitative: R² ≥ 0.99 or max |sim − prod| ≤ 1% of range.
- **medium**: Equivalent computation but different data source or non-trivial staleness. Strong correlation expected but not exact. Quantitative: R² ≥ 0.80 or rank-order correlation ≥ 0.90.
- **low**: Approximate or proxy signal with known semantic gap. Pipeline halts on low-fidelity signals. Quantitative: R² < 0.80 or qualitative gap documented.

> **Note:** Quantitative thresholds are provisional targets for PR5 (validation pipeline). PR1 ratings are based on design analysis; empirical validation deferred to Stage 5. **Design intent:** The extract command enforces fidelity ratings as hard gates (BC-6: low-fidelity signals halt the pipeline). This is conservative by design — it is safer to halt on an uncertain rating than to propagate a low-fidelity signal through the pipeline. Ratings may be revised upward in PR5 after empirical validation, which would unblock previously-halted signals.
>
> **Rollback procedure if PR5 downgrades a rating to low:** If empirical validation in PR5 reveals that a signal rated medium (e.g., CacheHitRate) actually has R² < 0.80, the rating must be downgraded to low in this mapping artifact. This will cause BC-6 to halt future extract runs. Any algorithms already transferred through the pipeline using the now-low-fidelity signal must be re-evaluated: (1) re-run extract — it will now halt on the low-fidelity signal, (2) determine whether the transferred scorer can tolerate the fidelity gap or needs re-generation with the signal excluded, (3) if re-generation is needed, re-run Stages 2-4 with the updated mapping. This is expected to be rare since provisional ratings are conservative. **F-7 fix — Rollback test note:** The rollback procedure is documented but not automated. The mechanism is indirectly verified by `TestFidelityHalt::test_low_fidelity_signal_halts_extract` (which confirms BC-6 halts on low-fidelity signals). PR5 SHOULD include a dedicated integration test that: (1) sets a provisional signal to low in a test copy of the mapping, (2) runs extract, (3) verifies it halts, (4) re-runs with the signal excluded, (5) verifies it succeeds. This would exercise the full rollback workflow end-to-end.

## Scorer Interface Reference

> **R3-F-7 WARNING — UNVERIFIED:** This section was documented from design knowledge, not verified against the actual `llm-d-inference-scheduler` source at the pinned commit. Both PR2 (scorer template) and PR3 (prompt templates + harness) depend on this section. If the `Score` method signature or factory pattern is incorrect, PR2's scorer template will be built on wrong assumptions. **PR2 gate:** Before generating the scorer template, PR2 MUST verify the interface below against the actual source. **PR3 gate:** PR3's convergence-review already requires interface verification. The UNVERIFIED note here serves as an additional reminder. See also Phase 0 § 1 (`llm-d-inference-scheduler` entry) for the original deferral rationale.

Target system: `llm-d-inference-scheduler` (gateway-api-inference-extension framework)

- **Interface:** `scheduling.Scorer` with `Score(ctx, cycleState, request, endpoints) map[Endpoint]float64`
- **Factory pattern:** `plugin.Register(typeName, factoryFunc)` in `pkg/plugins/register.go`
- **Existing scorers:** LoadAware, ActiveRequest, SessionAffinity, PrecisePrefixCache, NoHitLRU
- **Config:** YAML-based with scorer name, type, weight, and optional parameters. **Example config structure (PR3 must verify against actual schema):**
  ```yaml
  scorers:
    - name: EvolvedScorer
      type: evolved          # registered via plugin.Register("evolved", factory)
      weight: 1.0
      parameters:            # passed to factory function; schema TBD by PR3
        algorithm_source: "blis_v1"
  ```
  PR3 MUST verify the actual config schema from `llm-d-inference-scheduler`'s InferenceModel CRD or scorer configuration documentation. The parameter names and nesting above are illustrative only.
- **Thread-safety and lifecycle (PR3 must verify):** The `Score()` method is likely called concurrently from multiple goroutines (one per routing decision). PR3 MUST verify: (1) whether concurrent `Score()` calls are expected by inspecting the scheduler's request dispatch path, (2) whether scorers require initialization (`Init()`) or cleanup (`Close()`) methods beyond the factory constructor, (3) whether scorer instances are shared across requests or instantiated per-request. **Starting points:** inspect existing scorer implementations (e.g., LoadAware) for sync primitives (`sync.Mutex`, `atomic`) and lifecycle patterns.

> **Note for PR3:** This section provides a high-level reference for context only. PR3 MUST derive the full interface specification (method signatures, factory function signature, error handling contracts, thread-safety requirements, performance constraints) directly from the `llm-d-inference-scheduler` codebase at the pinned commit hash above. Do not rely solely on this summary — inspect `pkg/plugins/` and existing scorer implementations for the authoritative interface contract. **Starting points for PR3 interface discovery:** (1) `pkg/plugins/register.go` for the factory pattern, (2) `pkg/plugins/scorer/` for existing scorer implementations, (3) the `scheduling.Scorer` interface definition in the framework package.
>
> **Performance note (F-24):** The `Score()` method is called on every routing decision and is on the hot path. PR3 MUST consider latency and throughput when implementing the scorer. Specifically: (1) profile existing scorers (e.g., LoadAware) to establish baseline latency expectations, (2) avoid allocations in the `Score()` hot path where possible, (3) document any latency SLA derived from the scheduler's request-handling budget. PR1 does not define performance requirements because they depend on the production deployment context, but PR3 must not ignore them.

## Notes

- All v1 signals have `staleness_window_ms = 0` (approximate-scorer class). Suite B passes trivially. **Rationale:** This is a v1 simplification, not a claim about actual production staleness. In sim, RoutingSnapshot is instantaneous (zero staleness by design). In production, `GetMetrics()` latency is expected to be sub-millisecond (in-process call), so zero is a reasonable approximation. If production metrics are served via network calls with non-trivial latency, PR5 validation should measure actual staleness and update these values.
- **Temporal semantics assumption (IMPORTANT):** Sim RoutingSnapshot is a point-in-time snapshot taken at each routing decision. Production metrics are assumed to be point-in-time queries to endpoint `GetMetrics()`. **Risk:** If production uses rolling averages (e.g., exponential moving average over N seconds) or cumulative counters (e.g., total requests since startup), the evolved algorithm's scoring behavior will differ because it was optimized against instantaneous values. **Mitigation:** PR5 must verify the temporal semantics of each production metric endpoint and, if necessary, add a snapshot adapter that converts rolling/cumulative values to point-in-time equivalents. **Action for PR3/PR5:** This assumption MUST be promoted from an advisory note to a testable contract. PR3 should include a test that asserts the scorer receives point-in-time values (not rolling averages). PR5 should include a validation suite that measures the actual temporal characteristics of each production metric endpoint. **Verification criteria for PR5:** For each metric, call `GetMetrics()` twice within 1ms — values should reflect the instantaneous state, not a smoothed average. If values are identical across rapid calls despite state changes, the metric is likely a rolling average and requires a snapshot adapter.
- **EffectiveLoad() composite signal:** `EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests`. Production equivalent: sum the mapped production values (`WaitingQueueSize + RunningQueueSize + RunningRequestCount`). PR3 scorer must implement this composite computation inline — there is no single production metric equivalent. **Exact formula:** `effective_load = WaitingQueueSize + RunningQueueSize + RunningRequestCount` (integer arithmetic, no normalization needed since all three use the same unit: request count). **Missing value handling:** If any constituent metric is unavailable, the composite cannot be computed — return score 0.0 for that endpoint per the missing/default value assumption above. Do NOT substitute 0 for the missing metric, as this would undercount the load. **Caveat:** The semantic gaps in constituent signals (BatchSize≈RunningQueueSize, InFlightRequests≈RunningRequestCount) mean the composite sum is an approximation. Empirical validation in PR5 should compare the composite against the sim's EffectiveLoad() to assess error magnitude.
- CacheHitRate mapping to PrecisePrefixCache scorer may require adaptation since production uses ZMQ-based precise metrics while sim uses approximate router-side index.
- **Missing/default value assumption:** All production metrics are assumed to be always available from `endpoint.GetMetrics()`. If a metric field is missing, null, or zero-valued due to transient endpoint unavailability, the PR3 scorer should treat it as a routing error: return a score of 0.0 for that endpoint in the current scoring cycle (effectively skipping it), which causes the scheduler to prefer endpoints with available metrics. This is preferred over substituting default values because the evolved algorithm was not trained against missing-data scenarios. The evolved algorithm was not trained against missing-data scenarios, so substituting defaults could produce undefined scoring behavior. **Action for PR3:** This assumption MUST be promoted to a testable contract. PR3 should include a test that verifies the scorer returns an error (or skips the endpoint) when any mapped metric is unavailable, rather than substituting a default. **F-24 fix — Error handling convention verification (PR3 REQUIRED):** The "return score 0.0" recommendation above is a design assumption from PR1 — it has NOT been verified against llm-d-inference-scheduler's actual error handling conventions. PR3 MUST verify this convention before implementing it by: (1) inspecting existing scorer implementations (e.g., LoadAware in `pkg/plugins/scorer/`) for how they handle missing metrics or endpoint unavailability, (2) checking if the `scheduling.Scorer` interface documents expected error handling behavior, (3) verifying whether returning 0.0 causes the scheduler to deprioritize (not exclude) the endpoint, or if a different mechanism (e.g., returning an error, excluding from the result map) is the idiomatic approach. The pinned commit hash above provides the stable reference for this verification. If the convention differs from "return 0.0", update this mapping artifact's Notes section accordingly.
```

**Step 2: Fill in the actual commit hash (BLOCKER — must complete before Step 5)**

**F-8 fix: Prerequisite — ensure llm-d-inference-scheduler submodule is checked out.**
The CI workflow (Task 7) only checks out `inference-sim`, not `llm-d-inference-scheduler`. This step requires the submodule to be available locally. If not already checked out, run:
```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && git submodule update --init llm-d-inference-scheduler
```
Then obtain the commit hash:

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real/llm-d-inference-scheduler && git rev-parse HEAD`

Update the `Pinned commit hash` field in the mapping artifact. Replace `PLACEHOLDER_REQUIRES_STEP_2` with the hex hash output. **Verify the replacement**: `grep 'Pinned commit hash' docs/transfer/blis_to_llmd_mapping.md` — the output must show a 7-40 character hex string, NOT the placeholder text.

**F-21: When to update the pinned commit hash.** The hash MUST be updated whenever the llm-d-inference-scheduler submodule is updated (via `git submodule update`). Procedure: (1) update the submodule, (2) run `cd llm-d-inference-scheduler && git rev-parse HEAD`, (3) update the `Pinned commit hash` field in `blis_to_llmd_mapping.md`, (4) re-run `TestSourceSyncVerification` tests to verify hardcoded dicts still match, (5) bump the mapping artifact version (MAJOR bump if the interface changed, MINOR if only the hash changed). Between PR1 and PR3, if the submodule is updated, the mapping artifact MUST be re-committed with the new hash before PR3 uses it. **F-12 note on automated detection (F-21 fix — RECOMMENDED CI step):** There is no automated mechanism to detect hash staleness when the submodule is updated without updating the hash. `TestSourceSyncVerification` tests catch interface drift (struct fields, method bodies) but not hash staleness. **Recommended CI step:** Add a hash consistency check to the CI workflow alongside the existing submodule checkout:
```yaml
- name: Verify pinned commit hash matches submodule
  run: |
    cd llm-d-inference-scheduler
    ACTUAL=$(git rev-parse HEAD)
    PINNED=$(grep -oP 'Pinned commit hash:\s*\K[0-9a-f]+' ../docs/transfer/blis_to_llmd_mapping.md)
    if [ "$ACTUAL" != "$PINNED" ]; then
      echo "::warning::Submodule hash ($ACTUAL) differs from mapping artifact ($PINNED)"
      echo "Update blis_to_llmd_mapping.md per the F-21 update procedure above."
    fi
```
This is a WARNING (not a failure) because the submodule may be intentionally ahead during development. Promote to a hard failure if hash drift causes downstream issues. Submodule updates are infrequent and deliberate, so manual update is acceptable for v1, but the CI step provides an automated safety net.

**Step 3: MANDATORY — Run extract then validate-mapping to verify signal list consistency**

This step is NOT optional. The mapping table in Step 1 is a best-effort draft. The extract output is authoritative. You MUST run this verification and fix any mismatches before proceeding to Step 4.

**F-11 note on enforcement:** This step is procedural (developer must run it manually), not automated at this point in the workflow. However, the CI gate `TestValidateMapping::test_validate_mapping_passes_with_complete_mapping` catches mismatches post-commit before merge, so skipping Step 3 locally does not allow an incorrect mapping to reach the main branch.

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python tools/transfer_cli.py extract routing/ && python tools/transfer_cli.py validate-mapping`
Expected: Exit 0, `mapping_complete: true`

**Known risk: circular dependency (F-6).** The mapping artifact (Step 1) is written with a hardcoded signal list, then extract + validate-mapping (Step 3) verifies consistency. If they differ, the mapping must be rewritten. This is a one-time implementation friction, not a runtime issue — once the mapping artifact matches the extract output, subsequent runs are idempotent. **Commit procedure if iteration is needed:** If Step 3 reveals mismatches: (1) update the mapping table in Step 1, (2) re-run Step 3 to verify, (3) repeat until validate-mapping passes, (4) only then proceed to Step 5 (commit). Do NOT commit the mapping artifact until validate-mapping passes. If you already committed the mapping and later discover mismatches, amend the commit: `git add docs/transfer/blis_to_llmd_mapping.md && git commit --amend --no-edit`.

**Convergence limit (F-3 fix):** The iteration MUST converge within **3 rounds** (initial draft + 2 correction rounds). If validate-mapping still reports mismatches after 3 rounds, STOP and investigate using the following concrete escalation procedure: **(1) Diagnose:** Run `python tools/transfer_cli.py extract routing/ 2>&1` and `python tools/transfer_cli.py validate-mapping 2>&1`, capture both stdout JSON and stderr warnings. **(2) Classify the root cause** as one of: (a) **regex instability** — extract produces different signal lists on consecutive runs (fix: debug `_extract_signals` regex), (b) **mapping table parse error** — validate-mapping cannot match signal names due to Markdown formatting issues (fix: verify pipe-delimited table format), (c) **genuine signal ambiguity** — a signal exists in the EVOLVE-BLOCK but the regex alternates between detecting and missing it (fix: add the pattern to `_extract_signals` and update `EXPECTED_SIGNALS`). **(3) If the root cause is not identifiable after 30 minutes of investigation:** File a blocking issue on the macro plan with the extract stdout JSON, validate-mapping stdout JSON, and stderr output from both commands. Tag the issue as `transfer-pipeline-convergence-failure`. Do NOT proceed to Task 6 until the issue is resolved. **Failure mode for unmappable signals:** If extract discovers a signal that has NO production equivalent (i.e., the signal cannot be meaningfully mapped to any llm-d-inference-scheduler metric), the implementer MUST: (1) add the signal to the mapping table with fidelity rating `low` and a Rationale explaining why no mapping exists, (2) re-run extract — BC-6 will halt on the low-fidelity signal, confirming the pipeline correctly rejects unmappable signals, (3) document the unmappable signal in the Deviation Log (Section D) with a note that the EVOLVE-BLOCK uses a signal that cannot be transferred, (4) escalate to the macro plan: this may indicate the evolved algorithm is not transferable as-is and Stage 1 needs a different EVOLVE-BLOCK or algorithm variant. **This is a terminal condition for PR1** — do not proceed to PR3 if a required signal is unmappable. **F-14 fix — Test coverage note (F-22 fix):** While the unmappable signal procedure is documented, no dedicated test case exercises this scenario end-to-end (adding a low-fidelity signal and verifying extract halts). The existing `TestFidelityHalt::test_low_fidelity_signal_halts_extract` indirectly covers the mechanism (low-fidelity → exit 1), which validates the core behavior. A dedicated test with a synthetic unmappable signal would strengthen confidence but is deferred as the mechanism is verified. **Suggested test (optional, for future hardening):** `test_unmappable_signal_halts_extract` — (1) create a synthetic mapping with a new signal rated `low` with rationale "no production equivalent", (2) create a synthetic EVOLVE-BLOCK that accesses this signal, (3) run extract and verify exit 1 with a low-fidelity error. This would exercise the full unmappable signal → low fidelity → BC-6 halt pathway end-to-end.

**CI gate (F-2, F-3 — REQUIRED):** To prevent a mismatched mapping or un-replaced placeholder commit hash from being committed, validate-mapping MUST be run as part of the test suite. The test `TestValidateMapping::test_validate_mapping_rejects_placeholder_hash` provides an automated gate: it verifies that validate-mapping rejects the `PLACEHOLDER_REQUIRES_STEP_2` sentinel (which is not a valid hex hash). Additionally, `TestValidateMapping::test_validate_mapping_passes_with_complete_mapping` runs validate-mapping as part of every test run, catching signal mismatches. For CI, also add: `python tools/transfer_cli.py extract --strict routing/ && python tools/transfer_cli.py validate-mapping` as a CI step. The validate-mapping command will fail if: (1) any extracted signal is missing from the mapping table, (2) the commit hash is not a valid hex string. **Enforcement:** The test suite provides the automated gate. A pre-commit hook is recommended but not required in PR1 — the test suite catches the issue during `pytest`.

**If validate-mapping reports missing signals or mismatches:** The extracted signal list differs from the hardcoded mapping table above. You MUST: (1) re-run extract and inspect `workspace/algorithm_summary.json` to see the actual signal list, (2) update the mapping table in Step 1 to add/remove rows matching the extracted signals, (3) re-run validate-mapping to confirm `mapping_complete: true`. Do NOT proceed to Step 4 until validate-mapping passes. The extract output is authoritative — the mapping artifact must be updated to match, not vice versa.

**Step 4: Run the full test suite**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/ -v`
Expected: All tests pass

**Step 5: Verify placeholder replaced, then commit**

**Pre-commit gate (F-1, F-5 fix):** Before committing, verify the placeholder was replaced. The CI workflow (`.github/workflows/test.yml`) runs `validate-mapping` which rejects the placeholder via `TestValidateMapping::test_validate_mapping_rejects_placeholder_hash`. Additionally, run this manual check:
```bash
# This MUST show a hex hash, NOT 'PLACEHOLDER_REQUIRES_STEP_2'
grep 'Pinned commit hash' docs/transfer/blis_to_llmd_mapping.md | grep -qE '[0-9a-f]{7,40}' || { echo "ERROR: Placeholder commit hash not replaced — complete Step 2 first"; exit 1; }
```
**F-5 automated enforcement:** The placeholder is caught by three automated gates: (1) CI pytest runs `test_validate_mapping_rejects_placeholder_hash`, (2) CI workflow runs `validate-mapping` which checks for hex hash, (3) the manual pre-commit grep above. **F-3 fix — Pre-commit hook recommendation:** For projects using Git hooks, add this to `.git/hooks/pre-commit` (or `.pre-commit-config.yaml`):
```bash
#!/bin/sh
# Reject commits with PLACEHOLDER_REQUIRES_STEP_2
if git diff --cached --name-only | grep -q 'blis_to_llmd_mapping.md'; then
    if git diff --cached -- docs/transfer/blis_to_llmd_mapping.md | grep -q 'PLACEHOLDER_REQUIRES_STEP_2'; then
        echo "ERROR: Placeholder commit hash not replaced in mapping artifact."
        exit 1
    fi
fi
# Run validate-mapping if mapping artifact is staged
if git diff --cached --name-only | grep -qE '(blis_to_llmd_mapping|transfer_cli)'; then
    python tools/transfer_cli.py extract routing/ >/dev/null 2>&1 && \
    python tools/transfer_cli.py validate-mapping >/dev/null 2>&1 || {
        echo "ERROR: validate-mapping failed. Fix signal mismatches before committing."
        exit 1
    }
fi
```
This is optional but recommended — the CI test suite provides the mandatory gate.

```bash
git add docs/transfer/blis_to_llmd_mapping.md
git commit -m "$(cat <<'EOF'
feat(transfer): signal mapping artifact (BC-3, BC-6)

- Map 5 RoutingSnapshot signals + SessionID to llm-d equivalents
- Include fidelity ratings (3 high, 2 medium, 0 low)
- Pin llm-d-inference-scheduler commit hash
- Document scorer interface reference

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 6: Validate-Schema Command Integration Test

**Contracts Implemented:** BC-4 (integration)

**Files:**
- Modify: `tools/test_transfer_cli.py`

**Step 1: Write integration test for validate-schema via CLI**

```python
# Append to tools/test_transfer_cli.py

class TestValidateSchema:
    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        run_cli("extract", str(ROUTING_DIR))

    def test_validate_schema_passes_on_valid_summary(self):
        """BC-4: validate-schema passes on extract output."""
        code, output = run_cli("validate-schema", str(WORKSPACE / "algorithm_summary.json"))
        assert code == 0, f"Expected pass: {output}"
        assert output["status"] == "ok"
        assert output["violations"] == []

    def test_validate_schema_fails_on_missing_file(self):
        """BC-10: missing artifact exits with code 2."""
        code, output = run_cli("validate-schema", str(WORKSPACE / "nonexistent.json"))
        assert code == 2

    def test_validate_schema_fails_on_invalid_artifact(self):
        """BC-4: invalid artifact reports violations."""
        bad_path = WORKSPACE / "algorithm_summary_bad.json"
        bad_path.write_text(json.dumps({"algorithm_name": 123}))  # wrong type, missing fields
        try:
            code, output = run_cli("validate-schema", str(bad_path))
            # Note: schema name derived from filename stem — won't match
            # Test with correct name
        finally:
            bad_path.unlink(missing_ok=True)

        # Create a bad summary with correct filename
        real = WORKSPACE / "algorithm_summary.json"
        backup = real.read_text()
        real.write_text(json.dumps({"algorithm_name": 123}))
        try:
            code, output = run_cli("validate-schema", str(real))
            assert code == 1
            assert len(output["violations"]) > 0
        finally:
            real.write_text(backup)


class TestCompositeSignalConsistency:
    """F-19 fix: Cross-validate METHOD_EXPANSIONS against the mapping artifact's
    Composite Signals table to ensure they remain consistent."""

    def test_method_expansions_match_mapping_composite_table(self):
        """F-19: METHOD_EXPANSIONS dict must match mapping artifact composite table."""
        from tools.transfer_cli import METHOD_EXPANSIONS
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        if not mapping.exists():
            pytest.skip("Mapping artifact not yet created")
        content = mapping.read_text()
        import re
        for method, fields in METHOD_EXPANSIONS.items():
            # Verify the composite appears in the mapping
            assert method in content, (
                f"METHOD_EXPANSIONS has '{method}' but it's not in the mapping artifact"
            )
            # Verify each constituent field is mentioned in the composite row
            expansion_str = " + ".join(fields)
            assert expansion_str in content or all(f in content for f in fields), (
                f"METHOD_EXPANSIONS['{method}'] = {fields} but mapping artifact "
                f"composite table does not list the same expansion"
            )


class TestRoundTrip:
    """F-17: Explicit round-trip test: extract → validate-schema on the output."""

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_extract_then_validate_schema_round_trip(self):
        """Extract produces an artifact that passes schema validation."""
        extract_code, extract_output = run_cli("extract", str(ROUTING_DIR))
        assert extract_code == 0, f"Extract failed: {extract_output}"
        validate_code, validate_output = run_cli(
            "validate-schema", str(WORKSPACE / "algorithm_summary.json")
        )
        assert validate_code == 0, (
            f"validate-schema failed on extract output: {validate_output}. "
            f"This means extract produces artifacts that don't match the schema."
        )
        assert validate_output["violations"] == []


class TestHashDriftDetection:
    """BC-11 / F-3 fix: Verify content hash mechanism detects EVOLVE-BLOCK modifications.

    This tests the PR1 side of BC-11: that the hash changes when the source changes.
    PR3 MUST implement the consumer side: abort parsing when hash mismatches.
    """

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_hash_detects_source_modification(self):
        """BC-11: Modified EVOLVE-BLOCK produces different content hash."""
        import tempfile, shutil
        # Run extract on original source
        code1, _ = run_cli("extract", str(ROUTING_DIR))
        assert code1 == 0
        summary1 = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        hash1 = summary1["evolve_block_content_hash"]

        # Create modified copy with altered EVOLVE-BLOCK
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program.py"), str(tmpdir / "best_program.py"))
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            src = (tmpdir / "best_program.py").read_text()
            # Inject a comment into the EVOLVE-BLOCK to change the hash
            src = src.replace(
                "# EVOLVE-BLOCK-START",
                "# EVOLVE-BLOCK-START\n    # BC-11 drift detection test modification",
            )
            (tmpdir / "best_program.py").write_text(src)
            code2, _ = run_cli("extract", str(tmpdir))
            assert code2 == 0
            summary2 = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
            hash2 = summary2["evolve_block_content_hash"]

        assert hash1 != hash2, (
            "Content hash should differ after EVOLVE-BLOCK modification. "
            "Hash mechanism is broken — PR3 drift detection will not work."
        )


class TestUnknownSignalDetection:
    """F-23 fix: Verify that unrecognized field accesses produce 'unknown' type
    signals with a stderr WARNING, rather than being silently dropped."""

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_unknown_field_access_produces_unknown_type(self):
        """F-23: Unrecognized snap.Field produces signal with type 'unknown'."""
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            shutil.copy2(str(ROUTING_DIR / "best_program.py"), str(tmpdir / "best_program.py"))
            shutil.copy2(str(ROUTING_DIR / "best_program_info.json"), str(tmpdir / "best_program_info.json"))
            # Inject an unrecognized field access into the EVOLVE-BLOCK
            src = (tmpdir / "best_program.py").read_text()
            src = src.replace(
                "# EVOLVE-BLOCK-START",
                "# EVOLVE-BLOCK-START\n    unknown_val = snap.NovelMetricXYZ",
            )
            (tmpdir / "best_program.py").write_text(src)
            result = subprocess.run(
                [sys.executable, str(CLI), "extract", str(tmpdir)],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
            )
            assert result.returncode == 0, f"Extract should succeed: {result.stderr}"
            summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
            unknown_signals = [s for s in summary["signals"] if s["type"] == "unknown"]
            assert len(unknown_signals) > 0, (
                "Expected at least one 'unknown' type signal for unrecognized field access. "
                f"Got signals: {[s['name'] for s in summary['signals']]}"
            )
            assert any(s["name"] == "NovelMetricXYZ" for s in unknown_signals)
            # stderr should contain a WARNING about the unrecognized field
            assert "NovelMetricXYZ" in result.stderr, (
                f"Expected stderr WARNING about NovelMetricXYZ. Got: {result.stderr}"
            )


class TestExtractDeterminism:
    """F-22: Verify extract produces identical output for identical input."""

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_extract_is_deterministic(self):
        """F-22: Running extract twice on same input produces identical output."""
        code1, _ = run_cli("extract", str(ROUTING_DIR))
        assert code1 == 0
        output1 = (WORKSPACE / "algorithm_summary.json").read_text()

        code2, _ = run_cli("extract", str(ROUTING_DIR))
        assert code2 == 0
        output2 = (WORKSPACE / "algorithm_summary.json").read_text()

        assert output1 == output2, (
            "Extract is non-deterministic: two runs on the same input produced "
            "different outputs. This would break PR3 content hash verification."
        )


class TestFidelityHalt:
    """BC-6: Low-fidelity signal halts pipeline."""

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_low_fidelity_signal_halts_extract(self):
        """BC-6: extract exits 1 when mapping has a low-fidelity signal."""
        import shutil
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        backup = mapping.with_suffix(".md.bak")
        if mapping.exists():
            shutil.copy2(str(mapping), str(backup))
        try:
            # R3-F-4 fix: First run a successful extract to create the artifact,
            # establishing a pre-condition for the artifact-absence assertion below.
            # Without this, setup_method deletes the artifact and the assertion
            # 'assert not summary_path.exists()' passes vacuously — it would pass
            # even if the fidelity-halt code path were broken.
            code_setup, _ = run_cli("extract", str(ROUTING_DIR))
            summary_path = WORKSPACE / "algorithm_summary.json"
            assert code_setup == 0 and summary_path.exists(), (
                "Pre-condition: successful extract must create artifact before "
                "testing that fidelity failure prevents artifact creation"
            )
            # Now create a synthetic mapping with QueueDepth rated 'low'
            # R2-F-3 fix: The previous approach had two bugs:
            # (1) The first .replace() was a no-op (identical input/output strings).
            # (2) The second .replace() with count=1 targeted the first occurrence
            #     of '| high | 0 | Same computation' globally, which may not be
            #     the QueueDepth row. Multiple signals have 'high' fidelity.
            # Fixed by using a regex that targets the QueueDepth row specifically
            # and replaces its fidelity rating. Also added assertion that the
            # replacement actually occurred to prevent silent no-ops.
            content = mapping.read_text()
            import re
            new_content = re.sub(
                r'(\|\s*QueueDepth\s*\|[^|]*\|[^|]*\|[^|]*\|[^|]*\|)\s*high\s*(\|)',
                r'\1 low \2',
                content,
                count=1,
            )
            assert new_content != content, (
                "Failed to replace QueueDepth fidelity rating — "
                "mapping artifact format may have changed"
            )
            mapping.write_text(new_content)
            # Remove the artifact created by the successful extract above,
            # so we can verify the fidelity-failing extract does NOT recreate it.
            summary_path.unlink()
            code, output = run_cli("extract", str(ROUTING_DIR))
            assert code == 1, f"Expected exit 1 for low-fidelity, got {code}: {output}"
            assert output["status"] == "error"
            assert any("low fidelity" in e.lower() for e in output["errors"])
            # R2-F-22 fix: Verify artifact is NOT written after fidelity failure.
            # Fidelity failure returns exit 1 BEFORE writing the artifact (unlike
            # scope failure which writes the artifact with scope_validation_passed=false).
            # This asymmetry is documented in the F-13 fix comment in cmd_extract.
            # R3-F-4 fix: This assertion is now meaningful because we confirmed the
            # artifact existed (from the successful extract), deleted it, then ran the
            # fidelity-failing extract. If the artifact reappears, it means the fidelity
            # halt did not prevent the write.
            assert not summary_path.exists(), (
                "Artifact should NOT be written after fidelity failure — "
                "fidelity halt occurs before artifact write (see F-13 comment)"
            )
        finally:
            if backup.exists():
                shutil.move(str(backup), str(mapping))

    def test_medium_fidelity_signal_does_not_halt(self):
        """BC-6 negative: medium fidelity does not halt."""
        code, output = run_cli("extract", str(ROUTING_DIR))
        # Mapping has medium-fidelity signals (BatchSize, InFlightRequests) — should pass
        assert code == 0, f"Medium fidelity should not halt: {output}"

    def test_provisional_detection_matches_mapping_format(self):
        """R2-F-13: Verify *(provisional)* detection works against the actual mapping artifact.

        The _check_fidelity regex depends on exact string matching of '*(provisional)*'
        in the mapping table. This test verifies the regex matches the CacheHitRate row
        in the actual mapping artifact (which is annotated as provisional).
        """
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        if not mapping.exists():
            pytest.skip("Mapping artifact not present (pre-Task 5)")
        content = mapping.read_text()
        # Verify the provisional annotation exists in the mapping for CacheHitRate
        assert "*(provisional)*" in content, (
            "Expected *(provisional)* annotation in mapping artifact for CacheHitRate"
        )
        # Run extract and verify the provisional flag is detected
        code, output = run_cli("extract", str(ROUTING_DIR))
        assert code == 0, f"Extract failed: {output}"
        summary = json.loads((WORKSPACE / "algorithm_summary.json").read_text())
        cache_hit = [s for s in summary["signals"] if s["name"] == "CacheHitRate"]
        assert len(cache_hit) == 1, f"CacheHitRate not found in signals: {summary['signals']}"
        assert cache_hit[0].get("fidelity_provisional") is True, (
            "CacheHitRate should have fidelity_provisional=True — "
            "provisional detection regex may not match actual mapping format"
        )

    def test_fidelity_fallback_pattern_matches_additional_signals(self):
        """R5-F-11 fix: Verify the _check_fidelity fallback pattern (pattern_alt with {2}
        column skips) matches SessionID in the Additional Signals table.

        The fidelity check has two regex patterns:
        - Main table: {4} column skips (tested by CacheHitRate/QueueDepth tests)
        - Additional Signals table: {2} column skips (only SessionID uses this)

        Without this test, a column count change in the Additional Signals table
        would silently break SessionID's fidelity check with no test to catch it.
        """
        mapping = REPO_ROOT / "docs" / "transfer" / "blis_to_llmd_mapping.md"
        if not mapping.exists():
            pytest.skip("Mapping artifact not present (pre-Task 5)")
        content = mapping.read_text()
        import re
        # Directly test the fallback pattern against SessionID in the mapping
        pattern_alt = r'\|\s*SessionID(?:\s*\([^)]*\))?\s*\|(?:[^|]*\|){2}\s*(low|medium|high)\s*(?:\*\(provisional\)\*)?\s*\|'
        match = re.search(pattern_alt, content, re.IGNORECASE)
        assert match is not None, (
            "Fallback pattern (pattern_alt with {2} column skips) did not match "
            "SessionID in the Additional Signals table. The column count may have changed."
        )
        assert match.group(1).lower() == "high", (
            f"Expected SessionID fidelity 'high', got '{match.group(1)}'"
        )


class TestCIStrictEnforcement:
    """F-1 fix: Enforce --strict in CI to eliminate non-determinism boundary.

    The BC-6 non-determinism (extract produces different results depending on
    mapping artifact presence) is mitigated by --strict. In CI environments
    (CI env var set), extract without --strict FAILS with exit 1 — not just
    a warning, because CI systems do not fail on stderr output.
    """

    def setup_method(self):
        WORKSPACE.mkdir(exist_ok=True)
        summary = WORKSPACE / "algorithm_summary.json"
        if summary.exists():
            summary.unlink()

    def test_ci_env_requires_strict_flag(self):
        """F-1: In CI (CI env var set), extract without --strict FAILS with exit 1."""
        import os
        result = subprocess.run(
            [sys.executable, str(CLI), "extract", str(ROUTING_DIR)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env={**os.environ, "CI": "true"},
        )
        # Extract MUST fail in CI without --strict — a warning is insufficient
        # because CI systems do not fail on stderr output.
        assert result.returncode == 1, (
            f"In CI environment, extract without --strict must FAIL (exit 1), "
            f"got exit {result.returncode}. Stderr: {result.stderr}"
        )
        stdout = json.loads(result.stdout)
        assert stdout["status"] == "error"
        assert any("strict" in e.lower() for e in stdout.get("errors", [])), (
            "Error message should mention --strict. Got: " + str(stdout.get("errors"))
        )

    def test_ci_false_does_not_enforce_strict(self):
        """F-9: CI='false' should NOT trigger --strict enforcement."""
        import os
        env = {**os.environ, "CI": "false"}
        result = subprocess.run(
            [sys.executable, str(CLI), "extract", str(ROUTING_DIR)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env=env,
        )
        # F-25 fix: CI='false' should not enforce --strict — extract should succeed (exit 0).
        # Previous assertion used a weak OR that could mask unrelated failures.
        assert result.returncode == 0, (
            f"CI='false' should not enforce --strict. Expected exit 0, got {result.returncode}. "
            f"Stdout: {result.stdout[:200]}. Stderr: {result.stderr[:200]}"
        )

    def test_ci_env_with_strict_no_warning(self):
        """F-1: In CI with --strict, no warning about missing --strict."""
        import os
        result = subprocess.run(
            [sys.executable, str(CLI), "extract", "--strict", str(ROUTING_DIR)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env={**os.environ, "CI": "true"},
        )
        assert "strict" not in result.stderr.lower() or result.returncode == 0
```

**Step 2: Run tests**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/test_transfer_cli.py::TestValidateSchema tools/test_transfer_cli.py::TestFidelityHalt tools/test_transfer_cli.py::TestCIStrictEnforcement -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tools/test_transfer_cli.py
git commit -m "$(cat <<'EOF'
test(transfer): validate-schema + fidelity halt tests (BC-4, BC-6)

- Valid summary passes schema validation
- Missing file exits 2
- Invalid artifact reports violations
- Low-fidelity signal halts extract (BC-6)
- Medium-fidelity signal does not halt

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

#### Task 7: CLAUDE.md + CI Configuration + Final Documentation

**Contracts Implemented:** (none — documentation + CI enforcement for F-1, F-2)

**Files:**
- Create: `CLAUDE.md`
- Create: `.github/workflows/test.yml` (F-2 fix: ensures submodules checked out + --strict enforced)
- Modify: `docs/transfer/README.md` (update with final signal list)

**Step 1: Create CLAUDE.md**

```markdown
# CLAUDE.md — sim2real

## Project Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
inference-sim to production llm-d-inference-scheduler scorer plugins.

## Repository Structure

- `routing/` — Input artifacts from evolutionary optimization (EVOLVE-BLOCK, metrics, workloads)
- `docs/transfer/` — Mapping artifacts, scorer template, calibration log
- `docs/plans/` — Design docs and implementation plans
- `tools/` — Python CLI (`transfer_cli.py`) and Go test harness (PR3)
- `tools/schemas/` — JSON Schema files for workspace artifact validation
- `prompts/` — Pipeline stage prompt templates (PR3+)
- `workspace/` — Inter-stage JSON artifacts (gitignored, not committed)

## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `llm-d-inference-scheduler/` — Production scheduler with scorer plugin system (target)
- `llm-d-benchmark/` — Benchmark harness for cluster-level validation (target)

## Transfer Pipeline

6-stage prompt-driven pipeline:
1. **Extract** — Parse EVOLVE-BLOCK, produce algorithm_summary.json
2. **Translate** — Map sim signals to production equivalents
3. **Generate** — LLM produces scorer plugin code
4. **Test** — Build + test with retry logic
5. **Validate** — 3-suite equivalence + cluster benchmarks
6. **PR** — Create PRs in target repos

## CLI Commands

```bash
# Extract algorithm metadata
python tools/transfer_cli.py extract routing/

# Extract with strict fidelity checks (recommended for CI)
python tools/transfer_cli.py extract --strict routing/

# Validate mapping artifact completeness
python tools/transfer_cli.py validate-mapping

# Validate workspace artifact against JSON Schema
python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json
```

## Important: Artifact Consumption

The `extract` command produces **two** JSON outputs:
1. **File artifact** (`workspace/algorithm_summary.json`) — the pipeline contract, validated by schema. **All downstream stages MUST consume this file.**
2. **Stdout JSON** — operational metadata for human/CI feedback. **Do NOT consume stdout in downstream stages.**

## Development

- Python >= 3.9, stdlib only (no external dependencies)
- Tests: `python -m pytest tools/ -v`
- Lint: `ruff check tools/` (if installed)

## Pipeline Status

| PR | Description | Status |
|----|-------------|--------|
| PR1 | Mapping artifact + scaffolding + CLI extract | In progress |
| PR2 | Scorer template artifact | Not started |
| PR3 | Prompt templates (Stages 1-3) + Go harness | Not started |
| PR4 | Stage 4 prompt + test retry logic | Not started |
| PR5 | Validation pipeline (Stage 5) | Not started |
| PR6 | Stage 6 + self-verification + calibration | Not started |

## Cross-PR Notes

R5-F-6 fix: When implementing PR3, read `docs/transfer/README.md` § Cross-PR Contracts first.
```

**Step 2: Create CI workflow (F-2 fix)**

Context: Without a CI configuration that checks out submodules, `TestSourceSyncVerification` tests are silently skipped in CI, meaning drift between hardcoded dicts and inference-sim source goes undetected. This also ensures `--strict` is used (F-1 enforcement).

`.github/workflows/test.yml`:
```yaml
name: Transfer Pipeline Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    env:
      CI: "true"
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: false  # We only need inference-sim, not all submodules

      # F-2 fix: Ensure inference-sim submodule is checked out so
      # TestSourceSyncVerification tests run instead of being silently skipped.
      - name: Checkout inference-sim submodule
        run: git submodule update --init inference-sim

      # F-8 fix: Check out llm-d-inference-scheduler for commit hash verification.
      # Required by the recommended hash consistency check below and by Task 5 Step 2.
      - name: Checkout llm-d-inference-scheduler submodule
        run: git submodule update --init llm-d-inference-scheduler

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run tests
        run: python -m pytest tools/ -v

      # F-1 fix: Run extract with --strict in CI to enforce deterministic
      # fidelity checks. Without --strict, extract now fails in CI (hard gate).
      # R2-F-21 fix: Explicit if: success() for clarity. GitHub Actions default
      # behavior skips subsequent steps on failure, but explicit annotations
      # make the dependency chain clear to readers.
      - name: Verify extract --strict
        if: success()
        run: python tools/transfer_cli.py extract --strict routing/

      - name: Verify mapping
        if: success()
        run: python tools/transfer_cli.py validate-mapping

      # R3-F-17 fix: Verify the pinned commit hash in the mapping artifact matches
      # the actual llm-d-inference-scheduler submodule HEAD. Without this check, the
      # llm-d-inference-scheduler checkout above is dead weight. This ensures the
      # mapping artifact stays in sync with the submodule pointer.
      - name: Verify mapping commit hash consistency
        if: success()
        run: |
          # R4-F-1 fix: Use grep -oE instead of grep -oP with PCRE lookbehind.
          # The PCRE lookbehind (?<=\*\*Pinned commit hash:\*\*\s) is fragile
          # across grep implementations. Instead, match the full pattern and
          # extract the hash with a second grep.
          PINNED_HASH=$(grep -o 'Pinned commit hash:\*\*[[:space:]]*[0-9a-f]\{7,40\}' docs/transfer/blis_to_llmd_mapping.md | grep -oE '[0-9a-f]{7,40}$')
          # R4-F-1 fix: Guard against empty PINNED_HASH (grep failure, placeholder
          # present, or format mismatch). Without this guard, the comparison
          # ${ACTUAL_HASH:0:0} silently evaluates to empty string, making the
          # check vacuous ('' == '' passes) or misleading.
          if [ -z "$PINNED_HASH" ]; then
            echo "ERROR: Could not extract pinned commit hash from docs/transfer/blis_to_llmd_mapping.md"
            echo "Verify the mapping artifact contains '**Pinned commit hash:** <hex-hash>'"
            exit 1
          fi
          ACTUAL_HASH=$(cd llm-d-inference-scheduler && git rev-parse HEAD)
          # R5-F-1 fix: Normalize both hashes to the same length before comparison.
          # The previous prefix comparison `${ACTUAL_HASH:0:${#PINNED_HASH}}`
          # silently passes when PINNED_HASH is a 7-char abbreviated hash that
          # happens to be a prefix of a different commit's full hash. Instead,
          # if the pinned hash is abbreviated (<40 chars), resolve it to a full
          # hash via `git rev-parse` so the comparison is always 40-char vs 40-char.
          if [ ${#PINNED_HASH} -lt 40 ]; then
            PINNED_HASH=$(cd llm-d-inference-scheduler && git rev-parse "$PINNED_HASH" 2>/dev/null) || {
              echo "ERROR: Pinned hash from mapping artifact is not a valid commit in llm-d-inference-scheduler"
              exit 1
            }
          fi
          if [ "$ACTUAL_HASH" != "$PINNED_HASH" ]; then
            echo "ERROR: Mapping artifact pinned hash ($PINNED_HASH) does not match llm-d-inference-scheduler HEAD ($ACTUAL_HASH)"
            echo "Update the mapping artifact or the submodule pointer."
            exit 1
          fi
          echo "OK: Pinned hash matches submodule HEAD"
```

**Step 3: Run full test suite**

Run: `cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real && python -m pytest tools/ -v`
Expected: All tests pass

**Step 4: Commit**

```bash
git add CLAUDE.md .github/workflows/test.yml docs/transfer/README.md
git commit -m "$(cat <<'EOF'
docs(transfer): CLAUDE.md, CI workflow, and transfer pipeline documentation

- Create CLAUDE.md with project overview, structure, CLI commands
- Add .github/workflows/test.yml (F-2: submodule checkout + F-1: --strict enforcement)
- Update docs/transfer/README.md with R6 resolution details
- Document pipeline status table

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### H) Test Strategy

| Contract | Task | Test Type | Test Name / Description |
|----------|------|-----------|------------------------|
| BC-1 | Task 3 | Integration | `TestExtract::test_extract_produces_valid_summary` |
| BC-2 | Task 3 | Integration | `TestExtract::test_extract_identifies_signals` |
| BC-2 | Task 3 | Integration | `TestExtract::test_extract_signals_have_required_fields` |
| BC-3 | Task 4+5 | Integration | `TestValidateMapping::test_validate_mapping_passes_with_complete_mapping` |
| BC-4 | Task 2 | Unit | `TestValidateArtifact::test_valid_artifact_passes` + 4 others |
| BC-4 | Task 6 | Integration | `TestValidateSchema::test_validate_schema_passes_on_valid_summary` |
| BC-5 | Task 3 | Integration | `TestExtract::test_extract_scope_validation_passes_for_routing` |
| BC-5 | Task 3 | Integration | `TestExtract::test_extract_scope_validation_fails_for_out_of_scope` |
| BC-6 | Task 6 | Integration | `TestFidelityHalt::test_low_fidelity_signal_halts_extract` + `test_medium_fidelity_signal_does_not_halt` |
| R5-F-11 | Task 6 | Integration | `TestFidelityHalt::test_fidelity_fallback_pattern_matches_additional_signals` — verifies pattern_alt matches SessionID in Additional Signals table |
| BC-7 | Task 3 | Integration | `TestExtract::test_extract_output_is_json` |
| BC-8 | Task 3 | Integration | `TestExtract::test_extract_missing_directory_exits_2` |
| BC-9 | Task 4 | Integration | `TestValidateMapping::test_validate_mapping_reports_missing_artifact` |
| BC-10 | Task 2 | Unit | `TestValidateArtifact::test_scope_validation_bool_required` |
| F-2 | Task 3 | Integration | `TestGoldenSignalList::test_extracted_signals_match_golden_list` — golden-file test comparing extracted signals against manually-verified expected set |
| F-25 | Task 3 | Integration | `TestGoldenSignalList::test_composite_signals_match_golden_list` — golden-file test verifying composite_signals array content |
| F-1 | Task 3 | Integration | `TestSourceSyncVerification::test_method_expansion_matches_source` |
| F-3 | Task 3 | Integration | `TestSourceSyncVerification::test_routing_snapshot_fields_match_source` |
| BC-6 | Task 3 | Integration | `TestExtract::test_extract_without_mapping_graceful_degradation` |
| BC-6/F-1 | Task 3 | Integration | `TestExtract::test_extract_strict_fails_without_mapping` — CI determinism |
| BC-6/F-1 | Task 3 | Integration | `TestExtract::test_extract_strict_succeeds_with_mapping` |
| F-7 | Task 3 | Integration | `TestExtract::test_extract_stdout_differs_from_file_artifact` |
| F-17 | Task 6 | Integration | `TestRoundTrip::test_extract_then_validate_schema_round_trip` — explicit extract → validate-schema round-trip |
| F-13 | Task 2 | Unit | `TestValidateArtifact::test_empty_signals_array_fails_min_items` |
| F-13 | Task 2 | Unit | `TestValidateArtifact::test_unexpected_top_level_field_rejected` |
| F-15 | Task 3 | Integration | `TestExtract::test_extract_no_signals_exits_1` — exit code 1 for no signals |
| F-9 | Task 3 | Integration | `TestExtract::test_extract_few_signals_strict_exits_1` — 1 signal in --strict exits 1 |
| R3-F-15 | Task 3 | Integration | `TestExtract::test_extract_few_signals_boundary_2_fails` — exactly 2 signals in --strict exits 1 (boundary) |
| R3-F-15 | Task 3 | Integration | `TestExtract::test_extract_few_signals_boundary_3_passes_threshold` — exactly 3 signals passes threshold (boundary) |
| F-9 | Task 3 | Integration | `TestExtract::test_extract_missing_info_json_exits_2` — missing best_program_info.json exits 2 |
| F-18 | Task 3 | Integration | `TestExtract::test_extract_content_hash_matches_evolve_block` — SHA-256 round-trip |
| F-1 | Task 2 | Unit | `TestValidateArtifact::test_hash_with_trailing_chars_rejected` — re.fullmatch rejects trailing chars |
| F-9 | Task 2 | Unit | `TestValidateArtifact::test_absolute_path_evolve_block_source_rejected` — pattern rejects absolute paths |
| F-16 | Task 2 | Unit | `TestValidateArtifact::test_path_traversal_evolve_block_source_rejected` — pattern rejects `../` path traversal |
| F-28 | Task 2 | Unit | `TestValidateArtifact::test_dot_slash_evolve_block_source_rejected` — pattern rejects `./` current directory reference |
| F-12 | Task 2 | Unit | `TestValidateArtifact::test_invalid_line_range_rejected` — semantic check rejects start > end line ranges |
| F-10 | Task 2 | Unit | `TestValidateArtifact::test_excess_signals_array_fails_max_items` — maxItems: 20 enforcement |
| F-18 | Task 3 | Integration | `TestExtract::test_extract_mapping_version_parsed` — version parsing from mapping artifact |
| F-22 | Task 6 | Integration | `TestExtractDeterminism::test_extract_is_deterministic` — same input → same output |
| F-2 | Task 3 | Integration | `TestExtract::test_extract_non_determinism_boundary_documented` — fidelity check with/without mapping |
| F-8 | Task 3 | Integration | `TestSourceSyncVerification::test_ci_must_not_skip_sync_tests` — loud failure in CI if submodule missing |
| F-19 | Task 4 | Integration | `TestValidateMapping::test_validate_mapping_detects_duplicate_signals` — duplicate signal rows rejected (data quality, R3-F-14) |
| F-2 | Task 4 | Integration | `TestValidateMapping::test_validate_mapping_rejects_placeholder_hash` — automated gate for placeholder commit hash |
| F-3/BC-11 | Task 6 | Integration | `TestHashDriftDetection::test_hash_detects_source_modification` — content hash changes when EVOLVE-BLOCK is modified |
| F-1 | Task 6 | Integration | `TestCIStrictEnforcement::test_ci_env_requires_strict_flag` — CI without --strict FAILS (exit 1) |
| F-1 | Task 6 | Integration | `TestCIStrictEnforcement::test_ci_env_with_strict_no_warning` — CI with --strict has no warning |
| F-9 | Task 6 | Integration | `TestCIStrictEnforcement::test_ci_false_does_not_enforce_strict` — CI='false' does not trigger --strict enforcement |
| F-15 | Task 3 | Integration | `TestExtract::test_extract_empty_evolve_block_exits_1` — empty EVOLVE-BLOCK (markers present, no content) |
| F-15 | Task 3 | Integration | `TestExtract::test_extract_missing_metrics_key_warns` — missing metrics key in info JSON |
| F-26 | Task 3 | Integration | `TestExtract::test_extract_missing_metrics_key_strict_fails` — --strict mode fails with exit 1 when combined_score missing |
| F-27 | Task 3 | Integration | `TestExtract::test_extract_multiple_evolve_blocks_warns` — multiple EVOLVE-BLOCK pairs produce stderr warning |
| F-23 | Task 6 | Integration | `TestUnknownSignalDetection::test_unknown_field_access_produces_unknown_type` — unrecognized field produces unknown type + stderr WARNING |

**Test infrastructure:** All tests use pytest with subprocess invocation of the CLI. No shared test helpers needed beyond the `run_cli` fixture.

**Lint:** `ruff check tools/` if available; no strict lint gate for v1 (stdlib-only Python is low-risk).

### I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| EVOLVE-BLOCK signal regex misses a field access pattern | Medium | High | Conservative regex + golden-file test (`TestGoldenSignalList`) comparing extracted signals against manually-verified expected set + manual review of extracted signals against EVOLVE-BLOCK | Task 3, Review Guide |
| Mapping fidelity ratings are subjective | Low | Medium | Document rationale per signal; ratings are conservative (medium when uncertain). BatchSize and InFlightRequests have known semantic gaps (different counting points) documented in mapping — empirical validation deferred to PR5 | Task 5 |
| Schema validator too simplistic for future artifacts | Low | Low | Validator handles all v1 needs; can be extended in PR3/PR5 if needed | Task 2 |
| llm-d submodule commit hash stale by PR3 time | Low | Low | Stage 2 staleness check detects drift at runtime | Task 5 |
| R6 shim approach (deferred to PR3) proves infeasible | Low | High | Documented fallback: submit API PR to inference-sim (option b). **F-9 mitigation:** PR1 implementer should do a brief feasibility scan during Task 1 Step 2: verify the EVOLVE-BLOCK contains only Go constructs translatable to pure Go (no Python-only constructs, no dynamic dispatch). If untranslatable constructs are found, document in README.md and flag for PR3 plan. **F-15 pre-flight check:** During Task 3 Step 0 (pre-flight verification), visually scan the EVOLVE-BLOCK for Python-specific constructs that cannot be translated to Go (e.g., list comprehensions, lambda expressions, dynamic attribute access via getattr). The current EVOLVE-BLOCK contains Go source embedded in a Python string, so Go-translatability is expected. If Python-only constructs are found, document them as a PR3 blocker in the Deviation Log. | Task 1 |

### Convergence Review Finding Disposition (Round 10)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-1 | CRITICAL | **Fixed** | CI env detection changed from stderr warning to hard failure (exit 1). Test updated to assert failure. |
| F-2 | CRITICAL | **Fixed** | `.github/workflows/test.yml` added with `git submodule update --init inference-sim` and `extract --strict`. |
| F-3 | IMPORTANT | Accepted | Golden-file test IS independent verification (manual vs regex). Plan documents the limitation and warns against auto-deriving. |
| F-4 | IMPORTANT | Accepted | Schema's `additionalProperties:false` + test provides structural enforcement. PR3 defense-in-depth documented. |
| F-5 | IMPORTANT | **Mitigated** | CI workflow now runs `validate-mapping` (catches placeholder). Three automated gates documented. |
| F-6 | IMPORTANT | Accepted | Semantic gaps documented with quantitative PR5 thresholds. Rollback procedure documented in Notes section. |
| F-7 | IMPORTANT | **Fixed** | Provisional ratings now tagged with `fidelity_provisional: true` on the signal dict for machine-readable differentiation. |
| F-8 | IMPORTANT | Accepted | Cross-PR contract inherently unenforceable at runtime. PR3 convergence-review gate documented. |
| F-9 | IMPORTANT | Accepted | `normalization_note` field is machine-readable. PR3 convergence-review gate documented. |
| F-10 | IMPORTANT | Accepted | Go interface specification deferred to PR3 appropriately. Pinned commit hash provides stable reference. |
| F-11 | IMPORTANT | Accepted | Plan acknowledges circular dependency and provides iteration procedure. Golden-file test is safety net. |
| F-12 | IMPORTANT | **Mitigated** | Added automated detection command to F-21 procedure. Low-priority given infrequent submodule updates. |
| F-13 | IMPORTANT | Accepted | Blacklist documented as known limitation (F-16). Whitelist deferred as premature for v1. |
| F-14 | IMPORTANT | Accepted | Versioning policy provided. Enforcement appropriately deferred to consumer (PR3+). |
| F-15 | IMPORTANT | Accepted | Temporal semantics documented as advisory note with PR3/PR5 promotion requirement. |
| F-16 | IMPORTANT | Accepted | Missing-value handling documented with PR3 promotion requirement. |
| F-17 | IMPORTANT | **Fixed** | Semantic check now uses structural detection (`re.fullmatch` on value) instead of pattern-string equality. |
| F-18 | IMPORTANT | Accepted | Git `core.autocrlf` mitigates. Contingency documented. Low-likelihood for single-platform v1. |
| F-19 | IMPORTANT | **Fixed** | Added `TestCompositeSignalConsistency::test_method_expansions_match_mapping_composite_table`. |
| F-20 | IMPORTANT | Accepted | `maxItems: 20` documented as defense-in-depth guard. Current count is 6; 20 is generous. Easy to update. |
| F-21 | IMPORTANT | Accepted | Exit code boundary documented with clear distinguishing criteria. Inherently subjective for edge cases. |
| F-22 | IMPORTANT | Accepted | Format coupling documented (F-13 note). Parser and artifact co-created. Test catches regressions. |
| F-23 | IMPORTANT | **Fixed** | Added `TestUnknownSignalDetection::test_unknown_field_access_produces_unknown_type`. |
| F-24 | IMPORTANT | **Fixed** | Added `"enum": ["sum"]` constraint on `formula` field in schema. |

### Convergence Review Finding Disposition (Round 11 — Fixer)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-1 | CRITICAL | **Fixed** | Added LOCAL-VS-CI DIVERGENCE RISK section to BC-6 documenting state-dependency, recommending --strict for local dev, and clarifying residual risk is a bootstrap compromise. |
| F-2 | CRITICAL | **Fixed** | Added FALSE POSITIVE MITIGATION and FALSE NEGATIVE RISK documentation to `_extract_signals` docstring, explaining ROUTING_SNAPSHOT_FIELDS filter mitigates `[a-z]\.` broadness, and listing known blind spots (intermediate variables, method receivers, struct embedding). |
| F-3 | CRITICAL | **Fixed** | Added convergence limit (3 rounds max), failure mode for unmappable signals (rate as low → BC-6 halts → escalate to macro plan), and terminal condition documentation to Task 5 iteration procedure. |
| F-4 | IMPORTANT | **Fixed** | Added enforcement note to mapping_artifact_version schema description requiring PR3 test `test_stale_mapping_version_detected` and convergence-review gate. |
| F-5 | IMPORTANT | Accepted | Three automated gates (CI pytest, CI validate-mapping, manual grep) already catch placeholder before merge. --no-verify bypass is inherent to Git, not a PR1 defect. |
| F-6 | IMPORTANT | Accepted | Cross-PR enforcement is inherently asymmetric — PR1 provides mechanisms, PR3 convergence-review enforces. This is the correct architectural pattern. |
| F-7 | IMPORTANT | Accepted | Semantic gaps well-documented with quantitative PR5 thresholds and rollback procedure. Fidelity ratings are conservative (medium, not high). |
| F-8 | IMPORTANT | Accepted | CI gate (test_ci_must_not_skip_sync_tests) prevents silent skipping. Local skipping acceptable since dicts pinned to specific commit. |
| F-9 | IMPORTANT | Accepted | Blacklist documented as known limitation with guidance for extension. Whitelist premature for v1. |
| F-10 | IMPORTANT | Accepted | normalization_note field is machine-readable with clear documentation. PR3 convergence-review gate already specified. |
| F-11 | IMPORTANT | Accepted | Temporal semantics documented in mapping artifact Notes with PR3/PR5 promotion requirements. Correctly deferred. |
| F-12 | IMPORTANT | Accepted | Format coupling documented, test_extract_mapping_version_parsed catches regressions. Mapping artifact and parser co-created in PR1. |
| F-13 | IMPORTANT | **Fixed** | Added IMPLICIT CONTRACT note to evolve_block_source schema description documenting the semantic start<=end check for external validator implementers. |
| F-14 | IMPORTANT | **Fixed** | Added `fidelity_provisional` boolean property to signal object in schema, so the field added by _check_fidelity is now schema-valid. |
| F-15 | IMPORTANT | **Fixed** | Added F-15 pre-flight check to Risk Analysis table: visual scan of EVOLVE-BLOCK for Python-specific constructs during Task 3 Step 0. |
| F-16 | IMPORTANT | Accepted | Two-output design has multiple defense layers (different structures, additionalProperties: false, test verification, CLAUDE.md documentation). Defense-in-depth is appropriate. |
| F-17 | IMPORTANT | Accepted | Deviation Log documents scope mismatch with rationale. Macro plan delegates authority to PR1 extract. |
| F-18 | IMPORTANT | Accepted | Performance requirements are PR3 concerns. Scorer Interface Reference section provides starting points. Correctly deferred. |
| F-19 | IMPORTANT | **Fixed** | test_extract_non_determinism_boundary_documented now verifies asymmetry by running --strict without mapping and asserting failure, proving different exit codes for same EVOLVE-BLOCK. |
| F-20 | IMPORTANT | **Fixed** | Added F-20 note to State Changes documenting intentional overwrite behavior and that workspace/ is gitignored pipeline directory. |
| F-21 | IMPORTANT | **Fixed** | Added F-21 formula verification note documenting manual inspection of `return r.QueueDepth + r.BatchSize + r.InFlightRequests` and guidance for updating if operation changes. |
| F-22 | IMPORTANT | Accepted | Exit code boundary criteria are clear for common cases. Edge case subjectivity is inherent and documented. |
| F-23 | IMPORTANT | Accepted | Missing/default value assumption documented with PR3 promotion requirement. Correctly scoped as PR3 responsibility. |
| F-24 | IMPORTANT | Accepted | CI gate (test_ci_must_not_skip_sync_tests) provides adequate coverage. Local skipping is acceptable for pinned-commit dicts. |
| F-25 | IMPORTANT | Accepted | Fidelity check regex and mapping artifact are co-created in PR1. Test suite catches regressions. Format coupling is documented. |
| F-26 | IMPORTANT | Accepted | Deployment strategy is PR3/PR6 scope, not PR1. Macro plan addresses deployment. |
| F-27 | IMPORTANT | Accepted | algorithm_name hardcoded with explicit comment providing multi-algorithm extension guidance. v1 single-algorithm limitation is documented. |
| F-28 | IMPORTANT | Accepted | Schema validation catches missing combined_score downstream. Extract's graceful degradation with warning is appropriate for exploratory use. Test verifies the warning. **Round 2 update (F-12):** --strict mode now fails if combined_score is missing, preventing schema-invalid artifacts in CI. |
| F-29 | IMPORTANT | **Fixed** | _extract_evolve_block now counts EVOLVE-BLOCK-START markers and emits stderr WARNING if multiple blocks found, documenting that only the first is extracted. |

### Convergence Review Finding Disposition (Round 12 — Fixer Round 2)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-2 | IMPORTANT | Accepted | Cross-PR enforcement is inherently asymmetric. PR1 provides hash mechanism and tests; PR3 convergence-review gate enforces consumer-side test. This is the correct architectural pattern for cross-PR contracts. |
| F-3 | IMPORTANT | **Fixed** | validate-mapping now checks bidirectionally: (a) missing signals (extract→mapping) and (b) extra signals (mapping→extract). BC-3 updated, test added (`test_validate_mapping_detects_extra_signals`), stdout contract updated with `extra_signals[]`. |
| F-4 | IMPORTANT | Accepted | Deviation Log documents scope mismatch (5 vs 6 signals) with clear rationale. Macro plan delegates authority to PR1 extract. Downstream PRs consume `algorithm_summary.json`, not the macro plan signal list. |
| F-5 | IMPORTANT | Accepted | Schema correctly allows empty `composite_signals` (not all algorithms have composites). Round-trip test validates actual extract output against schema. Test fixture is specific to current EVOLVE-BLOCK. |
| F-6 | IMPORTANT | Accepted | `assert field in body` is a regression detector, not a proof of correctness. Manual verification documented (F-21 note). Test catches the most likely drift (field removal). |
| F-7 | IMPORTANT | Accepted | `normalization_note` is optional (not all signals need normalization), machine-readable, well-documented. PR3 convergence-review gate specified. PR1 cannot enforce PR3 behavior. |
| F-9 | IMPORTANT | Accepted | Three automated gates (CI pytest, CI validate-mapping, manual grep) catch placeholder before merge. `--no-verify` bypass is inherent to Git, not a PR1 defect. |
| F-11 | IMPORTANT | Accepted | Plan provides clear PR3 instructions (read file, split by `\n`, join, hash) and cross-platform contingency. `test_extract_is_deterministic` verifies within a checkout. Low-probability risk for single-platform v1. |
| F-12 | IMPORTANT | **Fixed** | `--strict` mode now fails with exit 1 if `metrics.combined_score` is missing in `best_program_info.json`. Non-strict mode retains graceful degradation for exploratory use, with updated warning noting schema validation will fail. |
| F-13 | IMPORTANT | Accepted | Semantic gaps thoroughly documented with conservative fidelity ratings (medium, not high). PR5 quantitative thresholds specified. Rollback procedure documented. |
| F-14 | IMPORTANT | Accepted | Plan provides precise boundary definition (5 categories), specifies output format (Go source implementing `scheduling.Scorer`), and provides PR3 starting points. Boundary is as formal as possible without implementing PR3. |
| F-15 | IMPORTANT | Accepted | Golden-file test IS the staleness detection mechanism — it fails when EVOLVE-BLOCK changes. Test failure message warns against auto-copying. Procedural risk (developer ignores warning) is inherent to golden-file testing. |
| F-16 | IMPORTANT | Accepted | Version field is set by plan author, not user input. Mapping artifact and parser co-created in PR1. Practical risk is low. |
| F-17 | IMPORTANT | Accepted | Blacklist documented as known limitation with extension guidance. Whitelist premature for v1. |
| F-19 | IMPORTANT | Accepted | CI workflow explicitly passes `--strict` in extract step, so `--strict` is enforced regardless of env var. The `CI` env var check is defense-in-depth, not primary enforcement. |
| F-20 | IMPORTANT | Accepted | Fidelity check regex and mapping artifact are co-created in PR1. Format coupling documented. Test suite catches regressions. |
| F-21 | IMPORTANT | Accepted | Deliberate design decision. EVOLVE-BLOCK content not included because PR3 must parse it anyway. Content hash provides drift detection. Including raw content would be redundant and bloat the artifact. |
| F-22 | IMPORTANT | Accepted | Exit code boundary criteria clear for common cases. Edge case subjectivity is inherent to exit code design and is documented. |

### Convergence Review Finding Disposition (Round 14 — Fixer Round 7)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-22 | IMPORTANT | **Fixed** | `test_extract_multiple_evolve_blocks_warns` now also asserts exit 0, verifies stdout status is "ok", and checks that QueueDepth (from the first block) is in the extracted signals. Warning assertions retained. |
| F-23 | IMPORTANT | **Fixed** | Added BOOTSTRAP WORKFLOW note to BC-6 documenting the clear task-ordering sequence: Tasks 1-4 use extract without --strict, Task 5 creates mapping from extract output, Task 7 enables --strict in CI. Key invariant: --strict is never required until after the mapping artifact exists. |
| F-24 | IMPORTANT | **Fixed** | Added F-24 error handling convention verification requirement to the missing/default value assumption note. PR3 MUST verify "return score 0.0" convention against actual llm-d-inference-scheduler scorer implementations before adopting it. Pinned commit hash provides stable reference. |
| F-25 | IMPORTANT | **Fixed** | Added `test_composite_signals_match_golden_list` to `TestGoldenSignalList` with `EXPECTED_COMPOSITES` dict verifying EffectiveLoad composite and its constituents. Test table updated. |
| F-26 | IMPORTANT | **Fixed** | Version parser now validates captured version against `\d+\.\d+` pattern (matching schema) before accepting. Three-component versions (e.g., '1.0.0') fall back to 'unknown' with a descriptive warning. |
| F-27 | IMPORTANT | **Fixed** | Added explicit "Marker line inclusion" documentation to the F-18 hash normalization note, specifying that `lines[start_idx:end_idx + 1]` includes both EVOLVE-BLOCK-START and EVOLVE-BLOCK-END marker lines. PR3 MUST use the same inclusive range. |

### Convergence Review Finding Disposition (Round 16 — Fixer Round 9)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-18 | IMPORTANT | **Fixed** | Added ReDoS assessment comment to the signal extraction regex documenting that the pattern lacks nested quantifiers and is NOT ReDoS-vulnerable. Alternation with fixed-length alternatives and a single `\w+` capture does not cause catastrophic backtracking. |
| F-21 | IMPORTANT | **Fixed** | Added 10 MB size guard (`MAX_FILE_SIZE`) before `read_text()` calls on `best_program.py` and `best_program_info.json`. Defense-in-depth — these are repo-local artifacts, not user uploads, but the guard prevents accidental unbounded reads. |
| F-22 | IMPORTANT | **Fixed** | `cmd_validate_mapping` now tracks signal row counts via `mapping_signal_counts` dict and emits a stderr WARNING if duplicate signal names are found in the mapping table. Duplicates indicate ambiguous fidelity ratings. |
| F-23 | IMPORTANT | **Fixed** | `cmd_extract` now resolves `routing_dir` and validates it is within `REPO_ROOT` before proceeding. Defense-in-depth — the CLI is a developer tool, not web-facing, but path bounding prevents accidental reads from unrelated directories. |

### Convergence Review Finding Disposition (Round 18 — Fixer Round 3)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-5 | IMPORTANT | Accepted | Two-output design has four defense layers documented (different structures, `additionalProperties:false`, `test_extract_stdout_differs_from_file_artifact`, CLAUDE.md warning). PR3 defense-in-depth (`output_type` key check) also documented. Adequate for a developer tool. |
| F-6 | IMPORTANT | **Fixed** | Reordered `cmd_extract` to validate `routing_dir` (existence, `is_relative_to(REPO_ROOT)`) BEFORE the CI env var check. Invalid routing_dir now produces an actionable infrastructure error (exit 2) regardless of CI/--strict state. |
| F-11 | IMPORTANT | **Fixed** | Added docstring note to `_check_scope` documenting that `re.search(pattern, block)` matches in comments, explaining this is acceptable for v1 (Go code comments unlikely to reference out-of-scope struct names) with a mitigation path (comment-stripping pre-pass). |
| F-12 | IMPORTANT | Accepted | Content hash comment sensitivity is explicitly documented as intentionally conservative in BC-11. Recovery is straightforward (re-run extract). Distinguishing semantic from non-semantic changes in Go code is fragile. Design trade-off, not a defect. |
| F-14 | IMPORTANT | **Fixed** | Changed "potentially conflicting fidelity" error message to "Duplicate signal rows found" with clarification that duplicates are rejected as a data quality error regardless of whether ratings conflict. Test description updated. |
| F-15 | IMPORTANT | **Fixed** | Added boundary tests: `test_extract_few_signals_boundary_2_fails` (2 signals → exit 1) and `test_extract_few_signals_boundary_3_passes_threshold` (3 signals passes threshold). Test table updated. |
| F-16 | IMPORTANT | **Fixed** | Added comment to `block_hash` computation explaining `block.encode()` is safe: `block` is a Python str from `read_text()`, and `str.encode()` always succeeds on a valid str. Non-UTF-8 source bytes would already fail at `read_text()`. |
| F-17 | IMPORTANT | **Fixed** | Added `_UNSUPPORTED_KEYWORDS` set and `_check_unsupported_keywords()` to schema_validator. `validate_artifact()` now rejects schemas using `$ref`, `allOf`, `anyOf`, `oneOf`, `patternProperties`, `if/then/else` with a clear error before validation. Tests added. |
| F-18 | IMPORTANT | Accepted | Pre-flight (Step 0) is a manual implementation aid, not a runtime gate. The plan explicitly documents that the test suite (`TestSourceSyncVerification`, `TestGoldenSignalList`, BC-8 tests) is the automated safety net if pre-flight is skipped. |
| F-19 | IMPORTANT | Accepted | Hardcoded header allowlist (`"Sim", "Signal", "Composite"`) is documented with F-14 note explaining the mapping artifact and parser are co-created in PR1. Risk is low for v1; the note provides guidance for extension. |
| F-20 | IMPORTANT | Accepted | Temporal semantics appropriately deferred to PR3/PR5. PR1 documents the assumption and the promotion requirement. PR1 cannot enforce PR3 behavior. |
| F-21 | IMPORTANT | Accepted | Missing/default value handling documented as unverified design assumption with concrete 3-step verification procedure for PR3. PR1 correctly scopes this as PR3 responsibility. |
| F-22 | IMPORTANT | **Fixed** | `test_extract_without_mapping_graceful_degradation` now verifies `fidelity_checked` is `false` in the output artifact when the mapping artifact is absent. |
| F-23 | IMPORTANT | Accepted | Multiple EVOLVE-BLOCK pairs handled as warning (exit 0, first block extracted) is documented as intentional. Test verifies warning is emitted. For v1 with a single expected block, a warning is reasonable. |
| F-24 | IMPORTANT | Accepted | Schema validation is a structural check; file existence verification requires filesystem access, which is outside the scope of a JSON Schema validator. The content hash mechanism (BC-11) provides runtime drift detection. Reasonable design boundary. |

### Convergence Review Finding Disposition (Round 20 — Fixer Round 5)

| Finding | Severity | Disposition | Notes |
|---------|----------|-------------|-------|
| F-12 | IMPORTANT | **Fixed** | `_extract_signals` now warns on stderr for unrecognized method calls (not in METHOD_EXPANSIONS or _IGNORE_METHODS). Golden-file test remains primary safety net; warning provides immediate development feedback. |
| F-14 | IMPORTANT | **Fixed** | Added runtime Python version check in `main()` — exits with clear error message if Python < 3.9. Prevents confusing AttributeError from `Path.is_relative_to()`. |
| F-18 | IMPORTANT | **Fixed** | `_extract_evolve_block` now differentiates three error conditions: (1) no markers at all, (2) END without START, (3) START without END. Each emits a specific stderr WARNING. Caller error message updated to reference stderr for diagnostics. |
| F-20 | IMPORTANT | **Fixed** | `test_extract_content_hash_matches_evolve_block` docstring now documents shared slicing convention (`lines[start_idx:end_idx + 1]`), references F-27 canonical spec, and warns that test must be updated if `_extract_evolve_block` slicing changes. |
| F-21 | IMPORTANT | **Fixed** | Strengthened CI step recommendation from "low-priority" to "RECOMMENDED" with a concrete YAML snippet for hash consistency checking. Warning-level (not failure) to allow intentional development drift. |
| F-22 | IMPORTANT | **Fixed** | Added suggested `test_unmappable_signal_halts_extract` test case to the F-14 test coverage note, describing the full unmappable signal → low fidelity → BC-6 halt pathway. Marked as optional since core mechanism is verified by existing test. |
| F-23 | IMPORTANT | **Fixed** | Added suggested `test_validate_schema_rejects_stdout_json` test case to the two-output design enforcement mechanism documentation. Marked as optional since four existing defense layers provide adequate coverage. |
| F-24 | IMPORTANT | **Fixed** | `test_ci_must_not_skip_sync_tests` now also verifies `routing_go.stat().st_size > 0` to catch empty/corrupt submodule checkouts. Provides clear error message with recovery command. |

---

## Part 3: Quality Assurance

### J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [x] All submodule API references match actual code — `ROUTING_SNAPSHOT_FIELDS` derived from `type RoutingSnapshot struct` at pinned inference-sim commit; `METHOD_EXPANSIONS` verified against `EffectiveLoad()` implementation
- [x] Commit pins are current — `git submodule status` matches docs; llm-d-inference-scheduler hash pinned in mapping artifact
- [x] No stale references to APIs that have been renamed or removed — `LoadEvolvedBlock` confirmed absent (R6); all referenced struct fields verified by `TestSourceSyncVerification`

**Dimension 2: Schema chain integrity**
- [x] Each workspace artifact's output fields match consuming stage's input — `algorithm_summary.schema.json` defines all 9 required fields (F-7 fix: including `fidelity_checked`); `TestRoundTrip::test_extract_then_validate_schema_round_trip` verifies extract output validates against schema
- [x] JSON schema files match the macro plan's workspace artifact table — all fields from macro plan present; `composite_signals` and `mapping_artifact_version` added (documented in Deviation Log as ADDITION)
- [x] Writer → Reader chains traced and verified — extract (writer) → validate-schema (PR1 reader) → PR3 prompt templates + Go harness (future readers)

**Dimension 3: Prompt completeness**
- [x] N/A for PR1 — no prompt templates in this PR. PR3 will create prompt templates and must satisfy this dimension.

**Dimension 4: CLI contract**
- [x] All CLI commands produce documented JSON schemas — `extract` produces stdout JSON (operational report) + file artifact (schema-validated); `validate-mapping` and `validate-schema` produce stdout JSON with documented fields
- [x] Exit codes are consistent (0 = success, 1 = validation failure, 2 = infrastructure error) — exit code boundary documented in Section F; all edge cases tested (no signals = exit 1, missing file = exit 2, etc.)
- [x] Error messages are actionable — `errors[]` array in JSON output contains diagnostic strings identifying the specific problem

**Dimension 5: Artifact consistency**
- [x] Signal names match across mapping artifact, schemas, prompts, CLI code, and README — `TestGoldenSignalList` verifies extract output matches golden list; `TestCompositeSignalConsistency::test_method_expansions_match_mapping_composite_table` verifies composite signals match mapping artifact. **R2-F-15 exception:** `ROUTING_SNAPSHOT_FIELDS` contains 'ID' and 'FreeKVBlocks' which do NOT appear in the mapping artifact or `EXPECTED_SIGNALS` — this is intentional per the F-1 RECONCILIATION comment (struct fields not accessed in EVOLVE-BLOCK are included for completeness but are not extracted signals).
- [x] Field names match across workspace artifact schemas and code — `additionalProperties: false` in schema catches any field name drift; `test_extract_stdout_differs_from_file_artifact` verifies the two output structures don't contaminate each other
- [x] File paths referenced in documents exist or will be created — all paths in README.md, CLAUDE.md, and mapping artifact correspond to files created by Tasks 1-7

**Dimension 6: Dead artifact prevention**
- [x] Every file created by this PR has an identified consumer — see Section F "Confirmation: No dead code"
- [x] No orphan schemas — `algorithm_summary.schema.json` consumed by `validate-schema` command (PR1) and PR3 stages
- [x] No unreferenced prompts — N/A (no prompt templates in PR1)
- [x] No unused artifacts — mapping artifact consumed by `validate-mapping` (PR1), PR2 (scorer template), PR3 (prompt templates)

**Additional checks:**
- [x] PR category correctly identified — Artifact (per docs/contributing/pr-workflow.md Quick Reference table)
- [x] Verification gate matches PR category — Artifact gate: `validate-mapping` + `validate-schema` (see Verification Gate below)
- [x] No feature creep beyond macro plan scope — no prompt templates, no Go harness, no scorer template
- [x] Deviation log reviewed — R6 deferral documented, signal list deviation documented, no unresolved deviations
- [x] Each task produces working, verifiable output (no scaffolding) — all 3 CLI commands fully functional with tests
- [x] Task dependencies are correctly ordered (1→2→3→4→5→6→7)
- [x] All contracts are mapped to specific tasks — see Section H test strategy table
- [x] No unnecessary abstractions (single module CLI, lightweight validator)
- [x] No unexercised flags or interfaces
- [x] No breaking changes
- [x] No hidden global state impact
- [x] Python stdlib-only — no external dependencies (BC-7)
- [x] CLAUDE.md created (new project)

### Verification Gate (Artifact PR)

After all tasks complete, run the Artifact PR verification gate:

```bash
# 1. Validate mapping artifact completeness (bidirectional signal check)
python tools/transfer_cli.py validate-mapping

# 2. Extract with strict mode and validate against schema (round-trip)
python tools/transfer_cli.py extract --strict routing/
python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json

# 3. Run full test suite
python -m pytest tools/ -v

# 4. Manual: verify no dead artifacts (each file has an identified consumer)
# See Section F "Confirmation" for the consumer mapping.
```

Expected: All commands exit 0, all tests pass.

---

## Appendix: File-Level Implementation Details

### File: `tools/transfer_cli.py`

**Purpose:** Main CLI entry point for transfer pipeline mechanical tasks.

**Key Implementation Notes:**
- Argparse subcommands: `extract`, `validate-mapping`, `validate-schema`
- All output as JSON to stdout via `_output()` helper
- Exit codes: 0 (success), 1 (validation failure), 2 (infrastructure error)
- Signal extraction uses regex on Go source code embedded in Python string
- `ROUTING_SNAPSHOT_FIELDS` dict maps field names to Go types
- `METHOD_EXPANSIONS` dict maps method names (e.g., `EffectiveLoad`) to constituent fields
- Fidelity check only runs when mapping artifact exists (graceful degradation); `--strict` flag requires it (CI mode). In CI (env var `CI` set), extract **fails with exit 1** if `--strict` is not passed — hard gate, not just a warning

### File: `tools/schema_validator.py`

**Purpose:** Lightweight JSON Schema validator using stdlib only.

**Key Implementation Notes:**
- Supports: required fields, type checks, enum values, string pattern validation, nested objects, array item schemas (minItems, maxItems), `additionalProperties: false`
- Does NOT support: `$ref`, `allOf`, `patternProperties`, `oneOf`, `anyOf`
- ~100 lines, recursive descent validation
- Returns list of error strings (empty = valid)

### File: `tools/schemas/algorithm_summary.schema.json`

**Purpose:** JSON Schema defining the `workspace/algorithm_summary.json` contract.

**Required fields (9):** `algorithm_name` (string), `evolve_block_source` (string, pattern-validated `^(?!.*(?:\\.\\.|\\.)/)[^/].+\\.py:\\d+-\\d+$` rejecting absolute paths and path traversal `../` and `./`, with semantic start<=end check), `evolve_block_content_hash` (string, SHA-256 hex digest for drift detection), `signals` (array, minItems: 1, maxItems: 20, of objects with `name`, `type`, `access_path`, optional `normalization_note`), `composite_signals` (array, of objects with `name`, `constituents[]`, optional `formula` — documents how method calls expand to constituent signals so PR3 can reconstruct composites), `metrics` (object, required: `combined_score`), `scope_validation_passed` (boolean), `mapping_artifact_version` (string, pattern `^(unknown|\d+\.\d+)$`, for mapping artifact staleness detection), `fidelity_checked` (boolean, whether fidelity checks were performed during extraction — true when mapping artifact existed, false during bootstrap phase).

### File: `docs/transfer/blis_to_llmd_mapping.md`

**Purpose:** Signal mapping artifact documenting sim-to-production correspondences.

**Key Implementation Notes:**
- 5 RoutingSnapshot signals + 1 request-level field (SessionID boolean check) = 6 total signals
- Fidelity ratings: 3 high (QueueDepth, KVUtilization, SessionID), 3 medium (BatchSize, InFlightRequests, CacheHitRate — CacheHitRate provisional)
- All v1 signals have `staleness_window_ms = 0`
- Pins llm-d-inference-scheduler commit hash
- Includes scorer interface reference for PR2/PR3 consumption
