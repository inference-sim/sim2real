---
name: latency-table
title: Per-workload latency comparison table
when-to-use: default first-look at a run — TTFT/TPOT/E2E mean/p50/p99 for baseline vs treatment, side-by-side per workload
inputs: run
output: table
runner: script
script: latency_table.py
---

# Per-workload latency comparison table

The default analysis for a sim2real run. Loads `trace_data.csv` from both
`results/baseline/<workload>/` and `results/treatment/<workload>/`, filters
to `status == "ok"`, and prints one section per workload with:

- **TTFT** mean / p50 / p99 (ms)
- **TPOT** mean / p50 / p99 (ms) — only when the workload has requests
  with `output_tokens > 1`
- **E2E** mean / p50 / p99 (ms)

Each row shows baseline, treatment, absolute delta, and a percentage
change with a `(better)` / `(worse)` / `(no change)` verdict.

The full table is also written to `<run>/deploy_comparison_table.txt` so
future runs of this analysis are diff-able.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/latency_table.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.
