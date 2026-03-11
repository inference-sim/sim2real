# Sim2Real Pipeline Invariants

Invariants are properties that must hold throughout the 6-stage transfer pipeline (Extract, Translate, Generate, Test, Validate, PR). They are verified by pipeline tooling and checked during self-audit (Step 8 in the PR workflow).

**Design traceability:** All invariants trace to the v3 transfer pipeline design (`docs/plans/2026-03-06-sim2real-transfer-design-v3.md`). Section references below point to that document.

**Self-audit mapping:** These invariants relate to the 6 self-audit dimensions in `docs/contributing/pr-workflow.md` as follows:

| Invariant | Self-audit dimension(s) |
|-----------|------------------------|
| INV-1 Signal-set conservation | 2. Schema chain integrity, 5. Artifact consistency |
| INV-2 Branch-count consistency | 2. Schema chain integrity |
| INV-3 Stage sequencing | 2. Schema chain integrity |
| INV-4 Artifact causality | 2. Schema chain integrity |
| INV-5 Commit pin freshness | 1. Cross-system accuracy |
| INV-6 Suite A reproducibility | 4. CLI contract |
| INV-7 No-op default | 4. CLI contract |
| INV-8 Dead artifact prevention | 6. Dead artifact prevention |

---

## INV-1: Signal-Set Conservation

**Statement:** `signals_used` from Stage 1 (Extract) == `mappings` entries in Stage 2 (Translate). No signals gained or lost in translation.

**Verification:** `transfer_cli.py validate-mapping` checks signal set consistency. Stage 2 halts if `unmapped_signals` is non-empty.

**Evidence:** v3 design, Inter-stage artifact contracts — `algorithm_summary.json` `signals_used` array must match `signal_coverage.json` `mappings` array by signal name.

---

## INV-2: Branch-Count Consistency

**Statement:** Stage 1 `branch_count` == Stage 2 `branch_count`. The translation must preserve the algorithm's conditional structure.

**Verification:** Stage 2 outputs `branch_count` which must match Stage 1's `algorithm_summary.json` `branch_count`.

**Evidence:** v3 design, Inter-stage artifact contracts.

---

## INV-3: Stage Sequencing

**Statement:** Strict stage ordering 1 -> 2 -> 3 -> 4 -> 5 -> 6. No stage reads artifacts that haven't been written by a prior stage.

**Verification:** Each stage validates its input artifact exists and is structurally valid before processing.

**Evidence:** v3 design, Pipeline Stages — each stage's input column traces to a prior stage's output column.

---

## INV-4: Artifact Causality

**Statement:** `completed_at` timestamps in workspace artifacts are strictly ordered: Stage N completes before Stage N+1 begins.

**Verification:** Each stage records completion timestamp; consuming stage verifies predecessor timestamp exists.

**Evidence:** v3 design, Template stability — `pipeline_commit` tracked from Stage 1 onward.

---

## INV-5: Commit Pin Freshness

**Statement:** The mapping artifact's pinned commit hash matches the submodule HEAD, or staleness has been explicitly acknowledged with a review summary.

**Verification:** Stage 2 compares mapping artifact pin against `git submodule status`; proceeds only if matching or `staleness_acknowledged: true`.

**Evidence:** v3 design, Dependencies — staleness guard at Stage 2 entry.

---

## INV-6: Suite A Reproducibility

**Statement:** Same test tuples + same code at same commit = identical score vectors from the Go harness.

**Verification:** Run `go test ./tools/harness/ -run TestEquivalence` twice with same inputs; diff outputs.

**Evidence:** v3 design, Validate stage — Suite A uses deterministic tuples, no randomness.

---

## INV-7: No-Op Default

**Statement:** When the generated plugin is disabled (using the `_disabled.yaml` config), the target system behaves identically to the pre-transfer state.

**Verification:** Stage 5 compares baseline config behavior against pre-transfer behavior; Suite C checks for no performance regressions with plugin disabled.

**Evidence:** v3 design, Generate stage — `config_baseline` role produces disabled config.

---

## INV-8: Dead Artifact Prevention

**Statement:** Every file created by the pipeline has at least one consumer (a later stage, a test, or the final PR).

**Verification:** Self-audit before commit; `generated_files.json` manifest tracks all created files and their roles.

**Evidence:** v3 design, Inter-stage artifact contracts — every output traces to an input. Self-audit dimension 6 in `pr-workflow.md`.
