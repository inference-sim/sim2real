# Incremental implementation plan — addendum

A companion to [incremental-implementation-plan.md](incremental-implementation-plan.md). The plan is internally consistent and the trajectory closes on the desired end state. This addendum surfaces what the plan understates: per-step scope realities (especially when measured against the current code), the open questions across all four proposals that need answers at each step, work that isn't assigned to any step but is needed for the refactor to be "done," and the inter-step interactions that show up only when you read the whole arc.

This is not a counter-proposal. It is a list of things the plan should not silently inherit.

---

## How to use this document

When `design-epic` runs against a phase, this addendum's section for that step is the first input. Every "open question resolving here" must be either resolved in the design doc or explicitly deferred with a reason.

The Out-of-band section is a separate intake: items that need scheduling outside the six steps.

The Open question index at the end is the master tracker — every question has a step or "out of band" assignment.

---

## Per-step open questions and scope realities

### Step 0 — Foundation

**Plan says**: "no operator flow yet — this is plumbing." Decide and document five JSON schemas (`cluster_config.json`, `state.json`, `translation_output.json`, `run_metadata.json`, `manifest.assembly.yaml`). Write `cluster.py provision`. Write layout helpers.

**Scope reality**:

- **`setup_config.json` retirement is the dominant work.** The file is referenced **282 times across 17 files** — production code (`pipeline/deploy.py`, `prepare.py`, `setup.py`, `run.py`, `lib/remote.py`, `lib/run_manager.py`), every pipeline test file, two skills (`sim2real-translate`, `sim2real-analyze`), `CLAUDE.md`, and `pipeline/README.md`. The plan addresses this with a single bullet in "Risks": *"Long-tail places where `setup_config.json` is read."* In practice Step 0 either:
  - Ports every callsite (mechanical but slow — likely the biggest single time sink in Step 0), or
  - Keeps a compatibility shim that reads `setup_config.json` and rewrites to the new layout (violates "no mode flags / no dual paths" and creates two sources of truth for workspace state).
  
  Picking the port keeps the discipline. Pricing it as several days of mechanical work is honest.

- **`transfer.yaml` schema is missing from Step 0's deliverables.** The five schemas Step 0 names cover the new internal artifacts but skip the user-facing manifest. The translation-slice / assembly-slice distinction (which is what enables "vary config, keep translation") lives entirely in `transfer.yaml`'s shape. Skipping it here means Steps 1–4 inherit unresolved field definitions and the schema churns under them. Step 0 should explicitly add `transfer.yaml` v4 schema (or whatever version designation makes sense) to its deliverables.

- **The layout-helpers module name matters because every later step imports it.** `pipeline/lib/layout.py` or `pipeline/lib/workspace.py` — pick once. Downstream commands' import paths bake in the choice.

- **`cluster.py provision` is a carve-out from `setup.py`, not a clean copy.** Today's `setup.py` does cluster-side work (namespaces, RBAC, PVCs, Tekton) AND operator-side work (writing `setup_config.json`, prompting for registry credentials, recording run metadata). The new design wants only the cluster-side bits. Determining the carve-out line isn't mechanical — `setup.py` mixes the two.

**Open questions resolving in Step 0**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 1 | `setup_config.json` retirement strategy | this addendum | The file's existence is what makes Step 0's layout module load-bearing; the retirement plan must be the first thing decided |
| 2 | `transfer.yaml` v-next schema | this addendum | Front-door manifest; schema must be pinned before any consumer is written |
| 3 | Pair-key suffix: `\|iN` vs `\|rN` | replicas §o.q.4 | `manifest.assembly.yaml`'s `replicas` field is a Step 0 schema deliverable; the suffix choice goes with it |
| 4 | Image-tag mutability enforcement for BYO | BYO appendix §o.q.2 | `registered.json` schema decision: digest recorded only, or also locked at deploy time |
| 5 | Run-name collision handling | 3D §o.q.2 | `run_metadata.json` schema must declare whether `run_name` collisions refuse or auto-suffix |
| 6 | State-machine semantics in the new layout | validate/execute §o.q.1 | `state.json` is in the schema list, but the new layout has many top-level commands rather than one linear pipeline; the file's contents need definition |

**Risks specific to Step 0**:

- The CI block in `CLAUDE.md` and `.github/workflows/test.yml` hardcodes today's paths (`pipeline/`, `.claude/skills/sim2real-*/tests/`). New modules (cluster, layout) need CI inclusion. The plan claims test infrastructure is unchanged — this is false in practice.
- `setup.py` writes operator-side state today. Removing `setup_config.json` while keeping `setup.py` (for the cluster-side bits later renamed to `cluster.py provision`) creates a transient half-step where `setup.py` exists but produces no operator state. Plan the carve-out as a single change, not a two-step.
- Schema decisions made here propagate forward without renegotiation. Step 0's design-epic deserves disproportionate time precisely because the cost of revisiting a Step 0 schema in Step 3 is high.

**Out-of-plan work attached to Step 0**:

- Tests for the new layout module (no test file exists today).
- CI workflow update (`.github/workflows/test.yml`).
- Removal or migration of `setup_config.json`-only tests.
- `transfer.yaml` schema documentation (new section in `pipeline/README.md` or similar).
- `cluster_config.json` example file for new cluster onboarding.

---

### Step 1 — BYO MVP

**Plan says**: `translation register`, `assemble`, orchestrator copy-adapt (~400 lines), `collect`, `use`, `list runs`. "The customer flow uses the same commands as the standard flow, with one step replaced and one step gaining a mode."

**Scope reality**:

- **`_cmd_run` is measured at 443 lines.** The plan estimates ~400; reality is close. Copy-adapt of a function this size is the largest single piece of work in Step 1. The plan correctly says "do not rewrite" — but every grep against the original `_cmd_run` going forward sees two definitions. **Decide explicitly**: delete the original `_cmd_run` immediately (cleaner, but accepts that today's main is unreachable from this branch even mid-step) vs. leave it until Step 5 (matches the discipline; tolerates a 443-line dead clone in the tree).

- **Six new commands at once.** `translation register`, `assemble`, `deploy.py run` (the ported version), `collect`, `use`, `list runs`. Each has argparse + validation + error paths. Each is individually small but the combined surface is real.

- **`assemble` is a rewrite, not a port.** Today's `pipeline/lib/assemble.py` is 247 lines and consumes the v3 transfer.yaml shape. Step 1's `assemble` consumes the new translation-slice / assembly-slice shape from Step 0's schema. The deep-merge primitives in `pipeline/lib/values.py` can transfer; the bundle/overlay structure changes.

- **Today's `_is_pair_key` is `return not key.startswith("_")`.** Literally no structured parse. The plan's "copy and adapt" of orchestrator code carries this forward unchanged. Step 4 will replace it with a structured parser. **Decision point in Step 1**: introduce the structured parser early (so Step 4 is smaller) or defer to Step 4 (matches plan but means Step 1's adapted code uses the trivial parser and Step 4 changes many callsites).

- **Step 1's demo needs an EPP image source.** Step 1 demos BYO — customer provides an image. For development/testing of Step 1, where does the image come from? Step 2 (which introduces `build`) hasn't happened yet. Options: (a) use today's build mechanics outside the new flow during Step 1 development; (b) hand-build an image; (c) reuse an image left over from current main. Plan doesn't say. **This is real** — without an image, Step 1's end-to-end demo isn't end-to-end.

**Open questions resolving in Step 1**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 7 | Test/demo image strategy | this addendum | Step 1 demos against an image; no upstream step produces one |
| 8 | Original `_cmd_run`'s fate (delete now or end-of-refactor) | this addendum | Step 1 creates a 443-line clone; whether the original lives is a maintainability decision that affects every later step |
| 9 | `assemble --run R` re-assemble semantics | 3D §o.q.2 (partial) | What happens when R/cluster/ already exists: clobber / refuse / auto-suffix |
| 10 | `list runs` ordering/filtering shape | this addendum | Recency / alphabetical / by-status — a small API call but visible to operators |
| 11 | Introduce structured pair-key parser now or in Step 4 | this addendum | Either pays the cost in Step 1 (small Step 4) or Step 4 (small Step 1) |

**Risks specific to Step 1**:

- The orchestrator copy-adapt is a place where subtle bugs from path-rewrites silently break the new flow. Mitigation: test the adapted `_cmd_run` against a real cluster before moving on; don't ship Step 1 on simulated runs alone.
- `assemble`'s new shape and Step 0's schema are an integration point. If Step 0 finalized the schema before testing it against a real consumer, Step 1's `assemble` is the first reality check. Be prepared to amend Step 0's schema if Step 1 surfaces a gap.
- The demo experiment repo. `admission-control` and other existing experiment repos have v3 `transfer.yaml`. Step 1 needs a new-schema demo repo — either a fresh one or a migrated copy of `admission-control`. Plan doesn't say.

**Out-of-plan work attached to Step 1**:

- Decision on the original `_cmd_run` (delete vs leave).
- Image source for Step 1 demo.
- A demo experiment repo in the new schema.
- Tests for `translation register`, `assemble`, `use`, `list runs`.
- Schema reality-check loop with Step 0.

---

### Step 2 — Skill-driven translation

**Plan says**: `sim2real translate` (skill-checkpointed). `sim2real build`. Update `/sim2real-translate` skill prompt for new paths. "Don't introduce `--auto-translate`-style auto-fix. That's step 5."

**Scope reality**:

- **The skill prompt update isn't isolated to `sim2real-translate`.** Measured: **31 workspace-path references across the four sim2real skill MD files** (`sim2real-translate`, `sim2real-bootstrap`, `sim2real-analyze`, `sim2real-check`). The plan flags `sim2real-translate` only. `sim2real-bootstrap` rightly waits for Step 3 (since it's the focus there), and `sim2real-analyze` rightly waits for Step 4 (since it grows replica logic there). But `sim2real-check` has no step assignment and references the old paths. **Decision**: update `sim2real-check` here as part of Step 2's skill work, or note it as out-of-band.

- **`build`'s image tag derivation doesn't include build environment.** Plan: `image_tag = translation_hash[:12]`. Hash is over algorithm sources + translation slice. **Not** in the hash: Go toolchain version, base image, build flags. An image at `<hash[:12]>` rebuilt under a different toolchain produces a different binary at the same tag. Step 2 needs to pick:
  - Re-build only if image missing (plan's apparent default — accept stale-on-toolchain-change footgun).
  - Re-build if any input changed including build environment (safer, more work to detect).
  - Lock the build environment via the registry tag's manifest digest (re-build only if digest mismatch).

- **`translate`'s slice extractor isn't designed.** `translate` reads transfer.yaml's translation slice. The slice definition is in 3D §"Proposed workspace layout": `scenario`, `component`, `context`, `algorithms[i].source`, `algorithms[i].config`. The parser/slicer is implementation — likely an extension to `pipeline/lib/manifest.py`. Plan doesn't say where the slice extractor lives. Affects Step 0's reality (the schema needs to declare which fields are which slice).

- **Registry-auth in `build` isn't specified.** Today's setup uses `REGISTRY_USER` / `REGISTRY_TOKEN` (quay.io). 3D says "Per-cluster registries... assume one public registry" — i.e., no auth needed for v1. For users running against private registries (most current users), this is a regression. Step 2 must either preserve the existing auth path (good) or break it explicitly (regression). Plan is silent.

**Open questions resolving in Step 2**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 12 | `translation_hash` inputs (toolchain, submodule pin) | this addendum | The hash determines build determinism; defining it without knowing what should and shouldn't invalidate it leaks bugs |
| 13 | Registry-auth strategy for `build` | this addendum / 3D deferred | Existing users depend on REGISTRY_TOKEN; "one public registry" is a regression unless explicit |
| 14 | Slice extractor location | this addendum | Affects manifest module API and Step 0 schema definitiveness |
| 15 | `build` skip-when-present vs re-build policy | this addendum | Idempotent-on-hash means stale-on-toolchain-change; pick |

Also affected (already resolved by step shapes, but worth noting):

| 16 | Build placement (standalone vs lazy) | 3D §o.q.1 | Step 2 makes it standalone; Step 5 layers auto-fix from `deploy.py run` |

**Risks specific to Step 2**:

- Skill prompt updates are mostly mechanical but the skill's tests at `.claude/skills/sim2real-translate/tests/` use today's path conventions. CI references these tests. The tests update with the skill.
- The translation slice extractor and Step 0's schema must agree exactly. Discrepancies surface only when `translate` runs against a real `transfer.yaml`.
- `build` working against an empty/clean cluster differs from `build` against a registry already holding a same-tag image. Test both.

**Out-of-plan work attached to Step 2**:

- `sim2real-check` skill path update (or explicit deferral with note).
- Registry-auth path decision documented in `cluster_config.json` schema if needed.
- Tests for `translate` and `build`.
- Build-environment determinism note in `translation_hash` documentation.

---

### Step 3 — `sim2real-check` port

**Plan says**: Port `.claude/skills/sim2real-check/SKILL.md` to accept `--translation T --run R`. Recompose the "real bundle" view from `workspace/translations/<hash>/generated/` + `workspace/runs/<R>/results/<phase>/<workload>/`. Preserve validation subsection structure. Don't add replica awareness (step 5). Don't fold in bootstrap (step 4).

**Scope reality**:

- **The port isn't mechanical find-replace.** The check skill's mental model today is "one bundle root with `generated/` + `baseline/`/`treatment/` as siblings, all under one directory the user picks." Step 2's outputs live under `workspace/translations/<hash>/generated/`; step 1's collected results live under `workspace/runs/<R>/results/<phase>/<workload>/`. The two roots are decoupled by design (that's the point of the three-dimensional split). The check skill's derivation logic has to compose paths from both at invocation time.

- **The skill uses `AskUserQuestion` today for path confirmation.** Post-port, `--translation T --run R` are inputs; the skill can still confirm-and-adjust but the default is derived from the args. The confirmation prompt becomes a diagnostic rather than the primary input path — flag both cases in the SKILL.md.

- **`sim2real list translations` becomes more useful once check accepts translation refs.** 3D lists it in the command table but no step ships it. Attach as a small deliverable to step 3 (~30 lines) or leave for a follow-up. If step 3 doesn't ship it, operators discover translations via `ls workspace/translations/` — workable but rough.

- **Translation-ref resolution shape must match `build` and `assemble`.** Step 2 lands the resolver (name / prefix / hash). Step 3 reuses it. If step 2's resolver lives in `sim2real.py`, step 3's skill invocation path needs to call into the same code — either via a Python entry point the skill shells out to, or by embedding the resolution rules in the skill's argument-preprocess step.

- **What check reads vs. writes.** Read-only. No new workspace state. Reports go to stdout or a caller-specified path. This makes the port lower-risk than the other skill ports (translate, bootstrap, analyze).

**Open questions resolving in Step 3**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 36 | Translation-ref resolver shape shared with step 2 | this addendum | Skill must resolve names/prefixes/hashes; either shells out to Python or embeds the rule |
| 37 | Handling multi-algorithm translations under `--translation T` | this addendum | Check all algorithms in T against run R, or accept `--algorithm A` to narrow? |
| 38 | Legacy manually-picked-bundle-root mode: keep or drop? | this addendum | Existing users may still have pre-refactor bundles; supporting both modes vs. clean break |
| 39 | Ship `sim2real list translations` here or defer | this addendum / 3D command table | Small delta; useful for discovery once names are aliases |

**Risks specific to Step 3**:

- The port's biggest risk is silent drift between where step 2 writes and where step 3 reads. Every path-composing rule step 3 encodes has to match step 2's writes exactly. Add an integration test that runs step 2's `assemble` end-to-end + step 3's check on the result.
- If `sim2real list translations` is deferred, operators debugging a check failure ("which translation did I mean?") have no CLI tool to enumerate their translations. Manageable but rough.
- The skill's existing `AskUserQuestion`-based prompt UX is well-established. Changing it without breaking user muscle memory means preserving the confirmation prompt even when args are supplied — treat args as defaults, not overrides.

**Out-of-plan work attached to Step 3**:

- Skill's existing tests (if any) rewritten for the new input model.
- Documentation update in the skill's README describing the `--translation`/`--run` inputs.
- Optional: `sim2real list translations` command (~30 lines in `sim2real.py`).
- Integration test: step 2 assemble + step 3 check against the assembled run.

---

### Step 4 — Scenario scaffolding

**Plan says**: Port `/sim2real-bootstrap` to new layout. Add `--byo` mode. "Don't ship a workload library yet unless one is clearly demanded."

**Scope reality**:

- **`/sim2real-bootstrap` has substantial code, not just a SKILL.md.** Under `.claude/skills/sim2real-bootstrap/`: `generate_from_config.py`, `generate_scenarios.py`, `templates/`, `tests/`. The port affects all of these. Path mappings: where the skill reads experiment-folder inputs (mostly unchanged — experiment folder shape is operator-side input) and where it writes outputs (changes to the new workspace layout — but bootstrap writes to the *experiment repo*, not `workspace/`, so the destination changes less than expected). Re-read the bootstrap code carefully to identify what actually moves.

- **`--byo` mode is largely new.** Today's bootstrap derives most fields from BLIS-format inputs (algorithms/*.go, config.md, top3_selection.json). BYO has none of those. The BYO appendix's three-priority handling (read what's there → prompt interactively → stub with TODOs) is a different shape than file-derivation. Interactive prompting from a skill is implementable but the prompt UX needs design.

- **Workload library is resolved (not yet, user-provided).** Plan: "Don't ship a workload library yet." `--byo` mode therefore points the user at their own `workloads/` directory or refuses if absent.

**Open questions resolving in Step 4**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 17 | `--byo` interactive vs stub-only ratio | BYO appendix §"missing pieces" | Plan says "both"; the ratio is a UX decision |
| 18 | `config.md` / BLIS-format vs free-form input tolerance | BYO appendix | "Read what's there" implies a tolerant parser; how tolerant? |
| 19 | Optional `component:` in `transfer.yaml` | BYO appendix | `--byo` produces transfer.yaml without `component:` or with `byo: true`; Step 0's schema must accommodate |

Resolved by plan / accepted:

| 20 | Workload library shipping | BYO appendix §o.q.1 | Not yet; user-provided. Plan resolves. |

**Risks specific to Step 4**:

- Schema implication: `transfer.yaml` schema (a Step 0 deliverable) must declare `component:` as optional. If Step 0 made it required, Step 4 forces a Step 0 schema amendment. Move this constraint to Step 0's design-epic explicitly.
- Bootstrap's existing tests use today's experiment-folder shape and produce today's `transfer.yaml`. Both inputs and outputs change. Test rewrite is non-trivial.
- The skill's templates directory holds `defaults/*.yaml` framework workaround fragments. These follow the experiment repo's `baselines/defaults/` shape. Confirm whether the shape changes (probably not, but verify).

**Out-of-plan work attached to Step 4**:

- Bootstrap skill's README update (it has its own).
- Tests for `--byo` mode (new test surface).
- Decision on prompt UX shape for `--byo` interactive prompts.

---

### Step 5 — Replicas + iteration filtering

**Plan says**: Pair-key suffix support across `_is_pair_key`, `_load_pairs`, status formatting. `replicas` field in `manifest.assembly.yaml`. `replica` PipelineRun param threaded through `pipeline.yaml`. `--iteration` filter on all filter-aware subcommands. `/sim2real-analyze` aggregation. "Verify with a single replica end-to-end before going parallel."

**Scope reality**:

- **The pair-key parser change is larger than implied.** Today's `_is_pair_key` is one line — *just* a metadata-exclusion check, no structured parse. Step 5 introduces a 3-segment key (`wl-<workload>|<package>|i<N>`). Adding the suffix means the parser goes from "is this metadata?" to "parse this into (workload, package, iteration)" — a structural change that touches every callsite that reads pair-key fields. **Measured: `_is_pair_key` and `_load_pairs` together appear ~20 times in `deploy.py`.** Every one of those callsites is a review point.

- **`pipeline.yaml` param threading is silent-on-error.** The replicas proposal says: thread `replica` through every task that touches `resultsDir`. Reading current `pipeline.yaml`: it has ~20 params already, and several tasks use `resultsDir`-shaped paths. Forgetting one task = silently wrong directory at runtime, only caught when results land in the wrong place. **The plan flags this** ("verify with a single replica before going parallel") but it deserves a dedicated checklist: list every task in `pipeline.yaml` that references results, confirm each one gets the new param.

- **Additive merge for `assemble`.** Step 1's `assemble` was single-replica (per the plan: "Don't generalize for replicas. Single pair key per (workload, phase)"). Step 5 grows it to support `--replicas N` re-runs with the additive-merge invariant. This is a real API change to a deliverable from three steps prior. Tests for additive-merge edge cases (replicas 3→5: preserve i1..i3, add i4..i5; replicas 3→1: refuse without `--force-shrink`) didn't exist in Step 1 and need to land here.

- **`/sim2real-analyze` aggregation across replicas.** Today the skill reads single per-(phase, workload) directories. New shape: walks `iN/` subdirs and aggregates (mean ± std, percentiles). The replicas proposal suggests an `aggregate.json` pre-skill helper as the simpler shape — recommended. The aggregation math itself is straightforward but the skill's prompt needs significant update.

- **Coexistence problem.** During Step 5 development, runs from Step 1–4 demos lack the `iN/` subdir layout. If they coexist in `runs/<run>/results/`, the analyzer either has to handle both shapes (Step 1–4 demo runs continue to work) or the Step 5 changes invalidate prior-step demo outputs (clean break, simpler analyzer). Pick.

**Open questions resolving in Step 5**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 21 | Replica decrease semantics (`--force-shrink`: wipe results too?) | replicas §o.q.5 | First step where `--force-shrink` matters |
| 22 | Old-shape vs new-shape result coexistence | this addendum | Prior-step demo outputs predate `iN/` subdirs |
| 23 | `aggregate.json` helper vs in-skill aggregation | replicas §"Analyze" | Implementation shape decision |
| 24 | `--iteration` filter parser shape (list vs range) | replicas §"Filter by iteration" | `--iteration 1,2,3` works via `_parse_list`; ranges (`1-5`) not designed |

Informational, not blocking:

| 25 | Practical replica-count ceiling | replicas §o.q.1 | ConfigMap size for high N; informational |

**Risks specific to Step 5**:

- Pipeline.yaml param threading is the single highest-risk piece of Step 5. Build a checklist; don't trust grep alone.
- `_is_pair_key` semantics change ripple. Today many tests assert `_is_pair_key("_some_metadata") == False`. The new parser must preserve metadata-exclusion AND add structured parsing. Both behaviors tested.
- Replica index in PipelineRun names: `{phase}-{workload}-{run}-i{N}`. Long workload/run names + i{N} suffix can exceed k8s DNS-label limits (253 chars). Step 5 should add a length validator at name-construction time, not at dispatch time.

**Out-of-plan work attached to Step 5**:

- PipelineRun name length validator.
- `aggregate.json` helper (if that path is chosen).
- Pipeline.yaml param-threading checklist as a docs artifact.
- Test fixture rewrites for the new pair-key parser.
- Documentation update for `--iteration` filter usage.

---

### Step 6 — Validate/execute + auto-fix

**Plan says**: `validate()` + `execute()` split for `assemble`, `deploy.py run`, `build`. `--plan` mode. `--no-auto` flag. `--replicas N` shorthand on `deploy.py run`. "Visible auto-execution." "Don't go back and refactor early commands... unless it's a small change."

**Scope reality**:

- **Step 6 is restructure, not polish.** Each affected command grows a `validate()` returning a list of preconditions and an `execute()` doing the work. The dispatcher is new code (~20 lines per the proposal, but realistically more once argparse integration, error formatting, and dependency tracking land). Auto-fix recursion logic + depth-limit decisions are non-trivial.

- **`deploy.py run`'s behavior changes between Step 1 and Step 6.** Step 1: errors if prereqs missing ("error with 'run sim2real assemble.'"). Step 6: auto-fixes by calling assemble. Same command, two different shapes across the refactor. This is documented in the validate/execute proposal but the plan does not surface it as "a previously-shipped command's behavior changes."

- **Coverage is partial.** Plan applies the pattern to three commands: `assemble`, `deploy.py run`, `build`. The validate/execute proposal lists six: `cluster.py provision`, `sim2real translate`, `sim2real build`, `sim2real assemble`, `deploy.py run`, `deploy.py collect`. The plan's "the pattern can spread incrementally" suggests it's OK for some commands to skip — but the asymmetry should be intentional, not silent. Either explicitly mark which commands keep their current shape or fold the missing three in.

- **`--replicas N` shorthand on `deploy.py run` is exactly the auto-execute footgun the proposal flags.** It chains assemble (potentially altering the pair-key set) + run (dispatching against the new set). The proposal says: "always print 'auto-executing: build, assemble, run' before starting." Step 6 should pin this as the dispatcher's contract, not a developer's discretion.

**Open questions resolving in Step 6**:

| # | Question | Source | Why it lands here |
|---|---|---|---|
| 26 | Auto-fix DAG depth limit | validate/execute §o.q.2 | `run → assemble → build → translate` is three hops; cap at one? two? unlimited with prompt? |
| 27 | `--plan` output format (text vs JSON vs both) | validate/execute §o.q.3 | Plain text default + `--plan --json` for tooling probably right |
| 28 | Validate/execute coverage (3 commands vs 6) | this addendum | Decide explicitly which commands keep informal preconditions |
| 29 | Auto-execute announcement format | validate/execute §"What this costs" | "auto-executing: build, assemble, run" — pin the format |

Carries forward from Step 0:

| 30 | State-machine skip's relationship to `validate()` | validate/execute §o.q.1 | Step 0 defined `state.json`; Step 6 either reaffirms or refines |

**Risks specific to Step 6**:

- The dispatcher is new code that every major command depends on. Bugs here cascade. Tests for the dispatcher itself (not just per-command validate/execute) are essential.
- "Visible auto-execution" risks becoming "noisy auto-execution" if every cheap precondition prints. Tune for signal — don't announce no-op auto-fixes.
- `--no-auto` is for scripted environments (CI). Step 6 should add a test mode that asserts `--no-auto` produces the same error message that Step 1's `deploy.py run` produced on missing prereqs — i.e., the failure-mode contract is stable across the refactor.

**Out-of-plan work attached to Step 6**:

- The dispatcher module (new code, with tests).
- Decision on whether `translate` and `collect` get the pattern here or remain informal.
- Tests for auto-fix chains (the DAG walker).
- Decision on `--plan --json` shape if structured output is in scope.
- A "Step 6.5: orchestrator cleanup pass" — explicitly accept (no cleanup, per plan discipline) or schedule (acknowledge the cumulative Step 1/5/6 modifications to `_cmd_run` left it messy).

---

## Out of band

Work required to declare the refactor "done" but not assigned to any step in the plan.

### Experiment-repo migration

Existing experiment repos (admission-control, others currently in use) have v3 `transfer.yaml`. The new design requires the translation-slice / assembly-slice format. None of Steps 0–6 says "and update admission-control's transfer.yaml."

This work has to happen:

- **Pre-Step-1** if Step 1 is to demo against real data (recommended — surfaces schema issues early).
- **Pre-merge-to-main** if it can wait, but if `refactor/v2` ships without migrated experiment repos, post-refactor `main` is unusable for current users until migration lands.

Recommendation: file a parallel issue ("migrate admission-control to v4 transfer.yaml") gated on Step 0's schema lock. Run it during Step 1's development as the schema reality check.

### Per-cluster registries (3D-deferred, currently in production)

`REGISTRY_USER` / `REGISTRY_TOKEN` is used for private quay.io registries. 3D §"Intentionally deferred for v1" says "Assume one public registry." This is a regression for current users.

Two paths:

- Preserve the existing auth path in `build` and the cluster's image-pull-secret in Step 2 — small extension to the plan, not a real deferral.
- Defer explicitly and gate `refactor/v2` merge-to-main on private-registry support landing. Current users on a private registry can't adopt the refactored main until then.

Plan should pick one explicitly.

### Commands listed in 3D's table but assigned to no step

- `sim2real list clusters` — clusters get created in Step 0; the listing command is implicit but not enumerated.
- `sim2real list translations` — translations get created in Steps 1 and 2; the listing command is implicit but not enumerated.

Both are small. Attach `list clusters` to Step 0 and `list translations` to Step 2 as line items.

### Validate/execute coverage gap

Step 6 applies the pattern to `assemble`, `deploy.py run`, `build`. The validate/execute proposal's per-command sketch covers six: those three plus `cluster.py provision`, `sim2real translate`, `deploy.py collect`. The other three either get the pattern in Step 6 or are explicitly informal. Pick.

### CI / test infrastructure updates

Plan claim: "Test pyramid is unchanged from today." This is false in practice.

- `.github/workflows/test.yml` hardcodes paths to today's modules; new modules from Step 0 (cluster, layout) need inclusion or they're CI-blind.
- Skill test paths (`.claude/skills/sim2real-*/tests/`) are referenced explicitly; updates to those skills require test updates.
- Test fixtures using `setup_config.json` (Step 0 retires it) need rewriting.
- Test fixtures using the v3 `transfer.yaml` shape (most of them) need rewriting.

Treat CI updates as a first-class deliverable in each step where module paths or test fixtures change — i.e., effectively every step.

### Documentation churn

Plan flags none of this. In practice:

- `CLAUDE.md` references `setup_config.json`, `prepare.py`'s phase structure, the old workspace layout, the `pipeline/` module map. All of this changes.
- `pipeline/README.md` documents all entry points, CLI flags, phase behaviors, workspace artifacts, and patterns. The plan says: *"After any change to `pipeline/` that affects CLI flags, phase behavior, artifact schema, or subcommands, update `pipeline/README.md` to match."* This is a Step-by-step commitment, not a one-time task.
- Each skill (`sim2real-translate`, `sim2real-bootstrap`, `sim2real-analyze`, `sim2real-check`) has its own SKILL.md and several have additional READMEs. Path references throughout.

Treat docs updates as a per-step deliverable.

### PipelineRun name length

Step 5 adds replica suffix to PipelineRun names. K8s DNS-label limit is 253 chars; in practice ~63 for label values. Long workload + run + replica names can exceed. Add a name-length validator at construction time, not dispatch time. Out of band because it crosses Step 5 (introduces the suffix) and `pipeline.yaml` operations broadly.

### Image-tag mutability enforcement for BYO

BYO appendix §o.q.2 raises this. `registered.json` records `image_digest` at register time. Whether to compare digest at deploy time is an enforcement decision. Out of band because it touches `assemble` (which reads `registered.json` indirectly via `run_metadata.json`) and `deploy.py run` (which uses the recorded image tag). Either pick a default (recommend: warn-on-digest-mismatch, refuse with `--allow-mutable`) or explicitly defer.

### Cross-translation aggregation in `/sim2real-analyze`

Plan lists this as out-of-scope. Accept, but call out that the model permits it (multiple translations × multiple runs) — the limit is tooling, not data. Useful to mention so a future plan iteration knows where to plug it in.

### Kubeconfig context validation, per-cluster overlays

3D §"Intentionally deferred." Accept as v1 stances. Out of band because they're explicit deferrals, not implicit gaps.

---

## Step weight reality

The plan's table presents seven rows of equal visual weight. Honest ordering:

| Tier | Steps | Why |
|---|---|---|
| **Heavy — major refactor** | 0, 1, 5 | Step 0: 282-reference retirement + 5+ schemas + new foundation module. Step 1: 6 new commands + 443-line orchestrator port + new `assemble`. Step 5: pair-key schema change across ~20 callsites + `pipeline.yaml` threading + assemble API growth. |
| **Medium — substantial but contained** | 2, 6 | Step 2: 2 commands + skill prompt port. Step 6: dispatcher + validate/execute split for 3+ commands + auto-fix logic + 4 flags. |
| **Light — skill-localized** | 3, 4 | Step 3: sim2real-check port (path-model rework of one skill, read-only). Step 4: sim2real-bootstrap port + `--byo` mode (skill code update). Each localized to one skill. |

**Pacing implications**:

- Steps 0 and 1 together likely consume the majority of the refactor's total work. They're also where the most unresolved design decisions live.
- Step 5 is the second-biggest content step. Pair-key parser change ripples across the orchestrator that Step 1 only just ported.
- Steps 3 and 4 are the smallest. Resist the urge to bundle other work into them.

**Implication for `design-epic` scheduling**:

- Step 0's design-epic is the highest-leverage. It pins schemas everyone depends on AND the `setup_config.json` strategy AND the pair-key suffix choice. Spend disproportionate time.
- Step 5's design-epic deserves real time too. It's where the orchestrator gets second-touched and where coexistence questions surface.
- Steps 3 and 4 can be light design passes.

**Implication for "Every step ends with a runnable workflow"**:

- Step 0 explicitly violates this. The plan says "this is plumbing" and the demo is `kubectl get ns,role,rolebinding,pvc -n ocp-east` — verifying provisioning, not running a sim2real workflow. **State this exception explicitly** in the plan. The discipline is a sound goal but Step 0 is an honest exception.

---

## Inter-step interactions

Things visible only when reading the whole arc.

### The orchestrator gets touched three times without cleanup

Step 1 says "copy and adapt, don't clean." Step 5 modifies pair-key parsing inside that adapted code. Step 6 restructures the same command's outer shape via validate/execute. By Step 6, `_cmd_run` has been modified three times without being refactored. The plan's discipline ("rewrites earn their shape across steps") accepts this. Two options to make the accept explicit:

- Accept silently (plan's current stance).
- Add a "Step 6.5" or post-Step-6 cleanup item to refactor the orchestrator with all three pieces of context now in hand.

Either is fine. Make it a deliberate choice, not an oversight.

### `assemble`'s API grows monotonically across three steps

| Step | `assemble` behavior |
|---|---|
| 1 | Single-replica, hand-rolled transfer.yaml, refuse-on-collision (or whatever resolves §o.q.9) |
| 5 | Adds `--replicas N`, additive merge, `--force-shrink`, `params_hash` drift detection |
| 6 | Splits into `validate()` + `execute()`, becomes auto-fixable from `deploy.py run` |

Three API changes to one command in three steps. Plan doesn't surface this; readers see Step 1 deliver `assemble` and may assume it's done. State explicitly: assemble is a Step 1 / 5 / 6 focus area.

### `deploy.py run`'s behavior changes between Step 1 and Step 6

| Step | Behavior |
|---|---|
| 1 | Errors on missing prereqs; user runs `sim2real assemble` manually |
| 6 | Auto-fixes cheap missing prereqs (chains assemble, build); `--no-auto` preserves Step 1 behavior |

Documented in the validate/execute proposal but not in the plan. State explicitly: `deploy.py run` ships in Step 1 with one behavior contract and ships again in Step 6 with another.

### Skill update fan-out

Skill ownership by step:

- `sim2real-translate` — Step 2 (path port).
- `sim2real-check` — Step 3 (input-model port).
- `sim2real-bootstrap` — Step 4 (port + `--byo` mode).
- `sim2real-analyze` — Step 5 (replica aggregation).

### Transfer.yaml schema lands in Step 0 but uses by Step 2

Schema decisions Step 0 makes (translation/assembly slice split, optional `component:`, BYO accommodations) are validated only when Step 2 reads them. Feedback loop: Step 2 may surface that Step 0's schema needs revision. Accept — but the design-epic for Step 0 should explicitly leave the schema "v0.9 — final lock after Step 2 reality check." This is honest. Pretending it's final at Step 0 invites Step 2 to either silently violate or re-litigate.

### Step 4's optional-component requirement is a Step 0 constraint

`--byo` mode produces `transfer.yaml` without `component:` (or with `byo: true`). Step 0's schema must declare `component:` as optional. Easy to forget in Step 0's design-epic; surface it explicitly.

---

## Open question index

Every open question across the four proposals plus everything surfaced here, with step assignment and current status. *Italics* mark items already surfaced by the proposals; non-italic items are surfaced by this addendum.

| # | Question | Source | Lands in | Status |
|---|---|---|---|---|
| 1 | `setup_config.json` retirement strategy | this addendum | Step 0 | unresolved |
| 2 | `transfer.yaml` v-next schema | this addendum | Step 0 | unresolved |
| 3 | *Pair-key suffix `\|iN` vs `\|rN`* | *replicas §o.q.4* | Step 0 (schema) | unresolved |
| 4 | *Image-tag mutability enforcement for BYO* | *BYO appendix §o.q.2* | Step 0 (`registered.json` schema) | unresolved |
| 5 | *Run-name collision handling* | *3D §o.q.2* | Step 0 (`run_metadata.json` schema) | unresolved |
| 6 | *State-machine semantics in new layout* | *validate/execute §o.q.1* | Step 0 (`state.json` schema) | unresolved |
| 7 | Test/demo image strategy | this addendum | Step 1 | unresolved |
| 8 | Original `_cmd_run`'s fate | this addendum | Step 1 | unresolved |
| 9 | `assemble --run R` re-assemble semantics | this addendum (partial: 3D §o.q.2) | Step 1 | unresolved |
| 10 | `list runs` ordering/filtering | this addendum | Step 1 | unresolved |
| 11 | Structured pair-key parser in Step 1 vs Step 4 | this addendum | Step 1 or 4 | unresolved |
| 12 | `translation_hash` inputs (toolchain, pin) | this addendum | Step 2 | unresolved |
| 13 | Registry-auth strategy (private registries) | this addendum / *3D deferred* | Step 2 + out-of-band | unresolved |
| 14 | Slice extractor location | this addendum | Step 2 | unresolved |
| 15 | `build` skip-when-present vs re-build policy | this addendum | Step 2 | unresolved |
| 16 | *Build placement (standalone vs lazy)* | *3D §o.q.1* | Step 2 / Step 6 | **resolved by step shapes** |
| 17 | *`--byo` interactive vs stub-only ratio* | *BYO appendix* | Step 4 | unresolved |
| 18 | *Config.md / BLIS-format vs free-form tolerance* | *BYO appendix* | Step 4 | unresolved |
| 19 | *Optional `component:` in `transfer.yaml`* | *BYO appendix* | Step 0 (schema impact) / Step 4 (use) | unresolved |
| 20 | *Workload library shipping* | *BYO appendix §o.q.1* | Step 4 | **resolved (no, user-provided)** |
| 21 | *Replica decrease semantics* | *replicas §o.q.5* | Step 5 | unresolved |
| 22 | Old-shape vs new-shape result coexistence | this addendum | Step 5 | unresolved |
| 23 | *`aggregate.json` helper vs in-skill aggregation* | *replicas §"Analyze"* | Step 5 | unresolved |
| 24 | `--iteration` filter parser shape (list vs range) | this addendum | Step 5 | unresolved |
| 25 | *Replica count practical ceiling* | *replicas §o.q.1* | Step 5 | informational |
| 26 | *Auto-fix DAG depth limit* | *validate/execute §o.q.2* | Step 6 | unresolved |
| 27 | *`--plan` output format* | *validate/execute §o.q.3* | Step 6 | likely text + `--json` |
| 28 | Validate/execute coverage (3 commands vs 6) | this addendum | Step 6 | unresolved |
| 29 | Auto-execute announcement format | this addendum | Step 6 | unresolved |
| 30 | Experiment-repo migration | this addendum | out of band | unresolved |
| 31 | *Per-cluster registries* | this addendum / *3D deferred* | out of band | unresolved |
| 32 | `sim2real list clusters` / `list translations` | this addendum | out of band (attach to Step 0/2) | unresolved |
| 33 | CI workflow updates | this addendum | out of band (every step) | underdocumented in plan |
| 34 | Documentation churn (CLAUDE.md, READMEs) | this addendum | out of band (every step) | underdocumented in plan |
| 35 | PipelineRun name length validation | this addendum | out of band (Step 5 introduces) | unresolved |
| 36 | Translation-ref resolver shape shared with Step 2 | this addendum | Step 3 | unresolved |
| 37 | Handling multi-algorithm translations under `--translation T` | this addendum | Step 3 | unresolved |
| 38 | Legacy manually-picked-bundle-root mode: keep or drop | this addendum | Step 3 | unresolved |
| 39 | Ship `sim2real list translations` in Step 3 or defer | this addendum | Step 3 | unresolved |
| 40 | Orchestrator cleanup pass after Step 6 | this addendum | Step 6.5 or accepted-as-tech-debt | unresolved |
| 41 | *Cross-translation aggregation* | *plan out-of-scope* | deferred | accepted |
| 42 | *Kubeconfig context validation* | *3D deferred* | deferred | accepted |
| 43 | *Per-cluster overlays* | *3D deferred* | deferred | accepted |

---

## What this addendum does not change

- The plan's step ordering (0 → 1 → 2 → 3 → 4 → 5 → 6) is sound and defended well in the original document.
- The plan's discipline ("each step is end-to-end runnable", "no mode flags / no dual paths", "the smallest first slice is BYO") is correct.
- The plan's final state (3D + replicas + validate/execute fully realized) is reachable from the proposed sequence.

What this addendum changes:

- Per-step open questions are explicitly named and assigned (43 items, was implicit).
- Out-of-band work is named (10+ items previously silent).
- Step weight is honestly portrayed (3 heavy, 2 medium, 2 light — was 7 equal).
- Inter-step interactions are surfaced (orchestrator triple-touch, assemble monotonic growth, `deploy.py run` behavior change, skill update fan-out, schema-revision feedback loop).

Acting on this addendum is the design-epic's job. This document just makes the work visible.
