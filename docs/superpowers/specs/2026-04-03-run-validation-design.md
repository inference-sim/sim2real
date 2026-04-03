# Design: Run Validation (`scripts/validate.py`)

**Date:** 2026-04-03
**Status:** Approved

## Problem

The sim2real pipeline currently has no systematic way to verify that a cluster run faithfully
reproduces the simulation conditions. Questions that cannot currently be answered:

- Did the workload that actually ran match the declared arrival rate, burstiness, and token
  distributions?
- Are the SLO class ratios and tenant counts correct?
- Is the vLLM deployment configured with the exact parameters from `llm_config.yaml`?
- Are all signals used by the algorithm available in real llm-d with acceptable staleness?
- Is the routing policy configured correctly for baseline vs. treatment?
- Does each workload run on its own isolated stack (no shared mutable state between runs)?

## Approach: Three-Phase Validation Script

`scripts/validate.py` with three subcommands backed by a shared `scripts/lib/validate_checks.py`
module. Phases run at different points in the pipeline lifecycle:

1. **pre-deploy** — static checks against existing artifacts, before any cluster deployment
2. **post-deploy** — live cluster probes against a running stack (operator-invoked or
   integrated into a future Tekton validation task)
3. **post-collection** — audit of collected trace CSVs after the benchmark completes

`deploy.py` calls `run_pre_deploy_checks` directly from `stage_benchmarks` (before the phase
loop) for phase 1. Phases 2 and 3 are operator-invoked as standalone commands; phase 2 is
designed for manual use against a running stack (e.g., during debugging) because stacks are
ephemeral inside Tekton PipelineRuns and there is no deploy.py hook between "stack ready" and
"workload starts". Phase 3 is also callable automatically after `_extract_phase_results`.

This follows the existing pattern established by `scripts/lib/manifest.py` — reusable check
logic lives in the lib module, testable independently of the CLI.

## Files

```
scripts/
  validate.py               # CLI entry point — three subcommands
  lib/
    validate_checks.py      # All check primitives (importable, testable)
```

No changes to `transfer_cli.py`. The live cluster checks (kubectl, Prometheus) don't fit the
CLI's cluster-agnostic design.

## Phase 1: Pre-Deploy (Static)

**Invocation:** `python scripts/validate.py pre-deploy --run NAME`

Reads: `workspace/runs/{run}/prepare_tekton/values.yaml`,
`workspace/runs/{run}/prepare_signal_coverage.json`,
`blis_router/llm_config.yaml`

### Workload checks

For each workload in `values.yaml` `observe.workloads`, parse the embedded spec YAML string:

- `aggregate_rate` is present and > 0
- Each client has `slo_class` in `{critical, standard, sheddable, batch}`; all values must be
  a subset of the declared `inferenceObjectives` names in `values.yaml` (case-sensitive match)
- Every `inferenceObjectives[].name` must be used by at least one client's `slo_class` (warn
  if an objective is declared but never exercised by any workload)
- Each client with `arrival.process: gamma` has a `cv` field
- Sum of `rate_fraction` across all clients ≈ 1.0 (tolerance ±0.01)
- `num_requests` is present and > 0
- All workload names are unique within `observe.workloads`

### vLLM config checks

Search `values.yaml` `stack.model.helmValues.decode.containers` for the container with
`modelCommand: vllmServe` (not by index — there may be sidecars). Fail if no such container
is found. Compare its `args` against `blis_router/llm_config.yaml`:

| Deployed value | `llm_config.yaml` field |
|---|---|
| `--gpu-memory-utilization` arg | `vllm_config.gpu_memory_utilization` |
| `--max-num-seqs` arg | `vllm_config.max_num_running_reqs` |
| `--max-num-batched-tokens` arg | `vllm_config.max_num_scheduled_tokens` |
| `--block-size` arg | `vllm_config.block_size_in_tokens` |
| `stack.model.helmValues.decode.replicas` | `cluster.num_instances` |
| `stack.model.helmValues.decode.parallelism.tensor` | `serving.tensor_parallelism` |
| `stack.model.modelName` | `model.id` (case-insensitive) |

### Signal checks

Read `prepare_signal_coverage.json`:

- All signals have `mapped: true` and `fidelity_rating` ≠ `"low"`
- `coverage_complete: true`
- `totalInFlight` (maps to `RunningRequestsSize`) → `staleness_window_ms: 0` (router-local,
  fresh every call)
- Prometheus-backed signals (QueueDepth, KVUtilization, FreeKVBlocks, MaxKVUtil) →
  `staleness_window_ms` ≤ 5000
- `sloClass` (ObjectiveKey) and `tenantID` (FairnessID) are present and mapped

### Routing policy check

Parse the `EndpointPickerConfig` YAML embedded in `values.yaml` for each phase:

- **Baseline:** `load-aware-scorer`, `decode-filter`, and `max-score-picker` all present in
  the plugins list; all three are included in the default scheduling profile — matches GAIE
  3:2:2 defaults
- **Treatment:** `schedulingProfiles` is present and non-empty; at least one profile references
  the custom scorer plugin. `values.yaml` `stack.gaie.treatment.admissionPolicy` exists and
  is non-empty (confirms the admission policy CRD will be applied).

### Isolation check

Verify all workload names in `observe.workloads` are unique (no two workloads share a name
that would collide when each gets its own stack).

---

## Phase 2: Post-Deploy, Pre-Benchmark (Live Cluster)

**Invocation:** `python scripts/validate.py post-deploy --run NAME --namespace NS --phase baseline|treatment`

Operator-invoked against a running stack. Because stacks are deployed and torn down entirely
inside Tekton PipelineRuns, deploy.py has no automatic window between "stack ready" and
"workload starts". This subcommand is intended for manual verification against long-lived
stacks deployed outside Tekton (e.g., via `helm install` directly for debugging), or as a
future Tekton task step inserted after model deploy and before workload run. For standard
Tekton-based runs, post-deploy checks cannot be run automatically because the stack is torn
down before an operator can invoke the CLI.

### Signal liveness

For each signal in `prepare_signal_coverage.json` that is Prometheus-backed
(`staleness_window_ms` > 0), query Prometheus (`GET /api/v1/query`) to confirm the metric
series exists with a data point within the last 30 seconds. Fail if any such signal has no
recent data.

Prometheus endpoint is resolved from the namespace via `kubectl get svc -n {namespace}` with
label `app=prometheus` or equivalent, or passed via `--prometheus-url`. If the label query
returns no services, the check fails with: "Prometheus service not found in namespace
{namespace}. Pass --prometheus-url explicitly if Prometheus is deployed outside the phase
namespace." (Prometheus may live in a cluster-wide monitoring namespace.)

### Prometheus staleness

Query `scrape_configs` via the Prometheus config API (`/api/v1/status/config`) or via the
ServiceMonitor in the namespace. Find the job(s) targeting the decode pods (label selector
`app=vllm` or equivalent). Verify all matching jobs have `scrape_interval` ≤ 5s. If a job
has no explicit interval, check the global default. Fail if any job exceeds 5s.

### Model loaded

`GET /v1/models` on the vLLM service endpoint (resolved from `kubectl get svc` matching the
model name label in the namespace). Verify the returned model `id` matches
`values.yaml` `stack.model.modelName` (case-insensitive).

### Stack readiness

Query `kubectl get deployment -n {namespace} -l sim2real-phase={phase}` and verify at least
one EPP deployment is Ready. This confirms the stack is up before proceeding with probes.

---

## Phase 3: Post-Collection Trace Audit

**Invocation:** `python scripts/validate.py post-collection --run NAME --phase baseline|treatment`

Reads `workspace/runs/{run}/deploy_{phase}_log/{workload}/trace_data.csv` and
`trace_header.yaml` for each workload. Compares against the workload spec embedded in
`values.yaml`.

Callable manually or automatically from `deploy.py` after `_extract_phase_results` completes.

### Arrival rate

Compute actual RPS: `request_count / ((max(arrival_time_us) - min(arrival_time_us)) / 1e6)`.
Compare against spec `aggregate_rate`. Tolerance: ±10%.

### Burstiness (CV)

Compute inter-arrival time CV per `client_id` group from `arrival_time_us` column, sorted
ascending. CV = `std(inter_arrival) / mean(inter_arrival)`.
Compare against spec `arrival.cv` for each client. Tolerance: ±20% relative.
(Gamma processes have natural variance; tighter tolerance would produce false positives.)
Clients with `arrival.process: poisson` are checked for CV ≈ 1.0 ±0.3.

### Token distributions

Per client group (matched by `client_id`), compute mean `input_tokens` and mean
`output_tokens`. Compare against spec gaussian `mean`. Tolerance: ±15% relative.

### SLO class ratios

Compute actual fraction per `slo_class` from row counts. Compare against spec `rate_fraction`
per class (summed across clients sharing a class). Tolerance: ±0.03 absolute.

### Tenant count

Verify distinct `tenant_id` values in the trace match the set of `tenant_id` fields declared
across all clients in the spec.

### Request count

Verify CSV row count against spec `num_requests`. Let `ratio = row_count / num_requests`:
- `ratio > 1.05`: **FAIL** (workload generator produced more requests than specified — bug)
- `ratio < 0.70`: **FAIL** (too many requests missing — workload runner likely crashed or
  was truncated)
- `0.70 ≤ ratio < 0.95`: **WARN** (expected for admission-controlled workloads where
  significant shedding occurs)
- `0.95 ≤ ratio ≤ 1.05`: **PASS**

### Header extraction

Verify `slo_class` and `tenant_id` columns in the trace are non-empty for all rows (no blank
or null values). This confirms request headers were extracted successfully by the EPP metadata
handler during the run.

### Model identity

`trace_header.yaml` `server.model` matches `values.yaml` `stack.model.modelName`
(case-insensitive).

### Mode field

`trace_header.yaml` `mode` equals `real`. The `mode` field distinguishes real cluster traces
(`real`) from simulation replays (`sim`). This check confirms the trace was produced by a
live cluster run, not a post-hoc simulation replay accidentally placed in the run directory.

### Workload seed

`trace_header.yaml` `workload_seed` matches spec `seed` field.

---

## Output Format

Each subcommand writes a JSON report to `workspace/runs/{run}/`:

- `validate_pre_deploy.json`
- `validate_post_deploy_{phase}.json`
- `validate_post_collection_{phase}.json`

All check groups share a consistent item structure:

```json
{
  "phase": "pre_deploy",
  "run": "admin4",
  "timestamp": "2026-04-03T18:00:00Z",
  "overall": "PASS",
  "checks": {
    "workloads": {
      "passed": true,
      "items": [
        {"name": "workload_bursty_adversary", "passed": true, "notes": []},
        {"name": "workload_overload_mixed_slo", "passed": true, "notes": []}
      ]
    },
    "vllm_config": {
      "passed": false,
      "items": [
        {"field": "--gpu-memory-utilization", "expected": "0.9", "actual": "0.85",
         "passed": false, "notes": []}
      ]
    },
    "signals": {
      "passed": true,
      "items": [
        {"name": "totalInFlight", "passed": true, "notes": []}
      ]
    },
    "routing_policy": {"passed": true, "items": [], "notes": []},
    "isolation": {"passed": true, "items": [], "notes": []}
  }
}
```

`overall` is `"PASS"` only if all checks with `passed: false` would cause a FAIL exit (not
WARN). Items with severity `"warn"` are reported but do not flip `overall` to `"FAIL"`.

Terminal output uses `[PASS]` / `[FAIL]` / `[WARN]` lines in the same color style as
`deploy.py` and `analyze.py`.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All checks passed (warnings are allowed) |
| `1` | One or more checks failed — operator must review report |
| `2` | Infrastructure error — missing artifact, parse failure, kubectl/Prometheus unreachable |

## deploy.py Integration

`deploy.py` integrates with validate_checks at two points:

1. **Pre-deploy gate** — `stage_benchmarks` calls `run_pre_deploy_checks(run_dir)` once,
   after the `benchmark-state` initialization block and before the `phases_to_run` loop
   (before any `_run_single_phase` calls). Exits with error on `FAIL`. Reads
   `run_dir / "prepare_tekton" / "values.yaml"`,
   `run_dir / "prepare_signal_coverage.json"`, and `REPO_ROOT / "blis_router" / "llm_config.yaml"`.

2. **Post-collection** — `_run_single_phase` calls `run_post_collection_checks(phase, run_dir)`
   immediately after `_extract_phase_results` returns (before the final `ok()` message), for
   baseline and treatment phases only (not noise). Reports WARN/FAIL to terminal but does not
   halt the pipeline — the phase is still marked done. Operator reviews the report.

Phase 2 (post-deploy) is not automatically called from deploy.py because stacks are ephemeral
inside Tekton PipelineRuns with no external hook between stack-ready and workload-start.

## Change Surface

| Component | Changes |
|---|---|
| `scripts/validate.py` | New file — three subcommands |
| `scripts/lib/validate_checks.py` | New file — all check primitives |
| `scripts/deploy.py` | Two new call sites: pre-deploy gate in `stage_benchmarks`; post-collection in `_run_single_phase` |
| `workspace/runs/{run}/` | Up to four new report JSON files per run |
