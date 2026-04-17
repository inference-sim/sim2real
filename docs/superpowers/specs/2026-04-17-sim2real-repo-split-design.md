# sim2real Repository Split Design

**Date:** 2026-04-17  
**Status:** Approved for implementation  
**Approach:** Option C (convention-based peer directories), designed toward Option B (published CLI + plugin)

## Motivation

sim2real is used to transfer simulation-discovered algorithms to production. Each algorithm exploration
is independent work — different teams, different timelines, different target systems. Keeping all
explorations in one repo creates shared git history, requires write access for all contributors, prevents
independent versioning, and clutters the framework with algorithm bundles.

Primary drivers (in priority order):
- **C — Collaboration:** teams own experiments independently
- **D — Extensibility:** external contributors create experiment repos without touching sim2real
- **B — Independent versioning:** each experiment evolves on its own timeline
- **A — Code clarity:** sim2real stays clean

## Two Repositories

### `sim2real` (framework — unchanged name)

The existing repo. Contains the pipeline engine, Claude skills, and shared submodules. No algorithm
bundles. Researchers open this directory in Claude Code; skills are discovered naturally from `.claude/skills/`.

Retains:
- `pipeline/` — setup.py, prepare.py, deploy.py, run.py, lib/
- `.claude/skills/` — sim2real-translate, sim2real-analyze, etc.
- `inference-sim/` submodule
- `tektonc-data-collection/` submodule
- `config/env_defaults.yaml` — cluster-level defaults (used when experiment has no override)
- `pipeline/templates/pipeline.yaml.j2` — default Tekton Pipeline template

Removes (each becomes its own experiment repo):
- `admission_by60/`
- `blis_router/`
- `sim2real_bundle_14b/`, `sim2real_bundle_14b.2026-04-15/`

### `sim2real-<name>` (experiment repo, one per algorithm exploration)

A lightweight repo containing only algorithm-specific content. Examples:
`sim2real-admission-14b`, `sim2real-blis-router`, `sim2real-prefill-scheduler`.

## Experiment Repo Contract

```
sim2real-<name>/
├── algorithm/            # Go source files (treatment + control variants)
├── workloads/            # workload YAML specs consumed by inference-sim
├── transfer.yaml         # manifest: scenario, algorithm source, workloads, hints
├── env_defaults.yaml     # cluster overrides (merged over sim2real's cluster defaults)
├── pipeline.yaml.j2      # optional: overrides sim2real's default Tekton Pipeline template
├── workspace/            # gitignored — generated artifacts (runs/, setup_config.json, etc.)
└── <target-project>/     # submodule for the system being enhanced
                          # e.g. llm-d-inference-scheduler, llm-d-foo
```

All paths in `transfer.yaml` are relative to the experiment repo root (not the sim2real root).

`pipeline.yaml.j2` is optional. When absent the framework default is used. When present it overrides
the framework default for this experiment only.

The target project submodule pins the exact version of the system the algorithm enhances. Its name
matches the project (e.g. `llm-d-inference-scheduler/`). Optional — omit if the experiment does not
need to pin a specific version of any target system.

## Changes to `sim2real/pipeline/`

### New flags on `prepare.py`, `setup.py`, `deploy.py`

| Flag | Default | Effect |
|------|---------|--------|
| `--experiment-root PATH` | `.` | Root of the experiment repo. All relative paths in `transfer.yaml` resolve from here. `workspace/` is created here. |
| `--pipeline-template PATH` | see resolution order below | Explicit path to a `pipeline.yaml.j2` override. |

### Pipeline template resolution order

1. `--pipeline-template` flag (explicit)
2. `<experiment-root>/pipeline.yaml.j2` (experiment-level override, if file exists)
3. `<sim2real>/pipeline/templates/pipeline.yaml.j2` (framework default)

### Path resolutions that shift to `--experiment-root`

| Artifact | Today | After |
|----------|-------|-------|
| Manifest | `config/transfer.yaml` | `<experiment-root>/transfer.yaml` |
| Env defaults | `config/env_defaults.yaml` | `<experiment-root>/env_defaults.yaml` |
| Algorithm source, workloads | relative to `sim2real/` root | relative to `<experiment-root>` |
| `workspace/` | `sim2real/workspace/` | `<experiment-root>/workspace/` |
| Target project submodule | `sim2real/llm-d-inference-scheduler/` | `<experiment-root>/<target-project>/` |

### Backward compatibility

`--experiment-root` defaults to `.`. `prepare.py` resolves the manifest by checking
`<experiment-root>/transfer.yaml` first, then `<experiment-root>/config/transfer.yaml` as a
fallback. This means existing monorepo usage (running from `sim2real/` with files still under
`config/`) continues to work unchanged during migration.

## Working Convention (Option C)

```
~/work/
├── sim2real/                    # clone once; open in Claude Code
└── sim2real-admission-14b/      # experiment repo (peer directory)
```

Run pipeline from the `sim2real/` directory:

```bash
python pipeline/prepare.py --experiment-root ../sim2real-admission-14b
```

Multiple experiments can run simultaneously — each has its own `workspace/`.

## Migration of Existing Bundles

| Bundle in `sim2real/` | New repo name |
|-----------------------|---------------|
| `admission_by60/` | `sim2real-admission-by60` |
| `blis_router/` | `sim2real-blis-router` |
| `sim2real_bundle_14b/`, `sim2real_bundle_14b.2026-04-15/` | `sim2real-admission-14b` |

Steps per bundle:
1. Create new repo; copy bundle contents to its root
2. Copy `config/env_defaults.yaml` from `sim2real` as `env_defaults.yaml`
3. Update `transfer.yaml` — rewrite paths from `sim2real_bundle_14b/algorithm/...` to `algorithm/...`
4. Add `workspace/` to `.gitignore`
5. Add target project as submodule at repo root
6. Verify pipeline runs with `--experiment-root <new-repo>`
7. Delete bundle directory from `sim2real/`

## Path Toward Option B

The experiment repo contract defined here is the same interface Option B would expose. When external
adoption warrants it, packaging sim2real as a CLI + Claude plugin becomes a mechanical lift:

- `pipeline/` scripts → `sim2real prepare`, `sim2real deploy` CLI commands
- `.claude/skills/` → published Claude plugin
- No changes needed to experiment repos — the contract is identical
