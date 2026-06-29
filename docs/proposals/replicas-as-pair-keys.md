# Replicas: repeated execution within a run

A complement to [three-dimensional-sim2real.md](three-dimensional-sim2real.md) and [validate-execute-pattern.md](validate-execute-pattern.md). Adds a sub-run dimension — replicas — to support the "run N times for N datapoints" workflow without leaving the three-dimensional model.

## Motivation

Operators routinely want to repeat the same `(workload, phase)` measurement N times for statistical confidence. Today this is a manual workflow: run once, copy `results/` aside, re-run, copy again, merge by hand. The result is brittle file shuffling and a lossy archive history.

In the three-dimensional model `(translation, cluster, run)`, none of the three dimensions captures repetition. Spawning N runs would work but multiplies `runs/<run>/cluster/` and `run_metadata.json` for byte-identical configurations — wasted disk and an awkward run list.

Replicas belong below run, not beside it.

## The core idea

Expand each pair key (`wl-<workload>|<package>`) into N replica pair keys (`wl-<workload>|<package>|i<N>`). The orchestrator's existing slot, scope, capacity, and failure-handling machinery operates on pair keys without caring whether they're replicas of one logical measurement — so replicas inherit all of it for free.

```
wl-chat-mid|sim2real-ac           →   wl-chat-mid|sim2real-ac|i1
                                       wl-chat-mid|sim2real-ac|i2
                                       wl-chat-mid|sim2real-ac|i3
```

Each replica is an independent PipelineRun dispatch with its own `resultsDir` subtree.

## Where replicas are declared

At assemble time, recorded in the run's metadata:

```bash
sim2real assemble --run R --replicas 3
```

Writes:

```yaml
# runs/R/manifest.assembly.yaml
replicas: 3
workloads: [chat-low, chat-mid]
baselines: [...]
...
```

And expands the base pair-key set: 2 workloads × 1 baseline package × 3 replicas = 6 pair keys (`wl-chat-low|sim2real-ac|i1..i3` and `wl-chat-mid|sim2real-ac|i1..i3`). The progress ConfigMap holds an entry for each.

Why at assemble time, not at deploy time:
- A run's identity includes "what was intended to be measured." Replicas is part of that intent.
- The pair-key set in the ConfigMap is stable across deploy invocations.
- Reproducible from disk: reading `manifest.assembly.yaml` tells you the declared count.

## Assemble is monotonic in pair keys

This is the property that makes the "run 1, look, run more" pattern work:

- Re-running `sim2real assemble --run R --replicas 6` after `--replicas 3` is an **additive merge**. Existing keys (`i1..i3`) are preserved unchanged; new keys (`i4..i6`) are added as pending.
- Same property for workloads: adding a workload to `transfer.yaml` and re-assembling adds new `wl-newone|pkg|iN` keys without disturbing existing ones.
- Replica decrease is refused by default. `--replicas 2` when 3 exist requires `--force-shrink`. Replicas are evidence, not configuration; shrinking creates the inconsistency of "we have i3 results, but i3 isn't in the pair set." Easier to refuse.
- Content changes that would invalidate existing cluster YAMLs (different baselines, different `defaults.disable`) are detected via `params_hash` comparison and refused without `--force`. The user is told to either re-assemble fresh into a new run or accept the clobber.

In one rule: **assemble adds pair keys, never removes them, and never silently rewrites old work.**

## Orchestrate

`deploy.py run --run R` needs three small changes:

1. **`_is_pair_key` and `_load_pairs`** recognize the `|i<N>` suffix and expose the replica index as a field on the pair entry.
2. **PipelineRun name** is derived from the full pair key (`{phase}-{workload}-{run}-i{N}`) so dispatched PRs don't collide on the same cluster.
3. **`resultsDir` substitution** at dispatch time includes the replica index. The PipelineRun gains a `replica` param; `pipeline.yaml` threads it through every task that touches `resultsDir`.

Everything else — slot allocation, capacity gating, GPU cost calculation, progress writes, failure handling, retries, `_reconcile_on_resume` — works unchanged because it operates on pair keys, not on their semantics.

Two consequences fall out:

- **Parallelism is free.** N replicas of one (workload, phase) dispatch into N slots simultaneously (the slot machinery already forbids two PRs in the same namespace). Wall-clock is roughly one PR's time, not N×.
- **Failure is per-replica.** If `wl-foo|pkg|i2` fails while `i1` and `i3` succeed, only `i2` is in the failed state. Retry with `deploy.py reset --only wl-foo|pkg|i2`.

## Observe

`deploy.py status` gets noisier — N× rows per (workload, phase). Two display modes, default to grouped:

```
# Grouped (default)
wl-chat-mid | sim2real-ac           3 done
wl-chat-mid | sim2real-routing      2 done, 1 running (i3 → kalantar-2)

# Detailed (--detailed)
wl-chat-mid | sim2real-ac | i1      done
wl-chat-mid | sim2real-ac | i2      done
wl-chat-mid | sim2real-ac | i3      done
wl-chat-mid | sim2real-routing | i1 done
wl-chat-mid | sim2real-routing | i2 done
wl-chat-mid | sim2real-routing | i3 running → kalantar-2
```

Live TaskRun logs (`tkn pr logs`) are unaffected.

## Collect

PVC layout grows one level:

```
data-pvc:/workspace/data/<runName>/<phase>/<workloadName>/
├── i1/{per_request_lifecycle_metrics.json, epp_logs/, gpu_logs/, server_logs/}
├── i2/...
└── i3/...
```

Local layout mirrors:

```
runs/R/results/<phase>/<workload>/{i1,i2,i3}/...
```

`deploy.py collect` walks pair keys in the ConfigMap and rsyncs per-replica subtrees. Filtering (see next section) lets `collect` target specific replicas.

## Filter by iteration

Today's pair-aware commands filter by `--workload`, `--package`, `--status`, and `--only` (full pair key). Add `--iteration`:

```bash
deploy.py status   --iteration 2                       # show all i2 pair keys
deploy.py status   --iteration 1,2                     # i1 and i2 (list syntax)
deploy.py collect  --iteration 3                       # pull just i3 results
deploy.py run      --iteration 2                       # dispatch only the i2 set
deploy.py run      --workload chat-mid --iteration 1   # AND filter
deploy.py reset    --iteration 3                       # reset all i3 to pending
deploy.py wipe     --iteration 1                       # delete local results for i1
```

Implementation:
- `_apply_run_filters` gains an `iteration` term. Filter test: extract the suffix from the pair key, match against the list.
- Parser adds `--iteration` to every filter-aware subcommand.
- `_parse_list` already handles comma/space-separated values — `--iteration 1,2,3` works automatically.
- `--only wl-foo|pkg|i2` already works (the full pair key); `--iteration` is the convenience for "any workload+package at this iteration."

Naming: "iteration" matches the `iN` suffix. "Replica" is the conceptual term ("how many replicas declared?"). Both terms appear in this doc; the CLI flag is `--iteration` because it reads more naturally next to a number.

## Auto-assemble in deploy.py run

A complement: `deploy.py run` should treat assembly as an auto-fixable precondition, per the [validate/execute pattern](validate-execute-pattern.md).

```
deploy.py run --run R
  validate:
    - runs/R/cluster/ exists?                       → auto-fixable: run assemble
    - manifest.assembly.yaml matches current        → auto-fixable: re-assemble (additive)
      transfer.yaml's params_hash?
    - image present in target registry?             → auto-fixable: run build (or no-op for BYO)
    - cluster reachable?                            → not auto-fixable
  execute:
    - dispatch PipelineRuns
```

Flags:
- `--no-auto`: error on missing prereqs instead of fixing them. For scripted environments that don't want surprise side effects.
- `--replicas N`: shorthand for "auto-execute assemble with this replica count, then run." Equivalent to `sim2real assemble --replicas N && deploy.py run`, but in one invocation.

Auto-execution should announce itself loudly:

```
$ deploy.py run --run R --replicas 5
[info]  Run R: declared replicas 3 → 5; auto-running `sim2real assemble`
[info]  Assemble: added 2 pair keys per workload (replicas i4, i5)
[info]  Run R has 4 pending pairs in 2 slots; starting orchestrator…
```

Silent auto-execution is uneasy; visible auto-execution is cohesive.

## The composed workflow

```bash
# Round 1: try with 1 replica
sim2real assemble --run R --translation T --cluster C --replicas 1
deploy.py run --run R                              # → runs/R/results/.../i1/
deploy.py collect --run R --iteration 1

# Look at i1, decide it's noisy and you want more datapoints.

# Round 2: add two more replicas — assemble auto-runs from deploy
deploy.py run --replicas 3                         # auto-(re)assembles, dispatches i2, i3
deploy.py collect --run R --iteration 2,3          # pull only the new ones

# Analyze across all three
# (/sim2real-analyze aggregates i1, i2, i3 per (phase, workload))
```

Three commands per round, no manual file shuffling, no clobbered results.

## Analyze (the only non-trivial downstream)

`/sim2real-analyze` today reads `runs/R/results/<phase>/<workload>/*.json` and produces per-workload latency tables. With replicas it should:

1. Iterate over `iN/` subdirs per (phase, workload).
2. Aggregate across replicas — at minimum mean and standard deviation of TTFT / TPOT / E2E; ideally percentiles.
3. Render comparison tables with `mean ± std` or `mean [p5–p95]` columns.

Two replica counts that aren't the same matter:

- **Declared**: `manifest.assembly.yaml:replicas`. The intent.
- **Successful**: count of `done` replicas per (workload, phase) in the ConfigMap. The reality.

The skill should report both. A pair with declared=3 and successful=2 is a partial result that the operator may want to retry or accept.

Aggregation could happen in the skill or in a pre-skill helper that writes `runs/R/results/<phase>/<workload>/aggregate.json`. The latter is more reusable and gives operators a CLI-readable summary without going through the skill. Either works; aggregation-in-helper is the simpler shape to start with.

## What changes in each file

- **`pipeline.yaml`** — add a `replica` Pipeline param; thread it into `resultsDir` for `prepare-results-dir`, `stream-epp-logs`, `stream-gpu-stats`, `run-workload-blis-observe-binary`, `collect-results`.
- **`pipeline/lib/tekton.py`** — when generating PipelineRun YAMLs, the template stays one per (workload, phase); the replica index is templated at dispatch.
- **`pipeline/lib/assemble.py`** (or successor) — read `replicas` from the assembly slice; expand the pair-key set; preserve existing entries (additive merge); detect `params_hash` drift and refuse content changes without `--force`.
- **`deploy.py:_is_pair_key`, `_load_pairs`** — parse the `|i<N>` suffix; extract iteration as a field.
- **`deploy.py:_cmd_run`** — pass `replica` as a PipelineRun param at dispatch; auto-execute assemble if precondition fails.
- **`deploy.py:_cmd_collect`** — pull `iN/` subtrees per replica.
- **`deploy.py:_cmd_status`** — grouped / detailed view; iteration in display.
- **`deploy.py:_apply_run_filters`** — add `iteration` filter term.
- **All filter-aware subcommands** — add `--iteration` argparse entry.
- **`/sim2real-analyze`** — replica aggregation; declared-vs-successful reporting.

The changes are spread across many files but each is small. Most of the orchestrator (capacity, slots, failures) is untouched.

## Open questions

1. **Replica counts above ~20.** ConfigMap size grows linearly with pair-key count. For (10 workloads × 2 packages × 20 replicas) = 400 entries, the ConfigMap is still small (< 1 MB), but worth watching. Practical ceiling probably ≥100 replicas without trouble.
2. **Cross-replica failure correlation.** If replicas 1 and 3 succeed but replica 2 fails, is that signal of flaky infrastructure or a real failure mode? Out of scope for the orchestrator; relevant for analyze interpretation.
3. **Replica diversity.** Replicas here are identical configurations. If an operator wants to vary parameters across replicas (different `temperature`, different prompt subsets), that's a different shape — closer to a parameter sweep than to replication. Separate proposal.
4. **Renaming vs. iteration.** Pair-key suffix `|i<N>` matches the term "iteration." If "replica" feels more accurate conceptually (it's a replicate measurement, not an iterative refinement), the suffix could be `|r<N>` instead. Worth deciding once before plumbing the parser.
5. **Decrease semantics.** `--force-shrink` from 3 to 2: delete i3's pair-key entry from the ConfigMap, leave `results/i3/` on disk? Or wipe both? Probably wipe both with `--wipe-shrunk-results` toggle.

## What stays out of scope

- **Cross-translation aggregation** (compare translation A's 3 replicas to translation B's 3 replicas). Two runs' results; the analyze skill's job, not the orchestrator's.
- **Replica diversity / parameter sweep** (varying knobs per replica). See open question 3.
- **Distributed replica execution** across clusters. Two replicas of one (workload, phase) split across clusters: technically possible (two separate runs), but the proposal stays within one run per `(translation, cluster)`.

## Relationship to the other proposals

- **Three-dimensional model**: replicas live below run as a sub-dimension. The three top-level dimensions don't change. `manifest.assembly.yaml` gains a `replicas` field; `run_metadata.json` records the same plus a successful-replica count.
- **Validate/execute pattern**: auto-assemble in `deploy.py run` is a direct application of the pattern. The precondition "run R is assembled and current" is auto-fixable by calling assemble.

This proposal lands cleanly on top of either. It depends on neither: replicas could be added to today's workspace layout, with `assemble` becoming an explicit (non-auto-fixable) precondition. The three-dimensional model + auto-execute makes the resulting workflow much cleaner, but isn't a prerequisite.
