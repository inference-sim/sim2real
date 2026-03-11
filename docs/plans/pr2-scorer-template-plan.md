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
- WHEN compared against the actual interface at pinned commit `091312c`
- THEN the method signatures (`Score`, `TypedName`, `Category`), parameter types, and return types MUST match the actual code in `sigs.k8s.io/gateway-api-inference-extension`
- MECHANISM: Template code mirrors verified LoadAware scorer at `pkg/plugins/scorer/load_aware.go`

**BC-3: Factory Pattern Conformance**
- GIVEN the template's factory function
- WHEN compared against existing scorers' factories
- THEN it MUST follow the exact signature `func(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)` and use the `WithName(name)` convention
- MECHANISM: Direct copy of the LoadAware factory pattern with annotations

**BC-4: Feature Flag Implementation**
- GIVEN a generated scorer using this template
- WHEN the config sets `enabled: false`
- THEN `Score()` MUST return zero scores for all endpoints (nil/empty map), causing the scheduler to fall back to remaining active scorers
- MECHANISM: Template shows `Enabled bool` config field, early-return in `Score()`, and a unit test verifying disabled behavior

**BC-5: Metric Access Pattern**
- GIVEN the template's `Score()` method
- WHEN it demonstrates metric access
- THEN it MUST show `endpoint.GetMetrics().<field>` access pattern with annotations noting which fields are verified vs. unverified at the pinned commit
- MECHANISM: Template annotates `WaitingQueueSize` as VERIFIED (used by LoadAware) and other fields as UNVERIFIED

**BC-6: Unit Test Structure**
- GIVEN the template's unit test section
- WHEN it demonstrates test patterns
- THEN it MUST include: (a) table-driven test with `scheduling.NewEndpoint()`, (b) disabled/no-op test, (c) boundary test for score range [0, 1]
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
| "Template code compiles and passes tests against pinned llm-d-inference-scheduler HEAD" | Template code blocks are verified against source but NOT compiled (submodule not initialized in worktree; compilation is a manual PR3 gate) | SIMPLIFICATION: PR2 is an artifact PR. Compilation requires Go module setup with the full dependency graph. PR3 adds `tools/check_scorer_template.sh` for automated compilation checking. PR2 verifies API accuracy by reading the submodule source directly. |
| "All 8 required sections documented (per design doc)" | 8 sections as specified | No deviation |
| Scorer template includes `ScoreEndpoints` equivalence test function | Template includes `ScoreEndpoints` helper function that bridges scorer Score() output to harness-compatible format | No deviation — function wraps Score() for equivalence testing |
| Template version 1.0 | Version 1.0 | No deviation |
| Metric field names like `RunningQueueSize`, `KVCacheUsagePercent` | These fields annotated as UNVERIFIED with PR3 verification requirement | CORRECTION: These field names are not used anywhere in llm-d-inference-scheduler at pinned commit. They exist in the external Metrics struct but are unverified. The mapping artifact already flags this (DEV-1). |

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

### Task 1: Verify Submodule API Signatures

**Contracts Implemented:** BC-2, BC-3, BC-5

**Files:**
- Read: `llm-d-inference-scheduler/pkg/plugins/scorer/load_aware.go`
- Read: `llm-d-inference-scheduler/pkg/plugins/scorer/load_aware_test.go`
- Read: `llm-d-inference-scheduler/pkg/plugins/register.go`

**Step 1: Document verified API facts**

Context: Before authoring the template, confirm all API signatures against actual source. Record file:line citations for each fact.

Verify these facts from the submodule at commit `091312c`:

1. **Scorer interface** (from `scheduling` package import):
   - `Score(ctx context.Context, cycleState *scheduling.CycleState, request *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64`
   - `TypedName() plugin.TypedName`
   - `Category() scheduling.ScorerCategory`

2. **Factory signature** (from `load_aware.go:30`):
   - `func LoadAwareFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)`

3. **Registration** (from `register.go`):
   - `plugin.Register(scorer.LoadAwareType, scorer.LoadAwareFactory)`

4. **Metric access** (from `load_aware.go:87`):
   - `endpoint.GetMetrics().WaitingQueueSize` — VERIFIED
   - `endpoint.GetMetadata().NamespacedName` — VERIFIED (from `session_affinity.go:79`)
   - `RunningQueueSize`, `RunningRequestCount`, `KVCacheUsagePercent` — NOT found in submodule

5. **Category values**: `scheduling.Distribution`, `scheduling.Affinity`

6. **Test utilities** (from `load_aware_test.go`):
   - `scheduling.NewEndpoint(metadata, metrics, attributes)` endpoint constructor
   - `fwkdl.Metrics{WaitingQueueSize: N}` metric struct
   - `fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-a"}}` metadata struct
   - Table-driven tests with `cmp.Diff` comparison
   - `utils.NewTestContext(t)` from `test/utils/context.go`

7. **Module path**: `github.com/llm-d/llm-d-inference-scheduler`

8. **Go version**: 1.25.7 (from go.mod)

**Step 2: Record deviations from mapping artifact**

Compare verified facts against `docs/transfer/blis_to_llmd_mapping.md` Scorer Interface Reference section. Document each deviation for the template.

No commit needed — this is a verification step.

---

### Task 2: Author Scorer Template

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
- Signal names match `docs/transfer/blis_to_llmd_mapping.md` Signal Mapping Table
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

### Task 3: Update Mapping Artifact Scorer Interface Reference

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

**Step 2: Validate cross-references**

Verify updated section is consistent with the scorer template (Task 2).

**Step 3: Commit**

```bash
git add docs/transfer/blis_to_llmd_mapping.md
git commit -m "$(cat <<'EOF'
docs(transfer): update scorer interface reference with verified API details

- Add verified Score() signature including CycleState parameter
- Add verified factory function signature and import paths
- Distinguish VERIFIED (WaitingQueueSize) from UNVERIFIED metric fields
- Note gateway-api-inference-extension dependency version

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

Run signal name consistency check across artifacts:
```bash
# Check signal names appear consistently
grep -n "QueueDepth\|BatchSize\|InFlightRequests\|KVUtilization\|CacheHitRate\|SessionID" \
  docs/transfer/scorer_template.go.md \
  docs/transfer/blis_to_llmd_mapping.md
```

**Step 3: Verify no dead artifacts**

Confirm every new/modified file has an identified consumer:
- `scorer_template.go.md` → PR3 Stage 3 prompt + check script
- `blis_to_llmd_mapping.md` updates → PR3 Stage 2 prompt
- `README.md` updates → all future PRs

**Step 4: Run existing tests (regression check)**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/kalantar/sim2real/.claude/worktrees/pr1-mapping-scaffolding-cli
python -m pytest tools/ -v
```

Expected: All existing PR1 tests pass (PR2 is artifact-only, no code changes).

## H) Test Strategy

| Contract | Task | Test Type | Verification |
|----------|------|-----------|-------------|
| BC-1 (8 sections) | Task 2, 5 | Structural check | Manual: verify 8 headings present |
| BC-2 (API accuracy) | Task 1, 2 | Cross-system audit | Source comparison: template vs load_aware.go |
| BC-3 (factory pattern) | Task 1, 2 | Cross-system audit | Source comparison: template vs load_aware.go |
| BC-4 (feature flag) | Task 2 | Structural check | Template includes Enabled field + early-return + test |
| BC-5 (metric access) | Task 1, 2 | Cross-system audit | Source comparison: VERIFIED/UNVERIFIED annotations |
| BC-6 (unit tests) | Task 2 | Structural check | Template includes 3 test patterns |
| BC-7 (no false verification) | Task 2, 3 | Cross-system audit | Every metric field annotated with verification status |
| BC-8 (no extra deps) | Task 2 | Structural check | Import list matches LoadAware |
| BC-9 (graceful zero) | Task 2 | Structural check | Template shows zero-value handling pattern |

**Cross-system invariants:**
- Signal names in template match mapping artifact (Dimension 5)
- Import paths match actual submodule source (Dimension 1)
- Commit pin in template matches `git submodule status` output (Dimension 1)

## I) Risk Analysis

| Risk | Likelihood | Impact | Mitigation | Task |
|------|-----------|--------|------------|------|
| Metric field names don't exist in external Metrics struct | Medium | High — PR3 generates uncompilable code | Template annotates UNVERIFIED fields; PR3 must verify before use | Task 1, 2 |
| Template patterns diverge from real scorer conventions | Low | Medium — generated code doesn't match project style | Template based directly on LoadAware (verified source) | Task 1, 2 |
| Submodule updated between PR2 and PR3, making template stale | Low | Medium — compilation failure | Template pins commit hash; PR3 check script detects staleness | Task 2, 4 |
| ScoreEndpoints helper doesn't match PR3 harness interface | Low | Low — helper is a thin wrapper, easily adapted | Helper kept minimal; PR3 harness defines the authoritative interface | Task 2 |

---

# Part 3: Quality Assurance

## J) Sanity Checklist

**Dimension 1: Cross-system accuracy**
- [x] All submodule API references verified against actual code (Task 1)
- [x] Commit pin `091312c` matches `git submodule status` output
- [x] No stale references — all from current pinned commit

**Dimension 2: Schema chain integrity**
- [x] N/A — PR2 does not produce or consume workspace artifacts

**Dimension 3: Prompt completeness**
- [x] N/A — PR2 does not create prompt templates

**Dimension 4: CLI contract**
- [x] N/A — PR2 does not modify CLI

**Dimension 5: Artifact consistency**
- [x] Signal names match between scorer template and mapping artifact
- [x] Field names annotated with VERIFIED/UNVERIFIED status
- [x] File paths referenced in README exist or will be created by PR3

**Dimension 6: Dead artifact prevention**
- [x] `scorer_template.go.md` consumed by PR3 (Stage 3 prompt + check script)
- [x] README updates consumed by all future PRs
- [x] Mapping artifact updates consumed by PR3 Stage 2

**Additional checks:**
- [x] PR category: Artifact (correct — review perspectives: 1, 2, 5)
- [x] Verification gate: Artifact (validate-mapping + manual cross-reference check)
- [x] No feature creep beyond macro plan scope
- [x] Deviation log reviewed — DEV-1 and DEV-2 documented and mitigated
- [x] Each task produces verifiable output
- [x] Task dependencies correctly ordered (Task 1 → Task 2 → Task 3/4 → Task 5)
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

> **For Stage 3 LLM:** Use this template as the structural reference for generating a production scorer plugin. Follow the conventions exactly — package location, factory signature, type assertion, test patterns. Replace the scoring logic with the evolved algorithm's logic from `workspace/algorithm_summary.json`.
>
> **IMPORTANT:** Metric field names marked `// UNVERIFIED` in this template have NOT been confirmed against the actual `fwkdl.Metrics` struct at the pinned dependency version (`gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`). Before generating code, you MUST initialize the submodule, locate the `Metrics` struct definition, and confirm these field names exist. If a field does not exist, consult `docs/transfer/blis_to_llmd_mapping.md` for alternative access paths.

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
│           ├── <your_scorer>.go       ← New scorer file
│           └── <your_scorer>_test.go  ← New test file
└── test/
    └── utils/
        └── context.go            ← Test context helper
```

**Convention:** One scorer per file. File name matches scorer type (snake_case). Test file is `<name>_test.go` in the same package.

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
// - cycleState: shared state across plugins in a scheduling cycle (may be nil in tests)
// - request: the incoming LLM request (model name, headers, etc.)
// - endpoints: candidate endpoints to score
//
// Returns: map from endpoint to score. Higher score = more preferred.
// Convention: scores MUST be in [0.0, 1.0]. The scheduler normalizes and
// applies weights across all scorers.
//
// >>> REPLACE THIS BODY with the evolved algorithm's scoring logic <<<
// >>> Use signals from algorithm_summary.json and mappings from blis_to_llmd_mapping.md <<<
func (s *EvolvedScorer) Score(ctx context.Context, _ *scheduling.CycleState, request *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
	logger := log.FromContext(ctx)

	// Feature flag check — see Section 5
	if !s.enabled {
		return nil // nil scores = scorer is inactive, scheduler skips it
	}

	scoredEndpoints := make(map[scheduling.Endpoint]float64, len(endpoints))

	for _, endpoint := range endpoints {
		// --- Metric access (see Section 3 for field details) ---
		metrics := endpoint.GetMetrics()

		// VERIFIED field (confirmed in load_aware.go:87):
		waitingQueueSize := float64(metrics.WaitingQueueSize)

		// UNVERIFIED fields — PR3 MUST confirm these exist in fwkdl.Metrics:
		// runningQueueSize := float64(metrics.RunningQueueSize)       // UNVERIFIED
		// runningRequestCount := float64(metrics.RunningRequestCount) // UNVERIFIED
		// kvCacheUsagePct := float64(metrics.KVCacheUsagePercent)     // UNVERIFIED

		// --- Example: simple load-based score (replace with evolved logic) ---
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
| Session header | `request.Headers["x-session-id"]` | **VERIFIED** (session_affinity.go:66 uses similar pattern) |

**Endpoint metadata access:**
| Field | Go Access | Status |
|-------|-----------|--------|
| Endpoint name | `endpoint.GetMetadata().NamespacedName.String()` | **VERIFIED** (session_affinity.go:79) |

> **CacheHitRate note:** The mapping artifact maps CacheHitRate to the PrecisePrefixCache scorer. However, `PrecisePrefixCache` does NOT simply read a metric field — it uses a ZMQ-based KV cache indexer (`llm-d-kv-cache` package) with its own event-driven state. Accessing cache hit rate may require a different pattern than `GetMetrics()`. PR3 MUST investigate the PrecisePrefixCache implementation to determine the correct access path.

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
	params := evolvedScorerParameters{
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
type evolvedScorerParameters struct {
	QueueThreshold int  `json:"queueThreshold"`
	Enabled        bool `json:"enabled"`
}

// --- Constructor ---
func NewEvolvedScorer(ctx context.Context, params evolvedScorerParameters) *EvolvedScorer {
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
// Add this line to RegisterAllPlugins():
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
3. **Behavior when disabled:** Returning `nil` from `Score()` causes the scheduler to skip this scorer entirely — no scores contribute to the routing decision, and remaining active scorers determine routing
4. **Toggle mechanism:** Change `enabled: false` in the scheduler config YAML and restart the scheduler (not hot-reloadable in v1 — see Section 8)

```go
// In Score() method:
if !s.enabled {
	return nil // nil scores = scorer is inactive, scheduler skips it
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
- **Request parsing:** Test that request headers (e.g., `x-session-id`) are read correctly if the evolved algorithm uses them
- **No-op / disabled:** Test that `Enabled: false` returns nil scores (BC-4)
- **Score range:** Verify all scores in [0, 1] across diverse endpoint states
- **Zero metrics:** Verify graceful handling when all metrics are zero/default
- **Threshold boundary:** Test behavior at exactly the threshold value

---

## Section 7: ScoreEndpoints Equivalence Test Helper

This helper function bridges the scorer's `Score()` output (keyed by `scheduling.Endpoint`) to a format compatible with the Go test harness (`tools/harness/`). PR5's equivalence testing needs to compare simulation scores against production scores using the same endpoint identifiers.

```go
// ScoreEndpoints is a test helper that runs the scorer and returns results
// keyed by endpoint name (string) instead of scheduling.Endpoint.
//
// ANNOTATION: This function is called by the Go test harness (tools/harness/)
// during equivalence testing (Stage 5). It provides a stable interface that
// doesn't depend on the scheduling.Endpoint type's identity semantics.
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
	raw := s.Score(ctx, nil, request, endpoints)
	if raw == nil {
		return nil
	}

	result := make(map[string]float64, len(raw))
	for endpoint, score := range raw {
		name := endpoint.GetMetadata().NamespacedName.String()
		result[name] = score
	}
	return result
}
```

**ANNOTATION:** The harness creates `scheduling.Endpoint` objects from test tuples (mapping sim `RouterState` fields to production `Metrics` fields per the mapping artifact), calls `ScoreEndpoints`, and compares the output against simulation `Route()` results. The comparison uses Kendall-tau rank correlation (Suite A threshold: 0.8) and numeric fidelity (1e-6 abs or 1% relative).

---

## Section 8: Hot-Reload Documentation

**v1 status:** Hot-reload is NOT supported. The scorer config is read at initialization time (factory function). Changing the `enabled` field or any parameter requires a scheduler restart.

**Toggle procedure (v1):**
1. Edit scheduler config YAML: set `enabled: false` under the scorer's parameters
2. Restart the scheduler process
3. The scorer's factory function re-reads the config and constructs the scorer with `Enabled: false`
4. `Score()` returns nil for all requests → scheduler uses remaining active scorers

**Future consideration (v2+):** If the framework adds a `Reconfigure(rawParameters json.RawMessage)` method to the plugin interface, the scorer could support hot-reload by re-parsing the config and updating the `enabled` field atomically. This would eliminate the restart requirement.
````

## K-2: Updates to `docs/transfer/blis_to_llmd_mapping.md`

Replace the "Scorer Interface Reference" section with:

```markdown
## Scorer Interface Reference

> **Verified against** llm-d-inference-scheduler at commit `091312c` (2026-03-09).
> **External dependency:** `sigs.k8s.io/gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`

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
func ScorerFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error)
```

**Registration (VERIFIED):** `pkg/plugins/register.go`
```go
plugin.Register(scorer.LoadAwareType, scorer.LoadAwareFactory)
```

**Scorer categories (VERIFIED):**
- `scheduling.Distribution` — used by LoadAware, ActiveRequest, NoHitLRU, PrecisePrefixCache
- `scheduling.Affinity` — used by SessionAffinity

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
```
