# Step 1 design — BYO MVP

Epic: [#443 — Epic: step-1 — BYO MVP](https://github.com/inference-sim/sim2real/issues/443)

Base branch: `refactor/v2`

Sources:
- `docs/proposals/incremental-implementation-plan.md` §Step 1 — BYO MVP
- `docs/proposals/incremental-implementation-plan-addendum.md` §Step 1
- `docs/proposals/three-dimensional-sim2real.md` §Appendix A — BYO
- `docs/epics/step-0/design.md` (design contract of the foundation step-1 sits on)

This document is the design contract for Step 1 of the sim2real v2 refactor.
It resolves the open questions the addendum surfaced for Step 1 (Q7–Q11)
and pins the schemas, commands, and PR shape needed to make split-epic's
child-issue set unambiguous.

---

## Scope

Step 1 delivers the smallest viable BYO (bring-your-own-translation) flow
on the refactor/v2 layout. At the end of step-1, the following commands
exist and compose into an end-to-end demo:

- `sim2real translation register` — imports a pre-built EPP image + treatment
  overlay as a registered translation (BYO producer).
- `sim2real assemble` — snapshots the assembly slice of `transfer.yaml` into
  `runs/<R>/`, resolves scenarios via deep-merge, generates PipelineRuns.
- `deploy.py run --run R` — copy-adapt of today's `_cmd_run`, ported to the
  new per-run layout.
- `deploy.py collect --run R` — copy-adapt of today's `_cmd_collect`.
- `sim2real use --run R` — flips `current_run`.
- `sim2real list runs` — lists runs, newest first.

The end-of-step demo:

```
python pipeline/sim2real.py translation register \
    --algorithm softreflective \
    --image <ref> \
    --config <treatment.yaml>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run trial-1
python pipeline/deploy.py run --run trial-1
python pipeline/deploy.py collect --run trial-1
# produces workspace/runs/trial-1/results/baseline/<workload>/per_request_lifecycle_metrics.json
```

Step 1 does **not**:

- Deliver skill-driven translation (`sim2real translate`) — that is Step 2.
- Deliver `sim2real build` — Step 2.
- Deliver `sim2real-check` skill port — Step 3.
- Deliver scenario bootstrap (`/sim2real-bootstrap` port) — Step 4.
- Support replicas, `--iteration` filtering, or drift detection — Step 5.
- Support auto-fix or the validate/execute pattern — Step 6.
- Support multi-algorithm register into one translation — deferred; each
  `register` call creates a single-algorithm translation.
- Migrate `admission-control` or other v3 experiment repos — the demo uses
  `kalantar-msb/sr` (already tested against step-0).

---

## Assumptions

- **Base branch state.** Step-1 implementation begins after step-0 merges
  into `refactor/v2`. All step-1 PRs assume `pipeline/lib/layout.py`,
  `pipeline/lib/slicer.py`, `pipeline/cluster.py`, `pipeline/lib/cluster_ops.py`,
  and the setup.py/deploy.py/prepare.py/remote.py cluster-field ports are
  already present. Design was written against step-0's merged shape as
  documented in `docs/epics/step-0/design.md`.
- **Demo experiment repo.** `kalantar-msb/sr` is the source-of-truth
  experiment for step-1's demo. Its `transfer.yaml` is v3 with two algorithms
  (`softreflective` treatment, `constantceiling` control) and 12 workloads.
  Step-1 exercises `softreflective` only; `constantceiling` sits unused
  until step-2's skill-driven `translate` lands.
- **Demo image.** A pre-built EPP image already exists in the target
  registry from current main. The demo reuses it — step-1 does not
  introduce build tooling.

---

## Open-question resolutions

Addendum questions (Step 1 table) resolved as follows:

| # | Question | Resolution |
|---|---|---|
| 7 | Test/demo image strategy | Reuse an image from current main. |
| 8 | Original `_cmd_run`'s fate | Dead code is removed. The PR that ships the new `deploy.py run --run R` deletes the original `_cmd_run` in the same commit. |
| 9 | `assemble --run R` re-assemble semantics | Refuse by default; `--force` required to overwrite. |
| 10 | `list runs` ordering/filtering | Recency-first (mtime desc), no filtering flags in step-1. |
| 11 | Structured pair-key parser in step-1 vs step-5 | Deferred to step-5. Step-1's ported orchestrator keeps today's trivial `_is_pair_key`. |

Additional decisions taken during design:

- **CLI entry point.** New `pipeline/sim2real.py` with argparse subcommands
  (`translation register`, `assemble`, `use`, `list runs`). Follows the
  existing convention (`cluster.py`, `deploy.py`, `setup.py` are all
  `pipeline/*.py`).
- **Multi-algorithm `translation register`.** Single algorithm per call.
  Multi-algorithm-into-one-translation lands in step-2 alongside skill-driven
  `translate`.
- **`current_run` storage.** Continues to live in `setup_config.json`
  (where step-0 left it). No `workspace/state.json` in step-1.
- **`prepare.py` fate.** Deleted in the same PR that ships `sim2real assemble`,
  along with `pipeline/lib/state_machine.py` and `pipeline/lib/context_builder.py`.
  The skill-driven flow (which uses prepare's Phase 3 checkpoint) is
  temporarily broken through step-1; `/sim2real-translate` gets a stub error
  message. Step-2 restores it on the new layout.
- **CI and documentation updates.** Each PR that changes a module path,
  entry point, or artifact schema updates `.github/workflows/test.yml`,
  `CLAUDE.md`, and `pipeline/README.md` in the same commit. A final PR
  sweeps anything missed and documents the end-of-step BYO demo flow.

---

## Schemas

Step-1 introduces four artifacts. All are version-tagged (`version: 1`)
so step-2+ can evolve them.

### `translations/<hash>/translation_output.json`

Index of what a translation contains. Written by `translation register`.
Read by `assemble`.

```json
{
  "version": 1,
  "translation_hash": "sha256-hex",
  "source": "byo",
  "algorithms": [
    { "name": "softreflective" }
  ],
  "image_ref": "ghcr.io/kalantar-msb/sr-router:some-tag",
  "created_at": "2026-07-01T14:00:00Z"
}
```

Step-2 grows `algorithms[i]` with `{go_source_path, config_hash, ...}` and
adds `source: "skill"` as a peer of `source: "byo"`. Step-1 accepts either
shape on read but only writes `source: "byo"`.

### `translations/<hash>/registered.json` (BYO-only)

Digest audit trail — separate from `translation_output.json` so step-2's
skill-produced translations don't have to fake it.

```json
{
  "version": 1,
  "image_ref": "ghcr.io/kalantar-msb/sr-router:some-tag",
  "image_digest": "sha256:aabbcc...",
  "source": "byo",
  "registered_at": "2026-07-01T14:00:00Z"
}
```

`image_digest` is pulled at register time via `docker manifest inspect`
(or equivalent). If the registry is unreachable, register warns and
records `image_digest: null` — no hard fail (needed for offline dev).

### `runs/<R>/run_metadata.json`

Records what triple this run is. Written by `assemble`. Read by every
downstream command.

```json
{
  "version": 1,
  "run_name": "trial-1",
  "translation_hash": "sha256-hex",
  "cluster_id": "ocp-east",
  "params_hash": "sha256-hex",
  "image_tag": "ghcr.io/kalantar-msb/sr-router:some-tag",
  "assembled_at": "2026-07-01T14:05:00Z"
}
```

`params_hash` is SHA-256 of `manifest.assembly.yaml`'s bytes. Step-1
records it but does not perform drift detection — that's step-5.

### `runs/<R>/manifest.assembly.yaml`

Verbatim snapshot of the assembly slice from `transfer.yaml` at assemble
time, sliced via step-0's `slicer.py`. Enables reproducibility from disk
and, later, drift detection.

```yaml
# generated by sim2real assemble at 2026-07-01T14:05:00Z; do not edit
workloads:
  - workloads/code_generation_4.yaml
  - workloads/interactive_chat_20.yaml
  # ...
baselines:
  - name: baseline
    scenario: baselines/softreflective.yaml
algorithms:
  - name: softreflective
    scenario: null       # BYO: no scenario needed
    defaults: baseline
defaults:
  disable: []
```

### `translation_hash` derivation (BYO)

Per 3D proposal §Appendix A:

```
translation_hash = sha256(
    image_digest_or_ref
    || treatment_config_content_bytes
    || algorithm_name
)
```

If `image_digest` is null (offline register), the raw `image_ref` string
is substituted so the hash is stable within the offline session. The
addendum's Q12 ("build-environment invariance") does not apply to BYO —
the customer already built the binary.

---

## Layout on disk after step-1

```
workspace/
├── setup_config.json                    # step-0 owns; step-1 adds `current_run`
├── clusters/<cluster_id>/
│   └── cluster_config.json              # step-0
├── translations/<hash>/
│   ├── translation_output.json          # step-1 schema
│   ├── registered.json                  # step-1 schema (BYO-only)
│   └── generated/
│       ├── baseline_config.yaml         # empty or missing when BYO omits --baseline-config
│       └── <algo>/<algo>_config.yaml    # customer's treatment overlay
└── runs/<run_name>/
    ├── cluster/                         # baseline.yaml, treatment.yaml, pipelinerun-*.yaml
    ├── results/<phase>/<workload>/
    ├── manifest.assembly.yaml           # step-1 schema
    └── run_metadata.json                # step-1 schema
```

Not landing in step-1: `translations/<hash>/skill_input.json`, per-algo
`{cmd, pkg}/` Go source, `runs/<R>/.state.json`, `runs/<R>/plans/`
schema changes (`plans/` remains as today for `deploy.py run` internal use).

---

## Commands

### `sim2real translation register`

```
sim2real translation register \
    --algorithm NAME \
    --image REF \
    --config PATH_TO_TREATMENT_OVERLAY \
    [--baseline-config PATH]
    [--registered-hash HASH]
```

**Validate:** algorithm name is `[a-z0-9-]+`; image ref is well-formed;
treatment overlay parses as YAML; `--registered-hash` (if given) matches
computed. Optionally pulls image digest via `docker manifest inspect`;
missing registry = warn, digest recorded as `null`.

**Execute:** creates `translations/<hash>/`, writes `translation_output.json`
and `registered.json`, copies the treatment overlay to
`generated/<algo>/<algo>_config.yaml`, copies the baseline overlay (if given)
to `generated/baseline_config.yaml`.

**Failure modes:**
- Existing translation dir with same hash → warn "already registered,
  no-op" (idempotent).
- Algorithm name collision within an existing translation → error.
- Malformed treatment overlay → error, no writes.

**Exit codes:** 0 success, 2 validation error, 3 registry check failed
but hash recorded (non-fatal warning).

### `sim2real assemble`

```
sim2real assemble \
    --translation HASH \
    --cluster CLUSTER_ID \
    --run RUN_NAME \
    [--force]
```

**Validate:** translation dir exists; `cluster_config.json` exists for
`--cluster`; `runs/<R>/` does not exist unless `--force`; `transfer.yaml`
in the experiment repo loads cleanly and its algorithms list contains at
least the registered algorithm; workloads referenced in transfer.yaml
exist on disk.

**Execute:**
1. Slice `transfer.yaml` via step-0's `slicer.py`; snapshot the assembly
   slice to `runs/<R>/manifest.assembly.yaml`.
2. Filter `transfer.yaml:algorithms` to those in
   `translation_output.json:algorithms` — skip the rest silently (this
   is how the `sr` demo exercises softreflective while leaving
   constantceiling in place).
3. Deep-merge via `pipeline/lib/values.py:deep_merge` (unchanged from today):
   - `baseline_resolved = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)`
   - `treatment_resolved = deep_merge(baseline_resolved, algo.scenario_overrides, treatment_overlay)`
4. Inject `image_tag` from `translation_output.json:image_ref` into
   treatment scenarios.
5. Write `runs/<R>/cluster/baseline.yaml`, `.../treatment.yaml`.
6. Generate `runs/<R>/cluster/pipelinerun-*.yaml` via `pipeline/lib/tekton.py`
   (unchanged from today).
7. Write `runs/<R>/run_metadata.json` with
   `params_hash = sha256(manifest.assembly.yaml)`.

**Failure modes:**
- `transfer.yaml` algorithm listed but no matching translation → warn and skip.
- Workload file missing → error.
- Slicer failure → error.
- Existing `runs/<R>/` without `--force` → error with the `--force` hint.

### `deploy.py run --run R`

Copy-adapt of today's `_cmd_run` (~443 lines). Delta from today:

- Reads `runs/<R>/cluster/` instead of `workspace/cluster/`.
- Reads `runs/<R>/run_metadata.json` for `cluster_id`; resolves namespaces
  via `layout.cluster_dir(cluster_id)/cluster_config.json`.
- ConfigMap name: `sim2real-progress-<run_name>` (unchanged shape; already
  keyed by run today).
- `_is_pair_key`, `_load_pairs` unchanged (deferred to step-5).
- No new flags — same interface as today.

**Failure modes on missing prereqs** — no auto-fix (step-6's job):

- `runs/<R>/` doesn't exist → `"run 'sim2real assemble --run <R>' first"`.
- `runs/<R>/cluster/` missing → same message.
- `run_metadata.json` missing or `cluster_id` field absent →
  `"run metadata corrupted; re-assemble"`.

### `deploy.py collect --run R`

Copy-adapt of today's `_cmd_collect`. Reads `runs/<R>/run_metadata.json`
to resolve cluster/namespace. Pulls PVC contents into
`runs/<R>/results/<phase>/<workload>/`. GPU logs into
`.../gpu_logs/<node>.log` (unchanged shape).

### `sim2real use --run R`

```
sim2real use --run RUN_NAME
```

**Validate:** `runs/<R>/` exists and `run_metadata.json` is readable.

**Execute:** writes `current_run` field in `setup_config.json`. If
step-0's `setup_config.json` already tracks `current_run` in the same
field, this is a rename-free change; verify at implementation time.

**Failure modes:** run doesn't exist → error with `sim2real list runs` hint.

### `sim2real list runs`

```
sim2real list runs
```

Walks `workspace/runs/*/run_metadata.json`. Prints one line per run in
reverse mtime order (newest first):

```
  RUN_NAME             TRANSLATION    CLUSTER     ASSEMBLED
* trial-2              abc12345       ocp-east    2026-07-01 14:32
  trial-1              abc12345       ocp-east    2026-07-01 12:10
```

`*` marks `current_run`. No filtering flags in step-1.

**Failure modes:** `workspace/runs/` missing → prints "no runs yet" and
exits 0.

---

## Child-PR breakdown

Six PRs. Each is sized for `/fix-issue` (~1–3 days). Dead code is deleted
in the PR that supersedes it — no separate cleanup PRs.

### PR 1 — CLI scaffold + `translation register`

Creates `pipeline/sim2real.py` with argparse dispatch and the
`translation register` subcommand. Pins `translation_output.json` and
`registered.json` schemas. Unit tests for validation, idempotency,
hash determinism, and offline-registry `image_digest: null` path.
No downstream consumers yet — this PR ships the register command only.

**Deletes:** none.

**Docs/CI:** adds `pipeline/sim2real.py` and its tests to
`.github/workflows/test.yml`; adds a "Register a translation" section
to `pipeline/README.md`.

### PR 2 — `sim2real assemble` + delete prepare/legacy-assemble

`sim2real assemble` implementation (validation + slice + merge + write).
Pins `run_metadata.json` and `manifest.assembly.yaml` schemas. Unit tests
for algorithms filter, framework-defaults merge, refuse-on-existing-run,
`--force` overwrite, and slicer round-trip.

**Deletes:**
- `pipeline/prepare.py`
- `pipeline/lib/state_machine.py`
- `pipeline/lib/context_builder.py`
- `pipeline/lib/assemble.py` (247-line legacy)
- Stubs `/sim2real-translate` skill's SKILL.md to error out with a
  "restored in step-2" message.

**Docs/CI:** removes deleted paths from `.github/workflows/test.yml`;
updates `CLAUDE.md` and `pipeline/README.md` to remove references to
`prepare.py` and the 6-phase state machine; adds "Assemble a run" section.

**Real-cluster gate:** after this PR, `sim2real assemble --run trial-1`
against `sr` must produce a valid `runs/trial-1/cluster/` tree (verified
structurally; no `deploy.py run` yet).

### PR 3 — `deploy.py run --run R` + delete original `_cmd_run`

Copy-adapt of `_cmd_run` for the new layout. Delete the original.
`test_deploy_run.py` rewrite (already heavily touched by step-0).

**Deletes:** original `_cmd_run` in `pipeline/deploy.py` and any helpers
that only its old shape used.

**Docs/CI:** updates `pipeline/README.md` to point at the new
`deploy.py run --run R` shape.

**Real-cluster gate:** `deploy.py run --run trial-1` reaches SUCCEEDED
against `sr` on a real cluster.

### PR 4 — `deploy.py collect --run R` + delete original `_cmd_collect`

Copy-adapt of `_cmd_collect`. Delete the original.

**Deletes:** original `_cmd_collect`.

**Docs/CI:** `pipeline/README.md` collect section updated.

**Real-cluster gate:** `deploy.py collect --run trial-1` produces
`per_request_lifecycle_metrics.json` for each workload — the end-of-step
demo criterion.

### PR 5 — `sim2real use` + `sim2real list runs` + delete `run.py`

Two small commands. Both walk `workspace/runs/`; `use` also updates
`setup_config.json:current_run`. Today's `run.py inspect` (debug aid for
displaying a run's translation_output.json etc.) is dropped without
replacement — operators can `cat` the JSON files directly. If demand
appears later, `sim2real inspect run <R>` can be filed as a follow-up.

**Deletes:** `pipeline/run.py` entirely.

**Docs/CI:** `pipeline/README.md` gets a "Manage runs" section;
`CLAUDE.md` reference to `run.py` removed.

### PR 6 — Ops subcommands port + docs/CI sweep

Today's `deploy.py` has more subcommands than `run` and `collect`:
`status`, `pairs`, `reset`, `wipe`, `stop`, `run-remote`. Each reads
run-scoped paths (workload directories, ConfigMap keyed by run,
per-pair status), so each breaks under the new layout unless ported.

- `status`, `pairs` — observe run state. Port to `--run R` shape; read
  `runs/<R>/cluster/` and `sim2real-progress-<R>` ConfigMap.
- `reset`, `wipe`, `stop` — mutate run state. Port to `--run R` shape;
  update ConfigMap and delete PipelineRuns for the specified run.
- `run-remote` — the containerized-orchestrator variant. Port
  alongside `run` since it shares `_cmd_run`'s core; treat this
  scope as an option to split into its own PR if it grows.

Also sweeps for any remaining reference to deleted modules. Adds an
"end-of-step-1 BYO demo" section to `pipeline/README.md`. Confirms
`.github/workflows/test.yml` covers all new modules and no stale paths.

**Deletes:** any deploy.py ops-command helpers that only serviced the
old layout.

**Ordering:** PRs 1 through 5 can each land independently once the
prior PR merges. PR 2 depends on PR 1 (assemble reads translations).
PR 3 depends on PR 2 (run reads assembled cluster/). PR 4 depends on
PR 3 (collect reads what run produced). PR 5 is independent — could
land any time after PR 2 (runs exist). PR 6 lands last and closes
scope by porting ops subcommands and sweeping docs/CI.

---

## Risks

1. **Orchestrator copy-adapt bugs.** `_cmd_run` is ~443 lines with subtle
   path-rewrites in nearly every branch. Path bugs surface only in a
   real cluster run.
   **Mitigation:** PR 3 gated on a real-cluster demo, not simulated
   tests alone.
2. **Schema reality check with step-0.** Step-0's `slicer.py` was tested
   against v3 `transfer.yaml` but not against a downstream consumer that
   assembles + runs. Step-1's assemble is the first real consumer.
   **Mitigation:** PR 2 includes slicer round-trip tests. If a fix is
   needed, patch step-0's already-merged code via a follow-up PR to
   `refactor/v2`.
3. **`translation register` idempotency.** Two `register` calls with the
   same `--algorithm/--image/--config` inputs must produce the same
   `translation_hash` and the second call must be a no-op, not an error.
   **Mitigation:** explicit idempotency test in PR 1.
4. **`image_digest = null` path.** Register succeeds even when the
   registry is unreachable. Downstream code (assemble → deploy) must not
   assume `image_digest` is set.
   **Mitigation:** unit test with a bogus registry; assemble/deploy code
   reads `image_ref` (always present), not `image_digest`.
5. **`/sim2real-translate` skill outage.** The skill errors out from PR 2
   merge until step-2's `sim2real translate` lands. Any operator or
   downstream tooling that pipes through the skill during that window
   sees the stub error.
   **Mitigation:** stub message names step-2 as the fix; step-2 kicks
   off immediately after step-1 merges.

---

## Deferred to later steps

Not landing in step-1; called out here so `split-epic` doesn't file
child issues by accident:

- **Structured pair-key parser** → step-5 (Q11).
- **`params_hash` drift detection** → step-5. Step-1 records the hash.
- **Auto-fix chains from `deploy.py run`** → step-6.
- **`--iteration` filter and replicas** → step-5.
- **`--plan` / `--no-auto` / validate-execute pattern** → step-6.
- **`sim2real build` / `sim2real translate`** → step-2.
- **`/sim2real-check` skill port** → step-3.
- **`/sim2real-bootstrap` port** → step-4.
- **Multi-algorithm register into one translation** → step-2 alongside
  skill-driven translate.

---

## Out-of-band (called out but not this epic's work)

Called out in the addendum. Step-1 does **not** address these; split-epic
should skip them:

- Registry auth for private registries (deferred to step-2's build).
- PipelineRun name length validator (step-5 introduces the suffix that
  risks it).
- Image-tag mutability enforcement (step-5 or later).
- Cross-translation aggregation in `/sim2real-analyze` (accepted as
  out-of-scope for the refactor).

---

## Testing strategy

- **Unit tests** for each new schema (serialize / deserialize round-trip,
  version tolerance).
- **Unit tests** for `translation register` (algorithm-name validation,
  hash determinism, idempotency, offline-registry path).
- **Unit tests** for `assemble` (algorithms filter, framework-defaults
  merge, refuse-on-existing-run, `--force` overwrite).
- **Unit tests** for `sim2real use` / `list runs` (mtime ordering,
  missing-run behavior, current_run marker).
- **`deploy.py run` and `collect` regression tests** — the existing
  suite (`test_deploy_run.py`, `test_deploy_collect.py`) rewritten for
  the new layout. Step-0 already touched these files heavily.
- **End-to-end demo** against `sr` on a real cluster at PR 2
  (assemble output structural check), PR 3 (run reaches SUCCEEDED),
  and PR 4 (collect produces per-request metrics). Manual operator
  sign-off each time — no CI equivalent.
- **CI update** in each PR that adds a new module: `.github/workflows/test.yml`
  gets the new paths.

---

## Success criterion for the epic

```
python pipeline/cluster.py provision <cluster_id> --namespaces ...          # step-0
python pipeline/setup.py --registry ... --experiment-root .../sr            # step-0
python pipeline/sim2real.py translation register \
    --algorithm softreflective \
    --image <existing-main-image> \
    --config <treatment-overlay>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run trial-1
python pipeline/deploy.py run --run trial-1
python pipeline/deploy.py collect --run trial-1
# produces:
#   workspace/runs/trial-1/results/baseline/<workload>/per_request_lifecycle_metrics.json
#   for each workload in sr/transfer.yaml
```

When this sequence produces the metrics file for at least one workload,
step-1 is complete and the epic can close via `/close-epic`.
