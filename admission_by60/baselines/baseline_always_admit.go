package sim

import (
	"fmt"
	"math"
)

// AdmissionPolicy decides whether a request is admitted for processing.
// Used by ClusterSimulator's online routing pipeline to gate incoming requests.
// Receives *RouterState with cluster-wide snapshots and clock.
type AdmissionPolicy interface {
	Admit(req *Request, state *RouterState) (admitted bool, reason string)
}

// AlwaysAdmit admits all requests unconditionally.
type AlwaysAdmit struct{}

func (a *AlwaysAdmit) Admit(_ *Request, _ *RouterState) (bool, string) {
	return true, ""
}

// TokenBucket implements rate-limiting admission control.
type TokenBucket struct {
	capacity      float64
	refillRate    float64 // tokens per second
	currentTokens float64
	lastRefill    int64 // last refill clock time in microseconds
}

// NewTokenBucket creates a TokenBucket with the given capacity and refill rate.
// Panics if capacity or refillRate is <= 0, NaN, or Inf (R3: validate at construction).
func NewTokenBucket(capacity, refillRate float64) *TokenBucket {
	if capacity <= 0 || math.IsNaN(capacity) || math.IsInf(capacity, 0) {
		panic(fmt.Sprintf("NewTokenBucket: capacity must be a finite value > 0, got %v", capacity))
	}
	if refillRate <= 0 || math.IsNaN(refillRate) || math.IsInf(refillRate, 0) {
		panic(fmt.Sprintf("NewTokenBucket: refillRate must be a finite value > 0, got %v", refillRate))
	}
	return &TokenBucket{
		capacity:      capacity,
		refillRate:    refillRate,
		currentTokens: capacity,
	}
}

// Admit checks whether the request can be admitted given current token availability.
func (tb *TokenBucket) Admit(req *Request, state *RouterState) (bool, string) {
	clock := state.Clock
	elapsed := clock - tb.lastRefill
	if elapsed > 0 {
		refill := float64(elapsed) * tb.refillRate / 1e6
		tb.currentTokens = min(tb.capacity, tb.currentTokens+refill)
		tb.lastRefill = clock
	}
	cost := float64(len(req.InputTokens))
	if tb.currentTokens >= cost {
		tb.currentTokens -= cost
		return true, ""
	}
	return false, "insufficient tokens"
}

// RejectAll rejects all requests unconditionally (pathological template for testing).
type RejectAll struct{}

func (r *RejectAll) Admit(_ *Request, _ *RouterState) (bool, string) {
	return false, "reject-all"
}

// AdaptiveAdmission implements cluster-aware, SLO-aware admission control.
// The Admit() logic inside the EVOLVE-BLOCK is mutated by the search framework.
type AdaptiveAdmission struct {
	tenantTokens   map[string]float64 // per-tenant token budget tracker
	tenantRequests map[string]int     // per-tenant request counter
	classCounters  map[string]int     // per-SLO-class admission counter
	windowStart    int64              // sliding window start (microseconds)
	windowCount    int                // requests in current window
	totalAdmitted  int
	totalRejected  int
	lastClock      int64
}

// NewAdaptiveAdmission creates an AdaptiveAdmission with pre-initialized state maps.
func NewAdaptiveAdmission() *AdaptiveAdmission {
	return &AdaptiveAdmission{
		tenantTokens:   make(map[string]float64),
		tenantRequests: make(map[string]int),
		classCounters:  make(map[string]int),
	}
}

// Admit implements AdmissionPolicy for AdaptiveAdmission.
func (a *AdaptiveAdmission) Admit(req *Request, state *RouterState) (bool, string) {
	// --- Derived signals (fixed, available to EVOLVE-BLOCK) ---
	numInstances := len(state.Snapshots)
	totalInFlight := 0
	totalQueueDepth := 0
	maxKVUtil := 0.0
	avgKVUtil := 0.0
	minFreeKV := int64(math.MaxInt64)
	for _, snap := range state.Snapshots {
		totalInFlight += snap.InFlightRequests
		totalQueueDepth += snap.QueueDepth
		if snap.KVUtilization > maxKVUtil {
			maxKVUtil = snap.KVUtilization
		}
		avgKVUtil += snap.KVUtilization
		if snap.FreeKVBlocks < minFreeKV {
			minFreeKV = snap.FreeKVBlocks
		}
	}
	if numInstances > 0 {
		avgKVUtil /= float64(numInstances)
	}
	inputLen := len(req.InputTokens)
	sloClass := req.SLOClass
	tenantID := req.TenantID
	clock := state.Clock

	// Suppress "unused variable" errors for signals the LLM may or may not use.
	_ = numInstances
	_ = totalInFlight
	_ = totalQueueDepth
	_ = maxKVUtil
	_ = avgKVUtil
	_ = minFreeKV
	_ = inputLen
	_ = sloClass
	_ = tenantID
	_ = clock

	// EVOLVE-BLOCK-START
	// Baseline: admit everything.
	// The search framework will evolve this block to discover adaptive
	// admission strategies that balance utilization, fairness, and isolation.
	//
	// Available local vars:
	//   numInstances, totalInFlight, totalQueueDepth,
	//   maxKVUtil, avgKVUtil, minFreeKV,
	//   inputLen, sloClass, tenantID, clock
	//
	// Available state (read/write via a.*):
	//   a.tenantTokens[tenantID], a.tenantRequests[tenantID],
	//   a.classCounters[sloClass], a.windowStart, a.windowCount,
	//   a.totalAdmitted, a.totalRejected, a.lastClock
	//
	// Return (true, "") to admit, (false, "reason") to reject.
	return true, ""
	// EVOLVE-BLOCK-END
}

// NewAdmissionPolicy creates an admission policy by name.
// NOTE: "always-admit" is remapped to AdaptiveAdmission for the evolution benchmark.
// The baseline EVOLVE-BLOCK returns (true, ""), so behavior is identical to the
// original AlwaysAdmit until the search framework evolves the block.
func NewAdmissionPolicy(name string, capacity, refillRate float64) AdmissionPolicy {
	if !IsValidAdmissionPolicy(name) {
		panic(fmt.Sprintf("unknown admission policy %q", name))
	}
	switch name {
	case "", "always-admit":
		return NewAdaptiveAdmission()
	case "token-bucket":
		return NewTokenBucket(capacity, refillRate)
	case "reject-all":
		return &RejectAll{}
	default:
		panic(fmt.Sprintf("unhandled admission policy %q", name))
	}
}
