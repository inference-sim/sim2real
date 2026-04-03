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
- Are all signals used by the algorithm available in the live cluster with acceptable staleness?
- Is the routing policy configured correctly for baseline vs. treatment?
- Does each workload run on its own isolated stack (no shared mutable state between runs)?

## Approach: Three-Phase Validation Script

`scripts/validate.py` with three subcommands backed by a shared `scripts/lib/validate_checks.py`
module. Phases run at different points in the pipeline lifecycle:

1. **pre-deploy** — static checks against existing artifacts, before any cluster deployment
2. **post-deploy** — live cluster probes after the stack is up, before the workload runs
3. **post-collection** — audit of collected trace CSVs after the benchmark completes

`deploy.py` calls the lib functions directly (no subprocess) for phases 1 and 2.
Phase 3 is operator-invoked as a standalone command.

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
`blis_router/llm_config.yaml`, `workspace/runs/{run}/prepare_plugin.go`

### Workload checks

For each workload in `values.yaml` `observe.workloads`, parse the embedded spec YAML string:

- `aggregate_rate` is present and > 0
- Each client has `slo_class` in `{critical, standard, sheddable, batch}`; must be a subset of
  the declared `inferenceObjectives` names in `values.yaml`
- Each client with `arrival.process: gamma` has a `cv` field
- Sum of `rate_fraction` across all clients ≈ 1.0 (tolerance ±0.01)
- `num_requests` is present and > 0

### vLLM config checks

Compare args in `values.yaml` `stack.model.helmValues.decode.containers[0].args` against
`blis_router/llm_config.yaml`:

| Deployed arg | `llm_config.yaml` field |
|---|---|
| `--gpu-memory-utilization` | `vllm_config.gpu_memory_utilization` |
| `--max-num-seqs` | `vllm_config.max_num_running_reqs` |
| `--max-num-batched-tokens` | `vllm_config.max_num_scheduled_tokens` |
| `--block-size` | `vllm_config.block_size_in_tokens` |
| `decode.replicas` | `cluster.num_instances` |
| `decode.parallelism.tensor` | `serving.tensor_parallelism` |
| `stack.model.modelName` | `model.id` (case-insensitive) |

### Signal checks

Read `prepare_signal_coverage.json`:

- All signals have `mapped: true` and `fidelity_rating` ≠ `"low"`
- `coverage_complete: true`
- `totalInFlight` (InFlightRequests) → `staleness_window_ms: 0` (router-local, fresh every call)
- Prometheus-backed signals (QueueDepth, KVUtilization, FreeKVBlocks, MaxKVUtil) →
  `staleness_window_ms` ≤ 5000
- `sloClass` (ObjectiveKey) and `tenantID` (FairnessID) are present and mapped

### Cache staleness check

Grep `prepare_plugin.go` for the hardcoded prefix-cache staleness constant. Fail if absent or
value ≠ 2 seconds. (The 2s value must be present as a literal — not derived from config —
to confirm it is hardcoded per spec.)

### Routing policy check

Parse the `EndpointPickerConfig` YAML embedded in `values.yaml` for the target phase:

- **Baseline:** `load-aware-scorer` present, `decode-filter` present, `max-score-picker`
  present; all are included in the default scheduling profile — matches GAIE 3:2:2 defaults
- **Treatment:** `schedulingProfiles` is present and non-empty; at least one profile references
  the custom scorer plugin

### Isolation check

Verify `observe.workloads` contains ≥ 1 entry. (Each workload receives its own stack per the
per-workload isolated stacks design; this confirms the input is well-formed.)

---

## Phase 2: Post-Deploy, Pre-Benchmark (Live Cluster)

**Invocation:** `python scripts/validate.py post-deploy --run NAME --namespace NS --phase baseline|treatment`

Runs after the EPP and model pods are Ready, before workload submission.

### Signal liveness

For each signal in `prepare_signal_coverage.json`, query Prometheus to confirm the metric
series exists with a data point within the last 30 seconds. Fail if any mapped signal has no
recent data.

### Prometheus staleness

Query the `scrape_interval` for the job that exports KV/queue metrics. Verify ≤ 5s. This
confirms the `llm_config.yaml` `snapshot_refresh_interval_us: 5000000` assumption holds in
the actual cluster configuration.

### SLO class / tenant header extraction

Verify the deployed EPP ConfigMap contains metadata handler configuration that extracts
`ObjectiveKey` (`X-Objective-Key`) and `FairnessID` (`X-Flow-Fairness-Id`) from request
headers. Check by inspecting the EPP ConfigMap — no live request injection required.

### Model loaded

`GET /v1/models` on the vLLM service endpoint. Verify the returned model ID matches
`values.yaml` `stack.model.modelName`.

### Stack count

Count EPP deployments in the namespace with the `sim2real` label selector. Verify the count
equals the number of workloads in `observe.workloads` for this phase (one stack per workload,
per the per-workload isolated stacks design). Fail if count is 0 or differs.

---

## Phase 3: Post-Collection Trace Audit

**Invocation:** `python scripts/validate.py post-collection --run NAME --phase baseline|treatment`

Reads `workspace/runs/{run}/deploy_{phase}_log/{workload}/trace_data.csv` and
`trace_header.yaml` for each workload. Compares against the workload spec embedded in
`values.yaml`.

### Arrival rate

Compute actual RPS: `request_count / ((max(arrival_time_us) - min(arrival_time_us)) / 1e6)`.
Compare against spec `aggregate_rate`. Tolerance: ±10%.

### Burstiness (CV)

Compute inter-arrival time CV per `client_id` group from `arrival_time_us` column.
Compare against spec `arrival.cv` for each client. Tolerance: ±20% relative.
(Gamma processes have natural variance; tighter tolerance would produce false positives.)
Clients with `arrival.process: poisson` are checked for CV ≈ 1.0 ±0.3.

### Token distributions

Per client group (matched by `client_id`), compute mean `input_tokens` and mean
`output_tokens`. Compare against spec gaussian `mean`. Tolerance: ±15% relative.

### SLO class ratios

Compute actual `rate_fraction` per `slo_class` from row counts across all clients.
Compare against spec `rate_fraction` per class (summed across clients sharing a class).
Tolerance: ±0.03 absolute.

### Tenant count

Verify distinct `tenant_id` values in the trace match the set of `tenant_id` fields
declared across all clients in the spec.

### Request count

Verify CSV row count ≈ spec `num_requests`. Tolerance: ±5%. (Some requests may be shed or
errored under load; strict equality would produce false positives for admission-controlled
workloads.)

### Model identity

`trace_header.yaml` `server.model` matches `values.yaml` `stack.model.modelName`.

### Workload seed

`trace_header.yaml` `workload_seed` matches spec `seed` field.

---

## Output Format

Each subcommand writes a JSON report to `workspace/runs/{run}/`:

- `validate_pre_deploy.json`
- `validate_post_deploy_{phase}.json`
- `validate_post_collection_{phase}.json`

Report structure:

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
        {"field": "--gpu-memory-utilization", "expected": "0.9", "actual": "0.85", "passed": false}
      ]
    },
    "signals": {"passed": true, "items": [...]},
    "cache_staleness": {"passed": true, "notes": []},
    "routing_policy": {"passed": true, "notes": []},
    "isolation": {"passed": true, "notes": []}
  }
}
```

Terminal output uses `[PASS]` / `[FAIL]` / `[WARN]` lines in the same color style as
`deploy.py` and `analyze.py`.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | One or more checks failed — operator must review report |
| `2` | Infrastructure error — missing artifact, parse failure, kubectl/Prometheus unreachable |

## deploy.py Integration

`deploy.py` calls `validate_checks` functions directly (no subprocess fork) at two points:

1. **Before stack deployment** — `stage_benchmarks` calls
   `run_pre_deploy_checks(run_dir, values_path)` and exits with error on `FAIL`
2. **After stack deploy, before workload submission** — `_run_pipeline_phase` calls
   `run_post_deploy_checks(phase, namespace, run_dir)` per phase before submitting the
   first PipelineRun

Phase 3 is operator-invoked standalone after `deploy.py` completes.

## Change Surface

| Component | Changes |
|---|---|
| `scripts/validate.py` | New file — three subcommands |
| `scripts/lib/validate_checks.py` | New file — all check primitives |
| `scripts/deploy.py` | Two new call sites: pre-deploy gate + post-deploy gate per phase |
| `workspace/runs/{run}/` | Three new report JSON files written per run |
