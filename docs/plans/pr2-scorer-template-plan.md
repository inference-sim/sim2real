# PR2: Scorer Template Artifact — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create an annotated scorer template that shows Stage 3's LLM exactly how to structure a production scorer plugin for llm-d-inference-scheduler.

**The problem today:** The Stage 3 prompt (PR3) will ask an LLM to generate a production scorer from a simulation-discovered algorithm. Without a concrete, annotated template showing the target system's conventions, the LLM has no reference for package structure, factory registration, metric access patterns, config toggles, or test structure. The generated code would likely fail to compile or deviate from project conventions.

**What this PR adds:**
1. `docs/transfer/scorer_template.go.md` — annotated example scorer with 8 required sections
2. Verified API references against the actual llm-d-inference-scheduler submodule source
3. Updated documentation (README.md cross-PR contracts)

**Why this matters:** This template is the bridge between the mapping artifact (PR1: what signals exist) and code generation (PR3: produce a working scorer). Without it, Stage 3 must infer conventions from scattered source files, risking compilation failures and convention violations.

**Architecture:** Pure Markdown artifact with embedded Go code blocks, annotated with comments explaining conventions. Based on the LoadAware scorer (simplest existing implementation). Code blocks are designed to be extractable and compilable against the pinned submodule HEAD (PR3 adds `tools/check_scorer_template.sh` for automated checking).

**PR Category:** Artifact (per docs/contributing/pr-workflow.md)

**Source:** Macro plan PR2: Scorer template artifact in docs/plans/2026-03-06-sim2real-transfer-macro-plan-v3.md

**Behavioral Contracts:** See Part 1, Section B below

---

# Part 1: Design Validation

## A) Executive Summary

PR2 creates a single artifact — `docs/transfer/scorer_template.go.md` — an annotated example scorer plugin that serves as the reference for Stage 3 (Generate) code generation. The template is based on the LoadAware scorer, the simplest existing implementation in llm-d-inference-scheduler, extended with annotations for multi-signal access, config toggle (feature flag), normalization, composite computation, and equivalence testing.

**Pipeline position:** PR1 (mapping artifact + CLI) → **PR2 (scorer template)** → PR3 (prompts + harness consume both).

**PR category:** Artifact — review perspectives: cross-system contracts (1), artifact completeness (2), plan structural validation (5).

**Phase 0 DEVIATION flags:**
- **DEV-1:** Metric field names (`RunningQueueSize`, `RunningRequestCount`, `KVCacheUsagePercent`) are NOT used anywhere in the llm-d-inference-scheduler codebase at the pinned commit. They exist in the external `gateway-api-inference-extension` Metrics struct but are unverified at the pinned dependency version. The mapping artifact already flags this as UNVERIFIED. The scorer template MUST annotate these fields as unverified and require PR3 to confirm.
- **DEV-2:** The `PrecisePrefixCache` scorer does NOT simply read a `KVCacheUsagePercent` metric field — it uses a ZMQ-based KV cache indexer (`llm-d-kv-cache` package). The CacheHitRate mapping is more complex than the mapping artifact implies. The template MUST note this for PR3.

## B) Behavioral Contracts

### Positive Contracts

**BC-1: Eight Required Sections**
- GIVEN the scorer template `docs/transfer/scorer_template.go.md`
- WHEN a reviewer checks its structure
- THEN it MUST contain all 8 sections: (1) package structure, (2) Scorer interface implementation, (3) metric access pattern, (4) config registration with factory, (5) feature flag / config toggle, (6) unit test structure, (7) ScoreEndpoints equivalence test function, (8) hot-reload documentation
- MECHANISM: Markdown headings enumerate sections; each contains annotated Go code blocks

**BC-2: API Signature Accuracy**
- GIVEN the scorer template references to the `scheduling.Scorer` interface
- WHEN compared against the actual interface
- THEN the method signatures (`Score`, `TypedName`, `Category`), parameter types, and return types MUST match the actual code in `sigs.k8s.io/gateway-api-inference-extension`
- MECHANISM: Template code mirrors the LoadAware scorer at `pkg/plugins/scorer/load_aware.go` (commit `091312c`), which imports and implements the `scheduling.Scorer` interface from the external `gateway-api-inference-extension` dependency. Verification is indirect: confirmed via `var _ scheduling.Scorer = &LoadAware{}` at `load_aware.go:27` and matching method signatures.

**BC-3: Factory Pattern Conformance**
- GIVEN the template's factory function
- WHEN compared against existing scorers' factories
- THEN it MUST follow the exact signature `func(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)` and use the `WithName(name)` convention
- MECHANISM: Direct copy of the LoadAware factory pattern with annotations

**BC-4: Feature Flag Implementation**
- GIVEN a generated scorer using this template
- WHEN the config sets `enabled: false`
- THEN `Score()` MUST return `nil` to signal that the scorer is inactive. **PARTIALLY VERIFIED:** PrecisePrefixCache.Score() returns nil in two production-reachable paths: (1) when request is nil (precise_prefix_cache.go:225) and (2) when getScores() returns an error (precise_prefix_cache.go:240). This confirms the framework must handle nil returns from scorers. However, the framework's nil-score aggregation logic is in the external `gateway-api-inference-extension` dependency, which has not been verified — the framework may handle nil differently depending on context (error path vs. intentional opt-out). PR3 MUST verify nil-score handling by reading the framework's score aggregation code before relying on this behavior for feature flag opt-out. If the framework treats nil from a feature-flag opt-out differently than nil from an error path, the feature flag pattern must be revised.
- MECHANISM: Template shows `Enabled bool` config field, early-return in `Score()` returning `nil`, and a unit test verifying disabled behavior

**BC-5: Metric Access Pattern**
- GIVEN the template's `Score()` method
- WHEN it demonstrates metric access
- THEN it MUST show `endpoint.GetMetrics().<field>` access pattern with annotations noting which fields are verified vs. unverified at the pinned commit
- MECHANISM: Template annotates `WaitingQueueSize` as VERIFIED (used by LoadAware) and other fields as UNVERIFIED

**BC-6: Unit Test Structure**
- GIVEN the template's unit test section
- WHEN it demonstrates test patterns
- THEN it MUST include: (a) table-driven test with `scheduling.NewEndpoint()`, (b) disabled/no-op test, (c) boundary test for score range [0, 1]. Note: the macro plan specifies an overlap assertion (disabled scorers have weight zero in config) as the third test type; this is substituted with a score-range boundary test because the overlap assertion requires a scheduler config fixture not available in a unit test context (see Deviation Log)
- MECHANISM: Go test code blocks following the `load_aware_test.go` pattern

### Negative Contracts

**BC-7: No Unverified Claims as Verified**
- GIVEN metric field names in the template
- WHEN the field has NOT been confirmed in the actual submodule source at the pinned commit
- THEN it MUST NOT be presented as verified — it MUST be annotated with `// UNVERIFIED` and a note that PR3 must confirm
- MECHANISM: Every metric field access is annotated with its verification status

**BC-8: No External Dependencies Beyond Framework**
- GIVEN the template's import list
- WHEN reviewed for dependencies
- THEN it MUST NOT introduce dependencies beyond the standard library, controller-runtime logging, and the gateway-api-inference-extension scheduling framework
- MECHANISM: Import list mirrors LoadAware (the simplest scorer with minimal deps)

### Error Handling Contracts

**BC-9: Missing Metric Graceful Handling**
- GIVEN the template's `Score()` method
- WHEN an endpoint returns a zero/default metric value
- THEN the scorer MUST handle it gracefully (return a defined score, not panic)
- MECHANISM: Template includes annotation showing zero-value handling pattern from LoadAware

## C) Component Interaction

```
PR1 artifacts                         PR2 artifact                      PR3 consumers
┌─────────────────────┐    informs   ┌───────────────────────┐  used by  ┌──────────────┐
│ blis_to_llmd_       │───────────→  │ scorer_template.go.md │────────→  │ prompts/     │
│ mapping.md          │    signals   │                       │  Stage 3  │ generate.md  │
│ (signal names,      │    + types   │  8 sections:          │  LLM ref  │              │
│  fidelity, norms)   │              │  - package structure  │           │ tools/       │
├─────────────────────┤              │  - Scorer interface   │           │ check_scorer │
│ algorithm_summary   │              │  - metric access      │           │ _template.sh │
│ .json               │              │  - factory + register │           └──────────────┘
│ (signal list)       │              │  - feature flag       │
└─────────────────────┘              │  - unit tests         │
                                     │  - ScoreEndpoints     │
llm-d-inference-scheduler            │  - hot-reload docs    │
┌─────────────────────┐   basis for  │                       │
│ pkg/plugins/scorer/  │───────────→ │  Version: 1.0         │
│ load_aware.go       │   patterns   │  Commit: 091312c      │
│ (Scorer interface,  │              └───────────────────────┘
│  factory, tests)    │
└─────────────────────┘
```

**Cross-system dependencies:**
- **Reads:** llm-d-inference-scheduler submodule source (commit `091312c`) for API verification
- **Reads:** PR1 mapping artifact for signal names, types, normalization notes
- **Produces:** `docs/transfer/scorer_template.go.md` (consumed by PR3)

**Dead artifact check:** The template is consumed by PR3's Stage 3 prompt (`prompts/generate.md`) and PR3's `tools/check_scorer_template.sh`.

## D) Deviation Log

| Macro Plan Says | Micro Plan Does | Reason |
|-----------------|-----------------|--------|
| "Template code compiles and passes tests against pinned llm-d-inference-scheduler HEAD" | Template code blocks are verified against source but NOT compiled (compilation requires full Go module dependency graph) | SIMPLIFICATION: PR2 is an artifact PR. Compilation requires Go module setup with the full dependency graph. PR3 adds `tools/check_scorer_template.sh` for automated compilation checking. PR2 verifies API accuracy by reading the submodule source directly (Task 1 initializes submodule for this purpose). |
| "All 8 required sections documented (per design doc)" | 8 sections as specified | No deviation |
| Scorer template includes `ScoreEndpoints` equivalence test function | Template includes `ScoreEndpoints` helper function that bridges scorer Score() output to harness-compatible format | No deviation — function wraps Score() for equivalence testing |
| Template version 1.0 | Version 1.0 | No deviation |
| Metric field names like `RunningQueueSize`, `KVCacheUsagePercent` | These fields annotated as UNVERIFIED with PR3 verification requirement | CORRECTION: These field names are not used anywhere in llm-d-inference-scheduler at pinned commit. They exist in the external Metrics struct but are unverified. The mapping artifact already flags this (DEV-1). |
| Template code verified by compilation | Template code verified by manual review of type visibility, export rules, and API signatures (Task 1); compilation deferred to PR3 | SIMPLIFICATION: Full compilation requires Go module setup. Task 1 performs manual verification of type exports, function signatures, and package visibility to catch errors like unexported types in external test packages. |
| Unit test structure includes overlap assertion (disabled scorers have weight zero in config) | Template includes boundary test for score range [0, 1] instead of overlap assertion | SUBSTITUTION: The design doc's overlap assertion ("disabled scorers have weight zero in config") is a config-level safety property that verifies the scheduler config sets `weight: 0` for disabled scorers to prevent double-counting. This requires a scheduler config fixture and config-parsing logic not available in a unit test. The template substitutes a score-range boundary test (verifying all scores in [0, 1]) as the third unit test pattern. PR3 MUST implement the overlap assertion as an integration test that loads a scheduler config fixture and verifies disabled scorers have weight zero. |
| LoadAware has no nil-metrics check; it returns 0.5 for zero-value WaitingQueueSize | Template adds a defensive nil guard returning 0.0 for nil metrics | DEFENSIVE ADDITION: LoadAware has no nil check on GetMetrics() — it accesses .WaitingQueueSize directly and returns 0.5 when the value is zero (non-nil pointer, zero value). The template adds a nil guard as a defensive measure beyond LoadAware's behavior. The mapping artifact's note ("All production metrics are assumed to be always available from endpoint.GetMetrics()") places nil-pointer handling out of scope for the mapping contract. The nil guard is a safety net, not a contract requirement. PR3's generated scorer should include it defensively. |

## E) Review Guide

1. **THE TRICKY PART:** The metric field names. The template must accurately distinguish VERIFIED fields (WaitingQueueSize — confirmed used by LoadAware) from UNVERIFIED fields (RunningQueueSize, RunningRequestCount, KVCacheUsagePercent — from design knowledge, not confirmed in submodule source). Getting this wrong means PR3's LLM generates code referencing non-existent fields.

2. **WHAT TO SCRUTINIZE:** BC-2 (API signature accuracy) and BC-7 (no unverified claims). Check that every `endpoint.GetMetrics().<field>` access in the template is annotated with its verification status. Check that the factory signature matches `load_aware.go`.

3. **WHAT'S SAFE TO SKIM:** The hot-reload documentation section (BC-1 section 8) — it's a brief note that v1 doesn't support hot-reload. The unit test boilerplate (BC-6) — it's a straightforward adaptation of `load_aware_test.go`.

4. **KNOWN DEBT:** The mapping artifact's `RunningQueueSize`, `RunningRequestCount`, and `KVCacheUsagePercent` field names are unverified. PR3 MUST initialize the submodule and confirm these fields exist in the `fwkdl.Metrics` struct at the pinned dependency version (`gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`).

---

# Part 2: Executable Implementation

## F) Implementation Overview

**Files to create:**
- `docs/transfer/scorer_template.go.md` — the scorer template artifact (main deliverable)

**Files to modify:**
- `docs/transfer/README.md` — add PR2 cross-references and template versioning note
- `docs/transfer/blis_to_llmd_mapping.md` — update Scorer Interface Reference with verified details

**Key decisions:**
1. Base template on LoadAware scorer (simplest, minimal deps, clear patterns)
2. Template shows a multi-signal scorer (not just single-metric like LoadAware) to match the evolved algorithm's needs
3. Feature flag uses config struct `Enabled bool` with `Score()` early-return (matches macro plan Objective 4)
4. ScoreEndpoints helper returns `map[string]float64` keyed by endpoint name for harness compatibility
5. Metric fields annotated as VERIFIED/UNVERIFIED to prevent silent API drift

**Confirmation:** No dead artifacts — `scorer_template.go.md` is consumed by PR3 (Stage 3 prompt + check script). README updates consumed by all future PRs.

## G) Task Breakdown

**Execution order:** Task 1 → Task 2 → Task 3 → Task 4 → Task 5. Task 2 (Update Mapping Artifact) must precede Task 3 (Author Scorer Template) because the template cross-references the mapping artifact.

### Task 1: Verify Submodule API Signatures

**Contracts Implemented:** BC-2, BC-3, BC-5

**Precondition:** The llm-d-inference-scheduler submodule MUST be initialized before this task begins. If the submodule is not initialized in the current worktree, run:
```bash
git submodule update --init llm-d-inference-scheduler
```
Verify the submodule is at the expected commit:
```bash
git submodule status llm-d-inference-scheduler
# Expected: 091312c333a50e94f5e60a2ca2926e8442eeffa9
```
**HALT:** If the submodule cannot be initialized or is at a different commit, STOP and resolve before proceeding.

**Files:**
- Read: `llm-d-inference-scheduler/pkg/plugins/scorer/load_aware.go`
- Read: `llm-d-inference-scheduler/pkg/plugins/scorer/load_aware_test.go`
- Read: `llm-d-inference-scheduler/pkg/plugins/register.go`
- Read: `llm-d-inference-scheduler/go.mod` (for Fact #7 module path and Fact #8 Go version)

**Step 1: Document verified API facts**

Context: Before authoring the template, confirm all API signatures against actual source. Record file:line citations for each fact. **Each fact below MUST be confirmed by reading the actual source file and recording the file:line where it was found.**

Verify these facts from the submodule at commit `091312c`:

1. **Scorer interface** (from `scheduling` package import):
   - `Score(ctx context.Context, cycleState *scheduling.CycleState, request *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64`
   - `TypedName() plugin.TypedName`
   - `Category() scheduling.ScorerCategory`

2. **Factory signature** (from `load_aware.go:30`):
   - `func LoadAwareFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)`

3. **Registration** (from `register.go`):
   - `plugin.Register(scorer.LoadAwareType, scorer.LoadAwareFactory)`
   - Verify the enclosing function name (e.g., `RegisterAllPlugins()`, `init()`, or other) — record the actual function name with file:line citation so K-1 Section 4 can reference it accurately

4. **Metric access** (from `load_aware.go:87`):
   - `endpoint.GetMetrics().WaitingQueueSize` — VERIFIED
   - `endpoint.GetMetadata().NamespacedName` — VERIFIED (from `session_affinity.go:79`)
   - `RunningQueueSize`, `RunningRequestCount`, `KVCacheUsagePercent` — NOT found in submodule

5. **Category values**: `scheduling.Distribution`, `scheduling.Affinity`
   - For each scorer listed in K-2's Scorer categories block, read its `Category()` method return value and record the `scheduling.Distribution` or `scheduling.Affinity` constant with file:line citation. Specifically: confirm PrecisePrefixCache's `Category()` return value (file:line) to earn the VERIFIED label on the K-2 assignment.

6. **Test utilities** (verify each with file:line citation from actual source):
   - `scheduling.NewEndpoint(metadata, metrics, attributes)` endpoint constructor — record file:line from `load_aware_test.go`
   - `fwkdl.Metrics{WaitingQueueSize: N}` metric struct — record file:line from `load_aware_test.go`
   - `fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-a"}}` metadata struct — record file:line
   - Table-driven tests with `cmp.Diff` comparison — record file:line
   - `utils.NewTestContext(t)` — confirm exists in `test/utils/context.go` with file:line citation

7. **Module path**: `github.com/llm-d/llm-d-inference-scheduler`

8. **Go version**: 1.25.7 (from go.mod)

9. **PrecisePrefixCache pattern** (from `pkg/plugins/scorer/precise_prefix_cache.go`):
   - Verify that PrecisePrefixCache uses a ZMQ-based KV cache indexer (`llm-d-kv-cache` package) rather than reading a simple `GetMetrics()` field for cache hit rate. Record file:line citation confirming the pattern. This validates DEV-2 and informs the CacheHitRate guidance in K-1 Section 3.

10. **Nil-propagation behavior of `scheduling.NewEndpoint`:** Read the `scheduling.NewEndpoint` implementation source (in the `gateway-api-inference-extension` dependency or the scheduling package) and the `GetMetrics()` method on the returned Endpoint type. Confirm whether `scheduling.NewEndpoint(metadata, nil, nil).GetMetrics()` returns `nil` or a zero-value `*fwkdl.Metrics{}` struct. Record file:line citation. This is required because K-1 Section 6 Test D passes `nil` metrics and expects `score 0.0` via the nil guard — if `GetMetrics()` returns a zero-value struct instead of nil, the nil guard will not trigger and Test D will fail with score 0.5 instead of 0.0.

11. **`plugin.Handle` methods:** K-1 Section 4 calls `handle.Context()` in the factory function. Verify that the `plugin.Handle` type (from `sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin`) exposes a `Context()` method returning `context.Context`. Record file:line citation. If `plugin.Handle` does not have a `Context()` method at commit `091312c`, K-1 Section 4 will produce code that fails to compile — update the factory to obtain context differently (e.g., from `context.Background()` or another Handle method).

12. **`logutil` constant names:** K-1 Section 4 uses `logutil.DEFAULT` and Section 2 references `logutil.DEBUG`. Verify that the `logutil` package (`sigs.k8s.io/gateway-api-inference-extension/pkg/common/util/logging`) exports constants named `DEFAULT` and `DEBUG` at the pinned dependency version. Record file:line citations. If the constant names differ (e.g., `DefaultLevel` instead of `DEFAULT`), update K-1 to use the correct names.

**Step 2: Record deviations from mapping artifact**

Compare verified facts against `docs/transfer/blis_to_llmd_mapping.md` Scorer Interface Reference section. Document each deviation for the template.

No commit needed — this is a verification step. However, record the verification results as a checklist in the executor's session notes or PR description body, listing each fact with its confirmed file:line citation. This creates an auditable record that Task 2 and Task 3 can reference, and that reviewers can verify.

**Step 3: Halt gate — evaluate verification results**

Review the facts documented in Steps 1-2. Apply these halt conditions:

| Condition | Action |
|-----------|--------|
| Scorer interface signature differs from what's listed in Step 1 | **HALT.** Update Step 1 facts, then update K-1 template content before proceeding to Task 2 (Update Mapping Artifact). |
| Factory signature differs | **HALT.** Same as above. |
| `WaitingQueueSize` field not found in `load_aware.go` | **HALT.** The only VERIFIED metric field is invalid — escalate. |
| UNVERIFIED fields confirmed to NOT exist and no alternatives found | **WARN.** Proceed to Task 2 but annotate fields as UNAVAILABLE instead of UNVERIFIED. (This is a PR2-scope decision: the template is still authored, but with UNAVAILABLE annotations. The K-1 template header contains a separate HALT CONDITION for PR3's code generation scope — if fewer than 2 of 3 UNVERIFIED fields can be resolved at PR3 time, PR3 must halt code generation.) |
| Fact #10: `GetMetrics()` returns zero-value struct (not nil) for nil metrics input | **UPDATE.** Revise K-1 Section 6 Test D: change `wantScore` from `0.0` to `0.5` (nil guard won't fire on zero-value struct). Update the Test D comment and Deviation Log accordingly. The nil guard still provides value for truly-nil metrics from other sources, but the test must reflect actual `NewEndpoint` behavior. |
| All facts match | **PROCEED** to Task 2. |

If any HALT condition fires, resolve it before proceeding. Document resolution in the commit message.

---

### Task 2: Update Mapping Artifact Scorer Interface Reference

**Contracts Implemented:** BC-2, BC-7

**Files:**
- Modify: `docs/transfer/blis_to_llmd_mapping.md`

**Step 1: Update Scorer Interface Reference section**

Context: The mapping artifact's Scorer Interface Reference section was written from design knowledge and is flagged as UNVERIFIED. Now that we've verified the API against the actual source (Task 1), update the section with verified details and clearer UNVERIFIED annotations.

Update the "Scorer Interface Reference" section to include:
- Verified `Score()` signature with `CycleState` parameter (which the current section omits)
- Verified import paths
- Verified factory signature
- Clear distinction: `WaitingQueueSize` = VERIFIED, other metric fields = UNVERIFIED
- Note about `gateway-api-inference-extension` dependency version (`v0.0.0-20260128235548-fd30cb97714a`)

**Also update the document-level UNVERIFIED annotation** (line 4 of the file): Replace the blanket "UNVERIFIED — submodule not initialized" statement with a partial-verification annotation. Example: `PARTIALLY VERIFIED — Scorer Interface Reference section verified against submodule at commit 091312c (PR2 Task 1). Signal Mapping Table field names and other sections remain UNVERIFIED pending PR3 submodule initialization.` This prevents the document header from contradicting the now-verified Scorer Interface Reference section.

**Step 2: Validate cross-references**

Verify updated section is consistent with Task 1 verification results.

**Step 3: Commit**

```bash
git add docs/transfer/blis_to_llmd_mapping.md
git commit -m "$(cat <<'EOF'
docs(transfer): update scorer interface reference with verified API details

- Update document-level UNVERIFIED annotation to PARTIALLY VERIFIED (line 4)
- Add verified Score() signature including CycleState parameter
- Add verified factory function signature and import paths
- Distinguish VERIFIED (WaitingQueueSize) from UNVERIFIED metric fields
- Note gateway-api-inference-extension dependency version

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Author Scorer Template

**Contracts Implemented:** BC-1, BC-2, BC-3, BC-4, BC-5, BC-6, BC-7, BC-8, BC-9

**Files:**
- Create: `docs/transfer/scorer_template.go.md`

**Step 1: Author artifact**

Context: This is the main deliverable — an annotated example scorer plugin that Stage 3's LLM will use as a reference for generating production scorer code. The template must show conventions for all 8 required sections using patterns verified in Task 1.

Complete artifact content is in Appendix K-1.

**Step 2: Validate cross-references**

Verify that:
- Every import path matches the verified imports from `load_aware.go`
- The factory signature matches the verified signature from Task 1
- Metric field `WaitingQueueSize` is annotated as VERIFIED
- Metric fields `RunningQueueSize`, `RunningRequestCount`, `KVCacheUsagePercent` are annotated as UNVERIFIED
- Signal names match the **updated** `docs/transfer/blis_to_llmd_mapping.md` Signal Mapping Table (updated in Task 2)
- The `scheduling.Distribution` category matches verified LoadAware usage

**Step 3: Verify no dead artifacts**

Consumer: PR3 `prompts/generate.md` (Stage 3 prompt references the template for code generation).
Consumer: PR3 `tools/check_scorer_template.sh` (automated compilation check).

**Step 4: Commit**

```bash
git add docs/transfer/scorer_template.go.md
git commit -m "$(cat <<'EOF'
docs(transfer): add scorer template artifact (BC-1 through BC-9)

- Annotated example scorer with 8 required sections
- Based on verified LoadAware scorer patterns (commit 091312c)
- Feature flag, metric access, factory registration, unit tests
- ScoreEndpoints equivalence test helper function
- Unverified metric fields clearly annotated for PR3 verification

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Update README Cross-PR Contracts

**Contracts Implemented:** BC-1

**Files:**
- Modify: `docs/transfer/README.md`

**Step 1: Add PR2 cross-references**

Context: The README already has Cross-PR Contracts for PR3. Add a section documenting what PR2 delivers and how PR3 should consume the scorer template.

Add to README:
- Scorer template version and location
- PR3 obligation: run `tools/check_scorer_template.sh` to verify template compiles
- PR3 obligation: verify UNVERIFIED metric fields before generating code

**Step 2: Commit**

```bash
git add docs/transfer/README.md
git commit -m "$(cat <<'EOF'
docs(transfer): add PR2 scorer template cross-PR contracts

- Document scorer template location and version
- Add PR3 obligations for template compilation and field verification

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Final Verification

**Contracts Implemented:** All (BC-1 through BC-9)

**Step 1: Verify all 8 sections present**

Check `docs/transfer/scorer_template.go.md` contains headings for all 8 sections:
1. Package Structure
2. Scorer Interface Implementation
3. Metric Access Pattern
4. Config Registration (Factory)
5. Feature Flag (Config Toggle)
6. Unit Test Structure
7. ScoreEndpoints Equivalence Test
8. Hot-Reload Documentation

**Step 2: Verify cross-document consistency**

Run signal name consistency check across artifacts. This check verifies both production field names (used in Go code blocks) and simulation signal names (used in annotation comments and the mapping table's "Sim Equivalent" column):
```bash
# Check production field names appear in both files
grep -n "WaitingQueueSize\|RunningQueueSize\|RunningRequestCount\|KVCacheUsagePercent" \
  docs/transfer/scorer_template.go.md \
  docs/transfer/blis_to_llmd_mapping.md

# Check simulation signal names appear in both files (in annotations/comments)
# Note: SessionID appears as "SessionID" in the mapping artifact and as
# "x-session-token" / "Session header" in the template. Use case-insensitive
# grep for session-related terms to catch both forms.
grep -ni "QueueDepth\|BatchSize\|InFlightRequests\|KVUtilization\|CacheHitRate\|session" \
  docs/transfer/scorer_template.go.md \
  docs/transfer/blis_to_llmd_mapping.md
```

**Success criteria:** Each of the 4 production field names (WaitingQueueSize, RunningQueueSize, RunningRequestCount, KVCacheUsagePercent) MUST appear in **both** files as Go code or field references. Each of the 5 simulation signal names (QueueDepth, BatchSize, InFlightRequests, KVUtilization, CacheHitRate) MUST appear in **both** files in annotation comments or mapping table columns. The session signal uses different surface forms: `SessionID` in the mapping artifact and `x-session-token` / `Session header` in the template — the case-insensitive grep for `session` confirms both files reference the session concept. If a name appears in only one file, it indicates an inconsistency that must be resolved before proceeding.

**Step 3: Verify no dead artifacts**

Confirm every new/modified file has an identified consumer:
- `scorer_template.go.md` → PR3 Stage 3 prompt + check script
- `blis_to_llmd_mapping.md` updates → PR3 Stage 2 prompt
- `README.md` updates → all future PRs

**Step 4: Run existing tests and validate mapping artifact**

```bash
# Run from the repo root (adapt path if using a worktree)
python -m pytest tools/ -v

# Validate mapping artifact (required by pr-workflow.md for Artifact PRs when mapping exists)
python tools/transfer_cli.py validate-mapping
```

Expected: All existing PR1 tests pass (PR2 is artifact-only, no code changes). The `validate-mapping` command must also pass, confirming the updated mapping artifact is structurally valid.

**NOTE:** The pr-workflow.md Artifact PR gate includes a step: "Manual: extract code blocks from .go.md files, compile against pinned submodule HEAD." This step applies to `scorer_template.go.md` but is **intentionally deferred to PR3** per Deviation Log DEV-1 — PR2 does not add a Go module environment. PR3 adds `tools/check_scorer_template.sh` for automated compilation checking. This Task 5 gate is complete for PR2 scope without the compilation step.

**If tests fail:** Since PR2 makes no code changes, test failures indicate a pre-existing issue or environment problem. **HALT** — do not proceed with the PR. Diagnose the failure: (a) if it is a pre-existing test failure unrelated to PR2 changes, document it in the PR description and proceed only if the same failure is reproducible on the base branch; (b) if it is an environment issue (missing dependency, wrong Python version), fix the environment and re-run; (c) if the failure is unexplained, escalate before merging.

## H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 (8 sections) | Task 3, 5 | Structural check | Manual: verify 8 headings present |
| BC-2 (API accuracy) | Task 1, 3 | Cross-system audit | Source comparison: template vs load_aware.go |
| BC-3 (factory pattern) | Task 1, 3 | Cross-system audit | Source comparison: template vs load_aware.go |
| BC-4 (feature flag) | Task 3 | Structural check | Template includes Enabled field + early-return + test |
| BC-5 (metric access) | Task 1, 3 | Cross-system audit | Source comparison: VERIFIED/UNVERIFIED annotations |
| BC-6 (unit tests) | Task 3 | Structural check | Template includes 3 test patterns |
| BC-7 (no false verification) | Task 2, 3 | Cross-system audit | Every metric field annotated with verification status |
| BC-8 (no extra deps) | Task 3 | Structural check | Import list matches LoadAware |
| BC-9 (graceful zero) | Task 3 | Structural check | Template shows zero-value handling pattern |

**Cross-system invariants:**
- Signal names in template match mapping artifact (Dimension 5)
- Import paths match actual submodule source (Dimension 1)
- Commit pin in template matches `git submodule status` output (Dimension 1)

## I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Metric field names don't exist in external Metrics struct | Medium | High — PR3 generates uncompilable code | Template annotates UNVERIFIED fields; PR3 must verify before use | Task 1, 3 |
| Template patterns diverge from real scorer conventions | Low | Medium — generated code doesn't match project style | Template based directly on LoadAware (verified source) | Task 1, 3 |
| Submodule updated between PR2 and PR3, making template stale | Low | Medium — compilation failure | Template pins commit hash; PR3 check script detects staleness | Task 3, 4 |
| ScoreEndpoints helper doesn't match PR3 harness interface | Low | Low — helper is a thin wrapper, easily adapted | Helper kept minimal; PR3 harness defines the authoritative interface | Task 3 |

---

# Part 3: Quality Assurance

## J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [ ] All submodule API references verified against actual code (Task 1 — verified at execution time, not at plan-writing time)
- [ ] Commit pin `091312c` matches `git submodule status` output (Task 1 precondition check confirms this)
- [ ] No stale references — all from current pinned commit (confirmed by Task 1 execution)

**Dimension 2: Schema chain integrity**
- [x] N/A — PR2 does not produce or consume workspace artifacts

**Dimension 3: Prompt completeness**
- [x] N/A — PR2 does not create prompt templates

**Dimension 4: CLI contract**
- [x] N/A — PR2 does not modify CLI

**Dimension 5: Artifact consistency**
- [ ] Signal names match between scorer template and mapping artifact (Task 5 Step 2 verifies at execution time)
- [x] Field names annotated with VERIFIED/UNVERIFIED status
- [x] File paths referenced in README exist or will be created by PR3

**Dimension 6: Dead artifact prevention**
- [x] `scorer_template.go.md` consumed by PR3 (Stage 3 prompt + check script)
- [x] README updates consumed by all future PRs
- [x] Mapping artifact updates consumed by PR3 Stage 2

**Additional checks:**
- [x] PR category: Artifact (correct — review perspectives: 1, 2, 5)
- [ ] Verification gate: Artifact (validate-mapping + manual cross-reference check — Task 5 Step 4 runs validate-mapping at execution time)
- [x] No feature creep beyond macro plan scope
- [x] Deviation log reviewed — DEV-1 and DEV-2 documented and mitigated
- [x] Each task produces verifiable output
- [x] Task dependencies correctly ordered (Task 1 → Task 2 → Task 3 → Task 4 → Task 5)
- [x] All contracts mapped to specific tasks

---

# Appendix K: File-Level Implementation Details

## K-1: `docs/transfer/scorer_template.go.md`

````markdown
# Scorer Template — llm-d-inference-scheduler Plugin

**Version:** 1.0
**Pinned commit:** `091312c333a50e94f5e60a2ca2926e8442eeffa9`
**Based on:** `pkg/plugins/scorer/load_aware.go` (LoadAware scorer)
**Module:** `github.com/llm-d/llm-d-inference-scheduler`
**Go version:** 1.25.7

> **For Stage 3 LLM:** Use this template as the structural reference for generating a production scorer plugin. Follow the conventions exactly — package location, factory signature, type assertion, test patterns. To generate the scoring logic:
> - **Signal names and types:** Read from `workspace/algorithm_summary.json` (the `signals` and `composite_signals` arrays).
> - **Scoring logic (weights, penalty functions, formulas):** Extract from the EVOLVE-BLOCK source file at the path in `algorithm_summary.json`'s `evolve_block_source` field. The source is a Python file containing Go code embedded in a triple-quoted string literal (`GO_ROUTING_CODE = """..."""`). You MUST parse the Python file to extract the embedded Go, then translate the scoring logic into the `Score()` method below.
> - **Signal-to-production mappings:** Use `docs/transfer/blis_to_llmd_mapping.md` to map simulation signal names to production `endpoint.GetMetrics()` fields.
> - **Do NOT assume `algorithm_summary.json` contains scoring logic** — it captures signal metadata only (see schema description).
>
> **PREREQUISITE — Artifact Validation (before consuming `workspace/algorithm_summary.json`):**
> 1. Verify the file exists: if `workspace/algorithm_summary.json` does not exist on disk, HALT — the extract stage has not run successfully.
> 2. Validate schema: run `python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json` and confirm exit code 0.
> 3. Check scope validation: read `scope_validation_passed` from the JSON — if `false`, HALT — the algorithm contains out-of-scope patterns.
> 4. **Stale artifact caveat:** Steps 1-3 are the Stage 3 LLM's actionable validation checks. However, a stale artifact from a prior successful extract run may remain on disk after a subsequent failed run (see CLAUDE.md). Exit code verification is the **pipeline orchestrator's** responsibility (not Stage 3's) — the orchestrator MUST confirm the extract stage exited 0 before invoking Stage 3. If Stage 3 is invoked without orchestrator validation, Steps 1-3 provide best-effort freshness detection but cannot guarantee the artifact corresponds to the most recent extract invocation.
> 5. **EVOLVE-BLOCK content hash verification:** Read `evolve_block_content_hash` and `evolve_block_source` from `algorithm_summary.json`. Parse the file path and line range from `evolve_block_source` (format: `path/to/file.py:START-END`). Read lines START through END (inclusive) from that file. To compute the hash: join the lines with `\n` (Unix newline, U+000A) as the separator, append a trailing `\n` after the last line, encode the result as UTF-8, and compute the SHA-256 hex digest (lowercase). This matches `transfer_cli.py`'s `extract` command, which reads lines with Python's `readlines()` (preserving trailing newlines) and hashes the UTF-8 encoded concatenation. Compare the computed digest against `evolve_block_content_hash`. If the hashes differ, HALT — the EVOLVE-BLOCK source has changed since extraction and the signal list in `algorithm_summary.json` may be stale. Re-run the extract stage before proceeding.
>
> **IMPORTANT — UNVERIFIED FIELD HALT CONDITION (PR3 scope — applies during code generation, not during PR2 template authoring):** Metric field names marked `// UNVERIFIED` in this template have NOT been confirmed against the actual `fwkdl.Metrics` struct at the pinned dependency version (`gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`). Before generating code, you MUST:
> 1. Initialize the submodule and run `go mod download`
> 2. Locate the `fwkdl.Metrics` struct definition and confirm each UNVERIFIED field name exists
> 3. **If a field does NOT exist:** (a) Search the codebase for the closest equivalent field name, (b) Update the mapping artifact and this template with the correct name, (c) If no equivalent exists, remove the field from the template and note it as UNAVAILABLE in the mapping artifact
> 4. **HALT CONDITION:** If fewer than 2 of the 3 UNVERIFIED fields can be resolved (confirmed or mapped to alternatives), STOP code generation and escalate — the evolved algorithm may not be implementable with the available production metrics. Do NOT generate a scorer that silently drops signals.

---

## Section 1: Package Structure

The scorer lives in the scorer plugin package alongside existing scorers.

```
llm-d-inference-scheduler/
├── pkg/
│   └── plugins/
│       ├── register.go           ← Add plugin.Register() call here
│       └── scorer/
│           ├── load_aware.go     ← Existing (reference implementation)
│           ├── load_aware_test.go
│           ├── <your_scorer>.go       ← New scorer file (includes ScoreEndpoints helper — see Section 7)
│           └── <your_scorer>_test.go  ← New test file (package scorer_test — external test package)
└── test/
    └── utils/
        └── context.go            ← Test context helper
```

**Convention:** One scorer per file. File name matches scorer type (snake_case). Test file is `<name>_test.go` using the external test package (`package scorer_test`) for black-box testing.

---

## Section 2: Scorer Interface Implementation

The `scheduling.Scorer` interface is defined in the `gateway-api-inference-extension` framework. All scorers MUST implement these three methods.

```go
package scorer

import (
	"context"
	"encoding/json"
	"fmt"

	"sigs.k8s.io/controller-runtime/pkg/log"
	logutil "sigs.k8s.io/gateway-api-inference-extension/pkg/common/util/logging"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"
)

const (
	// EvolvedScorerType is the unique type name for this scorer.
	// Convention: lowercase-hyphenated, suffixed with "-scorer".
	EvolvedScorerType = "evolved-routing-scorer"
)

// Compile-time type assertion — ensures EvolvedScorer implements scheduling.Scorer.
// If the interface changes, this line produces a compile error (not a runtime panic).
var _ scheduling.Scorer = &EvolvedScorer{}

// EvolvedScorer implements the evolved routing algorithm as a production scorer.
//
// ANNOTATION: The struct holds:
// - typedName: required by all scorers for plugin identity
// - config fields: parsed from JSON parameters at factory time
// - enabled: feature flag for config toggle (see Section 5)
type EvolvedScorer struct {
	typedName plugin.TypedName
	enabled   bool

	// Config fields from factory parameters.
	// These are set once at construction time; not hot-reloadable in v1.
	queueThreshold float64
}

// TypedName returns the plugin's type and instance name.
// ANNOTATION: The Type is the constant (e.g., "evolved-routing-scorer").
// The Name is set by WithName() from the factory — it's the instance name
// from the scheduler config YAML.
func (s *EvolvedScorer) TypedName() plugin.TypedName {
	return s.typedName
}

// WithName sets the instance name. Called by the factory.
// Convention: every scorer has this method.
func (s *EvolvedScorer) WithName(name string) *EvolvedScorer {
	s.typedName.Name = name
	return s
}

// Category returns the scoring category.
// ANNOTATION: Use scheduling.Distribution for load-balancing scorers
// (spread traffic across endpoints). Use scheduling.Affinity for
// session-pinning scorers (prefer specific endpoints).
// The evolved algorithm is a load-distribution scorer.
func (s *EvolvedScorer) Category() scheduling.ScorerCategory {
	return scheduling.Distribution
}

// Score computes per-endpoint scores in the range [0.0, 1.0].
//
// ANNOTATION: This is the core method. It receives:
// - ctx: context for logging and cancellation
// - cycleState: shared state across plugins in a scheduling cycle (may be nil in tests).
//   CONSTRAINT: The ScoreEndpoints test helper (Section 7) always passes nil for cycleState.
//   Generated scorers MUST NOT dereference cycleState — if the evolved algorithm needs
//   inter-plugin state, escalate (the harness must be extended first).
// - request: the incoming LLM request (model name, headers, etc.)
// - endpoints: candidate endpoints to score
//
// Returns: map from endpoint to score. Higher score = more preferred.
// Convention: scores MUST be in [0.0, 1.0]. The scheduler normalizes and
// applies weights across all scorers.
//
// >>> REPLACE THIS BODY with the evolved algorithm's scoring logic <<<
// >>> Signal names/types: from algorithm_summary.json; Scoring logic: extract from EVOLVE-BLOCK at evolve_block_source path <<<
// >>> Signal-to-production mappings: from blis_to_llmd_mapping.md <<<
func (s *EvolvedScorer) Score(ctx context.Context, _ *scheduling.CycleState, request *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
	logger := log.FromContext(ctx)

	// Feature flag check — see Section 5
	if !s.enabled {
		return nil // nil scores = scorer is inactive (UNVERIFIED — see BC-4; PR3 must confirm framework behavior)
	}

	scoredEndpoints := make(map[scheduling.Endpoint]float64, len(endpoints))

	for _, endpoint := range endpoints {
		// --- Metric access (see Section 3 for field details) ---
		metrics := endpoint.GetMetrics()

		// ANNOTATION: Defensive nil check — if an endpoint has no metrics,
		// return score 0.0 for that endpoint rather than panicking.
		// NOTE: LoadAware has no nil guard (it accesses .WaitingQueueSize directly
		// and returns 0.5 for zero-value, non-nil metrics). The mapping artifact
		// states "All production metrics are assumed to be always available from
		// endpoint.GetMetrics()" — nil-pointer handling is out of scope for that
		// contract. This nil guard is a defensive safety net beyond LoadAware's
		// behavior, not a mapping-artifact requirement. This satisfies BC-9.
		if metrics == nil {
			scoredEndpoints[endpoint] = 0.0
			continue
		}

		// VERIFIED field (confirmed in load_aware.go:87):
		waitingQueueSize := float64(metrics.WaitingQueueSize) // VERIFIED

		// ⚠ UNVERIFIED FIELDS — DO NOT UNCOMMENT until PR3 confirms they exist in fwkdl.Metrics.
		// If a field does not exist, see HALT CONDITION in the template header.
		// runningQueueSize := float64(metrics.RunningQueueSize)       // UNVERIFIED — may not compile
		// runningRequestCount := float64(metrics.RunningRequestCount) // UNVERIFIED — may not compile
		// kvCacheUsagePct := float64(metrics.KVCacheUsagePercent)     // UNVERIFIED — may not compile

		// --- PLACEHOLDER: simple load-based score (PR3 MUST replace with evolved logic) ---
		// PR3 validation: grep for "PLACEHOLDER" in generated code. If found, generation is incomplete.
		score := 0.5
		if waitingQueueSize > 0 {
			if waitingQueueSize > s.queueThreshold {
				waitingQueueSize = s.queueThreshold
			}
			score = 0.5 * (1.0 - (waitingQueueSize / s.queueThreshold))
		}

		// ANNOTATION: Normalization example for KVUtilization.
		// Sim uses 0.0–1.0 range; production KVCacheUsagePercent is 0–100.
		// Normalize: kvUtilization := kvCacheUsagePct / 100.0
		// See blis_to_llmd_mapping.md KVUtilization row for details.

		// ANNOTATION: Composite signal example (EffectiveLoad).
		// EffectiveLoad = QueueDepth + BatchSize + InFlightRequests
		// Production: WaitingQueueSize + RunningQueueSize + RunningRequestCount
		// Compute inline — there is no single production metric equivalent.
		// effectiveLoad := waitingQueueSize + runningQueueSize + runningRequestCount

		scoredEndpoints[endpoint] = score
		_ = logger // suppress unused warning; use logger.V(logutil.DEBUG).Info(...) for debug output
	}

	return scoredEndpoints
}
```

---

## Section 3: Metric Access Pattern

Scorers access per-endpoint metrics via `endpoint.GetMetrics()`. The metrics are populated by the data layer from endpoint health reports.

| Production Field | Go Access | Sim Equivalent | Status | Notes |
|-----------------|-----------|----------------|--------|-------|
| `WaitingQueueSize` | `endpoint.GetMetrics().WaitingQueueSize` | QueueDepth | **VERIFIED** (load_aware.go:87) | int, count of waiting requests |
| `RunningQueueSize` | `endpoint.GetMetrics().RunningQueueSize` | BatchSize (approx) | **UNVERIFIED** | int, PR3 must confirm field exists |
| `RunningRequestCount` | `endpoint.GetMetrics().RunningRequestCount` | InFlightRequests (approx) | **UNVERIFIED** | int, PR3 must confirm field exists |
| `KVCacheUsagePercent` | `endpoint.GetMetrics().KVCacheUsagePercent` | KVUtilization | **UNVERIFIED** | float64 (0-100), divide by 100 for sim scale |

**Request-level access:**
| Field | Go Access | Status |
|-------|-----------|--------|
| Session header | `request.Headers["x-session-token"]` | **VERIFIED** (session_affinity.go:20 defines `sessionTokenHeader = "x-session-token"`; line 66 accesses via this constant) |

**Endpoint metadata access:**
| Field | Go Access | Status |
|-------|-----------|--------|
| Endpoint name | `endpoint.GetMetadata().NamespacedName.String()` | **VERIFIED** (session_affinity.go:79) |

> **CacheHitRate note:** The mapping artifact maps CacheHitRate to the PrecisePrefixCache scorer. However, `PrecisePrefixCache` does NOT simply read a metric field — it uses a ZMQ-based KV cache indexer (`llm-d-kv-cache` package) with its own event-driven state. Accessing cache hit rate may require a different pattern than `GetMetrics()`.
>
> **PR3 investigation steps:**
> 1. Read `pkg/plugins/scorer/precise_prefix_cache.go` to understand the KV cache indexer integration
> 2. Determine if cache hit rate is available as a computed value or requires the ZMQ indexer
> 3. **If cache hit rate is not accessible via `GetMetrics()`:** The evolved scorer cannot use CacheHitRate as a simple metric field. Options: (a) omit CacheHitRate from the evolved scorer and note the signal loss, (b) integrate the KV cache indexer as a dependency (significant complexity increase), (c) use a proxy metric if one exists
> 4. **HALT CONDITION:** If CacheHitRate appears in `algorithm_summary.json`'s signals array (indicating it is used by the evolved algorithm) and no feasible access path exists, PR3 must parse the EVOLVE-BLOCK source (at the location in `algorithm_summary.json`'s `evolve_block_source`) to determine how prominently CacheHitRate is used. If CacheHitRate is used in the algorithm's scoring logic (e.g., in a penalty term or composite signal) and cannot be accessed, escalate — the algorithm may need re-evolution without this signal. If CacheHitRate is present in the signals array but not referenced in the scoring logic of the EVOLVE-BLOCK, it can be omitted with documented signal loss. Note: `algorithm_summary.json` captures signal metadata only, not scoring weights — PR3 must parse the EVOLVE-BLOCK directly to assess signal importance (see schema description).

---

## Section 4: Config Registration (Factory)

Every scorer MUST have a factory function and be registered in `register.go`.

```go
// --- Factory function ---
// ANNOTATION: Signature is fixed by the plugin framework.
// Parameters:
//   - name: instance name from scheduler config YAML
//   - rawParameters: JSON blob from config YAML "parameters" field
//   - handle: provides context and shared resources
//
// Convention: provide sensible defaults; only fail on genuinely invalid config.
func EvolvedScorerFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error) {
	params := EvolvedScorerParameters{
		QueueThreshold: 128,  // sensible default
		Enabled:        true, // enabled by default
	}
	if rawParameters != nil {
		if err := json.Unmarshal(rawParameters, &params); err != nil {
			return nil, fmt.Errorf("failed to parse parameters for '%s' scorer: %w", EvolvedScorerType, err)
		}
	}

	return NewEvolvedScorer(handle.Context(), params).WithName(name), nil
}

// --- Config struct ---
// ANNOTATION: Fields map to the YAML "parameters" block in the scheduler config.
// Use json tags for serialization. Provide defaults in the factory.
// ANNOTATION: Exported (uppercase) because external test packages (scorer_test)
// need to construct test instances via NewEvolvedScorer().
type EvolvedScorerParameters struct {
	QueueThreshold int  `json:"queueThreshold"`
	Enabled        bool `json:"enabled"`
}

// --- Constructor ---
func NewEvolvedScorer(ctx context.Context, params EvolvedScorerParameters) *EvolvedScorer {
	if params.QueueThreshold <= 0 {
		params.QueueThreshold = 128
		log.FromContext(ctx).V(logutil.DEFAULT).Info("queueThreshold must be positive, using default 128")
	}

	return &EvolvedScorer{
		typedName:      plugin.TypedName{Type: EvolvedScorerType},
		enabled:        params.Enabled,
		queueThreshold: float64(params.QueueThreshold),
	}
}
```

**Registration in `pkg/plugins/register.go`:**

```go
// Add this line to the registration function in register.go
// (Task 1 Step 1 Fact #3 verifies the actual function name — update this comment
// if the function is not called RegisterAllPlugins):
plugin.Register(scorer.EvolvedScorerType, scorer.EvolvedScorerFactory)
```

**Example scheduler config YAML:**

```yaml
scorers:
  - type: evolved-routing-scorer
    name: blis-evolved-v1
    weight: 1.0
    parameters:
      queueThreshold: 128
      enabled: true
```

---

## Section 5: Feature Flag (Config Toggle)

The generated scorer MUST be disableable via config toggle (Macro Plan Objective 4).

**Implementation pattern:**

1. **Config struct field:** `Enabled bool` in parameters (see Section 4)
2. **Score() early-return:** When disabled, return `nil` (see Section 2)
3. **Behavior when disabled:** Returning `nil` from `Score()` is expected to cause the scheduler to skip this scorer entirely — no scores contribute to the routing decision, and remaining active scorers determine routing. **PARTIALLY VERIFIED:** PrecisePrefixCache.Score() returns nil in error/nil-request paths (precise_prefix_cache.go:225,240), confirming the framework encounters nil returns. However, intentional feature-flag opt-out via nil is untested; PR3 must verify nil-score handling in the framework's aggregation logic (see BC-4).
4. **Toggle mechanism:** Change `enabled: false` in the scheduler config YAML and restart the scheduler (not hot-reloadable in v1 — see Section 8)

```go
// In Score() method:
if !s.enabled {
	return nil // nil scores = scorer is inactive (UNVERIFIED — see BC-4; PR3 must confirm framework behavior)
}
```

**Required unit test (see Section 6):** Verify that when `Enabled: false`, `Score()` returns nil.

---

## Section 6: Unit Test Structure

Tests follow the table-driven pattern used by all existing scorers.

```go
package scorer_test

import (
	"context"
	"testing"

	"github.com/google/go-cmp/cmp"
	k8stypes "k8s.io/apimachinery/pkg/types"
	fwkdl "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/datalayer"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"

	"github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer"
	"github.com/llm-d/llm-d-inference-scheduler/test/utils"
)

// ANNOTATION: Test endpoint creation pattern.
// scheduling.NewEndpoint takes (metadata, metrics, attributes).
// - metadata: endpoint identity (pod name)
// - metrics: current endpoint metrics (populate fields relevant to your scorer)
// - attributes: usually nil for scorer tests (used by filter plugins)

func TestEvolvedScorer(t *testing.T) {
	// --- Test endpoint creation ---
	endpointA := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-a"}},
		&fwkdl.Metrics{WaitingQueueSize: 2}, // VERIFIED field
		nil,
	)
	endpointB := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-b"}},
		&fwkdl.Metrics{WaitingQueueSize: 0},
		nil,
	)
	endpointHeavy := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-heavy"}},
		&fwkdl.Metrics{WaitingQueueSize: 200}, // above threshold
		nil,
	)
	endpointNilMetrics := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-nil"}},
		nil, // nil metrics pointer — tests defensive nil check in Score()
		nil,
	)

	tests := []struct {
		name       string
		scorer     scheduling.Scorer
		req        *scheduling.LLMRequest
		input      []scheduling.Endpoint
		wantScores map[scheduling.Endpoint]float64
	}{
		// --- Test A: Normal scoring (BC-2) ---
		{
			name:   "scores endpoints based on load",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointA, endpointB, endpointHeavy},
			wantScores: map[scheduling.Endpoint]float64{
				endpointA:     0.4,  // 2/10 load → 0.5 * (1 - 0.2) = 0.4
				endpointB:     0.5,  // empty queue → 0.5
				endpointHeavy: 0.0,  // capped at threshold → 0
			},
		},

		// --- Test B: Disabled scorer / no-op (BC-4) ---
		{
			name:       "disabled scorer returns nil",
			scorer:     scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: false}),
			req:        &scheduling.LLMRequest{TargetModel: "test-model"},
			input:      []scheduling.Endpoint{endpointA, endpointB},
			wantScores: nil, // nil = scorer inactive
		},

		// --- Test C: Score range [0, 1] boundary (BC-9) ---
		// ANNOTATION: The placeholder formula produces scores in [0, 0.5] only.
		// This is intentional for the example (matches LoadAware's current range).
		// The evolved algorithm SHOULD use the full [0.0, 1.0] range.
		// PR3's generated scorer tests should verify scores across the full range.
		{
			name:   "scores are within [0, 1] range",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 128, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointA, endpointB, endpointHeavy},
			wantScores: map[scheduling.Endpoint]float64{
				endpointA:     0.4921875, // 2/128 → 0.5 * (1 - 0.015625)
				endpointB:     0.5,
				endpointHeavy: 0.0, // 200 > 128, capped
			},
		},

		// --- Test D: Nil metrics graceful handling (BC-9) ---
		// UNVERIFIED DEPENDENCY: This test assumes scheduling.NewEndpoint(metadata, nil, nil).GetMetrics()
		// returns nil (not a zero-value *fwkdl.Metrics{} struct). If GetMetrics() returns a zero-value
		// struct, the nil guard won't fire and the expected score changes from 0.0 to 0.5.
		// Task 1 Fact #10 requires verifying this behavior. Update wantScore if needed.
		{
			name:   "nil metrics returns zero score",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointNilMetrics},
			wantScores: map[scheduling.Endpoint]float64{
				endpointNilMetrics: 0.0, // UNVERIFIED — defensive nil guard score; see Fact #10 and Deviation Log
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := test.scorer.Score(context.Background(), nil, test.req, test.input)
			if diff := cmp.Diff(test.wantScores, got); diff != "" {
				t.Errorf("Unexpected scores (-want +got):\n%s", diff)
			}
		})
	}
}
```

**ANNOTATION — Test patterns to include for the generated scorer:**
- **Request parsing:** Test that request headers (e.g., `x-session-token`) are read correctly if the evolved algorithm uses them
- **No-op / disabled:** Test that `Enabled: false` returns nil scores (BC-4)
- **Score range:** Verify all scores in [0, 1] across diverse endpoint states
- **Zero metrics:** Verify graceful handling when all metrics are zero/default
- **Threshold boundary:** Test behavior at exactly the threshold value

---

## Section 7: ScoreEndpoints Equivalence Test Helper

> **NOTE — File placement:** This function MUST reside in `<your_scorer>.go` (the main scorer file), NOT in `<your_scorer>_test.go`. The test file uses `package scorer_test` (external test package), which cannot be imported by external packages. The Go test harness (`tools/harness/`) imports this function as `scorer.ScoreEndpoints(...)`, which requires it to be in a non-test file in `package scorer`.

This helper function bridges the scorer's `Score()` output (keyed by `scheduling.Endpoint`) to a format compatible with the Go test harness (`tools/harness/`), which is created by **PR3** (Prompt Templates + Go Harness). PR3 creates the harness; PR5 (Validation Pipeline) extends it with Suite A/B/C logic. The equivalence testing needs to compare simulation scores against production scores using the same endpoint identifiers.

```go
// ScoreEndpoints is a test helper that runs the scorer and returns results
// keyed by endpoint name (string) instead of scheduling.Endpoint.
//
// ANNOTATION: This function is called by the Go test harness (tools/harness/)
// during equivalence testing (Stage 5). The harness is created by PR3
// (Prompt Templates + Go Harness) and extended by PR5 (Validation Pipeline).
// It provides a stable interface that doesn't depend on the
// scheduling.Endpoint type's identity semantics.
//
// WARNING — CycleState constraint: This helper passes nil for cycleState.
// Generated scorers MUST NOT dereference or depend on cycleState in their
// Score() implementation. If the evolved algorithm requires inter-plugin
// shared state via CycleState, the harness and this helper must be extended
// to provide a non-nil CycleState before equivalence testing can work.
// Stage 3 LLM: if the EVOLVE-BLOCK references shared state across plugins,
// flag this as a constraint violation and escalate.
//
// Usage in harness:
//   scores := scorer.ScoreEndpoints(ctx, evolvedScorer, request, endpoints)
//   // scores["pod-a"] = 0.4, scores["pod-b"] = 0.5, etc.
//   // Compare against simulation Route() output for the same inputs.
func ScoreEndpoints(
	ctx context.Context,
	s scheduling.Scorer,
	request *scheduling.LLMRequest,
	endpoints []scheduling.Endpoint,
) map[string]float64 {
	// NOTE: nil cycleState — see WARNING above. Generated scorers must not access cycleState.
	raw := s.Score(ctx, nil, request, endpoints)
	if raw == nil {
		return nil
	}

	result := make(map[string]float64, len(raw))
	for endpoint, score := range raw {
		name := endpoint.GetMetadata().NamespacedName.String()
		if _, exists := result[name]; exists {
			// Duplicate NamespacedName would silently overwrite scores,
			// corrupting equivalence test results (e.g., Kendall-tau rank correlation).
			// Kubernetes enforces NamespacedName uniqueness in practice, but test
			// fixtures or harness bugs could produce duplicates.
			panic(fmt.Sprintf("ScoreEndpoints: duplicate endpoint name %q — input contains two endpoints with the same NamespacedName", name))
		}
		result[name] = score
	}
	return result
}
```

**ANNOTATION:** The harness creates `scheduling.Endpoint` objects from test tuples (mapping sim `RouterState` fields to production `Metrics` fields per the mapping artifact), calls `ScoreEndpoints`, and compares the output against simulation `Route()` results. The comparison uses Kendall-tau rank correlation (Suite A threshold: 0.8) and numeric fidelity (1e-6 abs or 1% relative).

---

## Section 8: Hot-Reload Documentation

**v1 status:** Hot-reload is NOT supported. The scorer config is read at initialization time (factory function). Changing the `enabled` field or any parameter requires a scheduler restart.

```go
// ANNOTATION: v1 — no hot-reload support.
// Config is parsed once in the factory function (Section 4) and stored in the struct.
// There is no Reconfigure() method. To change parameters, restart the scheduler.
//
// The EvolvedScorer struct fields (enabled, queueThreshold) are set at construction
// time and never modified after. This is safe for concurrent Score() calls.
```

**Toggle procedure (v1):**
1. Edit scheduler config YAML: set `enabled: false` under the scorer's parameters
2. Restart the scheduler process
3. The scorer's factory function re-reads the config and constructs the scorer with `Enabled: false`
4. `Score()` returns nil for all requests → scheduler uses remaining active scorers

**Future consideration (v2+):** If the framework adds a `Reconfigure(rawParameters json.RawMessage)` method to the plugin interface, the scorer could support hot-reload by re-parsing the config and updating the `enabled` field atomically. This would eliminate the restart requirement.

---

## Stage 3 Output Validation

> **Before handing off to Stage 4**, Stage 3 MUST verify all of the following. If any check fails, Stage 3 generation is incomplete — fix and re-check before proceeding.

1. **No PLACEHOLDER markers:** `grep -r "PLACEHOLDER" <your_scorer>.go <your_scorer>_test.go` must return zero matches. Any remaining PLACEHOLDER indicates incomplete generation.
2. **Do NOT compile:** Compilation (`go build`) is deferred to Stage 4, which has the full Go module environment. Stage 3 should NOT attempt `go build` — failure in an environment without Go module setup would produce misleading errors.
3. **Structural invariants — verify these are present in the generated code:**
   - Import paths unchanged from template (same `sigs.k8s.io/...` paths)
   - Type assertion: `var _ scheduling.Scorer = &EvolvedScorer{}` present
   - Factory function registered: `plugin.Register(scorer.EvolvedScorerType, scorer.EvolvedScorerFactory)` added to `register.go`
   - UNVERIFIED metric fields remain commented-out unless explicitly confirmed by PR3's field verification step
   - `ScoreEndpoints` helper function present in `<your_scorer>.go` (not in `_test.go`)
4. **Test structure:** `<your_scorer>_test.go` contains at minimum: (a) table-driven scoring test, (b) disabled/no-op test returning nil, (c) nil-metrics graceful handling test
````

## K-2: Updates to `docs/transfer/blis_to_llmd_mapping.md`

**Update 1 — Document-level annotation (line 4 of the file):**

Replace the blanket UNVERIFIED statement on line 4:
```
**Target submodule:** llm-d-inference-scheduler (UNVERIFIED — submodule not initialized; field names and interface signatures in this document are based on design knowledge, not source verification)
```

With a partial-verification annotation:
```
**Target submodule:** llm-d-inference-scheduler (PARTIALLY VERIFIED — Scorer Interface Reference section verified against submodule at commit 091312c, PR2 Task 1. Signal Mapping Table field names and other sections remain UNVERIFIED pending PR3 submodule initialization.)
```

**Update 2 — Replace the "Scorer Interface Reference" section with:**

```markdown
## Scorer Interface Reference

> **Verified against** llm-d-inference-scheduler at commit `091312c` (2026-03-09) — signatures confirmed indirectly via LoadAware's `var _ scheduling.Scorer = &LoadAware{}` type assertion and matching method signatures.
> **Interface source:** The `scheduling.Scorer` interface is defined in the external dependency `sigs.k8s.io/gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`, not in the llm-d-inference-scheduler repository itself. Verification reads LoadAware's implementation of the interface as the ground truth.

Target system: `llm-d-inference-scheduler` (gateway-api-inference-extension framework)

**Interface (VERIFIED):** `scheduling.Scorer` from `sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling`

```go
type Scorer interface {
    Score(ctx context.Context, cycleState *CycleState, request *LLMRequest, endpoints []Endpoint) map[Endpoint]float64
    TypedName() plugin.TypedName
    Category() ScorerCategory
}
```

**Factory pattern (VERIFIED):** `pkg/plugins/scorer/load_aware.go:30`
```go
func LoadAwareFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)
```
> **Note:** Each scorer has its own factory function (e.g., `LoadAwareFactory`, `SessionAffinityFactory`). The PR3 evolved scorer should use `EvolvedScorerFactory` following this same signature pattern.

**Registration (VERIFIED):** `pkg/plugins/register.go`
```go
plugin.Register(scorer.LoadAwareType, scorer.LoadAwareFactory)
```

**Scorer categories (VERIFIED):**
- `scheduling.Distribution` — used by LoadAware, ActiveRequest, NoHitLRU
- `scheduling.Affinity` — used by SessionAffinity, PrecisePrefixCache

**Existing scorers (VERIFIED):** LoadAware, ActiveRequest, SessionAffinity, PrecisePrefixCache, NoHitLRU

**Metric access (PARTIALLY VERIFIED):**
- `endpoint.GetMetrics().WaitingQueueSize` — **VERIFIED** (load_aware.go:87)
- `endpoint.GetMetrics().RunningQueueSize` — **UNVERIFIED** (not used in submodule; assumed in external Metrics struct)
- `endpoint.GetMetrics().RunningRequestCount` — **UNVERIFIED** (not used in submodule; assumed in external Metrics struct)
- `endpoint.GetMetrics().KVCacheUsagePercent` — **UNVERIFIED** (not used in submodule; PrecisePrefixCache uses ZMQ-based indexer instead)

**Config:** YAML-based with scorer name, type, weight, and optional parameters (JSON blob parsed by factory).

> **Note for PR3:** This section is now partially verified. PR3 MUST initialize the submodule, run `go mod download`, and inspect the `fwkdl.Metrics` struct definition to confirm UNVERIFIED field names before generating scorer code.
```

## K-3: Updates to `docs/transfer/README.md`

Add after the "Prompt Template Contract (PR3)" section:

```markdown
## Scorer Template (PR2)

**Location:** `docs/transfer/scorer_template.go.md`
**Version:** 1.0
**Pinned commit:** `091312c333a50e94f5e60a2ca2926e8442eeffa9`

The scorer template is an annotated example showing llm-d-inference-scheduler plugin conventions. Stage 3's LLM uses it as the structural reference for generating production scorer code.

**PR3 obligations for scorer template:**
1. **Compilation check:** PR3 adds `tools/check_scorer_template.sh` that extracts Go code blocks from the template, compiles them against the current submodule HEAD, and fails if compilation fails. This provides automated staleness detection.
2. **Metric field verification:** Before generating code, PR3 MUST initialize the llm-d-inference-scheduler submodule, locate the `fwkdl.Metrics` struct definition, and confirm that UNVERIFIED fields (`RunningQueueSize`, `RunningRequestCount`, `KVCacheUsagePercent`) exist. If any field does not exist, PR3 must update both the mapping artifact and the scorer template with the correct field names.
3. **CacheHitRate access path:** The PrecisePrefixCache scorer uses a ZMQ-based KV cache indexer, not a simple `GetMetrics()` field. PR3 must determine the correct access path for cache hit rate information by reading the PrecisePrefixCache implementation.
4. **Placeholder replacement validation:** The template's `Score()` body contains a `PLACEHOLDER` comment marking the example scoring logic. After code generation, PR3 MUST verify that no `PLACEHOLDER` markers remain in the generated scorer code. If any remain, the generation is incomplete.
```
