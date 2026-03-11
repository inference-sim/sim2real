# Macro Plan Process

**Status:** Active (v1.1 — adapted for sim2real, 2026-03-11)

This document describes the process for creating a macro-level implementation plan (multi-PR feature). For the macro plan template, see [docs/contributing/templates/macro-plan.md](templates/macro-plan.md) (which redirects to the cross-system template).

## When a Macro Plan is Needed

- Features spanning 2+ PRs
- Work requiring a dependency DAG between PRs
- Features touching multiple pipeline stages or cross-system boundaries
- New transfer types or target system support

**Not needed for:** Single-PR features, bug fixes, documentation changes.

## Steps

1. **Design doc(s) as input** — read the relevant design doc(s) and/or GitHub issues
2. **Decompose into PRs** — each PR should be independently mergeable and testable
3. **Define the dependency DAG** — which PRs can be parallelized? Which must be sequential?
4. **Define workspace artifact contracts per PR boundary** — what does each PR guarantee to the next?
5. **Identify stable schemas** — which JSON schemas are stable (can be developed against in parallel)?
6. **Identify flexible internals** — which implementation details may change during micro-planning?
7. **Convergence review** — `/convergence-review macro-plan <plan-path>` dispatches 8 parallel perspectives and enforces convergence (see [docs/contributing/convergence.md](convergence.md)). Manual alternative: review against each perspective checklist below.
8. **Human review** — approve before micro-planning begins for any PR in the plan

## Macro Plan Review Perspectives (8)

For each perspective, check every item. Classify findings as CRITICAL / IMPORTANT / SUGGESTION per the [convergence protocol](convergence.md). Section references below refer to [design-guidelines.md](templates/design-guidelines.md) and the [macro plan template](templates/macro-plan.md) unless otherwise noted.

**Perspective 1 — Objective Clarity:**
- Are 3-7 crisp objectives defined?
- Are non-goals explicitly listed?
- Is the scoping table present (which submodule APIs, what can be deferred)?
- Are analysis questions specific enough to drive component selection?

**Perspective 2 — Concept Model Quality:**
- Is the concept model under 80 lines?
- Does every building block have all 6 contract aspects (reads, writes, validates, halts on, schema, extension friction)?
- Is cross-system correspondence documented (inference-sim / llm-d / llm-d-benchmark mapping table)?
- Is the workspace artifact ownership map complete (exactly one producing stage per artifact)?

**Perspective 3 — PR Decomposition:**
- Is every PR independently mergeable and testable?
- Does the dependency DAG have no cycles?
- Can workspace artifact schemas be tested with mock data (parallel development enabled)?
- Does each PR identify its category from [pr-workflow.md](pr-workflow.md) (Artifact, Pipeline Stage, Validation, Integration)?

**Perspective 4 — Abstraction Level:**
- Zero implementation code in plan sections (only schemas and contracts may appear)?
- Are all pre-implementation artifacts described behaviorally, not as code?
- Is every code reference a FACT about merged code, not an ASPIRATION about unwritten code?
- Are component contracts using the template from the design guidelines, not implementation details?

**Perspective 5 — Risk Register:**
- Does every non-obvious architectural decision have a risk entry?
- For decisions with cost-of-being-wrong >= 3 PRs, is validation MANDATORY with a specific gate?
- Does each validation gate have exact success criteria (not "looks good")?
- Are abort plans specified (what changes if validation fails)?

**Perspective 6 — Cross-Cutting Infrastructure:**
- Are test infrastructure, documentation, and CI changes each assigned to a specific PR?
- Is the schema stability schedule documented (which PR stabilizes which schema)?
- Is CLAUDE.md update ownership clear (the PR that causes the change updates it)?
- Are no items left as "address when needed"?

**Perspective 7 — Extension Friction:**
- For each new component boundary, is the touch-point count for adding one more variant specified?
- Are touch-point counts within reference targets from design guidelines Section 4.5?
- If friction exceeds targets, is this acknowledged and justified?

**Perspective 8 — Design Bug Prevention:**
- Is scaffolding creep prevented (every schema/artifact/prompt exercised by end of introducing PR)?
- Is documentation drift prevented (CLAUDE.md updated in the same PR that causes the change)?
- Is test infrastructure duplication prevented (shared test helpers created early)?
- Are sim2real-specific anti-patterns addressed:
  - Signal name drift (names consistent across mapping, schema, prompts)?
  - Staleness denial (commit pins verified, not just acknowledged)?
  - Schema without consumers (every schema has a producer and reader)?
  - Dead artifact accumulation (every workspace file has a consumer)?

## Quality Gates

- [ ] Every PR in the plan is independently mergeable (no PR requires another PR's uncommitted code)
- [ ] Dependency DAG has no cycles
- [ ] Workspace artifact schemas are testable with mock data (parallel development enabled)
- [ ] No implementation code (those belong in micro plans)
- [ ] Extension friction assessed for each new component boundary

## Prerequisites

| Skill | Purpose | Manual Alternative |
|-------|---------|--------------------|
| `convergence-review` | Dispatch parallel review perspectives (Step 7) | Review against each perspective checklist above |

## References

- Template: [docs/contributing/templates/macro-plan.md](templates/macro-plan.md)
- Design guidelines: [docs/contributing/templates/design-guidelines.md](templates/design-guidelines.md)
- Convergence protocol: [docs/contributing/convergence.md](convergence.md)
- Standards: [docs/contributing/standards/rules.md](standards/rules.md)
