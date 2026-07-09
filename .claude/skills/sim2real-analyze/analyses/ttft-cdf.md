---
name: ttft-cdf
title: TTFT empirical CDF per workload (baseline vs treatment)
when-to-use: see full-distribution shape of time-to-first-token — reveals bimodality and tail behavior that mean/p50/p99 collapses away
inputs: run
output: png
runner: script
script: ttft_cdf.py
---

# TTFT empirical CDF

One panel per workload, arranged in a grid capped at three columns
wide. Each panel plots the empirical CDF of TTFT (ms) for both phases:
baseline in blue, treatment in red. Legend shows sample count per phase.

TTFT is `(first_chunk_time_us - send_time_us) / 1000`, computed over
rows with `status == "ok"`. A panel with no ok-status rows in either
phase is hidden.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/ttft_cdf.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.

## Output

- `workspace/runs/<run>/results_charts/ttft_cdf.png`
- Standalone PNG at 130 dpi; laid out with `tight_layout` and cropped
  with `bbox_inches="tight"` so no whitespace slack in reports.

## When mean/p50/p99 hides what the CDF shows

- **Bimodality.** Two request populations with different queue behavior
  produce two visible steps in the CDF; the summary table's percentiles
  interpolate through them.
- **Long-tail spread.** A p99 number treats the tail as a scalar. The
  CDF shows how quickly the tail rises from p95 to p99 to p999.
- **Cold-start transient.** If early requests are 10× slower than
  steady-state, the CDF has a shelf that the mean smears out.
