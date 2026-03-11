# Transfer Validation Results Template

> **For Claude:** Use this template when documenting transfer validation results in `workspace/` or as a standalone validation summary.

## Validation Results Structure

Every transfer's validation documentation MUST contain these sections:

```
# Transfer Validation: <Algorithm Name>

**Transfer Type:** <routing | admission | priority>
**Source:** <input directory, e.g., routing/>
**Target System:** <e.g., llm-d-inference-scheduler>
**Date:** YYYY-MM-DD
**Pipeline Commit:** <sim2real repo commit hash at Stage 1>
**Overall Verdict:** PASS | PARTIAL | FAIL | ABORT

## Transfer Metadata

- **Algorithm:** <brief description of the evolved algorithm>
- **Input Artifacts:** <list of input files>
- **Scope Verdict:** pass | marginal (with marginal_ops listed) | reject
- **Signals Used:** <count> signals, all rated High/Medium/Upgrade
- **Branch Count:** <number of conditional branches>
- **Matched Workload:** <workload with highest improvement>
- **Submodule Pins:**
  - llm-d-inference-scheduler: <commit hash>
  - llm-d-benchmark: <commit hash> (if applicable)
  - inference-sim: <commit hash>
- **Staleness Status:** current | acknowledged (with drift range and review summary)

## Signal Coverage Summary

| Signal | Sim Field | Prod Field | Fidelity | Staleness Window | Notes |
|--------|-----------|------------|----------|------------------|-------|
| <signal 1> | <sim_field> | <prod_field> | High/Medium/Upgrade | <ms> | <notes> |

**Unmapped signals:** none (required — Stage 3 cannot proceed with unmapped signals)
**Scorer overlap:** <list of existing scorers with shared signals and recommendations>

## Suite A Results (Fidelity)

| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Kendall-tau (rank correlation) | <value> | ≥ 0.8 | Yes/No |
| Numeric fidelity failures | <count>/<total> tuples | ≤ 5% | Yes/No |
| Total test tuples | <count> | — | — |
| Boundary tuples included | <count> | ≥ 1 per branch | — |

**Per-signal fidelity:**
| Signal | Rank preserved? | Numeric fidelity | Notes |
|--------|:---:|---|---|

**Suite A verdict:** PASS | FAIL

## Suite B Results (Staleness)

| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Rank stability (Kendall-tau) | <value> | ≥ 0.7 | Yes/No |
| Threshold crossing change | <pct>% | — (informational) | — |
| Synthetic delay injected | <ms> | — | — |

**Suite B verdict:** PASS | FAIL
**Note for v1:** All v1 signals use the approximate (router-side) scorer with zero collection latency. Suite B is expected to pass trivially. Infrastructure exists for future precise-scorer transfers.

## Suite C Results (Concurrency)

| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Parallel safety | true/false | true | Yes/No |
| Pile-on max share | <value> | ≤ 0.4 | Yes/No |
| Concurrent workers | <count> | — | — |

**Suite C verdict:** PASS | FAIL

## Cluster Benchmark Results

**Noise characterization:**
- Baseline runs: <N>
- Coefficient of variation (CV): <value>
- Significance threshold: 2× CV = <value>

**Per-workload results:**
| Workload | Baseline | Treatment | Improvement | Is Matched? | Significant? |
|----------|----------|-----------|-------------|:-----------:|:------------:|
| <workload 1> | <value> | <value> | <pct>% | Yes/No | Yes/No |

**Mechanism check:**
- Expected mechanism: <what the algorithm should be doing>
- Observed behavior: <what actually happened>
- Mechanism check result: PASS | FAIL | INCONCLUSIVE

**Cluster benchmark verdict:** PASS | FAIL | INCONCLUSIVE
**If INCONCLUSIVE:** <reason and user override decision>

## Overall Verdict

| Component | Verdict |
|-----------|---------|
| Suite A (fidelity) | PASS/FAIL |
| Suite B (staleness) | PASS/FAIL |
| Suite C (concurrency) | PASS/FAIL |
| Cluster benchmark | PASS/FAIL/INCONCLUSIVE |
| **Overall** | **PASS/PARTIAL/FAIL** |

**Verdict rationale:** <1-2 sentences explaining the overall verdict>

## Calibration Log Entry

**Transfer number:** <sequential number, e.g., 1 of target 5>
**Actual thresholds observed:**
- Suite A Kendall-tau: <actual value> (threshold was 0.8)
- Suite B rank stability: <actual value> (threshold was 0.7)
- Suite C pile-on share: <actual value> (threshold was 0.4)
- Benchmark improvement: <actual %> (noise CV was <value>)

**Threshold adjustment recommendation:** <none | tighten X to Y because Z | relax X to Y because Z>

## Scope and Limitations

- **Algorithm type tested:** <e.g., conditional linear combination of routing signals>
- **Signals covered:** <list>
- **Signals NOT covered:** <any signals deferred or out of scope>
- **Target system version:** <pinned commit>
- **Cluster configuration:** <for benchmarks — nodes, GPUs, models>
- **What was NOT tested:** <parameter ranges, workloads, configs not covered>
- **Generalizability:** <does this transfer pattern generalize, or is it specific?>

## Standards Audit

Findings checked against docs/contributing/standards/:
- [ ] R1 (Submodule pin accuracy): All API references verified against pinned code? <result>
- [ ] R2 (Schema chain integrity): All workspace artifacts chain correctly? <result>
- [ ] R5 (Cross-artifact consistency): Signal names match everywhere? <result>
- [ ] R7 (Fidelity ratings): All signals rated? <result>
- [ ] R8 (Branch-count preservation): Branch count matches between stages? <result>
- [ ] R9 (Boundary coverage): Threshold boundary tuples included? <result>
- [ ] R10 (Noise characterization): Baseline noise characterized before benchmarks? <result>
- [ ] INV-1 (Signal-set conservation): No signals gained or lost? <result>
- [ ] INV-2 (Branch-count consistency): Stage 1 count == Stage 2 count? <result>
- [ ] INV-7 (No-op default): Plugin disabled config produces pre-transfer behavior? <result>
```

## Generated Files Manifest

Reference: `workspace/generated_files.json` structure per v3 design §Generate stage.

## Reproducing

```bash
# Full pipeline re-run
python tools/transfer_cli.py extract <input_dir>
# Follow prompts/stage-2-translate.md
# Follow prompts/stage-3-generate.md
go test ./tools/harness/ -run TestEquivalence
python tools/transfer_cli.py benchmark --baseline <config> --treatment <config>
```
