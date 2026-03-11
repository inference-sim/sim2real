# Design Process

**Status:** Active (v1.1 — adapted for sim2real, 2026-03-11)

This document describes the process for writing a sim2real design document. For the design document template itself, see [docs/contributing/templates/design-guidelines.md](templates/design-guidelines.md).

## When a Design Doc is Needed

- New pipeline stages or stage modifications
- New transfer types (e.g., admission policy transfer alongside routing)
- New target system support (e.g., SGLang alongside llm-d)
- Architecture changes affecting inter-stage contracts or submodule interactions

**Not needed for:** Bug fixes, new signal mappings behind existing schemas, documentation changes, prompt template improvements within existing structure.

## Steps

1. **Identify the extension type** — new transfer type, new signal mapping, new validation suite, new target system, new workspace artifact (see [design guidelines](templates/design-guidelines.md) Section 5)
2. **Choose the design doc species** — decision record, specification, problem analysis, or system overview (Section 3.2)
3. **Complete the cross-system checklist** (Section 2.6) — scoping, submodule dependencies, workspace artifacts, schemas, validation approach, prompt templates
4. **Write the design doc** per the template's required sections (Section 3.3): motivation, scope, modeling decisions, invariants, decisions with trade-offs, extension points, validation strategy
5. **Apply the staleness test** (Section 3.1) — would this content mislead if the implementation changes?
6. **Convergence review** — `/convergence-review design <design-doc-path>` dispatches 8 parallel perspectives and enforces convergence (see [docs/contributing/convergence.md](convergence.md)). Manual alternative: review against each perspective checklist below.
7. **Human review** — approve before macro/micro planning begins

## Design Review Perspectives (8)

For each perspective, check every item. Classify findings as CRITICAL / IMPORTANT / SUGGESTION per the [convergence protocol](convergence.md). Section references below refer to [design-guidelines.md](templates/design-guidelines.md) unless otherwise noted.

**Perspective 1 — Motivation & Scoping:**
- Are the analysis questions clear and specific?
- Is the scoping table complete (what submodule APIs are needed, what can be deferred)?
- Does every included component trace to an analysis question (Q1, Q2, or Q3)?
- Has each component been evaluated against the six scoping criteria (design-guidelines Section 2.1)?

**Perspective 2 — Cross-System Integration:**
- Is the cross-system design review checklist (Section 2.6) completed with all 10 questions answered?
- Are submodule API dependencies identified with commit pins?
- Are inter-stage artifact contracts specified with JSON schemas?
- Is the mapping artifact impact documented (what signals are affected)?
- Are prompt template changes identified with all 4 required sections?

**Perspective 3 — Component Contract Completeness:**
- Does every new or modified component have all 6 contract aspects (reads, writes, validates, halts on, schema, extension friction)?
- Are invariants named (INV-N) and cross-referenced with existing invariants?
- Is the extension friction count specified and within reference targets (Section 4.5)?

**Perspective 4 — Extension Framework Fit:**
- Is the extension type correctly identified (new transfer type / new signal / new suite / new target system)?
- Is the correct recipe from Section 5 followed?
- Is the no-op default specified (existing behavior unchanged when extension not configured)?
- Is parallel development path described?

**Perspective 5 — Prohibited Content:**
- Any implementation code that would go stale? (Prohibited — Section 3.4)
- Any submodule API signatures that should be referenced by commit pin instead? (Prohibited)
- Any file paths with line numbers? (Prohibited)
- Any schema definitions that should live in tools/schemas/ instead? (Prohibited)

**Perspective 6 — Trade-off Quality:**
- Does every non-obvious decision have alternatives listed with rationale?
- For each decision: what breaks if it's wrong?
- Is there a Decision Status column (Proposed / Implemented / Superseded)?

**Perspective 7 — Validation Strategy:**
- How will cross-system accuracy be verified? (Submodule API spot checks)
- How will schema chain integrity be validated? (Writer→Reader trace)
- Which validation suites (A/B/C/benchmarks) apply to this feature?
- Are both verification and validation addressed (not just one)?

**Perspective 8 — Staleness Resistance:**
- Apply the staleness test (Section 3.1) to every section
- Would any content mislead if the implementation changes during micro-planning?
- Is content described behaviorally (what crosses a boundary and why) rather than structurally (how the boundary is implemented)?

## Quality Gates

- [ ] Extension type identified and correct recipe followed
- [ ] Cross-system checklist from Section 2.6 completed
- [ ] No prohibited content (Section 3.4): no stale implementation code, no unpinned API references
- [ ] Every non-obvious decision has alternatives listed with rationale
- [ ] Validation strategy specified (which suites? against what production data?)

## Prerequisites

| Skill | Purpose | Manual Alternative |
|-------|---------|--------------------|
| `convergence-review` | Dispatch parallel review perspectives (Step 6) | Review against each perspective checklist above |

## References

- Template: [docs/contributing/templates/design-guidelines.md](templates/design-guidelines.md)
- Convergence protocol: [docs/contributing/convergence.md](convergence.md)
- Standards: [docs/contributing/standards/rules.md](standards/rules.md), [docs/contributing/standards/invariants.md](standards/invariants.md)
