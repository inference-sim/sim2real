# Admission Control: From BLIS to llm-d via GIE

## Summary

BLIS found an effective admission policy (`AdaptiveAdmission`) that sheds low-priority traffic under load with per-tenant fairness. We want to bring this to llm-d.

**GIE v1.4.0 (llm-d's upstream dependency) supports two complementary admission mechanisms:**

1. **`AdmissionPlugin`** — hard admit/reject per request. This is where the BLIS logic goes. It's a plugin interface: return `nil` to admit, return `error` to reject.
2. **Flow Control** — gateway-level queuing with priority ordering, saturation detection, and late binding. This is optional and additive. It queues requests instead of rejecting them.

These run in sequence inside GIE's [Director pipeline](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/requestcontrol/director.go#L151-L176):

```
Request
  → Flow Control admission (line 151)     — queue or reject by priority/capacity
  → Find candidate pods (line 155)
  → PrepareData plugins (line 165)         — tokenizer, etc.
  → AdmissionPlugin (line 171)             — YOUR custom admit/reject logic
  → Scheduler (line 176)                   — filter → score → pick
  → Pod
```

**The BLIS adaptive shedding logic maps to a custom `AdmissionPlugin`. Flow control cannot replace it** — it queues instead of rejecting, and has no per-class load thresholds or adaptive capacity learning.

### Key GIE Source References

| What | Link |
|---|---|
| `AdmissionPlugin` interface | [plugins.go#L76-L84](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/framework/interface/requestcontrol/plugins.go#L76-L84) |
| Director pipeline (where admission is called) | [director.go#L151-L176](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/requestcontrol/director.go#L151-L176) |
| `runAdmissionPlugins` implementation | [director.go#L399-L408](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/requestcontrol/director.go#L399-L408) |
| Flow control guide | [gateway-api-inference-extension.sigs.k8s.io/guides/flow-control](https://gateway-api-inference-extension.sigs.k8s.io/guides/flow-control/) |
| Utilization saturation detector | [utilizationdetector/detector.go](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/saturationdetector/framework/plugins/utilizationdetector/detector.go) |
| Concurrency saturation detector | [concurrencydetector/detector.go](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/saturationdetector/framework/plugins/concurrencydetector/detector.go) |

---

## What BLIS's AdaptiveAdmission Does

Source: `blis-best-admission.go`

The policy makes admit/reject decisions per request using three mechanisms:

### 1. Priority-Based Load Shedding

| SLO Class | Behavior |
|---|---|
| `critical` | Always admitted |
| `standard` | Always admitted |
| `batch` | Shed when per-instance load > 50% of typical capacity |
| `sheddable` | Shed when per-instance load > 75% of typical capacity |

### 2. Tenant Fairness

Per-tenant request counting. If a tenant exceeds 1.5x the average request count:
- `batch` threshold tightened by 20% (0.5 → 0.4)
- `sheddable` threshold tightened by 10% (0.75 → 0.675)

### 3. Adaptive Load Estimation

10-second sliding window estimates "typical load" from observed admission rates, bootstrapped at 40 req/instance. Load ratio (`perInstanceLoad / typicalLoad`) drives shedding.

### Signals Used

| BLIS Signal | Source |
|---|---|
| `totalInFlight` | Sum of `snap.InFlightRequests` across all instances |
| `totalQueueDepth` | Sum of `snap.QueueDepth` across all instances |
| `maxKVUtil` | Max `snap.KVUtilization` across instances |
| `avgKVUtil` | Mean `snap.KVUtilization` across instances |
| `minFreeKV` | Min `snap.FreeKVBlocks` across instances |
| `numInstances` | Count of instances |
| `inputLen` | `len(req.InputTokens)` |
| `sloClass` | `req.SLOClass` |
| `tenantID` | `req.TenantID` |

---

## Why GIE Flow Control Alone Can't Replace This

| | BLIS AdaptiveAdmission | GIE Flow Control |
|---|---|---|
| **Core action** | Reject request (client gets error) | Queue request (client waits) |
| **Per-class load thresholds** | Yes — batch at 50%, sheddable at 75% | No — saturation detector applies to all classes equally |
| **Adaptive capacity learning** | Yes — 10s sliding window | No — fixed thresholds only |
| **Tenant-aware threshold adjustment** | Yes — tighten for heavy tenants | No — fair dispatch ordering only |

GIE flow control adds queuing, priority ordering, and late binding. But it cannot replicate BLIS's load-ratio-based, per-class shedding with adaptive thresholds.

**You need a custom `AdmissionPlugin` for the BLIS logic. Flow control is optional and complementary.**

---

## Implementation: Custom AdmissionPlugin

### The GIE Interface

From [plugins.go#L76-L84](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/framework/interface/requestcontrol/plugins.go#L76-L84):

```go
type AdmissionPlugin interface {
    plugin.Plugin
    // Return nil to admit, return error to reject.
    AdmitRequest(ctx context.Context, request *LLMRequest, pods []Endpoint) error
}
```

Called by the [Director](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/requestcontrol/director.go#L399-L408) after data preparation, before scheduling. All admission plugins must return `nil` for the request to proceed — any single plugin returning an error rejects the request.

### Signal Mapping

Every BLIS signal has a direct equivalent in the `AdmitRequest` arguments:

| BLIS Signal | llm-d Equivalent | Source |
|---|---|---|
| `numInstances` | `len(pods)` | `pods []Endpoint` argument |
| `totalInFlight` | `sum of (RunningRequestsSize + WaitingQueueSize)` | `pod.GetMetrics()` |
| `totalQueueDepth` | `sum of WaitingQueueSize` | `pod.GetMetrics().WaitingQueueSize` |
| `maxKVUtil` | `max of KVCacheUsagePercent` | `pod.GetMetrics().KVCacheUsagePercent` |
| `avgKVUtil` | `mean of KVCacheUsagePercent` | `pod.GetMetrics().KVCacheUsagePercent` |
| `minFreeKV` | `min of CacheNumGPUBlocks × (1 - KVCacheUsagePercent/100)` | `pod.GetMetrics()` |
| `inputLen` | `len(request.Body.PromptText())` or tokenizer plugin output | `LLMRequestBody` |
| `sloClass` | `request.Objectives.Priority` (integer) | `RequestObjectives.Priority` |
| `tenantID` | `request.Headers["x-gateway-inference-fairness-id"]` | HTTP header |
| `clock` | `time.Now()` | Real wall clock |

### Key Differences from BLIS

**SLO class → Priority integer.** BLIS uses string SLO classes. GIE uses integer priority from `InferenceObjective` CRDs:

```
critical   → priority >= 100
standard   → priority >= 50
batch      → priority >= 0
sheddable  → priority < 0
```

**In-flight = Running + Waiting.** BLIS `InFlightRequests` combines running and queued. In llm-d: `RunningRequestsSize + WaitingQueueSize`.

**Token count.** BLIS has `req.InputTokens` (pre-tokenized). In llm-d, use `PromptText()` length as proxy, or read tokenizer plugin output from `PluginState` if `prepareDataPlugins` feature gate is enabled.

### Implementation Sketch

```go
package admission

import (
    "context"
    "encoding/json"
    "fmt"
    "sync"
    "time"

    "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin"
    "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"
)

const AdaptiveAdmissionType = "adaptive-admission"

type AdaptiveAdmissionParams struct {
    BatchThreshold     float64 `json:"batchThreshold"`     // default: 0.5
    SheddableThreshold float64 `json:"sheddableThreshold"` // default: 0.75
    FairnessLimit      float64 `json:"fairnessLimit"`      // default: 1.5
    WindowSeconds      float64 `json:"windowSeconds"`      // default: 10
    BootstrapLoad      float64 `json:"bootstrapLoad"`      // default: 40
}

type AdaptiveAdmission struct {
    params         AdaptiveAdmissionParams
    mu             sync.Mutex
    tenantRequests map[string]int
    windowStart    time.Time
    windowCount    int
}

func init() {
    plugin.Register(AdaptiveAdmissionType, func(name string, params json.RawMessage, handle plugin.Handle) (plugin.Plugin, error) {
        p := AdaptiveAdmissionParams{
            BatchThreshold: 0.5, SheddableThreshold: 0.75,
            FairnessLimit: 1.5, WindowSeconds: 10, BootstrapLoad: 40,
        }
        if params != nil {
            if err := json.Unmarshal(params, &p); err != nil {
                return nil, err
            }
        }
        return &AdaptiveAdmission{
            params:         p,
            tenantRequests: make(map[string]int),
        }, nil
    })
}

func (a *AdaptiveAdmission) Name() string { return AdaptiveAdmissionType }
func (a *AdaptiveAdmission) Type() string { return AdaptiveAdmissionType }

func (a *AdaptiveAdmission) AdmitRequest(
    ctx context.Context,
    request *scheduling.LLMRequest,
    pods []scheduling.Endpoint,
) error {
    a.mu.Lock()
    defer a.mu.Unlock()

    // --- Compute cluster-wide signals from pod metrics ---
    numPods := len(pods)
    totalInFlight := 0
    for _, pod := range pods {
        m := pod.GetMetrics()
        totalInFlight += m.RunningRequestsSize + m.WaitingQueueSize
    }

    perPodLoad := 0.0
    if numPods > 0 {
        perPodLoad = float64(totalInFlight) / float64(numPods)
    }

    // --- Priority from InferenceObjective CRD ---
    priority := request.Objectives.Priority

    // Critical (>= 100) and standard (>= 50): always admit
    if priority >= 50 {
        return nil
    }

    // --- Adaptive load estimation (sliding window) ---
    now := time.Now()
    windowDuration := time.Duration(a.params.WindowSeconds * float64(time.Second))
    if a.windowStart.IsZero() {
        a.windowStart = now
    }
    if now.Sub(a.windowStart) > windowDuration {
        a.windowStart = now
        a.windowCount = 0
    }
    a.windowCount++

    typicalLoad := a.params.BootstrapLoad
    if a.windowCount > 100 && numPods > 0 {
        elapsed := now.Sub(a.windowStart).Seconds()
        if elapsed > 0 {
            typicalLoad = float64(a.windowCount) / float64(numPods) / elapsed
            if typicalLoad < 10.0 {
                typicalLoad = a.params.BootstrapLoad
            }
        }
    }
    loadRatio := perPodLoad / typicalLoad

    // --- Tenant fairness ---
    tenantID := request.Headers["x-gateway-inference-fairness-id"]
    if tenantID == "" {
        tenantID = "_default"
    }
    a.tenantRequests[tenantID]++

    avgTenantReqs := 1.0
    if len(a.tenantRequests) > 0 {
        total := 0
        for _, c := range a.tenantRequests {
            total += c
        }
        avgTenantReqs = float64(total) / float64(len(a.tenantRequests))
    }
    fairnessRatio := float64(a.tenantRequests[tenantID]) / (avgTenantReqs + 1.0)

    // --- Batch (priority >= 0): shed at batchThreshold ---
    if priority >= 0 {
        threshold := a.params.BatchThreshold
        if fairnessRatio > a.params.FairnessLimit {
            threshold *= 0.8
        }
        if loadRatio > threshold {
            return fmt.Errorf("batch-shed: load %.2f exceeds threshold %.2f", loadRatio, threshold)
        }
        return nil
    }

    // --- Sheddable (priority < 0): shed at sheddableThreshold ---
    threshold := a.params.SheddableThreshold
    if fairnessRatio > a.params.FairnessLimit {
        threshold *= 0.9
    }
    if loadRatio > threshold {
        return fmt.Errorf("sheddable-shed: load %.2f exceeds threshold %.2f", loadRatio, threshold)
    }
    return nil
}
```

### Configuration

```yaml
apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
plugins:
- type: adaptive-admission
  parameters:
    batchThreshold: 0.5
    sheddableThreshold: 0.75
    fairnessLimit: 1.5
    windowSeconds: 10
    bootstrapLoad: 40
# ... existing scorers, filters, picker ...
```

---

## Optional: Add GIE Flow Control for Queuing

Flow control does not replace the admission plugin — it adds queuing. With both enabled, the [Director pipeline](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/requestcontrol/director.go#L151-L176) runs:

```
Request
  → Flow Control admission (queue/reject by priority)
  → Find candidate pods
  → PrepareData plugins
  → AdmissionPlugin (your custom admit/reject)     ← BLIS logic here
  → Scheduler (filter → score → pick)
  → Pod
```

To enable, add to the EPP config:

```yaml
featureGates:
- flowControl

plugins:
- type: utilization-detector
  parameters:
    queueDepthThreshold: 2
    kvCacheUtilThreshold: 0.85

saturationDetector:
  pluginRef: utilization-detector
```

### What the Saturation Detector Does

GIE v1.4.0 has [two saturation detector implementations](https://github.com/kubernetes-sigs/gateway-api-inference-extension/tree/v1.4.0/pkg/epp/saturationdetector/framework/plugins). Both return a continuous float (0.0 to 1.0+). Flow control holds requests when saturation >= 1.0.

**[Utilization detector](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/saturationdetector/framework/plugins/utilizationdetector/detector.go)** (default): reads scraped pod metrics.
```
PodScore = max(WaitingQueue / QueueThreshold, KVCacheUsage / KVCacheThreshold)
Saturation = avg(PodScore)
```
Defaults: `QueueDepthThreshold=5`, `KVCacheUtilThreshold=0.8`, `MetricsStalenessThreshold=200ms`.

**[Concurrency detector](https://github.com/kubernetes-sigs/gateway-api-inference-extension/blob/v1.4.0/pkg/epp/saturationdetector/framework/plugins/concurrencydetector/detector.go)**: tracks in-flight requests EPP-side.
```
Saturation = TotalInflight / (NumPods x MaxConcurrency)
```
Default: `MaxConcurrency=100`. Also acts as a scheduling filter — removes pods over `MaxConcurrency x (1 + Headroom)`.

---

## At a Glance

| What | How |
|---|---|
| **Must have** | Custom `AdmissionPlugin` implementing BLIS adaptive shedding (~120 lines of Go) |
| **Signal source** | `pod.GetMetrics()` for load + KV, `request.Objectives.Priority` for SLO class, `request.Headers` for tenant ID |
| **Nice to have** | GIE flow control feature gate for queuing + late binding (complementary, not a substitute) |
| **Config** | EPP YAML plugin entry with tunable thresholds |
| **llm-d status** | On GIE v1.4.0, but neither `AdmissionPlugin` nor flow control are currently used. Only `prepareDataPlugins` feature gate is enabled. |
