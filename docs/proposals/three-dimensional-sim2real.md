# Three-dimensional sim2real: translation, cluster, run

## Motivation

The current workspace conflates three orthogonal concerns under a single per-run tree, which surfaces as three recurring pain points:

- **Translation is locked to a run.** Switching runs forces re-translation, even when the algorithm code is unchanged.
- **Cluster switch is heavyweight.** Moving an experiment between clusters means re-running `setup.py` and copying `results/` out by hand to preserve prior cluster's work.
- **"Vary configuration, keep translation" has no first-class workflow.** The closest path is to edit `transfer.yaml` and re-run prepare end-to-end — which re-translates and risks overwriting prior `results/`.

These are three different axes of variation. This proposal separates them.

## The three dimensions

| Dimension | What it captures | Identity | Lifecycle |
|---|---|---|---|
| **Translation** | Translated scorer code, generated configs, skill artifacts. | content hash over algorithm sources + translation slice of `transfer.yaml` | changes when you (re)translate |
| **Cluster** | Cluster context, namespaces, registry, GPU resource type, RBAC posture, storage class, openshift flag. | operator-assigned `cluster_id` | changes when you provision a new cluster |
| **Run** | Assembled `cluster/` YAMLs, `results/`, runtime state, recorded params. | run-name + recorded `(translation_hash, cluster_id, params_hash)` in metadata | per invocation |

A **run** is the materialization of `(translation × cluster × run-params)` at a specific time.

## Proposed workspace layout

```
workspace/
├── state.json                            # { "current_run": "R" } — single global pointer
├── translations/<translation_hash>/
│   ├── generated/
│   │   ├── baseline_config.yaml          # shared baseline overlay (may be empty)
│   │   ├── <algo>/<algo>_config.yaml     # per-algorithm treatment overlay
│   │   └── <algo>/{cmd,pkg}/             # plugin Go source (skill-produced; absent for BYO)
│   ├── translation_output.json           # index of algorithms + provenance
│   ├── skill_input.json                  # audit trail of what the skill saw (skill-produced)
│   └── registered.json                   # only for BYO: image_ref, source = "byo"
├── clusters/<cluster_id>/
│   └── cluster_config.json               # namespaces, GPU resource type, openshift, ...
└── runs/<run_name>/
    ├── cluster/                          # baseline.yaml, treatment.yaml, pipelinerun-*.yaml
    ├── results/<phase>/<workload>/
    ├── plans/
    ├── .state.json                       # run-scoped phase machine
    ├── manifest.assembly.yaml            # snapshot of assembly slice at assemble time
    └── run_metadata.json                 # records translation_hash, cluster_id, params_hash, image_tag, ...
```

`transfer.yaml` continues to live in the experiment repo (unchanged). It is the single user-facing manifest, with two slices internally:

- **Translation slice**: `scenario`, `component`, `context`, `algorithms[i].source`, `algorithms[i].config`. Drives `translation_hash`.
- **Assembly slice**: `workloads`, `baselines[i]`, `algorithms[i].scenario`, `algorithms[i].defaults`, `defaults.disable`. Snapshotted at assemble time.

Changes to the assembly slice do **not** invalidate `translation_hash`. This is the property that makes "vary configuration, keep translation" cheap.

## Proposed commands

| Command | Inputs | Effects |
|---|---|---|
| `cluster.py provision <cluster_id>` | kubeconfig + flags | namespace, RBAC, PVCs, secrets, Tekton tasks applied; writes `clusters/<id>/cluster_config.json`. Idempotent. |
| `/sim2real-bootstrap <experiment-folder>` *(skill)* | experiment folder with `algorithms/`, `workloads/`, `config.md` / JSON | derives target component repo and submodule ref, generates `baseline.yaml`, copies framework defaults templates, scaffolds `transfer.yaml`. Standard mode for skill-driven translations; `--byo` mode for BYO (see appendix). |
| `sim2real translate` | `transfer.yaml`, algorithm sources | runs context + skill checkpoint; writes `translations/<hash>/{generated/, translation_output.json, skill_input.json}`. Idempotent on hash. Producer of a translation. |
| `sim2real translation register --image <ref> --config <path> --algorithm <name>` | image ref, treatment overlay YAML, optional baseline overlay | imports a pre-built translation: writes `translations/<name>/{generated/<algo>/<algo>_config.yaml, translation_output.json, registered.json}`. Peer producer of a translation, for BYO. See appendix. |
| `sim2real build --translation T` | translation_hash | builds EPP image tagged `T[:12]`, pushes to (public) registry. No-op when the translation is BYO (image already present). |
| `sim2real assemble --translation T --cluster C --run R` | translation + cluster + assembly slice | creates `runs/R/cluster/`, snapshots assembly slice as `manifest.assembly.yaml`, writes `run_metadata.json`. Lazily initializes any workspace state. |
| `deploy.py run --run R` | run identity | reads R's metadata, dispatches PipelineRuns to R's cluster. |
| `deploy.py collect --run R` | run identity | pulls PVC results into `runs/R/results/`. |
| `sim2real use --run R` | run name | flips `state.json:current_run`. Replaces today's `switch`. |
| `sim2real list runs` / `list clusters` / `list translations` | — | discovery. |

**Translation is a role, not a single command.** `sim2real translate` and `sim2real translation register` are two producers of the same artifact. Downstream (`build`, `assemble`, `deploy.py run`) doesn't care which producer wrote it.

Every command accepts `--run`/`--translation`/`--cluster` to override; defaults flow from the active run. The "current triple" is just `current_run`; translation and cluster are derived from the run's recorded metadata, not separate global pointers.

`setup.py` decomposes into `cluster.py provision` (cluster bits only) plus lazy workspace init done by whichever command first needs each piece. There is no single "start here, set everything up" step.

## Step-by-step flow

```
Operator action                                Workspace / experiment-repo state change
─────────────────────────────────────────      ─────────────────────────────────────────
1. cluster.py provision ocp-east               clusters/ocp-east/cluster_config.json
2. /sim2real-bootstrap <experiment-folder>     experiment-folder/{transfer.yaml,
                                                   baseline.yaml, baselines/defaults/}
                                               (only the first time, or when scenarios
                                                need re-deriving)
3. sim2real translate                          translations/<hash>/{generated/,
                                                   translation_output.json,
                                                   skill_input.json}
4. sim2real build --translation <hash>         registry/repo:<hash[:12]> pushed
5. sim2real assemble                           runs/R1/{cluster/, manifest.assembly.yaml,
       --translation <hash>                              run_metadata.json}
       --cluster ocp-east --run R1
6. deploy.py run --run R1                      PipelineRuns dispatched; PVC populated
7. deploy.py collect --run R1                  runs/R1/results/...

# Vary configuration without retranslating:
8. (edit transfer.yaml workloads / baselines)
9. sim2real assemble --run R2                  runs/R2/... (same translation + cluster)
10. deploy.py run --run R2

# Run on a different cluster:
11. cluster.py provision ocp-west              clusters/ocp-west/cluster_config.json
12. sim2real assemble --cluster ocp-west       runs/R3/... (same translation, new cluster)
       --run R3
13. deploy.py run --run R3
```

R1, R2, R3 coexist on disk. Their `results/` don't overlap. The operator switches active context via `sim2real use --run RN`.

Step 2 (`/sim2real-bootstrap`) is the scenario-scaffolding component. It's not repeated for every run — only when the experiment folder doesn't yet have a `transfer.yaml` / `baseline.yaml`, or when those need to be regenerated from updated source material.

## Run identity and reproducibility

`runs/R/run_metadata.json` records the full provenance:

```json
{
  "run_name": "R",
  "translation_hash": "abc123…",
  "cluster_id": "ocp-east",
  "params_hash": "def456…",
  "image_tag": "registry.example.com/sim2real-scorer:abc123abcd",
  "assembled_at": "2026-06-26T19:00:00Z"
}
```

Reproducing a run from disk requires: the translation tree, the cluster config, and the assembly snapshot. All three are present.

## Intentionally deferred for v1

- **Per-cluster registries.** Assume one public registry. Adding per-cluster requires `cluster_config.registry` and `image_pull_secret` — small change when needed.
- **Kubeconfig context validation.** No enforcement that active kubectl context matches `current_run.cluster_id`. Commands print both at startup; mismatch is the operator's problem.
- **Per-cluster overlays.** Cluster-specific framework workarounds stay in `baselines/defaults/` in the experiment repo, controlled by `transfer.yaml:defaults.disable`.
- **Re-using a run name.** Either refuse, or auto-suffix at assemble time. TBD.

## Open questions

1. **Build placement.** `build` as a tail of `translate`, head of `assemble`, or its own command. Standalone is most flexible; lazy-from-assemble is most convenient.
2. **Re-assembling an existing run.** `assemble --run R` when R already exists: clobber / refuse / auto-suffix. Probably refuse + `--force`.
3. **Replicas.** "Run R three times for three datapoints" is a sub-run dimension, not modeled by the three top-level dimensions. Addressable via a `--replicas N` pair-key suffix inside one run, but separate concern.
4. **Migration.** Existing workspaces store `generated/` inside `runs/<run>/`. Either lazy dual-read in the loader or a one-shot migration script.

## Out of scope

- Per-replica execution (separate proposal). Decoupled from this proposal — either can land first without affecting the other.
- Cluster-side drift detection.
- Multi-experiment orchestration — already supported via `--experiment-root`, unchanged.

---

## Appendix A — Bring-your-own translation (BYO)

### The use case

A customer arrives with:

- A pre-built EPP image (their scorer compiled by their own toolchain), pushed to a registry sim2real's clusters can pull from.
- A treatment overlay YAML telling the EPP what scorer to use and how to parameterize it (the content equivalent of `generated/<algo>/<algo>_config.yaml`).
- Optionally: a baseline overlay YAML; their plugin's Go source (for audit only, not deployment).

They want to:

- Provision a cluster.
- Scaffold the scenario files (`transfer.yaml`, `baseline.yaml`, framework defaults) — they don't have them.
- Execute against their image.

They are explicitly bypassing the skill-driven translate path. There is no `algorithms/*.go` source to feed to the skill, no `transfer.yaml` to start from.

### How the standard flow handles this

The customer flow uses the same commands as the standard flow, with **one step replaced** and **one step gaining a mode**:

```
Standard flow                                  BYO flow
──────────────────────────                     ─────────────────────────
cluster.py provision <id>                      cluster.py provision <id>
/sim2real-bootstrap <folder>                   /sim2real-bootstrap <folder> --byo
sim2real translate                             sim2real translation register \
sim2real build --translation <hash>                --image <ref> \
                                                   --config <treatment-overlay.yaml> \
                                                   --algorithm <name>
sim2real assemble --translation ... --run R    sim2real assemble --translation ... --run R
deploy.py run --run R                          deploy.py run --run R
deploy.py collect --run R                      deploy.py collect --run R
```

Identical bookends. Identical assemble, deploy, collect. The middle differs.

### What each piece needs to do differently

**`/sim2real-bootstrap` `--byo` mode**

Today bootstrap derives most of its inputs from a BLIS-generated experiment folder: it reads `algorithms/*.go` for component-repo Go import paths, reads `config.md` tables for model/replica/TP/PP/GPU settings, etc. A BYO customer typically has none of those:

- **No `algorithms/*.go`** — no source to scan for component repo. The customer instead names the algorithm at `translation register` time, and the algorithm in `transfer.yaml` refers to that name (no `source:` path needed since translate is skipped).
- **No `config.md` / structured JSON** — bootstrap cannot derive model name, replica count, TP, PP, GPU type, max model len, etc.
- **No need for a component submodule** — there's nothing to compile.

`--byo` mode therefore:

1. **Skips the component submodule derivation step entirely** (Task 1 + Task 2 in today's skill flow). The `transfer.yaml` it produces has no `component:` block, or a `component:` block with `byo: true` and no `ref`.
2. **Cannot derive baseline scenario settings**, so it must either:
   - Prompt the user for each required field interactively (model name, GPU type, replicas, TP/PP, tokenizer, max model len, vLLM image, …), or
   - Emit a stub `baseline.yaml` with `# TODO:` markers and a clear "fill these before running translate/register/assemble" message, then exit.
   - In practice probably both: prompt where there's a sensible default to offer, stub-TODO where there isn't.
3. **Asks for the algorithm name(s)** the customer will register later, so `transfer.yaml:algorithms[].name` matches.
4. **Still copies the framework defaults overlay templates** from the skill's `templates/defaults/` into `baselines/defaults/`. The customer can selectively `defaults.disable` them in `transfer.yaml`. This part is unchanged from standard mode.
5. **Still selects workloads from `workloads/*.yaml`** — and if the customer has no `workloads/` dir, points them at the workload library (see open question below) or asks them to provide their own.

The user-facing summary: standard bootstrap derives, BYO bootstrap asks (or instructs).

**`sim2real translation register` (new command, BYO-only)**

Validates:
- Image reference is well-formed; optionally pulls the manifest to confirm the image exists and is the right architecture (cheap pre-flight against the registry).
- Treatment overlay YAML is valid against the EPP plugin schema.
- Algorithm name doesn't collide with an already-registered translation.

Executes:
- Writes `translations/<name>/generated/<algo>/<algo>_config.yaml` (the customer's overlay).
- Writes `translations/<name>/generated/baseline_config.yaml` if `--baseline-config` was supplied; otherwise leaves it empty (assembly merges nothing).
- Writes `translations/<name>/translation_output.json` with `{algorithms: [<algo>], source: "byo", image_ref}`.
- Writes `translations/<name>/registered.json` recording image digest at register time (so tag mutability is auditable).
- Computes a `translation_hash` over `(image_digest, config content, algorithm name)`. Stored alongside.

The result is a `translations/<name>/` tree that's indistinguishable from a skill-produced one as far as assemble is concerned, except the Go source under `<algo>/{cmd,pkg}/` is absent.

**`sim2real build` becomes a no-op for BYO**

`build`'s validate phase already needs to ask "is the image present in the target registry?" For BYO, the answer is yes (the customer pushed it). The execute phase is a no-op. This falls out naturally from the validate/execute pattern in [validate-execute-pattern.md](validate-execute-pattern.md) — no code path needs to special-case BYO.

For safety, `build` should detect `registered.json:source == "byo"` and short-circuit even before the registry check, with an info-level "BYO translation; build skipped" line.

**`assemble`, `deploy.py run`, `deploy.py collect` are unchanged**

The translation slice that assemble consumes (`generated/<algo>/<algo>_config.yaml`, `generated/baseline_config.yaml`) is present either way. The image reference passed into the resolved `cluster/treatment.yaml` comes from `run_metadata.json:image_tag`, which is `translations/<name>/registered.json:image_ref` for BYO and `registry/repo:<hash[:12]>` for skill-driven. Assemble just uses whichever is stamped.

### The "missing pieces" problem in bootstrap

The BLIS-driven bootstrap relies heavily on a `config.md` table (or `top3_selection.json` / `scenario_input.json`) to derive baseline scenario fields. A BYO customer is unlikely to have any of these in the BLIS-expected format.

Bootstrap `--byo` must handle missing inputs without giving up. Three sensible behaviors, in priority order:

1. **Read what's there.** If the customer happens to have a `README.md` or a `config.yaml` with recognizable fields (model name, GPU count), extract them with the same parsers `generate_from_config.py` already uses. Don't require BLIS-specific structure.
2. **Prompt for the rest.** For each missing required field (model, max-model-len, GPU resource type, replica count, …), ask the user interactively. Re-use the question pattern the `/sim2real-translate` skill already uses for its checkpoint.
3. **Emit stub baseline.yaml with TODOs and stop.** If interactive prompting isn't feasible (CI, scripted), write a `baseline.yaml` with `# TODO: <field-name> — bootstrap could not derive` for each missing field, plus a top-of-file summary listing all TODOs, and exit non-zero with a message pointing the user at the file.

The user can always override or extend the generated files by hand. Bootstrap's job is to get them to a working starting point, not to be authoritative.

### Where this lands relative to the rest of the model

- **Zero schema changes** to the workspace layout, the three dimensions, or the cluster/run/translation identities.
- **One new CLI command** (`sim2real translation register`).
- **One new mode flag** on the bootstrap skill (`--byo`).
- **No changes** to assemble, build, deploy, collect (modulo build's no-op detection).

### Open questions specific to BYO

1. **Workload library shipping with sim2real.** Today workloads live in the experiment repo. A BYO customer often has none. Ship a small library (`pipeline/scenarios/workloads/chat-low.yaml`, etc.) for `--byo` mode to reference? Or always require the customer to provide their own?
2. **Image-tag mutability.** `registered.json` should record `image_digest` at register time. If the customer pushes new content under the same tag, the digest mismatch is detectable but not enforced. Lock to digest by default (`--image-tag-lock`) or accept mutability?
3. **Validation depth at register.** Pulling the image manifest is cheap. Pulling the image and smoke-testing the EPP endpoint is much more involved. Defer the second to a separate `sim2real translation validate` command, available later.
4. **Multi-algorithm BYO.** Supporting `register --algorithm a --image ... --config ... --algorithm b --image ... --config ...` for customers with multiple algorithms in one image (or one image per algorithm). Probably worth supporting; affects the CLI shape.
5. **Listing and describing.** `sim2real list translations` should distinguish skill-produced from registered (BYO). `sim2real translation describe <name>` for a one-shot summary of any translation regardless of source.
