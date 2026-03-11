You are operating with access to multiple repositories and external systems.

You are tasked with producing a MACRO-LEVEL DESIGN PLAN for a:

"Cross-System Pipeline Implementation."

This is a program-level plan that defines objectives, a component model,
cross-system integration, and an ordered PR series spanning multiple repos.

This is NOT implementation planning.
This is NOT a micro-level design.
This is NOT speculative architecture.

You MUST inspect all relevant codebases before proposing changes.

======================================================================
PREREQUISITE — DESIGN DOCUMENT
======================================================================

A cross-system pipeline design doc must exist BEFORE the macro plan.
The design doc describes WHAT the pipeline does and WHY (analysis
questions, abstraction gaps, validation strategy). The macro plan
describes WHAT to build and in WHAT ORDER (components, PRs, risks).

Read and reference:
- The design document for this pipeline
- The target systems' documentation and API surfaces
- Any mapping artifacts that bridge the systems

======================================================================
ABSTRACTION LEVEL RULE (NON-NEGOTIABLE)
======================================================================

The macro plan describes WHAT to build and in WHAT ORDER, not HOW to
implement each piece. Enforce these boundaries strictly:

ALLOWED in macro plan:
  - Behavioral descriptions of component contracts (prose)
  - External API references that are already stable/published
  - File path inventories (which packages, which files, per PR)
  - LOC estimates per PR (sizing heuristics)
  - CLI flag names and configuration surface area
  - Brief YAML/config examples
  - Architecture diagrams (text-based)
  - External system version requirements

PROHIBITED in macro plan:
  - Implementation code from any language (belongs in micro plan)
  - Internal type definitions from external systems (use behavioral
    descriptions — "the scorer plugin must implement the target
    system's plugin interface")
  - Pre-stabilized API signatures (describe behaviorally)
  - Factory/constructor code (belongs in micro plan)
  - Test code (belongs in micro plan)

THE TEST: Is this content a FACT about existing/stable systems, or an
ASPIRATION about code to be written? Facts are allowed. Aspirations
must be described behaviorally.

======================================================================
PHASE 0 — MULTI-SYSTEM RECON (MANDATORY)
======================================================================

Before proposing anything, recon EACH system involved in the pipeline:

1) For the HOST system (where pipeline code lives):
   - Top-level packages/modules and responsibilities
   - Existing extension points (skills, CLI commands, etc.)
   - Test framework and CI pipeline
   - Configuration patterns

2) For EACH TARGET system (systems the pipeline integrates with):
   - Public API surface (types, interfaces, methods the pipeline uses)
   - Plugin/extension conventions (how to add new components)
   - Build and test workflow (how generated code is validated)
   - Version cadence and stability guarantees
   - Deployment model (how changes reach production)

3) For EACH supporting artifact (mapping files, templates, etc.):
   - Purpose and ownership
   - Schema or structure
   - Staleness/versioning strategy

4) Clearly separate:
   - Confirmed facts (from inspection — cite file:line or doc section)
   - Inferred behavior (explicitly labeled as inference)
   - Open uncertainties

5) Identify cross-system constraints:
   - Which APIs are stable vs. evolving?
   - Which systems can be mocked for testing?
   - What ordering constraints exist between repos?

ANTI-HALLUCINATION RULE: For every claim about an external system's
API or behavior, cite the source (doc URL, file:line, or README section).
If you cannot cite it, mark as "UNVERIFIED" and do not rely on it.

======================================================================
PHASE 1 — HIGH-LEVEL OBJECTIVES AND SCOPING
======================================================================

Define:

- 3-7 crisp objectives
- Explicit non-goals
- External system version constraints (which versions are supported)
- Cross-system compatibility guarantees
- Performance constraints (pipeline runtime, resource usage)
- Rollback/disable guarantees (can target systems recover if pipeline
  produces bad output?)

PIPELINE SCOPING (required):

For this pipeline, answer:

1) What ANALYSIS QUESTIONS does this pipeline help answer?
   (from the design document)

2) What must be IMPLEMENTED to answer those questions?

3) What can be DEFERRED to later versions?
   (e.g., "multi-algorithm transfer deferred to v2")

4) What is explicitly OUT OF SCOPE?
   (e.g., "bidirectional transfer — production insights back to source")

Present as a table:

| Capability | v1 | Deferred | Out of Scope | Justification |
|------------|:--:|:--------:|:------------:|---------------|
| (example) Single algorithm transfer | X | | | Core use case |
| (example) Multi-algorithm batch | | X | | Config merge complexity |
| (example) Continuous integration trigger | | | X | Requires maturity first |

======================================================================
PHASE 2 — COMPONENT MODEL
======================================================================

Define the pipeline at the level a human would explain it on a
whiteboard. Components are pipeline stages, orchestration logic,
artifacts, and integration points — NOT internal modules of external
systems.

1) Components (3-10 named components)

   For EACH component, provide the COMPONENT CONTRACT:
   - Name and one-sentence responsibility
   - TYPE: one of:
     - PIPELINE STAGE: a step in the pipeline execution
     - ORCHESTRATOR: controls stage sequencing, retries, user interaction
     - ARTIFACT: a file/document produced or consumed by stages
     - INTEGRATION POINT: interface with an external system
   - INPUTS: what this component reads (artifacts, API responses, user input)
   - OUTPUTS: what this component produces (artifacts, API calls, PRs)
   - SIDE EFFECTS: external-facing effects (files created, PRs opened,
     deployments triggered, user prompts)
   - INVARIANTS: what must always hold for this component
   - FAILURE MODES: what goes wrong and what happens (halt, retry, degrade)
   - EXTERNAL DEPENDENCIES: which external systems/APIs are called

2) Data Flow
   - Stage-to-stage artifact flow (what crosses each boundary)
   - External API calls (which stages call which systems)
   - User interaction points (where the pipeline halts for human input)

3) System Invariants
   - What must ALWAYS hold (e.g., "no PR created without passing tests")
   - What must NEVER happen (e.g., "no deployment without equivalence pass")
   - Ordering constraints (which stages gate which)

4) Extension Points
   - Where do new capabilities plug in? (new validation suite, new
     target system, new signal type)
   - What is the current behavior for each extension point?
   - What is the cost of extension? (files to change, tests to add)

5) External System Map
   - Map each component to the external systems it touches:

   | Component | Host Repo | Target System A | Target System B | Artifacts |
   |-----------|-----------|-----------------|-----------------|-----------|

THE COMPONENT MODEL MUST FIT IN UNDER 100 LINES.
(Larger than the single-repo template because cross-system integration
adds inherent complexity. If it exceeds 100, simplify.)

======================================================================
PHASE 3 — RISK REGISTER
======================================================================

For every non-obvious decision, with special attention to cross-system
risks:

| Decision | Assumption | Validation Method | Cost if Wrong | Gate |
|----------|------------|-------------------|---------------|------|

CROSS-SYSTEM RISK CATEGORIES (check each):
- **API drift**: target system changes API between plan and implementation
- **Version mismatch**: pipeline targets version X, cluster runs version Y
- **Schema evolution**: artifact format changes break downstream stages
- **External availability**: target system's test infra is down/broken
- **Distributed rollback**: pipeline succeeds partially, needs cleanup
- **Mock fidelity**: test mocks don't reflect real system behavior

MANDATORY VALIDATION RULE:
If cost-of-being-wrong >= 3 PRs of rework, validation is MANDATORY.

For each validation gate, specify:
- Exact success criteria (measurable)
- Abort plan (what changes if validation fails)

======================================================================
PHASE 4 — IMPLEMENTATION EVOLUTION
======================================================================

Describe how the pipeline is built incrementally:

- Start from "nothing exists" (or from existing infrastructure)
- Show the progression: which components are built first, which last
- Identify the minimum viable pipeline (earliest point where the full
  pipeline can execute end-to-end, even with reduced validation)
- Explicitly describe what existing code is reused vs. new code
- For each external integration: when is the real API first called?
  (vs. mocked)

MILESTONE CHECKPOINTS:
Define 2-4 meaningful milestones where the pipeline reaches a testable
state. Example: "After PR3, the pipeline can extract + translate +
generate code, but cannot yet run equivalence tests."

======================================================================
PHASE 5 — CROSS-CUTTING INFRASTRUCTURE
======================================================================

Plan ONCE for the entire PR series:

1) Shared Test Infrastructure
   - Test helpers, shared fixtures, mock implementations of external APIs
   - Which PR creates them? Which PRs consume them?
   - External system mocking strategy: how are target systems simulated
     in tests? (recorded responses, lightweight stubs, full mock servers)

2) Documentation Maintenance
   - CLAUDE.md update triggers
   - README and user-facing docs
   - Artifact schema documentation

3) CI Pipeline Changes
   - New test packages
   - Integration test requirements (do tests need external access?)
   - Performance benchmarks

4) Dependency Management
   - New external dependencies (justify each)
   - External system version pinning strategy
   - Artifact version compatibility rules

5) Cross-Repo Coordination
   - Which PRs must land in which repos in what order?
   - How are cross-repo dependencies tested before merge?
   - Branch naming conventions across repos
   - Who reviews PRs in external repos?

6) Artifact Lifecycle
   - Which artifacts must exist before the first pipeline run?
   - Who creates and maintains each artifact?
   - Version scheme and staleness detection

No item may be left as "address when needed."

======================================================================
PHASE 6 — ORDERED PR SERIES (PR0 ... PRN)
======================================================================

Design an incremental, independently reviewable and mergeable PR
sequence. PRs may target DIFFERENT repositories.

For EACH PR, provide TWO TIERS:

--- TIER 1: Human Review Summary (target 15 lines, max 25) ---

- Title
- **Target Repo**: which repository this PR lands in
- Component Change: Which component model component is added/modified?
- PR Type (choose one):
    - infrastructure: shared test helpers, mocks, CI setup
    - artifact: mapping file, template, prompt, schema definition
    - pipeline-stage: implements a pipeline stage
    - integration: connects the pipeline to an external system
    - validation: adds a test suite or validation capability
    - orchestration: stage sequencing, retry logic, user interaction
- Motivation: Why does this PR exist? (1-2 sentences)
- Scope: In / Out (bullet points)
- Behavioral Guarantees: What MUST hold after this PR merges?
- Risks: Top 1-2 risks and how they're mitigated
- Cross-Cutting: Which shared infra does this PR create or consume?
- Validation Gate: Does this PR depend on a risk register validation?

--- TIER 2: Implementation Guide (for micro-planning) ---

- Architectural Impact (what changes structurally)
- API Surface Changes (described BEHAVIORALLY)
- Test Categories (unit, integration, end-to-end, mock-based)
- Documentation Updates
- Extension Friction: how hard is it to add a variant of what this
  PR introduces? (new signal type, new validation suite, new target)
- Cross-Repo Impact: does this PR require changes in other repos?
- Why this PR is independently reviewable
- Why it introduces no dead code

Constraints:

- Each PR must deliver one cohesive component change.
- Each PR must be exercisable immediately after merge.
  "Exercisable" means: via CLI, OR via tests that demonstrate the
  new behavior. Infrastructure PRs exercised by subsequent PRs are
  valid if they include their own test coverage.
- No speculative scaffolding.
- PRs targeting external repos must be self-contained (no dependency
  on unmerged PRs in the host repo, unless the external PR is a
  test/config change that can be reviewed independently).

======================================================================
PHASE 7 — DEPENDENCY DAG & PARALLELISM
======================================================================

Provide:

- A PR dependency graph (partial order), annotated with target repo
- Cross-repo ordering constraints (which external PRs gate which
  host-repo PRs)
- Parallelizable workstreams
- Merge sequencing guidance
- Validation gate placement (from risk register)
- Integration risk notes (which merges are highest-risk)

Maximize safe parallelism. Identify the critical path.

CROSS-REPO VISUALIZATION:
Show the DAG with repo annotations:

```
[PR0 host] → [PR1 host] → [PR3 host]
                  ↓              ↓
           [PR2 target-A]  [PR4 target-A]
```

======================================================================
PHASE 8 — DESIGN BUG PREVENTION
======================================================================

Include:

- Invariants that must never be broken (reference component model)
- Regression surfaces (which existing tests must keep passing)
- Cross-PR state migration risks (artifact format changes across PRs)

Common cross-system failure modes and how this plan prevents them:

  General:
  - Scaffolding creep (dead code introduced "for later").
    Prevention: every file, function, and config must be exercised
    by the end of the PR that introduces it.
  - Documentation drift.
    Prevention: the PR that causes the change updates docs in the
    same commit.
  - Test infrastructure duplication.
    Prevention: shared test packages created in an early PR.

  Cross-system-specific:
  - API contract drift: external system changes API after pipeline
    is built, breaking integration silently.
    Prevention: version-pinned artifacts + staleness checks at
    pipeline startup (Stage 1 prerequisites).
  - Mock divergence: test mocks don't reflect real system behavior,
    causing false confidence.
    Prevention: mocks are derived from real system artifacts (not
    hand-written); include at least one end-to-end test against
    real systems per release.
  - Distributed partial failure: pipeline succeeds in some repos
    but fails in others, leaving inconsistent state.
    Prevention: atomic multi-repo operations where possible; cleanup
    procedures for partial failures documented per stage.
  - Artifact staleness: mapping files or templates become outdated
    as target systems evolve.
    Prevention: staleness checks (compile, smoke test, version
    distance) as pipeline prerequisites.
  - Cross-repo merge ordering violations: PR in repo A merged before
    prerequisite PR in repo B, breaking integration.
    Prevention: dependency DAG includes cross-repo edges; CI checks
    for prerequisite PRs before merge.

======================================================================
OUTPUT FORMAT (STRICT)
======================================================================

A) Executive Summary (under 15 lines — what pipeline is being built,
   which systems it connects, how many PRs, key milestones)
B) Multi-System Recon Summary
C) High-Level Objectives + Non-Goals + Scoping Table
D) Component Model (under 100 lines — components with contracts,
   data flow, invariants, extension points, external system map)
E) Risk Register (with cross-system risk categories)
F) Implementation Evolution (current -> milestone 1 -> ... -> complete,
   described BEHAVIORALLY — no implementation code)
G) Stable API Reference (ONLY for external APIs that are already
   stable/published and that the pipeline depends on. Describe
   behaviorally unless a concrete signature is already frozen.
   Omit entirely if not applicable.)
H) Cross-Cutting Infrastructure Plan
I) PR Plan (PR0...PRN, Tier 1 + Tier 2 per PR, with target repo)
J) Dependency DAG (with cross-repo edges)
K) Design Bug Prevention Checklist

CONTEXT BUDGET RULE:
Sections A, C, and D are the human-review core and must be concise.
I-Tier-1 summaries should target 15 lines each (max 25).
All other sections are reference material consulted on demand.

======================================================================

Quality bar:

- Grounded in real code/docs with citations.
- No hallucinated APIs or behaviors.
- No dead code.
- No bloated PRs.
- Must withstand expert review.
- Must be realistic and implementable.
- Component model must be simple enough to explain verbally in 2 minutes.
- Every component has a contract.
- Every PR has a target repo and PR type.
- Every capability traces to a pipeline objective.

======================================================================
LIVING DOCUMENT PROTOCOL
======================================================================

This plan will evolve. When updating:

1) Add a dated revision note at the top explaining what changed and why.
2) If a risk register validation fails, document the finding and the
   resulting plan changes explicitly.
3) Never silently change a PR's behavioral guarantees — note old
   contract, new contract, and reason.
4) Track completed PRs by marking their status in the PR plan section.
5) After each PR merges, check: does the component model still
   accurately describe the pipeline? If not, update it.

======================================================================

Think deeply before answering.
Inspect before designing.
Validate before committing.
Scope before modeling.
Describe behavior, not implementation.
