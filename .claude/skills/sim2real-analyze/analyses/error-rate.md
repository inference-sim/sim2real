---
name: error-rate
title: Per-workload error count and rate (baseline vs treatment)
when-to-use: check whether a latency win is actually an error-rate loss — algorithms that shed load look faster in the ok-status view
inputs: run
output: table
runner: script
script: error_rate.py
---

# Error-rate table

One section per workload, each with baseline + treatment rows showing:

- **Total** — all rows in `trace_data.csv`, no `status` filter
- **Errors** — rows where `status != "ok"`
- **Rate** — errors / total, as a percent
- **Top statuses (non-ok)** — the three most common non-ok status
  values with counts, so a status_code:503 wave is visible without
  opening the CSV

Unlike every other catalog entry, this script does **not** filter to
`status == "ok"` before counting — error rows are the metric.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/error_rate.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.

## Output

- Table printed to stdout, one section per workload
- `workspace/runs/<run>/error_rate.txt` for diffability, mirroring
  `latency-table`'s `deploy_comparison_table.txt`

## Why this matters after a latency-win

The comparison table filters `status == "ok"` before computing TTFT,
TPOT, and E2E — deliberately, because a per-token latency of a failed
half-request is meaningless. But that filter hides the trade-off:

- If treatment sheds 20% more load than baseline, treatment's remaining
  requests will be faster on average because the slow / retrying tail
  is what got shed. The latency table shows a win; the error-rate
  table shows the cost.
- If baseline and treatment have the same error rate, latency wins are
  genuine.

Always run this after `latency-table` on any run where the delta is
suspiciously large.
