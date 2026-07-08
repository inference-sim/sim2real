---
description: Validate a sim2real translation bundle against its simulation bundle. Checks workloads, configs, signals, policies, and runtime health.
---

## User Input

```text
$ARGUMENTS
```

Parse user input for these flags (all optional; auto-detect fills in the rest):

- `--sim <path>` — path to the simulation bundle (BLIS output).
- `--real <path>` — path to a pre-refactor legacy real bundle on disk. Mutually exclusive with `--run`.
- `--run <name>` — a workspace-registered run name. Requires the run to have been assembled by `sim2real assemble --run <name>`. Mutually exclusive with `--real`.
- `--experiment-root <path>` — override the experiment repo root. Defaults to the current working directory. Used only in `--run` mode and in the auto-detect fallback; silently ignored in `--real` mode.

Removed as CLI flags vs. the pre-refactor version of this skill: `--translation`, `--algorithm`, `--workload` (all redundant now — `--run` resolves them from disk). `WORKLOAD` persists as an interactive-only override at the confirmation prompt (scope a check run to one workload without re-invoking the skill).

## Step 0: Resolve Input, Populate Variables, Confirm With User

Before running ANY checks, resolve the input into a common set of named shell variables, then confirm with the user before proceeding. Every downstream check subsection references these variables — Steps 1-N are mode-agnostic.

### Mutex enforcement

If both `--run` and `--real` are set, exit with:

```
--run and --real are mutually exclusive; --run resolves a workspace-registered run, --real reads a filesystem bundle path.
```

### Mode dispatch

Three modes: `--run` (resolve), `--real` (legacy), or neither (auto-detect + user picker). Each mode populates the same variable set.

```bash
# --experiment-root <path> resolves to a directory. Default to the current
# working directory when the flag is absent.
EXPERIMENT_ROOT="${EXPERIMENT_ROOT:-$(pwd)}"

if [ -n "$RUN" ]; then
    # Resolve-mode: pull the hydrated JSON view of the run and parse it.
    # --experiment-root is a top-level flag on sim2real (before the
    # subcommand), not a subcommand-scoped flag.
    #
    # Use a per-invocation temp file (mktemp) so a stale file from a
    # prior failed run can't be silently reused.
    RESOLVED_JSON=$(mktemp -t sim2real_check_resolved.XXXXXX.json)
    trap 'rm -f "$RESOLVED_JSON"' EXIT
    if ! sim2real --experiment-root "$EXPERIMENT_ROOT" resolve --run "$RUN" \
            > "$RESOLVED_JSON"; then
        echo "ERROR: 'sim2real resolve --run $RUN' failed. Verify the run exists"
        echo "with 'sim2real list runs', or pass --experiment-root <path>."
        exit 1
    fi
    # Mandatory fields — use `jq -re` so a schema mismatch fails loudly
    # instead of silently coalescing to an empty string.
    CONFIGS_DIR=$(jq -re '.translation.generated_dir' "$RESOLVED_JSON") || {
        echo "ERROR: '.translation.generated_dir' missing from resolve output — schema drift?"
        exit 1
    }
    RESULTS_DIR=$(jq -re '.results.results_dir' "$RESOLVED_JSON") || {
        echo "ERROR: '.results.results_dir' missing from resolve output — schema drift?"
        exit 1
    }
    PHASES=$(jq -re '.results.phases_with_data | join(" ")' "$RESOLVED_JSON") || {
        echo "ERROR: '.results.phases_with_data' missing from resolve output — schema drift?"
        exit 1
    }
    # WORKLOADS_BY_PHASE is a bash associative array keyed by phase name.
    declare -A WORKLOADS_BY_PHASE
    while IFS=$'\t' read -r phase wls; do
        WORKLOADS_BY_PHASE["$phase"]="$wls"
    done < <(jq -r '.results.workloads_by_phase | to_entries[] | "\(.key)\t\(.value | join(" "))"' "$RESOLVED_JSON")
    TRANSLATION_HASH=$(jq -r '.translation.hash' "$RESOLVED_JSON")
    IMAGE_TAG=$(jq -r '.image_tag' "$RESOLVED_JSON")
    MANIFEST_ASSEMBLY_PATH=$(jq -r '.manifest_assembly.path // ""' "$RESOLVED_JSON")
    CLUSTER_CONFIG_PATH=$(jq -r '.cluster_config_path // ""' "$RESOLVED_JSON")
    # BASELINE_CONFIG_PATH resolves the first baseline overlay in the
    # workspace's nested-under-baseline_<name>/ shape. Empty if the run has
    # no baseline. Downstream check subsections that need the baseline
    # EPP config reference this variable.
    BASELINE_CONFIG_PATH=$(jq -r '.translation.baselines[0].generated_overlay_path // ""' "$RESOLVED_JSON")
    # WORKLOADS_DIR points at the experiment repo's workload YAML directory.
    # In resolve-mode, workload paths in manifest.assembly.yaml are resolved
    # relative to $EXPERIMENT_ROOT (arbitrary relative paths per
    # pipeline/lib/assemble_run.py:_load_workload). By convention the project
    # places them under workloads/, so WORKLOADS_DIR defaults to that. If the
    # experiment repo uses a different layout, override WORKLOADS_DIR at the
    # confirmation prompt or read manifest.assembly.yaml's workloads list to
    # discover the actual paths.
    WORKLOADS_DIR=$(jq -r '.experiment_root' "$RESOLVED_JSON")/workloads
elif [ -n "$REAL" ]; then
    # Legacy-mode: scan the bundle directly. Populate the same variables.
    if [ ! -d "$REAL" ]; then
        echo "ERROR: --real path '$REAL' does not exist or is not a directory."
        exit 1
    fi
    # Pre-refactor bundles vary in shape: some have phase dirs as direct
    # children of $REAL, others under $REAL/results/. Probe both.
    CONFIGS_DIR="$REAL/generated"
    if [ -d "$REAL/results" ]; then
        RESULTS_DIR="$REAL/results"
    else
        RESULTS_DIR="$REAL"
    fi
    if [ ! -d "$RESULTS_DIR" ]; then
        echo "ERROR: results directory not found at '$RESULTS_DIR'."
        echo "Expected either '$REAL/results/' or '$REAL/' to contain phase subdirectories."
        exit 1
    fi
    # Discover phase dirs via denylist rather than allowlist — the current
    # skill accepts arbitrarily named phase dirs (e.g., baseline/, treatment/
    # or quartic/, control/). Anything under RESULTS_DIR that's a directory
    # AND not one of the well-known non-phase names is a candidate phase.
    PHASES=$(ls "$RESULTS_DIR" | while read d; do
        case "$d" in
            generated|workloads|results_charts|snapshots|review|cluster|plans) ;;
            *) [ -d "$RESULTS_DIR/$d" ] && echo "$d" ;;
        esac
    done | tr '\n' ' ')
    if [ -z "${PHASES// }" ]; then
        echo "ERROR: no phase directories found under '$RESULTS_DIR'."
        echo "Contents: $(ls "$RESULTS_DIR" 2>/dev/null | tr '\n' ' ')"
        echo "Expected phase subdirectories (e.g. baseline/, treatment/)."
        exit 1
    fi
    # Legacy bundles list workloads as siblings under each phase dir. No
    # trace_data.csv predicate — a phase dir with named workload subdirs
    # is enough (matches the pre-refactor skill's behavior).
    declare -A WORKLOADS_BY_PHASE
    for phase in $PHASES; do
        wls=$(ls -d "$RESULTS_DIR/$phase"/*/ 2>/dev/null | xargs -I{} basename {} | tr '\n' ' ')
        WORKLOADS_BY_PHASE["$phase"]="$wls"
    done
    # These variables are resolve-mode-only. Empty in legacy mode; downstream
    # checks that reference them silently skip (they're new to resolve-mode).
    TRANSLATION_HASH=""
    IMAGE_TAG=""
    MANIFEST_ASSEMBLY_PATH=""
    CLUSTER_CONFIG_PATH=""
    BASELINE_CONFIG_PATH="$CONFIGS_DIR/baseline_config.yaml"
    WORKLOADS_DIR="$REAL/workloads"
else
    # Neither flag provided → auto-detect flow. Enumerate candidates from
    # both workspace runs and legacy bundles, then let the user pick.
    # See the "Auto-detect + user picker" subsection below.
    :
fi
```

### Auxiliary detection (all modes)

Regardless of which mode-dispatch branch ran, the checks below need four additional variables — `SIM` (the simulation bundle), `BLIS` (the simulator codebase), `GAIE` (the gateway-api-inference-extension codebase), and `LLMD` (the llm-d-router codebase). The mode-dispatch block does not populate them. Run this detection *after* the mode dispatch completes, in every mode, using the same predicates as the pre-refactor skill:

- **Sim bundle** (`SIM`): if `--sim` was passed, use it verbatim. Otherwise scan `$EXPERIMENT_ROOT/experiments/*/` for directories containing `README.md` + `algorithm/` + `workloads/`. First match wins. Common patterns: `sim2real_bundle*`, `sim2real_*bundle*`. Leave unset if no candidate is found.
- **BLIS codebase** (`BLIS`): first derive `$SIM2REAL_ROOT` inline via the same discovery Step 0.5 uses (`python -c 'from pipeline.lib import layout; import pathlib; print(pathlib.Path(layout.__file__).resolve().parents[2])'`); then try `$SIM2REAL_ROOT/inference-sim` — the framework repo's own BLIS submodule (`.gitmodules`) — if that directory contains `sim/` + `go.mod`. Fall back to the current working directory if it contains `sim/` + `go.mod`, otherwise search `../` and `tmp/` for a directory with the same shape.
- **GAIE codebase** (`GAIE`): look for `gateway-api-inference-extension` under `tmp/`, `../`, or nearby.
- **llm-d router** (`LLMD`): look for `llm-d-router` under `tmp/`, `../`, or nearby. (Legacy `llm-d-inference-scheduler` checkouts are no longer detected — that project has been sunset in favor of `llm-d-router`.)

After all four variables have been resolved, apply this GAIE alias: **if `GAIE` is unset and `LLMD` points at an `llm-d-router` checkout, set `GAIE=$LLMD`**. `gateway-api-inference-extension` has been merged into `llm-d-router`, so the router codebase serves both roles when no standalone `gateway-api-inference-extension` checkout is on disk.

**Required vs. optional at check time:**

- `SIM` is dereferenced by Sections 1a (`$SIM/workloads/`), 2a (`$SIM/config.md`), 2d (`$SIM/config.md`), 3 (`$SIM/algorithm/`), 4d (`$SIM/README.md`), and 5b (`$SIM/README.md`). If `SIM` is unset after auto-detect, surface it at the confirmation prompt as `SIM: (not found — required)` and require the operator to provide `SIM=<path>` before typing `ok`. Do not proceed with an empty `SIM`.
- `GAIE` and `LLMD` are used by the "CODE PROOF REQUIRED" checks in Sections 3 and 4. If either is unset after auto-detect, either the operator supplies it at the confirmation prompt, or the affected sub-checks emit `INAPPLICABLE — <codebase> unavailable` in their report entries instead of PASS/FAIL.
- `BLIS` is used by Section 1c/1d to cite `distribution.go` line numbers. Same treatment as GAIE/LLMD — supply at prompt, or emit `INAPPLICABLE`.

### Auto-detect + user picker (when neither `--run` nor `--real` is provided)

1. Enumerate workspace-registered runs: scan `$EXPERIMENT_ROOT/workspace/runs/*/run_metadata.json`.
2. Enumerate legacy bundles: scan `$EXPERIMENT_ROOT/experiments/*/` for directories containing `generated/` + at least one phase-shaped sibling.

Then present candidates as a plain-text numbered list and ask the user to pick by number (do NOT use `AskUserQuestion` — respond with the list in your message and wait for the user's reply):

```
I found the following candidate inputs. Reply with a number to pick one,
or reply with `--run <name>` / `--real <path>` to override.

Workspace runs (--run mode):
  1) --run trial-3         (assembled 2026-07-04, translation softreflective)
  2) --run trial-2         (assembled 2026-07-03, translation quartic)

Legacy bundles (--real mode):
  3) --real experiments/bad_and_good_admission-2025-10-14
```

If nothing is found:

```
no workspace runs at <experiment-root>/workspace/runs/ and no legacy bundles at <experiment-root>/experiments/. Run 'sim2real assemble --run <name>' to create a run, or pass --real <path> to check a legacy bundle.
```

After the user picks, populate the variables using the `--run` or `--real` branch above.

### Confirmation prompt (all modes)

Once the variables are populated (from `--run`, `--real`, or auto-detect), present the resolved values as defaults and ask the user to confirm or override. Plain-text prompt, no `AskUserQuestion`:

```
I resolved the following paths. Reply `ok` to proceed, or reply with
overrides (e.g. `SIM=/other/path`, `WORKLOAD=w7`) followed by `ok`.

Sim bundle (SIM):                <path or "(not found — required)">
Real / run input:                <RUN=trial-3 or REAL=/path>
Configs dir (CONFIGS_DIR):       <path>
Results dir (RESULTS_DIR):       <path>
Phases with data (PHASES):       <space-separated list>
Workloads by phase:              <phase>: <workloads>, ...
Baseline config (BASELINE_CONFIG_PATH): <path or "(none)">
Translation hash:                <hash or "(legacy mode)">
Image tag:                       <tag or "(legacy mode)">
Manifest assembly path:          <path or "(legacy mode)">
Cluster config path:             <path or "(legacy mode)">
BLIS codebase:                   <path or "(not found)">
GAIE codebase:                   <path or "(not found)">
llm-d-router (LLMD):             <path or "(not found)">
Workload filter (WORKLOAD):      <name or "(all)">
```

**Args are defaults, not silent overrides.** If the user provided `--sim`/`--real`/`--run` on invocation, still show the resolved values here so the operator can override any of them before checks run.

Do NOT proceed until the user replies `ok` (or equivalent). Additionally, do NOT proceed if `SIM` is unset — every check subsection dereferences it. If auto-detect failed to find a sim bundle and the operator did not pass `--sim`, block on `SIM=<path>` at the prompt.

### Step 0.5: Enumerate declared iterations (both modes)

Once the confirmation prompt has been answered `ok`, enumerate the run's declared iteration range and cross-reference with disk state. This step must run **before** any check subsection — every subsection below iterates over the resulting `PRESENT` rows.

**Resolve-mode (`--run`):** invoke the enumerator against the workspace-registered run:

```bash
# $SIM2REAL_ROOT is the framework repo root (the directory that contains
# pipeline/ and .claude/). Derive it from the sim2real CLI's own path;
# fall back to the current working directory if the derivation is
# ambiguous (edge case for operators running from a repo checkout).
SIM2REAL_ROOT=$(python -c 'from pipeline.lib import layout; import pathlib, sys; print(pathlib.Path(layout.__file__).resolve().parents[2])' 2>/dev/null || pwd)

ENUM_JSON=$(mktemp -t sim2real_check_enum.XXXXXX.json)
# Chain onto RESOLVED_JSON's trap so both temp files are cleaned up.
trap 'rm -f "$RESOLVED_JSON" "$ENUM_JSON"' EXIT
# Capture the enumerator's exit code directly (not inside `if !`, where
# `$?` inside the then-block would always be 0 — the negation flattens
# the underlying exit code).
python "$SIM2REAL_ROOT/.claude/skills/sim2real-check/scripts/enumerate_iterations.py" \
    --run "$RUN" --experiment-root "$EXPERIMENT_ROOT" > "$ENUM_JSON"
ENUM_EXIT=$?
if [ "$ENUM_EXIT" -eq 2 ]; then
    # Invocation error — enumerator already wrote a specific message
    # to stderr. Bail so the operator can fix the input.
    exit 2
fi
# Exit 1 means "MISSING rows present" — that's a real-run diagnostic,
# not an invocation error. Keep going and let the final rollup surface
# the MISSING rows in the summary table.
SHAPE=$(jq -r '.shape' "$ENUM_JSON")
DECLARED_REPLICAS=$(jq -r '.declared_replicas' "$ENUM_JSON")
DIVERGENCE_WARNINGS=$(jq -r '.divergence_warnings | join("\n  ")' "$ENUM_JSON")
MALFORMED_ITER_COUNT=$(jq -r '.malformed_iter_dir_count' "$ENUM_JSON")
```

**Legacy-mode (`--real`):** the enumerator is `--run`-only (it reads `workspace/runs/<name>/`). In legacy mode, synthesize the enumeration in-shell: every `(phase, workload)` under `$RESULTS_DIR` becomes a single row with `iteration=1`, `status=PRESENT` if `$RESULTS_DIR/<phase>/<workload>/trace_data.csv` exists. Do not emit `MISSING` for legacy bundles.

```bash
# Legacy-mode synthesis: build a JSON payload with the same schema as
# the enumerator's output so downstream steps can consume $ENUM_JSON
# uniformly across both modes.
ENUM_JSON=$(mktemp -t sim2real_check_enum.XXXXXX.json)
{
    echo '{"run":"'"$REAL"'","shape":"legacy","declared_replicas":1,'
    echo '"divergence_warnings":[],"malformed_iter_dir_count":0,"rows":['
    first=1
    for phase in $PHASES; do
        for wl in ${WORKLOADS_BY_PHASE[$phase]}; do
            [ -f "$RESULTS_DIR/$phase/$wl/trace_data.csv" ] || continue
            [ $first -eq 0 ] && echo ','
            first=0
            printf '{"phase":"%s","workload":"%s","iteration":1,"status":"PRESENT","results_dir":"%s","note":null}' \
                "$phase" "$wl" "$RESULTS_DIR/$phase/$wl"
        done
    done
    echo '],"counts":{"PRESENT":0,"MISSING":0,"SKIP":0},"exit_code":0}'
} > "$ENUM_JSON"
SHAPE=legacy
DECLARED_REPLICAS=1
DIVERGENCE_WARNINGS=""
MALFORMED_ITER_COUNT=0
```

**Diagnostic header** — print immediately (before any check subsection runs) so the operator sees mixed/corrupt state up front:

```bash
if [ -n "$DIVERGENCE_WARNINGS" ] || [ "$MALFORMED_ITER_COUNT" -gt 0 ]; then
    echo "── Run shape diagnostic ─────────────────────────────────────"
    echo "  shape:                    $SHAPE"
    echo "  declared_replicas:        $DECLARED_REPLICAS"
    echo "  malformed_iter_dir_count: $MALFORMED_ITER_COUNT"
    if [ -n "$DIVERGENCE_WARNINGS" ]; then
        echo "  divergence_warnings:"
        echo "    $DIVERGENCE_WARNINGS"
    fi
    echo "─────────────────────────────────────────────────────────────"
fi
```

**`$ENUM_JSON` schema (v1):**

```json
{
  "run": "trial",
  "shape": "replica" | "legacy" | "mixed",
  "declared_replicas": 3,
  "divergence_warnings": ["..."],
  "malformed_iter_dir_count": 0,
  "rows": [
    {"phase": "baseline", "workload": "wl-chat", "iteration": 1,
     "status": "PRESENT", "results_dir": "<abs path>", "note": null},
    {"phase": "sim2real-ac", "workload": "wl-chat", "iteration": 2,
     "status": "MISSING", "results_dir": null,
     "note": "no results/i2/ directory"},
    {"phase": "sim2real-routing", "workload": "wl-chat", "iteration": 1,
     "status": "SKIP", "results_dir": null,
     "note": "algorithm not in translation"}
  ],
  "counts": {"PRESENT": N, "MISSING": M, "SKIP": S},
  "exit_code": 1
}
```

(The example carries a MISSING row, so `exit_code` is `1`. Contract: `0` when all rows are PRESENT or SKIP; `1` when any MISSING is present; `2` for invocation error caught by the enumerator.)

Each check subsection below iterates over the `PRESENT` rows in `$ENUM_JSON`. Use each row's `results_dir` as the working path — it already carries the `iN/` segment in replica shape and points at the direct workload dir in legacy shape. `MISSING` and `SKIP` rows do not run subsections; they land in the final rollup with their enumerator-assigned status verbatim.

**Iterate PRESENT rows:**

```bash
# Emit one line per PRESENT row: "<phase>\t<workload>\t<iteration>\t<results_dir>"
jq -r '.rows[] | select(.status == "PRESENT") |
       [.phase, .workload, .iteration, .results_dir] | @tsv' "$ENUM_JSON"
```

## Goal

Perform a comprehensive parity check between a BLIS simulation bundle and its real llm-d deployment results. Produce a structured **evidence-based** report.

## Operating Constraints

**STRICTLY READ-ONLY**: Do **not** modify any files. Output a structured report.

## Evidence Requirements

For EVERY check, you MUST show:
1. **What you checked** — plain English, one sentence
2. **Expected** — the value from simulation (with source: which file, which field)
3. **Actual** — the value from real deployment (with source: which file, how computed)
4. **Verdict** — PASS / WARN / FAIL
5. **Why** — if WARN or FAIL, explain in plain English what this means and what to do about it

For numerical checks, always show a comparison table. For code/config checks, show the relevant snippets side by side.

## Reference Paths (resolved after Step 0)

- **Simulation codebase** (`BLIS`): confirmed by user
- **GAIE codebase** (`GAIE`): confirmed by user
- **llm-d router** (`LLMD`): confirmed by user
- **Sim bundle** (`SIM`): `$SIM/README.md`, `$SIM/config.md`, `$SIM/algorithm/`, `$SIM/workloads/`, `$SIM/results/`
- **Configs directory** (`CONFIGS_DIR`): Go plugins + YAML configs. In resolve-mode this is the translation's `generated/` dir under `workspace/translations/<hash>/`; in legacy-mode it's `$REAL/generated/`.
- **Results directory** (`RESULTS_DIR`): per-phase workload data. **Do not iterate `$PHASES` × `${WORKLOADS_BY_PHASE[$phase]}` directly** — iterate the `PRESENT` rows in `$ENUM_JSON` (see Step 0.5) and use each row's `results_dir` field as the working path. In replica-shape runs (`SHAPE=replica`) `results_dir` is `$RESULTS_DIR/<phase>/<workload>/i<N>/`; in legacy-shape runs (`SHAPE=legacy`) it is `$RESULTS_DIR/<phase>/<workload>/`. Each `results_dir` contains `trace_header.yaml`, `trace_data.csv`, `server_logs/`, `epp_logs/`, `gpu_logs/`, `epp_stream_done`.
- **Baseline config path** (`BASELINE_CONFIG_PATH`): specific baseline EPP config YAML. Empty when no baseline is configured; check subsections that reference it should skip in that case.
- **Workloads directory** (`WORKLOADS_DIR`): source workload YAMLs. Resolve-mode: `<experiment-root>/workloads/`. Legacy-mode: `$REAL/workloads/`.

## Checklist

Run ALL checks below.

### Path substitution contract (post-Step 0.5)

Every check subsection below quotes paths of the form `$RESULTS_DIR/<phase>/<workload>/…`. Read these as an implicit `<row.results_dir>/…` where `<row>` is the current `PRESENT` row from `$ENUM_JSON`. In replica shape (`SHAPE=replica`) `<row.results_dir>` already carries the `iN/` segment; in legacy shape it does not. The subsection paths otherwise stay verbatim.

Every subsection's verdict (PASS / WARN / FAIL) is therefore per **(phase, workload, iteration)** — one triple per `PRESENT` row. The "Per-iteration verdict rollup" section below folds these per-triple verdicts and the enumerator's `MISSING` / `SKIP` rows into the final summary table plus the automation-facing exit code.

---

### 1. WORKLOAD PARITY

For each phase in `$PHASES`, and for each workload in `${WORKLOADS_BY_PHASE[$phase]}`, find the matching workload YAML spec. **Lookup order:**
1. First check `$WORKLOADS_DIR` — if a workload YAML exists there, use it as the source of truth (in legacy mode this reflects any rate rescaling done during pre-refactor sim2real translation; in resolve mode it points at the experiment repo's workload directory).
2. If not found there, fall back to `$SIM/workloads/`.

When both exist, note any differences (especially `aggregate_rate`) and use the **`$WORKLOADS_DIR` copy** as the expected value for parity checks.

**1a. Arrival Rate (QPS)**
- **Intended QPS**: compute from `trace_data.csv` `arrival_time_us` column: `num_requests / (max - min arrival)`. This is the rate the load generator *scheduled* requests at.
- **Actual QPS**: compute from `trace_data.csv` `send_time_us` column: `num_requests / (max - min send_time)`. This is the rate requests were *actually sent* to the server. If actual QPS << intended QPS, the load generator hit a concurrency ceiling and requests queued client-side.
- From workload YAML: `aggregate_rate` (prefer `$WORKLOADS_DIR`, fall back to `$SIM/workloads/`)
- Tolerance: intended QPS within 5% of spec. If actual QPS is more than 5% below intended QPS, emit WARN — this means the load generator couldn't keep up with the schedule (likely concurrency ceiling or server backpressure), so the server saw less load than intended and results are not comparable to simulation.
- Show: table with spec rate (note source: `$WORKLOADS_DIR` or `$SIM/workloads/`), intended QPS, actual QPS — all three columns so the reader can spot concurrency bottlenecks

**1b. SLO Class Distribution**
- From sim: each client's `rate_fraction`
- From real: count `slo_class` column in `trace_data.csv`, compute percentages
- Tolerance: within 2pp per class
- Show: table with each class — expected %, actual %, difference

**1c. Input Token Distribution**
- From sim: `input_distribution` params (e.g. for lognormal: mu, sigma, min, max).
- From real: compute mean, stdev, min, max of `input_tokens` column.
- **Expected-moment derivation (critical):** use sample-then-clamp Monte Carlo matching the BLIS sampler semantics (`inference-sim/sim/workload/distribution.go`). For each draw: sample from the configured distribution → round to int → clamp to `[min, max]` when bounds are set → floor at 1. Compute the expected mean and stdev as moments of the clamped samples (N ≥ 100k for stable means).
  - For lognormal (`LognormalSampler.Sample` at `distribution.go:105`), the closed-form `exp(mu + sigma²/2)` is correct ONLY when no clamp bounds are set. When `min > 0` (the common case — e.g. `code_generation` output at `mu=5.2711, sigma=1.2498, min=50`), a non-trivial below-min mass is piled at `min` by clamping, shifting the mean materially below the closed-form value.
  - Do not use rejection-sampling truncation (resample if outside `[min, max]`) for expected-moment computation. BLIS clamps; rejection produces a different distribution and a measurably different mean.
- Tolerance: mean within 5%, stdev within 10%.
- Show: table with expected vs actual for mean, stdev, min, max. Note the expected-mean derivation (`clamped MC` vs `closed-form`) in the table caption so the reader knows which path was used.

**1d. Output Token Distribution**
- From sim: `output_distribution` params.
- From real: compute from `output_tokens` column.
- Use the same sample-then-clamp expected-moment derivation as 1c.
- Same tolerances as 1c.
- Show: same table format as 1c.

**1e. Prefix Tokens**
- From sim: check if any client has `prefix_group` or prefix config
- From real: check `prefix_length` column — should match (all zeros if no prefix in sim)
- Show: "All zero: yes/no" + if not zero, show distribution

**1f. Streaming**
- From sim: `streaming` field per client
- From real: `streaming` column in trace
- Show: expected vs actual streaming fraction

**1g. Burstiness / Arrival Pattern**
- From sim: each client's `arrival.process` (poisson, constant, etc.) — every client carries its own spec.
- From real: compute coefficient of variation (CV) of inter-arrival times **per `client_id`**, not on the merged stream.
  - Group `trace_data.csv` by `client_id`. For each group: sort by `arrival_time_us`, take consecutive diffs, compute `CV = stdev / mean`.
  - Verdict each client against its own configured `arrival.process`:
    - Poisson: CV ~1.0 (0.8-1.2 acceptable)
    - Constant: CV ~0 (<0.1)
- **Why per-client.** Each client's IAT sampler runs independently (`inference-sim/sim/workload/arrival.go`); a `constant` client's per-client CV is 0 exactly. The aggregate stream is `concat(per_client_arrivals).sort_by(arrival_time)` (`sim/workload/generator.go`), so when two `constant` clients run at different rates the merged CV is whatever the deterministic superposition produces (e.g. ~0.5 for streams at 1.2 + 2.8 req/s). Pure Poisson superposition is approached only as N → ∞ (Palm-Khintchine). Gating on aggregate CV produces FAILs on workloads where every client is a perfect metronome.
- **Aggregate CV (diagnostic only).** Also compute the CV of the fully merged stream and report it for context, but do not pass/fail on it. Label it "merged-stream CV — informational; does not gate verdict."
- Show: per-`client_id` table — `client_id | configured process | measured CV | verdict` — plus a single-row "merged-stream CV" diagnostic below.

---

### 2. CONFIG PARITY

**2a. vLLM Server Config**
Read `trace_header.yaml` AND the first vLLM server log from `server_logs/` to extract actual config.

Check these against `$SIM/config.md`:
- Model name (exact match)
- TP degree (`tensor_parallel_size`)
- GPU type (from log or header)
- `max_model_len`
- `gpu_memory_utilization`
- `enable_prefix_caching` (must match sim expectation)
- `max_num_seqs`
- `max_num_batched_tokens`
- `enable_chunked_prefill`

Show: table with each config param — expected (from config.md), actual (from server log)

**2b. Instance Count**
- From sim: `--num-instances` in run commands or config.md
- From real: count distinct server log files in `server_logs/` (across `$RESULTS_DIR/<phase>/<workload>/server_logs/` for each phase × workload)
- Show: expected count vs actual count

**2c. Variant Isolation — CRITICAL CHECK**

The real bundle may contain multiple experiment variants — one per phase in `$PHASES` (e.g., `baseline`, `treatment`, or `quartic`, `control`). This check verifies that the variants are **identical in all respects except the flow control policy** — ensuring a clean A/B comparison.

**Locate configs:** Find all EndpointPickerConfig YAMLs under `$CONFIGS_DIR/`. In legacy-mode the baseline sits at `$CONFIGS_DIR/baseline_config.yaml` and per-treatment overlays live at `$CONFIGS_DIR/<treatment>/<treatment>_config.yaml`. In resolve-mode the baseline overlay path is exposed as `$BASELINE_CONFIG_PATH` and per-treatment overlays live at `$CONFIGS_DIR/<treatment>/<treatment>_config.yaml` (nested under `<treatment>/`).

**For each pair of configs, diff the following sections and classify:**

| Section | Must be IDENTICAL across all variants | Expected to DIFFER |
|---------|--------------------------------------|-------------------|
| `featureGates` | Yes | — |
| `plugins` (list of plugin types) | Mostly — see below | Only the flow control policy plugin entry |
| `schedulingProfiles` (scorers, weights, picker) | Yes | — |
| `flowControl` | — | Yes — this is the independent variable |
| `router.inferenceObjectives` (priority bands) | Yes | — |
| Instance count (from server_logs) | Yes | — |
| vLLM config (from server logs) | Yes | — |

**Specifically verify:**
1. **Routing is identical**: same scorer plugins, same weights, same picker in all variants.
2. **Priority bands are identical**: same InferenceObjective CRDs (or same priority values) applied to all variants.
3. **Feature gates are identical**: all variants enable/disable the same gates.
4. **The ONLY difference is the `flowControl` section**: baseline has `flowControl: {}` (built-in default ceiling 1.0), treatment references a custom policy plugin, control references a plugin that implements the same behavior as baseline (ceiling 1.0) but through the custom plugin registration path.

**Why control exists**: Control uses the same plugin registration mechanism as treatment but with baseline-equivalent logic. Comparing baseline vs control isolates plugin framework overhead. Comparing control vs treatment isolates the algorithm effect.

**Show:**
- Side-by-side diff of all config YAMLs (redacting identical sections, highlighting differences)
- Table: for each config section, state whether it matches across variants
- The generated Go code for each variant's policy plugin — confirm control returns constant 1.0 and treatment implements the sim algorithm
- Verdict: PASS if only flow control policy differs, FAIL if routing/scheduling/priority/feature-gates diverge

**2d. EPP & InferenceObjective Config Parity vs `config.md`**

Step 2c is a cross-variant **identity** check — all variants can be wrong in the same way and 2c still passes. This sub-section is the **correctness** check against the project-supplied truth in `$SIM/config.md`.

**Conditional**: run only if `$SIM/config.md` contains EPP config blocks (markdown sections matching `## llm-d EPP Configuration — Baseline|Treatment|Control`) and/or InferenceObjective entries (`## Priority Bands`, or any fenced YAML with `kind: InferenceObjective`). Otherwise SKIP with note "no EPP / InferenceObjective blocks in config.md."

**Source-of-truth → generated artifact mapping:**

| `config.md` block | Generated artifact |
|---|---|
| `## llm-d EPP Configuration — Baseline` | `$BASELINE_CONFIG_PATH` → `router.epp.pluginsCustomConfig.custom-plugins.yaml` (parse the inner YAML string). Falls back to legacy `inferenceExtension.pluginsCustomConfig.custom-plugins.yaml` if the `router.epp` path is absent (pre-v0.7.0 runs). |
| `## llm-d EPP Configuration — Treatment` | `$CONFIGS_DIR/<treatment>/<treatment>_config.yaml` deep-merged onto baseline |
| `## llm-d EPP Configuration — Control` | `$CONFIGS_DIR/<control>/<control>_config.yaml` deep-merged onto baseline |
| InferenceObjective entries (`kind: InferenceObjective`) | `$BASELINE_CONFIG_PATH` → `router.inferenceObjectives` (list of `{name, priority}` — the v0.9.0 chart renders each into a `kind: InferenceObjective` CR at deploy time). Falls back to legacy `extraObjects` filtered to `kind: InferenceObjective` if `router.inferenceObjectives` is absent (pre-v0.7.0 runs). |

**Compare these fields (order-independent for lists):**

- `apiVersion`, `kind`
- `featureGates` (set equality)
- `plugins` — list of `(type, name, parameters)` tuples
- `schedulingProfiles` — for each profile, the `(pluginRef, weight)` map
- `flowControl.usageLimitPolicyPluginRef`
- For each `router.inferenceObjectives` entry: `name`, `priority`. `poolRef.name` and `poolRef.group` are hardcoded by the v0.9.0 chart (`Release.Name` + `inference.networking.k8s.io`) and MUST be validated once against `config.md` rather than per-item. On legacy shape (`extraObjects` filtered to `kind: InferenceObjective`), compare `metadata.name`, `spec.priority`, `spec.poolRef.name`, `spec.poolRef.group` as before.

**Show:** side-by-side table — `field path | config.md value | generated value | match`.

**Verdict**: PASS only if every field matches. FAIL on any mismatch — call out the mismatched field path and both values. This catches the apiVersion / poolRef class of translation bug (e.g., #332) statically, before compute is burned.

---

### 3. SIGNAL PARITY

Read the algorithm files from `$SIM/algorithm/` and the generated Go plugin from `$CONFIGS_DIR/`.

**IMPORTANT: Code proof requirement.** For every claim about how GAIE/llm-d works, you MUST show the actual source code from the `GAIE` or `LLMD` codebase as proof. Do not make claims based on field names or assumptions — grep the codebase and show file:line and the relevant code snippet.

**3a. Signal Availability**
For each signal the algorithm uses, verify it exists in GAIE:
- Queue depth: grep for `WaitingQueueSize` in `GAIE` codebase
- KV utilization: grep for `KVCacheUsagePercent` in `GAIE` codebase
- Priority: grep for `Priority` in scheduling types in `GAIE` codebase
- Show: for each signal, the **exact GAIE source file:line** and the struct definition or accessor code snippet

**3b. Signal Transform — CODE PROOF REQUIRED**
Check the generated Go plugin applies correct transforms. To determine the correct transform:

1. **Find where GAIE populates the metric**: Grep for `KVCacheUsagePercent` assignments in `GAIE` codebase to see what value range it stores (0-1 or 0-100). Show the code.
2. **Find how GAIE's own saturation detector uses it**: Grep for the saturation detector in `GAIE` (e.g., `detector.go` or similar) to see if it divides by 100. Show the code.
3. **Find the Prometheus metric source**: Grep for the vLLM metric name (e.g., `kv_cache_usage_perc`) to see what vLLM emits. Show the code.
4. **Check test values**: Grep for test files that set `KVCacheUsagePercent` to see what range test data uses (0.5 vs 50.0). Show the code.

Then compare against the generated plugin's transform. Show side-by-side:
```
GAIE source (file:line):     <actual code>
GAIE detector (file:line):   <actual code>
GAIE test (file:line):       <actual code with test values>
Generated plugin (file:line): <actual code>
Sim algorithm (file:line):   <actual code>
```

Verdict: PASS if transforms are consistent, FAIL if not. Plain English explanation of what the bug means practically.

**3c. Saturation Formula — CODE PROOF REQUIRED**
1. **Find GAIE's saturation formula**: Grep for the saturation detector/calculator in `GAIE`. Show the full function.
2. **Find GAIE's empty-pool behavior**: What does GAIE return when there are no pods? Show the code.
3. Compare against sim algorithm and generated plugin. Show all three implementations side-by-side with file:line references.
4. Compare threshold values from YAML against GAIE defaults (grep for default threshold constants in `GAIE`).

Show:
```
GAIE detector (file:line):    <full saturation function>
Sim algorithm (file:line):    <full saturation function>
Generated plugin (file:line): <full saturation function>
```

**3d. Snapshot Delay — CODE PROOF REQUIRED**
- From sim: `--snapshot-refresh-interval` value from config.md
- From GAIE: grep for metric refresh/scrape/staleness configuration in `GAIE` and `LLMD` codebases. Look for:
  - `MetricsStalenessThreshold` or similar
  - Prometheus scrape interval defaults
  - Any signal propagation delay constants
- Show: the actual GAIE/llm-d code that controls signal freshness, with file:line
- Plain English: explain what this delay difference means for admission decisions

---

### 4. POLICY PARITY

**IMPORTANT: Code proof requirement.** For claims about GAIE routing/admission behavior, show the actual source code from the `GAIE` or `LLMD` codebase.

**4a. Routing Policy — CODE PROOF REQUIRED**
- From sim: `--routing-policy` in run commands or README
- From real: check `$BASELINE_CONFIG_PATH` and `$CONFIGS_DIR/<treatment>/<treatment>_config.yaml` for picker plugins
- **From GAIE**: grep for each picker plugin type (e.g., `random-picker`, `fcfs-ordering-policy`, `max-score-picker`) in the `GAIE` and `LLMD` codebases. Show what each plugin does (file:line + brief code snippet or doc comment).
- Map: sim `round-robin` ~ real `random-picker` (both are non-affinity); sim `weighted-scoring` ~ real `max-score-picker` with scorers
- Show: sim policy name, real YAML plugin list, GAIE code proof of what each plugin does, whether they're equivalent

**4b. Admission Policy — Baseline**
- From sim: admission policy for baseline runs (e.g., `gaie-legacy`)
- From real: `$BASELINE_CONFIG_PATH` should have NO admission plugin OR the matching GAIE control plugin
- **From GAIE**: if baseline has no admission plugin, grep for GAIE's default/built-in admission behavior. Does GAIE apply any admission by default? Show the code.
- Show: the relevant YAML section from `$BASELINE_CONFIG_PATH`, plus GAIE default behavior proof

**4c. Admission Policy — Treatment**
- From sim: admission algorithm parameters (thresholds, ramps)
- From real: the treatment config YAML at `$CONFIGS_DIR/<treatment>/<treatment>_config.yaml` — plugin parameters must match exactly
- Check: parameter names map correctly to priority tiers (sheddable vs batch — this has been a source of bugs!)
- Show: side-by-side table of sim params vs YAML params

**4d. Priority Tier Mapping — CODE PROOF REQUIRED**
Verify the generated Go plugin dispatches thresholds to the correct priority values:
- critical = 100, standard = 0: always admit
- sheddable = -50: should get the MORE aggressive ramp (lower shedStart)
- batch = -10: should get the LESS aggressive ramp (higher shedStart)

**From GAIE**: grep for priority constants or InferenceModel priority definitions in `GAIE`/`LLMD`. Show where these priority values (100, 0, -10, -50) are defined or documented.

Cross-check against `$SIM/README.md` transfer instructions.
- Show: the Go switch/case block from the generated plugin, annotated with which tier gets which ramp. Show the GAIE priority definitions as proof. Highlight if the mapping is wrong.

---

### 5. RUNTIME ANALYSIS

For each phase in `$PHASES` and each workload in `${WORKLOADS_BY_PHASE[$phase]}`, analyze the actual execution.

**5a. vLLM Config Validation**
Parse each server log's startup lines. Verify `non-default args` match expected config.
Extract: model, TP size, max_model_len, gpu_memory_utilization, enable_prefix_caching, max_num_seqs, max_num_batched_tokens.
- Show: extracted values from log vs expected

**5b. Instance Health & Request Distribution**
- Count `/completions` requests per instance by grepping each server log file in `$RESULTS_DIR/<phase>/<workload>/server_logs/` for `/completions` (or `/chat/completions` if chat API). This is the authoritative per-instance request count — `trace_data.csv` does not have an instance identifier column.
- Compute total served requests (sum across instances), per-instance percentage, and spread (max-min as % of average).
- Compare total served vs total sent (from `trace_data.csv` row count) — the difference is requests shed by admission before reaching any instance.
- Flag any instance that appears unhealthy (no requests, errors in logs)

**Distribution expectation depends on routing policy:**
- **Even-distribution routers** (`random-picker`, `round-robin`, or load-aware scorers like `queue-scorer` + `kv-cache-utilization-scorer`): expect roughly uniform distribution. Each instance should get approximately `100%/N` of requests. Spread (max−min as % of average) should be < 20%. WARN if > 20%, FAIL if > 40%.
- **Affinity routers** (`prefix-cache-affinity`, `session-affinity`, `lora-affinity`): expect skewed distribution aligned with the affinity key. Verify that the skew correlates with the affinity signal (e.g., prefix groups cluster on specific instances) rather than random hot-spotting.
- **Custom/experimental routers**: infer the expected distribution from the routing policy description in `$SIM/README.md` or config, and check whether the real distribution matches. Document the expectation and rationale.

**Run this check for ALL phases in `$PHASES` × ALL workloads in `${WORKLOADS_BY_PHASE[$phase]}`.** The distribution should be consistent across phases — if routing config is identical (verified in 2c), the distribution pattern should be similar. Flag any phase that shows significantly different distribution from others at the same workload (this would indicate a deployment issue, not an algorithm effect).

- Show: table with phase/workload, per-instance request count and percentage, total served, spread %, and verdict. One row per phase×workload combination.

**5c. EPP Runtime Proof — Treatment Active**

Verify from EPP logs (`$RESULTS_DIR/<phase>/<workload>/epp_logs/`) that the configured policy is actually executing at runtime. This is the proof that config translation resulted in correct runtime behavior — not just correct YAML.

**For each phase, check:**
1. **Flow control is active**: grep EPP logs for flow controller messages (e.g., `flow-controller`, `FlowControlAdmissionController`, `enqueued`, `dispatched`). If absent, the feature gate may not have activated despite being in config.
2. **Priority bands recognized**: grep for priority values in request processing logs. Confirm both configured priorities appear (e.g., priority:100 and priority:-50 for critical/sheddable). If only one priority appears, the InferenceObjective CRDs may not have been applied.
3. **Policy-specific behavior**:
   - For treatment phases (e.g., quartic): at high saturation, expect some requests to show gating/queueing delays or shed outcomes. At low saturation, all requests dispatched immediately (dormant behavior is valid).
   - For baseline/control phases: all requests should be dispatched (ceiling=1.0 means no gating).
4. **Pod discovery**: confirm the EPP sees all expected pods (extract unique PodNames from scheduling logs). Count should match instance count from 2b.
5. **Hardware parity**: extract `CacheNumBlocks` and `CacheBlockSize` from EPP endpoint data — must be identical across phases (confirms same GPU memory allocation / model config).

**Cross-phase deployment check:**
- Note whether phases share the same physical deployment (same pod-template-hash) or use separate deployments. If separate, confirm identical hardware config (CacheNumBlocks, CacheBlockSize, GPU type).
- Flag if phases run in different namespaces — not a problem per se, but document it as it means separate clusters or separate resource quotas.

- Show: table per phase with: flow control active (yes/no), priorities seen, dispatch outcomes (Dispatched/Shed/Timeout counts), pod count, CacheNumBlocks, deployment hash, namespace.

**5c.6. InferenceObjective Resolution at Runtime**

Step 5c.2 (priority bands recognized) is a downstream symptom check — it sees that priorities are missing without saying why. This sub-section pins the runtime cause: it confirms the EPP loaded the configured InferenceObjective CRs and associated them with requests.

**Conditional**: run only if `$BASELINE_CONFIG_PATH` declares at least one entry under `router.inferenceObjectives` (v0.9.0 chart-native shape) or at least one `kind: InferenceObjective` under `extraObjects` (legacy shape). Otherwise SKIP with note "no objectives configured."

**Verbosity prerequisite.** This check reads `objectiveKey` and `priority` fields from `Request handled` log lines, attached at `director.go:275` (`logger.WithValues("objectiveKey", ..., "priority", ...)`). These fields are present whenever the EPP is at `-v=3` (`V(logutil.VERBOSE)`) or higher. Before evaluating, grep `$RESULTS_DIR/<phase>/<workload>/epp_logs/*.log` for any line containing `"objectiveKey"`. If absent, this check is INAPPLICABLE — surface that the EPP must be redeployed with `-v=3` or higher.

**Why a positive resolution check rather than a negative-string grep.** A previous version of this check greped for `"No associated InferenceObjective found, using default"` and FAILed on any occurrence. That doesn't work in production-shaped deployments: k8s readiness/liveness probes from the gateway / Service hit the EPP without the `x-llm-d-objective` header by design, so they always trigger that fallback path. The grep can't distinguish that benign infrastructure traffic from a real workload-resolution bug, and it produces FAILs on every run. The positive check below tests what the bug-grep was *meant* to detect — that workload requests resolve to their configured objectives — without false-positiving on infrastructure traffic.

**Inputs.** Parse every `Request handled` line in `$RESULTS_DIR/<phase>/<workload>/epp_logs/*.log` and extract `(objectiveKey, priority)`. Read the configured set of `(name, priority)` pairs from `$BASELINE_CONFIG_PATH` `router.inferenceObjectives` (v0.9.0 chart-native shape); fall back to `(name, spec.priority)` pairs from `$BASELINE_CONFIG_PATH` `extraObjects` filtered to `kind: InferenceObjective` (legacy shape) if the chart-native path is absent. Read `trace_data.csv` row count per cell.

**Five criteria — all must PASS for the check to PASS:**

1. **Coverage.** Every configured `InferenceObjective` name appears at least once in the `Request handled` lines, resolving to its configured priority (`priority` from `router.inferenceObjectives` on chart-native shape, or `spec.priority` from `extraObjects` on legacy shape — see Inputs above). *Every configured objective receives traffic.*
2. **Determinism.** Each distinct `objectiveKey` value resolves to a singleton `priority`. No `objectiveKey` fans out to multiple priorities. *No mis-mapping mid-run.*
3. **Workload resolution rate.** `count(Request handled lines with objectiveKey != "")` ≥ `count(rows in trace_data.csv)` per cell. *Every workload request carried the header and resolved to a configured objective.*
4. **Priority value set.** The set of `priority` values seen across all `Request handled` lines is a subset of `{configured priorities} ∪ {0}`. The 0 is the documented `defaultPriority` fallback at `director.go:222`. *No stray priorities, e.g. from a misloaded CR.*
5. **Empty-key budget.** `count(objectiveKey == "")` is bounded — explainable as warmup + plausible probe rate × runtime. The fallback path is reserved for infrastructure traffic, not workload. A reasonable default budget: `≤ 5%` of total handled, or `≤ harness warmup_requests + 60 × runtime_seconds` (k8s default probe cadence ~1 Hz per replica). *The default-objective path is for probes, not workload.*

**Verdicts:**
- PASS: all five criteria pass for every cell.
- FAIL: any criterion fails. Common failure modes:
  - Coverage fails → InferenceObjective CR not loaded by the EPP (apiVersion / poolRef mismatch — `#332`-class).
  - Determinism fails → mid-run reload assigning the same `objectiveKey` to a different priority — config drift.
  - Workload resolution rate < trace count → workload requests landing in the empty-key bucket (header not propagating through gateway, or CR not yet loaded at workload start).
  - Priority value set has extras → stray priority from a misloaded CR.
  - Empty-key over budget → either probe traffic spike or workload requests slipping into the fallback path; cross-reference with §5b distribution to disambiguate.
- INAPPLICABLE: no `objectiveKey` field in any log line — EPP verbosity below `-v=3`.

**Show:** per-cell table — `cell | configured objectives | resolved (key→priority) | trace rows | workload-resolved | empty-key | verdict`.

**Trend diagnostic (secondary, do not gate on this).** The literal-string `"No associated InferenceObjective found, using default"` count is still useful as a **cross-run regression** signal. A jump in this count between two runs with the same workload shape and probe cadence indicates regression even if the absolute count is non-zero in both. Report as `delta vs prior run` if a prior run is available; do not pass/fail on it.

**5d. Prefix Hit Ratio**
If `enable_prefix_caching=True`, grep server logs for prefix cache hit metrics.
If `enable_prefix_caching=False`, confirm no unexpected prefix hits.
- Show: prefix caching status and any hit/miss metrics found

**5e. Request Status**
- Baseline phase: expect nearly 100% `status=ok` (no admission shedding)
- Treatment phase: check `status` column for rejection patterns. Compute shed rate per SLO class.
- Verify shed behavior matches algorithm expectations (sheddable shed most, batch shed less, critical/standard never shed)
- Show: table with SLO class, total requests, ok count, shed count, shed rate %

**5f. Error Analysis**
Grep server logs for `ERROR`, `WARNING`, `OOM`, `CUDA`, `timeout` patterns.
- Show: count of each pattern per log file, and any particularly concerning lines

**5g. KV Cache Utilization**
From server logs, look for KV cache size info:
- `GPU KV cache size: N tokens`
- `Maximum concurrency for X tokens per request: Y.Zx`
- Show: these values per instance

**5h. GPU Thermal/Power Health**

Verify that GPU hardware was operating within nominal range during the workload window — that the metrics being compared are not influenced by thermal or power throttling. A FAIL here invalidates latency-based conclusions for that cell.

**Source data:**
- `$RESULTS_DIR/<phase>/<workload>/gpu_logs/<pod>.uuids` — pod ↔ GPU UUID (one line per pod, format `GPU 0: <name> (UUID: GPU-...)`)
- `$RESULTS_DIR/<phase>/<workload>/gpu_logs/<node>.gpus.csv` — UUID ↔ host index ↔ bus-id (deterministic per-node map; format `index, uuid, gpu_bus_id` no header)
- `$RESULTS_DIR/<phase>/<workload>/gpu_logs/<node>.log` — per-sample temp / power / power_cap / util / mem (sampled `nvidia-smi` table)
- `$RESULTS_DIR/<phase>/<workload>/trace_data.csv` — workload window (`min(send_time_us)` to `max(last_chunk_time_us)`, status==ok)

**Resolution rule:** pod → UUID (.uuids) → host index (.gpus.csv) → metric rows in `<node>.log`. The `0` in `.uuids` is the **container-local** index and is NOT the host index — use the `.gpus.csv` join. If any artifact is missing for a cell, **SKIP** that cell with `reason: missing <artifact>`. Skipping is not failing — throttle status unknown.

**Sample filter:** restrict to samples in the workload window where `util_pct == 100` (steady-state running). If fewer than 30 such samples, **SKIP** with `reason: insufficient steady-state samples` (typical for very-low-QPS workloads with no sustained-busy phase).

**Compute per (cell × pod):**
- `temp_p95`, `temp_max` (°C)
- `power_cap_min` (W) — was the host/rack cap reduced below the accelerator's design TDP?
- `power_p10` (W) at sustained `util==100` — workload-driven, but a low p10 at high util suggests forced downclock
- `throttle_frac` — fraction of `util==100` samples that satisfy `temp ≥ 80 °C AND power ≤ (cell_power_mean − 50W)` (combined thermal-induced power-throttle inference)

**Thresholds (H100 SXM5; per-accelerator override expected for other parts — key the lookup on the GPU model name reported in `<node>.log`):**

| Check | PASS | WARN | FAIL |
|---|---|---|---|
| `temp_p95` | < 70 °C | 70–79 °C | ≥ 80 °C |
| `temp_max` | < 75 °C | 75–84 °C | ≥ 85 °C |
| `power_cap_min` vs design TDP | matches | within 5% below | > 5% below |
| `throttle_frac` | 0% | 0–5% | > 5% |

**Cell verdict:** PASS if all checks PASS; FAIL if any check FAILs; WARN otherwise.

**Show:** per `(cell × pod)` row with `phase | workload | pod | node | gpu_index | temp_p95 | temp_max | power_p10 | power_cap_min | throttle_frac | verdict`. Roll up:
- PASS / WARN / FAIL counts across all cells
- Cells excluded from clean-comparison stratum (= any non-PASS); these are tagged `thermal-FAIL` for cross-referencing
- **Persistent hotspots**: same `(node, host_index)` failing across ≥ 2 distinct (phase, workload) pairs — surfaces hardware-positional thermal issues independent of any single run

**Cross-reference with §5b/§5e:** any cell that FAILs §5h must be flagged `thermal-FAIL` in the §5e/§5b summary tables and excluded from any latency-based parity claim that aggregates across cells. Run §5h first within the runtime-analysis agent so its verdicts are available when §5b/§5e are assembled.

**Inferential limit (note in report):** the table-format `nvidia-smi` capture does not include `clocks_throttle_reasons.*` or `clocks.current.sm/mem`, so throttle detection is inferred from temp/power coupling rather than read directly. If those fields are added to per-sample collection in a future revision of the data pipeline, the WARN/FAIL temperature thresholds can tighten by ~10 °C.

---

## Per-iteration verdict rollup (final step)

After every check subsection has emitted its per-(phase, workload, iteration) PASS/WARN/FAIL, roll up the results into a single summary table. Fold WARN into PASS for the rollup — WARN rows still surface their reason in the per-subsection detail, but do not fail the run-level verdict.

**Row shape** (one row per `(phase, workload, iteration)` triple in `$ENUM_JSON`):

```
wl-<workload>|<phase>|i<N>  <verdict> [<reason>]
```

**Verdict per row:**

| Enumerator `status` | Signal check outcome | Row verdict |
|---|---|---|
| `PRESENT` | all signals PASS/WARN | `PASS` |
| `PRESENT` | any signal FAIL | `FAIL (<K signals failed: names>)` |
| `MISSING` | (skipped — no data) | `MISSING (no results/iN/ directory)` |
| `SKIP` | (skipped — no data) | `SKIP (algorithm not in translation)` |

**Rollup aggregates** (printed under the row table):

- PASS / FAIL / SKIP / MISSING counts across all rows.
- If `SHAPE=mixed` or `MALFORMED_ITER_COUNT > 0`, restate the divergence warnings from Step 0.5 for operator visibility.

**Exit code contract** (returned as the skill's overall status; matches `sim2real-check`'s automation surface):

- `0` — all rows are PASS or SKIP.
- `1` — any FAIL or MISSING.
- `2` — invocation error caught in Step 0.5 (missing run; unreadable workspace files — `run_metadata.json`, `manifest.assembly.yaml`, `translation_output.json`; malformed inputs). Never emitted from the rollup step itself.

Consumers (CI, dashboards) rely on this contract — do not fold FAIL or MISSING into `0` under any rollup rule.

**Example rollup:**

```
── Per-iteration verdicts ────────────────────────────────────────
  wl-chat-mid|baseline|i1         PASS
  wl-chat-mid|baseline|i2         PASS
  wl-chat-mid|baseline|i3         PASS
  wl-chat-mid|sim2real-ac|i1      PASS
  wl-chat-mid|sim2real-ac|i2      FAIL (2 signals failed: TTFT_p95, e2e_p50)
  wl-chat-mid|sim2real-ac|i3      MISSING (no results/i3/ directory)
  wl-chat-mid|sim2real-routing|i1 SKIP (algorithm not in translation)
──────────────────────────────────────────────────────────────────
Aggregates: 4 PASS  1 FAIL  1 MISSING  1 SKIP
Exit code:  1 (FAIL and MISSING present)
```

## Output Format

Structure the report as follows. Every section must have evidence tables/snippets, not just verdicts.

```markdown
# Sim2Real Parity Report
**Real bundle**: <RUN=trial-3 or REAL=/path>
**Sim reference**: <SIM path>
**Date**: <date>

## Summary
| Category | PASS | WARN | FAIL |
|----------|------|------|------|
| Workload | X | Y | Z |
| Config   | X | Y | Z |
| Variant Isolation | X | Y | Z |
| Signal   | X | Y | Z |
| Policy   | X | Y | Z |
| Runtime  | X | Y | Z |
| GPU Thermal/Power | X | Y | Z |
| **Total** | **X** | **Y** | **Z** |

## 1. Workload Parity
### Phase: <phase> / Workload: <name>

**1a. Arrival Rate** — PASS
Expected 210 req/s (from workloads/w3.yaml:aggregate_rate), measured 210.4 req/s (25200 requests over 119.8s). Difference: 0.2%.

| Metric | Sim Spec | Real Trace | Diff | Verdict |
|--------|----------|------------|------|---------|
| QPS | 210 | 210.4 | 0.2% | PASS |

[... continue for each check with tables and explanations ...]

## 5h. GPU Thermal/Power Health

| phase | workload | pod | node | gpu | temp_p95 | temp_max | pwr_p10 | cap_min | throttle% | verdict |
|-------|----------|-----|------|-----|----------|----------|---------|---------|-----------|---------|
| ...   | ...      | ... | ...  | ... | ...      | ...      | ...     | ...     | ...       | ...     |

**Persistent hotspots:** `<list of (node, host_index) repeated FAILs across runs, e.g. "pokprod-b93r39s2 / GPU 6 — 6/8 cells FAIL, temp_p95 mean 85 °C">`

**Cells excluded from clean-comparison stratum:** `<list>`

## Action Items
1. [FAIL] <what to fix and why>
2. [WARN] <what to investigate>
```

## Parallelization

Use the Agent tool to parallelize independent checks:
- Agent 1: Workload parity (`trace_data.csv` analysis via python3)
- Agent 2: Config + Signal parity (YAML/Go file comparison)
- Agent 3: Runtime analysis (server log parsing, including §5h GPU thermal/power health). Within Agent 3, run §5h **before** §5b/§5e so the per-cell `thermal-FAIL` flag is available when those subsections build their tables.

Combine results into the final report.
