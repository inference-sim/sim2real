# Sim2Real Transfer: BLIS Admission Control (admission_by60)

AI-discovered admission control policy for llm-d, found by SkyDiscover (OpenEvolve, 30 iterations).
The evolved policy achieves **+38.8% improvement** over the always-admit baseline under 2× overload,
raising SLO attainment from ~25% to ~84% while maintaining 100% throughput utilization.

**Key improvement over previous run (admission_old):** Prompt was redesigned to remove hardcoded
threshold hints and add a generalization requirement. The discovered policy uses normalized,
adaptive thresholds that self-calibrate to actual cluster load — making it more suitable for
real llm-d deployment than the previous version.

## What's in this directory

```
sim2real/admission_by60/
├── README.md                 # This file
├── llm_config.yaml           # LLM + hardware + cluster config
├── repro.py                  # Reproduction script
├── workloads/                # Traffic profiles (2× overload, 320 req/s)
│   ├── workload_overload_mixed_slo.yaml   # Sustained 2× overload, 4 SLO classes, 4 tenants
│   └── workload_bursty_adversary.yaml     # 2× overload with gamma bursts, 5 tenants
├── baselines/                # Baseline admission policy (Go)
│   └── baseline_always_admit.go           # Admits all requests — control
├── best/                     # The AI-discovered admission policy
│   ├── best_program.go       # Drop-in replacement for sim/admission.go
│   └── best_program_info.json             # Metrics, iteration, lineage
├── routing_config/           # llm-d routing policy config
│   └── routing_policy.yaml   # Fixed routing (1:1 weighted), only admission evolves
└── others/                   # Supporting files
    ├── calibration.json      # SLO targets + throughput cap + saturation rates
    ├── hardware_config.json  # GPU specs (H100)
    ├── baseline_metrics.json # Baseline scores per workload (seeds 42, 456)
    └── evaluator.py          # Multi-objective scoring function (reference)
```

## Results

| Policy | Score | SLO Attainment | Throughput | Fairness | Avg E2E |
|--------|-------|----------------|------------|----------|---------|
| Always-admit (baseline) | 0.6266 | 24.9% | 100% | 100% | 9,083 ms |
| **Evolved (best)** | **0.8700** | **84.4%** | **100%** | **74.1%** | **1,197 ms** |
| **Improvement** | **+38.8%** | **+3.4×** | — | — | **6.8× faster** |

Per-workload:

| Workload | Baseline | Evolved | Improvement |
|----------|----------|---------|-------------|
| overload_mixed_slo | 0.6508 | 0.8691 | +33.5% |
| bursty_adversary | 0.5977 | 0.8708 | +45.7% |

## Discovered Algorithm

The best policy uses **adaptive, normalized load shedding with tenant fairness**:

```go
// Compute per-instance load normalized to self-calibrated capacity estimate
perInstanceLoad = totalInFlight / numInstances
loadRatio = perInstanceLoad / typicalLoad   // typicalLoad adapts from 10s window

// Priority tiers — always admit
if sloClass == "critical" || sloClass == "standard" → ADMIT

// Shed batch when load > 50% of typical capacity
// Shed sheddable when load > 75% of typical capacity
// Tighten threshold for tenants consuming >1.5× their fair share
```

**Why this generalizes:** No magic constants. `typicalLoad` bootstraps at 40 req/s/instance
and self-calibrates from a 10-second sliding window of observed traffic. The policy adapts
to actual cluster behavior rather than being tuned to a specific simulation.

## Experiment Config

| Setting | Value |
|---------|-------|
| Experiment | `260324_30i_nohint_2seed` |
| Framework | OpenEvolve |
| Iterations | 30 |
| Seeds | 42, 456 |
| Models | Claude Sonnet 4.5 (70%) + Claude Opus 4.5 (30%) |
| Workload | 320 req/s (2.0× saturation of 160 req/s) |
| Scoring | `0.50×SLO + 0.30×throughput + 0.20×fairness` |
| Throughput cap | 0.50 (50% shedding is free) |
| Best found at | Iteration 7 |
| Wall time | ~18 minutes |
| Prompt | No threshold hints; generalization requirement added |

## LLM and cluster config

See `llm_config.yaml` for full details. Model: `qwen/qwen2.5-7b-instruct` on H100 (TP=1).

## How to reproduce

### Prerequisites

1. Clone inference-sim into this directory:
   ```bash
   git clone <inference-sim-repo> inference-sim
   git -C inference-sim checkout <commit>
   ```
2. Install Go (`go build` must work)
3. Install PyYAML: `pip install pyyaml`

### Run reproduction

```bash
cd sim2real/admission_by60

# Default: single seed 42
python repro.py

# Match original training setup (two seeds)
python repro.py --seeds 42,456

# Custom cluster size
python repro.py --seeds 42,456 --num-instances 4
```

### Expected output

```
Program                   Combined     SLO attn   Throughput   Fairness    Avg E2E     vs base
-------------------------------------------------------------------------------------------------
Always-admit (base) *       0.6266      24.9%       100.0%      100.0%    9083 ms   (control)
Evolved (best)              0.8700      84.4%       100.0%       74.1%    1197 ms     +38.8%
```

## Deployment notes for real llm-d

The evolved policy uses only three signals:

| Signal | Source in llm-d | Freshness |
|--------|-----------------|-----------|
| `totalInFlight / numInstances` | Router-local counter | Every request |
| `sloClass` | Request header (`X-SLO-Class`) | Every request |
| `tenantID` | Auth middleware | Every request |

All signals are available in llm-d with minimal integration work. No Prometheus,
no KV metrics, no staleness concerns. The adaptive `typicalLoad` estimate
self-calibrates within the first ~100 requests after startup.

## Comparison with admission_old

| | admission_old | **admission_by60** |
|---|---|---|
| Experiment | 260312_50i_admission_openevolve | 260324_30i_nohint_2seed |
| Iterations | 50 | 30 |
| Seeds | 1 (seed 42) | 2 (seeds 42, 456) |
| Prompt | Hardcoded hints (signal + thresholds) | High-level + generalization requirement |
| Score | 0.8519 (+36.5%) | **0.8700 (+38.8%)** |
| Fairness | 0.579 | **0.741** |
| Adaptivity | Fixed thresholds | Self-calibrating via sliding window |
| Deployability | Sim-tuned constants | Normalized / generalizes to new clusters |
