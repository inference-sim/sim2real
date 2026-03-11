# Universal Convergence Protocol

**Status:** Active (v1.1 — adapted for sim2real, 2026-03-11)

This document defines the convergence protocol used by all review gates across sim2real workflows:
- **PR workflow** (docs/contributing/pr-workflow.md): Category-appropriate review (3–5 perspectives depending on PR category)
- **Transfer validation workflow** (docs/contributing/transfer-validation.md): Validation review (5 perspectives)
- **Design process** (docs/contributing/design-process.md): Design Doc Review (8 perspectives)
- **Macro-plan process** (docs/contributing/macro-planning.md): Macro Plan Review (8 perspectives)

> **Executable implementation:** The `convergence-review` skill automates this protocol — dispatching perspectives, tallying findings independently, and enforcing the re-run gate. Invoke with `/convergence-review <gate-type> [artifact-path] [--model opus|sonnet|haiku]` (default: `haiku`).

---

## The Protocol

1. Run all N perspectives in parallel (one round)
2. Collect all findings; each classified as CRITICAL / IMPORTANT / SUGGESTION
3. **If zero CRITICAL and zero IMPORTANT across all N reviewers:** Converged — proceed to next step
4. **If any CRITICAL or IMPORTANT from any reviewer:** Fix all issues, return to step 1 (re-run **entire** round)
5. Repeat until convergence

## Rules

- **Max 10 rounds per gate.** Each gate has its own independent round counter. If a gate fails to converge within 10 rounds, suspend the work: document the remaining issues as future work.
- **No minimum round count.** Convergence in Round 1 is valid if no reviewer flags any CRITICAL or IMPORTANT item.
- **Hard gate — NO EXCEPTIONS.** You MUST re-run after fixes. You may NOT skip the re-run, propose alternative steps, or rationalize that fixes were "trivial enough." The re-run is the only evidence of convergence. This is non-negotiable.
- **SUGGESTION-level items** (documentation nits, cosmetic fixes, off-by-one line citations) do not block convergence.

## Severity Levels

Each reviewer must classify every finding:

- **CRITICAL**: Must fix before proceeding. Examples: missing signal in mapping artifact, schema field mismatch between stages, submodule commit pin doesn't match actual code, broken cross-reference between artifacts.
- **IMPORTANT**: Should fix before proceeding. The key test: **would proceeding with this unfixed item mislead a reader or produce incorrect results?** Examples: incomplete prompt template (missing halt condition), inconsistent signal names across artifacts, undocumented schema field.
- **SUGGESTION**: Does not affect correctness or reader understanding. Examples: formatting inconsistency, minor wording improvement, optional section enhancement.

**When in doubt between IMPORTANT and SUGGESTION:** If fixing the item would change any pipeline behavior, artifact content, or validation result, it is IMPORTANT. If it would only improve readability without changing any outcome, it is SUGGESTION. If multiple reviewers classify the same item at different severities, the highest severity applies.

## Agent Failure Handling

- **Timeout:** 5 minutes per reviewer agent. If an agent exceeds this, check its output file and restart if stalled.
- **Failure:** If a reviewer agent fails or hangs, fall back to performing that review directly (read the artifact yourself with that reviewer's checklist). Do not skip a reviewer perspective.
- **External contributors:** Submit your artifacts via PR. Maintainers will run the review protocol on your behalf.

## Expected Convergence Rates

Gates with more perspectives will naturally converge more slowly than gates with fewer. This is correct behavior — more eyes = higher quality bar. Typical expectations:

| Gate Type | Perspectives | Expected Rounds |
|-----------|:---:|---|
| Artifact PR review | 3 | 1 round (single pass + fix) |
| Pipeline Stage PR review | 4 | 1–2 rounds |
| Validation / Integration PR review | 5 | 1–3 rounds |
| Transfer validation review | 5 | 1–3 rounds |
| Design Doc Review | 8 | 1–2 rounds |
| Macro Plan Review | 8 | 1–2 rounds |

## References

- PR workflow: [docs/contributing/pr-workflow.md](pr-workflow.md)
- Transfer validation workflow: [docs/contributing/transfer-validation.md](transfer-validation.md)
- Design process: [docs/contributing/design-process.md](design-process.md)
- Macro-plan process: [docs/contributing/macro-planning.md](macro-planning.md)
