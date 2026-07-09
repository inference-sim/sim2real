---
name: tpot-cdf
title: TPOT empirical CDF per workload (baseline vs treatment)
when-to-use: see decode-time-per-token distribution — reveals steady-state throughput regressions the mean would average out
inputs: run
output: png
runner: script
script: tpot_cdf.py
---

# TPOT empirical CDF

One panel per workload, arranged in a grid capped at three columns
wide. Each panel plots the empirical CDF of TPOT (ms) for both phases:
baseline in blue, treatment in red. Legend shows sample count per phase.

TPOT is `(last_chunk_time_us - first_chunk_time_us) / (output_tokens - 1) / 1000`,
computed over rows with `status == "ok"` **and** `output_tokens > 1`.
A panel with no valid rows in either phase is hidden with a warning.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/tpot_cdf.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.

## Output

- `workspace/runs/<run>/results_charts/tpot_cdf.png`
- Standalone PNG at 130 dpi.

## Why the `output_tokens > 1` filter

TPOT is a per-decode-token quantity — undefined for one-token
completions where there was no second chunk to time against.
Workloads with all-single-token outputs (rare, but possible with
`max_tokens=1` benchmarks) will produce an empty panel.
