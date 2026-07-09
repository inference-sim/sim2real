---
name: throughput-over-time
title: Requests-per-second timeline per workload (baseline vs treatment)
when-to-use: see load-vs-time shape — makes ramp-up, sustained-rate, and burst behavior visible so latency changes can be attributed to load, not the algorithm
inputs: run
output: png
runner: script
script: throughput_over_time.py
---

# Throughput over time

One panel per workload arranged in a grid capped at three columns
wide. Each panel plots sends-per-second (bucketed by integer second
of `send_time_us`) for both phases: baseline in blue, treatment in
red. Legend shows total ok-status count per phase.

Time origin is the first send in the (phase, workload) cell — so the
two curves in a panel share an x-axis that starts at each phase's own
t=0. This makes ramp-up shape directly comparable across phases even
when they were launched at different wall-clock times.

## Invocation

The skill invokes this analysis by running its `script` field:

```bash
python .claude/skills/sim2real-analyze/analyses/throughput_over_time.py --run <name>
```

`--run` defaults to `current_run` from `workspace/setup_config.json`.

## Output

- `workspace/runs/<run>/results_charts/throughput_over_time.png`
- Standalone PNG at 130 dpi.

## Reading the chart

- **Both curves overlap.** Load-gen produced identical send timelines —
  any latency delta between phases is genuinely the algorithm, not a
  load mismatch.
- **Curves diverge at rate ceilings.** One phase is queuing sends while
  the other processes freely — check the latency table for the ceiling
  phase's TTFT rise.
- **Bursty flats.** Regular flat-then-spike shape suggests a rate
  limiter (client or server) rather than natural request arrival.

## Why sends, not completions

`send_time_us` measures load *offered* to the server. Completion-time
buckets (using `last_chunk_time_us`) would measure processing rate,
which is the algorithm's *response* to that load — informative but
easily confused with cause vs effect. Sends is the cleaner baseline.
