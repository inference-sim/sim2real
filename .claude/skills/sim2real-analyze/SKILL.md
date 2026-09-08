---
name: sim2real-analyze
description: |
  Analyze sim2real pipeline run results. Offers a catalog of standard analyses
  (per-workload latency comparison, per-request scatter) plus a free-form loop
  for custom charts, distributions, HTML reports, and cross-run comparisons.
argument-hint: "[--run NAME]"
user-invocable: true
allowed-tools:
  - Bash(python *)
  - Bash(python3 *)
  - Bash(ls *)
  - Bash(mkdir *)
  - Bash(rm *)
  - Bash(open *)
  - Bash(cat *)
  - Read
  - Write
  - Glob
---

# sim2real-analyze

Analyze sim2real pipeline run results interactively. You are a data visualization expert.

## Step 1 — Resolve run

Check the skill invocation arguments (everything after `/sim2real-analyze` in the user's invocation).
If the arguments contain `--run NAME`, extract NAME and use it as the run. Example: `/sim2real-analyze --run adaptive6` → run = `adaptive6`.

If not provided, read `workspace/setup_config.json`:
```bash
cat workspace/setup_config.json
```

Extract `current_run`. If missing or empty, check if `workspace/runs/` exists and list runs:
```bash
ls workspace/runs/
```

- If `workspace/runs/` does not exist: stop with `Error: workspace/runs/ not found — no runs available`
- If empty: stop with `Error: no runs found in workspace/runs/`
- Otherwise: show a numbered list and prompt `Enter run name:`

If `current_run` names a directory that doesn't exist under `workspace/runs/`, warn:
`Warning: run '<name>' not found` and fall back to the directory listing prompt.

## Step 2 — Ask

Once run name is resolved, prompt the user:

```
Found run '<name>'. Show the comparison table? (or describe what you'd like to analyze)
```

If the user says yes, proceeds, or says nothing meaningful → go to Step 3.
If the user describes a specific analysis → skip to Step 4 with that request.

## Step 3 — Compute and print comparison table

Run the default catalog entry (`latency-table`):

```bash
python .claude/skills/sim2real-analyze/analyses/latency_table.py --run <name>
```

Print the full output to the user.

**This script handles only a two-phase, legacy-shaped run.** It assumes `results/baseline/` +
`results/treatment/` and a `trace_data.csv` directly under each `<workload>/`, and formats a
strictly two-column table (`_format_row(metric, baseline, treatment)` in `latency_table.py`). Both
assumptions are violated by shapes documented as current under "Data reference", so it exits 1 in
**two** distinct ways — issue #890 covers both:

| exit-1 message | cause | what to do |
|---|---|---|
| `need both results/baseline/ and results/treatment/ — run 'pipeline/deploy.py collect' first` | an arm-named run: there is no `treatment/` dir. **Also fires when the named run does not exist at all**, so check that first. | multi-arm — go to Step 4 |
| `no workloads found in both baseline and treatment logs` | a replica-shaped run: the trace sits at `<workload>/i<N>/trace_data.csv`, and the workload scan only looks for `<workload>/trace_data.csv`, so it finds none — this fires even when `baseline/` **and** `treatment/` are both present | replica shape — go to Step 4 |

In both cases the message is misleading: the first tells the user to re-run `collect` and the second
reads as missing data, but the data is there and complete. Do NOT tell the user to re-run `collect`.
Say the catalog does not yet support this run shape (#890), then go to Step 4 and build the
comparison as a one-off script (Step 4 item 3) — the worked example at the end of this file already
handles N arms and the optional `i<N>/` segment. Every other `runner: script` entry shares both
limitations, since they come from `_common.py`'s `phase_log_dirs` and `discover_workloads`.

If the script exits 1 with any other message, surface it and stop.

After printing the table (two-phase runs), proactively note any interesting patterns:
- If any p99 is worse while mean/p50 is better → suggest "Would you like to see the latency distribution to understand the tail?"
- If treatment is consistently better → note "Treatment shows consistent improvement."
- If treatment is consistently worse → note "Treatment shows consistent regression."

On a multi-arm run, once you have the table from a one-off script, the same reading applies per arm
against `baseline` — and if arms disagree by workload, say so rather than reporting a single winner.

Then go to Step 4.

## Step 4 — Interactive analysis loop

Enumerate the catalog of standard analyses:

```bash
python .claude/skills/sim2real-analyze/scripts/list_analyses.py
```

Parse the resulting JSON. Each entry has `name`, `title`, `when-to-use`,
`inputs`, `output`, `runner`, `path`, and (for `runner: script`) `script`.

Present the catalog as a numbered menu, excluding the `latency-table` entry
(already run in Step 3):

```
Analyses available for run '<name>':
  1. <title> — <when-to-use>
  2. <title> — <when-to-use>
  ...

Choose a number, an analysis name, describe what you'd like, or 'done' to exit.
```

For each user request:

1. **Numbered / named catalog entry.** Look up the matching entry and invoke it by `runner`:
   - `runner: script` — invoke:
     ```bash
     python .claude/skills/sim2real-analyze/analyses/<script> --run <name>
     ```
     Print the output. Ask the user for any additional parameters the script requires.
   - `runner: prompt` — read the entry's file at `path` and follow the prompt block in its body as instructions. The prompt itself specifies required parameters, defaults, and the output path convention.

2. **Free-text.** Match the user's phrasing against each entry's `when-to-use`.
   - Exactly one strong match → confirm briefly ("Sounds like `<name>` — run it?") and invoke.
   - Multiple plausible matches → list the candidates and ask.
   - No match → fall back to the one-off-script flow (step 3 below).

3. **One-off-script fallback.** For requests with no catalog match:

   a. Before the first fallback in this session, check pandas and matplotlib are importable:
      ```bash
      python -c "import pandas, matplotlib" 2>/dev/null
      ```
      If exit code ≠ 0, print once:
      ```
      Some analyses require pandas and matplotlib. Install with:
        pip install pandas matplotlib seaborn
      ```
      and fall back to stdlib-only analysis (tables and statistics, no charts).

   b. Ensure the charts directory exists:
      ```bash
      mkdir -p workspace/runs/<name>/results_charts/
      ```

   c. Write a self-contained Python script to a temp file:
      ```python
      import os; name = f"/tmp/sim2real_analyze_{os.urandom(4).hex()}.py"
      ```
      The script must:
      - Import pandas, matplotlib, seaborn as needed
      - Discover arms from `workspace/runs/<name>/results/*/` — one dir per arm; `baseline` is the
        reference and every sibling is a comparison arm. Do NOT assume a `treatment/` dir.
      - Within each arm, enumerate the `<workload>/` dirs, skipping the `plans/` sibling that sits
        beside them (it holds plan YAMLs and has no `trace_data.csv`), and resolve traces through the
        optional replica segment:
        `results/<arm>/<workload>/*/trace_data.csv` plus `results/<arm>/<workload>/trace_data.csv`
      - **A cell can hold several iterations** (`i1`, `i2`, …), and they are replicas of the same
        cell, so that glob can legitimately match more than one file. Read them **all** and
        concatenate — taking only the first silently charts one replica and reports it as the cell.
        If you deliberately analyse a single iteration, say which one in the output.
      - All timestamps are in **microseconds** — divide by 1000 for milliseconds
      - Filter to `status == "ok"` rows before computing any metrics
      - Save charts to `workspace/runs/<name>/results_charts/<descriptive-name>.png` or `.html`
      - Print any tabular results to stdout

   d. Execute the script:
      ```bash
      python /tmp/sim2real_analyze_<hex>.py
      ```

   e. Delete the temp file:
      ```bash
      rm /tmp/sim2real_analyze_<hex>.py
      ```

   f. Report results:
      - PNG outputs: `Saved: workspace/runs/<name>/results_charts/<name>.png`
      - HTML outputs: report the path and open it: `open workspace/runs/<name>/results_charts/<name>.html`
      - Stdout tables: print them directly

   Recurring one-off analyses are candidates for the standard catalog — note them for follow-up ("this pattern would be worth adding to `analyses/` as a `runner: prompt` entry").

**Session memory:** Remember what has been generated so far. "Show me that last chart again" → re-run or re-open the chart at its saved path.

**Proactive suggestions:** After each analysis, suggest a follow-up when patterns are notable:
- Distribution looks bimodal → "Want a CDF to see the full shape?"
- Tail latency is high → "Want a p95/p99/p999 breakdown?"
- One workload looks different → "Want to compare just that workload against another run?"

## Step 5 — Exit

Loop until the user says "done", "exit", "quit", "that's all", or similar.

## Data reference

A collected cell contains 13 entries. Four of them are directories that answer
configuration and runtime questions: `resources/`, `server_logs/`, `epp_logs/`,
`gpu_logs/`. Read those for what actually ran — do NOT infer it from bundle
source (`transfer.yaml`, `baselines/`), which states intent and whose
enable/disable conventions are easy to invert.

```
workspace/runs/<name>/
  results/
    <arm>/                              # ONE DIR PER ARM, named after the arm:
                                        #   baseline/ causalsloexternality/ leastttftjoint/ ...
                                        # NOT baseline/ + treatment/. A run has as many arms
                                        # as the assembly declared; `baseline` is the reference
                                        # and every sibling is a comparison arm.
      plans/                            # resolved llm-d-benchmark plan YAMLs, per phase.
                                        # A SIBLING OF THE WORKLOADS, NOT A WORKLOAD — skip it
                                        # when enumerating (it has no trace_data.csv).
      <workload>/                       # e.g. deep-research-single-turn
        i<N>/                           # replica shape (i1, i2, ...). Legacy-shape runs omit
                                        # this segment and put the files directly under
                                        # <workload>/. Resolve both: glob
                                        #   <arm>/<workload>/*/trace_data.csv
                                        # plus <arm>/<workload>/trace_data.csv
          trace_data.csv                # per-request; see "trace_data.csv columns" below
          trace_header.yaml             # model, time_unit (microseconds), workload_spec,
                                        # warm_up_requests, server config
          resources/                    # THE APPLIED K8S OBJECTS. Authoritative for what
                                        # actually ran: pod specs (resource requests/limits,
                                        # env, annotations, nodeName), Deployments,
                                        # InferencePool, Services. Answer configuration
                                        # questions HERE, not from transfer.yaml / baselines/
                                        # — bundle source states intent and its enable/disable
                                        # conventions are easy to misread.
                                        # CAVEAT: redaction runs only on the --skip-logs
                                        # collect path, so a default full collect may leave
                                        # sensitive fields in place (issue #886).
          server_logs/                  # vLLM stdout, one file per model-server pod. Actual
                                        # engine config and startup banner; UCX/NIXL transport
                                        # selection when UCX_LOG_LEVEL/UCX_PROTO_INFO are set.
          epp_logs/                     # EPP (router) pod logs, time-bucketed, from
                                        # stream-epp-logs. Per-request routing decisions —
                                        # placement, profile, and policy cost terms.
          gpu_logs/                      # from stream-gpu-stats:
                                        #   <node>.log       sampled nvidia-smi (temp, power,
                                        #                    power_cap, util, mem)
                                        #   <node>.gpus.csv  index, uuid, gpu_bus_id
                                        #   <pod>.uuids      pod -> GPU UUID
                                        # Use for thermal/power confounds and node health.
          metrics/                      # see "metrics/" below (present when stream-metrics ran)
          saturation.json               # NOT LOAD-BEARING — see "saturation.json" below
          epp_log_since                 # ONE ISO8601 timestamp, written by the workload task
                                        # (tektonc run-workload-blis-observe.yaml:45) BEFORE the
                                        # workload starts, to bound EPP log collection to this
                                        # workload's window. A fixed start-of-window mark, not an
                                        # advancing bookmark, and no code reads it today. Useful
                                        # only as "when this cell began" — note it precedes the
                                        # first send (pod startup), so prefer trace_data.csv's
                                        # min(send_time_us) for a workload-window bound.
          epp_stream_done               # sentinels written by collect-results when the
          gpu_stream_done               # workload finishes; each streamer polls its own
          metrics_stream_done           # sentinel and exits
          .collect_complete # written by `deploy.py collect` only once every file in
                            # the remote inventory landed at the matching size
                            # (issue #885). PRESENT ⇒ the copy is known complete
                            # FOR THE SCOPE IN ITS `excluded_subdirs` field: a
                            # --skip-logs collect records ["server_logs"], a full
                            # collect records []. ABSENT is not by itself evidence
                            # of a problem — runs collected before #885 never got a
                            # marker, and neither does an iteration that was never
                            # collected. It only means completeness is unproven: if
                            # a cell's logs look truncated (e.g. a file ending
                            # mid-record), re-run `deploy.py collect`, which will
                            # report any cell it cannot complete and write the
                            # marker for the rest.
  deploy_comparison_table.txt  # written by analyses/latency_table.py
  results_charts/              # your analysis outputs go here
```

### trace_data.csv columns

Timestamps are microseconds, but **the columns do not share an epoch**:

- `send_time_us`, `first_chunk_time_us`, `last_chunk_time_us` — Unix epoch µs.
- `arrival_time_us` — **relative to run start**, i.e. the scheduled arrival from the
  workload's arrival process. Differencing it against `send_time_us` directly yields a
  nonsense ~1.788e15 µs "lag"; align each series to its own origin first. The comparison
  is how you tell an open-loop generator (send tracks the schedule regardless of backlog)
  from a closed-loop one.

**The full column set** is `traceV2Columns` in BLIS (`sim/workload/tracev2.go`) — 27 always-present
columns, in this order:

```
request_id, client_id, tenant_id, slo_class, session_id, round_index, prefix_group,
prefix_length, streaming, input_tokens, output_tokens, text_tokens, image_tokens,
audio_tokens, video_tokens, reason_ratio, model, deadline_us, server_input_tokens,
arrival_time_us, send_time_us, first_chunk_time_us, last_chunk_time_us, num_chunks,
status, error_message, finish_reason
```

Three more are inserted conditionally by `ExportTraceV2`, so **check the header before relying on
them** rather than assuming a fixed layout:

| column | present when |
|---|---|
| `vllm_priority` | priority was actually computed — inserted right after `slo_class` |
| `slo_target_us` | any record has a non-zero SLO target — inserted right after `deadline_us` |
| `x_request_id` | `trace_header.yaml`'s `mode: real` (absent for simulated traces) |

Worth knowing rather than rediscovering: `server_input_tokens` is the prompt length the server
actually counted after prefix-cache reuse, so it can exceed `input_tokens` when a shared prefix is
prepended. `round_index` and `client_id` are the columns a multi-turn or per-client breakdown needs
— present even in single-turn workloads, where `round_index` is constant. `text/image/audio/video_tokens`
and `reason_ratio` carry the modality and reasoning split.

- Filter to `status == "ok"` before computing any metric.
- `warm_up_requests` from `trace_header.yaml` are already excluded from the rows, so the
  row count is `num_requests - warm_up_requests`.
- **`deadline_us` is present but unset** — all zeros in practice. No SLO-deadline analysis
  is possible from the trace; τ values live in the arm's EPP config, not here.

### metrics/

```
metrics/raw/<pod>_<ts>_metrics.log     one Prometheus text-exposition dump per pod per
                                       scrape (default 15 s). UNFILTERED — every metric the
                                       target exposed. Produced by stream-metrics, which
                                       wraps llm-d-benchmark's collect_metrics.sh.
metrics/raw/collection_debug.log       collector-side errors
metrics/processed/metrics_summary.json  post-run percentiles, but ONLY over the metrics in
                                        process_metrics.py's AGGREGATE_METRICS — which is
                                        RESOLVED PER RUN, not a fixed list: it is
                                        TIME_SERIES_METRIC_SET when the run configured
                                        monitoring.timeSeriesMetrics (env
                                        LLMDBENCH_TIME_SERIES_METRICS), else
                                        LEGACY_AGGREGATE_METRICS. So two runs can summarise
                                        different metrics — check the file's own keys rather
                                        than assuming. Anything outside the set must be read
                                        from raw/, which always has every metric exposed.
metrics/processed/replica_status.json            infrastructure state, per iteration
metrics/processed/replica_status_timeseries.json replica state/scale over the run
metrics/processed/pod_startup_times.json         pod startup timings
metrics/metrics_collection.log          stdout+stderr of start/stop/process
```

**Metric names worth knowing, and which role populates them.** The pod's role is in its
name (`...-prefill-...`, `...-decode-...`, `...-router-epp-...`). Reading the wrong role
returns `0.0` rather than an error, which looks like "no data" instead of "wrong pod":

| metric | populated on | use |
|---|---|---|
| `vllm:kv_cache_usage_perc` | prefill + decode | KV pressure (0-1) |
| `vllm:num_preemptions_total` | decode | KV exhaustion → evict/recompute; drives TPOT |
| `vllm:num_requests_waiting` | prefill + decode | engine queue depth |
| `vllm:num_requests_waiting_by_reason{reason="capacity"}` | prefill + decode | distinguishes *refusing work* from *cache merely full* — a full KV with zero capacity-waiters is prefix-cache retention, not back-pressure |
| `vllm:num_requests_running` | prefill + decode | admitted concurrency |
| `vllm:prompt_tokens_total` | prefill + decode | prefill throughput; the split across roles shows how much prefill ran locally on decode |
| `vllm:generation_tokens_total` | decode (prefill non-zero but negligible — it emits the first token) | decode throughput ceiling |
| `vllm:prefix_cache_{hits,queries}_total` | prefill + decode | cache hit rate |
| `vllm:nixl_bytes_transferred_{sum,count}` | **decode only** | KV handoff volume (decode is the puller) |
| `vllm:nixl_xfer_time_seconds_{sum,count}` | **decode only** | handoff wire time → effective bandwidth, and channel occupancy |
| `vllm:nixl_post_time_seconds_sum`, `vllm:nixl_num_descriptors_sum` | **decode only** | post queueing, scatter/gather fragmentation |
| `vllm:nixl_num_{failed_transfers,kv_expired_reqs}_total` | **decode only** | handoff failures |
| `vllm:cache_config_info{num_gpu_blocks,block_size}` | prefill + decode | KV capacity in tokens = `num_gpu_blocks × block_size` |

Counters are cumulative: difference two scrapes and divide by their timestamp delta. Check
monotonicity first — a pod restart resets them.

To enumerate what a run actually exposed:

```bash
grep -ohE "^vllm:[a-zA-Z0-9_:]+" <cell>/metrics/raw/*_metrics.log | sort -u
```

### saturation.json

A BLIS `observe --saturation-report` verdict: `{level, score, confidence, signals}`, where
`score = max(rate_deficit, latency_trend)` and level is STABLE (<0.5) / BACKLOGGED
(0.5-0.75) / OVERLOADED (≥0.75).

**Do not use it as a saturation verdict.** It is computed entirely from client-side arrival
and completion records — it has no view of KV, queue depth, or preemption — and its
`latency_trend` signal is computed over records sorted by *completion* time, which on
high-output-variance workloads manufactures a rising trend by construction and classifies
unsaturated cells as OVERLOADED (upstream bug: inference-sim/inference-sim#1693). Observed:
eight `reasoning` cells reported OVERLOADED at score 0.789-1.000 while decode KV p95 sat at
42-44% with zero preemptions and an engine queue never deeper than 1.

Derive saturation from the trace and metrics instead — queue growth
(`num_requests_waiting_by_reason{reason="capacity"}`, or sent-minus-first-token from the
trace), preemption counts, and offered rate against sustained token throughput.


## Standard analyses catalog

The skill ships a curated catalog under `analyses/`, discovered at runtime
via `scripts/list_analyses.py`. Each entry is a `.md` file with YAML
front-matter:

```yaml
---
name: <slug>
title: <human title>
when-to-use: <one-line trigger>
inputs: run
output: html | png | table
runner: script | prompt
script: <filename>   # required when runner == script
---
```

The skill enumerates the catalog on entry to the interactive loop and
offers each entry as a menu choice. Free-text is matched against each
entry's `when-to-use`; on a strong match the skill invokes the entry, on
no match it falls back to the one-off-script flow.

To add a standard analysis:

- Create a new `.md` file under `analyses/` with valid front-matter.
- For `runner: script`, add the co-located script alongside — the skill
  invokes `python .claude/skills/sim2real-analyze/analyses/<script> --run <name>`.
- For `runner: prompt`, put the prompt in the `.md` body — the skill
  follows the prompt block verbatim when the entry is selected.

All timestamps are **microseconds**. Metrics:
- TTFT = (first_chunk_time_us - send_time_us) / 1000 ms
- TPOT = (last_chunk_time_us - first_chunk_time_us) / (output_tokens - 1) / 1000 ms (output_tokens > 1 only)
- E2E  = (last_chunk_time_us - send_time_us) / 1000 ms

## Example one-off analysis script

Common analyses live in the catalog (see previous section). This
example shows the shape of a one-off script for the fallback flow when
you need something the catalog doesn't cover — for instance, a
histogram at a different bin size or a workload subset.

### TTFT distribution histogram (one-off example)

For the standard CDF view use `ttft-cdf` from the catalog. Reach for a
per-workload histogram when you need to see bin density directly (e.g.,
"is the TTFT distribution bimodal at 50ms increments?") rather than
the empirical step-function CDF the catalog entry produces.

```python
import pandas as pd
import matplotlib.pyplot as plt

import glob, os

run = "<name>"
base = f"workspace/runs/{run}/results"

# Arms come from the directory listing — never a hardcoded ["baseline", "treatment"].
arms = sorted(d for d in os.listdir(base) if os.path.isdir(f"{base}/{d}"))
arms = ["baseline"] + [a for a in arms if a != "baseline"]

def traces(arm, wl):
    """Every trace for a cell: one per i<N>/ iteration, or one in the legacy shape.

    Returns a list, not a single path — a cell can hold i1, i2, ... and they are
    replicas of the same cell. Returning only the first would silently chart one
    replica and label it the cell.
    """
    return sorted(glob.glob(f"{base}/{arm}/{wl}/*/trace_data.csv")) \
         + sorted(glob.glob(f"{base}/{arm}/{wl}/trace_data.csv"))

# `plans/` is a sibling of the workloads, not a workload — it has no trace_data.csv.
workloads = sorted(w for w in os.listdir(f"{base}/baseline") if traces("baseline", w))

fig, axes = plt.subplots(len(workloads), 1, figsize=(10, 4 * len(workloads)))
if len(workloads) == 1:
    axes = [axes]

for ax, wl in zip(axes, workloads):
    for arm in arms:
        fs = traces(arm, wl)
        if not fs:
            print(f"note: no trace for {arm}/{wl} — omitted from the chart")
            continue
        if len(fs) > 1:
            print(f"note: {arm}/{wl} has {len(fs)} iterations — pooling all of them")
        df = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
        df = df[df["status"] == "ok"]  # only compute metrics for successful requests
        ttft = (df["first_chunk_time_us"] - df["send_time_us"]) / 1000
        ax.hist(ttft, bins=50, alpha=0.6, label=arm)
    wl_display = wl.replace("workload_", "").replace("_", "-")
    ax.set_title(f"TTFT distribution — {wl_display}")
    ax.set_xlabel("TTFT (ms)")
    ax.legend()

plt.tight_layout()
out = f"workspace/runs/{run}/results_charts/ttft_distribution.png"
plt.savefig(out)
print(f"Saved: {out}")
```
