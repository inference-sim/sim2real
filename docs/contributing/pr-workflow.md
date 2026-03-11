# PR Development Workflow

**Status:** Active (v1.0 — adapted for sim2real, 2026-03-10)

## Table of Contents

- [Overview](#overview)
- [PR Categories](#pr-categories)
- [Review Perspectives](#review-perspectives)
- [Verification Gates](#verification-gates)
- [Self-Audit Dimensions](#self-audit-dimensions)
- [Step-by-Step Process](#step-by-step-process)
- [Convergence Protocol](#convergence-protocol)
- [Quick Reference: Macro Plan PR Categories](#quick-reference-macro-plan-pr-categories)
- [Tips for Success](#tips-for-success)

---

## Overview

This document describes the PR workflow for sim2real — a mixed-language (Python + Go + Markdown), artifact-heavy, cross-system pipeline project. The workflow is adapted from the inference-sim PR workflow with these design principles:

1. **Right-size reviews to deliverable type** — artifact PRs don't need 10 code-review perspectives
2. **Verify what matters** — cross-system contract accuracy, schema consistency, prompt completeness
3. **Keep what works** — worktree isolation, plan-before-code, human review gates, self-audit
4. **Drop what doesn't** — DES/vLLM/distributed-platform review perspectives, Go-only verification, TDD enforcement for non-code deliverables

---

## PR Categories

Instead of size tiers, PRs are categorized by deliverable type. Each category determines the review depth, verification gate, and convergence protocol.

| Category | What it contains | Review perspectives | Convergence |
|----------|-----------------|---------------------|-------------|
| **Artifact** | Mapping docs, scorer templates, design artifacts | 3 | Single review-pr pass + fix |
| **Pipeline Stage** | Prompt templates + supporting code | 4 | Single convergence round; re-run only if CRITICAL |
| **Validation** | Test suites, benchmarks, harness code | 5 | Full convergence (max 3 rounds) |
| **Integration** | End-to-end pipeline wiring | 5 | Full convergence (max 3 rounds) |

---

## Review Perspectives

Five perspectives replace the inference-sim 10-perspective set. Each PR category uses a subset.

### 1. Cross-system contract integrity

Do artifacts correctly describe the APIs in submodules? Are commit pins current? Do workspace artifact schemas chain correctly between stages?

**Check for:** Submodule commit pin matches actual HEAD, API signatures in mapping artifact match actual Go code, JSON schema fields match producing/consuming code.

### 2. Artifact completeness & consistency

Are all required sections present? Do file paths, signal names, and field names match across documents? Is the deviation log current?

**Check for:** Missing required sections, name mismatches between mapping artifact and schemas, stale file path references, field name inconsistencies across documents.

### 3. Prompt template quality

Does the prompt guide through all required steps? Are validation checks specified? Are halt conditions clear? Are predecessor artifact checks included?

**Check for:** Missing validation steps, unclear halt conditions, missing predecessor artifact checks, ambiguous instructions that could cause LLM missteps, missing expected output format specifications.

### 4. Code correctness (Python CLI + Go harness)

Edge cases, error handling, JSON output contract, exit codes. Standard code review concerns.

**Check for:** Missing error handling, JSON output not matching documented schema, inconsistent exit codes, untested edge cases, security issues in CLI argument parsing.

### 5. Plan structural validation

Task dependencies sound? Template sections complete? Implementation steps specific enough?

**Check for:** Missing task dependencies, circular dependencies, steps that are too vague to execute, missing acceptance criteria, references to non-existent files or commands.

### Perspective Assignment by Category

| Category | 1. Contracts | 2. Artifacts | 3. Prompts | 4. Code | 5. Plan |
|----------|:---:|:---:|:---:|:---:|:---:|
| **Artifact** | X | X | | | X |
| **Pipeline Stage** | X | X | X | | X |
| **Validation** | X | X | X | X | X |
| **Integration** | X | X | X | X | X |

---

## Verification Gates

Each PR category has a tailored verification gate. Run the appropriate gate before committing.

### Artifact PRs

```bash
# Validate mapping artifact (when mapping exists)
python tools/transfer_cli.py validate-mapping

# JSON schema structural validation
python tools/transfer_cli.py validate-schema --schema tools/schemas/<schema>.schema.json --artifact <artifact>.json

# Manual: extract code blocks from .go.md files, compile against pinned submodule HEAD
```

### Pipeline Stage PRs

```bash
# Python tests
python -m pytest tests/

# Go build (if harness code exists)
go build ./tools/harness/...

# Prompt template YAML front-matter check (manual: verify all required front-matter fields present)
```

### Validation PRs

```bash
# Python tests
python -m pytest tests/

# Go tests
go test ./tools/harness/...

# Go build
go build ./tools/harness/...
```

### Integration PRs

```bash
# End-to-end smoke test with synthetic algorithm
# (specific command TBD — will be defined when PR6 is implemented)
python tools/transfer_cli.py extract routing/ && \
  python -m pytest tests/ && \
  go test ./tools/harness/... && \
  go build ./tools/harness/...
```

---

## Self-Audit Dimensions

Before committing, audit against these 6 dimensions. Each is specific to sim2real's cross-system, artifact-heavy nature.

### 1. Cross-system accuracy

Do all submodule API references match actual code? Are commit pins current?

**How to check:** `git submodule status` to verify commit pins. Read referenced API signatures in submodule source and compare to mapping artifact descriptions.

### 2. Schema chain integrity

Does each workspace artifact's output match the consuming stage's expected input?

**How to check:** For each workspace artifact modified, trace its `Writer → Reader(s)` chain from the macro plan's workspace artifact table. Verify required fields are present and types match.

### 3. Prompt completeness

Does every prompt template specify: prerequisites, validation steps, halt conditions, expected outputs?

**How to check:** Each prompt template must have these four sections. If any is missing, the prompt is incomplete.

### 4. CLI contract

Do all CLI commands produce documented JSON schemas? Are exit codes consistent?

**How to check:** Run each modified CLI command with `--help` or test input. Verify JSON output matches the schema documented in the macro plan's Component Model section (exit codes: 0 = success, 1 = validation failure, 2 = infrastructure error).

### 5. Artifact consistency

Do signal names, field names, and file paths match across mapping artifact, schemas, prompts, and README?

**How to check:** Grep for each signal name across all artifacts. Any mismatch is a bug.

### 6. Dead artifact prevention

Is every file created by this PR consumed by a test, a later PR, or the pipeline runtime?

**How to check:** For each new file, identify at least one consumer. If no consumer exists and none is planned in a later PR, the file is dead weight.

---

## Step-by-Step Process

### Step 1: Create Isolated Worktree

Use the `using-git-worktrees` skill to create an isolated worktree.

```
/superpowers:using-git-worktrees
```

### Step 2: Create Implementation Plan

Use the `writing-plans` skill with the micro-plan template (`docs/contributing/templates/micro-plan.md`).

```
/superpowers:writing-plans
```

### Step 3: Review Plan

- **Artifact / Pipeline Stage PRs:** Single `review-pr` pass. Fix any CRITICAL or IMPORTANT findings.
- **Validation / Integration PRs:** Full convergence review (see [Convergence Protocol](#convergence-protocol)).

```
/convergence-review
```

### Step 4: Human Review of Plan

Present the plan for human approval. Do not proceed without explicit approval.

### Step 5: Execute

- **Code PRs:** Use the `executing-plans` skill.
- **Artifact PRs:** Direct authoring — no execution skill needed for pure documentation.

```
/superpowers:executing-plans
```

### Step 6: Review Code/Artifacts

Run category-appropriate review perspectives (see [Perspective Assignment by Category](#perspective-assignment-by-category)).

```
/convergence-review x-pr-code
```

### Step 7: Verify

Run the category-appropriate [verification gate](#verification-gates).

### Step 8: Self-Audit

Walk through all 6 [self-audit dimensions](#self-audit-dimensions). Document any deviations.

### Step 9: Commit, Push, and Create PR

Use the commit-push-pr skill.

```
/superpowers:finishing-a-development-branch
```

---

## Convergence Protocol

The convergence protocol varies by PR category to avoid over-reviewing simple artifacts.

### Artifact PRs

No formal convergence. Single `review-pr` pass with 3 perspectives (contracts, artifacts, plan). Fix findings and proceed.

### Pipeline Stage PRs

Single convergence round with 4 perspectives (contracts, artifacts, prompts, plan). Re-run **only** if CRITICAL findings remain after fixes. Maximum 2 rounds.

### Validation / Integration PRs

Full convergence protocol:
- 5 perspectives (all)
- **Converged** = zero CRITICAL + zero IMPORTANT findings
- Maximum 3 rounds
- If not converged after 3 rounds, escalate to human review with the remaining findings listed

---

## Quick Reference: Macro Plan PR Categories

| PR | Description | Category | Perspectives |
|----|-------------|----------|:------------:|
| PR1 | Mapping artifact + project scaffolding + CLI extract | Artifact | 1, 2, 5 |
| PR2 | Scorer template artifact | Artifact | 1, 2, 5 |
| PR3 | Prompt templates (Stages 1-3) + Go harness skeleton | Pipeline Stage | 1, 2, 3, 5 |
| PR4 | Prompt template (Stage 4) + test retry logic | Pipeline Stage | 1, 2, 3, 5 |
| PR5 | Validation pipeline (Stage 5) + noise + benchmarks | Validation | 1, 2, 3, 4, 5 |
| PR6 | Stage 6 (PR creation) + pipeline self-verification | Integration | 1, 2, 3, 4, 5 |

---

## Tips for Success

- **Artifact PRs are lightweight.** Don't over-process them. A mapping document doesn't need full convergence review.
- **Cross-system accuracy is the #1 risk.** When in doubt, read the actual submodule source — don't trust cached knowledge of APIs.
- **Schema chain breaks are silent.** A field name mismatch between writer and reader won't surface until runtime. The self-audit schema chain check catches these.
- **Commit pins go stale.** Every PR that touches submodule-dependent artifacts should verify `git submodule status` matches the documented commit pin.
- **Prompt templates are code.** They control LLM behavior and have halt conditions, validation steps, and output contracts. Review them with the same rigor as code.
- **Dead artifacts accumulate.** If you create a file, document its consumer. If it has no consumer, question whether it belongs in this PR.
