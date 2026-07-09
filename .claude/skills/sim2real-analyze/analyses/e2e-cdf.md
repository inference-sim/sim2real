---
name: e2e-cdf
title: E2E empirical CDF per workload (baseline vs treatment)
when-to-use: see end-to-end latency distribution — the composite view that combines queue, prefill, and decode
inputs: run
output: png
runner: script
script: e2e_cdf.py
---

# E2E empirical CDF

One panel per workload, arranged in a grid capped at three columns
wide. Each panel plots the empirical CDF of end-to-end latency (ms)
for both phases: baseline in blue, treatment in red. Legend shows
sample count per phase.

E2E is `(last_chunk_time_us - send_time_us) / 1000`, computed over
rows with `status == "ok"`.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/e2e_cdf.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.

## Output

- `workspace/runs/<run>/results_charts/e2e_cdf.png`
- Standalone PNG at 130 dpi.

## E2E vs TTFT + TPOT

The comparison table (`latency-table`) reports E2E, TTFT, and TPOT
mean/p50/p99 side by side; E2E on its own is a composite that mixes
queue wait, prefill, and decode. When the E2E CDF diverges between
phases but neither TTFT nor TPOT does, the divergence is usually in
the output-length distribution — check the request mix rather than
blaming the algorithm.
