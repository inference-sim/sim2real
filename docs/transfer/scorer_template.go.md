# Scorer Template — llm-d-inference-scheduler Plugin

**Version:** 1.0
**Pinned commit:** `091312c333a50e94f5e60a2ca2926e8442eeffa9`
**Based on:** `pkg/plugins/scorer/load_aware.go` (LoadAware scorer)
**Module:** `github.com/llm-d/llm-d-inference-scheduler`
**Go version:** 1.25.7

> **For Stage 3 LLM:** Use this template as the structural reference for generating a production scorer plugin. Follow the conventions exactly — package location, factory signature, type assertion, test patterns. To generate the scoring logic:
> - **Signal names and types:** Read from `workspace/algorithm_summary.json` (the `signals` and `composite_signals` arrays).
> - **Scoring logic (weights, penalty functions, formulas):** Extract from the EVOLVE-BLOCK source file at the path in `algorithm_summary.json`'s `evolve_block_source` field. The source is a Python file containing Go code embedded in a triple-quoted string literal (`GO_ROUTING_CODE = """..."""`). You MUST parse the Python file to extract the embedded Go, then translate the scoring logic into the `Score()` method below.
> - **Signal-to-production mappings:** Use `docs/transfer/blis_to_llmd_mapping.md` to map simulation signal names to production `endpoint.GetMetrics()` fields.
> - **Do NOT assume `algorithm_summary.json` contains scoring logic** — it captures signal metadata only (see schema description).
>
> **PREREQUISITE — Artifact Validation (before consuming `workspace/algorithm_summary.json`):**
> 1. Verify the file exists: if `workspace/algorithm_summary.json` does not exist on disk, HALT — the extract stage has not run successfully.
> 2. Validate schema: run `python tools/transfer_cli.py validate-schema workspace/algorithm_summary.json` and confirm exit code 0.
> 3. Check scope validation: read `scope_validation_passed` from the JSON — if `false`, HALT — the algorithm contains out-of-scope patterns.
> 4. **Stale artifact caveat:** Steps 1-3 are the Stage 3 LLM's actionable validation checks. However, a stale artifact from a prior successful extract run may remain on disk after a subsequent failed run (see CLAUDE.md). Exit code verification is the **pipeline orchestrator's** responsibility (not Stage 3's) — the orchestrator MUST confirm the extract stage exited 0 before invoking Stage 3. If Stage 3 is invoked without orchestrator validation, Steps 1-3 provide best-effort freshness detection but cannot guarantee the artifact corresponds to the most recent extract invocation.
> 5. **EVOLVE-BLOCK content hash verification:** Read `evolve_block_content_hash` and `evolve_block_source` from `algorithm_summary.json`. Parse the file path and line range from `evolve_block_source` (format: `path/to/file.py:START-END`). Read lines START through END (inclusive) from that file. To compute the hash: join the lines with `\n` (Unix newline, U+000A) as the separator, append a trailing `\n` after the last line, encode the result as UTF-8, and compute the SHA-256 hex digest (lowercase). This matches `transfer_cli.py`'s `extract` command, which reads lines with Python's `readlines()` (preserving trailing newlines) and hashes the UTF-8 encoded concatenation. Compare the computed digest against `evolve_block_content_hash`. If the hashes differ, HALT — the EVOLVE-BLOCK source has changed since extraction and the signal list in `algorithm_summary.json` may be stale. Re-run the extract stage before proceeding.
>
> **IMPORTANT — UNVERIFIED FIELD HALT CONDITION (PR3 scope — applies during code generation, not during PR2 template authoring):** Metric field names marked `// UNVERIFIED` in this template have NOT been confirmed against the actual `fwkdl.Metrics` struct at the pinned dependency version (`gateway-api-inference-extension v0.0.0-20260128235548-fd30cb97714a`). Before generating code, you MUST:
> 1. Initialize the submodule and run `go mod download`
> 2. Locate the `fwkdl.Metrics` struct definition and confirm each UNVERIFIED field name exists
> 3. **If a field does NOT exist:** (a) Search the codebase for the closest equivalent field name, (b) Update the mapping artifact and this template with the correct name, (c) If no equivalent exists, remove the field from the template and note it as UNAVAILABLE in the mapping artifact
> 4. **HALT CONDITION:** If fewer than 2 of the 3 UNVERIFIED fields can be resolved (confirmed or mapped to alternatives), STOP code generation and escalate — the evolved algorithm may not be implementable with the available production metrics. Do NOT generate a scorer that silently drops signals.

---

## Section 1: Package Structure

The scorer lives in the scorer plugin package alongside existing scorers.

```
llm-d-inference-scheduler/
├── pkg/
│   └── plugins/
│       ├── register.go           ← Add plugin.Register() call here
│       └── scorer/
│           ├── load_aware.go     ← Existing (reference implementation)
│           ├── load_aware_test.go
│           ├── <your_scorer>.go       ← New scorer file (includes ScoreEndpoints helper — see Section 7)
│           └── <your_scorer>_test.go  ← New test file (package scorer_test — external test package)
└── test/
    └── utils/
        └── context.go            ← Test context helper
```

**Convention:** One scorer per file. File name matches scorer type (snake_case). Test file is `<name>_test.go` using the external test package (`package scorer_test`) for black-box testing.

---

## Section 2: Scorer Interface Implementation

The `scheduling.Scorer` interface is defined in the `gateway-api-inference-extension` framework. All scorers MUST implement these three methods.

```go
package scorer

import (
	"context"
	"encoding/json"
	"fmt"

	"sigs.k8s.io/controller-runtime/pkg/log"
	logutil "sigs.k8s.io/gateway-api-inference-extension/pkg/common/util/logging"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/plugin"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"
)

const (
	// EvolvedScorerType is the unique type name for this scorer.
	// Convention: lowercase-hyphenated, suffixed with "-scorer".
	EvolvedScorerType = "evolved-routing-scorer"
)

// Compile-time type assertion — ensures EvolvedScorer implements scheduling.Scorer.
// If the interface changes, this line produces a compile error (not a runtime panic).
var _ scheduling.Scorer = &EvolvedScorer{}

// EvolvedScorer implements the evolved routing algorithm as a production scorer.
//
// ANNOTATION: The struct holds:
// - typedName: required by all scorers for plugin identity
// - config fields: parsed from JSON parameters at factory time
// - enabled: feature flag for config toggle (see Section 5)
type EvolvedScorer struct {
	typedName plugin.TypedName
	enabled   bool

	// Config fields from factory parameters.
	// These are set once at construction time; not hot-reloadable in v1.
	queueThreshold float64
}

// TypedName returns the plugin's type and instance name.
// ANNOTATION: The Type is the constant (e.g., "evolved-routing-scorer").
// The Name is set by WithName() from the factory — it's the instance name
// from the scheduler config YAML.
func (s *EvolvedScorer) TypedName() plugin.TypedName {
	return s.typedName
}

// WithName sets the instance name. Called by the factory.
// Convention: every scorer has this method.
func (s *EvolvedScorer) WithName(name string) *EvolvedScorer {
	s.typedName.Name = name
	return s
}

// Category returns the scoring category.
// ANNOTATION: Use scheduling.Distribution for load-balancing scorers
// (spread traffic across endpoints). Use scheduling.Affinity for
// session-pinning scorers (prefer specific endpoints).
// The evolved algorithm is a load-distribution scorer.
func (s *EvolvedScorer) Category() scheduling.ScorerCategory {
	return scheduling.Distribution
}

// Score computes per-endpoint scores in the range [0.0, 1.0].
//
// ANNOTATION: This is the core method. It receives:
// - ctx: context for logging and cancellation
// - cycleState: shared state across plugins in a scheduling cycle (may be nil in tests).
//   CONSTRAINT: The ScoreEndpoints test helper (Section 7) always passes nil for cycleState.
//   Generated scorers MUST NOT dereference cycleState — if the evolved algorithm needs
//   inter-plugin state, escalate (the harness must be extended first).
// - request: the incoming LLM request (model name, headers, etc.)
// - endpoints: candidate endpoints to score
//
// Returns: map from endpoint to score. Higher score = more preferred.
// Convention: scores MUST be in [0.0, 1.0]. The scheduler normalizes and
// applies weights across all scorers.
//
// >>> REPLACE THIS BODY with the evolved algorithm's scoring logic <<<
// >>> Signal names/types: from algorithm_summary.json; Scoring logic: extract from EVOLVE-BLOCK at evolve_block_source path <<<
// >>> Signal-to-production mappings: from blis_to_llmd_mapping.md <<<
func (s *EvolvedScorer) Score(ctx context.Context, _ *scheduling.CycleState, request *scheduling.LLMRequest, endpoints []scheduling.Endpoint) map[scheduling.Endpoint]float64 {
	logger := log.FromContext(ctx)

	// Feature flag check — see Section 5
	if !s.enabled {
		return nil // nil scores = scorer is inactive (UNVERIFIED — see BC-4; PR3 must confirm framework behavior)
	}

	scoredEndpoints := make(map[scheduling.Endpoint]float64, len(endpoints))

	for _, endpoint := range endpoints {
		// --- Metric access (see Section 3 for field details) ---
		metrics := endpoint.GetMetrics()

		// ANNOTATION: Defensive nil check — if an endpoint has no metrics,
		// return score 0.0 for that endpoint rather than panicking.
		// NOTE: LoadAware has no nil guard (it accesses .WaitingQueueSize directly
		// and returns 0.5 for zero-value, non-nil metrics). The mapping artifact
		// states "All production metrics are assumed to be always available from
		// endpoint.GetMetrics()" — nil-pointer handling is out of scope for that
		// contract. This nil guard is a defensive safety net beyond LoadAware's
		// behavior, not a mapping-artifact requirement. This satisfies BC-9.
		if metrics == nil {
			scoredEndpoints[endpoint] = 0.0
			continue
		}

		// VERIFIED field (confirmed in load_aware.go:87):
		waitingQueueSize := float64(metrics.WaitingQueueSize) // VERIFIED

		// ⚠ UNVERIFIED FIELDS — DO NOT UNCOMMENT until PR3 confirms they exist in fwkdl.Metrics.
		// If a field does not exist, see HALT CONDITION in the template header.
		// runningQueueSize := float64(metrics.RunningQueueSize)       // UNVERIFIED — may not compile
		// runningRequestCount := float64(metrics.RunningRequestCount) // UNVERIFIED — may not compile
		// kvCacheUsagePct := float64(metrics.KVCacheUsagePercent)     // UNVERIFIED — may not compile

		// --- PLACEHOLDER: simple load-based score (PR3 MUST replace with evolved logic) ---
		// PR3 validation: grep for "PLACEHOLDER" in generated code. If found, generation is incomplete.
		score := 0.5
		if waitingQueueSize > 0 {
			if waitingQueueSize > s.queueThreshold {
				waitingQueueSize = s.queueThreshold
			}
			score = 0.5 * (1.0 - (waitingQueueSize / s.queueThreshold))
		}

		// ANNOTATION: Normalization example for KVUtilization.
		// Sim uses 0.0–1.0 range; production KVCacheUsagePercent is 0–100.
		// Normalize: kvUtilization := kvCacheUsagePct / 100.0
		// See blis_to_llmd_mapping.md KVUtilization row for details.

		// ANNOTATION: Composite signal example (EffectiveLoad).
		// EffectiveLoad = QueueDepth + BatchSize + InFlightRequests
		// Production: WaitingQueueSize + RunningQueueSize + RunningRequestCount
		// Compute inline — there is no single production metric equivalent.
		// effectiveLoad := waitingQueueSize + runningQueueSize + runningRequestCount

		scoredEndpoints[endpoint] = score
		_ = logger // suppress unused warning; use logger.V(logutil.DEBUG).Info(...) for debug output
	}

	return scoredEndpoints
}
```

---

## Section 3: Metric Access Pattern

Scorers access per-endpoint metrics via `endpoint.GetMetrics()`. The metrics are populated by the data layer from endpoint health reports.

| Production Field | Go Access | Sim Equivalent | Status | Notes |
|-----------------|-----------|----------------|--------|-------|
| `WaitingQueueSize` | `endpoint.GetMetrics().WaitingQueueSize` | QueueDepth | **VERIFIED** (load_aware.go:87) | int, count of waiting requests |
| `RunningQueueSize` | `endpoint.GetMetrics().RunningQueueSize` | BatchSize (approx) | **UNVERIFIED** | int, PR3 must confirm field exists |
| `RunningRequestCount` | `endpoint.GetMetrics().RunningRequestCount` | InFlightRequests (approx) | **UNVERIFIED** | int, PR3 must confirm field exists |
| `KVCacheUsagePercent` | `endpoint.GetMetrics().KVCacheUsagePercent` | KVUtilization | **UNVERIFIED** | float64 (0-100), divide by 100 for sim scale |

**Request-level access:**
| Field | Go Access | Status |
|-------|-----------|--------|
| Session header | `request.Headers["x-session-token"]` | **VERIFIED** (session_affinity.go:20 defines `sessionTokenHeader = "x-session-token"`; line 66 accesses via this constant) |

**Endpoint metadata access:**
| Field | Go Access | Status |
|-------|-----------|--------|
| Endpoint name | `endpoint.GetMetadata().NamespacedName.String()` | **VERIFIED** (session_affinity.go:79) |

> **CacheHitRate note:** The mapping artifact maps CacheHitRate to the PrecisePrefixCache scorer. However, `PrecisePrefixCache` does NOT simply read a metric field — it uses a ZMQ-based KV cache indexer (`llm-d-kv-cache` package) with its own event-driven state. Accessing cache hit rate may require a different pattern than `GetMetrics()`.
>
> **PR3 investigation steps:**
> 1. Read `pkg/plugins/scorer/precise_prefix_cache.go` to understand the KV cache indexer integration
> 2. Determine if cache hit rate is available as a computed value or requires the ZMQ indexer
> 3. **If cache hit rate is not accessible via `GetMetrics()`:** The evolved scorer cannot use CacheHitRate as a simple metric field. Options: (a) omit CacheHitRate from the evolved scorer and note the signal loss, (b) integrate the KV cache indexer as a dependency (significant complexity increase), (c) use a proxy metric if one exists
> 4. **HALT CONDITION:** If CacheHitRate appears in `algorithm_summary.json`'s signals array (indicating it is used by the evolved algorithm) and no feasible access path exists, PR3 must parse the EVOLVE-BLOCK source (at the location in `algorithm_summary.json`'s `evolve_block_source`) to determine how prominently CacheHitRate is used. If CacheHitRate is used in the algorithm's scoring logic (e.g., in a penalty term or composite signal) and cannot be accessed, escalate — the algorithm may need re-evolution without this signal. If CacheHitRate is present in the signals array but not referenced in the scoring logic of the EVOLVE-BLOCK, it can be omitted with documented signal loss. Note: `algorithm_summary.json` captures signal metadata only, not scoring weights — PR3 must parse the EVOLVE-BLOCK directly to assess signal importance (see schema description).

---

## Section 4: Config Registration (Factory)

Every scorer MUST have a factory function and be registered in `register.go`.

```go
// --- Factory function ---
// ANNOTATION: Signature is fixed by the plugin framework.
// Parameters:
//   - name: instance name from scheduler config YAML
//   - rawParameters: JSON blob from config YAML "parameters" field
//   - handle: provides context and shared resources
//
// Convention: provide sensible defaults; only fail on genuinely invalid config.
func EvolvedScorerFactory(name string, rawParameters json.RawMessage, handle plugin.Handle) (plugin.Plugin, error) {
	params := EvolvedScorerParameters{
		QueueThreshold: 128,  // sensible default
		Enabled:        true, // enabled by default
	}
	if rawParameters != nil {
		if err := json.Unmarshal(rawParameters, &params); err != nil {
			return nil, fmt.Errorf("failed to parse parameters for '%s' scorer: %w", EvolvedScorerType, err)
		}
	}

	return NewEvolvedScorer(handle.Context(), params).WithName(name), nil
}

// --- Config struct ---
// ANNOTATION: Fields map to the YAML "parameters" block in the scheduler config.
// Use json tags for serialization. Provide defaults in the factory.
// ANNOTATION: Exported (uppercase) because external test packages (scorer_test)
// need to construct test instances via NewEvolvedScorer().
type EvolvedScorerParameters struct {
	QueueThreshold int  `json:"queueThreshold"`
	Enabled        bool `json:"enabled"`
}

// --- Constructor ---
func NewEvolvedScorer(ctx context.Context, params EvolvedScorerParameters) *EvolvedScorer {
	if params.QueueThreshold <= 0 {
		params.QueueThreshold = 128
		log.FromContext(ctx).V(logutil.DEFAULT).Info("queueThreshold must be positive, using default 128")
	}

	return &EvolvedScorer{
		typedName:      plugin.TypedName{Type: EvolvedScorerType},
		enabled:        params.Enabled,
		queueThreshold: float64(params.QueueThreshold),
	}
}
```

**Registration in `pkg/plugins/register.go`:**

```go
// Add this line to RegisterAllPlugins() in register.go:
plugin.Register(scorer.EvolvedScorerType, scorer.EvolvedScorerFactory)
```

**Example scheduler config YAML:**

```yaml
scorers:
  - type: evolved-routing-scorer
    name: blis-evolved-v1
    weight: 1.0
    parameters:
      queueThreshold: 128
      enabled: true
```

---

## Section 5: Feature Flag (Config Toggle)

The generated scorer MUST be disableable via config toggle (Macro Plan Objective 4).

**Implementation pattern:**

1. **Config struct field:** `Enabled bool` in parameters (see Section 4)
2. **Score() early-return:** When disabled, return `nil` (see Section 2)
3. **Behavior when disabled:** Returning `nil` from `Score()` is expected to cause the scheduler to skip this scorer entirely — no scores contribute to the routing decision, and remaining active scorers determine routing. **PARTIALLY VERIFIED:** PrecisePrefixCache.Score() returns nil in error/nil-request paths (precise_prefix_cache.go:225,240), confirming the framework encounters nil returns. However, intentional feature-flag opt-out via nil is untested; PR3 must verify nil-score handling in the framework's aggregation logic (see BC-4).
4. **Toggle mechanism:** Change `enabled: false` in the scheduler config YAML and restart the scheduler (not hot-reloadable in v1 — see Section 8)

```go
// In Score() method:
if !s.enabled {
	return nil // nil scores = scorer is inactive (UNVERIFIED — see BC-4; PR3 must confirm framework behavior)
}
```

**Required unit test (see Section 6):** Verify that when `Enabled: false`, `Score()` returns nil.

---

## Section 6: Unit Test Structure

Tests follow the table-driven pattern used by all existing scorers.

```go
package scorer_test

import (
	"context"
	"testing"

	"github.com/google/go-cmp/cmp"
	k8stypes "k8s.io/apimachinery/pkg/types"
	fwkdl "sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/datalayer"
	"sigs.k8s.io/gateway-api-inference-extension/pkg/epp/framework/interface/scheduling"

	"github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer"
	"github.com/llm-d/llm-d-inference-scheduler/test/utils"
)

// ANNOTATION: Test endpoint creation pattern.
// scheduling.NewEndpoint takes (metadata, metrics, attributes).
// - metadata: endpoint identity (pod name)
// - metrics: current endpoint metrics (populate fields relevant to your scorer)
// - attributes: usually nil for scorer tests (used by filter plugins)

func TestEvolvedScorer(t *testing.T) {
	// --- Test endpoint creation ---
	endpointA := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-a"}},
		&fwkdl.Metrics{WaitingQueueSize: 2}, // VERIFIED field
		nil,
	)
	endpointB := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-b"}},
		&fwkdl.Metrics{WaitingQueueSize: 0},
		nil,
	)
	endpointHeavy := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-heavy"}},
		&fwkdl.Metrics{WaitingQueueSize: 200}, // above threshold
		nil,
	)
	endpointNilMetrics := scheduling.NewEndpoint(
		&fwkdl.EndpointMetadata{NamespacedName: k8stypes.NamespacedName{Name: "pod-nil"}},
		nil, // nil metrics pointer — tests defensive nil check in Score()
		nil,
	)

	tests := []struct {
		name       string
		scorer     scheduling.Scorer
		req        *scheduling.LLMRequest
		input      []scheduling.Endpoint
		wantScores map[scheduling.Endpoint]float64
	}{
		// --- Test A: Normal scoring (BC-2) ---
		{
			name:   "scores endpoints based on load",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointA, endpointB, endpointHeavy},
			wantScores: map[scheduling.Endpoint]float64{
				endpointA:     0.4,  // 2/10 load → 0.5 * (1 - 0.2) = 0.4
				endpointB:     0.5,  // empty queue → 0.5
				endpointHeavy: 0.0,  // capped at threshold → 0
			},
		},

		// --- Test B: Disabled scorer / no-op (BC-4) ---
		{
			name:       "disabled scorer returns nil",
			scorer:     scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: false}),
			req:        &scheduling.LLMRequest{TargetModel: "test-model"},
			input:      []scheduling.Endpoint{endpointA, endpointB},
			wantScores: nil, // nil = scorer inactive
		},

		// --- Test C: Score range [0, 1] boundary (BC-9) ---
		// ANNOTATION: The placeholder formula produces scores in [0, 0.5] only.
		// This is intentional for the example (matches LoadAware's current range).
		// The evolved algorithm SHOULD use the full [0.0, 1.0] range.
		// PR3's generated scorer tests should verify scores across the full range.
		{
			name:   "scores are within [0, 1] range",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 128, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointA, endpointB, endpointHeavy},
			wantScores: map[scheduling.Endpoint]float64{
				endpointA:     0.4921875, // 2/128 → 0.5 * (1 - 0.015625)
				endpointB:     0.5,
				endpointHeavy: 0.0, // 200 > 128, capped
			},
		},

		// --- Test D: Nil metrics graceful handling (BC-9) ---
		// UNVERIFIED DEPENDENCY: This test assumes scheduling.NewEndpoint(metadata, nil, nil).GetMetrics()
		// returns nil (not a zero-value *fwkdl.Metrics{} struct). If GetMetrics() returns a zero-value
		// struct, the nil guard won't fire and the expected score changes from 0.0 to 0.5.
		// Task 1 Fact #10 requires verifying this behavior. Update wantScore if needed.
		{
			name:   "nil metrics returns zero score",
			scorer: scorer.NewEvolvedScorer(utils.NewTestContext(t), scorer.EvolvedScorerParameters{QueueThreshold: 10, Enabled: true}),
			req:    &scheduling.LLMRequest{TargetModel: "test-model"},
			input:  []scheduling.Endpoint{endpointNilMetrics},
			wantScores: map[scheduling.Endpoint]float64{
				endpointNilMetrics: 0.0, // UNVERIFIED — defensive nil guard score; see Fact #10 and Deviation Log
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := test.scorer.Score(context.Background(), nil, test.req, test.input)
			if diff := cmp.Diff(test.wantScores, got); diff != "" {
				t.Errorf("Unexpected scores (-want +got):\n%s", diff)
			}
		})
	}
}
```

**ANNOTATION — Test patterns to include for the generated scorer:**
- **Request parsing:** Test that request headers (e.g., `x-session-token`) are read correctly if the evolved algorithm uses them
- **No-op / disabled:** Test that `Enabled: false` returns nil scores (BC-4)
- **Score range:** Verify all scores in [0, 1] across diverse endpoint states
- **Zero metrics:** Verify graceful handling when all metrics are zero/default
- **Threshold boundary:** Test behavior at exactly the threshold value

---

## Section 7: ScoreEndpoints Equivalence Test Helper

> **NOTE — File placement:** This function MUST reside in `<your_scorer>.go` (the main scorer file), NOT in `<your_scorer>_test.go`. The test file uses `package scorer_test` (external test package), which cannot be imported by external packages. The Go test harness (`tools/harness/`) imports this function as `scorer.ScoreEndpoints(...)`, which requires it to be in a non-test file in `package scorer`.

This helper function bridges the scorer's `Score()` output (keyed by `scheduling.Endpoint`) to a format compatible with the Go test harness (`tools/harness/`), which is created by **PR3** (Prompt Templates + Go Harness). PR3 creates the harness; PR5 (Validation Pipeline) extends it with Suite A/B/C logic. The equivalence testing needs to compare simulation scores against production scores using the same endpoint identifiers.

```go
// ScoreEndpoints is a test helper that runs the scorer and returns results
// keyed by endpoint name (string) instead of scheduling.Endpoint.
//
// ANNOTATION: This function is called by the Go test harness (tools/harness/)
// during equivalence testing (Stage 5). The harness is created by PR3
// (Prompt Templates + Go Harness) and extended by PR5 (Validation Pipeline).
// It provides a stable interface that doesn't depend on the
// scheduling.Endpoint type's identity semantics.
//
// WARNING — CycleState constraint: This helper passes nil for cycleState.
// Generated scorers MUST NOT dereference or depend on cycleState in their
// Score() implementation. If the evolved algorithm requires inter-plugin
// shared state via CycleState, the harness and this helper must be extended
// to provide a non-nil CycleState before equivalence testing can work.
// Stage 3 LLM: if the EVOLVE-BLOCK references shared state across plugins,
// flag this as a constraint violation and escalate.
//
// Usage in harness:
//   scores := scorer.ScoreEndpoints(ctx, evolvedScorer, request, endpoints)
//   // scores["pod-a"] = 0.4, scores["pod-b"] = 0.5, etc.
//   // Compare against simulation Route() output for the same inputs.
func ScoreEndpoints(
	ctx context.Context,
	s scheduling.Scorer,
	request *scheduling.LLMRequest,
	endpoints []scheduling.Endpoint,
) map[string]float64 {
	// NOTE: nil cycleState — see WARNING above. Generated scorers must not access cycleState.
	raw := s.Score(ctx, nil, request, endpoints)
	if raw == nil {
		return nil
	}

	result := make(map[string]float64, len(raw))
	for endpoint, score := range raw {
		name := endpoint.GetMetadata().NamespacedName.String()
		if _, exists := result[name]; exists {
			// Duplicate NamespacedName would silently overwrite scores,
			// corrupting equivalence test results (e.g., Kendall-tau rank correlation).
			// Kubernetes enforces NamespacedName uniqueness in practice, but test
			// fixtures or harness bugs could produce duplicates.
			panic(fmt.Sprintf("ScoreEndpoints: duplicate endpoint name %q — input contains two endpoints with the same NamespacedName", name))
		}
		result[name] = score
	}
	return result
}
```

**ANNOTATION:** The harness creates `scheduling.Endpoint` objects from test tuples (mapping sim `RouterState` fields to production `Metrics` fields per the mapping artifact), calls `ScoreEndpoints`, and compares the output against simulation `Route()` results. The comparison uses Kendall-tau rank correlation (Suite A threshold: 0.8) and numeric fidelity (1e-6 abs or 1% relative).

---

## Section 8: Hot-Reload Documentation

**v1 status:** Hot-reload is NOT supported. The scorer config is read at initialization time (factory function). Changing the `enabled` field or any parameter requires a scheduler restart.

```go
// ANNOTATION: v1 — no hot-reload support.
// Config is parsed once in the factory function (Section 4) and stored in the struct.
// There is no Reconfigure() method. To change parameters, restart the scheduler.
//
// The EvolvedScorer struct fields (enabled, queueThreshold) are set at construction
// time and never modified after. This is safe for concurrent Score() calls.
```

**Toggle procedure (v1):**
1. Edit scheduler config YAML: set `enabled: false` under the scorer's parameters
2. Restart the scheduler process
3. The scorer's factory function re-reads the config and constructs the scorer with `Enabled: false`
4. `Score()` returns nil for all requests → scheduler uses remaining active scorers

**Future consideration (v2+):** If the framework adds a `Reconfigure(rawParameters json.RawMessage)` method to the plugin interface, the scorer could support hot-reload by re-parsing the config and updating the `enabled` field atomically. This would eliminate the restart requirement.

---

## Stage 3 Output Validation

> **Before handing off to Stage 4**, Stage 3 MUST verify all of the following. If any check fails, Stage 3 generation is incomplete — fix and re-check before proceeding.

1. **No PLACEHOLDER markers:** `grep -r "PLACEHOLDER" <your_scorer>.go <your_scorer>_test.go` must return zero matches. Any remaining PLACEHOLDER indicates incomplete generation.
2. **Do NOT compile:** Compilation (`go build`) is deferred to Stage 4, which has the full Go module environment. Stage 3 should NOT attempt `go build` — failure in an environment without Go module setup would produce misleading errors.
3. **Structural invariants — verify these are present in the generated code:**
   - Import paths unchanged from template (same `sigs.k8s.io/...` paths)
   - Type assertion: `var _ scheduling.Scorer = &EvolvedScorer{}` present
   - Factory function registered: `plugin.Register(scorer.EvolvedScorerType, scorer.EvolvedScorerFactory)` added to `register.go`
   - UNVERIFIED metric fields remain commented-out unless explicitly confirmed by PR3's field verification step
   - `ScoreEndpoints` helper function present in `<your_scorer>.go` (not in `_test.go`)
4. **Test structure:** `<your_scorer>_test.go` contains at minimum: (a) table-driven scoring test, (b) disabled/no-op test returning nil, (c) nil-metrics graceful handling test
