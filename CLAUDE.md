# CLAUDE.md — sim2real

## Project Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
inference-sim to production llm-d-inference-scheduler scorer plugins.

## Repository Structure

- `config/` — Version-controlled configuration. `transfer.yaml` defines the experiment manifest; `env_defaults.yaml` provides deploy-time infrastructure defaults (image registry, fast-iteration flag). Experiment repos carry their own `transfer.yaml` at their root.
- `docs/transfer/` — Mapping artifacts, scorer template, calibration log
- `docs/plans/` — Design docs and implementation plans
- `pipeline/` — Pipeline entry points and shared library (see [`pipeline/README.md`](pipeline/README.md))
- `pipeline/templates/` — Default Tekton Pipeline template (`pipeline.yaml.j2`)
- `workspace/` — Inter-stage artifacts (gitignored, not committed)

## Submodules

- `inference-sim/` — Discrete-event LLM inference simulator (source of evolved algorithms)
- `tektonc-data-collection/` — Tekton-based cluster data collection pipeline

`llm-d-inference-scheduler` is not a submodule of the framework repo. Each experiment repo carries its own copy (e.g. `admission-control/llm-d-inference-scheduler/`).

## Transfer Pipeline

The pipeline runs in four scripts with a skill invoked between prepare and deploy:

```
setup.py → prepare.py → [/sim2real-translate] → deploy.py
```

Run all pipeline commands from the `sim2real/` directory, pointing `--experiment-root` at the experiment repo:

```bash
python pipeline/setup.py   --experiment-root ../admission-control
python pipeline/prepare.py --experiment-root ../admission-control
python pipeline/deploy.py  --experiment-root ../admission-control
python pipeline/run.py     --experiment-root ../admission-control list
python pipeline/run.py     --experiment-root ../admission-control switch <run-name>
```

**Backward compat:** Omitting `--experiment-root` defaults to the current working directory. Run all pipeline commands from the experiment repo root and the default will resolve correctly without the flag.

**`pipeline/setup.py`** — One-time cluster bootstrap (namespace, RBAC, secrets, PVCs, Tekton tasks). Idempotent — safe to re-run.

**`pipeline/prepare.py`** — 6-phase state machine. Re-running skips completed phases (tracked in `.state.json`):

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Init | Load `config/transfer.yaml`, validate file prerequisites |
| 2 | Context | Assemble context document, cache by SHA-256 hash |
| 3 | Translate checkpoint | Write `skill_input.json`; exit and wait for `/sim2real-translate` skill |
| 4 | Assembly | Assemble resolved scenarios from bundles + overlays, generate PipelineRuns |
| 5 | Summary | Write `run_summary.md` |
| 6 | Gate | Human review: `[d]eploy / [e]dit / [q]uit` |

**`/sim2real-translate`** — AI skill that reads `skill_input.json` and writes `translation_output.json`. Run this after prepare exits at Phase 3, then re-run prepare to continue.

**`pipeline/deploy.py`** — Builds EPP image, applies Tekton Pipeline resources, submits PipelineRuns. Use `deploy.py collect` to pull results from the cluster PVC after runs complete.

**`pipeline/run.py`** — Lists, inspects, and switches between runs. `switch` syncs generated scorer plugin files into the experiment repo's `llm-d-inference-scheduler/` directory. Pass `--experiment-root` to point at the experiment repo (default: current directory).

## Pipeline Library (`pipeline/lib/`)

| Module | Purpose |
|--------|---------|
| `manifest.py` | Loads and validates `config/transfer.yaml` (v2/v3 schema) |
| `state_machine.py` | Phase tracking with atomic JSON persistence (`.state.json`) |
| `context_builder.py` | Assembles context document, caches by SHA-256 hash |
| `values.py` | Deep-merge utility (`deep_merge`) used by `assemble.py` |
| `assemble.py` | Scenario assembly: deep-merges bundles + overlays into resolved scenarios |
| `tekton.py` | Generates PipelineRun YAMLs for scenario-based benchmarks |
| `run_manager.py` | `list_runs`, `inspect_run`, `switch_run` logic |

## Workspace Artifacts

All artifacts live under `<experiment-root>/workspace/` (gitignored). When no `--experiment-root` is given, defaults to `workspace/` in the framework directory (backward compat). Key files:

| File | Written by | Read by |
|------|-----------|---------|
| `setup_config.json` | `setup.py` | `prepare.py`, `deploy.py` |
| `runs/<run>/.state.json` | `prepare.py` | `prepare.py`, `deploy.py` |
| `runs/<run>/run_metadata.json` | `setup.py`, `deploy.py` | `deploy.py`, `run.py` |
| `runs/<run>/skill_input.json` | `prepare.py` Phase 3 | `/sim2real-translate` skill |
| `runs/<run>/translation_output.json` | `/sim2real-translate` skill | `prepare.py` Phase 4 |
| `runs/<run>/generated/baseline_config.yaml` | `/sim2real-translate` skill | `prepare.py` Phase 4 (overlay) |
| `runs/<run>/generated/treatment_config.yaml` | `/sim2real-translate` skill | `prepare.py` Phase 4 (overlay) |
| `runs/<run>/cluster/baseline.yaml` | `prepare.py` Phase 4 | `deploy.py` |
| `runs/<run>/cluster/treatment.yaml` | `prepare.py` Phase 4 | `deploy.py` |
| `runs/<run>/cluster/wl-*/*.yaml` | `prepare.py` Phase 4 | `deploy.py` |
| `runs/<run>/run_summary.md` | `prepare.py` Phase 5 | human review |
| `runs/<run>/results/{phase}/` | `deploy.py collect` | `/sim2real-analyze` skill |
| `runs/<run>/progress.json` | `deploy.py run` | `deploy.py status` |
| `context/{scenario}/{hash}.md` | `prepare.py` Phase 2 | `prepare.py` Phase 2 (cache) |

## Development

- Python >= 3.10
- Tests: `python -m pytest pipeline/ -v`
- Lint: `ruff check pipeline/` (if installed)
- PyYAML required for `assemble.py` and `tekton.py` — install via `pip install -r requirements.txt`

## CI

CI runs on every push and PR to `main` (`.github/workflows/test.yml`). **It must pass before merging.**

Two checks run in order:

```bash
# 1. Lint (pyflakes errors only — F codes)
ruff check pipeline/ .claude/skills/ --select F

# 2. Tests
python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-translate/tests/ -v
```

Run both locally before pushing. If your change adds a new module, test file location, or skill, update `.github/workflows/test.yml` to include it — CI only covers paths explicitly listed.

## Contributing: Read This First

**Before developing anything in `pipeline/`**, read [`pipeline/README.md`](pipeline/README.md). It documents all entry points, CLI flags, phase behaviors, workspace artifacts, and common patterns.

**After any change to `pipeline/`** that affects CLI flags, phase behavior, artifact schema, or subcommands, update `pipeline/README.md` to match.

## Contributing: Pipeline Stage Contracts

The `pipeline/` scripts form a linear dependency chain:

```
setup.py → prepare.py → [sim2real-translate skill] → deploy.py
```

Each stage communicates with the next through files written to `workspace/`. When modifying any stage, you **must** trace both directions:

- **Upstream** — does your change depend on output produced by a prior stage? Verify the prior stage still produces what you expect, or update it too.
- **Downstream** — does any later stage consume what your stage produces? If you change an output file's schema, keys, or format, update every consumer of that file in the same change.

The workspace artifact table above is the definitive map of what each stage reads and writes. Use it to determine the blast radius of any change.

## Fixing Pipeline Issues: Always Fix Upstream

`workspace/` is generated — any file there can be regenerated by re-running the stage that produced it. **Fixing a workspace file directly is never sufficient.** When a bug is found in a workspace artifact, the fix must go in the source that generates it:

- Resolved scenarios (`cluster/baseline.yaml`, `cluster/treatment.yaml`) — generated by `prepare.py` Phase 4 (`pipeline/lib/assemble.py`)
- `translation_output.json` and `generated/` overlays — written by the `/sim2real-translate` skill; re-run the skill to regenerate
- PipelineRun YAMLs under `cluster/wl-*/` — generated by `prepare.py` Phase 4 (`pipeline/lib/tekton.py`)
- Any other workspace artifact — trace it to its generating phase and fix there

If a fix only exists in `workspace/`, it will be silently lost the next time that phase runs.

## Scenario-Based Assembly Architecture

`prepare.py` Phase 4 assembles benchmarking artifacts using a bundle + overlay model:

**Bundle inputs** (version-controlled in experiment repo):
- `baseline.yaml` — baseline scenario (model config, baseline scorer EPP config)
- `treatment.yaml` (optional) — diffs from baseline for the treatment arm

**Skill-generated overlays** (in `workspace/runs/<run>/generated/`):
- `baseline_config.yaml` — baseline scorer plugin config overlay
- `treatment_config.yaml` — evolved treatment scorer plugin config overlay

**Assembly** is performed by `prepare.py` Phase 4 via `pipeline/lib/assemble.py`:
- `baseline_resolved = deep_merge(baseline_bundle, baseline_overlay)`
- `treatment_resolved = deep_merge(deep_merge(baseline_resolved, treatment_diffs), treatment_overlay)`
- EPP image injected into treatment scenarios from `run_metadata.json`
- PipelineRuns generated per workload × {baseline, treatment}

**Deploy-time config** — `config/env_defaults.yaml` (version-controlled):
- `epp_image.build.{hub, name, platform}` — EPP image build settings
- `pipeline.fast_iteration` (boolean, default `true`): when `true`, skips noise gate and mechanism check
- `observe.request_multiplier` (number, default `10`): multiplies each workload's `num_requests` for real-cluster benchmarks
