# pipeline/

Scripts that drive the sim2real transfer pipeline. Run from the repo root.

The pipeline has two phases:

```
cluster.py provision  (one-time per cluster — bootstrap namespaces, RBAC, PVCs, Tekton tasks, Pipeline definition)
                   ↓
setup.py → sim2real translation register → sim2real assemble → deploy.py   (per-workspace + per-run)
```

`sim2real.py`'s `use` and `list runs` subcommands manage runs independently of the main flow.

---

## Running with an Experiment Repo

When algorithm content lives in its own repo (peer directory), pass `--experiment-root`:

```bash
# From the sim2real/ directory:

# One-time per cluster (idempotent; re-run when adding/changing slots):
python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,...

# Per-workspace + per-run cycle:
python pipeline/setup.py       --experiment-root ../admission-control
python pipeline/sim2real.py translation register \
    --algorithm <name> --image <ref> --config <treatment-overlay-path>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run <run_name>
python pipeline/deploy.py      --experiment-root ../admission-control
```

The experiment repo must contain:
- `transfer.yaml` (or `config/transfer.yaml` for backward compat) — v3 schema with `component`, `baselines`, `algorithms`, `workloads` fields
- `baselines/<name>.yaml` — llmdbenchmark-style scenario file per baseline (referenced from `transfer.yaml:baselines[].scenario`)
- `baselines/defaults/*.yaml` (optional) — framework workaround fragments merged as an overlay under each baseline (opt out via `defaults.disable`)
- `workloads/` directory referenced from `transfer.yaml:workloads`
- `workspace/` in `.gitignore`

`pipeline/pipeline.yaml` is the static Tekton Pipeline definition (applied by `cluster.py provision`; `sim2real assemble` generates PipelineRuns that reference it).

---

## cluster.py

Cluster-side bootstrap. Run once per cluster, before any per-workspace or per-run commands. Idempotent — safe to re-run when adding namespace slots or rotating secrets.

```bash
python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,... [flags]
```

| Flag | Env var | Default |
|------|---------|---------|
| `--namespaces NS1,NS2,...` | — | required — slot namespaces to provision |
| `--storage-class SC` | — | cluster default |
| `--hf-token TOKEN` | `HF_TOKEN` | prompt |
| `--github-token TOKEN` | `GITHUB_TOKEN` | optional |
| `--registry-user USER` | `REGISTRY_USER` | prompt |
| `--registry-token TOKEN` | `REGISTRY_TOKEN` | prompt |
| `--dockerhub-user USER` | `DOCKERHUB_USER` | optional |
| `--dockerhub-token TOKEN` | `DOCKERHUB_TOKEN` | optional |
| `--pipeline-yaml PATH` | — | `<repo-root>/pipeline/pipeline.yaml` |
| `--experiment-root PATH` | — | cwd |

**`--pipeline-yaml PATH`** — override the Tekton Pipeline manifest applied to every namespace. When set, the path is recorded in `cluster_config.json["pipeline_yaml"]` and picked up by `apply_cluster_resources` on this run. **The flag is not sticky**: a re-run of `cluster.py provision <same-id>` without `--pipeline-yaml` drops the key and reverts to the built-in default — pass `--pipeline-yaml` on every re-run where you want the override.

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

Top-level CLI introduced in step-1 of the v2 refactor. Subcommands: `translation register` (BYO), `translate` and `translate --resume` (skill-driven checkpoint), `build` (per-algorithm image build against a checkpointed translation), `assemble` (materialize a run), `use`, `list runs`, `list translations`.

### Register a translation (BYO)

`sim2real.py translation register` imports a pre-built EPP image and its treatment overlay YAML as a registered translation. Downstream commands (`assemble`, `deploy.py run`) treat a BYO-registered translation identically to a skill-produced one.

```bash
python pipeline/sim2real.py translation register \
    --algorithm softreflective \
    --image ghcr.io/kalantar-msb/sr-router:some-tag \
    --config path/to/treatment-overlay.yaml \
    [--baseline-config path/to/baseline-overlay.yaml] \
    [--registered-hash <expected-sha256-hex>] \
    [--experiment-root PATH]
```

| Flag | Required | Notes |
|------|----------|-------|
| `--algorithm NAME` | yes | `[A-Za-z0-9][A-Za-z0-9._-]*`, max 128 chars, reject `.` / `..`. Also written as the translation's `alias`. Single algorithm per call. |
| `--image REF` | yes | Registry ref. If it contains `@sha256:HEX`, that digest is recorded; otherwise `image_digest` is `null` with a warning. |
| `--config PATH` | yes | Treatment overlay YAML. Validated as YAML before any writes. |
| `--baseline-config PATH` | no | Baseline overlay YAML, if the translation needs one. |
| `--registered-hash HASH` | no | Assert the computed `translation_hash` equals this value; error if not. |
| `--force` | no | Reassign the alias (`--algorithm` value) if another translation already owns it. Clears the alias on the previous translation atomically. |
| `--experiment-root PATH` | no | Defaults to cwd. |

**Outputs** — under `workspace/translations/<translation_hash>/`:

- `translation_output.json` — algorithm index + provenance (v1 schema). Includes `alias` (top-level, same value as `--algorithm`) and per-algorithm `image_ref` / `image_digest` inside `algorithms[i]`. Step-1 legacy files (top-level `image_ref`) remain readable via a compatibility shim in `pipeline/lib/translation_ref.py`.
- `registered.json` — image ref + digest (BYO-only audit trail; v1 schema).
- `generated/<algorithm>/<algorithm>_config.yaml` — the treatment overlay content.
- `generated/baseline_config.yaml` — present only when `--baseline-config` is given.

**`translation_hash` derivation (BYO):** SHA-256 hex over canonical JSON of `{algorithm_name, config_sha256, image_digest_or_ref}`. Deterministic — same inputs produce the same hash. When the image ref lacks a digest, the raw ref string is substituted for `image_digest_or_ref`, so the hash is stable within the offline session but changes if the same image is later re-registered with a digest ref.

**Idempotency:** re-registering the same triple (algorithm, image, config content) is a no-op — the existing translation directory is detected, a warning is printed, and exit is 0.

**Failure modes:**

- `--config` file missing or malformed YAML → exit 2, no writes.
- Existing translation directory records a different algorithm name (hash collision) → exit 2, no writes.
- `--registered-hash` given and does not match computed → exit 2, no writes.
- Another translation already owns the `--algorithm` alias with a different hash → exit 2, no writes; re-run with `--force` to reassign.

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
| Complete (all `<algo>_output.json` present) | Print `translation already complete — run 'sim2real build' next` and exit 0. | Same. | Delete + recreate; operator re-runs the skill. |

The translation hash is derived from `transfer.yaml`'s translation slice (scenario, component, context, per-algorithm sources) plus the SHA-256 of each `algorithms[i].source` file's bytes (see `pipeline/lib/slicer.py:translation_hash_with_sources`). Two runs of `translate` with the same `transfer.yaml` and the same source files produce the same hash and reuse the same `translations/<hash>/` directory.

**Outputs** — under `workspace/translations/<translation_hash>/` (initial run only; the skill populates the rest):

- `skill_input.json` — the material `/sim2real-translate` reads. Pinned schema (see `docs/epics/step-2/design.md`) — includes `translation_hash`, absolute `experiment_root` and `translations_dir`, `scenario`, a `baselines[]` list (one entry per baseline that any algorithm's `defaults` cross-references, each carrying `name` and a `generated_overlay_path` under `generated/baseline_<name>/`), `algorithms[]` (each with `source_path`, `source_sha256`, `output_dir`, `config_output_path`, and a `baseline_overlay_path` resolved via `defaults`), and `context` (text + file paths).
- `translation_output.json` — algorithm index with `image_ref: null` on every entry. `sim2real build` fills these in later.
- `generated/<algo>/` (empty) — the skill writes `cmd/`, `pkg/`, `<algo>_output.json`, and `<algo>_config.yaml` under this directory.
- `generated/baseline_<name>/` (empty) — one directory per referenced baseline. The skill writes `baseline_config.yaml` under each. Shared by all algorithms whose `defaults` names that baseline.

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
4. **Buildkit dispatch**: submits an in-cluster `moby/buildkit:latest` pod that reads component source from a PVC and pushes to the target registry via the credentials Secret whose name is recorded in `cluster_config.json:secret_names.registry_creds` (default `registry-creds`, provisioned by `cluster.py provision`). Same `pipeline/scripts/build-epp.sh` code path `deploy.py:_cmd_build` has always used; the Secret name is threaded in via `--registry-secret-name`.
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
    [--force]
```

`--translation` accepts an alias, a hash prefix (min 4 chars), or the full 64-char hash. Aliases are checked before prefixes, so a 4-char alias always resolves to its exact owner rather than a colliding hash prefix. Ambiguous prefixes exit 2 listing the candidates. Run `sim2real list translations` to see what's available.

**Inputs read:**

- `workspace/translations/<hash>/translation_output.json` — algorithms with per-algo `image_ref`. Legacy step-1 files (top-level `image_ref`) are read transparently via `pipeline/lib/translation_ref.py`'s on-read shim.
- `workspace/translations/<hash>/generated/baseline_<name>/baseline_config.yaml` — per-baseline overlay (skill-driven; written by `/sim2real-translate` for each baseline that any algorithm's `defaults` names). Assemble applies each entry to its matching `manifest.baselines[]` entry.
- `workspace/translations/<hash>/generated/baseline_config.yaml` — legacy BYO overlay (written by `translation register --baseline-config`). Applied to every baseline in the manifest when the per-baseline directory above is absent — falls back automatically so BYO translations remain resolvable.
- `workspace/translations/<hash>/generated/<algo>/<algo>_config.yaml` — per-algorithm treatment overlay.
- `workspace/clusters/<cluster_id>/cluster_config.json` — namespaces, workspace bindings, hf secret name.
- `<experiment-root>/transfer.yaml` (or `config/transfer.yaml`) — v3 manifest.
- `<experiment-root>/baselines/<name>.yaml` — baseline bundles referenced by `transfer.yaml:baselines[].scenario`.
- `<experiment-root>/baselines/defaults/*.yaml` — framework defaults overlays (opt-out via `transfer.yaml:defaults.disable`).
- `<sim2real-repo>/.gitmodules` and `<sim2real-repo>/{inference-sim,llm-d-benchmark}/` — the framework submodules' clone URLs (from `.gitmodules`) and HEAD SHAs (from `git rev-parse HEAD`), which populate `benchmarkGitRepoUrl` / `benchmarkGitCommit` / `blisGitRepoUrl` / `blisGitCommit` in every generated PipelineRun. Initialize with `git submodule update --init` in the sim2real repo before running `sim2real assemble`; a missing submodule falls back to `"unknown"` for its commit SHA (assemble prints a warning) and the cluster-side `git clone` step then fails visibly at the right point.

**Outputs written to `workspace/runs/<run>/`:**

| File | Purpose |
|------|---------|
| `manifest.assembly.yaml` | Verbatim snapshot of the assembly slice from `transfer.yaml` (produced by `pipeline/lib/slicer.py`), preceded by a top-level `replicas: N` field. |
| `run_metadata.json` | `{version, run_name, translation_hash, cluster_id, params_hash, image_tag, replicas, assembled_at}` — pinned schema, `version: 1`. |
| `cluster/baseline.yaml` | Resolved baseline scenario (framework defaults → bundle → baseline overlay). |
| `cluster/<algo>.yaml` | Resolved treatment scenario per registered algorithm (baseline_resolved → treatment bundle diffs → algo overlay → injected image_tag). |
| `cluster/pipelinerun-<workload>\|<package>\|iN.yaml` | One PipelineRun per (workload, package, iteration) tuple. Consumed by `deploy.py run`. |

**Assembly formula** (deep-merged via `pipeline/lib/values.py:deep_merge`):

```
baseline_resolved  = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(baseline_resolved, treatment_bundle_diffs, algo_overlay)
```

Then each treatment scenario has `images.inferenceScheduler` set from that algorithm's own `translation_output.json:algorithms[i].image_ref`, and every scenario has `huggingface.secretName` set from `cluster_config.json:secret_names.hf_token`.

**`params_hash`** is SHA-256 over the canonical bytes of `manifest.assembly.yaml` with the top-level `replicas` field excluded — bumping `--replicas N` must not change the hash. Recorded in `run_metadata.json` for drift detection on re-assemble.

**Replicas.** `--replicas N` (default 1) is the number of iterations per (workload, package) pair. Each iteration gets its own PipelineRun (`{phase}-{workload}-{run}-iN`, with `_` → `-` normalization) and its own results subdirectory (`results/<phase>/<workload>/iN/`). `manifest.assembly.yaml` carries a top-level `replicas: N`, and `run_metadata.json` carries the same value as a schema field. Both are set on every assemble.

**Additive-merge (grow-only).** Re-assembling an existing run with `--replicas` interacts with the prior state as follows:

- `N == prior_replicas` — true no-op. No files rewritten; the assemble returns silently.
- `N > prior_replicas` — additive grow. Existing PipelineRun files (`i1..i{prior}`) are preserved byte-for-byte and by mtime; new files are emitted for `i{prior+1}..iN`. `manifest.assembly.yaml` and `run_metadata.json` are rewritten with the new `replicas` count; `params_hash` is preserved (drift check passed).
- `N < prior_replicas` — refused with `run '<name>' already has <prior> replicas; refusing to shrink to <N>. Replica shrink is tracked in #506.` This guard runs BEFORE the drift check, so `--force` does NOT bypass it.

Two invariants shape the grow-only path:

- **Drift check.** The current assembly-slice content hash is compared against the run's recorded `params_hash`. Any mismatch refuses the assemble unless `--force` is passed — with `--force`, the whole run directory is rebuilt from scratch (existing `iN/` files are lost). Without `--force`, matching hashes are required to reach the additive-grow branch.
- **Legacy-run guard.** A pre-step-5 run has no `replicas` field in its `manifest.assembly.yaml`. Any re-assemble against this shape is refused unless `--force`, whether or not `--replicas` was explicitly passed — the `--replicas` argparse default (`1`) still trips the guard. With `--force`, the run is rebuilt from scratch as a fresh replica-shaped run.

**PipelineRun name length.** `metadata.name` is `{phase}-{workload}-{run}-i{iteration}` (with `_` → `-` normalization). This is a Kubernetes DNS subdomain, so the 253-char RFC 1123 limit applies. `assemble` validates each generated PipelineRun name and exits 2 with `error: PipelineRun name '<name>' is <len> chars, exceeds the 253-char DNS subdomain limit` if any pair (phase × workload × run × iteration) would overflow. Fail-fast at assemble time is preferable to Tekton admission rejection at dispatch time.

**Algorithm filtering:** algorithms listed in `transfer.yaml:algorithms` but absent from `translation_output.json:algorithms` are skipped with a warning — the run still assembles for the algorithms that are registered. Algorithms that ARE in the translation but whose `image_ref` is still `null` (skill-driven translation before `sim2real build`) fail fast: `assemble` exits 2 with `translation <ref> not built for algorithms: <names> — run 'sim2real build --translation <ref>' first`.

**Failure modes:**

- Existing `runs/<run>/` without `--force` → exit 2 with `--force` hint. No writes.
- Missing translation directory or `translation_output.json` → exit 2, no writes.
- Missing `cluster_config.json` for `--cluster` → exit 2, no writes.
- Workload file referenced in `transfer.yaml:workloads` missing → exit 2, no writes.
- Malformed YAML anywhere in the input chain → exit 2, no writes.

`--force` recursively deletes `workspace/runs/<run>/` before re-materializing it.

---

## deploy.py

Ensures all scenario images exist and orchestrates PipelineRun execution across namespace slots. Operates independently of `transfer.yaml` — driven by workspace files, `setup_config.json` (workspace-scoped), and `clusters/<id>/cluster_config.json` (namespaces, PVCs, secrets).

```bash
python pipeline/deploy.py {build|run|status|collect|stop|reset|wipe|pairs} [flags]
```

Common flags (all subcommands):

| Flag | Default | Notes |
|------|---------|-------|
| `--run NAME` | from `setup_config.json` | override active run |
| `--experiment-root PATH` | cwd | path to experiment repo |
| `--skip-build` | false | skip image build pre-flight |

**Image build** — `deploy.py build` (called implicitly as pre-flight by `deploy.py run`) iterates over all resolved scenarios in `cluster/`, collects unique `images.inferenceScheduler` refs, and builds any that are stale. Baseline images are tagged by the component directory's HEAD SHA (8 chars); algorithm images are tagged `{run_name}-{algo_name}` (per-algorithm). For each algorithm build, the component working tree is reset to baseline and only that algorithm's files are applied before building. Source hash comparison skips builds when the image is already current.

**Pair keys.** A pair key names a `(workload, package, iteration)` triple in the ConfigMap and on disk. Canonical grammar:

```
pair_key := "wl-" workload "|" package "|" iter
workload := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # kebab-case, no leading/trailing hyphen
package  := [a-z0-9]([a-z0-9-]*[a-z0-9])?    # same shape as workload
iter     := "i" [1-9][0-9]*                  # positive decimal, no leading zeros; i0 is invalid
```

Example: `wl-chat-mid|baseline|i1`.

The parser accepts a legacy no-suffix form (`wl-<workload>|<package>`) and reads it as `iteration=1`; canonical renderings always include the `|iN` suffix. Metadata keys (`_meta`, `_notes`, anything starting with `_`) are filtered out upstream via `deploy._is_pair_key` and never reach the parser.

**Pair discovery.** `deploy.py run` discovers `pipelinerun-*.yaml` files at the `cluster/` root. Each file's pair key is derived as `wl-` + filename stem minus the `pipelinerun-` prefix — the assembler names files as `pipelinerun-<workload>|<package>|iN.yaml`, so the pair key falls out directly.

**Scoping flags on filter-aware subcommands** (`run`, `status`, `collect`, `reset`, `wipe`):

| Flag | Scope | Notes |
|------|-------|-------|
| `--only PAIR…` | Full pair keys (with or without `wl-` prefix) | Narrows both workload and package. Takes precedence over `--workload`. |
| `--workload NAME…` | Workload dimension | Multiple values are OR'd within the flag. |
| `--package NAME…` | Package dimension | Multiple values are OR'd within the flag. |
| `--iteration SPEC` | Iteration dimension | Grammar below. |
| `--status STATE` | Progress state (`pending` / `running` / `done` / `failed` / `timed-out` / `stalled`) | Not available on every subcommand — see per-subcommand tables. |

Different flags compose as AND: `--workload X --package baseline --iteration 1,3` narrows to iterations 1 and 3 of workload X's baseline package.

**Iteration filter spec.** The `--iteration` value is a comma-separated list of tokens; each token is either a positive integer (`3`) or an inclusive range (`1-3`). Whitespace around commas and hyphens is tolerated. Rejected: `0`, negatives, reversed ranges (`5-1`), non-integer tokens (`abc`), leading zeros (`01`), empty spec, empty token. Malformed specs fail with `malformed iteration spec '<spec>': <reason>` before any pair discovery runs. Legacy pair keys (no `|iN` suffix) parse as iteration `1`, so `--iteration 1` matches them.

**Collection phases** — `deploy.py collect` derives valid phases dynamically from progress data (packages with status `done`). Falls back to `[baseline, treatment]` when no progress exists. Use `--package` to filter, or `--package experiment` to collect all known phases.

**`--skip-build`** — skips the image build; use when resubmitting after a failed PipelineRun without changing the scorer.

**Subcommands:**

```bash
python pipeline/deploy.py build   [flags]   # ensure all scenario images exist (pre-flight for run)
python pipeline/deploy.py run     [flags]   # ensure images + orchestrate parallel pool execution
python pipeline/deploy.py status            # show progress snapshot of all (workload, package, iteration) triples
python pipeline/deploy.py collect [flags]     # pull results from the cluster PVC
python pipeline/deploy.py stop               # stop the remote orchestrator Job
python pipeline/deploy.py reset [flags]     # reset all non-pending pairs to pending (with cluster cleanup)
python pipeline/deploy.py wipe  [flags]     # delete local result files for pairs in scope
python pipeline/deploy.py pairs   [flags]   # list available pair keys, workloads, and packages
```

**`deploy.py run`** — assigns `(workload, package, iteration)` triples to free namespace slots, polls for completion, and retries pairs that time out. Reads progress from the run-scoped `sim2real-progress-{run}` ConfigMap to resume interrupted runs. Requires a configured namespace. Use `deploy.py collect` to pull results off-cluster after runs complete. The run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`; if the run directory or its `cluster/` subdirectory does not exist, `deploy.py run --run <R>` exits with `run 'sim2real assemble --run <R>' first`, and if `run_metadata.json` is missing, unparseable, or lacks a non-empty `cluster_id`, it exits with `run metadata corrupted; re-assemble`.

| Flag | Default | Description |
|------|---------|-------------|
| `--remote` | — | Submit orchestrator as in-cluster Job instead of running locally |
| `--only PAIR…` | — | Scope execution to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | — | Scope execution to pairs matching these workloads (comma or space-separated) |
| `--package NAME…` | — | Scope execution to pairs matching these packages (comma or space-separated) |
| `--iteration SPEC` | — | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE` | — | Scope execution to pairs with this status (e.g. `failed`, `timed-out`) |
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

**Live slot-list updates (issue #372)** — each cycle re-reads `workspace/clusters/<cluster_id>/cluster_config.json` and updates the in-memory slot list. Adding a namespace makes it eligible for dispatch on the next cycle (subject to `_check_slot_ready` — PVCs bound, HF secret present); removing a namespace stops new dispatch to it but does **not** cancel a pair already running there — that pair drains and the slot is freed normally on completion. `namespaces[0]` (the primary, where the run-scoped progress ConfigMap lives) is pinned for the lifetime of the run; mid-run changes to it are logged and ignored. Parse / IO errors keep the prior list. The safe way to add a slot is `cluster.py provision <cluster_id> --namespaces NS1,NS2,NS3` (re-run with the new full list; provisions before publishing the change). When issue #377 lands, `deploy.py slots add NS` will be the operator-friendly form.

**Capacity probe filtering** — `pipeline/lib/capacity.py` filters cluster nodes the same way the K8s scheduler would before summing allocatable/requested GPUs. A node is excluded if cordoned (`spec.unschedulable: true`), if it carries a `NoSchedule`/`NoExecute` taint that no role's tolerations match, or if its `nvidia.com/gpu.product` label is not in the set required by the scenario. The required product set is read from `scenario[0].{decode,prefill}.acceleratorType.{labelKey, labelValue}`. When no per-role product constraint can be extracted, cordon and taint screening still apply on cluster facts. Tolerations are currently treated as empty per follow-up issue #263 — every blocking taint excludes the node until that's lifted.

**Pair statuses:** `pending` → `running` → `done`. Failure paths: `running` → `failed` (hard failure or non-recoverable pending), `running` → `timed-out` (4h timeout exceeded), `running` → `pending` (recoverable early reclaim, repeats up to `--max-pending-stalls` times) → `stalled`.

**Auto-cleanup** — when a PipelineRun succeeds, the orchestrator deletes the PipelineRun CR from the cluster. Failed PipelineRuns are left in place for debugging (`kubectl describe`, pod logs). Use `reset` to remove them when done. Note: `--skip-teardown` only suppresses the Tekton `llmdbenchmark-teardown` task (Helm-level resource cleanup); PipelineRun CR deletion by the orchestrator is unaffected. Use `--preserve-pipelineruns` to suppress PipelineRun CR deletion on success — useful for debugging steps that fail silently (e.g., `set +e` scripts that exit 0 despite internal errors).

**Remote mode** — `deploy.py run --remote` submits the orchestrator as a Kubernetes Job (`sim2real-orchestrator`) instead of running locally. The launcher builds the EPP image locally, packs workspace files into a ConfigMap, applies the Job, and waits for the pod to reach Running. Use `stop` to cancel, `status` to check progress, and `collect` to pull results after completion. Requires `orchestrator_image` in `setup_config.json`.

**`deploy.py status`** — prints the current state of all pairs. Reads from the run-scoped `sim2real-progress-{run}` ConfigMap. Requires a configured namespace.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Filter by workload names (comma or space-separated) |
| `--package NAME…` | Filter by package names (comma or space-separated) |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE` | Filter by status (e.g. `running`, `done`, `failed`) |
| `-s`, `--silent` | Suppress the per-pair table and banner; print only the summary line (machine-readable) |

**`deploy.py collect`** — extracts results from the cluster PVC and writes to `workspace/runs/<run>/results/{phase}/<workload>/i<N>/` (one subdirectory per iteration). Repeated collects are incremental: each workload's remote `trace_data.csv` mtime is probed and skipped if the local copy is already up to date. If the mtime probe fails (e.g., pod not running), collection falls back to a full copy — this is the expected degradation path. Like `deploy.py run`, the run's cluster is resolved from `workspace/runs/<R>/run_metadata.json:cluster_id`; missing `runs/<R>/` or `runs/<R>/cluster/` exits with `run 'sim2real assemble --run <R>' first`, and a missing / unparseable `run_metadata.json` or missing `cluster_id` exits with `run metadata corrupted; re-assemble`.

Per phase, the resolved llm-d-benchmark plan YAMLs are also pulled into `workspace/runs/<run>/results/{phase}/plans/{flow}/*.yaml` (top-level numbered manifests + `config.yaml`, no `helm/` subdir). Plans are workload-invariant within a phase, so collect picks one workload's latest `root-*` render to source the phase's plans. Plan extraction is best-effort and non-fatal — failures warn but do not block trace collection.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope to specific pair keys — narrows both workload and package (comma or space-separated, `wl-` prefix optional; takes precedence over `--workload`) |
| `--workload NAME…` | Scope to pairs matching these workloads (comma or space-separated) |
| `--package NAME…` | Scope to pairs matching these packages (comma or space-separated). Pass the synthetic value `experiment` to collect every package directory of the scoped pairs. |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--skip-logs` | Skip vLLM and EPP log files, collect only traces |

When `--only` or `--workload` is given, only matching workload subdirectories are pulled from the PVC (instead of entire phase directories). Multiple values within a flag use OR (union): `--workload X Y` matches pairs for workload X or Y. Different flags compose as AND: `--workload X Y --package baseline` scopes to baseline pairs whose workload is X or Y, and pulls those workloads from the baseline phase only — pairs of other packages are not in scope and do not trigger "skipping" warnings. The synthetic `--package experiment` value is the carve-out: it does not narrow the pair set, so it preserves today's "every package of the scoped pairs" behavior. Requires progress data to resolve pairs.

**`deploy.py stop`** — deletes the `sim2real-orchestrator` Kubernetes Job (with cascading pod deletion) in the primary namespace. Only meaningful when the orchestrator runs as an in-cluster Job. Pair state is left as-is. If no remote orchestrator Job exists, prints a message and returns. Use `reset` separately to clear failed/stalled pair state.

**`deploy.py reset`** — resets all non-pending pairs to `pending` and removes their cluster resources (PipelineRuns, Helm releases). This includes `done` pairs — use `--preserve-done-status` to clean up cluster resources for done pairs without re-queuing them.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope reset to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Scope reset to pairs matching these workloads (comma or space-separated) |
| `--package NAME…` | Scope reset to pairs matching these packages (comma or space-separated) |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
| `--status STATE` | Scope reset to pairs with this status |
| `--preserve-done-status` | Keep done pairs' status unchanged (cluster cleanup only) |
| `--dry-run` | Print what would be reset without acting |

**Safety:** Results in `workspace/runs/<run>/results/` are preserved — only cluster resources and ConfigMap status are affected.

**`deploy.py wipe`** — deletes local result files (`results/<package>/<workload>/`) for all pairs in scope. Does **not** modify pair status in the ConfigMap. Pairs with no results on disk are skipped. Empty package directories are cleaned up automatically.

| Flag | Description |
|------|-------------|
| `--only PAIR…` | Scope wipe to specific pair keys (comma or space-separated, `wl-` prefix optional) |
| `--workload NAME…` | Scope wipe to pairs matching these workloads (comma or space-separated) |
| `--package NAME…` | Scope wipe to pairs matching these packages (comma or space-separated) |
| `--iteration SPEC` | Scope to iteration(s): `'2'`, `'1,3'`, `'1-3'`, `'1,3-5'` |
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

**`sim2real list translations`** — Walks `workspace/translations/*/translation_output.json` and prints one row per translation, newest first (by `created_at`). Columns: `ALIAS` (`-` if the translation has none), `HASH` (first 12 chars), `SOURCE` (`skill` or `byo`), `IMAGES` (`N built` when every algorithm has an `image_ref`, `N pending` when none, `N/M built` mixed, `N registered` for BYO), `CREATED`. Prints `no translations yet` and exits 0 if the directory is empty or missing. Malformed `translation_output.json` files are logged as warnings and skipped, so a stray file doesn't break the listing.

**`sim2real list runs`** — Walks `workspace/runs/*/run_metadata.json` and prints one row per run, newest first (mtime desc). Columns: `RUN_NAME`, `TRANSLATION` (first 8 chars of the translation hash), `CLUSTER`, `ASSEMBLED`. The active run (`current_run` in `setup_config.json`) is marked with `*`. Prints `no runs yet` and exits 0 if `workspace/runs/` is empty or missing. A subdirectory without `run_metadata.json` is skipped; a corrupt `run_metadata.json` renders `?` cells instead of aborting the listing.

**`sim2real use --run <name>`** — Sets `current_run` in `setup_config.json` to the given run. Errors with `"run doesn't exist; try 'sim2real list runs'"` (exit 2) if `workspace/runs/<name>/run_metadata.json` does not exist. Read-modify-write preserves unrelated keys in `setup_config.json`.

**`sim2real resolve --run <name>`** — Emits a hydrated JSON view of a run on stdout. Reads `workspace/runs/<name>/run_metadata.json` to locate the referenced translation, then walks `workspace/translations/<hash>/`, `workspace/runs/<name>/results/`, `workspace/runs/<name>/cluster/`, and `workspace/runs/<name>/manifest.assembly.yaml` to produce a single JSON document with everything the `/sim2real-check` skill (and other operator tooling) needs to reason about the run: metadata (run_name, cluster_id, params_hash, image_tag, assembled_at, cluster_config_path), translation (hash / alias / source / per-algorithm image_ref+config paths / per-baseline overlay paths from the manifest), results (declared phases, phases with collected data, workloads-by-phase), cluster scenarios (baseline.yaml, per-algorithm treatment YAMLs, pipelinerun-*.yaml files), and the manifest.assembly.yaml slice (scenario, workloads, defaults.disable, blis_observe). Schema is v1; future versions are additive. `translation_hash` appears only under `translation.hash` (not duplicated at top level). Exit codes: `0` with JSON on stdout on success; `2` with a specific error message on stderr (unknown run, corrupt/missing `run_metadata.json`, unresolvable `translation_hash`, missing workspace) — each error names the `sim2real` command that would repair the state.

The previous `run.py inspect` debug view is dropped without replacement — `cat workspace/runs/<name>/run_metadata.json` is the shortest path. `sim2real resolve --run <name>` is the structured superset for tooling.

---

## End-of-step-1 BYO demo

The full BYO flow. Every step is idempotent and re-runnable. Substitute your own values for `<cluster_id>`, `<run-name>`, `<algorithm>`, `<image-ref>`, and `<treatment-overlay-path>`.

### Prerequisites

- An experiment repo with `transfer.yaml`, `baselines/<name>.yaml`, and the workloads referenced in `transfer.yaml:workloads`.
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
python pipeline/sim2real.py translation register \
    --algorithm <algorithm> \
    --image <image-ref> \
    --config <treatment-overlay-path> \
    --experiment-root <experiment-root>
```

Writes `workspace/translations/<hash>/` with `translation_output.json`, `registered.json`, and `generated/<algorithm>/<algorithm>_config.yaml`. Prints the `<hash>` on success.

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

Builds the treatment EPP image (if not current), dispatches PipelineRuns across the namespace slots, and polls for completion. Progress lands in the run-scoped `sim2real-progress-<run-name>` ConfigMap. Use `deploy.py --run <run-name> status` to snapshot progress; use `deploy.py --run <run-name> stop` to cancel the remote orchestrator Job (if `--remote` was passed); use `deploy.py --run <run-name> reset` to requeue non-pending pairs (which also cancels their in-flight PipelineRuns).

### 6. Collect results

```bash
python pipeline/deploy.py --experiment-root <experiment-root> \
    --run <run-name> collect
```

Pulls per-pair `per_request_lifecycle_metrics.json` and GPU logs from the cluster PVC into `workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/`. This is the epic's success gate — the demo is done when the JSON files exist locally.

### Success criterion

For each `<workload>` in `transfer.yaml:workloads`, each `<phase>` in `{baseline, <algorithm>}`, and each iteration `<N>` in `1..replicas`:

```
workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/per_request_lifecycle_metrics.json
```

Once these files exist, step-1's BYO demo is complete. Downstream skills (e.g. `/sim2real-analyze`) consume them for latency comparison and report generation.

---

## End-of-step-2 skill-driven demo

Same success criterion as the BYO demo — per-workload per-algorithm `per_request_lifecycle_metrics.json` files exist locally. The difference is that instead of registering a pre-built image (step 3 above), the operator invokes the `/sim2real-translate` Claude skill to produce the plugin source, then `sim2real build` compiles it into images.

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

The same success gate applies — `per_request_lifecycle_metrics.json` under `workspace/runs/<run-name>/results/<phase>/<workload>/i<N>/` (one file per iteration).

---

## Pipeline library (`pipeline/lib/`)

| Module | Purpose |
|--------|---------|
| `manifest.py` | Loads and validates `transfer.yaml` (v3 schema) |
| `slicer.py` | Splits `transfer.yaml` into translation-slice vs assembly-slice + computes `translation_hash` (`translation_hash_with_sources` folds algorithm source bytes into the hash for the skill-driven flow) |
| `translation_ref.py` | Shared alias/algorithm-name validator, on-read shim for `translation_output.json` (handles both step-1 legacy and step-2 per-algo shapes), and `resolve_translation_ref` (accepts alias / hash prefix / full hash) |
| `build.py` | Shared build primitives — image-ref construction, skopeo digest probe, buildkit-pod dispatch, atomic JSON write. Consumed by `sim2real build` and `deploy.py:_cmd_build`. |
| `assemble_run.py` | Assembly logic behind `sim2real assemble` (deep-merge + PipelineRun generation, additive-grow / drift / legacy-run decision tree) |
| `values.py` | Deep-merge utility used by `assemble_run.py` |
| `pairkey.py` | Pair-key parser (canonical grammar `wl-<w>\|<p>\|iN` with legacy `wl-<w>\|<p>` fallback) and `--iteration` spec parser (list + range) |
| `tekton.py` | Generates PipelineRun YAMLs; `validate_pipelinerun_name` enforces the RFC 1123 253-char limit at assemble time |
| `pod_pending.py` | Classifies pod scheduling failures (recoverable vs not) |
| `remote.py` | ConfigMap + Job generation for `deploy.py run --remote` |
| `capacity.py` | Cluster GPU capacity probe (taint / cordon / product filter) |
| `cluster_ops.py` | Cluster-side primitives: read/write/update `cluster_config.json`, `provision_namespace`, `apply_cluster_resources`, `detect_openshift` |
| `layout.py` | Workspace path helpers (`workspace_dir`, `cluster_dir`, `cluster_config_path`, `runs_dir`, `translations_dir`, `translation_dir`, `setup_config_path`) |
| `epp.py` | EPP image injection helpers (`inject_epp_image`, `inject_image_ref`) |

---

## Workspace artifacts

All artifacts live under `<experiment-root>/workspace/` (gitignored). Key files:

| File | Written by | Read by |
|------|-----------|---------|
| `setup_config.json` (workspace fields: registry, repo_name, orchestrator_image, sim2real_root) | `setup.py` | `deploy.py`, `sim2real.py list runs` |
| `setup_config.json:current_run` (active run pointer) | `sim2real.py use` | `deploy.py` (default `--run`), `sim2real.py list runs` (active-mark `*`) |
| `clusters/<id>/cluster_config.json` (cluster fields: cluster_id, namespaces, is_openshift, storage_class, secret_names, workspaces, created_at) | `cluster.py provision` | `sim2real assemble`, `deploy.py`, `lib/remote.py` |
| `translations/<hash>/translation_output.json` | `sim2real translation register` | `sim2real assemble`, `deploy.py` |
| `translations/<hash>/registered.json` | `sim2real translation register` | audit trail |
| `translations/<hash>/generated/…` | `sim2real translation register` | `sim2real assemble` |
| `runs/<run>/run_metadata.json` | `sim2real assemble` | `deploy.py`, `sim2real.py list runs` |
| `runs/<run>/manifest.assembly.yaml` | `sim2real assemble` | reproducibility / drift detection (step-5) |
| `runs/<run>/cluster/…` | `sim2real assemble` | `deploy.py` |
| `runs/<run>/results/{phase}/` | `deploy.py collect` | `/sim2real-analyze` skill, `deploy.py wipe` |
| ConfigMap `sim2real-progress-{run}` | `deploy.py run`, `deploy.py reset` | all `deploy.py` subcommands |

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

context:
  text: |                   # freeform instructions (consumed by step-2's translation skill)
  files: [<path>, ...]      # files assembled into context document (step-2 consumer)

defaults:                   # optional — controls framework defaults overlay
  disable: []               # fragment stems (filename without .yaml) to skip;
                            # fragments live in <experiment-root>/baselines/defaults/
                            # and are merged UNDER each baseline by `sim2real assemble`. See
                            # docs/troubleshooting.md#framework-defaults-overlay.

# v3 fields (required unless noted)
target:
  repo: <path>              # llm-d-inference-scheduler repo path
config:
  kind: <string>            # config kind (e.g. "gaie")
build:                      # optional — defaults applied if absent
  commands: []              # EPP build commands
epp_image:                  # optional
  upstream:
    hub: <registry>
    name: <repo>
    tag: <tag>
  build:                    # override for built EPP image coordinates
    hub: <registry>
    name: <repo>
    tag: <tag>
pipeline:                   # optional — defaults applied if absent
  name: sim2real            # Pipeline resource name referenced in PipelineRuns (default: "sim2real")
  yaml: pipeline/pipeline.yaml  # path relative to repo root (default: "pipeline/pipeline.yaml")

blis_observe:               # optional — per-transfer overrides for blis observe tuning
  maxConcurrency: 10000     # all keys optional; absent keys fall through to the
  timeout: 1800             # Pipeline-level defaults in pipeline/pipeline.yaml
  warmupRequests: 50        # (currently 10000 / 1800 / 50 / 60s / "").
  prewarmDuration: 60s      # Values are emitted as PipelineRun params to override
  extraArgs: ""             # the Pipeline defaults — Tekton handles the merge.
```

All paths are relative to the experiment root and validated by `sim2real assemble` at load time.

`component.ref` (optional): tag, branch, or commit SHA identifying the expected version of the component submodule. Reserved for step-2 (the skill-driven flow that will consume it).

---

## Parallel Pool Execution

`cluster.py provision <cluster_id> --namespaces NS1,NS2,...` provisions N namespace slots, each bootstrapped identically. `sim2real assemble` generates one PipelineRun per `(workload, package, iteration)` tuple (the shared Tekton Pipeline itself is applied by `cluster.py provision`). `deploy.py run` orchestrates execution by assigning pairs to free slots, polling for completion, and retrying on timeout. Use `deploy.py collect` to pull results off-cluster. `deploy.py status` reads progress from the ConfigMap.

| Artifact | Written by | Read by |
|----------|-----------|---------|
| ConfigMap `sim2real-progress-{run}` | `deploy.py run`, `deploy.py reset` | All subcommands |

All subcommands (`status`, `collect`, `run`, `reset`, `wipe`) use a run-scoped `sim2real-progress-{run}` ConfigMap as the sole progress store. Each run gets its own ConfigMap, avoiding cross-run conflicts. A configured namespace is required.

---

## Scenario Overlay Format

Overlay files live under `workspace/translations/<hash>/generated/`. The baseline overlay layout differs between the two translation producers:

- **Skill-driven** (`/sim2real-translate`) — writes one overlay per referenced baseline at `baseline_<name>/baseline_config.yaml`. Assemble reads each baseline's overlay from its own directory.
- **BYO** (`sim2real translation register`) — writes a single shared overlay at `baseline_config.yaml` (the operator-supplied `--baseline-config` file). Applied to every baseline in the manifest; used as the fallback when a per-baseline directory is absent.

Overlays present in each translation:

- `baseline_<name>/baseline_config.yaml` — per-baseline overlay (skill-driven). One directory per baseline that any algorithm's `defaults` cross-references.
- `baseline_config.yaml` — legacy shared overlay (BYO). Present when `--baseline-config` was passed to `translation register`.
- `{algo_name}/{algo_name}_config.yaml` — per-algorithm treatment overlay (verbatim copy of the `--config` file for BYO; produced by the skill for skill-driven).

### Assembly formula

`sim2real assemble` deep-merges via `pipeline/lib/values.py:deep_merge`:

```python
baseline_resolved = deep_merge(framework_defaults, baseline_bundle, baseline_overlay)
treatment_resolved = deep_merge(baseline_resolved, treatment_bundle_diffs, algo_overlay)
```

Where `baseline_bundle` is the experiment's `baselines/<name>.yaml`, `treatment_diffs` is the experiment's optional `treatment.yaml`, `framework_defaults` merges `<experiment-root>/baselines/defaults/*.yaml` fragments (opt out via `transfer.yaml:defaults.disable`), and the overlays are the files written by `sim2real translation register`.

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
- Lists of scalars are replaced entirely
- Treatment overlay only needs the delta from baseline_resolved (shared config propagates automatically)

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

The EPP plugin configuration goes inside `inferenceExtension.pluginsCustomConfig` as a YAML-in-YAML string:

```yaml
inferenceExtension:
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
- `extraObjects` (InferenceObjectives with `poolRef`)
- `inferenceExtension.pluginsCustomConfig` (baseline scorer config)

**Treatment overlay** — only the delta from baseline:
- `inferenceExtension.pluginsCustomConfig` (evolved scorer config)
- `images.inferenceScheduler` (custom EPP image — injected by `sim2real assemble` from `translation_output.json:algorithms[i].image_ref` for the matching algorithm)

If treatment uses the same InferenceObjectives as baseline, do NOT repeat them — they propagate from `baseline_resolved`.

---

## Common patterns

```bash
# Assemble a run
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1

# Re-assemble the same run (clobbers workspace/runs/trial-1/)
python pipeline/sim2real.py assemble --translation HASH --cluster CID --run trial-1 --force

# Resubmit without rebuilding EPP
python pipeline/deploy.py run --skip-build

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
