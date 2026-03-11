# sim2real Transfer Pipeline Rules

Every rule traces to a self-audit dimension or a v3 design contract. Rules are enforced at three checkpoints:
- **PR template** — checklist before merge
- **Micro-plan review** — checklist before implementation
- **Self-audit** — deliberate verification against the 6 self-audit dimensions before commit

For the full process and self-audit dimensions, see [docs/contributing/pr-workflow.md](../pr-workflow.md).

> **Note:** sim2real starts with 10 rules derived from the 6 self-audit dimensions. New rules will be added as real transfer bugs surface — following the same principle inference-sim used.

## Priority Tiers

New contributors: focus on **Critical** rules first. These protect correctness — violating them produces wrong transfer artifacts or broken generated code. **Important** rules protect artifact quality and pipeline consistency.

| Tier | Rules | Why |
|------|-------|-----|
| **Critical** (correctness) | R1, R2, R3, R7, R8 | Violations produce wrong API references, broken schema chains, incomplete prompts, missing fidelity ratings, or altered algorithm logic |
| **Important** (quality) | R4, R5, R6, R9, R10 | Violations produce inconsistent CLI behavior, silent name mismatches, dead artifacts, missing boundary tests, or unreliable benchmarks |

All 10 rules apply to every PR. The tiers help you prioritize during review — check Critical rules first.

## Rules

### R1: Submodule commit pin accuracy

Every reference to a submodule API (function signature, type name, field name) in a mapping artifact or design document must match the actual code at the pinned commit. Divergence means the mapping artifact is lying about the target system.

**Evidence:** The entire transfer depends on accurate API descriptions — a wrong type name in the mapping artifact causes Stage 3 (Generate) to produce code that won't compile.

**Check:** Run `git submodule status` to confirm the pinned commit. For each API reference in the mapping artifact, verify the function signature, type name, or field name exists at that commit.

**Enforced:** Self-audit dimension 1 (cross-system accuracy); PR template.

**Priority:** Critical.

---

### R2: Schema chain integrity

Each workspace artifact's output fields must match the consuming stage's expected input fields in name, type, and semantics. A field name mismatch between writer and reader won't surface until runtime.

**Evidence:** v3 design, inter-stage artifact contracts — explicit field-level schemas between stages.

**Check:** Trace the Writer-to-Reader chain from the macro plan workspace artifact table. For each output field, confirm the consuming stage's input schema expects that exact field name and type.

**Enforced:** Self-audit dimension 2 (schema chain integrity); PR template.

**Priority:** Critical.

---

### R3: Prompt template completeness

Every prompt template must specify: prerequisites (what artifacts must exist), validation steps (what to check before proceeding), halt conditions (when to stop and report), and expected outputs (what the stage produces). An incomplete prompt causes the LLM to skip validation or produce wrong artifacts.

**Evidence:** v3 design — each stage has explicit input/output contracts.

**Check:** Verify the prompt template front-matter contains all four required sections: prerequisites, validation steps, halt conditions, expected outputs. A missing section is a rule violation.

**Enforced:** Self-audit dimension 3 (prompt completeness); PR template.

**Priority:** Critical.

---

### R4: CLI JSON contract

All CLI commands output JSON to stdout on success and JSON error objects to stderr on failure. Exit codes: 0 = success, 1 = recoverable/validation error, 2 = unrecoverable/infrastructure error. Inconsistent exit codes or non-JSON output breaks downstream consumers.

**Evidence:** v3 design, "What Drives Each Stage" — CLI tool contract.

**Check:** For each CLI command, test: (a) stdout is valid JSON on success, (b) stderr is valid JSON on failure, (c) exit code is 0, 1, or 2 with correct semantics.

**Enforced:** Self-audit dimension 4 (CLI contract); PR template.

**Priority:** Important.

---

### R5: Cross-artifact name consistency

Signal names, field names, and file paths must match exactly across mapping artifact, JSON schemas, prompt templates, and README. A name mismatch is a silent bug.

**Evidence:** Self-audit dimension 5 in pr-workflow.md — "grep for each signal name across all artifacts. Any mismatch is a bug."

**Check:** For each signal name, field name, or file path introduced or changed, grep across all artifacts. Any mismatch in spelling, casing, or structure is a rule violation.

**Enforced:** Self-audit dimension 5 (artifact consistency); automated grep.

**Priority:** Important.

---

### R6: No dead artifacts

Every file created by a PR must have at least one consumer: a test, a later pipeline stage, or the final PR output. Files with no consumer are dead weight.

**Evidence:** Self-audit dimension 6 in pr-workflow.md.

**Check:** For each new file in the PR, identify its consumer by name. If no consumer exists, either add one or remove the file.

**Enforced:** Self-audit dimension 6 (dead artifact prevention); PR template.

**Priority:** Important.

---

### R7: Mapping artifact fidelity ratings

Every signal in the mapping artifact must have a fidelity rating (High, Medium, Low, or Upgrade). Missing ratings cause Stage 1 to halt with an unclear error.

**Evidence:** v3 design, Signal Mapping — the fidelity decision rule requires all signals to be rated.

**Check:** Run `transfer_cli.py validate-mapping` to confirm all signals have ratings. Alternatively, inspect the mapping artifact manually for any signal missing a fidelity field.

**Enforced:** `transfer_cli.py validate-mapping`; PR template.

**Priority:** Critical.

---

### R8: Branch-count preservation

The number of conditional branches in Stage 1's extracted algorithm must equal the number in Stage 2's mapped algorithm spec. A mismatch means the translation changed the algorithm's logic structure.

**Evidence:** v3 design, inter-stage artifact contracts — `branch_count` field must match between stages. Related: INV-2.

**Check:** Compare the `branch_count` field between the Stage 1 output and Stage 2 output. Any difference is a rule violation.

**Enforced:** Stage 2 validation; PR template.

**Priority:** Critical.

---

### R9: Test tuple boundary coverage

Suite A test tuples must include values at threshold boundaries defined in the algorithm (e.g., if the algorithm branches on `queue_depth > 10`, tuples must include queue_depth values of 9, 10, and 11). Boundary tuples are where translation bugs hide.

**Evidence:** v3 design, Validate stage — threshold-boundary tuples verify directional responses.

**Check:** For each conditional branch in the algorithm, verify Suite A includes at least one tuple below, at, and above the threshold. Trace each boundary tuple back to its source conditional branch.

**Enforced:** Suite A tuple generation must trace to algorithm's conditional branches; PR template.

**Priority:** Important.

---

### R10: Noise characterization prerequisite

Before running cluster benchmarks, baseline noise must be characterized via `transfer_cli.py noise-characterize`. Without knowing the noise floor (CV), benchmark results can't be distinguished from noise.

**Evidence:** v3 design, Validate stage — noise characterization runs N baseline repetitions, computes CV.

**Check:** Confirm `noise_characterization.md` exists before any cluster benchmark results are reported. The cluster benchmark stage checks for this file's existence.

**Enforced:** Cluster benchmark stage prerequisite check; PR template.

**Priority:** Important.

---

## Quick Reference Checklist

For PR authors — check each rule before submitting:

- [ ] **R1:** Submodule API references match actual code at pinned commit
- [ ] **R2:** Workspace artifact output fields match consuming stage's input fields
- [ ] **R3:** Prompt templates have prerequisites, validation steps, halt conditions, expected outputs
- [ ] **R4:** CLI commands output JSON, exit codes are 0/1/2
- [ ] **R5:** Signal names, field names, file paths consistent across all artifacts
- [ ] **R6:** Every new file has an identified consumer
- [ ] **R7:** Every mapping artifact signal has a fidelity rating
- [ ] **R8:** Branch count preserved between Stage 1 extraction and Stage 2 mapping
- [ ] **R9:** Suite A test tuples cover threshold boundaries (below, at, above)
- [ ] **R10:** Noise characterization completed before cluster benchmarks

---

## Rule Lifecycle

Rules are born from self-audit dimensions and real transfer bugs. They live as long as they prevent real bugs. As the pipeline evolves, some rules may become automated, consolidated, or no longer applicable.

### Lifecycle States

| State | Meaning | Action |
|-------|---------|--------|
| **Active** | Rule prevents a class of bugs that can still occur | Check in every PR review |
| **Automated** | Rule is enforced by CI (linter, test, build) | Note the enforcement mechanism; keep for documentation but skip manual checks |
| **Consolidated** | Rule merged into a broader rule | Redirect to the parent rule; remove from checklist |
| **Retired** | The class of bugs is no longer possible (e.g., the pipeline stage was removed) | Move to a "Retired Rules" appendix with rationale |

### When to Consolidate

If two rules address the same root principle and checking one always catches the other, consolidate them. Example: if `transfer_cli.py validate-mapping` were extended to also verify branch-count preservation, R8 could be consolidated into R7 under an "Automated" state.

### Periodic Review

Every ~10 PRs or quarterly (whichever comes first), scan the rule list:
1. Can any rule be automated by a CLI validation command or CI check?
2. Are any two rules always checked together and catching the same class of bugs?
3. Has the pipeline stage that motivated any rule been removed or redesigned?

File an issue for each proposed state change. Do not retire rules silently.

### Current State

All 10 rules (R1-R10) are **Active** as of 2026-03-11. No rules have been automated, consolidated, or retired.
