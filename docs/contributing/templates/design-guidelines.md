# sim2real Design Guidelines: Principles for Cross-System Transfer Pipeline Design

**Date:** 2026-03-11
**Status:** Draft (pending review)
**Species:** System Overview

## 1. Purpose & Scope

This document serves two audiences:
1. **Design doc authors** (human or Claude) — guidance on writing design docs that stay durable and useful across the project lifecycle
2. **Pipeline developers** — guidance on extending sim2real with new components that fit the architecture and enable parallel development

**What this document IS:**
- A target architecture specification for the sim2real transfer pipeline
- A set of principles grounded in cross-system methodology and v3 design experience
- A reference for evaluating whether a design doc or a new component meets sim2real's quality bar

**What this document is NOT:**
- Not an implementation plan (work happens in separate PRs, each following `docs/contributing/pr-workflow.md` [1])
- Not a replacement for CLAUDE.md (which captures engineering rules and code-level patterns)
- Not a replacement for the micro-plan template (which captures PR-level planning structure)

### Relationship to Existing Docs

```
Design Guidelines (this doc)          <- Principles, target architecture, extension framework
    | informs

+---------------------------------------------------+
|  Design Docs  <->  Macro Plan                      |
|       |                |                           |
|  Micro Plans     Micro Plans                       |
|       |                |                           |
|  CLAUDE.md (updated by each PR)                    |
+---------------------------------------------------+
```

| Scenario | Path |
|---|---|
| Large multi-PR feature | Design doc -> Macro plan -> Micro plans per PR |
| Single-PR feature | Design doc -> Micro plan directly |
| New transfer type | Design doc -> Macro plan (6-stage pipeline per type) -> Micro plans per PR |
| Infrastructure change | Issue or design doc -> Micro plan directly |

**Key distinction:** This document describes the *target state* and *design principles*. CLAUDE.md describes the *current state* and *implementation rules*. Where they diverge, this document is aspirational and CLAUDE.md is authoritative for today's code.

---

## 2. Cross-System Pipeline Foundations

sim2real is a 6-stage transfer pipeline (Extract -> Translate -> Generate -> Test -> Validate -> PR). Design docs for sim2real should be informed by cross-system methodology: bridging simulation models with production systems requires explicit contracts, verified mappings, and validation at every boundary.

**Core Principle: Cross-system boundaries are verified, not trusted.** Every mapping, schema, and submodule reference must be defensible against actual code — not cached knowledge, not documentation, not memory.

### 2.1 Scoping

Before including a component in the pipeline, evaluate it against these six criteria:

1. Does it affect the accuracy of transfer validation for the target analysis questions (Q1: Does the transferred algorithm produce equivalent decisions? Q2: Under what workload conditions does equivalence hold? Q3: What is the performance cost of the transfer?)?
2. What accuracy level is needed for the validation to be useful?
3. Can the component's data requirements be satisfied (submodule APIs, mapping artifact, production metrics)?
4. What is the cost of inclusion — prompt complexity, schema surface, maintenance?
5. What breaks if we omit it? (If removing it changes validation results by less than the noise floor, defer it)
6. What is the simplest version that answers the same questions? (Start minimal, extend only with evidence)

These criteria operationalize "YAGNI" for cross-system transfer. Example: the v3 design chose a single mapping artifact per transfer type over a normalized database because it answered the same questions with far less schema surface.

### 2.2 Inter-Stage Contract Design

- Contracts are defined by JSON schemas in `tools/schemas/`
- Each stage reads predecessor artifacts and writes its own outputs
- No ambient state — all communication through `workspace/` artifacts
- Schema validation at stage entry (required fields, types, non-null)
- Every schema must have exactly one producer and at least one consumer — schemas without consumers are dead weight (see Section 6.1)

### 2.3 Submodule Management

- Submodules (`llm-d-inference-scheduler`, `llm-d-benchmark`, `inference-sim`) are external dependencies
- Code changes happen on local branches within submodules — never modify existing files on main
- The mapping artifact pins a "last verified against" commit hash for each submodule
- Staleness guard checks pin freshness at Stage 2 (Translate) entry
- When a submodule advances, the mapping artifact must be reviewed against the diff between the old and new pinned commits

### 2.4 Validation & Verification Suites

sim2real uses a hierarchy of validation suites, each building on the prior:

| Suite | Purpose | What It Validates |
|---|---|---|
| **Suite A (Fidelity)** | Does the transferred algorithm produce equivalent decisions? | Signal mapping accuracy, generated code correctness |
| **Suite B (Staleness)** | Are submodule API references still current? | Commit pin freshness, API signature drift |
| **Suite C (Concurrency)** | Does equivalence hold under concurrent workloads? | Multi-request scenarios, race conditions, ordering effects |
| **Cluster Benchmarks** | Does the transfer work in production? | End-to-end performance, real workload validation |

Design docs must specify which suites validate any new component. The validation hierarchy is: A -> B -> C -> benchmarks (each depends on prior passing).

### 2.5 Prompt Template Design

Prompts are the primary pipeline artifact — they control LLM behavior at each stage and are the mechanism through which transfer knowledge is applied.

**Required sections for every prompt template:**
1. **Prerequisites** — what artifacts must exist before this prompt is used?
2. **Validation steps** — what checks does the LLM perform on inputs before proceeding?
3. **Halt conditions** — what conditions cause the LLM to stop and report rather than produce incorrect output?
4. **Expected outputs** — what artifacts does this prompt produce, in what format?

**Template metadata:**
- YAML front-matter with stage number, transfer type, version, and `pipeline_commit` hash
- Template stability: `pipeline_commit` is tracked so drift between the prompt and the pipeline version that generated it can be detected

### 2.6 Cross-System Design Review Checklist

Every sim2real design doc must answer these questions:

| Question | Principle |
|---|---|
| What analysis questions does this feature help answer? | Scoping |
| What submodule APIs does this feature depend on? | Submodule management |
| What workspace artifacts are read/written? | Inter-stage contracts |
| What JSON schemas need to be created or updated? | Schema-first design |
| How will cross-system accuracy be verified? | Validation |
| Does this introduce new prompt templates? With all 4 required sections? | Prompt template design |
| What is the simplest version that answers the same questions? | Scoping |
| Does this affect any existing validation suite? | V&V suites |
| What happens when the feature is disabled? (no-op default) | Submodule isolation |
| How will fidelity be validated in production benchmarks? | Cluster benchmarks |

---

## 3. Design Doc Guidelines

### 3.1 The Staleness Test

Before including any content in a design doc, apply this test:

> *"If the implementation changes this detail during micro-planning, will the design doc silently mislead future readers?"*

- **Durable content** (include): invariants, mapping decisions, fidelity trade-offs, extension points described behaviorally, decision rationale with alternatives considered, analysis questions the component answers.
- **Fragile content** (exclude): Python function signatures, Go struct field lists, file paths with line numbers, specific JSON field names that may be renamed, exact CLI argument names.

The dividing line: **describe what crosses a boundary and why, not how the boundary is implemented.**

### 3.2 Four Design Doc Species

Not all design docs serve the same purpose. Choose the right species based on scope:

| Species | When to Use | Structure | Example |
|---|---|---|---|
| **Decision Record** | Single-PR architectural choices that need trade-off analysis | Numbered decisions, each with Problem / Decision / Rationale / Alternatives | Mapping artifact format decision |
| **Specification** | New component with precise behavioral requirements | Behavioral contracts, input/output schemas, validation criteria | Scorer template specification |
| **Problem Analysis** | Refactoring motivated by identified friction or bugs | Extension scenario analysis, anti-pattern catalog with evidence, phased fix plan | Staleness guard redesign |
| **System Overview** | Multi-PR feature spanning multiple components | Concept model, component interactions, stage contracts, phased roadmap | v3 transfer pipeline design |

A design doc should declare its species at the top so readers know what to expect.

### 3.3 Required Sections (All Species)

Every sim2real design doc, regardless of species, must include:

1. **Motivation** — What problem does this solve? What can't users do today? (2-5 sentences, no jargon)
2. **Scope** — What's in, what's explicitly out, what's deferred to later
3. **Mapping Decisions** — What submodule concepts are mapped, simplified, and deliberately omitted (table format per Section 2.1)
4. **Invariants** — What must always hold after this design is implemented? What must never happen? (Named: INV-1, INV-2, ...)
5. **Decisions with Trade-offs** — For each non-obvious choice: what alternatives were considered, why this one won, what breaks if it's wrong
6. **Extension Points** — Where do future extensions plug in? What is the default behavior? What would a non-default look like?
7. **Validation Strategy** — How will correctness be verified (which suites?) and fidelity be validated (against what production data?)
8. **Cross-System Checklist** — Completed checklist from Section 2.6

### 3.4 Prohibited Content

Do NOT include in design docs (with rationale from project experience):

| Content | Why Not | What to Write Instead |
|---|---|---|
| Python function signatures or Go struct definitions | Diverge during implementation as contracts are refined | Describe what data crosses the boundary and its semantics |
| Method implementations or code blocks | Changed during micro-planning in every PR | Describe the behavioral contract (GIVEN/WHEN/THEN) |
| File paths with line numbers | Stale after any refactoring | Name the component and its responsibility |
| Specific JSON field names | Renamed during schema iteration | Describe the concept ("latency signal combining TTFT and TBT") |
| Exact CLI argument syntax | Refined during implementation | Describe the command's purpose, inputs, and outputs |

**Exception:** Decision Records (species 1) may include brief code snippets when the decision IS about a specific implementation choice (e.g., "use JSON Schema draft-07 not draft-2020-12 for tool compatibility"). Keep these minimal.

### 3.5 Abstraction Levels Across Document Tiers

| Content Type | Design Doc | Macro Plan | Micro Plan |
|---|---|---|---|
| Analysis questions (Q1-Q3) | Define with scope | Reference | N/A |
| Mapping decisions (mapped/simplified/omitted) | Define with justification | Summarize | N/A |
| Component boundaries (behavioral) | Define contract | Reference + annotate per-PR | Implement |
| JSON schemas | No (describe semantics) | Schema names + field inventory | Full schema |
| File paths | No | Inventory + per-PR | Exact `file:line` |
| Submodule API references | Behavioral description | Pinned commit + signature | Exact code |

---

## 4. Pipeline Component Model

### 4.1 Two-Layer Architecture

sim2real is organized as two layers:

**Layer 1: Pipeline Infrastructure** — shared across all transfer types
- CLI tools (`transfer_cli.py`) — mechanical pipeline tasks: extract, validate, schema-check
- JSON schemas (`tools/schemas/`) — contract definitions for all workspace artifacts
- Go test harness (`tools/harness/`) — equivalence testing using inference-sim
- Workspace management — directory structure, artifact lifecycle, cleanup

**Layer 2: Transfer-Type Components** — specific to each transfer type (routing, admission, priority)
- Mapping artifact — bridge between simulation and production concepts
- Prompt templates — LLM instructions per stage
- Scorer template — target system plugin conventions
- Input artifact conventions — how each transfer type structures its workspace

The infrastructure layer provides the execution substrate. Transfer-type components define *what* is being transferred. The infrastructure never contains algorithm-specific logic; transfer-type components never manage workspace structure or schema validation directly.

### 4.2 Component Model

| Component | Responsibility | Language | Location |
|---|---|---|---|
| CLI tools | Mechanical pipeline tasks (extract, validate, schema-check) | Python | `tools/transfer_cli.py` |
| Go test harness | Equivalence testing using inference-sim | Go | `tools/harness/` |
| Prompt templates | LLM instructions per stage | Markdown | `prompts/` |
| Mapping artifact | Bridge between sim and prod concepts | Markdown | `docs/transfer/<type>/` |
| Scorer template | Target system plugin conventions | Markdown | `docs/transfer/<type>/` |
| JSON schemas | Contract definitions for workspace artifacts | JSON Schema | `tools/schemas/` |
| Workspace artifacts | Inter-stage data passed through the pipeline | JSON/Markdown | `workspace/` |

### 4.3 Component Contract Template

Every component (current or target) is defined by this contract:

1. **Reads** — what artifacts/APIs does it consume?
2. **Writes** — what artifacts does it produce?
3. **Validates** — what checks does it perform on inputs?
4. **Halts on** — what conditions cause it to stop and report?
5. **Schema** — what JSON schema defines its output?
6. **Extension friction** — how many files must change to add a new variant of this component?

Example — Stage 2 (Translate) contract:

| Aspect | Contract |
|---|---|
| **Reads** | Mapping artifact (`docs/transfer/<type>/mapping.md`), Stage 1 extraction output (`workspace/extract.json`), submodule source at pinned commit |
| **Writes** | Translation artifact (`workspace/translate.json`) containing signal-by-signal mapping with code references |
| **Validates** | Mapping artifact commit pin matches `git submodule status`, all signals in mapping have corresponding extraction entries |
| **Halts on** | Commit pin mismatch > 10 commits behind, missing signal in extraction output, submodule API signature changed since pin |
| **Schema** | `tools/schemas/translate.schema.json` |
| **Extension friction** | 2 files to add a new signal (mapping artifact + schema update) |

### 4.4 Real-System Correspondence

Pipeline components map to concepts in the three submodules:

| Pipeline Component | inference-sim | llm-d-inference-scheduler | llm-d-benchmark |
|---|---|---|---|
| Mapping artifact | Signal definitions, state variables | API types and signatures | Workload configs |
| Scorer template | N/A | Plugin conventions, scorer interface | N/A |
| Go test harness | Simulation engine | N/A | Benchmark framework |
| Workspace artifacts | N/A | Generated plugin code | Benchmark configs |

The design implication: mapping artifacts must be **precise enough** to capture behavioral differences between submodules, but **abstract enough** that a submodule version bump doesn't invalidate the entire mapping. The scoping criteria (Section 2.1) determine where on this spectrum each mapping entry sits.

### 4.5 Touch-Point Rule

When a design doc introduces a new component, it must specify the expected touch-point count for adding one more variant. The following are **reference targets**:

| Extension Type | Reference Target |
|---|---|
| New transfer type | ~6 files (mapping artifact, scorer template, 3 prompt templates, CLI support) |
| New signal mapping | ~2 files (mapping artifact + schema update) |
| New validation suite | ~3 files (harness test + schema + CLI command) |
| New workspace artifact | ~3 files (schema + producer stage + consumer stage) |

If a design exceeds the reference target, the design doc must acknowledge the friction and explain whether it's acceptable (justified complexity) or whether structural improvement should happen first. The goal is **awareness, not rigidity** — some extensions genuinely require more touch points, but that should be a conscious choice, not an accident.

---

## 5. Extension Framework

### 5.1 Extension Taxonomy

There are four fundamentally different ways to extend sim2real:

| Type | What It Is | Example | Scope |
|---|---|---|---|
| **New transfer type** | Full pipeline for a new algorithm category | Admission policy transfer alongside routing | New mapping + prompts + tests |
| **New signal mapping** | Additional signal in an existing mapping artifact | Adding `cache_hit_rate` signal to routing mapping | Update mapping + schema |
| **New validation suite** | Additional test suite beyond A/B/C | Suite D for latency sensitivity analysis | New harness test + schema |
| **New target system** | New production system alongside llm-d | SGLang support | New mapping + scorer template + submodule |

Understanding which type an extension is determines which recipe to follow.

### 5.2 Recipe: New Transfer Type

*Adding a full 6-stage pipeline for a new algorithm category.*

This is the most significant extension. It requires a mapping artifact, scorer template, prompt templates for all stages, and validation test cases.

**Design doc must define:** Analysis questions (Q1-Q3 instantiated for the new type), scoping decisions, submodule API surface, all component contracts per Section 4.3.

**Reference:** See `docs/contributing/templates/extension-recipes.md` for detailed steps.

### 5.3 Recipe: New Signal Mapping

*Adding a signal to an existing mapping artifact.*

This is the lightest extension. The mapping artifact and transfer type already exist; only the signal-level mapping and its schema entry are new.

**Design doc must define:** Which analysis question the signal helps answer, the sim-to-prod correspondence, and validation approach.

**Reference:** See `docs/contributing/templates/extension-recipes.md` for detailed steps.

### 5.4 Recipe: New Validation Suite

*Adding a test suite beyond the standard A/B/C hierarchy.*

This extension adds a new dimension of validation. It requires a harness test, a schema for results, and CLI integration.

**Design doc must define:** What the suite validates that existing suites do not, where it fits in the validation hierarchy, and what its pass/fail criteria are.

**Reference:** See `docs/contributing/templates/extension-recipes.md` for detailed steps.

### 5.5 Recipe: New Target System

*Adding support for a production system alongside llm-d.*

This is the most architecturally significant extension because it tests whether the pipeline's abstractions are truly system-independent.

**Design doc must define:** New submodule integration, mapping artifact for the target system, scorer template reflecting the target's plugin conventions, and how validation suites generalize.

**Reference:** See `docs/contributing/templates/extension-recipes.md` for detailed steps.

### 5.6 Extension Checklist

Before submitting a design doc for any extension, verify:

- [ ] Extension type identified (new transfer type / new signal mapping / new validation suite / new target system)
- [ ] Correct recipe followed
- [ ] Component contract defined (reads / writes / validates / halts on / schema / friction)
- [ ] No-op default exists (existing behavior unchanged when extension not configured)
- [ ] Cross-system checklist from Section 2.6 completed
- [ ] Schema changes traced through Writer -> Reader chain
- [ ] All affected validation suites identified

---

## 6. Anti-Patterns with Evidence

Every anti-pattern in this section traces to a real risk, a known failure mode, or a design principle learned from cross-system transfer experience.

### 6.1 Design Doc Anti-Patterns

| Anti-Pattern | Lesson |
|---|---|
| **Schema Without Consumers** | Don't define JSON schemas that no stage reads. Every schema must have a producer and a consumer. Dead schemas accumulate silently and mislead future developers into thinking they are load-bearing. |
| **Mapping Without Verification** | Don't describe submodule APIs from memory. Read the actual code at the pinned commit. API descriptions written from cached knowledge diverge from reality within one submodule update cycle. |
| **Prompt Without Halt Conditions** | Don't write prompts that can silently proceed past errors. Every prompt needs explicit halt conditions. An LLM that generates code from a stale mapping without halting produces plausible but incorrect output. |
| **Dead Artifact Accumulation** | Don't create workspace files that no subsequent stage reads. Every artifact must have an identified consumer. If a file has no consumer, it does not belong in the pipeline. |

### 6.2 Pipeline Architecture Anti-Patterns

| Anti-Pattern | Lesson |
|---|---|
| **Ambient State** | Don't communicate between stages except through `workspace/` artifacts with schemas. Environment variables, global config files, or in-memory state between CLI invocations break reproducibility and make debugging impossible. |
| **Cross-Language Coupling** | Don't import Python packages in Go or vice versa. Communicate through JSON artifacts. The pipeline deliberately uses two languages for their strengths (Python for LLM orchestration, Go for test harness); coupling them defeats the purpose. |
| **Submodule Mutation** | Don't modify existing target system files. Only add new files on local branches. Modifying existing files creates merge conflicts on every submodule update and makes the transfer non-portable. |
| **Unpinned Dependencies** | Don't reference submodule APIs without recording the commit hash you verified against. An unpinned reference is a mapping that may already be stale — and staleness in cross-system transfer produces silently wrong results. |

### 6.3 sim2real-Specific Anti-Patterns

| Anti-Pattern | Lesson |
|---|---|
| **Signal Name Drift** | A signal called `queue_depth` in the mapping but `queueDepth` in the schema breaks silently. Use exact names everywhere. Signal names must be identical across the mapping artifact, JSON schemas, prompt templates, and generated code. Grep to verify. |
| **Staleness Denial** | Acknowledging commit pin drift without reviewing the actual diff is dangerous. The acknowledge option requires a review summary. Staleness is not a warning to dismiss — it is a signal that the mapping may have diverged from reality. |
| **Suite Skip** | Skipping Suite B because "staleness doesn't matter for approximate scorer" is wrong — v1 may have zero staleness but future transfers won't. Every suite exists for a reason, and skipping one creates a false sense of validation coverage. |
| **Threshold Hardcoding** | Don't hardcode validation thresholds (e.g., "equivalence if delta < 0.05"). Thresholds should come from the calibration procedure and be adjustable per transfer type. What constitutes "equivalent" depends on the algorithm and workload. |

### 6.4 The Meta-Lesson

All anti-patterns share one root cause: **treating cross-system boundaries as trusted rather than verified.** When a mapping artifact says "the API has field X", verify it against actual code. When a schema says "this field is required", verify the producer writes it. When a prompt says "the submodule uses this convention", verify it at the pinned commit.

> **Trust but verify — across every boundary.**

If a design doc follows this principle, its mappings stay accurate, its schemas stay connected, its prompts stay grounded in reality, and its validation catches drift before it becomes a silent failure.

---

## References

1. sim2real PR workflow — `docs/contributing/pr-workflow.md`
2. sim2real v3 transfer pipeline design — `docs/plans/v3-transfer-pipeline-design.md`
3. Macro plan template — `docs/contributing/templates/macro-plan.md`
4. Micro plan template — `docs/contributing/templates/micro-plan.md`
5. Extension recipes — `docs/contributing/templates/extension-recipes.md`
6. CLAUDE.md — project-level engineering rules and code patterns
