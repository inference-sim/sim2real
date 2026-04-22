# Parallel Pool Execution Design

**Date:** 2026-04-22
**Status:** Draft

---

## 1. Problem Statement and Goals

### Problem

The current execution model runs all `(workload, config)` pairs in a single sequential PipelineRun. Any failure or timeout aborts all remaining work. There is no way to re-run a single failed pair without manually editing `transfer.yaml` and re-running `prepare.py`. Cluster resources that could serve multiple concurrent stacks sit idle.

### Goals

1. Run `(workload, config)` pairs concurrently, bounded by available cluster resources
2. Isolate executions so no pair interferes with another
3. Collect results incrementally as each pair completes
4. On timeout: auto-retry up to a configurable limit
5. On hard failure: flag and continue; human decides whether to re-run
6. Support re-running specific failed pairs without re-running successful ones
7. Design for future extensibility to multiple treatment configs

### Out of scope

- Dynamic namespace provisioning (pool is statically defined)
- Changes to `prepare.py` phases 1–3 or the translate skill
- Changes to result analysis (`sim2real-analyze`)

---

## 2. Architecture Overview

### Core concept: Namespace Pool

A fixed set of pre-provisioned namespace slots, each with its own complete stack of resources (RBAC, secrets, PVCs, Tekton tasks). The pool size determines maximum parallelism. A slot is acquired before a PipelineRun starts and released only after inline collection completes.

### Unit of work

Each `(workload, config)` pair is an independent PipelineRun in its own namespace slot. Baseline and treatment for the same workload are independent work items with no forced ordering between them.

The parallelism model:

```
slot sim2real-0:   wl-smoke-baseline   →  wl-load-baseline   → ...
slot sim2real-1:   wl-smoke-treatment  →  wl-load-treatment  → ...
slot sim2real-2:   wl-heavy-baseline   →  ...
```

Within each slot: one PipelineRun at a time. Across slots: fully parallel.

### Component changes

| Component | Change |
|---|---|
| `setup.py` | Accepts `--namespaces` list; provisions each slot identically |
| `prepare.py` | Generates one parameterized Pipeline + one PipelineRun per `(workload, config)` pair |
| `deploy.py run` | New subcommand: orchestrator loop managing the pool |
| `deploy.py collect` | Unchanged; remains available for legacy single-namespace use and manual recovery |

### Data flow

```
setup.py ──→ provisions pool [sim2real-0, sim2real-1, sim2real-2]

prepare.py ──→ cluster/
                 sim2real-{run}.yaml                      (one Pipeline, shared by all pairs)
                 wl-{workload}-{config}/
                   pipelinerun-{workload}-{config}.yaml   (one PipelineRun per pair)

deploy.py run ──→ orchestrator loop
  acquire slot ──→ kubectl apply PipelineRun ──→ poll ──→ collect ──→ release slot
                                                              ↓
                                                results/{config}/{workload}/
progress.json ──→ persists state across interruptions
```

---

## 3. `setup.py` — Namespace Pool Provisioning

`setup.py` accepts `--namespaces ns1,ns2,...` and provisions each namespace identically: RBAC, secrets, PVCs, and Tekton tasks. The list is persisted in `setup_config.json`:

```json
{
  "namespaces": ["sim2real-0", "sim2real-1", "sim2real-2"],
  "workspaces": {
    "model-cache":    {"persistentVolumeClaim": {"claimName": "model-pvc"}},
    "data-storage":   {"persistentVolumeClaim": {"claimName": "data-pvc"}},
    "hf-credentials": {"secret": {"secretName": "hf-secret"}}
  }
}
```

**Backward compatibility:** If `--namespaces` is not given, defaults to a single namespace (current behavior). `setup_config.json` always writes `namespaces` as a list.

**PVC naming:** PVCs are namespace-scoped in Kubernetes, so the same claim name (e.g. `model-pvc`) can be reused across namespaces without collision.

---

## 4. `prepare.py` — Single Parameterized Pipeline

### Change from current model

Today `compile_pipeline()` renders config-specific values into the Pipeline YAML via Jinja2 at compile time, producing separate `baseline-pipeline.yaml` and `treatment-pipeline.yaml`. Both have identical task structure; only the values differ.

In the new model, one Pipeline is generated per experiment run. Config-specific values are promoted to Pipeline params and passed via PipelineRun.

### What `tektonc` still renders (static per experiment)

These values are the same for all `(workload, config)` pairs and remain baked in at compile time:

| Jinja2 expression | Used in |
|---|---|
| `{{ stack.model.modelName }}` | `download-model`, `deploy-model`, `run-workload` |
| `{{ stack.model.helmValues \| tojson }}` | `deploy-model.params.config` |
| `{{ stack.gateway.helmValues \| tojson }}` | `deploy-gateway.params.config` |
| `{{ observe.image }}` | `run-workload.params.blisImage` |
| `{{ run_name }}` | Pipeline `metadata.name` → `sim2real-{run_name}` |

`tektonc` is called once per experiment (not once per phase). `tekton.py` injects `run_name` instead of `phase` before invoking it.

### New Pipeline params (vary per PipelineRun)

| Param | Replaces | Varies between |
|---|---|---|
| `gaieConfig` | `{{ gaie_config \| tojson }}` | baseline vs treatment (different EPP/scorer Helm values) |
| `inferenceObjectives` | `{{ inference_objectives \| default([]) }}` | baseline vs treatment |
| `phase` | `{{ phase }}` in `resultsDir` | every pair |
| `workloadName` | already a param | every pair |
| `workloadSpec` | already a param | every pair |

The tasks in `tektonc-data-collection` are **unchanged** — they already accept `config` and `objectives` as `type: string` params.

### Output structure

```
cluster/
  sim2real-{run}.yaml                       # one Pipeline for the whole experiment
  wl-{workload}-baseline/
    pipelinerun-{workload}-baseline.yaml
  wl-{workload}-treatment/
    pipelinerun-{workload}-treatment.yaml
```

`make_experiment_pipeline()` is retired. A new `make_pipelinerun()` function generates one PipelineRun per `(workload, config)` pair.

---

## 5. Orchestrator — `deploy.py run`

### Interface

```
deploy.py run [--only PAIR] [--workload NAME] [--config NAME] [--status STATE]
```

`deploy.py run` with no flags submits all pending pairs and runs to completion. If `progress.json` already exists it automatically resumes, skipping `done` entries and reconciling `running` entries against cluster state.

### Re-run and resume flags

| Flag | Resets to `pending` |
|---|---|
| _(none, first run)_ | all pairs |
| _(none, subsequent)_ | skips `done`; reconciles `running` against cluster |
| `--only wl-smoke-treatment` | one specific pair |
| `--workload wl-smoke` | all configs for that workload |
| `--config treatment` | all workloads for that config |
| `--status failed` | all pairs currently `failed` |
| `--status timed-out` | all pairs currently `timed-out` |

Flags compose: `--config treatment --status failed` resets only failed treatment pairs.

### Progress file — `runs/{run}/progress.json`

```json
{
  "wl-smoke-baseline":  {"status": "done",       "namespace": "sim2real-0", "retries": 0},
  "wl-smoke-treatment": {"status": "running",    "namespace": "sim2real-1", "retries": 0},
  "wl-load-baseline":   {"status": "pending",    "namespace": null,         "retries": 0},
  "wl-load-treatment":  {"status": "timed-out",  "namespace": "sim2real-2", "retries": 1},
  "wl-heavy-baseline":  {"status": "failed",     "namespace": "sim2real-0", "retries": 0}
}
```

Valid statuses: `pending` → `running` → `collecting` → `done` | `failed` | `timed-out`

### Orchestrator loop

```
while work_remaining or slots_busy:

    for each newly-completed slot:
        collect results from that slot's namespace   (inline)
        write progress: collecting → done | collect-failed
        free the slot

    for each free slot and pending work item:
        run slot readiness check (see §6)
        run slot pre-flight cleanup (see §7)
        assign slot → work item
        kubectl apply PipelineRun (namespace = slot's namespace)
        write progress: pending → running

    poll running PipelineRuns for status
    on timeout:      requeue if retries < max_retries, else mark timed-out; free slot
    on hard failure: mark failed, free slot, continue

    sleep poll_interval
```

### Resume behavior

On resume, the orchestrator reconciles `progress.json` against cluster state before entering the loop:

- `done` → skip
- `pending` → queue normally
- `running` → check actual PipelineRun status on cluster; monitor if still running, update status if already completed or failed
- `collecting` → check if results exist; re-collect if missing, mark done if present
- `failed` / `timed-out` → leave as-is; re-run requires explicit flag

### Inline collection

Collection runs immediately after a PipelineRun completes, while the slot is still assigned. The slot is not freed until collection succeeds (or is marked `collect-failed`). This eliminates the need to track which namespace held which workload after the fact, and means `deploy.py collect` is not needed in the normal flow.

---

## 6. Slot Readiness Check

Before a slot is assigned a work item, the orchestrator invokes a readiness check for that namespace. The check is intentionally extensible:

**Initially implemented:**
- PVCs exist and are in `Bound` state
- HF credentials secret is present
- Required Tekton tasks are deployed in the namespace

**Explicitly deferred:**
- Compute capacity check (GPU/CPU availability): relies on existing timeout mechanism to surface scheduling failures; pre-emptive capacity checking is complex and brittle and not implemented in this design

A future implementation may add capacity-aware scheduling by querying node allocatable resources. The check is a named seam in the code to allow this without broader changes.

---

## 7. Slot Cleanup (Dependency)

When a PipelineRun is terminated unexpectedly — deleted by the orchestrator, controller crash, or node failure — Tekton's `finally` block does not execute, leaving orphaned resources in the namespace: Helm releases (model, GAIE, gateway) and Kubernetes custom resources (InferencePool, HTTPRoute, InferenceObjectives). These must be removed before the slot can safely accept a new workload. (Normal task failures do trigger `finally`; this gap applies only to abrupt terminations.)

The orchestrator requires a slot pre-flight cleanup step, but the mechanism for identifying and removing these resources is **out of scope for this design**. It is tracked in [Issue #6](https://github.com/inference-sim/sim2real/issues/6).

Issue #6 should be updated to reflect the new requirement: automated, per-slot cleanup invoked by the orchestrator before slot reuse, rather than the current framing as a user-triggered per-run command.

---

## 8. Backward Compatibility

- `deploy.py` default behavior (no subcommand) is preserved for single-namespace runs
- `setup_config.json` always writes `namespaces` as a list; single-namespace setups write a one-element list
- `deploy.py collect` remains available for legacy use and manual recovery
- `prepare.py` can still generate the legacy sequential experiment package behind a `--mode sequential` flag

---

## 9. Open Questions

- Should `deploy.py run` run in the foreground (blocking) or support a detached/background mode?
- What is `max_retries` default for timed-out pairs?
- Should `collect-failed` auto-retry collection on resume, or require explicit `--collect-only` flag?
