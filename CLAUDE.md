# CLAUDE.md — sim2real

## Project Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
inference-sim to production llm-d-inference-scheduler scorer plugins.

## Repository Structure

- `pipeline/` — Pipeline entry points and shared library (see [`pipeline/README.md`](pipeline/README.md))
- `pipeline/pipeline.yaml` — Static Tekton Pipeline definition (applied by `cluster.py provision`)
- `prompts/` — Agent prompt templates (currently disabled — see below)
- `workspace/` — Inter-stage artifacts (gitignored, not committed)

## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `tektonc-data-collection/` — Tekton-based cluster data collection pipeline

`llm-d-inference-scheduler` is not a submodule of the framework repo. Each experiment repo carries its own copy (e.g. `admission-control/llm-d-inference-scheduler/`).

## Transfer Pipeline

The pipeline has two phases: a one-time-per-cluster bootstrap, then a per-workspace and per-run cycle. Two producers write translations with the same on-disk shape:

- **BYO** (`sim2real translation register`) — the operator supplies a pre-built EPP image.
- **Skill-driven** (`sim2real translate` → `/sim2real-translate` skill → `sim2real translate --resume` → `sim2real build`) — the operator supplies algorithm source; the skill translates it into a plugin and `sim2real build` compiles+pushes an image.

```
cluster.py provision  (one-time per cluster)
                   ↓
setup.py → [BYO: sim2real translation register] OR
           [Skill: sim2real translate → /sim2real-translate → translate --resume → sim2real build]
                   ↓
sim2real assemble → deploy.py
```

Run all pipeline commands from the `sim2real/` directory, pointing `--experiment-root` at the experiment repo:

```bash
# One-time cluster bootstrap (re-run only when adding/changing slots):
python pipeline/cluster.py provision <cluster_id> --namespaces NS1,NS2,...

# Per-workspace + per-run cycle:
python pipeline/setup.py     --experiment-root ../admission-control
python pipeline/sim2real.py translation register \
    --algorithm <name> --image <ref> --config <treatment-overlay-path>
python pipeline/sim2real.py assemble \
    --translation <hash> --cluster <cluster_id> --run <run-name>
python pipeline/deploy.py run --experiment-root ../admission-control
python pipeline/sim2real.py --experiment-root ../admission-control list runs
python pipeline/sim2real.py --experiment-root ../admission-control use --run <run-name>
```

**Backward compat:** Omitting `--experiment-root` defaults to the current working directory. Run all pipeline commands from the experiment repo root and the default will resolve correctly without the flag.

**`pipeline/setup.py`** — One-time workspace config writer. Writes `setup_config.json` with operator-side fields (registry, repo name, orchestrator image, sim2real_root). Idempotent — safe to re-run. Does not touch `workspace/runs/` — run directory materialization is owned by `sim2real assemble`. `current_run` in `setup_config.json` is owned by `sim2real use`. Cluster-side bootstrap (namespaces, RBAC, secrets, PVCs, Tekton tasks, Pipeline definition, and the optional `--pipeline-yaml` manifest override) lives in `cluster.py provision`.

**`pipeline/sim2real.py translation register`** — Records a BYO translation on disk. Writes `workspace/translations/<hash>/translation_output.json` (algorithm index + provenance), `registered.json` (image ref + digest), and `generated/<algo>/<algo>_config.yaml` (verbatim copy of the treatment overlay). `translation_hash` is deterministic — same inputs produce the same hash. See [`pipeline/README.md`](pipeline/README.md#register-a-translation) for the flag reference and idempotency rules.

**`pipeline/sim2real.py translate`** — Skill-driven translation checkpoint (step-2). Computes the translation hash (folding algorithm-source bytes via `slicer.translation_hash_with_sources`), creates `workspace/translations/<hash>/`, writes `skill_input.json` + `translation_output.json` with `image_ref: null`, and exits at the checkpoint. `--resume` validates every algorithm has a `<algo>_output.json` on disk; `--force` blows away the directory and re-checkpoints. See `pipeline/README.md#translate-skill-driven-step-2` for the state machine.

**`pipeline/sim2real.py build`** — Skill-driven build (step-2). Resolves `--translation` via the alias resolver, probes the registry with `skopeo inspect`, dispatches an in-cluster buildkit pod (`pipeline/scripts/build-epp.sh`), and records `image_ref`/`image_digest` per algorithm back into `translation_output.json` via atomic write. `--force-rebuild` skips the pre-build probe; `--skip-build` bypasses everything (assemble will then fail if any `image_ref` is null). See `pipeline/README.md#build-translation-images`.

**`pipeline/sim2real.py assemble`** — Materializes `workspace/runs/<run>/` from a translation and the experiment repo's `transfer.yaml`. Slices the manifest via `pipeline/lib/slicer.py`, snapshots the assembly slice into `manifest.assembly.yaml` (with a top-level `replicas: N` from `--replicas`, default 1), deep-merges framework defaults + baseline bundle + per-algorithm overlays into resolved scenarios, injects the image ref into treatment scenarios, generates one PipelineRun per (workload, package, iteration) tuple, and writes `run_metadata.json` (with `params_hash` = SHA-256 of the canonical `manifest.assembly.yaml` with `replicas` excluded, so bumping `--replicas` does not change the hash). Re-assembling an existing run is grow-only: `--replicas N` with `N > prior` additively appends iterations; `N < prior` is refused with a pointer to #506; `N == prior` is a no-op. Algorithms in `transfer.yaml` but not in the translation are warned and skipped. Algorithms in the translation with `image_ref: null` fail fast with a pointer to `sim2real build`.

**`/sim2real-translate`** — Skill-driven translation. Reads `workspace/translations/<hash>/skill_input.json` (written by `sim2real translate`) and spawns a three-agent team (expert + writer + reviewer) per algorithm to produce the Go plugin source + treatment overlay under `workspace/translations/<hash>/generated/<algo>/`. Follow up with `sim2real translate --resume` to validate outputs. See `.claude/skills/sim2real-translate/SKILL.md`.

**`pipeline/deploy.py`** — Builds EPP image and orchestrates PipelineRun execution across namespace slots (`deploy.py run`). Use `deploy.py collect` to pull results from the cluster PVC after runs complete. Operates independently of `transfer.yaml` — driven by workspace files, `setup_config.json`, and `clusters/<id>/cluster_config.json`.

**`pipeline/sim2real.py` (`use`, `list runs`, `list translations`)** — Manage runs and translations. `use --run <name>` flips `current_run` in `setup_config.json`. `list runs` prints all runs newest-first (mtime desc) with the active run marked `*`. `list translations` prints all translations newest-first (by `created_at`) with `ALIAS / HASH / SOURCE / IMAGES / CREATED` columns. Downstream commands (`assemble --translation`, and step-2's `build --translation`) accept an alias, a hash prefix (min 4 chars), or a full hash — resolution happens via `pipeline/lib/translation_ref.py:resolve_translation_ref`.

**`pipeline/cluster.py`** — Cluster-side bootstrap and slot-pool management. `cluster.py init <cluster_id> <primary_namespace>` bootstraps a new cluster (cluster-wide config + Tekton Pipeline definition + primary namespace's per-namespace resources) — refuses if the cluster already exists. `cluster.py slot add|remove|list <cluster_id> [<namespace>]` grows / shrinks / inspects the pool at any time — safe to run mid-run, `--remote` orchestrators pick up pool changes within ~60s via a live-mounted `cluster_config--<id>` ConfigMap key (issue #571). `slot add` is fully idempotent; `slot remove` refuses primary and refuses namespaces not currently in the pool. `cluster.py provision <cluster_id> --namespaces NS1,NS2,...` is retained as backwards-compat sugar over `init` + `slot add`. All writes land in `workspace/clusters/<cluster_id>/cluster_config.json`. Every subcommand is safe to re-run.

## Pipeline Library (`pipeline/lib/`)

| Module | Purpose |
|--------|---------|
| `manifest.py` | Loads and validates `transfer.yaml` (v3 schema) |
| `slicer.py` | Splits `transfer.yaml` into translation-slice vs assembly-slice + computes `translation_hash` |
| `translation_ref.py` | Shared alias/algorithm-name validator, on-read shim for `translation_output.json` (handles both step-1 legacy and step-2 per-algo shapes), and `resolve_translation_ref` (accepts alias / hash prefix / full hash) |
| `build.py` | Shared build primitives — image-ref construction, skopeo digest probe, buildkit-pod dispatch, atomic JSON write. Consumed by `sim2real build` and `deploy.py:_cmd_build`. |
| `assemble_run.py` | Assembly logic behind `sim2real assemble` (deep-merge + PipelineRun generation, additive-grow / drift / legacy-run decision tree) |
| `values.py` | Deep-merge utility (`deep_merge`) used by `assemble_run.py` |
| `pairkey.py` | Pair-key parser (canonical grammar `wl-<w>\|<p>\|iN` with legacy `wl-<w>\|<p>` fallback) and `--iteration` spec parser (list + range) |
| `tekton.py` | Generates PipelineRun YAMLs for scenario-based benchmarks; `validate_pipelinerun_name` enforces the RFC 1123 253-char limit at assemble time |
| `pod_pending.py` | Classifies pod scheduling failures as recoverable or non-recoverable |
| `remote.py` | ConfigMap and Job generation for `deploy.py run --remote` |
| `capacity.py` | Cluster GPU capacity probe (taint / cordon / product filter) |
| `cluster_ops.py` | Cluster-side primitives: read/write/update `cluster_config.json`, `provision_namespace`, `apply_cluster_resources`, `detect_openshift` |
| `layout.py` | Workspace path helpers (`workspace_dir`, `cluster_dir`, `cluster_config_path`, `runs_dir`, `translations_dir`, `translation_dir`, `setup_config_path`) |
| `epp.py` | EPP image injection helpers (`inject_epp_image`, `inject_image_ref`) |

## Workspace Artifacts

All artifacts live under `<experiment-root>/workspace/` (gitignored). When no `--experiment-root` is given, defaults to `workspace/` in the framework directory (backward compat). Key files:

| File | Written by | Read by |
|------|-----------|---------|
| `setup_config.json` (workspace fields: registry, repo_name, orchestrator_image, sim2real_root) | `setup.py` | `deploy.py`, `sim2real.py list runs` |
| `setup_config.json:current_run` (active run pointer) | `sim2real.py use` | `deploy.py` (default `--run`), `sim2real.py list runs` (active-mark `*`) |
| `clusters/<id>/cluster_config.json` (cluster fields: cluster_id, namespaces, is_openshift, storage_class, secret_names, workspaces, pipeline_yaml (optional), created_at) | `cluster.py init` / `slot add` / `slot remove` (`provision` remains as sugar) | `sim2real assemble`, `deploy.py`, `lib/remote.py` |
| `translations/<hash>/translation_output.json` (step-2 shape: top-level `alias`; per-algo `image_ref`/`image_digest`/`config_path`/`source_path`/`source_sha256` inside `algorithms[i]`. Step-1 legacy files with top-level `image_ref` remain readable via `translation_ref.read_translation_output`) | `sim2real translation register` (BYO); `sim2real translate` (skill; writes null image fields); `sim2real build` (fills `image_ref`/`image_digest` per algo) | `sim2real assemble`, `sim2real list translations`, `deploy.py` |
| `translations/<hash>/skill_input.json` (skill-driven only) | `sim2real translate` | `/sim2real-translate` skill |
| `translations/<hash>/generated/{algo}/{algo}_output.json` (skill-driven only) | `/sim2real-translate` skill | `sim2real translate --resume`, `sim2real build` (completeness check) |
| `translations/<hash>/generated/{algo}/{cmd,pkg}/` (skill-driven only) | `/sim2real-translate` skill | `sim2real build` (buildkit input) |
| `translations/<hash>/registered.json` | `sim2real translation register` | audit trail |
| `translations/<hash>/generated/baseline_config.yaml` | `sim2real translation register` (via `--baseline-config`) | `sim2real assemble` (baseline overlay — legacy BYO fallback) |
| `translations/<hash>/generated/baselines/{name}/baseline_config.yaml` | `/sim2real-translate` skill | `sim2real assemble` (per-baseline overlay; primary skill-driven path — issue #544) |
| `translations/<hash>/generated/{algo}/{algo}_config.yaml` | `sim2real translation register` | `sim2real assemble` (per-algo treatment overlay) |
| `runs/<run>/run_metadata.json` | `sim2real assemble` | `deploy.py`, `sim2real.py list runs` |
| `runs/<run>/manifest.assembly.yaml` | `sim2real assemble` | reproducibility / drift detection on re-assemble; carries top-level `replicas: N` |
| `runs/<run>/cluster/baseline.yaml` | `sim2real assemble` | `deploy.py` |
| `runs/<run>/cluster/<algo>.yaml` | `sim2real assemble` | `deploy.py` |
| `runs/<run>/cluster/pipelinerun-*.yaml` | `sim2real assemble` | `deploy.py run` |
| `runs/<run>/results/{phase}/` | `deploy.py collect` | `/sim2real-analyze` skill, `deploy.py wipe` |
| `runs/<run>/results/{phase}/<workload>/i<N>/gpu_logs/<node>.log` | `deploy.py collect` (pulled from PVC) | analysis / debugging |
| `runs/<run>/results/{phase}/<workload>/i<N>/metrics/raw/<pod>_<ts>_metrics.log` | `deploy.py collect` (pulled from PVC) | analysis — raw Prometheus text-exposition dumps, one file per pod per scrape (produced by `collect_metrics.sh`, which `stream-metrics` wraps — see `pipeline/README.md#metric-capture-stream-metrics`) |
| `runs/<run>/results/{phase}/<workload>/i<N>/metrics/processed/metrics_summary.json` | `deploy.py collect` (pulled from PVC) | analysis — post-run percentiles over the metrics named in `process_metrics.py:AGGREGATE_METRICS` |
| `runs/<run>/results/{phase}/<workload>/i<N>/metrics/processed/replica_status_timeseries.json` | `deploy.py collect` (pulled from PVC) | analysis — replica state / scale over the workload window |
| ConfigMap `sim2real-progress-{scenario}-{run}` | `deploy.py run`, `deploy.py reset` | All `deploy.py` subcommands |
| `runs/<run>/plans/<phase>/<workload>/` | `deploy.py run` | workload tasks |

## Development

- Python >= 3.10
- Tests: `python -m pytest pipeline/ -v`
- Lint: `ruff check pipeline/` (if installed)
- PyYAML required for `assemble_run.py` and `tekton.py` — install via `pip install -r requirements.txt`

## CI

CI runs on every push and PR to `main` (`.github/workflows/test.yml`). **It must pass before merging.**

Two checks run in order:

```bash
# 1. Lint (pyflakes errors only — F codes)
ruff check pipeline/ .claude/skills/ --select F

# 2. Tests
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_translation_ref.py \
  pipeline/tests/test_translate.py \
  pipeline/tests/test_build.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v
```

Run both locally before pushing. If your change adds a new module, test file location, or skill, update `.github/workflows/test.yml` to include it — CI only covers paths explicitly listed.

## Contributing: Read This First

**Before developing anything in `pipeline/`**, read [`pipeline/README.md`](pipeline/README.md). It documents all entry points, CLI flags, artifact schemas, workspace layout, and common patterns.

**After any change to `pipeline/`** that affects CLI flags, subcommand behavior, artifact schema, or new modules, update `pipeline/README.md` to match.

## Contributing: Pipeline Stage Contracts

The `pipeline/` scripts form a linear dependency chain, with a one-time cluster bootstrap as prerequisite:

```
cluster.py provision  (one-time per cluster)
                   ↓
setup.py → [BYO: sim2real translation register] OR
           [Skill: sim2real translate → /sim2real-translate → sim2real translate --resume → sim2real build]
                   ↓
sim2real assemble → deploy.py
```

Each stage communicates with the next through files written to `workspace/`. When modifying any stage, you **must** trace both directions:

- **Upstream** — does your change depend on output produced by a prior stage? Verify the prior stage still produces what you expect, or update it too.
- **Downstream** — does any later stage consume what your stage produces? If you change an output file's schema, keys, or format, update every consumer of that file in the same change.

The workspace artifact table above is the definitive map of what each stage reads and writes. Use it to determine the blast radius of any change.

## Fixing Pipeline Issues: Always Fix Upstream

`workspace/` is generated — any file there can be regenerated by re-running the stage that produced it. **Fixing a workspace file directly is never sufficient.** When a bug is found in a workspace artifact, the fix must go in the source that generates it:

- Resolved scenarios (`cluster/baseline.yaml`, `cluster/<algo>.yaml`), PipelineRun YAMLs, `run_metadata.json`, `manifest.assembly.yaml` — generated by `sim2real assemble` (`pipeline/lib/assemble_run.py`)
- `translation_output.json`, `registered.json`, and `generated/` overlays — written by `sim2real translation register`; re-run register to regenerate
- Any other workspace artifact — trace it to its generating command and fix there

If a fix only exists in `workspace/`, it will be silently lost the next time that stage runs.
