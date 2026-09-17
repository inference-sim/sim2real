# pipeline/

Scripts that drive the sim2real transfer pipeline. Run from the repo root.

The pipeline has two phases:

```
cluster.py init      (one-time per cluster — bootstrap identity, primary namespace, cluster-wide Tekton)
cluster.py slot add  (any time — grow the slot pool; live-propagates to running --remote orchestrators)
                   ↓
setup.py → sim2real translation register → sim2real assemble → deploy.py   (per-workspace + per-run)
```

`sim2real.py`'s `use` and `list runs` subcommands manage runs independently of the main flow.

---

## Running with an Experiment Repo

When algorithm content lives in its own repo (peer directory), pass `--experiment-root`:

```bash
# From the sim2real/ directory:

# First-time cluster bootstrap:
python pipeline/cluster.py init <cluster_id> <primary_namespace>

# Grow the pool later (safe while a run is in flight; live-propagates via --remote):
python pipeline/cluster.py slot add <cluster_id> <namespace>

# Per-workspace + per-run cycle:
python pipeline/setup.py       --experiment-root ../admission-control
python pipeline/sim2real.py translation register \
    --algorithm <name>=<image-ref>@<config-path>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run <run_name>
python pipeline/deploy.py      --experiment-root ../admission-control
```

The experiment repo must contain:
- `transfer.yaml` (or `config/transfer.yaml` for backward compat) — v3 schema with `baselines`, `algorithms`, `workloads` fields; `component:` required unless every algorithm carries `byo: true` (see [Per-algorithm `byo:` marker](#per-algorithm-byo-marker) below)
- `baselines/baseline.yaml` — llmdbenchmark-style scenario file (referenced from `transfer.yaml:baselines[0].scenario`). Filename is always `baseline.yaml` (issue #544).
- `baselines/defaults/*.yaml` (optional) — framework workaround fragments merged as an overlay under each baseline (opt out via `defaults.disable`)
- `workloads/` directory referenced from `transfer.yaml:workloads`
- `workspace/` in `.gitignore`

### Per-algorithm `byo:` marker

An algorithm entry may carry `byo: true` to indicate the image is pre-built and the entry has no BLIS-format source. When present:

- `algorithms[i].source:` is optional for that entry (the loader otherwise requires it).
- Top-level `component:` is optional when *every* algorithm entry carries `byo: true` — BYO manifests have no submodule to pin. Mixed manifests (any non-BYO entry) still require `component:`.

`sim2real translate` refuses to run on a BYO entry (there is no `.go` source for the skill to translate) and `sim2real build` refuses to run on an all-BYO manifest (there is nothing to build). Attach pre-built BYO images with `sim2real translation register` directly. `sim2real assemble` and `sim2real-check` treat BYO entries the same as BLIS entries at the consumption boundary.

`pipeline/pipeline.yaml` is the static Tekton Pipeline definition (applied by `cluster.py provision`; `sim2real assemble` generates PipelineRuns that reference it).

---

## cluster.py

Cluster-side bootstrap and slot-pool management. Bootstrap runs once per cluster; slot add/remove/list run whenever the pool needs to grow, shrink, or be inspected. Idempotent — safe to re-run any subcommand.

```bash
# First-time bootstrap: cluster identity + cluster-wide config + primary namespace
python pipeline/cluster.py init <cluster_id> <primary_namespace> [flags]

# Grow / shrink / inspect the slot pool
python pipeline/cluster.py slot add    <cluster_id> <namespace> [flags]
python pipeline/cluster.py slot remove <cluster_id> <namespace>
python pipeline/cluster.py slot list   <cluster_id>

# Backwards-compat sugar (equivalent to init + slot add loop):
python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,... [flags]
```

### `cluster init`

One-time bootstrap. Refuses when `workspace/clusters/<cluster_id>/cluster_config.json` already exists. Establishes the cluster's identity, records cluster-wide config, applies the cluster-wide Tekton Pipeline definition to the primary namespace, and provisions the primary namespace's resources.

| Flag | Env var | Default |
|------|---------|---------|
| `<cluster_id>` | — | required — slug matching `workspace/clusters/<id>/` |
| `<primary_namespace>` | — | required — pinned for the cluster's lifetime; holds the run-inputs / progress ConfigMaps |
| `--storage-class SC` | — | cluster default |
| `--hf-token TOKEN` | `HF_TOKEN` | prompt |
| `--github-token TOKEN` | `GITHUB_TOKEN` | optional |
| `--registry-user USER` | `REGISTRY_USER` | prompt |
| `--registry-token TOKEN` | `REGISTRY_TOKEN` | prompt |
| `--dockerhub-user USER` | `DOCKERHUB_USER` | optional |
| `--dockerhub-token TOKEN` | `DOCKERHUB_TOKEN` | optional |
| `--pipeline-yaml PATH` | — | `<repo-root>/pipeline/pipeline.yaml` |
| `--experiment-root PATH` | — | cwd |

### `cluster slot add / remove / list`

- **`slot add <cluster_id> <namespace>`** — provisions the namespace's cluster-side resources (namespace, RBAC, secrets, PVCs, Tekton tasks), appends it to `cluster_config['namespaces']`, applies the Pipeline definition to just that namespace, and patches the `sim2real-run-inputs` ConfigMap if a `--remote` orchestrator is currently running. Accepts the same credential flags as `init` (each namespace's Secrets are re-seeded from `HF_TOKEN` / `REGISTRY_TOKEN` env vars). Idempotent — re-adding an already-provisioned namespace is a no-op at every layer.

- **`slot remove <cluster_id> <namespace>`** — drain-only. Removes the namespace from `cluster_config['namespaces']` and patches the CM. No cluster-side changes — PVCs, Secrets, and Tekton resources stay so `deploy.py collect` continues to work against the removed slot's data and the slot can be re-added later without re-provisioning. Refuses primary (`namespaces[0]`).

- **`slot list <cluster_id>`** — read-only. Prints the pool with a `(primary)` marker on `namespaces[0]` plus a `provisioned=Y/N` probe (presence of the `sim2real-runner` ServiceAccount).

### Live remote propagation

`cluster init / slot add / slot remove` each call `publish_slot_pool` after mutating `cluster_config.json`. When the `sim2real-run-inputs` CM exists in the primary namespace (i.e. a `--remote` run is in flight), the mutation is patched into the `cluster_config--<cluster_id>` key of that CM. The orchestrator Pod's runtime container mounts this key directly (no `subPath`), so kubelet propagates the update within ~60s and the orchestrator's per-cycle `_refresh_namespaces` picks it up. When no CM exists (local mode, or between remote runs), the on-disk change is sufficient and is picked up on the next `deploy.py run --remote`'s assemble step.

### `cluster provision` (backwards-compat sugar)

Preserves the pre-#571 CLI. `provision <id> --namespaces N1,N2,N3` is equivalent to:

- Fresh cluster: `init <id> <N1>` + `slot add <id> <N2>` + `slot add <id> <N3>`.
- Existing cluster: `slot add <id> <Nk>` for each namespace not already in the pool.

`--storage-class` and `--pipeline-yaml` on `provision` apply only when the cluster is being initialized; they are ignored (with a warning) against an existing cluster because cluster-wide config is fixed at init time.

**`--pipeline-yaml PATH`** — override the Tekton Pipeline manifest applied to every namespace. Recorded in `cluster_config.json["pipeline_yaml"]` at init time. **Not sticky under sugar**: a re-run of `cluster.py provision <same-id>` without `--pipeline-yaml` warns and ignores the change; edit `cluster_config.json` by hand if you need to swap the manifest after init.

**Output:** `workspace/clusters/<cluster_id>/cluster_config.json` records:

- `cluster_id` — the slug passed on the command line
- `namespaces` — the provisioned slot list
- `is_openshift` — detected cluster flavor
- `storage_class` — PVC storage class
- `secret_names` — dict of Secret names: `hf_token`, `registry_creds`, `github_token`, `dockerhub_creds` (consumers read e.g. `cluster_config["secret_names"]["hf_token"]`)
- `workspaces` — Tekton workspace bindings; keys `data-storage` and `source` map to PVC claim names `data-pvc` and `source-pvc` respectively (`cluster_config["workspaces"]["data-storage"]["persistentVolumeClaim"]["claimName"] == "data-pvc"`)
- `pipeline_yaml` — optional Pipeline manifest override (only present when `--pipeline-yaml` was passed)
- `created_at` — first-write timestamp (preserved across re-runs)

**What it provisions per namespace:** namespace, RBAC bindings, Secrets (HF, registry, GitHub, Docker Hub), PVCs (data, source), Tekton tasks, and the cluster-wide Pipeline definition. Re-runs reconcile via `kubectl apply` — drift is overwritten.

**Boundary with `setup.py`:** anything operator-side (registry choice, repo name, orchestrator image, sim2real_root) belongs in `setup.py` and lands in `setup_config.json`. The `current_run` pointer in that same file is written by `sim2real use` (setup.py leaves it alone). Anything cluster-side (namespaces, RBAC, secrets, PVCs, Tekton tasks, Pipeline definition, Pipeline manifest override) belongs in `cluster.py provision` and lands in `cluster_config.json`. The three writers never overlap on the same key.

---

## setup.py

Workspace config writer. Writes `workspace/setup_config.json` with operator-side fields (registry, repo_name, orchestrator_image, sim2real_root). Idempotent.

Run-directory materialization is owned by `sim2real assemble`; setup.py does not touch `workspace/runs/`. The `current_run` field in `setup_config.json` is written by `sim2real use` — setup.py leaves any pre-existing value in place.

Cluster-side provisioning (namespaces, RBAC, secrets, PVCs, Tekton tasks, Pipeline definition) lives in `cluster.py provision` and writes a separate `workspace/clusters/<cluster_id>/cluster_config.json`. Run `cluster.py provision` before `sim2real assemble` / `deploy.py`. The Pipeline manifest override (`--pipeline-yaml`) lives on `cluster.py provision`, not here.

```bash
python pipeline/setup.py [flags]
```

| Flag | Env var | Default |
|------|---------|---------|
| `--registry REG` | — | interactive |
| `--repo-name NAME` | — | `llm-d-inference-scheduler` |
| `--registry-user USER` | `REGISTRY_USER` | interactive (with `--test-push`) |
| `--registry-token TOKEN` | `REGISTRY_TOKEN` | interactive (with `--test-push`) |
| `--experiment-root PATH` | — | current working directory |
| `--orchestrator-image IMAGE` | `ORCHESTRATOR_IMAGE` | `ghcr.io/inference-sim/sim2real/orchestrator:latest` |
| `--test-push` | — | false |
| `--test-push-tag TAG` | — | `_test-image-push` |

**`--test-push`** — optional workspace-scoped registry credential check (pull busybox, tag, push to `<registry>/<repo_name>:<test-push-tag>`, pull back). Skipped when no registry is configured or no container runtime (`podman`/`docker`) is found. `--registry-user` / `--registry-token` (or `REGISTRY_USER` / `REGISTRY_TOKEN`) gate the registry login. The cluster-side registry credentials Secret (name recorded in `cluster_config.json:secret_names.registry_creds` — default `registry-creds`) is created independently by `cluster.py provision`; see #435 for the dedup plan.

Cluster-scoped fields (`namespaces`, `is_openshift`, `storage_class`, `secret_names`, `workspaces`, `pipeline_yaml`) live in `workspace/clusters/<cluster_id>/cluster_config.json`, written by `cluster.py provision`. PVC bind state (`data-pvc`, `source-pvc`) is gated by `deploy.py`'s slot-readiness check before `deploy.py run` accepts a namespace slot.

---

## sim2real.py

Top-level CLI introduced in step-1 of the v2 refactor. Subcommands: `translation register` (BYO + assisted-BYO via `--build`), `translation append` (extend an existing translation with more algorithms), `translate` and `translate --resume` (skill-driven checkpoint), `build` (per-algorithm image build against a checkpointed translation), `assemble` (materialize a run), `use`, `list runs`, `list translations`.

### Register a translation (BYO)

`sim2real.py translation register` imports one or more algorithms as a single registered translation. Each `--algorithm` spec attaches a pre-built EPP image (classic BYO); each `--build` spec hands source (a local path or a `git+<url>#<ref>`) to buildkit and registers the resulting image (assisted-BYO). The two spec kinds are mixable in one invocation. Downstream commands (`assemble`, `deploy.py run`) treat every entry uniformly regardless of how the image was produced.

The `/sim2real-bootstrap --byo` skill scaffolds a BYO experiment repo and emits a ready-to-run `translation register` command with all algorithms batched into a single call — see [`.claude/skills/sim2real-bootstrap/SKILL.md`](../.claude/skills/sim2real-bootstrap/SKILL.md#--byo-mode).

#### Preferred form: repeatable `--algorithm NAME=IMAGE@CONFIG`

Pass `--algorithm` once per algorithm. Each value is a single `NAME=IMAGE@CONFIG` triple: first `=` splits the name from the rest; rightmost `@` splits the image ref from the config path. This allows digest refs (`image@sha256:…`) without ambiguity.

```bash
# N=2 example
python pipeline/sim2real.py translation register \
    --algorithm softreflective=ghcr.io/org/sr-router:v1@overlays/sr.yaml \
    --algorithm eager=ghcr.io/org/eager-router@sha256:<64-hex-digest>@overlays/eager.yaml \
    [--baseline-config path/to/baseline-overlay.yaml] \
    [--registered-hash <expected-sha256-hex>] \
    [--experiment-root PATH]
```

| Flag | Required | Notes |
|------|----------|-------|
| `--algorithm NAME=IMAGE@CONFIG` | at least one of `--algorithm` / `--build` (repeatable) | `NAME` follows `[A-Za-z0-9][A-Za-z0-9._-]*`, max 128 chars, no `.` / `..`. `IMAGE` is the registry ref. `CONFIG` is the treatment overlay YAML path. Rightmost `@` is the split point, so digest refs (e.g. `image@sha256:abc`) work. **Overlay paths containing `@` cannot be represented** (structural constraint — the `@` would be misread as part of the image ref). Overlay paths containing `=` are supported. When `--like` follows, the `@CONFIG` part may be omitted — the config is copied from the referenced algorithm. |
| `--build NAME=LOCATION@CONFIG` | at least one of `--algorithm` / `--build` (repeatable) | Assisted-BYO: framework materializes `LOCATION`, dispatches buildkit, records `image_ref`/`image_digest`. `LOCATION` is either a filesystem path or a `git+<url>#<ref>` URL. See "**`--build`: assisted-BYO**" below. Mixable with `--algorithm` in one invocation. When `--like` follows, the `@CONFIG` part may be omitted. |
| `--like NAME` | no (repeatable, per-spec) | Copy the config from an existing algorithm as the starting point for the immediately preceding `--algorithm` or `--build` spec. `NAME` must match another algorithm in this invocation (forward-refs OK). Mutually exclusive with the `@<config>` part of the spec. See "**`--like`: config-copy sugar**" below. |
| `--baseline-config PATH` | no | Baseline overlay YAML, if the translation needs one. |
| `--registered-hash HASH` | no | Assert the computed `translation_hash` equals this value; error if not. |
| `--force` | no | Reassign an alias if another translation already owns it. Applies per-algorithm for any N — use `--force` whenever any algorithm name in the batch was previously used as an alias by a different translation. For N>1 the newly-registered translation itself has `alias=null` (batched translations are referenced by hash); `--force` only clears the colliding aliases on the previous owners. |
| `--experiment-root PATH` | no | Defaults to cwd. |

#### `--build`: assisted-BYO source builds

For revisions of an algorithm that aren't yet a pre-built image — typically iterative refinements during upstream PR review — `--build` accepts a source location and hands off to the existing buildkit dispatch. The resulting image is registered exactly like a classic BYO entry; no source metadata is tracked beyond the git ref when applicable.

Two location shapes are supported. Framework auto-detects by prefix:

- **Filesystem path** — buildkit consumes the directory verbatim. No copy, no snapshot. The path's identity (for the translation hash) is a canonical SHA-256 over the directory contents (skipping `.git/`).
- **Git URL** (`git+https://…#<ref>` or `git+ssh://…#<ref>`) — framework shallow-clones into a scratch directory, checks out `<ref>` (commit sha, branch, or tag), then hands the scratch dir to buildkit. Records `source_git_url` and the resolved full commit sha as `source_git_ref` on the algorithm entry.

```bash
# Path-based --build: build from a local working tree.
python pipeline/sim2real.py translation register \
    --build pr1956=./llm-d-router@overlays/pr1956.yaml

# Git-URL --build: build from a specific commit/branch/tag.
python pipeline/sim2real.py translation register \
    --build pr1956b=git+https://github.com/kalantar/llm-d-router.git#pr/1956@overlays/pr1956b.yaml

# Mixable with --algorithm in one invocation.
python pipeline/sim2real.py translation register \
    --algorithm baseline=ghcr.io/foo/baseline:v1@overlays/baseline.yaml \
    --build pr1956=./llm-d-router@overlays/pr1956.yaml
```

**Prerequisites** (`--build` only — pure `--algorithm` invocations don't need these):

- `workspace/setup_config.json` must have `registry` and `repo_name` set (run `setup.py --registry <ref>`).
- A cluster must be provisioned with `cluster.py provision <cluster_id>` — `--build` uses the primary namespace and its registry-credentials Secret.

**Idempotency:** if the composed image ref (`<registry>/<repo>:<translation_hash[:12]>-<name>`) already exists in the registry with a resolvable digest, buildkit is skipped and the existing digest is recorded. Re-running the same command against the same source therefore short-circuits to a no-op after the first successful build.

**Recorded provenance:**

- Path-based `--build`: nothing beyond `image_ref` + `image_digest` (working-tree state is not reproducible; no honest identifier to capture).
- Git-URL `--build`: `source_git_url` (with any `user:password@` userinfo stripped — PAT-in-URL clone specs are safe to persist; bare `git@` ssh-style users are kept because `git` is a conventional identifier, not a secret) and `source_git_ref` (full commit sha, resolved via `git ls-remote` at register time). These land as optional fields on the algorithm entry in `translation_output.json`.

#### `--like`: config-copy sugar

For iterative refinement of an algorithm — typically as it evolves through upstream PR review — the treatment overlay is almost always unchanged from revision to revision; hand-copying YAML is friction. `--like <existing-algorithm-name>` copies another algorithm's `<name>_config.yaml` byte-for-byte as the starting point for the new algorithm, replacing the need to supply an inline `@<config>`.

`--like` binds to the immediately preceding `--algorithm` or `--build` spec on the command line. Repeatable — one `--like` per spec.

```bash
# Register a baseline + a --build that borrows the baseline's config.
python pipeline/sim2real.py translation register \
    --algorithm baseline=ghcr.io/foo/baseline:v1@overlays/baseline.yaml \
    --build     pr1956=./llm-d-router \
    --like      baseline

# Iterate: on the next revision, append pr1956b and reuse pr1956's config
# (which is on disk from the previous register call).
python pipeline/sim2real.py translation append --translation softr \
    --build pr1956b=./llm-d-router \
    --like  pr1956
```

**Rules:**

- Applies to the immediately preceding `--algorithm` or `--build` spec. `--like` before any spec is an error.
- At most one `--like` per spec.
- Mutually exclusive with the `@<config>` portion of the spec: `--build A=<src>@<cfg> --like X` is rejected.
- `<NAME>` accepts only algorithm names (`[A-Za-z0-9][A-Za-z0-9._-]*`), not paths or hashes.
- `--like → --like` chains are unsupported: the target algorithm must itself supply its config via an inline `@<config>` (for `register`, within the same invocation) or must already be registered in the target translation (for `append`).
- Self-reference (`--like` targeting the spec it's attached to) is rejected.

**Resolution scope:**

- `translation register` — the target must be another spec in the same invocation with an inline `@<config>`. Forward-references are allowed: the target may appear later on the command line.
- `translation append` — the target may be another spec in the same invocation (with an inline `@<config>`) OR any already-registered algorithm in the target translation (its `generated/<target>/<target>_config.yaml` on disk is read verbatim).

#### Deprecated form: `--algorithm NAME --image REF --config PATH`

The step-1 single-algorithm form still works as the N=1 case but **is deprecated**. A deprecation warning is emitted to stderr. Removal is targeted for step-5+.

```bash
# Deprecated — prefer the NAME=IMAGE@CONFIG form above
python pipeline/sim2real.py translation register \
    --algorithm softreflective \
    --image ghcr.io/kalantar-msb/sr-router:some-tag \
    --config path/to/treatment-overlay.yaml \
    [--baseline-config path/to/baseline-overlay.yaml] \
    [--registered-hash <expected-sha256-hex>] \
    [--experiment-root PATH]
```

**Outputs** — under `workspace/translations/<translation_hash>/`:

- `translation_output.json` — algorithm index + provenance. Top-level `alias` (set to the algorithm name for N=1; `null` for N>1). Per-algorithm `image_ref` / `image_digest` inside `algorithms[i]`. Step-1 legacy files (top-level `image_ref`) remain readable via a compatibility shim in `pipeline/lib/translation_ref.py`.
- `registered.json` — per-algorithm image refs and digests (BYO-only audit trail; batched shape with an `algorithms` list).
- `generated/<algorithm>/<algorithm>_config.yaml` — the treatment overlay content, one file per algorithm.
- `generated/baseline_config.yaml` — present only when `--baseline-config` is given.

**`translation_hash` derivation (BYO, batched):** SHA-256 hex over canonical JSON of a list of `{name, image, config}` dicts sorted by `name`, where `config` is the SHA-256 of the overlay file content and `image` is the digest ref if present (otherwise the raw ref string). Order-invariant — registering the same algorithms in a different order produces the same hash.

```
sha256(canonical-json(sorted-by-name list of {name, image, config}))
```

**Idempotency:** re-registering the same set of (algorithm, image, config-content) tuples — in any order — is a no-op. The existing translation directory is detected, a warning is printed, and exit is 0.

**Failure modes:**

- `--config` file missing or malformed YAML → exit 2, no writes.
- Existing translation directory records different algorithm names (hash collision) → exit 2, no writes.
- `--registered-hash` given and does not match computed → exit 2, no writes.
- Another translation already owns an `--algorithm` alias (an algorithm name from the batch collides with an existing translation's alias, on any N) → exit 2, no writes; re-run with `--force` to clear the previous owners' aliases before proceeding.
- Duplicate algorithm names within a single invocation → exit 2, no writes.

### Append to a translation

For iterative refinement of an existing translation — typically as an algorithm evolves through upstream PR review — `sim2real.py translation append` adds one or more algorithms to a translation already on disk. Same spec grammar as `translation register` (`--algorithm` for pre-built images, `--build` for source builds; mixable in one call). Reuses the same buildkit dispatch, name-validation, and atomic-write machinery.

```bash
python pipeline/sim2real.py translation append \
    --translation <alias|hash-prefix|hash> \
    --algorithm pr1956b=ghcr.io/kalantar/llm-d-router:v2@overlays/pr1956b.yaml \
    --build     pr1956c=git+https://github.com/kalantar/llm-d-router.git#pr/1956@overlays/pr1956c.yaml
```

| Flag | Required | Notes |
|------|----------|-------|
| `--translation REF` | yes | alias, hash prefix (min 4 chars), or full 64-char hash — same resolver as `assemble --translation`. |
| `--algorithm NAME=IMAGE@CONFIG` | at least one of `--algorithm` / `--build` (repeatable) | Same shape as `register`. When `--like` follows, `@CONFIG` may be omitted. |
| `--build NAME=LOCATION@CONFIG` | at least one of `--algorithm` / `--build` (repeatable) | Same shape as `register`. Prerequisites (skopeo, cluster + registry-creds Secret) are checked before any state change. When `--like` follows, `@CONFIG` may be omitted. |
| `--like NAME` | no (repeatable, per-spec) | Copy the config from an existing algorithm. `NAME` may resolve to another spec in this invocation (with an inline `@<config>`) OR any algorithm already registered in the target translation. See "**`--like`: config-copy sugar**" under `translation register` for the full rule set. |

**Design notes** (see #584 for full rationale):

- **`translation_hash` is NOT recomputed.** It remains the hash at first registration. Renaming the directory on each append would break every prior run's reference to the hash — worse than the drift. `list translations` surfaces the drift with an `APPENDS` column showing the number of append events.
- **Additive only.** Each call appends algorithms; there is no removal, no history editing, no rollback. Re-running the same append fails on the name-collision check (algorithm names are unique within a translation).
- **Atomic append.** Build dispatch runs before `translation_output.json` is rewritten. Any build failure raises with no state change — the on-disk file retains its pre-append content.
- **`append_history[]` audit trail.** Each call appends one entry per kind (`byo` or `build`) to a new optional `append_history[]` field in `translation_output.json`. Mixed-kind appends produce two entries (one per kind), both stamped with the same `appended_at` timestamp:

  ```json
  {
    "append_history": [
      { "appended_at": "2026-07-20T14:32:11Z", "algorithms": ["pr1956b"], "kind": "byo" },
      { "appended_at": "2026-07-20T14:32:11Z", "algorithms": ["pr1956c"], "kind": "build" }
    ]
  }
  ```

**Failure modes:**

- `--translation` does not resolve (unknown alias / hash prefix / full hash) → exit 2, no writes.
- Any new-algorithm name is already in the target translation → exit 2, no writes.
- Any new-algorithm name shadows another translation's alias → exit 2, no writes.
- Duplicate algorithm names within a single invocation → exit 2, no writes.
- `--build` prerequisites missing (skopeo binary, cluster + registry-creds Secret, `setup_config.json` with `registry` + `repo_name`) → exit 2, no writes.
- Buildkit dispatch failure → exit 2; prior on-disk `translation_output.json` is unchanged.

---

### Translate (skill-driven, step-2)

An alternative to BYO: `sim2real translate` writes a checkpoint that the `/sim2real-translate` Claude skill fills in with translated Go sources, then `sim2real translate --resume` validates the result. `sim2real build` (below) turns those sources into images.

```bash
python pipeline/sim2real.py translate \
    [--experiment-root PATH]              # initial run — write checkpoint
python pipeline/sim2real.py translate --resume
python pipeline/sim2real.py translate --force  # blow away and re-checkpoint
```

Two-phase state machine — the operator picks the lane explicitly (plain vs `--resume` vs `--force`). Plain `translate` never mutates a partial directory; use `--force` to clear one, `--resume` to validate a completed one.

| State at `translations/<hash>/` | `translate` | `translate --resume` | `translate --force` |
|---|---|---|---|
| Absent | Create dir, write `skill_input.json` + `translation_output.json` with `image_ref: null`. Print checkpoint. Exit 0. | Error: `no translation to resume`. Exit 2. | Same as plain. |
| Partial (checkpoint files present, some `generated/<algo>/<algo>_output.json` missing) | Error: `translation incomplete — run '/sim2real-translate' then 'translate --resume'`. Exit 2. Never mutates. | Reports missing algorithms by name. Exit 2. Never mutates. | Delete + recreate as if absent. |
| Complete (all `<algo>_output.json` present) | Print `translation <hash> (alias: <alias>) already complete — run 'sim2real build --translation <alias>' next` and exit 0. | Print `translation <hash> (alias: <alias>) complete — run 'sim2real build --translation <alias>' next` and exit 0. | Delete + recreate; operator re-runs the skill. |

The translation hash is derived from `transfer.yaml`'s translation slice (scenario, component, context, per-algorithm sources) plus the SHA-256 of each `algorithms[i].source` file's bytes (see `pipeline/lib/slicer.py:translation_hash_with_sources`). Two runs of `translate` with the same `transfer.yaml` and the same source files produce the same hash and reuse the same `translations/<hash>/` directory.

**Outputs** — under `workspace/translations/<translation_hash>/` (initial run only; the skill populates the rest):

- `skill_input.json` — the material `/sim2real-translate` reads. Pinned schema (see `docs/epics/step-2/design.md`) — includes `translation_hash`, absolute `experiment_root` and `translations_dir`, `scenario`, a `baselines[]` list (one entry per baseline that any algorithm's `defaults` cross-references, each carrying `name` and a `generated_overlay_path` under `generated/baselines/<name>/`), `algorithms[]` (each with `source_path`, `source_sha256`, `output_dir`, `config_output_path`, and a `baseline_overlay_path` resolved via `defaults`), and `context` (text + file paths).
- `translation_output.json` — algorithm index with `image_ref: null` on every entry. `sim2real build` fills these in later.
- `generated/<algo>/` (empty) — the skill writes `cmd/`, `pkg/`, `<algo>_output.json`, and `<algo>_config.yaml` under this directory.
- `generated/baselines/<name>/` (empty) — one directory per referenced baseline (under a `baselines/` umbrella; issue #544). The skill writes `baseline_config.yaml` under each. Shared by all algorithms whose `defaults` names that baseline.

Once `translate --resume` succeeds, run `sim2real build --translation <alias>` to compile and push images, then `sim2real assemble` as usual.

---

## Build translation images

Once a skill-driven translation is checkpointed (`sim2real translate` + `/sim2real-translate` skill run + `sim2real translate --resume`), `sim2real build` compiles the per-algorithm plugin sources into container images and pushes them to your registry. Each algorithm gets its own image tagged `<translation_hash[:12]>-<algorithm_name>`.

```bash
python pipeline/sim2real.py build \
    --translation REF \
    [--force-rebuild] \
    [--skip-build] \
    [--experiment-root PATH]
```

| Flag | Required | Notes |
|------|----------|-------|
| `--translation REF` | yes | alias, hash prefix (min 4 chars), or full 64-char hash — same resolver as `assemble --translation`. |
| `--force-rebuild` | no | Rebuild and re-push every algorithm even if the registry already has the tag (skips the pre-build probe). |
| `--skip-build` | no | Skip all probe + build activity. Downstream `sim2real assemble` will fail if any `image_ref` is still null. Useful when you know the images already exist and want to bypass the probe. |

**Prerequisites (fail-early):**

- `skopeo` on `PATH` (`brew install skopeo` / `apt install skopeo` / `dnf install skopeo`). Not required when `--skip-build` is set.
- Translation completeness: every algorithm listed in `translations/<hash>/translation_output.json:algorithms[]` (written by `sim2real translate`) must have `translations/<hash>/generated/<algo>/<algo>_output.json` on disk. Run `/sim2real-translate` first if this is missing.
- `workspace/setup_config.json` has non-empty `registry` and `repo_name` (populated by `setup.py`).
- A single provisioned cluster (`workspace/clusters/<id>/cluster_config.json`) with at least one namespace slot — buildkit runs in `namespaces[0]`.
- `<experiment-root>/<repo_name>/` exists and contains the component source.

**Behavior per algorithm:**

1. Compose `image_ref = <registry>/<repo_name>:<translation_hash[:12]>-<algo>`.
2. **Idempotency short-circuit**: if the algorithm's recorded `image_ref` already equals the composed value AND `image_digest` is non-null AND `--force-rebuild` is not set, skip. Prints `already built: <ref> (<digest>)`.
3. **Pre-build registry probe**: `skopeo inspect docker://<ref>`. Success returns the digest; the digest is written back to `translation_output.json` and the build is skipped. Any failure (network, auth, missing tag, timeout) is treated as "absent → build" (fail-safe).
4. **Buildkit dispatch**: submits an in-cluster `moby/buildkit:latest` pod that reads component source from a PVC and pushes to the target registry via the credentials Secret whose name is recorded in `cluster_config.json:secret_names.registry_creds` (default `registry-creds`, provisioned by `cluster.py provision`), via `pipeline/scripts/build-epp.sh`; the Secret name is threaded in via `--registry-secret-name`.
5. **Post-build digest inspect**: `skopeo inspect` runs a second time to record the pushed digest. On success, `image_digest` is set. On failure, the build is still considered successful (the image was pushed); `image_digest` is recorded as `null` with a warning. Digest can be back-filled by a later `sim2real build --force-rebuild`.
6. **Atomic writeback**: `translations/<hash>/translation_output.json:algorithms[i].image_ref` and `image_digest` are written after every algorithm via tempfile-and-rename. A mid-run failure preserves prior algorithms' recorded state.

Return code: `0` on all-success (including all-idempotent). `2` on any prereq failure or the first build failure (loop stops there — subsequent algorithms are not attempted).

**Common failure modes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| `skopeo not found on PATH` | Local prereq missing | Install skopeo per the hint in the error message. |
| `translation <hash> incomplete — missing outputs for: X` | `/sim2real-translate` has not been run for algorithm X | Run `/sim2real-translate`, then `sim2real translate --resume` to verify, then `sim2real build`. |
| `workspace/setup_config.json is missing 'registry' or 'repo_name'` | `setup.py` was not run, or was run without `--registry` | `python pipeline/setup.py --registry quay.io/username --repo-name <repo>` |
| `build failed for <algo> (image_ref=...)` | Buildkit pod exited non-zero — usually a compile error, missing PVC, or bad registry credentials Secret (see `cluster_config.json:secret_names.registry_creds`) | Inspect `kubectl logs epp-build-sim2real-build-<hash8>-<algo> -n <namespace>` for the compiler output. |
| `translation <ref> not built for algorithms: X` when running `sim2real assemble` | You ran `assemble` before `build` | Run `sim2real build --translation <ref>` first. |

---

## Assemble a run

Once a translation is registered, `sim2real assemble` produces a run directory under `workspace/runs/<run>/` containing the resolved scenario YAMLs, generated PipelineRun manifests, an assembly-slice snapshot, and per-run metadata.

```bash
python pipeline/sim2real.py assemble \
    --translation REF \
    --cluster CLUSTER_ID \
    --run RUN_NAME \
    [--replicas N] \
    [--workload NAME ...] [--package NAME ...] \
    [--force] [--no-wipe] [-y|--yes]
```

| Flag | Required | Meaning |
|------|----------|---------|
| `--translation REF` | yes | Alias, hash prefix (min 4 chars), or full hash. |
| `--cluster CLUSTER_ID` | yes | Matches `workspace/clusters/<id>/`. |
| `--run RUN_NAME` | yes | Directory created at `workspace/runs/<run>/`. |
| `--replicas N` | no | Iterations per (workload, package) pair. Default 1. Cannot be combined with `--workload` / `--package`. |
| `--workload NAME ...` | no | Scope to these workloads. Comma- or space-separated; shell globs accepted. Requires an existing run. |
| `--package NAME ...` | no | Scope to these packages (`baseline` or an algorithm name). Same grammar as `--workload`. |
| `--force` | no | Re-generate PipelineRuns for pairs in scope that already have one, discarding their collected results unless `--no-wipe`. |
| `--no-wipe` | no | Keep the collected results of pairs being re-generated. |
| `-y`, `--yes` | no | Skip the confirmation prompt before deleting collected results. |

`--translation` accepts an alias, a hash prefix (min 4 chars), or the full 64-char hash. Aliases are checked before prefixes, so a 4-char alias always resolves to its exact owner rather than a colliding hash prefix. Ambiguous prefixes exit 2 listing the candidates. Run `sim2real list translations` to see what's available.

**Inputs read:**

- `workspace/translations/<hash>/translation_output.json` — algorithms with per-algo `image_ref`. Legacy step-1 files (top-level `image_ref`) are read transparently via `pipeline/lib/translation_ref.py`'s on-read shim.
- `workspace/translations/<hash>/generated/baselines/<name>/baseline_config.yaml` — per-baseline overlay (skill-driven; written by `/sim2real-translate` for each baseline that any algorithm's `defaults` names). Nested under a `baselines/` umbrella (issue #544). Assemble applies each entry to its matching `manifest.baselines[]` entry.
- `workspace/translations/<hash>/generated/baseline_config.yaml` — legacy BYO overlay (written by `translation register --baseline-config`). Applied to every baseline in the manifest when the per-baseline directory above is absent — falls back automatically so BYO translations remain resolvable.
- `workspace/translations/<hash>/generated/<algo>/<algo>_config.yaml` — per-algorithm treatment overlay.
- `workspace/clusters/<cluster_id>/cluster_config.json` — namespaces, workspace bindings, hf secret name.
- `<experiment-root>/transfer.yaml` (or `config/transfer.yaml`) — v3 manifest.
- `<experiment-root>/baselines/baseline.yaml` — baseline bundle referenced by `transfer.yaml:baselines[0].scenario`. The baseline identifier is always the literal string `baseline` (issue #544).
- `<experiment-root>/baselines/defaults/*.yaml` — framework defaults overlays (opt-out via `transfer.yaml:defaults.disable`).
- `<sim2real-repo>/.gitmodules` and `<sim2real-repo>/{inference-sim,llm-d-benchmark}/` — the framework submodules' clone URLs (from `.gitmodules`) and HEAD SHAs (from `git rev-parse HEAD`), which populate `benchmarkGitRepoUrl` / `benchmarkGitCommit` / `blisGitRepoUrl` / `blisGitCommit` in every generated PipelineRun. Initialize with `git submodule update --init` in the sim2real repo before running `sim2real assemble`; a missing submodule falls back to `"unknown"` for its commit SHA (assemble prints a warning) and the cluster-side `git clone` step then fails visibly at the right point.

**Outputs written to `workspace/runs/<run>/`:**

| File | Purpose |
|------|---------|
| `manifest.assembly.yaml` | Verbatim snapshot of the assembly slice from `transfer.yaml` (produced by `pipeline/lib/slicer.py`), preceded by a top-level `replicas: N` field. |
| `run_metadata.json` | `{version, run_name, translation_hash, cluster_id, params_hash, image_tag, replicas, assembled_at, scenario}` — pinned schema, `version: 1`. `scenario` is the value of `transfer.yaml:scenario`, threaded through by `deploy.py` to name the progress ConfigMap `sim2real-progress-{scenario}-{run}` (issue #551). |
| `cluster/baseline.yaml` | Resolved baseline scenario (framework defaults → bundle → baseline overlay). |
| `cluster/<algo>.yaml` | Resolved treatment scenario per registered algorithm (baseline_resolved → treatment bundle diffs → algo overlay → injected image_tag). |
| `cluster/pipelinerun-<workload>\|<package>\|iN.yaml` | One PipelineRun per (workload, package, iteration) tuple. Consumed by `deploy.py run`. |

**Assembly formula** (deep-merged via `pipeline/lib/values.py:deep_merge`):

```
baseline_resolved  = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(baseline_resolved, treatment_bundle_diffs, algo_overlay)
```

Then each treatment scenario has `router.epp.image` set from that algorithm's own `translation_output.json:algorithms[i].image_ref` (split into `registry` + bare `repository` fields for the routerlib chart's expected shape), and every scenario has `huggingface.secretName` set from `cluster_config.json:secret_names.hf_token`.

**`params_hash`** is SHA-256 over the canonical bytes of `manifest.assembly.yaml` with the top-level `replicas` field excluded — bumping `--replicas N` must not change the hash. Recorded in `run_metadata.json` for drift detection on re-assemble.

**Replicas.** `--replicas N` (default 1) is the number of iterations per (workload, package) pair. Each iteration gets its own PipelineRun (`{phase}-{workload}-{run}-iN`, with `_` → `-` normalization) and its own results subdirectory (`results/<phase>/<workload>/iN/`). `manifest.assembly.yaml` carries a top-level `replicas: N`, and `run_metadata.json` carries the same value as a schema field. Both are set on every assemble.

**Additive-merge (grow-only).** Re-assembling an existing run with `--replicas` interacts with the prior state as follows (without `--force`):

- `N == prior_replicas` — nothing to regenerate. No files rewritten. The CLI prints `run '<run>': <n> pair(s) already assembled, nothing to do.` plus the flags that would change that. Note what this message does *not* claim: that the inputs are unchanged. `params_hash` covers only `transfer.yaml`'s own fields, and the translation is not compared at all, so assemble is in no position to assert it (issue #876). The past-tense `assembled run <name>` ack is reserved for paths that touched disk.
- `N > prior_replicas` — additive grow. Existing PipelineRun files (`i1..i{prior}`) are preserved byte-for-byte and by mtime; new files are emitted for `i{prior+1}..iN`. `manifest.assembly.yaml` and `run_metadata.json` are rewritten with the new `replicas` count; `params_hash` is preserved (drift check passed).

  **Each new iteration is a copy of that pair's own `i1`**, with exactly two fields rewritten: the `-i<N>` suffix on `metadata.name` and the `replica` param. `resultsDir` threads `i$(params.replica)`, so it follows automatically. Grow reads no overlay, baseline, defaults, or workload YAML.

  This is what makes replicas actually replicate (issue #877). Grow used to re-run the whole resolution pipeline, so an overlay edited between two assembles produced `i2..iN` carrying a different plugin config than the preserved `i1` — with `params_hash` still matching, exit code 0, and no warning. Replicas exist to measure variance under a fixed condition; if the iterations differ, the variance figure measures nothing. Copying `i1` removes the hazard by construction rather than detecting it, and preserves legitimate per-pair differences for free, since each pair grows from itself.

  Consequences worth knowing:

  - A pair that has other iterations but no `i1` is **refused**, naming the pair. Falling back to resolution for that pair would reintroduce exactly the divergence this avoids.
  - Pre-existing divergence is **not repaired**. New iterations are made to match `i1`; an already-mismatched `i2` from an earlier grow stays mismatched.
  - Scalar-list conflicts (#851) are **not reported** on this path, because nothing is merged. The next full assemble, which does merge, still reports them.
  - The input validation that came free with resolution is gone: a deleted baseline scenario file no longer fails a grow. The translation directory and `translation_output.json` are still checked for *existence* ahead of the decision tree, so a deleted translation still refuses; a *corrupt* `translation_output.json` is caught by the CLI's own unbuilt-image pre-check, making that a CLI-level rather than library-level guarantee.
  - `metadata.name` is re-validated against the 253-char limit, since `i9` → `i10` adds a character and can push a name that fit over it.
  - Every grown document is built and validated **before the first file is written**, so a refusal on any pair leaves the run untouched rather than half-grown. This matters because `run_metadata.json` is only advanced after the whole batch, and `deploy` discovers iterations by globbing `cluster/pipelinerun-*.yaml` — a partial write would hand it iterations the metadata never claimed.
  - A `cluster/` file whose name does not parse as `pipelinerun-<workload>|<package>|i<N>.yaml` is skipped, and its pair therefore does not grow. Each is named in a warning, since the only other symptom would be results missing later.
- `N < prior_replicas` — refused with `run '<name>' already has <prior> replicas; refusing to shrink to <N>. Replica shrink is tracked in #506.` This guard runs BEFORE every other check, so `--force` does NOT bypass it.

Two invariants shape the grow-only path:

- **Drift check.** The current assembly-slice content hash is compared against the run's recorded `params_hash`. Any mismatch refuses the assemble unless `--force` is passed. Without `--force`, matching hashes are required to reach the additive-grow branch.
- **Legacy-run guard.** A pre-step-5 run has no `replicas` field in its `manifest.assembly.yaml`. Any re-assemble against this shape is refused unless `--force`, whether or not `--replicas` was explicitly passed. With `--force`, the run's metadata is rewritten as a fresh replica-shaped run.

### Re-assembling: `--force`, `--no-wipe`, and scope

`--force` does **not** delete the run directory. It used to (`shutil.rmtree` on every branch of the decision tree), and since `results/` lives inside the run directory, the documented escape hatch for "rebuild this run" also destroyed measured cluster data that cannot be recovered without re-running (issue #876).

Re-assembly is now decided per `(workload, package, iteration)` triple, over the scope selected by `--workload` / `--package`, along two orthogonal axes:

| axis | question | flag |
|------|----------|------|
| regenerate | this PipelineRun already exists — redo it? | `--force` |
| results | results exist for a pair being regenerated — keep them? | `--no-wipe` |

Giving four cases per triple. Both predicates are plain `Path.exists()` checks — on `cluster/pipelinerun-<wl>\|<pkg>\|iN.yaml` and `results/<pkg>/<wl>/iN/` respectively. No new state, no content hashing, no `run_metadata.json` schema change.

| PipelineRun exists | results exist | behavior |
|---|---|---|
| no | no | generate |
| no | yes | generate; wipe results unless `--no-wipe` — **prompts first** |
| yes | no | nothing unless `--force` |
| yes | yes | nothing unless `--force`; when `--force`, wipe results unless `--no-wipe` |

The wipe axis only applies to pairs actually being regenerated: a pair left alone keeps its results regardless of flags.

**Why only row 2 prompts.** Row 2 regenerates without the operator having typed any flag, so an unannounced wipe would destroy measured data nobody authorized. The prompt enumerates the target directories and reads `[y/N]`, mirroring `deploy wipe`; `--yes` skips it, EOF declines, and declining aborts with exit 2 having written nothing. `--force` stays non-interactive because discarding results is its long-documented behavior and prompting would break scripted use — `--force --no-wipe` is the way to regenerate YAML while keeping data.

**Scoped assemble.** Passing `--workload` and/or `--package` narrows the pairs considered. Both accept comma- or space-separated values and shell globs, using the same grammar as `deploy` (literally the same code — `pipeline/lib/scope.py`). A value matching nothing exits 2 listing the valid values. Scope is derived from the manifest cross product, not from `deploy`'s progress store, which needs a cluster namespace that assemble does not have.

A scoped assemble:

- **requires an existing run** — there is no subset of a run that does not exist yet;
- **rejects `--replicas`** and reuses the run's recorded count, so narrowing the scope cannot trip the shrink guard;
- **refuses on `transfer.yaml` drift**, even with `--force`, and points at an unscoped assemble;
- **refuses to repair** missing, corrupt, or legacy run metadata, and points at an unscoped `--force`;
- **rewrites `cluster/<package>.yaml`** for the packages in scope. These are advisory copies — the authoritative scenario is inlined into each PipelineRun — read by `deploy` for GPU-config derivation and by `/sim2real-check` for config parity;
- **leaves `manifest.assembly.yaml` and `run_metadata.json` untouched**, so `params_hash` keeps meaning "the last state in which the *whole* run was consistent" and a later unscoped assemble still detects drift rather than reporting nothing to do;
- **prunes nothing** — every PipelineRun outside the scope stays byte- and mtime-identical.

**Orphan pruning (unscoped only).** Because the run directory is no longer cleared, an unscoped assemble explicitly deletes `cluster/` files the current `transfer.yaml` no longer describes: PipelineRuns whose triple is outside the cross product, and per-package scenario YAMLs for packages that are gone. Without this, a manifest edit that drops a workload would leave its PipelineRun on disk for `deploy`'s `pipelinerun-*.yaml` glob to find and execute. Each removal prints a warning naming the file. The pruned pairs' `results/` are deliberately left in place.

### Ordering, and why the destructive steps are safe

Everything that can refuse happens before anything is deleted:

1. **PipelineRuns are built in memory first.** This is where the 253-char name limit is enforced, so an over-long name aborts while the results are still on disk. Building after the wipe would strand the pair with neither results nor a manifest.
2. **Pruning precedes the results wipe.** Both are deletions, and pruning is the cheap one — regenerable YAML rather than measured data — so a read-only filesystem or permissions problem aborts there, results intact.
3. **Every write goes through one helper** that turns `OSError` into `AssembleError` naming the file. A disk-full failure after the wipe is then a legible error rather than a raw traceback, which is the only thing telling the operator what state the run is in.

The results-wipe failure message also names how many directories were already removed, since nothing has been regenerated to replace them.

**Filter-value normalization.** `--workload` / `--package` values are compared with `_` normalized to `-`, so either spelling of a workload name matches. If two *declared* names differ only by that character they are indistinguishable to a filter, and assemble refuses rather than silently selecting one of them — such a manifest is already broken (both names produce the same `cluster/pipelinerun-*.yaml` filename), but a scope filter must not be what discovers it by deleting the wrong pair's results. The check only runs when a filter is given, so it does not retroactively reject an unscoped assemble.

**PipelineRun name length.** `metadata.name` is `{phase}-{workload}-{run}-i{iteration}`. `phase` and `workload` are normalized (`_` → `-`) before assembly; `run` is used verbatim, so a run name containing underscores would produce an invalid DNS subdomain. This is a Kubernetes DNS subdomain, so the 253-char RFC 1123 limit applies. `assemble` validates each generated PipelineRun name and exits 2 with `error: PipelineRun name '<name>' is <len> chars, exceeds the 253-char DNS subdomain limit` if any pair (phase × workload × run × iteration) would overflow. The validator only checks length, not character validity — pick a kebab-case `--run` name to stay within DNS rules. Fail-fast at assemble time is preferable to Tekton admission rejection at dispatch time.

**Algorithm filtering:** algorithms listed in `transfer.yaml:algorithms` but absent from `translation_output.json:algorithms` are skipped with a warning — the run still assembles for the algorithms that are registered. Algorithms that ARE in the translation but whose `image_ref` is still `null` (skill-driven translation before `sim2real build`) fail fast: `assemble` exits 2 with `translation <ref> not built for algorithms: <names> — run 'sim2real build --translation <ref>' first`.

**Failure modes:**

- Existing `runs/<run>/` whose `manifest.assembly.yaml` or `run_metadata.json` is missing or corrupt, without `--force` → exit 2 with `--force` hint. No writes.
- Missing translation directory or `translation_output.json` → exit 2, no writes.
- Missing `cluster_config.json` for `--cluster` → exit 2, no writes.
- Workload file referenced in `transfer.yaml:workloads` missing → exit 2, no writes.
- Malformed YAML anywhere in the input chain → exit 2, no writes.
- `--workload` / `--package` value matching nothing → exit 2 listing the valid values. No writes.
- `--workload` / `--package` against a run that does not exist, or alongside `--replicas` → exit 2. No writes.
- `--workload` / `--package` given when two declared names normalize to the same string → exit 2 naming both. No writes.
- Declining the results-wipe prompt → exit 2, no writes.
- A `cluster/` file the manifest no longer describes cannot be deleted → exit 2, no writes and no results wiped.

Validation and the wipe confirmation both run before anything is written, so every failure above leaves the run directory exactly as it was.

---

## deploy.py

Orchestrates PipelineRun execution across namespace slots. Operates independently of `transfer.yaml` — driven by workspace files, `setup_config.json` (workspace-scoped), and `clusters/<id>/cluster_config.json` (namespaces, PVCs, secrets).

```bash
python pipeline/deploy.py {run|status|collect|stop|reset|wipe|pairs} [flags]
```

Common flags (all subcommands):

| Flag | Default | Notes |
|------|---------|-------|
| `--run NAME` | from `setup_config.json` | override active run |
| `--experiment-root PATH` | cwd | path to experiment repo |

Images are built by `sim2real build` (step-2, per-algorithm) before `sim2real assemble`; `deploy.py` consumes the already-built image refs from the resolved `cluster/` scenarios and does not build.

**Pair keys.** A pair key names a `(workload, package, iteration)` triple in the ConfigMap and on disk. Canonical grammar:

```
pair_key := "wl-" workload "|" package [ "|" iter ]
workload := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # kebab-case, no leading/trailing hyphen
package  := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # same shape as workload
iter     := "i" [1-9][0-9]*                  # positive decimal, no leading zeros; i0 is invalid
```

Example: `wl-chat-mid|baseline|i1`.

The parser accepts a legacy no-suffix form (`wl-<workload>|<package>`) and reads it as `iteration=1`; canonical renderings always include the `|iN` suffix. Metadata keys (`_meta`, `_notes`, anything starting with `_`) are filtered out upstream via `deploy._is_pair_key` and never reach the parser.

**Terminology.** The second segment is called `<package>` here (matching `pairkey.py`) and `<phase>` in the `sim2real-check` output rollup — they refer to the same segment (`baseline`, `<algorithm-name>`, …).

**Pair discovery.** `deploy.py run` discovers `pipelinerun-*.yaml` files at the `cluster/` root. Each file's pair key is derived as `wl-` + filename stem minus the `pipelinerun-` prefix — the assembler names files as `pipelinerun-<workload>|<package>|iN.yaml`, so the pair key falls out directly.

**Scoping flags on filter-aware subcommands** (`run`, `status`, `collect`, `reset`, `wipe`):

| Flag | Scope | Notes |
|------|-------|-------|
| `--only PAIR…` | Full pair keys (with or without `wl-` prefix) | Narrows both workload and package. Takes precedence over `--workload`. |
| `--workload NAME…` | Workload dimension | Multiple values are OR'd within the flag. Glob patterns supported (see below). |
| `--package NAME…` | Package dimension | Multiple values are OR'd within the flag. Glob patterns supported (see below). |
| `--iteration SPEC` | Iteration dimension | Grammar below. |
| `--status STATE…` | Progress state (`pending` / `running` / `done` / `failed` / `timed-out` / `stalled`) | Multiple values are OR'd within the flag (comma or space-separated). Not available on every subcommand — see per-subcommand tables. |

Different flags compose as AND: `--workload X --package baseline --iteration 1,3` narrows to iterations 1 and 3 of workload X's baseline package.

**Glob patterns in `--workload` and `--package`** (issue #518). Values containing `*`, `?`, or `[` are matched against the valid set with Python's `fnmatch` (standard shell-glob, no regex). Values without those metacharacters are literals (backwards-compatible). Examples: `--workload 'code_generation_*'`, `--workload 'code*'`, `--workload code_generation_4 'reasoning_0_*'` (literal + glob). A pattern matching zero valid names fails fatally with the same "unrecognized values" error as an unknown literal. For `collect --package`, the synthetic `experiment` value remains literal-only — a pattern like `exp*` will NOT match it (pass `experiment` verbatim to keep today's expand-to-all-known-phases behavior).

**Iteration filter spec.** The `--iteration` value is a comma-separated list of tokens; each token is either a positive integer (`3`) or an inclusive range (`1-3`). Whitespace around commas and hyphens is tolerated. Rejected: `0`, negatives, reversed ranges (`5-1`), non-integer tokens (`abc`), leading zeros (`01`), empty spec, empty token. Malformed specs fail with `malformed iteration spec '<spec>': <reason>` before any pair discovery runs. Legacy pair keys (no `|iN` suffix) parse as iteration `1`, so `--iteration 1` matches them.

**Collection phases** — `deploy.py collect` derives valid phases dynamically from progress data (packages with status `done`). Falls back to `[baseline, treatment]` when no progress exists. Use `--package` (literals or globs) to filter, or `--package experiment` to collect all known phases. Note: globs (`base*`, `*`) never match the `experiment` magic token; pass it as a literal.

**Subcommands:**

```bash
python pipeline/deploy.py run     [flags]   # orchestrate parallel pool execution
python pipeline/deploy.py status            # show progress snapshot of all (workload, package, iteration) triples
python pipeline/deploy.py collect [flags]     # pull results from the cluster PVC
python pipeline/deploy.py stop               # stop the remote orchestrator Job
python pipeline/deploy.py reset [flags]     # reset all non-pending pairs to pending (with cluster cleanup)
python pipeline/deploy.py wipe  [flags]     # delete local result files for pairs in scope
python pipeline/deploy.py pairs   [flags]   # list available pair keys, workloads, and packages
```

**`deploy.py run`** — assigns `(workload, package, iteration)` triples to free namespace slots, polls for completion, and retries pairs that time out. Reads progress from the `sim2real-progress-{scenario}-{run}` ConfigMap (scenario + run scoped, so cross-experiment-root runs sharing a run name do not collide — issue #551) to resume interrupted runs. There is no automatic migration from the pre-#551 `sim2real-progress-{run}` name; operators clean those up manually with `kubectl delete cm sim2real-progress-<run> -n <ns>` when they want to. Requires a configured namespace. Use `deploy.py collect` to pull results off-cluster after runs complete. The run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`; if the run directory or its `cluster/` subdirectory does not exist, `deploy.py run --run <R>` exits with `run 'sim2real assemble --run <R>' first`, and if `run_metadata.json` is missing, unparseable, or lacks a non-empty `cluster_id`, it exits with `run metadata corrupted; re-assemble`.

| Flag | Default | Description |
|------|---------|-------------|
| `--remote` | — | Submit orchestrator as in-cluster Job instead of running locally |
| `--only PAIR…` | — | Scope execution to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | — | Scope execution to pairs matching these workloads (comma or space-separated; globs OK) |
| `--package NAME…` | — | Scope execution to pairs matching these packages (comma or space-separated; globs OK) |
| `--iteration SPEC` | — | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE…` | — | Scope execution to pairs matching these statuses (comma or space-separated; e.g. `failed`, `timed-out`) |
| `--skip-teardown` | — | Skip the Tekton teardown task, leaving namespace resources intact for debugging |
| `--preserve-pipelineruns` | — | Do not delete PipelineRun objects after completion (keeps TaskRun logs for debugging) |
| `--force` | — | Reset non-pending pairs to `pending`, cleaning cluster resources (PipelineRuns + Helm) for pairs with assigned namespaces |
| `--max-retries N` | 2 | Max retries for timed-out pairs |
| `--poll-interval N` | 30 | Seconds between status polls |
| `--gpu-resource-type` | auto-derived | Override GPU resource name (derived from scenario's `accelerator.resource`, else `nvidia.com/gpu`) |
| `--default-gpu-cost N` | 1 | Fallback GPU cost per pair when not derivable from scenario |
| `--pending-threshold N` | 600 | Seconds a pod may remain Pending (recoverable reason) before early reclaim |
| `--max-pending-stalls N` | 10 | Max early reclaims before marking pair `stalled` |
| `--dispatch-cooldown N` | 15 | Seconds to wait after a dispatch batch before dispatching again (0 to disable) |

**Dispatch cooldown** — after dispatching ≥1 pair, the orchestrator skips new dispatch for `--dispatch-cooldown` seconds. This prevents over-subscription when the GPU capacity probe hasn't yet reflected recently-dispatched workloads (typical probe lag: 10-20s). Completion/failure polling continues unaffected during cooldown. Set to 0 to disable.

**Early reclaim** — on each poll cycle, pods in `Running`/`Started` PipelineRuns are checked for scheduling failures. Recoverable reasons (e.g. `Insufficient nvidia.com/gpu`) trigger early reclaim after `--pending-threshold` seconds. Non-recoverable reasons (e.g. node affinity mismatch, PVC not found) fail the pair immediately. Each early reclaim increments `pending_stalls`; at `--max-pending-stalls` the pair transitions to `stalled` (terminal).

**Polling and slot-aware probe skipping** — the orchestrator always sleeps at `--poll-interval` (default 30s); there is no backoff state machine. On each cycle it first checks the status of every busy slot's PipelineRun. The GPU capacity probe and dispatch computation run only when at least one slot is free; when all slots are busy the cycle logs `Dispatching 0/<pending> pending — all <total> slots busy` (deduped per state transition) and skips the probe, so slot recovery is detected within one poll interval. The base interval is itself the rate limit against hot-loop reclaim cycles. (Old progress files may still carry a legacy `_orchestrator` metadata key; it is ignored.)

**Live slot-list updates (issues #372, #571)** — each cycle re-reads `workspace/clusters/<cluster_id>/cluster_config.json` and updates the in-memory slot list. Adding a namespace makes it eligible for dispatch on the next cycle (subject to `_check_slot_ready` — PVCs bound, HF secret present); removing a namespace stops new dispatch to it but does **not** cancel a pair already running there — that pair drains and the slot is freed normally on completion. `namespaces[0]` (the primary, where the run-scoped progress ConfigMap lives) is pinned for the lifetime of the run; mid-run changes to it are logged and ignored. Parse / IO errors keep the prior list. The operator-friendly way to grow the pool is `cluster.py slot add <cluster_id> <namespace>` (provisions the namespace, appends to the pool, patches the run-inputs ConfigMap if a `--remote` orchestrator is running); to shrink it is `cluster.py slot remove <cluster_id> <namespace>` (drain-only, no cluster-side changes so `deploy.py collect` continues to work). Under `--remote`, the orchestrator Pod's runtime container mounts the `cluster_config--<cluster_id>` key from the `sim2real-run-inputs` ConfigMap without a subPath, so kubelet propagates the update to the Pod within ~60s and `_refresh_namespaces` picks it up on its next cycle.

**Capacity probe filtering** — `pipeline/lib/capacity.py` filters cluster nodes the same way the K8s scheduler would before summing allocatable/requested GPUs. A node is excluded if cordoned (`spec.unschedulable: true`), if it carries a `NoSchedule`/`NoExecute` taint that no role's tolerations match, or if its `nvidia.com/gpu.product` label is not in the set required by the scenario. The required product set is read from `scenario[0].{decode,prefill}.acceleratorType.{labelKey, labelValue}`. When no per-role product constraint can be extracted, cordon and taint screening still apply on cluster facts. Tolerations are currently treated as empty per follow-up issue #263 — every blocking taint excludes the node until that's lifted.

**Pair statuses:** `pending` → `running` → `done`. Failure paths: `running` → `failed` (hard failure or non-recoverable pending), `running` → `timed-out` (4h timeout exceeded), `running` → `pending` (recoverable early reclaim, repeats up to `--max-pending-stalls` times) → `stalled`.

**Auto-cleanup** — when a PipelineRun succeeds, the orchestrator deletes the PipelineRun CR from the cluster. Failed PipelineRuns are left in place for debugging (`kubectl describe`, pod logs). Use `reset` to remove them when done. Note: `--skip-teardown` only suppresses the Tekton `llmdbenchmark-teardown` task (Helm-level resource cleanup); PipelineRun CR deletion by the orchestrator is unaffected. Use `--preserve-pipelineruns` to suppress PipelineRun CR deletion on success — useful for debugging steps that fail silently (e.g., `set +e` scripts that exit 0 despite internal errors).

**Remote mode** — `deploy.py run --remote` submits the orchestrator as a Kubernetes Job (`sim2real-orchestrator`) instead of running locally. The launcher packs workspace files into a ConfigMap, applies the Job, and waits for the pod to reach Running. Use `stop` to cancel, `status` to check progress, and `collect` to pull results after completion. Requires `orchestrator_image` in `setup_config.json`.

**`deploy.py status`** — prints the current state of all pairs. Reads from the `sim2real-progress-{scenario}-{run}` ConfigMap. Requires a configured namespace.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Filter by workload names (comma or space-separated; globs OK) |
| `--package NAME…` | Filter by package names (comma or space-separated; globs OK) |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE…` | Filter by status names (comma or space-separated; e.g. `running`, `done`, `failed`) |
| `-s`, `--silent` | Suppress the per-pair table and banner; print only the summary line (machine-readable) |

**`deploy.py collect`** — extracts results from the cluster PVC and writes to `workspace/runs/<run>/results/{phase}/<workload>/i<N>/` (one subdirectory per iteration). Like `deploy.py run`, the run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`; missing `runs/<R>/` or `runs/<R>/cluster/` exits with `run 'sim2real assemble --run <R>' first`, and a missing / unparseable `run_metadata.json` or missing `cluster_id` exits with `run metadata corrupted; re-assemble`.

**Incremental copy at file granularity (issue #885).** For each `i<N>/` the collector inventories the remote iteration once (`find . -type f -exec stat -c '%s|%n'` in the extractor pod), diffs it against local files by name and size, and copies only what is missing or short — one `kubectl cp` per remote top-level entry when nothing has landed yet, then one per individual file for anything still outstanding. The destination is never wiped, so a copy interrupted by the 120 s subprocess guard (#694) leaves its bytes in place and the next collect transfers only the delta. `kubectl cp` cannot resume mid-file, so a truncated file is re-copied whole. A `TimeoutExpired` is caught per copy — `check=False` does not suppress it — so one oversized file can no longer abort the rest of the slot's collect.

Before #885 the whole iteration went in a single `kubectl cp` under that 120 s guard. At the ~3.5 MB/s throughput measured through the API server, that capped an iteration at roughly 420 MB, and because each retry `rmtree`d the destination first, every attempt re-streamed from zero and died in about the same place — the operation could not converge by repetition.

**Completeness marker.** `i<N>/.collect_complete` is written **only** after every file in the remote inventory is present locally at the matching size, so a partially-copied iteration has no marker and is resumed rather than skipped. Before #885 completeness was inferred from `trace_data.csv`'s mtime alone; that file is copied early in the tar stream, so it survived a truncation and the next collect reported the partial iteration as up to date — silently retaining truncated logs across repeated "successful" collects. Deleting the marker forces a re-collect of that iteration.

Two independent decisions read the marker, and they ask different questions.

*Skip this iteration entirely?* Only when all three hold: the marker exists, its **scope** covers what this collect wants, and its recorded **mtime** is a number at least as new as the remote's current `trace_data.csv` mtime. Anything else re-examines — which costs one remote inventory and zero transfers when nothing has in fact changed, so declining to skip is cheap.

- **Scope.** `--skip-logs` establishes only "complete apart from `server_logs`", so the marker records what it left out (`excluded_subdirs`) and covers a request only when everything it omitted is also omitted now. `collect --skip-logs` followed by a plain `collect` — the sequence the >1 GB size prompt actively recommends — therefore re-examines and fetches `server_logs`, instead of reporting the iteration up to date and never transferring the logs at all. A marker with no scope field predates this check and is not honoured.
- **Mtime.** A marker records `null` here whenever the phase-wide probe produced no mtime for the iteration — the probe failed, or the iteration has no `trace_data.csv` for the probe to key off. That proves the transfer completed at the time but says nothing about whether the remote has moved since, so it does **not** count as coverage. Treating it as coverage would skip the iteration on every later collect no matter how new the remote became — a permanent silent skip, and strictly weaker than the pre-#885 gate, which re-copied whenever it had no mtime to compare.

*Refetch the whole inventory, or just the size delta?* The size delta is the default; a full refetch happens only when the size diff cannot be trusted, because an in-place remote rewrite can leave every size unchanged. Two cases qualify: the marker's mtime is older than the remote's (the remote demonstrably moved on), or the marker's mtime is present but unparseable (the marker itself is untrustworthy). A marker with no recorded mtime is neither — it is absence of information, not evidence of change — so it falls through to the size delta, which is what keeps a failed mtime probe cheap.

**Stale-file pruning.** Files present locally but absent from the remote inventory are deleted before the copy. This is the one useful effect the old `rmtree` had — clearing leftovers from an earlier collect of the same iteration — kept without the data loss, since a file the remote still holds is never touched whether or not it has been fetched yet. Pruning compares against the *unfiltered* remote inventory, so `--skip-logs` still clears a stale `server_logs/` the PVC no longer has while leaving one it does have in place rather than deleting what an earlier full collect fetched. A failed inventory probe prunes nothing.

**Partial-copy reporting.** An incomplete iteration prints a `PARTIAL COPY` warning naming the file and byte counts transferred, the truncated and missing files, the state of `trace_data.csv`, and the scoped `collect` command that resumes it. The trace verdict has three states, not two: `ok`, `MISSING OR TRUNCATED`, and `UNKNOWN` — the last when the inventory probe itself failed, so no file was ever examined. Reporting "safe" there would be the same false reassurance this issue was filed about.

An iteration that *completed* but hit a copy failure on the way also says so, as `recovered after N copy failure(s)`. Completeness is decided from the final missing/short check, so a bulk first-pass copy that hit the 120 s guard and was then rebuilt file-by-file would otherwise leave no trace anywhere — and that line is the only signal telling an operator a single top-level subtree has grown big enough to cost a wasted timeout on every cold copy. A multi-cell collect ends with a `Partial:` summary listing every iteration left incomplete, so a failure in cell 3 of 9 is not lost in the scrollback.

Iterations that live on other slots' PVCs (e.g. when replicas of one `(phase, workload)` pair dispatch across cluster slots) are left untouched on local disk — neither the workload directory nor any `i<N>/` is wiped as a whole (issue #564). If the mtime probe fails (e.g., pod not running), no iteration is skipped and every one is re-examined against its remote inventory — this is the expected degradation path, and it costs one inventory plus the size delta per iteration rather than a full re-download. An iteration that is already complete transfers nothing.

Per phase, the resolved llm-d-benchmark plan YAMLs are also pulled into `workspace/runs/<run>/results/{phase}/plans/{flow}/*.yaml` (top-level numbered manifests + `config.yaml`, no `helm/` subdir). Plans are workload-invariant within a phase, so collect picks one workload's latest `root-*` render to source the phase's plans. Plan extraction is best-effort and non-fatal — failures warn but do not block trace collection.

Sibling streaming sidecars land alongside the trace files under each `i<N>/`:

- `epp_logs/` — EPP pod logs, time-bucketed (produced by `stream-epp-logs`).
- `gpu_logs/` — per-node `nvidia-smi` samples, one file per node (produced by `stream-gpu-stats`).
- `metrics/raw/<pod>_<ts>_metrics.log` — Prometheus text-exposition dumps, one file per pod per scrape (produced by `stream-metrics`, which wraps llm-d-benchmark's `collect_metrics.sh`).
- `metrics/processed/*.json` — post-run aggregated summaries (`metrics_summary.json`, `replica_status_timeseries.json`, `pod_startup_times.json`) written by `collect_metrics.sh process`.
- `epp_stream_done` / `gpu_stream_done` / `metrics_stream_done` — sentinel files written by `collect-results` when the workload finishes; each streamer polls its sentinel and exits.
- `.collect_complete` — per-iteration completeness marker written locally by `collect`, not present on the PVC (JSON: `completed_at`, `file_count`, `byte_count`, `inventory_sha256`, `remote_mtime`, `excluded_subdirs`). Its presence is the only evidence `collect` accepts that an iteration transferred in full, **for the scope named in `excluded_subdirs`** (issue #885). A `--skip-logs` collect records `["server_logs"]` there and therefore does not satisfy a later full collect; a full collect records `[]` and does satisfy a later `--skip-logs` one. Delete the file to force a re-collect of that iteration.

### Metric capture (`stream-metrics`)

`stream-metrics` is a thin wrapper around `collect_metrics.sh` from `llm-d-benchmark`. The sidecar pod pulls `collect_metrics.sh` + `process_metrics.py` from raw.githubusercontent.com at a pinned git ref (task param `harnessSha`, default `"main"`), then runs the standard `start` / poll-sentinel / `stop` / `process` lifecycle. `collect_metrics.sh` handles pod discovery (EPP + vLLM decode), Prometheus text scraping, bearer-token auth for EPP, and per-run aggregation.

**Where output lands** (all under the cell dir):

- `metrics/raw/<pod>_<ts>_metrics.log` — one Prometheus text-exposition dump per pod per scrape (default 15 s cadence via `intervalSeconds` task param). Unfiltered — every metric the target exposes lands on disk.
- `metrics/raw/collection_debug.log` — collector-side errors.
- `metrics/processed/replica_status.json`, `replica_status_timeseries.json`, `pod_startup_times.json` — infrastructure state collected each iteration.
- `metrics/processed/metrics_summary.json` — post-run percentiles over the metrics named in `process_metrics.py`'s `AGGREGATE_METRICS` set.
- `metrics/metrics_collection.log` — stdout+stderr of `start` / `stop` / `process` runs.

**EPP auth.** GAIE serves `/metrics` on port 9090 with secure-serving on. `collect_metrics.sh` reads a bearer token from the `inference-gateway-sa-metrics-reader-secret` Secret (a `type: kubernetes.io/service-account-token` Secret provisioned per-namespace by `cluster.py provision` / `cluster.py slot add` — see `tektonc-data-collection/tekton/roles-ns.yaml`). The corresponding cluster-scoped RBAC (`sim2real-metrics-reader` ClusterRole + per-namespace ClusterRoleBinding on `system:serviceaccounts:<ns>`) is provisioned by the same admin bootstrap step from `roles-cluster.yaml`. See sim2real#579 for the design rationale.

**Widening the metric-name set.** `metrics/processed/metrics_summary.json` covers only the metrics named in `AGGREGATE_METRICS` in `process_metrics.py` (a hardcoded Python set in llm-d-benchmark). To add or remove metrics from the summary, upstream the change and bump sim2real's `llm-d-benchmark` submodule pointer to a commit containing it — `sim2real assemble` fills the sidecar's `harnessSha` from that pointer on every PipelineRun, so no additional pin edit is needed. The `metrics/raw/*.log` files ALWAYS carry every metric the target exposed, so post-run analysis can extract additional signals without re-running.

**Live inspection** (outside a run):

```bash
kubectl port-forward -n <namespace> <epp-or-vllm-pod> 9090:9090
curl -s -H "Authorization: Bearer $(kubectl -n <namespace> get secret inference-gateway-sa-metrics-reader-secret -o jsonpath='{.data.token}' | base64 -d)" \
  http://localhost:9090/metrics | grep -v '^#' | awk '{sub(/\{.*/, ""); print $1}' | sort -u
```

vLLM `/metrics` on port 8000 is unauthenticated — drop the `-H` flag when probing it directly.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope to specific pair keys — narrows both workload and package (comma or space-separated, `wl-` prefix optional; takes precedence over `--workload`) |
| `--workload NAME…` | Scope to pairs matching these workloads (comma or space-separated; globs OK) |
| `--package NAME…` | Scope to pairs matching these packages (comma or space-separated; globs OK, but never against `experiment`). Pass the synthetic value `experiment` as a literal to collect every package directory of the scoped pairs. |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--skip-logs` | Skip vLLM and EPP log files, collect only traces |

When `--only` or `--workload` is given, only matching workload subdirectories are pulled from the PVC (instead of entire phase directories). Multiple values within a flag use OR (union): `--workload X Y` matches pairs for workload X or Y. Different flags compose as AND: `--workload X Y --package baseline` scopes to baseline pairs whose workload is X or Y, and pulls those workloads from the baseline phase only — pairs of other packages are not in scope and do not trigger "skipping" warnings. The synthetic `--package experiment` value is the carve-out: it does not narrow the pair set, so it preserves today's "every package of the scoped pairs" behavior. Requires progress data to resolve pairs.

**`deploy.py stop`** — deletes the `sim2real-orchestrator` Kubernetes Job (with cascading pod deletion) in the primary namespace. Only meaningful when the orchestrator runs as an in-cluster Job. Pair state is left as-is. If no remote orchestrator Job exists, prints a message and returns. Use `reset` separately to clear failed/stalled pair state.

**`deploy.py reset`** — resets all non-pending pairs to `pending` and removes their cluster resources (PipelineRuns, Helm releases, and orphaned HTTPRoutes). This includes `done` pairs — use `--preserve-done-status` to clean up cluster resources for done pairs without re-queuing them. HTTPRoutes rendered by llm-d-benchmark's `08_httproute` template are applied directly to the cluster (no helm-release annotation, no ownerReference to their InferencePool), so neither `helm uninstall` nor Kubernetes GC removes them at teardown; reset sweeps any HTTPRoute whose backend InferencePool no longer exists (routes with a live backend are never touched), clearing debris that would otherwise strand on the shared gateway and steal traffic (issue #603).

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope reset to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Scope reset to pairs matching these workloads (comma or space-separated; globs OK) |
| `--package NAME…` | Scope reset to pairs matching these packages (comma or space-separated; globs OK) |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE…` | Scope reset to pairs matching these statuses (comma or space-separated) |
| `--preserve-done-status` | Keep done pairs' status unchanged (cluster cleanup only) |
| `--dry-run` | Print what would be reset without acting |

**Safety:** Results in `workspace/runs/<run>/results/` are preserved — only cluster resources and ConfigMap status are affected.

**`deploy.py wipe`** — deletes local result files for all pairs in scope. Does **not** modify pair status in the ConfigMap. Pairs with no results on disk are skipped. Empty workload and package directories are cleaned up automatically. Each pair key encodes its iteration, so under the step-5 `results/<package>/<workload>/i<N>/` layout the delete target is a single per-iteration subdirectory; legacy single-replica runs whose files sit directly under `results/<package>/<workload>/` fall back to the workload dir.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope wipe to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Scope wipe to pairs matching these workloads (comma or space-separated; globs OK) |
| `--package NAME…` | Scope wipe to pairs matching these packages (comma or space-separated; globs OK) |
| `--iteration SPEC` | Scope wipe to iteration(s): `2`, `1,3`, `1-3`, `1,3-5`. Only the named `i<N>/` subdirs are removed; sibling iterations are preserved. |
| `--dry-run` | Print what would be wiped without acting |
| `--yes` / `-y` | Skip confirmation prompt |

**Re-running wiped pairs:** `wipe` only removes files. To re-dispatch wiped pairs, use `reset` to move them back to `pending`.

**`deploy.py pairs`** — lists available pair keys, workloads, and packages by scanning `cluster/pipelinerun-*.yaml`.

| Flag | Description |
|------|-------------|
| `--keys-only` | Print pair keys only (one per line, for scripting) |
| `--workloads-only` | Print distinct workload names only (one per line) |
| `--packages-only` | Print distinct package names only (one per line) |

Flags are mutually exclusive. Default (no flag) prints a human-readable table with PAIR, WORKLOAD, and PACKAGE columns.

---

## Manage translations and runs

`pipeline/sim2real.py` exposes list/use subcommands for translations and runs.

```bash
python pipeline/sim2real.py --experiment-root ../admission-control list translations
python pipeline/sim2real.py --experiment-root ../admission-control list runs
python pipeline/sim2real.py --experiment-root ../admission-control use --run <name>
```

`--experiment-root` defaults to the current working directory; omit it when running from the experiment repo root.

**`sim2real list translations`** — Walks `workspace/translations/*/translation_output.json` and prints one row per translation, newest first (by `created_at`). Columns: `ALIAS` (`-` if the translation has none), `HASH` (first 12 chars), `SOURCE` (`skill` or `byo`), `IMAGES` (`N built` when every algorithm has an `image_ref`, `N pending` when none, `N/M built` mixed, `N registered` for BYO), `APPENDS` (number of `translation append` events recorded in `append_history[]`; `0` for translations that have never been appended to), `CREATED`. Prints `no translations yet` and exits 0 if the directory is empty or missing. Malformed `translation_output.json` files are logged as warnings and skipped, so a stray file doesn't break the listing.

**`sim2real list runs`** — Walks `workspace/runs/*/run_metadata.json` and prints one row per run, newest first (mtime desc). Columns: `RUN_NAME`, `TRANSLATION` (first 8 chars of the translation hash), `CLUSTER`, `ASSEMBLED`. The active run (`current_run` in `setup_config.json`) is marked with `*`. Prints `no runs yet` and exits 0 if `workspace/runs/` is empty or missing. A subdirectory without `run_metadata.json` is skipped; a corrupt `run_metadata.json` renders `?` cells instead of aborting the listing.

**`sim2real use --run <name>`** — Sets `current_run` in `setup_config.json` to the given run. Errors with `"run doesn't exist; try 'sim2real list runs'"` (exit 2) if `workspace/runs/<name>/run_metadata.json` does not exist. Read-modify-write preserves unrelated keys in `setup_config.json`.

**`sim2real resolve --run <name>`** — Emits a hydrated JSON view of a run on stdout. Reads `workspace/runs/<name>/run_metadata.json` to locate the referenced translation, then walks `workspace/translations/<hash>/`, `workspace/runs/<name>/results/`, `workspace/runs/<name>/cluster/`, and `workspace/runs/<name>/manifest.assembly.yaml` to produce a single JSON document with everything the `/sim2real-check` skill (and other operator tooling) needs to reason about the run: metadata (run_name, cluster_id, params_hash, image_tag, assembled_at, cluster_config_path), translation (hash / alias / source / per-algorithm image_ref+config paths / per-baseline overlay paths from the manifest), results (declared phases, phases with collected data, workloads-by-phase), cluster scenarios (baseline.yaml, per-algorithm treatment YAMLs, pipelinerun-*.yaml files), and the manifest.assembly.yaml slice (scenario, workloads, defaults.disable, measurement). Schema is v1; future versions are additive. `translation_hash` appears only under `translation.hash` (not duplicated at top level). Exit codes: `0` with JSON on stdout on success; `2` with a specific error message on stderr (unknown run, corrupt/missing `run_metadata.json`, unresolvable `translation_hash`, missing workspace) — each error names the `sim2real` command that would repair the state.

The previous `run.py inspect` debug view is dropped without replacement — `cat workspace/runs/<name>/run_metadata.json` is the shortest path. `sim2real resolve --run <name>` is the structured superset for tooling.

---

## End-of-step-1 BYO demo

The full BYO flow. Every step is idempotent and re-runnable. Substitute your own values for `<cluster_id>`, `<run-name>`, `<algorithm>`, `<image-ref>`, and `<treatment-overlay-path>`.

### Prerequisites

- An experiment repo with `transfer.yaml`, `baselines/baseline.yaml`, and the workloads referenced in `transfer.yaml:workloads`.
- A cluster with kubectl / Tekton reachable.
- Registry credentials (HF, image registry) exported or provided via flags.

### 1. Provision the cluster (one-time)

```bash
python pipeline/cluster.py provision <cluster_id> --namespaces sim2real-0,sim2real-1
```

Writes `workspace/clusters/<cluster_id>/cluster_config.json`. Idempotent — re-run when adding namespace slots.

### 2. Configure the workspace (one-time per experiment repo)

```bash
python pipeline/setup.py --experiment-root <experiment-root>
```

Writes `workspace/setup_config.json` with registry, repo name, and orchestrator image. Does not touch `workspace/runs/` — that's `sim2real assemble`'s job.

### 3. Register a translation (BYO)

```bash
# Preferred: repeatable --algorithm NAME=IMAGE@CONFIG (N algorithms in one call)
python pipeline/sim2real.py translation register \
    --algorithm <name>=<image-ref>@<config-path> \
    [--algorithm <name>=<image-ref>@<config-path> ...] \
    --experiment-root <experiment-root>
```

Writes `workspace/translations/<hash>/` with `translation_output.json`, `registered.json`, and `generated/<algorithm>/<algorithm>_config.yaml` for each algorithm. Prints the `<hash>` on success. See the [Register a translation](#register-a-translation-byo) section for the full flag reference, the hash formula, and the deprecated single-algorithm form.

### 4. Assemble the run

```bash
python pipeline/sim2real.py assemble \
    --translation <hash> \
    --cluster <cluster_id> \
    --run <run-name> \
    --experiment-root <experiment-root>
```

Writes `workspace/runs/<run-name>/` with resolved scenario YAMLs, `pipelinerun-*.yaml` manifests, `manifest.assembly.yaml`, and `run_metadata.json`.

### 5. Deploy (orchestrate PipelineRuns)

```bash
python pipeline/deploy.py --experiment-root <experiment-root> \
    --run <run-name> run
```

Dispatches PipelineRuns across the namespace slots (using the already-built EPP image refs baked into the resolved `cluster/` scenarios) and polls for completion. Progress lands in the `sim2real-progress-<scenario>-<run-name>` ConfigMap (scoped by both `transfer.yaml:scenario` and the run name — issue #551). Use `deploy.py --run <run-name> status` to snapshot progress; use `deploy.py --run <run-name> stop` to cancel the remote orchestrator Job (if `--remote` was passed); use `deploy.py --run <run-name> reset` to requeue non-pending pairs (which also cancels their in-flight PipelineRuns).

### 6. Collect results

```bash
python pipeline/deploy.py --experiment-root <experiment-root> \
    --run <run-name> collect
```

Pulls per-pair `trace_data.csv` and GPU logs from the cluster PVC into `workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/`. This is the epic's success gate — the demo is done when the CSV files exist locally.

### Success criterion

For each `<workload>` in `transfer.yaml:workloads`, each `<phase>` in `{baseline, <algorithm>}`, and each iteration `<N>` in `1..replicas`:

```
workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/trace_data.csv
```

Once these files exist, step-1's BYO demo is complete. Downstream skills (e.g. `/sim2real-analyze`) consume them for latency comparison and report generation.

---

## End-of-step-2 skill-driven demo

Same success criterion as the BYO demo — per-workload per-algorithm `trace_data.csv` files exist locally. The difference is that instead of registering a pre-built image (step 3 above), the operator invokes the `/sim2real-translate` Claude skill to produce the plugin source, then `sim2real build` compiles it into images.

### Prerequisites

Same as BYO plus:

- `skopeo` on `PATH` (registry probe).
- `transfer.yaml:algorithms[]` populated with `source:` paths pointing to the algorithm implementation files (Go or otherwise).

### 1-2. Provision cluster + configure workspace (identical to BYO)

### 3. Checkpoint the translation

```bash
python pipeline/sim2real.py translate --experiment-root <experiment-root>
```

Writes `workspace/translations/<hash>/skill_input.json` + `translation_output.json` (with `image_ref: null` per algorithm) and prints the checkpoint message.

### 4. Run the translation skill

At the Claude prompt: `/sim2real-translate`. The skill reads `skill_input.json`, translates each algorithm, and writes `generated/<algo>/{cmd,pkg,<algo>_output.json,<algo>_config.yaml}` under the translation directory.

### 5. Validate the skill's work

```bash
python pipeline/sim2real.py translate --resume --experiment-root <experiment-root>
```

Errors if any `generated/<algo>/<algo>_output.json` is missing. Success → exit 0.

### 6. Build images

```bash
python pipeline/sim2real.py build --translation <alias-or-hash> --experiment-root <experiment-root>
```

For each algorithm: probe the registry, build if absent, record `image_ref`/`image_digest` in `translation_output.json`. Prints per-algorithm status.

### 7-9. Assemble, deploy, collect (identical to BYO steps 4-6, using the same `--translation` alias)

The same success gate applies — `trace_data.csv` under `workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/` (one file per iteration).

---

## Pipeline library (`pipeline/lib/`)

| Module | Purpose |
|--------|---------|
| `manifest.py` | Loads and validates `transfer.yaml` (v3 schema) |
| `slicer.py` | Splits `transfer.yaml` into translation-slice vs assembly-slice + computes `translation_hash` (`translation_hash_with_sources` folds algorithm source bytes into the hash for the skill-driven flow) |
| `translation_ref.py` | Shared alias/algorithm-name validator, on-read shim for `translation_output.json` (handles both step-1 legacy and step-2 per-algo shapes), and `resolve_translation_ref` (accepts alias / hash prefix / full hash) |
| `build.py` | Shared build primitives — image-ref construction, skopeo digest probe, buildkit-pod dispatch, atomic JSON write. Consumed by `sim2real build`. |
| `assemble_run.py` | Assembly logic behind `sim2real assemble` (deep-merge + PipelineRun generation, additive-grow / drift / legacy-run decision tree) |
| `values.py` | Deep-merge utility used by `assemble_run.py`. Lists of scalars are replaced, except CLI-flag lists at `**.vllm.additionalFlags`, which merge by flag name (#851) |
| `pairkey.py` | Pair-key parser (canonical grammar `wl-<w>\|<p>\|iN` with legacy `wl-<w>\|<p>` fallback) and `--iteration` spec parser (list + range) |
| `tekton.py` | Generates PipelineRun YAMLs; `validate_pipelinerun_name` enforces the RFC 1123 253-char limit at assemble time |
| `pod_pending.py` | Classifies pod scheduling failures (recoverable vs not) |
| `remote.py` | ConfigMap + Job generation for `deploy.py run --remote` |
| `capacity.py` | Cluster GPU capacity probe (taint / cordon / product filter) |
| `cluster_ops.py` | Cluster-side primitives: read/write/update `cluster_config.json`, `provision_namespace`, `apply_cluster_resources`, `detect_openshift` |
| `layout.py` | Workspace path helpers — `repo_root()` (canonical repo-root, introduced in #772; use this instead of ad-hoc `Path(__file__).parent` chains), `set_experiment_root(arg)` / `experiment_root()` (module-level experiment-root state, mirrors the `--experiment-root` CLI rule), `workspace_dir`, `cluster_dir`, `cluster_config_path`, `list_cluster_ids`, `runs_dir`, `translations_dir`, `translation_dir`, `setup_config_path`, `translation_output_path`, `registered_path`, `generated_config_path` |
| `epp.py` | EPP image injection helpers (`inject_epp_image`, `inject_image_ref`) |
| `health.py` | Pod health detection and remediation for the deploy orchestrator |
| `log.py` | Shared logging functions for pipeline scripts |
| `progress.py` | Progress persistence for the parallel pool orchestrator (ConfigMap store) |
| `redact.py` | YAML redaction for collected plan and resource files — stubs sensitive fields, and prunes annotations that are a verbatim copy of the manifest they annotate (`kubectl.kubernetes.io/last-applied-configuration`, #894), before writing to `results/`. Runs on every collect path (#886) |
| `resolve.py` | Powers `sim2real resolve --run` — hydrated run-view helper for the workspace |
| `scope.py` | CLI filter-value primitives (`parse_name_list`, `is_glob`, `expand_glob_values`) shared by `deploy.py` and `sim2real assemble` so their `--workload` / `--package` grammar cannot drift |
| `shadow.py` | Shadow GPU reservation ledger for `deploy.py` orchestrator |
| `source_locator.py` | Source-location abstraction for `translation register --build` (PathLocation / GitLocation) |
| `errors.py` | Shared exception types (`AssembleError`) — breaks import cycles between low-level modules (e.g. `slicer`) and high-level modules (e.g. `assemble_run`) |
| `source_toggle.py` | Toggle component directory between baseline and treatment states for `sim2real build` |
| `proc.py` | Shared subprocess-execution seam (`run`, `which`) — consolidates the near-identical thin wrappers that formerly lived in `deploy.py`, `setup.py`, and `cluster_ops.py`; test monkeypatching targets this module |

---

## Workspace artifacts

All artifacts live under `<experiment-root>/workspace/` (gitignored). Key files:

| File | Written by | Read by |
|------|-----------|---------|
| `setup_config.json` (workspace fields: registry, repo_name, orchestrator_image, sim2real_root) | `setup.py` | `deploy.py`, `sim2real.py list runs` |
| `setup_config.json:current_run` (active run pointer) | `sim2real.py use` | `deploy.py` (default `--run`), `sim2real.py list runs` (active-mark `*`) |
| `clusters/<id>/cluster_config.json` (cluster fields: cluster_id, namespaces, is_openshift, storage_class, secret_names, workspaces, created_at) | `cluster.py init` / `slot add` / `slot remove` (`provision` remains as sugar) | `sim2real assemble`, `deploy.py`, `lib/remote.py` |
| `translations/<hash>/translation_output.json` | `sim2real translation register` (batched; N per-algo entries in `algorithms`) | `sim2real assemble`, `deploy.py` |
| `translations/<hash>/registered.json` | `sim2real translation register` (batched; N per-algo entries in `algorithms`) | audit trail |
| `translations/<hash>/generated/…` | `sim2real translation register` | `sim2real assemble` |
| `runs/<run>/run_metadata.json` | `sim2real assemble` | `deploy.py`, `sim2real.py list runs` |
| `runs/<run>/manifest.assembly.yaml` | `sim2real assemble` | reproducibility / drift detection (step-5) |
| `runs/<run>/cluster/…` | `sim2real assemble` | `deploy.py` |
| `runs/<run>/results/{phase}/` | `deploy.py collect` | `/sim2real-analyze` skill, `deploy.py wipe` |
| ConfigMap `sim2real-progress-{scenario}-{run}` | `deploy.py run`, `deploy.py reset` | all `deploy.py` subcommands |

See `CLAUDE.md`'s Workspace Artifacts table for the comprehensive per-file producer/consumer breakdown.

---

## <experiment-repo>/transfer.yaml

Manifest consumed by `sim2real assemble`. Version 3 required.

```yaml
kind: sim2real-transfer
version: 3
scenario: <name>            # scenario name used in generated PipelineRun labels

baselines:                  # required — list of baseline specs
  - name: <pkg-name>       # unique package name (lowercase alphanumeric, 1-20 chars)
    scenario: <path>        # baseline scenario YAML (null if none)
    sim:
      config: <path>        # baseline policy for sim
    real:
      config: <path>        # optional: baseline EPP config template
      notes: |              # optional: notes embedded in skill_input.json

algorithms:                 # optional — omit for baseline-only benchmarks
  - name: <pkg-name>       # unique package name
    source: <path>          # sim algorithm implementation
    defaults: <baseline>    # name of baseline this algorithm inherits from

workloads:
  - <path>                  # one or more workload YAMLs
                            # Each YAML is either a generative workload (any fields understood
                            # by llm-d-benchmark's WorkloadSpec) or a corpus workload (must have
                            # a non-empty top-level `corpus:` mapping plus `replay:` — see
                            # "Corpus workload schema" below). The legacy `trace:` mapping was
                            # replaced by `corpus:`/`replay:` and is now refused at assemble.

context:
  text: |                   # freeform instructions (consumed by step-2's translation skill)
  files: [<path>, ...]      # files assembled into context document (step-2 consumer)

defaults:                   # optional — controls framework defaults overlay
  disable: []               # fragment stems (filename without .yaml) to skip;
                            # fragments live in <experiment-root>/baselines/defaults/
                            # and are merged UNDER each baseline by `sim2real assemble`. See
                            # docs/troubleshooting.md#framework-defaults-overlay.

# v3 fields (required unless noted)
component:                    # required when algorithms[] contains any non-BYO entry;
                              # optional if every algorithm carries `byo: true`.
  repo: <name>                # component repo name (matches submodule directory in the sim2real repo)
  kind: <string>              # component kind (e.g. "EndpointPickerConfig")
  path: <string>              # optional — defaults to the last segment of `repo`
  ref: <string>               # optional — tag, branch, or commit SHA identifying the expected component version
  base_image:                 # optional — base image the built EPP layers on top of
    hub: <registry>           # e.g. ghcr.io/llm-d
    name: <repo>              # e.g. llm-d-inference-scheduler
  build:                      # optional — build overrides
    commands: []              # component build commands (list of argv-style commands)
    image:                    # optional — override coords for the built EPP image
      hub: <registry>         # defaults to base_image.hub when set
      name: <repo>            # defaults to base_image.name when set
pipeline:                   # optional — defaults applied if absent
  name: sim2real            # Pipeline resource name referenced in PipelineRuns (default: "sim2real")
  yaml: pipeline/pipeline.yaml  # path relative to repo root (default: "pipeline/pipeline.yaml")

measurement: measurement.yaml  # optional — bundle-level observe protocol file (#911)
```

Unknown top-level keys are **rejected**, not ignored (#911). The legal set is
`kind`, `version`, `scenario`, `baselines`, `algorithms`, `workloads`, `context`,
`defaults`, `component`, `pipeline`, `measurement`.

`blis_observe:` is **rejected** with a message naming its replacement. It was
removed rather than dual-supported: a bundle that dropped it and added an
unrecognised `measurement:` key used to validate cleanly while every observe value
silently fell back to the renderer's defaults, so the run measured something the
bundle did not describe.

All paths are relative to the experiment root and validated by `sim2real assemble` at load time.

### Measurement protocol

The `blis observe` protocol — how the instrument was configured — lives in its own
file, named by `measurement:` in `transfer.yaml`. It is the part a paper cites when
it says how something was measured, which is a different scope from a manifest's
*what to run against what*.

```yaml
kind: measurement-protocol
version: 1

maxConcurrency: 10000     # int      → --max-concurrency
timeout: 1800             # int      → --timeout  (PER-REQUEST HTTP timeout, not a run cap)
warmupRequests: 50        # int      → --warmup-requests  (see the caveat below)
prewarmDuration: 60s      # Go duration string → --prewarm-duration ("0" = off)
detectors: composite      # string   → --detectors  (empty = off)
apiFormat: completions    # enum     → --api-format  (completions | chat)
recordItl: false          # bool     → --record-itl when true
streaming: true           # bool     → --no-streaming when FALSE (inverted)
extraArgs: ""             # string   → appended verbatim, last
```

Key names, types and defaults are the roster in `pipeline/lib/observe_argv.py` —
the same table that renders the flags — reused verbatim rather than remapped.
Every rename would be a mapping layer, and mapping layers are where this repo's
drift has come from (`--post-hoc-detector` vs `detectors`, `format` vs
`traceFormat`). `kind` and `version` are the file's envelope and are not roster
keys; anything else unknown is rejected, as are wrong per-key types.

All keys are optional; absent keys take the defaults shown above, which reproduce
the command the pipeline ran before issue #900. Values are **not** emitted as
individual PipelineRun params — `sim2real assemble` renders the whole
`blis observe` command line into a single `observeArgs` param. See
[Observe argv rendering](#observe-argv-rendering) below.

> **`prewarmDuration` must be a real Go duration string, and `timeout` must not be.**
> They look interchangeable and are not: `--prewarm-duration` is a Cobra
> `DurationVar`, so it needs an explicit unit (`60s`, `15m`, or `0` for off),
> while `--timeout` is an `IntVar` of **seconds**, so `1800` is right and
> `"1800s"` is refused. Since sim2real#905 `prewarmDuration` is validated with
> the same duration grammar as `corpus.reconstruct.max_think_time` (they share
> one predicate in `pipeline/lib/duration.py`). Before that it was checked only
> for being a *string*, which let `"60"` and `""` through to die at blis argument
> parsing in-cluster, and `"60000ns"` through to prewarm for 60 µs.

**The protocol is bundle-scoped and constant.** Holding it identical across every
cell is what makes cells comparable; per-cell variation is a threat to validity,
not a feature. Hence one file per bundle rather than a per-workload block.

**`warmupRequests` and closed-loop replay.** It excludes the first N *dispatched*
requests by global index. For single-turn open-loop traffic that is an unbiased
drop of N iid requests, and 50 (the default) is the established norm. For
closed-loop multi-turn replay it is not: the leading dispatches are the opening
rounds of the initial session pool, which under `context_growth: accumulate` are
the smallest-context, cheapest rounds — excluding them biases inputs and latency
rightward and leaves the first pool-worth of sessions head-truncated while later
ones stay intact. Trace bundles should set `warmupRequests: 0` explicitly and do
cold-start exclusion in analysis, where `trace_data.csv`'s `session_id` and
`round_index` make it exact and reversible.

**Reproducibility.** `load_manifest` replaces the pointer with the file's resolved
values, so they are snapshotted into `manifest.assembly.yaml` and covered by
`params_hash` exactly as the old inline block was. Editing the protocol file
therefore changes the hash and is detected as drift on re-assemble — had the
pointer been hashed instead, two runs with identical hashes could have been
measured with different instrument settings.

To generate one from an experiment's `config.md`:

```bash
python3 .claude/skills/sim2real-bootstrap/generate_from_config.py \
    <experiment-root>/config.md --emit-measurement-yaml > <experiment-root>/measurement.yaml
```

`component.ref` (optional): tag, branch, or commit SHA identifying the expected version of the component submodule. Reserved for step-2 (the skill-driven flow that will consume it).

### Observe argv rendering

`sim2real assemble` renders the entire `blis observe` command line into one
`observeArgs` PipelineRun param ([`pipeline/lib/observe_argv.py`](lib/observe_argv.py),
issue #900). Before this, the command lived in three places — 12 scalar params
from assemble, 17 of `pipeline.yaml`'s 31 params declared purely to forward them,
and the Task's shell reassembling an argv — so adding one flag meant editing
three files in two repos, and the effective command existed nowhere until the pod
ran.

**`--server-url` is the one exclusion.** It is a runtime Task result (the standup
task's endpoint), so the assembler cannot know it; the Task appends it.

**Two paths are owned by the Task** (`run-workload-blis-observe-binary`) and the
renderer must match them: the `data` workspace mounts at `/workspace/data`, so
every data-PVC path carries that prefix; and `write-workload-spec` writes the
generative spec to `/workspace/workload.yaml`. Both are also documented in the
Task's own `observeArgs` param description, so the coupling is visible from
either end.

**Three mutual exclusions are enforced at assemble**, each previously upheld by a
shell conditional, by blis at runtime, or not at all:

| Exclusion | Why |
|---|---|
| corpus-mode vs spec-mode inputs | `--concurrent-sessions` is mutually exclusive with `--workload-spec`. Three distinct errors: both present; a corpus document with no `tracePath`; a generative workload given one. |
| `recordItl: true` + `streaming: false` | blis rejects `--record-itl` with `--no-streaming` — ITL needs per-chunk timestamps, which only exist for streaming responses. |
| `detectors: ""` while a saturation report would be emitted | blis errors with `--saturation-report requires --detectors`, so detectors-off suppresses the report too. Safe because `saturation.json` is documented **not load-bearing**. |

**Value validation belongs to the renderer alone.** Tekton substitutes params
textually into the Task's `OBSERVE_ARGS="$(params.observeArgs)"` assignment, and
the Task word-splits it unquoted — so a `"`, `$`, backtick or `;` in a rendered
value breaks out of the assignment before any shell guard can help. Rejected in
every value: whitespace (except in `extraArgs`, where multiple words are the
point) and `" ' $ \` ; | & > < \` plus newline. `extraArgs` is **not** exempt from
the metacharacter rule — a `;` there is command injection into the Task's step.

Glob characters (`*`, `?`, `[`) are deliberately **not** rejected: the Task runs
`set -f`, which disables pathname expansion while keeping word-splitting. That
defense sits at the consumer, so it covers every producer. If `set -f` is ever
removed from the Task, add `*?[` to `FORBIDDEN_CHARS` in the same change.

> **`--detectors`, not `--post-hoc-detector`.** The Task used to hardcode
> `--post-hoc-detector composite`. inference-sim #1516 (commit `18c2c926`)
> renamed that flag to `--detectors`, and sim2real #904 bumped the pin past it —
> so `blis observe` was dying at argument parsing on every run. `#900` and
> tektonc#70 remove the stale name from the two places it survived.
> `test_observe_flag_lists.py` now reads the pinned inference-sim source and
> asserts every flag the bootstrap skill can transcribe is one blis registers,
> so a future pin bump that renames an observe flag fails in CI rather than in a
> pod.

---

### Corpus workload schema

A workload YAML is exactly one of two kinds, and the discriminator is **structural**: the presence of a non-empty top-level `corpus:` mapping.

| Kind | Shape | Request source |
|------|-------|----------------|
| **corpus** | `corpus:` + `replay:` | a recorded conversation corpus built by `prepare-trace`, replayed by `blis observe` |
| **generative** | a bare blis `WorkloadSpec` (`version:`, `clients:`/`cohorts:`, …) | generated in-process from the spec |

There is deliberately **no `kind:` field** (a label can contradict the structure it labels) and **no `name:` field** — the workload name is the filename stem, which `sim2real assemble` derives. Both are rejected if present.

The schema is defined once, in [`pipeline/lib/corpus_schema.py`](lib/corpus_schema.py). Validation, PipelineRun param emission, and the corpus cache key all read the same tables, so they cannot drift apart.

```yaml
# workloads/exgentic_agentic.yaml  →  workload name "exgentic_agentic"
corpus:
  upstream:                     # where the raw corpus comes from
    source: hf:Exgentic/agent-llm-traces   # REQUIRED. "hf:<org>/<dataset>", or a path
    revision: 8f21c0a           # optional (default "" = upstream default branch) — HF dataset
                                #   revision: commit SHA, tag, or branch
    format: otel-parquet        # optional (default "otel-parquet") — or "weka-jsonl"
    shards: 39                  # optional (default 39) — take the first N shard files; 0 = all
  select:                       # which sessions survive into the corpus
    partition: test             # optional (default "test") — "test" | "train" | "all"
    min_rounds: 2               # optional (default 2) — drop sessions with fewer usable turns
    dedup_by_conversation: true # optional (default true) — one session per conversation
    shuffle_seed: 42            # optional (default 42) — seeded shuffle; breaks file order so the
                                #   replay pool's first-N sample spans many conversations
  reconstruct:                  # how each session's request stream is rebuilt
    context_growth: accumulate  # optional (default "accumulate") — or "independent"
    max_think_time: 60s         # REQUIRED — Go duration string; cap on the per-round client
                                #   think gap. "0" = no cap. No default: see below
replay:                         # REQUIRED — consumed by blis observe at replay time
  concurrent_sessions: 128      # REQUIRED, int >= 1  → --concurrent-sessions
  total_sessions: 192           # REQUIRED, int >= 0  → --total-sessions (0 = exhaust the corpus)
```

##### `max_think_time` is required, and has no default on purpose

It is the only required field under `corpus:`. Every candidate default is wrong somewhere, silently:

- **Omitting the flag** (what the Task does when the value is empty) lets each converter apply its own — and they differ in *opposite* directions: `blis convert otel` caps at 15s, `blis convert weka` does not cap at all. One descriptor would then mean two different things depending on `format`.
- **Any fixed value** is a guess about a distribution the schema cannot see. Measured on the exgentic corpus, inter-round gaps are **bimodal**: p50 8.0s, p90 27.6s, p99 14,406s. So a 15s cap distorts **22.4%** of gaps, cutting into the normal within-task band, while anything from 60s to 3600s clamps the same **~2%** outlier cluster. `60s` is the value that corpus argues for — above p90, below the outliers — but that is a fact about *that* corpus, not a default.
- **No cap** is not an option for anything we have: uncapped, exgentic's worst single session idles **5.03 h** against a 4 h pipeline timeout, so the run cannot finish. A weka session measured ~63 h.

> **Write the unit.** The value goes to `--max-think-time`, a Go `DurationVar`, so `"60s"` and `"15m"` are durations while a bare `60` is refused at assemble. A bare number is not merely untidy: Go rejects an unitless value itself, but only once the converter runs — inside a pod, after a namespace slot and a corpus download have been spent. The genuinely silent error is a **wrong unit**: TraceV2's column is `think_time_us`, so a number transcribed out of it invites `us` (correct by luck) or `ns` — and `15000000ns` is 15 ms, a 1000×-too-tight cap that fails nowhere. Forcing the unit to be written is what makes that visible in review.

> **Choose the cap against KV residency, not just the gap distribution.** Any cap converts a cache-*cold* return (hours idle, prefix long since evicted) into a warmer one. That is unavoidable inside a bounded run, and it is material because prefix-cache hit rate dominates prefill cost in these corpora — so the cap that reproduces production behaviour is the one that reflects the deployment's KV residency time, not only where the gap distribution's knee sits.

##### `format` selects the whole chain

`otel-parquet` (the default) discovers `.parquet` files, runs `build-otel`, and converts with `blis convert otel`. `weka-jsonl` discovers `.jsonl`, **skips** `build-otel` (JSONL is already blis's native Weka shape), and converts with `blis convert weka`. The legal set mirrors the `prepare-trace` guard step exactly, and a test parses that guard's `case` arm to keep the two from drifting.

> **A `weka-jsonl` descriptor should set `shards: 0`.** The shard cap is applied *after* discovery and is format-agnostic, so the default of 39 truncates the discovered `.jsonl` list the same way it truncates parquet shards. The default is itself a dataset-specific number (39 is exgentic's shard count) — tracked as #913.

##### `revision` pins the dataset, and a moving ref defeats the purpose

`revision` accepts a commit SHA, tag, or branch; empty (the default) resolves the upstream default branch. The Task resolves whatever is given to a commit SHA and keys its raw-download cache by `<repo>@<sha>`, so a moving ref that advances upstream lands in a fresh download directory.

> **Prefer a commit SHA.** `tracePath` hashes the descriptor **as written**, so `revision: main` keys *stably* while upstream moves — and the converted-trace cache is keyed by `tracePath`, not by the resolved SHA. A re-run therefore reuses the previously converted trace even though `main` has advanced. Pinning a SHA is what makes the corpus reproducible; naming a branch only makes it *look* pinned.

**Validation is strict.** An unrecognized key at any level — top level, a `corpus:` section, or `replay:` — fails at assemble time with the legal key set in the message. Nothing is silently ignored. This is the point of the schema: a field that is folded into the corpus cache key but reaches no PipelineRun param changes the key *without* changing the corpus, which is strictly worse than being refused.

**The invariant:** every field under `corpus:` is hashed into the cache key **and** applied to a param. Every field under `replay:` is applied and deliberately **not** hashed. `pipeline/tests/test_corpus_schema.py` asserts both halves, so adding a field to the schema without wiring it up fails the suite.

#### `tracePath` is content-addressed

`sim2real assemble` names the corpus `traces/<sha12>`, where `<sha12>` is the first 12 hex chars of SHA-256 over a canonical JSON serialization (`sort_keys=True`, no whitespace) of the `corpus:` mapping **alone**:

- **No workload name.** Two cells with byte-identical `corpus:` content resolve to the same path and share one build. (The earlier `traces/<workload-name>-<sha12>` form built the identical corpus once per cell.)
- **No `replay:`.** Session counts cannot change the corpus, so cells differing only in `replay:` reuse the same cache entry by construction.

The hash covers the mapping **as written**, with no default-filling. An explicitly written `shards: 39` therefore keys differently from an omitted `shards`, even though both build the same corpus — deliberate conservatism, so that changing a default in `corpus_schema.py` cannot silently re-point existing cache entries.

#### Not yet expressible

These fields are **rejected**, each with its own reason, because nothing downstream honors them. Accepting them would recreate the defect this schema exists to remove. They become legal in the same change that makes them reach the Task.

| Field | Why it is refused | Tracked by |
|-------|-------------------|------------|
| `corpus.select.partition_pct` | Not a Task parameter — `prepare-trace` hard-codes the split percentage as a constant (`TEST_PCT = 30`). | — |
| `corpus.reconstruct.max_context` | No support anywhere: neither the Task nor `blis convert otel`/`blis convert weka` accepts a context ceiling. | — |

`corpus.upstream.revision`, `corpus.upstream.format` and `corpus.reconstruct.max_think_time` used to sit in this table. sim2real#905 wired all three: it declared each param in `pipeline/pipeline.yaml`, forwarded it in the `prepare-trace` task block, and moved the field into the schema tables — one change, per the rule that a field becomes legal in the same commit that makes it reach the Task.

Three tests keep the wiring honest, in a chain from the schema to the pinned Task:

| Test | What it would catch |
|------|---------------------|
| `test_every_schema_param_is_declared_in_pipeline_yaml` | a schema field whose param the Pipeline never declares — a Tekton admission error |
| `test_every_schema_param_is_forwarded_to_prepare_trace` | a param declared but not forwarded to the taskRef — **silently dormant**, since the Task then runs its own default |
| `test_905_params_are_declared_by_the_pinned_task` | a submodule rollback that drops a param assemble now always sends — every corpus PipelineRun would be rejected |

#### Migrating from the old `trace:` shape

`trace:` was **replaced, not dual-supported**: no trace run had ever executed, so there was no cached corpus, no collected results, and no shipped bundle to keep working. A document still carrying a non-empty `trace:` mapping fails at assemble with a message naming the replacement fields, rather than falling through to the generative path and being handed to blis as a "WorkloadSpec" whose content is `{trace: {...}}`.

| Old | New |
|-----|-----|
| `trace.source` | `corpus.upstream.source` |
| `trace.shards` | `corpus.upstream.shards` |
| `trace.split` | `corpus.select.partition` |
| `trace.filters.min_rounds` | `corpus.select.min_rounds` |
| `trace.sample.dedup_by_conversation` | `corpus.select.dedup_by_conversation` |
| `trace.sample.shuffle_seed` | `corpus.select.shuffle_seed` |
| `trace.convert.context_growth` | `corpus.reconstruct.context_growth` |
| `trace.pool.concurrent_sessions` | `replay.concurrent_sessions` |
| `trace.pool.total_sessions` | `replay.total_sessions` |
| `trace.filters.skip_branching` | *removed* — never had task-side support |
| `trace.convert.max_think_time` | `corpus.reconstruct.max_think_time` — now **required**, as a Go duration string (sim2real#905) |

`transfer.yaml`'s `version:` stays **3**. Synthetic (generative) workload descriptors do not change, so nothing a real bundle contains becomes invalid, and their PipelineRun params are byte-identical before and after.

#### Emitted PipelineRun params

Corpus workloads emit these; generative workloads emit **none** of them (and a non-empty `workloadSpec`, which corpus workloads leave empty so `observe` cannot source requests from a spec in corpus mode).

| Param | From |
|-------|------|
| `traceSpec` | the `corpus:` mapping as compact YAML. Only its **emptiness** is load-bearing — `prepare-trace` tests `[ -z "$(params.traceSpec)" ]` to recognize a generative workload and skip the corpus build. Nothing parses the content. |
| `tracePath` | `traces/<sha12>` (above) |
| `traceSource`, `traceRevision`, `traceFormat`, `traceShards` | `corpus.upstream.*` |
| `traceSplit`, `traceMinRounds`, `traceDedupByConversation`, `traceShuffleSeed` | `corpus.select.*` |
| `traceContextGrowth`, `traceMaxThinkTime` | `corpus.reconstruct.*` |

`replay.*` emits **no** params of its own: #900 folded `concurrent_sessions` / `total_sessions` into the rendered `observeArgs` as `--concurrent-sessions` / `--total-sessions`, so the standalone `concurrentSessions` / `totalSessions` params no longer exist.

Every param above must be both **declared** in `pipeline/pipeline.yaml` and **forwarded** to the `prepare-trace` taskRef, and the two failures differ:

- Not declared → Tekton rejects the PipelineRun outright at creation. Loud.
- Declared but not forwarded → the PipelineRun carries the value, the Task never receives it, and it applies its own default. **Silent**, and exactly the dormancy sim2real#905 existed to remove.

`test_every_schema_param_is_declared_in_pipeline_yaml` and `test_every_schema_param_is_forwarded_to_prepare_trace` guard the two halves respectively.

---

## Parallel Pool Execution

`cluster.py provision <cluster_id> --namespaces NS1,NS2,...` provisions N namespace slots, each bootstrapped identically. `sim2real assemble` generates one PipelineRun per `(workload, package, iteration)` tuple (the shared Tekton Pipeline itself is applied by `cluster.py provision`). `deploy.py run` orchestrates execution by assigning pairs to free slots, polling for completion, and retrying on timeout. Use `deploy.py collect` to pull results off-cluster. `deploy.py status` reads progress from the ConfigMap.

| Artifact | Written by | Read by |
|----------|-----------|---------|
| ConfigMap `sim2real-progress-{scenario}-{run}` | `deploy.py run`, `deploy.py reset` | All subcommands |

All subcommands (`status`, `collect`, `run`, `reset`, `wipe`) use a `sim2real-progress-{scenario}-{run}` ConfigMap as the sole progress store. The name embeds both the `scenario` field from `transfer.yaml` and the run name, so two experiment repos with the same run name land in distinct ConfigMaps (issue #551). Each run gets its own ConfigMap, avoiding cross-run conflicts. A configured namespace is required.

---

## Scenario Overlay Format

Overlay files live under `workspace/translations/<hash>/generated/`. The baseline overlay layout differs between the two translation producers:

- **Skill-driven** (`/sim2real-translate`) — writes one overlay per referenced baseline at `baselines/<name>/baseline_config.yaml` (nested under a `baselines/` umbrella; issue #544). Assemble reads each baseline's overlay from its own directory.
- **BYO** (`sim2real translation register`) — writes a single shared overlay at `baseline_config.yaml` (the operator-supplied `--baseline-config` file). Applied to every baseline in the manifest; used as the fallback when a per-baseline directory is absent.

Overlays present in each translation:

- `baselines/<name>/baseline_config.yaml` — per-baseline overlay (skill-driven). One directory per baseline that any algorithm's `defaults` cross-references.
- `baseline_config.yaml` — legacy shared overlay (BYO). Present when `--baseline-config` was passed to `translation register`.
- `{algo_name}/{algo_name}_config.yaml` — per-algorithm treatment overlay (verbatim copy of the `--config` file for BYO; produced by the skill for skill-driven).

### Assembly formula

`sim2real assemble` deep-merges via `pipeline/lib/values.py:deep_merge`:

```python
baseline_resolved = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(baseline_resolved, treatment_bundle_diffs, algo_overlay)
```

Where `baseline_bundle` is the experiment's `baselines/baseline.yaml` (issue #544 — the baseline file is always this name), `treatment_diffs` is the experiment's optional `treatment.yaml`, `framework_defaults` merges `<experiment-root>/baselines/defaults/*.yaml` fragments (opt out via `transfer.yaml:defaults.disable`), and the overlays are the files written by `sim2real translation register`.

### Deep merge semantics

- Dict keys merge recursively (overlay overrides base)
- Lists where every entry is a Kubernetes manifest (a dict with `apiVersion` and
  `kind` — e.g. `extraObjects:`) merge by object identity. Entries with
  `metadata.name` merge by `(apiVersion, kind, metadata.name)` — distinct manifests
  are preserved, and a same-identity overlay entry patches the matching base manifest.
  Entries without `metadata.name` (e.g. `generateName`, `List` kinds) are carried
  through untouched (base first, then overlay), never folded. A duplicate identity
  within either list raises `ValueError`
- Lists of dicts with a common top-level `name` field merge by name
- Lists of dicts without a common key merge positionally. If the two entries at a
  position carry `apiVersion`/`kind` markers that differ and are not both absent — e.g.
  a malformed manifest missing one of them — the fold raises `ValueError` instead of
  silently smearing them. (Two malformed manifests with identical markers still fold;
  malformed manifests are out of scope — kubectl/Helm reject them.)
- Lists of scalars are replaced entirely — **except** CLI-flag lists (see below)
- Treatment overlay only needs the delta from baseline_resolved (shared config propagates automatically)

#### CLI-flag lists merge by flag name (issue #851)

Scalar lists at key paths ending in `vllm.additionalFlags` — matched as a **suffix**,
so `decode` and `prefill` are both covered — merge by flag name instead of being
replaced. Before #851, a flag list set by two layers kept only the last writer's copy,
so a `config.md`-derived flag reached the cluster only if the generated overlay happened
to restate it verbatim.

- Each entry is keyed on its flag name: `--max-num-seqs=256` keys on `--max-num-seqs`;
  a bare `--enable-chunked-prefill` is its own key.
- `--no-X` and `--X` canonicalize to **one** key, so a later layer stating either form
  overrides the earlier rather than emitting two contradictory flags whose winner would
  depend on vLLM's argparse ordering.
- Base entries come first in base order; overlay-only entries are appended — the same
  convention the named-key dict tier uses.
- **Removal:** state the `--no-` form to flip an inherited boolean flag, or set the list
  to `[]` to clear it entirely. There is deliberately no per-entry deletion sentinel for
  `--key=value` flags.
- **Refused:** an entry that is not a `--`-prefixed string (so a space-separated arg list
  such as `router.proxy.args` cannot be mis-keyed if pasted in), and two entries in one
  list that collapse to the same key. Both guards apply only when two layers actually
  contribute, since a single contributor has nothing to merge.

The scoping is by key path because `router.proxy.args` is also a scalar list, but its
values are separate elements (`["--concurrency", "8"]`) rather than `--key=value` pairs;
flag-name merging there would treat `8` as a flag name.

Scalar lists that still replace — `router.proxy.args` and
`{decode,prefill}.extraContainerConfig.securityContext.capabilities.add` — now produce a
warning from `sim2real assemble` when two layers both set one, naming the key path, the
discarded values, and which two layers disagreed.

### Required structure

Both overlays are llmdbenchmark scenario overlays. They must be valid YAML with a top-level `scenario:` list containing a single dict:

```yaml
scenario:
  - name: "<scenario-name>"

    # Fields to add or override (only include what you're changing)
    extraObjects: [...]
    inferenceExtension: {...}
    images: {...}
```

### Plugin config

The EPP plugin configuration goes inside `router.epp.pluginsCustomConfig` as a YAML-in-YAML string:

```yaml
router:
  epp:
    pluginsConfigFile: custom-plugins.yaml
    pluginsCustomConfig:
      custom-plugins.yaml: |
        apiVersion: inference.networking.x-k8s.io/v1alpha1
        kind: EndpointPickerConfig
        plugins:
        - type: my-plugin
          name: my-plugin
          parameters:
            threshold: 5
        schedulingProfiles:
        - name: default
          plugins:
          - pluginRef: my-plugin
```

### Typical overlay content

**Baseline overlay** — adds InferenceObjectives and the baseline EPP plugin config:
- `router.inferenceObjectives` (list of `{name, priority}` — the routerlib chart renders each as an `InferenceObjective` CR)
- `router.epp.pluginsCustomConfig` (baseline scorer config)

**Treatment overlay** — only the delta from baseline:
- `router.epp.pluginsCustomConfig` (evolved scorer config)
- `router.epp.image` (custom EPP image — injected by `sim2real assemble` from `translation_output.json:algorithms[i].image_ref` for the matching algorithm; registry and bare repository written as separate fields)

If treatment uses the same InferenceObjectives as baseline, do NOT repeat them — they propagate from `baseline_resolved`.

---

## Common patterns

```bash
# Assemble a run
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1

# Re-assemble the same run (regenerates PipelineRuns; discards the results of
# every pair it regenerates)
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1 --force

# Re-assemble one package after editing its overlay, keeping collected results
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1 \
    --package softreflective --force --no-wipe

# Reset failed pairs without acting (preview the plan)
python pipeline/deploy.py reset --dry-run

# Run one package only
python pipeline/deploy.py run --package baseline

# Collect results for a specific package
python pipeline/deploy.py collect --package treatment
```

## Troubleshooting

Pipeline-level recovery and operational commands. For experiment-config issues (EPP RBAC, logging verbosity), see [`docs/troubleshooting.md`](../docs/troubleshooting.md).

### Rerun failed pairs

```bash
python pipeline/deploy.py run [--remote] --status failed --force
```

### Stop orchestration

```bash
python pipeline/deploy.py stop
```

### Clean up cluster artifacts

Removes `PipelineRun` objects and deployed Helm charts:

```bash
python pipeline/deploy.py reset
```

### Erase collected data

```bash
python pipeline/deploy.py wipe
```

### Keep cluster artifacts (skip llmdbenchmark teardown)

```bash
python pipeline/deploy.py run [--remote] --skip-teardown
```
