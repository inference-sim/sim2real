# Incremental implementation plan

A complement to [three-dimensional-sim2real.md](three-dimensional-sim2real.md), [validate-execute-pattern.md](validate-execute-pattern.md), and [replicas-as-pair-keys.md](replicas-as-pair-keys.md). The other three describe what the system should look like. This one describes how to get there in steps that each leave a working system behind.

## Why this matters

The three proposals together are a major redesign. A naive layer-by-layer execution — refactor state machines, then refactor assembly, then refactor the orchestrator — breaks the system at every intermediate point and forces a single big-bang merge.

The work happens on a branch with no backward-compatibility requirement. There is no need to keep old behavior alive in parallel. The right shape for the implementation plan is therefore not "preserve everything, change one layer at a time" but **"ship a smaller working system, then enlarge it."** Each step replaces the prior step entirely; no mode flags, no dual-path code.

## The discipline

- **Every step ends with a runnable workflow.** The system at step N can be demoed end-to-end; it just covers fewer use cases than step N+1.
- **Each step is a vertical slice, not a horizontal layer.** Step 1 touches workspace layout, assembly, orchestration, and result collection — but only enough of each to satisfy one use case.
- **No mode flags or "old path / new path" branches.** When a step replaces a feature, the previous version goes away. The branch never has both alive.
- **The smallest first slice is the customer (BYO) flow.** It's deterministic input → deterministic output, with no skill, no orchestrator surgery beyond path renaming, and no replica machinery. It exercises the new workspace layout end-to-end at the smallest possible cost.

## The steps

| # | Step | What works at end of step | Deferred to later |
|---|---|---|---|
| 0 | **Foundation: workspace + cluster provisioning** | `cluster.py provision <id>` creates a cluster's namespaces, RBAC, PVCs, Tekton tasks, and writes `clusters/<id>/cluster_config.json`. Layout helpers know how to read/write `workspace/{state.json, clusters/, translations/, runs/}`. | All operator workflows |
| 1 | **BYO end-to-end (the MVP)** | `sim2real translation register`, `sim2real assemble`, `deploy.py run --run R`, `deploy.py collect --run R`. Hand-rolled `transfer.yaml` and `baseline.yaml` in the experiment repo. `sim2real use --run R`, `sim2real list runs`. A customer can provide an image + config, point at a provisioned cluster, and get results back. | Skill, build, bootstrap, replicas, auto-execute |
| 2 | **Skill-driven translation** | `sim2real translate` (skill-checkpointed), `sim2real build`. `/sim2real-translate` writes to the new paths. Both BYO and skill-driven flows produce identical-shaped translations; everything downstream is unchanged. | Check, bootstrap, replicas, auto-execute |
| 3 | **`sim2real-check` port** | `/sim2real-check --translation T --run R` accepts translation-and-run refs and stitches paths from the three-dimensional workspace (`workspace/translations/<hash>/generated/` and `workspace/runs/<R>/results/`) instead of a single bundle root. Validation subsection structure (workloads, configs, signals, policies, runtime health) is preserved. | Bootstrap, replicas, auto-execute |
| 4 | **Scenario scaffolding** | `/sim2real-bootstrap` (with `--byo` mode) generates `transfer.yaml`, `baseline.yaml`, `baselines/defaults/`. New experiments are turnkey for either flow. | Replicas, auto-execute |
| 5 | **Replicas + iteration filtering** | `--replicas N` at assemble; pair-key suffix `|i<N>`; `--iteration` filter on all filter-aware subcommands; `/sim2real-analyze` aggregates across replicas; additive-merge semantics for re-assemble. The "run 1, decide, run more" workflow works. | Auto-execute |
| 6 | **Validate/execute pattern + auto-fix** | Formal `validate()` + `execute()` split for major commands. `deploy.py run` auto-assembles when needed. `--no-auto`, `--plan`, `--replicas N` shorthand on `deploy.py run`. Cheap upstream steps run by default; heavy ones gated by opt-in flags. | — |

## What each step delivers as a demo

| Step | Operator can do… | Couldn't do at step N-1 |
|---|---|---|
| 0 | Provision a fresh cluster and have it record its own config | Anything operator-facing |
| 1 | Take a pre-built image + a config, point at a cluster, get benchmark results | End-to-end, period |
| 2 | Take an algorithm source, translate it via the skill, deploy and benchmark | Use the skill at all |
| 3 | Run `/sim2real-check --translation T --run R` against a step-2 translation + run and get a full validation report | Validate a translation's real-cluster behavior end-to-end |
| 4 | Start a brand-new experiment from a folder with `algorithms/`, `workloads/`, and a config doc — auto-scaffold the scenario files | Avoid writing `transfer.yaml` by hand |
| 5 | Run N replicas, see variability, decide to add more without manual file shuffling | Statistical confidence without copy-aside |
| 6 | Edit `transfer.yaml`, type `deploy.py run`, have the system catch up automatically | Forget the assemble step without consequence |
| 7 | Run `/sim2real-analyze --run R --analysis <name>` and get a named-standard analysis chart + summary against a step-2 run | Invoke a curated analysis without hand-writing the script every time |

Each row is a coherent end-user demo. Each is a real user need today.

## Why this order

- **Step 0 before anything.** Every later command reads or writes the workspace layout. Get the layout helpers and the `cluster_config.json` schema right once; everything else builds on them. No operator flow yet — this is plumbing.
- **Step 1 (BYO) before step 2 (skill).** The orchestrator and assembly logic don't care which producer wrote the translation. Implementing the BYO producer first exercises the consumer side without dragging in skill complexity. Once `register` + `assemble` + `deploy run` + `collect` work, the skill is just a different producer with the same output shape.
- **Step 3 (check) right after step 2.** `sim2real-check` validates translation outputs. Landing it immediately after the producer step (2) keeps the producer-consumer chain in order and validates step 2's outputs before step 4/5's more invasive changes pile on. It also pre-dates replicas — landing check first means step 5's replica work grows a pre-replica check rather than being replica-aware from birth.
- **Step 3 before step 4.** Bootstrap's job is to scaffold inputs for translate. Don't build the scaffolder before the thing it's scaffolding for works. (Check does not consume bootstrap's outputs, so check comes first.)
- **Step 5 before step 6.** Replicas changes the pair-key schema. Validate/execute is a refactor of how commands compose. Doing the schema change first means the refactor in step 6 is over the final schema, not over a moving target.
- **Step 6 before step 7.** Step 7 refactors the `sim2real-analyze` skill onto the workspace's `sim2real resolve` interface and introduces a standard-analysis library. It's the last skill port and rides on top of every prior step's outputs; landing it after the pipeline commands settle their shapes keeps the analysis library from being rewritten against a moving target.
- **Step 7 last.** This is the second polish step — analysis surface expansion. Doing it earlier would mean rewriting standard analyses each time the underlying phase model changes (steps 3, 5). Let the phase model settle first.

## What "working" means concretely at each step

The bar is "I can run a sequence of commands and get a real result on a real cluster."

- **End of step 0**: `cluster.py provision ocp-east`; then `kubectl get ns,role,rolebinding,pvc -n ocp-east` shows everything provisioned.
- **End of step 1**: BYO demo — image + config + `sim2real assemble` + `deploy.py run --run trial-1` + `deploy.py collect --run trial-1` produces `runs/trial-1/results/baseline/<workload>/per_request_lifecycle_metrics.json`.
- **End of step 2**: skill-driven demo — same as step 1 but the translation comes from `/sim2real-translate` instead of `translation register`. Both runs look identical from assemble onward.
- **End of step 3**: run `/sim2real-check --translation T --run R` against a step-2 translation + run; the skill emits a full validation report (workloads, configs, signals, policies, runtime health) with PASS/FAIL/SKIP verdicts per subsection.
- **End of step 4**: start from a BLIS-output folder, run `/sim2real-bootstrap`, then proceed with step 2's flow without hand-writing `transfer.yaml`.
- **End of step 5**: `sim2real assemble --replicas 3` then `deploy.py run` produces three independent results subtrees per (workload, phase). Re-assemble with `--replicas 5`, run again, get two more.
- **End of step 6**: edit `transfer.yaml`, type `deploy.py run --run R`, watch it auto-assemble and dispatch in one breath.
- **End of step 7**: `/sim2real-analyze --run R --analysis latency-table` prints an N-phase comparison table (baseline + each algorithm); `/sim2real-analyze --run R --analysis ttft-cdf` writes a per-workload CDF chart across all collected phases to `runs/R/results_charts/`.

## Step-by-step scope guidance

### Step 0 — Foundation

**Do**:
- Decide and document the JSON schemas: `cluster_config.json`, `state.json`, `translation_output.json`, `run_metadata.json`, `manifest.assembly.yaml`.
- Write `cluster.py provision` doing the same cluster-side work today's `setup.py` does (namespace, RBAC, secrets, PVCs, Tekton). Idempotent, no operator-side artifacts.
- Write layout helpers (`workspace_dir_for_cluster`, `translation_dir`, `run_dir`, etc.) in one module so subsequent steps don't reinvent path logic.

**Don't**:
- Don't write `assemble`, `translate`, or any operator-flow commands. Resist.
- Don't port `setup_config.json` semantics — that file goes away.

### Step 1 — BYO MVP

**Do**:
- `translation register` (small, ~100 lines).
- `assemble` consuming a hand-rolled experiment repo (no bootstrap yet).
- Orchestrator path: copy the relevant parts of today's `deploy.py:_cmd_run`, change progress lookups to use the new ConfigMap name and run-dir path. Keep the orchestrator's slot/capacity/failure logic intact — that's not the slice you're improving here.
- `collect`, `use`, `list runs`.

**Don't**:
- Don't generalize for replicas. Single pair key per (workload, phase) — same shape as today.
- Don't add auto-fix for missing prereqs. If `runs/R/cluster/` doesn't exist, error with "run `sim2real assemble`."
- Don't try to clean up `deploy.py` orchestrator code as you go. The code earns its shape over later steps.

### Step 2 — Skill-driven translation

**Do**:
- `translate` reading from `transfer.yaml`'s translation slice, writing `skill_input.json` and exiting at the checkpoint. Resume reads `translations/<hash>/generated/<algo>/<algo>_output.json` for completeness.
- Update `/sim2real-translate` skill prompt to read/write the new paths.
- `build` — copy logic from today's `_cmd_build`, image tag is `translation_hash[:12]`.

**Don't**:
- Don't introduce `--auto-translate`-style auto-fix. That's step 6.
- Don't merge or replace BYO `register` with `translate` — they're peer producers and stay separate commands.

### Step 3 — `sim2real-check` port

**Do**:
- Port `.claude/skills/sim2real-check/SKILL.md` to accept `--translation T --run R`. Rewrite path derivation to compose the "real bundle" view from the three-dimensional workspace at skill-invocation time: config paths from `workspace/translations/<hash>/generated/`, result paths from `workspace/runs/<R>/results/<phase>/<workload>/`.
- Preserve the skill's validation subsection structure (workloads, configs, signals, policies, runtime health) — that's the value proposition.
- Accept the same translation-ref resolution rules as `sim2real build` / `sim2real assemble` (name, prefix, or full hash).

**Don't**:
- Don't add replica awareness. That's step 5's territory. Step 3's check operates on the single-replica shape step-1/2 produced.
- Don't rewrite validation semantics. Only the input model changes.
- Don't fold in `sim2real-bootstrap` skill work. Bootstrap is step 4.

### Step 4 — Scenario scaffolding

**Do**:
- Port `/sim2real-bootstrap` to the new layout. The skill itself stays largely the same; what changes is where its output lands and what slices the resulting `transfer.yaml` is shaped against.
- Add `--byo` mode: skip component submodule derivation; prompt-or-stub baseline scenario fields; ask for the algorithm names the user will register later.

**Don't**:
- Don't ship a workload library yet unless one is clearly demanded. Customers can hand-provide workloads for now.

### Step 5 — Replicas + iteration

**Do**:
- Pair-key suffix support across `_is_pair_key`, `_load_pairs`, status formatting.
- `replicas` field in `manifest.assembly.yaml`. Assemble does additive merge.
- `replica` PipelineRun param; thread it into `pipeline.yaml` `resultsDir` substitutions.
- `--iteration` filter on `run`, `status`, `collect`, `reset`, `wipe`.
- `/sim2real-analyze` aggregation across replicas.
- Extend `sim2real-check` (from step 3) to walk `iN/` subdirs and report per-iteration verdicts.

**Don't**:
- Don't auto-spawn replicas without explicit `--replicas N`. That's step 6's territory (`deploy.py run --replicas N` shorthand).
- Don't allow replica decrease without `--force-shrink`. Keep the monotonic invariant.

### Step 6 — Validate/execute + auto-fix

**Do**:
- `validate()` + `execute()` split for `assemble`, `deploy.py run`, `build`.
- `--plan` mode that runs only `validate()`.
- `--no-auto` flag that disables auto-fix.
- `deploy.py run --replicas N` shorthand for "auto-execute assemble with N, then run."
- Visible auto-execution: print what's being chained.

**Don't**:
- Don't auto-execute `translate` by default. It's expensive and operator-surprising.
- Don't go back and refactor early commands to also use the formal pattern unless it's a small change. The pattern can spread incrementally over time.

### Step 7 — `sim2real-analyze` port + standard analysis library

**Do**:
- Port `.claude/skills/sim2real-analyze/SKILL.md` to the three-dimensional workspace via the `sim2real resolve --run R` interface introduced in step 3. Retain the `--run NAME` argument (already present); replace the hardcoded `phase ∈ {baseline, treatment}` scan with dynamic enumeration from resolve's `phases_with_data` and `workloads_by_phase` fields.
- Rewrite `compute_table.py` (the single baked-in analysis) to iterate the resolved phase list; report per-workload metrics for every collected package, not just a baseline-vs-treatment binary. Multi-algorithm and multi-baseline manifests render as N-column comparisons.
- Adapt the SKILL.md's example analysis scripts (TTFT distribution, throughput over time, etc.) to the new phase model.
- Introduce a `.claude/skills/sim2real-analyze/analyses/` directory containing a small library of ready-to-run standard analyses invocable by name from the skill (e.g., `/sim2real-analyze --run R --analysis ttft-cdf`). Analyses land as small Python scripts with a shared runtime shim (input: resolved-run JSON + output-dir; output: chart PNG + JSON summary).

**Don't**:
- Don't rewrite analysis semantics — TTFT / TPOT / E2E metric definitions stay verbatim; only the phase-iteration and I/O shell change.
- Don't add cross-run comparison in this step. The skill's interactive loop can still handle it (as it does today), but no standard analysis in the new library takes multiple runs as input. Cross-run is future work.
- Don't fold in `sim2real-check` work. That's step 3.

## Risks and what to watch for

- **Step 1 scope creep.** "While I'm here, let me also add filtering / better error messages / a richer status command." Resist. The smallest viable BYO flow is the deliverable.
- **Step 1 orchestrator regression.** `deploy.py:_cmd_run` is the most complex code in the tree (~400 lines). At step 1, copy-and-adapt; do not rewrite. Rewrites earn their shape across steps 5 and 6.
- **Step 5 PipelineRun param threading.** The `replica` param must flow through every task that touches `resultsDir`. Verify with a single replica end-to-end before going parallel.
- **Step 6 surprise auto-execution.** Operators tolerate auto-fix only when it's visible. Print every chained step before executing it.
- **Long-tail places where `setup_config.json` is read.** `setup_config.json` goes away in step 0; later steps will discover places where code still expects it. Grep for `setup_config.json` early in each step's testing.

## Out of scope for this plan

- **Cross-translation aggregation** in analyze. The model supports it; tooling is a later concern.
- **Migration scripts for existing workspaces.** Per the user's note, the branch reimplements without backward compat. Existing workspaces are abandoned, not migrated.
- **CI / test infrastructure.** Each step needs its own tests, but the test strategy isn't in this plan. Test pyramid is unchanged from today.

## Relationship to the other proposals

- **[three-dimensional-sim2real.md](three-dimensional-sim2real.md)** establishes the final shape. Step 0 implements its workspace layout. Steps 1-4 fill in the producers and consumers. Steps 5-6 add the sub-run dimension and the composition discipline.
- **[validate-execute-pattern.md](validate-execute-pattern.md)** is implemented in step 6. Earlier steps benefit from the pattern's discipline (each command has a precondition check), but the formalization waits for the schemas to stabilize.
- **[replicas-as-pair-keys.md](replicas-as-pair-keys.md)** is implemented in step 5 with one piece (the `--replicas N` shorthand on `deploy.py run`) landing in step 6.

All four proposals are internally consistent. This plan is the order in which to make them real.
