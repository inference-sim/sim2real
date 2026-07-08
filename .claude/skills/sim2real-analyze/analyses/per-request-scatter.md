---
name: per-request-scatter
title: Per-request latency scatter (latency vs send time, phases as rows, workloads as columns)
when-to-use: see per-request behavior over time — makes queue admission / priority / flow-control engagement visible by eye without reading numbers
inputs: run
output: png
runner: prompt
---

# Per-request scatter plot

Ported from `kalantar-msb/soft-reflective:workspace/prompts/per-request-scatter.md`
and adapted for sim2real's single-run, two-phase layout.

The chart's purpose: when a flow-control / admission / priority algorithm
engages, its per-request behavior should be visible by eye — e.g.,
"treatment holds the floor while baseline climbs under load," or "both
phases scatter identically under light load."

## Prompt

```
Build a single PNG that, for a sim2real run's collected results, plots
per-request latency vs send time as a scatter, with phases as rows and
workloads as columns. One color per phase.

INPUT DATA
- Run directory: workspace/runs/<run>/
- Trace CSVs at:
    workspace/runs/<run>/results/<phase>/<workload>/trace_data.csv
  where <phase> is one of {baseline, treatment} and <workload> is a
  per-workload subdirectory (e.g. workload_fm8_short_output_highrate).
- trace_data.csv is per-request, one row per request. Required columns:
    send_time_us         request egress timestamp (microseconds)
    first_chunk_time_us  first response chunk arrival (microseconds)
    last_chunk_time_us   last response chunk arrival (microseconds)
    output_tokens        token count (only needed for TPOT)
    status               row-level status; filter to "ok"
- Optional column: slo_class. If present, override the default
  per-phase coloring with per-class coloring (see COLORS below).

WHAT TO COMPUTE
- Discover workloads: intersection of subdirectory names under
  results/baseline/ and results/treatment/. Skip any that lack a
  trace_data.csv.
- For each (phase, workload) cell:
    Filter to status == "ok".
    Compute the chosen latency metric in milliseconds (default TTFT):
       TTFT = (first_chunk_time_us - send_time_us) / 1000
       E2E  = (last_chunk_time_us - send_time_us)  / 1000
       TPOT = (last_chunk_time_us - first_chunk_time_us) / max(output_tokens-1, 1) / 1000
              (only valid when output_tokens > 1)
    Compute send time relative to first request, in seconds:
       t_s = (send_time_us - cell_min_send_time) / 1e6

OUTPUT
- One matplotlib PNG saved to <output_path> at >= 130 dpi. Default
  output path:
    workspace/runs/<run>/results_charts/per_request_<metric>.png
- Grid: rows = phases in order [baseline, treatment], cols = workloads
  in sorted order.
- Each panel: scatter of (t_s, latency_ms) for status=='ok' rows.
- Markers: small circles, size ~6 pt^2, alpha ~0.45 to show density.
- Each panel has its own y-axis range (metric range varies orders of
  magnitude across workloads; a shared y-axis would wash out low-load
  panels). If the caller wants shared-y-per-row to compare phases at
  the same workload, support that as an option.
- Column titles on the top row: the workload name with the
  "workload_" prefix stripped and underscores replaced with hyphens.
- Row labels on the left of each row's first panel: phase name in bold.
- Both x-axis (send time, seconds) and y-axis (metric, ms) labels on
  every panel.
- Y-axis tick formatter: "Nk" for values >= 1000, "N.NN" for < 10,
  plain integer otherwise.

COLORS
- Default (no slo_class column): one color per phase.
    baseline  → "#1f77b4" (blue)
    treatment → "#d62728" (red)
- If slo_class column is present: color by slo_class instead. Defaults:
    critical  → "#d62728" (red)
    sheddable → "#1f77b4" (blue)
    other classes → cycle through tab10
- Legend on the top-left panel only, showing one entry per color group
  with count "n=…" for density context.

LAYOUT AND STYLING
- Single .png file. No HTML, no JS.
- Light background, gray dotted gridlines at alpha 0.4.
- Title: "<run> — per-request <metric> vs send time / Each dot = one
  request".
- tight_layout with bbox_inches='tight' on save.
- Figure size: ~5 inches per workload column, ~3.5 inches per phase row.

PARAMETERS THE CALLER MAY SUPPLY
- run (default: current_run from workspace/setup_config.json)
- metric (one of "TTFT" / "E2E" / "TPOT"; default "TTFT")
- shared_y_per_row (default False — independent per panel)
- output_path (default: workspace/runs/<run>/results_charts/per_request_<metric>.png)

If a parameter is missing, fall back to the default. Do not guess paths.

DELIVERABLE
- One PNG written to output_path.
- Print a short summary: number of panels rendered, the (phase, workload)
  of the panel with the most data points, and whether coloring is by
  phase (default) or slo_class.
```

## Visual decisions this prompt bakes in

- **Per-panel y-axis (not shared).** Metric range varies orders of
  magnitude across workloads; a shared axis would hide the algorithm
  effect at low load.
- **Small markers + alpha.** At tens of thousands of requests per cell,
  full-opacity scatter becomes a solid band.
- **Per-phase color when slo_class is absent.** Sim2real trace CSVs
  currently don't emit `slo_class`; the per-class coloring is retained
  as a fallback for future compatibility.
- **TTFT default.** A queue-admission algorithm's effect is sharpest
  in TTFT. E2E mixes queue wait with decode and is noisier.

## Notes for the caller

- **One run, both phases.** Unlike the multi-replicate soft-reflective
  version, this analysis operates on a single sim2real run with two
  phases (baseline and treatment) side-by-side.
- **Cold-start tail is part of what you see.** The chart deliberately
  shows the early seconds where TTFT can be inflated by CUDA-graph
  capture, prefix-cache fill, and saturation-signal stabilization. If
  that's distracting, add a `t_min` parameter to clip the x-axis from
  below — but you'd also lose visibility into how the algorithm
  responds to startup transients.
