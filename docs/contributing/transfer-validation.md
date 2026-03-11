# Transfer Validation Process

**Status:** Active (v1.0 — adapted for sim2real, 2026-03-11)

## Overview
This document describes the end-to-end process for validating that a sim-to-production algorithm transfer preserves the algorithm's behavior and benefit. For transfer validation standards, see standards/experiments.md. For the validation results template, see templates/transfer-validation.md.

## Prerequisites
Skills table: using-git-worktrees, convergence-review, finishing-a-development-branch

## Process Overview (10-step diagram)
```
Step 0: Create Worktree (isolated workspace)
Step 1: Workspace Setup (input artifacts ready, submodules updated)
Step 2: Extract + Translate (Stages 1-2: parse algorithm, map signals)
Step 3: Review Coverage (5-perspective review of signal coverage)
Step 4: Generate (Stage 3: LLM generates target-system code)
Step 5: Test (Stage 4: build and test generated code)
Step 6: Validate — Suites A/B/C (Stage 5a: equivalence testing)
Step 7: Validate — Cluster Benchmarks (Stage 5b: production benchmarks)
Step 8: Document Results (validation_results.json + summary)
Step 9: Self-Audit (6 dimensions from pr-workflow.md)
Step 10: PR (Stage 6: push branches, create PRs)
```

## Quick Reference Table
Step | Action
0 | /superpowers:using-git-worktrees
1 | Verify input artifacts, git submodule update
2 | python tools/transfer_cli.py extract, follow prompts/stage-2-translate.md
3 | 5-perspective coverage review (convergence-review)
4 | Follow prompts/stage-3-generate.md
5 | go build, go test in target submodule
6 | go test ./tools/harness/ -run TestEquivalence (Suites A/B/C)
7 | transfer_cli.py noise-characterize, transfer_cli.py benchmark
8 | Write validation_results.json using template
9 | Self-audit: 6 dimensions
10 | /superpowers:finishing-a-development-branch

## Step-by-Step Process

### Step 0: Create Isolated Worktree
Same pattern as before — /superpowers:using-git-worktrees

### Step 1: Workspace Setup
1. Verify input artifacts exist: best_program.py, best_program_info.json, workload YAMLs in the input directory
2. Update submodules: git submodule update --init --recursive
3. Check submodule status: git submodule status — compare against mapping artifact pins
4. Create workspace/ directory if not exists

### Step 2: Extract and Translate (Pipeline Stages 1-2)
1. Run extraction: python tools/transfer_cli.py extract <input_dir>
2. Review algorithm_summary.json — verify scope_verdict, signals_used, branch_count
3. Follow prompts/stage-2-translate.md to produce signal_coverage.json
4. Verify: unmapped_signals is empty, branch_count matches Stage 1, all signals have fidelity ratings

### Step 3: Review Signal Coverage (5 perspectives)
Run convergence-review with cross-system PR plan perspectives:
1. Cross-system contract integrity — do mapped signals match actual target APIs?
2. Artifact completeness — are all signal_coverage.json fields populated?
3. Prompt template quality — was Stage 2 prompt followed correctly?
4. Code correctness — does signal_coverage.json parse correctly?
5. Plan structural validation — are stage dependencies satisfied?

Human approval gate: present coverage review for human approval before proceeding to code generation.

### Step 4: Generate (Pipeline Stage 3)
1. Follow prompts/stage-3-generate.md
2. Verify generated_files.json manifest is complete
3. All required file roles present (plugin, plugin_test, config, config_baseline, equivalence_test)
4. Code compiles: go build ./... in target submodule

### Step 5: Test (Pipeline Stage 4)
1. Run unit tests: go test ./pkg/plugins/<algorithm_name>/... in target submodule
2. Run full test suite: go test ./... in target submodule
3. On failure: LLM fixes, retry (max 3 retries per error class)
4. On success: update generated_files.json with test_passed_at and final_commit

### Step 6: Validate — Equivalence Testing (Pipeline Stage 5a)
1. Run Suite A: go test ./tools/harness/ -run TestEquivalence -suite A -tuples <path>
   - Check: Kendall-tau ≥ threshold, numeric fidelity failures ≤ threshold
2. Run Suite B: go test ./tools/harness/ -run TestEquivalence -suite B -staleness 100ms
   - Check: Rank stability tau ≥ threshold
3. Run Suite C: go test ./tools/harness/ -run TestEquivalence -suite C -parallel 4
   - Check: Parallel safety = true, pile-on max share ≤ threshold
4. If any suite fails: debug, may loop back to Step 2 or Step 4

### Step 7: Validate — Cluster Benchmarks (Pipeline Stage 5b)
1. Noise characterization: python tools/transfer_cli.py noise-characterize --cluster <id> --runs 5
2. Run benchmarks: python tools/transfer_cli.py benchmark --baseline <config> --treatment <config> --workloads <dir>
3. Check: Treatment beats baseline on matched workload by margin > 2× noise CV
4. Mechanism check: verify improvement comes from the expected signal pathway (not just noise or side effects)

### Step 8: Document Results
1. Write workspace/validation_results.json following the inter-stage schema
2. Create validation summary in Markdown
3. Record in calibration log if this is one of the first 5 transfers
4. Update any threshold estimates based on observed values

### Step 9: Self-Audit (6 dimensions)
Walk through all 6 self-audit dimensions from pr-workflow.md:
1. Cross-system accuracy — do all submodule API references match actual code?
2. Schema chain integrity — do workspace artifacts chain correctly?
3. Prompt completeness — were all prompt templates followed completely?
4. CLI contract — do all CLI outputs match documented schemas?
5. Artifact consistency — do signal names match across all files?
6. Dead artifact prevention — does every created file have a consumer?

Fix all issues found before proceeding.

### Step 10: PR Creation (Pipeline Stage 6)
1. Push target submodule branch
2. Create PR against target repo with validation summary
3. Push sim2real branch with workspace artifacts
4. Create sim2real PR documenting the transfer
5. Link PRs together

## Universal Convergence Protocol
> **Canonical source:** docs/contributing/convergence.md. If this section diverges, convergence.md is authoritative.

Brief summary: run all N perspectives in parallel, fix CRITICAL/IMPORTANT, re-run until zero CRITICAL + zero IMPORTANT. Max 10 rounds.

## Quality Gates

### Pre-Generation Gates (check before Step 4)
- [ ] algorithm_summary.json exists and scope_verdict != "reject"
- [ ] signal_coverage.json complete with zero unmapped_signals
- [ ] branch_count matches between Stage 1 and Stage 2
- [ ] All signal fidelity ratings present (High/Medium/Upgrade only)
- [ ] Submodule commit pins match or staleness acknowledged
- [ ] Coverage review converged

### Pre-Validation Gates (check before Step 6)
- [ ] Generated code compiles (go build passes)
- [ ] All unit tests pass (go test passes)
- [ ] generated_files.json manifest complete with all required roles
- [ ] test_passed_at and final_commit recorded

### Final Gates (check before Step 10)
- [ ] Suite A passes (Kendall-tau + numeric fidelity)
- [ ] Suite B passes (rank stability under staleness)
- [ ] Suite C passes (parallel safety)
- [ ] Cluster benchmark mechanism check passes (or INCONCLUSIVE with user override)
- [ ] validation_results.json complete with overall_verdict
- [ ] Self-audit completed (6 dimensions)
- [ ] Calibration log entry added (if within first 5 transfers)

## References
- Transfer validation standards: docs/contributing/standards/experiments.md
- Transfer validation template: docs/contributing/templates/transfer-validation.md
- v3 pipeline design: docs/plans/2026-03-06-sim2real-transfer-design-v3.md
- PR workflow: docs/contributing/pr-workflow.md
- Convergence protocol: docs/contributing/convergence.md
