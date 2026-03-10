# BLIS-to-llm-d Signal Mapping Artifact

**Version:** 1.0
**Last verified against:** llm-d-inference-scheduler submodule HEAD
**Pinned commit hash:** 091312c333a50e94f5e60a2ca2926e8442eeffa9

## Signal Mapping Table

| Sim Signal | Go Type | Sim Access Path | Production Equivalent | Prod Access Path | Fidelity | Staleness Window (ms) | Rationale |
|------------|---------|-----------------|----------------------|------------------|----------|-----------------------|-----------|
| QueueDepth | int | `snap.QueueDepth` | `endpoint.GetMetrics().WaitingQueueSize` | LoadAware scorer | high | 0 | Same computation: count of waiting requests. Direct endpoint query in both systems. |
| BatchSize | int | `snap.BatchSize` | `endpoint.GetMetrics().RunningQueueSize` (approximate) | ActiveRequest scorer | medium | 0 | Sim tracks exact batch size; production uses running request count as proxy. **Structural semantic gap:** batch size (number of items in a batch) differs from queue size (number of items waiting). Correlation expected to be strong but not exact. **PR5 MUST measure:** Compare sim BatchSize distribution against prod RunningQueueSize to quantify the gap. If R² < 0.80, downgrade to low. |
| InFlightRequests | int | `snap.InFlightRequests` | `endpoint.GetMetrics().RunningRequestCount` (approximate) | ActiveRequest scorer | medium | 0 | Sim tracks in-flight requests at router level; production tracks running requests at endpoint. **Structural semantic gap:** Router-level counting includes requests in transit (not yet received by endpoint); endpoint-level counting only includes received requests. Same unit (request count) but different counting points. **PR5 MUST measure:** Compare sim InFlightRequests against prod RunningRequestCount under load to quantify the router-vs-endpoint gap. If R² < 0.80, downgrade to low. **Note:** Earlier drafts mapped to `ActiveModels` which is incorrect — ActiveModels counts model instances, not requests. `RunningRequestCount` is the correct production equivalent. If `RunningRequestCount` is unavailable, fall back to `RunningQueueSize` as a proxy. **F-10 WARNING: Double-counting risk** — `RunningQueueSize` is already the production mapping for BatchSize. If InFlightRequests also falls back to `RunningQueueSize`, the EffectiveLoad composite becomes `WaitingQueueSize + 2*RunningQueueSize`, double-counting that metric. PR3 MUST detect this case and either: (a) use a different proxy, or (b) adjust the composite computation to avoid double-counting. |
| KVUtilization | float64 | `snap.KVUtilization` | `endpoint.GetMetrics().KVCacheUsagePercent` | Custom scorer needed | high | 0 | Same computation: ratio of used KV cache to total. Both query endpoint metrics directly. **Units:** Sim = 0.0–1.0 ratio; Prod = 0–100 percentage. **Normalization (PR3):** Divide production value by 100 to match sim's 0.0–1.0 range (i.e., `prod_kv / 100.0`). The evolved algorithm expects the sim-scale range. **REQUIRED PR3 TEST:** PR3 MUST include a unit test verifying that production KVCacheUsagePercent values (0-100) are divided by 100 before being passed to the scorer. Without this normalization, the evolved algorithm receives values 100x larger than trained on. |
| CacheHitRate | float64 | `snap.CacheHitRate` | Prefix cache hit ratio from engine metrics | PrecisePrefixCache scorer | medium *(provisional)* | 0 | Sim uses router-side approximate cache index; production uses engine-reported precise cache metrics. Different data sources for same concept. **Provisional rating:** No empirical data supports the medium threshold (R² ≥ 0.80); the different data sources (approximate vs precise) could produce a larger gap than assumed. PR5 must validate empirically; if R² < 0.80, downgrade to low. |

## Composite Signals

| Composite | Expansion | Production Equivalent | Fidelity | Notes |
|-----------|-----------|----------------------|----------|-------|
| EffectiveLoad() | QueueDepth + BatchSize + InFlightRequests | WaitingQueueSize + RunningQueueSize + RunningRequestCount | medium (composite) | No single production metric equivalent. PR3 scorer must compute inline. Semantic gaps in constituent signals (see individual rows above) propagate to composite. **Composite fidelity computation (F-10):** The composite rating is the minimum of constituent ratings: min(high, medium, medium) = medium. |

## Additional Signals (Non-RoutingSnapshot)

| Signal | Context | Production Mapping | Fidelity | Notes |
|--------|---------|-------------------|----------|-------|
| SessionID (boolean check) | `req.SessionID != ""` | Request header `x-session-id` | high | Boolean presence check — identical semantics. |

## Fidelity Rating Scale

- **high**: Same computation, same data source, negligible staleness. Quantitative: R² ≥ 0.99 or max |sim − prod| ≤ 1% of range.
- **medium**: Equivalent computation but different data source or non-trivial staleness. Quantitative: R² ≥ 0.80 or rank-order correlation ≥ 0.90.
- **low**: Approximate or proxy signal with known semantic gap. Pipeline halts on low-fidelity signals. Quantitative: R² < 0.80 or qualitative gap documented.

> **Note:** Quantitative thresholds are provisional targets for PR5 (validation pipeline). PR1 ratings are based on design analysis; empirical validation deferred to Stage 5.
>
> **Rollback procedure if PR5 downgrades a rating to low:** If empirical validation in PR5 reveals that a signal rated medium (e.g., CacheHitRate) actually has R² < 0.80, the rating must be downgraded to low in this mapping artifact. This will cause BC-6 to halt future extract runs.

## Scorer Interface Reference

> **R3-F-7 WARNING — UNVERIFIED:** This section was documented from design knowledge, not verified against the actual `llm-d-inference-scheduler` source at the pinned commit.

Target system: `llm-d-inference-scheduler` (gateway-api-inference-extension framework)

- **Interface:** `scheduling.Scorer` with `Score(ctx, cycleState, request, endpoints) map[Endpoint]float64`
- **Factory pattern:** `plugin.Register(typeName, factoryFunc)` in `pkg/plugins/register.go`
- **Existing scorers:** LoadAware, ActiveRequest, SessionAffinity, PrecisePrefixCache, NoHitLRU
- **Config:** YAML-based with scorer name, type, weight, and optional parameters.

> **Note for PR3:** This section provides a high-level reference for context only. PR3 MUST derive the full interface specification directly from the `llm-d-inference-scheduler` codebase at the pinned commit hash above.

## Notes

- All v1 signals have `staleness_window_ms = 0` (approximate-scorer class).
- **Temporal semantics assumption:** Sim RoutingSnapshot is a point-in-time snapshot. Production metrics are assumed to be point-in-time queries to endpoint `GetMetrics()`. PR5 must verify.
- **EffectiveLoad() composite signal:** `EffectiveLoad() = QueueDepth + BatchSize + InFlightRequests`. Production equivalent: sum the mapped production values. PR3 scorer must implement this composite computation inline.
- CacheHitRate mapping to PrecisePrefixCache scorer may require adaptation since production uses ZMQ-based precise metrics while sim uses approximate router-side index.
- **Missing/default value assumption:** All production metrics are assumed to be always available from `endpoint.GetMetrics()`. If a metric field is missing, the PR3 scorer should return score 0.0 for that endpoint.
