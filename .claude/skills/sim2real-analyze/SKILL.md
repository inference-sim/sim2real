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

Print the full output to the user. If the script exits with code 1, surface the error and stop.

After printing the table, proactively note any interesting patterns:
- If any p99 is worse while mean/p50 is better → suggest "Would you like to see the latency distribution to understand the tail?"
- If treatment is consistently better → note "Treatment shows consistent improvement."
- If treatment is consistently worse → note "Treatment shows consistent regression."

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
      - Load CSVs from `workspace/runs/<name>/results/baseline/{workload}/trace_data.csv` and `results/treatment/`
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

```
workspace/runs/<name>/
  results/
    baseline/
      workload_<name>/
        trace_data.csv    # columns: send_time_us, first_chunk_time_us, last_chunk_time_us,
                          #          output_tokens, arrival_time_us, input_tokens, status, ...
        trace_header.yaml # model, time_unit (microseconds), workload_spec, server config
    treatment/
      workload_<name>/
        trace_data.csv
        trace_header.yaml
  deploy_comparison_table.txt  # written by analyses/latency_table.py
  results_charts/              # your analysis outputs go here
```

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

## Example analysis scripts

### TTFT distribution histogram

```python
import pandas as pd
import matplotlib.pyplot as plt

run = "<name>"
workloads = ["workload_fm8_short_output_highrate"]  # fill in actual workloads

fig, axes = plt.subplots(len(workloads), 1, figsize=(10, 4 * len(workloads)))
if len(workloads) == 1:
    axes = [axes]

for ax, wl in zip(axes, workloads):
    for phase in ["baseline", "treatment"]:
        df = pd.read_csv(f"workspace/runs/{run}/results/{phase}/{wl}/trace_data.csv")
        df = df[df["status"] == "ok"]  # only compute metrics for successful requests
        ttft = (df["first_chunk_time_us"] - df["send_time_us"]) / 1000
        ax.hist(ttft, bins=50, alpha=0.6, label=phase)
    wl_display = wl.replace("workload_", "").replace("_", "-")
    ax.set_title(f"TTFT distribution — {wl_display}")
    ax.set_xlabel("TTFT (ms)")
    ax.legend()

plt.tight_layout()
out = f"workspace/runs/{run}/results_charts/ttft_distribution.png"
plt.savefig(out)
print(f"Saved: {out}")
```

### Throughput over time

```python
import pandas as pd
import matplotlib.pyplot as plt

run = "<name>"
wl = "workload_fm8_short_output_highrate"

fig, ax = plt.subplots(figsize=(12, 4))
for phase in ["baseline", "treatment"]:
    df = pd.read_csv(f"workspace/runs/{run}/results/{phase}/{wl}/trace_data.csv")
    df = df[df["status"] == "ok"]  # only compute metrics for successful requests
    t0 = df["arrival_time_us"].min()
    df["t_sec"] = (df["arrival_time_us"] - t0) / 1e6
    counts = df.groupby(df["t_sec"].astype(int)).size()
    ax.plot(counts.index, counts.values, label=phase)

ax.set_xlabel("Time (s)")
ax.set_ylabel("Requests/s")
ax.set_title(f"Throughput over time — {wl.replace('workload_','').replace('_','-')}")
ax.legend()
plt.tight_layout()
out = f"workspace/runs/{run}/results_charts/throughput_over_time.png"
plt.savefig(out)
print(f"Saved: {out}")
```
