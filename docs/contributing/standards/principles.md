# sim2real Engineering Principles

Principles guide design decisions. The [antipattern rules](rules.md) are specific, checkable manifestations of these principles. The [invariants](invariants.md) are properties that must always hold.

## Cross-System Separation of Concerns

- The pipeline communicates with target systems ONLY through the mapping artifact and submodule code reading. No direct runtime coupling.
- Pipeline code (`tools/`, `prompts/`) never imports target system packages.
- Target system code changes happen in submodule branches, not in the sim2real repo.
- The mapping artifact is the single bridge between simulation concepts and production concepts.
- *(Enforced by R1, R5)*

## Artifact-Driven Pipeline

- Each stage reads structured input artifacts and writes structured output artifacts to `workspace/`.
- No ambient state — all inter-stage communication flows through explicit JSON/Markdown files.
- Artifacts have schemas defined in `tools/schemas/` and validated by consuming stages.
- If a stage fails, crash recovery reads the last written artifact and resumes from there.
- *(Enforced by R2, R6)*

## Mixed-Language Conventions

- **Python** (CLI tools in `tools/`): PEP 8, type hints, JSON output contract (stdout=data, stderr=errors, exit codes 0/1/2).
- **Go** (test harness in `tools/harness/`): Standard Go formatting, `go test` conventions, table-driven tests.
- **Markdown** (prompt templates in `prompts/`): YAML front-matter with required fields, structured sections, explicit halt conditions.
- Each language follows its own ecosystem conventions. Don't force Python patterns into Go or vice versa.
- *(Enforced by R3, R4)*

## Schema-First Design

- JSON schemas (`tools/schemas/`) define contracts BEFORE code is written.
- Every workspace artifact has a schema. Every CLI output has a schema.
- Schema changes require updating all producers and consumers — trace the Writer->Reader chain.
- Schemas are the source of truth for inter-stage contracts; code must conform to schemas, not the reverse.
- *(Enforced by R2)*

## Submodule Isolation

- All code changes to target systems happen on local branches within submodules.
- Changes are additive — new plugin files, new test files, new config files. No modifications to existing target system code.
- Atomic rollback: disable the plugin via feature flag config and the system returns to pre-transfer behavior.
- Submodule commits are pinned in the mapping artifact; staleness is tracked explicitly.
- *(Enforced by R1, INV-5, INV-7)*

## Validation Rigor Hierarchy

- **Suite A** (fidelity): Does the translated code rank endpoints the same as simulation? Kendall-tau + numeric fidelity.
- **Suite B** (staleness): Does behavior degrade gracefully under metric staleness? Rank stability.
- **Suite C** (concurrency): Does sequential-test behavior hold under concurrent execution? Parallel safety.
- **Cluster benchmarks**: Does the algorithm's benefit survive in production-like conditions? Mechanism check.
- Each level depends on the prior passing. Don't run cluster benchmarks if Suite A fails.
- *(Enforced by R9, R10)*

## Documentation Single Source of Truth

Every piece of documentation lives in exactly one canonical location. Other files may contain **working copies** (summaries for quick reference) with explicit canonical-source headers.

**The canonical-source pattern:**

> **Canonical source:** [`docs/contributing/standards/rules.md`](rules.md). If this section diverges, rules.md is authoritative.

**When updating any standard, invariant, rule, or recipe:**
1. Update the canonical source FIRST
2. Then update any working copies that reference it
3. If you can't update working copies immediately, the canonical-source header ensures readers know which version to trust

**Single-source-of-truth map:**

| Content | Canonical Source | Working Copies |
|---------|-----------------|----------------|
| Antipattern rules (R1-R10) | `docs/contributing/standards/rules.md` | CLAUDE.md (table) |
| Pipeline invariants (INV-1–INV-8) | `docs/contributing/standards/invariants.md` | CLAUDE.md (summary) |
| Engineering principles | `docs/contributing/standards/principles.md` | CLAUDE.md (summary) |
| Extension recipes | `docs/contributing/extension-recipes.md` | — |
| Design process | `docs/contributing/design-process.md` | — |
| Macro-plan process | `docs/contributing/macro-planning.md` | — |
| Transfer validation standards | `docs/contributing/standards/experiments.md` | — |
| Convergence protocol | `docs/contributing/convergence.md` | `docs/contributing/transfer-validation.md` (summary), `docs/contributing/pr-workflow.md` (summary), `.claude/skills/convergence-review/SKILL.md` (protocol copy) |
| Transfer validation workflow | `docs/contributing/transfer-validation.md` | — |
| PR workflow | `docs/contributing/pr-workflow.md` | `.claude/skills/convergence-review/pr-prompts.md` (perspective prompts) |
| v3 pipeline design | `docs/plans/2026-03-06-sim2real-transfer-design-v3.md` | — |
