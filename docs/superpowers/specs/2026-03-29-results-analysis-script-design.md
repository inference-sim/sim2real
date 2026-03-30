# Results Analysis Script Design

## Goal

Add `scripts/analyze.py` — a standalone deterministic script that reads sim2real run artifacts and produces latency comparison charts (per-workload bar charts + summary heatmap) alongside a terminal summary, replacing the ad-hoc results display embedded in `deploy.py`.

## Background

Currently, deploy.py prints a comparison table inline during the benchmark step. The `/sim2real-results` skill does deeper analysis via an LLM agent (reads JSONs, calls CLI subcommands, writes markdown reports). Neither produces visual charts. A deterministic script fills the gap: reproducible, version-controlled, runnable at any time post-deploy.

## Architecture

Single file: `scripts/analyze.py`. No new lib modules — the chart logic is cohesive and purpose-built. Reads JSON artifacts directly from the run directory. Calls the existing `transfer_cli.py compare` subcommand for the text table. Uses matplotlib and numpy for charts (both added to requirements.txt).

## CLI Interface

```
python scripts/analyze.py [--run <name>]
```

**Run resolution (in order):**
1. `--run <name>` argument if provided
2. `workspace/setup_config.json` → `run_name`
3. Exit 1 with clear message if neither resolves

After resolving `run_name`, the script validates that `workspace/runs/<run_name>/` exists on disk; exits 1 with a clear message if the directory is not found.

**Exit codes:**
- `0` — charts and summary produced
- `1` — required artifact missing or malformed, message printed to stderr

## Inputs

From `workspace/runs/<run_name>/`:

| File | Required | Used for |
|------|----------|----------|
| `deploy_baseline_results.json` | yes | chart data, text table |
| `deploy_treatment_results.json` | yes | chart data, text table |
| `deploy_validation_results.json` | no | verdict, noise_cv, suite results, benchmark classification |

If `deploy_validation_results.json` is absent (e.g. a partial run), the script proceeds with charts only — verdict, noise_cv, t_eff, and workload classifications are omitted from the terminal summary.

When an optional file is absent, an info message is logged before proceeding (e.g. `[INFO] deploy_validation_results.json not found — skipping verdict/mechanism check display`).

## Outputs

All outputs saved to `workspace/runs/<run_name>/results_charts/` (created if absent):

| File | Description |
|------|-------------|
| `workload_<name>.png` | Per-workload grouped bar chart |
| `summary_heatmap.png` | All workloads × all metrics, colored by % change |

Terminal output: compact summary block (verdict, noise_cv, t_eff, per-workload classification if available).

## Chart Specifications

### Per-workload bar charts (`workload_<name>.png`)

One PNG per workload. Layout: subplots grid (3 columns × N rows) where each subplot is one metric. Metrics: TTFT mean/p50/p99, TPOT mean/p50/p99, E2E mean/p50/p99.

The grid always renders all 9 metric slots. If a metric is absent from **both** baseline and treatment for that workload, its axis is hidden (blank space in the grid). If absent from only one side, it is shown with `N/A` for the missing bar.

Each subplot:
- Two bars: baseline (gray) and treatment (blue)
- Y axis: latency in ms
- Bar labels: value in ms
- Subplot title: metric name
- Delta % annotated above treatment bar in format `−X.X%` (improvement, green) or `+X.X%` (regression, red); omitted if either side is N/A

Chart title: `Workload: <name>  |  <verdict>` (verdict shown only if available).

### Summary heatmap (`summary_heatmap.png`)

Rows = workloads, columns = metrics. Cell value = `(treatment - baseline) / baseline * 100` (% change). Color scale: diverging (green = improvement/negative, white = 0, red = regression/positive). Cell text: `−X.X%` or `+X.X%`. Cells where either side is absent shown as gray with `N/A`.

Chart title: `sim2real Transfer: <run_name>  |  Verdict: <verdict>` (verdict omitted if unavailable).

## Terminal Summary Format

```
━━━ sim2real Results: <run_name> ━━━

Verdict:   PASS            Noise CV: 0.043    T_eff: 8.2%

Workload classifications:
  chat-short    matched    (improvement: 12.1%)
  code-gen      matched    (improvement: 9.4%)
  batch-long    unmatched  (improvement: 1.1%)

Charts saved to workspace/runs/<run_name>/results_charts/
```

The `improvement` value comes from `workload_classification[].improvement` in `deploy_validation_results.json`. It is a fraction (e.g. `0.1210` = 12.1%), computed by the CLI as `(baseline_p99 - treatment_p99) / baseline_p99`. The script multiplies by 100 and formats as `X.X%`. If `deploy_validation_results.json` is absent, the verdict/noise_cv/t_eff lines and the workload classifications block are omitted.

## Dependencies

Add `matplotlib` and `numpy` to `requirements.txt`. numpy is used directly for heatmap array construction and normalization (`numpy.array`, `numpy.nan`).

## Testing

Tests in `tests/test_analyze.py`. Strategy: fixture JSON files (minimal valid structure), assert chart files are created, assert terminal output contains expected strings. No visual pixel-comparison — only file existence and stdout content.

Key test cases:
- Happy path: baseline + treatment + validation → all outputs produced, terminal shows verdict
- Fast-iteration (no validation_results): charts produced, verdict lines absent from summary, info message printed
- Missing required file (baseline or treatment): exit 1, message to stderr, no partial outputs
- Single workload, multiple workloads
- Metric absent from both sides: axis hidden in bar chart, cell shown as N/A in heatmap
- Metric absent from one side: bar chart shows N/A bar, delta annotation omitted; heatmap shows N/A cell
- Run directory not found: exit 1 with message
- Run resolved from setup_config.json (no --run argument): correct run_name used

## File Layout

```
scripts/
  analyze.py          ← new
tests/
  test_analyze.py     ← new
  fixtures/
    analyze/
      baseline_results.json    ← minimal fixture
      treatment_results.json   ← minimal fixture
      validation_results.json  ← minimal fixture (full mode)
requirements.txt      ← add matplotlib, numpy
```
