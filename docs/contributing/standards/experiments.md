# Transfer Validation Standards

**Status:** Active (v1.0 — 2026-03-11)

Transfer validation is a first-class activity in sim2real — equal in rigor to mapping artifact authoring and pipeline implementation. The validation framework is grounded in the three analysis questions from the [v3 transfer pipeline design](../../plans/2026-03-06-sim2real-transfer-design-v3.md).

---

## Transfer Validation Framing

Every transfer validation answers three analysis questions:

| Question | Scope | Pipeline stage |
|----------|-------|----------------|
| **Q1:** Does the sim-discovered algorithm's benefit survive the abstraction gap? | End-to-end | Cluster benchmarks (Stage 5) |
| **Q2:** Which simulation signals have production equivalents that preserve the algorithm's mechanism? | Per-signal | Suites A, B, C (Stage 5) |
| **Q3 (future):** What is the minimum fidelity needed for the benefit to transfer? | Sensitivity | Not yet implemented |

### Question-to-verdict mapping

| Question | Verdicts that answer it | Evidence source |
|----------|------------------------|-----------------|
| Q1 | Cluster benchmark verdict (PASS/FAIL) + mechanism check | Per-workload benchmark results |
| Q2 | Suite A + Suite B + Suite C verdicts | Signal-level fidelity, staleness, and concurrency tests |
| Q3 | (future) Fidelity sensitivity sweep | Threshold ablation experiments |

### Definition of a successful transfer

A transfer is successful when `overall_verdict = PASS`, which requires:
- All individual suite verdicts (A, B, C) are PASS
- Cluster benchmark verdict is PASS
- Mechanism check confirms the improvement comes from the expected mechanism

The `overall_verdict` is computed by the validation pipeline (Stage 5) and recorded in the validation report artifact.

---

## Validation Categories

Five validation categories replace the six DES hypothesis families. Each category tests a distinct aspect of transfer fidelity.

| Category | Tests | Shape | Evidence required |
|----------|-------|-------|-------------------|
| **Suite A (fidelity)** | Signal mapping preserves ranking and boundary behavior | Per-signal + aggregate | Kendall-tau >= threshold, numeric fidelity within tolerance |
| **Suite B (staleness)** | Signal mapping robust to async metric delays | Per-signal under delay injection | Rank stability tau >= threshold |
| **Suite C (concurrency)** | Sequential-test results hold under concurrent requests | Aggregate | Parallel safety pass, pile-on max share <= threshold |
| **Cluster benchmarks** | Sim-predicted benefit appears in production | Per-workload | Treatment beats baseline on matched workload, mechanism check PASS |
| **Noise characterization** | Baseline measurement variability | Aggregate | CV documented, threshold computed |

### Suite A — Fidelity

Tests whether the signal mapping (Stage 2 output) preserves the ranking and boundary behavior of the original simulation signals. For each mapped signal pair, Suite A generates threshold-boundary tuples and compares the sim signal ranking against the production signal ranking.

- **Kendall-tau rank correlation**: Measures whether the production signal preserves the relative ordering of configurations that the sim signal distinguishes.
- **Numeric fidelity**: Checks that absolute signal values at algorithm threshold boundaries remain within tolerance.

### Suite B — Staleness

Tests whether the signal mapping remains valid when production metrics arrive with realistic delays. Suite B injects synthetic staleness delays into the metric pipeline and re-runs Suite A's ranking tests.

- **Delay injection**: Configurable synthetic delay (default 100ms) applied to metric reads.
- **Rank stability**: Kendall-tau computed under delayed conditions; must remain above threshold.

### Suite C — Concurrency

Tests whether validation results obtained under sequential test execution hold when the system processes concurrent requests.

- **Parallel safety**: Verifies no data races or inconsistent state under concurrent access.
- **Pile-on detection**: Measures whether concurrent requests disproportionately route to a single target (pile-on max share).

### Cluster benchmarks

Runs baseline vs treatment configurations on a real cluster, comparing per-workload metrics. The baseline uses the production default; the treatment applies the sim-discovered algorithm.

- **Per-workload comparison**: Treatment must beat baseline on the primary metric for matched workloads.
- **Mechanism check**: Verifies the improvement comes from the expected mechanism (e.g., the algorithm's scoring function, not load shedding or routing artifacts). A mechanism check failure means the benefit is real but unexplained, which blocks promotion.

### Noise characterization

Measures baseline variability before interpreting benchmark results. Uses `transfer_cli.py noise-characterize` to compute the coefficient of variation (CV) across repeated baseline runs.

- **CV documentation**: Records the measured CV for each primary metric.
- **Threshold computation**: Derives the minimum detectable effect size (2x CV) for statistical significance.

---

## Transfer Validation Design Rules (TV-1 through TV-6)

These six design rules replace the DES experiment design rules (ED-1 through ED-6). They encode lessons specific to cross-system transfer validation.

### TV-1: Controlled comparison

Vary exactly one dimension between baseline and treatment configurations. Everything else held constant: same cluster, same workload, same metric collection interval, same submodule commit pins. If the validation requires varying multiple dimensions, decompose into separate sub-experiments.

### TV-2: Boundary coverage

Test tuples must include values at algorithm threshold boundaries, not just values in the "easy" middle of the range. If the sim-discovered algorithm switches behavior at a threshold (e.g., queue depth > N), the validation tuples must include values near N. Missing boundary coverage can produce a false PASS — the mapping works in the interior but fails at the decision boundary where it matters most.

### TV-3: Noise floor prerequisite

Characterize baseline noise (CV) before interpreting benchmark results. Run `transfer_cli.py noise-characterize` and record the CV. Any benchmark improvement smaller than 2x the measured CV is not statistically distinguishable from noise. This step prevents false positives from noisy environments.

### TV-4: Workload match

Cluster benchmarks must use workloads that match the algorithm's demonstrated advantage in simulation. If the sim experiment showed benefit under high-concurrency bursty traffic, the cluster benchmark must use a comparable workload — not a steady-state low-rate workload where any algorithm performs similarly.

### TV-5: Reproducibility

Same tuples + same code + same commit = identical validation results. Every validation run must record:
- Exact submodule commit pins (`git submodule status`)
- Mapping artifact version (commit hash)
- CLI command and arguments used
- Tuple generation seed (if randomized)

Re-running with these inputs must produce byte-identical suite verdicts.

### TV-6: Staleness acknowledgment

If the mapping artifact's submodule commit pins differ from the current submodule HEAD, document the drift and its impact assessment. Stale pins mean the mapping was authored against a different API version. The validation may still pass, but the drift must be explicitly acknowledged — not silently ignored.

**How to check:** Compare `git submodule status` output against the commit pins recorded in the mapping artifact. Any mismatch requires a documented assessment: either update the pins and re-validate, or explain why the drift does not affect the mapped signals.

---

## Transfer Verification Thresholds

Initial thresholds for each validation category. These are starting values subject to calibration (see [Calibration Procedure](#calibration-procedure)).

| Category | Metric | Threshold |
|----------|--------|-----------|
| **Suite A** | Kendall-tau rank correlation | >= 0.8 |
| **Suite A** | Numeric fidelity failures | <= 5% of tuples |
| **Suite B** | Rank stability tau (under 100ms synthetic delay) | >= 0.7 |
| **Suite C** | Parallel safety | true |
| **Suite C** | Pile-on max share | <= 0.4 |
| **Cluster benchmarks** | Treatment improvement margin | > 2x noise CV |
| **Cluster benchmarks** | Mechanism check | PASS |

**Note:** These thresholds are initial values chosen conservatively. They will be refined through the calibration procedure as transfer data accumulates. A threshold that is too tight will produce false FAILs; too loose will let bad mappings through. The calibration log provides the data to find the right balance.

---

## Calibration Procedure

Thresholds are not permanent. They must be calibrated against real transfer outcomes.

### Process

1. **Initial thresholds** are set conservatively (as documented above).
2. **After each transfer**, record actual metrics in the calibration log (`docs/calibration/transfer-metrics.csv` or equivalent).
3. **Bootstrapping safeguard**: For the first 3 transfers, both Suite A failure and Suite A pass trigger mandatory human review. This prevents premature trust in uncalibrated thresholds — a PASS might be a false PASS with thresholds that are too loose.
4. **After 5+ transfers**, analyze the calibration log to tighten or relax thresholds with statistical evidence. Use the accumulated data to identify thresholds that are too conservative (many false FAILs) or too permissive (false PASSes that fail in production).
5. **Three-transfer review**: After 3 completed transfers, conduct a dedicated threshold review using all accumulated data. This is the earliest point at which threshold adjustments are justified.

### Calibration log fields

Each transfer adds a row with:
- Transfer ID, date, algorithm name
- Suite A/B/C actual metric values (not just PASS/FAIL)
- Cluster benchmark margin and mechanism check result
- Noise CV measured
- Overall verdict
- Human assessment (did the verdict match production reality?)

---

## Transfer Outcome Taxonomy

Every transfer resolves to one of four outcomes. These replace the DES hypothesis resolution categories.

| Outcome | Definition | Action |
|---------|-----------|--------|
| **PASS** | All suites pass AND cluster benchmark mechanism check passes | Proceed to Stage 6 (PR creation). The sim-discovered algorithm is validated for production. |
| **PARTIAL** | Some suites pass, cluster benchmark inconclusive | Human review of failed suites. May proceed with documented limitations if the failures are understood and accepted. |
| **FAIL** | Any suite fails OR mechanism check fails | Debug and iterate. Investigate root cause — may need to revisit Stage 2 (Translate) to fix the signal mapping, or Stage 3 (Implement) to fix the production implementation. |
| **ABORT** | Scope check failure (low-fidelity signals) or breaking API changes | Cannot proceed. Requires experiment redesign or mapping artifact changes. An ABORT is not a bug — it means the transfer was attempted against signals or APIs that cannot support it. |

### Outcome selection guide

```
All suites PASS and mechanism check PASS?
  -> Yes: PASS
  -> No:
    Did the scope check fail or did breaking API changes block execution?
      -> Yes: ABORT
      -> No:
        Did any suite FAIL or mechanism check FAIL?
          -> Yes: FAIL
          -> No (some inconclusive): PARTIAL
```

### PARTIAL outcomes require documentation

A PARTIAL outcome is not a shortcut. The documentation must include:
- Which suites passed and which failed or were inconclusive
- Root cause analysis for each non-passing suite
- Justification for why proceeding is acceptable despite incomplete validation
- Limitations that must be communicated in the Stage 6 PR description

---

## Iterative Review Protocol

> **Canonical source:** [`docs/contributing/convergence.md`](../convergence.md). If this section diverges, convergence.md is authoritative.

Transfer validation reviews follow the universal convergence protocol: zero CRITICAL + zero IMPORTANT findings from all reviewer perspectives to converge. SUGGESTION-level items do not block convergence. See [convergence.md](../convergence.md) for the full protocol, severity definitions, max round limits, and agent failure handling. See [pr-workflow.md](../pr-workflow.md) for perspective assignments by PR category.
