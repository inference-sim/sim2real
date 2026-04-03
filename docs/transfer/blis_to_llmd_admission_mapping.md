# BLIS-to-llm-d Signal Mapping Artifact — Admission Control

**Version:** 1.0
**Target submodule:** llm-d-inference-scheduler
**Pinned commit hash:** 4cd7046e2cf9121be6cdc2fc0815dbeeba721c9f

> **NOTE:** The admission control plugin type does not yet exist in llm-d-inference-scheduler.
> This mapping document defines how simulation signals map to production equivalents
> using the existing `fwkdl.Metrics` and `fwkdl.EndpointMetadata` data available in the
> EPP scheduling framework. The admission plugin will need to aggregate per-endpoint
> metrics into cluster-wide signals (the simulation operates on cluster-wide state).

## Signal Mapping Table

The simulation's `AdaptiveAdmission.Admit()` receives cluster-wide signals aggregated
from `state.Snapshots[]`. In production, the EPP sees individual endpoints. The admission
plugin must aggregate across all endpoints to reconstruct cluster-wide signals.

| Sim Signal | Go Type | Sim Access Path | Production Equivalent | Prod Access Path | Fidelity | Staleness Window (ms) | Rationale |
|------------|---------|-----------------|----------------------|------------------|----------|-----------------------|-----------|
| numInstances | int | `len(state.Snapshots)` | Count of available endpoints | `len(endpoints)` | high | 0 | Direct count of endpoints passed to plugin |
| totalInFlight | int | `sum(snap.InFlightRequests)` | Sum of RunningRequestsSize across all endpoints | `sum(e.GetMetrics().RunningRequestsSize)` | medium | 0 | Per-endpoint running requests summed to cluster total. Same semantic gap as scorer mapping: router-level vs endpoint-level counting. |
| totalQueueDepth | int | `sum(snap.QueueDepth)` | Sum of WaitingQueueSize across all endpoints | `sum(e.GetMetrics().WaitingQueueSize)` | high | 0 | Per-endpoint waiting queue summed to cluster total |
| maxKVUtil | float64 | `max(snap.KVUtilization)` | Max KVCacheUsagePercent / 100 across endpoints | `max(e.GetMetrics().KVCacheUsagePercent) / 100.0` | high | 0 | **Normalization:** Sim = 0.0-1.0 ratio; Prod = 0-100 percentage. Divide by 100. |
| avgKVUtil | float64 | `mean(snap.KVUtilization)` | Mean KVCacheUsagePercent / 100 across endpoints | `mean(e.GetMetrics().KVCacheUsagePercent) / 100.0` | high | 0 | **Normalization:** Same as maxKVUtil. |
| minFreeKV | int64 | `min(snap.FreeKVBlocks)` | Not directly available | N/A | low | N/A | `FreeKVBlocks` is not exposed in `fwkdl.Metrics`. Can be approximated from KVCacheUsagePercent if total KV blocks are known, but this signal is unused in the EVOLVE-BLOCK (suppressed with `_ = minFreeKV`). **Safe to omit.** |
| inputLen | int | `len(req.InputTokens)` | Request input token count | From `LLMRequest` prompt length | high | 0 | Available from the request object |
| sloClass | string | `req.SLOClass` | SLO class label from request headers | From `LLMRequest` headers/labels | medium | 0 | Requires SLO class to be propagated in request metadata. The EVOLVE-BLOCK uses "critical", "standard", "batch", "sheddable". |
| tenantID | string | `req.TenantID` | Tenant identifier from request headers | From `LLMRequest` headers/labels | medium | 0 | Requires tenant ID to be propagated in request metadata |
| clock | int64 | `state.Clock` (microseconds) | Current time | `time.Now().UnixMicro()` | high | 0 | Real wall clock replaces simulated clock |
| classCounters | map[string]int | `a.classCounters` | Plugin struct field | `p.classCounters` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| tenantRequests | map[string]int | `a.tenantRequests` | Plugin struct field | `p.tenantRequests` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| tenantTokens | map[string]float64 | `a.tenantTokens` | Plugin struct field | `p.tenantTokens` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| totalAdmitted | int | `a.totalAdmitted` | Plugin struct field | `p.totalAdmitted` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| totalRejected | int | `a.totalRejected` | Plugin struct field | `p.totalRejected` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| windowCount | int | `a.windowCount` | Plugin struct field | `p.windowCount` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |
| windowStart | int64 | `a.windowStart` | Plugin struct field | `p.windowStart` | high | 0 | Internal mutable state — maps directly to production plugin struct field. Persists across calls. |

## Fidelity Rating Scale

- **high**: Same computation, same data source, negligible staleness. R² >= 0.99 or max |sim - prod| <= 1% of range.
- **medium**: Equivalent computation but different data source or non-trivial staleness. R² >= 0.80 or rank-order correlation >= 0.90.
- **low**: Approximate or proxy signal with known semantic gap. Pipeline halts on low-fidelity signals unless signal is unused.

## Admission Plugin Interface (PROPOSED)

> **IMPORTANT:** No admission plugin interface exists in `llm-d-inference-scheduler` yet.
> The generated plugin will implement a new interface pattern. Two approaches:
>
> **Option A — PreRequest plugin:** Implement `requestcontrol.PreRequest` to intercept
> requests before scheduling. Return an error to reject (admission denial). This hooks
> into the existing plugin lifecycle but is per-request, not per-scheduling-cycle.
>
> **Option B — Filter plugin:** Implement `scheduling.Filter` to filter the request
> before endpoint selection. This is closer to the simulation's admission gate semantics
> (decide before routing).
>
> **Option C — Custom plugin type:** Define a new `AdmissionPolicy` interface in
> `pkg/plugins/admission/` with `Admit(ctx, request, endpoints) (bool, string)` that
> runs before the scoring phase.
>
> The template document specifies the chosen approach.

## Cluster-Wide Aggregation Pattern

The simulation's `Admit()` sees pre-aggregated cluster state (`state.Snapshots`).
In production, the plugin receives a list of `endpoints []Endpoint`. The plugin MUST
aggregate metrics across all endpoints to reconstruct cluster-wide signals:

```go
numInstances := len(endpoints)
totalInFlight := 0
totalQueueDepth := 0
maxKVUtil := 0.0
sumKVUtil := 0.0
for _, e := range endpoints {
    m := e.GetMetrics()
    totalInFlight += int(m.RunningRequestsSize)
    totalQueueDepth += int(m.WaitingQueueSize)
    kvUtil := float64(m.KVCacheUsagePercent) / 100.0
    if kvUtil > maxKVUtil {
        maxKVUtil = kvUtil
    }
    sumKVUtil += kvUtil
}
avgKVUtil := sumKVUtil / float64(numInstances)
```

## Stateful Admission

The simulation's `AdaptiveAdmission` maintains state across calls:
- `tenantTokens` — per-tenant token budgets
- `tenantRequests` — per-tenant request counters
- `classCounters` — per-SLO-class admission counters
- `windowStart`, `windowCount` — sliding window for load estimation

The production plugin MUST also maintain this state. Since the plugin is instantiated
once via factory and reused, struct fields persist across calls (same as scorer plugins).
**Thread safety:** The EPP may call the plugin concurrently — use `sync.Mutex` to protect
mutable state.

## Unused Signals

The EVOLVE-BLOCK declares `minFreeKV` but suppresses it with `_ = minFreeKV`.
This signal has no production equivalent and is safe to omit from the generated plugin.

## Notes

- The `perInstanceLoad` derivation (`totalInFlight / numInstances`) is computed inside
  the EVOLVE-BLOCK, not a raw signal — it must be replicated in the production plugin.
- SLO class priority order in the EVOLVE-BLOCK: critical > standard > batch > sheddable.
  "critical" and "standard" are always admitted; "batch" and "sheddable" are subject to
  adaptive load shedding.
- Tenant fairness uses a ratio of per-tenant requests to average requests across tenants.
