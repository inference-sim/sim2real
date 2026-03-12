# BLIS-to-llm-d Algorithm Transfer Pipeline Design

**Date:** 2026-02-26
**Status:** Draft
**Author:** Claude Code + toslali

## Problem Statement

We have a working discovery loop (OpenEvolve + BLIS) that evolves adaptive routing algorithms — conditional logic that adjusts how existing scorer outputs (prefix-affinity, load-balance, queue-depth) are combined based on request characteristics (input size, SLO class, queue depth). These algorithms are validated in simulation but exist only as Go code within BLIS's simplified abstractions.

We need a **transfer pipeline** that takes a discovered algorithm and:

1. Translates it into a production-quality `Scorer` plugin for [`llm-d-inference-scheduler`](https://github.com/llm-d/llm-d-inference-scheduler) (GIE-based Filter→Score→Pick pipeline with Kubernetes pod endpoints, ZMQ-based KV cache indexing, and P/D disaggregation)
2. Generates corresponding benchmark configs for [`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) to validate the new scorer
3. Runs CI (build + test) to validate correctness
4. Produces ready-to-review PRs against both repos
5. Includes a **validation runbook** with instructions to demonstrate real-world benefit

### Constraints

- **Trigger:** Manual, post-experiment (user decides which algorithm to transfer)
- **Automation:** Fully automated from trigger to PR, with CI as the quality gate
- **Human involvement:** Interactive Claude Code session — user sees each step and can intervene
- **Translation engine:** LLM-powered (Claude reads both codebases and bridges the abstraction gap)

---

## Architecture Overview

### Pipeline Stages

```
[1. Extract]  →  [2. Translate]  →  [3. Generate]  →  [4. Validate]  →  [5. Runbook]  →  [6. PR]
                                                            ↑
                                                     (retry on build/test failure, up to 3x)
```

| Stage | Input | Output | Tools |
|---|---|---|---|
| **Extract** | `best_program.py` + `best_program_info.json` | Algorithm summary: EVOLVE-BLOCK code, signal list, metric deltas | Read, Grep |
| **Translate** | Algorithm summary + mapping doc | Mapped algorithm spec: which llm-d scorers to wrap, what conditions to apply, which signals to use | Read (mapping doc + llm-d source) |
| **Generate** | Mapped spec + scorer template + llm-d repo | New `.go` files: scorer, registration, tests, configs | Write, Edit |
| **Validate** | Generated files in llm-d repo | Build + test results | Bash (`go build`, `go test`, `go vet`) |
| **Runbook** | Algorithm summary + benchmark repo | Runbook markdown in PR description | Read (benchmark repo), Write |
| **PR** | All artifacts | Two PRs: scheduler + benchmark | Bash (`gh pr create`) |

### Invocation

Interactive Claude Code session. The user triggers the transfer conversationally:

```
> Transfer the best discovered algorithm from examples/blis_router/openevolve_output/
  to llm-d-inference-scheduler at ~/repos/llm-d-inference-scheduler
  and llm-d-benchmark at ~/repos/llm-d-benchmark
```

Or via a future Claude Code skill:

```
> /transfer-to-llmd --best-program examples/blis_router/openevolve_output/best_program.py
```

---

## The Abstraction Gap

### Why Direct Copy Doesn't Work

BLIS and llm-d share the same *conceptual* architecture (weighted scoring across instances), but differ in 7 concrete dimensions:

| Dimension | BLIS Simulator | Production llm-d |
|---|---|---|
| **Routing unit** | `RoutingSnapshot` struct (int/float64 fields) | `Endpoint` (K8s Pod with labels, scraped metrics) |
| **Request type** | `*Request` with `InputTokens []int` | `*LLMRequest` (HTTP body + headers, chat/completion) |
| **State access** | `RouterState.Snapshots` — synchronous snapshot | `*CycleState` — per-cycle shared state across plugins |
| **KV cache signal** | `CacheHitRate float64` (coarse aggregate) | `kvcache.Indexer.GetPodScores()` (block-level prefix match) |
| **Prefix signal** | In-memory `prefixMap` hash→instance | FNV-64a block hashing matching vLLM internals over ZMQ |
| **Weight mechanism** | Normalized `[]float64` summing to 1.0 | Per-scorer integer `weight` in EPP config YAML |
| **P/D disaggregation** | Not present | Two-tier: decode profile + prefill decision |
| **Session affinity** | `req.SessionID` field | `x-session-token` HTTP header (**VERIFIED** — see `blis_to_llmd_mapping.md`) |
| **SLO classes** | `req.SLOClass` ("realtime", "interactive", "batch") | Not a native concept — needs header/label mapping |

### Translation Pattern: Composite Scorer

The discovered adaptive logic (conditional adjustments to scorer outputs) maps to a **new composite scorer plugin** in llm-d:

```go
// AdaptiveScorer wraps existing scorers and applies
// BLIS-discovered conditional logic on top.
type AdaptiveScorer struct {
    prefixScorer  *PrecisePrefixCacheScorer
    loadScorer    *LoadAwareScorer
    activeScorer  *ActiveRequestScorer
    // ... other wrapped scorers as needed
}

func (s *AdaptiveScorer) Score(ctx context.Context, cycleState *CycleState,
    req *LLMRequest, endpoints []Endpoint) map[Endpoint]float64 {

    // 1. Get base scores from wrapped scorers
    prefixScores := s.prefixScorer.Score(ctx, cycleState, req, endpoints)
    loadScores   := s.loadScorer.Score(ctx, cycleState, req, endpoints)

    // 2. Apply BLIS-discovered adaptive logic
    //    (THIS IS WHAT GETS TRANSLATED FROM THE EVOLVE-BLOCK)
    scores := make(map[Endpoint]float64, len(endpoints))
    for _, ep := range endpoints {
        // Example: large-prefix requests weight prefix higher
        inputLen := estimateTokenCount(req)
        if inputLen > 1000 {
            scores[ep] = 0.7*prefixScores[ep] + 0.3*loadScores[ep]
        } else {
            scores[ep] = 0.15*prefixScores[ep] + 0.85*loadScores[ep]
        }
        // Example: realtime SLO penalizes high queue depth
        // (mapped from req.SLOClass to request header)
    }
    return scores
}
```

This is:
- **Additive** — doesn't modify existing scorers
- **Reviewable** — the adaptive logic is isolated and clearly attributable to BLIS discovery
- **Testable** — can unit test the composite independently

---

## Signal Mapping Reference

This mapping is maintained as `docs/transfer/blis_to_llmd_mapping.md` and updated as llm-d evolves.

### Signals

| BLIS (`RoutingSnapshot`) | llm-d Equivalent | Access Pattern | Fidelity |
|---|---|---|---|
| `QueueDepth` | `waitingRequests` (scraped from vLLM `/metrics`) | `LoadAwareScorer` reads from endpoint metrics | High — direct equivalent |
| `InFlightRequests` | `endpoint.GetMetrics().RunningRequestCount` | `ActiveRequestScorer` tracks via `PreRequest`/`ResponseComplete` (**SUPERSEDED** — see `blis_to_llmd_mapping.md` for canonical mapping) | High |
| `EffectiveLoad()` | `waitingRequests + activeRequests` | Combine two scorer outputs | High |
| `KVUtilization` | `endpoint.GetMetrics().KVCacheUsagePercent` | Custom scorer needed (**SUPERSEDED** — see `blis_to_llmd_mapping.md` for canonical mapping) | High — direct field available |
| `CacheHitRate` | `kvcache.Indexer.GetPodScores()` | Per-request prefix match score (much richer than aggregate hit rate) | Upgrade — llm-d signal is strictly better |
| `FreeKVBlocks` | Not exposed to scheduler | Could scrape from vLLM metrics | Low — would need new metric plumbing |
| `BatchSize` | `endpoint.GetMetrics().RunningQueueSize` (approximate) | `ActiveRequest` scorer (**SUPERSEDED** — see `blis_to_llmd_mapping.md` for canonical mapping) | Medium |
| `len(req.InputTokens)` | `estimateTokenCount(req.Body)` | Parse request JSON, count tokens or estimate from char count | Medium — needs helper function |
| `req.SLOClass` | Custom request header (e.g., `x-slo-class`) | Read from `req.Headers` | Needs convention — not native |
| `req.SessionID` | `x-session-token` header (**VERIFIED** — see `blis_to_llmd_mapping.md`) | `SessionAffinityScorer` already handles this | High |

### Interfaces

| BLIS | llm-d | Translation |
|---|---|---|
| `scorerFunc(req, snapshots) → map[string]float64` | `Scorer.Score(ctx, cycleState, req, endpoints) → map[Endpoint]float64` | Same shape, different types |
| `WeightedScoring.Route()` EVOLVE-BLOCK | `AdaptiveScorer.Score()` body | Core translation target |
| `routing_policy.yaml` weights | `EndpointPickerConfig` per-plugin `weight` | Ratio transfer: BLIS `0.6:0.4` → llm-d `weight: 6` / `weight: 4` |
| `observerFunc` (post-route callback) | `PreRequest()` / `ResponseComplete()` lifecycle hooks | Same concept, different API |

---

## Workload Mapping Reference

| BLIS Workload | llm-d Benchmark Profile | What It Tests |
|---|---|---|
| `cache_warmup` (1000/s, 3 prefix groups, 5s) | `shared_prefix_synthetic.yaml.in` | Cache locality under prefix-heavy load |
| `load_spikes` (1000/s, bursty CV=3, 5s) | `random_concurrent.yaml.in` | Load balancing under bursty traffic |
| `multiturn` (150/s, sessions, 10s) | `shared_prefix_multi_turn_chat.yaml.in` | Session affinity + prefix reuse |

---

## Validation Runbook Template

The PR description includes a runbook section:

### Deploy
1. Build the modified scheduler: `make build` in `llm-d-inference-scheduler`
2. Update EPP config to enable the new `adaptive-scorer` plugin with appropriate weight
3. Deploy to test cluster (or use `llm-d-inference-sim` for local validation)

### Benchmark
1. Run baseline with current scorer config:
   ```bash
   # Using llm-d-benchmark with production scorer weights
   ./run_benchmark.sh --profile shared_prefix_multi_turn_chat --config baseline-epp.yaml
   ```
2. Run treatment with new adaptive scorer:
   ```bash
   ./run_benchmark.sh --profile shared_prefix_multi_turn_chat --config adaptive-epp.yaml
   ```
3. Compare metrics: TTFT (mean + P95), E2E latency (mean + P95), throughput

### Expected Improvements (from BLIS simulation)
- Cache-heavy workloads: ~X% reduction in mean E2E latency
- Bursty workloads: ~Y% reduction in P95 latency
- Multi-turn sessions: ~Z% improvement in TTFT

**Caveat:** BLIS improvements are simulated. Production improvements may differ due to: real network latency, vLLM scheduling internals, KV cache eviction policies, and cluster heterogeneity. The improvements above are directional estimates.

### Go/No-Go Criteria
- [ ] Mean E2E latency improvement > 5% on at least one workload
- [ ] No regression > 2% on any workload
- [ ] P95 latency does not increase on any workload

---

## Supporting Artifacts

These files live in `docs/transfer/` in this repo:

| File | Purpose | Maintained By |
|---|---|---|
| `blis_to_llmd_mapping.md` | Signal and interface mapping reference | Manual — update when llm-d APIs change |
| `scorer_template.go.md` | Example of a well-structured llm-d scorer for Claude to follow | Manual — based on existing llm-d scorers |
| `transfer_prompt.md` | Structured instructions for Claude Code during transfer | Manual — update as pipeline evolves |

### `transfer_prompt.md` Structure

This is the prompt template that guides Claude Code through the transfer:

```markdown
## Context
You are transferring a routing algorithm discovered by OpenEvolve+BLIS to production llm-d.

## Discovered Algorithm
{EVOLVE_BLOCK_CODE}

## Hypothesis Results
{HYPOTHESIS_LEDGER_SUMMARY}

## Performance vs Baseline
{METRIC_DELTAS}

## Mapping Reference
{BLIS_TO_LLMD_MAPPING}

## Target Repos
- Scheduler: {SCHEDULER_REPO_PATH}
- Benchmark: {BENCHMARK_REPO_PATH}

## Instructions
1. Read the mapping reference and understand the signal correspondences
2. Read existing scorers in the scheduler repo for style/pattern reference
3. Generate an AdaptiveScorer that implements the discovered logic using llm-d abstractions
4. Register the scorer in register.go
5. Write unit tests following existing test patterns
6. Generate EPP config YAML enabling the new scorer
7. Generate benchmark config for llm-d-benchmark
8. Build and test: go build ./..., go test ./..., go vet ./...
9. Fix any failures (up to 3 retries)
10. Generate validation runbook
11. Create PRs against both repos
```

---

## Future Extensions

These are explicitly **out of scope** for the initial implementation but worth noting:

1. **Automated `llm-d-inference-sim` run**: Add a stage that runs the lightweight vLLM mock to get preliminary performance numbers before PR creation
2. **Bidirectional transfer**: Insights from production llm-d benchmarks fed back to BLIS to improve simulation fidelity
3. **Claude Code skill**: Package the transfer as a first-class `/transfer-to-llmd` skill with argument parsing
4. **Multi-algorithm transfer**: Batch transfer of top-N algorithms from a single experiment, with comparative analysis
5. **Continuous integration**: Auto-trigger on new best program discovery above a threshold

---

## Summary

The transfer pipeline is an **interactive Claude Code session** guided by:
- A **mapping document** (`blis_to_llmd_mapping.md`) that captures the abstraction correspondences
- A **scorer template** showing the target code structure
- A **prompt template** that orchestrates the 6-stage pipeline

The core translation pattern is a **composite `AdaptiveScorer`** that wraps existing llm-d scorers and applies the BLIS-discovered conditional logic. This is additive, reviewable, and testable.

The pipeline produces **two PRs** (scheduler + benchmark) each containing code, config, tests, and a validation runbook with deployment instructions, workload mapping, expected improvements, and go/no-go criteria.
